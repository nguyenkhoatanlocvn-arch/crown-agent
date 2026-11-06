"""CROWN PROMPT STUDIO — dịch vụ: thư viện, bản nháp, phiên bản bất biến, diff, khôi phục, tìm kiếm,
biến/khối tái sử dụng, phân tích tĩnh, nguồn tham chiếu, nhập/xuất.

Mô hình trạng thái (tách biệt có chủ đích):
  * draft_content  — bản đang sửa (autosave ghi vào đây, KHÔNG bao giờ ghi vào phiên bản).
  * prompt_versions — snapshot bất biến (trigger SQLite chặn sửa/xóa nội dung).
  * review_status  — trạng thái kiểm thử/duyệt của TỪNG phiên bản.
  * production_version_id — con trỏ riêng, chỉ trỏ tới phiên bản APPROVED, đổi bằng thao tác tường minh.
  * revision       — số tăng mỗi lần ghi vào hàng prompt; client gửi If-Match để phát hiện xung đột.
"""
from __future__ import annotations

import base64
import binascii
import difflib
import json
import re
import sqlite3
import unicodedata
from urllib.parse import quote

from .auth import Actor, audit, readable_project_ids, require_owner, require_project
from .db import tx
from .httpcore import ApiError, Response, bad_request
from .util import dumps, fold, new_id, now_iso, sha256_text, tokens

MAX_CONTENT_CHARS = 200_000
MAX_TITLE = 200
MAX_TAGS = 20
IMPORT_MAX_BYTES = 1_000_000
IMPORT_MAX_FILES = 50
IMPORT_EXTS = {".txt": "txt", ".md": "md", ".markdown": "md"}

VAR_RE = re.compile(r"\{\{\s*([^\W\d][\w.]*)\s*\}\}")
BLOCK_RE = re.compile(r"\{\{>\s*(P-[0-9a-f]{12})\s*\}\}")
ANY_BRACE_RE = re.compile(r"\{\{(.*?)\}\}", re.S)


# ---------------------------------------------------------------- tiện ích kiểm tra

def _text(data: dict, key: str, limit: int, *, required=False, default="") -> str:
    val = data.get(key, default)
    if val is None:
        val = ""
    if not isinstance(val, str):
        raise bad_request(f"Trường '{key}' phải là chuỗi")
    val = val.strip()
    if required and not val:
        raise bad_request(f"Trường '{key}' là bắt buộc")
    if len(val) > limit:
        raise bad_request(f"Trường '{key}' tối đa {limit} ký tự")
    return val


def _content(val) -> str:
    if not isinstance(val, str):
        raise bad_request("Nội dung phải là chuỗi")
    if "\x00" in val:
        raise bad_request("Nội dung chứa ký tự NUL")
    if len(val) > MAX_CONTENT_CHARS:
        raise bad_request(f"Nội dung tối đa {MAX_CONTENT_CHARS} ký tự", "CONTENT_TOO_LARGE")
    return val


def _tags(val) -> list[str]:
    if val is None:
        return []
    if not isinstance(val, list) or not all(isinstance(t, str) for t in val):
        raise bad_request("tags phải là danh sách chuỗi")
    out, seen = [], set()
    for t in val:
        t = " ".join(t.split())
        if not t:
            continue
        if len(t) > 40:
            raise bad_request("Mỗi tag tối đa 40 ký tự")
        if t.casefold() not in seen:
            seen.add(t.casefold())
            out.append(t)
    if len(out) > MAX_TAGS:
        raise bad_request(f"Tối đa {MAX_TAGS} tag")
    return out


def _conflict(row) -> ApiError:
    return ApiError(409, "REVISION_CONFLICT",
                    "Prompt đã được thay đổi ở nơi khác. Hãy xem khác biệt và chọn cách giải quyết.",
                    {"current_revision": row["revision"], "draft_content": row["draft_content"],
                     "draft_updated_at": row["draft_updated_at"], "draft_updated_by": row["draft_updated_by"],
                     "title": row["title"], "updated_at": row["updated_at"]})


def _check_rev(row, if_match):
    if if_match is None:
        raise ApiError(428, "PRECONDITION_REQUIRED", "Thiếu If-Match (revision) cho thao tác ghi")
    if int(if_match) != row["revision"]:
        raise _conflict(row)


# ---------------------------------------------------------------- chỉ mục tìm kiếm

def fts_available(conn) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE name='prompt_fts'").fetchone() is not None


def ensure_search_index(conn, *, force_fallback: bool = False) -> str:
    """Tạo bảng FTS5 nếu SQLite hỗ trợ; dựng lại chỉ mục nếu lệch. Trả về 'fts5' hoặc 'like'."""
    mode = "like"
    if not force_fallback:
        try:
            conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS prompt_fts USING fts5("
                         "prompt_id UNINDEXED, title, body, tags, tokenize='unicode61 remove_diacritics 2')")
            mode = "fts5"
        except sqlite3.OperationalError:
            mode = "like"
    else:
        conn.execute("DROP TABLE IF EXISTS prompt_fts")
    n_prompts = conn.execute("SELECT COUNT(*) FROM prompts").fetchone()[0]
    n_docs = conn.execute("SELECT COUNT(*) FROM search_docs").fetchone()[0]
    n_fts = conn.execute("SELECT COUNT(*) FROM prompt_fts").fetchone()[0] if mode == "fts5" else n_prompts
    if n_prompts != n_docs or n_prompts != n_fts:
        with tx(conn):
            conn.execute("DELETE FROM search_docs")
            if mode == "fts5":
                conn.execute("DELETE FROM prompt_fts")
            for (pid,) in conn.execute("SELECT id FROM prompts").fetchall():
                _index(conn, pid)
    return mode


