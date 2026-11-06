"""Ca canh giữ cho 9 lỗi do kiểm định độc lập tìm ra ngày 2026-11-06 (mỗi ca tái hiện đúng kịch bản báo cáo)."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time

from crown_infinity import ai_hub
from crown_infinity.backup import BackupError, acquire_lock, create_backup, release_lock, restore_backup
from crown_infinity.config import REPO_ROOT
from tests.infinity.helpers import EDITOR_PW, OWNER_PW, InfinityTestCase, b64


def H(rev):
    return {"If-Match": f'"{rev}"'}


class R1BlockFanOut(InfinityTestCase):
    def test_wide_nested_blocks_are_capped_and_fast(self):
        n = 40  # mã cũ: 40^3 = 64.000 lần mở cho 3 tầng
        leaf = self.new_prompt(title="lá", content="x" * 50)
        level = leaf
        for depth in range(3):
            level = self.new_prompt(title=f"tầng {depth}", content=" ".join([f"{{{{> {level['id']}}}}}"] * n))
        t0 = time.monotonic()
        st, a, _ = self.owner.get(f"/api/v1/prompts/{level['id']}/analysis")
        self.assertEqual(st, 200)
        self.assertLess(time.monotonic() - t0, 2.0)
        self.assertIn("BLOCK_TOO_LARGE", {f["rule"] for f in a["findings"]})
        st, b, _ = self.owner.post(f"/api/v1/prompts/{level['id']}/render", {"variables": {}})
        self.assertEqual((st, b["error"]["details"]["block_errors"][0]["code"]), (400, "BLOCK_TOO_LARGE"))

    def test_shared_block_reused_within_limits(self):
        sig = self.new_prompt(title="chữ ký", content="— Lộc")
        d = self.new_prompt(content=f"A {{{{> {sig['id']}}}}} B {{{{> {sig['id']}}}}}")
        st, r, _ = self.owner.post(f"/api/v1/prompts/{d['id']}/render", {"variables": {}})
        self.assertEqual((st, r["text"]), (200, "A — Lộc B — Lộc"))


class R2DirtyDraftKept(InfinityTestCase):
    def test_import_new_version_snapshots_unversioned_draft(self):
        d = self.new_prompt(title="Trùng tên", content="v1")
        editor = self.h.client()
        editor.login("bientap", EDITOR_PW)
        st, e, _ = editor.put(f"/api/v1/prompts/{d['id']}/draft", {"content": "IMPORTANT unsaved work"},
                              headers=H(d["revision"]))
        f = {"filename": "Trùng tên.txt", "content_b64": b64("nội dung nhập")}
        st, pv, _ = self.owner.post("/api/v1/import/preview", {"project_id": self.ids["p1"], "files": [f]})
        tc = pv["items"][0]["title_conflict"]
        self.assertTrue(tc["draft_dirty"], "xem trước phải báo bản nháp chưa lưu phiên bản")
        st, r, _ = self.owner.post("/api/v1/import/commit", {"project_id": self.ids["p1"], "items": [
            dict(f, action="new_version", target_prompt_id=d["id"], target_revision=tc["revision"])]})
        self.assertEqual(st, 200, r)
        st, vs, _ = self.owner.get(f"/api/v1/prompts/{d['id']}/versions")
        contents = [self.owner.get(f"/api/v1/prompts/{d['id']}/versions/{v['id']}")[1]["content"] for v in vs["items"]]
        self.assertIn("IMPORTANT unsaved work", contents)
        self.assertEqual(contents[0], "nội dung nhập")

    def test_restore_snapshots_unversioned_draft(self):
        d = self.new_prompt(content="v1")
        pid, v1 = d["id"], d["head_version"]["id"]
        st, d, _ = self.owner.post(f"/api/v1/prompts/{pid}/versions", {"content": "v2"}, headers=H(d["revision"]))
        st, d, _ = self.owner.put(f"/api/v1/prompts/{pid}/draft", {"content": "nháp quý"}, headers=H(d["revision"]))
        st, d, _ = self.owner.post(f"/api/v1/prompts/{pid}/versions/{v1}/restore", {}, headers=H(d["revision"]))
        self.assertEqual(st, 201, d)
        self.assertEqual(d["draft_content"], "v1")
        st, vs, _ = self.owner.get(f"/api/v1/prompts/{pid}/versions")
        self.assertEqual([v["version_no"] for v in vs["items"]], [4, 3, 2, 1])
        snap = self.owner.get(f"/api/v1/prompts/{pid}/versions/{vs['items'][1]['id']}")[1]
        self.assertEqual(snap["content"], "nháp quý")


class R3ForeignCookie(InfinityTestCase):
    def test_bad_cookie_from_other_localhost_app_does_not_log_out(self):
        self.owner.cookies["theme"] = "dark mode"
        self.owner.cookies["x"] = '{"a":1}'
        self.assertEqual(self.owner.get("/api/v1/auth/me")[0], 200)


class R4BudgetEstimates(InfinityTestCase):
    def test_negative_nan_and_garbage_estimates_blocked(self):
        conn = self.h.db()
        try:
            for est in (-10, float("nan"), float("inf"), "x", True):
                g = ai_hub.gate(conn, None, est, 10)
                self.assertIn("INVALID_ESTIMATE", {r["code"] for r in g["reasons"]}, est)
            g = ai_hub.gate(conn, None, 0.1, -5)
            self.assertIn("INVALID_TOKENS", {r["code"] for r in g["reasons"]})
        finally:
            conn.close()
        st, b, _ = self.owner.post("/api/v1/ai/check", {"estimated_usd": "x", "max_output_tokens": "y"})
        self.assertEqual(st, 200)
        self.assertFalse(b["allowed"])


class R5Archived(InfinityTestCase):
    def test_restore_and_import_refuse_archived_prompt(self):
        d = self.new_prompt(title="Đã cất", content="v1")
        pid, v1 = d["id"], d["head_version"]["id"]
        st, d, _ = self.owner.post(f"/api/v1/prompts/{pid}/versions", {"content": "v2"}, headers=H(d["revision"]))
        st, d, _ = self.owner.post(f"/api/v1/prompts/{pid}/archive", {}, headers=H(d["revision"]))
        self.assertEqual(self.owner.post(f"/api/v1/prompts/{pid}/versions/{v1}/restore", {},
                                         headers=H(d["revision"]))[0], 409)
        f = {"filename": "Đã cất.txt", "content_b64": b64("mới")}
        st, pv, _ = self.owner.post("/api/v1/import/preview", {"project_id": self.ids["p1"], "files": [f]})
        self.assertIsNone(pv["items"][0]["title_conflict"], "không đề nghị prompt đã lưu trữ làm đích")
        st, b, _ = self.owner.post("/api/v1/import/commit", {"project_id": self.ids["p1"], "items": [
            dict(f, action="new_version", target_prompt_id=pid, target_revision=d["revision"])]})
        self.assertEqual((st, b["error"]["code"]), (409, "ARCHIVED"))


class R6DataDirLock(InfinityTestCase):
    def test_second_server_on_same_data_dir_refused_and_lock_kept(self):
        data = self.tmp / "lockdir"
        p = subprocess.Popen([sys.executable, "-m", "crown_infinity", "--data-dir", str(data), "serve", "--port", "0"],
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", cwd=REPO_ROOT)
        try:
            p.stdout.readline()
            r = subprocess.run([sys.executable, "-m", "crown_infinity", "--data-dir", str(data), "serve", "--port", "0"],
                               capture_output=True, text=True, encoding="utf-8", cwd=REPO_ROOT, timeout=30)
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("đang chạy", r.stderr)
            lock = json.loads((data / "server.lock").read_text(encoding="utf-8"))
            self.assertEqual(lock["pid"], p.pid, "máy chủ thứ hai không được xóa/ghi đè khóa của máy chủ đầu")
            z = create_backup(data / "crown_infinity.sqlite", data / "backups")
            with self.assertRaises(BackupError):
                restore_backup(z, data / "crown_infinity.sqlite", data / "backups", data / "server.lock")
        finally:
            p.terminate()
            p.wait(timeout=15)
            p.stdout.close()
            p.stderr.close()

    def test_stale_lock_recovered_and_release_only_own(self):
        lock = self.tmp / "x.lock"
        lock.write_text(json.dumps({"pid": 2 ** 22 + 12345}), encoding="utf-8")  # pid không tồn tại
        acquire_lock(lock, {"kind": "t"})
        self.assertEqual(json.loads(lock.read_text())["pid"], os.getpid())
        release_lock(lock)
        self.assertFalse(lock.exists())
        lock.write_text(json.dumps({"pid": os.getpid() + 1}), encoding="utf-8")
        release_lock(lock)
        self.assertTrue(lock.exists(), "không xóa khóa của tiến trình khác")


class R7AnonymousAuditFlood(InfinityTestCase):
    def test_anonymous_401_not_persisted(self):
        conn = self.h.db()
        try:
            before = conn.execute("SELECT COUNT(*) FROM activity_logs").fetchone()[0]
            anon = self.h.client()
            for _ in range(30):
                anon.get("/api/v1/dashboard")
            after = conn.execute("SELECT COUNT(*) FROM activity_logs").fetchone()[0]
            self.assertEqual(before, after)
        finally:
            conn.close()


class R8DeactivateWithZero(InfinityTestCase):
    def test_is_active_zero_revokes_sessions(self):
        ed = self.h.client()
        ed.login("bientap", EDITOR_PW)
        self.owner.patch(f"/api/v1/admin/users/{self.ids['editor']['id']}", {"is_active": 0})
        self.owner.patch(f"/api/v1/admin/users/{self.ids['editor']['id']}", {"is_active": 1})
        self.assertEqual(ed.get("/api/v1/auth/me")[0], 401, "phiên cũ không sống lại sau khi mở khóa")


class R9MalformedInput(InfinityTestCase):
    def test_malformed_input_gives_4xx_not_500(self):
        d = self.new_prompt(content="x")
        cases = [
            ("GET", "/api/v1/activity?limit=abc", None),
            ("GET", "/api/v1/activity?before_id=x", None),
            ("GET", "/api/v1/activity?before_id=99999999999999999999999", None),
            ("GET", "/api/v1/prompts?page=10000000000000000000", None),
            ("POST", f"/api/v1/prompts/{d['id']}/render", {"version": [1]}),
            ("POST", f"/api/v1/prompts/{d['id']}/render", {}),
            ("POST", "/api/v1/import/commit", {"project_id": self.ids["p1"], "items": [1]}),
            ("POST", "/api/v1/import/commit", {"project_id": self.ids["p1"], "items": [
                {"filename": "a.txt", "content_b64": b64("a"), "action": "new_version", "target_prompt_id": [1]}]}),
            ("POST", "/api/v1/tasks", {"title": "t", "project_id": [1]}),
            ("PUT", f"/api/v1/prompts/{d['id']}/production", {"version_id": [1], "expected_version_id": None}),
            ("POST", "/api/v1/ai/check", {"estimated_usd": "x"}),
            ("GET", "/" + "a" * 5000, None),
        ]
        for method, path, body in cases:
            st, b, _ = self.owner.request(method, path, body)
            self.assertLess(st, 500, f"{method} {path[:60]} → {st} {b}")
        self.assertEqual(self.owner.get("/api/v1/auth/me")[0], 200)
        _ = OWNER_PW
