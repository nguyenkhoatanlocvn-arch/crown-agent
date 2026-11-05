"""Sổ việc TOOL: khóa ghi một người, fencing token, tiếp nối từ bước đã ghi, không tự duyệt, Agent con."""
import shutil
import unittest
from datetime import datetime, timedelta, timezone

from tool.core.agents import Mailbox, Memory
from tool.core.identity import Directory
from tool.core.jobs import JobStore
from tool.core.subagents import SubAgents
from tool.core.util import ToolError

from .tool_fixtures import make_root

T, AT = "agent-tool", "agent-tool-at"


class Clock:
    def __init__(self):
        self.now = datetime(2026, 11, 5, 16, 0, tzinfo=timezone.utc)

    def __call__(self):
        return self.now

    def tick(self, **kw):
        self.now += timedelta(**kw)


class Base(unittest.TestCase):
    def setUp(self):
        self.root = make_root()
        self.addCleanup(shutil.rmtree, self.root.parent.parent, True)
        self.clock = Clock()
        self.dir = Directory(self.root)
        self.jobs = JobStore(self.root, self.dir, self.clock)
        self.subs = SubAgents(self.root, self.dir, self.clock)

    def new_job(self, **kw):
        return self.jobs.create(title="Đánh lại số phụ đề", prompt="Viết tool đánh lại số phụ đề từ 1",
                                requested_by="CHAIRMAN", assigned_to=T, **kw)

    def evidence(self, job_id, name="e.txt"):
        p = self.jobs.job_dir(job_id) / name
        p.write_text("bằng chứng", encoding="utf-8")
        return f"tool/workbench/{job_id}/{name}"


class IdentityTest(Base):
    def test_two_co_executives_registered_as_peers_of_vua(self):
        self.assertEqual(self.dir.exec_ids, [T, AT])
        for eid in (T, AT):
            e = self.dir.reg.executives[eid]
            self.assertEqual(e["supervisor"], "CHAIRMAN")
            self.assertEqual(e["peer_of"], ["VUA_CEO"])
            self.assertEqual(self.dir.reg.chain(eid), [eid, "CHAIRMAN"])
        self.assertEqual(self.dir.reg.executives[AT]["clone_of"], T)
        self.assertEqual(sorted(self.dir.branches), ["gh", "kt", "lt", "pt", "th"])

    def test_unknown_or_disabled_caller_rejected(self):
        with self.assertRaisesRegex(ToolError, "không có trong danh bạ"):
            self.dir.resolve("A99")
        with self.assertRaisesRegex(ToolError, "không có quyền"):
            self.dir.require_executive("A18", "phát hành tool")

    def test_future_agent_from_web_directory_is_recognised(self):
        (self.root / "agents" / "directory.json").write_text(
            '{"members": {"A21-AT": {"name": "CROWN-4 AT", "status": "ACTIVE", "boss": "CHAIRMAN"}}}', encoding="utf-8")
        self.assertEqual(self.dir.resolve("A21-AT")["source"], "web-office")


