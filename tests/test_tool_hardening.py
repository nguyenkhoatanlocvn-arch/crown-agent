"""Các lỗ hổng do đợt kiểm định độc lập 2026-11-06 tìm ra — mỗi lỗ một ca canh giữ.

Nhóm: mã định danh dùng làm đường dẫn; cổng phát hành qua sổ việc; quay lui đúng phiên bản; quyền dữ liệu;
bộ soát quyền của vùng cách ly; hộp nhận mẫu; đồng bộ git giữa hai bản sao.
"""
import json
import os
import shutil
import subprocess
import time
import unittest
import zipfile
from pathlib import Path

from tool.core import release, sandbox
from tool.core.access import Access, secret_reason
from tool.core.agents import Mailbox
from tool.core.catalog import Catalog, validate_manifest
from tool.core.gitsync import GitSync, SyncConflict, SyncFailed
from tool.core.identity import Directory
from tool.core.inbox import Inbox
from tool.core.jobs import JobStore
from tool.core.runner import Runner
from tool.core.scheduler import Scheduler
from tool.core.subagents import SubAgents
from tool.core.util import ToolError, read_json, sha256_file

from .tool_fixtures import AT, T, job_at_publish, make_build, make_root, publish_via_job


def git(cwd, *args, ok=True):
    r = subprocess.run(["git", "-C", str(cwd), "-c", "user.name=t", "-c", "user.email=t@t", *args], capture_output=True, text=True)
    if ok:
        assert r.returncode == 0, r.stderr
    return r.stdout.strip()


class Base(unittest.TestCase):
    def setUp(self):
        self.root = make_root()
        self.tmp = self.root.parent.parent
        self.repo = self.tmp / "repo"
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.dir = Directory(self.root)
        self.jobs, self.subs = JobStore(self.root, self.dir), SubAgents(self.root, self.dir)
        self.cat, self.runner = Catalog(self.root, self.dir), Runner(self.root, self.dir)
        self.acc = Access(self.root, self.dir)

    def data(self, relpath, text="chào anh Lộc\n"):
        p = self.repo / relpath
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        return p

    def new_job(self, actor=T):
        jid = self.jobs.create(title="Việc thử", prompt="làm", requested_by="CHAIRMAN", assigned_to=actor)["job_id"]
        return jid, self.jobs.claim(jid, actor)


