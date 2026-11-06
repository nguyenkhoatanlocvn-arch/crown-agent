"""Adapter CROWN AGENT (chỉ đọc) + danh tính Agent + cấp prompt theo phiên bản cố định.

Hợp đồng adapter (docs/infinity/API.md):
  health()          -> {"registry": {...}, "ledger": {...}}  mỗi phần có status ONLINE/OFFLINE/ERROR + evidence
  registry_agents() -> danh sách Agent từ registries/*.json của repo crown-agent
  ledger_tasks()    -> nhiệm vụ từ sổ crown tasks.db (mở chế độ chỉ đọc) nếu tệp tồn tại
Không có thao tác ghi sang CROWN AGENT trong phiên bản này.
"""
from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

from .auth import Actor, audit, require_owner, require_project
from .db import tx
from .httpcore import ApiError, bad_request
from .security import new_token, token_hash
from .util import new_id, now_iso


class CrownAgentAdapter:
    def __init__(self, repo_root: Path, ledger_path: Path | None = None):
        self.repo_root = Path(repo_root)
        env = os.environ.get("CROWN_DB")
        self.ledger_path = Path(ledger_path) if ledger_path else Path(env) if env else \
            self.repo_root / "runtime" / "tasks.db"

    def _reg(self, name: str) -> dict:
        return json.loads((self.repo_root / "registries" / name).read_text(encoding="utf-8"))

    def registry_agents(self) -> list[dict]:
        out = []
        for a in self._reg("agents.json").get("agents", []):
            out.append({"agent_id": a["id"], "display_name": a.get("name", a["id"]), "source": "registries/agents.json",
                        "registry_status": a.get("status", "UNKNOWN"), "leader": a.get("leader")})
        for leader in self._reg("leaders.json").get("leaders", []):
            if leader.get("kind") == "agent":
                out.append({"agent_id": leader["id"], "display_name": leader.get("name", leader["id"]),
                            "source": "registries/leaders.json", "registry_status": leader.get("status", "UNKNOWN"),
                            "leader": leader.get("supervisor")})
        dept = self.repo_root / "registries" / "dept_tool.json"
        if dept.is_file():
            for e in self._reg("dept_tool.json").get("executives", []):
                out.append({"agent_id": e["id"], "display_name": e.get("name", e["id"]),
                            "source": "registries/dept_tool.json", "registry_status": e.get("status", "UNKNOWN"),
                            "leader": e.get("supervisor")})
        return out

    def health(self) -> dict:
        res = {}
        try:
            n = len(self.registry_agents())
            res["registry"] = {"status": "ONLINE", "evidence": f"Đọc {n} Agent từ {self.repo_root / 'registries'} lúc {now_iso()}"}
        except (OSError, ValueError, KeyError) as exc:
            res["registry"] = {"status": "OFFLINE", "evidence": f"Không đọc được sổ đăng ký: {type(exc).__name__}"}
        if not self.ledger_path.is_file():
            res["ledger"] = {"status": "OFFLINE", "evidence": f"Không có tệp sổ nhiệm vụ tại {self.ledger_path}"}
        else:
            try:
                c = sqlite3.connect(self.ledger_path.resolve().as_uri() + "?mode=ro", uri=True, timeout=2)
                try:
                    n = c.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
                finally:
                    c.close()
                res["ledger"] = {"status": "ONLINE", "evidence": f"Đọc {n} nhiệm vụ (chỉ đọc) lúc {now_iso()}"}
            except sqlite3.Error as exc:
                res["ledger"] = {"status": "ERROR", "evidence": f"Lỗi đọc sổ: {exc}"}
        return res

    def ledger_tasks(self, limit: int = 100) -> dict:
        if not self.ledger_path.is_file():
            return {"status": "OFFLINE", "items": [], "evidence": f"Không có tệp {self.ledger_path}"}
        c = sqlite3.connect(self.ledger_path.resolve().as_uri() + "?mode=ro", uri=True, timeout=2)
        c.row_factory = sqlite3.Row
        try:
            rows = c.execute("SELECT task_id,parent_id,company_id,owner_agent_id,objective,state,updated_at "
                             "FROM tasks ORDER BY updated_at DESC LIMIT ?", (limit,)).fetchall()
        except sqlite3.Error as exc:
            return {"status": "ERROR", "items": [], "evidence": str(exc)}
        finally:
            c.close()
        return {"status": "ONLINE", "items": [dict(r) for r in rows], "evidence": f"Chỉ đọc {self.ledger_path}"}


