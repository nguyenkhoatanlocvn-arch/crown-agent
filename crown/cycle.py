"""Bộ phiên chạy của Thiên Não (D04 — lệnh DRAGON SINGULARITY mục 06).

Một "phiên" = một lần bộ lập lịch đánh thức hệ thống tại một mốc giờ Việt Nam.
Hai làn: `rule` (không AI, 12 mốc/ngày) và `ai` (4 mốc/ngày). Cả hai dùng chung
một khóa: tại một thời điểm chỉ một phiên được ghi trạng thái.

Bảo đảm:
- Không chạy trùng: khóa trong database + fencing token; phiên cũ mất khóa thì mọi lần ghi bị từ chối.
- Chống lặp: run_id = làn + mốc giờ; mốc đã chạy xong thì không chạy lại.
- Chết giữa chừng: khóa hết hạn → phiên sau đánh dấu INTERRUPTED và tiếp từ điểm kiểm.
- Lỗi: thử lại có backoff và trần; hết lượt → dead-letter queue, chờ người quyết.
- Mốc bị bỏ: ghi MISSED (có trần số dòng), phiên hiện tại làm bù — không chạy lại từng mốc.
- Dừng khẩn cấp và cầu dao tự ngắt.

Không gọi mạng, không gọi AI. Thời gian lấy từ `clock` (UTC, có múi giờ).
"""
from __future__ import annotations

import json
import math
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from . import policy as P
from . import registry as R

LANES = ("rule", "ai")
LANE_ACTOR = {"rule": "TN-ENGINE", "ai": "THIEN_NAO"}
PHASES = ["WAKE_RECOVERY", "SYSTEM_AUDIT", "PLANNING", "MODEL_ASSIGNMENT", "DELEGATION",
          "EXECUTION", "QUALITY_CONTROL", "LEARNING", "REPORTING"]