class IdsAreNotPathsTest(Base):
    def test_caller_and_sub_ids_cannot_be_paths(self):
        jid, _ = self.new_job()
        lt = self.subs.spawn(branch="lt", assigned_by=T, job_id=jid, output="build/")["id"]
        ghost = self.tmp / "ghost.json"                       # hồ sơ tự chế nằm ngoài sổ, tự cấp vùng ghi "mọi nơi"
        ghost.write_text(json.dumps({"id": str(ghost)[:-5], "name": "x", "state": "ACTIVE", "expires_at": "2099-01-01T00:00:00+00:00",
                                     "write_scope": [""]}), encoding="utf-8")   # tự khai ID trùng đúng đường dẫn của mình
        for bad in (f"./{lt}", f"../sub/{lt}", str(self.root / "agents" / "sub" / lt), str(ghost)[:-5], "..", "a/b", ".an", ""):
            with self.assertRaises(ToolError, msg=bad):
                self.dir.resolve(bad)
        self.assertEqual(self.dir.resolve(lt)["kind"], "subagent")
        (self.root / "agents" / "sub" / "gia-mao.json").write_text(json.dumps({"id": "khac", "write_scope": ["tool/"]}), encoding="utf-8")
        with self.assertRaisesRegex(ToolError, "không khớp ID"):       # tên tệp và ID bên trong phải trùng
            self.dir.resolve("gia-mao")

    def test_same_subagent_under_another_spelling_cannot_be_its_own_tester(self):
        jid, tok = self.new_job()
        for st in ("intake", "research", "analyze", "spec"):
            self.jobs.step_start(jid, st, T, tok); self.jobs.step_done(jid, st, T, tok, ["e"])
        lt = self.subs.spawn(branch="lt", assigned_by=T, job_id=jid, output="build/")["id"]
        self.jobs.step_start(jid, "build", T, tok, sub=lt)
        make_build(self.tmp, dest=self.jobs.job_dir(jid) / "build")
        self.jobs.step_done(jid, "build", T, tok, ["build/manifest.json"])
        for alias in (f"../sub/{lt}", f"./{lt}", lt):
            with self.assertRaises(ToolError, msg=alias):
                self.jobs.step_start(jid, "test", T, tok, sub=alias)
        with self.assertRaisesRegex(ToolError, "không phải Agent con của TOOL"):   # Agent thường không đóng vai người kiểm thử
            self.jobs.step_start(jid, "test", T, tok, sub="A15")
        other, _ = self.new_job(AT)
        stray = self.subs.spawn(branch="kt", assigned_by=AT, job_id=other, output="x")["id"]
        with self.assertRaisesRegex(ToolError, "được giao việc"):                  # Agent con của việc khác
            self.jobs.step_start(jid, "test", T, tok, sub=stray)
        self.subs.finish(stray, AT, True, "xong")
        wrong = self.subs.spawn(branch="pt", assigned_by=T, job_id=jid, output="x")["id"]
        with self.assertRaisesRegex(ToolError, "thuộc nhánh 'kt'"):
            self.jobs.step_start(jid, "test", T, tok, sub=wrong)

    def test_message_session_job_and_tool_ids_are_validated_before_touching_files(self):
        jid, tok = self.new_job(AT)
        before = (self.jobs.job_dir(jid) / "job.json").read_bytes()
        with self.assertRaisesRegex(ToolError, "Mã tin không hợp lệ"):
            Mailbox(self.root, self.dir).close(T, f"../../../workbench/{jid}/job", T, note="x")
        self.assertEqual((self.jobs.job_dir(jid) / "job.json").read_bytes(), before)
        self.jobs.heartbeat(jid, AT, tok)                                          # người giữ việc không bị ảnh hưởng
        with self.assertRaisesRegex(ToolError, "Mã phiên không hợp lệ"):
            Scheduler(self.root, self.dir).get("../locks/at-session")
        for bad in ("..", "../../x", "a/b", ".hidden", "", "TJ-x", "con.lock"):
            with self.assertRaisesRegex(ToolError, "Mã việc không hợp lệ", msg=bad):
                self.jobs.job_dir(bad)
            with self.assertRaisesRegex(ToolError, "Mã việc không hợp lệ", msg=bad):
                Inbox(self.root).add(bad, "một prompt")
        with self.assertRaisesRegex(ToolError, "Kho không có tool"):
            self.cat.rollback("../../../lac-ngoai/x", T, "thử")
        self.assertFalse((self.tmp / "lac-ngoai").exists())
        self.assertFalse((self.repo / "lac-ngoai").exists())

    def test_subagent_scope_is_a_real_job_and_ends_when_the_subagent_ends(self):
        jid, _ = self.new_job()
        for bad_job in ("*", "TJ-20261106-aaaaaa", "../x"):
            with self.assertRaisesRegex(ToolError, "Không có việc", msg=bad_job):
                self.subs.spawn(branch="lt", assigned_by=T, job_id=bad_job, output="o")
        for bad_scope in (["tool/"], ["crown/"], ["tool/workbench/*/"], ["tool/../crown/x/"], [""]):
            with self.assertRaisesRegex(ToolError, "Vùng ghi của Agent con", msg=str(bad_scope)):
                self.subs.spawn(branch="lt", assigned_by=T, job_id=jid, output="o", write_scope=bad_scope)
        sid = self.subs.spawn(branch="kt", assigned_by=T, job_id=jid, output="o")["id"]
        target = self.jobs.job_dir(jid) / "ghi.txt"
        self.assertTrue(self.acc.decide(sid, target, "write")["allow"])
        self.subs.finish(sid, T, False, "hỏng")
        self.assertFalse(self.acc.decide(sid, target, "write")["allow"])          # xong/hỏng/quá hạn: hết quyền ghi

    def test_verified_done_needs_a_real_release_from_this_job(self):
        jid, tok = self.new_job()
        with self.assertRaisesRegex(ToolError, "Mã tool không hợp lệ"):
            self.jobs.set_fields(jid, T, tok, tool_id="../x")
        j2, t2, build = job_at_publish(self.root, AT)
        self.cat.publish(build, AT, job_id=j2, token=t2)
        self.jobs.step_done(j2, "publish", AT, t2, ["catalog/upper-text/1.0.0/RELEASE.json"])
        self.jobs.step_start(j2, "verify", AT, t2); self.jobs.step_done(j2, "verify", AT, t2, ["run.json"])
        self.jobs.set_fields(j2, AT, t2, tool_id="ghost-tool", version="9.9.9")
        with self.assertRaisesRegex(ToolError, "Kho không có tool"):
            self.jobs.move(j2, "VERIFIED_DONE", AT, t2)
        self.jobs.set_fields(j2, AT, t2, tool_id="upper-text", version="1.0.0")
        self.assertEqual(self.jobs.move(j2, "VERIFIED_DONE", AT, t2)["state"], "VERIFIED_DONE")


