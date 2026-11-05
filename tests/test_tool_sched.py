"""Phiên AT: chống chạy chồng, tạm dừng, phân biệt lượt lịch / lượt tay, GitHub + loại trùng, hạn mức phát hành,
và khóa ghi giữa hai bản sao repo (hai phiên ở hai máy)."""
import shutil
import subprocess
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from tool.core.catalog import Catalog
from tool.core.gitsync import GitSync, SyncConflict
from tool.core.identity import Directory
from tool.core.jobs import JobStore
from tool.core.scheduler import Scheduler, classify_trigger, fingerprint
from tool.core.util import ToolError, read_json

from .tool_fixtures import make_build, make_root

T, AT = "agent-tool", "agent-tool-at"
UTC = timezone.utc


def at_vn(h, m):  # giờ Việt Nam → UTC
    return datetime(2026, 11, 6, h, m, tzinfo=UTC) - timedelta(hours=7)


class Clock:
    def __init__(self, now):
        self.now = now

    def __call__(self):
        return self.now


def git(cwd, *args):
    r = subprocess.run(["git", "-C", str(cwd), "-c", "user.name=t", "-c", "user.email=t@t", *args],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


class SessionTest(unittest.TestCase):
    def setUp(self):
        self.root = make_root()
        self.addCleanup(shutil.rmtree, self.root.parent.parent, True)
        self.clock = Clock(at_vn(9, 5))
        self.dir = Directory(self.root)
        self.s = Scheduler(self.root, self.dir, self.clock)

    def test_trigger_classification_separates_scheduler_from_manual(self):
        sch = self.s.schedule()
        self.assertEqual(classify_trigger(at_vn(9, 5), sch, None)[0], "scheduler")
        self.assertEqual(classify_trigger(at_vn(9, 7), sch, None)[0], "scheduler")
        self.assertEqual(classify_trigger(at_vn(9, 41), sch, None)[0], "manual")          # ngoài khung phút 05
        self.assertEqual(classify_trigger(at_vn(9, 5), sch, "MANUAL: Chủ tịch bấm Chạy ngay")[0], "manual")
        self.assertEqual(classify_trigger(at_vn(9, 5), sch, "WEB: RUN-123")[0], "web")
        a = self.s.begin(AT)
        self.assertEqual((a["trigger"], a["result"]), ("scheduler", None))
        self.assertIn("trong khung phút 05", a["trigger_basis"])

    def test_no_overlapping_sessions(self):
        a = self.s.begin(AT)
        self.clock.now += timedelta(minutes=10)
        b = self.s.begin(AT, marker="MANUAL: bấm lại")
        self.assertEqual(b["result"], "SKIPPED_OVERLAP")
        self.assertIn(a["run_id"], b["notes"][0])
        self.s.end(a["run_id"], "NO_CHANGE", "không có thay đổi")
        self.clock.now += timedelta(minutes=55)
        self.assertIsNone(self.s.begin(AT)["result"])

    def test_stale_lock_is_taken_over_and_old_session_cannot_write(self):
        a = self.s.begin(AT)
        self.clock.now += timedelta(minutes=61)        # phiên trước chết, không nhả khóa
        b = self.s.begin(AT)
        self.assertIsNone(b["result"])
        self.assertIn("tiếp quản", b["notes"][0])
        with self.assertRaisesRegex(ToolError, "không còn giữ khóa phiên"):
            self.s.note(a["run_id"], "ghi muộn")

    def test_pause_stops_scheduler_runs_but_not_manual(self):
        self.s.set_schedule(T, paused=True)
        self.assertEqual(self.s.begin(AT)["result"], "PAUSED")
        self.assertIsNone(self.s.begin(AT, marker="MANUAL: chạy tay khi tạm dừng")["result"])
        with self.assertRaisesRegex(ToolError, "không có quyền đổi lịch"):
            self.s.set_schedule("A18", paused=False)

    def test_at_most_one_auto_release_per_session(self):
        a = self.s.begin(AT)
        cat = Catalog(self.root, self.dir, self.clock)
        tmp = self.root.parent.parent
        cat.publish(make_build(tmp, version="1.0.0"), AT, session=self.s.get(a["run_id"]), auto=True)
        self.s.register_release(a["run_id"], "upper-text", "1.0.0", "OK")
        with self.assertRaisesRegex(ToolError, "hết hạn mức của phiên"):
            cat.publish(make_build(tmp, version="1.1.0"), AT, session=self.s.get(a["run_id"]), auto=True)
        with self.assertRaisesRegex(ToolError, "hết hạn mức của phiên"):
            self.s.register_release(a["run_id"], "upper-text", "1.1.0", "OK")
        self.assertEqual(self.s.end(a["run_id"], "RELEASED")["releases"][0]["version"], "1.0.0")
        with self.assertRaisesRegex(ToolError, "phải thuộc một phiên AT"):
            cat.publish(make_build(tmp, version="1.2.0"), AT, auto=True)

    def test_plan_prefers_backlog_then_new_source_then_no_change(self):
        a = self.s.begin(AT)
        self.assertEqual(self.s.plan(a["run_id"], {"changed": []})["action"], {"do": "NO_CHANGE"})
        gh = {"changed": [{"id": "jq", "from": "tag:jq-1.7", "to": "tag:jq-1.8", "key": "k1", "duplicate": False},
                          {"id": "ffmpeg", "from": "a", "to": "b", "key": "k0", "duplicate": True}]}
        p = self.s.plan(a["run_id"], gh)
        self.assertEqual((p["action"]["do"], p["action"]["id"], p["github_duplicates"]), ("EVALUATE_SOURCE", "jq", ["ffmpeg"]))
        jobs = JobStore(self.root, self.dir, self.clock)
        j = jobs.create(title="Việc tồn", prompt="làm", requested_by="CHAIRMAN", assigned_to=T)
        tok = jobs.claim(j["job_id"], T, ttl_seconds=60)
        jobs.step_start(j["job_id"], "intake", T, tok)
        self.clock.now += timedelta(minutes=5)          # Thiên Công ngừng giữa chừng
        p = self.s.plan(a["run_id"], gh)
        self.assertEqual(p["action"], {"do": "RESUME_JOB", "job_id": j["job_id"], "next_step": "intake", "previous_holder": T})


class GithubWatchTest(unittest.TestCase):
    def setUp(self):
        self.root = make_root()
        self.tmp = self.root.parent.parent
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.s = Scheduler(self.root, Directory(self.root), Clock(at_vn(9, 5)))
        self.up = self.tmp / "upstream"
        (self.up / "skills" / "demo").mkdir(parents=True)
        git(self.tmp, "init", "-q", "-b", "main", str(self.up))
        (self.up / "skills" / "demo" / "SKILL.md").write_text("v1\n")
        (self.up / "README.md").write_text("r1\n")
        git(self.up, "add", "-A"); git(self.up, "commit", "-q", "-m", "c1"); git(self.up, "tag", "n7.1")
        self.srcs = [{"id": "repo-head", "url": str(self.up), "mode": "head"},
                     {"id": "repo-tags", "url": str(self.up), "mode": "tags", "tag_pattern": "n*"},
                     {"id": "repo-path", "url": str(self.up), "mode": "path", "path": "skills/demo"}]

    def test_baseline_then_change_then_dedupe(self):
        first = self.s.check_github(self.srcs)
        self.assertEqual((len(first["checked"]), first["changed"], first["errors"]), (3, [], []))   # lần đầu = mốc gốc
        self.assertEqual(self.s.check_github(self.srcs)["changed"], [])                               # không đổi
        (self.up / "README.md").write_text("r2\n")       # đổi ngoài đường dẫn theo dõi, không có tag mới
        git(self.up, "add", "-A"); git(self.up, "commit", "-q", "-m", "c2")
        ch = self.s.check_github(self.srcs)["changed"]
        self.assertEqual([c["id"] for c in ch], ["repo-head"])
        self.assertFalse(ch[0]["duplicate"])
        self.s.mark_candidate("repo-head", ch[0]["key"], "REJECTED", "không liên quan TOOL")
        self.assertEqual(self.s.check_github(self.srcs)["changed"], [])    # đã xử lý: không lặp lại ở phiên sau
        (self.up / "skills" / "demo" / "SKILL.md").write_text("v2\n")
        git(self.up, "add", "-A"); git(self.up, "commit", "-q", "-m", "c3"); git(self.up, "tag", "n7.2"); git(self.up, "tag", "n8.0-rc1")
        ids = sorted(c["id"] for c in self.s.check_github(self.srcs)["changed"])
        self.assertEqual(ids, ["repo-head", "repo-path", "repo-tags"])
        self.assertTrue(fingerprint(self.srcs[1]).startswith("tag:n7.2@"))   # bỏ qua bản rc

    def test_unreachable_source_is_reported_not_fatal(self):
        out = self.s.check_github([{"id": "mat", "url": str(self.tmp / "khong-co"), "mode": "head"}])
        self.assertEqual((out["checked"], out["changed"], [e["id"] for e in out["errors"]]), ([], [], ["mat"]))


class TwoClonesTest(unittest.TestCase):
    """Hai phiên ở hai bản sao repo, chung một remote: đúng một bên ghi được."""

    def setUp(self):
        self.tmp = Path(make_root()).parent.parent
        self.addCleanup(shutil.rmtree, self.tmp, True)
        shutil.rmtree(self.tmp / "repo")
        seed = self.tmp / "seed"
        root = seed / "tool"
        for d in ("workbench", "catalog", "runs/locks", "runs/sessions", "config", "agents/sub", "inbox", "skills"):
            (root / d).mkdir(parents=True)
            (root / d / ".keep").write_text("")
        git(self.tmp, "init", "-q", "-b", "main", str(seed))
        self.jid = JobStore(root).create(title="Chung", prompt="một việc", requested_by="CHAIRMAN", assigned_to=T)["job_id"]
        git(seed, "add", "-A"); git(seed, "commit", "-q", "-m", "seed")
        git(self.tmp, "clone", "-q", "--bare", str(seed), str(self.tmp / "remote.git"))
        self.a, self.b = self.tmp / "A", self.tmp / "B"
        for c in (self.a, self.b):
            git(self.tmp, "clone", "-q", str(self.tmp / "remote.git"), str(c))
        self.ga, self.gb = GitSync(self.a, branch="main"), GitSync(self.b, branch="main")

    def test_two_sessions_claim_same_job_only_one_holds_it(self):
        ja, jb = JobStore(self.a / "tool"), JobStore(self.b / "tool")
        self.gb.pull()                                   # B đã có bản mới nhất trước khi A ghi
        with self.ga.tx("A nhận việc"):
            ja.claim(self.jid, T)
        jb_local_before = jb.get(self.jid)["state"]
        self.assertEqual(jb_local_before, "READY")       # B chưa thấy A
        with self.assertRaises(ToolError) as cm:
            with self.gb.tx("B nhận việc"):              # tx kéo bản mới → thấy A đang giữ → từ chối
                jb.claim(self.jid, AT)
        self.assertIn("một việc chỉ một người ghi", str(cm.exception))
        self.assertEqual(jb.get(self.jid)["lease"]["holder"], T)

    def test_push_race_loser_is_rolled_back(self):
        ja, jb = JobStore(self.a / "tool"), JobStore(self.b / "tool")
        # Cả hai đã kéo, cùng sửa cục bộ, A đẩy trước; B đẩy sau phải thua và bị hủy thay đổi.
        self.ga.pull(); self.gb.pull()
        start_b = self.gb.head()
        ja.claim(self.jid, T)
        jb.claim(self.jid, AT)
        self.ga.commit(["tool"], "A nhận"); self.ga.push(self.ga.head(), ["tool"])
        self.gb.commit(["tool"], "B nhận")
        with self.assertRaises(SyncConflict):
            self.gb.push(start_b, ["tool"])
        self.assertEqual(jb.get(self.jid)["lease"]["holder"], T)      # B nhận bản của bên thắng
        self.assertEqual(self.gb.dirty(["tool"]), [])

    def test_two_sessions_publish_same_version_only_one_lands(self):
        ca, cb = Catalog(self.a / "tool"), Catalog(self.b / "tool")
        self.ga.pull(); self.gb.pull()
        start_b = self.gb.head()
        ca.publish(make_build(self.tmp / "ba"), T)
        cb.publish(make_build(self.tmp / "bb"), AT)                    # cục bộ cả hai đều tưởng mình thắng
        self.ga.commit(["tool"], "A phát hành"); self.ga.push(self.ga.head(), ["tool"])
        self.gb.commit(["tool"], "B phát hành")
        with self.assertRaises(SyncConflict):
            self.gb.push(start_b, ["tool"])
        t = read_json(self.b / "tool" / "catalog" / "upper-text" / "tool.json")
        self.assertEqual([(v["version"], v["published_by"]) for v in t["versions"]], [("1.0.0", T)])
        log = git(self.tmp / "remote.git", "log", "--oneline", "main")
        self.assertEqual(log.count("phát hành"), 1)                    # remote chỉ có một bản phát hành

    def test_uncommitted_session_files_survive_a_later_transaction(self):
        """Bản ghi cục bộ của phiên (chưa commit) không bị mất khi giao dịch sau đó thua hoặc lỗi."""
        ja = JobStore(self.a / "tool")
        note = self.a / "tool" / "runs" / "sessions" / "AT-x.json"
        note.write_text("{}", encoding="utf-8")                      # thay đổi dở, chưa commit
        with self.assertRaises(ToolError):
            with self.ga.tx("thử lỗi"):
                ja.claim(self.jid, T)
                raise ToolError("hỏng")
        self.assertTrue(note.is_file())
        with self.ga.tx("ghi tiếp"):
            ja.claim(self.jid, T)
        self.gb.pull()
        self.assertTrue((self.b / "tool" / "runs" / "sessions" / "AT-x.json").is_file())   # đã lên remote, bên kia thấy

    def test_pending_local_work_is_replayed_on_top_of_remote_changes_without_git_identity(self):
        """Máy mới chưa cấu hình git: phiên có tệp dở + remote đã tiến thêm → giao dịch vẫn nối được và đẩy được."""
        import os
        env_backup = {k: os.environ.get(k) for k in ("HOME", "GIT_CONFIG_GLOBAL", "GIT_CONFIG_NOSYSTEM")}
        os.environ.update(HOME=str(self.tmp / "nohome"), GIT_CONFIG_GLOBAL=str(self.tmp / "none.gitconfig"), GIT_CONFIG_NOSYSTEM="1")
        self.addCleanup(lambda: [os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v) for k, v in env_backup.items()])
        ja, jb = JobStore(self.a / "tool"), JobStore(self.b / "tool")
        with self.ga.tx("A nhận việc"):                       # remote tiến thêm
            ja.claim(self.jid, T)
        (self.b / "tool" / "runs" / "sessions" / "AT-b.json").write_text("{}", encoding="utf-8")   # B có tệp dở
        j2 = None
        with self.gb.tx("B tạo việc khác"):                   # tx: commit tệp dở → kéo (rebase lên bản của A) → sửa → đẩy
            j2 = jb.create(title="Việc B", prompt="việc khác", requested_by="CHAIRMAN", assigned_to=AT)["job_id"]
        self.ga.pull()
        self.assertTrue((self.a / "tool" / "runs" / "sessions" / "AT-b.json").is_file())
        self.assertEqual(ja.get(j2)["title"], "Việc B")
        self.assertEqual(jb.get(self.jid)["lease"]["holder"], T)

    def test_failed_body_leaves_no_partial_state(self):
        ja = JobStore(self.a / "tool")
        with self.assertRaises(ToolError):
            with self.ga.tx("thử lỗi"):
                ja.claim(self.jid, T)
                raise ToolError("hỏng giữa chừng")
        self.assertEqual(ja.get(self.jid)["state"], "READY")
        self.assertEqual(self.ga.dirty(["tool"]), [])


if __name__ == "__main__":
    unittest.main()
