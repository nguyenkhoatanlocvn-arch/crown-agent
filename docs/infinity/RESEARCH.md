# CROWN INFINITY — Nghiên cứu thành phần (kiểm 2026-11-06)

Phạm vi: chỉ các ứng viên liên quan trực tiếp tới MVP. Phiên bản/giấy phép lấy từ **metadata chính thức của PyPI/npm registry** ngày 2026-11-06 (VERIFIED ở mức metadata gói; chưa đọc toàn văn LICENSE từng thành phần con). Không dùng số sao làm bằng chứng. Không chạy script cài đặt nào.

| Ứng viên | Nguồn | Phiên bản (ngày phát hành) | Giấy phép (metadata) | Bảo trì | Phụ thuộc / chi phí | Kết luận |
|---|---|---|---|---|---|---|
| FastAPI | pypi.org/project/fastapi · github.com/fastapi/fastapi | 0.162.0 (2026-10-23) | MIT | Có bản mới trong 2 tuần | Cần starlette, pydantic, uvicorn; cài qua pip | **REFERENCE** cho MVP. Lý do chọn stdlib: xem ADR-001. Hợp đồng API giữ dạng REST/JSON để chuyển sang FastAPI sau nếu cần |
| uvicorn | pypi.org/project/uvicorn | 0.54.0 (2026-09-25) | BSD-3-Clause | Có | — | REFERENCE (đi cùng FastAPI) |
| React / Vite / TypeScript | registry.npmjs.org | react 19.3.1 (MIT), vite 8.3.6 (MIT), typescript 7.0.2 (Apache-2.0) | như cột trước | Có | Cần Node + bước build trên máy Windows | **REFERENCE**. MVP dùng ES module thuần, không bước build (ADR-002) |
| Tauri CLI | npm `@tauri-apps/cli` · github.com/tauri-apps/tauri | 2.12.4 | Apache-2.0 OR MIT | Có | Rust toolchain + WebView2 trên Windows | **ADAPT (giai đoạn sau)** — đóng gói Desktop khi lõi ổn định; UI hiện tại là tệp tĩnh nên nhúng được |
| Playwright | npm `playwright` · github.com/microsoft/playwright | 1.65.0 mới nhất; máy này có 1.63.0 | Apache-2.0 | Có | Chỉ dùng cho kiểm thử | **REUSE** cho test giao diện (`tests/web/infinity_ui_test.cjs`) |
| LiteLLM | pypi.org/project/litellm · github.com/BerriAI/litellm | 1.108.1 (2026-11-05) | MIT (metadata; repo có thư mục enterprise riêng — chưa kiểm) | Rất năng động | Nhiều phụ thuộc | **REJECT cho MVP** (AI tắt). Cân nhắc lại khi bật AI Hub thật; phải đọc LICENSE thư mục enterprise |
| APScheduler | pypi.org/project/APScheduler | 3.11.3 (2026-06-28) | MIT | Có | — | **REJECT** lúc này: chưa có lịch nào cần chạy (chỉ thị mục 09) |
| MCP Python SDK | pypi.org/project/mcp · github.com/modelcontextprotocol/python-sdk | 2.3.0 (2026-10-02) | MIT | Có | — | **REFERENCE** cho MCP Gateway (PLANNED) |
| Langfuse (SDK Python) | pypi.org/project/langfuse | 5.2.0 (2026-11-05) | MIT (SDK) | Có | Máy chủ Langfuse là repo khác, giấy phép **NOT VERIFIED** | **REFERENCE** cho Test Lab/observability |
| Promptfoo | npm `promptfoo` · github.com/promptfoo/promptfoo | 0.125.1 | MIT | Có | Node | **REFERENCE** cho thiết kế Test Lab (rubric, expected output) |
| Coolify, Activepieces, Mixpost, Postiz, FFmpeg, screenshot-to-code, craft-anything | — | — | — | — | — | **NOT EVALUATED** trong lượt này — thuộc module PLANNED (deploy, automation, social, forge). Không suy đoán phiên bản/giấy phép |

## Sản phẩm tham chiếu (chỉ học ý tưởng, không dùng mã/tài sản)
| Sản phẩm | Nguồn công khai (đọc 2026-11-06) | Điều rút ra cho Prompt Studio | Đã áp dụng |
|---|---|---|---|
| PromptHub | [prompthub.us/features/prompt-versioning](https://www.prompthub.us/features/prompt-versioning) (trang marketing) | Phiên bản kiểu Git, review trước khi dùng, Agent lấy prompt qua API | Có: phiên bản bất biến, review_status, production pointer, API Agent theo phiên bản ghim. Chưa có: nhánh/merge request |
| OpenLIT / Arize Phoenix (kết quả phụ) | docs.openlit.io, phoenix.arize.com | Nhiều người thử biến thể không ghi đè nhau | Có: revision + xung đột rõ ràng |
| YouMind | [youmind.com/updates/…new-board](https://youmind.com/ru-RU/updates/officially-introducing-ai-write-and-new-board), [quick start](https://youmind.com/ru-RU/use-cases/youmind-quick-start-guide) (nguồn chủ yếu do hãng tự viết) | Board gom nguồn, ghi chú, đoạn trích, đầu ra theo dự án; trích dẫn nguồn | Một phần: dự án + nguồn tham chiếu (URL/ghi chú/tài liệu/lỗi/kết quả test). Board trực quan, đoạn trích, xuất bản: PLANNED |

Giới hạn: thông tin YouMind/PromptHub lấy từ trang giới thiệu, có thể phóng đại; không truy cập bản dùng thật.
