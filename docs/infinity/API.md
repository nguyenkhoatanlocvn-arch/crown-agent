# CROWN INFINITY — Hợp đồng API v1

Gốc: `/api/v1`. JSON UTF-8. Lỗi luôn có dạng `{"error": {"code", "message", "details?"}}`.
Mã thường gặp: 400 `VALIDATION_ERROR`/`IMPORT_*`/`RENDER_INCOMPLETE` · 401 `UNAUTHENTICATED` · 403 `FORBIDDEN`/`CSRF_FAILED`/`NOT_ASSIGNED` · 404 `NOT_FOUND` (kể cả khi không có quyền với dự án) · 409 `REVISION_CONFLICT`/`NO_CHANGE`/`NOT_APPROVED`/`IN_USE`/`IN_PRODUCTION`/`PRODUCTION_CONFLICT`/`MODULE_DISABLED`/`NOT_IMPLEMENTED` · 421 `BAD_HOST` · 428 `PRECONDITION_REQUIRED` · 429 `TOO_MANY_ATTEMPTS`.

**Xác thực người dùng**: cookie `ci_session` (từ `POST /auth/login`). Mọi POST/PUT/PATCH/DELETE phải có header `X-CSRF-Token` (lấy từ `/auth/login` hoặc `/auth/me`).
**Xác thực Agent**: `Authorization: Bearer cia_…` (OWNER cấp ở `POST /agents/{agent_id}/token`, hiển thị một lần).
**Optimistic concurrency**: các thao tác ghi lên prompt/task cần `If-Match: "<revision>"`; phản hồi chi tiết prompt có `ETag`.

## Công khai
| Method | Đường dẫn | Ghi chú |
|---|---|---|
| GET | `/health` | `{ok, app, version, schema_version}` |
| POST | `/auth/login` | `{username, password}` → cookie + `{user, csrf_token}` |

## Phiên & quản trị (cần đăng nhập)
| Method | Đường dẫn | Quyền |
|---|---|---|
| GET | `/auth/me` | mọi người |
| POST | `/auth/logout` · `/auth/password` `{current_password,new_password}` | mọi người |
| GET/POST | `/admin/users` | OWNER |
| PATCH | `/admin/users/{uid}` `{is_active?, role?, password?, display_name?}` | OWNER (thu hồi phiên khi đổi mật khẩu/khóa/đổi vai trò) |
| POST | `/admin/users/{uid}/revoke-sessions` | OWNER hoặc chính mình |
| GET/POST | `/admin/backups` | OWNER (tạo bản sao lưu nhất quán; khôi phục chỉ qua CLI) |

## Nền tảng
| Method | Đường dẫn | Quyền / ghi chú |
|---|---|---|
| GET | `/dashboard` | số liệu thật trong phạm vi dự án được xem |
| GET/POST | `/projects` · PATCH `/projects/{pid}` | đọc: thành viên; ghi: OWNER |
| GET | `/projects/{pid}/members` · PUT/DELETE `/projects/{pid}/members/{uid}` `{role: EDITOR\|VIEWER}` | OWNER để ghi |
| GET | `/modules` · PATCH `/modules/{mid}` `{enabled}` | OWNER để ghi; module PLANNED/BLOCKED không bật được |
| GET | `/tools` · POST `/tools/sync` | đọc `tool/catalog` (chỉ đọc); sync: OWNER |
| GET/POST | `/tasks` · PATCH `/tasks/{tid}` (If-Match) | ghi trong dự án có quyền EDITOR |
| GET | `/activity?limit&before_id&action&object_type&object_id&result` | OWNER thấy tất cả; người khác thấy của mình |