class PublishGateTest(Base):
    def test_no_path_into_the_catalog_outside_a_running_job(self):
        with self.assertRaisesRegex(ToolError, "phải kèm mã việc và fencing token"):
            self.cat.publish(make_build(self.tmp), T)
        jid, tok, build = job_at_publish(self.root)
        with self.assertRaisesRegex(ToolError, "token cũ hoặc khác người"):
            self.cat.publish(build, AT, job_id=jid, token=tok)                     # không phải người giữ việc
        with self.assertRaisesRegex(ToolError, "token cũ hoặc khác người"):
            self.cat.publish(build, T, job_id=jid, token=tok + 1)
        with self.assertRaisesRegex(ToolError, "Chỉ phát hành bản dựng của chính việc này"):
            self.cat.publish(make_build(self.tmp), T, job_id=jid, token=tok)       # bản dựng khác đội lốt việc đã kiểm
        (build / "main.py").write_text((build / "main.py").read_text(encoding="utf-8") + "\n# đổi sau khi kiểm\n", encoding="utf-8")
        with self.assertRaisesRegex(ToolError, "đã thay đổi sau khi kiểm thử độc lập"):
            self.cat.publish(build, T, job_id=jid, token=tok)
        self.jobs.move(jid, "CANCELLED", "CHAIRMAN", note="Chủ tịch dừng")         # quyền dừng cuối cùng
        make_build(self.tmp, dest=build)
        with self.assertRaisesRegex(ToolError, "không ở RUNNING"):
            self.cat.publish(build, T, job_id=jid, token=tok)
        self.assertEqual(self.cat.tools(), [])

    def test_changed_build_must_go_back_through_build_and_independent_test(self):
        jid, tok, build = job_at_publish(self.root)
        m = read_json(build / "manifest.json")
        m["version"] = "1.0.1"
        (build / "manifest.json").write_text(json.dumps(m, ensure_ascii=False), encoding="utf-8")
        with self.assertRaisesRegex(ToolError, "đã thay đổi sau khi kiểm thử độc lập"):
            self.cat.publish(build, T, job_id=jid, token=tok)
        with self.assertRaisesRegex(ToolError, "token cũ hoặc khác người"):
            self.jobs.reopen(jid, "build", AT, tok, "không phải người giữ")
        with self.assertRaisesRegex(ToolError, "phải ghi lý do"):
            self.jobs.reopen(jid, "build", T, tok, " ")
        job = self.jobs.reopen(jid, "build", T, tok, "đổi sang 1.0.1")
        self.assertEqual([s["status"] for s in job["steps"]][4:], ["PENDING"] * 4)
        self.assertNotIn("build_digest", next(s for s in job["steps"] if s["id"] == "test"))
        with self.assertRaisesRegex(ToolError, "Chưa có bước kiểm thử độc lập đạt|chưa được agent-tool bắt đầu|Bước kế tiếp"):
            self.jobs.step_start(jid, "publish", T, tok)
        lt = self.subs.spawn(branch="lt", assigned_by=T, job_id=jid, output="build/")["id"]
        self.jobs.step_start(jid, "build", T, tok, sub=lt); self.jobs.step_done(jid, "build", T, tok, ["build/manifest.json"])
        self.subs.finish(lt, T, True, "xong")
        kt = self.subs.spawn(branch="kt", assigned_by=T, job_id=jid, output="TEST_REPORT.json")["id"]
        self.jobs.step_start(jid, "test", T, tok, sub=kt); self.jobs.step_done(jid, "test", T, tok, ["TEST_REPORT.json"])
        self.jobs.step_start(jid, "publish", T, tok)
        self.assertEqual(self.cat.publish(build, T, job_id=jid, token=tok)["version"], "1.0.1")

    def test_crashed_publish_does_not_block_the_version_forever(self):
        tdir = self.root / "catalog" / "upper-text"
        (tdir / ".stage-1.0.0").mkdir(parents=True)
        claim = tdir / ".claim-1.0.0"
        claim.write_text("agent-tool 2026-11-05T00:00:00+00:00\n", encoding="utf-8")
        with self.assertRaisesRegex(ToolError, "không phát hành trùng"):          # dấu còn mới: có thể bên kia đang phát hành
            publish_via_job(self.root)
        old = time.time() - 3600
        os.utime(claim, (old, old))                                                # dấu cũ, chưa thành bản phát hành: tiến trình đã chết
        rel = publish_via_job(self.root)
        self.assertEqual(rel["version"], "1.0.0")
        self.assertFalse((tdir / ".stage-1.0.0").exists())
        os.utime(claim, (old, old))
        with self.assertRaisesRegex(ToolError, "không phát hành trùng"):          # đã phát hành thật: dấu cũ cũng không cho ghi đè
            publish_via_job(self.root)

    def test_release_table_itself_is_pinned(self):
        publish_via_job(self.root)
        v = self.cat.version_dir("upper-text", "1.0.0")
        (v / "main.py").write_text((v / "main.py").read_text(encoding="utf-8").replace("text.upper()", "text.upper() + 'SUA LEN'"), encoding="utf-8")
        rel = read_json(v / "RELEASE.json")
        rel["files"]["main.py"] = sha256_file(v / "main.py")                       # sửa mã rồi sửa luôn bảng băm
        (v / "RELEASE.json").write_text(json.dumps(rel), encoding="utf-8")
        rec = self.runner.run("upper-text", "A19", {"input": self.data("research/a.txt")})
        self.assertEqual(rec["status"], "ERROR")
        self.assertIn("RELEASE.json đã bị sửa", rec["error"])

    def test_build_with_secret_like_files_or_runaway_limits_is_refused(self):
        b = make_build(self.tmp)
        (b / ".env").write_text("KHOA=abc\n", encoding="utf-8")
        (b / "id_rsa").write_text("k\n", encoding="utf-8")
        m = read_json(b / "manifest.json")
        m["limits"] = {"timeout_s": 10 ** 6, "memory_mb": 10 ** 6, "max_output_mb": True}
        errs = validate_manifest(m, b)
        for word in ("tệp giống bí mật: .env", "tệp giống bí mật: id_rsa", "limits.timeout_s", "limits.memory_mb", "limits.max_output_mb"):
            self.assertTrue(any(word in e for e in errs), (word, errs))


