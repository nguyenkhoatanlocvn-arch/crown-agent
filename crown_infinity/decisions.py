"""Bộ nhớ quyết định theo dự án.

DECISION = quyết định đã được người (OWNER) xác nhận. PROPOSAL = đề xuất do AI/Agent viết, hiển thị tách biệt,
chỉ trở thành DECISION khi OWNER bấm xác nhận. Mọi thay đổi ghi một dòng lịch sử chỉ thêm (snapshot đầy đủ).
"""
from __future__ import annotations

import json

from .auth import Actor, audit, require_owner, require_project
from .db import tx
from .httpcore import ApiError, Response, bad_request
from .util import new_id, now_iso

PUBLIC_FIELDS = ("id", "project_id", "kind", "status", "title", "content", "source", "decided_at", "confirmed_by",
                 "confirmed_at", "proposed_by_type", "proposed_by", "ai_model", "revoked_at", "revoked_by",
                 "revoke_reason", "revision", "created_at", "updated_at")


def _s(data: dict, key: str, limit: int, required: bool = False, default: str = "") -> str:
    v = data.get(key, default)
    if v is None:
        v = ""
    if not isinstance(v, str):
        raise bad_request(f"Trường '{key}' phải là chuỗi")
    v = v.strip()
    if required and not v:
        raise bad_request(f"Trường '{key}' là bắt buộc")
    if len(v) > limit:
        raise bad_request(f"Trường '{key}' tối đa {limit} ký tự")
    return v


def _view(conn, r) -> dict:
    d = {k: r[k] for k in PUBLIC_FIELDS}
    if r["confirmed_by"]:
        u = conn.execute("SELECT display_name FROM users WHERE id=?", (r["confirmed_by"],)).fetchone()
        d["confirmed_by_name"] = u["display_name"] if u else r["confirmed_by"]
    d["is_confirmed_decision"] = r["kind"] == "DECISION"
    d["label"] = ("QUYẾT ĐỊNH ĐÃ XÁC NHẬN" if r["kind"] == "DECISION"
                  else f"ĐỀ XUẤT CỦA {'AI/AGENT' if r['proposed_by_type'] == 'AGENT' else r['proposed_by_type']} — CHƯA XÁC NHẬN")
    return d


def _history(conn, actor: Actor, r, action: str, note: str = "") -> None:
    snap = {k: r[k] for k in PUBLIC_FIELDS}
    conn.execute("INSERT INTO decision_history(decision_id,revision,action,actor_type,actor_id,ts,snapshot_json,note) "
                 "VALUES (?,?,?,?,?,?,?,?)", (r["id"], r["revision"], action, actor.kind, actor.id, now_iso(),
                                              json.dumps(snap, ensure_ascii=False), note[:500]))


def _row(conn, did: str):
    r = conn.execute("SELECT * FROM decisions WHERE id=?", (did,)).fetchone()
    if not r:
        raise ApiError(404, "NOT_FOUND", "Không tìm thấy quyết định")
    return r


def list_decisions(conn, actor: Actor, project_id: str, status: str | None = None) -> dict:
    require_project(conn, actor, project_id)
    where, args = ["project_id=?"], [project_id]
    if status:
        if status not in ("ACTIVE", "REVOKED", "PROPOSED", "REJECTED"):
            raise bad_request("status không hợp lệ")
        where.append("status=?"), args.append(status)
    rows = conn.execute(f"SELECT * FROM decisions WHERE {' AND '.join(where)} "
                        "ORDER BY CASE status WHEN 'ACTIVE' THEN 0 WHEN 'PROPOSED' THEN 1 ELSE 2 END, decided_at DESC, id",
                        args).fetchall()
    return {"items": [_view(conn, r) for r in rows]}


def active_decisions(conn, project_id: str) -> list[dict]:
    """Dùng nội bộ (đã kiểm phạm vi ở tầng gọi)."""
    rows = conn.execute("SELECT * FROM decisions WHERE project_id=? AND kind='DECISION' AND status='ACTIVE' "
                        "ORDER BY decided_at, id", (project_id,)).fetchall()
    return [_view(conn, r) for r in rows]


def create_decision(conn, actor: Actor, data: dict) -> Response:
    """OWNER ghi một quyết định đã xác nhận (người xác nhận = OWNER đang đăng nhập)."""
    require_owner(actor)
    project_id = _s(data, "project_id", 64, True)
    title = _s(data, "title", 200, True)
    content = _s(data, "content", 10_000, True)
    source = _s(data, "source", 1000, True)
    decided_at = _s(data, "decided_at", 40) or now_iso()
    with tx(conn):
        require_project(conn, actor, project_id, "write")
        did, ts = new_id("D"), now_iso()
        conn.execute("INSERT INTO decisions(id,project_id,kind,status,title,content,source,decided_at,confirmed_by,"
                     "confirmed_at,proposed_by_type,proposed_by,created_at,updated_at) VALUES "
                     "(?,?,'DECISION','ACTIVE',?,?,?,?,?,?,'USER',?,?,?)",
                     (did, project_id, title, content, source, decided_at, actor.id, ts, actor.id, ts, ts))
        r = _row(conn, did)
        _history(conn, actor, r, "CREATE")
        audit(conn, actor, "decision.create", "decision", did, detail={"project_id": project_id})
    return Response.json(_view(conn, r), 201, {"ETag": f'"{r["revision"]}"'})


