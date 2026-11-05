"""Kiểm tra quyền truy cập dữ liệu của người gọi — chạy ở MỖI lần gọi tool.

Luật (xét theo thứ tự; luật đứng trước thắng):
1. Tệp giống bí mật (.env, *.key, auth*.json, *secret*, cookie, ví, token...) và mọi đường dẫn có một đoạn là thư mục
   bảo vệ (.ssh, .git, .aws...) luôn bị từ chối, kể cả Chủ tịch (hiến chương Agent Vua mục 4: không Agent nào đọc bí mật).
   Xét trên TOÀN BỘ đường dẫn, kể cả chính thư mục đích và tên từng tệp kết quả.
2. Ngoài repo: từ chối thư mục hệ thống (/proc, /sys, /dev, /etc, /run...), mọi thư mục/tệp bắt đầu bằng dấu chấm trong
   thư mục nhà, và vùng được bảo vệ (vd. thư mục bot BTC) nếu người gọi không có tên trong danh sách cho phép.
   Còn lại cho phép (tệp làm việc của phiên).
3. Trong repo, ĐỌC: vùng ghi + bộ nhớ của chính mình, vùng được cấp đọc thêm, vùng của cấp dưới (lãnh đạo), mẫu của việc
   mình được giao (Agent con), và vùng dùng chung. Mẫu riêng tư (inbox/<việc>/private/, khung hình) KHÔNG thuộc vùng dùng chung.
4. Trong repo, GHI: chỉ vùng ghi của chính người gọi và vùng kết quả của chính lượt chạy. Quyền đọc không kéo theo quyền ghi;
   lãnh đạo không ghi vào vùng của cấp dưới. Kho, mã, cấu hình, nhật ký và sổ đăng ký không bao giờ là nơi nhận kết quả.
Mọi quyết định (cho/chặn + luật áp dụng) được ghi vào bản ghi lượt chạy.
"""
from __future__ import annotations

import fnmatch
from pathlib import Path

from .identity import CHAIRMAN, Directory
from .util import REPO_ROOT, ToolError, read_json, tool_root

DEFAULT_POLICY = {
    "deny_globs": [".env", ".env.*", "*.key", "*.pem", "*.pfx", "*.p12", "*.kdbx", "*.jks", "auth*.json", "*secret*",
                   "*cookie*", "*credential*", "*password*", "*passwd*", "id_rsa*", "id_ed25519*", "id_ecdsa*", "*.keystore",
                   "wallet*.dat", "*seed*phrase*", "*.token", "*token*.json", "*apikey*", "*api_key*", ".netrc", ".npmrc",
                   ".pypirc", ".git-credentials", "shadow", "gshadow", "authorized_keys", "known_hosts"],
    "deny_dirs": [".ssh", ".gnupg", ".aws", ".azure", ".kube", ".docker", ".grokbot", ".codex", ".claude", ".ccr", ".git",
                  ".config"],
    "deny_roots": ["/proc", "/sys", "/dev", "/etc", "/run", "/boot", "/var/run", "/var/lib"],
    "shared_read": ["tool/catalog/", "tool/inbox/", "tool/skills/", "tool/workbench/*/samples/", "docs/", "skills/"],
    "private_read": ["tool/inbox/*/private/", "tool/workbench/*/frames/"],
    "write_protected": ["tool/catalog/", "tool/core/", "tool/config/", "tool/runs/", "tool/skills/", "tool/agents/",
                        "registries/", "crown/", "tests/"],
    "grants": {},
    "protected_external": [
        {"pattern": "*BTCDesk*", "allow": ["CHAIRMAN", "CFO", "A11"], "why": "bot BTC tiền thật"},
        {"pattern": "*CROWN_COUNCIL*", "allow": ["CHAIRMAN", "VUA_CEO"], "why": "cầu nối Hội đồng AI (có khóa)"},
    ],
}


