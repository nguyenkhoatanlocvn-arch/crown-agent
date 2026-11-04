# TRADER_WATCHLIST — B02_BTC (bản nghiên cứu, chỉ để QUAN SÁT)

- Ngày kiểm tra: **2026-11-04** (mọi nguồn truy cập ngày này)
- Người lập: subagent nghiên cứu (Claude), cho Chủ tịch / đơn vị B02_BTC
- Mục tiêu: 40 người/nhóm. **Kết quả thực tế: 34 dòng được đưa vào danh sách. Không bịa thêm cho đủ 40.**
- Trong 34 dòng, danh tính kiểm chứng như sau:
  - **7 dòng mức A**: thấy liên kết chéo do chính chủ đăng, ví dụ trang web của họ trỏ tới handle X, hoặc có địa chỉ on-chain công khai.
  - **13 dòng mức B**: trang của chính họ xác nhận tên hoặc vai trò nhưng không có link X, hoặc báo chí uy tín gán danh tính.
  - **14 dòng mức C**: chỉ có handle lấy từ bài "danh sách trader nên theo dõi" và chưa kiểm chứng chéo.
- Bot hiện tại chỉ giao dịch **SPOT** (Binance). Phần lớn "hiệu suất kiểm chứng được" tìm thấy lại là **perp/futures đòn bẩy cao** (Hyperliquid), nên không thể sao chép trực tiếp.

---

## 1. Tóm tắt theo tier

| Tier | Số dòng | Ghi chú chính |
|---|---|---|
| 1. VERIFIED_PERFORMANCE | 4 | Cả 4 đều là cá voi perp trên Hyperliquid. Kết quả kiểm chứng của cả 4 đều là **LỖ LỚN hoặc rủi ro cực cao**, không phải bằng chứng về kỹ năng. Địa chỉ ví do bên thứ ba (Arkham, Lookonchain) gán, phiên này **chưa tự đối chiếu on-chain** vì proxy chặn API Hyperliquid. |
| 2. OBSERVED_SIGNALS | 7 | Có đăng lệnh hoặc nhận định có thời điểm trên X, nhưng chưa ai kiểm chứng độc lập hiệu suất. |
| 3. COMMENTARY_ONLY | 20 | Phân tích, macro, on-chain, giáo dục. Nhiều người bán sản phẩm trả phí. |
| 4. UNVERIFIED/INACTIVE | 3 | Gồm 1 vault đã đóng (TVL 0), 1 tài khoản có cờ đỏ và 1 tài khoản thiếu thông tin. |
| **Tổng** | **34** | |

**Không có trader nào đạt chuẩn "hiệu suất spot BTC, kiểm chứng bởi bên thứ ba, ≥ vài tháng, sau phí, có max drawdown" mà vẫn đang hoạt động và định danh được công khai.**
- Ứng viên gần nhất là Hyperdash Vault #1 (T05): on-chain, có MDD và lợi nhuận sau phí. Nhưng đây là perp, đã đóng nạp tiền với TVL 0, và vault này sao chép các trader top leaderboard nên không độc lập.
- Quỹ Capriole (T20) và Pantera (T22) có công bố hiệu suất sau phí, nhưng đó là số **tự công bố**:
  - Pantera ghi rõ là "unaudited estimates".
  - Capriole nói quỹ được kiểm toán hàng năm, nhưng báo cáo kiểm toán không công khai.

---

## 2. Phương pháp

1. **Tạo pool ứng viên rộng hơn 40.** Nguồn gồm:
   - Tin on-chain về cá voi Hyperliquid (The Block, Cointelegraph, The Defiant, Bubblemaps).
   - Hai bài "danh sách trader": coinbound.io, cập nhật 2026-01-09; kyledoops.substack.com, 2024-11-19. Cả hai bài đều có xung đột lợi ích, vì Coinbound là agency marketing, còn Kyle Doops tự đưa mình vào danh sách.
   - Trang chủ của chính các nhà phân tích.
   - Trang trợ giúp copy-trading của các sàn.
2. **Kiểm chứng danh tính** theo 3 mức:
   - **A**: trang của chính chủ trỏ tới handle X, hoặc có địa chỉ on-chain công khai.
   - **B**: trang chính chủ xác nhận tên hoặc vai trò nhưng không có link X, hoặc báo chí uy tín gán danh tính.
   - **C**: chỉ xuất hiện trong bài danh sách.

   Phiên này **không mở được X/Twitter** vì cần đăng nhập và không được phép đăng ký. Mọi URL `x.com/...` bên dưới được **dựng từ handle, chưa truy cập**.
