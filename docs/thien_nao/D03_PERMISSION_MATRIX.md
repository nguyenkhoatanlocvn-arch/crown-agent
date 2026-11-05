# D03 — BẢNG QUYỀN (PERMISSION MATRIX) · Thiên Não Tối Thượng

> Tệp này do `python scripts/tn_docs.py` sinh ra từ `registries/`. Không sửa tay. Muốn đổi quyền: sửa `registries/permissions.json` trong pull request do Chủ tịch gộp, rồi chạy lại lệnh.

- Nguồn: permissions.json `2026-11-05.1` · thien_nao.json `2026-11-05.1` · vuong_toa.json `2026-11-05.2`
- Căn cứ: Lệnh DRAGON SINGULARITY V1.0 mục 02 (Hiến pháp quyền lực), mục 16 (Security Constitution); Chủ tịch 05/11/2026
- Trong tài liệu này "Vương Toạ" = Chủ tịch Nguyễn Khoa Tấn Lộc (mã `CHAIRMAN`), theo lệnh DRAGON SINGULARITY mục 02.

## 1. Ba loại hành động

| Loại | Nghĩa | Ai thi hành |
|---|---|---|
| Tự chủ | Được tự làm nếu có trong dòng quyền của mình | `crown/policy.py` — `decide()` trả ALLOW |
| Cần phê duyệt riêng | Mỗi việc phải có một dòng phê duyệt của Chủ tịch trong `registries/approvals.json` (đúng người, đúng hành động, còn hạn) | `decide()` trả NEEDS_APPROVAL cho tới khi có dòng hợp lệ |
| Chỉ Chủ tịch | Agent chỉ được đề xuất; không ủy quyền được | `decide()` luôn trả DENY với mọi Agent |

## 2. Danh mục hành động

| Hành động | Loại | Mô tả |
|---|---|---|
| `TASK_MANAGE` | Tự chủ | Tạo, giao và sắp xếp nhiệm vụ |
| `RESOURCE_ALLOCATE` | Tự chủ | Phân bổ tài nguyên và mô hình trong ngân sách đã duyệt |
| `AGENT_LAUNCH` | Tự chủ | Khởi chạy Agent đã được cấp quyền |
| `SCHEDULE_MANAGE` | Tự chủ | Xây dựng và thay đổi lịch công việc |
| `REPORT_READ` | Tự chủ | Đọc báo cáo, trạng thái, nhật ký |
| `TEST_RUN` | Tự chủ | Chạy kiểm thử |
| `WORKFLOW_COORDINATE` | Tự chủ | Điều phối quy trình giữa các Agent |
| `CODE_BRANCH_WRITE` | Tự chủ | Tạo nhánh mã nguồn, sửa lỗi trong môi trường thử nghiệm, mở pull request |
| `RESEARCH` | Tự chủ | Nghiên cứu và đề xuất nâng cấp |
| `STATE_WRITE` | Tự chủ | Ghi trạng thái chu trình vào nhánh tn-state |
| `DEPLOY_LOW_RISK` | Tự chủ | Triển khai bản sửa nhỏ, ít rủi ro trong giới hạn đã phê duyệt |
| `EMERGENCY_STOP_SET` | Tự chủ | Bật dừng khẩn cấp trong phạm vi của mình |
| `SPEND` | Cần phê duyệt riêng | Chi tiền, mua dịch vụ, thay đổi hạn mức |
| `REAL_MONEY_TRADE` | Cần phê duyệt riêng | Giao dịch tài chính bằng tiền thật |
| `SECRET_ACCESS` | Cần phê duyệt riêng | Đọc hoặc tiết lộ bí mật ngoài phạm vi cần thiết |
| `DELETE_CRITICAL_DATA` | Cần phê duyệt riêng | Xóa dữ liệu quan trọng |
| `DEPLOY_HIGH_RISK` | Cần phê duyệt riêng | Triển khai thay đổi rủi ro cao lên production (xác thực, tài chính, dữ liệu, hạ tầng) |
| `ROOT_ADMIN_CHANGE` | Chỉ Chủ tịch | Thay đổi quyền quản trị gốc |
| `SECURITY_RULE_CHANGE` | Chỉ Chủ tịch | Thay đổi quy tắc bảo mật và cơ chế dừng khẩn cấp |
| `CONSTITUTION_CHANGE` | Chỉ Chủ tịch | Sửa hiến pháp, bảng quyền, sổ lãnh đạo |
| `PERMISSION_GRANT` | Chỉ Chủ tịch | Cấp thêm quyền cho bất kỳ Agent nào |
| `EMERGENCY_STOP_CLEAR` | Chỉ Chủ tịch | Gỡ dừng khẩn cấp |

## 3. Quyền tự chủ của từng thành viên

