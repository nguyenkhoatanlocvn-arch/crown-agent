"""Kho công cụ: cổng phát hành, chống phát hành trùng, quay lui, tìm kiếm, chạy có kiểm quyền dữ liệu."""
import json
import multiprocessing as mp
import shutil
import unittest
from pathlib import Path

from tool.core import sandbox
from tool.core.budget import Budget
from tool.core.catalog import Catalog, validate_manifest
from tool.core.identity import Directory
from tool.core.runner import Runner
from tool.core.skills import Skills
from tool.core.util import ToolError, read_json

from .tool_fixtures import make_build, make_root

T, AT = "agent-tool", "agent-tool-at"


def _race_publish(root, build, actor, q):
    try:
        Catalog(Path(root)).publish(Path(build), actor)
        q.put((actor, "OK"))
    except ToolError as e:
        q.put((actor, str(e)))


class Base(unittest.TestCase):
    def setUp(self):
        self.root = make_root()
        self.tmp = self.root.parent.parent
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.dir = Directory(self.root)
        self.cat = Catalog(self.root, self.dir)
        self.runner = Runner(self.root, self.dir)

    def publish(self, version="1.0.0", **kw):
        return self.cat.publish(make_build(self.tmp, version=version, **kw), T)

    def data(self, relpath, text="chào anh Lộc\n"):
        p = self.tmp / "repo" / relpath
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        return p


class PublishGateTest(Base):
    def test_publish_then_any_registered_agent_finds_and_runs(self):
        rel = self.publish()
        self.assertEqual(rel["test"]["ok"], True)
        hits = self.cat.find("viet hoa")                       # tìm không dấu
        self.assertEqual([h["id"] for h in hits], ["upper-text"])
        self.assertEqual(self.cat.find("", "video-image"), [])  # lọc theo nhóm
        src = self.data("research/ghi_chu.txt")                 # vùng ghi của A19
        rec = self.runner.run("upper-text", "A19", {"input": src}, out_dir=self.tmp / "repo" / "research" / "out")
        self.assertEqual(rec["status"], "OK", rec["error"])
        self.assertEqual((self.tmp / "repo" / "research" / "out" / "ghi_chu.txt").read_text(encoding="utf-8"), "CHÀO ANH LỘC\n")
        saved = read_json(self.root / rec["record"])
        self.assertEqual(saved["caller"], "A19")
        self.assertTrue(all(a["allow"] for a in saved["access"]))
        self.assertNotIn("chào anh", json.dumps(saved, ensure_ascii=False))   # bản ghi không chứa nội dung dữ liệu

    def test_only_executives_publish(self):
        with self.assertRaisesRegex(ToolError, "không có quyền phát hành tool"):
            self.cat.publish(make_build(self.tmp), "A18")

    def test_failing_tests_block_publish(self):
        with self.assertRaisesRegex(ToolError, "Không đạt kiểm thử"):
            self.publish(broken=True)
        self.assertEqual(self.cat.tools(), [])
        self.assertFalse((self.root / "catalog" / "upper-text" / ".claim-1.0.0").exists())  # nhả quyền để sửa rồi phát hành lại

    def test_undeclared_network_or_process_use_blocks_publish(self):
        for imp, word in (("import socket", "dùng mạng"), ("import subprocess", "gọi tiến trình"),
                          ("import numpy", "ngoài thư viện chuẩn"), ("import ctypes", "cấm dùng")):
            b = make_build(self.tmp / imp.split()[1], extra_import=imp)
            self.assertTrue(any(word in v for v in sandbox.lint_permissions(b, read_json(b / "manifest.json"))), imp)
            with self.assertRaisesRegex(ToolError, "Không đạt kiểm thử"):
                self.cat.publish(b, T)

    def test_paid_tool_blocked_until_cfo_budget_approved(self):
        b = make_build(self.tmp, cost=0.5)
        with self.assertRaisesRegex(ToolError, "Vượt hạn mức CFO"):
            self.cat.publish(b, T)
        Budget(self.root).approve(2.0, "API thử", "KHO-DX-001")
        self.cat.publish(b, T)
        other = make_root()                                # khoản duyệt của kho này không lọt sang kho khác
        self.addCleanup(shutil.rmtree, other.parent.parent, True)
        self.assertEqual(Budget(other).remaining(), 0)

    def test_manifest_must_carry_examples_source_permissions(self):
        b = make_build(self.tmp)
        m = read_json(b / "manifest.json")
        for key in ("examples", "source", "permissions", "keywords"):
            bad = {k: v for k, v in m.items() if k != key}
            self.assertTrue(validate_manifest(bad, b), key)

    def test_sandbox_strips_environment_and_network(self):
        import os
        os.environ["TOOL_TEST_SECRET"] = "khoa-bi-mat"
        self.addCleanup(os.environ.pop, "TOOL_TEST_SECRET", None)
        b = make_build(self.tmp)
        (b / "main.py").write_text(
            "import json, os, sys\nfrom pathlib import Path\nreq=json.loads(Path(sys.argv[1]).read_text())\n"
            "Path(req['out_dir'],'env.txt').write_text(os.environ.get('TOOL_TEST_SECRET','KHONG_THAY'))\nprint('{}')\n",
            encoding="utf-8")
        r = sandbox.execute(b, read_json(b / "manifest.json"), {"input": b / "examples" / "mau.txt"})
        try:
            self.assertEqual(r["outputs"]["env.txt"].read_text(), "KHONG_THAY")
            self.assertTrue(r["isolation"]["env_stripped"])
        finally:
            sandbox.cleanup(r["work"])