3. **Phân tier theo loại bằng chứng hiệu suất**, không theo số follower. "Verified" nghĩa là kết quả kiểm chứng được, kể cả khi kết quả đó là lỗ.
4. **Điểm chất lượng dữ liệu (DQ, 1–5):**
   - 5: on-chain hoặc kiểm toán, lịch sử dài, danh tính tự xác nhận.
   - 4: bên thứ ba kiểm chứng được nhưng lịch sử ngắn hoặc chỉ một phần.
   - 3: bên thứ ba báo cáo, danh tính do tracker gán.
   - 2: chỉ có bài đăng công khai, chưa kiểm chứng.
   - 1: chỉ có tên trong danh sách hoặc có cờ đỏ.
5. **Giới hạn của phiên:**
   - Tổng cộng 39 lượt search/fetch/curl.
   - Có 3 lần thất bại:
     - `stats-data.hyperliquid.xyz` bị proxy chặn (403) khi dùng curl.
     - Cũng trang đó lỗi khi dùng WebFetch.
     - `rektcapital.co` lỗi.
   - Leaderboard copy-trading của Binance là trang JS, không hiện dữ liệu trader.
   - Không truy cập tài khoản riêng, không đăng ký, không đăng bài. Mọi nội dung web được coi là dữ liệu không tin cậy.

---

## 3. Bảng WATCHLIST

Chú thích:
- **Loại**: SPOT / PERP (futures vĩnh cửu) / MACRO.
- **ID-chk**: mức kiểm chứng danh tính (A/B/C).
- **Ngày KT**: 2026-11-04 cho mọi dòng.

### Tier 1 — VERIFIED_PERFORMANCE (on-chain, qua tracker bên thứ ba)

| ID | Tên / handle | URL | Nền tảng | Chiến lược / Loại | Khung TG | Nguồn dữ liệu | Thu thập tín hiệu / độ trễ | Lịch sử | Kết quả kiểm chứng | DQ | ID-chk | Tier | Xung đột / ghi chú |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| T01 | James Wynn (@JamesWynnReal) | theblock.co/post/356341; intel.bubblemaps.io/cases/5/james-wynn-liquidations; x.com/JamesWynnReal (dựng) | Hyperliquid + X | Long/short BTC đòn bẩy 40x, có vị thế danh nghĩa tới ~$1.1–1.2 tỷ. **PERP** | Ngắn hạn đến swing | The Block, Cointelegraph, Bubblemaps (dẫn Arkham, Lookonchain) | Hyperliquid info API (`clearinghouseState`, công khai, gần real-time) khi đã có địa chỉ. Ngoài ra có thể theo X API (trả phí) | Khoảng 05/2025 trở đi | Báo chí ghi nhận **lỗ >$100M** (05–06/2025, chuỗi thanh lý). Sau đó có tin lãi nhỏ trở lại. Không có MDD hay số mẫu chuẩn hóa | 3 (on-chain nhưng địa chỉ chưa tự đối chiếu; danh tính do tracker gán) | B | 1 | Rủi ro cực cao, mang tính giải trí. Có tin "cá voi khác bắt chước Wynn long BTC" (cryptorank) nên **không độc lập**. Các trang tin flash (MEXC, KuCoin) đăng lại cùng một sự kiện, không được tính là nhiều xác nhận |
| T02 | AguilaTrades | forklog.com/en/hyperliquid-whale-faces-2-5-million-loss-after-unrealized-10-million-gain/; beincrypto.com/aguilatrades-crypto-liquidation-40-million | Hyperliquid | Long BTC lớn, đòn bẩy. **PERP** | Swing | Forklog, BeInCrypto, CoinDesk (bản sao) | Hyperliquid API khi có địa chỉ | Khoảng 06/2025 trở đi | Đang lãi chưa chốt $10M rồi chuyển thành **lỗ $2.5M** (06/2025). Sau đó có tin "lỗ $35M trong 2 tuần" và bị thanh lý ~$40M | 3 | B | 1 | Danh tính chỉ là một handle do tracker gán. Tín hiệu ngược (contrarian) chứ không phải tín hiệu theo |
| T03 | "Machi Big Brother" (báo chí thường gọi là Jeffrey Huang; tên này **chưa xác nhận** trong nguồn đã fetch) | thedefiant.io/news/defi/machi-big-brother-s-hyperliquid-losses-top-usd80m-as-he-sells-bored-apes-for-margin | Hyperliquid | Long đòn bẩy, chủ yếu **ETH**, ít BTC. **PERP** | Swing | The Defiant (dẫn Arkham), ngày 2026-06-30 | Hyperliquid API, Arkham | Từ 09/2025 đến 06/2026 | **Lỗ >$80M** kể từ tháng 9 (Arkham, tính đến 2026-06-30) | 3 | B | 1 | Liên quan tới BTC **thấp**, cân nhắc loại khỏi feed BTC |
| T04 | "HyperUnit whale" / "10/10 whale". Bị liên hệ với Garrett Jin (cựu CEO BitForex) qua ENS `garrettjin.eth` | theblock.co/post/387927 (2026-01-31); cointelegraph.com/news/hyperliquid-whale-linked-former-bitforex-ceo | Hyperliquid | Short BTC+ETH quy mô >$1 tỷ, sau đó long ETH/SOL/BTC >$900M. **PERP** | Sự kiện / swing | The Block, Decrypt, Cointelegraph (dẫn Arkham) | Arkham entity, Hyperliquid API | Từ 10/2025 đến 01/2026 | Lãi ~**$200M** nhờ short vài phút trước thông báo thuế quan (10/2025). Sau đó **lỗ ~$250M** khi chốt vị thế ETH (01/2026); tài khoản Hyperliquid còn $53 | 2 (danh tính **tranh chấp**: Jin phủ nhận sở hữu, nói đó là "tiền của khách hàng") | B (tranh chấp) | 1 | Bị cáo buộc giao dịch nội gián (chưa chứng minh). Không nên coi là tín hiệu tái lập được |

