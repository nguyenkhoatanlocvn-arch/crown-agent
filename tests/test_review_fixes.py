"""Tái hiện các lỗi do kiểm định độc lập (Agent 20) tìm ra ngày 2026-11-04."""
import copy
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from crown import registry as R
from crown.tasks import TaskError, TaskStore


class Clock:
    def __init__(self):
        self.t = datetime(2026, 11, 4, 14, 0, tzinfo=timezone.utc)

    def __call__(self):
        return self.t


class TaskFixes(unittest.TestCase):
    def setUp(self):
        self.reg = R.load()
        self.s = TaskStore(Path(tempfile.mkdtemp()) / "t.db", registry=self.reg, clock=Clock())
        self.addCleanup(self.s.close)

    def mk(self, **kw):
        base = dict(owner_agent_id="A11", objective="o", acceptance_criteria="c", actor="VUA_CEO", ready=True)
        base.update(kw)
        return self.s.create(**base)["task_id"]

    def to_review(self, tid, worker="A11"):
        tok = self.s.claim(tid, worker)
        self.s.record(tid, worker, tok, evidence=f"ev-{worker}")
        self.s.move(tid, "REVIEW", worker, token=tok)

    # CRITICAL 1: tên sai hoa/thường hoặc không có trong sổ không được duyệt
    def test_unregistered_or_case_variant_cannot_verify(self):
        t = self.mk(); self.to_review(t)
        for bad in ("a11", "random", "A99"):
            with self.assertRaises(TaskError):
                self.s.move(t, "VERIFIED_DONE", bad)

    # CRITICAL 2: bất kỳ ai từng làm việc đó đều không được duyệt
    def test_any_past_worker_cannot_verify(self):
        t = self.mk(); self.to_review(t, "A11")
        tok = self.s.claim(t, "A18")
        self.s.record(t, "A18", tok, evidence="ev-A18")
        self.s.move(t, "REVIEW", "A18", token=tok)
        with self.assertRaises(TaskError):
            self.s.move(t, "VERIFIED_DONE", "A11")
        with self.assertRaises(TaskError):
            self.s.move(t, "VERIFIED_DONE", "A18")

    def test_only_assurance_chairman_or_owner_leader_verifies(self):
        t = self.mk(); self.to_review(t)
        with self.assertRaises(TaskError):
            self.s.move(t, "VERIFIED_DONE", "A14")  # Agent ngang hàng không có thẩm quyền
        self.assertEqual(self.s.move(t, "VERIFIED_DONE", "A20")["state"], "VERIFIED_DONE")

    # IMPORTANT: bằng chứng cũ không dùng cho lần làm mới
    def test_stale_evidence_not_reused_after_retry(self):
        t = self.mk()
        tok = self.s.claim(t, "A11")
        self.s.record(t, "A11", tok, evidence="lần 1")
        self.s.move(t, "FAILED", "A11", token=tok)
        self.s.move(t, "READY", "VUA_CEO")
        tok2 = self.s.claim(t, "A11")
        with self.assertRaises(TaskError):
            self.s.move(t, "REVIEW", "A11", token=tok2)
        self.assertEqual(self.s.get(t)["evidence_refs"], ["lần 1"])  # lịch sử vẫn giữ

    # IMPORTANT: giới hạn thử lại tính mọi lần nhận việc
    def test_retry_cap_counts_interrupt_loops(self):
        t = self.mk()
        claims = 0
        with self.assertRaises(TaskError):
            for _ in range(10):
                tok = self.s.claim(t, "A11"); claims += 1
                self.s.move(t, "INTERRUPTED", "A11", token=tok)
                self.s.move(t, "READY", "VUA_CEO")
        self.assertLessEqual(claims, 4)

    # IMPORTANT: rời WAITING_APPROVAL cần người có thẩm quyền + nội dung duyệt
    def test_approval_requires_authority_and_ref(self):
        t = self.mk()
        tok = self.s.claim(t, "A11")
        self.s.move(t, "WAITING_APPROVAL", "A11", token=tok)
        with self.assertRaises(TaskError):
            self.s.move(t, "READY", "A11", approval="tự duyệt")
        with self.assertRaises(TaskError):
            self.s.move(t, "READY", "VUA_CEO")  # thiếu nội dung duyệt
        got = self.s.move(t, "READY", "VUA_CEO", approval="D-012: cho phép")
        self.assertEqual(got["approval_refs"], ["VUA_CEO: D-012: cho phép"])

    def test_moves_need_registered_actor(self):
        t = self.mk(ready=False)
        with self.assertRaises(TaskError):
            self.s.move(t, "READY", "someone")

    # IMPORTANT: worker phải có trong sổ và không bị tắt
    def test_claim_requires_registered_worker(self):
        t = self.mk()
        with self.assertRaises(TaskError):
            self.s.claim(t, "ghost-worker")

    def test_old_db_worker_history_backfilled(self):
        """DB tạo trước bản sửa: bảng task_workers trống nhưng events có lịch sử nhận việc."""
        t = self.mk(owner_agent_id="A01")
        self.to_review(t, "VUA_CEO")  # cấp trên tự làm rồi định tự duyệt
        self.s.db.execute("DELETE FROM task_workers")  # mô phỏng DB cũ
        path = self.s.path
        self.s.close()
        s2 = TaskStore(path, registry=self.reg, clock=Clock())
        self.addCleanup(s2.close)
        with self.assertRaises(TaskError) as cm:
            s2.move(t, "VERIFIED_DONE", "VUA_CEO")
        self.assertIn("đã từng làm", str(cm.exception))

    # Vòng review 2
    def test_outsider_cannot_cancel_or_resume(self):
        t = self.mk()
        with self.assertRaises(TaskError):
            self.s.move(t, "CANCELLED", "A04")
        tok = self.s.claim(t, "A11")
        self.s.move(t, "PAUSED_QUOTA", "A11", token=tok)
        with self.assertRaises(TaskError):
            self.s.move(t, "READY", "A04")
        self.assertEqual(self.s.move(t, "READY", "A11")["state"], "READY")  # chủ nhiệm vụ được tiếp tục

    def test_quota_pauses_do_not_burn_retries(self):
        t = self.mk()
        for _ in range(6):
            tok = self.s.claim(t, "A11")
            self.s.move(t, "PAUSED_QUOTA", "A11", token=tok)
            self.s.move(t, "READY", "A11")
        self.assertEqual(self.s.get(t)["retry_count"], 0)

    def test_review_rework_counts_as_retry(self):
        t = self.mk()
        with self.assertRaises(TaskError):
            for _ in range(6):
                tok = self.s.claim(t, "A11")
                self.s.record(t, "A11", tok, evidence="e")
                self.s.move(t, "REVIEW", "A11", token=tok)

    # Vòng review 3
    def test_retry_flag_survives_block_detour(self):
        t = self.mk()
        tok = self.s.claim(t, "A11")
        self.s.move(t, "FAILED", "A11", token=tok)
        self.s.move(t, "READY", "VUA_CEO")
        self.s.move(t, "BLOCKED", "VUA_CEO")
        self.s.move(t, "READY", "VUA_CEO")
        self.s.claim(t, "A11")
        self.assertEqual(self.s.get(t)["retry_count"], 1)

    def test_worker_cannot_fail_own_review(self):
        t = self.mk(); self.to_review(t, "A11")
        with self.assertRaises(TaskError):
            self.s.move(t, "FAILED", "A11")
        self.assertEqual(self.s.move(t, "FAILED", "A20")["state"], "FAILED")

    # MINOR
    def test_record_unknown_task_is_task_error(self):
        with self.assertRaises(TaskError):
            self.s.record("T-nope", "A11", 1, evidence="x")

    def test_unknown_dependency_rejected_at_create(self):
        with self.assertRaises(TaskError):
            self.mk(dependencies=["T-nope"])

    def test_idempotency_key_reuse_with_different_payload(self):
        self.mk(idempotency_key="k")
        with self.assertRaises(TaskError):
            self.mk(idempotency_key="k", objective="khác")