class RollbackTest(Base):
    def setUp(self):
        super().setUp()
        self.case_in = self.data("research/khac.txt", "abc\n")
        self.case_bad = self.data("research/khac.sai.txt", "SAI\n")
        self.case_ok = self.data("research/khac.dung.txt", "ABC\n")

    def versions(self):
        t = self.cat.tool("upper-text")
        return t["current_version"], [(v["version"], v["status"]) for v in t["versions"]]

    def test_smoke_withdraws_only_the_version_it_tested(self):
        publish_via_job(self.root, version="2.0.0")
        publish_via_job(self.root, version="1.0.1")                                # nhánh vá, thấp hơn bản hiện hành
        res = release.smoke("upper-text@1.0.1", "A19", root=self.root, rollback_actor=AT,
                            extra=[{"inputs": {"input": self.case_in}, "expected": {"khac.txt": self.case_bad}}])
        self.assertFalse(res["ok"])
        self.assertEqual(res["rolled_back"], {"tool_id": "upper-text", "withdrawn": "1.0.1", "current_version": "2.0.0"})
        self.assertEqual(self.versions(), ("2.0.0", [("2.0.0", "GOOD"), ("1.0.1", "WITHDRAWN")]))

    def test_harness_mistakes_never_withdraw_a_good_release(self):
        publish_via_job(self.root)
        with self.assertRaisesRegex(ToolError, "không thấy tệp"):                  # gõ sai đường dẫn ca thử
            release.smoke("upper-text", "A19", root=self.root, rollback_actor=AT,
                          extra=[{"inputs": {"input": "/khong/co/tep.txt"}, "expected": {"x.txt": self.case_ok}}])
        with self.assertRaisesRegex(ToolError, "không có trong danh bạ"):          # người gọi thử lạ
            release.smoke("upper-text", "A99", root=self.root, rollback_actor=AT)
        with self.assertRaisesRegex(ToolError, "không có quyền khôi phục"):        # người thu hồi không phải Agent điều hành
            release.smoke("upper-text", "A19", root=self.root, rollback_actor="A15")
        denied = release.smoke("upper-text", "A19", root=self.root, rollback_actor=AT,   # đầu vào ngoài vùng đọc của người gọi
                               extra=[{"inputs": {"input": self.data("agents/A09/x.txt")}, "expected": {"x.txt": self.case_ok}}])
        self.assertEqual((denied["ok"], denied["harness_error"], denied["rolled_back"]), (False, True, None))
        self.assertEqual(self.versions(), ("1.0.0", [("1.0.0", "GOOD")]))

    def test_plain_rollback_falls_back_to_the_best_remaining_good_version(self):
        for v in ("1.0.0", "1.1.0", "1.2.0"):
            publish_via_job(self.root, version=v)
        self.cat.rollback("upper-text", T, "lỗi", to_version="1.0.0")              # bỏ 1.2.0, chủ động về 1.0.0
        out = self.cat.rollback("upper-text", T, "1.0.0 cũng lỗi")
        self.assertEqual(out["current_version"], "1.1.0")                          # không rơi về "không còn bản nào" khi 1.1.0 vẫn tốt
        with self.assertRaisesRegex(ToolError, "không phải bản đang gọi được"):
            self.cat.rollback("upper-text", T, "thu hồi lần hai", version="1.2.0")
        with self.assertRaisesRegex(ToolError, "--to chỉ dùng khi thu hồi bản hiện hành"):
            publish_via_job(self.root, version="1.3.0")
            self.cat.rollback("upper-text", T, "x", to_version="1.3.0", version="1.1.0")