class DuplicateAndRollbackTest(Base):
    def test_two_agents_publishing_same_version_exactly_one_wins(self):
        b1, b2 = make_build(self.tmp / "a"), make_build(self.tmp / "b")
        q = mp.Queue()
        ps = [mp.Process(target=_race_publish, args=(str(self.root), str(b), who, q)) for b, who in ((b1, T), (b2, AT))]
        [p.start() for p in ps]
        [p.join(60) for p in ps]
        res = dict(q.get(timeout=5) for _ in ps)
        self.assertEqual(sorted(v == "OK" for v in res.values()), [False, True], res)
        loser = next(v for v in res.values() if v != "OK")
        self.assertIn("không phát hành trùng", loser)
        t = self.cat.tool("upper-text")
        self.assertEqual([v["version"] for v in t["versions"]], ["1.0.0"])      # đúng một bản trong kho
        self.assertEqual(len([h for h in t["history"] if h["action"] == "publish"]), 1)

    def test_republishing_existing_version_refused(self):
        self.publish()
        with self.assertRaisesRegex(ToolError, "không phát hành trùng"):
            self.publish()

    def test_bad_new_version_rolls_back_to_good_one(self):
        self.publish("1.0.0")
        self.publish("1.1.0")
        self.assertEqual(self.cat.resolve("upper-text"), ("upper-text", "1.1.0"))
        out = self.cat.rollback("upper-text", AT, "bản 1.1.0 sai kết quả khi gọi thử từ kho")
        self.assertEqual(out, {"tool_id": "upper-text", "withdrawn": "1.1.0", "current_version": "1.0.0"})
        self.assertEqual(self.cat.resolve("upper-text"), ("upper-text", "1.0.0"))
        with self.assertRaisesRegex(ToolError, "đã bị thu hồi"):
            self.cat.resolve("upper-text@1.1.0")
        self.assertTrue((self.root / "catalog" / "upper-text" / "1.1.0" / "main.py").is_file())   # không xóa tệp
        rec = self.runner.run("upper-text", "A19", {"input": self.data("research/a.txt")})
        self.assertEqual((rec["status"], rec["version"]), ("OK", "1.0.0"))
        self.assertEqual(self.cat.tool("upper-text")["history"][-1]["action"], "rollback")

    def test_smoke_from_shared_catalog_auto_rolls_back_a_bad_release(self):
        from tool.core import release
        self.publish("1.0.0")
        other = self.data("research/khac_mau.txt", "dữ liệu khác mẫu\n")
        want = self.data("research/khac_mau.expected.txt", "DỮ LIỆU KHÁC MẪU\n")
        case = [{"inputs": {"input": other}, "expected": {"khac_mau.txt": want}}]
        ok = release.smoke("upper-text", "A19", root=self.root, extra=case, rollback_actor=AT)
        self.assertTrue(ok["ok"]); self.assertIsNone(ok["rolled_back"]); self.assertEqual(len(ok["cases"]), 2)
        # Bản 1.1.0 "đạt" bộ thử của chính nó (ví dụ bị sửa theo lỗi) nhưng sai với dữ liệu khác mẫu.
        b = make_build(self.tmp, version="1.1.0", broken=True)
        (b / "examples" / "mau.expected.txt").write_text("XIN CHÀO\n!", encoding="utf-8")
        self.cat.publish(b, AT)
        self.assertEqual(self.cat.resolve("upper-text")[1], "1.1.0")
        bad = release.smoke("upper-text", "A19", root=self.root, extra=case, rollback_actor=AT)
        self.assertFalse(bad["ok"])
        self.assertEqual(bad["rolled_back"], {"tool_id": "upper-text", "withdrawn": "1.1.0", "current_version": "1.0.0"})
        self.assertTrue(release.smoke("upper-text", "A19", root=self.root, extra=case)["ok"])   # bản tốt gọi lại được

    def test_rollback_of_only_version_makes_tool_uncallable(self):
        self.publish()
        self.cat.rollback("upper-text", T, "lỗi")
        self.assertEqual(self.cat.find("viết hoa"), [])
        rec = self.runner.run("upper-text", "A19", {"input": self.data("research/a.txt")})
        self.assertEqual(rec["status"], "ERROR")
        self.assertIn("không có phiên bản nào được phép gọi", rec["error"])

    def test_tampered_release_is_refused(self):
        self.publish()
        f = self.root / "catalog" / "upper-text" / "1.0.0" / "main.py"
        f.write_text(f.read_text(encoding="utf-8") + "\n# sửa lén\n", encoding="utf-8")
        rec = self.runner.run("upper-text", "A19", {"input": self.data("research/a.txt")})
        self.assertEqual(rec["status"], "ERROR")
        self.assertIn("đã bị sửa sau khi phát hành", rec["error"])


