"""Khung kiểm thử: chạy máy chủ thật trong luồng nền trên cổng ngẫu nhiên, client HTTP có cookie."""
from __future__ import annotations

import base64
import http.client
import json
import shutil
import tempfile
import threading
import unittest
from http.cookies import SimpleCookie
from pathlib import Path
from urllib.parse import quote

from crown_infinity import auth as AU
from crown_infinity.app import App
from crown_infinity.config import REPO_ROOT, Config
from crown_infinity.db import connect
from crown_infinity.httpcore import Server, make_handler
from crown_infinity.platform import create_project

OWNER_PW = "mat-khau-chu-so-huu-01"
EDITOR_PW = "mat-khau-bien-tap-0002"
VIEWER_PW = "mat-khau-nguoi-xem-003"


class Client:
    def __init__(self, port: int, host_header: str | None = None):
        self.port, self.cookies, self.csrf = port, {}, None
        self.host_header = host_header or f"127.0.0.1:{port}"

    def request(self, method, path, body=None, headers=None, raw=False, csrf=True):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        h = {"Host": self.host_header}
        if self.cookies:
            h["Cookie"] = "; ".join(f"{k}={v}" for k, v in self.cookies.items())
        data = None
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            h["Content-Type"] = "application/json"
        if csrf and self.csrf and method in ("POST", "PUT", "PATCH", "DELETE"):
            h["X-CSRF-Token"] = self.csrf
        h.update(headers or {})
        conn.request(method, quote(path, safe="/?&=%:+-_.~"), body=data, headers=h)
        resp = conn.getresponse()
        payload = resp.read()
        for v in resp.headers.get_all("Set-Cookie") or []:
            c = SimpleCookie()
            c.load(v)
            for k, m in c.items():
                if m.value:
                    self.cookies[k] = m.value
                else:
                    self.cookies.pop(k, None)
        conn.close()
        if raw:
            return resp.status, payload, resp.headers
        try:
            return resp.status, json.loads(payload.decode("utf-8")) if payload else None, resp.headers
        except ValueError:
            return resp.status, payload, resp.headers

    def login(self, username, password):
        st, body, _ = self.request("POST", "/api/v1/auth/login", {"username": username, "password": password})
        if st == 200:
            self.csrf = body["csrf_token"]
        return st, body

    # tiện ích
    def get(self, path, **kw):
        return self.request("GET", path, **kw)

    def post(self, path, body=None, **kw):
        return self.request("POST", path, body if body is not None else {}, **kw)

    def put(self, path, body=None, **kw):
        return self.request("PUT", path, body if body is not None else {}, **kw)

    def patch(self, path, body=None, **kw):
        return self.request("PATCH", path, body if body is not None else {}, **kw)


class ServerHarness:
    def __init__(self, data_dir: Path, repo_root: Path | None = None, graphify_bin: str | None = None, **app_kw):
        self.data_dir = Path(data_dir)
        self.cfg = Config(data_dir=self.data_dir, port=0, repo_root=repo_root or REPO_ROOT, graphify_bin=graphify_bin)
        self.app = App(self.cfg, **app_kw)
        self.srv = Server(("127.0.0.1", 0), make_handler(self.app))
        self.port = self.srv.server_address[1]
        self.cfg.port = self.port
        self.thread = threading.Thread(target=self.srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        self.thread.start()

    def client(self, **kw) -> Client:
        return Client(self.port, **kw)

    def db(self):
        return connect(self.cfg.db_path)

    def stop(self):
        self.srv.shutdown()
        self.srv.server_close()
        self.thread.join(timeout=5)


def seed_users(h: ServerHarness) -> dict:
    conn = h.db()
    try:
        owner = AU.create_user(conn, "loc", OWNER_PW, "OWNER", "Anh Lộc")
        editor = AU.create_user(conn, "bientap", EDITOR_PW, "EDITOR", "Biên tập")
        viewer = AU.create_user(conn, "nguoixem", VIEWER_PW, "VIEWER", "Người xem")
        oa = AU.Actor("USER", owner["id"], "OWNER")
        p1 = create_project(conn, oa, {"name": "Dự án Etsy"})
        p2 = create_project(conn, oa, {"name": "Dự án BTC riêng"})
        from crown_infinity.platform import set_member
        set_member(conn, oa, p1["id"], editor["id"], "EDITOR")
        set_member(conn, oa, p1["id"], viewer["id"], "VIEWER")
        return {"owner": owner, "editor": editor, "viewer": viewer, "p1": p1["id"], "p2": p2["id"]}
    finally:
        conn.close()


def b64(text: str | bytes) -> str:
    data = text.encode("utf-8") if isinstance(text, str) else text
    return base64.b64encode(data).decode("ascii")


class InfinityTestCase(unittest.TestCase):
    """Mỗi lớp test có thư mục dữ liệu riêng và máy chủ riêng."""

    app_kw: dict = {}

    def harness_kw(self) -> dict:
        return {}

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="ci-test-"))
        self.h = ServerHarness(self.tmp / "data", **self.harness_kw(), **self.app_kw)
        self.ids = seed_users(self.h)
        self.owner = self.h.client()
        st, _ = self.owner.login("loc", OWNER_PW)
        assert st == 200, st

    def tearDown(self):
        self.h.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def new_prompt(self, client=None, **kw):
        c = client or self.owner
        body = {"project_id": self.ids["p1"], "title": "Prompt thử", "content": "Nội dung ban đầu"}
        body.update(kw)
        st, d, _ = c.post("/api/v1/prompts", body)
        self.assertEqual(st, 201, d)
        return d
