# CROWN INFINITY — Kiến trúc, quyết định (ADR), bảo mật, chi phí

Phiên bản 0.1.0 · 2026-11-06 · Nguyên tắc: BUILD ONCE — REUSE ACROSS WEB AND DESKTOP · LOCAL-FIRST — AI-ON-DEMAND.

## 1. Tổng quan
```
Trình duyệt (web responsive, ES module thuần, không bước build)
   │  HTTPS (qua reverse proxy, khi truy cập từ xa) / HTTP 127.0.0.1 (máy cục bộ)
   ▼
crown_infinity (Python ≥3.10, chỉ thư viện chuẩn) — modular monolith
   httpcore.py   router, JSON, lỗi có cấu trúc, header bảo mật, tệp tĩnh
   app.py        định tuyến /api/v1, xác thực phiên, CSRF, kiểm Host/Origin, cổng bật/tắt module
   auth.py       người dùng, phiên, quyền theo dự án, nhật ký chỉ thêm
   prompts.py    CROWN PROMPT STUDIO
   platform.py   dự án, Module Registry, Tool Registry, Tasks, nhật ký, cài đặt, dashboard
   agents.py     adapter CROWN AGENT (chỉ đọc) + danh tính/token Agent + cấp prompt ghim phiên bản
   ai_hub.py     provider, ngân sách, cổng chặn + giữ chỗ (AI TẮT mặc định)
   backup.py     sao lưu nhất quán / khôi phục có kiểm tra
   db.py         kết nối, giao dịch BEGIN IMMEDIATE, migration chỉ tiến
   ▼
SQLite (WAL, khóa ngoại, trigger bất biến, FTS5) tại thư mục dữ liệu NGOÀI repo
   %LOCALAPPDATA%\CrownInfinity\crown_infinity.sqlite  (+ backups\, logs\, server.lock)
```
Repo `crown-agent` chỉ được đọc: `registries/*.json` (danh tính Agent), `tool/catalog/` (Tool Registry), `runtime/tasks.db` nếu có (sổ nhiệm vụ, `mode=ro`).

