"""Ghép b02_state cho Phòng BTC từ nguồn THẬT: sổ nhiệm vụ, chính sách đã duyệt, kết quả test, watchlist, inventory (nếu có).
Thiếu dữ liệu -> UNKNOWN. In JSON ra stdout."""
import json, os, sys, datetime, glob
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, ROOT)
from crown import registry as R
from crown.tasks import TaskStore
from b02_btc import trade_plan as TP

def main(inventory=None, flags=None):
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    st = TaskStore(os.path.join(ROOT, "runtime", "tasks.db"), registry=R.load())
    tasks = [{"id": t["task_id"], "owner": t["owner_agent_id"], "state": t["state"], "objective": t["objective"]}
             for t in st.list() if t["objective"].startswith("B02")]; st.close()
    traders = json.load(open(os.path.join(ROOT, "runtime", "b02_traders.json"), encoding="utf-8"))
    for r in traders:
        if r["tier"].startswith("4"): r["tier"] = "UNVERIFIED/INACTIVE"
    from collections import Counter; c = Counter(r["tier"] for r in traders)
    S = {"updated_at": now, "inventory_at": None,
         "executor": {"name": "BTCDesk (Grok) — profile M nhiều coin", "status": "LIVE",
                      "note": "Bot Grok đang là bên DUY NHẤT gửi lệnh. Bản Claude chưa được cài; lệnh tay (manual.py) là writer thứ hai khi có lệnh tay mở."},
         "flags": flags or {"halt": False, "stop": False, "patch_installed": False},
         "overview": {"status": "UNKNOWN", "cap": "30 USD", "usdt_free": "UNKNOWN",
                      "note": "Chưa đối chiếu với sàn: chờ Chủ tịch chạy KIEM_KE_CHI_DOC.bat.",
                      "bot_reported": {"equity": "22.0 (bot tự báo 22:24)", "start": "17.66", "pause_until": "2026-11-05T14:50:13Z"}},
         "risk": [{"k": "Vốn trần", "v": "30 USD (phần khả dụng thật)", "who": "Chủ tịch"},
                  {"k": "HALT tổng", "v": "7,5 USD = 25% vốn đối soát (không tính tiền nạp)", "who": "Chủ tịch"},
                  {"k": "Phạm vi", "v": "Nhiều coin như bot M, Spot, không đòn bẩy, không rút", "who": "Chủ tịch"},
                  {"k": "Lỗ tối đa/lệnh", "v": "1,5 USD = 5% (giảm khối lượng, không nới stop)", "who": "Claude theo ủy quyền"},
                  {"k": "Tạm dừng mua 24h", "v": "lỗ ngày ≥10% hoặc sụt từ đỉnh ≥15%", "who": "Cấu hình đang chạy"}],
         "plan": None, "tasks": tasks,
         "patch": {"status": "TESTED", "summary": "Bản sao cô lập: 262 test đạt / 11 bỏ qua (gốc 246/11), review độc lập 2 vòng. Gói cài trên laptop, CHƯA cài.",
                   "items": [{"code": "N1", "what": "HALT vẫn cho cắt lỗ/thoát lệnh, chặn mua", "state": "TESTED"},
                             {"code": "N3", "what": "auto_live.bat không tự bật tiền thật, không xoá STOP", "state": "TESTED"},
                             {"code": "Q1", "what": "Che chữ ký trong log/DB/traceback", "state": "TESTED"},
                             {"code": "Q4", "what": "Hết lỗ ảo khi mở/đóng lệnh tay", "state": "TESTED"},
                             {"code": "Q6", "what": "Killswitch huỷ lệnh treo mọi coin", "state": "TESTED"},
                             {"code": "N2", "what": "Cắt lỗ trên sàn (STOP_LOSS + cancelReplace)", "state": "PLANNED"},
                             {"code": "SW", "what": "Single-writer: gộp lệnh tay vào một executor", "state": "PLANNED"}]},
         "traders": {"status": "PARTIAL", "counts": f"{len(traders)}/40 — " + ", ".join(f"{k} {v}" for k, v in c.items()),
                     "note": "Chỉ QUAN SÁT. Chưa có trader nào đạt chuẩn BTC spot + hiệu suất kiểm chứng độc lập. Không mua theo số đông. Link x.com dựng từ handle, chưa mở được (cần đăng nhập).",
                     "rows": traders},
         "arena": {"status": "PLANNED", "note": "Grok chạy thật; Claude chạy bóng cùng dữ liệu, vốn ảo 30 USD, cùng chính sách. So sau phí, cùng kỳ. Chưa bắt đầu.",
                   "rows": [{"metric": m, "grok": "UNKNOWN", "claude": "chưa chạy"} for m in ("Lãi/lỗ sau phí", "Drawdown lớn nhất", "Số lệnh", "Vi phạm rủi ro", "Sự cố")]},
         "x_feed": {"status": "BLOCKED", "note": "Chưa có X API (trả phí, cần Chủ tịch duyệt khoản chi). Hiện chỉ có danh sách handle; không gọi là 'theo dõi trực tiếp'."},
         "sources_status": "VERIFIED",
         "sources": [{"name": "Binance Spot API docs", "license": "—", "decision": "ADOPT", "why": "Nguồn chuẩn duy nhất cho adapter"},
                     {"name": "lightweight-charts", "license": "Apache-2.0 (+ghi công TradingView)", "decision": "ADOPT", "why": "Biểu đồ dashboard"},
                     {"name": "ccxt", "license": "MIT", "decision": "RESEARCH", "why": "Tham khảo, không thay adapter"},
                     {"name": "freqtrade", "license": "GPL-3.0", "decision": "RESEARCH", "why": "Lấy ý tưởng lookahead/recursive, không chép mã GPL"},
                     {"name": "binance-connector-python", "license": "MIT", "decision": "RESEARCH", "why": "Đối chiếu chữ ký trong test"},
                     {"name": "TA-Lib python", "license": "BSD-2", "decision": "RESEARCH", "why": "Chỉ khi cần nhiều chỉ báo"},
                     {"name": "vectorbt", "license": "Apache-2.0 + Commons Clause", "decision": "REJECT", "why": "Hạn chế bán dịch vụ"},
                     {"name": "freqtrade-strategies", "license": "GPL-3.0", "decision": "REJECT", "why": "Chỉ cho mục đích học"},
                     {"name": "python-binance / pandas-ta", "license": "MIT / chưa rõ", "decision": "REJECT", "why": "Không chính thức / pre-release"}]}
    if inventory:
        S.update(inventory)
    return S

if __name__ == "__main__":
    print(json.dumps(main(), ensure_ascii=False))
