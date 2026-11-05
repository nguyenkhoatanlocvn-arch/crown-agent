"""Chạy đúng chuỗi lệnh trong điều lệ Thiên Công AT bằng CLI thật, trên hai bản sao repo chung một remote."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from .tool_fixtures import make_build

REPO = Path(__file__).resolve().parent.parent


def git(cwd, *args):
    r = subprocess.run(["git", "-C", str(cwd), "-c", "user.name=t", "-c", "user.email=t@t", *args], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


class CliFlowTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="toolcli-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        seed = self.tmp / "seed"
        for d in ("crown", "registries"):
            shutil.copytree(REPO / d, seed / d, ignore=shutil.ignore_patterns("__pycache__"))
        (seed / "tool").mkdir()
        for f in ("__init__.py", "__main__.py", "cli.py"):
            shutil.copyfile(REPO / "tool" / f, seed / "tool" / f)
        shutil.copytree(REPO / "tool" / "core", seed / "tool" / "core", ignore=shutil.ignore_patterns("__pycache__"))
        up = self.tmp / "upstream"                       # nguồn "GitHub" giả để AT theo dõi
        up.mkdir()
        git(self.tmp, "init", "-q", "-b", "main", str(up))
        (up / "f.txt").write_text("1\n"); git(up, "add", "-A"); git(up, "commit", "-q", "-m", "c1")
        (seed / "tool" / "config").mkdir()
        (seed / "tool" / "config" / "watch.json").write_text(json.dumps({"sources": [{"id": "nguon", "url": str(up), "mode": "head"}]}))
        (seed / ".gitignore").write_text("__pycache__/\n*.lock/\ntool/runs/*/*/out/\n")
        git(self.tmp, "init", "-q", "-b", "master", str(seed)); git(seed, "add", "-A"); git(seed, "commit", "-q", "-m", "seed")
        git(self.tmp, "clone", "-q", "--bare", str(seed), str(self.tmp / "remote.git"))
        self.a, self.b, self.up = self.tmp / "A", self.tmp / "B", up
        for c in (self.a, self.b):
            git(self.tmp, "clone", "-q", str(self.tmp / "remote.git"), str(c))
            git(c, "config", "user.name", "t"); git(c, "config", "user.email", "t@t")

    def tool(self, clone, *args, ok=True):
        env = {k: v for k, v in os.environ.items() if k != "TOOL_ROOT"}
        r = subprocess.run([sys.executable, "-m", "tool", *args], cwd=clone, capture_output=True, text=True, env=env)
        if ok:
            self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        return r

    def test_scheduled_session_then_overlap_then_close(self):
        s = json.loads(self.tool(self.a, "--sync", "at", "begin", "--agent", "agent-tool-at", "--trigger", "scheduler").stdout)
        self.assertEqual((s["trigger"], s["result"]), ("scheduler", None))
        run = s["run_id"]
        # Phiên thứ hai (máy khác) mở khi phiên đầu chưa xong: bị bỏ qua, có ghi bản ghi
        s2 = json.loads(self.tool(self.b, "--sync", "at", "begin", "--agent", "agent-tool-at", "--marker", "MANUAL: bấm tay").stdout)
        self.assertEqual((s2["trigger"], s2["result"]), ("manual", "SKIPPED_OVERLAP"))
        # Phiên đầu: không có thay đổi GitHub → đóng phiên
        gh = json.loads(self.tool(self.a, "at", "github", run).stdout)
        self.assertEqual((len(gh["checked"]), gh["changed"]), (1, []))
        plan = json.loads(self.tool(self.a, "at", "plan", run).stdout)
        self.assertEqual(plan["action"], {"do": "NO_CHANGE"})
        end = json.loads(self.tool(self.a, "--sync", "at", "end", run, "NO_CHANGE", "--note", "không có thay đổi").stdout)
        self.assertEqual(end["result"], "NO_CHANGE")
        # Máy B thấy: khóa đã nhả, đủ hai bản ghi phiên, phân biệt lượt lịch và lượt tay
        git(self.b, "pull", "-q", "--rebase")
        st = json.loads(self.tool(self.b, "at", "status").stdout)
        self.assertEqual(st["lock"]["state"], "FREE")
        self.assertEqual(sorted((x["trigger"], x["result"]) for x in st["last_sessions"]), [("manual", "SKIPPED_OVERLAP"), ("scheduler", "NO_CHANGE")])
        # Giờ sau: nguồn có thay đổi → phiên mới thấy đúng một ứng viên; xử lý xong thì phiên sau nữa không lặp lại
        (self.up / "f.txt").write_text("2\n"); git(self.up, "add", "-A"); git(self.up, "commit", "-q", "-m", "c2")
        s3 = json.loads(self.tool(self.b, "--sync", "at", "begin", "--agent", "agent-tool-at", "--trigger", "scheduler").stdout)
        self.tool(self.b, "at", "github", s3["run_id"])
        act = json.loads(self.tool(self.b, "at", "plan", s3["run_id"]).stdout)["action"]
        self.assertEqual((act["do"], act["id"]), ("EVALUATE_SOURCE", "nguon"))
        self.tool(self.b, "at", "candidate", "nguon", act["key"], "REJECTED", "--note", "không liên quan")
        self.tool(self.b, "--sync", "at", "end", s3["run_id"], "NO_RELEASE", "--note", "ứng viên không đạt")
        s4 = json.loads(self.tool(self.a, "--sync", "at", "begin", "--agent", "agent-tool-at", "--trigger", "scheduler").stdout)
        self.assertEqual(json.loads(self.tool(self.a, "at", "github", s4["run_id"]).stdout)["changed"], [])

    def to_publish_step(self, clone, actor):
        """Đúng chuỗi lệnh trong PLAYBOOK: tạo việc → nhận → từng bước có bằng chứng → Agent con viết ≠ Agent con kiểm.

        Tạo Agent con luôn kèm --sync: sổ Agent con là dữ liệu chung (ID nối tiếp, hạn mức chạy đồng thời tính trên mọi máy).
        """
        t = lambda *a: self.tool(clone, *a).stdout
        job = t("job", "new", "--title", "Viết hoa văn bản", "--prompt", "Viết tool viết hoa", "--by", "CHAIRMAN", "--to", actor).split()[0]
        tok = t("job", "claim", job, actor).strip().rsplit(" ", 1)[1]
        for st in ("intake", "research", "analyze", "spec"):
            t("job", "start", job, st, actor, tok); t("job", "done", job, st, actor, tok, "--evidence", "bằng chứng thử")
        lt = json.loads(t("--sync", "sub", "spawn", "--branch", "lt", "--by", actor, "--job", job, "--step", "build", "--output", "build/"))["id"]
        t("job", "start", job, "build", actor, tok, "--sub", lt)
        build = make_build(self.tmp, dest=clone / "tool" / "workbench" / job / "build")
        t("job", "done", job, "build", actor, tok, "--evidence", "build/manifest.json")
        t("sub", "finish", lt, "--by", actor, "--ok", "--result", "viết xong")
        kt = json.loads(t("--sync", "sub", "spawn", "--branch", "kt", "--by", actor, "--job", job, "--step", "test", "--output", "TEST_REPORT.json"))["id"]
        r = self.tool(clone, "job", "start", job, "test", actor, tok, "--sub", lt, ok=False)       # người viết không tự kiểm
        self.assertIn("kiểm thử phải do Agent con khác làm", r.stderr)
        t("job", "start", job, "test", actor, tok, "--sub", kt)
        t("job", "done", job, "test", actor, tok, "--evidence", "TEST_REPORT.json")
        t("sub", "finish", kt, "--by", actor, "--ok", "--result", "kiểm xong")
        return job, tok, build

    def test_publish_find_run_and_duplicate_publish_across_machines(self):
        job, tok, b1 = self.to_publish_step(self.a, "agent-tool")
        r = self.tool(self.a, "publish", str(b1), "--actor", "agent-tool", "--job", job, "--token", tok, ok=False)
        self.assertIn("chưa được agent-tool bắt đầu", r.stderr)                                    # phải mở bước publish trước
        self.tool(self.a, "job", "start", job, "publish", "agent-tool", tok)
        (b1 / "main.py").write_text((b1 / "main.py").read_text(encoding="utf-8") + "\n# sửa sau khi kiểm\n", encoding="utf-8")
        r = self.tool(self.a, "publish", str(b1), "--actor", "agent-tool", "--job", job, "--token", tok, ok=False)
        self.assertIn("đã thay đổi sau khi kiểm thử độc lập", r.stderr)                            # bản dựng bị sửa sau kiểm thử
        make_build(self.tmp, dest=b1)                                                             # trả lại đúng bản đã kiểm
        out = self.tool(self.a, "--sync", "publish", str(b1), "--actor", "agent-tool", "--job", job, "--token", tok).stdout
        self.assertIn("ĐÃ PHÁT HÀNH upper-text@1.0.0", out)
        job2, tok2, b2 = self.to_publish_step(self.b, "agent-tool-at")                             # máy B làm cùng phiên bản, không xem kho
        self.tool(self.b, "job", "start", job2, "publish", "agent-tool-at", tok2)
        r = self.tool(self.b, "--sync", "publish", str(b2), "--actor", "agent-tool-at", "--job", job2, "--token", tok2, ok=False)
        self.assertEqual(r.returncode, 1)
        self.assertIn("không phát hành trùng", r.stderr)
        self.assertIn("upper-text@1.0.0", self.tool(self.b, "find", "viet hoa").stdout)              # B đã nhận bản của A
        src = self.tmp / "ghi_chu.txt"
        src.write_text("xin chào\n", encoding="utf-8")
        rec = json.loads(self.tool(self.b, "run", "upper-text", "--caller", "A15", "--in", f"input={src}", "--out", str(self.tmp / "out"), "--json").stdout)
        self.assertEqual((rec["status"], rec["caller"], rec["version"]), ("OK", "A15", "1.0.0"))
        self.assertEqual((self.tmp / "out" / "ghi_chu.txt").read_text(encoding="utf-8"), "XIN CHÀO\n")
        denied = self.tool(self.b, "run", "upper-text", "--caller", "A99", "--in", f"input={src}", ok=False)
        self.assertEqual(denied.returncode, 1)
        self.assertIn("không có trong danh bạ", denied.stderr)


if __name__ == "__main__":
    unittest.main()