def secret_reason(path: str | Path, policy: dict | None = None) -> str | None:
    """Lý do một đường dẫn bị coi là bí mật (hoặc None). Xét MỌI đoạn: tên tệp theo mẫu, thư mục theo danh sách."""
    policy = policy or DEFAULT_POLICY
    parts = [x for x in Path(path).parts if x not in ("/", "\\")]
    if not parts:
        return None
    name = parts[-1].lower()
    for g in policy["deny_globs"]:
        if fnmatch.fnmatch(name, g.lower()):
            return f"tên tệp khớp mẫu bí mật '{g}'"
    for part in parts:
        if part.lower() in policy["deny_dirs"]:
            return f"nằm trong thư mục bảo vệ '{part}'"
    return None


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
        return secret_reason(p, self.policy)

    def _rel(self, p: Path) -> str | None:
        try:
            return p.relative_to(self.repo).as_posix()
        except ValueError:
            return None

    @staticmethod
    def _in_scope(relpath: str, scope: str) -> bool:
        """scope dạng 'a/b/' ('*' = một cấp bất kỳ); khớp khi relpath nằm trong scope. Scope rỗng không khớp gì."""
        ss = [x for x in str(scope).split("/") if x]
        ps = [x for x in relpath.split("/") if x]
        if not ss or len(ps) < len(ss):
            return False
        return all(s == "*" or s == q for s, q in zip(ss, ps))

    def _write_scopes(self, who: dict) -> list[str]:
        """Nơi người gọi được GHI: đúng vùng ghi của chính họ. Không gồm vùng được cấp đọc, không gồm vùng cấp dưới."""
        return list(who["write_scope"])

    def _read_scopes(self, who: dict) -> list[str]:
        scopes = list(who["write_scope"]) + ([who["memory_ns"]] if who.get("memory_ns") else [])
        scopes += list((self.policy.get("grants") or {}).get(who["id"], {}).get("read", []))
        if who.get("job_id") and who.get("live"):  # Agent con đang chạy đọc được mẫu của đúng việc mình được giao
            scopes.append(f"tool/inbox/{who['job_id']}/")
        if who["kind"] in ("leader", "executive"):  # lãnh đạo đọc được vùng của cấp dưới
            for mid in self.dir.team_of(who["id"]):
                try:
                    m = self.dir.resolve(mid)
                    scopes += m["write_scope"] + ([m["memory_ns"]] if m.get("memory_ns") else [])
                except ToolError:
                    pass
        return scopes

    def _external(self, who: dict, p: Path, mode: str) -> tuple[bool, str]:
        s = p.as_posix()
        for root in self.policy["deny_roots"]:
            if s == root or s.startswith(root.rstrip("/") + "/"):
                return False, f"thư mục hệ thống '{root}' — không phải dữ liệu làm việc"
        try:
            first = p.relative_to(Path.home().resolve()).parts[:1]
            if first and first[0].startswith("."):
                return False, f"mục ẩn trong thư mục nhà ('{first[0]}') — nơi chứa cấu hình và khóa đăng nhập"
        except ValueError:
            pass
        for rule in self.policy["protected_external"]:
            if fnmatch.fnmatch(s, rule["pattern"]) and who["id"] not in rule["allow"]:
                return False, f"vùng được bảo vệ ({rule.get('why', rule['pattern'])}); chỉ {', '.join(rule['allow'])}"
        return True, "tệp làm việc ngoài repo, không thuộc vùng bảo vệ"

    # ---- công khai ----
    def decide(self, caller: str, path: str | Path, mode: str = "read", run_dir: Path | None = None) -> dict:
        if mode not in ("read", "write"):
            raise ToolError("mode phải là read hoặc write")
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
            ok, rule = self._external(who, p, mode)
            return dict(out, allow=ok, rule=rule)
        verb = "đọc" if mode == "read" else "ghi"
        if mode == "write":
            for sc in self.policy["write_protected"]:
                if self._in_scope(relp, sc):
                    return dict(out, allow=False, rule=f"'{sc}' là vùng hệ thống — không nhận kết quả chạy tool")
            if caller == CHAIRMAN:
                return dict(out, allow=True, rule="Chủ tịch")
            for sc in self._write_scopes(who):
                if self._in_scope(relp, sc):
                    return dict(out, allow=True, rule=f"vùng ghi của người gọi ('{sc}')")
            return dict(out, allow=False, rule=f"'{relp}' không thuộc vùng ghi của {caller}")
        if caller == CHAIRMAN:
            return dict(out, allow=True, rule="Chủ tịch")
        for sc in self._read_scopes(who):
            if self._in_scope(relp, sc):
                return dict(out, allow=True, rule=f"vùng của người gọi, được cấp, hoặc của cấp dưới ('{sc}')")
        for sc in self.policy["private_read"]:
            if self._in_scope(relp, sc):
                return dict(out, allow=False, rule=f"mẫu riêng tư ('{sc}') — chỉ Agent điều hành TOOL và Agent con được giao việc đó")
        for sc in self.policy["shared_read"]:
            if self._in_scope(relp, sc):
                return dict(out, allow=True, rule=f"vùng dùng chung ('{sc}')")
        return dict(out, allow=False, rule=f"'{relp}' không thuộc vùng {verb} của {caller}")

    def check(self, caller: str, path: str | Path, mode: str = "read", run_dir: Path | None = None) -> dict:
        d = self.decide(caller, path, mode, run_dir)
        if not d["allow"]:
            raise AccessDenied(f"TỪ CHỐI truy cập dữ liệu: {caller} {('đọc' if mode == 'read' else 'ghi')} "
                               f"{d['path']} — {d['rule']}")
        return d
