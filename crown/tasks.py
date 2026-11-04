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
        self._tx()
        try:
            if idempotency_key:
                row = self.db.execute("SELECT task_id FROM tasks WHERE idempotency_key=?",
                                      (idempotency_key,)).fetchone()
                if row:
                    self.db.execute("COMMIT")
                    return self.get(row["task_id"])
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
            token = t["fencing_token"] + 1
            expiry = (now + timedelta(seconds=ttl_seconds)).astimezone(timezone.utc).isoformat()
            self.db.execute(
                """UPDATE tasks SET state='RUNNING', lease_owner=?, lease_expiry=?, fencing_token=?,
                   last_worker=?, state_version=state_version+1, started_at=COALESCE(started_at, ?),
                   updated_at=? WHERE task_id=?""",
                (worker, expiry, token, worker, self._now(), self._now(), task_id),
            )
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
    def move(self, task_id: str, to_state: str, actor: str, *, token: int | None = None, note: str = "") -> dict:
        if to_state not in STATES:
            raise TaskError(f"Trạng thái '{to_state}' không tồn tại")
        if to_state == "RUNNING":
            raise TaskError("Dùng claim() để vào RUNNING")
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
            if to_state == "REVIEW" and not json.loads(t["evidence_refs"]):
                raise TaskError("Chưa có bằng chứng — không được gửi REVIEW")
            if to_state == "VERIFIED_DONE":
                if not json.loads(t["evidence_refs"]):
                    raise TaskError("VERIFIED_DONE cần bằng chứng")
                if actor == t["last_worker"]:
                    raise TaskError("Người làm không được tự duyệt VERIFIED_DONE — cần người kiểm khác")
            retry = t["retry_count"]
            if frm == "FAILED" and to_state == "READY":
                if retry >= MAX_RETRY:
                    raise TaskError(f"Đã thử lại {retry} lần — cần Chủ tịch/Vua quyết định")
                retry += 1
            release = to_state != "RUNNING"
            self.db.execute(
                f"""UPDATE tasks SET state=?, state_version=state_version+1, retry_count=?, updated_at=?
                    {', lease_owner=NULL, lease_expiry=NULL' if release else ''} WHERE task_id=?""",
                (to_state, retry, self._now(), task_id),
            )
            self._event(task_id, actor, frm, to_state, note)
            self.db.execute("COMMIT")
        except Exception:
            self.db.execute("ROLLBACK")
            raise
        return self.get(task_id)
