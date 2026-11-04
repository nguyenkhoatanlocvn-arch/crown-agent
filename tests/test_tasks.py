import sqlite3
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from crown import registry as R
from crown.tasks import TaskError, TaskStore, to_vn


class FakeClock:
    def __init__(self):
        self.t = datetime(2026, 11, 4, 14, 0, tzinfo=timezone.utc)

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += timedelta(seconds=seconds)


class TaskTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.clock = FakeClock()
        self.reg = R.load()
        self.s = TaskStore(self.dir / "t.db", registry=self.reg, clock=self.clock)
        self.addCleanup(lambda: self.s.db.close())

    def new(self, **kw):
        base = dict(owner_agent_id="A03", objective="Kiểm skill X", acceptance_criteria="Có báo cáo PASS/FAIL",
                    actor="VUA_CEO", ready=True)
        base.update(kw)
        return self.s.create(**base)

    # Tình huống 1: hai worker cùng nhận
    def test_two_workers_only_one_wins(self):
        t = self.new()
        tok = self.s.claim(t["task_id"], "A11")
        with self.assertRaises(TaskError):
            self.s.claim(t["task_id"], "A18")
        self.assertEqual(self.s.get(t["task_id"])["lease_owner"], "A11")
        self.assertEqual(tok, 1)

    def test_parallel_threads_claim_once(self):
        t = self.new()
        wins, errs = [], []

        def go(name):
            st = TaskStore(self.dir / "t.db", clock=self.clock)
            try:
                st.claim(t["task_id"], name)
                wins.append(name)
            except (TaskError, sqlite3.OperationalError) as e:
                errs.append(e)
            finally:
                st.close()

        th = [threading.Thread(target=go, args=(f"w{i}",)) for i in range(5)]
        [x.start() for x in th]
        [x.join() for x in th]
        self.assertEqual(len(wins), 1, (wins, errs))

    # Tình huống 2: worker cũ hết lease vẫn ghi
    def test_stale_worker_rejected_after_reclaim(self):
        t = self.new()
        old = self.s.claim(t["task_id"], "A11", ttl_seconds=60)
        self.clock.advance(61)
        new = self.s.claim(t["task_id"], "A18")
        self.assertEqual(new, old + 1)
        with self.assertRaises(TaskError):
            self.s.record(t["task_id"], "A11", old, evidence="log.txt")
        self.s.record(t["task_id"], "A18", new, evidence="log.txt")

    def test_expired_lease_cannot_write_even_without_reclaim(self):
        t = self.new()
        tok = self.s.claim(t["task_id"], "A11", ttl_seconds=10)
        self.clock.advance(11)
        with self.assertRaises(TaskError):
            self.s.record(t["task_id"], "A11", tok, evidence="x")

    # Tình huống 3: tạo 2 lần
    def test_idempotent_create(self):
        a = self.new(idempotency_key="chat-msg-123")
        b = self.new(idempotency_key="chat-msg-123")
        self.assertEqual(a["task_id"], b["task_id"])
        self.assertEqual(len(self.s.list()), 1)

    # Tình huống 4: xong mà không có bằng chứng / tự duyệt
    def test_review_requires_evidence(self):
        t = self.new()
        tok = self.s.claim(t["task_id"], "A03")
        with self.assertRaises(TaskError):
            self.s.move(t["task_id"], "REVIEW", "A03", token=tok)

    def test_worker_cannot_self_verify(self):
        t = self.new()
        tok = self.s.claim(t["task_id"], "A03")
        self.s.record(t["task_id"], "A03", tok, evidence="tests: 18/18 OK")
        self.s.move(t["task_id"], "REVIEW", "A03", token=tok)
        with self.assertRaises(TaskError):
            self.s.move(t["task_id"], "VERIFIED_DONE", "A03")
        done = self.s.move(t["task_id"], "VERIFIED_DONE", "A20")
        self.assertEqual(done["state"], "VERIFIED_DONE")

    def test_cannot_skip_states(self):
        t = self.new(ready=False)
        with self.assertRaises(TaskError):
            self.s.move(t["task_id"], "VERIFIED_DONE", "A20")
        with self.assertRaises(TaskError):
            self.s.move(t["task_id"], "RUNNING", "A03")

    def test_leaving_running_needs_token(self):
        t = self.new()
        self.s.claim(t["task_id"], "A03")
        with self.assertRaises(TaskError):
            self.s.move(t["task_id"], "PAUSED_QUOTA", "A03")

    def test_quota_pause_and_resume_keeps_history(self):
        t = self.new()
        tok = self.s.claim(t["task_id"], "A03")
        self.s.record(t["task_id"], "A03", tok, artifact="draft.md", next_action="viết phần 2")
        self.s.move(t["task_id"], "PAUSED_QUOTA", "A03", token=tok, note="hết hạn mức")
        self.s.move(t["task_id"], "READY", "VUA_CEO")
        tok2 = self.s.claim(t["task_id"], "A03")
        got = self.s.get(t["task_id"])
        self.assertEqual(got["artifact_refs"], ["draft.md"])
        self.assertEqual(got["next_action"], "viết phần 2")
        self.assertGreater(tok2, tok)

    def test_retry_limit(self):
        t = self.new()
        for _ in range(3):
            tok = self.s.claim(t["task_id"], "A11")
            self.s.move(t["task_id"], "FAILED", "A11", token=tok)
            self.s.move(t["task_id"], "READY", "VUA_CEO")
        tok = self.s.claim(t["task_id"], "A11")
        self.s.move(t["task_id"], "FAILED", "A11", token=tok)
        with self.assertRaises(TaskError):
            self.s.move(t["task_id"], "READY", "VUA_CEO")

    def test_dependency_must_be_done(self):
        a = self.new()
        b = self.new(dependencies=[a["task_id"]])
        with self.assertRaises(TaskError):
            self.s.claim(b["task_id"], "A18")

    # Kiểm theo sổ đăng ký
    def test_unknown_owner_or_company_rejected(self):
        with self.assertRaises(TaskError):
            self.new(owner_agent_id="A99")
        with self.assertRaises(TaskError):
            self.new(company_id="NOT_A_COMPANY")

    def test_empty_objective_rejected(self):
        with self.assertRaises(TaskError):
            self.new(objective="   ")

    # Tình huống 5: giờ UTC, hiển thị giờ VN; nhật ký không sửa được
    def test_time_stored_utc_shown_vn(self):
        t = self.new()
        self.assertTrue(t["created_at"].endswith("+00:00"))
        self.assertEqual(to_vn(t["created_at"]), "2026-11-04 21:00 (UTC+7)")

    def test_event_log_append_only(self):
        t = self.new()
        with self.assertRaises(sqlite3.DatabaseError):
            self.s.db.execute("UPDATE events SET note='sua' WHERE task_id=?", (t["task_id"],))
        with self.assertRaises(sqlite3.DatabaseError):
            self.s.db.execute("DELETE FROM events")
        self.assertEqual(len(self.s.events(t["task_id"])), 1)

    def test_state_survives_restart(self):
        t = self.new()
        tok = self.s.claim(t["task_id"], "A03")
        self.s.record(t["task_id"], "A03", tok, evidence="e1")
        self.s.db.close()
        s2 = TaskStore(self.dir / "t.db", clock=self.clock)
        self.addCleanup(s2.close)
        got = s2.get(t["task_id"])
        self.assertEqual((got["state"], got["lease_owner"], got["evidence_refs"]), ("RUNNING", "A03", ["e1"]))


if __name__ == "__main__":
    unittest.main()
