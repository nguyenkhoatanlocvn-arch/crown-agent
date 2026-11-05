"""Bảng quyền D03 của Thiên Não (lệnh DRAGON SINGULARITY mục 02, 16).

Ba loại hành động:
  AUTONOMOUS     — được tự làm nếu có trong dòng quyền của mình (hoặc của mức quyền).
  SENSITIVE      — Agent chỉ làm khi có phê duyệt riêng của Chủ tịch cho đúng việc đó.
  CHAIRMAN_ONLY  — chỉ Chủ tịch; Agent chỉ được đề xuất.

Phê duyệt nằm trong registries/approvals.json trên nhánh chính: Agent không ghi
được vào đó nếu không qua Chủ tịch gộp PR, nên trạng thái chu trình (nhánh
tn-state, Agent ghi được) không thể tự cấp phê duyệt.

Không dùng mạng, không đọc secret.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from . import registry as R

MATRIX_FILE = R.REG_DIR / "permissions.json"
APPROVALS_FILE = R.REG_DIR / "approvals.json"
LOCK_FILE = R.REG_DIR / "CONSTITUTION.lock"
CLASSES = ("AUTONOMOUS", "SENSITIVE", "CHAIRMAN_ONLY")
VERDICTS = ("ALLOW", "NEEDS_APPROVAL", "DENY")
# Tệp phải luôn được bảo vệ, kể cả khi ai đó sửa danh sách protected_paths.
MUST_PROTECT = ("registries/permissions.json", "registries/approvals.json", "registries/leaders.json",
                "registries/thien_nao.json", "registries/vuong_toa.json", "registries/CONSTITUTION.lock",
                "crown/policy.py", ".github/workflows/")


class PolicyError(Exception):
    """Bảng quyền hỏng, hoặc hành động bị từ chối."""


@dataclass(frozen=True)
class Decision:
    verdict: str
    reason: str

    @property
    def allowed(self) -> bool:
        return self.verdict == "ALLOW"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _norm(path: str) -> str:
    p = str(path).replace("\\", "/")
    while p.startswith("./"):
        p = p[2:]
    return p.lstrip("/")


class Policy:
    def __init__(self, registry: R.Registry, matrix: dict, approvals: dict | None = None,
                 clock: Callable[[], datetime] = _utc_now):
        self.reg = registry
        self.matrix = matrix
        self.approvals = approvals or {"approvals": []}
        self.clock = clock
        self.validate()

    @classmethod
    def load(cls, registry: R.Registry | None = None, reg_dir: Path | None = None,
             clock: Callable[[], datetime] = _utc_now) -> "Policy":
        d = reg_dir or R.REG_DIR
        reg = registry or R.load(d)
        matrix = json.loads((d / "permissions.json").read_text(encoding="utf-8"))
        ap_path = d / "approvals.json"
        approvals = json.loads(ap_path.read_text(encoding="utf-8")) if ap_path.exists() else None
        return cls(reg, matrix, approvals, clock=clock)

    # ---------- kiểm tra chính bảng quyền ----------
    def _class(self, action: str) -> str | None:
        a = self.matrix.get("actions", {}).get(action)
        return a.get("class") if isinstance(a, dict) else None

    def validate(self) -> None:
        errors: list[str] = []
        actions = self.matrix.get("actions")
        grants = self.matrix.get("grants")
        if not isinstance(actions, dict) or not actions or not isinstance(grants, dict):
            raise PolicyError("Bảng quyền phải có 'actions' và 'grants'")
        for name, a in actions.items():
            if not isinstance(a, dict) or a.get("class") not in CLASSES:
                errors.append(f"Hành động {name}: class phải thuộc {CLASSES}")
        if grants.get("CHAIRMAN") != ["*"]:
            errors.append("CHAIRMAN phải giữ toàn quyền ['*']")
        for who, acts in grants.items():
            if who == "CHAIRMAN":
                continue
            if who.startswith("@tier:"):
                if who[6:] not in R.TIERS or who[6:] == "SENSITIVE":
                    errors.append(f"Dòng quyền {who}: mức quyền không hợp lệ")
            elif not self.reg.has_member(who):
                errors.append(f"Dòng quyền '{who}': không có trong sổ đăng ký")
            if not isinstance(acts, list):
                errors.append(f"Dòng quyền {who}: phải là danh sách hành động")
                continue
            for act in acts:
                if act == "*":
                    errors.append(f"{who}: Agent không được giữ toàn quyền '*' (tự nâng quyền)")
                elif act not in actions:
                    errors.append(f"{who}: hành động '{act}' không có trong bảng")
                elif self._class(act) != "AUTONOMOUS":
                    errors.append(f"{who}: không được cấp sẵn {act} (loại {self._class(act)}) — chỉ Chủ tịch duyệt từng việc")
        prot = [_norm(p) for p in self.matrix.get("protected_paths", [])]
        for must in MUST_PROTECT:
            if must not in prot:
                errors.append(f"protected_paths thiếu '{must}' — tệp quyền phải luôn được bảo vệ")
        for act in self.matrix.get("estop_allowed", []):
            if act not in actions:
                errors.append(f"estop_allowed: hành động '{act}' không có trong bảng")
        for ap in self.approvals.get("approvals", []):
            aid = ap.get("id", "?")
            if ap.get("granted_by") != "CHAIRMAN":
                errors.append(f"Phê duyệt {aid}: chỉ Chủ tịch cấp (granted_by=CHAIRMAN)")
            if self._class(ap.get("action", "")) != "SENSITIVE":
                errors.append(f"Phê duyệt {aid}: hành động '{ap.get('action')}' không thuộc loại SENSITIVE "
                              f"(việc CHAIRMAN_ONLY không ủy quyền được)")
            if not self.reg.has_member(str(ap.get("principal", ""))):
                errors.append(f"Phê duyệt {aid}: principal '{ap.get('principal')}' không có trong sổ đăng ký")
            for k in ("granted_at", "expires_at"):
                try:
                    datetime.fromisoformat(str(ap.get(k)))
                except ValueError:
                    errors.append(f"Phê duyệt {aid}: {k} phải là thời gian ISO")
        if errors:
            raise PolicyError("Bảng quyền không hợp lệ:\n- " + "\n- ".join(errors))

    # ---------- quyết định ----------
    def _granted(self, actor: str) -> set[str]:
        grants = self.matrix["grants"]
        if actor in grants:
            return set(grants[actor])
        tier = self.reg.member(actor).get("tier")
        return set(grants.get(f"@tier:{tier}", []))

    def _approval_ok(self, actor: str, action: str, approval_id: str | None) -> bool:
        if not approval_id:
            return False
        now = self.clock()
        for ap in self.approvals.get("approvals", []):
            if ap.get("id") != approval_id or ap.get("action") != action or ap.get("principal") != actor:
                continue
            start, end = datetime.fromisoformat(ap["granted_at"]), datetime.fromisoformat(ap["expires_at"])
            if start.tzinfo is None:
                start = start.replace(tzinfo=timezone.utc)
            if end.tzinfo is None:
                end = end.replace(tzinfo=timezone.utc)
            if start <= now < end:
                return True
        return False

    def decide(self, actor: str, action: str, *, approval_id: str | None = None, estop: bool = False) -> Decision:
        if not self.reg.has_member(actor):
            return Decision("DENY", f"'{actor}' không có trong sổ đăng ký")
        if self.reg.member(actor).get("status") == "DISABLED":
            return Decision("DENY", f"{actor} đang bị tắt")
        cls = self._class(action)
        if cls is None:
            return Decision("DENY", f"Hành động '{action}' không có trong bảng quyền")
        if actor == "CHAIRMAN":
            return Decision("ALLOW", "Chủ tịch")
        if cls == "CHAIRMAN_ONLY":
            return Decision("DENY", f"{action} chỉ Chủ tịch làm; {actor} chỉ được đề xuất")
        if estop and action not in self.matrix.get("estop_allowed", []):
            return Decision("DENY", f"Đang dừng khẩn cấp — {actor} không được {action}")
        if cls == "SENSITIVE":
            if self._approval_ok(actor, action, approval_id):
                return Decision("ALLOW", f"Chủ tịch đã phê duyệt ({approval_id})")
            return Decision("NEEDS_APPROVAL", f"{action} cần phê duyệt riêng của Chủ tịch cho {actor}")
        if action in self._granted(actor):
            return Decision("ALLOW", f"{actor} được tự chủ {action}")
        return Decision("DENY", f"{actor} không có quyền {action}")

    def require(self, actor: str, action: str, *, approval_id: str | None = None, estop: bool = False) -> None:
        d = self.decide(actor, action, approval_id=approval_id, estop=estop)
        if not d.allowed:
            raise PolicyError(f"{d.verdict}: {d.reason}")

    # ---------- tệp được bảo vệ ----------
    def protected(self, path: str) -> bool:
        p = _norm(path)
        for prot in self.matrix.get("protected_paths", []):
            q = _norm(prot)
            if p == q or (q.endswith("/") and p.startswith(q)):
                return True
        return False

    def guard_paths(self, actor: str, paths: list[str]) -> Decision:
        hit = sorted(_norm(p) for p in paths if self.protected(p))
        if not hit:
            return Decision("ALLOW", "Không chạm tệp được bảo vệ")
        if actor == "CHAIRMAN":
            return Decision("ALLOW", "Chủ tịch")
        return Decision("DENY", f"{actor} không được sửa tệp quyền/hiến pháp: {', '.join(hit)} — chỉ Chủ tịch gộp")

    # ---------- khóa hiến pháp: phát hiện tệp quyền bị đổi ngoài quy trình ----------
    def protected_files(self, root: Path) -> list[str]:
        out: list[str] = []
        for prot in self.matrix.get("protected_paths", []):
            q = _norm(prot)
            if q == "registries/CONSTITUTION.lock":
                continue
            p = root / q
            if q.endswith("/"):
                if p.is_dir():
                    out += sorted(f.relative_to(root).as_posix() for f in p.rglob("*") if f.is_file())
            elif p.is_file():
                out.append(q)
        return sorted(set(out))

    @staticmethod
    def _sha(path: Path) -> str:
        # Chuẩn hóa xuống dòng để Windows (CRLF) và Linux cho cùng mã băm.
        return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()

    def build_lock(self, root: Path) -> dict:
        return {"note": "Mã băm tệp quyền/hiến pháp. Chỉ cập nhật trong PR do Chủ tịch gộp.",
                "files": {f: self._sha(root / f) for f in self.protected_files(root)}}

    def verify_lock(self, root: Path) -> list[dict]:
        lock_path = root / "registries" / "CONSTITUTION.lock"
        if not lock_path.exists():
            return [{"path": "registries/CONSTITUTION.lock", "problem": "thiếu tệp khóa"}]
        locked = json.loads(lock_path.read_text(encoding="utf-8")).get("files", {})
        now = self.build_lock(root)["files"]
        bad = [{"path": f, "problem": "nội dung khác bản đã khóa"} for f in sorted(now) if f in locked and now[f] != locked[f]]
        bad += [{"path": f, "problem": "tệp được bảo vệ chưa có trong khóa"} for f in sorted(set(now) - set(locked))]
        bad += [{"path": f, "problem": "tệp trong khóa đã bị xóa"} for f in sorted(set(locked) - set(now))]
        return bad

    # ---------- bảng D03 ----------
    def principals(self) -> list[str]:
        ids = list(self.reg.leaders) + list(self.reg.units)
        ids += sorted(self.reg.agents) + sorted(self.reg.subagents)
        ids += sorted(sid for sid, s in self.reg.tn_seats.items() if not s.get("holder"))
        return ids

    def matrix_rows(self) -> list[dict]:
        rows = []
        for who in self.principals():
            row = {"principal": who, "tier": self.reg.member(who).get("tier", "")}
            for act in self.matrix["actions"]:
                row[act] = self.decide(who, act).verdict
            rows.append(row)
        return rows


def write_lock(root: Path | None = None) -> Path:
    """Ghi lại khóa hiến pháp. Chỉ dùng khi chuẩn bị PR để Chủ tịch gộp."""
    root = root or R.ROOT
    pol = Policy.load(R.load(root / "registries"), root / "registries")
    path = root / "registries" / "CONSTITUTION.lock"
    path.write_text(json.dumps(pol.build_lock(root), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path
