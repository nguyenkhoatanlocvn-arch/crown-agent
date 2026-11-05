"""Bộ phiên chạy D04 (lệnh DRAGON SINGULARITY mục 06).

Yêu cầu: không chạy trùng phiên · khóa bằng database · chống lặp bằng run_id · retry có
backoff và trần · dead-letter queue · heartbeat, timeout, watchdog · khôi phục việc bị
gián đoạn · không vòng lặp vô hạn · mốc giờ theo Asia/Ho_Chi_Minh.
"""
import shutil
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from crown import cycle as C
from crown import policy as P
from crown import registry as R

UTC = timezone.utc


class FakeClock:
    def __init__(self, start):
        self.now = start

    def __call__(self):
        return self.now

    def tick(self, **kw):
        self.now += timedelta(**kw)


def vn(y, mo, d, h, mi=0, s=0):
    """Giờ Việt Nam → datetime UTC."""
    return datetime(y, mo, d, h, mi, s, tzinfo=timezone(timedelta(hours=7))).astimezone(UTC)


class Base(unittest.TestCase):
    START = vn(2026, 11, 6, 0, 17, 5)       # 00:17:05 giờ VN = 17:17:05 UTC hôm trước

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.reg = R.load()
        self.clock = FakeClock(self.START)
        self.pol = P.Policy.load(self.reg, clock=self.clock)
        self.cy = self.open()

    def open(self):
        c = C.Cycle(self.tmp / "tn.db", registry=self.reg, policy=self.pol, clock=self.clock)
        self.addCleanup(c.close)
        return c

    def run_ok(self, lane="rule"):
        b = self.cy.begin(lane)
        self.assertIn(b["action"], ("RUN", "RESUME"), b)
        self.cy.finish(b["run_id"], b["token"], "SUCCEEDED", summary={"ok": True})
        return b


class SlotTest(unittest.TestCase):
    def setUp(self):
        self.cfg = R.load().tn_config["schedule"]

    def test_slot_is_computed_in_vietnam_time_even_on_utc_machine(self):
        self.assertEqual(C.slot_id(C.current_slot("rule", datetime(2026, 11, 5, 17, 17, 0, tzinfo=UTC), self.cfg)),
                         "2026-11-06T00:17+07:00")

    def test_one_second_before_the_slot_belongs_to_previous_day_slot(self):
        self.assertEqual(C.slot_id(C.current_slot("rule", datetime(2026, 11, 5, 17, 16, 59, tzinfo=UTC), self.cfg)),
                         "2026-11-05T22:17+07:00")

    def test_twelve_rule_slots_per_day_at_minute_17_even_hours(self):
        day = [C.current_slot("rule", vn(2026, 11, 6, h, 30), self.cfg) for h in range(24)]
        ids = sorted({C.slot_id(s) for s in day})
        self.assertEqual(len(ids), 12)
        self.assertEqual(ids[0], "2026-11-06T00:17+07:00")
        self.assertEqual(ids[-1], "2026-11-06T22:17+07:00")
        for s in day:
            self.assertEqual((s.minute, s.hour % 2, s.utcoffset()), (17, 0, timedelta(hours=7)))

    def test_ai_lane_has_four_slots_per_day(self):
        ids = sorted({C.slot_id(C.current_slot("ai", vn(2026, 11, 6, h, 50), self.cfg)) for h in range(24)})
        self.assertEqual(ids, ["2026-11-06T00:47+07:00", "2026-11-06T06:47+07:00", "2026-11-06T12:47+07:00",
                               "2026-11-06T18:47+07:00"])

    def test_lanes_cannot_collide_when_triggers_are_on_time(self):
        # Phiên AI giữ khóa tối đa lock_ttl; phiên luật làm tối đa cycle_time_budget. Lịch phải chừa đủ chỗ cho cả hai.
        self.assertEqual(C.schedule_conflicts(self.cfg), [])
        bad = {**self.cfg, "ai_lane": {"times": ["01:47", "06:47"]}}          # 01:47 + 40 phút khóa = 02:27 > mốc luật 02:17
        got = C.schedule_conflicts(bad)
        self.assertEqual(len(got), 1)
        self.assertIn("01:47", got[0])
        tight = {**self.cfg, "ai_lane": {"times": ["00:20"]}}                 # phiên luật 00:17 còn có thể đang chạy
        self.assertIn("00:20", C.schedule_conflicts(tight)[0])

    def test_slots_between_is_exclusive_inclusive(self):
        a, b = vn(2026, 11, 6, 0, 17), vn(2026, 11, 6, 6, 17)
        got = [C.slot_id(s) for s in C.slots_between("rule", a, b, self.cfg)]
        self.assertEqual(got, ["2026-11-06T02:17+07:00", "2026-11-06T04:17+07:00", "2026-11-06T06:17+07:00"])

    def test_naive_datetime_is_rejected(self):
        with self.assertRaises(C.CycleError):
            C.current_slot("rule", datetime(2026, 11, 6, 0, 17), self.cfg)


