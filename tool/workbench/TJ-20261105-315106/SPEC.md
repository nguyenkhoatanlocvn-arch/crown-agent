# Đặc tả tool `srt-renumber` — Đánh lại số phụ đề SRT

- Mã việc: TJ-20261105-315106 · Yêu cầu của Chủ tịch: "Viết tool đánh lại số phụ đề từ 1, giữ nguyên timecode và lời thoại".
- Nguồn đặc tả: `OBSERVATION.md` (hành vi nhìn thấy trong video mẫu) + câu lệnh của Chủ tịch. KHÔNG đọc mã bên trong của tiện ích mẫu.
- Người viết: `tool-pt-01`. Người lập trình chỉ được dùng tệp này và thư mục `testdata/`.
- Sửa đổi 2026-11-06 02:35 (Thiên Công, sau vòng kiểm thử 1 của `tool-kt-02`): mục 2 thêm khóa `ignored_timecodes` và luật lỗi khi không ghi được
  stdout; thêm mục 5b (G12–G21).
- Kiểm thử độc lập vòng 2 (2026-11-06, `tool-kt-02`): PASS 72/72. Hai luật ở mục 2 không diễn đạt được bằng khuôn ca thử ("báo cáo không có khóa nào khác"; "không ghi được stdout thì thoát 2 và hủy tệp") đã được kiểm bằng tay ở cả hai vòng; chưa có ca tự động canh giữ.

## 1. Việc tool làm
Nhận một tệp `.srt`, trả một tệp `.srt` cùng tên, trong đó số thứ tự các đoạn phụ đề được đánh lại 1, 2, 3, … theo thứ tự xuất hiện.
Mọi byte khác của tệp giữ nguyên.

## 2. Hợp đồng chạy (theo PLAYBOOK mục 5)
- Gọi: `python main.py request.json`; `request.json = {"inputs": {"input": "in/<tên>.srt"}, "out_dir": "out", "params": {"start": 1}}`.
- Kết quả: `out/<tên>.srt` (cùng tên tệp đầu vào). Dòng cuối stdout là JSON:
  `{"ok": true, "cues": <số đoạn>, "changed": <số dòng số đã đổi>, "inserted": <số dòng số đã chèn>, "mapping": [["3", 1], ["8", 2], ...], "ignored_timecodes": <số dòng timecode không mở đầu đoạn nào, xem G12>}`
  (`mapping`: số cũ dạng chuỗi → số mới; đoạn thiếu số ghi số cũ là `null`; chỉ liệt kê tối đa 200 cặp đầu). Báo cáo không có khóa nào khác.
- Tham số `start` (số nguyên ≥ 0, mặc định 1): số của đoạn đầu tiên.
- Lỗi: thoát mã 2, in một dòng tiếng Việt ra stderr, KHÔNG tạo tệp kết quả. Áp dụng cho mọi lỗi, kể cả khi không ghi được dòng báo cáo
  ra stdout (khi đó tệp kết quả vừa ghi phải bị hủy).
- Chỉ thư viện chuẩn Python; không mạng, không gọi tiến trình khác; manifest khai `network: false`, `subprocess: []`.

## 3. Hành vi đã THẤY trong video (bắt buộc, có dữ liệu thử)
| Mã | Hành vi | Dữ liệu thử |
|---|---|---|
| H1 | Số cũ 3, 8, 8, 15 → 1, 2, 3, 4 (không liên tục và trùng nhau đều đổi theo vị trí) | `testdata/mau_tu_video.srt` → `mau_tu_video.expected.srt` |
| H2 | Dòng timecode giữ nguyên từng ký tự | cùng tệp |
| H3 | Lời thoại giữ nguyên: đoạn hai dòng, dòng bắt đầu bằng "- ", chữ tiếng Việt có dấu | cùng tệp |
| H4 | Tệp kết quả cùng tên tệp đầu vào | cùng tệp |
| H5 | Báo số đoạn và bảng đổi số cũ→mới | báo cáo: `cues = 4`, `mapping = [["3",1],["8",2],["8",3],["15",4]]` |