class AccessTest(Base):
    def test_secret_dirs_system_dirs_and_home_dotfiles_are_off_limits(self):
        for p, word in ((self.tmp / "ngoai" / ".ssh", "thư mục bảo vệ"), (self.repo / ".git", "thư mục bảo vệ"),
                        (self.repo / ".git" / "hooks", "thư mục bảo vệ"), (Path.home() / ".cache" / "x", "mục ẩn trong thư mục nhà"),
                        (Path("/etc/cron.d"), "thư mục hệ thống"), (self.tmp / "ngoai" / ".config" / "gh", "thư mục bảo vệ")):
            for who in ("A19", "CHAIRMAN"):
                d = self.acc.decide(who, p, "write")
                self.assertFalse(d["allow"], (who, p)); self.assertIn(word, d["rule"])
        for p in ("/proc/self/environ", "/etc/shadow", str(Path.home() / ".netrc"), str(Path.home() / ".config" / "gh" / "hosts.yml"),
                  str(Path.home() / ".claude.json"), str(Path.home() / ".docker" / "config.json"), str(self.tmp / "token.json"),
                  str(self.tmp / "x" / ".git-credentials")):
            self.assertFalse(self.acc.decide("CHAIRMAN", p, "read")["allow"], p)
        self.assertTrue(self.acc.decide("A19", self.tmp / "lam_viec" / "a.srt", "read")["allow"])   # tệp làm việc bình thường vẫn được

    def test_tool_output_names_are_checked_before_anything_is_written(self):
        def leaky(b):                                                             # tool tự đặt tên tệp kết quả giống bí mật
            code = (b / "main.py").read_text(encoding="utf-8")
            (b / "main.py").write_text(code + '\n(Path(req["out_dir"]) / "api_key.txt").write_text("x", encoding="utf-8")\n', encoding="utf-8")
        publish_via_job(self.root, mutate=leaky)
        dest = self.repo / "research" / "out"
        rec = self.runner.run("upper-text", "A19", {"input": self.data("research/a.txt")}, out_dir=dest)
        self.assertEqual(rec["status"], "DENIED")
        self.assertIn("bí mật", rec["error"])
        self.assertFalse(dest.exists())                                            # không tệp nào được ghi ra

    def test_read_rights_never_imply_write_rights(self):
        (self.root / "config" / "access.json").write_text(json.dumps({"grants": {"A15": {"read": ["agents/A01/"]}}}), encoding="utf-8")
        acc = Access(self.root, self.dir)
        a01 = self.data("agents/A01/ke_hoach.txt")
        self.assertTrue(acc.decide("A15", a01, "read")["allow"])
        self.assertFalse(acc.decide("A15", a01.parent / "out", "write")["allow"])                    # được cấp đọc ≠ được ghi
        self.assertTrue(acc.decide("CFO", self.data("agents/A09/so.txt"), "read")["allow"])
        self.assertFalse(acc.decide("CFO", self.repo / "agents" / "A09" / "out", "write")["allow"])  # lãnh đạo không ghi vào vùng cấp dưới
        for sysdir in ("tool/catalog/upper-text/1.0.0", "tool/core", "tool/config", "registries", "tool/agents/agent-tool-at"):
            for who in (T, "CHAIRMAN"):
                self.assertFalse(acc.decide(who, self.repo / sysdir, "write")["allow"], (who, sysdir))

    def test_private_samples_are_not_shared(self):
        jid, _ = self.new_job()
        f = self.root / "inbox" / jid / "private" / "mau.txt"
        f.parent.mkdir(parents=True); f.write_text("mẫu riêng của Chủ tịch\n", encoding="utf-8")
        frame = self.jobs.job_dir(jid) / "frames" / "f_000.png"
        frame.parent.mkdir(parents=True); frame.write_bytes(b"png")
        for p in (f, frame):
            self.assertFalse(self.acc.decide("A19", p, "read")["allow"], p)       # Agent thường: không
            self.assertTrue(self.acc.decide(AT, p, "read")["allow"], p)           # Agent điều hành TOOL: có
        pt = self.subs.spawn(branch="pt", assigned_by=T, job_id=jid, output="OBSERVATION.md")["id"]
        self.assertTrue(self.acc.decide(pt, f, "read")["allow"])                  # Agent con được giao đúng việc đó: có
        other, _ = self.new_job(AT)
        stranger = self.subs.spawn(branch="pt", assigned_by=AT, job_id=other, output="x")["id"]
        self.assertFalse(self.acc.decide(stranger, f, "read")["allow"])           # Agent con của việc khác: không
        self.assertTrue(self.acc.decide("A19", self.data("tool/inbox/" + jid + "/files/cong_khai.txt"), "read")["allow"])