class LockTest(Base):
    def test_begin_takes_the_lock_and_records_a_run(self):
        b = self.cy.begin("rule")
        self.assertEqual((b["action"], b["attempt"], b["mode"]), ("RUN", 1, "NORMAL"))
        self.assertEqual(b["run_id"], "rule-2026-11-06T00:17+07:00")
        self.assertEqual(self.cy.run(b["run_id"])["status"], "RUNNING")

    def test_two_sessions_at_once_only_one_runs(self):
        first = self.cy.begin("rule")
        other = self.open()                       # tiến trình thứ hai trên cùng database
        second = other.begin("rule", trigger="manual")
        self.assertEqual((second["action"], second["reason"]), ("SKIP", "LOCKED"))
        self.assertFalse(second["persist"])
        self.assertEqual(len(self.cy.runs()), 1)
        self.assertEqual(self.cy.run(first["run_id"])["status"], "RUNNING")

    def test_ai_lane_cannot_start_while_rule_lane_holds_the_lock(self):
        self.cy.begin("rule")
        self.clock.tick(minutes=2)
        self.assertEqual(self.cy.begin("ai")["reason"], "LOCKED")

    def test_same_slot_called_twice_is_rejected_by_run_id(self):
        self.run_ok()
        self.clock.tick(minutes=5)
        again = self.cy.begin("rule", trigger="manual")
        self.assertEqual((again["action"], again["reason"]), ("SKIP", "DUPLICATE"))
        self.assertEqual(len(self.cy.runs()), 1)

    def test_heartbeat_keeps_the_lock_alive(self):
        b = self.cy.begin("rule")
        ttl = self.reg.tn_config["schedule"]["lock_ttl_seconds"]["rule"]
        self.clock.tick(seconds=ttl - 10)
        self.cy.heartbeat(b["run_id"], b["token"])
        self.clock.tick(seconds=ttl - 10)
        self.assertEqual(self.open().begin("rule")["reason"], "LOCKED")

    def test_finish_releases_the_lock(self):
        self.run_ok()
        self.clock.tick(hours=2)
        self.assertEqual(self.cy.begin("rule")["action"], "RUN")


