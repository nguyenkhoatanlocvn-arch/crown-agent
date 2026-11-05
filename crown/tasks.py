"""Sổ nhiệm vụ CROWN AGENT (master prompt mục 11-12).

SQLite có transaction. Mỗi nhiệm vụ có máy trạng thái, khóa nhận việc (lease)
kèm fencing token để worker cũ hết hạn không ghi đè, và idempotency key để
bấm tạo 2 lần không sinh 2 nhiệm vụ. Nhật ký sự kiện chỉ thêm, không sửa.
Giờ lưu UTC; hiển thị theo Asia/Ho_Chi_Minh.
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo

VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")
MAX_RETRY = 3

TRANSITIONS: dict[str, set[str]] = {
    "DRAFT": {"READY", "CANCELLED"},
    "READY": {"RUNNING", "BLOCKED", "CANCELLED"},
    "RUNNING": {"REVIEW", "BLOCKED", "WAITING_APPROVAL", "PAUSED_QUOTA", "INTERRUPTED", "FAILED"},
    "REVIEW": {"VERIFIED_DONE", "RUNNING", "FAILED"},
    "BLOCKED": {"READY", "CANCELLED"},
    "WAITING_APPROVAL": {"READY", "CANCELLED"},
    "PAUSED_QUOTA": {"READY", "CANCELLED"},
    "INTERRUPTED": {"READY", "CANCELLED"},
    "FAILED": {"READY", "CANCELLED"},
    "VERIFIED_DONE": set(),
    "CANCELLED": set(),
}
STATES = set(TRANSITIONS)

SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
  task_id TEXT PRIMARY KEY,
  parent_id TEXT,
  company_id TEXT,
  owner_agent_id TEXT NOT NULL,
  objective TEXT NOT NULL,
  acceptance_criteria TEXT NOT NULL,
  dependencies TEXT NOT NULL DEFAULT '[]',
  state TEXT NOT NULL,
  state_version INTEGER NOT NULL DEFAULT 0,
  lease_owner TEXT,
  lease_expiry TEXT,
  fencing_token INTEGER NOT NULL DEFAULT 0,
  last_worker TEXT,
  budget_reservation REAL NOT NULL DEFAULT 0,
  approval_refs TEXT NOT NULL DEFAULT '[]',
  artifact_refs TEXT NOT NULL DEFAULT '[]',
  evidence_refs TEXT NOT NULL DEFAULT '[]',
  next_action TEXT,
  retry_count INTEGER NOT NULL DEFAULT 0,
  idempotency_key TEXT UNIQUE,
  created_at TEXT NOT NULL,
  started_at TEXT,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS task_workers (
  task_id TEXT NOT NULL,
  worker TEXT NOT NULL,
  PRIMARY KEY (task_id, worker)
);
CREATE TABLE IF NOT EXISTS events (
  event_id INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id TEXT NOT NULL,
  at TEXT NOT NULL,
  actor TEXT NOT NULL,
  from_state TEXT,
  to_state TEXT,
  note TEXT
);
CREATE TRIGGER IF NOT EXISTS events_no_update BEFORE UPDATE ON events
BEGIN SELECT RAISE(ABORT, 'events la nhat ky chi them'); END;
CREATE TRIGGER IF NOT EXISTS events_no_delete BEFORE DELETE ON events
BEGIN SELECT RAISE(ABORT, 'events la nhat ky chi them'); END;
"""


class TaskError(Exception):
    """Thao tác nhiệm vụ bị từ chối vì vi phạm luật."""


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def to_vn(iso: str | None) -> str:
    if not iso:
        return "-"
    return datetime.fromisoformat(iso).astimezone(VN_TZ).strftime("%Y-%m-%d %H:%M (UTC+7)")


