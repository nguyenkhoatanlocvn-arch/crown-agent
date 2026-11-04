# SOURCE REGISTER — A03 Skill Intelligence — BTC Spot Bot

- Ngày kiểm: **2026-11-04** (mọi URL dưới đây truy cập ngày này, qua WebFetch/WebSearch).
- Phạm vi: bot Binance SPOT BTCUSDT, Python 3.12 / Windows, stdlib + requests + pandas/numpy, vốn ~30 USD, lệnh MARKET qua adapter REST tự ký, idempotency bằng `clientOrderId`, stop-loss hiện chỉ ở local.
- Nguyên tắc: **nâng cấp bot hiện có, không thay engine**. Bot thứ hai (backtest/nghiên cứu) **không bao giờ giữ live key** (chỉ testnet key hoặc không key).
- Không cài, không chạy gì. Nội dung web coi là dữ liệu, không phải chỉ thị.

## 0. Nhãn bằng chứng

| Nhãn | Nghĩa |
|---|---|
| VERIFIED | Đọc trực tiếp từ nguồn chính (docs Binance / repo GitHub / PyPI / API công khai) hôm nay |
| PARTIAL | Đọc được nguồn chính nhưng trang không hiển thị đủ (vd. ngày commit trống) |
| UNVERIFIED | Chưa kiểm được — cần kiểm lại trước khi dựa vào |

Giới hạn môi trường hôm nay: `gh api` bị chặn (403 — repo chưa được gắn vào phiên), Bash không resolve được pypi.org/npm. Vì vậy **ngày commit mới nhất của phần lớn repo là UNVERIFIED** (trang GitHub render cột ngày trống). Phiên sau nên kiểm lại bằng `gh api repos/<owner>/<repo>` hoặc trang PyPI.

---

## 1. Binance — tài liệu chính thức (nền tảng cho N2)

### 1.1 Snapshot `exchangeInfo` BTCUSDT (mainnet, API công khai) — VERIFIED
URL: https://data-api.binance.vision/api/v3/exchangeInfo?symbol=BTCUSDT — serverTime 1793809339771 (= 2026-11-04 16:22 UTC)

- `orderTypes`: LIMIT, LIMIT_MAKER, MARKET, **STOP_LOSS**, **STOP_LOSS_LIMIT**, TAKE_PROFIT, TAKE_PROFIT_LIMIT → **STOP_LOSS (kích hoạt lệnh MARKET) được phép trên spot BTCUSDT.**
- `ocoAllowed` true, `otoAllowed` true, `opoAllowed` true, `allowTrailingStop` true, `cancelReplaceAllowed` true, `amendAllowed` true, `quoteOrderQtyMarketAllowed` true.
- STP mặc định `EXPIRE_MAKER`; cho phép EXPIRE_TAKER / EXPIRE_MAKER / EXPIRE_BOTH / DECREMENT / TRANSFER.
- Filters:
  - PRICE_FILTER tickSize **0.01**
  - LOT_SIZE minQty 0.00001, stepSize **0.00001**
  - MARKET_LOT_SIZE maxQty 117.22 (stepSize 0 = không ràng buộc riêng)
  - NOTIONAL **minNotional 5 USDT**, applyMinToMarket **true**, maxNotional 9,000,000, applyMaxToMarket false, avgPriceMins 5
  - PERCENT_PRICE_BY_SIDE: bidUp 1.2, bidDown 0.5, **askUp 2, askDown 0.8**, avgPriceMins 5 → giá lệnh SELL phải ≥ 0.8 × giá tham chiếu
  - TRAILING_DELTA: 10–2000 BIPS (cả above/below)
  - **MAX_NUM_ALGO_ORDERS 5**, MAX_NUM_ORDERS 200, MAX_NUM_ORDER_LISTS 20, MAX_NUM_ORDER_AMENDS 10, ICEBERG_PARTS 100
- Lưu ý: giá trị filter có thể thay đổi — bot phải đọc `exchangeInfo` lúc khởi động, không hard-code.