def _index(conn, pid: str) -> None:
    p = conn.execute("SELECT * FROM prompts WHERE id=?", (pid,)).fetchone()
    tags = [r[0] for r in conn.execute(
        "SELECT t.name FROM prompt_tags pt JOIN tags t ON t.id=pt.tag_id WHERE pt.prompt_id=? ORDER BY t.name", (pid,))]
    head = ""
    if p["head_version_id"]:
        head = conn.execute("SELECT content FROM prompt_versions WHERE id=?", (p["head_version_id"],)).fetchone()[0]
    body_parts = [p["description"], p["category"], p["target_ai"], p["draft_content"]]
    if head and head != p["draft_content"]:
        body_parts.append(head)
    title_f, body_f, tags_f = fold(p["title"]), fold("\n".join(body_parts)), fold(" ".join(tags))
    conn.execute("INSERT OR REPLACE INTO search_docs(prompt_id,title_f,body_f,tags_f) VALUES (?,?,?,?)",
                 (pid, title_f, body_f, tags_f))
    if fts_available(conn):
        conn.execute("DELETE FROM prompt_fts WHERE prompt_id=?", (pid,))
        conn.execute("INSERT INTO prompt_fts(prompt_id,title,body,tags) VALUES (?,?,?,?)",
                     (pid, title_f, body_f, tags_f))


# ---------------------------------------------------------------- đọc

def _row(conn, pid: str):
    row = conn.execute("SELECT * FROM prompts WHERE id=?", (pid,)).fetchone()
    if not row:
        raise ApiError(404, "NOT_FOUND", "Không tìm thấy hoặc không có quyền truy cập")
    return row


def _load(conn, actor: Actor, pid: str, need: str = "read"):
    row = _row(conn, pid)
    role = require_project(conn, actor, row["project_id"], need)
    return row, role


def _version_summary(r) -> dict:
    return {k: r[k] for k in ("id", "version_no", "checksum", "changelog", "author_id", "created_at",
                              "restored_from", "review_status", "reviewed_by", "reviewed_at", "review_note")}


def detail(conn, actor: Actor, pid: str) -> dict:
    row, role = _load(conn, actor, pid)
    tags = [r[0] for r in conn.execute(
        "SELECT t.name FROM prompt_tags pt JOIN tags t ON t.id=pt.tag_id WHERE pt.prompt_id=? ORDER BY t.name", (pid,))]
    head = prod = None
    if row["head_version_id"]:
        head = _version_summary(conn.execute("SELECT * FROM prompt_versions WHERE id=?",
                                             (row["head_version_id"],)).fetchone())
        head_content = conn.execute("SELECT content FROM prompt_versions WHERE id=?",
                                    (row["head_version_id"],)).fetchone()[0]
    else:
        head_content = ""
    if row["production_version_id"]:
        prod = _version_summary(conn.execute("SELECT * FROM prompt_versions WHERE id=?",
                                             (row["production_version_id"],)).fetchone())
    project = conn.execute("SELECT id,name FROM projects WHERE id=?", (row["project_id"],)).fetchone()
    n_versions = conn.execute("SELECT COUNT(*) FROM prompt_versions WHERE prompt_id=?", (pid,)).fetchone()[0]
    return {
        "id": row["id"], "project": dict(project), "title": row["title"], "description": row["description"],
        "category": row["category"], "target_ai": row["target_ai"], "tags": tags, "author_id": row["author_id"],
        "created_at": row["created_at"], "updated_at": row["updated_at"], "archived_at": row["archived_at"],
        "favorite": bool(row["favorite"]), "revision": row["revision"], "draft_content": row["draft_content"],
        "draft_updated_at": row["draft_updated_at"], "draft_updated_by": row["draft_updated_by"],
        "draft_dirty": row["draft_content"] != head_content, "head_version": head, "production_version": prod,
        "version_count": n_versions, "your_role": role,
    }


def _resp(conn, actor, pid, status=200):
    d = detail(conn, actor, pid)
    return Response.json(d, status, {"ETag": f'"{d["revision"]}"'})


# ---------------------------------------------------------------- ghi

def _set_tags(conn, pid: str, tags: list[str]) -> None:
    conn.execute("DELETE FROM prompt_tags WHERE prompt_id=?", (pid,))
    for t in tags:
        conn.execute("INSERT OR IGNORE INTO tags(name) VALUES (?)", (t,))
        tid = conn.execute("SELECT id FROM tags WHERE name=? COLLATE NOCASE", (t,)).fetchone()[0]
        conn.execute("INSERT OR IGNORE INTO prompt_tags(prompt_id,tag_id) VALUES (?,?)", (pid, tid))


def _insert_version(conn, actor: Actor, pid: str, content: str, changelog: str, restored_from=None) -> str:
    no = conn.execute("SELECT COALESCE(MAX(version_no),0)+1 FROM prompt_versions WHERE prompt_id=?",
                      (pid,)).fetchone()[0]
    vid = new_id("V")
    conn.execute("INSERT INTO prompt_versions(id,prompt_id,version_no,content,checksum,changelog,author_id,created_at,"
                 "restored_from) VALUES (?,?,?,?,?,?,?,?,?)",
                 (vid, pid, no, content, sha256_text(content), changelog, actor.id, now_iso(), restored_from))
    return vid


def create(conn, actor: Actor, data: dict, *, _in_tx=False) -> str:
    project_id = _text(data, "project_id", 64, required=True)
    title = _text(data, "title", MAX_TITLE, required=True)
    description = _text(data, "description", 2000)
    category = _text(data, "category", 80)
    target_ai = _text(data, "target_ai", 80)
    tags = _tags(data.get("tags"))
    content = _content(data.get("content", ""))
    changelog = _text(data, "changelog", 500, default="Bản đầu tiên") or "Bản đầu tiên"

    def work():
        require_project(conn, actor, project_id, "write")
        pid, ts = new_id("P"), now_iso()
        conn.execute("INSERT INTO prompts(id,project_id,title,description,category,target_ai,author_id,created_at,"
                     "updated_at,draft_content,draft_updated_at,draft_updated_by) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                     (pid, project_id, title, description, category, target_ai, actor.id, ts, ts, content, ts,
                      actor.id))
        if content.strip():
            vid = _insert_version(conn, actor, pid, content, changelog)
            conn.execute("UPDATE prompts SET head_version_id=? WHERE id=?", (vid, pid))
        _set_tags(conn, pid, tags)
        _index(conn, pid)
        audit(conn, actor, "prompt.create", "prompt", pid, detail={"project_id": project_id})
        return pid

    if _in_tx:
        return work()
    with tx(conn):
        return work()


