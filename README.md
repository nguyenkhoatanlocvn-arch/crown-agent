# CROWN AGENT — Lõi tổ chức (M1-a)

Sổ đăng ký tổ chức + sổ nhiệm vụ cho CROWN AGENT HOLDINGS (kế thừa KING AI).
Python ≥3.10, chỉ thư viện chuẩn. Không mạng, không secret.

## Có gì
| Phần | File | Vai trò |
|---|---|---|
| Lãnh đạo | `registries/leaders.json` | Chủ tịch, Vua = CEO, CFO, CMO-01, CMO-02 |
| 20 Agent | `registries/agents.json` | Nhiệm vụ, lãnh đạo chính, mức quyền, skill, bộ nhớ, vùng ghi, việc không làm |
| Công ty con | `registries/companies.json` | BTC, Etsy, Shopify, Facebook, X, TikTok/CapCut, Tạp hóa, Venture |
| Mã cũ KING AI | `registries/legacy_map.json` | 25 đơn vị V8 → Agent/công ty mới (giữ làm alias) |
| Kiểm tra tổ chức | `crown/registry.py` | Chặn: thiếu Agent, 2 lãnh đạo, chu trình chỉ huy, quyền con > cha, Agent giữ SENSITIVE, vùng ghi chồng nhau |
| Sổ nhiệm vụ | `crown/tasks.py` | SQLite: máy trạng thái, lease + fencing token, idempotency, bằng chứng bắt buộc, không tự duyệt, nhật ký chỉ thêm |
| Lệnh | `crown/cli.py` | `python -m crown ...` |

## Dùng
```
python -m crown check          # kiểm sổ đăng ký
python -m crown org            # sơ đồ tổ chức
python -m crown agent A03      # hồ sơ Agent
python -m crown task list      # nhiệm vụ (DB: runtime/tasks.db)
python scripts/seed_m1.py      # nạp nhiệm vụ thật ngày 2026-11-04 (chạy lại không nhân đôi)
python -m unittest discover -s tests -t .
```
Windows: dùng `py` thay `python` nếu cần.

## Luật nhiệm vụ
DRAFT → READY → RUNNING → REVIEW → VERIFIED_DONE; nhánh BLOCKED / WAITING_APPROVAL / PAUSED_QUOTA / INTERRUPTED / FAILED / CANCELLED.
- Vào RUNNING chỉ bằng `claim` (một người giữ, có hạn). Rời RUNNING phải kèm token còn hiệu lực.
- REVIEW và VERIFIED_DONE cần bằng chứng; người làm không tự duyệt.
- Thử lại tối đa 3 lần rồi phải có người quyết.

## Chưa có (đúng phạm vi M1-a)
Chưa nối laptop, chưa chạy Agent thật bằng model, chưa có web/3D, chưa có ngân sách giữ chỗ, chưa backup tự động.
"20 Agent đăng ký" ≠ 20 phiên đang chạy: M1 chỉ A03 ACTIVE.