## 4. Quy tắc nhận diện (để "giữ nguyên" là thật)
- **Dòng timecode**: sau khi bỏ khoảng trắng hai đầu, khớp `G:PP:SS,mmm --> G:PP:SS,mmm` với G 1–3 chữ số, dấu thập phân là `,` hoặc `.`,
  1–3 chữ số phần nghìn, số khoảng trắng quanh `-->` tùy ý, phía sau có thể còn thông tin vị trí (vd. `X1:40 X2:600`).
- **Dòng số thứ tự** của một đoạn: dòng nằm NGAY TRƯỚC dòng timecode và chỉ gồm chữ số (cho phép khoảng trắng hai đầu).
  Chỉ dòng này được thay. Một dòng chỉ gồm chữ số nằm ở chỗ khác (vd. lời thoại "2024") là lời thoại, không đụng tới.
- Khi thay dòng số: giữ nguyên khoảng trắng đầu/cuối dòng và ký tự xuống dòng của chính dòng đó; chỉ thay cụm chữ số.

## 5. GIẢ ĐỊNH (chức năng bên trong chưa quan sát được — mỗi giả định phải có ca thử)
| Mã | Giả định | Ca thử phải có |
|---|---|---|
| G1 | Tệp là UTF-8; nếu có BOM ở đầu thì BOM được giữ và dòng số đầu tiên vẫn được nhận ra | Tệp có BOM: kết quả vẫn có BOM, số đầu = 1 |
| G2 | Kiểu xuống dòng của tệp được giữ nguyên (CRLF vẫn là CRLF, LF vẫn là LF, kể cả tệp trộn) | Tệp CRLF: kết quả không có LF đơn lẻ |
| G3 | Số dòng trống giữa các đoạn giữ nguyên (không gộp, không thêm) | Tệp có 3 dòng trống giữa hai đoạn |
| G4 | Tệp không kết thúc bằng xuống dòng thì kết quả cũng không; kết thúc bằng nhiều dòng trống thì giữ nguyên | Hai tệp: thiếu và thừa xuống dòng cuối |
| G5 | Đoạn thiếu dòng số (timecode đứng đầu tệp hoặc ngay sau dòng trống) được CHÈN một dòng số mới phía trước, dùng kiểu xuống dòng của dòng timecode đó; đếm vào `inserted` | Tệp có đoạn giữa thiếu số |
| G6 | Lời thoại là dòng chỉ gồm chữ số không bị đổi | Đoạn có lời thoại "2024" và "15" |
| G7 | Timecode dùng dấu chấm, giờ 1 chữ số, hoặc có thông tin vị trí phía sau vẫn được nhận | Tệp có `0:00:01.2 --> 0:00:02.5 X1:40` |
| G8 | Tệp rỗng, chỉ có khoảng trắng, hoặc không có dòng timecode nào → lỗi mã 2, không tạo tệp | Ba tệp lỗi |
| G9 | Tệp không giải mã được UTF-8 → lỗi mã 2 (không đoán bảng mã, không làm hỏng chữ) | Tệp có byte 0xFF 0xFE … |
| G10 | `start` khác 1 (vd. 100) đánh 100, 101, …; `start` âm hoặc không phải số nguyên → lỗi mã 2 | Hai ca |
| G11 | Tệp 5.000 đoạn xử lý xong trong giới hạn 30 giây | Một ca sinh tự động |

