# TOOL — Thiết kế v1 (2026-11-05)

Người lập: Thiên Công (`agent-tool`), theo lệnh Chủ tịch 2026-11-05 22:58. Trạng thái: ĐÃ XÂY, có test. Sổ tay vận hành: `tool/PLAYBOOK.md`.

## Mục tiêu
Chủ tịch đưa một video, prompt, tệp, ZIP hoặc thư mục mẫu → nhận lại một công cụ chạy được → mọi Agent trong danh bạ tìm và gọi được.

## Ràng buộc thật của môi trường (kiểm 2026-11-05 23:10)
- Mỗi phiên Claude (chat, tác vụ định kỳ) chạy trong một máy cloud riêng, mất sau phiên. Thứ dùng chung bền vững: repo GitHub `crown-agent`,
  web Hoàng Thành (artifact + cơ sở dữ liệu của nó), tài liệu Project.
- Trang web không chạy được mã Python. Việc chạy tool do một phiên Claude làm (worker).
- Laptop của Chủ tịch: liên kết được nhưng chưa cấp thư mục, không có cửa sổ lệnh từ xa → TOOL không phụ thuộc laptop.

## Kiến trúc
| Lớp | Ở đâu | Vai trò |
|---|---|---|
| Lõi | `tool/core/*.py` (chỉ thư viện chuẩn) | Sổ việc, khóa ghi, Agent con, kho, vùng thử, quyền dữ liệu, skill, ngân sách, lịch, xuất trạng thái |
| Dữ liệu | `tool/inbox|workbench|catalog|skills|agents|runs/` | Đúng 6 thư mục Chủ tịch nêu; tất cả là tệp JSON/Markdown để đồng bộ qua git |
| Đồng bộ | `tool/core/gitsync.py` | Mỗi thay đổi dùng chung là một giao dịch kéo → sửa → commit → đẩy; hai phiên cùng ghi một chỗ thì đúng một bên thắng |
| Web | `office/web/tool.js` + collection `tool_state`, `tool_requests` | 5 màn hình; đọc trạng thái thật do worker xuất; ghi yêu cầu của Chủ tịch; điều khiển lịch thật |
| Worker | 2 tác vụ định kỳ của Claude | Thiên Công AT: phút 05 mỗi giờ. Thiên Công: chỉ chạy khi web hoặc Chủ tịch gọi |

