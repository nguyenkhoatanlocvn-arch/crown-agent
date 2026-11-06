"""Nghiệm thu nền tảng — tiêu chí 7–11 và 15 (phần mock) của CROWN INFINITY MASTER DIRECTIVE V2.0."""
from __future__ import annotations

import json
import os
import shutil
import socket
import sqlite3
import tempfile
import threading
import zipfile
from pathlib import Path
from unittest import mock

from crown_infinity import ai_hub
from crown_infinity.auth import SYSTEM
from crown_infinity.backup import BackupError, copy_db_for_test, create_backup, restore_backup, verify_backup
from crown_infinity.db import MIGRATIONS_DIR, MigrationError, connect, migrate, table_counts
from crown_infinity.httpcore import ApiError
from tests.infinity.helpers import EDITOR_PW, OWNER_PW, VIEWER_PW, InfinityTestCase, ServerHarness, b64


def H(rev):
    return {"If-Match": f'"{rev}"'}


class Fixture(InfinityTestCase):
    def build_rich_data(self):
        """Prompt + 2 phiên bản + tag + nguồn + assignment Agent."""
        d = self.new_prompt(title="Kịch bản CSKH", content="Chào {{khach}}", tags=["cskh", "tiếng việt"])
        pid, v1 = d["id"], d["head_version"]["id"]
        self.owner.post(f"/api/v1/prompts/{pid}/versions/{v1}/review", {"status": "APPROVED"})
        st, d, _ = self.owner.post(f"/api/v1/prompts/{pid}/versions", {"content": "Chào anh/chị {{khach}}"},
                                   headers=H(d["revision"]))
        self.owner.post(f"/api/v1/prompts/{pid}/sources", {"kind": "URL", "title": "Chính sách", "url": "https://example.com/p"})
        self.owner.post("/api/v1/agents/sync")
        st, a, _ = self.owner.post("/api/v1/assignments", {"agent_id": "A14", "prompt_id": pid, "version_id": v1})
        self.assertEqual(st, 201, a)
        return pid, v1, d["head_version"]["id"]


