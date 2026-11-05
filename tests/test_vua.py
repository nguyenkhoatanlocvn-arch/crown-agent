import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

from crown import registry as R
from crown.tasks import TaskError
from crown.vua import Vua

ROOT = Path(__file__).resolve().parent.parent


class Clock:
    def __init__(self):
        self.t = datetime(2026, 11, 5, 15, 0, tzinfo=timezone.utc)

    def __call__(self):
        return self.t

    def advance(self, s):
        self.t += timedelta(seconds=s)


REQ = dict(request_id="VT-TEST-0001", objective="Thẩm định skill X", scope={"domain": "SKILL"},
           acceptance=["Có link SKILL.md đúng commit", "Có 1 ví dụ đạt, 1 ví dụ trượt"], issued_by="CHAIRMAN",
           mode="TEST")
PLAN = [
    {"key": "S1", "owner": "A03", "objective": "Thẩm định", "output": "báo cáo", "acceptance": "đủ 4 mục"},
    {"key": "S2", "owner": "A03", "objective": "Đồng bộ tab SKILL", "output": "biên nhận", "acceptance": "đọc lại được",
     "depends_on": ["S1"]},
]


class VuaTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.clock = Clock()
        self.v = Vua(self.dir / "t.db", registry=R.load(), clock=self.clock)
        self.addCleanup(self.v.close)

    # --- quyền Vương Tọa ---
    def test_only_delegated_principal_can_issue(self):
        with self.assertRaisesRegex(TaskError, "chưa được ủy nhiệm"):
            self.v.intake(**{**REQ, "issued_by": "VUA_CEO"})
        with self.assertRaisesRegex(TaskError, "không có quyền"):
            self.v.intake(**{**REQ, "issued_by": "A03"})
        with self.assertRaisesRegex(TaskError, "chỉ Chủ tịch"):
            self.v.intake(**{**REQ, "issued_by": "VUA_CEO", "scope": {"domain": "MONEY"}})

    # --- gửi lại cùng request_id ---
    def test_same_request_returns_same_task(self):
        a = self.v.intake(**REQ)
        b = self.v.intake(**REQ)
        self.assertFalse(a["duplicate"])
        self.assertTrue(b["duplicate"])
        self.assertEqual(a["root"]["task_id"], b["root"]["task_id"])
        self.assertEqual(len(self.v.store.list()), 1)

    def test_same_request_id_different_content_rejected(self):
        self.v.intake(**REQ)
        with self.assertRaisesRegex(TaskError, "nội dung khác"):
            self.v.intake(**{**REQ, "objective": "việc khác"})

    # --- kế hoạch ---
    def test_plan_requires_owner_output_acceptance_and_no_cycle(self):
        self.v.intake(**REQ)
        with self.assertRaisesRegex(TaskError, "thiếu 'output'"):
            self.v.plan(request_id=REQ["request_id"], subtasks=[{**PLAN[0], "output": ""}])
        cyc = [{**PLAN[0], "depends_on": ["S2"]}, PLAN[1]]
        with self.assertRaisesRegex(TaskError, "vòng phụ thuộc"):
            self.v.plan(request_id=REQ["request_id"], subtasks=cyc)
        with self.assertRaisesRegex(TaskError, "chưa kích hoạt"):
            self.v.plan(request_id=REQ["request_id"], subtasks=[{**PLAN[0], "owner": "A01"}])

    def test_plan_is_reused_and_dependency_enforced(self):
        self.v.intake(**REQ)
        p1 = self.v.plan(request_id=REQ["request_id"], subtasks=PLAN)
        p2 = self.v.plan(request_id=REQ["request_id"], subtasks=PLAN)
        self.assertTrue(p2["reused"])
        self.assertEqual(p1["map"], p2["map"])
        with self.assertRaisesRegex(TaskError, "chưa VERIFIED_DONE"):
            self.v.claim(request_id=REQ["request_id"], key="S2", worker="A03")

    # --- ngắt giữa chừng rồi tiếp tục ---
    def test_resume_same_worker_keeps_token_and_evidence(self):
        self.v.intake(**REQ)
        self.v.plan(request_id=REQ["request_id"], subtasks=PLAN)
        c1 = self.v.claim(request_id=REQ["request_id"], key="S1", worker="A03")
        self.v.evidence(request_id=REQ["request_id"], key="S1", worker="A03", token=c1["token"], evidence=["link"])
        # phiên chết ở đây; phiên mới chạy lại cùng request
        c2 = self.v.claim(request_id=REQ["request_id"], key="S1", worker="A03")
        self.assertTrue(c2["resumed"])
        self.assertEqual(c1["token"], c2["token"])
        st = self.v.status(REQ["request_id"])
        self.assertEqual(st["children"]["S1"]["retry_count"], 0)
        self.assertEqual(st["children"]["S1"]["evidence"], ["link"])

    def test_resume_after_lease_expiry_counts_retry(self):
        self.v.intake(**REQ)
        self.v.plan(request_id=REQ["request_id"], subtasks=PLAN)
        c1 = self.v.claim(request_id=REQ["request_id"], key="S1", worker="A03", ttl=60)
        self.clock.advance(120)
        c2 = self.v.claim(request_id=REQ["request_id"], key="S1", worker="A03")
        self.assertFalse(c2["resumed"])
        self.assertGreater(c2["token"], c1["token"])
        self.assertEqual(self.v.status(REQ["request_id"])["children"]["S1"]["retry_count"], 1)

    # --- duyệt ---
    def test_reviewer_must_be_independent_and_checks_consistent(self):
        self.v.intake(**REQ)
        self.v.plan(request_id=REQ["request_id"], subtasks=PLAN)
        c = self.v.claim(request_id=REQ["request_id"], key="S1", worker="A03")
        self.v.evidence(request_id=REQ["request_id"], key="S1", worker="A03", token=c["token"], evidence=["e"])
        self.v.move(request_id=REQ["request_id"], key="S1", to="REVIEW", actor="A03", token=c["token"])
        ok = [{"text": "link đúng", "passed": True, "evidence": "sha khớp"}]
        with self.assertRaisesRegex(TaskError, "tự duyệt|người khác|không có thẩm quyền"):
            self.v.review(request_id=REQ["request_id"], key="S1", reviewer="A03", verdict="PASS", checks=ok)
        with self.assertRaisesRegex(TaskError, "trượt nhưng kết luận PASS"):
            self.v.review(request_id=REQ["request_id"], key="S1", reviewer="A20", verdict="PASS",
                          checks=[{"text": "x", "passed": False, "evidence": "y"}])
        r = self.v.review(request_id=REQ["request_id"], key="S1", reviewer="A20", verdict="PASS", checks=ok)
        self.assertEqual(r["state"], "VERIFIED_DONE")

    def test_waiting_approval_needs_valid_decision(self):
        self.v.intake(**REQ)
        self.v.plan(request_id=REQ["request_id"], subtasks=PLAN)
        c = self.v.claim(request_id=REQ["request_id"], key="S1", worker="A03")
        self.v.move(request_id=REQ["request_id"], key="S1", to="WAITING_APPROVAL", actor="A03", token=c["token"])
        with self.assertRaisesRegex(TaskError, "phải ghi nội dung"):
            self.v.move(request_id=REQ["request_id"], key="S1", to="READY", actor="CHAIRMAN")
        with self.assertRaisesRegex(TaskError, "không có thẩm quyền chấp thuận"):
            self.v.move(request_id=REQ["request_id"], key="S1", to="READY", actor="A20", approval="ok")
        r = self.v.move(request_id=REQ["request_id"], key="S1", to="READY", actor="CHAIRMAN", approval="QD-01")
        self.assertEqual(r["state"], "READY")

    # --- Hội đồng: hạn mức CFO ---
    def test_council_session_limit_counts_retries_and_reserves_conclusion(self):
        self.v.intake(**REQ)
        g = lambda kind="call": self.v.council_gate(request_id=REQ["request_id"], session_id="s1", provider="gpt",
                                                    kind=kind, connected=True, mode="TEST")
        got = [g("retry" if i % 2 else "call")["ok"] for i in range(11)]
        self.assertEqual(got, [True] * 11)
        last = g()
        self.assertFalse(last["ok"])
        self.assertIn("chừa 1 lượt kết luận", last["reason"])
        self.assertTrue(g("conclusion")["ok"])          # lượt thứ 12 dành cho kết luận
        self.assertFalse(g("conclusion")["ok"])         # hết hẳn 12/12

    def test_council_daily_limit_paid_and_missing_connection(self):
        self.v.intake(**REQ)
        for s in range(4):
            for _ in range(10):
                r = self.v.council_gate(request_id=REQ["request_id"], session_id=f"s{s}", provider="gpt",
                                        connected=True, mode="TEST")
        self.assertFalse(r["ok"])  # 39 lượt thường + chừa 1 kết luận = 40/ngày
        self.assertIn("ngày", r["reason"])
        p = self.v.council_gate(request_id=REQ["request_id"], session_id="x", provider="deepseek", billing="paid",
                                usd=0.02, connected=True, mode="LIVE")
        self.assertFalse(p["ok"])
        self.assertIn("trả phí", p["reason"])
        n = self.v.council_gate(request_id=REQ["request_id"], session_id="x", provider="grok", connected=False)
        self.assertFalse(n["ok"])
        self.assertIn("chưa có kết nối", n["reason"])

    # --- outbox: đối soát trước khi thử lại ---
    def test_outbox_test_data_cannot_reach_real_screen(self):
        self.v.intake(**REQ)
        with self.assertRaisesRegex(TaskError, "kiểm thử"):
            self.v.outbox_put(request_id=REQ["request_id"], op_id="o1", target="hoang-thanh", payload={"a": 1})

    def test_outbox_flow_requires_receipt_and_reconcile(self):
        self.v.intake(**{**REQ, "mode": "LIVE", "request_id": "VT-LIVE-X"})
        o = self.v.outbox_put(request_id="VT-LIVE-X", op_id="o1", target="hoang-thanh", payload={"a": 1})
        self.assertEqual(o["status"], "PENDING")
        self.assertEqual(self.v.outbox_put(request_id="VT-LIVE-X", op_id="o1", target="hoang-thanh",
                                           payload={"a": 1})["status"], "PENDING")  # đặt lại không nhân đôi
        self.v.outbox_mark(op_id="o1", status="SENDING")
        u = self.v.outbox_mark(op_id="o1", status="UNKNOWN", reason="mất kết nối sau khi gửi")
        self.assertTrue(u["needs_reconcile"])
        with self.assertRaisesRegex(TaskError, "UNKNOWN → SENDING"):
            self.v.outbox_mark(op_id="o1", status="SENDING")   # không được gửi lại khi chưa đối soát
        with self.assertRaisesRegex(TaskError, "biên nhận"):
            self.v.outbox_mark(op_id="o1", status="CONFIRMED")
        c = self.v.outbox_mark(op_id="o1", status="CONFIRMED", receipt="skill_state v26 có run VT-LIVE-X")
        self.assertFalse(c["needs_reconcile"])

    # --- khóa lượt chạy ---
    def test_run_lock_blocks_second_run_until_expiry_or_takeover(self):
        self.v.intake(**REQ)
        rid = REQ["request_id"]
        a = self.v.lock(request_id=rid, ttl=60)
        self.assertTrue(a["acquired"])
        self.assertFalse(self.v.lock(request_id=rid, ttl=60)["acquired"])          # lượt thứ hai bị chặn
        self.assertTrue(self.v.lock(request_id=rid, ttl=60, nonce=a["nonce"])["renewed"])
        t = self.v.lock(request_id=rid, ttl=60, takeover=True)                      # Vương Tọa cưỡng chế
        self.assertTrue(t["took_over"])
        self.clock.advance(120)
        self.assertTrue(self.v.lock(request_id=rid, ttl=60)["acquired"])            # hết hạn → lượt mới nhận
        self.assertFalse(self.v.unlock(request_id=rid, nonce=a["nonce"])["released"])  # nonce cũ không mở được

    # --- tra cứu khi phòng ngủ đông ---
    def test_lookup_works_for_done_task(self):
        self.v.intake(**REQ)
        rid = REQ["request_id"]
        self.v.plan(request_id=rid, subtasks=PLAN[:1])
        c = self.v.claim(request_id=rid, key="S1", worker="A03")
        self.v.evidence(request_id=rid, key="S1", worker="A03", token=c["token"], evidence=["link@sha"])
        self.v.move(request_id=rid, key="S1", to="REVIEW", actor="A03", token=c["token"])
        self.v.review(request_id=rid, key="S1", reviewer="A20", verdict="PASS",
                      checks=[{"text": "t", "passed": True, "evidence": "e"}])
        tid = self.v.status(rid)["children"]["S1"]["task_id"]
        self.assertEqual(self.v.doors(["A03"])["A03"]["activity"], "IDLE")
        self.assertEqual(self.v.lookup(tid)["evidence"], ["link@sha"])
        self.assertEqual(self.v.lookup(self.v.status(rid)["root"]["task_id"])["request_id"], rid)
        self.assertEqual(self.v.doors(["A01"])["A01"]["door"], "phòng tối — ngủ đông")