class LeaseTest(Base):
    def test_only_one_writer_at_a_time(self):
        j = self.new_job()
        tok = self.jobs.claim(j["job_id"], T)
        with self.assertRaisesRegex(ToolError, "một việc chỉ một người ghi"):
            self.jobs.claim(j["job_id"], AT)
        with self.assertRaisesRegex(ToolError, "không còn giữ"):
            self.jobs.step_start(j["job_id"], "intake", AT, tok)  # đúng token nhưng sai người
        self.jobs.step_start(j["job_id"], "intake", T, tok)

    def test_non_executive_cannot_claim(self):
        j = self.new_job()
        with self.assertRaisesRegex(ToolError, "không có quyền"):
            self.jobs.claim(j["job_id"], "A18")

    def test_other_agent_continues_from_recorded_step_after_lease_expiry(self):
        j = self.new_job()
        jid = j["job_id"]
        tok = self.jobs.claim(jid, T, ttl_seconds=600)
        self.jobs.step_start(jid, "intake", T, tok)
        self.jobs.step_done(jid, "intake", T, tok, [self.evidence(jid)])
        self.jobs.step_done(jid, "research", T, tok, [], "không cần nguồn ngoài", skipped=True)
        self.jobs.step_start(jid, "analyze", T, tok)       # Thiên Công ngừng giữa bước analyze
        self.clock.tick(seconds=601)
        self.assertEqual([x["job_id"] for x in self.jobs.backlog()], [jid])
        tok2 = self.jobs.claim(jid, AT)                    # Thiên Công AT nhận lại
        job = self.jobs.get(jid)
        self.assertEqual(self.jobs.next_step(job)["id"], "analyze")   # tiếp từ bước đã ghi, không làm lại intake
        self.assertEqual([s["status"] for s in job["steps"][:3]], ["DONE", "SKIPPED", "PENDING"])
        self.assertIn("tiếp tục sau khi lease của agent-tool hết hạn", self.jobs.events(jid)[-1]["detail"])
        with self.assertRaisesRegex(ToolError, "không còn giữ"):      # người cũ quay lại với token cũ: bị chặn
            self.jobs.step_done(jid, "analyze", T, tok, ["x"])
        self.assertEqual(tok2, tok + 1)
        self.jobs.step_start(jid, "analyze", AT, tok2)
        self.assertEqual(self.jobs.get(jid)["workers"], [T, AT])

    def test_expired_lease_blocks_writes_even_without_takeover(self):
        j = self.new_job()
        tok = self.jobs.claim(j["job_id"], T, ttl_seconds=60)
        self.clock.tick(seconds=61)
        with self.assertRaisesRegex(ToolError, "đã hết hạn"):
            self.jobs.step_start(j["job_id"], "intake", T, tok)

    def test_release_hands_over_and_keeps_done_steps(self):
        j = self.new_job()
        jid = j["job_id"]
        tok = self.jobs.claim(jid, T)
        self.jobs.step_start(jid, "intake", T, tok)
        self.jobs.step_done(jid, "intake", T, tok, [self.evidence(jid)])
        self.jobs.release(jid, T, tok, "hết phiên")
        tok2 = self.jobs.claim(jid, AT)
        self.assertEqual(self.jobs.next_step(self.jobs.get(jid))["id"], "research")
        self.assertGreater(tok2, tok)

    def test_steps_in_order_and_need_evidence(self):
        j = self.new_job()
        jid = j["job_id"]
        tok = self.jobs.claim(jid, T)
        with self.assertRaisesRegex(ToolError, "Bước kế tiếp là 'intake'"):
            self.jobs.step_start(jid, "build", T, tok, sub="A18")
        self.jobs.step_start(jid, "intake", T, tok)
        with self.assertRaisesRegex(ToolError, "phải có bằng chứng"):
            self.jobs.step_done(jid, "intake", T, tok, [])
        with self.assertRaisesRegex(ToolError, "bắt buộc"):
            self.jobs.step_done(jid, "intake", T, tok, [], "lười", skipped=True)

    def test_tester_must_differ_from_builder(self):
        j = self.new_job()
        jid = j["job_id"]
        tok = self.jobs.claim(jid, T)
        for step in ("intake", "analyze", "spec"):
            if step == "analyze":
                self.jobs.step_done(jid, "research", T, tok, [], "không cần", skipped=True)
            self.jobs.step_start(jid, step, T, tok)
            self.jobs.step_done(jid, step, T, tok, [self.evidence(jid)])
        with self.assertRaisesRegex(ToolError, "phải ghi rõ Agent con"):
            self.jobs.step_start(jid, "build", T, tok)
        lt = self.subs.spawn(branch="lt", assigned_by=T, job_id=jid, step="build", output="build/")
        self.jobs.step_start(jid, "build", T, tok, sub=lt["id"])
        self.jobs.step_done(jid, "build", T, tok, [self.evidence(jid)])
        with self.assertRaisesRegex(ToolError, "kiểm thử phải do Agent con khác"):
            self.jobs.step_start(jid, "test", T, tok, sub=lt["id"])
        kt = self.subs.spawn(branch="kt", assigned_by=T, job_id=jid, step="test", output="TEST_REPORT.json")
        self.jobs.step_start(jid, "test", T, tok, sub=kt["id"])

    def test_done_requires_all_steps_and_release(self):
        j = self.new_job()
        tok = self.jobs.claim(j["job_id"], T)
        with self.assertRaisesRegex(ToolError, "Chưa xong các bước"):
            self.jobs.move(j["job_id"], "VERIFIED_DONE", T, tok)

    def test_idempotent_create(self):
        a = self.new_job(idempotency_key="web-req-1")
        b = self.new_job(idempotency_key="web-req-1")
        self.assertEqual(a["job_id"], b["job_id"])
        self.assertEqual(len(self.jobs.list()), 1)
        with self.assertRaisesRegex(ToolError, "đã dùng cho việc khác"):
            self.jobs.create(title="Khác", prompt="khác", requested_by="CHAIRMAN", assigned_to=T, idempotency_key="web-req-1")

    def test_chairman_has_final_stop(self):
        j = self.new_job()
        self.jobs.claim(j["job_id"], T)
        out = self.jobs.move(j["job_id"], "CANCELLED", "CHAIRMAN", note="Chủ tịch dừng")
        self.assertEqual(out["state"], "CANCELLED")
        with self.assertRaisesRegex(ToolError, "fencing token"):
            j2 = self.new_job()
            self.jobs.claim(j2["job_id"], T)
            self.jobs.move(j2["job_id"], "CANCELLED", AT, note="Agent kia tự hủy")


