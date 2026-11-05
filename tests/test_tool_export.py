"""Bản xuất trạng thái cho web: đúng số liệu thật, đủ 4 tài liệu, không lộ nội dung dữ liệu người gọi."""
import json
import shutil
import unittest

from tool.core import export
from tool.core.agents import Mailbox
from tool.core.catalog import Catalog
from tool.core.identity import Directory
from tool.core.jobs import JobStore
from tool.core.runner import Runner
from tool.core.scheduler import Scheduler
from tool.core.skills import Skills
from tool.core.subagents import SubAgents

from .tool_fixtures import make_build, make_root

T, AT = "agent-tool", "agent-tool-at"


class ExportTest(unittest.TestCase):
    def setUp(self):
        self.root = make_root()
        self.tmp = self.root.parent.parent
        self.addCleanup(shutil.rmtree, self.tmp, True)
        d = Directory(self.root)
        Catalog(self.root, d).publish(make_build(self.tmp, version="1.0.0"), T)
        Catalog(self.root, d).publish(make_build(self.tmp, version="1.1.0"), AT)
        Catalog(self.root, d).rollback("upper-text", AT, "diễn tập quay lui")
        secret_text = "NỘI DUNG RIÊNG CỦA NGƯỜI GỌI\n"
        src = self.tmp / "repo" / "research" / "rieng.txt"
        src.parent.mkdir(parents=True)
        src.write_text(secret_text, encoding="utf-8")
        r = Runner(self.root, d)
        r.run("upper-text", "A19", {"input": src})
        r.run("upper-text", "A19", {"input": self.tmp / "repo" / "agents" / "A09" / "x.txt"})
        jobs = JobStore(self.root, d)
        j = jobs.create(title="Việc mẫu", prompt="làm một tool", requested_by="CHAIRMAN", assigned_to=T)
        jobs.claim(j["job_id"], T)
        Mailbox(self.root, d).send(T, "CHAIRMAN", "Việc mới")
        SubAgents(self.root, d).spawn(branch="pt", assigned_by=T, job_id=j["job_id"], output="OBSERVATION.md")
        s = Scheduler(self.root, d)
        a = s.begin(AT, trigger="scheduler"); s.end(a["run_id"], "NO_CHANGE")
        b = s.begin(AT, marker="MANUAL: thử"); s.end(b["run_id"], "NO_RELEASE")
        md = self.tmp / "s.md"
        md.write_text("---\nname: tool-release-gate\ndescription: Use before publishing.\n---\nnội dung\n", encoding="utf-8")
        Skills(self.root, d).add(md, granted_by=T, actor=T, source="viết gốc", test_result="PASS", test_evidence="ca thử")
        self.docs = export.build(self.root)
        self.secret = secret_text.strip()

    def test_four_documents_with_real_numbers(self):
        self.assertEqual(sorted(self.docs), ["catalog", "jobs", "main", "runs"])
        m = self.docs["main"]
        self.assertEqual(m["summary"]["tools_callable"], 1)
        self.assertEqual(m["summary"]["versions_total"], 2)
        self.assertEqual(m["summary"]["jobs_open"], 1)
        self.assertEqual((m["summary"]["runs_24h"], m["summary"]["runs_denied_24h"]), (2, 1))
        self.assertEqual(m["summary"]["skills_current"], 1)
        ex = {e["id"]: e for e in m["agents"]["executives"]}
        self.assertEqual(sorted(ex), sorted([AT, T]))
        self.assertEqual((ex[T]["activity"], ex[T]["inbox_open"], ex[AT]["clone_of"]), ("WORKING", 1, T))
        self.assertEqual(ex[AT]["last_session"]["trigger"], "manual")
        self.assertEqual(len(m["agents"]["branches"]), 5)
        self.assertEqual((m["agents"]["max_concurrent"], m["agents"]["active"]), (2, ["tool-pt-01"]))
        self.assertEqual(m["schedule"]["cron"], "5 * * * *")
        self.assertEqual(m["schedule"]["last_scheduler_session"]["result"], "NO_CHANGE")   # chỉ lượt do lịch kích hoạt
        self.assertEqual(m["schedule"]["lock"]["state"], "FREE")
        self.assertEqual(m["budget"]["remaining"], 0)

    def test_catalog_has_examples_versions_source_and_history(self):
        t = self.docs["catalog"]["tools"][0]
        self.assertEqual((t["id"], t["current_version"], t["callable"]), ("upper-text", "1.0.0", True))
        self.assertEqual([(v["version"], v["status"]) for v in t["versions"]], [("1.0.0", "GOOD"), ("1.1.0", "WITHDRAWN")])
        self.assertEqual([h["action"] for h in t["history"]], ["publish", "publish", "rollback"])
        ex = t["examples"][0]
        self.assertEqual(ex["inputs"]["input"]["text"], "xin chào\n")
        self.assertEqual(ex["expected"]["mau.txt"]["text"], "XIN CHÀO\n")
        self.assertIn("python -m tool run upper-text --caller", t["call"])
        self.assertEqual(t["source"]["kind"], "test-fixture")

    def test_runs_and_sessions_distinguish_trigger_and_hide_user_data(self):
        runs, sess = self.docs["runs"]["runs"], self.docs["runs"]["sessions"]
        self.assertEqual(sorted(r["status"] for r in runs), ["DENIED", "OK"])
        self.assertEqual(sorted(s["trigger"] for s in sess), ["manual", "scheduler"])
        blob = json.dumps(self.docs, ensure_ascii=False)
        self.assertNotIn(self.secret, blob)                 # nội dung dữ liệu người gọi không lên web
        self.assertIn("rieng.txt", blob)                    # chỉ tên tệp + cỡ + băm
        self.assertEqual(json.loads(blob)["jobs"]["jobs"][0]["holder"], T)


if __name__ == "__main__":
    unittest.main()