class VuaCliTest(unittest.TestCase):
    def _run(self, db, cmd, obj):
        return subprocess.Popen([sys.executable, "-m", "crown", "vua", cmd, "--q", quote(json.dumps(obj, ensure_ascii=False))],
                                cwd=ROOT, env={**os.environ, "CROWN_DB": str(db)}, stdout=subprocess.PIPE, text=True)

    def test_concurrent_duplicate_submissions_make_one_task(self):
        db = Path(tempfile.mkdtemp()) / "c.db"
        procs = [self._run(db, "intake", REQ) for _ in range(6)]
        outs = [json.loads(p.communicate()[0]) for p in procs]
        self.assertTrue(all(o["ok"] for o in outs), outs)
        roots = {o["result"]["root"]["task_id"] for o in outs}
        self.assertEqual(len(roots), 1)
        self.assertEqual(sum(not o["result"]["duplicate"] for o in outs), 1)
        import sqlite3
        with sqlite3.connect(db) as c:   # không có nhiệm vụ mồ côi do tạo trùng
            self.assertEqual(c.execute("SELECT COUNT(*) FROM tasks").fetchone()[0], 1)

    def test_cli_error_is_json(self):
        db = Path(tempfile.mkdtemp()) / "e.db"
        out = json.loads(self._run(db, "status", {"request_id": "nope"}).communicate()[0])
        self.assertFalse(out["ok"])
        self.assertIn("Không có request", out["error"])


if __name__ == "__main__":
    unittest.main()