### 1.2 Trading endpoints — VERIFIED
URL: https://developers.binance.com/docs/binance-spot-api-docs/rest-api/trading-endpoints

- `POST /api/v3/order`:
  - STOP_LOSS: bắt buộc `quantity` + (`stopPrice` hoặc `trailingDelta`); "Triggers a MARKET order".
  - STOP_LOSS_LIMIT: thêm `timeInForce`, `price`.
  - Hướng kích hoạt: "Price below market price: STOP_LOSS SELL" → stopPrice của SELL phải **dưới** giá thị trường.
  - `newClientOrderId`: phải duy nhất trong số lệnh đang mở; "Orders with the same newClientOrderID can be accepted only when the previous one is filled" → **luôn sinh ID mới cho mỗi lần đặt** (vd. kèm số attempt).
  - `newOrderRespType` ACK/RESULT/FULL; MARKET/LIMIT mặc định FULL, loại khác mặc định **ACK** → với lệnh stop nên truyền `RESULT` hoặc `FULL` để nhận `orderId`/`status` ngay.
- Order list:
  - `POST /api/v3/orderList/oco`: `aboveType` ∈ {STOP_LOSS_LIMIT, STOP_LOSS, LIMIT_MAKER, TAKE_PROFIT, TAKE_PROFIT_LIMIT}; `belowType` ∈ {STOP_LOSS, STOP_LOSS_LIMIT, TAKE_PROFIT, TAKE_PROFIT_LIMIT}; `quantity` dùng chung cho 2 chân; SELL: giá LIMIT_MAKER > last price > stopPrice. OCO cộng 2 lệnh vào MAX_NUM_ORDERS.
  - OTO / OTOCO / OPO / OPOCO tồn tại (OPO/OPOCO mở cho mọi symbol từ changelog 2025-12-02). OTO: "The pending order is only placed on the order book when the working order gets fully filled." Working leg của OTO là LIMIT/LIMIT_MAKER → **không dùng được với entry MARKET** của bot hiện tại.
  - "Canceling an individual order from an order list will cancel the entire order list."
- `DELETE /api/v3/order` hỗ trợ `cancelRestrictions` = ONLY_NEW / ONLY_PARTIALLY_FILLED.
- `POST /api/v3/order/cancelReplace`: `cancelReplaceMode` **STOP_ON_FAILURE** ("If the cancel request fails, the new order placement will not be attempted") hoặc ALLOW_FAILURE; `cancelOrderId`/`cancelOrigClientOrderId`; `orderRateLimitExceededMode`.
- `PUT /api/v3/order/amend/keepPriority`: **chỉ giảm số lượng** (newQty < qty cũ) — không đổi được giá → không dùng để dời stop.
- `POST /api/v3/order/test`: kiểm tra lệnh nhưng không đưa vào matching engine.

### 1.3 Filters — VERIFIED
URL: https://developers.binance.com/docs/binance-spot-api-docs/filters
- MAX_NUM_ALGO_ORDERS tính các loại: STOP_LOSS, STOP_LOSS_LIMIT, TAKE_PROFIT, TAKE_PROFIT_LIMIT.
- MAX_NUM_ORDERS: "both "algo" orders and normal orders are counted".
- NOTIONAL với lệnh không có giá: dùng reference price nếu có, nếu không dùng VWAP `avgPriceMins` phút.
- PERCENT_PRICE_BY_SIDE: SELL dùng askMultiplierUp/Down. **Docs không nói rõ filter có kiểm `stopPrice` hay kiểm lại lúc trigger** → UNVERIFIED, phải thử trên testnet.

### 1.4 Trailing stop FAQ — VERIFIED
URL: https://developers.binance.com/docs/binance-spot-api-docs/faqs/trailing-stop-faq
- `trailingDelta` đơn vị BIPS (100 = 1%). Có `stopPrice` → chỉ bắt đầu theo dõi khi điều kiện stopPrice thỏa; không có `stopPrice` → theo dõi từ trade kế tiếp; trigger khi giá giảm `delta` từ **giá cao nhất kể từ khi đặt**.

