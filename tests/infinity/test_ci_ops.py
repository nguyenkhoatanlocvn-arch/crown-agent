"""Bộ não điều hành: bộ nhớ quyết định, bản đồ mã, phạm vi Agent, Rà soát bản build, chống chạy trùng, lịch, AI.

Dùng một repo git TẠM làm nguồn để kết quả không phụ thuộc trạng thái repo thật. Ca dùng Graphify thật chỉ chạy khi
có biến CROWN_INFINITY_GRAPHIFY trỏ tới lệnh graphify (gói graphifyy).
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import threading
import unittest
from pathlib import Path

from crown_infinity import graphmap as GM
from crown_infinity import ops as OPS
from crown_infinity.auth import Actor
from crown_infinity.util import iso_after
from tests.infinity.helpers import EDITOR_PW, OWNER_PW, VIEWER_PW, InfinityTestCase, ServerHarness

FIRST_DECISION = "Tạm hoãn Voice Studio, Video Studio và SEO Studio. Ưu tiên nền tảng CROWN INFINITY."
MOD_A = "def helper():\n    return 1\n\n\ndef main():\n    return helper()\n"
REPORT = ("# Kết quả\n| # | Tiêu chí | Phương pháp | Kết quả |\n|---|---|---|---|\n"
          "| 1 | Lưu tiếng Việt | test | **PASS** |\n| 14 | Chạy trên Windows | thủ công | **NOT VERIFIED** |\n")


def H(rev):
    return {"If-Match": f'"{rev}"'}


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True).stdout.strip()


def make_repo(root: Path) -> Path:
    repo = root / "repo"
    (repo / "crown_infinity").mkdir(parents=True)
    (repo / "docs" / "infinity").mkdir(parents=True)
    (repo / "crown_infinity" / "mod_a.py").write_text(MOD_A, encoding="utf-8")
    (repo / "docs" / "infinity" / "TEST_RESULTS.md").write_text(REPORT, encoding="utf-8")
    git(repo, "init", "-q")
    git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "add", "-A")
    git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "khởi tạo")
    return repo


def fixture_graph() -> bytes:
    """Đúng định dạng graph.json của Graphify 0.9.103 (khóa nodes/links, confidence, source_location)."""
    return json.dumps({"directed": False, "graph": {"graphify_version": "0.9.103"}, "nodes": [
        {"id": "mod_a_helper", "label": "helper()", "_callable": True, "source_file": "mod_a.py", "source_location": "L1"},
        {"id": "mod_a_main", "label": "main()", "_callable": True, "source_file": "mod_a.py", "source_location": "L5"},
        {"id": "mod_a", "label": "mod_a.py", "file_type": "code", "source_file": "mod_a.py", "source_location": "L1"}],
        "links": [
            {"source": "mod_a_main", "target": "mod_a_helper", "relation": "calls", "confidence": "EXTRACTED",
             "confidence_score": 1.0, "source_file": "mod_a.py", "source_location": "L6"},
            {"source": "mod_a", "target": "mod_a_helper", "relation": "contains", "confidence": "EXTRACTED",
             "source_file": "mod_a.py", "source_location": "L1"},
            {"source": "mod_a_helper", "target": "mod_a_main", "relation": "uses", "confidence": "INFERRED",
             "confidence_score": 0.6, "source_file": "mod_a.py", "source_location": "L2"}]}).encode()


class OpsBase(InfinityTestCase):
    def harness_kw(self):
        import tempfile
        self._repo_tmp = Path(tempfile.mkdtemp(prefix="ci-repo-"))
        self.repo = make_repo(self._repo_tmp)
        return {"repo_root": self.repo, "graphify_bin": os.environ.get("CROWN_INFINITY_GRAPHIFY")}

    def tearDown(self):
        super().tearDown()
        import shutil
        shutil.rmtree(self._repo_tmp, ignore_errors=True)

    def owner_actor(self):
        return Actor("USER", self.ids["owner"]["id"], "OWNER")

    def import_fixture_map(self, project=None):
        conn = self.h.db()
        try:
            sha = git(self.repo, "rev-parse", "HEAD")
            files = [{"path": "mod_a.py", "sha256": hashlib.sha256(MOD_A.encode()).hexdigest()}]
            return GM.import_graph(conn, self.owner_actor(), project or self.ids["p1"], fixture_graph(), sha,
                                   "crown_infinity", "0.9.103", files, "fixture")
        finally:
            conn.close()

    def add_decision(self, content=FIRST_DECISION, project=None):
        st, d, _ = self.owner.post("/api/v1/decisions", {"project_id": project or self.ids["p1"], "title": "Ưu tiên nền tảng",
                                                         "content": content, "source": "Tin nhắn của Chủ tịch 2026-11-06"})
        self.assertEqual(st, 201, d)
        return d

    def agent_with_task(self, agent_id="A14", project=None, permission="READ"):
        conn = self.h.db()  # repo tạm không có registries/ → tạo danh tính Agent trực tiếp
        try:
            OPS.ensure_agent(conn, agent_id, f"Agent {agent_id}")
        finally:
            conn.close()
        st, t, _ = self.owner.post("/api/v1/tasks", {"title": "Đọc bản đồ", "project_id": project or self.ids["p1"]})
        st, g, _ = self.owner.post("/api/v1/agent-grants", {"agent_id": agent_id, "task_id": t["id"], "permission": permission})
        self.assertEqual(st, 201, g)
        st, tok, _ = self.owner.post(f"/api/v1/agents/{agent_id}/token")
        c = self.h.client()
        return c, {"Authorization": f"Bearer {tok['token']}"}, t, g


class TDecisions(OpsBase):
    def test_owner_decision_lifecycle_and_history(self):
        d = self.add_decision()
        self.assertEqual((d["kind"], d["status"], d["confirmed_by"]), ("DECISION", "ACTIVE", self.ids["owner"]["id"]))
        self.assertEqual(d["label"], "QUYẾT ĐỊNH ĐÃ XÁC NHẬN")
        self.assertEqual(d["content"], FIRST_DECISION)
        st, e, _ = self.owner.patch(f"/api/v1/decisions/{d['id']}", {"action": "EDIT", "content": FIRST_DECISION + " (sửa)"})
        self.assertEqual(st, 428)
        st, e, _ = self.owner.patch(f"/api/v1/decisions/{d['id']}", {"action": "EDIT", "content": FIRST_DECISION + " (sửa)"},
                                    headers=H(d["revision"]))
        self.assertEqual((st, e["revision"]), (200, 2))
        st, r, _ = self.owner.patch(f"/api/v1/decisions/{d['id']}", {"action": "REVOKE", "reason": "đổi ưu tiên"},
                                    headers=H(e["revision"]))
        self.assertEqual((st, r["status"]), (200, "REVOKED"))
        st, act, _ = self.owner.get(f"/api/v1/decisions?project_id={self.ids['p1']}&status=ACTIVE")
        self.assertEqual(act["items"], [])
        st, hist, _ = self.owner.get(f"/api/v1/decisions/{d['id']}/history")
        self.assertEqual([x["action"] for x in hist["items"]], ["CREATE", "EDIT", "REVOKE"])
        self.assertEqual(hist["items"][0]["snapshot"]["content"], FIRST_DECISION, "bản cũ giữ nguyên trong lịch sử")

    def test_permissions_and_db_constraints(self):
        d = self.add_decision()
        editor, viewer = self.h.client(), self.h.client()
        editor.login("bientap", EDITOR_PW)
        viewer.login("nguoixem", VIEWER_PW)
        self.assertEqual(editor.post("/api/v1/decisions", {"project_id": self.ids["p1"], "title": "x", "content": "x",
                                                           "source": "x"})[0], 403)
        self.assertEqual(viewer.get(f"/api/v1/decisions?project_id={self.ids['p1']}")[0], 200)
        self.assertEqual(editor.get(f"/api/v1/decisions/{d['id']}/history")[0], 403)
        self.assertEqual(editor.patch(f"/api/v1/decisions/{d['id']}", {"action": "REVOKE", "reason": "x"},
                                      headers=H(1))[0], 403)
        self.assertEqual(editor.get(f"/api/v1/decisions?project_id={self.ids['p2']}")[0], 404)
        conn = self.h.db()
        try:
            with self.assertRaises(sqlite3.IntegrityError):  # quyết định không có người xác nhận
                conn.execute("INSERT INTO decisions(id,project_id,kind,status,title,content,source,decided_at,"
                             "proposed_by_type,proposed_by,created_at,updated_at) VALUES ('D-x',?,'DECISION','ACTIVE',"
                             "'t','c','s','2026','AGENT','A14','2026','2026')", (self.ids["p1"],))
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("DELETE FROM decisions")
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("UPDATE decision_history SET note='x'")
        finally:
            conn.close()

    def test_decision_survives_restart(self):
        d = self.add_decision()
        self.h.stop()
        h2 = ServerHarness(self.h.data_dir, repo_root=self.repo)
        try:
            c = h2.client()
            c.login("loc", OWNER_PW)
            st, lst, _ = c.get(f"/api/v1/decisions?project_id={self.ids['p1']}")
            self.assertEqual([(x["id"], x["content"]) for x in lst["items"]], [(d["id"], FIRST_DECISION)])
        finally:
            h2.stop()
            self.h = ServerHarness(self.h.data_dir, repo_root=self.repo)

    def test_ai_proposal_is_distinct_until_owner_confirms(self):
        agent, auth, task, grant = self.agent_with_task(permission="READ")
        body = {"title": "Đề xuất", "content": "Nên thêm test Windows", "source": "rà soát RV-x"}
        st, b, _ = agent.post(f"/api/v1/agent/tasks/{task['id']}/proposals", body, headers=auth)
        self.assertEqual((st, b["error"]["code"]), (403, "READ_ONLY"))
        agent2, auth2, task2, _ = self.agent_with_task(agent_id="A20", permission="READ_PROPOSE")
        st, p, _ = agent2.post(f"/api/v1/agent/tasks/{task2['id']}/proposals", dict(body, ai_model="mô hình thử"),
                               headers=auth2)
        self.assertEqual((st, p["kind"], p["status"], p["confirmed_by"]), (201, "PROPOSAL", "PROPOSED", None))
        self.assertIn("CHƯA XÁC NHẬN", p["label"])
        st, act, _ = self.owner.get(f"/api/v1/decisions?project_id={self.ids['p1']}&status=ACTIVE")
        self.assertEqual(act["items"], [], "đề xuất không được tính là quyết định")
        st, c, _ = self.owner.patch(f"/api/v1/decisions/{p['id']}", {"action": "CONFIRM"}, headers=H(p["revision"]))
        self.assertEqual((c["kind"], c["status"], c["confirmed_by"]), ("DECISION", "ACTIVE", self.ids["owner"]["id"]))
        st, hist, _ = self.owner.get(f"/api/v1/decisions/{p['id']}/history")
        self.assertEqual([(x["action"], x["actor_type"]) for x in hist["items"]], [("PROPOSE", "AGENT"), ("CONFIRM", "USER")])


class TGraph(OpsBase):
    def test_query_relation_with_provenance_and_open_source_at_commit(self):
        m = self.import_fixture_map()
        st, q, _ = self.owner.get(f"/api/v1/graph/maps/{m['id']}/query?node=helper")
        self.assertEqual(st, 200)
        self.assertEqual(q["node"]["node_id"], "mod_a_helper")
        rels = {(r["direction"], r["relation"], r["other"]["node_id"], r["confidence"]) for r in q["relations"]}
        self.assertIn(("in", "calls", "mod_a_main", "EXTRACTED"), rels)
        self.assertIn(("out", "uses", "mod_a_main", "INFERRED"), rels)
        # tệp trên đĩa đổi sau commit: vẫn mở đúng nội dung ở commit của bản đồ
        (self.repo / "crown_infinity" / "mod_a.py").write_text("# đã đổi\n", encoding="utf-8")
        st, src, _ = self.owner.get(f"/api/v1/graph/maps/{m['id']}/source?path=mod_a.py&line=6")
        self.assertEqual(st, 200, src)
        self.assertEqual([x["text"] for x in src["lines"] if x["n"] == 6], ["    return helper()"])
        self.assertIn("git show", src["origin"])
        for bad in ("../secret.txt", "/etc/passwd", "khong_co.py"):
            self.assertEqual(self.owner.get(f"/api/v1/graph/maps/{m['id']}/source?path={bad}")[0], 404, bad)
        editor = self.h.client()
        editor.login("bientap", EDITOR_PW)
        m2 = self.import_fixture_map(self.ids["p2"])
        self.assertEqual(editor.get(f"/api/v1/graph/maps/{m2['id']}/query?node=helper")[0], 404)

    @unittest.skipUnless(os.environ.get("CROWN_INFINITY_GRAPHIFY"), "cần CROWN_INFINITY_GRAPHIFY (graphifyy)")
    def test_real_graphify_build(self):
        st, m, _ = self.owner.post("/api/v1/graph/maps/build", {"project_id": self.ids["p1"]})
        self.assertEqual(st, 201, m)
        self.assertEqual(m["source_commit"], git(self.repo, "rev-parse", "HEAD"))
        self.assertGreaterEqual(m["edge_count"], 1)
        self.assertIn("GRAPHIFY_BACKEND=none", m["notes"])
        st, q, _ = self.owner.get(f"/api/v1/graph/maps/{m['id']}/query?node=helper")
        self.assertTrue(any(r["relation"] == "calls" and r["other"]["label"].startswith("main") for r in q["relations"]), q)


class TAgentScope(OpsBase):
    def test_agent_reads_only_granted_project_and_task(self):
        m1 = self.import_fixture_map()
        m2 = self.import_fixture_map(self.ids["p2"])
        self.add_decision()
        agent, auth, task, grant = self.agent_with_task()
        st, g, _ = agent.get("/api/v1/agent/grants", headers=auth)
        self.assertEqual([x["task_id"] for x in g["items"]], [task["id"]])
        st, q, _ = agent.get(f"/api/v1/agent/tasks/{task['id']}/graph/query?node=helper", headers=auth)
        self.assertEqual((st, q["map_id"]), (200, m1["id"]))
        st, src, _ = agent.get(f"/api/v1/agent/tasks/{task['id']}/graph/source?path=mod_a.py&line=1", headers=auth)
        self.assertEqual((st, src["lines"][0]["text"]), (200, "def helper():"))
        self.assertEqual(agent.get(f"/api/v1/agent/tasks/{task['id']}/graph/query?node=helper&map_id={m2['id']}",
                                   headers=auth)[0], 403)
        st, d, _ = agent.get(f"/api/v1/agent/tasks/{task['id']}/decisions", headers=auth)
        self.assertEqual([x["content"] for x in d["items"]], [FIRST_DECISION])
        st, t2, _ = self.owner.post("/api/v1/tasks", {"title": "khác", "project_id": self.ids["p2"]})
        self.assertEqual(agent.get(f"/api/v1/agent/tasks/{t2['id']}/decisions", headers=auth)[0], 403)
        self.owner.patch(f"/api/v1/tasks/{task['id']}", {"status": "DONE"}, headers=H(task["revision"]))
        self.assertEqual(agent.get(f"/api/v1/agent/tasks/{task['id']}/decisions", headers=auth)[0], 403,
                         "nhiệm vụ đóng thì hết quyền")


class TReview(OpsBase):
    def test_manual_review_report_labels_and_duplicate_guard(self):
        self.add_decision()
        self.import_fixture_map()
        st, r, _ = self.owner.post("/api/v1/ops/reviews", {"project_id": self.ids["p1"]})
        self.assertEqual((st, r["status"], r["ai_calls"]), (201, "SUCCEEDED", 0), r)
        self.assertTrue(r["id"].startswith("RV-") and r["task_id"].startswith("T-"))
        self.assertTrue(all(not s["ai_called"] for s in r["steps"]))
        head = git(self.repo, "rev-parse", "HEAD")
        txt = json.dumps(r["report"], ensure_ascii=False)
        self.assertIn(head[:12], txt)
        self.assertIn("không thấy tệp đổi nào có tên liên quan", txt)
        self.assertTrue(any("khẳng định" in x["text"] for x in r["report"]["reported"]))
        self.assertTrue(any("quan hệ mẫu" in x["text"].lower() and "EXTRACTED" in x["text"] for x in r["report"]["verified"]))
        owners = {a["owner"] for a in r["report"]["next_actions"]}
        self.assertIn("CHAIRMAN", owners, "mục NOT VERIFIED giao Chủ tịch chạy trên Windows")
        conn = self.h.db()
        try:
            t = conn.execute("SELECT * FROM tasks WHERE id=?", (r["task_id"],)).fetchone()
            self.assertEqual((t["assignee"], t["status"], t["project_id"]), ("VUA_CEO", "DONE", self.ids["p1"]))
            g = conn.execute("SELECT * FROM agent_grants WHERE task_id=?", (r["task_id"],)).fetchone()
            self.assertEqual((g["permission"], g["agent_id"]), ("READ", "VUA_CEO"))
            self.assertIsNotNone(g["revoked_at"], "quyền đọc thu hồi khi xong")
        finally:
            conn.close()
        st, r2, _ = self.owner.post("/api/v1/ops/reviews", {"project_id": self.ids["p1"]})
        self.assertEqual((r2["status"], r2["duplicate_of"]), ("SKIPPED_DUPLICATE", r["id"]))
        # commit mới → đầu vào mới → chạy thật
        (self.repo / "crown_infinity" / "mod_b.py").write_text("x = 1\n", encoding="utf-8")
        git(self.repo, "add", "-A")
        git(self.repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "thêm mod_b")
        st, r3, _ = self.owner.post("/api/v1/ops/reviews", {"project_id": self.ids["p1"]})
        self.assertEqual(r3["status"], "SUCCEEDED")
        self.assertTrue(any("CŨ hơn" in x["text"] for x in r3["report"]["verified"]))
        # quyết định đổi → đầu vào mới
        self.add_decision("Quyết định thứ hai")
        st, r4, _ = self.owner.post("/api/v1/ops/reviews", {"project_id": self.ids["p1"]})
        self.assertEqual(r4["status"], "SUCCEEDED")

    def test_review_permissions(self):
        editor = self.h.client()
        editor.login("bientap", EDITOR_PW)
        self.assertEqual(editor.post("/api/v1/ops/reviews", {"project_id": self.ids["p1"]})[0], 403)
        st, r, _ = self.owner.post("/api/v1/ops/reviews", {"project_id": self.ids["p2"]})
        self.assertEqual(editor.get(f"/api/v1/ops/reviews/{r['id']}")[0], 404)
        self.assertEqual(editor.put("/api/v1/ops/schedule", {"project_id": self.ids["p1"], "enabled": False})[0], 403)

    def test_ai_models_selected_but_no_real_call(self):
        st, paid, _ = self.owner.post("/api/v1/ai/providers", {"name": "Claude", "kind": "anthropic", "model": "x",
                                                               "key_env": "CI_NO_SUCH_KEY", "enabled": True})
        st, local, _ = self.owner.post("/api/v1/ai/providers", {"name": "Máy cục bộ", "kind": "ollama", "model": "y",
                                                                "paid": False, "enabled": True})
        self.owner.put("/api/v1/ai/settings", {"enabled": True, "budget": {"max_output_tokens": 2000}})
        st, r, _ = self.owner.post("/api/v1/ops/reviews", {"project_id": self.ids["p1"], "analysis_provider_id": paid["id"],
                                                           "verify_provider_id": local["id"]})
        self.assertEqual((st, r["status"], r["ai_calls"]), (201, "SUCCEEDED", 0))
        steps = {s["name"]: s for s in r["steps"]}
        self.assertFalse(steps["AI phân tích"]["ai_called"])
        self.assertIn("chặn", steps["AI phân tích"]["note"])
        self.assertFalse(steps["AI kiểm tra lại"]["ai_called"])
        self.assertIn("NOT_IMPLEMENTED", steps["AI kiểm tra lại"]["note"])
        conn = self.h.db()
        try:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM ai_usage WHERE status IN ('SUCCEEDED','FAILED')").fetchone()[0], 0)
        finally:
            conn.close()
        st, ai, _ = self.owner.get("/api/v1/ai")
        self.assertEqual(ai["budget"]["daily_usd"], 0, "ngân sách giữ nguyên 0")


class TSchedule(OpsBase):
    def test_schedule_requires_manual_success_runs_and_skips_duplicates(self):
        P = self.ids["p1"]
        st, sc, _ = self.owner.get(f"/api/v1/ops/schedule?project_id={P}")
        self.assertEqual((sc["enabled"], sc["interval_minutes"], sc["can_enable"]), (0, 120, False))
        st, b, _ = self.owner.put("/api/v1/ops/schedule", {"project_id": P, "enabled": True})
        self.assertEqual((st, b["error"]["code"]), (409, "MANUAL_RUN_REQUIRED"))
        st, r, _ = self.owner.post("/api/v1/ops/reviews", {"project_id": P})
        st, sc, _ = self.owner.put("/api/v1/ops/schedule", {"project_id": P, "enabled": True})
        self.assertEqual((st, sc["enabled"]), (200, 1))
        self.assertIsNotNone(sc["next_run_at"])
        future = iso_after(121 * 60)
        res = []
        ts = [threading.Thread(target=lambda: res.extend(OPS.scheduler_tick(self.h.cfg.db_path, self.repo, future)))
              for _ in range(3)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        self.assertEqual(len(res), 1, "ba nhịp đồng thời chỉ giành được MỘT lượt")
        self.assertEqual(res[0]["status"], "SKIPPED_DUPLICATE", "cùng commit + cùng đầu vào với lượt thủ công")
        st, sc, _ = self.owner.get(f"/api/v1/ops/schedule?project_id={P}")
        self.assertEqual(sc["last_status"], "SKIPPED_DUPLICATE")
        self.assertGreater(sc["next_run_at"], future[:16])
        self.assertEqual(sc["service"]["status"], "ONLINE")
        st, runs, _ = self.owner.get(f"/api/v1/ops/reviews?project_id={P}")
        self.assertEqual([x["trigger"] for x in runs["items"]], ["SCHEDULE", "MANUAL"])
        st, sc, _ = self.owner.put("/api/v1/ops/schedule", {"project_id": P, "enabled": False})
        self.assertEqual((sc["enabled"], sc["next_run_at"]), (0, None))