class T07BackupRestore(Fixture):
    def test_backup_then_restore_into_clean_database(self):
        pid, v1, v2 = self.build_rich_data()
        st, b, _ = self.owner.post("/api/v1/admin/backups")
        self.assertEqual(st, 201, b)
        zpath = self.h.cfg.backup_dir / b["name"]
        with zipfile.ZipFile(zpath) as z:
            self.assertEqual(set(z.namelist()), {"metadata.json", "database.sqlite"})
            meta = json.loads(z.read("metadata.json"))
        self.assertEqual(meta["schema_version"], 1)
        clean = self.tmp / "clean"
        clean.mkdir()
        res = restore_backup(zpath, clean / "crown_infinity.sqlite", clean / "backups", clean / "server.lock")
        self.assertIsNone(res["safety_backup"], "DB sạch nên không cần sao lưu an toàn")
        src, dst = self.h.db(), connect(clean / "crown_infinity.sqlite")
        try:
            # bản khôi phục khớp đúng ảnh chụp lúc sao lưu (dòng nhật ký 'backup.create' được ghi SAU ảnh chụp)
            self.assertEqual(table_counts(dst), meta["counts"])
            live = table_counts(src)
            self.assertEqual(live.pop("activity_logs") - 1, meta["counts"]["activity_logs"])
            self.assertEqual({k: v for k, v in meta["counts"].items() if k != "activity_logs"}, live)
            q = "SELECT id,version_no,content,checksum,review_status FROM prompt_versions ORDER BY id"
            self.assertEqual([tuple(r) for r in src.execute(q)], [tuple(r) for r in dst.execute(q)])
            q = "SELECT agent_id,prompt_id,version_id FROM agent_prompt_assignments"
            self.assertEqual([tuple(r) for r in dst.execute(q)], [("A14", pid, v1)])
            q = "SELECT t.name FROM prompt_tags pt JOIN tags t ON t.id=pt.tag_id WHERE pt.prompt_id=? ORDER BY t.name"
            self.assertEqual([r[0] for r in dst.execute(q, (pid,))], ["cskh", "tiếng việt"])
            self.assertEqual(dst.execute("SELECT COUNT(*) FROM prompt_sources").fetchone()[0], 1)
        finally:
            src.close()
            dst.close()
        # máy chủ mới trên dữ liệu khôi phục vẫn đăng nhập và đọc được
        h2 = ServerHarness(clean)
        try:
            c = h2.client()
            self.assertEqual(c.login("loc", OWNER_PW)[0], 200)
            st, d, _ = c.get(f"/api/v1/prompts/{pid}")
            self.assertEqual((st, d["draft_content"]), (200, "Chào anh/chị {{khach}}"))
            st, r, _ = c.get("/api/v1/prompts?q=cskh")
            self.assertEqual(r["total"], 1, "chỉ mục tìm kiếm dùng được sau khôi phục")
        finally:
            h2.stop()

    def test_restore_over_existing_keeps_safety_backup(self):
        self.build_rich_data()
        z = create_backup(self.h.cfg.db_path, self.h.cfg.backup_dir)
        other = self.tmp / "other"
        h2 = ServerHarness(other)
        h2.stop()
        res = restore_backup(z, other / "crown_infinity.sqlite", other / "backups", other / "server.lock")
        self.assertTrue(Path(res["safety_backup"]).is_file())

    def test_tampered_or_foreign_backup_rejected(self):
        z = create_backup(self.h.cfg.db_path, self.h.cfg.backup_dir)
        bad = self.tmp / "bad.zip"
        with zipfile.ZipFile(z) as zi, zipfile.ZipFile(bad, "w") as zo:
            zo.writestr("metadata.json", zi.read("metadata.json"))
            data = bytearray(zi.read("database.sqlite"))
            data[-10] ^= 0xFF
            zo.writestr("database.sqlite", bytes(data))
        with self.assertRaisesRegex(BackupError, "Băm"):
            verify_backup(bad, self.tmp / "w1")
        slip = self.tmp / "slip.zip"
        with zipfile.ZipFile(slip, "w") as zo:
            zo.writestr("../evil.txt", "x")
        with self.assertRaises(BackupError):
            verify_backup(slip, self.tmp / "w2")

    def test_restore_refused_while_server_running(self):
        z = create_backup(self.h.cfg.db_path, self.h.cfg.backup_dir)
        self.h.cfg.lock_path.write_text(json.dumps({"pid": os.getpid()}), encoding="utf-8")
        with self.assertRaisesRegex(BackupError, "đang chạy"):
            restore_backup(z, self.h.cfg.db_path, self.h.cfg.backup_dir, self.h.cfg.lock_path)


class T08Migration(Fixture):
    def _mig_dir(self, extra_sql: str, name="0002_test_change.sql") -> Path:
        d = Path(tempfile.mkdtemp(dir=self.tmp))
        shutil.copy(MIGRATIONS_DIR / "0001_init.sql", d / "0001_init.sql")
        (d / name).write_text(extra_sql, encoding="utf-8")
        return d

    def test_migration_on_copy_keeps_data_and_backs_up_first(self):
        pid, v1, v2 = self.build_rich_data()
        copy = self.tmp / "copy.sqlite"
        copy_db_for_test(self.h.cfg.db_path, copy)
        c = connect(copy)
        before = table_counts(c)
        versions_before = [tuple(r) for r in c.execute("SELECT id, content, checksum FROM prompt_versions ORDER BY id")]
        c.close()
        mig = self._mig_dir("ALTER TABLE prompts ADD COLUMN test_flag INTEGER NOT NULL DEFAULT 0;\n"
                            "CREATE TABLE test_extra (id INTEGER PRIMARY KEY);")
        res = migrate(copy, migrations_dir=mig, backup_dir=self.tmp / "mbk")
        self.assertEqual((res["from"], res["to"], res["applied"]), (1, 2, [2]))
        self.assertTrue(Path(res["backup"]).is_file(), "phải sao lưu trước khi đổi lược đồ")
        c = connect(copy)
        try:
            after = table_counts(c)
            after.pop("test_extra")
            before.pop("schema_migrations"), after.pop("schema_migrations")
            self.assertEqual(before, after)
            self.assertEqual(versions_before,
                             [tuple(r) for r in c.execute("SELECT id, content, checksum FROM prompt_versions ORDER BY id")])
            self.assertEqual(c.execute("SELECT test_flag FROM prompts WHERE id=?", (pid,)).fetchone()[0], 0)
        finally:
            c.close()
        live = self.h.db()
        try:  # bản đang chạy không bị đụng tới
            self.assertNotIn("test_flag", [r[1] for r in live.execute("PRAGMA table_info(prompts)")])
        finally:
            live.close()

    def test_failed_migration_rolls_back(self):
        self.build_rich_data()
        copy = self.tmp / "copy2.sqlite"
        copy_db_for_test(self.h.cfg.db_path, copy)
        mig = self._mig_dir("CREATE TABLE half (id INTEGER);\nINSERT INTO bang_khong_ton_tai VALUES (1);")
        with self.assertRaises(MigrationError):
            migrate(copy, migrations_dir=mig)
        c = connect(copy)
        try:
            self.assertIsNone(c.execute("SELECT 1 FROM sqlite_master WHERE name='half'").fetchone())
            self.assertEqual(c.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0], 1)
        finally:
            c.close()

    def test_edited_applied_migration_detected(self):
        copy = self.tmp / "copy3.sqlite"
        copy_db_for_test(self.h.cfg.db_path, copy)
        d = Path(tempfile.mkdtemp(dir=self.tmp))
        (d / "0001_init.sql").write_text((MIGRATIONS_DIR / "0001_init.sql").read_text(encoding="utf-8") + "\n-- sửa lén\n",
                                         encoding="utf-8")
        with self.assertRaisesRegex(MigrationError, "bị sửa"):
            migrate(copy, migrations_dir=d)


