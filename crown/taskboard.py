"""Bảng nhiệm vụ của Thiên Não (D05 — lệnh DRAGON SINGULARITY mục 07).

Không tạo sổ thứ hai: nhiệm vụ vẫn nằm trong sổ `tasks` (crown/tasks.py — khóa nhận
việc, fencing token, không tự duyệt, nhật ký chỉ thêm). Bảng này chỉ thêm phần sổ cũ
chưa có (mô hình được giao, ưu tiên, rủi ro, chi phí, hạn, điểm chất lượng, phê duyệt)
và trình bày đúng 17 trường + 11 trạng thái của lệnh.

Ánh xạ trạng thái (sổ → lệnh):
  DRAFT → BACKLOG · READY → PLANNED / ASSIGNED (đã giao mô hình) / RETRY_PENDING (chờ làm lại)
  RUNNING → RUNNING · REVIEW → TESTING (chưa có điểm kiểm) / REVIEW (đã có, chờ người kiểm độc lập)
  VERIFIED_DONE → COMPLETED · BLOCKED, WAITING_APPROVAL, PAUSED_QUOTA, INTERRUPTED → BLOCKED
  FAILED → RETRY_PENDING (còn lượt) / FAILED (hết lượt) · CANCELLED → CANCELLED
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from . import policy as P
from . import registry as R
from .tasks import MAX_RETRY, TaskError, TaskStore, utc_now

PRIORITIES = ("P0", "P1", "P2", "P3")
RISKS = ("LOW", "MEDIUM", "HIGH", "CRITICAL")
WORK_TYPES = ("LOG_TRIAGE", "PERIODIC_CHECK", "RESEARCH", "CODE", "ARCHITECTURE", "SECURITY_AUDIT", "MAJOR_UPGRADE")
SPEC_STATUSES = ("BACKLOG", "PLANNED", "ASSIGNED", "RUNNING", "TESTING", "REVIEW", "COMPLETED",
                 "BLOCKED", "FAILED", "RETRY_PENDING", "CANCELLED")
TASK_BACKOFF_SECONDS = 7200          # 2 giờ = một mốc lịch; gấp đôi mỗi lần thử lại
TASK_BACKOFF_CAP_SECONDS = 86400

SCHEMA = """
CREATE TABLE IF NOT EXISTS tn_task_meta (
  task_id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  project TEXT NOT NULL,
  seat TEXT,
  work_type TEXT NOT NULL,
  priority TEXT NOT NULL,
  risk_level TEXT NOT NULL,
  action_class TEXT NOT NULL,
  assigned_model TEXT,
  model_tier TEXT,
  estimated_cost REAL NOT NULL DEFAULT 0,
  actual_cost REAL NOT NULL DEFAULT 0,
  deadline TEXT,
  quality_score REAL,
  approval_id TEXT,
  dedupe_key TEXT NOT NULL UNIQUE,
  created_run TEXT,
  updated_at TEXT NOT NULL
);
"""


class BoardError(Exception):
    """Thao tác bảng nhiệm vụ bị từ chối."""


class TaskBoard:
    def __init__(self, db_path: str | Path, registry: R.Registry | None = None, policy: P.Policy | None = None,
                 clock: Callable[[], datetime] = utc_now):
        self.reg = registry or R.load()
        self.clock = clock
        self.policy = policy or P.Policy.load(self.reg, clock=clock)
        self.store = TaskStore(db_path, registry=self.reg, clock=clock)
        self.db = self.store.db
        self.db.executescript(SCHEMA)

    def close(self) -> None:
        self.store.close()

    def _now(self) -> str:
        return self.clock().astimezone(timezone.utc).isoformat()

    def _meta(self, task_id: str) -> dict:
        row = self.db.execute("SELECT * FROM tn_task_meta WHERE task_id=?", (task_id,)).fetchone()
        if row is None:
            raise BoardError(f"{task_id} không có trên bảng Thiên Não")
        return dict(row)

    # ---------- tạo (không trùng) ----------
    def create(self, *, name: str, objective: str, acceptance: str, project: str, priority: str,
               risk_level: str, work_type: str, action_class: str, dedupe_key: str, seat: str | None = None,
               owner: str | None = None, deadline: str | None = None,
               estimated_cost: float = 0, dependencies: list[str] | None = None, actor: str = "THIEN_NAO",
               run_id: str | None = None) -> dict:
        """Việc của Agent đi qua ghế TN (`seat`). Việc làm bằng luật giao thẳng cho một đơn vị dưới
        Thiên Não (`owner`, ví dụ TN-ENGINE). Phải có đúng một trong hai."""
        if not name.strip() or not objective.strip() or not acceptance.strip() or not project.strip():
            raise BoardError("Nhiệm vụ phải có tên, mục tiêu, tiêu chí hoàn thành và dự án")
        if not str(dedupe_key).strip():
            raise BoardError("Thiếu khóa chống trùng (dedupe_key)")
        if priority not in PRIORITIES:
            raise BoardError(f"Ưu tiên phải thuộc {PRIORITIES}")
        if risk_level not in RISKS:
            raise BoardError(f"Mức rủi ro phải thuộc {RISKS}")
        if work_type not in WORK_TYPES:
            raise BoardError(f"Loại việc phải thuộc {WORK_TYPES}")
        if (seat is None) == (owner is None):
            raise BoardError("Phải có đúng một trong hai: ghế TN (seat) hoặc đơn vị dưới Thiên Não (owner)")
        if seat is not None and seat not in self.reg.tn_seats:
            raise BoardError(f"Ghế '{seat}' không có trong sổ Thiên Não")
        if owner is not None and owner not in self.reg.units:
            raise BoardError(f"'{owner}' không phải đơn vị dưới Thiên Não — Agent nhận việc qua ghế TN")
        if deadline is not None:
            try:
                if datetime.fromisoformat(deadline).tzinfo is None:
                    raise ValueError
            except ValueError:
                raise BoardError("Hạn phải là thời gian ISO có múi giờ") from None
        old = self.db.execute("SELECT task_id FROM tn_task_meta WHERE dedupe_key=?", (dedupe_key,)).fetchone()
        if old:
            return {"task_id": old["task_id"], "created": False}
        owner = owner if seat is None else self.reg.seat_owner(seat)
        self.policy.require(actor, "TASK_MANAGE")
        d = self.policy.decide(owner, action_class)
        if d.verdict == "DENY":                     # việc người làm không bao giờ được phép → không tạo
            raise P.PolicyError(f"DENY: {d.reason}")
        try:
            t = self.store.create(owner_agent_id=owner, objective=objective, acceptance_criteria=acceptance, actor=actor,
                                  dependencies=dependencies, idempotency_key=f"TN:{dedupe_key}", ready=False)
        except TaskError as e:
            raise BoardError(str(e)) from e
        self.db.execute(
            """INSERT OR IGNORE INTO tn_task_meta(task_id, name, project, seat, work_type, priority, risk_level, action_class,
               estimated_cost, deadline, dedupe_key, created_run, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (t["task_id"], name.strip(), project.strip(), seat, work_type, priority, risk_level, action_class,
             float(estimated_cost), deadline, dedupe_key, run_id, self._now()))
        return {"task_id": t["task_id"], "created": True}

    # ---------- phê duyệt ----------
    def approval_status(self, task: dict, meta: dict) -> str:
        if self.policy._class(meta["action_class"]) != "SENSITIVE":
            return "NOT_REQUIRED"
        ok = self.policy.decide(task["owner_agent_id"], meta["action_class"], approval_id=meta["approval_id"]).allowed
        return "APPROVED" if ok else "PENDING"

    def attach_approval(self, task_id: str, approval_id: str) -> None:
        """Gắn mã phê duyệt của Chủ tịch. Mã chỉ có hiệu lực nếu khớp registries/approvals.json."""
        self._meta(task_id)
        self.db.execute("UPDATE tn_task_meta SET approval_id=?, updated_at=? WHERE task_id=?", (approval_id, self._now(), task_id))

    # ---------- chuyển bước ----------
    def plan(self, task_id: str, actor: str = "THIEN_NAO") -> str:
        """BACKLOG → PLANNED. Việc nhạy cảm chưa có phê duyệt hợp lệ thì ở lại BACKLOG."""
        t, m = self.store.get(task_id), self._meta(task_id)
        if t["state"] != "DRAFT":
            return self.spec_status(t, m)
        if self.approval_status(t, m) == "PENDING":
            return "NEEDS_APPROVAL"
        self.store.move(task_id, "READY", actor, note="Thiên Não đưa vào kế hoạch")
        return "PLANNED"

    def assign(self, task_id: str, *, model: str, tier: str, estimated_cost: float | None = None) -> None:
        m = self._meta(task_id)
        self.db.execute("UPDATE tn_task_meta SET assigned_model=?, model_tier=?, estimated_cost=?, updated_at=? WHERE task_id=?",
                        (model, tier, m["estimated_cost"] if estimated_cost is None else float(estimated_cost), self._now(), task_id))

    def record_quality(self, task_id: str, score: float) -> None:
        if not 0 <= score <= 1:
            raise BoardError("Điểm chất lượng phải trong [0, 1]")
        self._meta(task_id)
        self.db.execute("UPDATE tn_task_meta SET quality_score=?, updated_at=? WHERE task_id=?", (float(score), self._now(), task_id))

    def record_cost(self, task_id: str, actual_cost: float) -> None:
        self._meta(task_id)
        self.db.execute("UPDATE tn_task_meta SET actual_cost=?, updated_at=? WHERE task_id=?", (float(actual_cost), self._now(), task_id))

    # ---------- trình bày theo lệnh ----------
    @staticmethod
    def spec_status(task: dict, meta: dict) -> str:
        st = task["state"]
        if st == "DRAFT":
            return "BACKLOG"
        if st == "READY":
            if task.get("pending_retry") == 1:
                return "RETRY_PENDING"
            return "ASSIGNED" if meta.get("assigned_model") else "PLANNED"
        if st == "RUNNING":
            return "RUNNING"
        if st == "REVIEW":
            return "TESTING" if meta.get("quality_score") is None else "REVIEW"
        if st == "VERIFIED_DONE":
            return "COMPLETED"
        if st == "FAILED":
            return "RETRY_PENDING" if task["retry_count"] < MAX_RETRY else "FAILED"
        if st == "CANCELLED":
            return "CANCELLED"
        return "BLOCKED"            # BLOCKED, WAITING_APPROVAL, PAUSED_QUOTA, INTERRUPTED

    def _completed_time(self, task: dict) -> str | None:
        if task["state"] != "VERIFIED_DONE":
            return None
        row = self.db.execute("SELECT at FROM events WHERE task_id=? AND to_state='VERIFIED_DONE' ORDER BY event_id DESC LIMIT 1",
                              (task["task_id"],)).fetchone()
        return row["at"] if row else None

    def row(self, task_id: str) -> dict:
        t, m = self.store.get(task_id), self._meta(task_id)
        return {
            "TASK_ID": t["task_id"], "TASK_NAME": m["name"], "PROJECT": m["project"], "OWNER_AGENT": t["owner_agent_id"],
            "ASSIGNED_MODEL": m["assigned_model"], "PRIORITY": m["priority"], "STATUS": self.spec_status(t, m),
            "DEPENDENCIES": t["dependencies"], "ESTIMATED_COST": m["estimated_cost"], "ACTUAL_COST": m["actual_cost"],
            "DEADLINE": m["deadline"], "START_TIME": t["started_at"], "COMPLETED_TIME": self._completed_time(t),
            "QUALITY_SCORE": m["quality_score"], "TEST_EVIDENCE": t["evidence_refs"], "RISK_LEVEL": m["risk_level"],
            "APPROVAL_STATUS": self.approval_status(t, m),
            # phần phụ để điều phối (không thuộc 17 trường)
            "seat": m["seat"], "work_type": m["work_type"], "action_class": m["action_class"], "model_tier": m["model_tier"],
            "ledger_state": t["state"], "retry_count": t["retry_count"], "objective": t["objective"],
            "acceptance": t["acceptance_criteria"],
        }

    def board(self, status: str | None = None) -> list[dict]:
        ids = [r["task_id"] for r in self.db.execute("SELECT task_id FROM tn_task_meta ORDER BY priority, task_id")]
        rows = [self.row(i) for i in ids]
        return [r for r in rows if status is None or r["STATUS"] == status]

    # ---------- chọn việc cho phiên ----------
    def select(self, limit: int) -> list[str]:
        """Việc sẵn sàng làm tiếp: ưu tiên cao trước, hạn gần trước; bỏ việc còn phụ thuộc chưa xong."""
        cands = []
        for r in self.db.execute("""SELECT m.task_id, m.priority, m.deadline, t.created_at, t.dependencies
                                    FROM tn_task_meta m JOIN tasks t USING(task_id) WHERE t.state='READY'"""):
            deps = json.loads(r["dependencies"])
            if all(self.store.get(d)["state"] == "VERIFIED_DONE" for d in deps):
                cands.append((PRIORITIES.index(r["priority"]), r["deadline"] or "9999", r["created_at"], r["task_id"]))
        return [c[3] for c in sorted(cands)[:max(0, limit)]]

    # ---------- dọn: thử lại có backoff, hết lượt → dead-letter, việc treo ----------
    @staticmethod
    def task_backoff_seconds(retry_count: int) -> int:
        return min(TASK_BACKOFF_CAP_SECONDS, TASK_BACKOFF_SECONDS * 2 ** max(0, retry_count))

    def sweep(self, cycle, actor: str = "THIEN_NAO") -> dict:
        now = self.clock()
        out = {"requeued": [], "dead": [], "stale": []}
        for t in self.store.list("FAILED"):
            tid = t["task_id"]
            if t["retry_count"] >= MAX_RETRY:
                cycle.dlq_put("task", tid, f"Đã thử lại {t['retry_count']} lần — cần Chủ tịch hoặc Vua quyết",
                              {"owner": t["owner_agent_id"], "objective": t["objective"][:200]})
                out["dead"].append(tid)
                continue
            due = datetime.fromisoformat(t["updated_at"]) + timedelta(seconds=self.task_backoff_seconds(t["retry_count"]))
            if now < due:
                continue
            try:
                self.store.move(tid, "READY", actor, note="Thiên Não xếp lại sau thời gian chờ")
                out["requeued"].append(tid)
            except TaskError as e:
                cycle.dlq_put("task", tid, f"Ngoài thẩm quyền của {actor} — chờ Chủ tịch: {e}",
                              {"owner": t["owner_agent_id"], "objective": t["objective"][:200]})
                out["dead"].append(tid)
        for t in self.store.list("RUNNING"):
            if t["lease_expiry"] and datetime.fromisoformat(t["lease_expiry"]) <= now:
                out["stale"].append(t["task_id"])
        return out
