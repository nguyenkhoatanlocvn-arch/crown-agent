"""Nạp các nhiệm vụ THẬT đang mở ngày 2026-11-04 vào runtime/tasks.db.

Chạy lại nhiều lần an toàn (idempotency key). Không gọi mạng, không secret.
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from crown import registry as R  # noqa: E402
from crown.tasks import TaskError, TaskStore  # noqa: E402

db = Path(os.environ.get("CROWN_DB", ROOT / "runtime" / "tasks.db"))
db.parent.mkdir(parents=True, exist_ok=True)
s = TaskStore(db, registry=R.load())


def ensure(key, **kw):
    return s.create(idempotency_key=key, actor="VUA_CEO", **kw)


king = ensure("m1-king-ai-handoff", owner_agent_id="VUA_CEO", ready=True,
              objective="Tiếp nhận hồ sơ KING AI: đọc bản kiểm kê Codex king-ai-windows-audit-01-ki và source V9",
              acceptance_criteria="Bản đồ hiện trạng: mỗi dữ kiện gắn VERIFIED/REPORTED/UNKNOWN, kèm đường dẫn nguồn")
btc = ensure("m1-btc-readonly-audit", owner_agent_id="CFO", company_id="BTC", ready=True,
             objective="Kiểm tra bot BTC chỉ đọc theo skill btc-readonly-audit",
             acceptance_criteria="Báo cáo kiến trúc bot, quyền key (true/false), lỗ hổng theo mức; không đụng lệnh/secret")
skills = ensure("m1-skill-library-batch1-4", owner_agent_id="A03", ready=True,
                objective="Thư viện skill đợt 1-4 từ GitHub và tài liệu chính thức",
                acceptance_criteria="Mỗi skill có nguồn, license, kết quả kiểm định dạng; ghi trong claude/SKILL_REGISTRY.md")
ensure("m1-decide-cmo-split", owner_agent_id="VUA_CEO",
       objective="Chủ tịch chốt cách chia CMO-01/CMO-02 và chủ quản Venture Studio (D-002, D-007)",
       acceptance_criteria="Quyết định ghi vào sổ D-xxx; cập nhật registries/leaders.json, companies.json")
ensure("m1-etsy-decisions", owner_agent_id="CMO-01", company_id="ETSY",
       objective="Etsy: Chủ tịch chốt DEC-001 (niche) và chọn writer duy nhất cho Master Control; sửa hồ sơ khôi phục bị hỏng",
       acceptance_criteria="Writer ghi trong Sheet; hồ sơ khôi phục sạch, đọc lại xác nhận")


def safe(fn, *a, **kw):
    try:
        return fn(*a, **kw)
    except TaskError:
        return None


for t, note in ((king, "B-001: chờ Chủ tịch Add folder"), (btc, "B-001/B-002: chờ thư mục + terminal laptop")):
    if s.get(t["task_id"])["state"] == "READY":
        s.move(t["task_id"], "BLOCKED", "VUA_CEO", note=note)

if s.get(skills["task_id"])["state"] == "READY":
    tok = s.claim(skills["task_id"], "A03")
    s.record(skills["task_id"], "A03", tok, artifact="Project: claude/SKILL_REGISTRY.md",
             evidence="13 skill CROWN: format PASS (kiểm frontmatter tự viết); hành vi NOT_RUN")
    s.move(skills["task_id"], "REVIEW", "A03", token=tok, note="chờ kiểm định độc lập")

for t in s.list():
    print(f"{t['task_id']}  {t['state']:<14} {t['owner_agent_id']:<8} {t['objective'][:70]}")
s.close()