Với mọi Agent (kể cả Thiên Não): 5 hành động "Cần phê duyệt riêng" đều ở trạng thái chờ phê duyệt, 5 hành động "Chỉ Chủ tịch" đều bị từ chối. Bảng dưới chỉ liệt kê phần tự chủ.

| Thành viên | Vai | Mức quyền | Được tự chủ |
|---|---|---|---|
| `CHAIRMAN` | Vương Toạ — Chủ tịch | — | **Toàn quyền** |
| `THIEN_NAO` | Thiên Não Tối Thượng | OPERATIONAL_WRITE | TASK_MANAGE, RESOURCE_ALLOCATE, AGENT_LAUNCH, SCHEDULE_MANAGE, REPORT_READ, TEST_RUN, WORKFLOW_COORDINATE, CODE_BRANCH_WRITE, RESEARCH, STATE_WRITE, DEPLOY_LOW_RISK, EMERGENCY_STOP_SET |
| `VUA_CEO` | Agent Vua | OPERATIONAL_WRITE | TASK_MANAGE, AGENT_LAUNCH, REPORT_READ, TEST_RUN, WORKFLOW_COORDINATE, CODE_BRANCH_WRITE, RESEARCH, EMERGENCY_STOP_SET |
| `CFO` | EX-CFO | OPERATIONAL_WRITE | RESOURCE_ALLOCATE, REPORT_READ, RESEARCH, EMERGENCY_STOP_SET |
| `CMO-01` | EX-CMO-01 | OPERATIONAL_WRITE | TASK_MANAGE, REPORT_READ, RESEARCH, EMERGENCY_STOP_SET |
| `CMO-02` | EX-CMO-02 | OPERATIONAL_WRITE | TASK_MANAGE, REPORT_READ, RESEARCH, EMERGENCY_STOP_SET |
| `TN-ENGINE` | Bộ máy chu trình Thiên Não | SANDBOX_WRITE | TASK_MANAGE, REPORT_READ, TEST_RUN, WORKFLOW_COORDINATE, STATE_WRITE |
| `A03-NB` | A03-NB | SANDBOX_WRITE | TASK_MANAGE, REPORT_READ, TEST_RUN, WORKFLOW_COORDINATE, RESEARCH |
| `A21-AT` | CROWN-4 AT | SANDBOX_WRITE | TASK_MANAGE, REPORT_READ, TEST_RUN, WORKFLOW_COORDINATE, RESEARCH |
| `A01` | Royal Brain | SANDBOX_WRITE | REPORT_READ, TEST_RUN, CODE_BRANCH_WRITE, RESEARCH |
| `A02` | Distributed Brain | SANDBOX_WRITE | REPORT_READ, TEST_RUN, CODE_BRANCH_WRITE, RESEARCH |
| `A03` | Skill Intelligence | SANDBOX_WRITE | REPORT_READ, TEST_RUN, CODE_BRANCH_WRITE, RESEARCH |
| `A04` | Tool & App Observatory | READ | REPORT_READ, RESEARCH |
| `A05` | Identity & Account Ops | READ | REPORT_READ, RESEARCH |
| `A06` | Recovery & Daily Journal | SANDBOX_WRITE | REPORT_READ, TEST_RUN, CODE_BRANCH_WRITE, RESEARCH |
| `A07` | Device & Release Maintenance | READ | REPORT_READ, RESEARCH |
| `A08` | Treasury & Wallet Registry | READ | REPORT_READ, RESEARCH |
| `A09` | Corporate & Personal Finance | READ | REPORT_READ, RESEARCH |
| `A10` | Orchestration & Automation | SANDBOX_WRITE | TASK_MANAGE, REPORT_READ, TEST_RUN, WORKFLOW_COORDINATE, CODE_BRANCH_WRITE, RESEARCH |
| `A11` | Reliability & Bug Repair | SANDBOX_WRITE | REPORT_READ, TEST_RUN, CODE_BRANCH_WRITE, RESEARCH |
| `A12` | Token & Cost Intelligence | READ | REPORT_READ, RESEARCH |
| `A13` | Venture Studio | READ | REPORT_READ, RESEARCH |
| `A14` | Prompt Studio & Vault | SANDBOX_WRITE | REPORT_READ, TEST_RUN, CODE_BRANCH_WRITE, RESEARCH |
| `A15` | Workspace & Document Ops | READ | REPORT_READ, RESEARCH |
| `A16` | Integration Gateway | READ | REPORT_READ, RESEARCH |
| `A17` | Security, Privacy & Access | READ | REPORT_READ, TEST_RUN, RESEARCH, EMERGENCY_STOP_SET |
| `A18` | Engineering & Product Factory | SANDBOX_WRITE | REPORT_READ, TEST_RUN, CODE_BRANCH_WRITE, RESEARCH |
| `A19` | Market & Business Intelligence | READ | REPORT_READ, RESEARCH |
| `A20` | Independent Assurance | READ | REPORT_READ, TEST_RUN, RESEARCH |
| `A03.1` | Skill Scout | SANDBOX_WRITE | REPORT_READ, TEST_RUN, CODE_BRANCH_WRITE, RESEARCH |
| `A03.2` | Skill Builder | SANDBOX_WRITE | REPORT_READ, TEST_RUN, CODE_BRANCH_WRITE, RESEARCH |
| `A03.3` | Skill Evaluator & Release | SANDBOX_WRITE | REPORT_READ, TEST_RUN, CODE_BRANCH_WRITE, RESEARCH |
| `TN-07` | Model Router | READ | REPORT_READ, RESEARCH |
| `TN-12` | Evolution Scientist | SANDBOX_WRITE | REPORT_READ, TEST_RUN, CODE_BRANCH_WRITE, RESEARCH |

