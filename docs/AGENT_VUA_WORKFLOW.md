# Workflow AGENT VUA – VƯƠNG TỌA (`agent-vua-vuong-toa`)

Vương Tọa giao một mục tiêu → nhận mã nhiệm vụ → theo dõi tới khi Agent Vua trả kết quả kèm bằng chứng.

## Tệp
| Tệp | Vai trò |
|---|---|
| `.claude/workflows/agent-vua-vuong-toa.js` | Workflow Claude Code (meta.name `agent-vua-vuong-toa`). JS quyết định thứ tự, rẽ nhánh, điểm dừng |
| `crown/vua.py` | Lớp sổ: nhận lệnh theo `request_id`, quyền Vương Tọa, kế hoạch, điểm kiểm, khóa lượt chạy, hạn mức Hội đồng, hộp thư đồng bộ có đối soát, tra cứu, cửa phòng |
| `registries/vuong_toa.json` | Ai được giao việc tại Vương Tọa, phạm vi nào. Chỉ Chủ tịch sửa |
| `tests/test_vua.py` | 17 test đơn vị lớp sổ |
| `tests/workflow/harness.mjs`, `run_scenarios.mjs` | Chạy thử workflow cục bộ: lệnh sổ chạy thật trên SQLite, phần việc dùng fixture (43 kiểm) |
| `tests/workflow/live_step.mjs` | Chạy thật từng bước: dừng ở mỗi vai cần Agent, Agent Vua giao đúng prompt cho Agent thật |

## Cách gọi (Claude Code trên laptop, trong thư mục repo `crown-agent`)
```
/agent-vua-vuong-toa {"request_id":"VT-20261106-001","objective":"...","scope":{"domain":"SKILL"},"acceptance":["...","..."],"python":"py"}
```
Hoặc nhờ Claude: *"Chạy workflow agent-vua-vuong-toa với args {...}"*. Trên Windows đặt `"python":"py"`.

Tham số chính: `request_id` (bắt buộc, dùng lại = trả nhiệm vụ cũ), `objective`, `scope.domain` (SKILL/DOCS/WEB/CODE/MONEY), `acceptance[]`, `issued_by` (mặc định CHAIRMAN), `mode` (LIVE/TEST), `plan[]` (tùy chọn; không có thì Agent Vua tự lập), `approvals{key: "mã quyết định"}`, `takeover` (nhận lại sau khi lượt trước chết), `action` = `lookup` | `stop`.

Tra theo mã (kể cả khi phòng đã ngủ đông):
```
/agent-vua-vuong-toa {"action":"lookup","code":"T-xxxxxxxxxx"}
python -m crown vua lookup --q '%7B%22code%22%3A%22T-xxxxxxxxxx%22%7D'
```

## Luồng
1. **Nhận lệnh** — kiểm quyền theo `vuong_toa.json`; ghi sổ (nhiệm vụ gốc chủ VUA_CEO, idempotency = `request_id`) trước khi giao; giữ khóa lượt chạy.
2. **Lập kế hoạch** — 1 việc → giao thẳng một Rồng; nhiều việc → mỗi việc một owner ACTIVE, đầu ra, phụ thuộc, tiêu chí, vùng ghi. Ghi điểm kiểm `plan`.
3. **Thực hiện** — theo sóng phụ thuộc; chỉ song song việc `parallel_ok` có vùng ghi không chồng. Việc `needs_approval` dừng ở WAITING_APPROVAL tới khi có quyết định hợp lệ của Chủ tịch. Việc `needs_council` qua cổng hạn mức CFO; thiếu kết nối hoặc hết lượt → ghi lý do, làm phần cục bộ.
4. **Kiểm bằng chứng** — A20 (không phải người làm) kiểm từng mục `text/passed/evidence`; trượt → làm lại tối đa `max_rework` lần (mặc định 1), sổ chặn quá 3 lần thử.
5. **Đồng bộ Hoàng thành** — qua `outbox`: PENDING → SENDING → CONFIRMED (bắt buộc biên nhận đọc lại) / BLOCKED (thiếu quyền) / UNKNOWN. SENDING/UNKNOWN khi chạy lại → **đối soát trước khi gửi lại**.
6. **Nghiệm thu** — mọi việc con VERIFIED_DONE → A20 nghiệm thu gốc. Còn việc chờ → gốc BLOCKED, chạy lại cùng `request_id` để tiếp tục.

## Cửa phòng (đọc từ sổ)
| Trạng thái sổ | Hoạt động | Cửa |
|---|---|---|
| DRAFT/READY chưa ai nhận | WAITING | cuộn xanh |
| RUNNING, lease còn hạn | WORKING | đèn cam |
| REVIEW, WAITING_APPROVAL | REVIEW | lồng đèn vàng |
| BLOCKED, PAUSED_QUOTA, INTERRUPTED | BLOCKED | xích đỏ |
| Agent chưa kích hoạt | OFF | phòng tối |

Thay đổi so với trước: WAITING_APPROVAL chuyển từ xích đỏ sang lồng đèn vàng (theo yêu cầu "lồng đèn vàng khi chờ duyệt"), sửa cả `scripts/export_office.py`.

