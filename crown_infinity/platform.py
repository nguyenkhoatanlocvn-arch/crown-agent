"""Lõi nền tảng dùng chung: dự án, Module Registry, Tool Registry, Tasks, nhật ký, cài đặt, dashboard."""
from __future__ import annotations

import json
from pathlib import Path

from .auth import Actor, audit, project_role, readable_project_ids, require_owner, require_project
from .db import tx
from .httpcore import ApiError, Response, bad_request
from .util import new_id, now_iso

# ---------------------------------------------------------------- cài đặt

DEFAULT_SETTINGS = {
    "ai.enabled": False,
    "ai.budget": {"daily_usd": 0, "monthly_usd": 0, "per_request_usd": 0, "max_output_tokens": 0,
                  "timeout_seconds": 60, "max_retries": 1, "max_concurrency": 1},
    "prompt.max_chars_warning": 20000,
}


def get_setting(conn, key: str):
    row = conn.execute("SELECT value_json FROM settings WHERE key=?", (key,)).fetchone()
    if row:
        return json.loads(row[0])
    return json.loads(json.dumps(DEFAULT_SETTINGS.get(key)))


def put_setting(conn, actor: Actor, key: str, value) -> None:
    conn.execute("INSERT INTO settings(key,value_json,updated_at,updated_by) VALUES (?,?,?,?) "
                 "ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json, updated_at=excluded.updated_at, "
                 "updated_by=excluded.updated_by", (key, json.dumps(value, ensure_ascii=False), now_iso(), actor.id))


# ---------------------------------------------------------------- dự án

def list_projects(conn, actor: Actor) -> dict:
    ids = readable_project_ids(conn, actor)
    if ids is None:
        rows = conn.execute("SELECT * FROM projects ORDER BY archived_at IS NOT NULL, name COLLATE NOCASE").fetchall()
    elif not ids:
        rows = []
    else:
        rows = conn.execute(f"SELECT * FROM projects WHERE id IN ({','.join('?' * len(ids))}) "
                            "ORDER BY archived_at IS NOT NULL, name COLLATE NOCASE", ids).fetchall()
    items = []
    for r in rows:
        n = conn.execute("SELECT COUNT(*) FROM prompts WHERE project_id=? AND archived_at IS NULL", (r["id"],)).fetchone()[0]
        items.append(dict(r) | {"prompt_count": n, "your_role": project_role(conn, actor, r["id"])})
    return {"items": items}


def create_project(conn, actor: Actor, data: dict, *, project_id: str | None = None) -> dict:
    require_owner(actor)
    name = str(data.get("name") or "").strip()
    if not name or len(name) > 120:
        raise bad_request("Tên dự án 1–120 ký tự")
    desc = str(data.get("description") or "").strip()[:2000]
    pid, ts = project_id or new_id("PRJ"), now_iso()
    with tx(conn):
        conn.execute("INSERT INTO projects(id,name,description,created_by,created_at,updated_at) VALUES (?,?,?,?,?,?)",
                     (pid, name, desc, actor.id, ts, ts))
        audit(conn, actor, "project.create", "project", pid)
    return dict(conn.execute("SELECT * FROM projects WHERE id=?", (pid,)).fetchone())


def update_project(conn, actor: Actor, pid: str, data: dict) -> dict:
    require_owner(actor)
    with tx(conn):
        if not conn.execute("SELECT 1 FROM projects WHERE id=?", (pid,)).fetchone():
            raise ApiError(404, "NOT_FOUND", "Không tìm thấy dự án")
        sets, vals = [], []
        if "name" in data:
            name = str(data["name"] or "").strip()
            if not name or len(name) > 120:
                raise bad_request("Tên dự án 1–120 ký tự")
            sets.append("name=?"), vals.append(name)
        if "description" in data:
            sets.append("description=?"), vals.append(str(data["description"] or "")[:2000])
        if "archived" in data:
            sets.append("archived_at=?"), vals.append(now_iso() if data["archived"] else None)
        if not sets:
            raise bad_request("Không có trường nào để cập nhật")
        conn.execute(f"UPDATE projects SET {', '.join(sets)}, updated_at=? WHERE id=?", (*vals, now_iso(), pid))
        audit(conn, actor, "project.update", "project", pid)
    return dict(conn.execute("SELECT * FROM projects WHERE id=?", (pid,)).fetchone())


