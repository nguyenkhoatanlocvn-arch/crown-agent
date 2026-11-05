"""Model router của Thiên Não (D06, bản tối thiểu — lệnh DRAGON SINGULARITY mục 05).

Trước mỗi nhiệm vụ: chọn bậc mô hình theo loại việc, rồi chọn mô hình có thật trong bậc
đó. Nguyên tắc:
- Việc làm được bằng luật thì không gọi AI.
- Mô hình chưa xác minh không được chọn (LISTED chỉ cho việc rủi ro thấp).
- Mô hình trả phí theo API không được chọn khi ngân sách chi = 0.
- Hết hạn mức bậc: việc thường hạ bậc; việc rủi ro cao CHỜ, không tự hạ tiêu chuẩn.
- Mọi quyết định có phương án dự phòng và lý do theo 6 tiêu chí.

Chưa có số đo QUALITY/LATENCY/COST/CONTEXT thật; các ô đó ghi "chưa đo".
"""
from __future__ import annotations

import json
from datetime import timedelta, timezone

from . import registry as R

MODELS_FILE = R.REG_DIR / "models.json"
STATUSES = ("VERIFIED", "REPORTED", "LISTED", "NOT_CONNECTED", "NOT_SURVEYED")
RISKS = ("LOW", "MEDIUM", "HIGH", "CRITICAL")
HIGH_RISK = ("HIGH", "CRITICAL")


class RouterError(Exception):
    """Sổ mô hình hỏng hoặc yêu cầu chọn mô hình không hợp lệ."""


