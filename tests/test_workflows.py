"""Tệp workflow GitHub Actions: ghim SHA, quyền tối thiểu, lịch khớp bộ máy (lệnh mục 06, 16)."""
import re
import unittest
from pathlib import Path

from crown import cycle as C
from crown import registry as R

WF_DIR = R.ROOT / ".github" / "workflows"
CYCLE = WF_DIR / "thien-nao-cycle.yml"
CI = WF_DIR / "ci.yml"


def text(p):
    return p.read_text(encoding="utf-8")


class AllWorkflowsTest(unittest.TestCase):
    def files(self):
        fs = sorted(WF_DIR.glob("*.yml")) + sorted(WF_DIR.glob("*.yaml"))
        self.assertTrue(fs, "chưa có workflow nào")
        return fs

    def test_every_action_is_pinned_to_a_full_commit_sha_with_its_version(self):
        for f in self.files():
            uses = re.findall(r"^\s*(?:-\s*)?uses:\s*(\S+)(.*)$", text(f), re.M)
            for ref, rest in uses:
                self.assertRegex(ref, r"^[\w.-]+/[\w./-]+@[0-9a-f]{40}$", f"{f.name}: {ref} phải ghim theo SHA đủ 40 ký tự")
                self.assertRegex(rest, r"#\s*v\d+\.\d+\.\d+", f"{f.name}: {ref} thiếu ghi chú phiên bản")

    def test_only_reviewed_actions_are_used(self):
        allowed = {"actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1"}     # v7.0.1 — kiểm bằng git ls-remote 06/11/2026
        for f in self.files():
            for ref in re.findall(r"uses:\s*(\S+)", text(f)):
                self.assertIn(ref, allowed, f"{f.name}: action {ref} chưa được rà")

    def test_no_secrets_no_untrusted_triggers_no_event_text_in_shell(self):
        for f in self.files():
            t = text(f)
            self.assertNotIn("pull_request_target", t, f.name)
            self.assertNotIn("workflow_run", t, f.name)
            self.assertNotRegex(t, r"secrets\.", f"{f.name}: không dùng secret nào")
            for expr in re.findall(r"\$\{\{(.*?)\}\}", t):
                self.assertRegex(expr.strip(), r"^github\.event_name == 'schedule' && 'scheduled' \|\| 'manual'$",
                                 f"{f.name}: biểu thức lạ trong workflow: {expr}")

    def test_default_permissions_are_empty_or_read_only_and_jobs_have_timeouts(self):
        for f in self.files():
            t = text(f)
            top = re.search(r"^permissions:\s*(.*)$", t, re.M)
            self.assertIsNotNone(top, f"{f.name}: thiếu permissions ở mức workflow")
            self.assertIn(top.group(1).strip(), ("{}", ""), f.name)
            self.assertNotIn("write-all", t)
            self.assertEqual(len(re.findall(r"^\s+timeout-minutes:\s*\d+", t, re.M)), len(re.findall(r"^\s+runs-on:", t, re.M)), f.name)
            self.assertNotIn("ubuntu-latest", t, f"{f.name}: ghim phiên bản máy chạy")


class CycleWorkflowTest(unittest.TestCase):
    def setUp(self):
        self.t = text(CYCLE)
        self.cfg = R.load().tn_config["schedule"]

    def test_schedule_matches_the_engine_slots(self):
        m = re.search(r'-\s*cron:\s*"(\d+) \*/(\d+) \* \* \*"\s*\n\s*timezone:\s*"([^"]+)"', self.t)
        self.assertIsNotNone(m, "lịch phải có dạng \"<phút> */<giờ> * * *\" kèm timezone")
        minute, every, tz = int(m.group(1)), int(m.group(2)), m.group(3)
        self.assertEqual((minute, every, tz), (self.cfg["rule_lane"]["minute"], self.cfg["rule_lane"]["every_hours"], self.cfg["timezone"]))
        self.assertEqual(self.cfg["rule_lane"].get("first_hour", 0) % every, 0)        # */2 bắt đầu từ giờ 0
        self.assertEqual(len(re.findall(r"-\s*cron:", self.t)), 1)
        self.assertEqual(24 // every, 12)

    def test_never_two_cycles_at_once_and_never_cancel_a_running_one(self):
        self.assertRegex(self.t, r"concurrency:\s*\n\s+group:\s*thien-nao-cycle\s*\n\s+cancel-in-progress:\s*false")

    def test_job_time_limit_is_shorter_than_the_lock(self):
        minutes = int(re.search(r"timeout-minutes:\s*(\d+)", self.t).group(1))
        self.assertLessEqual(minutes * 60, 2 * 3600 - 600)                 # xong trước mốc sau
        self.assertGreaterEqual(minutes * 60, self.cfg["cycle_time_budget_seconds"]["rule"])

    def test_write_permission_is_only_contents_for_the_state_branch(self):
        perms = re.findall(r"^\s+([a-z-]+):\s*(write)\s*(?:#.*)?$", self.t, re.M)
        self.assertEqual(perms, [("contents", "write")])

    def test_runs_the_approved_entry_point_with_tests(self):
        self.assertRegex(self.t, r"(?m)^\s+run:\s*python3 scripts/run_cycle\.py rule --run-tests\s*$")
        self.assertIn("workflow_dispatch", self.t)
        self.assertIn("DRAGON_MODE", self.t)


class CiWorkflowTest(unittest.TestCase):
    def test_ci_is_read_only_and_runs_tests_and_checks(self):
        t = text(CI)
        self.assertNotRegex(t, r":\s*write\b")
        self.assertRegex(t, r"contents:\s*read")
        self.assertIn("python3 -m unittest discover -s tests -t .", t)
        self.assertIn("python3 -m crown check", t)
        self.assertIn("persist-credentials: false", t)
        self.assertIn("pull_request", t)


if __name__ == "__main__":
    unittest.main()
