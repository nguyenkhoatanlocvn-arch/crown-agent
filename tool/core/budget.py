"""Hạn mức CFO cho TOOL.

Nguồn luật (đọc 2026-11-05 từ Kho Báu `kho_rules/main` và sổ quyết định D-006):
mọi khoản chi tiêu thêm do Agent đề xuất, Chủ tịch duyệt từng khoản; mặc định chi thêm = 0.
TOOL chỉ dùng dịch vụ tính phí khi đã có khoản được duyệt ghi trong tool/config/budget.json.
"""
from __future__ import annotations

import copy
import re
from pathlib import Path

from .util import ToolError, atomic_write_json, iso, mutex, read_json, tool_root

DEFAULT = {
    "currency": "USD",
    "paid_services_cap": 0,
    "approved": [],
    "spent": [],
    "source": "Kho Báu kho_rules/main (đọc 2026-11-05): mọi khoản chi thêm Chủ tịch duyệt từng khoản; D-006 chi thêm mặc định 0",
    "updated_at": None,
}


class Budget:
    def __init__(self, root: Path | None = None):
        self.root = Path(root) if root else tool_root()
        self.path = self.root / "config" / "budget.json"

    def load(self) -> dict:
        # Sao chép sâu: danh sách approved/spent mặc định không được dùng chung giữa các lần gọi.
        return dict(copy.deepcopy(DEFAULT), **read_json(self.path, default={}))

    def remaining(self) -> float:
        b = self.load()
        cap = float(b["paid_services_cap"]) + sum(float(a["amount"]) for a in b["approved"])
        return cap - sum(float(s["amount"]) for s in b["spent"])

    def require(self, cost: float, what: str, actor: str) -> None:
        """Ném ToolError nếu khoản này vượt phần ngân sách đã được duyệt."""
        if cost < 0:
            raise ToolError("Chi phí không được âm")
        if cost == 0:
            return
        rem = self.remaining()
        if cost > rem + 1e-9:
            raise ToolError(f"Vượt hạn mức CFO: '{what}' cần {cost:g} USD, còn {rem:g} USD đã duyệt. "
                            f"Tạo đề xuất chi_them trên Kho Báu để Chủ tịch duyệt ({actor} không tự chi).")

    def approve(self, amount: float, what: str, approval_ref: str, actor: str) -> dict:
        """Ghi một khoản ĐÃ được Chủ tịch duyệt trên Kho Báu (mã đề xuất bắt buộc). Chỉ Chủ tịch ghi.

        Giới hạn đã biết: ID là tự khai, nên đây là hàng rào chống tự duyệt nhầm và là dấu vết kiểm tra,
        không phải chữ ký. Khoản duyệt hiện trên web (tab Agent & lịch) để Chủ tịch đối chiếu với Kho Báu.
        """
        if actor != "CHAIRMAN":
            raise ToolError(f"Chỉ Chủ tịch ghi khoản duyệt chi — {actor or '(không nêu tên)'} bị từ chối (Agent không tự duyệt ngân sách)")
        if isinstance(amount, bool) or not isinstance(amount, (int, float)) or not 0 < amount <= 10_000:
            raise ToolError("Khoản duyệt phải là số > 0 (và không quá 10.000 USD một khoản)")
        ref = (approval_ref or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]{3,63}", ref):
            raise ToolError("Thiếu mã đề xuất đã duyệt trên Kho Báu (4–64 ký tự chữ/số, vd. KHO-DX-001)")
        if not (what or "").strip():
            raise ToolError("Khoản duyệt phải ghi rõ chi cho việc gì")
        with mutex(self.path):
            b = self.load()
            if any(a["approval_ref"] == ref for a in b["approved"]):
                return b
            b["approved"].append({"amount": amount, "what": what.strip(), "approval_ref": ref, "by": actor, "at": iso()})
            b["updated_at"] = iso()
            atomic_write_json(self.path, b)
            return b

    def spend(self, amount: float, what: str, actor: str, evidence: str) -> None:
        with mutex(self.path):
            self.require(amount, what, actor)
            b = self.load()
            b["spent"].append({"amount": amount, "what": what, "actor": actor, "evidence": evidence, "at": iso()})
            b["updated_at"] = iso()
            atomic_write_json(self.path, b)