def module_health(adapter: CrownAgentAdapter, repo_root: Path) -> dict:
    h = adapter.health()
    catalog = Path(repo_root) / "tool" / "catalog"
    return {
        "crown_prompt_studio": {"status": "ONLINE", "evidence": "API Prompt Studio đang trả lời trong tiến trình này"},
        "crown_admin_center": {"status": "ONLINE", "evidence": "Xác thực phiên đang hoạt động"},
        "crown_ai_hub": {"status": "ONLINE", "evidence": "Cấu hình đọc được; gọi model thật chưa triển khai"},
        "crown_agent_control": {"status": h["registry"]["status"],
                                "evidence": f"Sổ đăng ký: {h['registry']['evidence']} · Sổ nhiệm vụ: {h['ledger']['evidence']}"},
        "crown_tool_desk": {"status": "ONLINE" if catalog.is_dir() else "OFFLINE",
                            "evidence": f"{'Có' if catalog.is_dir() else 'Không có'} thư mục {catalog}"},
    }


# ---------------------------------------------------------------- danh tính Agent

def sync_agents(conn, actor: Actor, adapter: CrownAgentAdapter) -> dict:
    require_owner(actor)
    agents = adapter.registry_agents()
    ts = now_iso()
    with tx(conn):
        for a in agents:
            conn.execute("INSERT INTO agent_identities(agent_id,display_name,source,created_at,updated_at) "
                         "VALUES (?,?,?,?,?) ON CONFLICT(agent_id) DO UPDATE SET display_name=excluded.display_name, "
                         "source=excluded.source, updated_at=excluded.updated_at",
                         (a["agent_id"], a["display_name"][:120], a["source"], ts, ts))
        audit(conn, actor, "agent.sync", "agent", None, detail={"count": len(agents)})
    return list_agents(conn, actor)


def list_agents(conn, actor: Actor) -> dict:
    require_owner(actor)
    rows = conn.execute("SELECT a.*, (SELECT COUNT(*) FROM agent_prompt_assignments x WHERE x.agent_id=a.agent_id "
                        "AND x.revoked_at IS NULL) AS active_assignments FROM agent_identities a ORDER BY a.agent_id")
    items = []
    for r in rows:
        d = dict(r)
        d["has_token"] = d.pop("token_hash") is not None
        items.append(d)
    return {"items": items}


def issue_token(conn, actor: Actor, agent_id: str) -> dict:
    require_owner(actor)
    token = "cia_" + new_token()
    with tx(conn):
        if not conn.execute("SELECT 1 FROM agent_identities WHERE agent_id=?", (agent_id,)).fetchone():
            raise ApiError(404, "NOT_FOUND", "Không có Agent này; đồng bộ sổ đăng ký trước")
        conn.execute("UPDATE agent_identities SET token_hash=?, token_hint=?, token_issued_at=?, updated_at=? "
                     "WHERE agent_id=?", (token_hash(token), token[-4:], now_iso(), now_iso(), agent_id))
        audit(conn, actor, "agent.token.issue", "agent", agent_id)
    return {"agent_id": agent_id, "token": token,
            "warning": "Token chỉ hiển thị MỘT lần. Lưu vào nơi an toàn phía Agent; không dán vào chat/Git/log."}


def revoke_token(conn, actor: Actor, agent_id: str) -> dict:
    require_owner(actor)
    with tx(conn):
        n = conn.execute("UPDATE agent_identities SET token_hash=NULL, token_hint='', updated_at=? WHERE agent_id=?",
                         (now_iso(), agent_id)).rowcount
        if not n:
            raise ApiError(404, "NOT_FOUND", "Không có Agent này")
        audit(conn, actor, "agent.token.revoke", "agent", agent_id)
    return {"agent_id": agent_id, "revoked": True}


def resolve_agent(conn, bearer: str) -> Actor | None:
    """Danh tính Agent lấy từ token, KHÔNG từ agent_id do client tự khai."""
    if not bearer or not bearer.startswith("cia_"):
        return None
    row = conn.execute("SELECT * FROM agent_identities WHERE token_hash=? AND is_active=1",
                       (token_hash(bearer),)).fetchone()
    if not row:
        return None
    conn.execute("UPDATE agent_identities SET last_used_at=? WHERE agent_id=?", (now_iso(), row["agent_id"]))
    return Actor("AGENT", row["agent_id"], "", row["display_name"])


# ---------------------------------------------------------------- cấp prompt cho Agent

def list_assignments(conn, actor: Actor, q: dict) -> dict:
    where, args = ["1"], []
    if q.get("prompt_id"):
        prow = conn.execute("SELECT project_id FROM prompts WHERE id=?", (q["prompt_id"],)).fetchone()
        if not prow:
            raise ApiError(404, "NOT_FOUND", "Không tìm thấy prompt")
        require_project(conn, actor, prow["project_id"])
        where.append("x.prompt_id=?"), args.append(q["prompt_id"])
    else:
        require_owner(actor)
    if q.get("agent_id"):
        where.append("x.agent_id=?"), args.append(q["agent_id"])
    if q.get("active", "1") == "1":
        where.append("x.revoked_at IS NULL")
    rows = conn.execute("SELECT x.*, v.version_no, p.title AS prompt_title FROM agent_prompt_assignments x "
                        "JOIN prompt_versions v ON v.id=x.version_id JOIN prompts p ON p.id=x.prompt_id "
                        f"WHERE {' AND '.join(where)} ORDER BY x.assigned_at DESC", args).fetchall()
    return {"items": [dict(r) for r in rows]}