def update_meta(conn, actor: Actor, pid: str, data: dict, if_match) -> Response:
    with tx(conn):
        row, _ = _load(conn, actor, pid, "write")
        _check_rev(row, if_match)
        sets, vals, changed = [], [], []
        for key, limit, required in (("title", MAX_TITLE, True), ("description", 2000, False),
                                     ("category", 80, False), ("target_ai", 80, False)):
            if key in data:
                sets.append(f"{key}=?"), vals.append(_text(data, key, limit, required=required)), changed.append(key)
        if "favorite" in data:
            sets.append("favorite=?"), vals.append(1 if data["favorite"] else 0), changed.append("favorite")
        if "project_id" in data and data["project_id"] != row["project_id"]:
            new_project = _text(data, "project_id", 64, required=True)
            require_project(conn, actor, new_project, "write")
            sets.append("project_id=?"), vals.append(new_project), changed.append("project_id")
        if "tags" in data:
            _set_tags(conn, pid, _tags(data["tags"]))
            changed.append("tags")
        if not changed:
            raise bad_request("Không có trường nào để cập nhật")
        sets += ["revision=revision+1", "updated_at=?"]
        vals += [now_iso(), pid]
        conn.execute(f"UPDATE prompts SET {', '.join(sets)} WHERE id=?", vals)
        _index(conn, pid)
        audit(conn, actor, "prompt.update_meta", "prompt", pid, detail={"fields": changed})
    return _resp(conn, actor, pid)


def save_draft(conn, actor: Actor, pid: str, content, if_match) -> Response:
    content = _content(content)
    with tx(conn):
        row, _ = _load(conn, actor, pid, "write")
        if row["archived_at"]:
            raise ApiError(409, "ARCHIVED", "Prompt đã lưu trữ; khôi phục trước khi sửa")
        _check_rev(row, if_match)
        ts = now_iso()
        conn.execute("UPDATE prompts SET draft_content=?, draft_updated_at=?, draft_updated_by=?, "
                     "revision=revision+1, updated_at=? WHERE id=?", (content, ts, actor.id, ts, pid))
        _index(conn, pid)
        # Không ghi audit cho từng lần autosave để nhật ký không phình; lần lưu phiên bản mới được ghi.
    return _resp(conn, actor, pid)


def commit_version(conn, actor: Actor, pid: str, data: dict, if_match) -> Response:
    changelog = _text(data, "changelog", 500)
    with tx(conn):
        row, _ = _load(conn, actor, pid, "write")
        if row["archived_at"]:
            raise ApiError(409, "ARCHIVED", "Prompt đã lưu trữ; khôi phục trước khi sửa")
        _check_rev(row, if_match)
        content = row["draft_content"]
        if "content" in data:
            content = _content(data["content"])
        if not content.strip():
            raise bad_request("Không lưu phiên bản rỗng", "EMPTY_CONTENT")
        if row["head_version_id"]:
            head = conn.execute("SELECT content FROM prompt_versions WHERE id=?", (row["head_version_id"],)).fetchone()
            if head[0] == content:
                raise ApiError(409, "NO_CHANGE", "Nội dung giống phiên bản hiện tại; không tạo phiên bản mới")
        vid = _insert_version(conn, actor, pid, content, changelog or "Cập nhật nội dung")
        ts = now_iso()
        conn.execute("UPDATE prompts SET head_version_id=?, draft_content=?, draft_updated_at=?, draft_updated_by=?, "
                     "revision=revision+1, updated_at=? WHERE id=?", (vid, content, ts, actor.id, ts, pid))
        _index(conn, pid)
        audit(conn, actor, "prompt.version.create", "prompt", pid, detail={"version_id": vid})
    return _resp(conn, actor, pid, 201)


def list_versions(conn, actor: Actor, pid: str) -> dict:
    _load(conn, actor, pid)
    rows = conn.execute("SELECT * FROM prompt_versions WHERE prompt_id=? ORDER BY version_no DESC", (pid,)).fetchall()
    return {"items": [_version_summary(r) for r in rows]}


def _version(conn, pid: str, vid: str):
    r = conn.execute("SELECT * FROM prompt_versions WHERE id=? AND prompt_id=?", (vid, pid)).fetchone()
    if not r:
        raise ApiError(404, "NOT_FOUND", "Không tìm thấy phiên bản")
    return r


def get_version(conn, actor: Actor, pid: str, vid: str) -> dict:
    _load(conn, actor, pid)
    r = _version(conn, pid, vid)
    out = _version_summary(r)
    out["content"] = r["content"]
    return out


def line_diff(a: str, b: str) -> dict:
    al, bl = a.split("\n"), b.split("\n")
    sm = difflib.SequenceMatcher(None, al, bl, autojunk=False)
    rows, added, removed = [], 0, 0
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            for k in range(i2 - i1):
                rows.append({"op": "equal", "a": i1 + k + 1, "b": j1 + k + 1, "text": al[i1 + k]})
            continue
        for k in range(i1, i2):
            rows.append({"op": "delete", "a": k + 1, "b": None, "text": al[k]})
            removed += 1
        for k in range(j1, j2):
            rows.append({"op": "insert", "a": None, "b": k + 1, "text": bl[k]})
            added += 1
    return {"rows": rows, "added": added, "removed": removed, "identical": added == 0 and removed == 0}


def _content_of(conn, row, ref: str) -> tuple[str, str]:
    if ref == "draft":
        return row["draft_content"], "Bản nháp"
    if ref == "head":
        if not row["head_version_id"]:
            return "", "Chưa có phiên bản"
        ref = row["head_version_id"]
    v = _version(conn, row["id"], ref)
    return v["content"], f"v{v['version_no']}"