class SubAgentTest(Base):
    def test_max_two_concurrent_by_default(self):
        a = self.subs.spawn(branch="pt", assigned_by=T, job_id=None, output="OBSERVATION.md")
        b = self.subs.spawn(branch="lt", assigned_by=AT, job_id=None, output="build/")
        self.assertEqual((a["id"], b["id"]), ("tool-pt-01", "tool-lt-01"))
        with self.assertRaisesRegex(ToolError, "Đang chạy 2/2 Agent con"):
            self.subs.spawn(branch="kt", assigned_by=T, job_id=None, output="báo cáo")
        self.subs.finish(a["id"], T, True, "xong", ["OBSERVATION.md"])
        c = self.subs.spawn(branch="kt", assigned_by=T, job_id=None, output="báo cáo")
        self.assertEqual(c["id"], "tool-kt-01")
        for k in ("id", "assigned_by", "output", "tier", "limits", "write_scope"):
            self.assertIn(k, c)

    def test_only_executives_spawn_and_stale_subagents_free_their_slot(self):
        with self.assertRaisesRegex(ToolError, "không có quyền tạo Agent con"):
            self.subs.spawn(branch="lt", assigned_by="A18", job_id=None, output="x")
        self.subs.spawn(branch="pt", assigned_by=T, job_id=None, output="x", limits={"max_runtime_s": 60})
        self.subs.spawn(branch="pt", assigned_by=T, job_id=None, output="y", limits={"max_runtime_s": 60})
        self.clock.tick(seconds=61)
        self.assertEqual(self.subs.spawn(branch="pt", assigned_by=T, job_id=None, output="z")["id"], "tool-pt-03")
        self.assertEqual(len(self.subs.list("EXPIRED")), 2)

    def test_paid_limit_needs_cfo_budget(self):
        with self.assertRaisesRegex(ToolError, "Vượt hạn mức CFO"):
            self.subs.spawn(branch="gh", assigned_by=T, job_id=None, output="x", limits={"paid_usd": 5})


class MailboxMemoryTest(Base):
    def test_separate_inbox_and_private_memory(self):
        mb, mem = Mailbox(self.root, self.dir), Memory(self.root, self.dir)
        m = mb.send(T, "CHAIRMAN", "Làm tool SRT")
        self.assertEqual(len(mb.list(T, "OPEN")), 1)
        self.assertEqual(mb.list(AT), [])
        with self.assertRaisesRegex(ToolError, "không đóng tin"):
            mb.close(T, m["id"], AT)
        mb.close(T, m["id"], T, "đã nhận")
        p = mem.append(AT, AT, "FFmpeg có sẵn trên máy cloud")
        self.assertIn("FFmpeg có sẵn", p.read_text(encoding="utf-8"))
        with self.assertRaisesRegex(ToolError, "chỉ do chính"):
            mem.append(AT, T, "ghi hộ")
        self.assertFalse(mem.path(T).exists())


if __name__ == "__main__":
    unittest.main()
