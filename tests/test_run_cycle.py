"""Chu trình 9 pha chạy thật trên kho git giả lập cục bộ (thay cho GitHub).

Lát cắt: scheduler → planner → Agent Vua → nhiệm vụ → kiểm thử → báo cáo.
Phần việc cần AI trong test do một "Agent giả" làm và được ghi nhãn TEST.
"""
import json
import shutil
import subprocess
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from crown import cycle as C
from crown import registry as R
from crown import runner as RN
from crown import statesync as S
from crown import thiennao as TN
from crown.vua import Vua

UTC = timezone.utc
VN = timezone(timedelta(hours=7))


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


class Clock:
    def __init__(self, now):
        self.now = now

    def __call__(self):
        return self.now

    def set_vn(self, d, h, mi=17, s=5):
        self.now = datetime(2026, 11, d, h, mi, s, tzinfo=VN).astimezone(UTC)

    def sleep(self, seconds):
        self.now += timedelta(seconds=seconds)


class Sandbox(unittest.TestCase):
    INBOX = [{"key": "web-in-git", "name": "Đưa mã web đang chạy vào repo", "objective": "Ba tệp của web nằm trong repo, kèm sha256",
              "acceptance": "sha256 trong repo khớp bản đang phát hành", "seat": "TN-05", "project": "HOANG_THANH",
              "priority": "P1", "risk_level": "LOW", "work_type": "CODE", "action_class": "CODE_BRANCH_WRITE"}]

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.remote = self.tmp / "remote.git"
        git(self.tmp, "init", "-q", "--bare", "-b", "master", str(self.remote))
        seed = self.clone("seed")
        (seed / "README.md").write_text("crown-agent giả lập\n", encoding="utf-8")
        git(seed, "add", "-A")
        git(seed, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "init")
        git(seed, "push", "-q", "origin", "HEAD:refs/heads/master")
        self.clock = Clock(None)
        self.clock.set_vn(6, 0)
        self.inbox = self.tmp / "inbox.json"
        self.inbox.write_text(json.dumps({"items": self.INBOX}, ensure_ascii=False), encoding="utf-8")

    def clone(self, name):
        d = self.tmp / name
        if not d.exists():
            git(self.tmp, "clone", "-q", str(self.remote), str(d))
        return d

    def runner(self, name="a", **kw):
        return RN.Runner(self.clone(name), self.tmp / f"{name}.db", clock=self.clock, sleep=self.clock.sleep,
                         inbox_path=self.inbox, **kw)

    def remote_files(self):
        st = S.GitState(self.clone("reader"))
        return st.pull()

    def remote_db(self):
        """Dựng lại database từ nhánh trạng thái để kiểm bằng mắt của người ngoài."""
        p = self.tmp / "inspect.db"
        S.restore_sql(S.unpack(self.remote_files()), p)
        return p

    def commits(self):
        return int(git(self.remote, "rev-list", "--count", "refs/heads/tn-state"))