def diff(conn, actor: Actor, pid: str, a_ref: str, b_ref: str) -> dict:
    row, _ = _load(conn, actor, pid)
    if not a_ref or not b_ref:
        raise bad_request("Cần tham số from và to")
    a, al = _content_of(conn, row, a_ref)
    b, bl = _content_of(conn, row, b_ref)
    out = line_diff(a, b)
    out.update({"from": {"ref": a_ref, "label": al}, "to": {"ref": b_ref, "label": bl}})
    return out


def restore(conn, actor: Actor, pid: str, vid: str, data: dict, if_match) -> Response:
    with tx(conn):
        row, _ = _load(conn, actor, pid, "write")
        _check_rev(row, if_match)
        old = _version(conn, pid, vid)
        head = None
        if row["head_version_id"]:
            head = conn.execute("SELECT content FROM prompt_versions WHERE id=?", (row["head_version_id"],)).fetchone()
        if head and head[0] == old["content"]:
            raise ApiError(409, "NO_CHANGE", "Phiên bản hiện tại đã có đúng nội dung này")
        note = _text(data, "changelog", 500) or f"Khôi phục nội dung từ v{old['version_no']}"
        new_vid = _insert_version(conn, actor, pid, old["content"], note, restored_from=vid)
        ts = now_iso()
        # Không đụng production_version_id và assignment của Agent.
        conn.execute("UPDATE prompts SET head_version_id=?, draft_content=?, draft_updated_at=?, draft_updated_by=?, "
                     "revision=revision+1, updated_at=? WHERE id=?",
                     (new_vid, old["content"], ts, actor.id, ts, pid))
        _index(conn, pid)
        audit(conn, actor, "prompt.version.restore", "prompt", pid,
              detail={"from_version_id": vid, "new_version_id": new_vid})
    return _resp(conn, actor, pid, 201)


REVIEW_STATES = ("DRAFT", "TESTING", "APPROVED", "REJECTED")


def review(conn, actor: Actor, pid: str, vid: str, data: dict) -> dict:
    status = data.get("status")
    if status not in REVIEW_STATES:
        raise bad_request("status phải là DRAFT, TESTING, APPROVED hoặc REJECTED")
    note = _text(data, "note", 500)
    with tx(conn):
        row, _ = _load(conn, actor, pid, "write")
        v = _version(conn, pid, vid)
        if status in ("APPROVED", "REJECTED") or v["review_status"] == "APPROVED":
            require_owner(actor)
        if v["review_status"] == "APPROVED" and status != "APPROVED":
            if row["production_version_id"] == vid:
                raise ApiError(409, "IN_PRODUCTION", "Phiên bản đang là production; đổi con trỏ production trước")
            used = conn.execute("SELECT COUNT(*) FROM agent_prompt_assignments WHERE version_id=? AND revoked_at IS NULL",
                                (vid,)).fetchone()[0]
            if used:
                raise ApiError(409, "IN_USE", f"Phiên bản đang được cấp cho {used} Agent; thu hồi trước")
        conn.execute("UPDATE prompt_versions SET review_status=?, reviewed_by=?, reviewed_at=?, review_note=? WHERE id=?",
                     (status, actor.id, now_iso(), note, vid))
        audit(conn, actor, "prompt.version.review", "prompt", pid, detail={"version_id": vid, "status": status})
    return get_version(conn, actor, pid, vid)


def set_production(conn, actor: Actor, pid: str, data: dict) -> Response:
    require_owner(actor)
    vid = data.get("version_id")
    if "expected_version_id" not in data:
        raise bad_request("Cần expected_version_id (con trỏ production hiện tại mà bạn thấy, có thể null)")
    with tx(conn):
        row, _ = _load(conn, actor, pid, "write")
        if row["production_version_id"] != data["expected_version_id"]:
            raise ApiError(409, "PRODUCTION_CONFLICT", "Con trỏ production đã bị đổi ở nơi khác",
                           {"current_production_version_id": row["production_version_id"]})
        if vid is not None:
            v = _version(conn, pid, vid)
            if v["review_status"] != "APPROVED":
                raise ApiError(409, "NOT_APPROVED", "Chỉ phiên bản APPROVED mới được đặt làm production")
        # Không tăng revision: con trỏ production có kiểm tra riêng (expected_version_id), không làm
        # bản nháp đang mở ở tab khác bị báo xung đột giả.
        conn.execute("UPDATE prompts SET production_version_id=?, updated_at=? WHERE id=?", (vid, now_iso(), pid))
        audit(conn, actor, "prompt.production.set", "prompt", pid,
              detail={"from": row["production_version_id"], "to": vid})
    return _resp(conn, actor, pid)


def set_archived(conn, actor: Actor, pid: str, archived: bool, if_match) -> Response:
    with tx(conn):
        row, _ = _load(conn, actor, pid, "write")
        _check_rev(row, if_match)
        conn.execute("UPDATE prompts SET archived_at=?, revision=revision+1, updated_at=? WHERE id=?",
                     (now_iso() if archived else None, now_iso(), pid))
        audit(conn, actor, "prompt.archive" if archived else "prompt.unarchive", "prompt", pid)
    return _resp(conn, actor, pid)


def duplicate(conn, actor: Actor, pid: str, data: dict) -> Response:
    with tx(conn):
        row, _ = _load(conn, actor, pid)
        tags = [r[0] for r in conn.execute(
            "SELECT t.name FROM prompt_tags pt JOIN tags t ON t.id=pt.tag_id WHERE pt.prompt_id=?", (pid,))]
        title = _text(data, "title", MAX_TITLE) or (row["title"][: MAX_TITLE - 12] + " (bản sao)")
        new_pid = create(conn, actor, {
            "project_id": data.get("project_id") or row["project_id"], "title": title,
            "description": row["description"], "category": row["category"], "target_ai": row["target_ai"],
            "tags": tags, "content": row["draft_content"], "changelog": f"Nhân bản từ {pid}"}, _in_tx=True)
    return _resp(conn, actor, new_pid, 201)


# ---------------------------------------------------------------- tìm kiếm

