"""Bảng quyền D03 (lệnh DRAGON SINGULARITY mục 02, 16).

Tự chủ: giao việc, phân bổ, lịch, kiểm thử, nhánh mã, nghiên cứu.
Phải có phê duyệt riêng của Chủ tịch: chi tiền, giao dịch tiền thật, bí mật, xóa dữ liệu
quan trọng, triển khai rủi ro cao. Chỉ Chủ tịch: quyền gốc, quy tắc bảo mật, hiến pháp.
"""
import copy
import json
import shutil
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from crown import policy as P
from crown import registry as R
from crown.tasks import TaskError
from crown.vua import Vua

AUTONOMOUS = ["TASK_MANAGE", "RESOURCE_ALLOCATE", "AGENT_LAUNCH", "SCHEDULE_MANAGE", "REPORT_READ",
              "TEST_RUN", "CODE_BRANCH_WRITE", "RESEARCH"]
SENSITIVE = ["SPEND", "REAL_MONEY_TRADE", "SECRET_ACCESS", "DELETE_CRITICAL_DATA", "DEPLOY_HIGH_RISK"]
CHAIRMAN_ONLY = ["ROOT_ADMIN_CHANGE", "SECURITY_RULE_CHANGE", "CONSTITUTION_CHANGE", "EMERGENCY_STOP_CLEAR",
                 "PERMISSION_GRANT"]
NOW = datetime(2026, 11, 6, 3, 0, tzinfo=timezone.utc)


def approvals(*items):
    return {"version": "t", "approvals": list(items)}


def ok_approval(**over):
    a = {"id": "AP-001", "action": "DEPLOY_HIGH_RISK", "principal": "THIEN_NAO", "granted_by": "CHAIRMAN",
         "granted_at": "2026-11-05T17:00:00+00:00", "expires_at": "2026-11-07T00:00:00+00:00", "scope": "thử"}
    a.update(over)
    return a


