import io
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

from crown.cli import main


def run(*argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = main(list(argv))
    return code, out.getvalue(), err.getvalue()


class CliTest(unittest.TestCase):
    def setUp(self):
        self.old = os.environ.get("CROWN_DB")
        os.environ["CROWN_DB"] = os.path.join(tempfile.mkdtemp(), "cli.db")

    def tearDown(self):
        if self.old is None:
            os.environ.pop("CROWN_DB", None)
        else:
            os.environ["CROWN_DB"] = self.old

    def test_check_and_org(self):
        code, out, _ = run("check")
        self.assertEqual(code, 0)
        self.assertIn("20 Agent", out)
        code, out, _ = run("org")
        self.assertEqual(code, 0)
        self.assertLess(out.index("VUA_CEO"), out.index("CFO —"))
        self.assertIn("Agent ACTIVE: 10/20", out)

    def test_unknown_agent(self):
        code, _, err = run("agent", "A99")
        self.assertEqual(code, 1)

    def test_task_flow_rejections_exit_nonzero(self):
        code, out, _ = run("task", "new", "A11", "obj", "crit", "--actor", "VUA_CEO", "--ready", "--key", "k1")
        self.assertEqual(code, 0)
        tid = out.split()[0]
        code, out, _ = run("task", "claim", tid, "A11")
        self.assertEqual(code, 0)
        code, _, err = run("task", "claim", tid, "A18")
        self.assertEqual(code, 1)
        self.assertIn("TỪ CHỐI", err)


if __name__ == "__main__":
    unittest.main()