### Tier 2 — OBSERVED_SIGNALS (đăng lệnh hoặc nhận định có thời điểm, hiệu suất chưa kiểm chứng)

| ID | Tên / handle | URL | Nền tảng | Chiến lược / Loại | Khung TG | Nguồn dữ liệu | Thu thập / độ trễ | Lịch sử | Kết quả kiểm chứng | DQ | ID-chk | Tier | Xung đột / ghi chú |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| T06 | Peter L. Brandt | peterlbrandt.com; x.com/PeterLBrandt (dựng; trang chủ **không** link X công khai) | Web + X | Phân tích kỹ thuật cổ điển (chart pattern), có BTC. Hay nêu vị thế. SPOT/futures tùy lệnh | Swing đến trung hạn | peterlbrandt.com | X API (trả phí). Bài blog công khai | Trang ghi "40 năm" kinh nghiệm | **UNKNOWN**. Trang chỉ nêu "decades of profitable trading", không có track record kiểm toán | 2 | B | 2 | **Factor Report trả phí** ($347/năm). Cảnh báo theo thời gian thực nằm trên "X riêng tư" dành cho thành viên, không được truy cập |
| T07 | Smart Contracter (@SmartContracter) | coinbound.io/crypto-traders-to-follow/; x.com/SmartContracter (dựng) | X | Elliott wave / TA, nhiều coin, có BTC | Trung hạn | Coinbound | X API | Nhiều năm | UNKNOWN. Được ghi công gọi đáy BTC ~$3.2k năm 2018 (chỉ là giai thoại) | 2 | C | 2 | Coinbound ghi là không bán nhóm trả phí |
| T08 | Pentoshi (@Pentosh1) | coinbound.io; x.com/Pentosh1 (dựng) | X | TA / chart. BTC và alt | Swing | Coinbound | X API | Nhiều năm | UNKNOWN. Được nói là gọi đỉnh ~$64k năm 2021 (giai thoại) | 2 | C | 2 | Chưa kiểm tra xung đột lợi ích |
| T09 | DonAlt (@CryptoDonAlt) | coinbound.io; iq.wiki/wiki/cryptodonalt (chỉ thấy trong kết quả search); x.com/CryptoDonAlt (dựng) | X, podcast TechnicalRoundup | Swing trade đảo chiều xu hướng khung lớn | HTF (ngày/tuần) | Coinbound, iq.wiki | X API; RSS podcast (rss.com/feed/podcasts/technicalroundup/) | Nhiều năm | UNKNOWN | 2 | C | 2 | Coinbound ghi "no-promotion policy". **Đồng dẫn TechnicalRoundup với CryptoCred (T24)** nên quan điểm tương quan, không tính là 2 phiếu độc lập |
| T10 | Crypto Kaleo (@CryptoKaleo) | coinbound.io; x.com/CryptoKaleo (dựng) | X | TA, crypto và cổ phiếu | Swing | Coinbound | X API | Nhiều năm | UNKNOWN | 2 | C | 2 | **Đồng sáng lập dự án NFT LedgerArt** (xung đột lợi ích khi nhắc tới tài sản liên quan) |
| T11 | TheCryptoDog (@TheCryptoDog) | coinbound.io; x.com/TheCryptoDog (dựng) | X | Đăng lệnh gần như hằng ngày | Ngắn hạn | Coinbound | X API | Nhiều năm | UNKNOWN | 2 | C | 2 | Liên quan tới BTC chưa rõ (có nhiều alt / web3) |
| T12 | Il Capo of Crypto (@CryptoCapo_) | ca.investing.com/news/cryptocurrency-news/top-trader-predicts-bitcoin-will-crash-to-12000-3028265; benzinga.com (02/2024); t.me/s/CryptoCapoTG (kênh Telegram công khai, chỉ thấy trong kết quả search) | X, Telegram | Gọi lệnh BTC theo hướng (directional), nổi tiếng với kịch bản giảm về $12k | Swing đến trung hạn | Investing.com, Benzinga | X API; bản xem trước Telegram công khai (scrape HTML, lưu ý điều khoản sử dụng) | Từ 2022 trở đi | UNKNOWN. Kịch bản $12k **không xảy ra** (theo hiểu biết chung, chưa đối chiếu giá trong phiên). Benzinga 2024 trích meme "ngày Capo bullish là ngày đỉnh" | 2 | B | 2 | Dư luận dùng Capo như **chỉ báo ngược**. Có tin đồn xóa hoặc ẩn bài cũ nhưng **chưa xác nhận** trong phiên |

