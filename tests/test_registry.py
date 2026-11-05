import copy
import json
import tempfile
import unittest
from pathlib import Path

from crown import registry as R


class RealRegistryTest(unittest.TestCase):
    def setUp(self):
        self.reg = R.load()

    def test_full_roster(self):
        self.assertEqual(sorted(self.reg.agents), R.EXPECTED_AGENT_IDS)
        for lid in ("CHAIRMAN", "VUA_CEO", "CFO", "CMO-01", "CMO-02"):
            self.assertIn(lid, self.reg.leaders)

    def test_every_chain_ends_at_chairman(self):
        for mid in list(self.reg.leaders) + list(self.reg.agents):
            self.assertEqual(self.reg.chain(mid)[-1], "CHAIRMAN", mid)

    def test_decisions_reflected(self):
        # D-001 Vua kiêm CEO; D-003 Agent 3 dưới Vua; Kiểm soát độc lập báo thẳng Chủ tịch.
        # 2026-11-05: Chủ tịch chốt Thiên Não điều hành tất cả → Vua báo cáo Thiên Não.
        self.assertEqual(self.reg.leaders["VUA_CEO"]["supervisor"], "THIEN_NAO")
        self.assertEqual(self.reg.leaders["THIEN_NAO"]["supervisor"], "CHAIRMAN")
        self.assertEqual(self.reg.agents["A03"]["leader"], "VUA_CEO")
        self.assertEqual(self.reg.agents["A17"]["leader"], "CHAIRMAN")
        self.assertEqual(self.reg.agents["A20"]["leader"], "CHAIRMAN")
        self.assertEqual(self.reg.companies["BTC"]["owner"], "CFO")

    def test_only_m1_core_active(self):
        active = sorted(a for a, v in self.reg.agents.items() if v["status"] == "ACTIVE")
        # 2026-11-04: B02_BTC kích hoạt A11/A17/A18/A20. 2026-11-05: A01/A02/A04/A10/A12 nắm ghế TN-01/09/08/02/11.
        self.assertEqual(active, ["A01", "A02", "A03", "A04", "A10", "A11", "A12", "A17", "A18", "A20"])

    def test_legacy_25_units_all_mapped(self):
        self.assertEqual(len(self.reg.legacy), 25)