class T09AiOff(Fixture):
    def test_full_flow_without_ai_makes_no_outbound_connection(self):
        outbound = []
        real_connect = socket.socket.connect

        def guard(sock, addr):
            host = addr[0] if isinstance(addr, tuple) else str(addr)
            if host not in ("127.0.0.1", "::1", "localhost"):
                outbound.append(addr)
                raise OSError("Kiểm thử chặn kết nối ra ngoài")
            return real_connect(sock, addr)

        with mock.patch.object(socket.socket, "connect", guard):
            pid, v1, v2 = self.build_rich_data()
            self.owner.get("/api/v1/prompts?q=khach")
            self.owner.get(f"/api/v1/prompts/{pid}/diff?from={v1}&to={v2}")
            self.owner.get(f"/api/v1/prompts/{pid}/export?format=json")
            st, pv, _ = self.owner.post("/api/v1/import/preview", {"project_id": self.ids["p1"],
                                                                   "files": [{"filename": "x.md", "content_b64": b64("# X\nnội dung")}]})
            self.assertEqual(st, 200)
            st, _, _ = self.owner.post(f"/api/v1/prompts/{pid}/render", {"variables": {"khach": "An"}})
            self.assertEqual(st, 200)
        self.assertEqual(outbound, [])
        conn = self.h.db()
        try:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM ai_usage").fetchone()[0], 0)
            g = ai_hub.gate(conn, None, 0.01, 100)
            self.assertFalse(g["allowed"])
            self.assertIn("AI_DISABLED", {r["code"] for r in g["reasons"]})
            transport = mock.Mock()
            with self.assertRaises(ApiError):
                ai_hub.call_model(conn, "khong-co", {}, est_usd=0.01, est_output_tokens=10, purpose="test",
                                  transport=transport)
            transport.assert_not_called()
        finally:
            conn.close()

    def test_api_key_never_accepted_via_web(self):
        st, body, _ = self.owner.post("/api/v1/ai/providers", {"name": "x", "kind": "anthropic", "api_key": "sk-123"})
        self.assertEqual(st, 400)
        st, p, _ = self.owner.post("/api/v1/ai/providers", {"name": "Claude", "kind": "anthropic", "key_env": "CI_TEST_KEY"})
        self.assertEqual(st, 201)
        self.assertNotIn("sk-", json.dumps(p))
        self.assertFalse(p["key_configured"])


