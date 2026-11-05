"""Kiểm tra quyền truy cập dữ liệu của người gọi — chạy ở MỖI lần gọi tool.

Luật:
1. Tệp giống bí mật (.env, *.key, auth*.json, *secret*, cookie, ví, .ssh ...) luôn bị từ chối, kể cả Chủ tịch
   (hiến chương Agent Vua mục 4: không Agent nào đọc bí mật).
2. Tệp trong repo: người gọi đọc được vùng ghi + bộ nhớ của chính mình, vùng dùng chung, vùng được cấp thêm,
   và vùng của cấp dưới mình. Vùng riêng của Agent khác bị từ chối.
3. Tệp ngoài repo: bị từ chối nếu khớp vùng được bảo vệ (vd. thư mục bot BTC) mà người gọi không có tên
   trong danh sách cho phép; còn lại cho phép (tệp làm việc của phiên).
4. Ghi kết quả: chỉ vào vùng ghi của người gọi, vùng kết quả của lượt chạy, hoặc ngoài repo không thuộc vùng bảo vệ.
Mọi quyết định (cho/chặn + luật áp dụng) được ghi vào bản ghi lượt chạy.
"""
from __future__ import annotations

import fnmatch
from pathlib import Path

from .identity import CHAIRMAN, Directory
from .util import REPO_ROOT, ToolError, read_json, tool_root

DEFAULT_POLICY = {
    "deny_globs": [".env", ".env.*", "*.key", "*.pem", "*.pfx", "*.p12", "*.kdbx", "auth*.json", "*secret*",
                   "*cookie*", "*credential*", "*password*", "*passwd*", "id_rsa*", "id_ed25519*", "*.keystore",
                   "wallet*.dat", "*seed*phrase*", "*.token", "*apikey*", "*api_key*"],
    "deny_dirs": [".ssh", ".gnupg", ".aws", ".grokbot", ".codex", ".git"],
    "shared_read": ["tool/catalog/", "tool/inbox/", "tool/skills/", "tool/workbench/*/samples/", "docs/", "skills/"],
    "grants": {},
    "protected_external": [
        {"pattern": "*BTCDesk*", "allow": ["CHAIRMAN", "CFO", "A11"], "why": "bot BTC tiền thật"},
        {"pattern": "*CROWN_COUNCIL*", "allow": ["CHAIRMAN", "VUA_CEO"], "why": "cầu nối Hội đồng AI (có khóa)"},
    ],
}


class AccessDenied(ToolError):
    pass


class Access:
    def __init__(self, root: Path | None = None, directory: Directory | None = None, repo_root: Path | None = None):
        self.root = Path(root) if root else tool_root()
        self.dir = directory or Directory(self.root)
        # Gốc repo để xét vùng: mặc định là cha của gốc TOOL (repo/tool) — test đổi được.
        self.repo = Path(repo_root).resolve() if repo_root else (
            REPO_ROOT if self.root.resolve() == (REPO_ROOT / "tool").resolve() else self.root.resolve().parent)
        self.policy = dict(DEFAULT_POLICY, **read_json(self.root / "config" / "access.json", default={}))

    # ---- nội bộ ----
    def _secretish(self, p: Path) -> str | None:
        name = p.name.lower()
        for g in self.policy["deny_globs"]:
            if fnmatch.fnmatch(name, g.lower()):
                return f"tên tệp khớp mẫu bí mật '{g}'"
        for part in p.parts[:-1]:
            if part.lower() in self.policy["deny_dirs"]:
                return f"nằm trong thư mục bảo vệ '{part}'"
        return None

    def _rel(self, p: Path) -> str | None:
        try:
            return p.relative_to(self.repo).as_posix()
        except ValueError:
            return None

    @staticmethod
    def _in_scope(relpath: str, scope: str) -> bool:
        """scope dạng 'a/b/' ('*' = một cấp bất kỳ); khớp khi relpath nằm trong scope."""
        ss = [x for x in scope.split("/") if x]
        ps = [x for x in relpath.split("/") if x]
        if len(ps) < len(ss):
            return False
        return all(s == "*" or s == q for s, q in zip(ss, ps))

    def _own_scopes(self, who: dict) -> list[str]:
        scopes = list(who["write_scope"]) + ([who["memory_ns"]] if who.get("memory_ns") else [])
        scopes += list((self.policy.get("grants") or {}).get(who["id"], {}).get("read", []))
        if who["kind"] in ("leader", "executive"):  # lãnh đạo đọc được vùng của cấp dưới
            for mid in self.dir.team_of(who["id"]):
                try:
                    m = self.dir.resolve(mid)
                    scopes += m["write_scope"] + ([m["memory_ns"]] if m.get("memory_ns") else [])
                except ToolError:
                    pass
        return scopes

    def _external(self, who: dict, p: Path) -> tuple[bool, str]:
        s = p.as_posix()
        for rule in self.policy["protected_external"]:
            if fnmatch.fnmatch(s, rule["pattern"]) and who["id"] not in rule["allow"]:
                return False, f"vùng được bảo vệ ({rule.get('why', rule['pattern'])}); chỉ {', '.join(rule['allow'])}"
        return True, "tệp làm việc ngoài repo, không thuộc vùng bảo vệ"

    # ---- công khai ----
    def decide(self, caller: str, path: str | Path, mode: str = "read", run_dir: Path | None = None) -> dict:
        who = self.dir.resolve(caller)
        p = Path(path).expanduser()
        p = (p if p.is_absolute() else Path.cwd() / p).resolve()
        out = {"path": p.as_posix(), "mode": mode, "caller": caller}
        why = self._secretish(p)
        if why:
            return dict(out, allow=False, rule=f"bí mật: {why}")
        relp = self._rel(p)
        if run_dir is not None:
            try:
                p.relative_to(Path(run_dir).resolve())
                return dict(out, allow=True, rule="vùng kết quả của chính lượt chạy")
            except ValueError:
                pass
        if relp is None:
            ok, rule = self._external(who, p)
            return dict(out, allow=ok, rule=rule)
        if caller == CHAIRMAN:
            return dict(out, allow=True, rule="Chủ tịch")
        for sc in self._own_scopes(who):
            if self._in_scope(relp, sc):
                return dict(out, allow=True, rule=f"vùng của người gọi hoặc cấp dưới ('{sc}')")
        if mode == "read":
            for sc in self.policy["shared_read"]:
                if self._in_scope(relp, sc):
                    return dict(out, allow=True, rule=f"vùng dùng chung ('{sc}')")
        return dict(out, allow=False, rule=f"'{relp}' không thuộc vùng {('đọc' if mode == 'read' else 'ghi')} của {caller}")

    def check(self, caller: str, path: str | Path, mode: str = "read", run_dir: Path | None = None) -> dict:
        d = self.decide(caller, path, mode, run_dir)
        if not d["allow"]:
            raise AccessDenied(f"TỪ CHỐI truy cập dữ liệu: {caller} {('đọc' if mode == 'read' else 'ghi')} "
                               f"{d['path']} — {d['rule']}")
        return d
