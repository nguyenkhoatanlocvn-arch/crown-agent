# Bộ não điều hành v1 (CROWN AGENT trong CROWN INFINITY) — 2026-11-06

Chủ sở hữu: Nguyễn Khoa Tấn Lộc. Phạm vi phiên: bộ nhớ quyết định theo dự án, bản đồ mã Graphify, workflow "Rà soát bản build", lịch 2 giờ.
Giai đoạn này **chỉ đọc và đề xuất**: không tự merge, không deploy, không tăng ngân sách AI (mặc định 0).

## 1. Tái sử dụng phần đã có
| Phần có sẵn | Dùng lại thế nào |
|---|---|
| Projects + thành viên dự án | Quyết định, bản đồ, lượt rà soát, lịch đều gắn `project_id`; kiểm quyền bằng `require_project` |
| Tasks | Mỗi lượt rà soát tạo một nhiệm vụ `T-…` giao `VUA_CEO` (IN_PROGRESS → DONE) |
| Agent identities + token | Agent đọc qua Bearer token; quyền theo nhiệm vụ ở bảng mới `agent_grants` |
| AI Hub (cổng ngân sách) | Chọn model phân tích/kiểm tra lại trong danh sách provider; gọi qua `ai_hub.gate`/`call_model` |
| Phân quyền OWNER/EDITOR/VIEWER | Ghi quyết định, rà soát, lịch, cấp quyền Agent: OWNER. Thành viên dự án được đọc |
| Activity Logs | Mọi thao tác ghi `decision.*`, `graph.import`, `review.*`, `schedule.update`, `agent.grant` |
| Sao lưu/migration | Migration `0002_ops_brain.sql`, tự sao lưu trước khi nâng schema |

## 2. Màn hình "Điều hành" (`#/ops`)
Web và desktop (Tauri, giai đoạn sau) dùng chung trang `crown_infinity/web/js/ops.js` và API `/api/v1`.
Gồm: bộ nhớ quyết định · Rà soát bản build · Lịch tự động · Bản đồ mã (Graphify).
**Đã chạy thử:** Linux (container cloud) + Chromium headless, khung desktop 1360px và điện thoại 390px. **Windows: chưa chạy thử.**

## 3. Bộ nhớ quyết định
- `DECISION` = quyết định đã được người xác nhận (cơ sở dữ liệu bắt buộc có `confirmed_by` + `confirmed_at`).
- `PROPOSAL` = đề xuất do AI/Agent viết (chỉ Agent có quyền `READ_PROPOSE` trên nhiệm vụ mới ghi được); hiển thị nhãn
  "ĐỀ XUẤT AI/AGENT — CHƯA XÁC NHẬN", không được Agent Vua dùng làm căn cứ, chỉ thành quyết định khi OWNER bấm "Xác nhận".
- OWNER: xem, thêm, sửa (cần revision), thu hồi (cần lý do), xem lịch sử. Lịch sử chỉ thêm (trigger chặn sửa/xóa), mỗi dòng
  giữ snapshot đầy đủ. Quyết định không xóa được, chỉ thu hồi.

### Quyết định đầu tiên của Chủ tịch
> Tạm hoãn Voice Studio, Video Studio và SEO Studio. Ưu tiên nền tảng CROWN INFINITY.
> Nguồn: tin nhắn của Chủ tịch trong phiên Claude Code ngày 06/11/2026.

Đã lưu và đọc lại trong **cơ sở dữ liệu chạy thử** (container cloud) qua giao diện. Cơ sở dữ liệu trên máy Windows của anh là
tệp khác, nên cần ghi lại một lần trên máy đó — trên giao diện (Điều hành → "+ Thêm quyết định") hoặc dòng lệnh:
```
py -m crown_infinity decision-add --as-user loc --project "Chung" --title "Ưu tiên nền tảng CROWN INFINITY" ^
  --content "Tạm hoãn Voice Studio, Video Studio và SEO Studio. Ưu tiên nền tảng CROWN INFINITY." ^
  --source "Tin nhắn của Chủ tịch trong phiên Claude Code ngày 06/11/2026"
```