### Tier 3 — COMMENTARY_ONLY

| ID | Tên / handle | URL | Nền tảng | Chiến lược / Loại | Khung TG | Nguồn dữ liệu | Thu thập / độ trễ | Lịch sử | Kết quả kiểm chứng | DQ | ID-chk | Tier | Xung đột / ghi chú |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| T13 | Benjamin Cowen (Into The Cryptoverse) | intothecryptoverse.com → twitter.com/benjamincowen, youtube.com/@benjaminjcowen | Web, YouTube, X, Telegram | Macro + risk metric (MVRV, log regression). **MACRO** | Dài hạn | Trang chủ (BC Analytics LLC) | **YouTube RSS (miễn phí)**, X API | Nhiều năm | Không có lệnh cụ thể. N/A | 3 | A | 3 | Bán gói Pro / Direct Access. Trang không có disclaimer đầu tư. Lưu ý: kyledoops ghi handle là @intocryptoverse, còn trang chủ trỏ tới @benjamincowen, **cần xác nhận lại** |
| T14 | Willy Woo | willywoo.substack.com ("Bitcoin Vector Lite"); x.com/woonomic (dựng, theo kyledoops) | Substack, X | On-chain / dòng tiền nhà đầu tư. **MACRO** | Chu kỳ | Substack | **Substack RSS `/feed` (miễn phí)**, hằng tuần | Nhiều năm | N/A. Tự nhận đã "xác định vùng đỉnh rủi ro cao" (tự công bố) | 3 | B | 3 | Có sản phẩm trả phí (kyledoops nhắc "The Bitcoin Forecast"). Trang không công bố affiliation |
| T15 | Arthur Hayes | cryptohayes.substack.com ("Crypto Trader Digest"); x.com/CryptoHayes (dựng) | Substack, X | Bài luận macro / thanh khoản. **MACRO** | Trung đến dài hạn | Substack (tự giới thiệu là CIO Maelstrom, đồng sáng lập BitMEX) | Substack RSS | Nhiều năm | N/A | 3 | B | 3 | **CIO quỹ Maelstrom**, có lợi ích từ vị thế của quỹ; nhận định có thể "nói theo túi tiền" |
| T16 | Rekt Capital | rektcapital.substack.com/about; x.com/rektcapital (dựng) | Substack, X | TA chu kỳ halving BTC + alt | Tuần / chu kỳ | Substack | Substack RSS (thứ 2/4/6) | Nhiều năm | N/A | 2 | B (trang không nêu tên tác giả hay link X) | 3 | Có gói Premium (giá không hiện). rektcapital.co lỗi khi fetch |
| T17 | Checkmate / James Check (@_Checkmatey_) | checkonchain.com → @_Checkmatey_ | Web, X | On-chain BTC. **MACRO** | Chu kỳ | Trang chủ | RSS hoặc newsletter; charts.checkonchain.com | Nhiều năm | N/A. Không đưa ra lệnh mua/bán | 4 (danh tính rõ, phương pháp minh bạch, nhưng không có lệnh) | A | 3 | Có gói trả phí (Orange Plan) và dịch vụ tư vấn |
| T18 | Scott Melker (@scottmelker) | thewolfofallstreets.com → x.com/scottmelker | Web, YouTube, Yahoo Finance show, podcast | Bình luận thị trường | Ngắn đến trung hạn | Trang chủ | YouTube RSS, newsletter | Nhiều năm | N/A | 3 | A | 3 | Có nhận hợp tác thương hiệu (brand partnership, liên hệ qua email) nên **có thể có nội dung được tài trợ** |
| T19 | Tone Vays (@ToneVays) | tonevays.com → twitter.com/ToneVays, youtube.com/tonevays | Web, YouTube, X | TA BTC, tự giới thiệu là "derivatives trader". **SPOT/PERP** | Swing | Trang chủ | YouTube RSS | Nhiều năm | UNKNOWN. Không thấy lệnh có thời điểm trên trang | 3 | A | 3 | Có trang **Affiliates** (chương trình chưa rõ), workshop trả phí, tổ chức sự kiện |
| T20 | Capriole Investments (@capriole_fund; người sáng lập thường được biết là Charles Edwards, **chưa xác nhận** trên trang đã fetch) | capriole.com | Web, X | Chỉ báo macro/on-chain BTC; quỹ đa tài sản | Trung đến dài hạn | Trang chủ | Newsletter / X | Live từ 2019, quỹ từ 03/2021 | **Tự công bố**: CAGR 61% và 1.668% lũy kế **sau phí** (tới 2026-01-02); "kiểm toán hàng năm" nhưng báo cáo không công khai; giai đoạn trước khi lập quỹ chỉ có xác minh dấu thời gian, chưa kiểm toán. **MDD không công bố** | 3 | A | 3 | **Bán quỹ** (phí hiệu suất) và công cụ trả phí. Quỹ không chỉ BTC (còn AI, quantum, hàng hóa) |
| T21 | PlanB (@100trillionUSD) | cointelegraph.com/opinion/three-reasons-why-planb-s-stock-to-flow-model-is-not-reliable; bookmap.com/blog/is-planbs-stock-to-flow-model-broken | X | Mô hình Stock-to-Flow. **MACRO** | Chu kỳ | Bài phê bình trên báo | X API | Từ 2019 | Mô hình S2F bị phê bình rộng rãi là sai lệch dự báo | 2 | B | 3 | Dự báo mô hình, không phải lệnh. Nhiều bài báo coi S2F là "đã gãy" |
| T22 | Pantera Bitcoin Fund (Dan Morehead) | panteracapital.com/blockchain-letter/1000x-pantera-bitcoin-fund/ (2024-11-26) | Thư nhà đầu tư | Quỹ BTC (giống nắm giữ dài hạn; trang không nói rõ có giao dịch chủ động hay không). **SPOT** | Rất dài hạn | Thư của quỹ | RSS hoặc trang thư (định kỳ) | Từ 2013 | **Tự công bố**: +131.165% từ khi lập quỹ, **sau phí** (phí quản lý 0,75%). Ghi rõ là **"unaudited estimates"**. Không có MDD của quỹ (chỉ nhắc BTC −87% từ 12/2013) | 3 | A | 3 | Quảng bá quỹ của chính mình. Tiêu đề "1000x" không khớp với 131.165% (~1.312x) |
| T23 | Michaël van de Poppe (@CryptoMichNL) | tradersunion.com/news/market-voices/show/1942561-mnfund-launches-crypto-fund/; altrady.com/blog/news/mn-trading-michael-van-de-poppe-interview | X, YouTube | TA BTC + alt | Swing | Tin flash, phỏng vấn | X API | Nhiều năm | MN Fund **tự báo** "vượt BTC 34,49% sau 7 tháng", "+16% tháng 3". Chưa kiểm toán | 2 | B | 3 | **Sáng lập MN Fund / MN Trading Capital**: nhận định công khai có thể phục vụ vị thế của quỹ |
| T24 | CryptoCred (@CryptoCred) | coinbound.io; kyledoops.substack.com | X, YouTube (TechnicalRoundup) | Giáo dục TA, khung lớn | HTF | Bài danh sách | X API, RSS podcast | Nhiều năm | N/A | 2 | C | 3 | Tương quan với DonAlt (T09) |
| T25 | Material Indicators | materialindicators.com | Web, X | Thanh khoản order book (FireCharts), CVD, chỉ báo "Trend Precognition". SPOT/PERP | Ngắn đến trung hạn | Trang chủ | X; công cụ trả phí (không truy cập) | 3 năm dữ liệu order book | UNKNOWN | 2 | B (trang không link X, không nêu tên founder) | 3 | **Sản phẩm trả phí**, có chương trình affiliate |
| T26 | Kyle Doops (@kyledoops) | kyledoops.substack.com/p/best-crypto-traders-to-follow-in | Substack, X | Heatmap thanh lý (liquidation heatmap) | Ngắn hạn | Substack của chính anh ấy | Substack RSS | Từ 2024 trở về trước | N/A | 2 | B | 3 | Bán membership "Whale Room"; **tự đưa mình vào danh sách "best traders"** |
| T27 | Eric Krown (@KrownCryptoCave) | kyledoops.substack.com | X, YouTube | TA xác suất | Swing | Bài danh sách | X / YouTube RSS | ? | N/A | 1 | C | 3 | Có khóa học |
| T29 | John Bollinger (@bbands) | kyledoops.substack.com | X | Bollinger Bands áp dụng cho crypto | Swing | Bài danh sách | X API | Rất lâu (người phát minh Bollinger Bands) | N/A | 2 | C | 3 | Điều hành Bollinger Capital Management |
| T30 | TraderSZ (@trader1sz) | kyledoops.substack.com | X | Price action thuần, đa thị trường | Swing | Bài danh sách | X API; **feed riêng (không truy cập)** | ? | UNKNOWN | 1 | C | 3 | Có feed và nhật ký giao dịch riêng (trả phí hoặc riêng tư) |
| T31 | AlphaBTC (@mark_cullen) | kyledoops.substack.com | X, nền tảng giáo dục | Order flow, funding, heatmap | Ngắn hạn | Bài danh sách | X | ? | UNKNOWN | 1 | C | 3 | Nền tảng giáo dục (có thể trả phí) |
| T32 | Jason Shapiro (Crowded Market Report) | kyledoops.substack.com (YouTube @crowdedmarketreport) | YouTube, Discord | Contrarian, chỉ số COT. **MACRO** | Trung hạn | Bài danh sách | YouTube RSS | ? | UNKNOWN | 1 | C | 3 | Discord riêng, báo cáo hằng tuần |
| T34 | Avi Felman (@AviFelman) | coinbound.io | X | Bình luận crypto/macro. **MACRO** | Trung hạn | Bài danh sách | X API | ? | N/A | 1 | C | 3 | Coinbound ghi rõ "không phải trader" |

