"""Trạng thái bền của Thiên Não: SQLite ↔ bản kết xuất văn bản trên nhánh git tn-state.

Đẩy không ép: nếu nhánh đã đổi kể từ lúc đọc thì lần đẩy bị từ chối (so-sánh-rồi-ghi),
nên hai phiên không thể cùng thắng.
"""
import shutil
import sqlite3
import subprocess
import tempfile
import unittest
from pathlib import Path

from crown import registry as R
from crown import statesync as S
from crown.tasks import TaskStore


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


class Sandbox(unittest.TestCase):
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
        git(self.tmp, "clone", "-q", str(self.remote), str(d))
        return d

    def make_db(self, name="a.db"):
        store = TaskStore(self.tmp / name, registry=R.load())
        t = store.create(owner_agent_id="A11", objective="Sửa lỗi đồng bộ — có dấu tiếng Việt",
                         acceptance_criteria="test đạt", actor="THIEN_NAO", ready=True, idempotency_key="k1")
        tok = store.claim(t["task_id"], "A11")
        store.record(t["task_id"], "A11", tok, evidence="tests: 3/3 đạt")
        store.close()
        return self.tmp / name


class DumpRestoreTest(Sandbox):
    def test_round_trip_gives_identical_text(self):
        db = self.make_db()
        text = S.dump_sql(db)
        S.restore_sql(text, self.tmp / "b.db")
        self.assertEqual(S.dump_sql(self.tmp / "b.db"), text)

    def test_restored_ledger_still_works_and_stays_append_only(self):
        S.restore_sql(S.dump_sql(self.make_db()), self.tmp / "b.db")
        store = TaskStore(self.tmp / "b.db", registry=R.load())
        self.addCleanup(store.close)
        tasks = store.list()
        self.assertEqual(len(tasks), 1)
        self.assertIn("tiếng Việt", tasks[0]["objective"])
        self.assertEqual(tasks[0]["evidence_refs"], ["tests: 3/3 đạt"])
        with self.assertRaises(sqlite3.DatabaseError):
            store.db.execute("UPDATE events SET note='sửa lịch sử'")

    def test_restore_replaces_existing_database(self):
        db = self.make_db()
        text = S.dump_sql(db)
        other = self.make_db("c.db")
        con = sqlite3.connect(other)
        con.execute("CREATE TABLE junk(x)")
        con.commit(); con.close()
        S.restore_sql(text, other)
        self.assertEqual(S.dump_sql(other), text)

    def test_pack_then_unpack_checks_integrity(self):
        text = S.dump_sql(self.make_db())
        files = S.pack(text, run_id="rule-x")
        self.assertEqual(S.unpack(files), text)
        files[S.DUMP_PATH] = files[S.DUMP_PATH].replace("tests: 3/3", "tests: 9/9")
        with self.assertRaisesRegex(S.StateError, "sha256"):
            S.unpack(files)


class GitStateTest(Sandbox):
    def state(self, name):
        return S.GitState(self.clone(name), branch="tn-state")

    def test_first_pull_on_missing_branch_returns_none(self):
        self.assertIsNone(self.state("a").pull())

    def test_push_then_other_clone_reads_it(self):
        a = self.state("a")
        a.pull()
        self.assertTrue(a.push({"state/x.txt": "một\n"}, "phiên 1"))
        b = self.state("b")
        self.assertEqual(b.pull()["state/x.txt"], "một\n")

    def test_second_writer_loses_and_must_reread(self):
        a, b = self.state("a"), self.state("b")
        a.pull(); b.pull()
        self.assertTrue(a.push({"state/x.txt": "A\n"}, "A thắng"))
        self.assertFalse(b.push({"state/x.txt": "B\n"}, "B thua"))
        self.assertEqual(b.pull()["state/x.txt"], "A\n")       # đọc lại rồi mới được ghi
        self.assertTrue(b.push({"state/x.txt": "B sau A\n"}, "B ghi sau khi đọc lại"))
        self.assertEqual(a.pull()["state/x.txt"], "B sau A\n")

    def test_two_first_writers_on_empty_branch_cannot_both_win(self):
        a, b = self.state("a"), self.state("b")
        a.pull(); b.pull()
        self.assertTrue(a.push({"state/x.txt": "A\n"}, "A"))
        self.assertFalse(b.push({"state/x.txt": "B\n"}, "B"))

    def test_two_writers_with_identical_content_cannot_both_win(self):
        # Hai máy cùng trạng thái, cùng giây, cùng thông điệp: commit giống hệt nhau thì git coi là "đã có" và
        # cả hai cùng tưởng mình thắng. Mỗi người ghi phải để lại dấu riêng.
        for parent_exists in (False, True):
            with self.subTest(parent_exists=parent_exists):
                tag = "1" if parent_exists else "0"
                if parent_exists:
                    first = self.state("first")
                    first.pull()
                    self.assertTrue(first.push({"state/x.txt": "gốc\n"}, "gốc"))
                a, b = self.state("a" + tag), self.state("b" + tag)
                a.pull(); b.pull()
                same = {"state/x.txt": "giống nhau\n"}
                wins = [a.push(dict(same), "cùng thông điệp"), b.push(dict(same), "cùng thông điệp")]
                self.assertEqual(wins, [True, False])
                git(self.remote, "update-ref", "-d", "refs/heads/tn-state")

    def test_push_without_changes_makes_no_commit(self):
        a = self.state("a")
        a.pull()
        a.push({"state/x.txt": "một\n"}, "phiên 1")
        before = git(self.remote, "rev-parse", "refs/heads/tn-state")
        a.pull()
        self.assertTrue(a.push({"state/x.txt": "một\n"}, "không đổi"))
        self.assertEqual(git(self.remote, "rev-parse", "refs/heads/tn-state"), before)

    def test_push_never_touches_other_branches(self):
        a = self.state("a")
        master = git(self.remote, "rev-parse", "refs/heads/master")
        a.pull()
        a.push({"state/x.txt": "một\n"}, "phiên 1")
        self.assertEqual(git(self.remote, "rev-parse", "refs/heads/master"), master)
        self.assertEqual(git(a.repo, "status", "--porcelain"), "")   # không làm bẩn thư mục mã

    def test_only_state_paths_may_be_written(self):
        a = self.state("a")
        a.pull()
        with self.assertRaisesRegex(S.StateError, "state/"):
            a.push({"crown/policy.py": "x"}, "ghi nhầm chỗ")

    def test_unreachable_remote_is_an_error_not_an_empty_state(self):
        a = self.state("a")
        git(a.repo, "remote", "set-url", "origin", str(self.tmp / "khong-ton-tai.git"))
        with self.assertRaises(S.StateError):
            a.pull()


if __name__ == "__main__":
    unittest.main()
