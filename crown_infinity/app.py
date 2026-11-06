"""Ứng dụng CROWN INFINITY: định tuyến API v1, xác thực, CSRF, kiểm Host, phục vụ giao diện web."""
from __future__ import annotations

import logging
from pathlib import Path
from urllib.parse import urlsplit

from . import APP_NAME, APP_VERSION
from . import agents as AG
from . import ai_hub as AI
from . import auth as AU
from . import platform as PF
from . import prompts as PS
from .backup import BackupError, create_backup, list_backups
from .config import Config
from .db import connect, migrate, schema_version
from .httpcore import ApiError, Request, Response, Router, bad_request, serve_static
from .security import LoginLimiter, safe_equal

WEB_ROOT = Path(__file__).resolve().parent / "web"
SESSION_COOKIE = "ci_session"
UNSAFE = {"POST", "PUT", "PATCH", "DELETE"}


class App:
    def __init__(self, config: Config, *, force_like_search: bool = False):
        self.config = config
        config.ensure_dirs()
        self.limiter = LoginLimiter()
        self.log = logging.getLogger("crown_infinity")
        self.migration = migrate(config.db_path, backup_dir=config.backup_dir)
        conn = connect(config.db_path)
        try:
            self.search_mode = PS.ensure_search_index(conn, force_fallback=force_like_search)
            PF.sync_modules(conn)
        finally:
            conn.close()
        self.adapter = AG.CrownAgentAdapter(config.repo_root)
        self.router = Router()
        self._routes()

    # ------------------------------------------------------------ hạ tầng
    def log_access(self, client: str, line: str) -> None:
        self.log.info("%s %s", client, line)

    def log_error(self, text: str) -> None:
        self.log.error(text)

    def health(self) -> dict:
        return AG.module_health(self.adapter, self.config.repo_root)

    def _host_ok(self, req: Request) -> bool:
        host = req.header("host")
        if not host:
            return False
        name = host.rsplit(":", 1)[0] if not host.startswith("[") else host.split("]")[0] + "]"
        return name.lower() in self.config.host_allowlist()

    def _origin_ok(self, req: Request) -> bool:
        origin = req.header("origin")
        if not origin:
            return True  # trình duyệt cũ/công cụ dòng lệnh; vẫn phải qua CSRF token
        try:
            o = urlsplit(origin)
        except ValueError:
            return False
        return f"{o.hostname or ''}".lower() in self.config.host_allowlist() and o.netloc == req.header("host")

    def handle(self, req: Request) -> Response:
        if not self._host_ok(req):
            return Response.json({"error": {"code": "BAD_HOST", "message": "Host không được phép"}}, 421)
        if not req.path.startswith("/api/"):
            if req.method not in ("GET", "HEAD"):
                return Response.json({"error": {"code": "METHOD_NOT_ALLOWED", "message": "Không hỗ trợ"}}, 405)
            try:
                return serve_static(WEB_ROOT, req.path)
            except ApiError as exc:
                return Response.json({"error": {"code": exc.code, "message": exc.message}}, exc.status)
        conn = connect(self.config.db_path)
        try:
            try:
                return self._dispatch(conn, req)
            except ApiError as exc:
                # Chỉ ghi lượt bị từ chối của tác nhân ĐÃ xác thực; 401 ẩn danh không ghi để trang lạ không
                # làm phình nhật ký chỉ thêm (kiểm định 2026-11-06, lỗi #7).
                if exc.status == 403 and req.ctx.get("actor") is not None:
                    try:
                        AU.audit(conn, req.ctx.get("actor"), f"http.{req.method.lower()}", "route", req.path[:120],
                                 result="DENIED", detail={"code": exc.code})
                    except Exception:  # nhật ký không được làm hỏng phản hồi lỗi
                        pass
                body = {"error": {"code": exc.code, "message": exc.message}}
                if exc.details is not None:
                    body["error"]["details"] = exc.details
                return Response.json(body, exc.status, {"Cache-Control": "no-store"})
        finally:
            conn.close()

    def _dispatch(self, conn, req: Request) -> Response:
        route, params = self.router.match(req.method, req.path)
        if not route:
            raise ApiError(404, "NOT_FOUND", "Không có API này")
        req.params = params
        if route.auth == "user":
            token = req.cookies().get(SESSION_COOKIE, "")
            resolved = AU.resolve_session(conn, token, self.config)
            if not resolved:
                raise ApiError(401, "UNAUTHENTICATED", "Cần đăng nhập")
            req.ctx["actor"], req.ctx["session"] = resolved
            if req.method in UNSAFE:
                sent = req.header("x-csrf-token")
                if not sent or not safe_equal(sent, resolved[1]["csrf_token"]) or not self._origin_ok(req):
                    raise ApiError(403, "CSRF_FAILED", "Thiếu hoặc sai mã chống giả mạo (CSRF)")
        elif route.auth == "agent":
            auth = req.header("authorization")
            bearer = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
            agent = AG.resolve_agent(conn, bearer)
            if not agent:
                raise ApiError(401, "UNAUTHENTICATED", "Token Agent không hợp lệ hoặc đã thu hồi")
            req.ctx["actor"] = agent
        out = route.handler(conn, req)
        if isinstance(out, Response):
            out.headers.setdefault("Cache-Control", "no-store")
            return out
        return Response.json(out, headers={"Cache-Control": "no-store"})

    # ------------------------------------------------------------ routes
    def _routes(self):
        r = self.router.add
        A = lambda req: req.ctx["actor"]  # noqa: E731

        def module_guard(module_id):
            def deco(fn):
                def wrapped(conn, req):
                    if not PF.module_enabled(conn, module_id):
                        raise ApiError(409, "MODULE_DISABLED", f"Module {module_id} đang tắt trong Module Registry")
                    return fn(conn, req)
                return wrapped
            return deco

        # --- công khai
        r("GET", "/api/v1/health", lambda c, q: {"ok": True, "app": APP_NAME, "version": APP_VERSION,
                                                 "schema_version": schema_version(c)}, auth="public")
        r("POST", "/api/v1/auth/login", self._login, auth="public")

        # --- phiên
        r("GET", "/api/v1/auth/me", lambda c, q: {"user": {k: q.ctx["session"][k] for k in
                                                           ("user_id", "username", "display_name", "role")},
                                                  "csrf_token": q.ctx["session"]["csrf_token"],
                                                  "app": {"name": APP_NAME, "version": APP_VERSION,
                                                          "search_mode": self.search_mode}})
        r("POST", "/api/v1/auth/logout", self._logout)
        r("POST", "/api/v1/auth/password", lambda c, q: (AU.change_own_password(
            c, A(q), str(q.json().get("current_password", "")), str(q.json().get("new_password", "")),
            q.ctx["session"]["token_hash"]), {"ok": True})[1])

        # --- dashboard, dự án, module, tool, task, nhật ký
        r("GET", "/api/v1/dashboard", lambda c, q: PF.dashboard(c, A(q), self.health()))
        r("GET", "/api/v1/projects", lambda c, q: PF.list_projects(c, A(q)))
        r("POST", "/api/v1/projects", lambda c, q: Response.json(PF.create_project(c, A(q), q.json()), 201))
        r("PATCH", "/api/v1/projects/{pid}", lambda c, q: PF.update_project(c, A(q), q.params["pid"], q.json()))
        r("GET", "/api/v1/projects/{pid}/members", lambda c, q: PF.list_members(c, A(q), q.params["pid"]))
        r("PUT", "/api/v1/projects/{pid}/members/{uid}",
          lambda c, q: PF.set_member(c, A(q), q.params["pid"], q.params["uid"], q.json().get("role")))
        r("DELETE", "/api/v1/projects/{pid}/members/{uid}",
          lambda c, q: PF.set_member(c, A(q), q.params["pid"], q.params["uid"], None))
        r("GET", "/api/v1/modules", lambda c, q: PF.list_modules(c, A(q), self.health()))
        r("PATCH", "/api/v1/modules/{mid}", lambda c, q: (PF.set_module_enabled(
            c, A(q), q.params["mid"], bool(q.json().get("enabled"))), PF.list_modules(c, A(q), self.health()))[1])
        r("GET", "/api/v1/tools", module_guard("crown_tool_desk")(lambda c, q: PF.list_tools(c)))
        r("POST", "/api/v1/tools/sync", module_guard("crown_tool_desk")(
            lambda c, q: (AU.require_owner(A(q)), PF.sync_tools_from_catalog(c, A(q), self.config.repo_root))[1]))
        r("GET", "/api/v1/tasks", lambda c, q: PF.list_tasks(c, A(q), {k: q.q(k) for k in ("status", "project_id")}))
        r("POST", "/api/v1/tasks", lambda c, q: Response.json(PF.create_task(c, A(q), q.json()), 201))
        r("PATCH", "/api/v1/tasks/{tid}", lambda c, q: PF.update_task(c, A(q), q.params["tid"], q.json(), q.if_match()))
        r("GET", "/api/v1/activity", lambda c, q: PF.list_activity(c, A(q), {k: q.q(k) for k in (
            "limit", "before_id", "action", "object_type", "object_id", "result")}))

        # --- Prompt Studio
        ps = module_guard("crown_prompt_studio")
        r("GET", "/api/v1/prompts", ps(lambda c, q: PS.search(c, A(q), {k: q.q(k) for k in (
            "q", "project_id", "category", "target_ai", "tag", "favorite", "archived", "page", "limit", "sort")})))
        r("POST", "/api/v1/prompts", ps(lambda c, q: PS._resp(c, A(q), PS.create(c, A(q), q.json()), 201)))
        r("GET", "/api/v1/prompts/facets", ps(lambda c, q: PS.facets(c, A(q))))
        r("GET", "/api/v1/prompts/{pid}", ps(lambda c, q: PS._resp(c, A(q), q.params["pid"])))
        r("PATCH", "/api/v1/prompts/{pid}", ps(lambda c, q: PS.update_meta(c, A(q), q.params["pid"], q.json(),
                                                                           q.if_match())))
        r("PUT", "/api/v1/prompts/{pid}/draft", ps(lambda c, q: PS.save_draft(
            c, A(q), q.params["pid"], q.json().get("content"), q.if_match())))
        r("GET", "/api/v1/prompts/{pid}/versions", ps(lambda c, q: PS.list_versions(c, A(q), q.params["pid"])))
        r("POST", "/api/v1/prompts/{pid}/versions", ps(lambda c, q: PS.commit_version(
            c, A(q), q.params["pid"], q.json(), q.if_match())))
        r("GET", "/api/v1/prompts/{pid}/versions/{vid}", ps(lambda c, q: PS.get_version(
            c, A(q), q.params["pid"], q.params["vid"])))
        r("POST", "/api/v1/prompts/{pid}/versions/{vid}/restore", ps(lambda c, q: PS.restore(
            c, A(q), q.params["pid"], q.params["vid"], q.json(), q.if_match())))
        r("POST", "/api/v1/prompts/{pid}/versions/{vid}/review", ps(lambda c, q: PS.review(
            c, A(q), q.params["pid"], q.params["vid"], q.json())))
        r("PUT", "/api/v1/prompts/{pid}/production", ps(lambda c, q: PS.set_production(
            c, A(q), q.params["pid"], q.json())))
        r("GET", "/api/v1/prompts/{pid}/diff", ps(lambda c, q: PS.diff(c, A(q), q.params["pid"], q.q("from"), q.q("to"))))
        r("POST", "/api/v1/prompts/{pid}/duplicate", ps(lambda c, q: PS.duplicate(c, A(q), q.params["pid"], q.json())))
        r("POST", "/api/v1/prompts/{pid}/archive", ps(lambda c, q: PS.set_archived(c, A(q), q.params["pid"], True,
                                                                                   q.if_match())))
        r("POST", "/api/v1/prompts/{pid}/unarchive", ps(lambda c, q: PS.set_archived(c, A(q), q.params["pid"], False,
                                                                                     q.if_match())))
        r("POST", "/api/v1/prompts/{pid}/render", ps(lambda c, q: PS.render(c, A(q), q.params["pid"], q.json())))
        r("GET", "/api/v1/prompts/{pid}/analysis", ps(lambda c, q: PS.analysis(c, A(q), q.params["pid"],
                                                                               q.q("version", "draft"))))
        r("GET", "/api/v1/prompts/{pid}/export", ps(lambda c, q: PS.export(c, A(q), q.params["pid"],
                                                                           q.q("format", "txt"), q.q("version"))))
        r("GET", "/api/v1/prompts/{pid}/sources", ps(lambda c, q: PS.list_sources(c, A(q), q.params["pid"])))
        r("POST", "/api/v1/prompts/{pid}/sources", ps(lambda c, q: PS.add_source(c, A(q), q.params["pid"], q.json())))
        r("DELETE", "/api/v1/prompts/{pid}/sources/{sid}", ps(lambda c, q: PS.unlink_source(
            c, A(q), q.params["pid"], q.params["sid"])))
        r("POST", "/api/v1/import/preview", ps(lambda c, q: PS.import_preview(c, A(q), q.json())))
        r("POST", "/api/v1/import/commit", ps(lambda c, q: PS.import_commit(c, A(q), q.json())))

        # --- AI Hub
        r("GET", "/api/v1/ai", lambda c, q: AI.get_hub(c, A(q)))
        r("PUT", "/api/v1/ai/settings", lambda c, q: AI.put_settings(c, A(q), q.json()))
        r("POST", "/api/v1/ai/providers", lambda c, q: Response.json(AI.upsert_provider(c, A(q), q.json()), 201))
        r("PATCH", "/api/v1/ai/providers/{aid}", lambda c, q: AI.upsert_provider(c, A(q), q.json(), q.params["aid"]))
        r("POST", "/api/v1/ai/check", lambda c, q: (AU.require_owner(A(q)), AI.gate(
            c, q.json().get("provider_id"), q.json().get("estimated_usd"), q.json().get("max_output_tokens")))[1])

        # --- CROWN AGENT
        ag = module_guard("crown_agent_control")
        r("GET", "/api/v1/integrations/crown-agent/health", ag(lambda c, q: self.adapter.health()))
        r("GET", "/api/v1/integrations/crown-agent/tasks", ag(lambda c, q: (AU.require_owner(A(q)),
                                                                           self.adapter.ledger_tasks())[1]))
        r("GET", "/api/v1/agents", ag(lambda c, q: AG.list_agents(c, A(q))))
        r("POST", "/api/v1/agents/sync", ag(lambda c, q: AG.sync_agents(c, A(q), self.adapter)))
        r("POST", "/api/v1/agents/{agent_id}/token", ag(lambda c, q: AG.issue_token(c, A(q), q.params["agent_id"])))
        r("DELETE", "/api/v1/agents/{agent_id}/token", ag(lambda c, q: AG.revoke_token(c, A(q), q.params["agent_id"])))
        r("GET", "/api/v1/assignments", ag(lambda c, q: AG.list_assignments(c, A(q), {k: q.q(k) for k in (
            "prompt_id", "agent_id", "active")})))
        r("POST", "/api/v1/assignments", ag(lambda c, q: Response.json(AG.create_assignment(c, A(q), q.json()), 201)))
        r("POST", "/api/v1/assignments/{aid}/revoke", ag(lambda c, q: AG.revoke_assignment(c, A(q), q.params["aid"])))
        # Agent gọi bằng Bearer token
        r("GET", "/api/v1/agent/me", ag(lambda c, q: {"agent_id": A(q).id, "display_name": A(q).name}), auth="agent")
        r("GET", "/api/v1/agent/prompts", ag(lambda c, q: AG.agent_list_prompts(c, A(q))), auth="agent")
        r("GET", "/api/v1/agent/prompts/{pid}", ag(lambda c, q: AG.agent_get_prompt(c, A(q), q.params["pid"])),
          auth="agent")

        # --- Admin
        r("GET", "/api/v1/admin/users", lambda c, q: (AU.require_owner(A(q)), {"items": AU.list_users(c)})[1])
        r("POST", "/api/v1/admin/users", self._create_user)
        r("PATCH", "/api/v1/admin/users/{uid}", lambda c, q: AU.update_user(c, A(q), q.params["uid"], q.json()))
        r("POST", "/api/v1/admin/users/{uid}/revoke-sessions",
          lambda c, q: {"revoked": AU.revoke_user_sessions(c, A(q), q.params["uid"])})
        r("GET", "/api/v1/admin/backups", lambda c, q: (AU.require_owner(A(q)),
                                                        {"items": list_backups(self.config.backup_dir),
                                                         "directory": str(self.config.backup_dir)})[1])
        r("POST", "/api/v1/admin/backups", self._create_backup)

    # ------------------------------------------------------------ handler có trạng thái
    def _login(self, conn, req: Request) -> Response:
        if not self._origin_ok(req):  # chống login-CSRF từ trang lạ
            raise ApiError(403, "CSRF_FAILED", "Nguồn yêu cầu không hợp lệ")
        data = req.json()
        username, password = str(data.get("username", "")), str(data.get("password", ""))
        wait = self.limiter.blocked(username, req.client_ip)
        if wait:
            raise ApiError(429, "TOO_MANY_ATTEMPTS", f"Đăng nhập sai quá nhiều lần. Thử lại sau {wait} giây.")
        try:
            token, user, extra = AU.login(conn, username, password, req.header("user-agent"), self.config)
        except ApiError:
            self.limiter.fail(username, req.client_ip)
            AU.audit(conn, None, "auth.login", "user", None, result="DENIED", detail={"username": username[:64]})
            raise
        self.limiter.success(username, req.client_ip)
        cookie = (f"{SESSION_COOKIE}={token}; Path=/; HttpOnly; SameSite=Strict; "
                  f"Max-Age={self.config.session_absolute_seconds}" + ("; Secure" if self.config.secure_cookies else ""))
        return Response.json({"user": user, **extra}, headers={"Set-Cookie": cookie})

    def _logout(self, conn, req: Request) -> Response:
        AU.logout(conn, req.ctx["session"]["token_hash"], req.ctx["actor"])
        cookie = f"{SESSION_COOKIE}=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0"
        return Response.json({"ok": True}, headers={"Set-Cookie": cookie})

    def _create_user(self, conn, req: Request) -> Response:
        actor = req.ctx["actor"]
        AU.require_owner(actor)
        d = req.json()
        u = AU.create_user(conn, str(d.get("username", "")), str(d.get("password", "")), str(d.get("role", "")),
                           str(d.get("display_name", "")), actor=actor)
        return Response.json(u, 201)

    def _create_backup(self, conn, req: Request) -> Response:
        actor = req.ctx["actor"]
        AU.require_owner(actor)
        try:
            path = create_backup(self.config.db_path, self.config.backup_dir, reason="manual")
        except BackupError as exc:
            raise bad_request(str(exc), "BACKUP_FAILED") from exc
        AU.audit(conn, actor, "backup.create", "backup", path.name)
        return Response.json({"name": path.name, "bytes": path.stat().st_size, "directory": str(path.parent)}, 201)
