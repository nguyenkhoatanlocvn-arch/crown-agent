"""Sổ đăng ký tổ chức CROWN AGENT: Chủ tịch, 3C, 20 Agent, công ty con.

Nạp file JSON trong registries/ và kiểm tra các luật tổ chức của master prompt
(mục 04-08). Không dùng mạng, không đọc secret.
"""
from __future__ import annotations

import json, re
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REG_DIR = ROOT / "registries"

TIERS = ["READ", "SANDBOX_WRITE", "OPERATIONAL_WRITE", "SENSITIVE"]
AGENT_STATUSES = {"ACTIVE", "REGISTERED", "DISABLED"}
EXPECTED_AGENT_IDS = [f"A{i:02d}" for i in range(1, 21)]
# Lãnh đạo hợp lệ. Thêm lãnh đạo mới = thay đổi mô hình tổ chức → Chủ tịch quyết, sửa danh sách này có review.
EXPECTED_LEADER_IDS = {"CHAIRMAN", "VUA_CEO", "CFO", "CMO-01", "CMO-02"}
EXPECTED_LEGACY_COUNT = 25
# Bộ phận có Agent đồng điều hành và vùng của nó. Lập bộ phận mới = thay đổi mô hình tổ chức → Chủ tịch quyết,
# sửa danh sách này có review (cùng luật với EXPECTED_LEADER_IDS). Thả thêm một tệp dept_*.json là không đủ.
EXPECTED_DEPARTMENTS = {"TOOL": "tool/"}
EXEC_ID_RE = re.compile(r"[a-z][a-z0-9]*(-[a-z0-9]+){1,3}")


class RegistryError(Exception):
    """Sổ đăng ký hỏng hoặc vi phạm luật tổ chức."""


@dataclass
class Registry:
    leaders: dict[str, dict]
    agents: dict[str, dict]
    companies: dict[str, dict]
    legacy: dict[str, dict] = field(default_factory=dict)
    subagents: dict[str, dict] = field(default_factory=dict)   # 2026-11-05: Agent con (A03.1...) thuộc một Agent cha
    departments: dict[str, dict] = field(default_factory=dict)  # 2026-11-05: bộ phận có Agent đồng điều hành (dept_*.json)
    executives: dict[str, dict] = field(default_factory=dict)   # Agent đồng điều hành của các bộ phận, theo ID

    def member(self, member_id: str) -> dict:
        if member_id in self.leaders:
            return self.leaders[member_id]
        if member_id in self.agents:
            return self.agents[member_id]
        if member_id in self.subagents:
            return self.subagents[member_id]
        if member_id in self.executives:
            return self.executives[member_id]
        raise KeyError(member_id)

    def boss_of(self, member_id: str) -> str | None:
        m = self.member(member_id)
        if member_id in self.leaders: return m.get("supervisor")
        if member_id in self.subagents: return m.get("parent")
        if member_id in self.executives: return m.get("supervisor")
        return m.get("leader")

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


def clean_path(p) -> str | None:
    """Chuẩn hóa đường dẫn tương đối kiểu 'a/b/'. Trả None nếu rỗng, tuyệt đối, có '.', '..' hoặc '\\'."""
    if not isinstance(p, str) or not p.strip() or p.startswith("/") or "\\" in p:
        return None
    segs = p.strip().split("/")
    if segs and segs[-1] == "":
        segs = segs[:-1]
    if not segs or any(s in ("", ".", "..") for s in segs):
        return None
    return "/".join(segs) + "/"


def scopes_overlap(a: str, b: str) -> bool:
    """Hai vùng ghi chồng nhau nếu một vùng là tiền tố của vùng kia ('*' = một cấp bất kỳ)."""
    sa = [s for s in a.split("/") if s]
    sb = [s for s in b.split("/") if s]
    for x, y in zip(sa, sb):
        if x != y and x != "*" and y != "*":
            return False
    return True