## 2. Quyết định kiến trúc (ADR) — Claude quyết theo ủy quyền; OWNER bác được
| Mã | Quyết định | Lý do | Đánh đổi / cách đảo ngược |
|---|---|---|---|
| ADR-001 | Backend Python **chỉ thư viện chuẩn** (`http.server` + router tự viết), không FastAPI | Giữ stack hiện hữu của repo (crown/, tool/ đều stdlib); trên Windows chỉ cần `py`, không pip, không chuỗi cung ứng gói; ít phụ thuộc, chi phí 0 | Phải tự viết validation/CSRF; `http.server` không nên phơi thẳng Internet → mặc định 127.0.0.1, truy cập xa qua reverse proxy HTTPS. Hợp đồng REST giữ nguyên nên đổi sang FastAPI/uvicorn là việc cơ học |
| ADR-002 | Frontend **ES module thuần + CSS**, không React/Vite | Không cần Node trên máy chạy; mở ngay; CSP chặt (`script-src 'self'`, không inline) | Mã UI dài hơn; khi UI lớn có thể chuyển sang Vite mà giữ API |
| ADR-003 | **SQLite** (WAL, FTS5) là nguồn dữ liệu có thẩm quyền duy nhất; localStorage chỉ là dự phòng bản nháp | Một người dùng/ít người dùng, local-first, sao lưu đơn giản | Nhiều người dùng đồng thời từ xa → cân nhắc PostgreSQL (lớp `db.py` cô lập SQL) |
| ADR-004 | Mô hình phiên bản: `draft_content` (autosave) ≠ `prompt_versions` (snapshot bất biến, trigger chặn sửa/xóa) ≠ `review_status` (từng phiên bản) ≠ `production_version_id` (con trỏ, chỉ trỏ bản APPROVED, đổi bằng compare-and-set) | Autosave không bao giờ chạm bản phát hành; khôi phục = tạo phiên bản mới | Prompt không xóa cứng được, chỉ lưu trữ |
| ADR-005 | Xung đột: `revision` + header `If-Match`; sai → 409 kèm bản trên máy chủ; client tự gửi lại **chỉ khi** bản nháp máy chủ không đổi (chỉ thông tin khác đổi), còn lại hiện diff để người dùng chọn | Không last-write-wins âm thầm | Đổi thông tin (tiêu đề, tag) cũng tăng revision |
| ADR-006 | Phân quyền: OWNER toàn cục + thành viên dự án (EDITOR/VIEWER); dự án không được cấp trả **404** | Kiểm phía server trên từng đối tượng; không lộ sự tồn tại | VIEWER toàn cục không nâng được lên EDITOR nhờ thành viên dự án |
| ADR-007 | Phiên: cookie `HttpOnly; SameSite=Strict` (+`Secure` khi `--secure-cookies`), DB chỉ lưu sha256 token; CSRF token gửi qua header `X-CSRF-Token`; kiểm `Origin` và allowlist `Host` (chống DNS rebinding) | Chuẩn cho web cục bộ | MFA/SSO chưa có (ghi trung thực trong Module Registry) |
| ADR-008 | Agent đọc prompt bằng **Bearer token riêng** (băm trong DB), assignment = `agent_id + prompt_id + version_id` cụ thể, chỉ bản APPROVED, trigger chặn sửa assignment | Không lấy "latest" ngầm; danh tính lấy từ token, không từ tham số tự khai | Đổi phiên bản = thu hồi + cấp mới (có nhật ký) |
| ADR-009 | Tìm kiếm: chuỗi đã **gấp dấu** (NFD bỏ dấu, đ→d, casefold) đánh chỉ mục FTS5; ưu tiên tiêu đề chứa đúng chuỗi có dấu; tự lùi về LIKE nếu SQLite không có FTS5 | Gõ không dấu vẫn tìm được; Windows Python có thể khác bản SQLite | "phan" khớp cả "phân", "phần" (chấp nhận, xếp hạng bù) |
| ADR-010 | Nhập tệp: xem trước → chọn hành động từng tệp → ghi **tất cả hoặc không** | Không ghi đè âm thầm; dữ liệu sai bị từ chối có lý do | CRLF được chuẩn hóa thành LF (ghi rõ) |
| ADR-011 | Sao lưu: SQLite Online Backup API → `integrity_check` + `foreign_key_check` → zip (DB + metadata có băm, schema, số dòng). Khôi phục: kiểm trên bản tạm + migrate bản tạm → sao lưu dữ liệu hiện tại → thay nguyên tử; **từ chối khi máy chủ đang chạy** | Không copy tệp SQLite đang chạy | Khôi phục làm qua CLI, không qua web |
| ADR-012 | Migration chỉ tiến, mỗi migration một giao dịch, kiểm checksum tệp đã áp dụng, **tự sao lưu trước** nếu DB đã có dữ liệu | Rollback = khôi phục bản `pre-migration-*` | — |
| ADR-013 | AI Hub: `ai.enabled=false`, mọi ngân sách 0; khóa API chỉ là **tên biến môi trường** phía máy chủ; cổng chặn + giữ chỗ ngân sách trong cùng giao dịch ghi; không có transport gọi model thật | Đúng chỉ thị mục 08; không phát sinh chi phí | AI Writer/Optimizer/Test Lab model thật = PLANNED |
| ADR-014 | Code đặt ở package `crown_infinity/` trong repo `crown-agent` (thay vì `apps/`, `services/` như gợi ý) | Khớp quy ước repo (package Python cấp gốc: `crown/`, `tool/`); không tạo thư mục rỗng | Tách repo khi bắt đầu thương mại hóa |

## 3. Mô hình dữ liệu (schema 1 — `crown_infinity/migrations/0001_init.sql`)
`users`, `sessions`, `projects`, `project_members`, `modules`, `tools`, `tasks`, `activity_logs` (chỉ thêm, trigger chặn sửa/xóa), `settings`, `ai_providers`, `ai_usage`, `prompts`, `prompt_versions` (bất biến), `tags`, `prompt_tags`, `sources`, `prompt_sources`, `agent_identities`, `agent_prompt_assignments` (ghim), `search_docs` (+ bảng ảo `prompt_fts` tạo lúc chạy, dựng lại được), `schema_migrations`.
Không tạo `collections`/board: dự án đóng vai trò bộ sưu tập ở MVP. Không tạo `roles/permissions` dạng bảng: 3 vai trò cố định trong mã.

