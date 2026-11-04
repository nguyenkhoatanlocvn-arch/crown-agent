import unittest
from decimal import Decimal as D
from b02_btc import trade_plan as TP

DAY = 86400000

def inv(trend="up", usdt="30.5", now=200 * DAY + 5):
    kl = []
    for i in range(200):
        c = 60000 + (i * 100 if trend == "up" else -i * 100)
        kl.append([i * DAY, str(c), str(c * 1.01), str(c * 0.99), str(c), "1", (i + 1) * DAY - 1])
    kl.append([200 * DAY, "80000", "80100", "79900", "80000", "1", 201 * DAY - 1])   # nến đang chạy -> bỏ
    last = float(kl[-2][4])
    return {"generated_utc": "t", "clock": {"server_ms": now},
            "btc_book": {"bidPrice": str(last - 1), "askPrice": str(last)},
            "btc_exchange_info": {"orderTypes": ["LIMIT", "MARKET", "STOP_LOSS_LIMIT"], "filters": [
                {"filterType": "PRICE_FILTER", "tickSize": "0.01"}, {"filterType": "LOT_SIZE", "stepSize": "0.00001", "minQty": "0.00001"},
                {"filterType": "MARKET_LOT_SIZE", "stepSize": "0.00000000", "minQty": "0"}, {"filterType": "NOTIONAL", "minNotional": "5.00"}]},
            "btc_klines_1d": kl,
            "account": {"balances": [{"asset": "USDT", "free": usdt, "locked": "0"}], "commissionRates": {"taker": "0.00100000"}}}

class TradePlanTest(unittest.TestCase):
    def test_missing_data_unknown(self):
        self.assertEqual(TP.plan({})["decision"], "UNKNOWN")

    def test_no_approved_limit_waits(self):
        p = TP.plan(inv(), None); self.assertEqual(p["decision"], "WAIT"); self.assertIn("chưa duyệt", p["reason"])

    def test_buy_respects_cap_and_loss(self):
        p = TP.plan(inv(usdt="100"), 2.5)
        self.assertEqual(p["decision"], "BUY")
        self.assertLessEqual(D(p["order"]["notional_usdt"]), D(30))
        self.assertLessEqual(D(p["order"]["expected_loss_usd"]), D("2.5"))
        self.assertTrue(D("0.05") <= D(p["order"]["stop_pct"]) / 100 <= D("0.12"))

    def test_loss_limit_shrinks_size_not_widens_stop(self):
        a = TP.plan(inv(), 3.0)["order"]; b = TP.plan(inv(), 1.0)["order"]
        self.assertEqual(a["stop_trigger"], b["stop_trigger"]); self.assertLess(D(b["qty_btc"]), D(a["qty_btc"]))

    def test_too_small_waits_not_round_up(self):
        p = TP.plan(inv(), 0.2); self.assertEqual(p["decision"], "WAIT"); self.assertIn("minNotional", p["reason"])

    def test_downtrend_waits(self):
        self.assertEqual(TP.plan(inv("down"), 2.5)["decision"], "WAIT")

    def test_open_candle_ignored(self):
        p = TP.plan(inv(), 2.5); self.assertNotEqual(p["market"]["last_daily_close"], "80000")

if __name__ == "__main__": unittest.main()
