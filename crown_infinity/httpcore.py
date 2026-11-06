"""Lớp HTTP tối giản trên http.server: định tuyến, JSON, lỗi có cấu trúc, tệp tĩnh an toàn.

Giới hạn đã biết: http.server không được thiết kế để phơi trực tiếp ra Internet. Mặc định chỉ nghe 127.0.0.1;
truy cập từ xa phải qua reverse proxy có HTTPS (xem docs/infinity/ARCHITECTURE.md).
"""
from __future__ import annotations

import json
import mimetypes
import re
import traceback
from dataclasses import dataclass, field
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, details=None):
        super().__init__(message)
        self.status, self.code, self.message, self.details = status, code, message, details


def bad_request(message: str, code: str = "VALIDATION_ERROR", details=None):
    return ApiError(400, code, message, details)


@dataclass
class Response:
    status: int = 200
    body: bytes = b""
    headers: dict = field(default_factory=dict)

    @classmethod
    def json(cls, data, status: int = 200, headers: dict | None = None):
        h = {"Content-Type": "application/json; charset=utf-8"}
        h.update(headers or {})
        return cls(status, json.dumps(data, ensure_ascii=False).encode("utf-8"), h)


@dataclass
class Request:
    method: str
    path: str
    query: dict
    headers: dict
    body: bytes
    client_ip: str
    params: dict = field(default_factory=dict)
    ctx: dict = field(default_factory=dict)

    def header(self, name: str, default: str = "") -> str:
        return self.headers.get(name.lower(), default)

    def cookies(self) -> dict:
        raw = self.header("cookie")
        if not raw:
            return {}
        c = SimpleCookie()
        try:
            c.load(raw)
        except CookieError:
            return {}
        return {k: v.value for k, v in c.items()}

    def json(self) -> dict:
        if not self.body:
            return {}
        ctype = self.header("content-type")
        if "application/json" not in ctype:
            raise ApiError(415, "UNSUPPORTED_MEDIA_TYPE", "Chỉ nhận application/json")
        try:
            data = json.loads(self.body.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise bad_request("JSON không hợp lệ", "INVALID_JSON") from exc
        if not isinstance(data, dict):
            raise bad_request("Thân yêu cầu phải là một đối tượng JSON", "INVALID_JSON")
        return data

    def q(self, name: str, default=None):
        vals = self.query.get(name)
        return vals[0] if vals else default

    def if_match(self) -> int | None:
        raw = self.header("if-match").strip().strip('"').removeprefix("W/").strip('"')
        if not raw:
            return None
        try:
            return int(raw)
        except ValueError as exc:
            raise bad_request("If-Match phải là số revision", "INVALID_REVISION") from exc


@dataclass
class Route:
    method: str
    pattern: re.Pattern
    template: str
    handler: object
    auth: str  # 'public' | 'user' | 'agent'


class Router:
    def __init__(self):
        self.routes: list[Route] = []

    def add(self, method: str, template: str, handler, auth: str = "user"):
        regex = "^" + re.sub(r"\{(\w+)\}", r"(?P<\1>[A-Za-z0-9_.\-]+)", template) + "$"
        self.routes.append(Route(method, re.compile(regex), template, handler, auth))

    def match(self, method: str, path: str):
        allowed = False
        for r in self.routes:
            m = r.pattern.match(path)
            if m:
                if r.method == method:
                    return r, m.groupdict()
                allowed = True
        if allowed:
            raise ApiError(405, "METHOD_NOT_ALLOWED", "Phương thức không được hỗ trợ cho đường dẫn này")
        return None, None


SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Content-Security-Policy": ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
                                "connect-src 'self'; font-src 'self'; object-src 'none'; base-uri 'none'; "
                                "form-action 'self'; frame-ancestors 'none'"),
}

STATIC_TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
                ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml", ".png": "image/png",
                ".ico": "image/x-icon", ".json": "application/json; charset=utf-8"}


def serve_static(root: Path, path: str) -> Response:
    rel = unquote(path).lstrip("/") or "index.html"
    if rel.endswith("/"):
        rel += "index.html"
    try:
        target = (root / rel).resolve()
        target.relative_to(root.resolve())
    except (ValueError, OSError):
        raise ApiError(404, "NOT_FOUND", "Không tìm thấy")
    if not target.is_file():
        # SPA dùng hash-router nên mọi đường dẫn lạ trả 404 thay vì index.html
        raise ApiError(404, "NOT_FOUND", "Không tìm thấy")
    ctype = STATIC_TYPES.get(target.suffix.lower())
    if not ctype:
        ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
    return Response(200, target.read_bytes(), {"Content-Type": ctype, "Cache-Control": "no-cache"})


def make_handler(app):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "CrownInfinity"
        sys_version = ""
        timeout = 60

        def log_message(self, fmt, *args):  # không in đường dẫn/nội dung ra console
            app.log_access(self.address_string(), fmt % args)

        def _handle(self):
            parts = urlsplit(self.path)
            length = int(self.headers.get("Content-Length") or 0)
            if length > app.config.max_body_bytes:
                self._send(Response.json({"error": {"code": "PAYLOAD_TOO_LARGE",
                                                    "message": "Yêu cầu quá lớn"}}, 413), close=True)
                return
            body = self.rfile.read(length) if length else b""
            headers = {k.lower(): v for k, v in self.headers.items()}
            req = Request(self.command, parts.path, parse_qs(parts.query, keep_blank_values=True), headers, body,
                          self.client_address[0])
            try:
                resp = app.handle(req)
            except Exception:  # lỗi không lường trước: không lộ chi tiết ra ngoài
                app.log_error(traceback.format_exc())
                resp = Response.json({"error": {"code": "INTERNAL_ERROR", "message": "Lỗi máy chủ"}}, 500)
            self._send(resp)

        def _send(self, resp: Response, close: bool = False):
            self.send_response(resp.status)
            headers = dict(SECURITY_HEADERS)
            headers.update(resp.headers)
            headers["Content-Length"] = str(len(resp.body))
            if close:
                headers["Connection"] = "close"
            for k, v in headers.items():
                if isinstance(v, list):
                    for item in v:
                        self.send_header(k, item)
                else:
                    self.send_header(k, v)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(resp.body)

        do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = do_HEAD = _handle

    return Handler


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


__all__ = ["ApiError", "bad_request", "Response", "Request", "Router", "serve_static", "make_handler", "Server"]