### 1.5 Changelog Spot API — VERIFIED (mục mới nhất 2026-10-30)
URL: https://developers.binance.com/docs/binance-spot-api-docs
- **2025-12-17**: payload REST có chữ ký phải **percent-encode trước khi ký**, áp dụng từ ~2026-01-15 07:00 UTC (testnet trước). → **Kiểm tra adapter hiện tại** (rủi ro -1022 với tham số có ký tự đặc biệt).
- **2026-01-21**: REST `POST/PUT/DELETE /api/v3/userDataStream` và WS `userDataStream.start/ping/stop` (listenKey) **ngừng từ 2026-02-20 07:00 UTC**.
- 2025-08-12: `userDataStream.subscribe.signature` — subscribe không cần login, **dùng được với mọi loại API key**; MAX_NUM_ORDER_LISTS 20, MAX_NUM_ORDER_AMENDS 10.
- 2026-03-09: `executionReport` có trường tùy chọn `eR` (lý do expire, vd. `INSUFFICIENT_LIQUIDITY`).
- 2026-05-06: PERCENT_PRICE, PERCENT_PRICE_BY_SIDE, MIN_NOTIONAL, NOTIONAL dùng **reference price** khi có.
- 2026-03-27/04-02: một số lệnh place/cancel thành công có weight 0 (chi tiết có điều kiện).

### 1.6 API key restrictions — VERIFIED
URL: https://developers.binance.com/docs/wallet/account/api-key-permission
- `GET /sapi/v1/account/apiRestrictions` (USER_DATA, signed, IP weight 1; params `timestamp`, `recvWindow` ≤ 60000).
- Trường trả về: `ipRestrict`, `createTime`, `enableReading`, `enableWithdrawals`, `enableInternalTransfer`, `enableMargin`, `enableFutures`, `permitsUniversalTransfer`, `enableVanillaOptions`, `enableFixApiTrade`, `enableFixReadOnly`, `enableSpotAndMarginTrading`, `enablePortfolioMarginTrading`.
- Đề xuất gate khởi động live (thiết kế, không phải trích docs): chỉ chạy khi `ipRestrict=true`, `enableWithdrawals=false`, `enableInternalTransfer=false`, `permitsUniversalTransfer=false`, `enableMargin=false`, `enableFutures=false`, `enableVanillaOptions=false`, `enablePortfolioMarginTrading=false`, `enableSpotAndMarginTrading=true`. Sai một trường → dừng, ghi log, không đặt lệnh.

### 1.7 Spot Testnet — VERIFIED một phần
URL: https://developers.binance.com/docs/binance-spot-api-docs/testnet/rest-api/trading-endpoints
- REST: "The base endpoint is https://testnet.binance.vision/api".
- Key: "API keys can be created on the SPOT Test Network upon logging in with your Github account"; "By default, an API key cannot `TRADE`. You need to enable trading in API Management first." RSA chỉ PKCS#8.
- WS API testnet: `wss://ws-api.testnet.binance.vision:443/ws-api/v3` (từ trang User Data Stream requests). Có thêm môi trường Demo `wss://demo-ws-api.binance.com:443/ws-api/v3`.
- UNVERIFIED: chính sách reset số dư/key; `exchangeInfo` testnet (WebFetch tới testnet.binance.vision trả client_error hôm nay) → filter testnet có thể khác mainnet, phải đọc lúc chạy thử.

