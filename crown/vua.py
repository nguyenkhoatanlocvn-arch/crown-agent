"""Vương Tọa → Agent Vua: lớp sổ cho workflow agent-vua-vuong-toa.

Workflow (JavaScript) quyết định thứ tự, rẽ nhánh và điểm dừng. Mọi thao tác
đọc/ghi sổ đi qua lệnh `python -m crown vua <lệnh> --q <JSON đã urlencode>`,
in ra đúng MỘT dòng JSON. Lớp này không gọi mạng, không giữ secret.

Dựa trên sổ nhiệm vụ có sẵn (crown/tasks.py): idempotency key, lease + fencing
token, không tự duyệt, nhật ký chỉ thêm. Thêm 4 bảng:
  vua_requests  — một dòng cho mỗi request_id (chống gửi trùng)
  vua_steps     — điểm kiểm đã xác nhận (tiếp tục sau gián đoạn)
  council_usage — lượt gọi Hội đồng AI, tính cả lượt thử lại
  outbox        — thao tác ra ngoài (đồng bộ Hoàng thành) có đối soát
Dữ liệu kiểm thử tách khỏi chạy thật bằng cột mode (TEST/LIVE) và nên dùng DB
riêng (CROWN_DB).
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import unquote

from . import registry as R
from .tasks import TaskError, TaskStore, utc_now

ROOT = R.ROOT
VUONG_TOA_FILE = ROOT / "registries" / "vuong_toa.json"
COUNCIL_POLICY_FILE = ROOT / "runtime" / "council_policy.json"
# Trùng DEFAULT_POLICY trong council.js của web Hoàng thành (05/11/2026).
DEFAULT_COUNCIL_POLICY = {"session_max_calls": 12, "daily_max_calls": 40, "paid_usd_day": 0,
                          "paid_usd_session": 0, "reserve_for_conclusion": 1}
MODES = {"TEST", "LIVE"}
DOOR = {  # trạng thái hoạt động (export_office.py) → biểu tượng cửa phòng
    "WAITING": "cuộn xanh — có việc chưa nhận",
    "WORKING": "đèn cam — đang làm",
    "REVIEW": "lồng đèn vàng — chờ duyệt",
    "BLOCKED": "xích đỏ — bị chặn",
    "OFF": "phòng tối — ngủ đông",
    "IDLE": "phòng sáng, không có việc",
}
DOOR_PRIORITY = ["WORKING", "REVIEW", "BLOCKED", "WAITING", "IDLE", "OFF"]

SCHEMA = """
CREATE TABLE IF NOT EXISTS vua_requests (
  request_id TEXT PRIMARY KEY,
  root_task_id TEXT NOT NULL,
  issued_by TEXT NOT NULL,
  domain TEXT NOT NULL,
  payload_hash TEXT NOT NULL,
  payload TEXT NOT NULL,
  mode TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS vua_steps (
  request_id TEXT NOT NULL,
  step TEXT NOT NULL,
  at TEXT NOT NULL,
  detail TEXT NOT NULL DEFAULT '{}',
  PRIMARY KEY (request_id, step)
);
CREATE TRIGGER IF NOT EXISTS vua_steps_no_update BEFORE UPDATE ON vua_steps
BEGIN SELECT RAISE(ABORT, 'vua_steps chi them'); END;
CREATE TABLE IF NOT EXISTS council_usage (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  at TEXT NOT NULL, day TEXT NOT NULL, session_id TEXT NOT NULL,
  request_id TEXT, provider TEXT NOT NULL, kind TEXT NOT NULL,
  billing TEXT NOT NULL, usd REAL NOT NULL DEFAULT 0, mode TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS outbox (
  op_id TEXT PRIMARY KEY,
  request_id TEXT NOT NULL,
  target TEXT NOT NULL,
  payload TEXT NOT NULL,
  mode TEXT NOT NULL,
  status TEXT NOT NULL,
  attempts INTEGER NOT NULL DEFAULT 0,
  receipt TEXT, reason TEXT,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS run_locks (
  request_id TEXT PRIMARY KEY,
  nonce TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
"""
OUTBOX_FLOW = {  # trạng thái → được chuyển sang
    "PENDING": {"SENDING", "BLOCKED"},
    "SENDING": {"CONFIRMED", "UNKNOWN", "BLOCKED", "PENDING"},
    "UNKNOWN": {"CONFIRMED", "PENDING", "BLOCKED"},   # chỉ sau khi đối soát
    "BLOCKED": {"PENDING"},
    "CONFIRMED": set(),
}


def _canon(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class Vua:
    def __init__(self, db_path: str | Path, registry: R.Registry | None = None, clock=utc_now):
        self.reg = registry or R.load()
        self.store = TaskStore(db_path, registry=self.reg, clock=clock)
        self.db = self.store.db
        self.clock = clock
        self.db.executescript(SCHEMA)
        self.vt = json.loads(VUONG_TOA_FILE.read_text(encoding="utf-8"))

    def close(self):
        self.store.close()

    def _now(self) -> str:
        return self.clock().astimezone(timezone.utc).isoformat()

    # ---------- quyền Vương Tọa ----------
    def authorize(self, issued_by: str, domain: str) -> None:
        p = self.vt["principals"].get(issued_by)
        if p is None:
            raise TaskError(f"'{issued_by}' không có quyền đặt mục tiêu tại Vương Tọa")
        if domain not in self.vt["domains"]:
            raise TaskError(f"Phạm vi '{domain}' không có trong danh mục Vương Tọa")
        if domain in self.vt["chairman_only_domains"] and issued_by != "CHAIRMAN":
            raise TaskError(f"Phạm vi {domain} chỉ Chủ tịch giao")
        if "*" not in p["scopes"] and domain not in p["scopes"]:
            raise TaskError(f"{issued_by} chưa được ủy nhiệm phạm vi {domain}")

    # ---------- điểm kiểm ----------
    def steps(self, request_id: str) -> dict:
        return {r["step"]: json.loads(r["detail"]) for r in self.db.execute(
            "SELECT step, detail FROM vua_steps WHERE request_id=? ORDER BY at", (request_id,))}

    def step(self, request_id: str, step: str, detail: dict | None = None) -> dict:
        """Ghi điểm kiểm đã xác nhận. Ghi lại cùng tên → giữ bản đầu (không sửa lịch sử)."""
        self._req(request_id)
        self.db.execute("INSERT OR IGNORE INTO vua_steps(request_id, step, at, detail) VALUES (?,?,?,?)",
                        (request_id, step, self._now(), _canon(detail or {})))
        return {"step": step, "detail": self.steps(request_id)[step]}

    def _req(self, request_id: str):
        row = self.db.execute("SELECT * FROM vua_requests WHERE request_id=?", (request_id,)).fetchone()
        if row is None:
            raise TaskError(f"Không có request {request_id}")
        return row

    # ---------- nhận lệnh ----------
    def intake(self, *, request_id: str, objective: str, scope: dict, acceptance: list, issued_by: str,
               mode: str = "LIVE") -> dict:
        if not request_id or not request_id.strip():
            raise TaskError("Thiếu request_id")
        if mode not in MODES:
            raise TaskError(f"mode phải là {sorted(MODES)}")
        if not acceptance:
            raise TaskError("Thiếu tiêu chí nghiệm thu")
        domain = (scope or {}).get("domain", "")
        self.authorize(issued_by, domain)
        payload = {"objective": objective, "scope": scope, "acceptance": acceptance, "issued_by": issued_by, "mode": mode}
        h = hashlib.sha256(_canon(payload).encode()).hexdigest()
        old = self.db.execute("SELECT * FROM vua_requests WHERE request_id=?", (request_id,)).fetchone()
        if old is not None:
            if old["payload_hash"] != h:
                raise TaskError(f"request_id {request_id} đã dùng cho nội dung khác ({old['root_task_id']})")
            return {"duplicate": True, **self.status(request_id)}
        # Sổ nhiệm vụ chống trùng bằng idempotency key (BEGIN IMMEDIATE) → gửi đồng thời chỉ ra 1 nhiệm vụ.
        root = self.store.create(owner_agent_id="VUA_CEO", objective=objective,
                                 acceptance_criteria=" | ".join(acceptance), actor="VUA_CEO",
                                 idempotency_key=request_id, ready=True)
        cur = self.db.execute(
            "INSERT OR IGNORE INTO vua_requests VALUES (?,?,?,?,?,?,?,?)",
            (request_id, root["task_id"], issued_by, domain, h, _canon(payload), mode, self._now()))
        won = cur.rowcount == 1
        row = self._req(request_id)
        if row["payload_hash"] != h:
            raise TaskError(f"request_id {request_id} đã dùng cho nội dung khác")
        if won:
            self.step(request_id, "intake", {"root": root["task_id"], "issued_by": issued_by})
        return {"duplicate": not won, **self.status(request_id)}

    # ---------- lập kế hoạch ----------
    def plan(self, *, request_id: str, subtasks: list[dict]) -> dict:
        req = self._req(request_id)
        done = self.steps(request_id).get("plan")
        if done:
            return {"reused": True, "map": done["map"]}
        keys = [s.get("key") for s in subtasks]
        if not subtasks or len(set(keys)) != len(keys) or not all(keys):
            raise TaskError("Kế hoạch phải có việc con với key riêng, không trùng")
        for s in subtasks:
            for f in ("owner", "objective", "output", "acceptance"):
                if not str(s.get(f, "")).strip():
                    raise TaskError(f"Việc {s['key']} thiếu '{f}' (mỗi việc một người phụ trách, đầu ra, tiêu chí đạt)")
            m = self.reg.member(s["owner"])  # KeyError → không có trong sổ đăng ký
            if m.get("status") != "ACTIVE":
                raise TaskError(f"{s['owner']} chưa kích hoạt — không giao được")
            for d in s.get("depends_on", []):
                if d not in keys:
                    raise TaskError(f"Việc {s['key']} phụ thuộc '{d}' không có trong kế hoạch")
        order = self._topo(subtasks)
        mp: dict[str, str] = {}
        for k in order:
            s = next(x for x in subtasks if x["key"] == k)
            t = self.store.create(owner_agent_id=s["owner"], objective=s["objective"],
                                  acceptance_criteria=f"Đầu ra: {s['output']} | Đạt khi: {s['acceptance']}",
                                  actor="VUA_CEO", parent_id=req["root_task_id"],
                                  dependencies=[mp[d] for d in s.get("depends_on", [])],
                                  idempotency_key=f"{request_id}:{k}", ready=True)
            mp[k] = t["task_id"]
        self.step(request_id, "plan", {"map": mp, "subtasks": subtasks})
        return {"reused": False, "map": mp}

    @staticmethod
    def _topo(subtasks):
        deps = {s["key"]: set(s.get("depends_on", [])) for s in subtasks}
        out = []
        while deps:
            ready = sorted(k for k, d in deps.items() if not d - set(out))
            if not ready:
                raise TaskError(f"Kế hoạch có vòng phụ thuộc: {sorted(deps)}")
            out += ready
            for k in ready:
                deps.pop(k)
        return out

    def _task_of(self, request_id: str, key: str) -> str:
        if key == "ROOT":
            return self._req(request_id)["root_task_id"]
        mp = (self.steps(request_id).get("plan") or {}).get("map", {})
        if key not in mp:
            raise TaskError(f"Không có việc '{key}' trong kế hoạch {request_id}")
        return mp[key]

    # ---------- làm việc (bọc sổ nhiệm vụ) ----------
    def claim(self, *, request_id: str, key: str, worker: str, ttl: int = 1800) -> dict:
        tid = self._task_of(request_id, key)
        t = self.store.get(tid)
        if t["state"] == "VERIFIED_DONE":
            return {"task_id": tid, "state": t["state"], "token": None, "resumed": False, "skip": True}
        lease_ok = t["lease_expiry"] and datetime.fromisoformat(t["lease_expiry"]) > self.clock()
        if t["state"] == "RUNNING" and t["lease_owner"] == worker and lease_ok:
            # Tiếp tục sau gián đoạn: cùng người, lease còn hạn → dùng lại token, không tính thử lại.
            return {"task_id": tid, "state": "RUNNING", "token": t["fencing_token"], "resumed": True, "skip": False}
        tok = self.store.claim(tid, worker, ttl)
        return {"task_id": tid, "state": "RUNNING", "token": tok, "resumed": False, "skip": False}

    def evidence(self, *, request_id: str, key: str, worker: str, token: int, evidence: list[str] | None = None,
                 artifacts: list[str] | None = None, next_action: str | None = None) -> dict:
        tid = self._task_of(request_id, key)
        evs, arts = list(evidence or []), list(artifacts or [])
        if not evs and not arts and not next_action:
            raise TaskError("Không có gì để ghi")
        n = max(len(evs), len(arts), 1)
        for i in range(n):
            self.store.record(tid, worker, token, evidence=evs[i] if i < len(evs) else None,
                              artifact=arts[i] if i < len(arts) else None,
                              next_action=next_action if i == n - 1 else None)
        return {"task_id": tid, "evidence_count": len(self.store.get(tid)["evidence_refs"])}

    def move(self, *, request_id: str, key: str, to: str, actor: str, token: int | None = None,
             note: str = "", approval: str | None = None) -> dict:
        tid = self._task_of(request_id, key)
        t = self.store.move(tid, to, actor, token=token, note=note, approval=approval)
        return {"task_id": tid, "state": t["state"]}

    def review(self, *, request_id: str, key: str, reviewer: str, verdict: str, checks: list[dict]) -> dict:
        """Kết luận của người kiểm độc lập. PASS → VERIFIED_DONE; FAIL → FAILED (sổ tự chặn tự duyệt)."""
        if verdict not in ("PASS", "FAIL"):
            raise TaskError("verdict phải là PASS hoặc FAIL")
        for c in checks:
            if not {"text", "passed", "evidence"} <= set(c):
                raise TaskError("Mỗi mục kiểm phải có text, passed, evidence")
        if verdict == "PASS" and not all(c["passed"] for c in checks):
            raise TaskError("Có mục kiểm trượt nhưng kết luận PASS — từ chối")
        if verdict == "PASS" and not checks:
            raise TaskError("PASS cần ít nhất một mục kiểm có bằng chứng")
        note = f"{verdict} bởi {reviewer}: " + "; ".join(f"[{'đạt' if c['passed'] else 'trượt'}] {c['text']}" for c in checks)
        return self.move(request_id=request_id, key=key, to="VERIFIED_DONE" if verdict == "PASS" else "FAILED",
                         actor=reviewer, note=note[:2000])

    # ---------- khóa lượt chạy: hai lượt cùng request không chạy song song ----------
    def lock(self, *, request_id: str, ttl: int = 900, nonce: str | None = None, takeover: bool = False) -> dict:
        """Giữ/gia hạn khóa lượt chạy. Lượt thứ hai cùng request_id nhận acquired=False và chỉ đọc trạng thái.
        Lượt chết giữa chừng: khóa tự hết hạn sau ttl; Vương Tọa có thể takeover=True."""
        import uuid
        self._req(request_id)
        now = self.clock()
        exp = (now + timedelta(seconds=ttl)).astimezone(timezone.utc).isoformat()
        self.db.execute("BEGIN IMMEDIATE")
        try:
            row = self.db.execute("SELECT * FROM run_locks WHERE request_id=?", (request_id,)).fetchone()
            free = row is None or datetime.fromisoformat(row["expires_at"]) <= now
            mine = row is not None and nonce is not None and row["nonce"] == nonce
            if not (free or mine or takeover):
                self.db.execute("COMMIT")
                return {"acquired": False, "held_until": row["expires_at"]}
            n = row["nonce"] if mine else uuid.uuid4().hex[:12]
            self.db.execute("INSERT OR REPLACE INTO run_locks VALUES (?,?,?,?)", (request_id, n, exp, self._now()))
            self.db.execute("COMMIT")
        except Exception:
            self.db.execute("ROLLBACK")
            raise
        return {"acquired": True, "nonce": n, "expires_at": exp, "renewed": mine,
                "took_over": bool(takeover and not free and not mine)}

    def unlock(self, *, request_id: str, nonce: str) -> dict:
        cur = self.db.execute("DELETE FROM run_locks WHERE request_id=? AND nonce=?", (request_id, nonce))
        return {"released": cur.rowcount == 1}

    # ---------- Hội đồng AI: hạn mức CFO ----------
    def council_policy(self) -> dict:
        pol = dict(DEFAULT_COUNCIL_POLICY)
        if COUNCIL_POLICY_FILE.exists():
            pol.update(json.loads(COUNCIL_POLICY_FILE.read_text(encoding="utf-8")))
            pol["source"] = str(COUNCIL_POLICY_FILE.relative_to(ROOT))
        else:
            pol["source"] = "mặc định council.js (council_cfg trống)"
        pol["session_max_calls"] = min(12, max(1, int(pol["session_max_calls"])))  # trần cứng như web
        return pol

    def council_gate(self, *, request_id: str, session_id: str, provider: str, kind: str = "call",
                     billing: str = "plan", usd: float = 0.0, connected: bool = False, mode: str = "LIVE") -> dict:
        """Xin một lượt gọi Hội đồng. Lượt thử lại (kind=retry) cũng bị tính. Chừa lượt kết luận."""
        pol = self.council_policy()
        day = self.clock().astimezone(timezone.utc).date().isoformat()
        base = {"policy": pol, "provider": provider, "kind": kind}
        if not connected:
            return {**base, "ok": False, "reason": f"{provider}: chưa có kết nối thật — bỏ qua, làm phần cục bộ"}
        self.db.execute("BEGIN IMMEDIATE")
        try:
            q = "SELECT COUNT(*), COALESCE(SUM(usd),0) FROM council_usage WHERE mode=? AND "
            d_calls, d_usd = self.db.execute(q + "day=?", (mode, day)).fetchone()
            s_calls, s_usd = self.db.execute(q + "session_id=?", (mode, session_id)).fetchone()
            reserve = 0 if kind == "conclusion" else int(pol["reserve_for_conclusion"])
            why = None
            if s_calls + 1 > pol["session_max_calls"] - reserve:
                why = f"Hết hạn mức phiên: {s_calls}/{pol['session_max_calls']} (chừa {reserve} lượt kết luận)"
            elif d_calls + 1 > int(pol["daily_max_calls"]) - reserve:
                why = f"Hết hạn mức ngày: {d_calls}/{pol['daily_max_calls']} (chừa {reserve} lượt kết luận)"
            elif billing == "paid" and (float(pol["paid_usd_day"]) <= 0 or d_usd + usd > float(pol["paid_usd_day"])):
                why = f"Gọi trả phí không được phép: trần {pol['paid_usd_day']} USD/ngày"
            if why:
                self.db.execute("COMMIT")
                return {**base, "ok": False, "reason": why, "session_used": s_calls, "day_used": d_calls}
            self.db.execute(
                "INSERT INTO council_usage(at, day, session_id, request_id, provider, kind, billing, usd, mode) VALUES (?,?,?,?,?,?,?,?,?)",
                (self._now(), day, session_id, request_id, provider, kind, billing, usd, mode))
            self.db.execute("COMMIT")
        except Exception:
            self.db.execute("ROLLBACK")
            raise
        return {**base, "ok": True, "session_used": s_calls + 1, "day_used": d_calls + 1}

    # ---------- thao tác ra ngoài có đối soát ----------
    def outbox_put(self, *, request_id: str, op_id: str, target: str, payload: dict) -> dict:
        req = self._req(request_id)
        if req["mode"] == "TEST" and target != "local":
            raise TaskError("Dữ liệu kiểm thử không được đẩy lên Hoàng thành thật (target phải là 'local')")
        self.db.execute("INSERT OR IGNORE INTO outbox VALUES (?,?,?,?,?,?,?,?,?,?)",
                        (op_id, request_id, target, _canon(payload), req["mode"], "PENDING", 0, None, None, self._now()))
        row = self._outbox(op_id)
        if row["payload"] != _canon(payload):
            raise TaskError(f"op_id {op_id} đã dùng cho nội dung khác")
        return self._ob(row)

    def outbox_mark(self, *, op_id: str, status: str, receipt: str | None = None, reason: str | None = None) -> dict:
        self.db.execute("BEGIN IMMEDIATE")
        try:
            row = self._outbox(op_id)
            if status not in OUTBOX_FLOW[row["status"]]:
                raise TaskError(f"outbox {op_id}: không được {row['status']} → {status}")
            if status == "CONFIRMED" and not receipt:
                raise TaskError("CONFIRMED cần biên nhận đọc lại từ đích")
            if status in ("BLOCKED", "UNKNOWN") and not reason:
                raise TaskError(f"{status} cần lý do")
            att = row["attempts"] + (1 if status == "SENDING" else 0)
            self.db.execute("UPDATE outbox SET status=?, attempts=?, receipt=COALESCE(?, receipt), reason=?, updated_at=? WHERE op_id=?",
                            (status, att, receipt, reason, self._now(), op_id))
            self.db.execute("COMMIT")
        except Exception:
            self.db.execute("ROLLBACK")
            raise
        return self._ob(self._outbox(op_id))

    def _outbox(self, op_id):
        row = self.db.execute("SELECT * FROM outbox WHERE op_id=?", (op_id,)).fetchone()
        if row is None:
            raise TaskError(f"Không có outbox {op_id}")
        return row

    @staticmethod
    def _ob(row) -> dict:
        d = dict(row)
        d["payload"] = json.loads(d["payload"])
        d["needs_reconcile"] = d["status"] in ("SENDING", "UNKNOWN")  # chưa rõ đã tới đích → đối soát trước khi gửi lại
        return d

    # ---------- tra cứu ----------
    def _door(self, mid: str, tasks: list[dict]) -> str:
        now = self.clock()
        found = []
        for t in tasks:
            lease_ok = t["lease_expiry"] and datetime.fromisoformat(t["lease_expiry"]) > now
            if t["state"] == "RUNNING" and t["lease_owner"] == mid and lease_ok:
                found.append("WORKING")
            elif t["owner_agent_id"] == mid:
                if t["state"] in ("REVIEW", "WAITING_APPROVAL"):   # chờ duyệt → lồng đèn vàng
                    found.append("REVIEW")
                elif t["state"] in ("BLOCKED", "PAUSED_QUOTA", "INTERRUPTED"):
                    found.append("BLOCKED")
                elif t["state"] in ("DRAFT", "READY"):
                    found.append("WAITING")
        if not found:
            return "IDLE" if self.reg.member(mid).get("status") == "ACTIVE" else "OFF"
        return min(found, key=DOOR_PRIORITY.index)

    def doors(self, members: list[str] | None = None) -> dict:
        tasks = self.store.list()
        ids = members or sorted(set(self.reg.leaders) | set(self.reg.agents) | set(self.reg.subagents))
        return {m: {"activity": (a := self._door(m, tasks)), "door": DOOR[a]} for m in ids}

    def status(self, request_id: str) -> dict:
        req = self._req(request_id)
        root = self.store.get(req["root_task_id"])
        steps = self.steps(request_id)
        mp = (steps.get("plan") or {}).get("map", {})
        children = {}
        for k, tid in mp.items():
            t = self.store.get(tid)
            children[k] = {"task_id": tid, "owner": t["owner_agent_id"], "state": t["state"],
                           "lease_owner": t["lease_owner"], "retry_count": t["retry_count"],
                           "evidence": t["evidence_refs"], "artifacts": t["artifact_refs"],
                           "attempt_evidence": t["evidence_refs"][t["attempt_evidence_start"]:],
                           "approvals": t["approval_refs"], "next_action": t["next_action"],
                           "last_event": (self.store.events(tid) or [{}])[-1]}
        outbox = [self._ob(r) for r in self.db.execute("SELECT * FROM outbox WHERE request_id=? ORDER BY op_id", (request_id,))]
        involved = sorted({"VUA_CEO"} | {c["owner"] for c in children.values()})
        return {"request_id": request_id, "mode": req["mode"], "issued_by": req["issued_by"], "domain": req["domain"],
                "root": {"task_id": root["task_id"], "state": root["state"], "evidence": root["evidence_refs"],
                         "attempt_evidence": root["evidence_refs"][root["attempt_evidence_start"]:]},
                "children": children, "steps": sorted(steps), "outbox": outbox,
                "doors": self.doors(involved),
                "done": root["state"] == "VERIFIED_DONE"}

    def lookup(self, code: str) -> dict:
        """Tra theo request_id hoặc mã nhiệm vụ T-xxxx — kể cả khi phòng đã về ngủ đông."""
        row = self.db.execute("SELECT request_id FROM vua_requests WHERE request_id=? OR root_task_id=?", (code, code)).fetchone()
        if row:
            return self.status(row["request_id"])
        t = self.store.get(code)
        return {"task_id": code, "state": t["state"], "owner": t["owner_agent_id"], "objective": t["objective"],
                "evidence": t["evidence_refs"], "artifacts": t["artifact_refs"],
                "events": self.store.events(code)}


# ---------- CLI: python -m crown vua <lệnh> --q <urlencoded JSON> ----------
COMMANDS = {
    "intake": lambda v, a: v.intake(**a),
    "plan": lambda v, a: v.plan(**a),
    "claim": lambda v, a: v.claim(**a),
    "evidence": lambda v, a: v.evidence(**a),
    "move": lambda v, a: v.move(**a),
    "review": lambda v, a: v.review(**a),
    "step": lambda v, a: v.step(**a),
    "status": lambda v, a: v.status(**a),
    "lookup": lambda v, a: v.lookup(**a),
    "doors": lambda v, a: v.doors(**a),
    "council": lambda v, a: v.council_gate(**a),
    "policy": lambda v, a: v.council_policy(),
    "lock": lambda v, a: v.lock(**a),
    "unlock": lambda v, a: v.unlock(**a),
    "outbox_put": lambda v, a: v.outbox_put(**a),
    "outbox_mark": lambda v, a: v.outbox_mark(**a),
}


def db_path() -> Path:
    p = Path(os.environ.get("CROWN_DB", ROOT / "runtime" / "tasks.db"))
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def main(argv: list[str]) -> int:
    if not argv or argv[0] not in COMMANDS:
        print(json.dumps({"ok": False, "error": f"lệnh phải là một trong {sorted(COMMANDS)}"}, ensure_ascii=False))
        return 2
    cmd, rest = argv[0], argv[1:]
    raw = "{}"
    if rest[:1] == ["--q"] and len(rest) > 1:
        raw = unquote(rest[1])
    elif rest[:1] == ["--json"] and len(rest) > 1:
        raw = rest[1]
    try:
        args = json.loads(raw)
        v = Vua(db_path())
        try:
            out = COMMANDS[cmd](v, args)
        finally:
            v.close()
        print(json.dumps({"ok": True, "cmd": cmd, "result": out, "db": str(db_path())}, ensure_ascii=False, default=str))
        return 0
    except (TaskError, KeyError, TypeError, ValueError, sqlite3.Error) as e:  # luôn trả JSON, không im lặng
        msg = f"không có trong sổ đăng ký: {e}" if isinstance(e, KeyError) else str(e)
        print(json.dumps({"ok": False, "cmd": cmd, "error": msg}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