SAFE_PHASES = ["WAKE_RECOVERY", "SYSTEM_AUDIT", "REPORTING"]
DEFAULT_SCHEDULE = {
    "timezone": "Asia/Ho_Chi_Minh", "utc_offset_hours": 7,
    "rule_lane": {"minute": 17, "every_hours": 2, "first_hour": 0},
    "ai_lane": {"times": ["01:47", "07:47", "13:47", "19:47"]},
    "catch_up_max_slots": 24, "run_max_attempts": 3, "run_backoff_seconds": 60, "run_backoff_cap_seconds": 900,
    "lock_ttl_seconds": {"rule": 900, "ai": 2400},
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS tn_runs (
  run_id TEXT PRIMARY KEY,
  lane TEXT NOT NULL,
  slot TEXT NOT NULL,
  status TEXT NOT NULL,
  mode TEXT NOT NULL DEFAULT 'NORMAL',
  attempt INTEGER NOT NULL DEFAULT 0,
  interruptions INTEGER NOT NULL DEFAULT 0,
  trigger_kind TEXT NOT NULL DEFAULT 'schedule',
  started_at TEXT,
  heartbeat_at TEXT,
  finished_at TEXT,
  summary TEXT NOT NULL DEFAULT '{}',
  error TEXT
);
CREATE TABLE IF NOT EXISTS tn_lock (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  run_id TEXT,
  lane TEXT,
  fencing INTEGER NOT NULL DEFAULT 0,
  acquired_at TEXT,
  expires_at TEXT
);
INSERT OR IGNORE INTO tn_lock(id, fencing) VALUES (1, 0);
CREATE TABLE IF NOT EXISTS tn_checkpoints (
  run_id TEXT NOT NULL,
  phase TEXT NOT NULL,
  at TEXT NOT NULL,
  detail TEXT NOT NULL DEFAULT '{}',
  PRIMARY KEY (run_id, phase)
);
CREATE TABLE IF NOT EXISTS tn_dlq (
  kind TEXT NOT NULL,
  ref TEXT NOT NULL,
  reason TEXT NOT NULL,
  payload TEXT NOT NULL DEFAULT '{}',
  at TEXT NOT NULL,
  resolved_at TEXT,
  resolved_by TEXT,
  resolution TEXT,
  PRIMARY KEY (kind, ref)
);
CREATE TABLE IF NOT EXISTS tn_health (
  run_id TEXT NOT NULL,
  check_id TEXT NOT NULL,
  status TEXT NOT NULL,
  evidence TEXT NOT NULL DEFAULT '',
  at TEXT NOT NULL,
  PRIMARY KEY (run_id, check_id)
);
CREATE TABLE IF NOT EXISTS tn_lessons (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  at TEXT NOT NULL,
  run_id TEXT,
  kind TEXT NOT NULL,
  source TEXT NOT NULL,
  confidence REAL NOT NULL,
  version TEXT NOT NULL,
  text TEXT NOT NULL,
  dedupe_key TEXT UNIQUE
);
CREATE TABLE IF NOT EXISTS tn_control (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  updated_by TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tn_usage (
  day TEXT NOT NULL,
  key TEXT NOT NULL,
  used INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (day, key)
);
"""


class CycleError(Exception):
    """Thao tác phiên chạy bị từ chối."""


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


# ---------- mốc giờ ----------
def _tz(cfg: dict) -> timezone:
    return timezone(timedelta(hours=cfg.get("utc_offset_hours", 7)))


def _aware(now: datetime) -> datetime:
    if now.tzinfo is None or now.utcoffset() is None:
        raise CycleError("Thời gian phải có múi giờ (không dùng datetime trần)")
    return now


def _day_slots(lane: str, day: datetime, cfg: dict) -> list[datetime]:
    """Các mốc của một ngày theo giờ Việt Nam, tăng dần."""
    base = day.replace(hour=0, minute=0, second=0, microsecond=0)
    if lane == "rule":
        r = cfg["rule_lane"]
        hours = range(r.get("first_hour", 0) % r["every_hours"], 24, r["every_hours"])
        return [base.replace(hour=h, minute=r["minute"]) for h in hours]
    if lane == "ai":
        out = []
        for t in cfg["ai_lane"]["times"]:
            h, m = t.split(":")
            out.append(base.replace(hour=int(h), minute=int(m)))
        return sorted(out)
    raise CycleError(f"Làn '{lane}' không tồn tại (chỉ có {LANES})")


def current_slot(lane: str, now: datetime, cfg: dict) -> datetime:
    """Mốc gần nhất không muộn hơn `now`, tính theo giờ Việt Nam dù máy chạy ở múi giờ nào."""
    local = _aware(now).astimezone(_tz(cfg))
    for back in (0, 1):
        cands = [s for s in _day_slots(lane, local - timedelta(days=back), cfg) if s <= local]
        if cands:
            return cands[-1]
    raise CycleError(f"Không tìm được mốc cho làn {lane}")


def slots_between(lane: str, after: datetime, until: datetime, cfg: dict) -> list[datetime]:
    """Các mốc trong khoảng (after, until]."""
    a, b = _aware(after).astimezone(_tz(cfg)), _aware(until).astimezone(_tz(cfg))
    out, day = [], a
    while day.date() <= b.date():
        out += [s for s in _day_slots(lane, day, cfg) if a < s <= b]
        day += timedelta(days=1)
    return out


def slot_id(slot: datetime) -> str:
    return slot.strftime("%Y-%m-%dT%H:%M") + "+07:00" if slot.utcoffset() == timedelta(hours=7) else slot.isoformat(timespec="minutes")


def parse_slot(sid: str) -> datetime:
    return datetime.fromisoformat(sid)


def backoff_seconds(attempt: int, cfg: dict) -> int:
    """Thời gian chờ sau lần thử thứ `attempt` thất bại: gấp đôi mỗi lần, có trần."""
    return min(cfg["run_backoff_cap_seconds"], cfg["run_backoff_seconds"] * 2 ** max(0, attempt - 1))


def wilson(successes: int, n: int, z: float = 1.96) -> tuple[float, float] | None:
    """Khoảng tin cậy Wilson 95% cho tỷ lệ thành công. None nếu chưa có mẫu."""
    if n <= 0:
        return None
    p = successes / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    lo = 0.0 if successes == 0 else max(0.0, center - half)
    hi = 1.0 if successes == n else min(1.0, center + half)
    return (lo, hi)


class Cycle:
    def __init__(self, db_path: str | Path, registry: R.Registry | None = None, policy: P.Policy | None = None,
                 clock: Callable[[], datetime] = _utc_now, config: dict | None = None):
        self.reg = registry or R.load()
        self.clock = clock
        self.policy = policy or P.Policy.load(self.reg, clock=clock)
        tn = self.reg.tn_config
        self.cfg = {**DEFAULT_SCHEDULE, **tn.get("schedule", {}), **(config or {})}
        self.breaker_threshold = int(tn.get("breaker", {}).get("open_after_consecutive_failures", 3))
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(db_path), isolation_level=None, timeout=10, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(SCHEMA)

    def close(self) -> None:
        self.db.close()

    # ---------- tiện ích ----------
    def _now(self) -> datetime:
        return _aware(self.clock()).astimezone(timezone.utc)

    @staticmethod
    def _iso(dt: datetime) -> str:
        return dt.astimezone(timezone.utc).isoformat()

    def _control(self, key: str, default):
        row = self.db.execute("SELECT value FROM tn_control WHERE key=?", (key,)).fetchone()
        return json.loads(row["value"]) if row else default

    def _set_control(self, key: str, value, by: str) -> None:
        self.db.execute("INSERT OR REPLACE INTO tn_control(key, value, updated_at, updated_by) VALUES (?,?,?,?)",
                        (key, json.dumps(value, ensure_ascii=False, sort_keys=True), self._iso(self._now()), by))

    @staticmethod
    def _row(row) -> dict:
        d = dict(row)
        d["summary"] = json.loads(d["summary"])
        return d

    def run(self, run_id: str) -> dict:
        row = self.db.execute("SELECT * FROM tn_runs WHERE run_id=?", (run_id,)).fetchone()
        if row is None:
            raise CycleError(f"Không có phiên {run_id}")
        return self._row(row)

    def runs(self, lane: str | None = None) -> list[dict]:
        q, args = "SELECT * FROM tn_runs", ()
        if lane:
            q, args = q + " WHERE lane=?", (lane,)
        return [self._row(r) for r in self.db.execute(q + " ORDER BY slot, lane", args)]

    def lock(self) -> dict:
        return dict(self.db.execute("SELECT * FROM tn_lock WHERE id=1").fetchone())

    # ---------- dừng khẩn cấp ----------
    def estop(self) -> dict:
        return self._control("estop", {"active": False})

    def estop_set(self, actor: str, reason: str) -> dict:
        self.policy.require(actor, "EMERGENCY_STOP_SET")
        if not reason.strip():
            raise CycleError("Dừng khẩn cấp phải ghi lý do")
        v = {"active": True, "by": actor, "at": self._iso(self._now()), "reason": reason.strip()}
        self._set_control("estop", v, actor)
        return v

    def estop_clear(self, actor: str) -> dict:
        self.policy.require(actor, "EMERGENCY_STOP_CLEAR")       # chỉ Chủ tịch
        v = {"active": False, "cleared_by": actor, "at": self._iso(self._now())}
        self._set_control("estop", v, actor)
        return v

    # ---------- cầu dao ----------
    def breaker(self) -> dict:
        return self._control("breaker", {"state": "CLOSED", "failures": 0})

    def _breaker_after(self, status: str, mode: str, summary: dict) -> None:
        b = self.breaker()
        state, failures = b["state"], int(b["failures"])
        if status == "FAILED":
            failures += 1
            if state == "HALF_OPEN" or failures >= self.breaker_threshold:
                state = "OPEN"
        elif mode == "SAFE":
            state = "HALF_OPEN" if summary.get("audit_ok") is True else "OPEN"
        else:
            state, failures = "CLOSED", 0
        self._set_control("breaker", {"state": state, "failures": failures}, "TN-ENGINE")

    # ---------- khóa ----------
    def _check_lock(self, run_id: str, token: int) -> None:
        lk = self.lock()
        if lk["run_id"] != run_id or lk["fencing"] != token:
            raise CycleError(f"Phiên {run_id} không còn giữ khóa (token cũ hoặc phiên khác đã nhận) — từ chối ghi")
        if datetime.fromisoformat(lk["expires_at"]) <= self._now():
            raise CycleError(f"Khóa của phiên {run_id} đã hết hạn — từ chối ghi")

    def _ttl(self, lane: str) -> timedelta:
        return timedelta(seconds=int(self.cfg["lock_ttl_seconds"][lane]))

    # ---------- bắt đầu phiên ----------
    def begin(self, lane: str, trigger: str = "schedule") -> dict:
        if lane not in LANES:
            raise CycleError(f"Làn '{lane}' không tồn tại (chỉ có {LANES})")
        actor = LANE_ACTOR[lane]
        now = self._now()
        slot = current_slot(lane, now, self.cfg)
        sid = slot_id(slot)
        run_id = f"{lane}-{sid}"
        out = {"action": "SKIP", "reason": None, "persist": False, "run_id": run_id, "slot": sid, "lane": lane,
               "attempt": 0, "token": None, "mode": "NORMAL", "done_phases": [], "missed": [],
               "missed_not_recorded": 0, "recovered": []}
        self.db.execute("BEGIN IMMEDIATE")
        try:
            # 1. Watchdog: khóa hết hạn = phiên giữ khóa đã chết.
            lk = self.lock()
            if lk["run_id"]:
                if datetime.fromisoformat(lk["expires_at"]) > now:
                    self.db.execute("COMMIT")
                    return {**out, "reason": "LOCKED", "held_by": lk["run_id"], "held_until": lk["expires_at"]}
                cur = self.db.execute(
                    """UPDATE tn_runs SET status='INTERRUPTED', interruptions=interruptions+1, finished_at=?,
                       error='mất heartbeat — watchdog đánh dấu gián đoạn' WHERE run_id=? AND status='RUNNING'""",
                    (self._iso(now), lk["run_id"]))
                if cur.rowcount:
                    out["recovered"].append(lk["run_id"])
                self.db.execute("UPDATE tn_lock SET run_id=NULL, lane=NULL, acquired_at=NULL, expires_at=NULL WHERE id=1")
                out["persist"] = True

            # 2. Dừng khẩn cấp: ghi lại là đã bỏ qua, không làm gì khác.
            estop = self.estop().get("active", False)
            row = self.db.execute("SELECT * FROM tn_runs WHERE run_id=?", (run_id,)).fetchone()
            if estop:
                if row is None:
                    self.db.execute(
                        "INSERT INTO tn_runs(run_id, lane, slot, status, trigger_kind, started_at, finished_at, error) VALUES (?,?,?,?,?,?,?,?)",
                        (run_id, lane, sid, "SKIPPED", trigger, self._iso(now), self._iso(now), "ESTOP: " + str(self.estop().get("reason", ""))))
                self.db.execute("COMMIT")
                return {**out, "reason": "ESTOP", "persist": True}
            self.policy.require(actor, "STATE_WRITE")

            # 3. Cùng mốc đã có dòng: chống lặp, thử lại có backoff và trần.
            attempt = 0
            action = "RUN"
            if row is not None:
                st, attempt = row["status"], row["attempt"]
                max_att = int(self.cfg["run_max_attempts"])
                if st == "SUCCEEDED":
                    self.db.execute("COMMIT")
                    return {**out, "reason": "DUPLICATE", "attempt": attempt}
                if st in ("FAILED", "INTERRUPTED", "RUNNING"):
                    if attempt >= max_att:
                        self._dlq_put("run", run_id, row["error"] or f"hết {max_att} lượt thử", {"slot": sid, "lane": lane})
                        self.db.execute("COMMIT")
                        return {**out, "reason": "EXHAUSTED", "attempt": attempt, "persist": True}
                    if st == "FAILED":
                        due = datetime.fromisoformat(row["finished_at"]) + timedelta(seconds=backoff_seconds(attempt, self.cfg))
                        if now < due:
                            self.db.execute("COMMIT")
                            return {**out, "reason": "BACKOFF", "attempt": attempt, "retry_at": self._iso(due)}
                    action = "RESUME"

            # 4. Nhận khóa + ghi phiên.
            b = self.breaker()
            mode = {"OPEN": "SAFE", "HALF_OPEN": "PROBE"}.get(b["state"], "NORMAL")
            token = lk["fencing"] + 1
            self.db.execute("UPDATE tn_lock SET run_id=?, lane=?, fencing=?, acquired_at=?, expires_at=? WHERE id=1",
                            (run_id, lane, token, self._iso(now), self._iso(now + self._ttl(lane))))
            if row is None:
                self.db.execute(
                    "INSERT INTO tn_runs(run_id, lane, slot, status, mode, attempt, trigger_kind, started_at, heartbeat_at) VALUES (?,?,?,?,?,?,?,?,?)",
                    (run_id, lane, sid, "RUNNING", mode, 1, trigger, self._iso(now), self._iso(now)))
            else:
                self.db.execute(
                    "UPDATE tn_runs SET status='RUNNING', mode=?, attempt=?, heartbeat_at=?, finished_at=NULL, started_at=COALESCE(started_at, ?) WHERE run_id=?",
                    (mode, attempt + 1, self._iso(now), self._iso(now), run_id))

            # 5. Mốc bị bỏ từ lần gần nhất của làn này: ghi MISSED (có trần), phiên này làm bù.
            missed, dropped = self._catch_up(lane, slot, run_id, now)
            self.db.execute("COMMIT")
        except Exception:
            self.db.execute("ROLLBACK")
            raise
        done = [r["phase"] for r in self.db.execute("SELECT phase FROM tn_checkpoints WHERE run_id=? ORDER BY at, rowid", (run_id,))]
        return {**out, "action": action, "persist": True, "attempt": attempt + 1, "token": token, "mode": mode,
                "done_phases": done, "missed": missed, "missed_not_recorded": dropped}

    def _catch_up(self, lane: str, slot: datetime, run_id: str, now: datetime) -> tuple[list[str], int]:
        last = self.db.execute("SELECT MAX(slot) AS s FROM tn_runs WHERE lane=? AND run_id<>?", (lane, run_id)).fetchone()["s"]
        if not last:
            return [], 0
        gaps = [s for s in slots_between(lane, parse_slot(last), slot, self.cfg) if s < slot]
        cap = int(self.cfg["catch_up_max_slots"])
        dropped = max(0, len(gaps) - cap)
        gaps = gaps[-cap:] if cap else []
        others = [r for r in self.db.execute(
            "SELECT run_id, started_at, finished_at FROM tn_runs WHERE lane<>? AND started_at IS NOT NULL", (lane,))]
        out = []
        for g in gaps:
            cause = "NO_RUN"
            for o in others:
                start = datetime.fromisoformat(o["started_at"])
                end = datetime.fromisoformat(o["finished_at"]) if o["finished_at"] else now
                if start <= g <= end:
                    cause = "LOCKED_BY " + o["run_id"]
                    break
            mid = f"{lane}-{slot_id(g)}"
            self.db.execute(
                "INSERT OR IGNORE INTO tn_runs(run_id, lane, slot, status, summary) VALUES (?,?,?,?,?)",
                (mid, lane, slot_id(g), "MISSED", json.dumps({"covered_by": run_id, "cause": cause}, ensure_ascii=False)))
            out.append(mid)
        return out, dropped

    # ---------- trong phiên ----------
    def heartbeat(self, run_id: str, token: int) -> None:
        self.db.execute("BEGIN IMMEDIATE")
        try:
            self._check_lock(run_id, token)
            now = self._now()
            lane = self.lock()["lane"]
            self.db.execute("UPDATE tn_lock SET expires_at=? WHERE id=1", (self._iso(now + self._ttl(lane)),))
            self.db.execute("UPDATE tn_runs SET heartbeat_at=? WHERE run_id=?", (self._iso(now), run_id))
            self.db.execute("COMMIT")
        except Exception:
            self.db.execute("ROLLBACK")
            raise

    def checkpoint(self, run_id: str, token: int, phase: str, detail: dict | None = None) -> None:
        """Ghi một pha đã xong. Ghi lại cùng pha → giữ bản đầu."""
        if phase not in PHASES:
            raise CycleError(f"Pha '{phase}' không tồn tại")
        self.db.execute("BEGIN IMMEDIATE")
        try:
            self._check_lock(run_id, token)
            self.db.execute("INSERT OR IGNORE INTO tn_checkpoints(run_id, phase, at, detail) VALUES (?,?,?,?)",
                            (run_id, phase, self._iso(self._now()), json.dumps(detail or {}, ensure_ascii=False, sort_keys=True)))
            self.db.execute("COMMIT")
        except Exception:
            self.db.execute("ROLLBACK")
            raise
        self.heartbeat(run_id, token)

    def checkpoints(self, run_id: str) -> dict:
        return {r["phase"]: json.loads(r["detail"]) for r in self.db.execute(
            "SELECT phase, detail FROM tn_checkpoints WHERE run_id=? ORDER BY at, rowid", (run_id,))}

    def finish(self, run_id: str, token: int, status: str, summary: dict | None = None, error: str | None = None) -> dict:
        if status not in ("SUCCEEDED", "FAILED"):
            raise CycleError("Kết thúc phiên phải là SUCCEEDED hoặc FAILED")
        self.db.execute("BEGIN IMMEDIATE")
        try:
            self._check_lock(run_id, token)
            row = self.run(run_id)
            merged = {**row["summary"], **(summary or {})}
            self.db.execute("UPDATE tn_runs SET status=?, finished_at=?, summary=?, error=? WHERE run_id=?",
                            (status, self._iso(self._now()), json.dumps(merged, ensure_ascii=False, sort_keys=True),
                             (error or "")[:2000] or None, run_id))
            self.db.execute("UPDATE tn_lock SET run_id=NULL, lane=NULL, acquired_at=NULL, expires_at=NULL WHERE id=1")
            self._breaker_after(status, row["mode"], merged)
            if status == "FAILED" and row["attempt"] >= int(self.cfg["run_max_attempts"]):
                self._dlq_put("run", run_id, error or "phiên thất bại", {"slot": row["slot"], "lane": row["lane"]})
            self.db.execute("COMMIT")
        except Exception:
            self.db.execute("ROLLBACK")
            raise
        return self.run(run_id)

    # ---------- dead-letter queue ----------
    def _dlq_put(self, kind: str, ref: str, reason: str, payload: dict | None = None) -> None:
        self.db.execute("INSERT OR IGNORE INTO tn_dlq(kind, ref, reason, payload, at) VALUES (?,?,?,?,?)",
                        (kind, ref, reason[:1000], json.dumps(payload or {}, ensure_ascii=False, sort_keys=True), self._iso(self._now())))

    def dlq_put(self, kind: str, ref: str, reason: str, payload: dict | None = None) -> None:
        """Đưa việc/phiên hết lượt thử vào hàng chờ người quyết. Gọi lại cùng (kind, ref) → giữ bản đầu."""
        self._dlq_put(kind, ref, reason, payload)

    def dlq_open(self) -> list[dict]:
        return [{**dict(r), "payload": json.loads(r["payload"])} for r in self.db.execute(
            "SELECT * FROM tn_dlq WHERE resolved_at IS NULL ORDER BY at, kind, ref")]

    def dlq_resolve(self, kind: str, ref: str, actor: str, note: str) -> None:
        self.policy.require(actor, "TASK_MANAGE")
        cur = self.db.execute("UPDATE tn_dlq SET resolved_at=?, resolved_by=?, resolution=? WHERE kind=? AND ref=? AND resolved_at IS NULL",
                              (self._iso(self._now()), actor, note, kind, ref))
        if cur.rowcount != 1:
            raise CycleError(f"Không có mục chờ {kind}:{ref}")

    # ---------- sức khỏe, bài học, hạn mức ----------
    def health_put(self, run_id: str, check_id: str, status: str, evidence: str = "") -> None:
        if status not in ("PASS", "FAIL", "SKIP"):
            raise CycleError("Kết quả kiểm sức khỏe phải là PASS, FAIL hoặc SKIP")
        self.db.execute("INSERT OR REPLACE INTO tn_health(run_id, check_id, status, evidence, at) VALUES (?,?,?,?,?)",
                        (run_id, check_id, status, evidence[:2000], self._iso(self._now())))

    def health(self, run_id: str) -> list[dict]:
        return [dict(r) for r in self.db.execute("SELECT * FROM tn_health WHERE run_id=? ORDER BY check_id", (run_id,))]

    def lesson_add(self, *, kind: str, text: str, source: str, confidence: float, version: str,
                   run_id: str | None = None, dedupe_key: str | None = None) -> bool:
        """Ghi một bài học/quyết định/lỗi. Mỗi bản ghi có thời gian, nguồn, mức tin cậy, phiên bản. Trả False nếu trùng."""
        if not 0 <= confidence <= 1:
            raise CycleError("Mức tin cậy phải trong [0, 1]")
        cur = self.db.execute(
            "INSERT OR IGNORE INTO tn_lessons(at, run_id, kind, source, confidence, version, text, dedupe_key) VALUES (?,?,?,?,?,?,?,?)",
            (self._iso(self._now()), run_id, kind, source, confidence, version, text[:2000], dedupe_key))
        return cur.rowcount == 1

    def lessons(self, limit: int = 20, kind: str | None = None) -> list[dict]:
        q, args = "SELECT * FROM tn_lessons", []
        if kind:
            q, args = q + " WHERE kind=?", [kind]
        return [dict(r) for r in self.db.execute(q + " ORDER BY id DESC LIMIT ?", (*args, limit))]

    def usage_take(self, day: str, key: str, cap: int, n: int = 1) -> bool:
        """Lấy `n` lượt từ hạn mức ngày. Trả False (không ghi) nếu vượt trần — dừng cứng."""
        self.db.execute("BEGIN IMMEDIATE")
        try:
            row = self.db.execute("SELECT used FROM tn_usage WHERE day=? AND key=?", (day, key)).fetchone()
            used = row["used"] if row else 0
            ok = used + n <= cap
            if ok:
                self.db.execute("INSERT OR REPLACE INTO tn_usage(day, key, used) VALUES (?,?,?)", (day, key, used + n))
            self.db.execute("COMMIT")
        except Exception:
            self.db.execute("ROLLBACK")
            raise
        return ok

    def usage(self, day: str) -> dict:
        return {r["key"]: r["used"] for r in self.db.execute("SELECT key, used FROM tn_usage WHERE day=? ORDER BY key", (day,))}

    # ---------- số đo độ tin cậy ----------
    def metrics(self) -> dict:
        out = {}
        for lane in LANES:
            rows = [r for r in self.runs(lane) if r["status"] != "SKIPPED" and r["status"] != "RUNNING"]
            n = len(rows)
            by = {s: sum(r["status"] == s for r in rows) for s in ("SUCCEEDED", "FAILED", "INTERRUPTED", "MISSED")}
            interrupted_ever = [r for r in rows if r["interruptions"] > 0]
            ci = wilson(by["SUCCEEDED"], n)
            out[lane] = {
                "slots": n, "succeeded": by["SUCCEEDED"], "failed": by["FAILED"], "interrupted": by["INTERRUPTED"],
                "missed": by["MISSED"],
                "scheduling_reliability": (by["SUCCEEDED"] / n) if n else None,
                "ci95": [round(ci[0], 6), round(ci[1], 6)] if ci else None,
                "target_9999_demonstrated": bool(ci and ci[0] >= 0.9999),
                "recovery_success_rate": (sum(r["status"] == "SUCCEEDED" for r in interrupted_ever) / len(interrupted_ever))
                                         if interrupted_ever else None,
            }
        out["dead_letter_open"] = len(self.dlq_open())
        out["breaker"] = self.breaker()
        out["estop"] = bool(self.estop().get("active"))
        return out
