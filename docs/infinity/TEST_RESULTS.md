# CROWN INFINITY — Kết quả kiểm thử (2026-11-06)

**Môi trường:** container Linux cloud (`Linux-6.18 x86_64`), Python 3.13.18, SQLite 3.53.1 (FTS5), Node 22.23.4, Playwright 1.63 + Chromium headless. **Không phải Windows.**
**Lệnh:**
```
python3 -m unittest discover -s tests -t .          # toàn bộ repo: 255 test — OK (198 cũ + 57 mới)
python3 -m unittest discover -s tests/infinity -t . # riêng CROWN INFINITY: 57 test — OK
node tests/web/infinity_ui_test.cjs <thư-mục-ảnh>   # 16/16 kiểm tra trình duyệt — PASS
```

## Theo 15 tiêu chí nghiệm thu
| # | Tiêu chí | Phương pháp | Kết quả |
|---|---|---|---|
| 1 | Prompt tiếng Việt lưu DB; khởi động lại vẫn còn | `T01Persistence` (máy chủ mới cùng thư mục dữ liệu) + `test_ci_cli` (tiến trình `python -m crown_infinity serve` thật: dừng bằng Ctrl+C → chạy lại) + UI tải lại trang | **PASS** |
| 2 | Sửa nội dung tạo phiên bản mới; bản trước và APPROVED không đổi | `T02Versions` (5 lần autosave không tạo phiên bản; v1 APPROVED + production giữ nguyên checksum; trigger chặn UPDATE/DELETE) | **PASS** |
| 3 | Diff đúng; restore tạo bản mới, giữ lịch sử | `T03DiffRestore` (so khớp từng dòng diff; v3 `restored_from`=v1; v2 nguyên vẹn) | **PASS** |
| 4 | Hai phiên cùng revision bị xung đột, không mất âm thầm | `T04Conflict` (API) + UI hai tab: báo xung đột kèm diff, bản tab trước còn nguyên, ghi đè chỉ khi chọn | **PASS** |
| 5 | Tìm tiêu đề/nội dung/tag, lọc dự án | `T05Search` (có dấu, không dấu, đ→d, tag, dự án, phân trang ổn định, quyền) + chạy lại cùng bộ trên đường LIKE dự phòng (`T05SearchFallback`) | **PASS** |
| 6 | Nhập TXT/MD rồi xuất giữ nguyên Unicode; dữ liệu sai bị từ chối | `T06ImportExport` (UTF-8 BOM, UTF-16, NFD, emoji; 7 loại tệp sai mỗi loại có mã lỗi; tất-cả-hoặc-không; trùng không ghi đè) | **PASS** — ghi chú: CRLF được chuẩn hóa thành LF |
| 7 | Backup rồi restore vào DB sạch giữ prompt, phiên bản, quan hệ | `T07BackupRestore` (so số dòng với ảnh chụp, so từng phiên bản/checksum, tag, nguồn, assignment; máy chủ mới đọc được; zip bị sửa/zip-slip bị từ chối) | **PASS** |
| 8 | Migration không xóa dữ liệu, kiểm trên bản sao | `T08Migration` (migration thử 0002 trên bản sao: dữ liệu nguyên, có bản sao lưu trước; migration lỗi hoàn tác; sửa migration đã áp dụng bị phát hiện; DB đang chạy không bị đụng) | **PASS** |
| 9 | Tắt AI: thư viện, phiên bản, tìm kiếm, nhập/xuất chạy; không gọi model | `T09AiOff` (chặn mọi kết nối socket ra ngoài trong suốt luồng: 0 kết nối; `ai_usage` = 0; transport giả không bị gọi) | **PASS** |
| 10 | Không đăng nhập/không quyền bị backend từ chối, kể cả gọi API trực tiếp | `T10Authz` (duyệt **mọi** route trong router → 401; VIEWER/EDITOR/dự án khác; CSRF, Origin, Host; thu hồi phiên; giới hạn đăng nhập) | **PASS** |
| 11 | Agent chỉ đọc đúng prompt/phiên bản được cấp | `T11AgentAssignment` (phiên bản mới + đổi production không đổi assignment; `agent_id` tự khai bị bỏ qua; Agent khác/prompt khác 403; token thu hồi 401; trigger ghim assignment) | **PASS** |
| 12 | Mất kết nối/refresh có xử lý bản nháp; autosave không ghi đè bản phát hành | UI: lỗi 500 và mất mạng → "Lỗi lưu", bản nháp giữ trong trình duyệt → tải lại → đề nghị khôi phục → lưu thành công; phiên bản APPROVED giữ checksum | **PASS** |
| 13 | UI không báo thành công khi API thất bại; không hiện tích hợp giả | UI test (500 → không hiện "Đã lưu"); trang CROWN AGENT hiện sổ nhiệm vụ OFFLINE kèm lý do thật | **PASS** |
| 14 | Lệnh start/build chạy trong môi trường hiện có | `test_ci_cli` (init-owner, serve, dừng, backup, restore, status) trên Linux | **PASS (Linux)** · Windows/PowerShell: **NOT VERIFIED** |
| 15 | AI thật/bridge thật | Không có AI thật hay bridge thật. Logic giữ chỗ ngân sách kiểm bằng transport giả (`T15BudgetReservationMock`: 8 luồng đồng thời, đúng 3 lần được giữ chỗ trong trần 1 USD; thử lại hữu hạn) | **SKIPPED (thật)** · mock PASS — chỉ chứng minh logic cục bộ |

