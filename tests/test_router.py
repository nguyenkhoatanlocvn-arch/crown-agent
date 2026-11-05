"""Model router D06, bản tối thiểu (lệnh DRAGON SINGULARITY mục 05).

Không coi mô hình là có sẵn khi chưa xác minh. Hết ngân sách: việc thường hạ bậc,
việc rủi ro cao phải CHỜ. Mọi nhiệm vụ có phương án dự phòng.
"""
import copy
import json
import shutil
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from crown import cycle as C
from crown import policy as P
from crown import registry as R
from crown import router as M

DAY = "2026-11-06"
NOW = datetime(2026, 11, 5, 17, 17, 5, tzinfo=timezone.utc)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.reg = R.load()
        self.cy = C.Cycle(self.tmp / "tn.db", registry=self.reg, policy=P.Policy.load(self.reg, clock=lambda: NOW),
                          clock=lambda: NOW)
        self.addCleanup(self.cy.close)
        self.models = json.loads(M.MODELS_FILE.read_text(encoding="utf-8"))
        self.rt = M.Router(self.reg, self.models, self.cy)

    def pick(self, work, risk="LOW", **kw):
        return self.rt.assign(work, risk, day=DAY, **kw)


class RealRouterTest(Base):
    def test_periodic_check_uses_rules_not_ai(self):
        d = self.pick("PERIODIC_CHECK")
        self.assertEqual((d["decision"], d["tier"], d["model"]), ("RULE", "RULE", "rule-engine"))
        self.assertEqual(self.cy.usage(DAY), {})

    def test_architecture_gets_advanced_model_and_spends_one_call(self):
        d = self.pick("ARCHITECTURE")
        self.assertEqual((d["decision"], d["tier"], d["model"]), ("ASSIGNED", "ADVANCED", "opus"))
        self.assertEqual(self.cy.usage(DAY), {"tier:ADVANCED": 1})

    def test_default_table_matches_the_order(self):
        want = {"LOG_TRIAGE": "FAST", "PERIODIC_CHECK": "RULE", "RESEARCH": "BALANCED", "CODE": "CODING",
                "ARCHITECTURE": "ADVANCED", "SECURITY_AUDIT": "ADVANCED", "MAJOR_UPGRADE": "ADVANCED"}
        self.assertEqual({w: self.rt.wanted_tier(w) for w in want}, want)

    def test_unconnected_models_are_never_chosen(self):
        usable = {m["id"] for m in self.models["models"] if m["status"] in ("VERIFIED", "REPORTED", "LISTED")}
        for work in self.models["routing"]:
            for risk in ("LOW", "MEDIUM", "HIGH", "CRITICAL"):
                d = self.rt.assign(work, risk, day=DAY, take=False)
                if d["model"] is not None:
                    self.assertIn(d["model"], usable, f"{work}/{risk}")
                for f in d["fallback"]:
                    self.assertTrue(f == "WAIT" or f in usable, f"{work}/{risk} fallback {f}")

    def test_every_decision_has_a_fallback_plan(self):
        for work in self.models["routing"]:
            self.assertTrue(self.rt.assign(work, "LOW", day=DAY, take=False)["fallback"], work)

    def test_out_of_budget_low_risk_work_is_downgraded(self):
        cap = self.reg.tn_config["budget"]["tier_calls_per_day"]["ADVANCED"]
        for _ in range(cap):
            self.assertEqual(self.pick("ARCHITECTURE")["decision"], "ASSIGNED")
        d = self.pick("ARCHITECTURE")
        self.assertEqual((d["decision"], d["wanted_tier"], d["model"]), ("DOWNGRADED", "ADVANCED", "sonnet"))
        self.assertEqual(self.cy.usage(DAY)["tier:ADVANCED"], cap)

    def test_out_of_budget_high_risk_work_waits_instead_of_downgrading(self):
        cap = self.reg.tn_config["budget"]["tier_calls_per_day"]["ADVANCED"]
        for _ in range(cap):
            self.pick("MAJOR_UPGRADE")
        before = self.cy.usage(DAY)
        d = self.pick("MAJOR_UPGRADE", risk="HIGH")
        self.assertEqual((d["decision"], d["model"], d["fallback"]), ("WAIT", None, ["WAIT"]))
        self.assertEqual(self.cy.usage(DAY), before)

    def test_untested_model_is_allowed_only_for_low_risk(self):
        haiku = next(m for m in self.models["models"] if m["id"] == "haiku")
        self.assertEqual(haiku["status"], "LISTED")              # có trong danh sách, chưa gọi thử
        self.assertEqual(self.pick("LOG_TRIAGE", "LOW")["model"], "haiku")
        d = self.pick("LOG_TRIAGE", "MEDIUM")
        self.assertEqual((d["decision"], d["model"]), ("UPGRADED", "sonnet"))

    def test_security_audit_and_high_risk_need_independent_reviewer(self):
        self.assertTrue(self.pick("SECURITY_AUDIT")["independent_review"])
        self.assertTrue(self.pick("CODE", "HIGH")["independent_review"])
        self.assertFalse(self.pick("CODE", "LOW")["independent_review"])

    def test_take_false_does_not_spend_budget(self):
        self.rt.assign("ARCHITECTURE", "LOW", day=DAY, take=False)
        self.assertEqual(self.cy.usage(DAY), {})

    def test_unknown_work_or_risk_is_an_error(self):
        with self.assertRaises(M.RouterError):
            self.pick("MAGIC")
        with self.assertRaises(M.RouterError):
            self.pick("CODE", "EXTREME")

    def test_decision_explains_itself_on_six_criteria(self):
        d = self.pick("RESEARCH")
        self.assertEqual(sorted(d["criteria"]), ["AVAILABILITY", "CONTEXT", "COST", "LATENCY", "QUALITY", "RISK"])
        self.assertTrue(d["reason"])


class BrokenModelsTest(Base):
    def _bad(self, mutate, pattern):
        m = copy.deepcopy(self.models)
        mutate(m)
        with self.assertRaisesRegex(M.RouterError, pattern):
            M.Router(self.reg, m, self.cy)

    def _model(self, m, mid):
        return next(x for x in m["models"] if x["id"] == mid)

    def test_verified_needs_evidence_and_date(self):
        self._bad(lambda m: self._model(m, "opus").update(evidence=""), "opus")
        self._bad(lambda m: self._model(m, "opus").update(verified_at=None), "opus")

    def test_unknown_status_or_tier(self):
        self._bad(lambda m: self._model(m, "sonnet").update(status="PROBABLY"), "sonnet")
        self._bad(lambda m: self._model(m, "sonnet").update(tier="ULTRA"), "sonnet")

    def test_routing_must_point_to_known_tier(self):
        self._bad(lambda m: m["routing"]["CODE"].update(tier="ULTRA"), "CODE")

    def test_paid_api_stays_off_while_budget_is_zero(self):
        def m(d):
            # Giả sử GPT đã nối xong và được xếp ưu tiên nhất: vẫn không được chọn khi chưa bật khoản chi.
            self._model(d, "gpt").update(status="VERIFIED", evidence="thử", verified_at="2026-11-06",
                                         billing="paid_api", preference=0)
        mm = copy.deepcopy(self.models)
        m(mm)
        rt = M.Router(self.reg, mm, self.cy)
        self.assertEqual(self.reg.tn_config["budget"]["usd_per_day"], 0)
        for _ in range(6):
            d = rt.assign("ARCHITECTURE", "LOW", day=DAY)
            self.assertNotEqual(d["model"], "gpt")
            self.assertNotIn("gpt", d["fallback"])


if __name__ == "__main__":
    unittest.main()
