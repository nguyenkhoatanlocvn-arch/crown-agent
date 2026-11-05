"""Sổ đăng ký Thiên Não: tầng điều hành mới, 12 ghế TN, đơn vị tự trị.

Quyết định Chủ tịch 05/11/2026: Thiên Não điều hành tất cả; A03-NB và CROWN-4 AT
không qua Vua; giữ A01–A20, thêm TN-01…TN-12.
"""
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from crown import registry as R
from crown.tasks import TaskError, TaskStore

SEAT_FIELDS = ("name", "mission", "limits", "system_instruction", "memory_ns", "skills", "tools",
               "model_tier", "reasoning", "budget", "reports_to", "receives_from")
BUDGET_FIELDS = ("max_tokens_per_task", "max_usd_per_day", "max_minutes_per_task", "max_ai_calls_per_day")


class RealThienNaoTest(unittest.TestCase):
    def setUp(self):
        self.reg = R.load()

    def test_thien_nao_sits_between_chairman_and_vua(self):
        tn = self.reg.leaders["THIEN_NAO"]
        self.assertEqual(tn["supervisor"], "CHAIRMAN")
        self.assertEqual(tn["kind"], "agent")
        self.assertNotEqual(tn["tier"], "SENSITIVE")
        for lid in ("VUA_CEO", "CFO", "CMO-01"):
            self.assertEqual(self.reg.leaders[lid]["supervisor"], "THIEN_NAO", lid)

    def test_independent_control_stays_outside_thien_nao(self):
        # Người kiểm và an ninh không nằm dưới bên bị kiểm.
        for aid in ("A17", "A20"):
            self.assertNotIn("THIEN_NAO", self.reg.chain(aid), aid)

    def test_twelve_seats_with_all_required_fields(self):
        self.assertEqual(sorted(self.reg.tn_seats), R.EXPECTED_TN_SEATS)
        for sid, s in self.reg.tn_seats.items():
            for f in SEAT_FIELDS:
                self.assertIn(f, s, f"{sid} thiếu {f}")
            for b in BUDGET_FIELDS:
                self.assertIn(b, s["budget"], f"{sid} thiếu budget.{b}")
            self.assertEqual(s["reports_to"], "VUA_CEO", sid)
            self.assertEqual(s["receives_from"], "THIEN_NAO", sid)
            self.assertEqual(s["budget"]["max_usd_per_day"], 0, f"{sid}: mặc định chưa bật khoản chi mới")

    def test_ten_seats_point_to_existing_active_agents(self):
        held = {sid: s["holder"] for sid, s in self.reg.tn_seats.items() if s.get("holder")}
        self.assertEqual(len(held), 10)
        for sid, holder in held.items():
            self.assertEqual(self.reg.member(holder)["status"], "ACTIVE", f"{sid} → {holder}")

    def test_two_new_seats_are_members_under_vua(self):
        new = sorted(sid for sid, s in self.reg.tn_seats.items() if not s.get("holder"))
        self.assertEqual(new, ["TN-07", "TN-12"])
        for sid in new:
            self.assertEqual(self.reg.chain(sid), [sid, "VUA_CEO", "THIEN_NAO", "CHAIRMAN"])

    def test_autonomous_units_report_to_thien_nao_not_vua(self):
        for uid in ("A03-NB", "A21-AT"):
            u = self.reg.units[uid]
            self.assertEqual(u["supervisor"], "THIEN_NAO")
            self.assertIn("VUA_CEO", u["bypass"])
            self.assertEqual(self.reg.chain(uid), [uid, "THIEN_NAO", "CHAIRMAN"])

    def test_owner_resolves_seat_to_holder(self):
        self.assertEqual(self.reg.seat_owner("TN-02"), self.reg.tn_seats["TN-02"]["holder"])
        self.assertEqual(self.reg.seat_owner("TN-07"), "TN-07")


class BrokenThienNaoTest(unittest.TestCase):
    def _load(self, mutate):
        d = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        for p in R.REG_DIR.glob("*.json"):
            shutil.copy(p, d / p.name)
        f = d / "thien_nao.json"
        data = json.loads(f.read_text(encoding="utf-8"))
        mutate(data)
        f.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return R.load(d)

    def _seat(self, data, sid):
        return next(s for s in data["seats"] if s["id"] == sid)

    def _unit(self, data, uid):
        return next(u for u in data["units"] if u["id"] == uid)

    def test_missing_seat(self):
        with self.assertRaisesRegex(R.RegistryError, "TN-05"):
            self._load(lambda d: d["seats"].remove(self._seat(d, "TN-05")))

    def test_seat_missing_required_field(self):
        with self.assertRaisesRegex(R.RegistryError, "TN-03.*system_instruction"):
            self._load(lambda d: self._seat(d, "TN-03").pop("system_instruction"))

    def test_seat_holder_must_exist(self):
        with self.assertRaisesRegex(R.RegistryError, "TN-02.*A99"):
            self._load(lambda d: self._seat(d, "TN-02").update(holder="A99"))

    def test_seat_cannot_enable_spending_itself(self):
        def m(d):
            self._seat(d, "TN-11")["budget"]["max_usd_per_day"] = 5
        with self.assertRaisesRegex(R.RegistryError, "TN-11.*chi"):
            self._load(m)

    def test_new_seat_cannot_outrank_vua(self):
        with self.assertRaisesRegex(R.RegistryError, "TN-12"):
            self._load(lambda d: self._seat(d, "TN-12").update(tier="SENSITIVE"))

    def test_unit_cannot_hold_sensitive(self):
        with self.assertRaisesRegex(R.RegistryError, "A21-AT"):
            self._load(lambda d: self._unit(d, "A21-AT").update(tier="SENSITIVE"))

    def test_unit_must_report_to_thien_nao(self):
        with self.assertRaisesRegex(R.RegistryError, "A03-NB"):
            self._load(lambda d: self._unit(d, "A03-NB").update(supervisor="A03-NB"))

    def test_unit_id_cannot_shadow_agent(self):
        with self.assertRaisesRegex(R.RegistryError, "A05"):
            self._load(lambda d: self._unit(d, "A21-AT").update(id="A05"))


class ThienNaoAuthorityTest(unittest.TestCase):
    """Thẩm quyền trên sổ nhiệm vụ đi theo chuỗi chỉ huy mới."""

    def setUp(self):
        self.reg = R.load()
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        self.store = TaskStore(Path(d) / "t.db", registry=self.reg)
        self.addCleanup(self.store.close)

    def _failed(self, owner):
        t = self.store.create(owner_agent_id=owner, objective="việc thử", acceptance_criteria="xong",
                              actor=owner, ready=True)
        tok = self.store.claim(t["task_id"], owner)
        self.store.move(t["task_id"], "FAILED", owner, token=tok, note="hỏng")
        return t["task_id"]

    def test_thien_nao_can_requeue_work_of_its_engine(self):
        tid = self._failed("TN-ENGINE")
        self.assertEqual(self.store.move(tid, "READY", "THIEN_NAO", note="thử lại")["state"], "READY")

    def test_thien_nao_can_requeue_work_under_vua(self):
        tid = self._failed("A11")
        self.assertEqual(self.store.move(tid, "READY", "THIEN_NAO", note="thử lại")["state"], "READY")

    def test_thien_nao_has_no_authority_over_independent_assurance(self):
        tid = self._failed("A20")
        with self.assertRaises(TaskError):
            self.store.move(tid, "READY", "THIEN_NAO", note="can thiệp")


if __name__ == "__main__":
    unittest.main()