def _like_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def search(conn, actor: Actor, q: dict) -> dict:
    where, args = [], []
    ids = readable_project_ids(conn, actor)
    if ids is not None:
        if not ids:
            return {"items": [], "total": 0, "page": 1, "limit": 20, "search_mode": "none"}
        where.append(f"p.project_id IN ({','.join('?' * len(ids))})")
        args += ids
    project_id = q.get("project_id")
    if project_id:
        require_project(conn, actor, project_id)
        where.append("p.project_id=?"), args.append(project_id)
    archived = q.get("archived", "0")
    if archived == "0":
        where.append("p.archived_at IS NULL")
    elif archived == "1":
        where.append("p.archived_at IS NOT NULL")
    for key in ("category", "target_ai"):
        if q.get(key):
            where.append(f"p.{key}=?"), args.append(q[key])
    if q.get("favorite") == "1":
        where.append("p.favorite=1")
    if q.get("tag"):
        where.append("EXISTS (SELECT 1 FROM prompt_tags pt JOIN tags t ON t.id=pt.tag_id "
                     "WHERE pt.prompt_id=p.id AND t.name=? COLLATE NOCASE)")
        args.append(q["tag"])
    try:
        page = max(1, int(q.get("page") or 1))
        limit = min(100, max(1, int(q.get("limit") or 20)))
    except ValueError as exc:
        raise bad_request("page/limit phải là số") from exc
    text = (q.get("q") or "").strip()
    toks = tokens(text)[:12]
    mode = "fts5" if fts_available(conn) else "like"
    join, order_rank, rank_args = "", "", []
    if toks:
        if mode == "fts5":
            join = ("JOIN (SELECT prompt_id, bm25(prompt_fts, 0.0, 6.0, 1.0, 3.0) AS rank FROM prompt_fts "
                    "WHERE prompt_fts MATCH ?) f ON f.prompt_id=p.id")
            rank_args = [" AND ".join(f'"{t}"*' for t in toks)]
            order_rank = "f.rank,"
        else:
            join = "JOIN search_docs s ON s.prompt_id=p.id"
            for t in toks:
                like = f"%{_like_escape(t)}%"
                where.append("(s.title_f LIKE ? ESCAPE '\\' OR s.body_f LIKE ? ESCAPE '\\' "
                             "OR s.tags_f LIKE ? ESCAPE '\\')")
                args += [like, like, like]
            order_rank = ("(CASE WHEN s.title_f LIKE ? ESCAPE '\\' THEN 0 ELSE 1 END),")
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""
    total = conn.execute(f"SELECT COUNT(*) FROM prompts p {join} {where_sql}", rank_args + args).fetchone()[0]
    if toks:
        # Ưu tiên tiêu đề chứa đúng chuỗi có dấu người dùng gõ, sau đó điểm liên quan, rồi thứ tự ổn định.
        exact = f"%{_like_escape(text)}%"
        order = f"ORDER BY (CASE WHEN p.title LIKE ? ESCAPE '\\' THEN 0 ELSE 1 END), {order_rank} p.updated_at DESC, p.id"
        order_args = [exact] + ([f"%{_like_escape(toks[0])}%"] if mode == "like" else [])
    elif q.get("sort") == "title":
        order, order_args = "ORDER BY p.title COLLATE NOCASE, p.id", []
    else:
        order, order_args = "ORDER BY p.updated_at DESC, p.id", []
    rows = conn.execute(
        f"SELECT p.*, pr.name AS project_name FROM prompts p JOIN projects pr ON pr.id=p.project_id {join} {where_sql} "
        f"{order} LIMIT ? OFFSET ?", rank_args + args + order_args + [limit, (page - 1) * limit]).fetchall()
    items = []
    for r in rows:
        tags = [t[0] for t in conn.execute(
            "SELECT t.name FROM prompt_tags pt JOIN tags t ON t.id=pt.tag_id WHERE pt.prompt_id=? ORDER BY t.name",
            (r["id"],))]
        items.append({"id": r["id"], "title": r["title"], "description": r["description"],
                      "project_id": r["project_id"], "project_name": r["project_name"], "category": r["category"],
                      "target_ai": r["target_ai"], "tags": tags, "favorite": bool(r["favorite"]),
                      "archived": r["archived_at"] is not None, "updated_at": r["updated_at"],
                      "has_production": r["production_version_id"] is not None,
                      "snippet": r["draft_content"][:160]})
    return {"items": items, "total": total, "page": page, "limit": limit, "search_mode": mode if toks else "filter"}


def facets(conn, actor: Actor) -> dict:
    ids = readable_project_ids(conn, actor)
    cond, args = "", []
    if ids is not None:
        if not ids:
            return {"tags": [], "categories": [], "target_ais": []}
        cond = f"WHERE p.project_id IN ({','.join('?' * len(ids))})"
        args = ids
    tags = [r[0] for r in conn.execute(
        f"SELECT DISTINCT t.name FROM tags t JOIN prompt_tags pt ON pt.tag_id=t.id JOIN prompts p ON p.id=pt.prompt_id "
        f"{cond} ORDER BY t.name COLLATE NOCASE", args)]
    cats = [r[0] for r in conn.execute(
        f"SELECT DISTINCT p.category FROM prompts p {cond} {'AND' if cond else 'WHERE'} p.category<>'' "
        f"ORDER BY p.category", args)]
    ais = [r[0] for r in conn.execute(
        f"SELECT DISTINCT p.target_ai FROM prompts p {cond} {'AND' if cond else 'WHERE'} p.target_ai<>'' "
        f"ORDER BY p.target_ai", args)]
    return {"tags": tags, "categories": cats, "target_ais": ais}


# ---------------------------------------------------------------- biến, khối, phân tích tĩnh

def variables_of(text: str) -> list[str]:
    seen, out = set(), []
    for m in VAR_RE.finditer(text):
        if m.group(1) not in seen:
            seen.add(m.group(1))
            out.append(m.group(1))
    return out