class Router:
    def __init__(self, registry: R.Registry, models: dict | None = None, cycle=None):
        self.reg = registry
        self.models = models if models is not None else json.loads(MODELS_FILE.read_text(encoding="utf-8"))
        self.cycle = cycle
        self.budget = registry.tn_config.get("budget", {})
        self.validate()

    # ---------- kiểm sổ mô hình ----------
    def validate(self) -> None:
        errors: list[str] = []
        tiers = self.models.get("tiers")
        if not isinstance(tiers, list) or tiers[:1] != ["RULE"]:
            raise RouterError("models.json: 'tiers' phải là danh sách bắt đầu bằng RULE, tăng dần theo chi phí")
        seen = set()
        for m in self.models.get("models", []):
            mid = m.get("id", "?")
            if mid in seen:
                errors.append(f"Mô hình {mid}: ID trùng")
            seen.add(mid)
            if m.get("tier") not in tiers or any(t not in tiers for t in m.get("also", [])):
                errors.append(f"Mô hình {mid}: bậc không hợp lệ")
            if m.get("status") not in STATUSES:
                errors.append(f"Mô hình {mid}: trạng thái '{m.get('status')}' không hợp lệ")
            if m.get("status") == "VERIFIED" and (not str(m.get("evidence", "")).strip() or not m.get("verified_at")):
                errors.append(f"Mô hình {mid}: VERIFIED phải có bằng chứng và ngày xác minh")
            if m.get("billing") not in ("none", "plan", "paid_api"):
                errors.append(f"Mô hình {mid}: billing phải là none, plan hoặc paid_api")
        for work, r in self.models.get("routing", {}).items():
            if r.get("tier") not in tiers or ("escalate_to" in r and r["escalate_to"] not in tiers):
                errors.append(f"Loại việc {work}: bậc không hợp lệ")
        if errors:
            raise RouterError("Sổ mô hình không hợp lệ:\n- " + "\n- ".join(errors))

    # ---------- tra cứu ----------
    def wanted_tier(self, work_type: str) -> str:
        r = self.models["routing"].get(work_type)
        if r is None:
            raise RouterError(f"Loại việc '{work_type}' không có trong bảng phân bổ")
        return r["tier"]

    def usable(self, tier: str, risk: str) -> list[dict]:
        ok = {"VERIFIED", "REPORTED"} | ({"LISTED"} if risk == "LOW" else set())
        paid_on = float(self.budget.get("usd_per_day", 0)) > 0
        out = [m for m in self.models["models"]
               if (m["tier"] == tier or tier in m.get("also", []))
               and m["status"] in ok and (m["billing"] != "paid_api" or paid_on)]
        return sorted(out, key=lambda m: m.get("preference", 1))

    def _cap(self, tier: str) -> int:
        return int(self.budget.get("tier_calls_per_day", {}).get(tier, 0))

    def _day(self) -> str:
        now = self.cycle.clock() if self.cycle is not None else None
        if now is None:
            raise RouterError("Thiếu ngày để tính hạn mức")
        off = self.reg.tn_config.get("schedule", {}).get("utc_offset_hours", 7)
        return now.astimezone(timezone(timedelta(hours=off))).date().isoformat()

    def _budget_ok(self, tier: str, day: str, take: bool) -> tuple[bool, int]:
        cap = self._cap(tier)
        if self.cycle is None:
            return cap > 0, 0
        used = self.cycle.usage(day).get(f"tier:{tier}", 0)
        if take:
            return self.cycle.usage_take(day, f"tier:{tier}", cap), used
        return used < cap, used

    # ---------- quyết định ----------
    def assign(self, work_type: str, risk: str, *, day: str | None = None, take: bool = True) -> dict:
        if risk not in RISKS:
            raise RouterError(f"Mức rủi ro phải thuộc {RISKS}")
        wanted = self.wanted_tier(work_type)
        route = self.models["routing"][work_type]
        tiers = self.models["tiers"]
        review = bool(route.get("independent_review")) or risk in HIGH_RISK
        base = {"work_type": work_type, "risk": risk, "wanted_tier": wanted, "independent_review": review}

        if wanted == "RULE":
            esc = route.get("escalate_to")
            fb = [m["id"] for m in self.usable(esc, risk)] if esc else []
            return {**base, "decision": "RULE", "tier": "RULE", "model": "rule-engine", "fallback": fb + ["WAIT"],
                    "reason": "Việc làm được bằng luật — không gọi AI",
                    "criteria": self._criteria("RULE", "rule-engine", "VERIFIED", risk, review, None, None)}

        day = day or self._day()
        i = tiers.index(wanted)
        ai_tiers = [t for t in tiers if t != "RULE"]
        lower = [t for t in reversed(tiers[:i]) if t in ai_tiers]
        higher = [t for t in tiers[i + 1:] if t in ai_tiers]
        order = [(wanted, "ASSIGNED")]
        if risk not in HIGH_RISK:                      # việc rủi ro cao không được đổi bậc
            order += [(t, "DOWNGRADED") for t in lower] + [(t, "UPGRADED") for t in higher]

        blocked = []
        for tier, decision in order:
            cands = self.usable(tier, risk)
            if not cands:
                blocked.append(f"{tier}: không có mô hình đã xác minh")
                continue
            ok, used = self._budget_ok(tier, day, take)
            if not ok:
                blocked.append(f"{tier}: hết hạn mức ngày ({used}/{self._cap(tier)})")
                continue
            chosen = cands[0]
            fb = [m["id"] for m in cands[1:]]
            if risk not in HIGH_RISK:
                for t, _ in order:
                    if t != tier:
                        fb += [m["id"] for m in self.usable(t, risk) if m["id"] not in fb and m["id"] != chosen["id"]]
            why = f"{work_type} → bậc {wanted}" if decision == "ASSIGNED" else \
                  f"{work_type} muốn bậc {wanted} nhưng {'; '.join(blocked)} → dùng bậc {tier}"
            return {**base, "decision": decision, "tier": tier, "model": chosen["id"], "fallback": fb + ["WAIT"],
                    "reason": why,
                    "criteria": self._criteria(tier, chosen["id"], chosen["status"], risk, review, used + (1 if take else 0), self._cap(tier))}

        why = "; ".join(blocked) or "không có lựa chọn"
        if risk in HIGH_RISK:
            why += " — việc rủi ro cao phải chờ, không tự hạ tiêu chuẩn"
        return {**base, "decision": "WAIT", "tier": None, "model": None, "fallback": ["WAIT"], "reason": why,
                "criteria": self._criteria(wanted, None, None, risk, review, None, self._cap(wanted))}

    @staticmethod
    def _criteria(tier, model, status, risk, review, used, cap) -> dict:
        return {
            "QUALITY": f"bậc {tier} theo bảng phân bổ; chưa có điểm đo",
            "LATENCY": "chưa đo",
            "COST": "0 (luật)" if tier == "RULE" else (f"gói hiện có; hạn mức ngày bậc {tier}: {used}/{cap}" if used is not None else f"hạn mức ngày bậc {tier}: {cap}"),
            "CONTEXT": "chưa đo",
            "AVAILABILITY": f"{model}: {status}" if model else "không có mô hình dùng được",
            "RISK": f"{risk}; kiểm độc lập: {'có' if review else 'không bắt buộc'}",
        }
