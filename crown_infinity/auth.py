"""Người dùng, phiên, danh tính tác nhân và kiểm quyền phía máy chủ trên từng đối tượng."""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass

from .db import tx
from .httpcore import ApiError, bad_request
from .security import hash_password, new_token, token_hash, verify_password
from .util import iso_after, new_id, now_iso, parse_iso

ROLES = ("OWNER", "EDITOR", "VIEWER")


@dataclass(frozen=True)
class Actor:
    kind: str            # USER | AGENT | SYSTEM
    id: str
    role: str = ""       # vai trò toàn cục của USER
    name: str = ""

    @property
    def is_owner(self) -> bool:
        return self.kind == "USER" and self.role == "OWNER"


SYSTEM = Actor("SYSTEM", "system", "OWNER", "Hệ thống")


def audit(conn: sqlite3.Connection, actor: Actor | None, action: str, object_type: str | None = None,
          object_id: str | None = None, result: str = "OK", detail: dict | None = None) -> None:
    """Ghi nhật ký chỉ thêm. Không đưa secret/nội dung prompt vào detail."""
    conn.execute(
        "INSERT INTO activity_logs(ts,actor_type,actor_id,action,object_type,object_id,result,detail_json) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (now_iso(), actor.kind if actor else "ANONYMOUS", actor.id if actor else None, action, object_type,
         object_id, result, json.dumps(detail or {}, ensure_ascii=False)))


def require_owner(actor: Actor) -> None:
    if not actor.is_owner:
        raise ApiError(403, "FORBIDDEN", "Chỉ OWNER được thực hiện thao tác này")


# ---------- người dùng ----------

def _clean_username(username: str) -> str:
    username = (username or "").strip()
    if not (3 <= len(username) <= 64) or not all(ch.isalnum() or ch in "._-" for ch in username):
        raise bad_request("Tên đăng nhập 3–64 ký tự, chỉ gồm chữ, số, dấu . _ -")
    return username


def create_user(conn, username: str, password: str, role: str, display_name: str = "",
                actor: Actor = SYSTEM) -> dict:
    username = _clean_username(username)
    if role not in ROLES:
        raise bad_request("Vai trò không hợp lệ")
    try:
        pw = hash_password(password)
    except ValueError as exc:
        raise bad_request(str(exc), "WEAK_PASSWORD") from exc
    uid, ts = new_id("U"), now_iso()
    with tx(conn):
        if role == "OWNER" and actor is not SYSTEM:
            require_owner(actor)
        try:
            conn.execute("INSERT INTO users(id,username,display_name,password_hash,role,is_active,created_at,updated_at)"
                         " VALUES (?,?,?,?,?,1,?,?)", (uid, username, display_name or username, pw, role, ts, ts))
        except sqlite3.IntegrityError as exc:
            raise ApiError(409, "USERNAME_TAKEN", "Tên đăng nhập đã tồn tại") from exc
        audit(conn, actor, "user.create", "user", uid, detail={"role": role})
    return get_user(conn, uid)


def get_user(conn, uid: str) -> dict:
    row = conn.execute("SELECT id,username,display_name,role,is_active,created_at,updated_at FROM users WHERE id=?",
                       (uid,)).fetchone()
    if not row:
        raise ApiError(404, "NOT_FOUND", "Không tìm thấy người dùng")
    return dict(row)


def list_users(conn) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT id,username,display_name,role,is_active,created_at,updated_at FROM users ORDER BY created_at")]


def owner_exists(conn) -> bool:
    return conn.execute("SELECT 1 FROM users WHERE role='OWNER' AND is_active=1").fetchone() is not None


def update_user(conn, actor: Actor, uid: str, data: dict) -> dict:
    require_owner(actor)
    with tx(conn):
        user = get_user(conn, uid)
        fields, vals = [], []
        if "is_active" in data:
            active = 1 if data["is_active"] else 0
            if uid == actor.id and not active:
                raise bad_request("Không thể tự khóa tài khoản OWNER đang đăng nhập")
            fields.append("is_active=?"), vals.append(active)
        if "role" in data:
            if data["role"] not in ROLES:
                raise bad_request("Vai trò không hợp lệ")
            if uid == actor.id and data["role"] != "OWNER":
                raise bad_request("Không thể tự hạ quyền OWNER đang đăng nhập")
            fields.append("role=?"), vals.append(data["role"])
        if "password" in data:
            try:
                fields.append("password_hash=?"), vals.append(hash_password(str(data["password"])))
            except ValueError as exc:
                raise bad_request(str(exc), "WEAK_PASSWORD") from exc
        if "display_name" in data:
            fields.append("display_name=?"), vals.append(str(data["display_name"])[:120] or user["username"])
        if not fields:
            raise bad_request("Không có trường nào để cập nhật")
        conn.execute(f"UPDATE users SET {', '.join(fields)}, updated_at=? WHERE id=?", (*vals, now_iso(), uid))
        if "password" in data or data.get("is_active") is False or "role" in data:
            conn.execute("UPDATE sessions SET revoked_at=? WHERE user_id=? AND revoked_at IS NULL", (now_iso(), uid))
        audit(conn, actor, "user.update", "user", uid, detail={"fields": sorted(k for k in data if k != "password")
                                                               + (["password"] if "password" in data else [])})
    return get_user(conn, uid)


# ---------- phiên ----------

