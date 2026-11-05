"""Bảng nhiệm vụ D05 (lệnh DRAGON SINGULARITY mục 07): 17 trường, 11 trạng thái, không tạo trùng."""
import shutil
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from crown import cycle as C
from crown import policy as P
from crown import registry as R
from crown import taskboard as T

FIELDS = ["TASK_ID", "TASK_NAME", "PROJECT", "OWNER_AGENT", "ASSIGNED_MODEL", "PRIORITY", "STATUS", "DEPENDENCIES",
          "ESTIMATED_COST", "ACTUAL_COST", "DEADLINE", "START_TIME", "COMPLETED_TIME", "QUALITY_SCORE",
          "TEST_EVIDENCE", "RISK_LEVEL", "APPROVAL_STATUS"]
NOW = datetime(2026, 11, 5, 17, 17, 5, tzinfo=timezone.utc)


class Clock:
    def __init__(self):
        self.now = NOW

    def __call__(self):
        return self.now


class Base(unittest.TestCase):
    approvals = None

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.reg = R.load()
        self.clock = Clock()
        import json
        matrix = json.loads(P.MATRIX_FILE.read_text(encoding="utf-8"))
        self.pol = P.Policy(self.reg, matrix, self.approvals, clock=self.clock)
        self.tb = T.TaskBoard(self.tmp / "tn.db", registry=self.reg, policy=self.pol, clock=self.clock)
        self.addCleanup(self.tb.close)

    def make(self, **over):
        args = dict(name="Sửa lỗi đồng bộ bảng nhiệm vụ", objective="Bảng trên web khớp sổ", acceptance="đọc lại khớp",
                    seat="TN-05", project="HOANG_THANH", priority="P1", risk_level="LOW", work_type="CODE",
                    action_class="CODE_BRANCH_WRITE", dedupe_key="web-sync-001")
        args.update(over)
        return self.tb.create(**args)

    def status(self, tid):
        return self.tb.row(tid)["STATUS"]


class FieldsTest(Base):
    def test_row_has_all_seventeen_fields(self):
        r = self.tb.row(self.make(deadline="2026-11-07T00:00:00+00:00", estimated_cost=0)["task_id"])
        for f in FIELDS:
            self.assertIn(f, r)
        self.assertEqual((r["TASK_NAME"], r["PROJECT"], r["OWNER_AGENT"], r["PRIORITY"], r["RISK_LEVEL"]),
                         ("Sửa lỗi đồng bộ bảng nhiệm vụ", "HOANG_THANH", "A18", "P1", "LOW"))
        self.assertEqual((r["STATUS"], r["ASSIGNED_MODEL"], r["APPROVAL_STATUS"], r["DEPENDENCIES"]),
                         ("BACKLOG", None, "NOT_REQUIRED", []))

    def test_seat_resolves_to_holder_or_to_itself(self):
        self.assertEqual(self.tb.row(self.make(seat="TN-02", dedupe_key="a", action_class="TASK_MANAGE")["task_id"])["OWNER_AGENT"], "A10")
        self.assertEqual(self.tb.row(self.make(seat="TN-07", dedupe_key="b", action_class="REPORT_READ")["task_id"])["OWNER_AGENT"], "TN-07")

    def test_rule_engine_unit_can_own_a_task_without_a_seat(self):
        r = self.tb.row(self.make(seat=None, owner="TN-ENGINE", action_class="STATE_WRITE", work_type="PERIODIC_CHECK",
                                  dedupe_key="daily:2026-11-05")["task_id"])
        self.assertEqual((r["OWNER_AGENT"], r["seat"], r["STATUS"]), ("TN-ENGINE", None, "BACKLOG"))

    def test_owner_without_seat_must_be_a_unit_and_seat_and_owner_are_exclusive(self):
        for over in ({"seat": None, "owner": "A18"},            # Agent nhận việc qua ghế, không nhận trực tiếp
                     {"seat": None, "owner": "NOBODY"},
                     {"seat": None, "owner": None},
                     {"seat": "TN-05", "owner": "TN-ENGINE"}):
            with self.assertRaises(T.BoardError, msg=str(over)):
                self.make(dedupe_key="x-" + str(over), **over)

    def test_same_dedupe_key_never_creates_a_second_task(self):
        a = self.make()
        b = self.make(name="Tên khác nhưng cùng khóa")
        self.assertTrue(a["created"])
        self.assertFalse(b["created"])
        self.assertEqual(a["task_id"], b["task_id"])
        self.assertEqual(len(self.tb.board()), 1)

    def test_bad_values_are_rejected(self):
        for over in ({"priority": "P9"}, {"risk_level": "EXTREME"}, {"work_type": "MAGIC"}, {"seat": "TN-99"},
                     {"dedupe_key": ""}, {"name": " "}):
            with self.assertRaises(T.BoardError, msg=str(over)):
                self.make(**over)

    def test_owner_must_have_the_right_for_the_action(self):
        with self.assertRaises(P.PolicyError):
            self.make(seat="TN-08", action_class="CODE_BRANCH_WRITE", dedupe_key="x")   # A04 chỉ có quyền đọc
        with self.assertRaises(P.PolicyError):
            self.make(action_class="CONSTITUTION_CHANGE", dedupe_key="y")               # chỉ Chủ tịch