class T15BudgetReservationMock(Fixture):
    """Chỉ chứng minh logic cục bộ bằng transport giả — KHÔNG phải gọi model thật."""

    def test_concurrent_reservations_never_exceed_daily_budget(self):
        conn = self.h.db()
        try:
            with mock.patch.dict(os.environ, {"CI_TEST_KEY": "gia-tri-gia"}):
                st, p, _ = self.owner.post("/api/v1/ai/providers", {"name": "Giả", "kind": "other", "key_env": "CI_TEST_KEY",
                                                                   "enabled": True})
                st, _, _ = self.owner.put("/api/v1/ai/settings", {"enabled": True, "budget": {
                    "daily_usd": 1.0, "monthly_usd": 5.0, "per_request_usd": 0.4, "max_output_tokens": 500,
                    "max_concurrency": 16}})
                self.assertEqual(st, 200)
                ok, denied, lock = [], [], threading.Lock()

                def worker():
                    c = connect(self.h.cfg.db_path)
                    try:
                        uid = ai_hub.reserve(c, p["id"], 0.3, 100, "test")
                        with lock:
                            ok.append(uid)
                    except ApiError as exc:
                        with lock:
                            denied.append(exc.details)
                    finally:
                        c.close()
                ts = [threading.Thread(target=worker) for _ in range(8)]
                [t.start() for t in ts]
                [t.join() for t in ts]
                self.assertEqual(len(ok), 3, "0.3 × 3 = 0.9 ≤ 1.0; lần thứ 4 vượt trần ngày")
                self.assertEqual(len(denied), 5)
                spent = conn.execute("SELECT SUM(reserved_usd) FROM ai_usage WHERE status='RESERVED'").fetchone()[0]
                self.assertLessEqual(spent, 1.0)
                # transport giả: thử lại hữu hạn rồi FAILED
                for uid in ok:
                    ai_hub.finalize(conn, uid, "RELEASED")
                calls = []

                def flaky(prov, payload, timeout):
                    calls.append(timeout)
                    raise TimeoutError()
                with self.assertRaises(ApiError) as cm:
                    ai_hub.call_model(conn, p["id"], {}, est_usd=0.1, est_output_tokens=50, purpose="t", transport=flaky)
                self.assertEqual(cm.exception.code, "AI_CALL_FAILED")
                self.assertEqual(len(calls), 2, "max_retries=1 → tổng 2 lần")
        finally:
            conn.close()


class T10Authz(Fixture):
    def test_every_user_route_requires_login(self):
        anon = self.h.client()
        for r in self.h.app.router.routes:
            path = r.template
            for k in ("pid", "vid", "sid", "uid", "tid", "mid", "aid", "agent_id"):
                path = path.replace("{" + k + "}", "X-1")
            st, body, _ = anon.request(r.method, path, {} if r.method != "GET" else None)
            if r.auth == "public":
                continue
            self.assertEqual(st, 401, f"{r.method} {r.template} → {st}")

    def test_roles_and_object_level_permissions(self):
        d = self.new_prompt(content="Riêng")
        secret = self.new_prompt(project_id=self.ids["p2"], content="Bí mật dự án 2")
        viewer, editor = self.h.client(), self.h.client()
        viewer.login("nguoixem", VIEWER_PW)
        editor.login("bientap", EDITOR_PW)
        self.assertEqual(viewer.get(f"/api/v1/prompts/{d['id']}")[0], 200)
        self.assertEqual(viewer.put(f"/api/v1/prompts/{d['id']}/draft", {"content": "x"}, headers=H(d["revision"]))[0], 403)
        self.assertEqual(viewer.post("/api/v1/prompts", {"project_id": self.ids["p1"], "title": "x"})[0], 403)
        self.assertEqual(editor.get(f"/api/v1/prompts/{secret['id']}")[0], 404)
        self.assertEqual(editor.put(f"/api/v1/prompts/{secret['id']}/draft", {"content": "x"},
                                    headers=H(secret["revision"]))[0], 404)
        self.assertEqual(editor.post("/api/v1/prompts", {"project_id": self.ids["p2"], "title": "x"})[0], 404)
        self.assertEqual(editor.get(f"/api/v1/prompts/{secret['id']}/export?format=txt")[0], 404)
        v1 = d["head_version"]["id"]
        self.assertEqual(editor.post(f"/api/v1/prompts/{d['id']}/versions/{v1}/review", {"status": "APPROVED"})[0], 403)
        self.assertEqual(editor.post(f"/api/v1/prompts/{d['id']}/versions/{v1}/review", {"status": "TESTING"})[0], 200)
        for path in ("/api/v1/admin/users", "/api/v1/ai", "/api/v1/agents", "/api/v1/admin/backups"):
            self.assertEqual(editor.get(path)[0], 403, path)
        self.assertEqual(editor.post("/api/v1/admin/users", {"username": "x", "password": "y" * 12, "role": "OWNER"})[0], 403)
        # project_id do client tự khai không vượt được quyền
        self.assertEqual(editor.patch(f"/api/v1/prompts/{d['id']}", {"project_id": self.ids["p2"]},
                                      headers=H(d["revision"]))[0], 404)

    def test_csrf_host_origin_session(self):
        d = self.new_prompt(content="x")
        st, b, _ = self.owner.put(f"/api/v1/prompts/{d['id']}/draft", {"content": "y"}, headers=H(d["revision"]), csrf=False)
        self.assertEqual((st, b["error"]["code"]), (403, "CSRF_FAILED"))
        st, b, _ = self.owner.put(f"/api/v1/prompts/{d['id']}/draft", {"content": "y"},
                                  headers={**H(d["revision"]), "Origin": "http://evil.example"})
        self.assertEqual(st, 403)
        evil = self.h.client(host_header="evil.example")
        self.assertEqual(evil.get("/api/v1/health")[0], 421)
        # thu hồi phiên → 401
        other = self.h.client()
        other.login("loc", OWNER_PW)
        st, _, _ = self.owner.post(f"/api/v1/admin/users/{self.ids['owner']['id']}/revoke-sessions")
        self.assertEqual(st, 200)
        self.assertEqual(other.get("/api/v1/auth/me")[0], 401)

    def test_login_rate_limit_and_inactive_user(self):
        c = self.h.client()
        for _ in range(5):
            self.assertEqual(c.login("bientap", "sai-mat-khau-123")[0], 401)
        self.assertEqual(c.login("bientap", EDITOR_PW)[0], 429)
        self.owner.patch(f"/api/v1/admin/users/{self.ids['viewer']['id']}", {"is_active": False})
        self.assertEqual(self.h.client().login("nguoixem", VIEWER_PW)[0], 401)

    def test_audit_has_no_prompt_content(self):
        self.new_prompt(content="NOI-DUNG-NHAY-CAM-123")
        self.owner.get("/api/v1/agents")  # thêm hoạt động
        conn = self.h.db()
        try:
            rows = conn.execute("SELECT detail_json FROM activity_logs").fetchall()
            self.assertTrue(rows)
            self.assertFalse(any("NOI-DUNG-NHAY-CAM" in r[0] for r in rows))
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("DELETE FROM activity_logs")
        finally:
            conn.close()


