---
name: crown-brainstorm-before-build
description: Use before building anything new or non-trivial for CROWN AGENT (new agent, feature, bot strategy, web page, company workflow) — read first, ask the Chairman grouped questions, present 2–3 options, get the design approved before any code.
---

Chuyển thể từ `obra/superpowers` skill brainstorming (MIT, © 2025 Jesse Vincent, commit 22f0b46), viết lại theo ưu tiên của Chủ tịch. Khác bản gốc: hỏi THEO NHÓM thay vì một câu mỗi lượt, vì Chủ tịch yêu cầu hỏi kỹ từng nhóm.

## Phân loại trước
- Việc rõ, đã đủ căn cứ → làm luôn, báo kết quả. Không nghi thức.
- Thiếu điều làm đổi cách xây → vào quy trình dưới.

## Quy trình
1. **Đọc trước khi hỏi.** Đọc tài liệu Project, sổ quyết định, mã liên quan. Không hỏi lại điều tài liệu đã trả lời; nêu ngắn mình đã đọc gì.
2. **Phản hồi đầu (~200 chữ):** nhắc lại yêu cầu bằng lời mình, nêu phát hiện quan trọng, hỏi "vì sao/để kiếm tiền thế nào" nếu chưa rõ.
3. **Hỏi theo nhóm**, chỉ những câu có đáp án làm đổi bản xây: Mục tiêu · Phạm vi (có/không/cấm) · Ngân sách & chi phí · Quyền hạn & dữ liệu · Tiêu chí hoàn thành. Nhóm đã rõ thì bỏ. Câu quan trọng nhất đặt đầu.
4. **Nhắc lại hiểu biết** sau mỗi khối thông tin: tách rõ "anh nói" với "em đề xuất".
5. **Trình phương án** (tin nhắn riêng): 2–3 phương án thực tế, mỗi cái có đánh đổi (tiền, thời gian, rủi ro, công sức của anh), một khuyến nghị, rồi xin anh chọn.
6. **Viết thiết kế:** ý định & lý do; mục tiêu / không làm / cấm làm; ràng buộc; phương án đã xét và lựa chọn; phần đã chốt; phần để người xây tự quyết. Lưu vào thư mục sản phẩm của nhánh đang làm (theo `crown-multi-tab-sync`), không ghi vào file của nhánh khác.
7. **Tự phản biện:** với dự án, giao subagent mới đọc thiết kế như người xây và liệt kê câu hỏi; chia 3 loại: đã có đáp án → bổ sung; nhỏ → em tự chốt và ghi "em quyết"; của anh → gửi một lần, quan trọng trước. Không có subagent thì tự đóng vai người xây.
8. **Cổng duyệt:** chưa được duyệt thì không viết mã sản phẩm, không dựng khung, không cài gói, không gọi skill triển khai. Đọc và khảo sát thì được. Sau duyệt → `crown-plan-and-isolate`.

## Cỡ việc
Thay đổi nhỏ → thiết kế ngắn. Dự án → thiết kế đủ. Phạm vi phình giữa chừng → dừng, nâng cỡ. Nhiều dự án độc lập → nói ra, thống nhất thứ tự, làm từng cái.

## Bản thử (spike)
Chỉ khi anh đồng ý: dựng nhanh, không test đầy đủ, mang bài học về. Bản thử không được chạm khóa API, tài khoản hay sàn thật. Giữ lại bản thử = yêu cầu mới, phải duyệt.

## Lời bào chữa phải bác
- "Đưa luôn menu lựa chọn" → để anh mô tả trước, phương án sau.
- "Tự điền mặc định hợp lý" → chỉ được nếu nói thẳng ra.
- "Anh nói 'ok' rồi" → chỉ duyệt điều đã trình, không duyệt bản chưa viết.
- "Bản thử chạy được, xây tiếp luôn" → cần duyệt.
- Không giảng quy trình cho anh; không dùng biệt ngữ quy trình.
