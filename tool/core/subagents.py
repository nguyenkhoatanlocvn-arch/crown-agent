"""Agent con của bộ phận TOOL, chia theo 5 nhánh.

Hai Agent điều hành được tự tạo Agent con khi cần. Mỗi Agent con có ID, người giao, đầu ra,
quyền và hạn mức riêng. Mặc định tối đa hai Agent con chạy đồng thời (đổi trong dept_tool.json).

"Agent con" là một bản ghi trách nhiệm + một worker thật: worker có thể là Agent con của Claude
(công cụ Agent trong phiên), hoặc một tiến trình của bộ điều phối. Bản ghi luôn nêu Agent chịu trách nhiệm.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable

from .identity import Directory
from .util import ToolError, atomic_write_json, iso, mutex, parse_iso, read_json, tool_root, utc_now

WORKER_KINDS = {"claude-subagent", "process", "session"}
DEFAULT_LIMITS = {"max_runtime_s": 1800, "paid_usd": 0, "max_output_mb": 50}


class SubAgents:
    def __init__(self, root: Path | None = None, directory: Directory | None = None,
                 clock: Callable[[], datetime] = utc_now):
        self.root = Path(root) if root else tool_root()
        self.dir = directory or Directory(self.root)
        self.clock = clock
        self.base = self.root / "agents" / "sub"
        self.base.mkdir(parents=True, exist_ok=True)

    @property
    def max_concurrent(self) -> int:
        return int(self.dir.limits.get("max_concurrent_subagents", 2))

    def get(self, sub_id: str) -> dict:
        p = self.base / f"{sub_id}.json"
        if not p.is_file():
            raise ToolError(f"Không có Agent con {sub_id}")
        return read_json(p)

    def list(self, state: str | None = None) -> list[dict]:
        out = [read_json(p) for p in sorted(self.base.glob("*.json"))]
        return [s for s in out if state is None or s["state"] == state]

    def _reap(self) -> None:
        """Agent con quá hạn mà không báo xong: đánh dấu EXPIRED để không giữ chỗ mãi."""
        for s in self.list("ACTIVE"):
            if parse_iso(s["expires_at"]) <= self.clock():
                s.update(state="EXPIRED", finished_at=iso(self.clock()), result="quá hạn mức thời gian, không báo xong")
                atomic_write_json(self.base / f"{s['id']}.json", s)

    def spawn(self, *, branch: str, assigned_by: str, job_id: str | None, output: str, step: str | None = None,
              worker: str = "claude-subagent", limits: dict | None = None, write_scope: list[str] | None = None) -> dict:
        """Tạo Agent con. Từ chối khi đã đủ số Agent con chạy đồng thời."""
        self.dir.require_executive(assigned_by, "tạo Agent con")
        if branch not in self.dir.branches:
            raise ToolError(f"Không có nhánh '{branch}' (các nhánh: {', '.join(self.dir.branches)})")
        if worker not in WORKER_KINDS:
            raise ToolError(f"Loại worker '{worker}' không hợp lệ")
        if not output or not output.strip():
            raise ToolError("Agent con phải có đầu ra cần giao (output)")
        lim = dict(DEFAULT_LIMITS, **(limits or {}))
        if lim["paid_usd"] != 0:
            from .budget import Budget
            Budget(self.root).require(lim["paid_usd"], f"hạn mức trả phí cho Agent con nhánh {branch}", assigned_by)
        with mutex(self.base / "_spawn"):
            self._reap()
            active = self.list("ACTIVE")
            if len(active) >= self.max_concurrent:
                raise ToolError(f"Đang chạy {len(active)}/{self.max_concurrent} Agent con "
                                f"({', '.join(a['id'] for a in active)}) — chờ một Agent con xong")
            nums = [int(p.stem.rsplit("-", 1)[1]) for p in self.base.glob(f"tool-{branch}-*.json")]
            sub_id = f"tool-{branch}-{(max(nums) + 1 if nums else 1):02d}"
            b = self.dir.branches[branch]
            scope = write_scope or ([f"tool/workbench/{job_id}/"] if job_id else [f"tool/agents/sub/{sub_id}/"])
            rec = {
                "id": sub_id, "name": f"{b['name']} #{sub_id.rsplit('-', 1)[1]}", "branch": branch,
                "branch_name": b["name"], "kind": "agent", "status": "ACTIVE", "state": "ACTIVE",
                "assigned_by": assigned_by, "job_id": job_id, "step": step, "output": output.strip(),
                "tier": b["tier"], "write_scope": scope, "limits": lim, "worker": worker,
                "created_at": iso(self.clock()),
                "expires_at": iso(self.clock() + timedelta(seconds=int(lim["max_runtime_s"]))),
                "finished_at": None, "result": None, "evidence": [],
            }
            atomic_write_json(self.base / f"{sub_id}.json", rec)
            return rec

    def finish(self, sub_id: str, actor: str, ok: bool, result: str, evidence: list[str] | None = None) -> dict:
        self.dir.require_executive(actor, "đóng Agent con")
        with mutex(self.base / "_spawn"):
            rec = self.get(sub_id)
            if rec["state"] != "ACTIVE":
                raise ToolError(f"{sub_id} đã ở trạng thái {rec['state']}")
            rec.update(state="DONE" if ok else "FAILED", finished_at=iso(self.clock()),
                       result=result.strip(), evidence=list(evidence or []))
            atomic_write_json(self.base / f"{sub_id}.json", rec)
            return rec
