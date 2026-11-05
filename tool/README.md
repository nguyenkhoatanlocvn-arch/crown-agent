# TOOL — bộ phận Thiên Công

Đưa vào một mẫu (video, prompt, tệp, ZIP, thư mục) → nhận lại một công cụ chạy được → mọi Agent trong danh bạ tìm và gọi được.
Python ≥ 3.10, chỉ thư viện chuẩn. Lấy khung hình video cần FFmpeg đã cài sẵn trên máy.

| Muốn | Đọc |
|---|---|
| Gọi một tool có sẵn | mục "Gọi tool" bên dưới |
| Làm một tool mới từ mẫu | `PLAYBOOK.md` |
| Hiểu vì sao xây như vậy | `DESIGN.md` |
| Vai trò hai Agent điều hành | `agents/agent-tool/CHARTER.md`, `agents/agent-tool-at/CHARTER.md` |
| Nguồn GitHub đã khảo sát | `skills/sources/SOURCE_REGISTER.json` |

## Gọi tool (mọi Agent trong danh bạ)
```
python -m tool find "đánh số phụ đề"        # tìm theo chức năng; gõ không dấu cũng được
python -m tool find --category data-files    # video-image | data-files | web-app | automation | testing
python -m tool show srt-renumber             # mô tả, ví dụ vào/ra, phiên bản, nguồn, cách gọi
python -m tool run srt-renumber --caller <ID của mình> --in input=<tệp.srt> --out <thư mục>
```
Mỗi lần chạy đều kiểm: người gọi có trong danh bạ và không bị tắt; quyền đọc từng tệp đầu vào; quyền ghi nơi nhận kết quả và từng tệp
kết quả; băm của phiên bản tool. Tệp/thư mục giống bí mật (`.env`, `*.key`, `auth*.json`, `.ssh/`, `.git/`…), thư mục hệ thống và mục ẩn
trong thư mục nhà luôn bị từ chối. Mỗi lượt chạy để lại `runs/<ngày>/<lượt>/run.json`.

## Thư mục
```
inbox/       mẫu gửi vào, theo mã việc (mẫu riêng tư nằm ở inbox/<việc>/private/, không vào git)
workbench/   đặc tả, mã nguồn, bản thử theo mã việc (job.json = trạng thái + khóa ghi; events.jsonl = nhật ký chỉ thêm)
catalog/     tool đã phát hành: <mã tool>/<phiên bản>/ (bất biến, có RELEASE.json ghi băm từng tệp)
skills/      skill riêng của bộ phận: <tên>/v<N>/SKILL.md + registry.json (người cấp, kết quả thử, lịch sử quay lui)
agents/      hồ sơ, hộp việc và bộ nhớ riêng của từng Agent điều hành; sub/ = Agent con
runs/        lượt chạy tool, phiên AT (sessions/), khóa phiên (locks/), trạng thái theo dõi GitHub
config/      watch.json (nguồn theo dõi), budget.json (hạn mức CFO), schedule.json (lịch), access.json (quyền dữ liệu, tùy chọn)
core/        mã của bộ phận; cli.py = lệnh `python -m tool`
```

## Kiểm thử
```
python -m unittest discover -s tests -t .                      # toàn bộ (gồm sổ đăng ký + sổ nhiệm vụ CROWN)
node tests/web/tool_tab_test.cjs [thư mục xuất] [thư mục ảnh]   # tab TOOL trên web, bằng trình duyệt thật + runtime giả
```

## Giới hạn đã biết
- ID người gọi là tự khai (chưa có khóa riêng từng Agent) — giống CLI `crown`. Mọi cổng ở đây chặn sai sót và lách luật vô ý,
  và để lại dấu vết; chúng không thay được quyền ghi của repo. Vì vậy `budget approve --actor CHAIRMAN` là hàng rào + nhật ký, không phải chữ ký.
- Vùng cách ly chặn sai sót và việc dùng mạng/tiến trình ngoài ý muốn; không phải nhà tù cho mã độc. Bộ soát quyền đọc mã theo tên đã phân giải
  (bí danh, `from os import`, `getattr`, `sys.modules`, mô-đun nội bộ) nhưng mã cố tình che giấu vẫn có thể lọt. Trên Windows không tách được mạng.
- Lệnh ngoài: PATH của lượt chạy chỉ chứa lệnh đã khai trong manifest, nhưng tool vẫn gọi được đường dẫn tuyệt đối nếu cố ý.
- Web không chạy mã: nút Chạy thử ghi yêu cầu rồi gọi worker (một phiên Claude), kết quả về sau 1–3 phút.
- Tool chỉ dùng thư viện chuẩn Python; tool cần gói ngoài phải qua thẩm định riêng.
- `--sync` đẩy mọi commit đang có trên nhánh `master` của bản sao, không riêng phần `tool/`.