def _expand_blocks(conn, actor: Actor, text: str, chain: tuple, errors: list) -> str:
    def repl(m):
        ref = m.group(1)
        if ref in chain:
            errors.append({"code": "BLOCK_CYCLE", "message": f"Khối {ref} tham chiếu vòng: {' → '.join(chain + (ref,))}"})
            return m.group(0)
        if len(chain) >= 5:
            errors.append({"code": "BLOCK_TOO_DEEP", "message": "Khối lồng quá 5 tầng"})
            return m.group(0)
        row = conn.execute("SELECT * FROM prompts WHERE id=?", (ref,)).fetchone()
        if not row:
            errors.append({"code": "BLOCK_MISSING", "message": f"Không tìm thấy khối {ref}"})
            return m.group(0)
        try:
            require_project(conn, actor, row["project_id"])
        except ApiError:
            errors.append({"code": "BLOCK_FORBIDDEN", "message": f"Không có quyền đọc khối {ref}"})
            return m.group(0)
        vid = row["production_version_id"] or row["head_version_id"]
        if not vid:
            errors.append({"code": "BLOCK_EMPTY", "message": f"Khối {ref} chưa có phiên bản"})
            return m.group(0)
        content = conn.execute("SELECT content FROM prompt_versions WHERE id=?", (vid,)).fetchone()[0]
        return _expand_blocks(conn, actor, content, chain + (ref,), errors)

    return BLOCK_RE.sub(repl, text)


def render(conn, actor: Actor, pid: str, data: dict) -> dict:
    row, _ = _load(conn, actor, pid)
    ref = data.get("version", "draft")
    text, label = _content_of(conn, row, ref)
    values = data.get("variables") or {}
    if not isinstance(values, dict):
        raise bad_request("variables phải là đối tượng")
    for k, v in values.items():
        if not isinstance(v, (str, int, float)) or isinstance(v, bool):
            raise bad_request(f"Giá trị biến '{k}' phải là chuỗi hoặc số")
    errors: list = []
    expanded = _expand_blocks(conn, actor, text, (pid,), errors)
    needed = variables_of(expanded)
    missing = [n for n in needed if n not in values or str(values[n]) == ""]
    unused = [k for k in values if k not in needed]
    if errors or missing:
        raise ApiError(400, "RENDER_INCOMPLETE", "Chưa đủ dữ liệu để dựng prompt: thiếu biến hoặc khối lỗi",
                       {"missing_variables": missing, "block_errors": errors, "unused_variables": unused})
    # Thay một lượt: giá trị chứa {{...}} không bị mở rộng tiếp.
    out = VAR_RE.sub(lambda m: str(values[m.group(1)]), expanded)
    return {"text": out, "source": label, "variables": needed, "unused_variables": unused}


OUTPUT_HINTS = ("định dạng", "dinh dang", "format", "output", "đầu ra", "trả lời theo", "json", "bảng", "markdown")


def analyze_text(text: str, max_chars: int = 20_000) -> list[dict]:
    out = []
    if not text.strip():
        return [{"rule": "EMPTY", "severity": "error", "message": "Prompt rỗng"}]
    if len(text) > max_chars:
        out.append({"rule": "LENGTH", "severity": "warning",
                    "message": f"Dài {len(text):,} ký tự, vượt ngưỡng {max_chars:,} đã đặt"})
    lines = text.split("\n")
    for m in ANY_BRACE_RE.finditer(text):
        whole = m.group(0)
        if not VAR_RE.fullmatch(whole) and not BLOCK_RE.fullmatch(whole):
            line = text.count("\n", 0, m.start()) + 1
            out.append({"rule": "VAR_SYNTAX", "severity": "error", "line": line,
                        "message": f"Cú pháp biến/khối không hợp lệ: {whole[:60]}"})
    stray = len(re.findall(r"\{\{", text)) - len(ANY_BRACE_RE.findall(text))
    if stray > 0:
        out.append({"rule": "VAR_UNCLOSED", "severity": "error", "message": f"{stray} chỗ mở '{{{{' chưa đóng"})
    seen: dict[str, int] = {}
    for i, ln in enumerate(lines, 1):
        key = " ".join(ln.split())
        if len(key) >= 20:
            if key in seen:
                out.append({"rule": "DUPLICATE_LINE", "severity": "warning", "line": i,
                            "message": f"Dòng {i} lặp lại dòng {seen[key]}"})
            else:
                seen[key] = i
    if len(text) > 1500 and not any(ln.lstrip().startswith("#") for ln in lines):
        out.append({"rule": "STRUCTURE", "severity": "info",
                    "message": "Prompt dài nhưng chưa chia mục (tiêu đề #). Cân nhắc tách Mục tiêu/Đầu vào/Ràng buộc/Đầu ra"})
    folded = fold(text)
    if not any(h in folded for h in (fold(x) for x in OUTPUT_HINTS)):
        out.append({"rule": "OUTPUT_FORMAT", "severity": "info",
                    "message": "Chưa thấy mô tả định dạng đầu ra (gợi ý, không bắt buộc)"})
    return out


def analysis(conn, actor: Actor, pid: str, ref: str = "draft") -> dict:
    row, _ = _load(conn, actor, pid)
    text, label = _content_of(conn, row, ref)
    findings = analyze_text(text)
    errors: list = []
    _expand_blocks(conn, actor, text, (pid,), errors)
    for e in errors:
        findings.append({"rule": e["code"], "severity": "error", "message": e["message"]})
    return {"source": label, "chars": len(text), "lines": text.count("\n") + 1 if text else 0,
            "variables": variables_of(text), "blocks": sorted(set(BLOCK_RE.findall(text))),
            "findings": findings, "kind": "STATIC_RULES",
            "disclaimer": "Phân tích tĩnh theo quy tắc; không chứng minh prompt cho kết quả đúng."}


# ---------------------------------------------------------------- nguồn tham chiếu

SOURCE_KINDS = ("URL", "NOTE", "DOCUMENT", "REPOSITORY", "ISSUE", "TEST_RESULT", "GOAL")


def list_sources(conn, actor: Actor, pid: str) -> dict:
    _load(conn, actor, pid)
    rows = conn.execute("SELECT s.*, ps.linked_at FROM prompt_sources ps JOIN sources s ON s.id=ps.source_id "
                        "WHERE ps.prompt_id=? ORDER BY ps.linked_at DESC", (pid,)).fetchall()
    return {"items": [dict(r) for r in rows]}


