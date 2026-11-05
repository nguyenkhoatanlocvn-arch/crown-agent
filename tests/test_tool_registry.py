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
        self.assertBroken(lambda d: d["executives"][0].update(memory_ns="agents/A03/"), "bộ nhớ phải nằm trong vùng bộ phận")
        self.assertBroken(lambda d: d["department"].update(domain="skills/"), "chưa được Chủ tịch phê duyệt")

    def test_new_department_needs_chairman_approved_entry_not_just_a_file(self):
        base = json.loads(self.path.read_text(encoding="utf-8"))
        for dom in ("crown/", "b02_btc/", ".git/", "tests/", "zz/"):          # thả thêm một tệp dept_*.json là không đủ
            extra = dict(base, department={"id": "X", "domain": dom},
                         executives=[dict(base["executives"][0], id="agent-x", memory_ns=dom + "m/", write_scope=[dom])])
            extra["executives"][0].pop("clone_of", None)
            (self.tmp / "reg" / "dept_x.json").write_text(json.dumps(extra, ensure_ascii=False), encoding="utf-8")
            with self.assertRaisesRegex(R.RegistryError, "chưa được Chủ tịch phê duyệt", msg=dom):
                R.load(self.tmp / "reg")

    def test_executive_id_branch_tier_and_limits_are_validated(self):
        for bad in ("../../crown", "", "A03.9", "Agent-Tool", "tool"):
            d = json.loads(self.path.read_text(encoding="utf-8"))
            d["executives"][0]["id"] = bad
            self.path.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
            with self.assertRaisesRegex(R.RegistryError, "ID Agent điều hành phải dạng", msg=bad):
                R.load(self.tmp / "reg")
        self.path.write_text(json.dumps(self.d, ensure_ascii=False), encoding="utf-8")
        def weak_execs(d):
            d["executives"][0]["tier"] = d["executives"][1]["tier"] = "READ"
            d["branches"][2]["tier"] = "OPERATIONAL_WRITE"
        self.assertBroken(weak_execs, "cao quyền hơn Agent điều hành")

    def test_limits_reject_booleans_and_runaway_values(self):
        self.assertBroken(lambda d: d["limits"].update(max_auto_releases_per_session=999), "max_auto_releases_per_session")
        self.d["limits"]["max_auto_releases_per_session"] = 1
        self.assertBroken(lambda d: d["limits"].update(max_concurrent_subagents=True), "max_concurrent_subagents")
        self.d["limits"]["max_concurrent_subagents"] = 2
        self.assertBroken(lambda d: d["executives"][0].update(peer_of=["VUA_CEO", "VUA_CEO"]), "không trùng")

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