## Kiểm định độc lập (Agent riêng, chỉ đọc, 2026-11-06)
Tìm 9 lỗi có tái hiện: 1 HIGH (khối `{{> P-id}}` lồng nhiều nhánh làm treo máy chủ), 3 MEDIUM (nhập "phiên bản mới"/khôi phục làm mất bản nháp chưa lưu phiên bản; cookie lạ trên localhost làm mọi người bị đăng xuất; cổng ngân sách nhận ước tính âm/NaN), 5 LOW (prompt đã lưu trữ vẫn bị khôi phục/nhập; máy chủ thứ hai xóa khóa của máy chủ đầu nên khôi phục chạy chồng; 401 ẩn danh làm phình nhật ký; `is_active: 0` không thu hồi phiên; đầu vào sai gây lỗi 500).
**Đã sửa cả 9**; ca canh giữ ở `tests/infinity/test_ci_review_fixes.py` (12 test). Chạy bộ test này trên mã TRƯỚC khi sửa (commit `8db5e62`): **11/12 đỏ** (ca còn lại kiểm hành vi đúng); trên mã sau khi sửa: **12/12 xanh**.

## Kiểm tra ngược (cố ý gỡ bảo vệ để chắc test bắt được)
| Bảo vệ bị gỡ | Test bắt được |
|---|---|
| Kiểm revision khi autosave | Có (`T04Conflict`) |
| Trigger phiên bản bất biến | Có (`T02Versions`) |
| Lọc assignment theo Agent | Có (`T11AgentAssignment`) |
| Kiểm CSRF | Có (`T10Authz`) |
| Kiểm quyền dự án | Có (`T10Authz`) |
| Giữ chỗ ngân sách trong giao dịch | Có (`T15BudgetReservationMock`) |

## Chưa kiểm / giới hạn
- Windows 10/11 thật, `start.ps1`/`stop.ps1`, PowerShell 5.1: **NOT VERIFIED**.
- iPhone/Safari thật: **NOT VERIFIED** (chỉ giả lập khung 390px trên Chromium; không cuộn ngang).
- Tải nhiều người dùng đồng thời: chưa đo.
- Test có sẵn của repo `test_parallel_processes_open_fresh_db` chập chờn (lần đầu lỗi "database is locked", các lần sau đạt) — không thuộc mã INFINITY.
