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

## Kiểm định
3 vòng review độc lập (Agent 20 chạy bằng subagent riêng, chỉ đọc). Vòng 1 tìm 2 lỗi NGHIÊM TRỌNG (tự duyệt qua tên viết thường / qua người làm trước) + 9 QUAN TRỌNG; vòng 2–3 tìm thêm lỗi do chính bản sửa gây ra. Tất cả có test tái hiện trong `tests/test_review_fixes.py`.

## Giới hạn đã biết (ghi rõ, chưa xử lý)
- Khi chủ nhiệm vụ cũng là người làm, có thể báo BLOCKED thay vì FAILED để không tốn lượt thử lại. Cần quy tắc ở tầng điều phối (A10) hoặc giới hạn số lần BLOCKED.
- CLI không xác thực người dùng: `--actor` chỉ là ID khai báo. Xác thực thật thuộc tầng web/backend sau này.
- Chưa có giữ chỗ ngân sách, backup tự động, chạy Agent bằng model.

## Chưa có (đúng phạm vi M1-a)
Chưa nối laptop, chưa chạy Agent thật bằng model, chưa có web/3D, chưa có ngân sách giữ chỗ, chưa backup tự động.
"20 Agent đăng ký" ≠ 20 phiên đang chạy: M1 chỉ A03 ACTIVE.

---

# CROWN INFINITY + CROWN PROMPT STUDIO (v0.1.0, 2026-11-06)

Nền tảng chung quản lý dự án, module, công cụ, prompt, kết nối AI và tác vụ. Module ưu tiên: **CROWN PROMPT STUDIO**.
Python ≥3.10 chỉ thư viện chuẩn + SQLite; web responsive không cần Node. Dữ liệu nằm ngoài repo
(`%LOCALAPPDATA%\CrownInfinity` trên Windows, `~/.local/share/crown-infinity` trên Linux).

```
py -m crown_infinity init-owner --username loc      # tạo OWNER (hỏi mật khẩu, không hiện ra)
py -m crown_infinity serve                          # http://localhost:8765/
py -m crown_infinity backup                         # sao lưu nhất quán
py -m crown_infinity restore <tệp.zip> [--verify-only]   # khôi phục (máy chủ phải dừng)
py -m crown_infinity status
powershell -ExecutionPolicy Bypass -File scripts\infinity\start.ps1   # Windows: tạo OWNER nếu thiếu + chạy + mở trình duyệt
python -m unittest discover -s tests/infinity -t .  # test nghiệm thu backend
node tests/web/infinity_ui_test.cjs                 # test giao diện bằng trình duyệt thật (cần Playwright)
```
Tài liệu: `docs/infinity/` — SYSTEM_AUDIT, ARCHITECTURE (ADR, bảo mật, chi phí), RESEARCH, API, TEST_RESULTS, HANDOFF.
