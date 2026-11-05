# Nhật ký quan sát video mẫu — TJ-20261105-315106

- Mẫu: `tool/inbox/TJ-20261105-315106/files/mau_danh_lai_so_srt.mp4` (12,58 giây, 1280×720, 12 hình/giây; băm trong `frames.json`).
- Người quan sát: `tool-pt-01` (nhánh Phân tích mẫu), giao bởi `agent-tool`. Cách lấy hình: FFmpeg cục bộ, 17 khung
  (đầu, ngay sau mỗi lần màn hình đổi, lưới cách đều, cuối). Ảnh ghép: `samples/contact_sheet.jpg`.
- Mức chắc chắn: **THẤY** = đọc rõ trong khung hình · **SUY RA** = kết luận từ nhiều khung · **ĐOÁN** = không có căn cứ trong video.

## Theo mốc thời gian
| Mốc (giây) | Người dùng làm gì | Màn hình đổi ra sao | Đầu vào | Kết quả | Mức |
|---|---|---|---|---|---|
| 0,00 | — | Trang chưa vẽ (khung trắng) | — | — | THẤY |
| 0,37 – 1,26 | Chưa thao tác | Tiện ích "Tiện ích phụ đề SRT": nút ① Chọn tệp .srt, nhãn "Chưa chọn tệp", nút ② Đánh lại số từ 1, nút ③ Tải kết quả; hai khung trống "PHỤ ĐỀ GỐC (ĐẦU VÀO)" và "KẾT QUẢ (ĐẦU RA)"; dòng trạng thái "Bước 1: chọn một tệp phụ đề .srt" | — | — | THẤY |
| 2,52 | **Bấm nút ① Chọn tệp .srt** (vòng đỏ + viền đỏ quanh nút) | Chưa đổi | — | — | THẤY |
| 2,79 | (đã chọn một tệp) | Nhãn đổi thành "phim_mau.srt · 4 đoạn"; khung trái hiện nội dung tệp; trạng thái "Đã nạp phim_mau.srt: 4 đoạn phụ đề. Bước 2: bấm "Đánh lại số từ 1"." | Tệp `phim_mau.srt`, 4 đoạn, số thứ tự **3, 8, 8, 15** | — | THẤY (hộp chọn tệp của hệ điều hành không có trong video: SUY RA) |
| 3,77 – 5,37 | Chưa thao tác | Không đổi | — | — | THẤY |
| 5,79 | **Bấm nút ② Đánh lại số từ 1** (viền đỏ quanh nút) | Khung phải hiện kết quả; trạng thái "Đã đánh lại 4 đoạn: 3→1, 8→2, 8→3, 15→4. Timecode và lời thoại giữ nguyên." | Nội dung khung trái | 4 đoạn, số thứ tự **1, 2, 3, 4**; timecode và lời thoại từng đoạn giống hệt khung trái | THẤY |
| 6,29 – 8,81 | Chưa thao tác | Không đổi | — | — | THẤY |
| 10,07 | **Bấm nút ③ Tải kết quả** (viền đỏ quanh nút) | Trạng thái "Đã lưu kết quả thành tệp phim_mau.srt (251 ký tự)." | Kết quả ở khung phải | Tệp tải về mang **cùng tên** tệp gốc, dài **251 ký tự** | THẤY |
| 10,45 – 12,46 | Chưa thao tác | Không đổi | — | — | THẤY |

## Đầu vào đọc được (khung 3,77 giây) — chép lại vào `testdata/mau_tu_video.srt`
```
3
00:00:01,200 --> 00:00:03,900
Chào buổi sáng, anh Lộc.

8
00:00:04,100 --> 00:00:07,450
Hôm nay mình bàn về
kế hoạch quý tư nhé.

8
00:00:08,000 --> 00:00:10,300
- Dạ, em chuẩn bị xong rồi.
- Tốt lắm!

15
00:00:11,050 --> 00:00:13,800
Bắt đầu thôi.
```
Kết quả đọc được (khung 7,55 giây): y hệt, chỉ khác bốn dòng số thứ tự thành 1, 2, 3, 4 → `testdata/mau_tu_video.expected.srt`.

## Điều rút ra
1. THẤY — Chức năng: đánh lại số thứ tự các đoạn phụ đề, bắt đầu từ 1, tăng dần 1 theo thứ tự xuất hiện trong tệp.
2. THẤY — Số cũ không liên tục (3, 8, 15) và trùng nhau (8, 8) đều được thay theo vị trí, không theo giá trị cũ.
3. THẤY — Dòng timecode và mọi dòng lời thoại (kể cả đoạn có hai dòng, dòng bắt đầu bằng "- ") giữ nguyên từng ký tự.
4. THẤY — Tệp kết quả mang cùng tên với tệp gốc; tiện ích báo số đoạn và bảng đổi số "cũ→mới".
5. SUY RA — Tiện ích mẫu báo kết quả dài **251 ký tự**. Bản chép kết quả ở trên (chỉ đổi bốn dòng số, kết thúc bằng một dấu
   xuống dòng) đếm được **250 ký tự**. Vậy kết quả của tiện ích mẫu dài hơn đúng 1 ký tự so với bản "chỉ đổi số".
   ĐOÁN: ký tự thừa là một dấu xuống dòng thêm ở cuối tệp (không nhìn thấy trong khung hình). Xem quyết định D1 trong SPEC.
6. SUY RA — Chữ tiếng Việt trong khung hình có vài dấu bị vẽ lệch ("vê`", "kê´hoạch") do phông chữ đơn cách của video;
   nội dung thật là "về", "kế hoạch". Bản chép dùng chữ đúng chính tả, dạng dựng sẵn (NFC).

## Không quan sát được (đưa sang mục GIẢ ĐỊNH của SPEC, mỗi mục có ca thử)
Bảng mã và BOM của tệp · kiểu xuống dòng (LF hay CRLF) · tệp có nhiều dòng trống giữa các đoạn · tệp thiếu dòng trống cuối ·
đoạn thiếu số thứ tự · lời thoại là một dòng chỉ gồm chữ số · timecode dùng dấu chấm hoặc có thông tin vị trí phía sau ·
tệp rỗng hoặc không phải SRT · tệp rất lớn. Mã bên trong của tiện ích mẫu KHÔNG được đọc; đặc tả chỉ dựa trên phần nhìn thấy.