### Tier 4 — UNVERIFIED / INACTIVE

| ID | Tên / handle | URL | Nền tảng | Chiến lược / Loại | Khung TG | Nguồn dữ liệu | Thu thập / độ trễ | Lịch sử | Kết quả kiểm chứng | DQ | ID-chk | Tier | Xung đột / ghi chú |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| T05 | Hyperdash Vault #1 (địa chỉ `0x4078582c42fdb547b1397fabb5d5a4beab81be9e`) | tradingstrategy.ai/trading-view/vaults/address/0x4078582c42fdb547b1397fabb5d5a4beab81be9e | Hyperliquid vault | Sao chép vị thế của "vài trader top leaderboard". BTC/ETH **PERP** | ? | tradingstrategy.ai (dữ liệu cập nhật 2026-11-04 11:11 UTC) | Hyperliquid API, tradingstrategy.ai | 2025-01-08 đến nay (~1,8 năm) | **Đã kiểm chứng on-chain (lịch sử)**: lũy kế net +50,9%, CAGR net 25,3%; 1 năm net CAGR 8,2%; 3 tháng −0,6%; **MDD −29,4%**; phí hiệu suất 10% | 3 (trang có số liệu mâu thuẫn: cảnh báo "giảm >95% so với đỉnh", giá share bị ghi khác nhau) | A (on-chain; không nêu tên người quản lý) | 4 (**INACTIVE**: TVL 0, đóng nạp tiền vĩnh viễn) | **Không độc lập**: vault này copy các trader khác |
| T28 | Trader_J (@Trader_Jibon) | kyledoops.substack.com | X | Dự báo BTC | ? | Bài danh sách | X | ? | UNKNOWN | 1 | C | 4 | Thông tin quá ít |
| T33 | Crypto Chase (@Crypto_Chase) | kyledoops.substack.com | X | ? | ? | Bài danh sách | X | Từ 2016 | UNKNOWN | 1 | C | 4 | **Cờ đỏ**: dịch vụ trả phí "hứa lợi nhuận cao"; ngay bài nguồn cũng khuyên thận trọng |

