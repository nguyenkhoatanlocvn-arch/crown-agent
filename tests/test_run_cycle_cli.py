"""scripts/run_cycle.py — lệnh mà bộ lập lịch gọi. Chạy thật bằng tiến trình con trên kho git giả lập,
đồng hồ thật. Không bao giờ gọi --run-tests ở đây (bộ test tự gọi lại chính nó sẽ lặp vô hạn)."""
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from crown import statesync as S

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "run_cycle.py"


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def load_script():
    spec = importlib.util.spec_from_file_location("run_cycle_script", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class CliSandbox(unittest.TestCase):
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

    def clone(self, name):
        d = self.tmp / name
        if not d.exists():
            git(self.tmp, "clone", "-q", str(self.remote), str(d))
        return d

    def cli(self, name, *args, env=None):
        e = dict(os.environ)
        e.pop("GITHUB_STEP_SUMMARY", None)
        e.update(env or {})
        r = subprocess.run([sys.executable, str(SCRIPT), args[0], "--repo", str(self.clone(name)),
                            "--db", str(self.tmp / f"{name}.db"), *args[1:]],
                           capture_output=True, text=True, encoding="utf-8", env=e, timeout=120)
        lines = [l for l in r.stdout.splitlines() if l.strip()]
        self.assertEqual(len(lines), 1, f"phải in đúng một dòng JSON:\n{r.stdout}\n{r.stderr}")
        return r.returncode, json.loads(lines[0])

    def remote_files(self):
        return S.GitState(self.clone("reader")).pull()


class RuleCliTest(CliSandbox):
    def test_rule_runs_once_then_refuses_the_same_slot(self):
        code, out = self.cli("a", "rule")
        self.assertEqual((code, out["ok"], out["result"]["status"]), (0, True, "SUCCEEDED"))
        self.assertEqual(out["result"]["summary"]["health"]["tests"], "SKIP")
        files = self.remote_files()
        self.assertIn(out["result"]["run_id"], files["state/REPORT.md"])
        code, again = self.cli("b", "rule", "--trigger", "manual")
        self.assertEqual((code, again["result"]["status"], again["result"]["reason"]), (0, "SKIP", "DUPLICATE"))

    def test_status_is_read_only(self):
        code, out = self.cli("a", "status")
        self.assertEqual((code, out["result"]["exists"]), (0, False))
        self.cli("a", "rule")
        n = git(self.remote, "rev-list", "--count", "refs/heads/tn-state")
        code, out = self.cli("b", "status")
        self.assertEqual((out["result"]["exists"], out["result"]["run"]["status"]), (True, "SUCCEEDED"))
        self.assertEqual(git(self.remote, "rev-list", "--count", "refs/heads/tn-state"), n)

    def test_stop_file_means_exit_zero_and_no_work(self):
        self.cli("a", "rule")
        st = S.GitState(self.clone("chairman"))
        st.pull()
        st.push({"state/STOP": "dừng\n"}, "STOP")
        code, out = self.cli("b", "rule", "--trigger", "manual")
        self.assertEqual((code, out["result"]["status"], out["result"]["reason"]), (0, "SKIP", "ESTOP"))
        self.assertIn("state/STOP", self.remote_files())

    def test_corrupt_state_exits_two_and_writes_nothing(self):
        self.cli("a", "rule")
        st = S.GitState(self.clone("vandal"))
        files = st.pull()
        st.push({"state/tn_state.sql": files["state/tn_state.sql"] + "-- sửa tay\n"}, "phá")
        n = git(self.remote, "rev-list", "--count", "refs/heads/tn-state")
        code, out = self.cli("b", "rule")
        self.assertEqual((code, out["ok"]), (2, False))
        self.assertIn("sha256", out["error"])
        self.assertEqual(git(self.remote, "rev-list", "--count", "refs/heads/tn-state"), n)

    def test_step_summary_gets_the_report(self):
        summary = self.tmp / "summary.md"
        code, out = self.cli("a", "rule", env={"GITHUB_STEP_SUMMARY": str(summary)})
        self.assertEqual(code, 0)
        self.assertIn(out["result"]["run_id"], summary.read_text(encoding="utf-8"))


class AiCliTest(CliSandbox):
    def test_ai_claim_then_finish(self):
        code, brief = self.cli("ai", "ai-claim", "--trigger", "manual")
        self.assertEqual((code, brief["result"]["action"]), (0, "RUN"))
        self.assertTrue(brief["result"]["hard_rules"])
        r = brief["result"]
        code, done = self.cli("ai", "ai-finish", "--run-id", r["run_id"], "--token", str(r["token"]),
                              "--status", "SUCCEEDED", "--summary-json", json.dumps({"note": "TEST"}))
        self.assertEqual((code, done["result"]["status"]), (0, "SUCCEEDED"))
        code, again = self.cli("ai2", "ai-claim", "--trigger", "manual")
        self.assertEqual(again["result"]["action"], "SKIP")

    def test_ai_finish_refuses_when_someone_else_wrote_the_state_meanwhile(self):
        code, brief = self.cli("ai", "ai-claim", "--trigger", "manual")
        r = brief["result"]
        st = S.GitState(self.clone("other"))
        files = st.pull()
        other_db = self.tmp / "other.db"
        S.restore_sql(S.unpack(files), other_db)
        import sqlite3
        con = sqlite3.connect(other_db)
        con.execute("INSERT INTO tn_control(key, value, updated_at, updated_by) VALUES ('x', '1', 't', 'ai-khac')")
        con.commit(); con.close()
        st.push(S.pack(S.dump_sql(other_db), run_id="ai-khac"), "bên khác ghi")
        n = git(self.remote, "rev-list", "--count", "refs/heads/tn-state")
        code, out = self.cli("ai", "ai-finish", "--run-id", r["run_id"], "--token", str(r["token"]), "--status", "SUCCEEDED")
        self.assertEqual((code, out["ok"]), (2, False))
        self.assertIn("không ghi đè", out["error"])
        self.assertEqual(git(self.remote, "rev-list", "--count", "refs/heads/tn-state"), n)

    def test_ai_finish_with_wrong_token_is_refused(self):
        code, brief = self.cli("ai", "ai-claim", "--trigger", "manual")
        r = brief["result"]
        code, out = self.cli("ai", "ai-finish", "--run-id", r["run_id"], "--token", str(r["token"] + 7), "--status", "SUCCEEDED")
        self.assertEqual((code, out["ok"]), (2, False))


class ParseTest(unittest.TestCase):
    def setUp(self):
        self.m = load_script()

    def test_ok_output(self):
        r = self.m.parse_unittest_output("....\n" + "-" * 70 + "\nRan 231 tests in 17.893s\n\nOK\n", 0)
        self.assertEqual((r["ran"], r["failed"], r["ok"]), (231, 0, True))

    def test_failed_output_counts_failures_and_errors(self):
        r = self.m.parse_unittest_output("Ran 5 tests in 0.1s\n\nFAILED (failures=1, errors=2, skipped=1)\n", 1)
        self.assertEqual((r["ran"], r["failed"], r["ok"]), (5, 3, False))
        self.assertIn("FAILED", r["tail"])

    def test_zero_tests_or_garbage_is_not_ok(self):
        self.assertFalse(self.m.parse_unittest_output("Ran 0 tests in 0.000s\n\nOK\n", 0)["ok"])     # không có test = không chứng minh gì
        self.assertFalse(self.m.parse_unittest_output("Traceback ...", 1)["ok"])
        self.assertFalse(self.m.parse_unittest_output("Ran 9 tests in 1s\n\nOK\n", 3)["ok"])         # mã thoát khác 0


if __name__ == "__main__":
    unittest.main()