class StatusTest(Base):
    def test_happy_path_walks_through_all_seven_states(self):
        tid = self.make()["task_id"]
        self.assertEqual(self.status(tid), "BACKLOG")
        self.tb.plan(tid)
        self.assertEqual(self.status(tid), "PLANNED")
        self.tb.assign(tid, model="sonnet", tier="CODING", estimated_cost=0)
        self.assertEqual(self.status(tid), "ASSIGNED")
        tok = self.tb.store.claim(tid, "A18")
        self.assertEqual(self.status(tid), "RUNNING")
        self.tb.store.record(tid, "A18", tok, evidence="tests: 12/12 đạt")
        self.tb.store.move(tid, "REVIEW", "A18", token=tok)
        self.assertEqual(self.status(tid), "TESTING")
        self.tb.record_quality(tid, 0.95)
        self.assertEqual(self.status(tid), "REVIEW")
        self.clock.now += timedelta(minutes=9)
        self.tb.store.move(tid, "VERIFIED_DONE", "A20", note="PASS")
        r = self.tb.row(tid)
        self.assertEqual((r["STATUS"], r["QUALITY_SCORE"], r["ASSIGNED_MODEL"], r["TEST_EVIDENCE"]),
                         ("COMPLETED", 0.95, "sonnet", ["tests: 12/12 đạt"]))
        self.assertEqual(r["COMPLETED_TIME"], (NOW + timedelta(minutes=9)).isoformat())
        self.assertIsNotNone(r["START_TIME"])

    def test_failure_blocked_and_cancel_states(self):
        tid = self.make()["task_id"]
        self.tb.plan(tid)
        tok = self.tb.store.claim(tid, "A18")
        self.tb.store.move(tid, "FAILED", "A18", token=tok, note="hỏng")
        self.assertEqual(self.status(tid), "RETRY_PENDING")
        self.tb.store.move(tid, "READY", "THIEN_NAO")
        self.assertEqual(self.status(tid), "RETRY_PENDING")          # đang chờ nhận lại
        tok = self.tb.store.claim(tid, "A18")
        self.tb.store.move(tid, "BLOCKED", "A18", token=tok, note="thiếu quyền")
        self.assertEqual(self.status(tid), "BLOCKED")
        self.tb.store.move(tid, "CANCELLED", "THIEN_NAO")
        self.assertEqual(self.status(tid), "CANCELLED")

    def test_task_out_of_retries_shows_failed(self):
        tid = self.make()["task_id"]
        self.tb.plan(tid)
        for i in range(4):
            tok = self.tb.store.claim(tid, "A18")
            self.tb.store.move(tid, "FAILED", "A18", token=tok, note=f"hỏng {i}")
            if i < 3:
                self.tb.store.move(tid, "READY", "THIEN_NAO")
        self.assertEqual(self.status(tid), "FAILED")