### 5b. Làm rõ sau kiểm thử độc lập (2026-11-06, theo 10 điểm mơ hồ do tool-kt-02 nêu)
Người lập trình đã chọn cách hiểu chặt cho các chỗ đặc tả chưa nói. Nay ghi thành giả định chính thức; mỗi dòng có ca thử.
| Mã | Giả định | Ca thử |
|---|---|---|
| G12 | Dòng timecode đứng ngay sau một dòng chữ (không trống, không phải dòng số) hoặc ngay sau một dòng timecode khác KHÔNG mở đầu đoạn nào: để nguyên, không đếm vào `cues`, đếm vào `ignored_timecodes`. Tệp không có đoạn hợp lệ nào → lỗi mã 2 | `s4-timecode-sau-loi-thoai-bo-qua`, `g8-timecode-khong-mo-doan` |
| G13 | Chèn dòng số cho đoạn thiếu số mà dòng timecode là dòng cuối, không có ký tự xuống dòng: dùng kiểu xuống dòng gần nhất đã gặp trong tệp; chưa gặp thì LF | `g5-timecode-cuoi-khong-xuong-dong`, `g5-chi-mot-dong-timecode` |
| G14 | "Khoảng trắng" gồm mọi khoảng trắng Unicode và U+FEFF nằm giữa tệp; dòng chỉ có khoảng trắng được coi là dòng trống | `s4-bom-giua-tep-ghep`, `g5-sau-dong-chi-co-dau-cach` |
| G15 | Số có số 0 đứng đầu ("003", "01") được viết lại không có số 0 đầu và tính là đã đổi; `mapping` giữ nguyên chuỗi cũ | `s4-khoang-trang-quanh-so` |
| G16 | Dòng số viết bằng chữ số không phải ASCII (vd. chữ số toàn chiều rộng) là dòng chữ, không phải dòng số (khi đó áp dụng G12) | ca `kt-` bổ sung ở vòng kiểm lại |
| G17 | Thông tin vị trí sau giờ kết thúc phải cách bằng khoảng trắng; dính liền (vd. `00:00:02,000X1:40`) thì dòng đó không phải timecode | ca `kt-` bổ sung ở vòng kiểm lại |
| G18 | Ký tự CR đơn lẻ (`\r`, kiểu Mac cũ) và chuỗi CR CR LF (`\r\r\n`) cũng là ký tự xuống dòng và được giữ nguyên | `g2-cr-mac-cu`, `g2-crcrlf` |
| G19 | `start` phải là số nguyên JSON ≥ 0 (1.0, null, "5", true đều bị từ chối); tham số không có tên trong đặc tả bị từ chối, mã 2 | các ca `g10-*`, `tham-so-la` |
| G20 | Tên tệp đầu vào có đuôi viết hoa (`.SRT`) được chấp nhận; tệp kết quả giữ nguyên tên | ca `kt-` bổ sung ở vòng kiểm lại |
| G21 | Theo mục 4, một dòng chỉ gồm chữ số nằm ngay trước dòng timecode kế tiếp (không có dòng trống ngăn) là dòng số của đoạn sau và bị thay, kể cả khi người viết coi nó là lời thoại. Đây là giới hạn đã biết với tệp thiếu dòng trống giữa hai đoạn | `s4-thieu-dong-trong-giua-doan` |

## 6. Quyết định thiết kế (em quyết, Chủ tịch bác được)
- **D1 — Không thêm ký tự nào ngoài dòng số.** Tiện ích mẫu trả kết quả dài hơn 1 ký tự so với bản "chỉ đổi số" (251 so với 250;
  đoán là thêm một dấu xuống dòng cuối tệp). Yêu cầu của Chủ tịch là "giữ nguyên", nên tool mới KHÔNG lặp lại chi tiết đó:
  với tệp mẫu, kết quả dài đúng 250 ký tự.
- **D2 — Không sửa lỗi chính tả, không chuẩn hóa dấu, không sắp xếp lại đoạn theo thời gian.** Chỉ đánh số.
- **D3 — Dòng số đang đúng vẫn được ghi lại y nguyên** (`changed` chỉ đếm dòng thật sự khác đi).

## 7. Không thuộc phạm vi
Chuyển định dạng (VTT, ASS), dịch, chỉnh thời gian, gộp/tách đoạn, đoán bảng mã khác UTF-8.

## 8. Thông tin cho manifest
`id: srt-renumber` · `version: 1.0.0` · `category: data-files` · `name: Đánh lại số phụ đề SRT` ·
keywords gợi ý: srt, phụ đề, subtitle, đánh số, đánh lại số, renumber, số thứ tự, timecode.
Ví dụ trong manifest: `examples/phim_mau.srt` → `examples/phim_mau.expected.srt` (chính là cặp `testdata/mau_tu_video*`).