def list_members(conn, actor: Actor, pid: str) -> dict:
    require_project(conn, actor, pid)
    rows = conn.execute("SELECT m.user_id, m.role, m.added_at, u.username, u.display_name FROM project_members m "
                        "JOIN users u ON u.id=m.user_id WHERE m.project_id=? ORDER BY u.username", (pid,)).fetchall()
    return {"items": [dict(r) for r in rows]}


def set_member(conn, actor: Actor, pid: str, uid: str, role: str | None) -> dict:
    require_owner(actor)
    with tx(conn):
        if not conn.execute("SELECT 1 FROM projects WHERE id=?", (pid,)).fetchone():
            raise ApiError(404, "NOT_FOUND", "Không tìm thấy dự án")
        if not conn.execute("SELECT 1 FROM users WHERE id=?", (uid,)).fetchone():
            raise ApiError(404, "NOT_FOUND", "Không tìm thấy người dùng")
        if role is None:
            conn.execute("DELETE FROM project_members WHERE project_id=? AND user_id=?", (pid, uid))
        else:
            if role not in ("EDITOR", "VIEWER"):
                raise bad_request("role phải là EDITOR hoặc VIEWER")
            conn.execute("INSERT INTO project_members(project_id,user_id,role,added_at) VALUES (?,?,?,?) "
                         "ON CONFLICT(project_id,user_id) DO UPDATE SET role=excluded.role", (pid, uid, role, now_iso()))
        audit(conn, actor, "project.member.set", "project", pid, detail={"user_id": uid, "role": role})
    return list_members(conn, actor, pid)


# ---------------------------------------------------------------- Module Registry

MANIFEST_PATH = Path(__file__).resolve().parent / "modules.json"


def load_manifests() -> list[dict]:
    data = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    return data["modules"]


def sync_modules(conn) -> None:
    """Đồng bộ manifest từ mã nguồn; giữ nguyên lựa chọn bật/tắt của người dùng."""
    ts = now_iso()
    with tx(conn):
        for m in load_manifests():
            row = conn.execute("SELECT enabled FROM modules WHERE module_id=?", (m["module_id"],)).fetchone()
            default_enabled = 1 if m["dev_status"] in ("IMPLEMENTED", "VERIFIED") else 0
            enabled = row["enabled"] if row else default_enabled
            if m["dev_status"] in ("PLANNED", "BLOCKED"):
                enabled = 0
            conn.execute("INSERT INTO modules(module_id,name,version,manifest_json,dev_status,enabled,updated_at) "
                         "VALUES (?,?,?,?,?,?,?) ON CONFLICT(module_id) DO UPDATE SET name=excluded.name, "
                         "version=excluded.version, manifest_json=excluded.manifest_json, dev_status=excluded.dev_status,"
                         " enabled=excluded.enabled",
                         (m["module_id"], m["name"], m["version"], json.dumps(m, ensure_ascii=False), m["dev_status"],
                          enabled, ts))


def list_modules(conn, actor: Actor, health: dict) -> dict:
    items = []
    for r in conn.execute("SELECT * FROM modules ORDER BY module_id"):
        m = json.loads(r["manifest_json"])
        op = health.get(r["module_id"], {"status": "UNKNOWN", "evidence": "Chưa có kiểm tra vận hành"})
        if r["dev_status"] in ("PLANNED", "BLOCKED"):
            op = {"status": "UNKNOWN", "evidence": "Module chưa triển khai"}
        elif not r["enabled"]:
            op = {"status": "OFFLINE", "evidence": "Đang tắt trong Module Registry"}
        items.append({**m, "enabled": bool(r["enabled"]), "dev_status": r["dev_status"], "op_status": op["status"],
                      "op_evidence": op["evidence"], "updated_at": r["updated_at"], "updated_by": r["updated_by"]})
    return {"items": items,
            "note": "Bật module chỉ đổi cờ hiển thị/định tuyến; không cài phần mềm và không cấp quyền mới."}