class RecoveryTest(Base):
    def test_dead_session_is_resumed_from_checkpoint_in_same_slot(self):
        b = self.cy.begin("rule")
        self.cy.checkpoint(b["run_id"], b["token"], "WAKE_RECOVERY", {"n": 1})
        self.cy.checkpoint(b["run_id"], b["token"], "SYSTEM_AUDIT", {"n": 2})
        # ... tiến trình chết ở đây, không finish ...
        self.clock.tick(minutes=20)
        r = self.open().begin("rule")
        self.assertEqual((r["action"], r["attempt"], r["run_id"]), ("RESUME", 2, b["run_id"]))
        self.assertEqual(r["done_phases"], ["WAKE_RECOVERY", "SYSTEM_AUDIT"])
        self.assertGreater(r["token"], b["token"])
        self.cy.finish(r["run_id"], r["token"], "SUCCEEDED")
        run = self.cy.run(b["run_id"])
        self.assertEqual((run["status"], run["attempt"], run["interruptions"]), ("SUCCEEDED", 2, 1))

    def test_dead_session_found_in_next_slot_is_marked_interrupted(self):
        b = self.cy.begin("rule")
        self.clock.tick(hours=2)
        n = self.open().begin("rule")
        self.assertEqual((n["action"], n["run_id"]), ("RUN", "rule-2026-11-06T02:17+07:00"))
        self.assertEqual(self.cy.run(b["run_id"])["status"], "INTERRUPTED")
        self.assertIn(b["run_id"], n["recovered"])

    def test_zombie_with_old_token_cannot_write(self):
        a = self.cy.begin("rule")
        self.clock.tick(hours=2)
        b = self.open().begin("rule")
        for call in (lambda: self.cy.checkpoint(a["run_id"], a["token"], "PLANNING", {}),
                     lambda: self.cy.heartbeat(a["run_id"], a["token"]),
                     lambda: self.cy.finish(a["run_id"], a["token"], "SUCCEEDED")):
            with self.assertRaises(C.CycleError):
                call()
        self.assertEqual(self.cy.run(a["run_id"])["status"], "INTERRUPTED")
        self.assertEqual(self.cy.run(b["run_id"])["status"], "RUNNING")

    def test_checkpoint_keeps_first_record(self):
        b = self.cy.begin("rule")
        self.cy.checkpoint(b["run_id"], b["token"], "PLANNING", {"v": 1})
        self.cy.checkpoint(b["run_id"], b["token"], "PLANNING", {"v": 2})
        self.assertEqual(self.cy.checkpoints(b["run_id"])["PLANNING"], {"v": 1})


class MissedSlotTest(Base):
    def test_skipped_slots_are_recorded_and_covered_by_current_run(self):
        self.run_ok()
        self.clock.tick(hours=6)                   # GitHub bỏ 2 phiên 02:17 và 04:17
        b = self.cy.begin("rule")
        self.assertEqual(b["run_id"], "rule-2026-11-06T06:17+07:00")
        self.assertEqual(b["missed"], ["rule-2026-11-06T02:17+07:00", "rule-2026-11-06T04:17+07:00"])
        m = self.cy.run("rule-2026-11-06T02:17+07:00")
        self.assertEqual((m["status"], m["summary"]["covered_by"]), ("MISSED", b["run_id"]))

    def test_catch_up_is_bounded(self):
        self.run_ok()
        self.clock.tick(days=10)
        b = self.cy.begin("rule")
        cap = self.reg.tn_config["schedule"]["catch_up_max_slots"]
        self.assertEqual(len(b["missed"]), cap)
        self.assertEqual(b["missed_not_recorded"], 10 * 12 - 1 - cap)
        self.assertEqual(len(self.cy.runs()), cap + 2)

    def test_a_stopped_slot_does_not_hide_the_slots_missed_before_it(self):
        self.run_ok()                                             # 00:17
        self.clock.tick(hours=6)                                  # 02:17 và 04:17 không ai chạy
        b = self.cy.begin("rule", external_stop="dừng")           # 06:17 bị dừng khẩn cấp
        self.assertEqual(b["reason"], "ESTOP")
        self.assertEqual(sorted(b["missed"]), ["rule-2026-11-06T02:17+07:00", "rule-2026-11-06T04:17+07:00"])
        self.clock.tick(hours=2)
        self.run_ok()                                             # 08:17 chạy lại
        st = {r["run_id"][-11:-6]: r["status"] for r in self.cy.runs("rule")}
        self.assertEqual(st, {"00:17": "SUCCEEDED", "02:17": "MISSED", "04:17": "MISSED", "06:17": "SKIPPED", "08:17": "SUCCEEDED"})
        self.assertEqual(self.cy.metrics()["rule"]["missed"], 2)

    def test_first_run_ever_has_nothing_to_catch_up(self):
        self.assertEqual(self.cy.begin("rule")["missed"], [])

    def test_missed_slot_is_not_blamed_when_other_lane_held_the_lock(self):
        self.run_ok()
        self.clock.now = vn(2026, 11, 6, 1, 47, 3)
        ai = self.cy.begin("ai")
        self.clock.now = vn(2026, 11, 6, 2, 17, 4)   # phiên luật 02:17 gặp khóa của phiên AI
        self.assertEqual(self.cy.begin("rule")["reason"], "LOCKED")
        self.clock.now = vn(2026, 11, 6, 2, 25)
        self.cy.finish(ai["run_id"], ai["token"], "SUCCEEDED")
        self.clock.now = vn(2026, 11, 6, 4, 17, 2)
        b = self.cy.begin("rule")
        self.assertEqual(b["missed"], ["rule-2026-11-06T02:17+07:00"])
        self.assertEqual(self.cy.run("rule-2026-11-06T02:17+07:00")["summary"]["cause"], "LOCKED_BY " + ai["run_id"])