Kiểm tự động khi sinh tài liệu: số Agent đang được phép làm việc nhạy cảm hoặc việc chỉ-Chủ-tịch mà không có phê duyệt = **0**.

## 4. Ai được đặt mục tiêu tại Vương Toạ (giao việc cho Agent Vua)

| Người giao | Phạm vi được giao | Được dừng | Được duyệt thay Chủ tịch |
|---|---|---|---|
| `CHAIRMAN` | * | có | có |
| `THIEN_NAO` | SKILL, DOCS, WEB, CODE | có | không |
| `VUA_CEO` | SKILL, DOCS | có | không |

Phạm vi: **SKILL** — Thẩm định, viết, kiểm skill (chỉ văn bản, không chạy script ngoài) · **DOCS** — Tài liệu nội bộ, báo cáo, kế hoạch · **WEB** — Giao diện Hoàng thành (không đổi quyền truy cập) · **CODE** — Mã CROWN trên nhánh/worktree riêng · **MONEY** — Bất cứ gì chạm tiền thật, bot BTC, ví, mua dịch vụ.
Phạm vi chỉ Chủ tịch giao: MONEY.

## 5. Mười hai ghế Thiên Não

| Ghế | Vai | Người giữ | Bậc mô hình | Suy luận | Công cụ | Trần mỗi việc / ngày |
|---|---|---|---|---|---|---|
| `TN-01` | Chief Strategist — Chiến lược | A01 · ACTIVE | ADVANCED | high | ledger, repo_read, project_docs_read, web_research | 150,000 token · 25 phút · 2 lượt AI/ngày · 0 USD/ngày |
| `TN-02` | Task Commander — Giao việc | A10 · ACTIVE | BALANCED | medium | ledger, repo_read, project_docs_read | 80,000 token · 20 phút · 4 lượt AI/ngày · 0 USD/ngày |
| `TN-03` | Skill Evolution — Nghiên cứu skill | A03 · ACTIVE | BALANCED | medium | ledger, repo_read, web_research, project_docs_read, project_docs_write:claude/thien_nao/ | 80,000 token · 20 phút · 4 lượt AI/ngày · 0 USD/ngày |
| `TN-04` | Software Architect — Kiến trúc | A18 · ACTIVE | ADVANCED | high | ledger, repo_read, project_docs_read, web_research | 150,000 token · 25 phút · 2 lượt AI/ngày · 0 USD/ngày |
| `TN-05` | Web Developer — Cập nhật web | A18 · ACTIVE | CODING | medium | ledger, repo_read, repo_branch_write, run_tests | 120,000 token · 25 phút · 3 lượt AI/ngày · 0 USD/ngày |
| `TN-06` | Security Guardian — Bảo mật | A17 · ACTIVE | ADVANCED | high | ledger, repo_read, project_docs_read | 150,000 token · 25 phút · 2 lượt AI/ngày · 0 USD/ngày |
| `TN-07` | Model Router — Chọn mô hình | (ghế mới) · ACTIVE | RULE | none | ledger, repo_read | 0 token · 5 phút · 0 lượt AI/ngày · 0 USD/ngày |
| `TN-08` | Research Intelligence — Nghiên cứu | A04 · ACTIVE | BALANCED | medium | ledger, repo_read, web_research, project_docs_read, project_docs_write:claude/thien_nao/ | 80,000 token · 20 phút · 4 lượt AI/ngày · 0 USD/ngày |
| `TN-09` | Memory Architect — Bộ nhớ | A02 · ACTIVE | BALANCED | medium | ledger, repo_read, project_docs_read, project_docs_write:claude/thien_nao/ | 80,000 token · 20 phút · 4 lượt AI/ngày · 0 USD/ngày |
| `TN-10` | Quality Inspector — Kiểm thử | A20 · ACTIVE | BALANCED | high | ledger, repo_read, run_tests | 80,000 token · 20 phút · 6 lượt AI/ngày · 0 USD/ngày |
| `TN-11` | FinOps Controller — Kiểm soát chi phí | A12 · ACTIVE | FAST | low | ledger, repo_read, scheduled_task_read | 30,000 token · 10 phút · 4 lượt AI/ngày · 0 USD/ngày |
| `TN-12` | Evolution Scientist — Thử nghiệm cải tiến | (ghế mới) · ACTIVE | ADVANCED | high | ledger, repo_read, repo_branch_write, run_tests | 150,000 token · 25 phút · 2 lượt AI/ngày · 0 USD/ngày |

