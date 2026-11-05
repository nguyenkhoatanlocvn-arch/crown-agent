"""Hộp việc và bộ nhớ riêng của từng Agent điều hành TOOL.

agents/<id>/inbox/<mã>.json   hộp việc: lệnh và tin gửi cho đúng Agent đó
agents/<id>/MEMORY.md         bộ nhớ riêng: chỉ chính Agent đó ghi (Agent kia và Chủ tịch đọc được)
Hai Agent dùng chung kho, hàng đợi và lịch sử; hộp việc và bộ nhớ thì tách riêng.
"""
from __future__ import annotations

from pathlib import Path

from .identity import CHAIRMAN, Directory
from .util import ToolError, atomic_write_json, iso, new_id, read_json, tool_root, vn


class Mailbox:
    def __init__(self, root: Path | None = None, directory: Directory | None = None):
        self.root = Path(root) if root else tool_root()
        self.dir = directory or Directory(self.root)

    def _box(self, agent: str) -> Path:
        if not self.dir.is_executive(agent):
            raise ToolError(f"{agent} không có hộp việc trong TOOL")
        d = self.root / "agents" / agent / "inbox"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def send(self, to: str, sender: str, subject: str, body: str = "", job_id: str | None = None) -> dict:
        self.dir.resolve(sender)
        if not subject.strip():
            raise ToolError("Tin phải có tiêu đề")
        msg = {"id": new_id("M", fmt="%Y%m%d-%H%M%S"), "to": to, "from": sender, "subject": subject.strip(),
               "body": body.strip(), "job_id": job_id, "state": "OPEN", "at": iso(), "closed_at": None, "closed_note": ""}
        atomic_write_json(self._box(to) / f"{msg['id']}.json", msg)
        return msg

    def list(self, agent: str, state: str | None = None) -> list[dict]:
        out = [read_json(p) for p in sorted(self._box(agent).glob("*.json"))]
        return [m for m in out if state is None or m["state"] == state]

    def close(self, agent: str, msg_id: str, actor: str, note: str = "") -> dict:
        """Chỉ chủ hộp việc (hoặc Chủ tịch) đóng tin của mình."""
        if actor not in (agent, CHAIRMAN):
            raise ToolError(f"{actor} không đóng tin trong hộp việc của {agent}")
        p = self._box(agent) / f"{msg_id}.json"
        if not p.is_file():
            raise ToolError(f"Không có tin {msg_id} trong hộp việc của {agent}")
        m = read_json(p)
        m.update(state="DONE", closed_at=iso(), closed_note=note.strip())
        atomic_write_json(p, m)
        return m


class Memory:
    def __init__(self, root: Path | None = None, directory: Directory | None = None):
        self.root = Path(root) if root else tool_root()
        self.dir = directory or Directory(self.root)

    def path(self, agent: str) -> Path:
        if not self.dir.is_executive(agent):
            raise ToolError(f"{agent} không có bộ nhớ riêng trong TOOL")
        return self.root / "agents" / agent / "MEMORY.md"

    def append(self, agent: str, actor: str, text: str) -> Path:
        if actor != agent:
            raise ToolError(f"Bộ nhớ của {agent} chỉ do chính {agent} ghi — {actor} bị từ chối")
        if not text.strip():
            raise ToolError("Nội dung ghi nhớ rỗng")
        p = self.path(agent)
        p.parent.mkdir(parents=True, exist_ok=True)
        if not p.exists():
            p.write_text(f"# Bộ nhớ riêng của {agent}\n\nChỉ {agent} ghi. Mỗi dòng: thời điểm · điều cần nhớ.\n\n", encoding="utf-8")
        with open(p, "a", encoding="utf-8", newline="\n") as f:
            f.write(f"- {vn(iso())} · {' '.join(text.split())}\n")
        return p
