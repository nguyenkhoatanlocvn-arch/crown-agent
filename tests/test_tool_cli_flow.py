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

    def test_publish_find_run_and_duplicate_publish_across_machines(self):
        b1 = make_build(self.tmp / "b1")
        out = self.tool(self.a, "--sync", "publish", str(b1), "--actor", "agent-tool").stdout
        self.assertIn("ĐÃ PHÁT HÀNH upper-text@1.0.0", out)
        b2 = make_build(self.tmp / "b2")
        r = self.tool(self.b, "--sync", "publish", str(b2), "--actor", "agent-tool-at", ok=False)   # máy B chưa kéo, phát hành trùng
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