Trần token là giới hạn mềm ghi trong chỉ thị của ghế. Giới hạn cứng do bộ máy thi hành là số lượt theo bậc mô hình mỗi ngày, số phiên AI mỗi ngày, số phút mỗi phiên và ngân sách chi (hiện bằng 0).

## 6. Đơn vị dưới Thiên Não

| Đơn vị | Loại | Không qua | Giới hạn cứng |
|---|---|---|---|
| `TN-ENGINE` | rule_engine | — | Chỉ ghi nhánh trạng thái tn-state; Không gọi AI, không gọi mạng ngoài git; Không sửa mã, không gộp nhánh |
| `A03-NB` | autonomous_agent | VUA_CEO | Skill chạm tiền chỉ tới bước chờ Chủ tịch; Không chạy script repo ngoài; Không tạo hay sửa tác vụ định kỳ; Không sửa bot, BOARD, RISK_POLICY |
| `A21-AT` | autonomous_agent | VUA_CEO | Không tạo tài khoản, không đăng nhập; Không gửi gì ra ngoài cho khách; Không mua, nạp, rút, chuyển tiền; Không tạo hay sửa tác vụ định kỳ |

## 7. Ngân sách và lịch (Chủ tịch chỉnh trong `registries/thien_nao.json`)

- Chi tiền: 0 USD/ngày · 0 USD/tháng (mặc định chưa kích hoạt khoản chi mới).
- Phiên AI: tối đa 4 phiên/ngày, mỗi phiên tối đa 3 việc.
- Lượt giao việc theo bậc mô hình mỗi ngày: FAST 12 · BALANCED 8 · CODING 4 · ADVANCED 3.
- Làn luật: phút 17 mỗi 2 giờ, múi giờ Asia/Ho_Chi_Minh; mỗi phiên xử lý tối đa 5 việc.
- Làn AI: 00:47, 06:47, 12:47, 18:47.

## 8. Tệp được bảo vệ

Không Agent nào được sửa các tệp sau; `registries/CONSTITUTION.lock` giữ mã băm của chúng và mỗi phiên, mỗi pull request đều kiểm lại:

- `registries/permissions.json`
- `registries/approvals.json`
- `registries/leaders.json`
- `registries/thien_nao.json`
- `registries/vuong_toa.json`
- `registries/models.json`
- `registries/CONSTITUTION.lock`
- `crown/policy.py`
- `.github/workflows/`

## 9. Dừng khẩn cấp

- Bật: Chủ tịch và các thành viên có `EMERGENCY_STOP_SET`. Cách nhanh nhất cho Chủ tịch: tạo tệp `state/STOP` trên nhánh `tn-state`.
- Gỡ: chỉ Chủ tịch (`EMERGENCY_STOP_CLEAR`); với tệp `state/STOP` là xóa tệp. Bộ máy không bao giờ tự gỡ.
- Trong lúc dừng, Agent chỉ còn được: REPORT_READ, EMERGENCY_STOP_SET.

## 10. Điều mã thi hành được và điều phải dựa vào cài đặt GitHub

| Luật | Thi hành bằng | Mức chắc chắn |
|---|---|---|
| Agent không tự cấp quyền trong bảng quyền | `Policy.validate()` từ chối bảng có `*` hoặc cấp sẵn việc nhạy cảm; test `tests/test_policy.py` | Chắc trong phạm vi mã |
| Tệp quyền bị đổi ngoài quy trình | Khóa hiến pháp + CI + kiểm sức khỏe mỗi phiên | Phát hiện được, không ngăn được việc ghi |
| Không ai ngoài Chủ tịch gộp thay đổi vào nhánh chính | Quy tắc bảo vệ nhánh của GitHub (Settings → Rules) | Ngoài tầm của mã; chưa kiểm được đã bật hay chưa — việc của Chủ tịch |
| Workflow chỉ ghi nhánh `tn-state` | `GitState.push()` chỉ nhận đường dẫn `state/` và một nhánh | Chắc với mã này; token của workflow vẫn có quyền ghi cả repo nếu nhánh chính chưa được bảo vệ |
| Gỡ dừng khẩn cấp chỉ Chủ tịch | Bảng quyền (công tắc trong database); tệp `state/STOP` thì ai có quyền ghi repo cũng xóa được | Dựa vào quyền ghi repo |
