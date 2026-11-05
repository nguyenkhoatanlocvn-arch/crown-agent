"""Sổ đăng ký: bộ phận có Agent đồng điều hành (dept_*.json) — ngang quyền trong phạm vi, không vượt quyền."""
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from crown import registry as R
from crown.tasks import TaskStore


class DeptRegistryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="regtest-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        shutil.copytree(R.REG_DIR, self.tmp / "reg")
        self.path = self.tmp / "reg" / "dept_tool.json"
        self.d = json.loads(self.path.read_text(encoding="utf-8"))

    def load_with(self, mutate):
        mutate(self.d)
        self.path.write_text(json.dumps(self.d, ensure_ascii=False), encoding="utf-8")
        return R.load(self.tmp / "reg")

    def assertBroken(self, mutate, msg):
        with self.assertRaisesRegex(R.RegistryError, msg):
            self.load_with(mutate)

    def test_real_registry_has_tool_department_with_two_peers(self):
        reg = R.load()
        self.assertEqual(reg.departments["TOOL"]["executive_ids"], ["agent-tool", "agent-tool-at"])
        self.assertEqual(reg.member("agent-tool-at")["clone_of"], "agent-tool")
        self.assertEqual(reg.boss_of("agent-tool"), "CHAIRMAN")
        self.assertEqual(len(reg.departments["TOOL"]["branches"]), 5)
        self.assertEqual(reg.departments["TOOL"]["limits"]["max_concurrent_subagents"], 2)

    def test_registry_without_department_file_still_loads(self):
        self.path.unlink()
        reg = R.load(self.tmp / "reg")
        self.assertEqual((reg.departments, reg.executives), ({}, {}))

    def test_executive_cannot_hold_sensitive_or_outrank_peer(self):
        self.assertBroken(lambda d: d["executives"][0].update(tier="SENSITIVE"), "chỉ Chủ tịch giữ quyền SENSITIVE")

    def test_peer_must_be_real_leader_not_chairman(self):
        self.assertBroken(lambda d: d["executives"][0].update(peer_of=["CHAIRMAN"]), "peer_of phải là danh sách lãnh đạo")
        self.assertBroken(lambda d: d["executives"][0].update(peer_of=["KHONG_CO"]), "peer_of phải là danh sách lãnh đạo")
        self.assertBroken(lambda d: d["executives"][0].update(peer_scope=" "), "peer_scope phải ghi rõ")

    def test_executive_reports_to_chairman_only(self):
        self.assertBroken(lambda d: d["executives"][1].update(supervisor="VUA_CEO"), "báo thẳng Chủ tịch")

    def test_write_scope_stays_inside_department_and_off_other_agents(self):
        self.assertBroken(lambda d: d["executives"][0].update(write_scope=["skills/"]), "vùng ghi phải nằm trong vùng bộ phận")
        self.assertBroken(lambda d: d["department"].update(domain="skills/"), "chồng vùng ghi của A03")
        self.assertBroken(lambda d: d["executives"][0].update(memory_ns="agents/A03/"), "bộ nhớ phải nằm trong vùng bộ phận")

    def test_ids_do_not_collide_and_decision_is_recorded(self):
        self.assertBroken(lambda d: d["executives"][0].update(id="A03"), "ID trùng với lãnh đạo/Agent đã có")
        self.assertBroken(lambda d: d.pop("decision_ref"), "thiếu decision_ref")
        self.assertBroken(lambda d: d["executives"][1].update(clone_of="agent-tool-at"), "clone_of phải là Agent điều hành khác")
        self.assertBroken(lambda d: d["limits"].update(max_concurrent_subagents=0), "max_concurrent_subagents")

    def test_task_ledger_accepts_tool_executives(self):
        """Nối vào sổ nhiệm vụ đang dùng: Thiên Công tạo/nhận việc; người làm không tự duyệt; Chủ tịch duyệt được."""
        reg = R.load()
        store = TaskStore(self.tmp / "tasks.db", registry=reg)
        self.addCleanup(store.close)
        t = store.create(owner_agent_id="agent-tool", objective="Tool đánh lại số phụ đề", acceptance_criteria="7,19 → 1,2",
                         actor="CHAIRMAN", ready=True)
        tok = store.claim(t["task_id"], "agent-tool")
        store.record(t["task_id"], "agent-tool", tok, evidence="tool/runs/x/run.json")
        store.move(t["task_id"], "REVIEW", "agent-tool", token=tok)
        with self.assertRaises(Exception):
            store.move(t["task_id"], "VERIFIED_DONE", "agent-tool")
        self.assertEqual(store.move(t["task_id"], "VERIFIED_DONE", "CHAIRMAN")["state"], "VERIFIED_DONE")


if __name__ == "__main__":
    unittest.main()
