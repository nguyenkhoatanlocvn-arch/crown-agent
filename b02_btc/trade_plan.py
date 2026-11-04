"""B02_BTC — TRADE_PLAN cho BTCUSDT Spot, tính TẤT ĐỊNH từ file kiểm kê thật (KIEM_KE_CHI_DOC).

Không gọi mạng, không gửi lệnh. Mọi giá lấy từ inventory JSON có timestamp; thiếu dữ liệu -> UNKNOWN/WAIT.
Quy tắc (kế thừa BTCDesk, không phát minh mới):
  - Chế độ thị trường: giá đóng nến NGÀY gần nhất đã đóng > SMA50 ngày  -> được phép mua (profile B/M).
  - Cắt lỗ: 3 x ATR14 ngày, kẹp trong 5%..12% (giống profile M).
  - Thoát chiến lược: nến ngày đóng < SMA50.
  - Khối lượng: không vượt trần vốn, VÀ lỗ dự kiến (gồm phí + trượt giá) <= mức lỗ/lệnh Chủ tịch duyệt.
    Nếu không đạt minNotional mà vẫn trong mức lỗ -> WAIT (không làm tròn tăng khối lượng).
"""
from decimal import Decimal as D, ROUND_DOWN, ROUND_UP
import json, sys, datetime

FEE = D("0.001")          # 0.1% mỗi chiều (mặc định Binance spot; nếu tài khoản có commissionRates thì dùng số thật)
SLIP = D("0.002")         # dự phòng trượt giá 0.2% khi cắt lỗ
STOP_MIN, STOP_MAX, ATR_K = D("0.05"), D("0.12"), D(3)
BUFFER = D("0.99")        # chỉ dùng 99% vốn khả dụng (phí/biến động)


def _floor(x, step): return (x / step).to_integral_value(ROUND_DOWN) * step
def _ceil(x, step): return (x / step).to_integral_value(ROUND_UP) * step


def filters(info):
    f = {x["filterType"]: x for x in info.get("filters", [])}
    lot = f.get("MARKET_LOT_SIZE") if D(f.get("MARKET_LOT_SIZE", {}).get("stepSize", "0") or "0") > 0 else f["LOT_SIZE"]
    notional = f.get("NOTIONAL") or f.get("MIN_NOTIONAL") or {}
    return {"step": D(lot["stepSize"]), "min_qty": D(lot["minQty"]), "tick": D(f["PRICE_FILTER"]["tickSize"]),
            "min_notional": D(notional.get("minNotional", "5")), "order_types": info.get("orderTypes", []),
            "oco": info.get("ocoAllowed"), "oto": info.get("otoAllowed")}


def closed_daily(klines, now_ms):
    return [k for k in klines if int(k[6]) < now_ms]     # k[6] = close time; bỏ nến chưa đóng


def sma(xs, n): return sum(xs[-n:]) / D(n)


def atr14(kl):
    trs = []
    for i in range(1, len(kl)):
        h, l, pc = D(kl[i][2]), D(kl[i][3]), D(kl[i - 1][4])
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    a = sum(trs[:14]) / 14
    for t in trs[14:]: a = (a * 13 + t) / 14       # Wilder
    return a