### 1.8 User data stream (fills) — VERIFIED
URL: https://developers.binance.com/docs/binance-spot-api-docs/websocket-api/user-data-stream-requests ; https://developers.binance.com/docs/binance-spot-api-docs/user-data-stream
- Prod WS API: `wss://ws-api.binance.com:443/ws-api/v3`.
- `userDataStream.subscribe`: "requires an authenticated WebSocket connection using Ed25519 keys" (qua `session.logon`).
- `userDataStream.subscribe.signature`: signed (timestamp, recvWindow) — theo changelog dùng được với mọi loại key (HMAC hiện tại vẫn được).
- Sự kiện: `executionReport`, `listStatus` (cho order list), `outboundAccountPosition`, `balanceUpdate`, `eventStreamTerminated`, `externalLockUpdate`. Giá khớp TB = `Z / z`.
- Tác động cho bot: listenKey đã chết → nếu muốn stream phải thêm thư viện WebSocket (phá nguyên tắc chỉ stdlib+requests). **Giai đoạn N2 đề xuất polling REST** (`GET /api/v3/order` theo `origClientOrderId`, `GET /api/v3/openOrders`, `GET /api/v3/myTrades`); WS là RESEARCH cho giai đoạn sau.

---

## 2. Bảng ứng viên

| # | Ứng viên | URL | Owner | Phiên bản / ngày | License (chính xác) | Bảo trì | Quyền cần | Phụ thuộc | Windows | Chi phí | Lợi ích cho bot NÀY | Rủi ro | Quyết định |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | ccxt/ccxt | https://github.com/ccxt/ccxt , https://github.com/ccxt/ccxt/releases | ccxt (org) | **v4.5.90 — 23 Oct** (năm không hiển thị; suy ra 2026), phát hành gần như hằng tuần — VERIFIED/PARTIAL | MIT | Rất tích cực | Live key nếu giao dịch; public data không cần | Gói lớn (đa sàn; dependency Python chưa đọc được — UNVERIFIED) | Có (Python 3) | Miễn phí | Tham khảo mẫu reconcile/fetchOrder, unified error; có thể dùng read-only để đối chiếu | Thay adapter = thay engine; lớp trừu tượng che chi tiết Binance (STOP_LOSS, cancelReplace, percent-encode); README có link referral (bỏ qua) | **RESEARCH** — chỉ đọc làm tham khảo, không thay adapter tự viết |
| 2 | freqtrade/freqtrade | https://github.com/freqtrade/freqtrade/releases | freqtrade (org) | **2026.9 — 29 Sep 2026** (2026.8 31 Aug, 2026.7 31 Jul) — VERIFIED | GPL-3.0 (trang release không hiển thị; GPL-3.0 xác nhận gián tiếp qua repo strategies cùng org — PARTIAL) | Rất tích cực, phát hành hàng tháng; hỗ trợ Python 3.14 | Không key cho backtest; live key nếu chạy live (CẤM với bot thứ hai) | Nặng (TA-Lib, ccxt, SQLAlchemy…) | Có nhưng tài liệu ưu tiên Docker/Linux | Miễn phí | Phương pháp **lookahead-analysis** và **recursive-analysis** để kiểm chiến lược | GPL-3.0: chép code vào bot → phải GPL bot; engine khác hoàn toàn | **RESEARCH** — dùng như công cụ kiểm tra độc lập không key, hoặc tự viết lại ý tưởng trong test harness của bot |
| 2a | Freqtrade lookahead-analysis | https://www.freqtrade.io/en/stable/lookahead-analysis/ | freqtrade | docs stable | (docs) | — | — | — | — | — | So sánh backtest toàn bộ vs backtest cắt tại từng tín hiệu; phát hiện `shift(-n)`, `iloc`, `.mean()` không giới hạn | Chỉ kiểm tín hiệu thực sự kích hoạt (false negative) | **ADAPT** — viết lại test "cắt dữ liệu tại t, tính lại chỉ báo, so sánh" trong bot (không copy code) |
| 2b | Freqtrade recursive-analysis | https://www.freqtrade.io/en/stable/recursive-analysis/ | freqtrade | docs stable | (docs) | — | — | — | — | — | So chỉ báo ở dòng cuối với các startup candle 199/499/999/1999; Binance giới hạn ~4999 nến | Chỉ so dòng cuối | **ADAPT** — thêm test warm-up: EMA/RSI với N nến khác nhau phải lệch < ngưỡng |
| 3 | freqtrade/freqtrade-strategies | https://github.com/freqtrade/freqtrade-strategies | freqtrade | Ngày commit UNVERIFIED | GPL-3.0 | Có hoạt động (12 issue mở) | — | freqtrade ≥ 2022.4 | — | Miễn phí | Ý tưởng chiến lược | "These strategies are for educational purposes only"; overfit; GPL | **REJECT** cho live; chỉ đọc ý tưởng |
| 4 | TA-Lib/ta-lib-python | https://github.com/TA-Lib/ta-lib-python | TA-Lib (org) | Nhánh 0.8.x (đề cập 0.8.3, C lib 0.8.1); ngày UNVERIFIED | BSD-2-Clause | Đang duy trì nhiều nhánh, CI | Không | numpy 2 (0.5.x–0.8.x) | **Có wheel Windows x86_64/x86/arm64, Python 3.9–3.14**, "do not require a compiler or a separate TA-Lib C installation" | Miễn phí | Chỉ báo chuẩn, nhanh | Thêm dependency nhị phân; pandas/numpy tự viết đủ cho vài chỉ báo | **RESEARCH** (tùy chọn) — chỉ thêm nếu cần >5 chỉ báo; ghim phiên bản |
| 5 | tradingview/lightweight-charts | https://github.com/tradingview/lightweight-charts | TradingView | Phiên bản/ngày UNVERIFIED (trang Releases trống khi đọc) | Apache-2.0 + yêu cầu ghi công: "This license requires specifying TradingView as the product creator" (NOTICE + link tradingview.com, có option `attributionLogo`) | Tích cực (3.4k commit) | Không | JS, không phụ thuộc runtime đáng kể | Chạy trong trình duyệt | Miễn phí | Biểu đồ nến + marker lệnh cho web quản trị/dashboard read-only | Quên attribution = vi phạm license | **ADOPT** cho dashboard (không chạm bot), bật `attributionLogo` + NOTICE |
| 6 | polakowo/vectorbt | https://github.com/polakowo/vectorbt/blob/master/LICENSE.md | Oleg Polakow | UNVERIFIED | **Apache 2.0 with Commons Clause** — "the License does not grant to you, the right to Sell the Software"; "Sell" = cung cấp cho bên thứ ba sản phẩm/dịch vụ có giá trị "entirely or substantially" từ phần mềm | Có | Không | numba, pandas, plotly… | Có | Miễn phí (bản PRO trả phí) | Backtest vector hóa nhanh, walk-forward | Không phải OSI open source; mô hình holdings/venture studio có thể bán dịch vụ → xung đột | **REJECT** cho hệ thống CROWN AGENT (license); dùng cá nhân thuần túy cần Chủ tịch quyết |
| 7 | binance/binance-connector-python | https://github.com/binance/binance-connector-python | Binance (chính thức) | Version/ngày UNVERIFIED; repo đã **chuyển sang mô-đun** (`binance-sdk-spot`), có Migration Guide | MIT | Chính thức, sinh code bằng OpenAPI Generator | Live key nếu dùng | Python ≥ 3.10 | Có | Miễn phí | Tài liệu tham chiếu cách ký (HMAC/RSA/Ed25519, percent-encode), tên tham số order list | Thay adapter = thay engine; code sinh tự động, nhiều phụ thuộc | **RESEARCH** — dùng làm "oracle" đối chiếu chữ ký/tham số khi viết test cho adapter; không thay adapter |
| 8 | sammchardy/python-binance | https://github.com/sammchardy/python-binance | cá nhân (unofficial) | v1.0.37; ngày UNVERIFIED | MIT | 506 issue mở, 27 PR | Live key | — | Có | Miễn phí | Ít | "unofficial Python wrapper"; có thể chậm theo thay đổi API (listenKey retire, percent-encode) | **REJECT** — đã có adapter riêng; nguồn chính thức tốt hơn |
| 9 | pandas-ta (twopirllc) | https://pypi.org/project/pandas-ta/ , https://github.com/twopirllc/pandas-ta | twopirllc | **0.4.71b0 (pre-release) — 2025-09-14**; Requires-Python ≥ 3.12; "4 - Beta" | UNVERIFIED (PyPI chỉ link pandas-ta.dev/legal/license) | Không rõ; trang GitHub đọc được là snapshot cũ; nhiều fork (pandas-ta-classic, -openbb, -kozmoai) → dấu hiệu phân mảnh | Không | numba + numpy | Có | Miễn phí | Nhiều chỉ báo | Pre-release, license chưa xác nhận, numba nặng | **REJECT** (hiện tại) — tự viết chỉ báo bằng pandas, có unit test |
| 10 | Binance Spot API docs (mục 1) | developers.binance.com | Binance | changelog mới nhất 2026-10-30 | (docs) | Chính thức | API key: Spot trading, không withdrawal, IP whitelist | — | — | Miễn phí (rate limit) | Nền tảng của N2 | Thay đổi thường xuyên → theo dõi changelog | **ADOPT** — nguồn chuẩn duy nhất cho adapter |