class TaskStore:
    def __init__(self, path: str | Path, registry=None, clock: Callable[[], datetime] = utc_now):
        self.path = str(path)
        self.registry = registry
        self.clock = clock
        self.db = sqlite3.connect(self.path, isolation_level=None, timeout=10, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(SCHEMA)
        cols = {r["name"] for r in self.db.execute("PRAGMA table_info(tasks)")}
        # Nâng cấp DB cũ, không mất dữ liệu. Nhiều tiến trình có thể cùng nâng cấp một lúc
        # (gửi trùng đồng thời): tiến trình đến sau gặp "duplicate column" là bình thường.
        for col in ("attempt_evidence_start",   # bằng chứng tính từ lần nhận việc hiện tại
                    "pending_retry"):           # 1 = lần nhận tới là thử lại sau thất bại/gián đoạn
            if col not in cols:
                try:
                    self.db.execute(f"ALTER TABLE tasks ADD COLUMN {col} INTEGER NOT NULL DEFAULT 0")
                except sqlite3.OperationalError as e:
                    if "duplicate column" not in str(e):
                        raise
        # Khôi phục lịch sử người làm cho DB tạo trước khi có bảng task_workers (idempotent).
        self.db.execute("""INSERT OR IGNORE INTO task_workers(task_id, worker)
                           SELECT task_id, actor FROM events WHERE to_state='RUNNING' AND note LIKE '%token=%'
                           UNION SELECT task_id, last_worker FROM tasks WHERE last_worker IS NOT NULL""")

    # ---------- danh tính & thẩm quyền ----------
    def _require_member(self, actor: str, what: str) -> dict | None:
        if self.registry is None:
            return None
        try:
            m = self.registry.member(actor)
        except KeyError:
            raise TaskError(f"'{actor}' không có trong sổ đăng ký — không được {what}") from None
        if m.get("status") == "DISABLED":
            raise TaskError(f"{actor} đang bị tắt — không được {what}")
        return m

    def _workers(self, task_id: str) -> set[str]:
        return {r["worker"] for r in self.db.execute("SELECT worker FROM task_workers WHERE task_id=?", (task_id,))}

    def _authorities(self, owner: str) -> set[str]:
        """Ai được duyệt/chấp thuận: cấp trên của chủ nhiệm vụ, Chủ tịch, và (với duyệt kết quả) Agent 20."""
        try:   # mọi loại thành viên: lãnh đạo, Agent, Agent con, ghế TN mới, đơn vị dưới Thiên Não
            chain = self.registry.chain(owner)
        except KeyError:
            chain = []
        return set(chain[1:]) | {"CHAIRMAN"}

    def _current_evidence(self, t) -> list[str]:
        return json.loads(t["evidence_refs"])[t["attempt_evidence_start"]:]

    def close(self) -> None:
        self.db.close()

    # ---------- tiện ích ----------
    def _now(self) -> str:
        return self.clock().astimezone(timezone.utc).isoformat()

    def _tx(self):
        self.db.execute("BEGIN IMMEDIATE")

    def _event(self, task_id, actor, frm, to, note=""):
        self.db.execute(
            "INSERT INTO events(task_id, at, actor, from_state, to_state, note) VALUES (?,?,?,?,?,?)",
            (task_id, self._now(), actor, frm, to, note),
        )

    def get(self, task_id: str) -> dict:
        row = self.db.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone()
        if row is None:
            raise TaskError(f"Không có nhiệm vụ {task_id}")
        d = dict(row)
        for k in ("dependencies", "approval_refs", "artifact_refs", "evidence_refs"):
            d[k] = json.loads(d[k])
        return d

    def events(self, task_id: str) -> list[dict]:
        return [dict(r) for r in self.db.execute(
            "SELECT * FROM events WHERE task_id=? ORDER BY event_id", (task_id,))]

    def list(self, state: str | None = None) -> list[dict]:
        q, args = "SELECT task_id FROM tasks", ()
        if state:
            q, args = q + " WHERE state=?", (state,)
        return [self.get(r["task_id"]) for r in self.db.execute(q + " ORDER BY created_at", args)]

    # ---------- tạo ----------
    def create(self, *, owner_agent_id: str, objective: str, acceptance_criteria: str, actor: str,
               company_id: str | None = None, parent_id: str | None = None,
               dependencies: list[str] | None = None, idempotency_key: str | None = None,
               ready: bool = False) -> dict:
        if not objective.strip() or not acceptance_criteria.strip():
            raise TaskError("Nhiệm vụ phải có mục tiêu và tiêu chí hoàn thành")
        if self.registry is not None:
            try:
                owner = self.registry.member(owner_agent_id)
            except KeyError:
                raise TaskError(f"Chủ nhiệm vụ '{owner_agent_id}' không có trong sổ đăng ký") from None
            if owner.get("status") == "DISABLED":
                raise TaskError(f"{owner_agent_id} đang bị tắt")
            if company_id and company_id not in self.registry.companies:
                raise TaskError(f"Công ty '{company_id}' không có trong sổ đăng ký")
            self._require_member(actor, "tạo nhiệm vụ")
        self._tx()
        try:
            if idempotency_key:
                row = self.db.execute("SELECT * FROM tasks WHERE idempotency_key=?",
                                      (idempotency_key,)).fetchone()
                if row:
                    same = (row["owner_agent_id"], row["objective"], row["acceptance_criteria"], row["company_id"]) == \
                           (owner_agent_id, objective, acceptance_criteria, company_id)
                    if not same:
                        raise TaskError(f"Khóa '{idempotency_key}' đã dùng cho nhiệm vụ khác ({row['task_id']})")
                    self.db.execute("COMMIT")
                    return self.get(row["task_id"])
            for dep in dependencies or []:
                if self.db.execute("SELECT 1 FROM tasks WHERE task_id=?", (dep,)).fetchone() is None:
                    raise TaskError(f"Phụ thuộc '{dep}' không tồn tại")
            tid = "T-" + uuid.uuid4().hex[:10]
            state = "READY" if ready else "DRAFT"
            now = self._now()
            self.db.execute(
                """INSERT INTO tasks(task_id,parent_id,company_id,owner_agent_id,objective,acceptance_criteria,
                   dependencies,state,idempotency_key,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (tid, parent_id, company_id, owner_agent_id, objective, acceptance_criteria,
                 json.dumps(dependencies or []), state, idempotency_key, now, now),
            )
            self._event(tid, actor, None, state, "tạo nhiệm vụ")
            self.db.execute("COMMIT")
        except Exception:
            self.db.execute("ROLLBACK")
            raise
        return self.get(tid)

    # ---------- nhận việc ----------
    def claim(self, task_id: str, worker: str, ttl_seconds: int = 900) -> int:
        """Nhận việc. Trả về fencing token; worker phải gửi kèm token khi ghi."""
        self._require_member(worker, "nhận việc")
        self._tx()
        try:
            t = self.db.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone()
            if t is None:
                raise TaskError(f"Không có nhiệm vụ {task_id}")
            now = self.clock()
            expired = t["lease_expiry"] is not None and datetime.fromisoformat(t["lease_expiry"]) <= now
            if t["state"] == "READY":
                note = "nhận việc"
            elif t["state"] == "REVIEW":
                note = "nhận lại để sửa theo review"
            elif t["state"] == "RUNNING" and expired:
                note = f"nhận lại sau khi lease của {t['lease_owner']} hết hạn"
            elif t["state"] == "RUNNING":
                raise TaskError(f"{task_id} đang do {t['lease_owner']} giữ tới {to_vn(t['lease_expiry'])}")
            else:
                raise TaskError(f"{task_id} ở trạng thái {t['state']}, không nhận được")
            for dep in json.loads(t["dependencies"]):
                d = self.db.execute("SELECT state FROM tasks WHERE task_id=?", (dep,)).fetchone()
                if d is None or d["state"] != "VERIFIED_DONE":
                    raise TaskError(f"Phụ thuộc {dep} chưa VERIFIED_DONE")
            retry = t["retry_count"]
            # Tính là thử lại: sau FAILED/INTERRUPTED, nhận lại khi lease hết hạn, làm lại sau review.
            # KHÔNG tính: tiếp tục sau hết hạn mức, bị chặn, chờ duyệt (việc vẫn khỏe, chỉ tạm dừng).
            is_retry = t["pending_retry"] == 1 or t["state"] in ("REVIEW", "RUNNING")
            if is_retry:
                if retry >= MAX_RETRY:
                    raise TaskError(f"Đã thử lại {retry} lần — cần Chủ tịch/Vua quyết định")
                retry += 1
            token = t["fencing_token"] + 1
            expiry = (now + timedelta(seconds=ttl_seconds)).astimezone(timezone.utc).isoformat()
            ev_start = len(json.loads(t["evidence_refs"]))  # bằng chứng lần trước không tính cho lần này
            self.db.execute(
                """UPDATE tasks SET state='RUNNING', lease_owner=?, lease_expiry=?, fencing_token=?,
                   last_worker=?, retry_count=?, attempt_evidence_start=?, pending_retry=0, state_version=state_version+1,
                   started_at=COALESCE(started_at, ?), updated_at=? WHERE task_id=?""",
                (worker, expiry, token, worker, retry, ev_start, self._now(), self._now(), task_id),
            )
            self.db.execute("INSERT OR IGNORE INTO task_workers(task_id, worker) VALUES (?,?)", (task_id, worker))
            self._event(task_id, worker, t["state"], "RUNNING", f"{note}; token={token}")
            self.db.execute("COMMIT")
            return token
        except Exception:
            self.db.execute("ROLLBACK")
            raise

    def _check_lease(self, t, worker: str, token: int):
        if t["lease_owner"] != worker or t["fencing_token"] != token:
            raise TaskError(f"{worker} không còn giữ {t['task_id']} (token cũ/khác người) — từ chối ghi")
        if datetime.fromisoformat(t["lease_expiry"]) <= self.clock():
            raise TaskError(f"Lease của {worker} trên {t['task_id']} đã hết hạn — từ chối ghi")

    # ---------- ghi trong lúc làm ----------
    def record(self, task_id: str, worker: str, token: int, *, artifact: str | None = None,
               evidence: str | None = None, next_action: str | None = None) -> None:
        self._tx()
        try:
            t = self.db.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone()
            if t is None:
                raise TaskError(f"Không có nhiệm vụ {task_id}")
            if t["state"] != "RUNNING":
                raise TaskError(f"{task_id} không ở RUNNING")
            self._check_lease(t, worker, token)
            arts, evs = json.loads(t["artifact_refs"]), json.loads(t["evidence_refs"])
            if artifact:
                arts.append(artifact)
            if evidence:
                evs.append(evidence)
            self.db.execute(
                "UPDATE tasks SET artifact_refs=?, evidence_refs=?, next_action=COALESCE(?, next_action), updated_at=? WHERE task_id=?",
                (json.dumps(arts), json.dumps(evs), next_action, self._now(), task_id),
            )
            self._event(task_id, worker, "RUNNING", "RUNNING",
                        "; ".join(x for x in (artifact and f"artifact={artifact}", evidence and f"evidence={evidence}",
                                              next_action and f"next={next_action}") if x))
            self.db.execute("COMMIT")
        except Exception:
            self.db.execute("ROLLBACK")
            raise

    # ---------- chuyển trạng thái ----------
    def move(self, task_id: str, to_state: str, actor: str, *, token: int | None = None, note: str = "",
             approval: str | None = None) -> dict:
        if to_state not in STATES:
            raise TaskError(f"Trạng thái '{to_state}' không tồn tại")
        if to_state == "RUNNING":
            raise TaskError("Dùng claim() để vào RUNNING")
        self._require_member(actor, f"chuyển sang {to_state}")
        if to_state == "VERIFIED_DONE" and self.registry is None:
            raise TaskError("Duyệt VERIFIED_DONE cần sổ đăng ký để kiểm thẩm quyền")
        self._tx()
        try:
            t = self.db.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone()
            if t is None:
                raise TaskError(f"Không có nhiệm vụ {task_id}")
            frm = t["state"]
            if to_state not in TRANSITIONS[frm]:
                raise TaskError(f"Không được chuyển {frm} → {to_state}")
            if frm == "RUNNING":
                if token is None:
                    raise TaskError("Rời RUNNING phải kèm fencing token của người đang giữ")
                self._check_lease(t, actor, token)
            elif self.registry is not None and to_state != "VERIFIED_DONE" and frm != "WAITING_APPROVAL":
                # Hủy / tiếp tục / mở việc / đánh trượt review: chỉ chủ nhiệm vụ hoặc cấp trên (+A20 khi đánh trượt).
                allowed = self._authorities(t["owner_agent_id"]) | {t["owner_agent_id"]}
                if frm == "REVIEW":
                    allowed |= {"A20"}
                    if actor in self._workers(task_id):
                        raise TaskError(f"{actor} đã làm nhiệm vụ này — kết luận review phải do người khác")
                if actor not in allowed:
                    raise TaskError(f"{actor} không có quyền chuyển {frm} → {to_state} (được: {', '.join(sorted(allowed))})")
            if to_state == "REVIEW" and not self._current_evidence(t):
                raise TaskError("Chưa có bằng chứng của lần làm này — không được gửi REVIEW")
            workers = self._workers(task_id)
            approvals = json.loads(t["approval_refs"])
            if to_state == "VERIFIED_DONE":
                if not self._current_evidence(t):
                    raise TaskError("VERIFIED_DONE cần bằng chứng của lần làm hiện tại")
                if actor in workers:
                    raise TaskError(f"{actor} đã từng làm nhiệm vụ này — không được tự duyệt")
                allowed = self._authorities(t["owner_agent_id"]) | {"A20"}
                if actor not in allowed:
                    raise TaskError(f"{actor} không có thẩm quyền duyệt (được: {', '.join(sorted(allowed))})")
            if frm == "WAITING_APPROVAL" and to_state == "READY":
                if not approval or not approval.strip():
                    raise TaskError("Rời WAITING_APPROVAL phải ghi nội dung/mã quyết định chấp thuận")
                allowed = self._authorities(t["owner_agent_id"]) if self.registry else {"CHAIRMAN"}
                if actor in workers or actor not in allowed:
                    raise TaskError(f"{actor} không có thẩm quyền chấp thuận (được: {', '.join(sorted(allowed))})")
                approvals.append(f"{actor}: {approval.strip()}")
            if frm == "FAILED" and to_state == "READY" and t["retry_count"] >= MAX_RETRY:
                raise TaskError(f"Đã thử lại {t['retry_count']} lần — cần Chủ tịch/Vua quyết định")
            # Cờ thử lại chỉ được BẬT ở đây; chỉ claim() mới tắt (đi vòng qua BLOCKED không xóa được).
            pending = 1 if (to_state == "READY" and frm in ("FAILED", "INTERRUPTED")) else t["pending_retry"]
            self.db.execute(
                """UPDATE tasks SET state=?, state_version=state_version+1, approval_refs=?, pending_retry=?,
                   updated_at=?, lease_owner=NULL, lease_expiry=NULL WHERE task_id=?""",
                (to_state, json.dumps(approvals, ensure_ascii=False), pending, self._now(), task_id),
            )
            self._event(task_id, actor, frm, to_state, note or (approval and f"chấp thuận: {approval}") or "")
            self.db.execute("COMMIT")
        except Exception:
            self.db.execute("ROLLBACK")
            raise
        return self.get(task_id)