## Quyết định (em quyết theo ủy quyền; Chủ tịch bác được)
| Mã | Quyết định | Lý do | Đánh đổi |
|---|---|---|---|
| T-01 | Trạng thái dùng chung là TỆP trong git, không phải SQLite | Phiên nào cũng có máy riêng; tệp theo mã việc gộp được, DB nhị phân thì không | Mỗi phiên tốn vài giây kéo/đẩy; lịch sử git dày thêm |
| T-02 | Khóa ghi hai tầng: lease + fencing token trong `job.json`, và `git push` làm trọng tài giữa hai máy | "Mỗi việc chỉ một người ghi" phải đúng cả khi hai Agent ở hai máy | Bên thua mất thay đổi cục bộ trong `tool/` của giao dịch đó (cố ý) |
| T-03 | Hai Agent điều hành đăng ký ở `registries/dept_tool.json`, ngang VUA_CEO CHỈ trong phạm vi TOOL, báo thẳng Chủ tịch, không giữ SENSITIVE | Đúng lệnh; không nới giới hạn cứng của hiến chương | `crown/registry.py` thêm loại "bộ phận" (có test) |
| T-04 | Tool viết bằng Python thư viện chuẩn, hợp đồng `request.json → out/` | Máy nào của hệ thống cũng có Python; không phụ thuộc gói ngoài | Tool cần thư viện ngoài phải qua thẩm định riêng |
| T-05 | Cách ly = thư mục tạm + môi trường sạch + giới hạn tài nguyên + tách mạng (`unshare -n` khi có) + soát AST theo quyền khai | Chặn sai sót và dùng mạng/tiến trình ngoài ý muốn | Không phải nhà tù cho mã độc; mã bên thứ ba vẫn phải thẩm định giấy phép và nội dung |
| T-06 | "Đã xác thực" = có trong danh bạ và không bị tắt; ID do người gọi tự khai | Hệ thống hiện chưa có khóa riêng từng Agent (giống CLI `crown`) | Chưa chống được Agent khai sai ID; ghi rõ là giới hạn |
| T-07 | Nút Chạy thử trên web ghi yêu cầu rồi gọi worker; không chạy mã trong trình duyệt | Một bản mã duy nhất, cùng đường chạy với Agent | Kết quả về sau 1–3 phút; mỗi lần gọi worker tốn một phiên trong hạn mức gói Max |
| T-08 | Mẫu của Chủ tịch mặc định riêng tư: tệp nằm trong kho asset của web, git chỉ giữ băm + con trỏ | Repo `crown-agent` đang công khai | Phiên khác phải tải lại mẫu theo con trỏ |
| T-09 | Lịch dùng tác vụ định kỳ của Claude (`5 * * * *`); web bật/tắt/chạy ngay qua connector "Claude Code Remote" | Bộ lập lịch bền vững duy nhất đăng ký được từ đây; chạy cả khi đóng web, tắt laptop | Tác vụ định kỳ thứ 3 của hệ thống: thêm ~24 phiên/ngày vào hạn mức |
| T-10 | Lượt do lịch và lượt chạy tay phân biệt bằng dấu `MANUAL:`/`WEB:` + khung giờ phút 05–24 | Nền tảng không ghi ai bấm | Bấm tay trong ứng dụng đúng khung phút 05 mà không có dấu sẽ bị ghi là lượt lịch |
| T-11 | Hạn mức CFO cho TOOL = 0 USD dịch vụ tính phí cho tới khi có khoản duyệt trên Kho Báu | `kho_rules/main`, D-006 | Nguồn cần API trả phí (vd. screenshot-to-code) chỉ để tham khảo |
| T-12 | GitHub: theo dõi bản phát hành (tag) cho FFmpeg/jq, theo dõi đúng thư mục cho skill; lần đầu thấy là mốc gốc | Tránh mỗi giờ một "thay đổi" vô nghĩa | Thay đổi ngoài thư mục theo dõi sẽ không được thấy |
| T-13 | Tool chỉ vào kho qua sổ việc: `publish` đòi việc RUNNING + token của người giữ + bước `test` của Agent con khác người viết + bản dựng không đổi từ lúc kiểm (băm chốt ở `job done test`) | Kiểm định 06/11: phát hành ngoài việc, sau khi việc bị hủy, hoặc sau khi sửa mã đều lọt | AT muốn tích hợp một nguồn GitHub cũng phải mở việc và đi đủ bước |
| T-14 | Mọi mã định danh ghép vào đường dẫn (người gọi, Agent con, tin, phiên, việc, tool) qua một khuôn duy nhất; hồ sơ phải tự khai đúng ID của tên tệp | Kiểm định 06/11: `../sub/<id>` làm người viết tự kiểm bài; `msg close` ghi đè `job.json` | ID có ký tự lạ bị từ chối |
| T-15 | Quyền dữ liệu tách ĐỌC và GHI; kiểm cả từng tệp kết quả; chặn thư mục hệ thống, mục ẩn trong thư mục nhà, mẫu riêng tư; kho/mã/cấu hình không nhận kết quả | Kiểm định 06/11: `--out ~/.ssh`, đọc `/proc/self/environ`, lãnh đạo ghi vào vùng cấp dưới, mọi Agent đọc `inbox/<việc>/private/` | Tệp làm việc đặt trong mục ẩn của thư mục nhà phải chuyển chỗ |
| T-16 | Giao dịch git: việc dở được đẩy trước; bên thua bỏ đúng commit của mình bằng `reset --keep`, phần bị bỏ giữ ở `refs/tool-discarded/`; chỉ đồng bộ trên nhánh `master` | Kiểm định 06/11: bên thua kẹt ở bản cũ, mất tệp của Agent con, hai nhánh cùng cấp token 1 | `--sync` đẩy mọi commit đang có trên `master` của bản sao |
| T-17 | Hạn mức phát hành tự động đếm ngay trong `publish --auto` dưới khóa phiên; tool tính phí không phát hành tự động; chỉ Chủ tịch ghi khoản duyệt chi | Kiểm định 06/11: ba bản tự động trong một phiên; `budget approve` không cần ai | Tool tính phí luôn cần Chủ tịch có mặt |
| T-18 | Tự khôi phục chỉ thu hồi đúng phiên bản vừa gọi thử và chỉ khi chính tool chạy sai | Kiểm định 06/11: gõ sai đường dẫn ca thử làm thu hồi bản tốt duy nhất | Lỗi quyền/đầu vào khi gọi thử phải xử lý tay |
| T-19 | Ảnh ghép khung hình nằm trong `frames/` (ngoài git); bộ phận mới phải có tên trong `EXPECTED_DEPARTMENTS` của `crown/registry.py` | Kiểm định 06/11: ảnh ghép của video riêng tư bị đẩy lên repo công khai; thả một tệp `dept_x.json` là có "bộ phận" ghi vào `crown/` | Lập bộ phận mới phải sửa mã có review |