## 4. Bảo mật đã áp dụng
- Băm mật khẩu scrypt (n=2^14, r=8, p=1; dự phòng PBKDF2-SHA256 600k), tối thiểu 10 ký tự; so sánh hằng thời gian; giả lập băm khi sai tên để không lộ tên tồn tại.
- Giới hạn đăng nhập: 5 lần sai/15 phút cho mỗi (tên, IP), 30 lần/IP (bộ nhớ tiến trình — khởi động lại thì xóa).
- Thu hồi phiên: đăng xuất, đổi mật khẩu (thu hồi phiên khác), khóa tài khoản, OWNER thu hồi phiên người khác.
- Header: CSP chặt, `X-Frame-Options: DENY`, `nosniff`, `no-referrer`, `Cache-Control: no-store` cho API.
- Markdown xem trước dựng bằng nút DOM (`textContent`), không bao giờ chèn HTML từ prompt; link chỉ http(s) + `rel=noopener noreferrer nofollow`.
- Nhập tệp: chỉ lấy tên gốc (bỏ đường dẫn), chặn tên ẩn/ký tự điều khiển, chỉ .txt/.md/.markdown, ≤1 MB, UTF-8/UTF-16 BOM, chặn nhị phân; tên tệp không dùng để ghi đĩa.
- Tải xuống: `Content-Disposition` dùng slug ASCII + `filename*` mã hóa phần trăm.
- Không có chức năng tự truy cập URL (không có SSRF). URL nguồn chỉ là dữ liệu.
- Nhật ký không chứa nội dung prompt hay secret (có test).

## 5. Vận hành Windows (chưa kiểm trên Windows — NOT VERIFIED)
- Yêu cầu: Windows 10/11, Python ≥3.10 từ python.org (lệnh `py`). Không cần pip/Node.
- Chạy: `powershell -ExecutionPolicy Bypass -File scripts\infinity\start.ps1` hoặc `py -m crown_infinity serve`.
- Dừng: Ctrl+C trong cửa sổ máy chủ (sạch nhất) hoặc `scripts\infinity\stop.ps1`.
- Dữ liệu: `%LOCALAPPDATA%\CrownInfinity\` · nhật ký: `...\logs\server.log` · sao lưu: `...\backups\`.
- Gỡ: dừng máy chủ → sao lưu (`py -m crown_infinity backup`) → chép tệp zip ra nơi an toàn → xóa thư mục dữ liệu và thư mục mã.
- Truy cập từ iPhone/ngoài nhà: KHÔNG bật sẵn. Cần reverse proxy HTTPS (ví dụ Caddy, Cloudflare Tunnel, Tailscale) + `--allowed-host <tên>` + `--secure-cookies`; quyết định thuộc OWNER (mục 7).
- Ba việc tách biệt: *deploy code* (git pull), *cập nhật ứng dụng* (khởi động lại → migration tự sao lưu trước), *đồng bộ dữ liệu* (chưa có; không đặt tệp SQLite vào thư mục OneDrive/Google Drive).

## 6. Chi phí
| Cấu hình | Đã xác minh | Ước tính | UNKNOWN |
|---|---|---|---|
| Local-only (máy Windows của Chủ tịch) | Phần mềm 0 USD: Python, SQLite và mã này không thu phí; không gọi dịch vụ trả phí; AI ngân sách 0 | Điện năng và hao mòn máy khi để chạy (không phải 0) | Thời gian Claude Code để phát triển tiếp (tính vào gói Claude của Chủ tịch — mức sử dụng không đo trong phiên này) |
| Private web (truy cập từ xa riêng tư) | — | Có phương án miễn phí cho cá nhân (đường hầm/VPN) nhưng **chưa tra giá/điều khoản** | Giá, giới hạn gói miễn phí của từng nhà cung cấp — cần tra nguồn chính thức trước khi chọn |
| Commercial cloud | — | VPS + tên miền + sao lưu ngoài máy + PostgreSQL | Toàn bộ giá: chưa tra cứu, không ước lượng con số |
| Gọi model AI | Hiện 0 (bị chặn) | Phụ thuộc provider/model khi bật | Bảng giá provider — phải tra lúc bật |

## 7. Giới hạn đã biết (không phải lỗi mở)
- `http.server` đa luồng đủ cho 1–vài người dùng; không dùng làm máy chủ Internet trực tiếp.
- Giới hạn đăng nhập ở bộ nhớ tiến trình. MFA/SSO chưa có.
- Ước tính token = ký tự/4 (ghi rõ là ước tính). Tìm kiếm ngữ nghĩa chưa có.
- Diff phía client (màn xung đột) bỏ qua khi văn bản quá dài (>4 triệu ô so sánh) và báo rõ.
- Font monospace trong container Linux hiển thị dấu tiếng Việt kém; dữ liệu đúng (test so khớp từng ký tự). Trên Windows dùng Cascadia Mono/Consolas.