def plan(inv, max_loss_usd=None, cap_usd=D(30)):
    now_ms = int((inv.get("clock") or {}).get("server_ms") or 0)
    out = {"symbol": "BTCUSDT", "source": "Binance /api/v3 (inventory " + str(inv.get("generated_utc")) + ")",
           "server_time_utc": datetime.datetime.utcfromtimestamp(now_ms / 1000).isoformat() + "Z" if now_ms else "UNKNOWN",
           "valid_minutes": 15, "blockers": []}
    book, info, k1d = inv.get("btc_book"), inv.get("btc_exchange_info"), inv.get("btc_klines_1d")
    if not (now_ms and isinstance(book, dict) and "askPrice" in book and isinstance(info, dict) and isinstance(k1d, list)):
        out.update(decision="UNKNOWN", reason="thiếu dữ liệu giá/bộ lọc từ sàn"); return out
    f = filters(info); bid, ask = D(book["bidPrice"]), D(book["askPrice"])
    kl = closed_daily(k1d, now_ms)
    if len(kl) < 60: out.update(decision="UNKNOWN", reason="thiếu nến ngày"); return out
    closes = [D(k[4]) for k in kl]; s50 = sma(closes, 50); last = closes[-1]; atr = atr14(kl[-60:])
    regime = last > s50
    stop_pct = min(max(ATR_K * atr / ask, STOP_MIN), STOP_MAX)
    # vốn khả dụng thật
    usdt_free = None; fee = FEE
    acct = inv.get("account")
    if isinstance(acct, dict) and "balances" in acct:
        usdt_free = sum((D(b["free"]) for b in acct["balances"] if b["asset"] == "USDT"), D(0))
        cr = acct.get("commissionRates") or {}
        if cr.get("taker"): fee = D(str(cr["taker"]))
    capital = min(usdt_free, cap_usd) if usdt_free is not None else None
    entry = ask; stop = _floor(entry * (1 - stop_pct), f["tick"])
    per_unit_loss = (entry - stop) + entry * fee + stop * (fee + SLIP)
    out.update(market={"bid": str(bid), "ask": str(ask), "spread_pct": f"{(ask - bid) / ask * 100:.4f}",
                       "last_daily_close": str(last), "sma50_daily": f"{s50:.2f}", "atr14_daily": f"{atr:.2f}",
                       "regime_on": regime},
               filters={k: (str(v) if isinstance(v, D) else v) for k, v in f.items()},
               fee_rate=str(fee), usdt_free=str(usdt_free) if usdt_free is not None else "UNKNOWN",
               capital_usable=str(capital) if capital is not None else "UNKNOWN")
    if capital is None:
        out["blockers"].append("chưa đọc được số dư thật")
        capital = cap_usd
    q_cap = _floor(capital * BUFFER / (entry * (1 + fee)), f["step"])
    q = q_cap
    if max_loss_usd is not None:
        q_risk = _floor(D(str(max_loss_usd)) / per_unit_loss, f["step"]); q = min(q_cap, q_risk)
    notional = q * entry
    sell_qty = _floor(q * (1 - fee), f["step"])       # phí mua trừ bằng BTC (nếu không dùng BNB)
    loss = q * (entry - stop) + q * entry * fee + sell_qty * stop * (fee + SLIP)
    tp1 = _ceil(entry + 2 * (entry - stop), f["tick"])
    half = _floor(sell_qty / 2, f["step"])
    can_split = half * tp1 >= f["min_notional"] and (sell_qty - half) * stop >= f["min_notional"]
    out.update(order={
        "entry_type": "MARKET (quoteOrderQty) — hoặc LIMIT tại ask nếu muốn tránh trượt", "entry_price_est": str(entry),
        "qty_btc": str(q), "notional_usdt": f"{notional:.2f}", "fee_buy_est": f"{notional * fee:.4f}",
        "sell_qty_after_fee": str(sell_qty),
        "stop_trigger": str(stop), "stop_pct": f"{stop_pct * 100:.2f}",
        "stop_limit_price": str(_floor(stop * D("0.995"), f["tick"])),
        "stop_order_type": "STOP_LOSS_LIMIT trên sàn" if "STOP_LOSS_LIMIT" in f["order_types"] else "UNKNOWN (sàn không báo hỗ trợ)",
        "tp1_price_2R": str(tp1), "tp1_qty": str(half) if can_split else "không chia được (dưới minNotional) — bán toàn bộ khi thoát",
        "strategy_exit": "nến NGÀY đóng < SMA50",
        "expected_loss_usd": f"{loss:.4f}", "expected_loss_pct_of_capital": f"{loss / capital * 100:.2f}",
        "profit_at_tp1_usd": f"{(tp1 - entry) * (half if can_split else sell_qty) - (tp1 * (half if can_split else sell_qty) * fee):.4f}",
        "gap_warning": "Giá có thể nhảy qua stop; STOP_LOSS_LIMIT có thể không khớp, lỗ thật có thể lớn hơn dự kiến."})
    if notional < f["min_notional"] or q < f["min_qty"]:
        out["blockers"].append(f"khối lượng theo mức lỗ ({notional:.2f} USDT) dưới minNotional {f['min_notional']}")
    if max_loss_usd is None: out["blockers"].append("Chủ tịch chưa duyệt mức lỗ tối đa/lệnh")
    if not regime: decision, why = "WAIT", f"giá đóng ngày {last} <= SMA50 {s50:.2f}: chưa có xu hướng tăng"
    elif out["blockers"]: decision, why = "WAIT", "; ".join(out["blockers"])
    else: decision, why = "BUY", "xu hướng tăng (close > SMA50 ngày), rủi ro trong mức duyệt"
    out.update(decision=decision, reason=why,
               invalid_if="giá đóng ngày < SMA50 trước khi vào lệnh; dữ liệu cũ hơn 15 phút; spread > 0.1%; có HALT/STOP; bot chưa cài bản vá")
    return out


if __name__ == "__main__":
    inv = json.load(open(sys.argv[1], encoding="utf-8"))
    for L in (None, 1.5, 2.5, 3.0):
        print(json.dumps({"max_loss_usd": L, **plan(inv, L)}, ensure_ascii=False, indent=1))
