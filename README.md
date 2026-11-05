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

## Thiên Não Tối Thượng (Phase B — 06/11/2026)

Tầng điều hành trên Agent Vua theo lệnh DRAGON SINGULARITY V1.0. Bộ máy luật chạy theo lịch (không AI) + phiên Claude nhận việc qua sổ.

| Phần | File | Vai trò |
|---|---|---|
| Sổ Thiên Não | `registries/thien_nao.json`, `models.json` | 12 ghế TN, 3 đơn vị, lịch, ngân sách, sổ mô hình |
| Bảng quyền | `registries/permissions.json`, `approvals.json`, `CONSTITUTION.lock`, `crown/policy.py` | Tự chủ / cần phê duyệt riêng / chỉ Chủ tịch; khóa hiến pháp |
| Phiên chạy | `crown/cycle.py`, `crown/statesync.py` | Mốc giờ VN, khóa, watchdog, thử lại, dead-letter, cầu dao; trạng thái trên nhánh `tn-state` |
| Bảng việc, router | `crown/taskboard.py`, `crown/router.py` | 17 trường, 11 trạng thái; chọn bậc mô hình theo việc và hạn mức |
| Chu trình 9 pha | `crown/thiennao.py`, `crown/runner.py` | scheduler → planner → Agent Vua → nhiệm vụ → kiểm → báo cáo |
| Lệnh | `scripts/run_cycle.py` | `rule`, `ai-claim`, `ai-finish`, `status` |
| Lịch, CI | `.github/workflows/` | 12 mốc/ngày; test + kiểm quyền mỗi pull request |

```
python scripts/run_cycle.py status        # đọc trạng thái mới nhất, không ghi
python scripts/tn_trial.py                # chạy thử 13 mục trên "GitHub giả" trong thư mục tạm
python scripts/tn_docs.py --check         # D03 có khớp registries/ không
python -m crown check                     # sổ đăng ký + bảng quyền + khóa hiến pháp
```
Tài liệu: `docs/thien_nao/D02_ARCHITECTURE.md` (kiến trúc, giới hạn, cách bật/quay lại), `D03_PERMISSION_MATRIX.md`, `AI_LANE_PROMPT.md`.
Chưa có: lần chạy thật trên GitHub Actions, phiên AI thật, tab Thiên Não trên web, bộ nhớ có truy xuất, Evolution Lab.

## Luật nhiệm vụ
DRAFT → READY → RUNNING → REVIEW → VERIFIED_DONE; nhánh BLOCKED / WAITING_APPROVAL / PAUSED_QUOTA / INTERRUPTED / FAILED / CANCELLED.
- Vào RUNNING chỉ bằng `claim` (một người giữ, có hạn). Rời RUNNING phải kèm token còn hiệu lực.
- REVIEW và VERIFIED_DONE cần bằng chứng; người làm không tự duyệt.
- Thử lại tối đa 3 lần rồi phải có người quyết.

## Kiểm định
3 vòng review độc lập (Agent 20 chạy bằng subagent riêng, chỉ đọc). Vòng 1 tìm 2 lỗi NGHIÊM TRỌNG (tự duyệt qua tên viết thường / qua người làm trước) + 9 QUAN TRỌNG; vòng 2–3 tìm thêm lỗi do chính bản sửa gây ra. Tất cả có test tái hiện trong `tests/test_review_fixes.py`.

## Giới hạn đã biết (ghi rõ, chưa xử lý)
- Khi chủ nhiệm vụ cũng là người làm, có thể báo BLOCKED thay vì FAILED để không tốn lượt thử lại. Cần quy tắc ở tầng điều phối (A10) hoặc giới hạn số lần BLOCKED.
- CLI không xác thực người dùng: `--actor` chỉ là ID khai báo. Xác thực thật thuộc tầng web/backend sau này.
- Chưa có giữ chỗ ngân sách, backup tự động, chạy Agent bằng model.

## Chưa có (đúng phạm vi M1-a)
Chưa nối laptop, chưa chạy Agent thật bằng model, chưa có web/3D, chưa có ngân sách giữ chỗ, chưa backup tự động.
"20 Agent đăng ký" ≠ 20 phiên đang chạy: M1 chỉ A03 ACTIVE.
