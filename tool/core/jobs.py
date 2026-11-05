"""Sổ việc TOOL: mỗi mã việc một thư mục trong workbench/, có khóa ghi và điểm tiếp nối.

Luật (cùng tinh thần sổ nhiệm vụ crown/tasks.py):
- Một việc chỉ một người giữ quyền ghi tại một thời điểm: lease có hạn + fencing token.
  Người giữ cũ (token cũ hoặc lease hết hạn) bị từ chối mọi lần ghi.
- Mỗi bước xong phải có bằng chứng. Khi một Agent ngừng giữa chừng, Agent kia nhận lại
  sau khi lease hết hạn (hoặc được nhả) và làm tiếp từ bước đầu tiên chưa xong.
- Người kiểm thử không được là người viết mã (không tự duyệt).
- Bấm tạo hai lần với cùng khóa idempotency không sinh hai việc.
Trạng thái lưu thành tệp JSON để mọi phiên dùng chung qua git; nhật ký sự kiện chỉ thêm.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable

from .identity import CHAIRMAN, Directory
from .util import (ToolError, append_jsonl, atomic_write_json, iso, mutex, new_id, parse_iso, read_json,
                   read_jsonl, tool_root, utc_now)

PIPELINE: list[tuple[str, str, str]] = [
    # (mã bước, nhánh chịu trách nhiệm, việc)
    ("intake", "th", "Nhận mẫu vào inbox, băm tệp, ghi nguồn"),
    ("research", "gh", "Tìm nguồn/skill liên quan, ghi giấy phép; hoặc ghi lý do bỏ qua"),
    ("analyze", "pt", "Lấy khung hình/đọc mẫu, ghi thao tác, thay đổi màn hình, đầu vào, kết quả"),
    ("spec", "pt", "Viết đặc tả + giả định cần kiểm + dữ liệu thử"),
    ("build", "lt", "Viết công cụ từ đặc tả và dữ liệu thử"),
    ("test", "kt", "Kiểm thử độc lập trong vùng cách ly"),
    ("publish", "th", "Phát hành vào kho chung"),
    ("verify", "th", "Gọi thử từ kho chung bằng dữ liệu khác mẫu"),
]
STEP_IDS = [s[0] for s in PIPELINE]
JOB_ID_RE = re.compile(r"^TJ-\d{8}-[0-9a-f]{6}$")
MAX_ATTEMPTS = 3

TRANSITIONS: dict[str, set[str]] = {
    "READY": {"RUNNING", "CANCELLED"},
    "RUNNING": {"READY", "BLOCKED", "WAITING_APPROVAL", "FAILED", "VERIFIED_DONE", "CANCELLED"},
    "BLOCKED": {"READY", "CANCELLED"},
    "WAITING_APPROVAL": {"READY", "CANCELLED"},
    "FAILED": {"READY", "CANCELLED"},
    "VERIFIED_DONE": set(),
    "CANCELLED": set(),
}


class JobStore:
    def __init__(self, root: Path | None = None, directory: Directory | None = None,
                 clock: Callable[[], datetime] = utc_now):
        self.root = Path(root) if root else tool_root()
        self.dir = directory or Directory(self.root)
        self.clock = clock
        self.wb = self.root / "workbench"
        self.wb.mkdir(parents=True, exist_ok=True)

    # ---------- đường dẫn & đọc ----------
    def job_dir(self, job_id: str) -> Path:
        if not job_id or "/" in job_id or "\\" in job_id or job_id.startswith("."):
            raise ToolError(f"Mã việc không hợp lệ: {job_id!r}")
        return self.wb / job_id

    def _path(self, job_id: str) -> Path:
        return self.job_dir(job_id) / "job.json"

    def get(self, job_id: str) -> dict:
        p = self._path(job_id)
        if not p.is_file():
            raise ToolError(f"Không có việc {job_id}")
        return read_json(p)

    def list(self, state: str | None = None) -> list[dict]:
        out = []
        for p in sorted(self.wb.glob("*/job.json")):
            j = read_json(p)
            if state is None or j["state"] == state:
                out.append(j)
        out.sort(key=lambda j: (j.get("priority", 5), j["created_at"]))
        return out

    def events(self, job_id: str) -> list[dict]:
        return read_jsonl(self.job_dir(job_id) / "events.jsonl")

    def _event(self, job: dict, actor: str, action: str, detail: str = "") -> None:
        append_jsonl(self.job_dir(job["job_id"]) / "events.jsonl",
                     {"at": iso(self.clock()), "actor": actor, "action": action, "detail": detail,
                      "state": job["state"], "token": job["fencing_token"]})

    def _save(self, job: dict) -> None:
        job["updated_at"] = iso(self.clock())
        job["state_version"] = job.get("state_version", 0) + 1
        atomic_write_json(self._path(job["job_id"]), job)

    # ---------- tạo ----------
    def create(self, *, title: str, prompt: str, requested_by: str, assigned_to: str,
               inputs: list[dict] | None = None, idempotency_key: str | None = None,
               priority: int = 5, origin: str = "chat", job_id: str | None = None) -> dict:
        """Tạo việc. `job_id` cho phép dùng mã việc đã cấp sẵn (web cấp mã ngay khi Chủ tịch bấm nút)."""
        if not title.strip() or not prompt.strip():
            raise ToolError("Việc phải có tên và yêu cầu (prompt)")
        if job_id is not None and not JOB_ID_RE.match(job_id):
            raise ToolError(f"Mã việc cấp sẵn phải dạng TJ-YYYYMMDD-xxxxxx: {job_id!r}")
        self.dir.resolve(requested_by)
        self.dir.require_executive(assigned_to, "nhận việc TOOL")
        if assigned_to == CHAIRMAN:
            raise ToolError("Việc phải giao cho một Agent điều hành TOOL")
        with mutex(self.wb / "_create"):
            if idempotency_key:
                for j in self.list():
                    if j.get("idempotency_key") == idempotency_key:
                        if (j["title"], j["prompt"]) != (title, prompt):
                            raise ToolError(f"Khóa '{idempotency_key}' đã dùng cho việc khác ({j['job_id']})")
                        return j
            if job_id is not None:
                if self._path(job_id).exists():
                    raise ToolError(f"Mã việc {job_id} đã tồn tại")
            else:
                job_id = new_id("TJ", self.clock())
                while self._path(job_id).exists():
                    job_id = new_id("TJ", self.clock())
            now = iso(self.clock())
            job = {
                "job_id": job_id, "title": title.strip(), "prompt": prompt.strip(),
                "requested_by": requested_by, "assigned_to": assigned_to, "origin": origin,
                "priority": int(priority), "idempotency_key": idempotency_key,
                "state": "READY", "state_version": 0, "fencing_token": 0, "lease": None,
                "workers": [], "inputs": inputs or [],
                "steps": [{"id": sid, "branch": br, "what": what, "status": "PENDING", "attempts": 0,
                           "by": None, "sub": None, "evidence": [], "note": ""} for sid, br, what in PIPELINE],
                "tool_id": None, "version": None, "result": None,
                "created_at": now, "updated_at": now,
            }
            self.job_dir(job_id).mkdir(parents=True, exist_ok=False)
            atomic_write_json(self._path(job_id), job)
            self._event(job, requested_by, "create", f"giao cho {assigned_to}; nguồn {origin}")
        return job

    # ---------- khóa ghi ----------
    def _expired(self, job: dict) -> bool:
        return bool(job["lease"]) and parse_iso(job["lease"]["expires_at"]) <= self.clock()

    def claim(self, job_id: str, actor: str, ttl_seconds: int = 3600) -> int:
        """Nhận quyền ghi. Trả fencing token; mọi lần ghi sau phải kèm token này."""
        self.dir.require_executive(actor, "nhận việc TOOL")
        with mutex(self._path(job_id)):
            job = self.get(job_id)
            note = "nhận việc"
            if job["state"] == "RUNNING":
                if not self._expired(job):
                    raise ToolError(f"{job_id} đang do {job['lease']['holder']} giữ quyền ghi tới "
                                    f"{job['lease']['expires_at']} — một việc chỉ một người ghi")
                prev = job["lease"]["holder"]
                for s in job["steps"]:  # bước đang làm dở của người cũ: làm lại bước đó
                    if s["status"] == "RUNNING":
                        s["status"], s["note"] = "PENDING", f"bị gián đoạn khi {prev} đang làm; {actor} làm lại"
                nxt = self.next_step(job)
                note = f"tiếp tục sau khi lease của {prev} hết hạn, từ bước {nxt['id'] if nxt else '(hết)'}"
            elif job["state"] != "READY":
                raise ToolError(f"{job_id} ở trạng thái {job['state']}, không nhận được")
            job["fencing_token"] += 1
            job["lease"] = {"holder": actor, "token": job["fencing_token"],
                            "expires_at": iso(self.clock() + timedelta(seconds=ttl_seconds))}
            job["state"] = "RUNNING"
            if actor not in job["workers"]:
                job["workers"].append(actor)
            self._save(job)
            self._event(job, actor, "claim", note)
            return job["fencing_token"]

    def _held(self, job: dict, actor: str, token: int) -> None:
        if job["state"] != "RUNNING" or not job["lease"]:
            raise ToolError(f"{job['job_id']} không ở RUNNING — không ai đang giữ quyền ghi")
        if job["lease"]["holder"] != actor or job["lease"]["token"] != token:
            raise ToolError(f"{actor} không còn giữ {job['job_id']} (token cũ hoặc khác người) — từ chối ghi")
        if self._expired(job):
            raise ToolError(f"Lease của {actor} trên {job['job_id']} đã hết hạn — từ chối ghi")

    def heartbeat(self, job_id: str, actor: str, token: int, ttl_seconds: int = 3600) -> None:
        with mutex(self._path(job_id)):
            job = self.get(job_id)
            self._held(job, actor, token)
            job["lease"]["expires_at"] = iso(self.clock() + timedelta(seconds=ttl_seconds))
            self._save(job)

    def release(self, job_id: str, actor: str, token: int, note: str = "") -> dict:
        """Nhả quyền ghi để Agent kia nhận tiếp. Các bước đã xong giữ nguyên."""
        with mutex(self._path(job_id)):
            job = self.get(job_id)
            self._held(job, actor, token)
            for s in job["steps"]:
                if s["status"] == "RUNNING":
                    s["status"], s["note"] = "PENDING", f"{actor} nhả việc khi đang làm bước này"
            job["state"], job["lease"] = "READY", None
            self._save(job)
            self._event(job, actor, "release", note or "nhả quyền ghi")
            return job

    # ---------- bước ----------
    @staticmethod
    def next_step(job: dict) -> dict | None:
        for s in job["steps"]:
            if s["status"] not in ("DONE", "SKIPPED"):
                return s
        return None

    @staticmethod
    def _step(job: dict, step_id: str) -> dict:
        for s in job["steps"]:
            if s["id"] == step_id:
                return s
        raise ToolError(f"Không có bước '{step_id}' (các bước: {', '.join(STEP_IDS)})")

    def _independence(self, job: dict, step_id: str, sub: str | None) -> None:
        if step_id in ("build", "test") and not sub:
            raise ToolError(f"Bước {step_id} phải ghi rõ Agent con thực hiện (sub)")
        if step_id == "test":
            builder = self._step(job, "build")["sub"]
            if sub == builder:
                raise ToolError(f"{sub} đã viết mã ở bước build — kiểm thử phải do Agent con khác làm")

    def step_start(self, job_id: str, step_id: str, actor: str, token: int, sub: str | None = None) -> dict:
        with mutex(self._path(job_id)):
            job = self.get(job_id)
            self._held(job, actor, token)
            nxt = self.next_step(job)
            if nxt is None or nxt["id"] != step_id:
                raise ToolError(f"Bước kế tiếp là '{nxt['id'] if nxt else '(hết)'}', không phải '{step_id}'")
            if nxt["status"] == "RUNNING":
                raise ToolError(f"Bước {step_id} đang chạy")
            if nxt["attempts"] >= MAX_ATTEMPTS:
                raise ToolError(f"Bước {step_id} đã thử {nxt['attempts']} lần — cần Chủ tịch hoặc Agent điều hành quyết")
            if sub:
                self.dir.resolve(sub)
            self._independence(job, step_id, sub)
            nxt.update(status="RUNNING", attempts=nxt["attempts"] + 1, by=actor, sub=sub,
                       started_at=iso(self.clock()), note="")
            self._save(job)
            self._event(job, actor, "step_start", f"{step_id}" + (f" · Agent con {sub}" if sub else ""))
            return job

    def step_done(self, job_id: str, step_id: str, actor: str, token: int, evidence: list[str],
                  note: str = "", skipped: bool = False) -> dict:
        with mutex(self._path(job_id)):
            job = self.get(job_id)
            self._held(job, actor, token)
            s = self._step(job, step_id)
            if skipped:
                nxt = self.next_step(job)
                if nxt is None or nxt["id"] != step_id:
                    raise ToolError(f"Chỉ được bỏ qua bước kế tiếp ('{nxt['id'] if nxt else '(hết)'}')")
                if step_id not in ("research",):
                    raise ToolError(f"Bước {step_id} là bắt buộc, không bỏ qua được")
                if not note.strip():
                    raise ToolError("Bỏ qua bước phải ghi lý do")
                s.update(status="SKIPPED", by=actor, note=note.strip(), finished_at=iso(self.clock()))
            else:
                if s["status"] != "RUNNING":
                    raise ToolError(f"Bước {step_id} chưa được bắt đầu (đang {s['status']})")
                if s["by"] != actor:
                    raise ToolError(f"Bước {step_id} do {s['by']} bắt đầu — {actor} không ghi thay")
                ev = [e for e in (evidence or []) if e and e.strip()]
                if not ev:
                    raise ToolError(f"Bước {step_id} xong phải có bằng chứng (đường dẫn tệp hoặc kết quả đo)")
                self._independence(job, step_id, s["sub"])
                s.update(status="DONE", evidence=ev, note=note.strip(), finished_at=iso(self.clock()))
            self._save(job)
            self._event(job, actor, "step_skip" if skipped else "step_done",
                        f"{step_id}: {note.strip() or '; '.join(s['evidence'])}")
            return job

    def step_fail(self, job_id: str, step_id: str, actor: str, token: int, note: str) -> dict:
        with mutex(self._path(job_id)):
            job = self.get(job_id)
            self._held(job, actor, token)
            s = self._step(job, step_id)
            if s["status"] != "RUNNING":
                raise ToolError(f"Bước {step_id} không đang chạy")
            s.update(status="FAILED", note=note.strip() or "lỗi", finished_at=iso(self.clock()))
            self._save(job)
            self._event(job, actor, "step_fail", f"{step_id}: {s['note']}")
            return job

    # ---------- trạng thái & trường ----------
    def set_fields(self, job_id: str, actor: str, token: int, **fields) -> dict:
        allowed = {"tool_id", "version", "result", "inputs"}
        bad = set(fields) - allowed
        if bad:
            raise ToolError(f"Không được sửa trường: {', '.join(sorted(bad))}")
        with mutex(self._path(job_id)):
            job = self.get(job_id)
            self._held(job, actor, token)
            job.update(fields)
            self._save(job)
            self._event(job, actor, "set", ", ".join(f"{k}={fields[k]!r}"[:120] for k in sorted(fields)))
            return job

    def move(self, job_id: str, to_state: str, actor: str, token: int | None = None, note: str = "") -> dict:
        if to_state not in TRANSITIONS:
            raise ToolError(f"Trạng thái '{to_state}' không tồn tại")
        if to_state == "RUNNING":
            raise ToolError("Dùng claim() để vào RUNNING")
        with mutex(self._path(job_id)):
            job = self.get(job_id)
            frm = job["state"]
            if to_state not in TRANSITIONS[frm]:
                raise ToolError(f"Không được chuyển {frm} → {to_state}")
            if frm == "RUNNING":
                if actor == CHAIRMAN and to_state == "CANCELLED":
                    self.dir.resolve(actor)  # quyền dừng cuối cùng của Chủ tịch: không cần token
                else:
                    if token is None:
                        raise ToolError("Rời RUNNING phải kèm fencing token của người đang giữ")
                    self._held(job, actor, token)
            else:
                self.dir.require_executive(actor, f"chuyển {frm} → {to_state}")
            if to_state == "VERIFIED_DONE":
                todo = [s["id"] for s in job["steps"] if s["status"] not in ("DONE", "SKIPPED")]
                if todo:
                    raise ToolError(f"Chưa xong các bước: {', '.join(todo)}")
                if not job.get("tool_id") or not job.get("version"):
                    raise ToolError("VERIFIED_DONE cần mã tool và phiên bản đã phát hành")
            if to_state in ("BLOCKED", "WAITING_APPROVAL", "FAILED", "CANCELLED") and not note.strip():
                raise ToolError(f"Chuyển sang {to_state} phải ghi lý do")
            for s in job["steps"]:
                if s["status"] == "RUNNING":
                    s["status"], s["note"] = "PENDING", f"dừng khi chuyển sang {to_state}"
            job["state"], job["lease"] = to_state, None
            self._save(job)
            self._event(job, actor, "move", f"{frm} → {to_state}" + (f": {note.strip()}" if note.strip() else ""))
            return job

    # ---------- hàng đợi ----------
    def backlog(self) -> list[dict]:
        """Việc cần người làm: READY, hoặc RUNNING mà lease đã hết hạn (người giữ đã ngừng)."""
        out = []
        for j in self.list():
            if j["state"] == "READY" or (j["state"] == "RUNNING" and self._expired(j)):
                out.append(j)
        return out
