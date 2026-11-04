"""Sổ đăng ký tổ chức CROWN AGENT: Chủ tịch, 3C, 20 Agent, công ty con.

Nạp file JSON trong registries/ và kiểm tra các luật tổ chức của master prompt
(mục 04-08). Không dùng mạng, không đọc secret.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REG_DIR = ROOT / "registries"

TIERS = ["READ", "SANDBOX_WRITE", "OPERATIONAL_WRITE", "SENSITIVE"]
AGENT_STATUSES = {"ACTIVE", "REGISTERED", "DISABLED"}
EXPECTED_AGENT_IDS = [f"A{i:02d}" for i in range(1, 21)]


class RegistryError(Exception):
    """Sổ đăng ký hỏng hoặc vi phạm luật tổ chức."""


@dataclass
class Registry:
    leaders: dict[str, dict]
    agents: dict[str, dict]
    companies: dict[str, dict]
    legacy: dict[str, dict] = field(default_factory=dict)

    def member(self, member_id: str) -> dict:
        if member_id in self.leaders:
            return self.leaders[member_id]
        if member_id in self.agents:
            return self.agents[member_id]
        raise KeyError(member_id)

    def boss_of(self, member_id: str) -> str | None:
        m = self.member(member_id)
        return m.get("supervisor") if member_id in self.leaders else m.get("leader")

    def chain(self, member_id: str) -> list[str]:
        """Chuỗi chỉ huy từ member lên Chủ tịch."""
        out, cur, seen = [], member_id, set()
        while cur is not None:
            if cur in seen:
                raise RegistryError(f"Chu trình chỉ huy tại {cur}: {' -> '.join(out + [cur])}")
            seen.add(cur)
            out.append(cur)
            cur = self.boss_of(cur)
        return out

    def team_of(self, leader_id: str) -> list[str]:
        return sorted(a for a, v in self.agents.items() if v["leader"] == leader_id)


def _load_json(path: Path) -> dict:
    if not path.exists():
        raise RegistryError(f"Thiếu file sổ đăng ký: {path.name}")
    text = path.read_text(encoding="utf-8")
    if not text.strip():
        raise RegistryError(f"File sổ đăng ký rỗng: {path.name}")
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise RegistryError(f"File {path.name} không phải JSON hợp lệ: dòng {e.lineno}") from e


def scopes_overlap(a: str, b: str) -> bool:
    """Hai vùng ghi chồng nhau nếu một vùng là tiền tố của vùng kia ('*' = một cấp bất kỳ)."""
    sa = [s for s in a.split("/") if s]
    sb = [s for s in b.split("/") if s]
    for x, y in zip(sa, sb):
        if x != y and x != "*" and y != "*":
            return False
    return True


def load(reg_dir: Path = REG_DIR) -> Registry:
    lead = _load_json(reg_dir / "leaders.json")
    ag = _load_json(reg_dir / "agents.json")
    co = _load_json(reg_dir / "companies.json")
    legacy_path = reg_dir / "legacy_map.json"
    legacy = _load_json(legacy_path)["units"] if legacy_path.exists() else {}
    reg = Registry(
        leaders={x["id"]: x for x in lead.get("leaders", [])},
        agents={x["id"]: x for x in ag.get("agents", [])},
        companies={x["id"]: x for x in co.get("companies", [])},
        legacy=legacy,
    )
    validate(reg)
    return reg


def validate(reg: Registry) -> None:
    errors: list[str] = []

    # 1. Đủ lãnh đạo bắt buộc, ID không trùng giữa lãnh đạo và Agent.
    for must in ("CHAIRMAN", "VUA_CEO", "CFO", "CMO-01"):
        if must not in reg.leaders:
            errors.append(f"Thiếu lãnh đạo bắt buộc {must}")
    dup = set(reg.leaders) & set(reg.agents)
    if dup:
        errors.append(f"ID trùng giữa lãnh đạo và Agent: {sorted(dup)}")

    # 2. Đúng 20 Agent A01..A20 (master mục 06).
    if sorted(reg.agents) != EXPECTED_AGENT_IDS:
        missing = sorted(set(EXPECTED_AGENT_IDS) - set(reg.agents))
        extra = sorted(set(reg.agents) - set(EXPECTED_AGENT_IDS))
        errors.append(f"Danh mục Agent phải là A01..A20. Thiếu {missing}, thừa {extra}")

    # 3. Chỉ Chủ tịch là người, không có cấp trên, có SENSITIVE.
    for lid, l in reg.leaders.items():
        if lid == "CHAIRMAN":
            if l.get("supervisor") is not None:
                errors.append("CHAIRMAN không được có cấp trên")
            continue
        if l.get("tier") == "SENSITIVE":
            errors.append(f"{lid}: chỉ Chủ tịch giữ quyền SENSITIVE")
        if l.get("supervisor") not in reg.leaders:
            errors.append(f"{lid}: cấp trên '{l.get('supervisor')}' không tồn tại")

    # 4. Mỗi Agent đúng 1 lãnh đạo chính, tồn tại, trường bắt buộc đủ.
    required = ("name", "leader", "status", "tier", "mission", "skills", "memory_ns", "write_scope", "out_of_scope")
    for aid, a in reg.agents.items():
        for f in required:
            if f not in a:
                errors.append(f"{aid}: thiếu trường '{f}'")
        if isinstance(a.get("leader"), list):
            errors.append(f"{aid}: chỉ được có MỘT lãnh đạo chính")
        elif a.get("leader") not in reg.leaders:
            errors.append(f"{aid}: lãnh đạo '{a.get('leader')}' không tồn tại")
        if a.get("status") not in AGENT_STATUSES:
            errors.append(f"{aid}: trạng thái '{a.get('status')}' không hợp lệ")
        if a.get("tier") not in TIERS:
            errors.append(f"{aid}: mức quyền '{a.get('tier')}' không hợp lệ")
        elif a["tier"] == "SENSITIVE":
            errors.append(f"{aid}: Agent không được giữ quyền SENSITIVE")
        if a.get("memory_ns") and not str(a["memory_ns"]).startswith(f"agents/{aid}/"):
            errors.append(f"{aid}: namespace bộ nhớ phải nằm trong agents/{aid}/")

    # 5. Quyền cấp dưới <= quyền cấp trên.
    for mid in list(reg.leaders) + list(reg.agents):
        boss = reg.boss_of(mid)
        if not isinstance(boss, str) or boss not in reg.leaders:
            continue
        mt, bt = reg.member(mid).get("tier"), reg.leaders[boss].get("tier")
        if mt in TIERS and bt in TIERS and TIERS.index(mt) > TIERS.index(bt):
            errors.append(f"{mid}: quyền {mt} cao hơn cấp trên {boss} ({bt})")

    # 6. Không chu trình chỉ huy, mọi chuỗi kết thúc ở Chủ tịch.
    if not errors:
        for mid in list(reg.leaders) + list(reg.agents):
            try:
                c = reg.chain(mid)
                if c[-1] != "CHAIRMAN":
                    errors.append(f"{mid}: chuỗi chỉ huy không về Chủ tịch ({' -> '.join(c)})")
            except RegistryError as e:
                errors.append(str(e))

    # 7. Vùng ghi giữa các Agent không chồng nhau (master mục 09: một nơi ghi).
    scopes = [(aid, s) for aid, a in reg.agents.items() for s in a.get("write_scope", [])]
    for i, (a1, s1) in enumerate(scopes):
        for a2, s2 in scopes[i + 1:]:
            if a1 != a2 and scopes_overlap(s1, s2):
                errors.append(f"Vùng ghi chồng nhau: {a1}:{s1} và {a2}:{s2}")

    # 8. Công ty có chủ quản tồn tại.
    for cid, c in reg.companies.items():
        if c.get("owner") not in reg.leaders:
            errors.append(f"Công ty {cid}: chủ quản '{c.get('owner')}' không tồn tại")

    # 9. Mã cũ KING AI trỏ tới đích tồn tại.
    targets = set(reg.leaders) | set(reg.agents) | set(reg.companies)
    for code, u in reg.legacy.items():
        for t in u.get("maps_to", []):
            if t not in targets:
                errors.append(f"Mã cũ {code}: đích '{t}' không tồn tại")

    if errors:
        raise RegistryError("Sổ đăng ký không hợp lệ:\n- " + "\n- ".join(errors))