class BrokenRegistryTest(unittest.TestCase):
    """Cố tình làm hỏng để chứng minh bộ kiểm tra bắt được lỗi."""

    def _write(self, mutate):
        src = R.REG_DIR
        data = {n: json.loads((src / n).read_text(encoding="utf-8"))
                for n in ("leaders.json", "agents.json", "companies.json", "legacy_map.json")}
        data = copy.deepcopy(data)
        mutate(data)
        d = Path(tempfile.mkdtemp())
        for n, v in data.items():
            (d / n).write_text(json.dumps(v, ensure_ascii=False), encoding="utf-8")
        return d

    def _agent(self, data, aid):
        return next(a for a in data["agents.json"]["agents"] if a["id"] == aid)

    def assertRejected(self, mutate, needle):
        with self.assertRaises(R.RegistryError) as cm:
            R.load(self._write(mutate))
        self.assertIn(needle, str(cm.exception))

    def test_missing_agent(self):
        self.assertRejected(lambda d: d["agents.json"]["agents"].pop(), "A01..A20")

    def test_unknown_leader(self):
        self.assertRejected(lambda d: self._agent(d, "A05").update(leader="GHOST"), "không tồn tại")

    def test_two_leaders(self):
        self.assertRejected(lambda d: self._agent(d, "A05").update(leader=["VUA_CEO", "CFO"]), "MỘT lãnh đạo")

    def test_agent_cannot_hold_sensitive(self):
        self.assertRejected(lambda d: self._agent(d, "A08").update(tier="SENSITIVE"), "SENSITIVE")

    def test_child_tier_above_boss(self):
        def m(d):
            next(l for l in d["leaders.json"]["leaders"] if l["id"] == "CFO")["tier"] = "READ"
            self._agent(d, "A08")["tier"] = "SANDBOX_WRITE"
        self.assertRejected(m, "cao hơn cấp trên")

    def test_command_cycle(self):
        def m(d):
            ls = d["leaders.json"]["leaders"]
            next(l for l in ls if l["id"] == "CMO-01")["supervisor"] = "CMO-02"
        self.assertRejected(m, "Chu trình")

    def test_overlapping_write_scope(self):
        self.assertRejected(lambda d: self._agent(d, "A14")["write_scope"].append("skills/prompts/"), "chồng nhau")

    def test_wildcard_overlap(self):
        self.assertRejected(lambda d: self._agent(d, "A13")["write_scope"].append("companies/VENTURE/budget/"), "chồng nhau")

    def test_memory_namespace_outside_own_folder(self):
        self.assertRejected(lambda d: self._agent(d, "A02").update(memory_ns="agents/A01/"), "namespace")

    def test_legacy_points_to_missing(self):
        self.assertRejected(lambda d: d["legacy_map.json"]["units"]["B02_BTC"].update(maps_to=["NOPE"]), "Mã cũ")

    def test_empty_file(self):
        d = self._write(lambda d: None)
        (d / "agents.json").write_text("", encoding="utf-8")
        with self.assertRaises(R.RegistryError) as cm:
            R.load(d)
        self.assertIn("rỗng", str(cm.exception))

    def test_corrupt_json(self):
        d = self._write(lambda d: None)
        (d / "companies.json").write_text("{not json", encoding="utf-8")
        with self.assertRaises(R.RegistryError):
            R.load(d)


class ScopeOverlapTest(unittest.TestCase):
    def test_cases(self):
        self.assertTrue(R.scopes_overlap("skills/", "skills/x/"))
        self.assertTrue(R.scopes_overlap("companies/*/ledger/", "companies/ETSY/"))
        self.assertFalse(R.scopes_overlap("companies/*/ledger/", "companies/*/budget/"))
        self.assertFalse(R.scopes_overlap("agents/A01/", "agents/A02/"))


if __name__ == "__main__":
    unittest.main()


class SubagentTest(unittest.TestCase):
    """2026-11-05: Agent con A03.1-A03.3."""
    def setUp(self):
        import copy, tempfile, shutil
        self.tmp = Path(tempfile.mkdtemp()); shutil.copytree(R.REG_DIR, self.tmp, dirs_exist_ok=True)
        self.sub = json.loads((self.tmp / "subagents.json").read_text(encoding="utf-8"))
    def _write(self): (self.tmp / "subagents.json").write_text(json.dumps(self.sub, ensure_ascii=False), encoding="utf-8")
    def test_real_subagents_load_and_chain(self):
        reg = R.load()
        self.assertEqual(sorted(reg.subagents), ["A03.1", "A03.2", "A03.3"])
        self.assertEqual(reg.chain("A03.2"), ["A03.2", "A03", "VUA_CEO", "THIEN_NAO", "CHAIRMAN"])
    def test_scope_outside_parent_rejected(self):
        self.sub["subagents"][0]["write_scope"] = ["companies/BTC/"]; self._write()
        with self.assertRaises(R.RegistryError): R.load(self.tmp)
    def test_sibling_overlap_rejected(self):
        self.sub["subagents"][1]["write_scope"] = ["skills/"]; self._write()
        with self.assertRaises(R.RegistryError): R.load(self.tmp)
    def test_tier_above_parent_rejected(self):
        self.sub["subagents"][0]["tier"] = "OPERATIONAL_WRITE"; self._write()
        with self.assertRaises(R.RegistryError): R.load(self.tmp)
    def test_bad_id_or_parent_rejected(self):
        self.sub["subagents"][0]["id"] = "A04.1"; self._write()
        with self.assertRaises(R.RegistryError): R.load(self.tmp)
