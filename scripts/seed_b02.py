"""Nạp nhiệm vụ THẬT của B02_BTC ngày 2026-11-04 (idempotent theo --key). Bằng chứng là commit/test/file có thật."""
import subprocess, sys, json, re
def c(*a):
    r = subprocess.run([sys.executable, "-m", "crown", *a], capture_output=True, text=True); out = (r.stdout + r.stderr).strip()
    print("$ crown", " ".join(a[:3]), "->", out[:160]); return out
def tid(out):
    m = re.search(r"T-[0-9a-f]{10}", out); return m.group(0) if m else None
def token(out):
    m = re.search(r"token[^0-9]*(\d+)", out); return m.group(1) if m else None

T = {}
specs = [
 ("A17", "b02-01", "B02-01 Kiểm kê thực tế BTCDesk: tiến trình, lịch tự chạy, số dư, lệnh mở, quyền API key (script chỉ đọc)", "File inventory JSON từ laptop, đối soát xong, không lộ key"),
 ("A11", "b02-02", "B02-02 Vá an toàn bot BTC trên bản sao: N1 HALT vẫn cắt lỗ, N3, Q1, Q4, Q6", "Test đạt, review độc lập, gói cài + quay lại trên laptop"),
 ("A03", "b02-03", "B02-03 SOURCE_REGISTER: ccxt, freqtrade, TA-Lib, lightweight-charts, vectorbt, tài liệu Binance (license, version)", "Bảng ADOPT/RESEARCH/REJECT có nguồn + ngày"),
 ("CFO", "b02-05", "B02-05 RISK_POLICY đề xuất + TRADE_PLAN BTCUSDT bằng giá thật (vốn trần 30 USD)", "Chủ tịch chốt mức lỗ; TRADE_PLAN từ dữ liệu sàn có timestamp"),
 ("A18", "b02-06", "B02-06 Phòng BTC trong Hoàng thành + module web B02_BTC (2D trước)", "Màn hình tổng quan/lệnh/kế hoạch dùng dữ liệu thật"),
 ("A11", "b02-02b", "B02-02b Single-writer: một executor duy nhất gửi lệnh (gộp lệnh tay vào cổng chung)", "Test 2 writer bị chặn; Chủ tịch duyệt cutover"),
 ("A20", "b02-09", "B02-09 Ma trận kiểm định PASS/FAIL/NOT_RUN/BLOCKED trước khi chạy tiền thật", "Ma trận có log + commit cho từng mục"),
]
for owner, key, obj, crit in specs:
    T[key] = tid(c("task", "new", owner, obj, crit, "--actor", "VUA_CEO", "--ready", "--key", key, "--company", "BTC"))
json.dump(T, open("runtime/b02_tasks.json", "w"), indent=1); print(T)
