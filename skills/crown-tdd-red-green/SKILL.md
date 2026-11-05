---
name: crown-tdd-red-green
description: Use when writing or changing CROWN AGENT code (BTC bot patches, Etsy hub, web, scripts) before writing the implementation — a failing test first, seen failing for the right reason, then minimal code to green, then the full suite.
---

Chuyển thể từ `obra/superpowers` skill test-driven-development (MIT, © 2025 Jesse Vincent, commit 22f0b46), viết lại cho CROWN AGENT. Bổ sung cho `crown-plan-and-isolate` (kế hoạch + worktree) và `root-cause-debugging` (tìm nguyên nhân trước).

## Luật sắt
KHÔNG có mã sản phẩm nào khi chưa có test hỏng trước. Lỡ viết mã trước → bỏ, viết test rồi làm lại; không giữ "để tham khảo".

## Vòng lặp
1. **ĐỎ:** một test nhỏ cho một hành vi, tên nói rõ hành vi. Dùng mã thật; chỉ mock thứ không thể chạy thật (sàn Binance, Etsy, mạng).
2. **Xem ĐỎ (bắt buộc):** chạy, test phải hỏng đúng lý do (thiếu tính năng), không phải lỗi gõ/import. Test xanh ngay → nó đang kiểm hành vi đã có → sửa test.
3. **XANH:** mã tối thiểu để qua. Không thêm tùy chọn, không refactor chỗ khác.
4. **Xem XANH (bắt buộc):** chạy CẢ bộ test của dự án (ví dụ `.venv\Scripts\python -m pytest -q` trong worktree), báo tên từng test hỏng, kể cả test không phải do mình làm hỏng.
5. **Dọn:** chỉ sau khi xanh; giữ xanh; không thêm hành vi.
6. Lặp với test hỏng tiếp theo.

## Riêng bot tiền thật
- Mọi vá bot làm trên bản sao/worktree, không trên `BTCDesk\` đang chạy.
- Lỗi đã gặp (ví dụ lỗ ảo do equity cap, mất mạng nhiều giờ, lệnh trùng khi retry) → trước tiên một test tái hiện lỗi đó, chạy thấy hỏng, rồi mới vá.
- Test chạm lệnh: dùng testnet hoặc client giả; không bao giờ gửi lệnh thật trong test.
- Ghi vào báo cáo: lệnh đã chạy, số test đỏ → xanh, tổng số test đạt.

## Test tốt
Nhỏ (tên có chữ "và" → tách); rõ (tên mô tả hành vi); trước khi viết, nói được thay đổi nào trong mã sẽ làm test hỏng; kiểm hành vi thật, không kiểm mock.

## Cổng đưa lên bot chạy thật
Xanh trên bản sao chưa phải là xong. Đưa lên bản đang chạy chỉ qua một thư mục gói vá đặt tên `BTCDesk_FIX_<tên>` gồm: sao lưu bản cũ, diff đã được người khác review (`agent-dispatch-and-review`), tệp `CAI.bat` (cài bản vá), tệp `QUAY_LAI.bat` (khôi phục bản sao lưu), kết quả test. Chủ tịch (hoặc người được Chủ tịch giao rõ) bấm cài; agent không tự cài vào `BTCDesk\` đang chạy.

## Ngoại lệ
Chỉ khi Chủ tịch nói rõ "bỏ TDD cho việc X" hoặc kế hoạch đã duyệt ghi rõ: bản thử vứt đi, mã sinh tự động, tệp cấu hình thuần. KHÔNG có ngoại lệ cho mã chạm lệnh, số dư, kích cỡ lệnh hay tiền — dù ai yêu cầu.

## Lời bào chữa phải bác → làm lại theo TDD
"Gấp, bot đang chạy" (→ càng gấp càng phải có test, vá trên bản sao) · "khỏi viết test" (→ chỉ chấp nhận khi đúng điều kiện mục Ngoại lệ) · "đơn giản quá không cần test" · "test sau" · "đã thử tay rồi" · "xóa thì phí công" · "mã cũ chưa có test" (→ thêm test cho nó) · "lần này khác" · test xanh ngay lần đầu · không giải thích được vì sao test hỏng.

## Kẹt thì
Không biết test kiểu gì → viết API mong muốn và câu assert trước. Phải mock mọi thứ → thiết kế quá dính, tách phụ thuộc (dependency injection). Setup quá dài → rút helper, rồi đơn giản hóa thiết kế.