def create_assignment(conn, actor: Actor, data: dict) -> dict:
    require_owner(actor)
    agent_id, pid, vid = data.get("agent_id"), data.get("prompt_id"), data.get("version_id")
    if not all(isinstance(x, str) and x for x in (agent_id, pid, vid)):
        raise bad_request("Cần agent_id, prompt_id và version_id cụ thể (không mặc định lấy bản mới nhất)")
    with tx(conn):
        agent = conn.execute("SELECT * FROM agent_identities WHERE agent_id=?", (agent_id,)).fetchone()
        if not agent:
            raise ApiError(404, "NOT_FOUND", "Không có Agent này")
        v = conn.execute("SELECT * FROM prompt_versions WHERE id=? AND prompt_id=?", (vid, pid)).fetchone()
        if not v:
            raise ApiError(404, "NOT_FOUND", "Phiên bản không thuộc prompt này")
        if v["review_status"] != "APPROVED":
            raise ApiError(409, "NOT_APPROVED", "Chỉ cấp cho Agent phiên bản đã APPROVED")
        if conn.execute("SELECT 1 FROM agent_prompt_assignments WHERE agent_id=? AND prompt_id=? AND revoked_at IS NULL",
                        (agent_id, pid)).fetchone():
            raise ApiError(409, "ALREADY_ASSIGNED", "Agent đã được cấp prompt này; thu hồi trước khi cấp phiên bản khác")
        aid = new_id("ASG")
        conn.execute("INSERT INTO agent_prompt_assignments(id,agent_id,prompt_id,version_id,permission,note,assigned_by,"
                     "assigned_at) VALUES (?,?,?,?,'READ',?,?,?)",
                     (aid, agent_id, pid, vid, str(data.get("note") or "")[:300], actor.id, now_iso()))
        audit(conn, actor, "assignment.create", "assignment", aid,
              detail={"agent_id": agent_id, "prompt_id": pid, "version_id": vid})
    return dict(conn.execute("SELECT * FROM agent_prompt_assignments WHERE id=?", (aid,)).fetchone())


def revoke_assignment(conn, actor: Actor, aid: str) -> dict:
    require_owner(actor)
    with tx(conn):
        n = conn.execute("UPDATE agent_prompt_assignments SET revoked_at=?, revoked_by=? WHERE id=? AND revoked_at IS NULL",
                         (now_iso(), actor.id, aid)).rowcount
        if not n:
            raise ApiError(404, "NOT_FOUND", "Không có assignment đang hiệu lực với mã này")
        audit(conn, actor, "assignment.revoke", "assignment", aid)
    return dict(conn.execute("SELECT * FROM agent_prompt_assignments WHERE id=?", (aid,)).fetchone())


def agent_list_prompts(conn, agent: Actor) -> dict:
    rows = conn.execute("SELECT x.prompt_id, x.version_id, v.version_no, p.title, x.assigned_at FROM "
                        "agent_prompt_assignments x JOIN prompt_versions v ON v.id=x.version_id JOIN prompts p ON "
                        "p.id=x.prompt_id WHERE x.agent_id=? AND x.revoked_at IS NULL ORDER BY p.title", (agent.id,))
    return {"agent_id": agent.id, "items": [dict(r) for r in rows]}


def agent_get_prompt(conn, agent: Actor, pid: str) -> dict:
    row = conn.execute("SELECT x.*, v.content, v.version_no, v.checksum, p.title FROM agent_prompt_assignments x "
                       "JOIN prompt_versions v ON v.id=x.version_id JOIN prompts p ON p.id=x.prompt_id "
                       "WHERE x.agent_id=? AND x.prompt_id=? AND x.revoked_at IS NULL", (agent.id, pid)).fetchone()
    if not row:
        audit(conn, agent, "agent.prompt.read", "prompt", pid, result="DENIED")
        raise ApiError(403, "NOT_ASSIGNED", "Agent không được cấp prompt này")
    audit(conn, agent, "agent.prompt.read", "prompt", pid, detail={"version_id": row["version_id"]})
    return {"agent_id": agent.id, "prompt_id": pid, "title": row["title"], "version_id": row["version_id"],
            "version_no": row["version_no"], "checksum": row["checksum"], "content": row["content"],
            "assignment_id": row["id"]}