class RuleCycleTest(Sandbox):
    def test_first_cycle_creates_state_branch_with_report(self):
        res = self.runner().rule()
        self.assertEqual((res["status"], res["run_id"]), ("SUCCEEDED", "rule-2026-11-06T00:17+07:00"))
        files = self.remote_files()
        for f in ("state/tn_state.sql", "state/meta.json", "state/latest.json", "state/REPORT.md"):
            self.assertIn(f, files)
        latest = json.loads(files["state/latest.json"])
        self.assertEqual(latest["run"]["status"], "SUCCEEDED")
        self.assertEqual(latest["run"]["phases_done"], C.PHASES)
        health = {h["check_id"]: h["status"] for h in latest["health"]}
        self.assertEqual((health["registry"], health["policy"], health["ledger"], health["schedule"]), ("PASS", "PASS", "PASS", "PASS"))
        self.assertEqual(health["tests"], "SKIP")                       # test không chạy trong phiên này → nói rõ
        self.assertIn("rule-2026-11-06T00:17+07:00", files["state/REPORT.md"])
        self.assertEqual(git(self.remote, "rev-parse", "refs/heads/master"), git(self.clone("a"), "rev-parse", "origin/master"))

    def test_same_slot_twice_writes_nothing_the_second_time(self):
        self.runner().rule()
        n = self.commits()
        self.clock.sleep(300)
        res = self.runner("b").rule(trigger="manual")
        self.assertEqual((res["status"], res["reason"]), ("SKIP", "DUPLICATE"))
        self.assertEqual(self.commits(), n)

    def test_next_slot_continues_from_pushed_state_on_a_fresh_machine(self):
        self.runner("a").rule()
        self.clock.set_vn(6, 2)
        res = self.runner("b").rule()                                   # máy khác, không có database cũ
        self.assertEqual(res["status"], "SUCCEEDED")
        cy = C.Cycle(self.remote_db(), clock=self.clock)
        self.addCleanup(cy.close)
        self.assertEqual([(r["run_id"][-17:-6], r["status"]) for r in cy.runs("rule")],
                         [("11-06T00:17", "SUCCEEDED"), ("11-06T02:17", "SUCCEEDED")])

    def test_inbox_item_flows_planner_to_agent_vua(self):
        self.runner().rule()
        latest = json.loads(self.remote_files()["state/latest.json"])
        task = next(t for t in latest["board"]["tasks"] if t["TASK_NAME"] == "Đưa mã web đang chạy vào repo")
        self.assertEqual((task["STATUS"], task["OWNER_AGENT"], task["ASSIGNED_MODEL"]), ("ASSIGNED", "A18", "sonnet"))
        v = Vua(self.remote_db(), clock=self.clock)
        self.addCleanup(v.close)
        st = v.status("TN-" + task["TASK_ID"])
        self.assertEqual((st["issued_by"], st["domain"]), ("THIEN_NAO", "WEB"))
        self.assertEqual(st["children"]["S1"]["task_id"], task["TASK_ID"])   # Vua nhận đúng việc trên bảng, không tạo việc trùng
        self.assertEqual(st["root"]["state"], "READY")

    def test_inbox_is_not_ingested_twice(self):
        self.runner().rule()
        self.clock.set_vn(6, 2)
        self.runner("b").rule()
        latest = json.loads(self.remote_files()["state/latest.json"])
        self.assertEqual(len([t for t in latest["board"]["tasks"] if t["TASK_NAME"].startswith("Đưa mã web")]), 1)

    def test_inbox_on_state_branch_is_used_when_no_local_inbox_is_given(self):
        def plain(name):
            return RN.Runner(self.clone(name), self.tmp / f"{name}.db", clock=self.clock, sleep=self.clock.sleep)
        plain("a").rule()
        st = S.GitState(self.clone("chairman"))
        st.pull()
        st.push({"state/inbox.json": json.dumps({"items": self.INBOX}, ensure_ascii=False)}, "Chủ tịch thêm việc")
        self.clock.set_vn(6, 2)
        plain("b").rule()
        files = self.remote_files()
        latest = json.loads(files["state/latest.json"])
        self.assertEqual([t["STATUS"] for t in latest["board"]["tasks"] if t["TASK_NAME"].startswith("Đưa mã web")], ["ASSIGNED"])
        self.assertIn("state/inbox.json", files)                        # bộ máy không xóa hộp việc

    def test_bad_inbox_items_are_rejected_one_by_one_without_failing_the_cycle(self):
        good = self.INBOX[0]
        items = [
            {**good, "key": "thieu-truong", "acceptance": ""},
            {**good, "key": "ghe-la", "seat": "TN-99"},
            {**good, "key": "tu-nang-quyen", "action_class": "PERMISSION_GRANT"},          # chỉ Chủ tịch → không tạo
            {**good, "key": "chi-tien", "name": "Mua gói API", "action_class": "SPEND", "work_type": "RESEARCH", "seat": "TN-11"},
            {**good, "key": "lenh-la", "name": "Việc có trường lạ", "owner": "CHAIRMAN", "approval_id": "tự-cấp", "status": "COMPLETED"},
            "không phải object",
            good,
        ]
        self.inbox.write_text(json.dumps({"items": items}, ensure_ascii=False), encoding="utf-8")
        res = self.runner().rule()
        self.assertEqual(res["status"], "SUCCEEDED")
        latest = json.loads(self.remote_files()["state/latest.json"])
        by_name = {t["TASK_NAME"]: t for t in latest["board"]["tasks"]}
        self.assertEqual(by_name["Đưa mã web đang chạy vào repo"]["STATUS"], "ASSIGNED")
        self.assertEqual((by_name["Mua gói API"]["STATUS"], by_name["Mua gói API"]["APPROVAL_STATUS"]), ("BACKLOG", "PENDING"))
        odd = by_name["Việc có trường lạ"]                                # trường lạ bị bỏ qua, không đổi chủ, không tự duyệt
        self.assertEqual((odd["OWNER_AGENT"], odd["STATUS"], odd["APPROVAL_STATUS"]), ("A18", "ASSIGNED", "NOT_REQUIRED"))
        rejected = {r["key"] for r in latest["phase_details"]["PLANNING"]["rejected"]}
        self.assertEqual(rejected, {"thieu-truong", "ghe-la", "tu-nang-quyen"})
        self.assertEqual(len(latest["board"]["tasks"]), 3)

    def test_broken_inbox_file_is_reported_not_fatal(self):
        self.inbox.write_text("{ đây không phải JSON", encoding="utf-8")
        res = self.runner().rule()
        self.assertEqual(res["status"], "SUCCEEDED")
        latest = json.loads(self.remote_files()["state/latest.json"])
        self.assertTrue(any(l["source"] == "inbox" for l in latest["lessons"]))

    def test_failing_tests_create_exactly_one_repair_task_per_day(self):
        bad = {"ran": 210, "failed": 2, "ok": False, "tail": "FAILED (failures=2)"}
        self.runner("a").rule(tests=bad)
        self.clock.set_vn(6, 2)
        self.runner("b").rule(tests=bad)
        latest = json.loads(self.remote_files()["state/latest.json"])
        repairs = [t for t in latest["board"]["tasks"] if "tests" in t["TASK_NAME"]]
        self.assertEqual(len(repairs), 1)
        self.assertEqual((repairs[0]["OWNER_AGENT"], repairs[0]["RISK_LEVEL"]), ("A18", "MEDIUM"))
        self.assertFalse(latest["run"]["summary"]["audit_ok"])
        self.assertTrue(any("tests" in l["source"] for l in latest["lessons"]))

    def test_flaky_tests_are_recorded_as_a_lesson_but_do_not_create_a_repair_task(self):
        flaky = {"ran": 258, "failed": 0, "ok": True, "flaky": True, "first_tail": "FAILED (failures=1) | database is locked"}
        self.runner().rule(tests=flaky)
        latest = json.loads(self.remote_files()["state/latest.json"])
        health = {h["check_id"]: h for h in latest["health"]}
        self.assertEqual(health["tests"]["status"], "PASS")
        self.assertIn("lần đầu trượt", health["tests"]["evidence"])
        self.assertTrue(latest["run"]["summary"]["audit_ok"])
        self.assertEqual([t for t in latest["board"]["tasks"] if "tests" in t["TASK_NAME"]], [])
        self.assertTrue(any(l["source"] == "health:tests-flaky" and "database is locked" in l["text"] for l in latest["lessons"]))

    def test_daily_report_goes_through_ledger_and_is_verified_by_someone_else(self):
        self.runner("a").rule()                                         # 06/11 00:17
        self.clock.set_vn(6, 22)
        self.runner("b").rule()
        self.clock.set_vn(7, 0)
        self.runner("c").rule()                                         # phiên đầu ngày 07/11 → báo cáo ngày 06/11
        files = self.remote_files()
        self.assertIn("state/daily/2026-11-06.md", files)
        latest = json.loads(files["state/latest.json"])
        rep = next(t for t in latest["board"]["tasks"] if t["TASK_NAME"] == "Báo cáo ngày 2026-11-06")
        self.assertEqual((rep["STATUS"], rep["OWNER_AGENT"], rep["ASSIGNED_MODEL"], rep["QUALITY_SCORE"]),
                         ("COMPLETED", "TN-ENGINE", "rule-engine", 1.0))
        tb = TN.ThienNao(self.remote_db(), clock=self.clock, inbox_path=self.inbox)
        self.addCleanup(tb.close)
        ev = tb.board.store.events(rep["TASK_ID"])
        self.assertEqual((ev[-1]["actor"], ev[-1]["to_state"]), ("THIEN_NAO", "VERIFIED_DONE"))
        self.assertIn("2/12", files["state/daily/2026-11-06.md"])       # 2 phiên chạy / 12 mốc của ngày