def set_module_enabled(conn, actor: Actor, module_id: str, enabled: bool) -> None:
    require_owner(actor)
    with tx(conn):
        r = conn.execute("SELECT * FROM modules WHERE module_id=?", (module_id,)).fetchone()
        if not r:
            raise ApiError(404, "NOT_FOUND", "Không tìm thấy module")
        if enabled and r["dev_status"] in ("PLANNED", "BLOCKED"):
            raise ApiError(409, "NOT_IMPLEMENTED", "Module chưa triển khai nên không bật được")
        if module_id in ("crown_admin_center",) and not enabled:
            raise ApiError(409, "REQUIRED_MODULE", "Admin Center là module bắt buộc")
        conn.execute("UPDATE modules SET enabled=?, updated_at=?, updated_by=? WHERE module_id=?",
                     (1 if enabled else 0, now_iso(), actor.id, module_id))
        audit(conn, actor, "module.enable" if enabled else "module.disable", "module", module_id)


def module_enabled(conn, module_id: str) -> bool:
    r = conn.execute("SELECT enabled FROM modules WHERE module_id=?", (module_id,)).fetchone()
    return bool(r and r["enabled"])


# ---------------------------------------------------------------- Tool Registry (đọc kho TOOL của CROWN AGENT)

def sync_tools_from_catalog(conn, actor: Actor, repo_root: Path) -> dict:
    """Đọc tool/catalog/<id>/tool.json (chỉ đọc). Không chạy tool, không sửa kho."""
    catalog = Path(repo_root) / "tool" / "catalog"
    if not catalog.is_dir():
        return {"status": "OFFLINE", "synced": 0, "evidence": f"Không thấy thư mục {catalog}"}
    synced, errors, ts = 0, [], now_iso()
    with tx(conn):
        for tdir in sorted(p for p in catalog.iterdir() if p.is_dir()):
            meta_path = tdir / "tool.json"
            if not meta_path.is_file():
                continue
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                errors.append(f"{tdir.name}: {exc}")
                continue
            tool_id = str(meta.get("id") or tdir.name)
            current = meta.get("current") or meta.get("current_version") or meta.get("version")
            manifest = {}
            if current and (tdir / str(current) / "manifest.json").is_file():
                try:
                    manifest = json.loads((tdir / str(current) / "manifest.json").read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    manifest = {}
            versions = [{"version": v.get("version"), "status": v.get("status"), "published_at": v.get("published_at"),
                         "published_by": v.get("published_by")} for v in (meta.get("versions") or [])
                        if isinstance(v, dict)]
            iface = manifest.get("interface") or {}
            details = {"versions": versions, "keywords": meta.get("keywords") or [],
                       "how_to_call": f"python -m tool run {tool_id} --caller <ID> --in input=<tệp> --out <thư mục>",
                       "inputs": [i.get("name") for i in iface.get("inputs", []) if isinstance(i, dict)],
                       "permissions": manifest.get("permissions"), "cost_usd_per_run": manifest.get("cost_usd_per_run")}
            name = meta.get("name") or manifest.get("name") or tool_id
            desc = meta.get("summary") or manifest.get("summary") or meta.get("description") or ""
            category = meta.get("category") or manifest.get("category") or ""
            conn.execute("INSERT INTO tools(id,name,description,category,version,source,source_ref,details_json,synced_at,"
                         "created_at,updated_at) VALUES (?,?,?,?,?,'tool-catalog',?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
                         "name=excluded.name, description=excluded.description, category=excluded.category, "
                         "version=excluded.version, source_ref=excluded.source_ref, details_json=excluded.details_json, "
                         "synced_at=excluded.synced_at, updated_at=excluded.updated_at",
                         (tool_id, str(name)[:200], str(desc)[:2000], str(category)[:80], str(current or ""),
                          f"tool/catalog/{tdir.name}", json.dumps(details, ensure_ascii=False), ts, ts, ts))
            synced += 1
        audit(conn, actor, "tool.sync", "tool", None, detail={"synced": synced, "errors": len(errors)})
    return {"status": "ONLINE", "synced": synced, "errors": errors, "evidence": f"Đọc {catalog} lúc {ts}"}


def list_tools(conn) -> dict:
    rows = conn.execute("SELECT * FROM tools ORDER BY name COLLATE NOCASE").fetchall()
    return {"items": [dict(r) | {"details": json.loads(r["details_json"])} for r in rows],
            "note": "Web không chạy tool. Tool chạy qua CLI `python -m tool run` hoặc worker của bộ phận TOOL."}


# ---------------------------------------------------------------- Tasks tối thiểu

TASK_STATUS = ("TODO", "IN_PROGRESS", "BLOCKED", "DONE", "CANCELLED")


def list_tasks(conn, actor: Actor, q: dict) -> dict:
    ids = readable_project_ids(conn, actor)
    where, args = [], []
    if ids is not None:
        # Việc không gắn dự án là của OWNER: người khác chỉ thấy việc trong dự án mình là thành viên.
        where.append(f"project_id IN ({','.join('?' * len(ids)) or 'NULL'})")
        args += ids
    if q.get("status") in TASK_STATUS:
        where.append("status=?"), args.append(q["status"])
    if q.get("project_id"):
        where.append("project_id=?"), args.append(q["project_id"])
    sql = "SELECT * FROM tasks " + ("WHERE " + " AND ".join(where) if where else "") + \
          " ORDER BY CASE status WHEN 'IN_PROGRESS' THEN 0 WHEN 'TODO' THEN 1 WHEN 'BLOCKED' THEN 2 ELSE 3 END, " \
          "updated_at DESC, id LIMIT 200"
    return {"items": [dict(r) for r in conn.execute(sql, args)]}


def create_task(conn, actor: Actor, data: dict) -> dict:
    if actor.role == "VIEWER":
        raise ApiError(403, "FORBIDDEN", "VIEWER không tạo được việc")
    title = str(data.get("title") or "").strip()
    if not title or len(title) > 200:
        raise bad_request("Tiêu đề việc 1–200 ký tự")
    project_id = data.get("project_id") or None
    if project_id is not None and not isinstance(project_id, str):
        raise bad_request("project_id phải là chuỗi")
    status = data.get("status") or "TODO"
    if status not in TASK_STATUS:
        raise bad_request("Trạng thái không hợp lệ")
    with tx(conn):
        if project_id:
            require_project(conn, actor, project_id, "write")
        elif not actor.is_owner:
            raise ApiError(403, "FORBIDDEN", "Chỉ OWNER tạo việc không gắn dự án")
        tid, ts = new_id("T"), now_iso()
        conn.execute("INSERT INTO tasks(id,project_id,title,description,status,assignee,created_by,created_at,updated_at)"
                     " VALUES (?,?,?,?,?,?,?,?,?)", (tid, project_id, title, str(data.get("description") or "")[:5000],
                                                     status, str(data.get("assignee") or "")[:80], actor.id, ts, ts))
        audit(conn, actor, "task.create", "task", tid)
    return dict(conn.execute("SELECT * FROM tasks WHERE id=?", (tid,)).fetchone())


def update_task(conn, actor: Actor, tid: str, data: dict, if_match) -> Response:
    with tx(conn):
        row = conn.execute("SELECT * FROM tasks WHERE id=?", (tid,)).fetchone()
        if not row:
            raise ApiError(404, "NOT_FOUND", "Không tìm thấy việc")
        if row["project_id"]:
            require_project(conn, actor, row["project_id"], "write")
        else:
            require_owner(actor)
        if if_match is None:
            raise ApiError(428, "PRECONDITION_REQUIRED", "Thiếu If-Match")
        if if_match != row["revision"]:
            raise ApiError(409, "REVISION_CONFLICT", "Việc đã bị đổi ở nơi khác", {"current": dict(row)})
        sets, vals = [], []
        if "status" in data:
            if data["status"] not in TASK_STATUS:
                raise bad_request("Trạng thái không hợp lệ")
            sets.append("status=?"), vals.append(data["status"])
        for k, lim in (("title", 200), ("description", 5000), ("assignee", 80)):
            if k in data:
                v = str(data[k] or "").strip()
                if k == "title" and not v:
                    raise bad_request("Tiêu đề không được rỗng")
                sets.append(f"{k}=?"), vals.append(v[:lim])
        if not sets:
            raise bad_request("Không có trường nào để cập nhật")
        conn.execute(f"UPDATE tasks SET {', '.join(sets)}, revision=revision+1, updated_at=? WHERE id=?",
                     (*vals, now_iso(), tid))
        audit(conn, actor, "task.update", "task", tid, detail={"fields": sorted(data)})
    r = dict(conn.execute("SELECT * FROM tasks WHERE id=?", (tid,)).fetchone())
    return Response.json(r, headers={"ETag": f'"{r["revision"]}"'})


# ---------------------------------------------------------------- nhật ký

def _int_param(value, default: int, lo: int, hi: int, name: str) -> int:
    if value in (None, ""):
        return default
    try:
        v = int(value)
    except (TypeError, ValueError) as exc:
        raise bad_request(f"{name} phải là số nguyên") from exc
    return min(hi, max(lo, v))


def list_activity(conn, actor: Actor, q: dict) -> dict:
    where, args = [], []
    if not actor.is_owner:
        where.append("actor_id=?"), args.append(actor.id)
    if q.get("before_id"):
        where.append("id<?"), args.append(_int_param(q["before_id"], 0, 0, 2 ** 62, "before_id"))
    for k in ("action", "object_type", "object_id", "result"):
        if q.get(k):
            where.append(f"{k}=?"), args.append(q[k])
    limit = _int_param(q.get("limit"), 50, 1, 200, "limit")
    rows = conn.execute("SELECT * FROM activity_logs " + ("WHERE " + " AND ".join(where) if where else "") +
                        " ORDER BY id DESC LIMIT ?", (*args, limit)).fetchall()
    return {"items": [dict(r) | {"detail": json.loads(r["detail_json"])} for r in rows]}


# ---------------------------------------------------------------- dashboard (số liệu thật từ DB)

def dashboard(conn, actor: Actor, health: dict) -> dict:
    ids = readable_project_ids(conn, actor)
    if ids is None:
        pcond, pargs = "", []
    elif ids:
        pcond, pargs = f" AND project_id IN ({','.join('?' * len(ids))})", ids
    else:
        pcond, pargs = " AND 0", []
    one = lambda sql, a=(): conn.execute(sql, a).fetchone()[0]  # noqa: E731
    prompts_active = one(f"SELECT COUNT(*) FROM prompts WHERE archived_at IS NULL{pcond}", pargs)
    prompts_archived = one(f"SELECT COUNT(*) FROM prompts WHERE archived_at IS NOT NULL{pcond}", pargs)
    versions = one(f"SELECT COUNT(*) FROM prompt_versions WHERE prompt_id IN (SELECT id FROM prompts WHERE 1{pcond})", pargs)
    approved = one("SELECT COUNT(*) FROM prompt_versions WHERE review_status='APPROVED' AND prompt_id IN "
                   f"(SELECT id FROM prompts WHERE 1{pcond})", pargs)
    in_prod = one(f"SELECT COUNT(*) FROM prompts WHERE production_version_id IS NOT NULL{pcond}", pargs)
    tasks = {r[0]: r[1] for r in conn.execute(
        f"SELECT status, COUNT(*) FROM tasks WHERE 1{pcond if ids is None else pcond + ' AND project_id IS NOT NULL'} "
        "GROUP BY status", pargs)}
    modules = {r[0]: r[1] for r in conn.execute("SELECT dev_status, COUNT(*) FROM modules GROUP BY dev_status")}
    today = now_iso()[:10]
    ai_calls_today = one("SELECT COUNT(*) FROM ai_usage WHERE ts>=? AND status IN ('SUCCEEDED','FAILED')", (today,))
    ai_cost_month = conn.execute("SELECT COALESCE(SUM(cost_usd),0) FROM ai_usage WHERE ts>=?", (today[:7],)).fetchone()[0]
    recent = conn.execute("SELECT id,title,updated_at,project_id FROM prompts WHERE archived_at IS NULL"
                          f"{pcond} ORDER BY updated_at DESC, id LIMIT 8", pargs).fetchall()
    activity = list_activity(conn, actor, {"limit": 10})["items"]
    return {
        "generated_at": now_iso(),
        "prompts": {"active": prompts_active, "archived": prompts_archived, "versions": versions,
                    "approved_versions": approved, "in_production": in_prod},
        "projects": len(list_projects(conn, actor)["items"]),
        "tasks": tasks, "modules": modules,
        "ai": {"enabled": bool(get_setting(conn, "ai.enabled")), "calls_today": ai_calls_today,
               "cost_month_usd": ai_cost_month},
        "integrations": health,
        "recent_prompts": [dict(r) for r in recent],
        "recent_activity": activity,
    }
