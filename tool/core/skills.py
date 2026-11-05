"""Kho skill riêng của bộ phận TOOL: tool/skills/<tên>/v<N>/SKILL.md + registry.json.

Nhận skill từ ba nguồn: chính bộ phận TOOL (hai Agent điều hành), Agent Vua, hoặc A03 (kể cả A03.x, A03-NB).
Mỗi phiên bản lưu: người cấp, nguồn, băm, kết quả thử và bằng chứng. Bản trước luôn được giữ để quay lui.
Chỉ phiên bản có kết quả thử PASS mới thành bản hiện hành.
"""
from __future__ import annotations

import re
import shutil
from pathlib import Path

from .identity import Directory
from .util import ToolError, atomic_write_json, iso, mutex, read_json, sha256_file, tool_root

NAME_RE = re.compile(r"^[a-z][a-z0-9]*(-[a-z0-9]+)*$")
GRANTOR_PREFIXES = ("A03",)  # A03, A03.1-3, A03-NB
GRANTOR_IDS = {"VUA_CEO", "CHAIRMAN"}


def parse_frontmatter(text: str) -> dict:
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n", text, re.S)
    if not m:
        return {}
    out = {}
    for line in m.group(1).splitlines():
        k, sep, v = line.partition(":")
        if sep and k.strip() and not k.startswith((" ", "\t")):
            out[k.strip()] = v.strip().strip('"').strip("'")
    return out


class Skills:
    def __init__(self, root: Path | None = None, directory: Directory | None = None):
        self.root = Path(root) if root else tool_root()
        self.dir = directory or Directory(self.root)
        self.base = self.root / "skills"
        self.base.mkdir(parents=True, exist_ok=True)
        self.reg_path = self.base / "registry.json"

    def registry(self) -> dict:
        return read_json(self.reg_path, default={"skills": {}, "updated_at": None})

    def _may_grant(self, granted_by: str) -> bool:
        return (self.dir.is_executive(granted_by) or granted_by in GRANTOR_IDS
                or granted_by.startswith(GRANTOR_PREFIXES))

    def add(self, skill_md: Path, *, granted_by: str, actor: str, source: str, test_result: str,
            test_evidence: str = "", note: str = "") -> dict:
        """Thêm một phiên bản skill. actor (Agent điều hành TOOL) là người ghi; granted_by là người cấp."""
        self.dir.require_executive(actor, "ghi kho skill TOOL")
        if not self._may_grant(granted_by):
            raise ToolError(f"{granted_by} không được cấp skill cho TOOL (chỉ bộ phận TOOL, Agent Vua, A03)")
        if granted_by not in GRANTOR_IDS and not self.dir.is_executive(granted_by) and granted_by != "A03-NB":
            self.dir.resolve(granted_by)
        if test_result not in ("PASS", "FAIL", "NOT_RUN"):
            raise ToolError("test_result phải là PASS, FAIL hoặc NOT_RUN")
        if test_result == "PASS" and not test_evidence.strip():
            raise ToolError("Kết quả thử PASS phải kèm bằng chứng")
        if not source.strip():
            raise ToolError("Phải ghi nguồn của skill")
        skill_md = Path(skill_md)
        text = skill_md.read_text(encoding="utf-8")
        fm = parse_frontmatter(text)
        name = fm.get("name", "")
        if not NAME_RE.match(name) or not fm.get("description"):
            raise ToolError("SKILL.md phải có frontmatter với name (kebab-case) và description")
        with mutex(self.reg_path):
            reg = self.registry()
            s = reg["skills"].setdefault(name, {"name": name, "current": None, "versions": [], "history": []})
            digest = sha256_file(skill_md)
            if any(v["sha256"] == digest for v in s["versions"]):
                raise ToolError(f"Skill {name} đã có phiên bản nội dung y hệt")
            n = len(s["versions"]) + 1
            vdir = self.base / name / f"v{n}"
            vdir.mkdir(parents=True, exist_ok=False)
            shutil.copyfile(skill_md, vdir / "SKILL.md")
            ver = {"version": n, "granted_by": granted_by, "recorded_by": actor, "source": source.strip(),
                   "sha256": digest, "description": fm["description"], "at": iso(),
                   "test": {"result": test_result, "evidence": test_evidence.strip()}, "status": "CANDIDATE",
                   "note": note.strip(), "path": (vdir / "SKILL.md").relative_to(self.root).as_posix()}
            prev = s["current"]
            if test_result == "PASS":
                ver["status"] = "CURRENT"
                for v in s["versions"]:
                    if v["status"] == "CURRENT":
                        v["status"] = "PREVIOUS"
                s["current"] = n
                s["history"].append({"at": ver["at"], "actor": actor, "action": "activate", "from": prev, "to": n})
            s["versions"].append(ver)
            reg["updated_at"] = iso()
            atomic_write_json(self.reg_path, reg)
            return ver

    def rollback(self, name: str, actor: str, reason: str) -> dict:
        """Quay về phiên bản PASS liền trước. Bản đang dùng chuyển thành WITHDRAWN, tệp vẫn giữ."""
        self.dir.require_executive(actor, "quay lui skill TOOL")
        if not reason.strip():
            raise ToolError("Quay lui phải ghi lý do")
        with mutex(self.reg_path):
            reg = self.registry()
            s = reg["skills"].get(name)
            if not s or s["current"] is None:
                raise ToolError(f"Skill {name} không có bản hiện hành")
            cur = s["current"]
            older = [v for v in s["versions"] if v["version"] < cur and v["test"]["result"] == "PASS"
                     and v["status"] != "WITHDRAWN"]
            target = older[-1]["version"] if older else None
            for v in s["versions"]:
                if v["version"] == cur:
                    v.update(status="WITHDRAWN", note=reason.strip())
                elif v["version"] == target:
                    v["status"] = "CURRENT"
            s["current"] = target
            s["history"].append({"at": iso(), "actor": actor, "action": "rollback", "from": cur, "to": target,
                                 "note": reason.strip()})
            reg["updated_at"] = iso()
            atomic_write_json(self.reg_path, reg)
            return {"name": name, "withdrawn": cur, "current": target}

    def current_path(self, name: str) -> Path:
        s = self.registry()["skills"].get(name)
        if not s or s["current"] is None:
            raise ToolError(f"Skill {name} không có bản hiện hành")
        return self.base / name / f"v{s['current']}" / "SKILL.md"