class AccessTest(Base):
    def setUp(self):
        super().setUp()
        self.publish()

    def run_as(self, caller, relpath, **kw):
        return self.runner.run("upper-text", caller, {"input": self.data(relpath)}, **kw)

    def test_caller_checked_on_every_run(self):
        self.assertEqual(self.run_as("A19", "research/x.txt")["status"], "OK")
        r = self.run_as("A19", "agents/A09/so_cai.txt")           # vùng riêng của Agent khác
        self.assertEqual(r["status"], "DENIED")
        self.assertIn("không thuộc vùng đọc của A19", r["error"])
        self.assertEqual(self.run_as("A09", "agents/A09/so_cai.txt")["status"], "OK")   # chủ vùng thì được
        self.assertEqual(self.run_as("CFO", "agents/A09/so_cai.txt")["status"], "OK")   # lãnh đạo đọc vùng cấp dưới
        self.assertEqual(self.run_as("CHAIRMAN", "agents/A09/so_cai.txt")["status"], "OK")

    def test_secrets_denied_even_for_chairman(self):
        for name in ("research/.env.txt".replace(".txt", ""), "research/api_key.txt", "research/auth_binance.json"):
            p = self.data(name)
            r = self.runner.run("upper-text", "CHAIRMAN", {"input": p})
            self.assertEqual(r["status"], "DENIED", name)
            self.assertIn("bí mật", r["error"])

    def test_unknown_caller_and_protected_external_area(self):
        r = self.run_as("A99", "research/x.txt")
        self.assertEqual(r["status"], "ERROR")
        self.assertIn("không có trong danh bạ", r["error"])
        ext = self.tmp / "BTCDesk_Windows" / "log.txt"
        ext.parent.mkdir()
        ext.write_text("x\n", encoding="utf-8")
        self.assertEqual(self.runner.run("upper-text", "A19", {"input": ext})["status"], "DENIED")
        self.assertEqual(self.runner.run("upper-text", "CFO", {"input": ext})["status"], "OK")

    def test_output_only_into_callers_write_scope(self):
        r = self.run_as("A19", "research/x.txt", out_dir=self.tmp / "repo" / "agents" / "A09" / "out")
        self.assertEqual(r["status"], "DENIED")
        self.assertFalse((self.tmp / "repo" / "agents" / "A09" / "out").exists())

    def test_denied_and_ok_runs_are_both_in_history(self):
        self.run_as("A19", "agents/A09/so_cai.txt")
        self.run_as("A19", "research/x.txt")
        self.assertEqual(sorted(r["status"] for r in self.runner.history()), ["DENIED", "OK"])


class SkillsTest(Base):
    def md(self, name, body="Nội dung v1"):
        p = self.tmp / f"{name}-{abs(hash(body))}.md"
        p.write_text(f"---\nname: {name}\ndescription: Use when testing TOOL skills.\n---\n\n{body}\n", encoding="utf-8")
        return p

    def test_versions_grantor_test_result_and_rollback(self):
        sk = Skills(self.root, self.dir)
        v1 = sk.add(self.md("video-to-observation"), granted_by=T, actor=T, source="viết gốc", test_result="PASS", test_evidence="ca thử 1")
        v2 = sk.add(self.md("video-to-observation", "Nội dung v2"), granted_by="A03", actor=AT, source="skills/ của A03",
                    test_result="PASS", test_evidence="ca thử 2")
        self.assertEqual((v1["version"], v2["version"]), (1, 2))
        s = sk.registry()["skills"]["video-to-observation"]
        self.assertEqual((s["current"], [v["status"] for v in s["versions"]]), (2, ["PREVIOUS", "CURRENT"]))
        self.assertEqual([v["granted_by"] for v in s["versions"]], [T, "A03"])
        out = sk.rollback("video-to-observation", T, "v2 làm hỏng bước lấy khung")
        self.assertEqual(out["current"], 1)
        self.assertIn("Nội dung v1", sk.current_path("video-to-observation").read_text(encoding="utf-8"))
        self.assertTrue((self.root / "skills" / "video-to-observation" / "v2" / "SKILL.md").is_file())  # bản bị thu hồi vẫn giữ

    def test_untested_skill_never_becomes_current_and_grantors_are_limited(self):
        sk = Skills(self.root, self.dir)
        v = sk.add(self.md("clean-room-spec"), granted_by="VUA_CEO", actor=T, source="Agent Vua cấp", test_result="NOT_RUN")
        self.assertEqual(v["status"], "CANDIDATE")
        self.assertIsNone(sk.registry()["skills"]["clean-room-spec"]["current"])
        with self.assertRaisesRegex(ToolError, "không được cấp skill"):
            sk.add(self.md("x-skill"), granted_by="A19", actor=T, source="?", test_result="PASS", test_evidence="e")
        with self.assertRaisesRegex(ToolError, "phải kèm bằng chứng"):
            sk.add(self.md("y-skill"), granted_by=T, actor=T, source="gốc", test_result="PASS")


if __name__ == "__main__":
    unittest.main()