class T11AgentAssignment(Fixture):
    def agent_client(self, agent_id):
        st, t, _ = self.owner.post(f"/api/v1/agents/{agent_id}/token")
        self.assertEqual(st, 200, t)
        c = self.h.client()
        return c, {"Authorization": f"Bearer {t['token']}"}

    def test_agent_reads_only_pinned_version(self):
        pid, v1, v2 = self.build_rich_data()
        other = self.new_prompt(title="Không cấp", content="cấm đọc")
        agent, auth = self.agent_client("A14")
        st, r, _ = agent.get(f"/api/v1/agent/prompts/{pid}", headers=auth)
        self.assertEqual((st, r["version_id"], r["content"]), (200, v1, "Chào {{khach}}"))
        # duyệt + đặt production v2 → assignment KHÔNG đổi
        self.owner.post(f"/api/v1/prompts/{pid}/versions/{v2}/review", {"status": "APPROVED"})
        self.owner.put(f"/api/v1/prompts/{pid}/production", {"version_id": v2, "expected_version_id": None})
        st, r, _ = agent.get(f"/api/v1/agent/prompts/{pid}?agent_id=A03", headers=auth)
        self.assertEqual((st, r["version_id"], r["agent_id"]), (200, v1, "A14"), "agent_id tự khai bị bỏ qua")
        self.assertEqual(agent.get(f"/api/v1/agent/prompts/{other['id']}", headers=auth)[0], 403)
        # Agent khác không có assignment
        a03, auth03 = self.agent_client("A03")
        self.assertEqual(a03.get(f"/api/v1/agent/prompts/{pid}", headers=auth03)[0], 403)
        # token Agent không dùng được cho API người dùng
        self.assertEqual(agent.get("/api/v1/prompts", headers=auth)[0], 401)
        # thu hồi token → 401; không cấp được bản chưa duyệt
        self.owner.request("DELETE", "/api/v1/agents/A14/token")
        self.assertEqual(agent.get(f"/api/v1/agent/prompts/{pid}", headers=auth)[0], 401)
        st, d, _ = self.owner.get(f"/api/v1/prompts/{other['id']}")
        st, b, _ = self.owner.post("/api/v1/assignments", {"agent_id": "A03", "prompt_id": other["id"],
                                                          "version_id": d["head_version"]["id"]})
        self.assertEqual((st, b["error"]["code"]), (409, "NOT_APPROVED"))
        # assignment bị ghim ở tầng DB
        conn = self.h.db()
        try:
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("UPDATE agent_prompt_assignments SET version_id=?", (v2,))
        finally:
            conn.close()

    def test_cannot_unapprove_version_in_use(self):
        pid, v1, v2 = self.build_rich_data()
        st, b, _ = self.owner.post(f"/api/v1/prompts/{pid}/versions/{v1}/review", {"status": "REJECTED"})
        self.assertEqual((st, b["error"]["code"]), (409, "IN_USE"))