Chưa đánh giá (hết ngân sách tool call, để phiên sau nếu cần): nautechsystems/nautilus_trader, hummingbot/hummingbot, jesse-ai/jesse, bukosabino/ta. Không ứng viên nào trong số này được kỳ vọng tốt hơn việc nâng cấp adapter hiện tại cho bot 30 USD.

---

## 3. N2 — Exchange-side stop (khuyến nghị)

### 3.1 Cơ chế chọn
**Một lệnh `STOP_LOSS` SELL độc lập (kích hoạt MARKET), đặt ngay sau khi lệnh BUY MARKET được xác nhận khớp; bot thoát bằng `cancelReplace` STOP_ON_FAILURE.**

Lý do:
1. `STOP_LOSS` có trong `orderTypes` của BTCUSDT (mục 1.1) và "Triggers a MARKET order" (1.2) → **thoát chắc chắn** khi giá xuyên stop; với 30 USD trên BTCUSDT, trượt giá MARKET là nhỏ so với rủi ro STOP_LOSS_LIMIT không khớp khi giá rơi nhanh (lệnh limit nằm lại trên sổ, vị thế mất bảo vệ).
2. Không dùng OCO làm mặc định: logic chốt lời của bot là tín hiệu local, không phải giá TP cố định; OCO buộc thêm chân LIMIT_MAKER phía trên. OCO (`aboveType=LIMIT_MAKER`, `belowType=STOP_LOSS`) là **phương án B** nếu sau này có TP cố định.
3. Không dùng OTO/OTOCO: working leg phải LIMIT/LIMIT_MAKER (1.2), không khớp với entry MARKET hiện tại; đổi entry sang limit là thay đổi chiến lược → để RESEARCH.
4. Trailing (`trailingDelta`, BIPS, 10–2000) để **RESEARCH** — chỉ bật sau khi stop cố định chạy ổn trên testnet.
5. Đặt lệnh SELL làm **khóa (locked) BTC** — đây là chốt chặn tự nhiên chống bán hai lần: bot không thể MARKET SELL số BTC đang bị stop khóa.