class RetryTest(Base):
    def fail(self, b, msg="lỗi thử"):
        self.cy.finish(b["run_id"], b["token"], "FAILED", error=msg)

    def test_failed_run_retries_with_backoff_then_goes_to_dead_letter(self):
        b1 = self.cy.begin("rule")
        self.fail(b1)
        self.assertEqual(self.cy.begin("rule")["reason"], "BACKOFF")             # chưa tới 60 giây
        self.clock.tick(seconds=61)
        b2 = self.cy.begin("rule")
        self.assertEqual((b2["action"], b2["attempt"]), ("RESUME", 2))
        self.fail(b2)
        self.clock.tick(seconds=61)
        self.assertEqual(self.cy.begin("rule")["reason"], "BACKOFF")             # lần 2 chờ 120 giây
        self.clock.tick(seconds=60)
        b3 = self.cy.begin("rule")
        self.assertEqual(b3["attempt"], 3)
        self.fail(b3, "vẫn lỗi")
        self.clock.tick(minutes=30)
        end = self.cy.begin("rule")
        self.assertEqual((end["action"], end["reason"]), ("SKIP", "EXHAUSTED"))
        dlq = self.cy.dlq_open()
        self.assertEqual([(d["kind"], d["ref"]) for d in dlq], [("run", b1["run_id"])])
        self.assertIn("vẫn lỗi", dlq[0]["reason"])
        self.assertEqual(self.cy.run(b1["run_id"])["attempt"], 3)                # không có lần thứ 4

    def test_backoff_is_capped(self):
        cfg = self.reg.tn_config["schedule"]
        self.assertEqual([C.backoff_seconds(a, cfg) for a in (1, 2, 3, 4, 5, 9)], [60, 120, 240, 480, 900, 900])

    def test_dead_letter_is_idempotent_and_resolvable_only_with_rights(self):
        self.cy.dlq_put("task", "T-1", "hết lượt thử", {"x": 1})
        self.cy.dlq_put("task", "T-1", "hết lượt thử lần nữa", {"x": 2})
        self.assertEqual(len(self.cy.dlq_open()), 1)
        with self.assertRaises(P.PolicyError):
            self.cy.dlq_resolve("task", "T-1", actor="A04", note="tự xử")
        self.cy.dlq_resolve("task", "T-1", actor="THIEN_NAO", note="đã giao lại")
        self.assertEqual(self.cy.dlq_open(), [])