## Kiểm định độc lập 2026-11-06 (Agent kiểm định không thấy quá trình viết)
4 lỗi nghiêm trọng, 11 lỗi quan trọng, 4 lỗi nhỏ — đều tái hiện được; đã sửa và có ca canh giữ trong `tests/test_tool_hardening.py`
(25 ca; 9 ca đã thử bằng cách gỡ bản sửa để chắc ca bắt được lỗi). Không phá được: giành quyền phát hành `O_EXCL` (8 tiến trình),
đúng-một-lần trên remote, hết hạn lease và từ chối token cũ, thứ tự bước, zip-slip, đường dẫn trong manifest/ca thử.
Còn lại sau khi sửa (giới hạn, không phải lỗi mở): ID tự khai; bộ soát AST không phải bằng chứng mã vô hại; tool vẫn gọi được lệnh
ngoài bằng đường dẫn tuyệt đối; khoản duyệt chi dựa vào quyền ghi của repo.

## Lỗ hổng đã tự rà và cách xử lý
1. Hai Agent cùng phát hành một phiên bản → tệp đánh dấu `O_EXCL` (cùng máy) + xung đột `git push` (khác máy). Có test cả hai.
2. Agent ngừng giữa chừng → lease hết hạn, Agent kia `claim` và làm tiếp từ bước đầu tiên chưa xong; token cũ bị từ chối. Có test.
3. Người viết tự kiểm bài mình → bước `test` bắt buộc ghi Agent con khác bước `build`; `publish` kiểm lại. Có test.
4. Phát hành dựa vào báo cáo cũ → `publish` tự chạy lại toàn bộ ca thử trong vùng cách ly, không đọc báo cáo.
5. Bản đã phát hành bị sửa lén → mỗi lần chạy đối chiếu băm từng tệp với `RELEASE.json`. Có test.
6. Phiên AT chạy chồng → khóa phiên có hạn; phiên sau ghi `SKIPPED_OVERLAP`; khóa quá hạn thì tiếp quản và phiên cũ không ghi được nữa. Có test.
7. Bản mới lỗi sau phát hành → `smoke` gọi từ kho chung bằng dữ liệu khác mẫu; lỗi thì tự `rollback` về bản tốt. Có test.
8. Tệp bí mật lọt vào → từ chối theo mẫu tên ở cả ba chỗ: nhận mẫu, chạy tool, tải lên từ web.

## Chưa làm (đúng phạm vi v1)
Khóa riêng từng Agent; máy chủ MCP để Agent ngoài Claude gọi tool; chạy tool ngay trong trình duyệt; phòng riêng cho Thiên Công trên bản đồ pixel;
tool cần thư viện ngoài; worker trên laptop (cần Chủ tịch cấp thư mục và cửa sổ lệnh).