class TModulesTools(Fixture):
    def test_registry_reads_real_repo_data_and_planned_modules_cannot_enable(self):
        st, m, _ = self.owner.get("/api/v1/modules")
        mods = {x["module_id"]: x for x in m["items"]}
        self.assertEqual(len(mods), 11)
        self.assertEqual(mods["crown_btc_control_center"]["dev_status"], "PLANNED")
        st, b, _ = self.owner.patch("/api/v1/modules/crown_btc_control_center", {"enabled": True})
        self.assertEqual((st, b["error"]["code"]), (409, "NOT_IMPLEMENTED"))
        st, t, _ = self.owner.post("/api/v1/tools/sync")
        self.assertEqual(st, 200)
        self.assertGreaterEqual(t["synced"], 1)
        st, tl, _ = self.owner.get("/api/v1/tools")
        self.assertIn("srt-renumber", {x["id"] for x in tl["items"]})
        # tắt Prompt Studio → API trả MODULE_DISABLED, bật lại thì chạy
        self.owner.patch("/api/v1/modules/crown_prompt_studio", {"enabled": False})
        self.assertEqual(self.owner.get("/api/v1/prompts")[0], 409)
        self.owner.patch("/api/v1/modules/crown_prompt_studio", {"enabled": True})
        self.assertEqual(self.owner.get("/api/v1/prompts")[0], 200)

    def test_dashboard_counts_are_real(self):
        self.build_rich_data()
        st, d, _ = self.owner.get("/api/v1/dashboard")
        self.assertEqual(d["prompts"]["active"], 1)
        self.assertEqual(d["prompts"]["versions"], 2)
        self.assertEqual(d["prompts"]["approved_versions"], 1)
        self.assertFalse(d["ai"]["enabled"])
        self.assertEqual(d["integrations"]["crown_agent_control"]["status"], "ONLINE")

    def test_task_revision_conflict(self):
        st, t, _ = self.owner.post("/api/v1/tasks", {"title": "Việc A", "project_id": self.ids["p1"]})
        self.assertEqual(st, 201)
        self.assertEqual(self.owner.patch(f"/api/v1/tasks/{t['id']}", {"status": "DONE"}, headers=H(1))[0], 200)
        self.assertEqual(self.owner.patch(f"/api/v1/tasks/{t['id']}", {"status": "TODO"}, headers=H(1))[0], 409)


_ = SYSTEM


class TTaskVisibility(Fixture):
    def test_non_owner_cannot_see_owner_global_tasks(self):
        self.owner.post("/api/v1/tasks", {"title": "Việc riêng của OWNER"})
        self.owner.post("/api/v1/tasks", {"title": "Việc dự án 1", "project_id": self.ids["p1"]})
        self.owner.post("/api/v1/tasks", {"title": "Việc dự án 2", "project_id": self.ids["p2"]})
        editor = self.h.client()
        editor.login("bientap", EDITOR_PW)
        st, r, _ = editor.get("/api/v1/tasks")
        self.assertEqual([t["title"] for t in r["items"]], ["Việc dự án 1"])
        st, d, _ = editor.get("/api/v1/dashboard")
        self.assertEqual(sum(d["tasks"].values()), 1)

    def test_login_from_foreign_origin_rejected(self):
        c = self.h.client()
        st, b, _ = c.request("POST", "/api/v1/auth/login", {"username": "loc", "password": OWNER_PW},
                             headers={"Origin": "http://evil.example"})
        self.assertEqual(st, 403)