### 3.2 Tham số đặt stop (`POST /api/v3/order`)
```
symbol=BTCUSDT
side=SELL
type=STOP_LOSS
quantity=Q_sell
stopPrice=P_stop
newClientOrderId=<prefix>-sl-<tradeId>-<attempt>     # duy nhất mỗi lần đặt (≤36 ký tự, chỉ [A-Za-z0-9-_])
newOrderRespType=RESULT                               # mặc định của STOP_LOSS là ACK
recvWindow=5000
timestamp=...
```
- `Q_sell` = số BTC **thực có và free** sau entry: `executedQty` của BUY trừ phí nếu `commissionAsset == BTC` (lấy từ `fills` trong response FULL hoặc `myTrades`), rồi **làm tròn XUỐNG** theo `stepSize` (0.00001). Đối chiếu thêm `GET /api/v3/account` (free BTC) và lấy min.
- `P_stop` = giá entry TB × (1 − SL%), làm tròn **XUỐNG** theo `tickSize` (0.01); phải < giá hiện tại (STOP_LOSS SELL "below market price").
- Kiểm trước khi gửi (dùng filter đọc lúc khởi động): `Q_sell ≥ minQty`; `Q_sell × P_stop ≥ minNotional (5 USDT)` — với 30 USD và SL vài %, dư nhiều; `P_stop ≥ askMultiplierDown × giá TB` (0.8 → SL không quá −20%); số lệnh algo mở < `maxNumAlgoOrders` (5).
- Chưa rõ (UNVERIFIED, phải thử testnet): NOTIONAL/PERCENT_PRICE_BY_SIDE có kiểm lại tại thời điểm trigger hay không; phần dư "dust" < 5 USDT không bán được.
- Thử nghiệm khô trước bằng `POST /api/v3/order/test` với cùng tham số.