class SandboxLintTest(Base):
    def lint(self, code, perms=None):
        b = make_build(self.tmp / "lint", permissions=perms)
        (b / "main.py").write_text(code, encoding="utf-8")
        return sandbox.lint_permissions(b, read_json(b / "manifest.json"))

    def test_process_and_network_use_is_caught_under_any_spelling(self):
        for code in ("from os import system\nsystem('id')\n", "import os as o\no.system('id')\n", "import os\nos.posix_spawn('/bin/id', ['id'], {})\n",
                     "import os\nf = os.system\nf('id')\n", "import os\ngetattr(os, 'sys' + 'tem')('id')\n", "from os import *\n",
                     "import sys\nsys.modules['os'].system('id')\n", "from sys import modules\n", "import _socket\n",
                     "import _posixsubprocess\n", "import nntplib\n", "import builtins\n", "__builtins__['eval']('1')\n",
                     "from posix import system\n", "import os\nos.execv('/bin/sh', ['sh'])\n"):
            self.assertTrue(self.lint(code), code)
        self.assertEqual(self.lint("import os, sys, json, re\nfrom pathlib import Path\nprint(os.path.join('a', 'b'), os.sep)\n"), [])
        self.assertEqual(self.lint("import os\nos.system('ffmpeg -version')\n", {"network": False, "subprocess": ["ffmpeg"]}), [])

    def test_only_declared_commands_are_on_path(self):
        work = self.tmp / "work"
        work.mkdir()
        env = sandbox.clean_env(work, ["true", "khong-co-lenh-nay", "../sh", "/bin/sh"])
        self.assertEqual(env["PATH"], str(work / "bin"))
        self.assertEqual(sorted(p.name for p in (work / "bin").iterdir()), ["true"])
        w2 = self.tmp / "w2"
        w2.mkdir()
        self.assertEqual(list(sandbox._bin_dir(w2, []).iterdir()), [])            # không khai lệnh nào: PATH rỗng


class InboxTest(Base):
    def setUp(self):
        super().setUp()
        self.jid, _ = self.new_job()
        self.ib = Inbox(self.root)

    def test_folder_intake_skips_secret_dirs_and_symlinks(self):
        src = self.tmp / "mau"
        for rel in (".git/config", ".ssh/config", ".aws/config", ".netrc", ".npmrc", "config/token.json", ".env", "src/a.txt"):
            (src / rel).parent.mkdir(parents=True, exist_ok=True)
            (src / rel).write_text("x\n", encoding="utf-8")
        key = self.tmp / "id_rsa"
        key.write_text("-----BEGIN KEY-----\n", encoding="utf-8")
        os.symlink(key, src / "src" / "vo_hai.txt")                               # tên vô hại trỏ tới khóa
        os.symlink(self.tmp, src / "lien_ket_thu_muc")
        man = self.ib.add(self.jid, src)
        self.assertEqual([i["name"] for i in man["items"]], ["a.txt"])
        stored = [p.relative_to(self.root / "inbox" / self.jid).as_posix() for p in (self.root / "inbox" / self.jid).rglob("*") if p.is_file()]
        self.assertEqual(sorted(stored), ["manifest.json", "private/mau/src/a.txt"])
        lone = self.tmp / "sample.txt"
        os.symlink(key, lone)
        man = self.ib.add(self.jid, lone)                                         # gửi riêng một liên kết: cũng bỏ qua
        self.assertIn({"name": "sample.txt", "why": "liên kết tượng trưng"}, man["skipped"])
        self.assertNotIn("BEGIN KEY", " ".join(p.read_text(errors="replace") for p in (self.root / "inbox" / self.jid).rglob("*") if p.is_file()))

    def test_zip_and_missing_path(self):
        z = self.tmp / "goi.zip"
        with zipfile.ZipFile(z, "w") as zf:
            for name in ("ok/a.txt", "ok/.git/config", "..\\..\\win.txt", "ok/.env", "../../thoat.txt"):
                zf.writestr(name, "x")
        man = self.ib.add(self.jid, z)
        self.assertEqual([i["name"] for i in man["items"]], ["ok/a.txt"])
        self.assertEqual(len(man["skipped"]), 4)
        with self.assertRaisesRegex(ToolError, "Không thấy tệp hay thư mục"):      # đường dẫn gõ sai không bị ghi thành prompt
            self.ib.add(self.jid, "/khong/co/video.mp4")
        self.assertEqual(self.ib.add(self.jid, "Viết tool đổi tên tệp theo ngày")["items"][-1]["kind"], "prompt")
        self.assertIsNone(secret_reason("src/a.txt"))