class RegistryFixes(unittest.TestCase):
    def write(self, mutate):
        data = {n: json.loads((R.REG_DIR / n).read_text(encoding="utf-8"))
                for n in ("leaders.json", "agents.json", "companies.json", "legacy_map.json")}
        data = copy.deepcopy(data)
        mutate(data)
        d = Path(tempfile.mkdtemp())
        for n, v in data.items():
            (d / n).write_text(json.dumps(v, ensure_ascii=False), encoding="utf-8")
        return d

    def bad(self, mutate):
        with self.assertRaises(R.RegistryError):
            R.load(self.write(mutate))

    def ag(self, d, aid):
        return next(a for a in d["agents.json"]["agents"] if a["id"] == aid)

    def ld(self, d, lid):
        return next(x for x in d["leaders.json"]["leaders"] if x["id"] == lid)

    def test_cmo02_required(self):
        def m(d):
            d["leaders.json"]["leaders"] = [x for x in d["leaders.json"]["leaders"] if x["id"] != "CMO-02"]
        self.bad(m)

    def test_extra_or_second_human_leader_rejected(self):
        self.bad(lambda d: d["leaders.json"]["leaders"].append(
            {"id": "ROGUE", "name": "x", "role": "x", "supervisor": "CHAIRMAN", "kind": "agent", "status": "ACTIVE", "tier": "READ"}))
        self.bad(lambda d: self.ld(d, "CFO").update(kind="human"))

    def test_invalid_leader_tier_rejected(self):
        self.bad(lambda d: self.ld(d, "CFO").update(tier="READ_ONLY"))

    def test_namespace_traversal_and_empty(self):
        self.bad(lambda d: self.ag(d, "A02").update(memory_ns="agents/A02/../A01/"))
        self.bad(lambda d: self.ag(d, "A02").update(memory_ns=""))

    def test_scope_traversal_and_dot(self):
        self.bad(lambda d: self.ag(d, "A01")["write_scope"].append("agents/A01/../A02/x"))
        self.bad(lambda d: self.ag(d, "A14")["write_scope"].append("./skills/"))

    def test_duplicate_ids(self):
        def m(d):
            dup = dict(self.ag(d, "A05")); dup["leader"] = "GHOST"
            d["agents.json"]["agents"].insert(0, dup)
        self.bad(m)

    def test_legacy_required_and_complete(self):
        def m(d):
            d["legacy_map.json"]["units"].pop("N07_ACCOUNT_OPERATIONS")
        self.bad(m)

    def test_scope_into_other_agents_folder(self):
        self.bad(lambda d: self.ag(d, "A01")["write_scope"].append("agents/A02/"))

    def test_agents_as_object_not_list(self):
        self.bad(lambda d: d["agents.json"].update(agents={"x": 1}))

    def test_wrong_types_give_registry_error_not_crash(self):
        self.bad(lambda d: self.ag(d, "A05").update(leader={"id": "VUA_CEO"}))
        self.bad(lambda d: d.update({"agents.json": []}))


if __name__ == "__main__":
    unittest.main()