### 3.3 Khi bot tự thoát (tín hiệu local)
Dùng **`POST /api/v3/order/cancelReplace`**:
```
symbol=BTCUSDT
cancelReplaceMode=STOP_ON_FAILURE
cancelOrigClientOrderId=<id của stop>
cancelRestrictions=ONLY_NEW
side=SELL  type=MARKET  quantity=Q_sell
newClientOrderId=<prefix>-ex-<tradeId>-<attempt>
newOrderRespType=FULL
```
- STOP_ON_FAILURE: "If the cancel request fails, the new order placement will not be attempted" → nếu stop đã trigger/khớp ngay trước đó, **không** đặt MARKET SELL lần hai.
- Khi lỗi/timeout: **không retry mù**. Truy vấn `GET /api/v3/order?origClientOrderId=` cho cả stop và lệnh exit, rồi quyết định theo trạng thái thực (reconcile).
- Dời stop (vd. lên hòa vốn): cũng dùng cancelReplace STOP_ON_FAILURE, lệnh mới là STOP_LOSS với stopPrice mới. **Không** dùng `amend/keepPriority` (chỉ giảm qty).

### 3.4 Xử lý khớp một phần
| Tình huống | Xử lý |
|---|---|
| BUY MARKET khớp một phần (status EXPIRED, `executedQty>0`, có thể `eR`) | Đặt stop cho đúng `executedQty` thực nhận (trừ phí); không mua bù trong cùng tín hiệu |
| Stop trigger, lệnh MARKET khớp một phần rồi EXPIRED | Poll thấy `executedQty < origQty` → tính BTC free còn lại; nếu ≥ minQty và notional ≥ 5 USDT → MARKET SELL phần còn lại (ID mới); nếu nhỏ hơn → ghi DUST, cảnh báo, đánh dấu vị thế đóng |
| Exit MARKET qua cancelReplace khớp một phần | Như trên; nếu còn đủ lớn và bot vẫn muốn giữ → đặt lại stop cho phần còn lại |
| (Phương án STOP_LOSS_LIMIT, nếu dùng) phần còn lại nằm trên sổ | cancelReplace sang MARKET cho phần còn lại — lý do chính khiến N2 chọn STOP_LOSS |

### 3.5 Đối chiếu (reconcile) — chạy khi khởi động và mỗi vòng
1. `GET /api/v3/openOrders?symbol=BTCUSDT` + `GET /api/v3/account` (free/locked BTC).
2. Bất biến: **có BTC ≥ minQty ⇔ có đúng 1 stop SELL mở cho đúng số lượng đó**.
   - Có BTC, không có stop → đặt stop ngay (hoặc kill switch nếu đặt thất bại 2 lần).
   - Có stop, không có BTC/vị thế → hủy stop.
   - >1 stop → hủy thừa, giữ cái khớp số lượng.
3. Phát hiện fill: polling `GET /api/v3/order` theo `origClientOrderId` (stdlib + requests, không thêm dependency). WebSocket `userDataStream.subscribe.signature` để giai đoạn sau (listenKey đã ngừng 2026-02-20).
4. Lãi/lỗ chỉ tính từ `myTrades` đã đối chiếu (giá, qty, commission, commissionAsset).