def _items(data, key: str, fname: str, errors: list[str]) -> list[dict]:
    if not isinstance(data, dict) or not isinstance(data.get(key), list):
        errors.append(f"{fname}: phải là object có trường '{key}' dạng danh sách")
        return []
    items = data[key]
    bad = [i for i in items if not isinstance(i, dict) or not isinstance(i.get("id"), str)]
    if bad:
        errors.append(f"{fname}: có {len(bad)} mục không phải object có 'id' dạng chữ")
    return [i for i in items if isinstance(i, dict) and isinstance(i.get("id"), str)]


def _index(items: list[dict], fname: str, errors: list[str]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for it in items:
        if it["id"] in out:
            errors.append(f"{fname}: ID trùng '{it['id']}'")
        out[it["id"]] = it
    return out


def load(reg_dir: Path = REG_DIR) -> Registry:
    errors: list[str] = []
    lead = _load_json(reg_dir / "leaders.json")
    ag = _load_json(reg_dir / "agents.json")
    co = _load_json(reg_dir / "companies.json")
    lg = _load_json(reg_dir / "legacy_map.json")
    leaders = _index(_items(lead, "leaders", "leaders.json", errors), "leaders.json", errors)
    agents = _index(_items(ag, "agents", "agents.json", errors), "agents.json", errors)
    companies = _index(_items(co, "companies", "companies.json", errors), "companies.json", errors)
    sub_path = reg_dir / "subagents.json"
    subagents = _index(_items(_load_json(sub_path), "subagents", "subagents.json", errors), "subagents.json", errors) if sub_path.exists() else {}
    departments: dict[str, dict] = {}
    executives: dict[str, dict] = {}
    for dp in sorted(reg_dir.glob("dept_*.json")):
        d = _load_json(dp)
        dep = d.get("department") if isinstance(d, dict) else None
        if not isinstance(dep, dict) or not isinstance(dep.get("id"), str):
            errors.append(f"{dp.name}: thiếu 'department' có 'id'")
            continue
        if dep["id"] in departments:
            errors.append(f"{dp.name}: bộ phận trùng ID '{dep['id']}'")
        execs = _index(_items(d, "executives", dp.name, errors), dp.name, errors)
        for eid, e in execs.items():
            if eid in executives:
                errors.append(f"{dp.name}: Agent điều hành trùng ID '{eid}'")
            e["department"] = dep["id"]
            executives[eid] = e
        departments[dep["id"]] = {**d, "executive_ids": sorted(execs)}
    legacy = lg.get("units") if isinstance(lg, dict) else None
    if not isinstance(legacy, dict) or not all(isinstance(v, dict) for v in legacy.values()):
        errors.append("legacy_map.json: 'units' phải là object mã → {maps_to: [...]}")
        legacy = {}
    if errors:
        raise RegistryError("Sổ đăng ký không hợp lệ:\n- " + "\n- ".join(errors))
    reg = Registry(leaders=leaders, agents=agents, companies=companies, legacy=legacy, subagents=subagents,
                   departments=departments, executives=executives)
    validate(reg)
    return reg


def validate(reg: Registry) -> None:
    errors: list[str] = []

    # 1. Đúng bộ lãnh đạo đã chốt; ID không trùng giữa lãnh đạo và Agent.
    for must in sorted(EXPECTED_LEADER_IDS - set(reg.leaders)):
        errors.append(f"Thiếu lãnh đạo bắt buộc {must}")
    for extra in sorted(set(reg.leaders) - EXPECTED_LEADER_IDS):
        errors.append(f"Lãnh đạo '{extra}' chưa được Chủ tịch phê duyệt trong mô hình tổ chức")
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
        if l.get("tier") not in TIERS:
            errors.append(f"{lid}: mức quyền '{l.get('tier')}' không hợp lệ")
        if lid == "CHAIRMAN":
            if l.get("supervisor") is not None:
                errors.append("CHAIRMAN không được có cấp trên")
            if l.get("kind") != "human":
                errors.append("CHAIRMAN phải là người (kind=human)")
            continue
        if l.get("kind") != "agent":
            errors.append(f"{lid}: chỉ Chủ tịch là người; lãnh đạo khác phải kind=agent")
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
        elif not isinstance(a.get("leader"), str):
            errors.append(f"{aid}: lãnh đạo phải là một ID dạng chữ")
        elif a.get("leader") not in reg.leaders:
            errors.append(f"{aid}: lãnh đạo '{a.get('leader')}' không tồn tại")
        if a.get("status") not in AGENT_STATUSES:
            errors.append(f"{aid}: trạng thái '{a.get('status')}' không hợp lệ")
        if a.get("tier") not in TIERS:
            errors.append(f"{aid}: mức quyền '{a.get('tier')}' không hợp lệ")
        elif a["tier"] == "SENSITIVE":
            errors.append(f"{aid}: Agent không được giữ quyền SENSITIVE")
        ns = clean_path(a.get("memory_ns"))
        if ns is None or not ns.startswith(f"agents/{aid}/"):
            errors.append(f"{aid}: namespace bộ nhớ phải là đường dẫn sạch trong agents/{aid}/")
        if not isinstance(a.get("write_scope"), list) or not all(clean_path(x) for x in a.get("write_scope", [])):
            errors.append(f"{aid}: vùng ghi phải là danh sách đường dẫn sạch (không '.', '..', rỗng, tuyệt đối)")
        for sc in a.get("write_scope", []) if isinstance(a.get("write_scope"), list) else []:
            c = clean_path(sc)
            if c and c.startswith("agents/") and not c.startswith(f"agents/{aid}/"):
                errors.append(f"{aid}: vùng ghi '{sc}' nằm trong thư mục của Agent khác")
        if not isinstance(a.get("skills", []), list):
            errors.append(f"{aid}: skills phải là danh sách")

    # 5. Quyền cấp dưới <= quyền cấp trên.
    for mid in list(reg.leaders) + list(reg.agents):
        boss = reg.boss_of(mid)
        if not isinstance(boss, str) or boss not in reg.leaders:
            continue
        mt, bt = reg.member(mid).get("tier"), reg.leaders[boss].get("tier")
        if mt in TIERS and bt in TIERS and TIERS.index(mt) > TIERS.index(bt):
            errors.append(f"{mid}: quyền {mt} cao hơn cấp trên {boss} ({bt})")

    # 6. Không chu trình chỉ huy, mọi chuỗi kết thúc ở Chủ tịch (chỉ chạy khi cấu trúc đã đúng kiểu).
    if not errors:
        for mid in list(reg.leaders) + list(reg.agents) + list(reg.executives):
            try:
                c = reg.chain(mid)
                if c[-1] != "CHAIRMAN":
                    errors.append(f"{mid}: chuỗi chỉ huy không về Chủ tịch ({' -> '.join(c)})")
            except RegistryError as e:
                errors.append(str(e))

    # 7. Vùng ghi giữa các Agent không chồng nhau (master mục 09: một nơi ghi).
    scopes = [(aid, clean_path(s)) for aid, a in reg.agents.items()
              if isinstance(a.get("write_scope"), list) for s in a["write_scope"] if clean_path(s)]
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
    # Agent con: A##.n thuộc Agent cha A##; quyền <= cha; vùng ghi nằm TRONG vùng cha và không chồng anh em.
    sub_scopes = []
    for sid, sa in reg.subagents.items():
        par = sa.get("parent")
        if not re.fullmatch(r"A\d{2}\.\d+", sid) or par not in reg.agents or not sid.startswith(par + "."):
            errors.append(f"{sid}: Agent con phải có dạng <cha>.<số> và cha có trong A01..A20"); continue
        for k in ("name", "status", "tier", "mission", "memory_ns", "write_scope", "out_of_scope"):
            if k not in sa: errors.append(f"{sid}: thiếu trường {k}")
        if sa.get("status") not in AGENT_STATUSES: errors.append(f"{sid}: trạng thái không hợp lệ")
        pt, st_ = reg.agents[par].get("tier"), sa.get("tier")
        if st_ not in TIERS or (pt in TIERS and TIERS.index(st_) > TIERS.index(pt)):
            errors.append(f"{sid}: mức quyền {st_} vượt cha {par} ({pt})")
        pscopes = [clean_path(x) for x in reg.agents[par].get("write_scope", [])]
        for w in sa.get("write_scope", []):
            cw = clean_path(w)
            if cw is None or not any(ps and cw.startswith(ps) for ps in pscopes):
                errors.append(f"{sid}: vùng ghi '{w}' phải nằm trong vùng của {par}")
            else:
                for oid, ow in sub_scopes:
                    if scopes_overlap(cw, ow): errors.append(f"{sid}: vùng ghi '{w}' chồng {oid} ('{ow}')")
                sub_scopes.append((sid, cw))
        if clean_path(sa.get("memory_ns", "")) is None or not clean_path(sa.get("memory_ns", "")).startswith(clean_path(reg.agents[par]["memory_ns"]) or "-"):
            errors.append(f"{sid}: bộ nhớ phải nằm trong {reg.agents[par].get('memory_ns')}")

    # 10. Bộ phận có Agent đồng điều hành (2026-11-05, lệnh Chủ tịch): ngang một lãnh đạo CHỈ trong phạm vi bộ phận.
    taken = set(reg.leaders) | set(reg.agents) | set(reg.subagents)
    domains: list[tuple[str, str]] = []
    for did, d in reg.departments.items():
        dom = clean_path((d.get("department") or {}).get("domain"))
        if dom is None:
            errors.append(f"Bộ phận {did}: 'domain' phải là đường dẫn sạch")
            continue
        if EXPECTED_DEPARTMENTS.get(did) != dom:
            errors.append(f"Bộ phận '{did}' với vùng '{dom}' chưa được Chủ tịch phê duyệt trong mô hình tổ chức")
            continue
        if not d.get("decision_ref"):
            errors.append(f"Bộ phận {did}: thiếu decision_ref (lập bộ phận là quyết định của Chủ tịch)")
        if not d.get("executive_ids"):
            errors.append(f"Bộ phận {did}: phải có ít nhất một Agent điều hành")
        for aid, sc in scopes:
            if scopes_overlap(dom, sc):
                errors.append(f"Bộ phận {did}: vùng '{dom}' chồng vùng ghi của {aid} ('{sc}')")
        for od, odom in domains:
            if scopes_overlap(dom, odom):
                errors.append(f"Bộ phận {did}: vùng '{dom}' chồng bộ phận {od} ('{odom}')")
        domains.append((did, dom))
        for eid in d.get("executive_ids", []):
            e = reg.executives[eid]
            if not EXEC_ID_RE.fullmatch(eid):
                errors.append(f"{eid!r}: ID Agent điều hành phải dạng chữ thường nối gạch (vd. agent-tool)")
            if eid in taken:
                errors.append(f"{eid}: ID trùng với lãnh đạo/Agent đã có")
            for k in ("name", "role", "status", "tier", "peer_of", "peer_scope", "memory_ns", "write_scope", "out_of_scope"):
                if k not in e:
                    errors.append(f"{eid}: thiếu trường '{k}'")
            if e.get("kind") != "agent":
                errors.append(f"{eid}: Agent điều hành phải kind=agent")
            if e.get("supervisor") != "CHAIRMAN":
                errors.append(f"{eid}: Agent điều hành bộ phận báo thẳng Chủ tịch (supervisor=CHAIRMAN)")
            if e.get("status") not in AGENT_STATUSES:
                errors.append(f"{eid}: trạng thái '{e.get('status')}' không hợp lệ")
            if e.get("tier") not in TIERS:
                errors.append(f"{eid}: mức quyền '{e.get('tier')}' không hợp lệ")
            elif e["tier"] == "SENSITIVE":
                errors.append(f"{eid}: chỉ Chủ tịch giữ quyền SENSITIVE")
            peers = e.get("peer_of")
            if not isinstance(peers, list) or not peers or len(set(peers)) != len(peers) \
                    or any(not isinstance(p, str) or p not in reg.leaders or p == "CHAIRMAN" for p in peers):
                errors.append(f"{eid}: peer_of phải là danh sách lãnh đạo có thật, không trùng, không gồm Chủ tịch")
            else:
                for p in peers:  # ngang quyền, không vượt quyền
                    pt = reg.leaders[p].get("tier")
                    if e.get("tier") in TIERS and pt in TIERS and TIERS.index(e["tier"]) > TIERS.index(pt):
                        errors.append(f"{eid}: quyền {e['tier']} cao hơn lãnh đạo ngang cấp {p} ({pt})")
            if not isinstance(e.get("peer_scope"), str) or not e.get("peer_scope", "").strip():
                errors.append(f"{eid}: peer_scope phải ghi rõ phạm vi ngang quyền")
            ws = e.get("write_scope")
            if not isinstance(ws, list) or not ws or not all(clean_path(x) and clean_path(x).startswith(dom) for x in ws):
                errors.append(f"{eid}: vùng ghi phải nằm trong vùng bộ phận '{dom}'")
            ns = clean_path(e.get("memory_ns"))
            if ns is None or not ns.startswith(dom):
                errors.append(f"{eid}: bộ nhớ phải nằm trong vùng bộ phận '{dom}'")
            co = e.get("clone_of")
            if co is not None and (co == eid or co not in d.get("executive_ids", [])):
                errors.append(f"{eid}: clone_of phải là Agent điều hành khác cùng bộ phận")
        seen_b = set()
        for b in d.get("branches", []) if isinstance(d.get("branches"), list) else []:
            bid = b.get("id") if isinstance(b, dict) else None
            if not isinstance(bid, str) or not re.fullmatch(r"[a-z0-9]{1,8}", bid) or bid in seen_b:
                errors.append(f"Bộ phận {did}: nhánh '{bid}' không hợp lệ hoặc trùng")
                continue
            seen_b.add(bid)
            if b.get("tier") not in TIERS or b.get("tier") == "SENSITIVE":
                errors.append(f"Bộ phận {did}: nhánh {bid} có mức quyền không hợp lệ")
                continue
            for eid in d.get("executive_ids", []):  # nhánh (và Agent con của nó) không vượt quyền người điều hành
                et = reg.executives[eid].get("tier")
                if et in TIERS and TIERS.index(b["tier"]) > TIERS.index(et):
                    errors.append(f"Bộ phận {did}: nhánh {bid} ({b['tier']}) cao quyền hơn Agent điều hành {eid} ({et})")
        lim = d.get("limits", {})
        if not isinstance(lim, dict):
            errors.append(f"Bộ phận {did}: limits phải là object")
        else:
            for key, default, lo, hi in (("max_concurrent_subagents", 2, 1, 8), ("max_auto_releases_per_session", 1, 0, 3)):
                v = lim.get(key, default)
                if isinstance(v, bool) or not isinstance(v, int) or not lo <= v <= hi:
                    errors.append(f"Bộ phận {did}: limits.{key} phải là số nguyên từ {lo} đến {hi}")

    if len(reg.legacy) != EXPECTED_LEGACY_COUNT:
        errors.append(f"Bảng mã cũ phải có đủ {EXPECTED_LEGACY_COUNT} đơn vị KING AI (đang có {len(reg.legacy)})")
    for code, u in reg.legacy.items():
        if not isinstance(u.get("maps_to"), list) or not u["maps_to"]:
            errors.append(f"Mã cũ {code}: maps_to phải là danh sách không rỗng")
            continue
        for t in u["maps_to"]:
            if t not in targets:
                errors.append(f"Mã cũ {code}: đích '{t}' không tồn tại")

    if errors:
        raise RegistryError("Sổ đăng ký không hợp lệ:\n- " + "\n- ".join(errors))
