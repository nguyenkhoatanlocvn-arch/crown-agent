# Workflow AGENT VUA – VƯƠNG TỌA (`agent-vua-vuong-toa`)

Vương Tọa giao một mục tiêu → nhận mã nhiệm vụ → theo dõi tới khi Agent Vua trả kết quả kèm bằng chứng.

## Tệp
| Tệp | Vai trò |
|---|---|
| `.claude/workflows/agent-vua-vuong-toa.js` | Workflow Claude Code (meta.name `agent-vua-vuong-toa`). JS quyết định thứ tự, rẽ nhánh, điểm dừng |
| `crown/vua.py` | Lớp sổ: nhận lệnh theo `request_id`, quyền Vương Tọa, kế hoạch, điểm kiểm, khóa lượt chạy, hạn mức Hội đồng, hộp thư đồng bộ có đối soát, tra cứu, cửa phòng |
| `registries/vuong_toa.json` | Ai được giao việc tại Vương Tọa, phạm vi nào. Chỉ Chủ tịch sửa |
| `tests/test_vua.py` | 17 test đơn vị lớp sổ |
| `tests/workflow/harness.mjs`, `run_scenarios.mjs` | Chạy thử workflow cục bộ: lệnh sổ chạy thật trên SQLite, phần việc dùng fixture (39 kiểm) |
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
