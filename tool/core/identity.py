"""Danh tính và thẩm quyền trong TOOL.

Nguồn danh bạ, theo thứ tự:
  1. Sổ đăng ký CROWN (registries/: lãnh đạo, A01..A20, Agent con, Agent điều hành bộ phận).
  2. Agent con do TOOL tạo (tool/agents/sub/<id>.json).
  3. Danh bạ web Hoàng Thành đã đồng bộ (tool/agents/directory.json) — gồm Agent tạo sau này
     mà chưa có trong repo (vd. A03-NB, A21-AT).
Danh bạ được nạp lại ở MỖI lần gọi, nên Agent mới thêm tự có quyền tìm và gọi tool.

Giới hạn đã biết: ID do người gọi tự khai (giống CLI crown). Chưa có khóa riêng từng Agent;
"đã xác thực" ở đây nghĩa là: có trong danh bạ và không bị tắt.
"""
from __future__ import annotations

from pathlib import Path

from crown import registry as R

from .util import ToolError, read_json, tool_root

DEPT_ID = "TOOL"
CHAIRMAN = "CHAIRMAN"


class Directory:
    def __init__(self, root: Path | None = None, reg_dir: Path | None = None):
        self.root = Path(root) if root else tool_root()
        try:
            self.reg = R.load(reg_dir) if reg_dir else R.load()
        except R.RegistryError as e:
            raise ToolError(f"Sổ đăng ký CROWN không hợp lệ — TOOL từ chối chạy: {e}") from e
        self.dept = self.reg.departments.get(DEPT_ID) or {}
        self.exec_ids: list[str] = list(self.dept.get("executive_ids", []))
        self.branches: dict[str, dict] = {b["id"]: b for b in self.dept.get("branches", [])}
        self.limits: dict = dict(self.dept.get("limits", {}))

    # ---- tra cứu ----
    def _sub(self, member_id: str) -> dict | None:
        p = self.root / "agents" / "sub" / f"{member_id}.json"
        return read_json(p) if p.is_file() else None

    def _web(self, member_id: str) -> dict | None:
        d = read_json(self.root / "agents" / "directory.json", default={})
        m = (d.get("members") or {}).get(member_id)
        return dict(m, source="web-office") if isinstance(m, dict) else None

    def resolve(self, member_id: str) -> dict:
        """Trả hồ sơ thành viên; ném ToolError nếu không có trong danh bạ hoặc bị tắt."""
        if not isinstance(member_id, str) or not member_id.strip():
            raise ToolError("Thiếu ID người gọi")
        m, source = None, None
        try:
            m, source = self.reg.member(member_id), "registry"
        except KeyError:
            m = self._sub(member_id)
            source = "tool-subagent" if m else None
            if m is None:
                m = self._web(member_id)
                source = "web-office" if m else None
        if m is None:
            raise ToolError(f"'{member_id}' không có trong danh bạ Agent — từ chối")
        if m.get("status") == "DISABLED":
            raise ToolError(f"{member_id} đang bị tắt — từ chối")
        kind = ("chairman" if member_id == CHAIRMAN else "executive" if member_id in self.exec_ids
                else "leader" if member_id in self.reg.leaders else "subagent" if source == "tool-subagent"
                or member_id in self.reg.subagents else "agent")
        return {"id": member_id, "name": m.get("name", member_id), "kind": kind, "source": source,
                "tier": m.get("tier", "READ"), "status": m.get("status", "ACTIVE"),
                "write_scope": list(m.get("write_scope", []) or []), "memory_ns": m.get("memory_ns"),
                "boss": self.boss_of(member_id, m), "auth": "danh bạ (ID tự khai, chưa có khóa riêng)"}

    def boss_of(self, member_id: str, m: dict | None = None) -> str | None:
        try:
            return self.reg.boss_of(member_id)
        except KeyError:
            m = m or {}
            return m.get("assigned_by") or m.get("boss") or m.get("parent")

    # ---- thẩm quyền ----
    def is_executive(self, member_id: str) -> bool:
        return member_id in self.exec_ids

    def require_executive(self, member_id: str, what: str) -> dict:
        """Chỉ hai Agent điều hành TOOL (hoặc Chủ tịch) được làm việc này."""
        who = self.resolve(member_id)
        if member_id != CHAIRMAN and not self.is_executive(member_id):
            raise ToolError(f"{member_id} không có quyền {what} — chỉ {', '.join(self.exec_ids)} hoặc Chủ tịch")
        return who

    def team_of(self, leader_id: str) -> list[str]:
        """Thành viên dưới quyền trực tiếp/gián tiếp của một lãnh đạo (để xét quyền đọc dữ liệu)."""
        out = []
        for mid in list(self.reg.agents) + list(self.reg.subagents) + list(self.reg.leaders):
            if mid == leader_id:
                continue
            try:
                if leader_id in self.reg.chain(mid)[1:]:
                    out.append(mid)
            except Exception:
                pass
        return out