class RealPolicyTest(unittest.TestCase):
    def setUp(self):
        self.reg = R.load()
        self.pol = P.Policy.load(self.reg, clock=lambda: NOW)

    def test_thien_nao_runs_daily_operations_on_its_own(self):
        for a in AUTONOMOUS:
            self.assertEqual(self.pol.decide("THIEN_NAO", a).verdict, "ALLOW", a)

    def test_sensitive_actions_wait_for_chairman(self):
        for actor in ("THIEN_NAO", "VUA_CEO", "A18", "A21-AT"):
            for a in SENSITIVE:
                self.assertEqual(self.pol.decide(actor, a).verdict, "NEEDS_APPROVAL", f"{actor} {a}")

    def test_chairman_only_actions_are_denied_to_every_agent(self):
        for actor in ("THIEN_NAO", "VUA_CEO", "CFO", "A17", "A20", "TN-ENGINE", "A03-NB"):
            for a in CHAIRMAN_ONLY:
                self.assertEqual(self.pol.decide(actor, a, approval_id="AP-001").verdict, "DENY", f"{actor} {a}")

    def test_chairman_may_do_everything(self):
        for a in AUTONOMOUS + SENSITIVE + CHAIRMAN_ONLY:
            self.assertEqual(self.pol.decide("CHAIRMAN", a).verdict, "ALLOW", a)

    def test_unknown_actor_or_action_is_denied(self):
        self.assertEqual(self.pol.decide("GHOST", "TASK_MANAGE").verdict, "DENY")
        self.assertEqual(self.pol.decide("THIEN_NAO", "FLY_TO_MOON").verdict, "DENY")

    def test_agents_without_own_row_get_rights_from_their_tier(self):
        self.assertEqual(self.reg.agents["A04"]["tier"], "READ")
        self.assertEqual(self.pol.decide("A04", "RESEARCH").verdict, "ALLOW")
        self.assertEqual(self.pol.decide("A04", "CODE_BRANCH_WRITE").verdict, "DENY")
        self.assertEqual(self.reg.agents["A18"]["tier"], "SANDBOX_WRITE")
        self.assertEqual(self.pol.decide("A18", "CODE_BRANCH_WRITE").verdict, "ALLOW")
        self.assertEqual(self.pol.decide("A18", "SCHEDULE_MANAGE").verdict, "DENY")

    def test_emergency_stop_blocks_work_but_not_reading(self):
        self.assertEqual(self.pol.decide("THIEN_NAO", "TASK_MANAGE", estop=True).verdict, "DENY")
        self.assertEqual(self.pol.decide("THIEN_NAO", "REPORT_READ", estop=True).verdict, "ALLOW")
        self.assertEqual(self.pol.decide("THIEN_NAO", "EMERGENCY_STOP_SET", estop=True).verdict, "ALLOW")
        self.assertEqual(self.pol.decide("CHAIRMAN", "EMERGENCY_STOP_CLEAR", estop=True).verdict, "ALLOW")

    def test_protected_files_cannot_be_changed_by_agents(self):
        for path in ("registries/permissions.json", "registries/thien_nao.json", "registries/leaders.json",
                     "registries/vuong_toa.json", "registries/approvals.json", "crown/policy.py",
                     ".github/workflows/dragon-singularity-cycle.yml"):
            self.assertEqual(self.pol.guard_paths("THIEN_NAO", [path]).verdict, "DENY", path)
        self.assertEqual(self.pol.guard_paths("THIEN_NAO", ["crown/cycle.py", "docs/x.md"]).verdict, "ALLOW")
        self.assertEqual(self.pol.guard_paths("CHAIRMAN", ["registries/permissions.json"]).verdict, "ALLOW")

    def test_require_raises_with_reason(self):
        with self.assertRaisesRegex(P.PolicyError, "Chủ tịch"):
            self.pol.require("THIEN_NAO", "SPEND")
        self.pol.require("THIEN_NAO", "TASK_MANAGE")

    def test_matrix_rows_cover_every_principal(self):
        rows = {r["principal"]: r for r in self.pol.matrix_rows()}
        for must in ("CHAIRMAN", "THIEN_NAO", "VUA_CEO", "TN-ENGINE", "A03-NB", "A21-AT", "A20", "TN-07"):
            self.assertIn(must, rows)
        self.assertEqual(rows["THIEN_NAO"]["SPEND"], "NEEDS_APPROVAL")
        self.assertEqual(rows["THIEN_NAO"]["CONSTITUTION_CHANGE"], "DENY")
        self.assertEqual(rows["CHAIRMAN"]["CONSTITUTION_CHANGE"], "ALLOW")

    def test_constitution_lock_matches_repo(self):
        self.assertEqual(self.pol.verify_lock(R.ROOT), [])

    def test_thien_nao_may_issue_goals_at_vuong_toa_but_not_money(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        v = Vua(Path(d) / "t.db", registry=self.reg)
        self.addCleanup(v.close)
        for dom in ("SKILL", "DOCS", "WEB", "CODE"):
            v.authorize("THIEN_NAO", dom)
        with self.assertRaises(TaskError):
            v.authorize("THIEN_NAO", "MONEY")


class ApprovalTest(unittest.TestCase):
    def setUp(self):
        self.reg = R.load()
        self.matrix = json.loads(P.MATRIX_FILE.read_text(encoding="utf-8"))

    def _pol(self, appr):
        return P.Policy(self.reg, self.matrix, appr, clock=lambda: NOW)

    def test_valid_chairman_approval_unlocks_exactly_that_action(self):
        pol = self._pol(approvals(ok_approval()))
        self.assertEqual(pol.decide("THIEN_NAO", "DEPLOY_HIGH_RISK", approval_id="AP-001").verdict, "ALLOW")
        self.assertEqual(pol.decide("THIEN_NAO", "SPEND", approval_id="AP-001").verdict, "NEEDS_APPROVAL")
        self.assertEqual(pol.decide("VUA_CEO", "DEPLOY_HIGH_RISK", approval_id="AP-001").verdict, "NEEDS_APPROVAL")
        self.assertEqual(pol.decide("THIEN_NAO", "DEPLOY_HIGH_RISK", approval_id="AP-404").verdict, "NEEDS_APPROVAL")

    def test_expired_approval_is_ignored(self):
        pol = self._pol(approvals(ok_approval(expires_at="2026-11-06T02:59:00+00:00")))
        self.assertEqual(pol.decide("THIEN_NAO", "DEPLOY_HIGH_RISK", approval_id="AP-001").verdict, "NEEDS_APPROVAL")

    def test_approval_not_from_chairman_is_rejected_at_load(self):
        with self.assertRaisesRegex(P.PolicyError, "AP-001"):
            self._pol(approvals(ok_approval(granted_by="THIEN_NAO")))

    def test_approval_cannot_cover_chairman_only_action(self):
        with self.assertRaisesRegex(P.PolicyError, "CONSTITUTION_CHANGE"):
            self._pol(approvals(ok_approval(action="CONSTITUTION_CHANGE")))


class BrokenMatrixTest(unittest.TestCase):
    """Ma trận tự nâng quyền phải bị từ chối ngay khi nạp."""

    def setUp(self):
        self.reg = R.load()
        self.matrix = json.loads(P.MATRIX_FILE.read_text(encoding="utf-8"))

    def _bad(self, mutate, pattern):
        m = copy.deepcopy(self.matrix)
        mutate(m)
        with self.assertRaisesRegex(P.PolicyError, pattern):
            P.Policy(self.reg, m, approvals())

    def test_agent_cannot_be_granted_everything(self):
        self._bad(lambda m: m["grants"].update({"THIEN_NAO": ["*"]}), "THIEN_NAO")

    def test_agent_cannot_be_granted_sensitive_action(self):
        self._bad(lambda m: m["grants"]["VUA_CEO"].append("SPEND"), "VUA_CEO.*SPEND")

    def test_agent_cannot_be_granted_chairman_only_action(self):
        self._bad(lambda m: m["grants"]["THIEN_NAO"].append("CONSTITUTION_CHANGE"), "CONSTITUTION_CHANGE")

    def test_unknown_principal_or_action(self):
        self._bad(lambda m: m["grants"].update({"GHOST": ["REPORT_READ"]}), "GHOST")
        self._bad(lambda m: m["grants"]["THIEN_NAO"].append("FLY"), "FLY")

    def test_chairman_must_keep_full_rights(self):
        self._bad(lambda m: m["grants"].update({"CHAIRMAN": ["REPORT_READ"]}), "CHAIRMAN")

    def test_policy_file_itself_must_stay_protected(self):
        self._bad(lambda m: m["protected_paths"].remove("registries/permissions.json"), "permissions.json")


class LockTest(unittest.TestCase):
    def test_tampered_protected_file_is_detected(self):
        d = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        shutil.copytree(R.ROOT / "registries", d / "registries")
        (d / "crown").mkdir()
        shutil.copy(R.ROOT / "crown" / "policy.py", d / "crown" / "policy.py")
        if (R.ROOT / ".github").exists():
            shutil.copytree(R.ROOT / ".github", d / ".github")
        pol = P.Policy.load(R.load(), clock=lambda: NOW)
        self.assertEqual(pol.verify_lock(d), [])
        with open(d / "registries" / "leaders.json", "a", encoding="utf-8") as fh:
            fh.write("\n")
        bad = pol.verify_lock(d)
        self.assertIn("registries/leaders.json", [b["path"] for b in bad])


if __name__ == "__main__":
    unittest.main()