class FailureTest(Sandbox):
    def test_two_runners_race_only_one_writes(self):
        a, b = self.runner("a"), self.runner("b")
        a.load(); b.load()
        ba = a.claim_rule()
        self.assertEqual(ba["action"], "RUN")
        bb = b.claim_rule()                                             # cùng gốc → đẩy bị từ chối → đọc lại → thấy khóa
        self.assertEqual((bb["action"], bb["reason"]), ("SKIP", "LOCKED"))
        a.work_rule(ba)
        cy = C.Cycle(self.remote_db(), clock=self.clock)
        self.addCleanup(cy.close)
        self.assertEqual([(r["status"], r["attempt"]) for r in cy.runs("rule")], [("SUCCEEDED", 1)])

    def test_crash_is_recorded_and_next_session_recovers_without_duplicates(self):
        with self.assertRaises(TN.SimulatedCrash):
            self.runner("a", crash_after="DELEGATION").rule()           # chết sau khi giao việc, chưa kịp ghi kết quả
        self.clock.set_vn(6, 2)
        res = self.runner("b").rule()
        self.assertEqual(res["status"], "SUCCEEDED")
        self.assertEqual(res["recovered"], ["rule-2026-11-06T00:17+07:00"])
        tn = TN.ThienNao(self.remote_db(), clock=self.clock, inbox_path=self.inbox)
        self.addCleanup(tn.close)
        self.assertEqual(tn.cycle.run("rule-2026-11-06T00:17+07:00")["status"], "INTERRUPTED")
        self.assertEqual(len([t for t in tn.board.board() if t["TASK_NAME"].startswith("Đưa mã web")]), 1)
        self.assertTrue(any("gián đoạn" in l["text"] for l in tn.cycle.lessons(50)))

    def test_exception_retries_in_same_job_and_resumes_from_checkpoint(self):
        r = self.runner("a", fail_once_in="MODEL_ASSIGNMENT")
        res = r.rule()
        self.assertEqual((res["status"], res["attempt"], res["action"]), ("SUCCEEDED", 2, "RESUME"))
        self.assertEqual(res["skipped_phases"], ["WAKE_RECOVERY", "SYSTEM_AUDIT", "PLANNING"])
        self.assertGreaterEqual(r.slept, 60)                            # có chờ backoff, không thử dồn dập

    def test_permanent_failure_stops_after_three_attempts_and_lands_in_dead_letter(self):
        r = self.runner("a", fail_always_in="PLANNING")
        res = r.rule()
        self.assertEqual((res["status"], res["attempt"]), ("FAILED", 3))
        tn = TN.ThienNao(self.remote_db(), clock=self.clock, inbox_path=self.inbox)
        self.addCleanup(tn.close)
        self.assertEqual([(d["kind"], d["ref"]) for d in tn.cycle.dlq_open()], [("run", "rule-2026-11-06T00:17+07:00")])
        self.assertEqual(tn.cycle.breaker()["state"], "OPEN")

    def test_open_breaker_runs_safe_mode_only(self):
        self.runner("a", fail_always_in="PLANNING").rule()
        self.clock.set_vn(6, 2)
        res = self.runner("b").rule()
        self.assertEqual((res["status"], res["mode"]), ("SUCCEEDED", "SAFE"))
        latest = json.loads(self.remote_files()["state/latest.json"])
        self.assertEqual(latest["run"]["phases_done"], C.SAFE_PHASES)
        self.assertEqual(latest["board"]["tasks"], [])                  # chế độ an toàn không tạo, không giao việc
        self.assertEqual(latest["metrics"]["breaker"]["state"], "HALF_OPEN")

    def test_stop_file_on_state_branch_halts_everything(self):
        self.runner("a").rule()
        st = S.GitState(self.clone("chairman"))
        st.pull()
        st.push({"state/STOP": "Chủ tịch dừng để kiểm tra\n"}, "STOP")
        self.clock.set_vn(6, 2)
        res = self.runner("b").rule()
        self.assertEqual((res["status"], res["reason"]), ("SKIP", "ESTOP"))
        self.assertIn("state/STOP", self.remote_files())                # không tự xóa tệp dừng

    def test_corrupt_state_is_never_overwritten(self):
        self.runner("a").rule()
        st = S.GitState(self.clone("vandal"))
        files = st.pull()
        st.push({"state/tn_state.sql": files["state/tn_state.sql"] + "-- sửa tay\n"}, "phá")
        n = self.commits()
        self.clock.set_vn(6, 2)
        with self.assertRaisesRegex(S.StateError, "sha256"):
            self.runner("b").rule()
        self.assertEqual(self.commits(), n)


