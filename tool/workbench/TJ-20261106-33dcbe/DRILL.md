# DIỄN TẬP nghiệm thu mục 8 — TJ-20261106-33dcbe

Chủ tịch yêu cầu (lệnh lập mục TOOL, mục 8): thử thêm hai trường hợp trên hệ thống thật.
Người làm: Thiên Công AT (`agent-tool-at`), trong phiên chạy tay `AT-20261106-0309-c22d0b` (dấu MANUAL, không phải lượt do lịch kích hoạt).
Worker: phiên làm việc của Chủ tịch (Claude), không phải phiên định kỳ.

## (a) Hai Agent cùng phát hành một phiên bản
Thiên Công (`agent-tool`) đã phát hành `srt-renumber@1.0.0` (việc TJ-20261105-315106).
Thiên Công AT đưa đúng bản dựng đó qua đủ bước của việc này rồi phát hành lại số 1.0.0.
Mong đợi: lệnh từ chối "không phát hành trùng"; kho không đổi một byte.

## (b) Bản mới bị lỗi cần quay lui
Bản 1.0.1 CỐ Ý lỗi: đổi mọi `\r\n` thành `\n` trước khi ghi (trái đặc tả G2 "giữ nguyên kiểu xuống dòng").
1. Bộ thử đầy đủ phải bắt được lỗi này (cổng phát hành chặn).
2. Để diễn tập quay lui, bộ thử bị RÚT GỌN CÓ CHỦ Ý (bỏ các ca dùng CRLF/CR) cho bản lỗi lọt cổng — mô phỏng một bộ thử thiếu ca.
   Người "kiểm thử" ở bước này là một bản ghi Agent con nhánh Kiểm thử do chính phiên này đóng vai: đúng luật về hình thức,
   KHÔNG phải kiểm thử độc lập thật. Chỉ dùng cho diễn tập.
3. Phát hành tự động trong phiên AT (`--auto --session`), rồi một Agent thường (A15) gọi thử từ kho bằng tệp CRLF khác mẫu.
Mong đợi: gọi thử lỗi → tự thu hồi đúng bản 1.0.1 → bản 1.0.0 lại là hiện hành → gọi thử lại đạt.

Kết quả thật được ghi ở cuối tệp sau khi chạy.
