# CROWN INFINITY — Bàn giao phiên 2026-11-06

## Trạng thái
- Nhánh: `feature/crown-infinity-prompt-studio` (repo `nguyenkhoatanlocvn-arch/crown-agent`). **Chưa merge vào `master`, chưa mở PR.**
- Milestone đạt: **M1 — lõi nền tảng + luồng hoàn chỉnh của CROWN PROMPT STUDIO** từ giao diện tới database, có test tự động.
- Không có tiến trình chạy nền 24/7, không có lịch tự chạy, không triển khai cloud, không kết nối máy Windows.

## Tệp đã thêm/sửa
| Đường dẫn | Nội dung |
|---|---|
| `crown_infinity/` | Mã nguồn (Python stdlib) + `web/` (giao diện) + `migrations/0001_init.sql` + `modules.json` |
| `tests/infinity/` | 47 test backend (helpers, prompts, platform, cli) |
| `tests/web/infinity_ui_test.cjs` | 16 kiểm tra giao diện bằng Chromium thật |
| `docs/infinity/` | SYSTEM_AUDIT, ARCHITECTURE (ADR + bảo mật + chi phí), RESEARCH, API, TEST_RESULTS, HANDOFF |
| `scripts/infinity/start.ps1`, `stop.ps1` | Chạy/dừng trên Windows (chưa thử trên Windows) |
| `README.md` | Thêm mục CROWN INFINITY (phần cũ giữ nguyên) |
Không sửa: `crown/`, `tool/`, `registries/`, `office/`, `b02_btc/`, `skills/`.

## Chạy (Windows, sau khi `git pull` nhánh này)
```
cd <thư mục repo crown-agent>
git fetch origin feature/crown-infinity-prompt-studio
git checkout feature/crown-infinity-prompt-studio
powershell -ExecutionPolicy Bypass -File scripts\infinity\start.ps1
```
Hoặc từng bước: `py -m crown_infinity init-owner --username loc` → `py -m crown_infinity serve` → mở http://localhost:8765/.
Dữ liệu: `%LOCALAPPDATA%\CrownInfinity\`. Sao lưu: trang Quản trị → "Sao lưu ngay", hoặc `py -m crown_infinity backup`.

## Lỗi/giới hạn còn mở
1. Chưa chạy trên Windows thật (start.ps1/stop.ps1, đường dẫn `%LOCALAPPDATA%`, FTS5 của Python Windows): **NOT VERIFIED**. Nếu SQLite không có FTS5, ứng dụng tự lùi về tìm kiếm LIKE (đã test).
2. Test có sẵn của repo `tests.test_tasks.ConcurrentInitTest.test_parallel_processes_open_fresh_db` chập chờn ("database is locked" ở `crown/tasks.py:104` — `PRAGMA journal_mode=WAL` chạy trước khi đặt `busy_timeout`). Không thuộc phạm vi phiên này; chưa sửa.
3. Chưa có: MFA/SSO, AI Writer/Optimizer, Test Lab gọi model thật, semantic search, board kiểu YouMind, đồng bộ nhiều thiết bị, Local Bridge, Desktop (Tauri), đồng bộ sang Hoàng Thành Rồng.
4. Truy cập từ iPhone/ngoài nhà chưa bật (cần reverse proxy HTTPS — quyết định của OWNER).

## Milestone kế tiếp (đề xuất theo thứ tự)
- **M2 — chạy thật trên máy Windows của Chủ tịch**: chạy `start.ps1`, nhập 20–50 prompt thật từ thư mục hiện có, sao lưu/khôi phục thử, ghi kết quả vào TEST_RESULTS (chuyển các mục NOT VERIFIED sang VERIFIED/FAIL).
- **M3 — Test Lab tĩnh + mock**: lưu ca thử (đầu vào, expected output, rubric), chạy phân tích tĩnh hàng loạt; chưa gọi model.
- **M4 — AI Hub thật (khi OWNER duyệt ngân sách)**: transport cho 1 provider, đo usage/chi phí thật, AI Writer/Optimizer tạo bản đề xuất + diff.
- **M5 — Tích hợp CROWN AGENT**: Agent A14/A03 đọc prompt qua token; xuất trạng thái sang Hoàng Thành Rồng; đọc `tasks.db` thật trên laptop.
- **M6 — Desktop (Tauri) + private web** sau khi M2 ổn định.

## Quyết định cần OWNER
1. Merge nhánh vào `master` hay để review thêm? (Repo có tác vụ AT tự push `master` mỗi giờ.)
2. Repo đang private hay public? (Tài liệu trong repo mâu thuẫn — xem SYSTEM_AUDIT.)
3. Có cần truy cập từ iPhone/ngoài nhà không; nếu có, chọn phương án đường hầm/VPN nào (cần tra giá/điều khoản trước).
4. Có cấp ngân sách AI (USD/ngày, provider) cho M4 không — hiện 0.
5. Có đăng ký CROWN INFINITY vào `registries/` (vùng ghi của A14/A18) không — phiên này không sửa sổ đăng ký.

## Tiếp tục phiên mới
Đọc `docs/infinity/HANDOFF.md` → `ARCHITECTURE.md` (ADR) → chạy `python -m unittest discover -s tests/infinity -t .` và `node tests/web/infinity_ui_test.cjs` để xác nhận trạng thái trước khi sửa. Không lưu secret trong repo; khóa API (khi có) đặt vào biến môi trường phía máy chủ và chỉ ghi TÊN biến trong AI Hub.
