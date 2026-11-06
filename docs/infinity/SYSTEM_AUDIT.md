# CROWN INFINITY — Kiểm kê hệ thống (2026-11-06)

Người lập: Claude (phiên Claude Code trên cloud, repo `nguyenkhoatanlocvn-arch/crown-agent`).
Phương pháp: đọc tệp trong repo, chạy lệnh trong container Linux. Nhãn: **VERIFIED** (đã đọc/chạy được), **REPORTED** (tài liệu trong repo ghi, chưa tự kiểm), **UNKNOWN** (không tìm thấy).

## Môi trường thực tế của phiên này
| Mục | Giá trị | Nhãn |
|---|---|---|
| Thư mục | `/home/user/crown-agent` (container Linux cloud, mất sau phiên) | VERIFIED |
| Git | nhánh `master` sạch, commit `f251eee`; làm việc trên nhánh `feature/crown-infinity-prompt-studio` | VERIFIED |
| Runtime | Python 3.13.18, SQLite 3.53.1 (có FTS5), Node 22.23.4, Playwright 1.63 + Chromium | VERIFIED |
| Không có | FastAPI, PowerShell, máy Windows, laptop của Chủ tịch (chưa gắn thư mục) | VERIFIED |
| Mạng | Đi qua proxy; PyPI và npm registry truy cập được | VERIFIED |
| Test sẵn có | 198 test; lần chạy đầu 1 lỗi `test_parallel_processes_open_fresh_db` ("database is locked"), các lần sau đạt → test chập chờn có sẵn | VERIFIED |

## Thành phần đã tìm thấy
| Thành phần | Đường dẫn | Stack | API / dữ liệu | Trạng thái | Tái sử dụng cho INFINITY |
|---|---|---|---|---|---|
| CROWN AGENT — sổ đăng ký tổ chức | `registries/*.json`, `crown/registry.py` | Python stdlib | JSON trong repo: 5 lãnh đạo, 20 Agent (A01–A20), 2 Agent TOOL | VERIFIED (đọc được) | **Có** — adapter chỉ đọc, đồng bộ danh tính Agent |
| CROWN AGENT — sổ nhiệm vụ | `crown/tasks.py`, DB `runtime/tasks.db` (biến `CROWN_DB`) | SQLite | Bảng `tasks`, `events` (chỉ thêm) | Mã VERIFIED; **tệp DB không có trong máy này** (bị `.gitignore`) | Adapter đọc chế độ `mode=ro` khi có tệp; hiện OFFLINE |
| Agent Vua / Vương Tọa | `crown/vua.py`, `.claude/workflows/agent-vua-vuong-toa.js`, `docs/AGENT_VUA_WORKFLOW.md` | Python + workflow JS | Lớp sổ trên `tasks.db` | VERIFIED (mã) | Chưa nối — cần endpoint/quyền riêng (PLANNED) |
| Agent TOOL / TOOL AT (Thiên Công) | `tool/`, `registries/dept_tool.json` | Python stdlib | `tool/catalog/<id>/tool.json` + phiên bản bất biến | VERIFIED; 1 tool phát hành: `srt-renumber` 1.0.0 (GOOD), 1.0.1 (WITHDRAWN) | **Có** — Tool Registry đọc kho này (chỉ đọc) |
| HOÀNG THÀNH RỒNG | `office/web/index.html`, `office/web/tool.js`, `office/office.html` | HTML/JS artifact claude.ai | Đọc DB của artifact (`window.claude.use("db")`, collection `office`) | Mã VERIFIED; trang trực tiếp là artifact riêng, **không truy cập được từ phiên này** | Không nhúng; tái dùng bảng màu. Đồng bộ sang Hoàng Thành = PLANNED |
| CEO/CFO/CMO | `registries/leaders.json` | JSON | — | VERIFIED (đăng ký) | Đồng bộ làm danh tính Agent |
| A03 (Skill Intelligence) | `skills/A03_NB_CHARTER.md` | Markdown | — | REPORTED: ACTIVE | Không dùng trong lát này |
| A14 Prompt Studio & Vault | `registries/agents.json` | JSON | write_scope `agents/A14/`, `prompts/` | REGISTERED (chưa có mã) | CROWN PROMPT STUDIO là phần mềm cho vai trò này |
| A18 Xưởng phần mềm | `registries/agents.json` | JSON | — | ACTIVE | Vai trò xây INFINITY |
| CROWN TOOL DESK | — | — | — | **UNKNOWN** (không có mã) | Lập module mới, nguồn = `tool/catalog` |
| CROWN SOFTWARE FORGE | — | — | — | **UNKNOWN** (không có mã) | PLANNED |
| CROWN INFINITY / PROMPT STUDIO | — | — | — | Chưa tồn tại trước phiên này | Xây mới trong `crown_infinity/` |
| Bot BTC | `b02_btc/` (trade_plan, build_state — chỉ đọc) | Python | `runtime/b02_*.json` | VERIFIED (mã); bot tiền thật ở repo riêng `BTCDesk` (REPORTED) | Không đụng; BTC Control Center = PLANNED, chỉ đọc |

## Mâu thuẫn / điều chưa rõ
- `docs/GIT_SYNC.md` ghi "Repo private", `tool/DESIGN.md` (T-08) ghi "repo đang công khai" → **UNKNOWN**. Vì vậy không commit dữ liệu thật, mật khẩu hay khóa; dữ liệu INFINITY nằm ngoài repo.
- Tác vụ định kỳ Thiên Công AT commit/push lên `master` mỗi giờ (thấy trong `git log`) → làm việc trên nhánh riêng để không va chạm.

## Giả định có thể đảo ngược
1. INFINITY là package mới `crown_infinity/` trong repo `crown-agent` (không tách repo) — đổi được bằng cách chuyển thư mục.
2. Dữ liệu chạy ở `%LOCALAPPDATA%\CrownInfinity` (Windows) / `~/.local/share/crown-infinity` (Linux), đổi bằng `--data-dir` hoặc `CROWN_INFINITY_DATA`.
3. Không sửa `registries/` hay `crown/` của CROWN AGENT: chỉ đọc.
