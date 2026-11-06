"""Bộ não điều hành: quyền Agent theo nhiệm vụ, lượt "Rà soát bản build" của Agent Vua, lịch chạy.

Agent Vua (VUA_CEO) nhận một NHIỆM VỤ có ID trong đúng một dự án; quyền đọc cấp qua agent_grants và thu hồi khi
xong. Mọi dữ liệu Agent đọc đi qua AgentScope → không đọc được dự án/nhiệm vụ khác.

Nhãn bằng chứng trong báo cáo:
  VERIFIED  — đọc trực tiếp lúc chạy (database, git, bản đồ ở commit ghi rõ).
  REPORTED  — điều tài liệu/báo cáo khác KHẲNG ĐỊNH; lượt rà soát này không chạy lại để kiểm.
  UNKNOWN   — không đọc được / không có dữ liệu.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import socket
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from . import ai_hub
from . import decisions as DC
from . import graphmap as GM
from .auth import Actor, audit, require_owner, require_project
from .db import connect, tx
from .httpcore import ApiError, bad_request
from .platform import get_setting, put_setting
from .util import iso_after, new_id, now_iso, parse_iso

REVIEW_AGENT = "VUA_CEO"
REVIEW_VERSION = "review-rules-1"
TEST_REPORT = "docs/infinity/TEST_RESULTS.md"
CODE_PATHS = ("crown_infinity", "tests")
DEFAULT_INTERVAL = 120
HEARTBEAT_KEY = "scheduler.heartbeat"
OWNERS = {"VUA_CEO": "Agent Vua", "A18": "A18 Xưởng phần mềm", "A20": "A20 Kiểm định độc lập",
          "A11": "A11 Sửa lỗi", "CHAIRMAN": "Chủ tịch (OWNER)"}


# ---------------------------------------------------------------- quyền Agent theo nhiệm vụ

@dataclass(frozen=True)
class AgentScope:
    agent_id: str
    project_id: str
    task_id: str
    permission: str


def ensure_agent(conn, agent_id: str, name: str) -> None:
    conn.execute("INSERT OR IGNORE INTO agent_identities(agent_id,display_name,source,created_at,updated_at) "
                 "VALUES (?,?,'system',?,?)", (agent_id, name, now_iso(), now_iso()))


def grant(conn, actor: Actor, agent_id: str, task_id: str, permission: str = "READ") -> dict:
    """OWNER cấp cho Agent quyền trên ĐÚNG một nhiệm vụ (và dự án của nhiệm vụ đó)."""
    require_owner(actor)
    if permission not in ("READ", "READ_PROPOSE"):
        raise bad_request("permission phải là READ hoặc READ_PROPOSE")
    with tx(conn):
        t = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if not t or not t["project_id"]:
            raise ApiError(404, "NOT_FOUND", "Không có nhiệm vụ gắn dự án với mã này")
        if t["status"] in ("DONE", "CANCELLED"):
            raise ApiError(409, "TASK_CLOSED", "Nhiệm vụ đã đóng")
        if not conn.execute("SELECT 1 FROM agent_identities WHERE agent_id=?", (agent_id,)).fetchone():
            raise ApiError(404, "NOT_FOUND", "Không có Agent này")
        gid = new_id("G")
        conn.execute("INSERT INTO agent_grants(id,agent_id,project_id,task_id,permission,granted_by,granted_at) "
                     "VALUES (?,?,?,?,?,?,?)", (gid, agent_id, t["project_id"], task_id, permission, actor.id, now_iso()))
        audit(conn, actor, "agent.grant", "task", task_id, detail={"agent_id": agent_id, "permission": permission})
    return dict(conn.execute("SELECT * FROM agent_grants WHERE id=?", (gid,)).fetchone())


def revoke_grant(conn, actor: Actor, gid: str) -> dict:
    require_owner(actor)
    with tx(conn):
        n = conn.execute("UPDATE agent_grants SET revoked_at=?, revoked_by=? WHERE id=? AND revoked_at IS NULL",
                         (now_iso(), actor.id, gid)).rowcount
        if not n:
            raise ApiError(404, "NOT_FOUND", "Không có quyền đang hiệu lực với mã này")
        audit(conn, actor, "agent.grant.revoke", "agent_grant", gid)
    return dict(conn.execute("SELECT * FROM agent_grants WHERE id=?", (gid,)).fetchone())


def list_grants(conn, actor: Actor, project_id: str | None = None) -> dict:
    require_owner(actor)
    q, a = "SELECT * FROM agent_grants", []
    if project_id:
        q, a = q + " WHERE project_id=?", [project_id]
    return {"items": [dict(r) for r in conn.execute(q + " ORDER BY granted_at DESC LIMIT 200", a)]}


def scope_for(conn, agent: Actor, task_id: str, need: str = "READ") -> AgentScope:
    """Agent chỉ qua được khi có quyền còn hiệu lực trên đúng nhiệm vụ đang mở."""
    r = conn.execute("SELECT g.*, t.status AS task_status FROM agent_grants g JOIN tasks t ON t.id=g.task_id "
                     "WHERE g.agent_id=? AND g.task_id=? AND g.revoked_at IS NULL ORDER BY g.granted_at DESC LIMIT 1",
                     (agent.id, task_id)).fetchone()
    if not r or r["task_status"] in ("DONE", "CANCELLED"):
        audit(conn, agent, "agent.scope", "task", str(task_id)[:64], result="DENIED")
        raise ApiError(403, "NOT_GRANTED", "Agent không được cấp nhiệm vụ này (hoặc nhiệm vụ đã đóng)")
    if need == "PROPOSE" and r["permission"] != "READ_PROPOSE":
        raise ApiError(403, "READ_ONLY", "Quyền hiện tại chỉ là ĐỌC; chưa được ghi đề xuất")
    return AgentScope(agent.id, r["project_id"], task_id, r["permission"])


def agent_grants(conn, agent: Actor) -> dict:
    rows = conn.execute("SELECT g.id, g.task_id, g.project_id, g.permission, g.granted_at, t.title AS task_title, "
                        "t.status AS task_status, p.name AS project_name FROM agent_grants g JOIN tasks t ON t.id=g.task_id "
                        "JOIN projects p ON p.id=g.project_id WHERE g.agent_id=? AND g.revoked_at IS NULL AND "
                        "t.status NOT IN ('DONE','CANCELLED') ORDER BY g.granted_at DESC", (agent.id,)).fetchall()
    return {"agent_id": agent.id, "items": [dict(r) for r in rows]}


def scoped_map(conn, scope: AgentScope, map_id: str | None):
    if map_id:
        m = GM.map_row(conn, map_id)
        if m["project_id"] != scope.project_id:
            raise ApiError(403, "OUT_OF_SCOPE", "Bản đồ không thuộc dự án của nhiệm vụ")
        return GM.map_info(conn, map_id)
    m = GM.latest_map(conn, scope.project_id)
    if not m:
        raise ApiError(404, "NO_MAP", "Dự án chưa có bản đồ mã")
    return m


# ---------------------------------------------------------------- thu thập đầu vào (chỉ đọc)

def _git(repo: Path, *args, strip: bool = True) -> str | None:
    ok, out = GM.run_git(repo, *args)
    if not ok:
        return None
    text = out.decode("utf-8", "replace")
    return text.strip() if strip else text.rstrip("\n")


def commit_info(repo: Path) -> dict:
    sha = _git(repo, "rev-parse", "HEAD")
    if not sha:
        return {"available": False, "reason": "Không đọc được git (máy chủ chưa cài git hoặc thư mục không phải repo)"}
    status = _git(repo, "status", "--porcelain", "--untracked-files=no", strip=False) or ""  # giữ khoảng trắng cột trạng thái
    dirty = [ln[3:] for ln in status.splitlines() if ln.strip()]
    return {"available": True, "sha": sha, "branch": _git(repo, "rev-parse", "--abbrev-ref", "HEAD"),
            "committed_at": _git(repo, "log", "-1", "--format=%cI"), "subject": _git(repo, "log", "-1", "--format=%s"),
            "dirty_files": dirty[:50], "dirty_count": len(dirty),
            "dirty_hash": hashlib.sha256(status.encode()).hexdigest()[:16] if dirty else ""}


def test_report_info(repo: Path, head: str | None) -> dict:
    p = Path(repo) / TEST_REPORT
    if not p.is_file():
        return {"available": False, "reason": f"Không có {TEST_REPORT}"}
    text = p.read_text(encoding="utf-8", errors="replace")
    rows = []
    for ln in text.splitlines():
        cells = [c.strip() for c in ln.strip().strip("|").split("|")] if ln.startswith("|") else []
        if len(cells) >= 4 and re.fullmatch(r"\d{1,2}", cells[0]):
            res = cells[-1]
            status = ("FAIL" if "FAIL" in res else "NOT VERIFIED" if "NOT VERIFIED" in res
                      else "SKIPPED" if "SKIPPED" in res else "PASS" if "PASS" in res else "UNKNOWN")
            rows.append({"no": int(cells[0]), "criterion": re.sub(r"\*", "", cells[1])[:200], "claimed": status,
                         "raw": re.sub(r"\*", "", res)[:200]})
    last = _git(repo, "log", "-1", "--format=%H", "--", TEST_REPORT)
    changed_after = []
    if last and head:
        out = _git(repo, "diff", "--name-only", last, head, "--", *CODE_PATHS) or ""
        changed_after = [f for f in out.splitlines() if f]
    return {"available": True, "path": TEST_REPORT, "sha256": hashlib.sha256(text.encode()).hexdigest()[:16],
            "report_commit": last, "rows": rows, "code_changed_after_report": changed_after[:100],
            "headline": next((ln.strip() for ln in text.splitlines() if "test" in ln.lower() and "OK" in ln), "")}


def _postponed_terms(decs: list[dict]) -> list[str]:
    terms = []
    for d in decs:
        if re.search(r"ho[aã]n|postpone|tạm dừng", d["content"], re.I):
            terms += re.findall(r"([A-ZĐ][\wÀ-ỹ]*(?: [A-ZĐ][\wÀ-ỹ]*)* Studio)", d["content"])
    return sorted(set(terms))


def _ai_step(conn, provider_id: str | None, purpose: str, payload: dict) -> dict:
    """Thử gọi AI qua AI Hub. Ghi CHÍNH XÁC có gọi thật hay không."""
    if not provider_id:
        return {"status": "SKIPPED", "ai_called": False, "reason": "Không chọn model"}
    prov = conn.execute("SELECT * FROM ai_providers WHERE id=?", (provider_id,)).fetchone()
    label = f"{prov['name']} / {prov['model'] or '?'}" if prov else provider_id
    est_tokens = 800
    g = ai_hub.gate(conn, provider_id, 0.0 if prov and not prov["paid"] else None, est_tokens)
    if not g["allowed"]:
        return {"status": "SKIPPED", "ai_called": False, "model": label,
                "reason": "Bị cổng AI Hub chặn: " + "; ".join(r["message"] for r in g["reasons"])}
    try:
        res = ai_hub.call_model(conn, provider_id, payload, est_usd=0.0, est_output_tokens=est_tokens, purpose=purpose)
    except ApiError as exc:
        return {"status": "SKIPPED", "ai_called": False, "model": label, "reason": f"{exc.code}: {exc.message}"}
    return {"status": "DONE", "ai_called": True, "model": label, "output": str(res.get("text", ""))[:4000],
            "usage_id": res.get("usage_id")}


def gather_inputs(conn, scope: AgentScope, repo: Path, models: dict) -> dict:
    decs = DC.active_decisions(conn, scope.project_id)
    proposals = conn.execute("SELECT id, title FROM decisions WHERE project_id=? AND status='PROPOSED'",
                             (scope.project_id,)).fetchall()
    ci = commit_info(repo)
    gm = GM.latest_map(conn, scope.project_id)
    tr = test_report_info(repo, ci.get("sha"))
    fp_src = {"v": REVIEW_VERSION, "project": scope.project_id,
              "commit": ci.get("sha"), "dirty": ci.get("dirty_hash", ""),
              "decisions": [f"{d['id']}:{d['revision']}" for d in decs],
              "map": gm["id"] if gm else None, "test_report": tr.get("sha256"),
              "analysis_model": models.get("analysis_provider_id") or None,
              "verify_model": models.get("verify_provider_id") or None}
    fp = hashlib.sha256(json.dumps(fp_src, sort_keys=True).encode()).hexdigest()
    return {"fingerprint": fp, "fingerprint_inputs": fp_src, "decisions": decs,
            "proposals": [dict(p) for p in proposals], "commit": ci, "map": gm, "test_report": tr}


# ---------------------------------------------------------------- lượt rà soát

def _impact(conn, gm: dict, repo: Path, head: str | None) -> dict:
    out = {"map_commit": gm["source_commit"], "fresh": head == gm["source_commit"], "changed_files": [],
           "impacted": [], "sample_relation": None}
    if head and head != gm["source_commit"]:
        diff = _git(repo, "diff", "--name-only", gm["source_commit"], head, "--", gm["scope_prefix"]) or ""
        out["changed_files"] = [f for f in diff.splitlines() if f][:50]
    prefix = gm["scope_prefix"] + "/"
    for f in out["changed_files"][:10]:
        rel = f[len(prefix):] if f.startswith(prefix) else f
        n = conn.execute("SELECT COUNT(*) FROM graph_nodes WHERE map_id=? AND source_file=?", (gm["id"], rel)).fetchone()[0]
        callers = conn.execute(
            "SELECT COUNT(DISTINCT e.source) FROM graph_edges e JOIN graph_nodes n ON n.map_id=e.map_id AND "
            "n.node_id=e.target WHERE e.map_id=? AND n.source_file=? AND e.relation IN ('calls','imports','imports_from',"
            "'uses','indirect_call')", (gm["id"], rel)).fetchone()[0]
        out["impacted"].append({"file": f, "nodes_in_map": n, "incoming_dependents": callers})
    # một quan hệ thật để đối chiếu (ưu tiên EXTRACTED) trên tệp đổi đầu tiên hoặc toàn bản đồ
    focus = out["changed_files"][0][len(prefix):] if out["changed_files"] and out["changed_files"][0].startswith(prefix) else None
    row = conn.execute("SELECT * FROM graph_edges WHERE map_id=? AND confidence='EXTRACTED' AND relation='calls' "
                       + ("AND source_file=? " if focus else "") + "ORDER BY id LIMIT 1",
                       (gm["id"], focus) if focus else (gm["id"],)).fetchone()
    if row:
        s = GM._node(conn, gm["id"], row["source"])
        t = GM._node(conn, gm["id"], row["target"])
        out["sample_relation"] = {"source": s["label"] if s else row["source"], "relation": row["relation"],
                                  "target": t["label"] if t else row["target"], "confidence": row["confidence"],
                                  "source_file": row["source_file"], "source_line": row["source_line"]}
    return out


def _synthesize(inp: dict, impact: dict | None, ai_steps: dict) -> dict:
    verified, reported, unknown, actions = [], [], [], []
    ci, tr, gm = inp["commit"], inp["test_report"], inp["map"]
    decs = inp["decisions"]
    verified.append({"text": f"{len(decs)} quyết định đang hiệu lực của dự án", "evidence": "database (decisions, kind=DECISION, status=ACTIVE)",
                     "items": [f"{d['title']} — xác nhận bởi {d.get('confirmed_by_name', d['confirmed_by'])} lúc {d['confirmed_at']}" for d in decs]})
    if inp["proposals"]:
        unknown.append({"text": f"{len(inp['proposals'])} đề xuất của AI/Agent CHƯA được xác nhận — không dùng làm căn cứ",
                        "evidence": "database (kind=PROPOSAL)"})
    if ci.get("available"):
        verified.append({"text": f"Commit {ci['sha'][:12]} trên nhánh {ci['branch']} ({ci['committed_at']}): {ci['subject']}",
                         "evidence": "git rev-parse/log lúc chạy"})
        if ci["dirty_count"]:
            verified.append({"text": f"Có {ci['dirty_count']} tệp đã sửa nhưng CHƯA commit: {', '.join(ci['dirty_files'][:5])}",
                             "evidence": "git status"})
            actions.append({"action": "Commit hoặc bỏ các thay đổi chưa commit rồi rà soát lại, để báo cáo gắn đúng một commit",
                            "owner": "A18", "priority": "CAO"})
    else:
        unknown.append({"text": "Không xác định được commit", "evidence": ci.get("reason")})
        actions.append({"action": "Cài Git for Windows trên máy chạy máy chủ hoặc chạy trong thư mục repo", "owner": "CHAIRMAN",
                        "priority": "CAO"})
    if gm and impact:
        verified.append({"text": (f"Bản đồ {gm['id']} (Graphify {gm['tool_version']}, commit {gm['source_commit'][:12]}, "
                                  f"{gm['node_count']} nút / {gm['edge_count']} cạnh: {gm['extracted_count']} EXTRACTED, "
                                  f"{gm['inferred_count']} INFERRED)"), "evidence": "database graph_maps"})
        if impact["fresh"]:
            verified.append({"text": "Bản đồ được dựng đúng commit đang rà soát", "evidence": "so sánh commit"})
        else:
            verified.append({"text": f"Bản đồ CŨ hơn commit hiện tại; {len(impact['changed_files'])} tệp trong phạm vi đã đổi",
                             "evidence": "git diff --name-only", "items": [
                                 f"{x['file']}: {x['nodes_in_map']} nút trong bản đồ, {x['incoming_dependents']} nơi phụ thuộc"
                                 for x in impact["impacted"]]})
            actions.append({"action": f"Dựng lại bản đồ Graphify ở commit {ci.get('sha', '?')[:12]} (nút 'Dựng bản đồ' hoặc "
                                      "`py -m crown_infinity graph build`)", "owner": "A18", "priority": "TRUNG BÌNH"})
        if impact["sample_relation"]:
            s = impact["sample_relation"]
            verified.append({"text": f"Quan hệ mẫu: {s['source']} --{s['relation']}--> {s['target']} [{s['confidence']}] "
                                     f"tại {gm['scope_prefix']}/{s['source_file']}:{s['source_line']}",
                             "evidence": "truy vấn bản đồ (nhãn EXTRACTED = đọc trực tiếp từ mã)"})
    else:
        unknown.append({"text": "Dự án chưa có bản đồ mã", "evidence": "database graph_maps trống"})
        actions.append({"action": "Dựng bản đồ Graphify lần đầu cho phạm vi crown_infinity", "owner": "A18", "priority": "TRUNG BÌNH"})
    if tr.get("available"):
        counts = {}
        for r in tr["rows"]:
            counts[r["claimed"]] = counts.get(r["claimed"], 0) + 1
        reported.append({"text": f"{TEST_REPORT} khẳng định: " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items())),
                         "evidence": f"tài liệu, commit {str(tr['report_commit'])[:12]} — lượt rà soát KHÔNG chạy lại test",
                         "items": [f"#{r['no']} {r['criterion']}: {r['raw']}" for r in tr["rows"]]})
        if tr["code_changed_after_report"]:
            verified.append({"text": f"{len(tr['code_changed_after_report'])} tệp mã/test đã đổi SAU lần cập nhật báo cáo test",
                             "evidence": "git diff giữa commit báo cáo và HEAD",
                             "items": tr["code_changed_after_report"][:10]})
            actions.append({"action": "Chạy lại toàn bộ test (python -m unittest discover -s tests -t . và test giao diện) rồi "
                                      "cập nhật TEST_RESULTS.md", "owner": "A20", "priority": "CAO"})
        if any(r["claimed"] == "FAIL" for r in tr["rows"]):
            actions.append({"action": "Sửa các tiêu chí FAIL trong báo cáo test", "owner": "A11", "priority": "CAO"})
        nv = [r for r in tr["rows"] if r["claimed"] in ("NOT VERIFIED", "SKIPPED")]
        if nv or "NOT VERIFIED" in json.dumps(tr["rows"], ensure_ascii=False):
            actions.append({"action": "Chạy thử trên máy Windows (scripts\\infinity\\start.ps1) và ghi kết quả các mục NOT VERIFIED",
                            "owner": "CHAIRMAN", "priority": "TRUNG BÌNH"})
    else:
        unknown.append({"text": "Không có báo cáo test", "evidence": tr.get("reason")})
        actions.append({"action": "Lập báo cáo test", "owner": "A20", "priority": "CAO"})
    terms = _postponed_terms(decs)
    if terms and ci.get("available"):
        changed = (impact or {}).get("changed_files", []) + tr.get("code_changed_after_report", []) + ci.get("dirty_files", [])
        hits = [f for f in changed if any(t.split()[0].lower() in f.lower() for t in terms)]
        verified.append({"text": (f"Đối chiếu quyết định tạm hoãn ({', '.join(terms)}): " +
                                  (f"CÓ {len(hits)} tệp đổi có tên liên quan" if hits else "không thấy tệp đổi nào có tên liên quan")),
                         "evidence": "so tên tệp đã đổi với từ khóa trong quyết định (kiểm theo tên, không đọc nội dung)",
                         "items": hits[:10]})
        if hits:
            actions.append({"action": "Xem lại các thay đổi chạm tới mục đang tạm hoãn theo quyết định của Chủ tịch",
                            "owner": "VUA_CEO", "priority": "CAO"})
    for key, step in ai_steps.items():
        line = f"Bước AI '{key}': {'ĐÃ GỌI ' + step.get('model', '') if step['ai_called'] else 'KHÔNG gọi AI'}"
        (verified if step["ai_called"] else unknown).append(
            {"text": line + ("" if step["ai_called"] else f" — {step['reason']}"), "evidence": "nhật ký AI Hub"})
    for a in actions:
        a["owner_name"] = OWNERS.get(a["owner"], a["owner"])
        a["status"] = "ĐỀ XUẤT"
    return {"verified": verified, "reported": reported, "unknown": unknown, "next_actions": actions,
            "policy": "Chỉ đọc và lập báo cáo. Không merge, không deploy, không đổi ngân sách."}


def _render_md(run_id: str, task_id: str, inp: dict, rep: dict, steps: list) -> str:
    L = [f"# Báo cáo rà soát bản build {run_id}", f"Nhiệm vụ {task_id} · Agent Vua (VUA_CEO) · tạo lúc {now_iso()}", ""]
    for title, key in (("Đã kiểm chứng (VERIFIED)", "verified"), ("Chỉ được báo cáo, chưa kiểm lại (REPORTED)", "reported"),
                       ("Chưa biết / không dùng làm căn cứ (UNKNOWN)", "unknown")):
        L.append(f"## {title}")
        for x in rep[key] or [{"text": "—", "evidence": ""}]:
            L.append(f"- {x['text']}" + (f" (nguồn: {x['evidence']})" if x.get("evidence") else ""))
            for it in x.get("items", [])[:12]:
                L.append(f"  - {it}")
        L.append("")
    L.append("## Việc tiếp theo (đề xuất — cần người duyệt)")
    for a in rep["next_actions"] or [{"action": "Không có", "owner_name": "—", "priority": ""}]:
        L.append(f"- [{a.get('priority', '')}] {a['action']} — phụ trách: {a['owner_name']}")
    L += ["", "## Các bước đã chạy"]
    for s in steps:
        L.append(f"- {s['name']}: {s['status']} · gọi AI: {'CÓ' if s.get('ai_called') else 'KHÔNG'}"
                 + (f" · {s['note']}" if s.get("note") else ""))
    L += ["", rep["policy"]]
    return "\n".join(L)


def run_review(conn, actor: Actor, repo: Path, project_id: str, models: dict | None = None,
               trigger: str = "MANUAL") -> dict:
    """Tạo nhiệm vụ cho Agent Vua, cấp quyền đọc đúng dự án, chạy các bước, thu hồi quyền, lưu báo cáo."""
    require_owner(actor)
    models = {k: v for k, v in (models or {}).items() if k in ("analysis_provider_id", "verify_provider_id") and v}
    for v in models.values():
        if not isinstance(v, str) or not conn.execute("SELECT 1 FROM ai_providers WHERE id=?", (v,)).fetchone():
            raise bad_request("Model đã chọn không có trong AI Hub")
    with tx(conn):
        require_project(conn, actor, project_id, "write")
        ensure_agent(conn, REVIEW_AGENT, "Agent Vua (kiêm EX-CEO)")
        tid, ts = new_id("T"), now_iso()
        conn.execute("INSERT INTO tasks(id,project_id,title,description,status,assignee,created_by,created_at,updated_at) "
                     "VALUES (?,?,?,?,'IN_PROGRESS',?,?,?,?)",
                     (tid, project_id, "Rà soát bản build", f"Kích hoạt: {trigger}. Quyền: chỉ đọc, lập báo cáo.",
                      REVIEW_AGENT, actor.id, ts, ts))
        gid = new_id("G")
        conn.execute("INSERT INTO agent_grants(id,agent_id,project_id,task_id,permission,granted_by,granted_at) "
                     "VALUES (?,?,?,?,'READ',?,?)", (gid, REVIEW_AGENT, project_id, tid, actor.id, ts))
    agent = Actor("AGENT", REVIEW_AGENT, "", "Agent Vua")
    scope = scope_for(conn, agent, tid)
    steps: list[dict] = []
    try:
        inp = gather_inputs(conn, scope, repo, models)
        steps.append({"name": "Đọc quyết định còn hiệu lực", "status": "DONE", "ai_called": False,
                      "note": f"{len(inp['decisions'])} quyết định, {len(inp['proposals'])} đề xuất chưa xác nhận"})
        steps.append({"name": "Xác định commit", "status": "DONE" if inp["commit"].get("available") else "FAILED",
                      "ai_called": False, "note": inp["commit"].get("sha", inp["commit"].get("reason", ""))[:60]})
        with tx(conn):
            dup = conn.execute("SELECT id FROM review_runs WHERE project_id=? AND input_fingerprint=? AND status IN "
                               "('RUNNING','SUCCEEDED') ORDER BY created_at LIMIT 1",
                               (project_id, inp["fingerprint"])).fetchone()
            rid = new_id("RV")
            status = "SKIPPED_DUPLICATE" if dup else "RUNNING"
            conn.execute("INSERT INTO review_runs(id,task_id,project_id,agent_id,trigger,requested_by,status,"
                         "input_fingerprint,inputs_json,duplicate_of,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                         (rid, tid, project_id, REVIEW_AGENT, trigger, actor.id, status, inp["fingerprint"],
                          json.dumps(inp["fingerprint_inputs"], ensure_ascii=False), dup["id"] if dup else None, now_iso()))
        if dup:
            _close(conn, tid, gid, "DONE", f"Bỏ qua: cùng commit và cùng đầu vào với {dup['id']}")
            with tx(conn):
                conn.execute("UPDATE review_runs SET steps_json=?, finished_at=? WHERE id=?",
                             (json.dumps(steps + [{"name": "Chống chạy trùng", "status": "SKIPPED", "ai_called": False,
                                                   "note": f"trùng đầu vào với {dup['id']}"}], ensure_ascii=False), now_iso(), rid))
                audit(conn, actor, "review.skip_duplicate", "review_run", rid, detail={"duplicate_of": dup["id"]})
            return get_run(conn, rid)
        impact = None
        if inp["map"]:
            impact = _impact(conn, scoped_map(conn, scope, inp["map"]["id"]), repo, inp["commit"].get("sha"))
            steps.append({"name": "Tra bản đồ mã", "status": "DONE", "ai_called": False,
                          "note": f"{inp['map']['id']} · {'đúng commit' if impact['fresh'] else 'cũ hơn HEAD'}"})
        else:
            steps.append({"name": "Tra bản đồ mã", "status": "SKIPPED", "ai_called": False, "note": "chưa có bản đồ"})
        steps.append({"name": "Đọc báo cáo test", "status": "DONE" if inp["test_report"].get("available") else "SKIPPED",
                      "ai_called": False, "note": "REPORTED — không chạy lại test"})
        summary_payload = {"decisions": [d["title"] for d in inp["decisions"]], "commit": inp["commit"].get("sha"),
                           "test_rows": inp["test_report"].get("rows", [])[:20]}
        ai_steps = {"phân tích": _ai_step(conn, models.get("analysis_provider_id"), "build_review.analysis", summary_payload),
                    "kiểm tra lại": _ai_step(conn, models.get("verify_provider_id"), "build_review.verify", summary_payload)}
        for k, s in ai_steps.items():
            steps.append({"name": f"AI {k}", "status": s["status"], "ai_called": s["ai_called"],
                          "note": s.get("reason") or s.get("model", "")})
        rep = _synthesize(inp, impact, ai_steps)
        steps.append({"name": "Tổng hợp theo quy tắc", "status": "DONE", "ai_called": False, "note": REVIEW_VERSION})
        md = _render_md(rid, tid, inp, rep, steps)
        ai_calls = sum(1 for s in ai_steps.values() if s["ai_called"])
        with tx(conn):
            conn.execute("UPDATE review_runs SET status='SUCCEEDED', steps_json=?, report_json=?, report_md=?, ai_calls=?, "
                         "finished_at=? WHERE id=?", (json.dumps(steps, ensure_ascii=False),
                                                      json.dumps(rep | {"inputs": _public_inputs(inp)}, ensure_ascii=False),
                                                      md, ai_calls, now_iso(), rid))
            audit(conn, actor, "review.run", "review_run", rid, detail={"task_id": tid, "trigger": trigger, "ai_calls": ai_calls})
        _close(conn, tid, gid, "DONE", f"Báo cáo {rid}")
        return get_run(conn, rid)
    except Exception as exc:
        err = f"{type(exc).__name__}: {getattr(exc, 'message', str(exc))}"[:500]
        try:
            with tx(conn):
                conn.execute("UPDATE review_runs SET status='FAILED', error=?, steps_json=?, finished_at=? WHERE task_id=? "
                             "AND status='RUNNING'", (err, json.dumps(steps, ensure_ascii=False), now_iso(), tid))
            _close(conn, tid, gid, "BLOCKED", err)
        except sqlite3.Error:
            pass
        raise


def _public_inputs(inp: dict) -> dict:
    return {"commit": inp["commit"], "map": inp["map"], "test_report": {k: v for k, v in inp["test_report"].items() if k != "rows"},
            "decision_ids": [d["id"] for d in inp["decisions"]], "fingerprint": inp["fingerprint"]}


def _close(conn, tid: str, gid: str, status: str, note: str) -> None:
    with tx(conn):
        conn.execute("UPDATE tasks SET status=?, description=description || ? , revision=revision+1, updated_at=? WHERE id=?",
                     (status, f"\nKết thúc: {note}", now_iso(), tid))
        conn.execute("UPDATE agent_grants SET revoked_at=?, revoked_by='system' WHERE id=? AND revoked_at IS NULL",
                     (now_iso(), gid))


def get_run(conn, rid: str) -> dict:
    r = conn.execute("SELECT * FROM review_runs WHERE id=?", (rid,)).fetchone()
    if not r:
        raise ApiError(404, "NOT_FOUND", "Không tìm thấy lượt rà soát")
    d = dict(r)
    for k in ("inputs_json", "steps_json", "report_json"):
        d[k[:-5]] = json.loads(d.pop(k))
    return d


def list_runs(conn, actor: Actor, project_id: str, limit: int = 20) -> dict:
    require_project(conn, actor, project_id)
    rows = conn.execute("SELECT id, task_id, trigger, status, duplicate_of, ai_calls, created_at, finished_at, "
                        "inputs_json, error FROM review_runs WHERE project_id=? "
                        "ORDER BY created_at DESC, id DESC LIMIT ?", (project_id, limit)).fetchall()
    items = []
    for r in rows:  # không dùng json_extract: SQLite của một số bản Python trên Windows có thể thiếu JSON1
        d = dict(r)
        d["commit_sha"] = json.loads(d.pop("inputs_json") or "{}").get("commit")
        items.append(d)
    return {"items": items}


# ---------------------------------------------------------------- lịch chạy

def _schedule_row(conn, project_id: str):
    return conn.execute("SELECT * FROM schedules WHERE project_id=? AND kind='BUILD_REVIEW'", (project_id,)).fetchone()


def service_status(conn) -> dict:
    hb = get_setting(conn, HEARTBEAT_KEY)
    if not hb:
        return {"status": "OFFLINE", "evidence": "Chưa từng có nhịp của bộ lập lịch (máy chủ chưa chạy với lịch)"}
    age = (parse_iso(now_iso()) - parse_iso(hb["ts"])).total_seconds()
    return {"status": "ONLINE" if age <= 180 else "OFFLINE", "last_heartbeat": hb["ts"], "age_seconds": int(age),
            "host": hb.get("host"), "pid": hb.get("pid"),
            "evidence": f"Nhịp cuối từ máy {hb.get('host')} (pid {hb.get('pid')}) cách đây {int(age)} giây"}


def get_schedule(conn, actor: Actor, project_id: str) -> dict:
    require_project(conn, actor, project_id)
    r = _schedule_row(conn, project_id)
    ok_manual = conn.execute("SELECT id FROM review_runs WHERE project_id=? AND trigger='MANUAL' AND status='SUCCEEDED' "
                             "LIMIT 1", (project_id,)).fetchone()
    base = dict(r) if r else {"project_id": project_id, "enabled": 0, "interval_minutes": DEFAULT_INTERVAL,
                              "next_run_at": None, "last_run_at": None, "last_run_id": None, "last_status": None,
                              "config_json": "{}"}
    base["config"] = json.loads(base.pop("config_json", "{}") or "{}")
    base.update({"can_enable": bool(ok_manual), "service": service_status(conn),
                 "machine_note": ("Lịch chạy BÊN TRONG tiến trình `py -m crown_infinity serve` trên máy đang giữ dữ liệu "
                                  "(máy Windows của Chủ tịch). Máy phải bật, không ngủ/ngủ đông, và máy chủ phải đang chạy. "
                                  "Lỡ giờ khi máy tắt sẽ chạy bù MỘT lần khi bật lại, không chạy dồn.")})
    return base


def set_schedule(conn, actor: Actor, project_id: str, data: dict) -> dict:
    require_owner(actor)
    enabled = bool(data.get("enabled"))
    interval = data.get("interval_minutes", DEFAULT_INTERVAL)
    if not isinstance(interval, int) or isinstance(interval, bool) or not (30 <= interval <= 24 * 60):
        raise bad_request("interval_minutes phải là số nguyên 30–1440")
    config = {k: data[k] for k in ("analysis_provider_id", "verify_provider_id") if data.get(k)}
    with tx(conn):
        require_project(conn, actor, project_id, "write")
        if enabled and not conn.execute("SELECT 1 FROM review_runs WHERE project_id=? AND trigger='MANUAL' AND "
                                        "status='SUCCEEDED'", (project_id,)).fetchone():
            raise ApiError(409, "MANUAL_RUN_REQUIRED", "Cần một lần rà soát thủ công thành công trước khi bật lịch")
        r = _schedule_row(conn, project_id)
        nxt = iso_after(interval * 60) if enabled else None
        if r:
            conn.execute("UPDATE schedules SET enabled=?, interval_minutes=?, config_json=?, next_run_at=?, updated_at=?, "
                         "updated_by=? WHERE id=?", (1 if enabled else 0, interval, json.dumps(config), nxt, now_iso(),
                                                     actor.id, r["id"]))
        else:
            conn.execute("INSERT INTO schedules(id,project_id,kind,interval_minutes,enabled,config_json,next_run_at,"
                         "updated_at,updated_by) VALUES (?,?,'BUILD_REVIEW',?,?,?,?,?,?)",
                         (new_id("SCH"), project_id, interval, 1 if enabled else 0, json.dumps(config), nxt, now_iso(),
                          actor.id))
        audit(conn, actor, "schedule.update", "project", project_id, detail={"enabled": enabled, "interval": interval})
    return get_schedule(conn, actor, project_id)


def _iso_plus(iso: str, minutes: int) -> str:
    import datetime as _dt
    t = parse_iso(iso) + _dt.timedelta(minutes=minutes)
    return t.strftime("%Y-%m-%dT%H:%M:%S.") + f"{t.microsecond // 1000:03d}Z"


def scheduler_tick(db_path, repo: Path, now: str | None = None) -> list[dict]:
    """Một nhịp: ghi heartbeat, chạy các lịch đến hạn (giành quyền bằng compare-and-set để không chạy hai lần)."""
    conn = connect(db_path)
    results = []
    try:
        now = now or now_iso()
        with tx(conn):
            put_setting(conn, Actor("SYSTEM", "scheduler"), HEARTBEAT_KEY,
                        {"ts": now_iso(), "pid": os.getpid(), "host": socket.gethostname()})
        due = conn.execute("SELECT * FROM schedules WHERE enabled=1 AND next_run_at IS NOT NULL AND next_run_at<=?",
                           (now,)).fetchall()
        for s in due:
            # Mốc kế tiếp tính từ thời điểm của NHỊP này (không chạy dồn khi máy bật lại sau thời gian tắt).
            nxt = _iso_plus(now, s["interval_minutes"])
            with tx(conn):
                claimed = conn.execute("UPDATE schedules SET next_run_at=? WHERE id=? AND next_run_at=? AND enabled=1",
                                       (nxt, s["id"], s["next_run_at"])).rowcount
                owner = conn.execute("SELECT id FROM users WHERE id=? AND role='OWNER' AND is_active=1",
                                     (s["updated_by"],)).fetchone()
            if not claimed:
                continue
            if not owner:
                with tx(conn):
                    conn.execute("UPDATE schedules SET last_status='FAILED', last_run_at=? WHERE id=?", (now_iso(), s["id"]))
                continue
            actor = Actor("USER", owner["id"], "OWNER", "lịch")
            try:
                run = run_review(conn, actor, repo, s["project_id"], json.loads(s["config_json"] or "{}"), "SCHEDULE")
                status, rid = run["status"], run["id"]
            except Exception as exc:  # lịch không được làm sập máy chủ
                status, rid = f"FAILED: {type(exc).__name__}", None
            with tx(conn):
                conn.execute("UPDATE schedules SET last_run_at=?, last_run_id=?, last_status=? WHERE id=?",
                             (now_iso(), rid, status[:80], s["id"]))
            results.append({"schedule_id": s["id"], "run_id": rid, "status": status})
    finally:
        conn.close()
    return results


class Scheduler(threading.Thread):
    def __init__(self, db_path, repo: Path, interval_seconds: int = 60):
        super().__init__(daemon=True, name="crown-infinity-scheduler")
        self.db_path, self.repo, self.interval = db_path, repo, interval_seconds
        self.stop_event = threading.Event()

    def run(self):
        while not self.stop_event.is_set():
            try:
                scheduler_tick(self.db_path, self.repo)
            except Exception:  # ghi nhận ở nhật ký máy chủ, không dừng luồng
                import logging
                logging.getLogger("crown_infinity").exception("scheduler tick failed")
            self.stop_event.wait(self.interval)

    def stop(self):
        self.stop_event.set()


_ = time