class ApprovalTest(Base):
    approvals = {"approvals": [{"id": "AP-7", "action": "DEPLOY_HIGH_RISK", "principal": "A18", "granted_by": "CHAIRMAN",
                                "granted_at": "2026-11-05T00:00:00+00:00", "expires_at": "2026-11-09T00:00:00+00:00",
                                "scope": "đưa tab mới lên web"}]}

    def test_sensitive_task_waits_in_backlog_until_chairman_approves(self):
        tid = self.make(risk_level="HIGH", action_class="DEPLOY_HIGH_RISK", dedupe_key="deploy-1")["task_id"]
        self.assertEqual(self.tb.row(tid)["APPROVAL_STATUS"], "PENDING")
        self.assertEqual(self.tb.plan(tid), "NEEDS_APPROVAL")
        self.assertEqual(self.status(tid), "BACKLOG")
        self.tb.attach_approval(tid, "AP-7")
        self.assertEqual(self.tb.row(tid)["APPROVAL_STATUS"], "APPROVED")
        self.assertEqual(self.tb.plan(tid), "PLANNED")
        self.assertEqual(self.status(tid), "PLANNED")

    def test_wrong_approval_does_not_unlock(self):
        tid = self.make(risk_level="HIGH", action_class="SPEND", dedupe_key="spend-1")["task_id"]
        self.tb.attach_approval(tid, "AP-7")                        # phê duyệt cho việc khác
        self.assertEqual(self.tb.row(tid)["APPROVAL_STATUS"], "PENDING")
        self.assertEqual(self.tb.plan(tid), "NEEDS_APPROVAL")


class SelectAndSweepTest(Base):
    def setUp(self):
        super().setUp()
        self.cy = C.Cycle(self.tmp / "tn.db", registry=self.reg, policy=self.pol, clock=self.clock)
        self.addCleanup(self.cy.close)

    def test_select_orders_by_priority_then_deadline_and_skips_unmet_dependencies(self):
        a = self.make(dedupe_key="a", priority="P2")["task_id"]
        b = self.make(dedupe_key="b", priority="P0")["task_id"]
        c = self.make(dedupe_key="c", priority="P2", deadline="2026-11-06T00:00:00+00:00")["task_id"]
        d = self.make(dedupe_key="d", priority="P0", dependencies=[a])["task_id"]
        for t in (a, b, c, d):
            self.tb.plan(t)
        self.assertEqual(self.tb.select(limit=10), [b, c, a])       # d chờ a xong
        self.assertEqual(self.tb.select(limit=2), [b, c])

    def _fail(self, tid, owner):
        tok = self.tb.store.claim(tid, owner)
        self.tb.store.move(tid, "FAILED", owner, token=tok, note="hỏng")

    def test_failed_task_is_requeued_only_after_backoff(self):
        tid = self.make()["task_id"]
        self.tb.plan(tid)
        self._fail(tid, "A18")
        self.assertEqual(self.tb.sweep(self.cy)["requeued"], [])
        self.clock.now += timedelta(hours=2, seconds=1)
        self.assertEqual(self.tb.sweep(self.cy)["requeued"], [tid])
        self.assertEqual(self.tb.store.get(tid)["state"], "READY")

    def test_task_out_of_retries_goes_to_dead_letter_once(self):
        tid = self.make()["task_id"]
        self.tb.plan(tid)
        for i in range(4):
            self._fail(tid, "A18")
            self.clock.now += timedelta(days=2)
            self.tb.sweep(self.cy)
        self.assertEqual([(d["kind"], d["ref"]) for d in self.cy.dlq_open()], [("task", tid)])
        self.assertEqual(self.tb.store.get(tid)["state"], "FAILED")
        self.tb.sweep(self.cy)
        self.assertEqual(len(self.cy.dlq_open()), 1)

    def test_failed_task_outside_thien_nao_authority_waits_for_chairman(self):
        t = self.tb.store.create(owner_agent_id="A20", objective="kiểm định", acceptance_criteria="xong",
                                 actor="A20", ready=True)
        self._fail(t["task_id"], "A20")
        self.clock.now += timedelta(days=1)
        out = self.tb.sweep(self.cy)
        self.assertEqual(out["requeued"], [])
        self.assertIn("thẩm quyền", self.cy.dlq_open()[0]["reason"])

    def test_stale_running_task_is_reported(self):
        tid = self.make()["task_id"]
        self.tb.plan(tid)
        self.tb.store.claim(tid, "A18", ttl_seconds=60)
        self.clock.now += timedelta(minutes=5)
        self.assertEqual(self.tb.sweep(self.cy)["stale"], [tid])


if __name__ == "__main__":
    unittest.main()
