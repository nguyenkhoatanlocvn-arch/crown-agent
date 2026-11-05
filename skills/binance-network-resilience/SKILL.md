---
name: binance-network-resilience
description: Use when writing or reviewing code that calls Binance (REST or WebSocket) for the CROWN BTC bot — timeouts, retry with backoff and jitter, 429/418 handling, and never re-sending an order whose result is unknown.
---

Chuyển thể ý tưởng từ `wshobson/agents` skill python-resilience (giấy phép MIT theo SKILLS_INDEX; commit 7e2fb55; chủ bản quyền chưa xác minh — ghi đúng từ tệp LICENSE trước khi phân phối lại). Quy tắc Binance lấy từ tài liệu chính thức developers.binance.com (Spot REST "Limits" và "General API Information", đọc 05/11/2026). Dùng cùng `crown-tdd-red-green`; mọi thay đổi bot làm trên bản sao.

## Retry chỉ dành cho lời gọi ĐỌC
- Chỉ bọc retry tự động cho hàm đọc (klines, ticker, depth, account, query order, openOrders, myTrades).
- CẤM `retry_if_exception_type(Exception)` hoặc bắt mọi ngoại lệ. Chỉ liệt kê lớp lỗi mạng (timeout, lỗi kết nối, DNS) và mã HTTP cụ thể.
- Không bao giờ gắn decorator retry vào hàm đặt lệnh hoặc hủy lệnh.

## Phân loại lỗi
| Tình huống | Lời gọi ĐỌC | Đặt lệnh | Hủy lệnh |
|---|---|---|---|
| Timeout / mất kết nối / DNS | Retry: backoff mũ có jitter, ≤ 5 lần VÀ ≤ 60 s tổng | KẾT QUẢ CHƯA BIẾT → quy trình tra lệnh (dưới) | Tra lệnh: còn mở thì hủy lại; đã hủy/đã khớp thì dừng |
| HTTP 429 | Dừng gửi mọi request; chờ đúng `Retry-After` giây | Như cột trái; không gửi dồn | Như cột trái |
| HTTP 418 (IP bị cấm tự động; thời gian cấm tăng dần với người tái phạm) | KHÔNG retry. Ghi sự cố, bot về CHỜ đến hết `Retry-After`, báo người vận hành | Như cột trái | Như cột trái |
| HTTP 5XX hoặc -1007 TIMEOUT (Binance: trạng thái thực thi CHƯA BIẾT, có thể đã thành công) | Retry như lỗi mạng | KHÔNG gửi lại → quy trình tra lệnh | Tra lệnh trước khi hủy lại |
| 4XX khác (sai tham số, chữ ký, thiếu số dư, bị từ chối) | Không retry | Không retry | Không retry |
| Lỗi timestamp/`recvWindow` | Đồng bộ giờ với server một lần, thử lại một lần; giữ `recvWindow` ≤ 5000 ms (khuyến nghị của Binance) | Tra lệnh trước khi thử lại | Tra lệnh trước khi thử lại |
| `ValueError`, `TypeError`, lỗi lập trình | Không retry — là bug | Không retry | Không retry |

## Quy trình tra lệnh khi kết quả chưa biết
1. Mỗi lệnh có `newClientOrderId` riêng, sinh và ghi xuống đĩa TRƯỚC khi gửi.
2. Tra `GET /api/v3/order` với `origClientOrderId` ít nhất 3 lần, cách nhau tăng dần (ví dụ 2 s, 5 s, 10 s); đồng thời đối chiếu `openOrders`, `myTrades` gần nhất (và User Data Stream nếu có).
3. Thấy lệnh (bất kỳ trạng thái) → dùng kết quả đó, KHÔNG gửi lệnh mới.
4. "Chắc chắn không có" chỉ khi: mọi lần tra trả "lệnh không tồn tại" VÀ `myTrades` không có giao dịch khớp tương ứng VÀ số dư không đổi theo hướng lệnh. Nếu trong khoảng đó có nạp/rút hoặc lệnh khác làm số dư thay đổi thì không kết luận được → bước 6.
5. Chỉ khi đủ điều kiện 4 mới được gửi lại, dùng CÙNG `newClientOrderId`. Lưu ý: ID chỉ được Binance coi là duy nhất trong các lệnh đang mở, nên lệnh MARKET đã khớp sẽ không chặn ID trùng — vì vậy bước 2–4 là bắt buộc, không dựa vào việc sàn từ chối.
6. Còn nghi ngờ → KHÔNG gửi; bot về CHỜ và báo người vận hành.
7. Theo dõi header `X-MBX-ORDER-COUNT-*`; tiến gần giới hạn → giảm nhịp.

## Quy tắc chung
- Mọi lời gọi mạng có timeout (ví dụ kết nối 5 s, đọc 10 s).
- Retry tách khỏi logic giao dịch, log mỗi lần ở mức WARNING kèm số lần và thời gian chờ.
- Hết trần retry → báo lỗi rõ, bot về trạng thái an toàn (không mở lệnh mới). Vị thế đang mở không bị bỏ mặc: lần khởi động/nối lại tiếp theo phải đối soát trước khi làm gì khác.
- Khởi động lại bot: đối soát lệnh dở dang (các `newClientOrderId` đã ghi đĩa nhưng chưa có kết quả) bằng quy trình tra lệnh.
- Đường hiển thị (dashboard) được dùng cache có ghi giờ; đường quyết định lệnh thì không.
- Tỷ lệ retry cao = có vấn đề gốc → `root-cause-debugging`, không nâng số lần retry.
- WebSocket: nối lại có backoff + jitter và trần số lần; heartbeat phát hiện kết nối chết; sau khi nối lại đối soát số dư/lệnh qua REST.

## Bài kiểm tra chấp nhận (viết test trước; tham số `origClientOrderId` phải được thử trên testnet trước khi dùng thật)
- 429 có `Retry-After: 3` → chờ ≥ 3 s, không gửi gì trong lúc chờ.
- 418 → không retry, trạng thái bot = CHỜ.
- Timeout khi đặt lệnh nhưng lệnh có trên sàn → không có lệnh thứ hai.
- Timeout khi đặt lệnh, tra 3 lần đều không thấy nhưng `myTrades` có khớp → không gửi lại.
- 4XX "lệnh bị từ chối" → đúng một lần gọi.
- Hàm đặt lệnh không có decorator retry (kiểm tĩnh).