### Pool đã xét nhưng KHÔNG đưa vào (lý do)

- **Ít liên quan tới BTC hoặc chỉ có tên trong danh sách, quá ít thông tin:**
  - Jason Pizzino và Michael Pizzino: hai anh em, quan điểm tương quan.
  - Trader Mayne (@Tradermayne).
  - Cheds (@BigCheds).
  - JACKIS (@i_am_jackis).
  - Mr. Anderson (@TrueCrypto28).
  - InvestAnswers.
  - VirtualBacon.
- **Không phải trader BTC:**
  - Ansem (@blknoiz06): chủ yếu alt/memecoin.
  - Tyler Fallon: DeFi.
  - Ty Smith: CEO của Coinbound, chính là đơn vị đăng bài danh sách.
- **Lead trader trên leaderboard copy-trading của các sàn:** ẩn danh, không định danh công khai được, xem mục 4.
- **Dữ liệu tổ chức, không phải trader** (chưa kiểm tra trong phiên): giao dịch mua BTC của Strategy (MSTR) theo hồ sơ SEC 8-K, và dòng tiền ETF BTC spot. Có thể là đặc trưng (feature) dữ liệu thị trường riêng, nhưng không thuộc watchlist trader.
- **Tracker hoặc nhà cung cấp dữ liệu** (Arkham, Lookonchain, Hypurrscan, Bubblemaps, CoinMarketMan HyperTracker): là **nguồn** dữ liệu, không phải trader. Không tính các bản đăng lại của họ là xác nhận độc lập.

---

## 4. Leaderboard copy-trading của các sàn (kiểm tra 2026-11-04)