## 4. Graphify — kết quả kiểm tra (2026-11-06)
| Mục | Kết quả | Nhãn |
|---|---|---|
| Repo | https://github.com/Graphify-Labs/graphify — HEAD `16218bfc80d4ca2799734d045039f7ed1a5fa9fb` (2026-11-05) | VERIFIED (git clone) |
| Gói | PyPI **`graphifyy`** (hai chữ y) 0.9.103, wheel sha256 `7948c030f8b04153d88bc1ee23a485ddaa0c2d6aaa03a748c177e3c217bcfe34` khớp PyPI; tên `graphify` một chữ y: không có trên PyPI | VERIFIED |
| Giấy phép | Apache-2.0 (LICENSE); phần đóng góp trước khi đổi giấy phép vẫn theo MIT (NOTICE, LICENSE-MIT) → dùng thương mại được | VERIFIED (đọc tệp) |
| Lệnh | `graphify extract <thư mục> --code-only` → `graphify-out/graph.json`; truy vấn `query`/`path`/`explain` | VERIFIED (đã chạy) |
| Nhu cầu API | Phần code: tree-sitter cục bộ, không cần khóa. Phần tài liệu/ảnh/PDF cần model (tự dò khóa Gemini→Kimi→Claude→OpenAI…, có thể gọi `claude -p`) → **tắt bằng `GRAPHIFY_BACKEND=none` + `--code-only`** | VERIFIED (README) + đã chạy với mạng bị cắt |
| Chạy thử | Phạm vi `crown_infinity/` lấy bằng `git archive` (chỉ tệp đã commit; không dữ liệu chạy, không backup, không `.env`), `unshare -n` cắt mạng, không biến khóa API → 532 nút, 2300 cạnh (2140 EXTRACTED, 160 INFERRED) ở commit `541e9af` | VERIFIED |
| Giới hạn | Tệp `.sql` không được phân tích (thiếu gói `graphifyy[sql]`); `app.css` không phân loại. Trên Windows không cắt mạng được bằng `unshare` → dựa vào `--code-only` + `GRAPHIFY_BACKEND=none` + môi trường con không có khóa API | VERIFIED / NOT VERIFIED (Windows) |
| Không làm | KHÔNG chạy `graphify install` hay `graphify hook install` (chúng sửa cấu hình trợ lý và git hook). Không dùng Graphify Cloud | Quy tắc skill graphify-eval |

Bản đồ lưu trong database: `graph_maps` (commit nguồn, thời điểm tạo, phiên bản Graphify, băm graph.json, danh sách tệp + sha256),
`graph_nodes`, `graph_edges` (nhãn `EXTRACTED`/`INFERRED`, tệp + dòng). Mở nguồn bằng `git show <commit>:<tệp>` và đối chiếu sha256.

### Cài Graphify trên Windows (tùy chọn; chỉ cần nếu muốn dựng bản đồ)
```
py -m venv %LOCALAPPDATA%\CrownInfinity\graphify-venv
%LOCALAPPDATA%\CrownInfinity\graphify-venv\Scripts\pip install graphifyy==0.9.103
set CROWN_INFINITY_GRAPHIFY=%LOCALAPPDATA%\CrownInfinity\graphify-venv\Scripts\graphify.exe
py -m crown_infinity graph-build --as-user loc --project "Chung"      (hoặc nút "Dựng bản đồ" trên màn Điều hành)
```
Cần Git for Windows trên máy (đọc commit, `git archive`, `git show`). Chưa thử trên Windows.