### 3.6 Kế hoạch test trên Spot Testnet (key testnet riêng, không bao giờ dùng live key)
Môi trường: REST `https://testnet.binance.vision/api`; key tạo bằng đăng nhập GitHub, bật quyền TRADE trong API Management.
Ghi PASS/FAIL/NOT_RUN cho từng bước; hiện tại tất cả **NOT_RUN**.

| # | Test | Kỳ vọng |
|---|---|---|
| T0 | Đọc `exchangeInfo` BTCUSDT testnet, so với mainnet | Có STOP_LOSS, cancelReplaceAllowed; ghi khác biệt filter |
| T1 | `order/test` STOP_LOSS SELL hợp lệ; và các ca sai: qty lẻ stepSize, stopPrice lẻ tick, stopPrice > giá hiện tại, notional < 5 | Hợp lệ → `{}`; ca sai → đúng mã lỗi filter |
| T2 | Kiểm chữ ký percent-encode với `newClientOrderId` có `-`/`_` | Không lỗi -1022 |
| T3 | BUY MARKET nhỏ → đặt STOP_LOSS cho qty sau phí | Stop NEW, BTC chuyển sang locked |
| T4 | Đặt lại cùng `newClientOrderId` khi stop đang mở | Bị từ chối (idempotency) |
| T5 | Thử MARKET SELL thường khi stop đang khóa BTC | Bị từ chối do thiếu số dư (chốt chặn bán đôi) |
| T6 | cancelReplace STOP_ON_FAILURE stop→MARKET | Stop CANCELED, exit FILLED, BTC ≈ 0 |
| T7 | cancelReplace với stop đã bị hủy/khớp trước | Cancel lỗi, **không** có lệnh mới |
| T8 | Dời stop bằng cancelReplace STOP_LOSS→STOP_LOSS | Đúng 1 stop mở với stopPrice mới |
| T9 | Stop sát giá để trigger thật | Lệnh FILLED (hoặc partial + xử lý 3.4), poll phát hiện trong ≤ N giây |
| T10 | Tắt bot giữa chừng (sau BUY, trước stop) rồi bật lại | Reconcile đặt stop bù |
| T11 | Mất mạng/timeout khi cancelReplace | Không retry mù; query trạng thái; không bán đôi |
| T12 | Lệnh không nên kích hoạt: không có vị thế | Bot không đặt stop nào |

Tiêu chí hoàn thành N2: T0–T12 PASS có log/ảnh response lưu lại; sau đó chạy live tối thiểu với 1 vị thế, kiểm `apiRestrictions` gate (1.6) trước khi bật.

---

## 4. Ghi chú thêm (thiết kế, không phải trích nguồn)
- **Walk-forward**: chia dữ liệu theo thời gian (train→test kế tiếp, cuộn), không xáo trộn; kết hợp test lookahead/recursive kiểu Freqtrade (mục 2a/2b) tự viết lại.
- **Kill switch**: file cờ / lệnh dừng → hủy mọi lệnh mở không phải stop, giữ stop sàn; tùy chọn "flatten" = cancelReplace stop→MARKET. Tự kích hoạt khi: reconcile lệch, `apiRestrictions` sai, lỗi chữ ký liên tiếp, drawdown ngày vượt ngưỡng.
- **Bot thứ hai** (backtest/nghiên cứu/freqtrade nếu thử): chỉ dữ liệu công khai hoặc testnet key.

## 5. Việc còn mở (UNVERIFIED)
1. Ngày commit/phiên bản mới nhất cho TA-Lib, lightweight-charts, vectorbt, binance-sdk-spot, python-binance, freqtrade-strategies (cần `gh api` hoặc PyPI).
2. License chính xác của pandas-ta bản 0.4.x.
3. Filter testnet BTCUSDT và chính sách reset testnet.
4. NOTIONAL / PERCENT_PRICE_BY_SIDE có áp tại thời điểm trigger STOP_LOSS hay không.
5. Adapter hiện tại đã percent-encode payload trước khi ký chưa (changelog 2025-12-17) — cần đọc code bot.
