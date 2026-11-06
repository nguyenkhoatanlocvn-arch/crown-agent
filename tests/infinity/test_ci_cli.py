"""Tiêu chí 1 + 14: chạy bằng lệnh thật `python -m crown_infinity`, dừng tiến trình, chạy lại, dữ liệu còn nguyên."""
from __future__ import annotations

import json
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from crown_infinity.config import REPO_ROOT
from tests.infinity.helpers import OWNER_PW, Client


def run(*args, stdin=None):
    return subprocess.run([sys.executable, "-m", "crown_infinity", *args], input=stdin, capture_output=True,
                          text=True, encoding="utf-8", cwd=REPO_ROOT, timeout=60)


class ServeProcess:
    def __init__(self, data_dir):
        self.p = subprocess.Popen([sys.executable, "-m", "crown_infinity", "--data-dir", str(data_dir), "serve",
                                   "--port", "0"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                  encoding="utf-8", cwd=REPO_ROOT)
        line = self.p.stdout.readline()
        m = re.search(r":(\d+)/", line)
        if not m:
            self.p.kill()
            raise AssertionError(f"Không đọc được cổng: {line!r} {self.p.stderr.read()}")
        self.port = int(m.group(1))

    def stop(self):
        self.p.send_signal(signal.SIGINT)
        try:
            self.p.wait(timeout=15)
        finally:
            for s in (self.p.stdout, self.p.stderr):
                s.close()
        return self.p.returncode


class CliTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="ci-cli-"))
        self.data = self.tmp / "data"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_init_serve_stop_restart_persist_backup_restore(self):
        r = run("--data-dir", str(self.data), "init-owner", "--username", "loc", "--password-stdin", stdin=OWNER_PW + "\n")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn(OWNER_PW, r.stdout + r.stderr, "mật khẩu không được in ra")
        r = run("--data-dir", str(self.data), "init-owner", "--username", "khac", "--password-stdin", stdin=OWNER_PW + "\n")
        self.assertEqual(r.returncode, 1, "không tạo OWNER thứ hai qua CLI")

        srv = ServeProcess(self.data)
        try:
            self.assertTrue((self.data / "server.lock").is_file())
            c = Client(srv.port)
            self.assertEqual(c.login("loc", OWNER_PW)[0], 200)
            st, projects, _ = c.get("/api/v1/projects")
            pid_project = projects["items"][0]["id"]
            st, d, _ = c.post("/api/v1/prompts", {"project_id": pid_project, "title": "Lời chào Hoàng Thành",
                                                  "content": "Kính chào Chủ tịch Nguyễn Khoa Tấn Lộc 🐉"})
            self.assertEqual(st, 201)
            # khôi phục bị từ chối khi máy chủ đang chạy
            r = run("--data-dir", str(self.data), "backup")
            self.assertEqual(r.returncode, 0, r.stderr)
            zpath = r.stdout.strip()
            r = run("--data-dir", str(self.data), "restore", zpath)
            self.assertEqual(r.returncode, 2)
            self.assertIn("đang chạy", r.stderr)
        finally:
            self.assertEqual(srv.stop(), 0)
        self.assertFalse((self.data / "server.lock").exists(), "dừng sạch phải xóa khóa")

        srv = ServeProcess(self.data)  # chạy lại
        try:
            c = Client(srv.port)
            c.login("loc", OWNER_PW)
            st, got, _ = c.get(f"/api/v1/prompts/{d['id']}")
            self.assertEqual(got["draft_content"], "Kính chào Chủ tịch Nguyễn Khoa Tấn Lộc 🐉")
        finally:
            srv.stop()

        r = run("--data-dir", str(self.data), "restore", zpath, "--verify-only")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("HỢP LỆ", r.stdout)
        r = run("--data-dir", str(self.data), "restore", zpath)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(Path(json.loads(r.stdout)["safety_backup"]).is_file())
        r = run("--data-dir", str(self.data), "status")
        st = json.loads(r.stdout)
        self.assertEqual((st["db_schema"], st["counts"]["prompts"]), (1, 1))

    def test_refuses_lan_without_flag(self):
        r = run("--data-dir", str(self.data), "serve", "--host", "0.0.0.0", "--port", "0")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("--allow-lan", r.stderr)


if __name__ == "__main__":
    unittest.main()
_ = time