def add_source(conn, actor: Actor, pid: str, data: dict) -> dict:
    kind = data.get("kind")
    if kind not in SOURCE_KINDS:
        raise bad_request(f"kind phải thuộc {', '.join(SOURCE_KINDS)}")
    title = _text(data, "title", 200, required=True)
    url = _text(data, "url", 2000)
    if url and not re.match(r"^https?://", url, re.I):
        raise bad_request("URL phải bắt đầu bằng http:// hoặc https://")
    note = _text(data, "note", 20_000)
    captured = _text(data, "captured_at", 40) or now_iso()
    with tx(conn):
        _load(conn, actor, pid, "write")
        sid, ts = new_id("S"), now_iso()
        conn.execute("INSERT INTO sources(id,kind,title,url,note,captured_at,created_by,created_at) VALUES (?,?,?,?,?,?,?,?)",
                     (sid, kind, title, url, note, captured, actor.id, ts))
        conn.execute("INSERT INTO prompt_sources(prompt_id,source_id,linked_at,linked_by) VALUES (?,?,?,?)",
                     (pid, sid, ts, actor.id))
        audit(conn, actor, "prompt.source.add", "prompt", pid, detail={"source_id": sid, "kind": kind})
    return list_sources(conn, actor, pid)


def unlink_source(conn, actor: Actor, pid: str, sid: str) -> dict:
    with tx(conn):
        _load(conn, actor, pid, "write")
        n = conn.execute("DELETE FROM prompt_sources WHERE prompt_id=? AND source_id=?", (pid, sid)).rowcount
        if not n:
            raise ApiError(404, "NOT_FOUND", "Không có liên kết nguồn này")
        audit(conn, actor, "prompt.source.unlink", "prompt", pid, detail={"source_id": sid})
    return list_sources(conn, actor, pid)


# ---------------------------------------------------------------- xuất