def propose(conn, agent: Actor, project_id: str, data: dict, ai_model: str = "") -> dict:
    """Agent/AI ghi đề xuất. Không bao giờ thành quyết định nếu OWNER chưa xác nhận."""
    title = _s(data, "title", 200, True)
    content = _s(data, "content", 10_000, True)
    source = _s(data, "source", 1000, True)
    with tx(conn):
        did, ts = new_id("D"), now_iso()
        conn.execute("INSERT INTO decisions(id,project_id,kind,status,title,content,source,decided_at,proposed_by_type,"
                     "proposed_by,ai_model,created_at,updated_at) VALUES (?,?,'PROPOSAL','PROPOSED',?,?,?,?,?,?,?,?,?)",
                     (did, project_id, title, content, source, ts, "AGENT" if agent.kind == "AGENT" else "SYSTEM",
                      agent.id, ai_model[:120], ts, ts))
        r = _row(conn, did)
        _history(conn, agent, r, "PROPOSE")
        audit(conn, agent, "decision.propose", "decision", did, detail={"project_id": project_id})
    return _view(conn, r)


def update_decision(conn, actor: Actor, did: str, data: dict, if_match) -> Response:
    require_owner(actor)
    with tx(conn):
        r = _row(conn, did)
        if if_match is None:
            raise ApiError(428, "PRECONDITION_REQUIRED", "Thiếu If-Match (revision)")
        if int(if_match) != r["revision"]:
            raise ApiError(409, "REVISION_CONFLICT", "Quyết định đã được sửa ở nơi khác", {"current": _view(conn, r)})
        if r["status"] in ("REVOKED", "REJECTED"):
            raise ApiError(409, "NOT_EDITABLE", "Quyết định đã thu hồi/đề xuất đã bác thì không sửa; tạo mục mới")
        action = data.get("action", "EDIT")
        ts = now_iso()
        if action == "EDIT":
            sets, vals = [], []
            for key, lim, req in (("title", 200, True), ("content", 10_000, True), ("source", 1000, True),
                                  ("decided_at", 40, True)):
                if key in data:
                    sets.append(f"{key}=?"), vals.append(_s(data, key, lim, req))
            if not sets:
                raise bad_request("Không có trường nào để sửa")
            conn.execute(f"UPDATE decisions SET {', '.join(sets)}, revision=revision+1, updated_at=? WHERE id=?",
                         (*vals, ts, did))
        elif action == "REVOKE":
            if r["kind"] != "DECISION":
                raise bad_request("Chỉ thu hồi được quyết định; đề xuất thì dùng REJECT")
            reason = _s(data, "reason", 1000, True)
            conn.execute("UPDATE decisions SET status='REVOKED', revoked_at=?, revoked_by=?, revoke_reason=?, "
                         "revision=revision+1, updated_at=? WHERE id=?", (ts, actor.id, reason, ts, did))
        elif action == "CONFIRM":
            if r["kind"] != "PROPOSAL":
                raise bad_request("Mục này đã là quyết định")
            conn.execute("UPDATE decisions SET kind='DECISION', status='ACTIVE', confirmed_by=?, confirmed_at=?, "
                         "revision=revision+1, updated_at=? WHERE id=?", (actor.id, ts, ts, did))
        elif action == "REJECT":
            if r["kind"] != "PROPOSAL":
                raise bad_request("Chỉ bác được đề xuất")
            reason = _s(data, "reason", 1000)
            conn.execute("UPDATE decisions SET status='REJECTED', revoke_reason=?, revoked_at=?, revoked_by=?, "
                         "revision=revision+1, updated_at=? WHERE id=?", (reason, ts, actor.id, ts, did))
        else:
            raise bad_request("action phải là EDIT, REVOKE, CONFIRM hoặc REJECT")
        r2 = _row(conn, did)
        _history(conn, actor, r2, action, str(data.get("reason") or ""))
        audit(conn, actor, f"decision.{action.lower()}", "decision", did)
    return Response.json(_view(conn, r2), headers={"ETag": f'"{r2["revision"]}"'})


def history(conn, actor: Actor, did: str) -> dict:
    require_owner(actor)
    _row(conn, did)
    rows = conn.execute("SELECT * FROM decision_history WHERE decision_id=? ORDER BY id", (did,)).fetchall()
    return {"items": [dict(r) | {"snapshot": json.loads(r["snapshot_json"])} for r in rows]}