class StopAndBreakerTest(Base):
    def test_emergency_stop_blocks_runs_until_chairman_clears_it(self):
        self.cy.estop_set("THIEN_NAO", "thử dừng khẩn cấp")
        b = self.cy.begin("rule")
        self.assertEqual((b["action"], b["reason"]), ("SKIP", "ESTOP"))
        self.assertEqual(self.cy.run(b["run_id"])["status"], "SKIPPED")
        with self.assertRaises(P.PolicyError):
            self.cy.estop_clear("THIEN_NAO")
        self.cy.estop_clear("CHAIRMAN")
        self.clock.tick(minutes=1)
        self.assertEqual(self.cy.begin("rule")["action"], "RUN")

    def test_stop_file_from_outside_blocks_the_run_without_touching_the_stored_switch(self):
        b = self.cy.begin("rule", external_stop="Chủ tịch dừng để kiểm tra")
        self.assertEqual((b["action"], b["reason"], b["persist"]), ("SKIP", "ESTOP", True))
        row = self.cy.run(b["run_id"])
        self.assertEqual(row["status"], "SKIPPED")
        self.assertIn("Chủ tịch dừng để kiểm tra", row["error"])
        self.assertFalse(self.cy.estop().get("active"))          # gỡ tệp là chạy lại; công tắc trong database không bị đổi
        self.assertIsNone(self.cy.lock()["run_id"])
        self.clock.tick(hours=2)
        self.assertEqual(self.cy.begin("rule")["action"], "RUN")

    def test_agent_without_right_cannot_set_stop(self):
        with self.assertRaises(P.PolicyError):
            self.cy.estop_set("A04", "không có quyền")

    def test_breaker_opens_after_repeated_failures_then_recovers_step_by_step(self):
        for _ in range(3):
            b = self.cy.begin("rule")
            self.cy.finish(b["run_id"], b["token"], "FAILED", error="hỏng")
            self.clock.tick(minutes=10)
        self.assertEqual(self.cy.breaker()["state"], "OPEN")
        self.clock.tick(hours=2)
        safe = self.cy.begin("rule")
        self.assertEqual(safe["mode"], "SAFE")                                   # chỉ kiểm sức khỏe và báo cáo
        self.cy.finish(safe["run_id"], safe["token"], "SUCCEEDED", summary={"audit_ok": True})
        self.assertEqual(self.cy.breaker()["state"], "HALF_OPEN")
        self.clock.tick(hours=2)
        probe = self.cy.begin("rule")
        self.assertEqual(probe["mode"], "PROBE")                                 # chạy thử giới hạn
        self.cy.finish(probe["run_id"], probe["token"], "SUCCEEDED")
        self.assertEqual(self.cy.breaker()["state"], "CLOSED")
        self.clock.tick(hours=2)
        self.assertEqual(self.cy.begin("rule")["mode"], "NORMAL")

    def test_breaker_stays_open_when_safe_audit_fails(self):
        for _ in range(3):
            b = self.cy.begin("rule")
            self.cy.finish(b["run_id"], b["token"], "FAILED", error="hỏng")
            self.clock.tick(minutes=10)
        self.clock.tick(hours=2)
        safe = self.cy.begin("rule")
        self.cy.finish(safe["run_id"], safe["token"], "SUCCEEDED", summary={"audit_ok": False})
        self.assertEqual(self.cy.breaker()["state"], "OPEN")

    def test_success_resets_failure_count(self):
        b = self.cy.begin("rule")
        self.cy.finish(b["run_id"], b["token"], "FAILED", error="hỏng")
        self.clock.tick(seconds=61)
        b = self.cy.begin("rule")
        self.cy.finish(b["run_id"], b["token"], "SUCCEEDED")
        self.assertEqual(self.cy.breaker(), {"state": "CLOSED", "failures": 0})


class MetricsTest(Base):
    def test_wilson_interval(self):
        self.assertIsNone(C.wilson(0, 0))
        lo, hi = C.wilson(10, 10)
        self.assertAlmostEqual(lo, 0.7225, places=3)
        self.assertEqual(hi, 1.0)
        lo, hi = C.wilson(29956, 29956)
        self.assertGreater(lo, 0.9998)

    def test_reliability_counts_missed_slots_as_failures(self):
        self.run_ok()
        self.clock.tick(hours=6)
        b = self.cy.begin("rule")
        self.cy.finish(b["run_id"], b["token"], "SUCCEEDED")
        m = self.cy.metrics()
        self.assertEqual((m["rule"]["slots"], m["rule"]["succeeded"], m["rule"]["missed"]), (4, 2, 2))
        self.assertEqual(m["rule"]["scheduling_reliability"], 0.5)
        self.assertEqual(len(m["rule"]["ci95"]), 2)
        self.assertFalse(m["rule"]["target_9999_demonstrated"])

    def test_usage_counters_enforce_daily_cap(self):
        day = "2026-11-06"
        self.assertTrue(self.cy.usage_take(day, "tier:ADVANCED", cap=2))
        self.assertTrue(self.cy.usage_take(day, "tier:ADVANCED", cap=2))
        self.assertFalse(self.cy.usage_take(day, "tier:ADVANCED", cap=2))        # dừng cứng khi chạm hạn mức
        self.assertEqual(self.cy.usage(day), {"tier:ADVANCED": 2})


if __name__ == "__main__":
    unittest.main()