class AiLaneTest(Sandbox):
    def fake_agent_vua(self, db, brief):
        """Agent giả đóng vai Agent Vua + thợ + người kiểm (chỉ dùng trong test)."""
        v = Vua(db, clock=self.clock)
        try:
            for t in brief["tasks"]:
                rid, owner = t["request_id"], t["OWNER_AGENT"]
                c = v.claim(request_id=rid, key="S1", worker=owner)
                v.evidence(request_id=rid, key="S1", worker=owner, token=c["token"], evidence=["TEST: sha256 khớp 3/3 tệp"])
                v.move(request_id=rid, key="S1", to="REVIEW", actor=owner, token=c["token"])
                v.review(request_id=rid, key="S1", reviewer="A20", verdict="PASS",
                         checks=[{"text": "sha256 khớp", "passed": True, "evidence": "TEST"}])
                r = v.claim(request_id=rid, key="ROOT", worker="VUA_CEO")
                v.evidence(request_id=rid, key="ROOT", worker="VUA_CEO", token=r["token"], evidence=["TEST: S1 VERIFIED_DONE"])
                v.move(request_id=rid, key="ROOT", to="REVIEW", actor="VUA_CEO", token=r["token"])
                v.review(request_id=rid, key="ROOT", reviewer="A20", verdict="PASS",
                         checks=[{"text": "việc con xong", "passed": True, "evidence": "TEST"}])
        finally:
            v.close()

    def test_ai_session_claims_does_delegated_work_and_reports(self):
        self.runner("a").rule()                                         # 00:17 giao 1 việc cho Agent Vua
        self.clock.set_vn(6, 1, 47, 3)                                  # mốc AI 00:47 bị kích hoạt trễ 1 giờ
        ai = self.runner("ai1")
        brief = ai.ai_claim()
        self.assertEqual((brief["action"], brief["run_id"], brief["limit"]), ("RUN", "ai-2026-11-06T00:47+07:00", 3))
        self.assertEqual([t["TASK_NAME"] for t in brief["tasks"]], ["Đưa mã web đang chạy vào repo"])
        t = brief["tasks"][0]
        self.assertEqual((t["ASSIGNED_MODEL"], t["seat"]["id"], t["request_id"]), ("sonnet", "TN-05", "TN-" + t["TASK_ID"]))
        self.assertIn("Web Developer", t["seat"]["system_instruction"])
        self.assertTrue(brief["hard_rules"])

        self.clock.set_vn(6, 2, 17, 4)                                  # phiên luật 02:17 gặp khóa của phiên AI
        blocked = self.runner("b").rule()
        self.assertEqual((blocked["status"], blocked["reason"]), ("SKIP", "LOCKED"))

        self.fake_agent_vua(ai.db_path, brief)
        self.clock.set_vn(6, 2, 20)
        done = ai.ai_finish(brief["run_id"], brief["token"], "SUCCEEDED", {"note": "TEST"})
        self.assertEqual(done["status"], "SUCCEEDED")
        latest = json.loads(self.remote_files()["state/latest.json"])
        task = next(x for x in latest["board"]["tasks"] if x["TASK_ID"] == t["TASK_ID"])
        self.assertEqual(task["STATUS"], "COMPLETED")
        self.assertEqual(latest["budget"]["usage"]["ai_sessions"], 1)

        self.clock.set_vn(6, 4)
        after = self.runner("c").rule()
        self.assertEqual(after["missed"], ["rule-2026-11-06T02:17+07:00"])
        cy = C.Cycle(self.remote_db(), clock=self.clock)
        self.addCleanup(cy.close)
        self.assertTrue(cy.run("rule-2026-11-06T02:17+07:00")["summary"]["cause"].startswith("LOCKED_BY ai-"))

    def test_ai_session_whose_lock_expired_cannot_overwrite(self):
        self.runner("a").rule()
        self.clock.set_vn(6, 1, 47, 3)
        ai = self.runner("ai1")
        brief = ai.ai_claim()
        self.clock.set_vn(6, 4)                                         # phiên AI treo quá hạn khóa (40 phút)
        self.assertEqual(self.runner("b").rule()["status"], "SUCCEEDED")
        with self.assertRaises((C.CycleError, RN.RunnerError)):
            ai.ai_finish(brief["run_id"], brief["token"], "SUCCEEDED", {})
        cy = C.Cycle(self.remote_db(), clock=self.clock)
        self.addCleanup(cy.close)
        self.assertEqual(cy.run(brief["run_id"])["status"], "INTERRUPTED")

    def test_ai_sessions_per_day_is_a_hard_cap(self):
        self.runner("a").rule()
        for i, (d, h) in enumerate([(6, 1), (6, 7), (6, 13), (6, 19)]):
            self.clock.set_vn(d, h, 47, 3)
            r = self.runner(f"ai{i}")
            b = r.ai_claim()
            self.assertEqual(b["action"], "RUN", b)
            r.ai_finish(b["run_id"], b["token"], "SUCCEEDED", {})
        self.clock.set_vn(6, 23, 0)
        extra = self.runner("ai-extra").ai_claim(trigger="manual")      # bấm tay thêm trong ngày
        self.assertEqual(extra["action"], "SKIP")


if __name__ == "__main__":
    unittest.main()