> **Bot của Chủ tịch chỉ giao dịch SPOT.** Phần lớn leaderboard copy-trading là futures/đòn bẩy, nên ROI ở đó không chuyển sang spot được.

- **Binance Spot Copy Trading** (binance.com/en/copy-trading/spot, truy cập 2026-11-04):
  - Trang có xác nhận đúng là "Spot Copy Trading".
  - Các tab sắp xếp: Recommended, All Portfolios, High PNL, High ROI, High Copiers' PNL, Most Copied.
  - Trang render bằng JS nên **không lấy được danh sách trader hay khung thời gian**.
  - Lead trader ẩn danh và nhận chia lợi nhuận từ người copy (có trang "Lead Trader Growth Plan"), tức là có xung đột lợi ích.
  - **Chưa đưa ai vào danh sách.**
- **Bybit Copy Trading Classic** (trang help "A Guide to Master Trader Levels and Leaderboard Rankings", truy cập 2026-11-04):
  - Các nhóm xếp hạng:
    - Top Balanced: ROI 30 ngày + MDD + Sharpe/Sortino + số dư.
    - Top by ROI: ROI 7 ngày, điều kiện MDD 30 ngày ≤ 40%.
    - Intra-Day: ROI 7 ngày ≥ 20%, tỷ lệ thắng 50%.
    - New Talents: dưới 30 ngày giao dịch.
    - Highest Follower Profit.
    - Lowest Drawdown: MDD 30 ngày ≤ 20%, đòn bẩy trung bình ≤ 25x.
  - Dữ liệu **cập nhật hằng ngày lúc 02:00 UTC**; xét cấp độ hằng tuần.
  - Bài viết **không nói rõ spot hay derivatives**.
  - Các cửa sổ 7 hoặc 30 ngày quá ngắn để coi là "track record nhiều tháng".
- **OKX, Bitget:** chưa kiểm tra trang gốc trong phiên, chỉ thấy kết quả search, nên không có dữ liệu.
- **Kết luận:** nếu sau này dùng leaderboard, mỗi snapshot phải ghi: sàn, tab, tiêu chí xếp hạng, cửa sổ thời gian, timestamp UTC, spot hay futures, đòn bẩy trung bình. Lọc chỉ giữ portfolio **spot** có ≥ 180 ngày lịch sử và MDD công khai. Ngay cả khi thỏa các điều kiện đó, nguồn này vẫn chỉ dùng để quan sát.

---

## 5. Cách sử dụng danh sách này (bắt buộc)

1. **Chỉ là feed QUAN SÁT.** Không có dòng nào được nối thẳng vào quyết định đặt lệnh của bot. Mọi tín hiệu chỉ được ghi vào log ở chế độ shadow, có timestamp UTC lúc thu thập.
2. **Phải kiểm định ngoài mẫu (out-of-sample) trước khi dùng:**
   - So với **baseline chỉ dùng dữ liệu thị trường** (giá/volume BTC spot của chính Binance, cùng phí và trượt giá).
   - Đăng ký trước chỉ số đo (lợi nhuận sau phí, MDD, số lệnh); chạy walk-forward; cỡ mẫu tối thiểu phải được thống nhất trước.
   - Chỉ khi feed **cải thiện có ý nghĩa** so với baseline mới được đề xuất làm feature, và phải có **Chủ tịch phê duyệt**.
3. **Không bao giờ mua chỉ vì N trader đồng thuận.**
   - Nhiều người trong danh sách tương quan với nhau (T09/T24 cùng podcast; cá voi bắt chước nhau; T05 copy người khác; tin flash đăng lại một sự kiện).
   - Đồng thuận trên mạng xã hội thường đến muộn và hay đánh dấu đỉnh, chứ không phải xác nhận xu hướng.
4. **Bot SPOT không sao chép được short hay đòn bẩy.** Tín hiệu short hoặc perp, nếu được kiểm định là có giá trị, tối đa chỉ được hiểu là "giảm hoặc không mua thêm", trong khuôn khổ quy tắc rủi ro hiện có.
5. **Tier 1 không có nghĩa là "giỏi".** Cả 4 dòng Tier 1 đều có kết quả kiểm chứng là lỗ lớn hoặc rủi ro cực cao. Nếu muốn dùng, chỉ dùng như dữ liệu tâm lý hoặc vị thế (positioning).
6. **Chi phí thu thập:**
   - Miễn phí: Substack/YouTube RSS, Hyperliquid info API (cần mở quyền domain; trong phiên này bị proxy chặn), tradingstrategy.ai.
   - Trả phí: X API và các nhóm trả phí. **Phải được Chủ tịch duyệt từng khoản trước khi phát sinh chi phí.**
   - Không đăng ký, không đăng nhập hộ, không truy cập kênh riêng tư hay trả phí.