## Hạn mức Hội đồng
Đọc `runtime/council_policy.json` nếu có; không có thì dùng mặc định trùng `council.js`: 12 lượt/phiên (trần cứng), 40 lượt/ngày, chừa 1 lượt kết luận, trả phí 0 USD. Lượt thử lại cũng tính. TEST và LIVE đếm riêng.

## Tách dữ liệu kiểm thử
`mode: TEST` → không được đẩy lên Hoàng thành thật (outbox chỉ nhận target `local`), nên dùng DB riêng qua `db`/`CROWN_DB`. Báo cáo workflow ghi nhãn `DỮ LIỆU KIỂM THỬ`.

## Nguồn (chỉ lấy ý, viết lại)
- obra/superpowers @22f0b463e7a5e579904dd6211a520321035526f7 (MIT): writing-plans, verification-before-completion
- affaan-m/everything-claude-code @b38be6a7f6350964d59328b52f0624e1d1d552d1 (MIT): skills/team-agent-orchestration
- anthropics/skills @ca45fc496a3436251f191354091d557da31c15b5 (Apache-2.0): skills/skill-creator

## Giới hạn đã biết
- Workflow trong Claude Code không có công cụ ghi database artifact (ArtifactData) — bước đồng bộ sẽ BLOCKED "thiếu quyền đồng bộ" khi chạy trên laptop; phiên claude.ai (Vương Tọa) có quyền này và hoàn tất hộp thư.
- Sổ thật `runtime/tasks.db` không nằm trong git. Bản sao lưu ở laptop: `Downloads\BTCDesk_Windows\CROWN_AGENT_backup_20261105\tasks.db`.
- Khóa lượt chạy hết hạn sau `lock_ttl` (mặc định 900 giây) nếu lượt trước chết; muốn tiếp ngay dùng `takeover: true`.

## Lần chạy thật đầu tiên — VT-20261105-001 (05/11/2026, 23:06–23:24 giờ VN)
Bằng chứng đầy đủ: `docs/evidence/VT-20261105-001/` (args, câu trả lời từng vai, nhật ký từng lượt, bản xuất sổ).

| Mã | Việc | Người làm | Người kiểm | Kết quả |
|---|---|---|---|---|
| T-5a0bacbbcd | Gốc (VUA_CEO) | Agent Vua | A20 nghiệm thu 6/6 tiêu chí | VERIFIED_DONE |
| T-67d2f9bdeb | S1 thẩm định skill | A03 (Agent thật) | A20 (clone riêng) 7/7 | VERIFIED_DONE, thử lại 0 |
| T-f9045edfb9 | S2 đồng bộ tab SKILL | A03 qua phiên Vương Tọa (có ArtifactData) | A20: lần 1 FAIL, lần 2 PASS | VERIFIED_DONE, thử lại 1 |

Cách chạy: workflow được chạy bằng `tests/workflow/live_step.mjs` — đúng mã JS của workflow, lệnh sổ chạy thật, mỗi vai Agent dừng lại để Agent Vua giao đúng prompt cho subagent thật (A03, A20). Lý do: lệnh tự chạy Claude Code không giám sát (`claude -p` với quyền rộng) bị bộ lọc an toàn của phiên chặn.

Quan sát thật trong lượt chạy:
- Workflow chạy 9 lượt cho cùng request_id (dừng chờ Agent hoặc dừng vì lỗi rồi chạy lại); mỗi lượt sau tiếp tục từ điểm kiểm, không làm lại S1, không tính thử lại.
- Đồng bộ dừng ở SENDING rồi chạy lại → workflow đối soát trước, thấy đã ghi, không gửi lại (outbox attempts = 1).
- A20 đánh trượt biên nhận lần 1 vì biên nhận ghi "pipeline 37 mục" (thực tế 36, không mất mục) và mốc thời gian thiếu phút. Workflow mở lần đồng bộ mới `S2#1`, A20 kiểm lại PASS.
- Lỗi workflow phát hiện khi chạy thật và đã sửa: (1) góp ý người kiểm nằm trong payload nên lượt tiếp tục tính payload khác → outbox từ chối; đã chuyển góp ý sang điểm kiểm riêng (thêm phép kiểm 4c). Dòng outbox `S2#1` trong sổ cloud được sửa tay một lần, có điểm kiểm `manual_fix:outbox:S2#1` ghi hash cũ/mới. (2) Clone nông không push được (GitHub `commit_refs_rules`) → fetch sâu rồi push.
- Lỗi có sẵn được sửa: `TaskStore` chết "duplicate column" khi nhiều tiến trình mở DB mới cùng lúc (test đỏ trước, xanh sau).

Đọc lại trên Hoàng thành: `office/skill_state` version 27 đọc ở quyền view (đúng quyền người xem trang) có `vua_vetting["VT-20261105-001_S2"]`; trang đã thêm thẻ "Thẩm định cho Agent Vua · Vương Tọa" ở tab SKILL (phiên bản trang 12). Thẻ hiển thị trên màn hình chưa được nhìn tận mắt: trình duyệt trong app của laptop chưa đăng nhập claude.ai.