## 5. Rà soát bản build
Bấm nút → Agent Vua nhận nhiệm vụ `T-…` + quyền `READ` chỉ trên dự án đang chọn (bảng `agent_grants`), thu hồi khi xong.
| Bước | Nguồn | Gọi AI |
|---|---|---|
| Đọc quyết định còn hiệu lực | database (DECISION/ACTIVE); đề xuất chưa xác nhận được tách riêng | không |
| Xác định commit | `git rev-parse`, `git log`, `git status` | không |
| Tra bản đồ | bản đồ mới nhất của dự án; so commit; tệp đổi từ commit bản đồ; một quan hệ EXTRACTED mẫu | không |
| Đọc báo cáo test | `docs/infinity/TEST_RESULTS.md` → nhãn REPORTED (KHÔNG chạy lại test); so tệp mã đổi sau báo cáo | không |
| AI phân tích / kiểm tra lại | provider chọn trong AI Hub, qua cổng ngân sách | chỉ khi cổng cho phép VÀ có transport; hiện **chưa có transport** → luôn "KHÔNG" kèm lý do |
| Tổng hợp theo quy tắc | `review-rules-1`: VERIFIED / REPORTED / UNKNOWN + việc tiếp theo + người phụ trách (A18, A20, A11, Agent Vua, Chủ tịch) | không |
**Chống chạy trùng:** vân tay = dự án + commit + băm thay đổi chưa commit + id/revision quyết định + bản đồ + băm báo cáo test + model đã chọn + phiên bản quy tắc. Trùng → ghi lượt `SKIPPED_DUPLICATE` trỏ về lượt gốc; chỉ mục UNIQUE trong database chặn cả khi hai yêu cầu đến cùng lúc.

## 6. Lịch mỗi 2 giờ
- Mặc định TẮT; chỉ bật được sau ít nhất một lượt thủ công thành công. Lưu trong bảng `schedules`.
- Chạy BÊN TRONG tiến trình `py -m crown_infinity serve` (luồng nền, nhịp 60 giây; tắt bằng `--no-scheduler`).
  **Máy phải bật:** máy đang chạy máy chủ và giữ dữ liệu (máy Windows của Chủ tịch). Máy ngủ/tắt → không chạy; bật lại → chạy bù MỘT lần.
- Giành lượt bằng compare-and-set trên `next_run_at` → nhiều nhịp đồng thời chỉ chạy một lượt. Lượt cùng đầu vào tự bỏ qua.
- Màn hình hiện lần chạy gần nhất, lần kế tiếp, và nhịp dịch vụ (ONLINE nếu nhịp cuối ≤ 3 phút).

## 7. Mở trên Windows
```
cd <thư mục repo crown-agent>
git fetch origin feature/crown-infinity-ops-brain
git checkout feature/crown-infinity-ops-brain
powershell -ExecutionPolicy Bypass -File scripts\infinity\start.ps1
```
Mở http://localhost:8765/#/ops. Khởi động lần đầu sau khi cập nhật sẽ tự sao lưu rồi nâng schema 1 → 2.
Muốn dựng bản đồ: cài Graphify như mục 4 rồi chạy `start.ps1` trong cùng cửa sổ đã `set CROWN_INFINITY_GRAPHIFY=…`.

## 8. Giới hạn còn lại
- Chưa chạy trên Windows; PowerShell, Git for Windows, Graphify trên Windows: NOT VERIFIED.
- Không có lời gọi model thật (chưa có transport, ngân sách 0). Bước AI luôn ghi "KHÔNG gọi AI" kèm lý do.
- Rà soát không chạy lại test (chỉ đọc báo cáo → REPORTED). Muốn VERIFIED phải có worker chạy test — giai đoạn sau, cần OWNER duyệt.
- Đối chiếu "quyết định tạm hoãn" chỉ so TÊN tệp đã đổi với từ khóa (Voice/Video/SEO), không đọc nội dung.
- Lịch phụ thuộc máy chủ đang chạy; chưa có dịch vụ Windows tự khởi động.
- Agent bên ngoài (Claude/GPT) muốn dùng API phải được OWNER cấp token + quyền theo nhiệm vụ; chưa có giao diện cấp quyền riêng (dùng API `/api/v1/agent-grants`).