## Prompt Studio (module `crown_prompt_studio`)
| Method | Đường dẫn | Thân / tham số | Ghi chú |
|---|---|---|---|
| GET | `/prompts` | `q, project_id, category, target_ai, tag, favorite=1, archived=0\|1\|all, page, limit≤100, sort=updated\|title` | `{items,total,page,limit,search_mode}`; thứ tự ổn định |
| GET | `/prompts/facets` | — | tag, danh mục, AI đích trong phạm vi được xem |
| POST | `/prompts` | `{project_id, title, description?, category?, target_ai?, tags?, content?, changelog?}` | tạo v1 nếu có nội dung |
| GET | `/prompts/{pid}` | — | chi tiết + `revision`, `head_version`, `production_version`, `draft_dirty`, `your_role` |
| PATCH | `/prompts/{pid}` (If-Match) | `{title?, description?, category?, target_ai?, tags?, favorite?, project_id?}` | |
| PUT | `/prompts/{pid}/draft` (If-Match) | `{content}` | autosave; không tạo phiên bản |
| GET/POST | `/prompts/{pid}/versions` (POST cần If-Match) | `{changelog?, content?}` | snapshot bất biến; trùng nội dung → 409 `NO_CHANGE` |
| GET | `/prompts/{pid}/versions/{vid}` | — | có `content`, `checksum` |
| POST | `/prompts/{pid}/versions/{vid}/restore` (If-Match) | `{changelog?}` | tạo phiên bản MỚI; production/assignment không đổi |
| POST | `/prompts/{pid}/versions/{vid}/review` | `{status: DRAFT\|TESTING\|APPROVED\|REJECTED, note?}` | APPROVED/REJECTED: OWNER |
| PUT | `/prompts/{pid}/production` | `{version_id\|null, expected_version_id}` | OWNER; compare-and-set; chỉ bản APPROVED |
| GET | `/prompts/{pid}/diff?from=&to=` | `draft`, `head` hoặc version id | `{rows[{op,a,b,text}], added, removed, identical}` |
| POST | `/prompts/{pid}/duplicate` · `/archive` · `/unarchive` (If-Match cho archive) | | |
| POST | `/prompts/{pid}/render` | `{version: draft\|head\|<vid>, variables:{}}` | thiếu biến/khối lỗi → 400 kèm danh sách; không đoán giá trị |
| GET | `/prompts/{pid}/analysis?version=` | | quy tắc tĩnh, `kind: STATIC_RULES` |
| GET | `/prompts/{pid}/export?format=txt\|md\|json&version=` | | tải tệp; json gồm mọi phiên bản |
| GET/POST | `/prompts/{pid}/sources` · DELETE `/prompts/{pid}/sources/{sid}` | `{kind, title, url?, note?, captured_at?}` | nguồn là dữ liệu, không tự truy cập |
| POST | `/import/preview` | `{project_id, files:[{filename, content_b64}]}` | không ghi gì; báo trùng/lỗi từng tệp |
| POST | `/import/commit` | `{project_id, items:[{filename, content_b64, title?, action: create\|new_version\|skip, target_prompt_id?, target_revision?}]}` | tất cả hoặc không |

## AI Hub (OWNER)
| Method | Đường dẫn | Ghi chú |
|---|---|---|
| GET | `/ai` | cài đặt, ngân sách, provider (`key_configured` có/không), usage |
| PUT | `/ai/settings` | `{enabled?, budget?{daily_usd, monthly_usd, per_request_usd, max_output_tokens, timeout_seconds, max_retries, max_concurrency}}` |
| POST / PATCH | `/ai/providers` · `/ai/providers/{aid}` | `{name, kind, model, key_env, base_url, paid, enabled}` — trường `api_key` bị từ chối |
| POST | `/ai/check` | `{provider_id, estimated_usd, max_output_tokens}` → `{allowed, reasons}` (không gọi mạng) |

## CROWN AGENT (module `crown_agent_control`)
| Method | Đường dẫn | Quyền |
|---|---|---|
| GET | `/integrations/crown-agent/health` | đăng nhập — `{registry, ledger}` mỗi phần `{status, evidence}` |
| GET | `/integrations/crown-agent/tasks` | OWNER — đọc `tasks.db` chế độ chỉ đọc nếu có |
| GET · POST | `/agents` · `/agents/sync` | OWNER — đọc `registries/*.json` |
| POST · DELETE | `/agents/{agent_id}/token` | OWNER — cấp (hiện một lần) / thu hồi |
| GET · POST | `/assignments?prompt_id&agent_id&active` · `/assignments` `{agent_id, prompt_id, version_id, note?}` | đọc theo quyền dự án; tạo: OWNER, chỉ bản APPROVED |
| POST | `/assignments/{aid}/revoke` | OWNER |
| GET | `/agent/me` · `/agent/prompts` · `/agent/prompts/{pid}` | **Bearer token Agent** — chỉ phiên bản được ghim |

### Hợp đồng adapter CROWN AGENT (`crown_infinity/agents.py`)
`CrownAgentAdapter(repo_root, ledger_path=None)` — `health()`, `registry_agents()`, `ledger_tasks(limit)`. Chỉ đọc. Muốn thêm thao tác ghi (giao/dừng nhiệm vụ) phải có endpoint + quyền phía CROWN AGENT trước, rồi bổ sung phương thức mới có test; không suy diễn trạng thái ONLINE khi chưa đọc được.

Ví dụ Agent lấy prompt:
```
curl -H "Authorization: Bearer cia_xxx" http://127.0.0.1:8765/api/v1/agent/prompts/P-0123456789ab
→ {"agent_id":"A14","prompt_id":"P-…","version_id":"V-…","version_no":1,"checksum":"…","content":"…"}
```