def login(conn, username: str, password: str, user_agent: str, cfg) -> tuple[str, dict, dict]:
    from .security import dummy_verify
    row = conn.execute("SELECT * FROM users WHERE username=? COLLATE NOCASE", (username.strip(),)).fetchone()
    if not row or not row["is_active"]:
        dummy_verify(password)
        raise ApiError(401, "INVALID_CREDENTIALS", "Sai tên đăng nhập hoặc mật khẩu")
    if not verify_password(password, row["password_hash"]):
        raise ApiError(401, "INVALID_CREDENTIALS", "Sai tên đăng nhập hoặc mật khẩu")
    token, csrf, ts = new_token(), new_token(), now_iso()
    with tx(conn):
        conn.execute("INSERT INTO sessions(token_hash,user_id,csrf_token,created_at,last_seen_at,expires_at,user_agent)"
                     " VALUES (?,?,?,?,?,?,?)", (token_hash(token), row["id"], csrf, ts, ts,
                                                 iso_after(cfg.session_absolute_seconds), user_agent[:200]))
        audit(conn, Actor("USER", row["id"], row["role"]), "auth.login", "user", row["id"])
    return token, {"id": row["id"], "username": row["username"], "display_name": row["display_name"],
                   "role": row["role"]}, {"csrf_token": csrf}


def resolve_session(conn, token: str, cfg) -> tuple[Actor, dict] | None:
    if not token:
        return None
    row = conn.execute(
        "SELECT s.*, u.username, u.display_name, u.role, u.is_active FROM sessions s JOIN users u ON u.id=s.user_id "
        "WHERE s.token_hash=?", (token_hash(token),)).fetchone()
    if not row or row["revoked_at"] or not row["is_active"]:
        return None
    now = now_iso()
    if row["expires_at"] <= now:
        return None
    idle = (parse_iso(now) - parse_iso(row["last_seen_at"])).total_seconds()
    if idle > cfg.session_idle_seconds:
        return None
    if idle > 60:  # giảm số lần ghi
        conn.execute("UPDATE sessions SET last_seen_at=? WHERE token_hash=?", (now, row["token_hash"]))
    actor = Actor("USER", row["user_id"], row["role"], row["display_name"])
    return actor, {"token_hash": row["token_hash"], "csrf_token": row["csrf_token"], "username": row["username"],
                   "display_name": row["display_name"], "role": row["role"], "user_id": row["user_id"]}


def logout(conn, session_hash: str, actor: Actor) -> None:
    with tx(conn):
        conn.execute("UPDATE sessions SET revoked_at=? WHERE token_hash=? AND revoked_at IS NULL",
                     (now_iso(), session_hash))
        audit(conn, actor, "auth.logout", "user", actor.id)


def revoke_user_sessions(conn, actor: Actor, uid: str) -> int:
    if uid != actor.id:
        require_owner(actor)
    with tx(conn):
        n = conn.execute("UPDATE sessions SET revoked_at=? WHERE user_id=? AND revoked_at IS NULL",
                         (now_iso(), uid)).rowcount
        audit(conn, actor, "auth.revoke_sessions", "user", uid, detail={"count": n})
    return n


def change_own_password(conn, actor: Actor, current: str, new: str, keep_session_hash: str) -> None:
    row = conn.execute("SELECT password_hash FROM users WHERE id=?", (actor.id,)).fetchone()
    if not row or not verify_password(current, row["password_hash"]):
        raise ApiError(403, "INVALID_CREDENTIALS", "Mật khẩu hiện tại không đúng")
    try:
        new_hash = hash_password(new)
    except ValueError as exc:
        raise bad_request(str(exc), "WEAK_PASSWORD") from exc
    with tx(conn):
        conn.execute("UPDATE users SET password_hash=?, updated_at=? WHERE id=?", (new_hash, now_iso(), actor.id))
        conn.execute("UPDATE sessions SET revoked_at=? WHERE user_id=? AND token_hash<>? AND revoked_at IS NULL",
                     (now_iso(), actor.id, keep_session_hash))
        audit(conn, actor, "auth.change_password", "user", actor.id)


# ---------- quyền theo dự án ----------

def project_role(conn, actor: Actor, project_id: str) -> str | None:
    """OWNER toàn cục thấy mọi dự án; người khác cần là thành viên dự án."""
    if actor.is_owner or actor.kind == "SYSTEM":
        return "OWNER"
    if actor.kind != "USER":
        return None
    row = conn.execute("SELECT role FROM project_members WHERE project_id=? AND user_id=?",
                       (project_id, actor.id)).fetchone()
    if not row:
        return None
    # Vai trò toàn cục VIEWER không được nâng lên EDITOR nhờ thành viên dự án
    if actor.role == "VIEWER":
        return "VIEWER"
    return row["role"]


def require_project(conn, actor: Actor, project_id: str, need: str = "read") -> str:
    role = project_role(conn, actor, project_id)
    if role is None:
        # Trả 404 để không lộ sự tồn tại của dự án không được cấp quyền
        raise ApiError(404, "NOT_FOUND", "Không tìm thấy hoặc không có quyền truy cập")
    if need == "write" and role not in ("OWNER", "EDITOR"):
        raise ApiError(403, "FORBIDDEN", "Bạn chỉ có quyền xem trong dự án này")
    return role


def readable_project_ids(conn, actor: Actor) -> list[str] | None:
    """None = mọi dự án (OWNER)."""
    if actor.is_owner or actor.kind == "SYSTEM":
        return None
    return [r[0] for r in conn.execute("SELECT project_id FROM project_members WHERE user_id=?", (actor.id,))]