class TwoClonesBase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(make_root()).parent.parent
        self.addCleanup(shutil.rmtree, self.tmp, True)
        shutil.rmtree(self.tmp / "repo")
        seed = self.tmp / "seed"
        root = seed / "tool"
        for d in ("workbench", "catalog", "runs/locks", "runs/sessions", "config", "agents/sub", "inbox", "skills"):
            (root / d).mkdir(parents=True)
            (root / d / ".keep").write_text("")
        (seed / "khac").mkdir()
        (seed / "khac" / "f.txt").write_text("v1\n")
        (seed / ".gitignore").write_text("__pycache__/\n*.lock/\ntool/runs/*/*/out/\n")
        git(self.tmp, "init", "-q", "-b", "main", str(seed))
        self.jid = JobStore(root).create(title="Chung", prompt="một việc", requested_by="CHAIRMAN", assigned_to=T)["job_id"]
        git(seed, "add", "-A"); git(seed, "commit", "-q", "-m", "seed")
        git(self.tmp, "clone", "-q", "--bare", str(seed), str(self.tmp / "remote.git"))
        self.a, self.b, self.c = self.tmp / "A", self.tmp / "B", self.tmp / "C"
        for c in (self.a, self.b, self.c):
            git(self.tmp, "clone", "-q", str(self.tmp / "remote.git"), str(c))
        self.ga, self.gb = GitSync(self.a, branch="main"), GitSync(self.b, branch="main")
        self.ja, self.jb = JobStore(self.a / "tool"), JobStore(self.b / "tool")

    def status(self, clone):
        return git(clone, "status", "-sb").splitlines()

    def remote_head(self):
        return git(self.tmp / "remote.git", "rev-parse", "main")


