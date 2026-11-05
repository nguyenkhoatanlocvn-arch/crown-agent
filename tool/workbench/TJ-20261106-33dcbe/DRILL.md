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

## Kết quả thật (2026-11-06, giờ VN) — ĐÃ KIỂM CHỨNG bằng kết quả lệnh
| Trường hợp | Lệnh | Kết quả | Bằng chứng |
|---|---|---|---|
| (a) AT phát hành lại 1.0.0 | `--sync publish … --actor agent-tool-at --job TJ-20261106-33dcbe --token 1` | TỪ CHỐI "không phát hành trùng"; `tool.json` của kho giữ nguyên băm | `PUBLISH_a.txt` |
| (b1) Bộ thử đầy đủ với bản lỗi 1.0.1 | `python -m tool test build` | KHÔNG ĐẠT 56/72 (16 ca CRLF/CR trượt) — cổng phát hành sẽ chặn | `TEST_b_day_du.txt` |
| (b2) Bộ thử rút gọn có chủ ý | bỏ 16 ca rồi `test` | 56/56 — bản lỗi lọt cổng (đúng ý diễn tập) | `TEST_b_rut_gon.json`, `TEST_b_rut_gon.txt` |
| (b3) Phát hành tự động trong phiên AT | `--sync publish … --auto --session AT-20261106-0309-c22d0b` | ĐÃ PHÁT HÀNH 1.0.1, thành bản hiện hành; phiên ghi 1/1 suất | `PUBLISH_b.txt` |
| (b4) A15 gọi thử từ kho, tệp CRLF khác mẫu | `--sync smoke srt-renumber@1.0.1 --caller A15 … --rollback-as agent-tool-at` | LỖI → tự thu hồi 1.0.1 → hiện hành 1.0.0 | `SMOKE_b_1.0.1.txt` |
| (b5) Gọi thử lại bản hiện hành | `--sync smoke srt-renumber --caller A15 …` (2 tệp khác mẫu) | ĐẠT 3/3 trên 1.0.0 | `SMOKE_b_sau_quay_lui.txt` |
| (b6) Gọi thẳng bản đã thu hồi | `run srt-renumber@1.0.1 --caller A15` | TỪ CHỐI "đã bị thu hồi" | `tool/runs/2026-11-06/R-20261106-031213-7d049c/run.json` |

Việc này đóng ở trạng thái CANCELLED (diễn tập, không tạo tool mới). Bản 1.0.1 nằm lại trong kho ở trạng thái WITHDRAWN làm dấu vết.