def _slug(title: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", fold(title)).strip("-")
    return (s or "prompt")[:60]


def export(conn, actor: Actor, pid: str, fmt: str, ref: str | None) -> Response:
    row, _ = _load(conn, actor, pid)
    if fmt not in ("txt", "md", "json"):
        raise bad_request("format phải là txt, md hoặc json")
    base = _slug(row["title"])
    if fmt == "json":
        versions = conn.execute("SELECT * FROM prompt_versions WHERE prompt_id=? ORDER BY version_no", (pid,)).fetchall()
        d = detail(conn, actor, pid)
        d.pop("your_role", None)
        payload = {"format": "crown-prompt", "format_version": 1, "exported_at": now_iso(), "prompt": d,
                   "versions": [dict(_version_summary(v), content=v["content"]) for v in versions]}
        body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        ctype, name = "application/json; charset=utf-8", f"{base}.json"
        label = "all"
    else:
        text, label = _content_of(conn, row, ref or ("head" if row["head_version_id"] else "draft"))
        body = text.encode("utf-8")
        ctype = "text/markdown; charset=utf-8" if fmt == "md" else "text/plain; charset=utf-8"
        name = f"{base}.{fmt}"
    audit(conn, actor, "prompt.export", "prompt", pid, detail={"format": fmt, "source": label})
    disp = f"attachment; filename=\"{name}\"; filename*=UTF-8''{quote(name)}"
    return Response(200, body, {"Content-Type": ctype, "Content-Disposition": disp, "Cache-Control": "no-store"})


# ---------------------------------------------------------------- nhập

def _decode_upload(item: dict) -> dict:
    """Kiểm tên tệp, kích thước, mã hóa. Trả về {filename, ext, content} hoặc ném lỗi có thông báo rõ."""
    raw_name = item.get("filename")
    if not isinstance(raw_name, str) or not raw_name.strip():
        raise bad_request("Thiếu tên tệp", "IMPORT_BAD_NAME")
    name = re.split(r"[\\/]", raw_name.strip())[-1]
    name = "".join(ch for ch in name if unicodedata.category(ch)[0] != "C")
    if not name or name.startswith(".") or len(name) > 200:
        raise bad_request(f"Tên tệp không hợp lệ: {raw_name[:80]!r}", "IMPORT_BAD_NAME")
    dot = name.rfind(".")
    ext = name[dot:].lower() if dot > 0 else ""
    if ext not in IMPORT_EXTS:
        raise bad_request(f"{name}: chỉ nhận .txt, .md, .markdown", "IMPORT_BAD_TYPE")
    b64 = item.get("content_b64")
    if not isinstance(b64, str):
        raise bad_request(f"{name}: thiếu nội dung", "IMPORT_EMPTY")
    if len(b64) > (IMPORT_MAX_BYTES * 4) // 3 + 8:
        raise bad_request(f"{name}: tệp vượt {IMPORT_MAX_BYTES:,} byte", "IMPORT_TOO_LARGE")
    try:
        raw = base64.b64decode(b64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise bad_request(f"{name}: dữ liệu base64 hỏng", "IMPORT_BAD_DATA") from exc
    if len(raw) > IMPORT_MAX_BYTES:
        raise bad_request(f"{name}: tệp vượt {IMPORT_MAX_BYTES:,} byte", "IMPORT_TOO_LARGE")
    if raw.startswith(b"\xef\xbb\xbf"):
        enc, raw = "utf-8", raw[3:]
    elif raw.startswith(b"\xff\xfe") or raw.startswith(b"\xfe\xff"):
        enc = "utf-16"
    else:
        enc = "utf-8"
    try:
        text = raw.decode(enc)
    except UnicodeDecodeError as exc:
        raise bad_request(f"{name}: không phải UTF-8 hợp lệ (lỗi ở byte {exc.start}). Hãy lưu lại tệp dạng UTF-8.",
                          "IMPORT_BAD_ENCODING") from exc
    if enc == "utf-16":
        text = text.lstrip("﻿")
    bad = [ch for ch in text if (ord(ch) < 32 and ch not in "\t\n\r") or ord(ch) == 0x7f]
    if bad:
        raise bad_request(f"{name}: chứa ký tự điều khiển/nhị phân (có thể không phải tệp văn bản)", "IMPORT_BINARY")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if not text.strip():
        raise bad_request(f"{name}: tệp rỗng", "IMPORT_EMPTY")
    if len(text) > MAX_CONTENT_CHARS:
        raise bad_request(f"{name}: vượt {MAX_CONTENT_CHARS:,} ký tự", "IMPORT_TOO_LARGE")
    return {"filename": name, "ext": ext, "content": text, "encoding": enc}


def _title_from(name: str, ext: str, text: str) -> str:
    if IMPORT_EXTS[ext] == "md":
        for ln in text.split("\n")[:30]:
            m = re.match(r"^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$", ln)
            if m:
                return m.group(1)[:MAX_TITLE]
    return name[: name.rfind(".")][:MAX_TITLE] or "Prompt nhập"


def import_preview(conn, actor: Actor, data: dict) -> dict:
    project_id = _text(data, "project_id", 64, required=True)
    require_project(conn, actor, project_id, "write")
    files = data.get("files")
    if not isinstance(files, list) or not files:
        raise bad_request("Cần danh sách files")
    if len(files) > IMPORT_MAX_FILES:
        raise bad_request(f"Tối đa {IMPORT_MAX_FILES} tệp mỗi lần")
    items = []
    for i, f in enumerate(files):
        if not isinstance(f, dict):
            items.append({"index": i, "ok": False, "error": "Mục không hợp lệ"})
            continue
        try:
            d = _decode_upload(f)
        except ApiError as exc:
            items.append({"index": i, "ok": False, "filename": str(f.get("filename", ""))[:200],
                          "error": exc.message, "code": exc.code})
            continue
        checksum = sha256_text(d["content"])
        title = _title_from(d["filename"], d["ext"], d["content"])
        dup = conn.execute("SELECT p.id, p.title, v.version_no FROM prompt_versions v JOIN prompts p ON p.id=v.prompt_id "
                           "WHERE p.project_id=? AND v.checksum=? LIMIT 1", (project_id, checksum)).fetchone()
        if not dup:
            dup = conn.execute("SELECT p.id, p.title, NULL AS version_no FROM prompts p WHERE p.project_id=? AND "
                               "p.draft_content=? LIMIT 1", (project_id, d["content"])).fetchone()
        same_title = conn.execute("SELECT id, title, revision FROM prompts WHERE project_id=? AND title=? COLLATE NOCASE "
                                  "LIMIT 1", (project_id, title)).fetchone()
        items.append({"index": i, "ok": True, "filename": d["filename"], "encoding": d["encoding"], "title": title,
                      "chars": len(d["content"]), "lines": d["content"].count("\n") + 1, "checksum": checksum,
                      "preview": d["content"][:600], "variables": variables_of(d["content"]),
                      "duplicate_of": dict(dup) if dup else None,
                      "title_conflict": dict(same_title) if same_title else None,
                      "suggested_action": "skip" if dup else "create"})
    return {"project_id": project_id, "items": items,
            "note": "Chưa ghi gì. Chọn hành động cho từng tệp rồi gửi /import/commit."}


def import_commit(conn, actor: Actor, data: dict) -> dict:
    """Ghi tất cả hoặc không ghi gì. Mục trùng không bị ghi đè âm thầm: phải chọn create/new_version/skip."""
    project_id = _text(data, "project_id", 64, required=True)
    items = data.get("items")
    if not isinstance(items, list) or not items or len(items) > IMPORT_MAX_FILES:
        raise bad_request(f"Cần 1–{IMPORT_MAX_FILES} mục")
    results = []
    with tx(conn):
        require_project(conn, actor, project_id, "write")
        for i, it in enumerate(items):
            action = it.get("action")
            if action not in ("create", "new_version", "skip"):
                raise bad_request(f"Mục {i}: action phải là create, new_version hoặc skip")
            if action == "skip":
                results.append({"index": i, "action": "skip"})
                continue
            d = _decode_upload(it)
            title = _text(it, "title", MAX_TITLE) or _title_from(d["filename"], d["ext"], d["content"])
            if action == "create":
                pid = create(conn, actor, {"project_id": project_id, "title": title, "content": d["content"],
                                           "tags": it.get("tags") or [],
                                           "changelog": f"Nhập từ tệp {d['filename']}"}, _in_tx=True)
                results.append({"index": i, "action": "create", "prompt_id": pid})
            else:
                target = it.get("target_prompt_id")
                row = conn.execute("SELECT * FROM prompts WHERE id=?", (target,)).fetchone() if target else None
                if not row or row["project_id"] != project_id:
                    raise bad_request(f"Mục {i}: target_prompt_id không thuộc dự án đã chọn")
                if it.get("target_revision") != row["revision"]:
                    raise ApiError(409, "REVISION_CONFLICT", f"Mục {i}: prompt đích đã đổi, hãy xem trước lại",
                                   {"index": i, "current_revision": row["revision"]})
                if row["head_version_id"] and conn.execute(
                        "SELECT content FROM prompt_versions WHERE id=?", (row["head_version_id"],)).fetchone()[0] == d["content"]:
                    results.append({"index": i, "action": "skip", "reason": "Trùng phiên bản hiện tại"})
                    continue
                vid = _insert_version(conn, actor, target, d["content"], f"Nhập từ tệp {d['filename']}")
                ts = now_iso()
                conn.execute("UPDATE prompts SET head_version_id=?, draft_content=?, draft_updated_at=?, "
                             "draft_updated_by=?, revision=revision+1, updated_at=? WHERE id=?",
                             (vid, d["content"], ts, actor.id, ts, target))
                _index(conn, target)
                results.append({"index": i, "action": "new_version", "prompt_id": target, "version_id": vid})
        audit(conn, actor, "prompt.import", "project", project_id,
              detail={"created": sum(r["action"] == "create" for r in results),
                      "new_versions": sum(r["action"] == "new_version" for r in results),
                      "skipped": sum(r["action"] == "skip" for r in results)})
    return {"results": results}


__all__ = ["create", "detail", "search", "dumps"]