class GitSyncTest(TwoClonesBase):
    def test_loser_with_pending_local_files_still_lands_on_the_winners_state(self):
        (self.b / "tool" / "runs" / "sessions" / "ghi-chu.json").write_text("{}")   # việc dở bình thường của phiên B
        ga, ja, jid = self.ga, self.ja, self.jid

        class Racy(GitSync):                                                       # A thắng đúng lúc B sắp đẩy
            fired = False

            def _push_once(self):
                if not Racy.fired and "B nhận việc" in self._git("log", "-1", "--format=%s").stdout:
                    Racy.fired = True
                    with ga.tx("A nhận việc"):
                        ja.claim(jid, T)
                return super()._push_once()

        with self.assertRaises(SyncConflict) as cm:
            with Racy(self.b, branch="main").tx("B nhận việc"):
                self.jb.claim(self.jid, AT)
        self.assertIn("refs/tool-discarded/", str(cm.exception))                   # bản bị hủy còn lấy lại được
        self.assertEqual(self.jb.get(self.jid)["lease"]["holder"], T)              # B thấy đúng người thắng
        self.assertEqual(self.status(self.b), ["## main...origin/main"])           # không lệch, không còn gì dở
        self.assertEqual(git(self.b, "rev-parse", "HEAD"), self.remote_head())
        self.assertTrue((self.b / "tool" / "runs" / "sessions" / "ghi-chu.json").is_file())   # việc dở của B đã lên bản chung, không mất
        self.assertIn("ghi-chu.json", git(self.tmp / "remote.git", "ls-tree", "-r", "--name-only", "main"))

    def test_unrelated_push_then_conflict_leaves_clone_clean_and_usable(self):
        state = {"n": 0}
        ga, ja, jid, c = self.ga, self.ja, self.jid, self.c

        class Racy(GitSync):
            def _push_once(self):
                state["n"] += 1
                if state["n"] == 1:                                                # đội khác đẩy tệp ngoài tool/ ngay trước lượt đẩy đầu
                    (c / "khac" / "f.txt").write_text("v2 của đội khác\n"); (c / "khac" / "moi.txt").write_text("x\n")
                    git(c, "add", "-A"); git(c, "commit", "-q", "-m", "đội khác"); git(c, "push", "-q", "origin", "HEAD:main")
                if state["n"] == 2:                                                # rồi A nhận đúng việc đó ngay trước lượt đẩy hai
                    with ga.tx("A nhận việc"):
                        ja.claim(jid, T)
                return super()._push_once()

        with self.assertRaises(SyncConflict):
            with Racy(self.b, branch="main").tx("B nhận việc"):
                self.jb.claim(self.jid, AT)
        self.assertEqual(self.status(self.b), ["## main...origin/main"])           # không "behind", không tệp bẩn của đội khác
        self.assertEqual((self.b / "khac" / "f.txt").read_text(), "v2 của đội khác\n")
        self.assertEqual(self.jb.get(self.jid)["lease"]["holder"], T)
        with self.gb.tx("B làm việc khác"):                                        # lệnh --sync kế tiếp vẫn chạy được
            other = self.jb.create(title="Khác", prompt="việc khác", requested_by="CHAIRMAN", assigned_to=AT)["job_id"]
        self.assertIn(other, git(self.tmp / "remote.git", "ls-tree", "-r", "--name-only", "main"))

    def test_network_loss_after_local_change_is_rolled_back_not_left_half_done(self):
        class Net(GitSync):
            down = False

            def _git(self, *args, **kw):
                if self.down and args[:1] in (("push",), ("fetch",)):
                    args = (args[0], "-q", "/khong/co/remote.git", "main")
                return super()._git(*args, **kw)

        g = Net(self.b, branch="main")
        with self.assertRaises(SyncFailed) as cm:
            with g.tx("B nhận việc"):
                self.jb.claim(self.jid, AT)
                g.down = True
        self.assertIn("đã được hoàn lại", str(cm.exception))
        j = self.jb.get(self.jid)
        self.assertEqual((j["state"], j["lease"]), ("READY", None))                # không còn "đang giữ việc" mà chưa ai xác nhận
        self.assertEqual(self.status(self.b), ["## main...origin/main"])

    def test_failed_transaction_keeps_other_work_in_the_same_clone(self):
        outside = self.b / "khac" / "f.txt"
        outside.write_text("đang sửa dở ngoài tool/\n")                            # việc dở ở chỗ khác trong cùng bản sao
        later = self.b / "tool" / "workbench" / "ghi_sau.txt"
        with self.assertRaisesRegex(ToolError, "cố ý"):
            with self.gb.tx("B lỗi giữa chừng"):
                self.jb.claim(self.jid, AT)
                (self.b / "tool" / "workbench" / "trong_giao_dich.txt").write_text("phần của giao dịch\n")
                raise ToolError("cố ý")
        later.write_text("Agent con ghi sau giao dịch\n")
        self.assertEqual(outside.read_text(), "đang sửa dở ngoài tool/\n")
        self.assertTrue(later.is_file())
        self.assertFalse((self.b / "tool" / "workbench" / "trong_giao_dich.txt").exists())   # phần của giao dịch: hoàn lại
        self.assertEqual(self.jb.get(self.jid)["state"], "READY")
        ref = git(self.b, "for-each-ref", "--format=%(refname)", "refs/tool-discarded").splitlines()[0]
        self.assertEqual(git(self.b, "show", f"{ref}:tool/workbench/trong_giao_dich.txt"), "phần của giao dịch")   # nhưng còn lấy lại được
        with self.gb.tx("B nhận việc"):                                            # tệp bẩn ngoài tool/ không cản lệnh --sync kế tiếp
            self.jb.claim(self.jid, AT)
        self.assertEqual(outside.read_text(), "đang sửa dở ngoài tool/\n")

    def test_sync_refuses_wrong_branch_detached_head_and_unfinished_rebase(self):
        git(self.b, "checkout", "-q", "-b", "claude/phien-b")
        with self.assertRaisesRegex(ToolError, "không phải nhánh chung 'main'"):   # hai phiên ở hai nhánh sẽ không thấy khóa của nhau
            with self.gb.tx("B nhận việc"):
                self.jb.claim(self.jid, AT)
        self.assertEqual(self.jb.get(self.jid)["state"], "READY")
        git(self.c, "checkout", "-q", "--detach")
        with self.assertRaisesRegex(ToolError, "không phải nhánh chung"):
            with GitSync(self.c, branch="main").tx("C"):
                pass
        self.assertEqual(GitSync(self.a).branch, "master")                         # mặc định: nhánh chung của repo thật
        (self.a / "tool" / "config" / "sync.json").write_text(json.dumps({"branch": "main"}))
        self.assertEqual(GitSync(self.a).branch, "main")
        (self.a / ".git" / "MERGE_HEAD").write_text(git(self.a, "rev-parse", "HEAD") + "\n")
        with self.assertRaisesRegex(ToolError, "rebase/merge chưa xong"):          # không tự hủy việc dở của người khác
            with self.ga.tx("A"):
                pass
        self.assertTrue((self.a / ".git" / "MERGE_HEAD").exists())


if __name__ == "__main__":
    unittest.main()