7. **Kiểm tra lại định kỳ** (gợi ý mỗi 30 ngày): xem trạng thái hoạt động, handle đổi tên, bài bị xóa, xung đột mới. Mọi dòng mức ID C phải được nâng lên A/B trước khi đưa vào feed.

---

## 6. Nhật ký nguồn (truy vấn và ngày truy cập; tất cả ngày 2026-11-04)

**WebSearch:**
- Q1 "James Wynn Hyperliquid wallet address BTC position 2026" → theblock.co/post/356341, theblock.co/post/355669, cointelegraph (1B BTC bet 40x), intel.bubblemaps.io/cases/5, cryptorank (whales join James Wynn)
- Q2 "AguilaTrades Hyperliquid BTC long address loss" → forklog.com/en/hyperliquid-whale-faces-2-5-million-loss-after-unrealized-10-million-gain/, beincrypto.com/aguilatrades-crypto-liquidation-40-million
- Q3 "James Wynn Hyperliquid address 0x5078 Lookonchain" → thedefiant.io (Wynn lỗ $47M/tuần), beincrypto (profit comeback). **Không xác nhận được địa chỉ.**
- Q4 "Machi Big Brother Jeffrey Huang Hyperliquid BTC long address liquidated 2026" → thedefiant.io (losses top $80M), stocktwits (Arkham)
- Q5 "Hyperliquid insider whale BTC short before October 10 crash Garrett Jin wallet" → theblock.co/post/387927, theblock.co/post/374290, decrypt.co/345734, cointelegraph (linked former BitForex CEO)
- Q6 "Binance spot copy trading lead trader ROI MDD ranking criteria 30 days 90 days how leaderboard sorted" → binance.com FAQ, Lead Trader Growth Plan
- Q7 "best bitcoin traders analysts to follow on X 2026 list handles" → coinbound.io/crypto-traders-to-follow/, kyledoops.substack.com/p/best-crypto-traders-to-follow-in
- Q8 "eToro Popular Investor crypto bitcoin verified track record max drawdown copy" → không có ứng viên BTC định danh được; bỏ qua
- Q9 "Rekt Capital newsletter Substack bitcoin halving cycle analyst X @rektcapital" → rektcapital.substack.com
- Q10 "PlanB @100trillionUSD stock-to-flow model 2025 2026 prediction failed criticism website" → cointelegraph opinion, bookmap blog
- Q11 "Il Capo of Crypto CryptoCapo_ account deleted tweets bitcoin call controversy" → investing.com ($12k), benzinga 02/2024, t.me/s/CryptoCapoTG
- Q12 "Bybit OKX Bitget copy trading lead trader ranking ROI 7D 30D 90D max drawdown spot copy trading leaderboard criteria" → bybit.com help center
- Q13 "Pantera Bitcoin Fund returns since inception 2013 letter 2026 net of fees" → panteracapital.com letter
- Q14 "Hyperliquid public vault leaderboard identifiable manager track record BTC vault Hyperdash top traders identified X" → tradingstrategy.ai (Hyperdash Vault #1)
- Q15 "Michaël van de Poppe MN Fund MN Trading Capital founder crypto fund conflict of interest" → tradersunion, altrady, blockchainnews flash
- Q16 "Technical Roundup DonAlt CryptoCred Daan Crypto Trades YouTube podcast hosts" → iq.wiki, player.fm, rss.com feed

**WebFetch** (ngày truy cập 2026-11-04):
- intel.bubblemaps.io/cases/5/james-wynn-liquidations
- stats-data.hyperliquid.xyz/Mainnet/leaderboard: **LỖI**
- theblock.co/post/387927
- thedefiant.io (bài Machi, 2026-06-30)
- binance.com/en/copy-trading/spot
- peterlbrandt.com
- capriole.com
- rektcapital.co: **LỖI**
- coinbound.io/crypto-traders-to-follow/
- kyledoops.substack.com/p/best-crypto-traders-to-follow-in
- rektcapital.substack.com/about
- willywoo.substack.com
- cryptohayes.substack.com
- intothecryptoverse.com
- tonevays.com
- materialindicators.com
- checkonchain.com
- thewolfofallstreets.io (chuyển hướng) → thewolfofallstreets.com
- bybit.com/en/help-center/article/A-Guide-to-Master-Trader-Levels-and-Leaderboard-Rankings
- panteracapital.com/blockchain-letter/1000x-pantera-bitcoin-fund/
- tradingstrategy.ai/trading-view/vaults/address/0x4078582c42fdb547b1397fabb5d5a4beab81be9e

**Bash curl:** `stats-data.hyperliquid.xyz`: **bị proxy từ chối (403 connect_rejected)**. Cần Chủ tịch hoặc admin cho phép domain này nếu muốn tự đối chiếu on-chain.

**Tổng:** 16 search, 22 fetch, 1 curl (39 lượt).
