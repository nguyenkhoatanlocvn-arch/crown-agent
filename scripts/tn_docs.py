"""Sinh tài liệu D03 — BẢNG QUYỀN từ chính dữ liệu trong registries/ (không viết tay, nên không lệch).

  python scripts/tn_docs.py            ghi docs/thien_nao/D03_PERMISSION_MATRIX.md
  python scripts/tn_docs.py --check    chỉ kiểm tệp đang có khớp dữ liệu (mã thoát 1 nếu lệch)
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from crown import policy as P  # noqa: E402
from crown import registry as R  # noqa: E402

OUT = ROOT / "docs" / "thien_nao" / "D03_PERMISSION_MATRIX.md"
CLASS_VI = {"AUTONOMOUS": "Tự chủ", "SENSITIVE": "Cần phê duyệt riêng", "CHAIRMAN_ONLY": "Chỉ Chủ tịch"}


def _name(reg: R.Registry, mid: str) -> str:
    m = reg.member(mid)
    return str(m.get("name") or m.get("role") or mid).split(" — ")[0].split(" (")[0][:60]


def build() -> str:
    reg = R.load()
    pol = P.Policy.load(reg)
    mx = pol.matrix
    vt = json.loads((R.REG_DIR / "vuong_toa.json").read_text(encoding="utf-8"))
    tn = json.loads((R.REG_DIR / "thien_nao.json").read_text(encoding="utf-8"))
    acts = mx["actions"]
    auto = [a for a, v in acts.items() if v["class"] == "AUTONOMOUS"]
    L = ["# D03 — BẢNG QUYỀN (PERMISSION MATRIX) · Thiên Não Tối Thượng", "",
         "> Tệp này do `python scripts/tn_docs.py` sinh ra từ `registries/`. Không sửa tay. Muốn đổi quyền: sửa "
         "`registries/permissions.json` trong pull request do Chủ tịch gộp, rồi chạy lại lệnh.", "",
         f"- Nguồn: permissions.json `{mx['version']}` · thien_nao.json `{tn['version']}` · vuong_toa.json `{vt['version']}`",
         f"- Căn cứ: {mx['decision_ref']}",
         "- Trong tài liệu này \"Vương Toạ\" = Chủ tịch Nguyễn Khoa Tấn Lộc (mã `CHAIRMAN`), theo lệnh DRAGON SINGULARITY mục 02.", "",
         "## 1. Ba loại hành động", "",
         "| Loại | Nghĩa | Ai thi hành |", "|---|---|---|",
         "| Tự chủ | Được tự làm nếu có trong dòng quyền của mình | `crown/policy.py` — `decide()` trả ALLOW |",
         "| Cần phê duyệt riêng | Mỗi việc phải có một dòng phê duyệt của Chủ tịch trong `registries/approvals.json` (đúng người, đúng hành động, còn hạn) | `decide()` trả NEEDS_APPROVAL cho tới khi có dòng hợp lệ |",
         "| Chỉ Chủ tịch | Agent chỉ được đề xuất; không ủy quyền được | `decide()` luôn trả DENY với mọi Agent |", "",
         "## 2. Danh mục hành động", "", "| Hành động | Loại | Mô tả |", "|---|---|---|"]
    L += [f"| `{a}` | {CLASS_VI[v['class']]} | {v['desc']} |" for a, v in acts.items()]
    L += ["", "## 3. Quyền tự chủ của từng thành viên", "",
          "Với mọi Agent (kể cả Thiên Não): 5 hành động \"Cần phê duyệt riêng\" đều ở trạng thái chờ phê duyệt, "
          "5 hành động \"Chỉ Chủ tịch\" đều bị từ chối. Bảng dưới chỉ liệt kê phần tự chủ.", "",
          "| Thành viên | Vai | Mức quyền | Được tự chủ |", "|---|---|---|---|"]
    for row in pol.matrix_rows():
        who = row["principal"]
        if who == "CHAIRMAN":
            L.append("| `CHAIRMAN` | Vương Toạ — Chủ tịch | — | **Toàn quyền** |")
            continue
        yes = [a for a in auto if row[a] == "ALLOW"]
        L.append(f"| `{who}` | {_name(reg, who)} | {row['tier']} | {', '.join(yes) or '—'} |")
    bad = [r["principal"] for r in pol.matrix_rows() if r["principal"] != "CHAIRMAN"
           and any(r[a] == "ALLOW" for a, v in acts.items() if v["class"] != "AUTONOMOUS")]
    L += ["", f"Kiểm tự động khi sinh tài liệu: số Agent đang được phép làm việc nhạy cảm hoặc việc chỉ-Chủ-tịch mà không có phê duyệt = **{len(bad)}**"
              + (f" ({', '.join(bad)})" if bad else "") + ".", "",
          "## 4. Ai được đặt mục tiêu tại Vương Toạ (giao việc cho Agent Vua)", "",
          "| Người giao | Phạm vi được giao | Được dừng | Được duyệt thay Chủ tịch |", "|---|---|---|---|"]
    for pid, v in vt["principals"].items():
        L.append(f"| `{pid}` | {', '.join(v['scopes']) or '— (chưa ủy nhiệm)'} | {'có' if v.get('can_stop') else 'không'} | {'có' if v.get('can_approve') else 'không'} |")
    L += ["", "Phạm vi: " + " · ".join(f"**{k}** — {d}" for k, d in vt["domains"].items()) + ".",
          f"Phạm vi chỉ Chủ tịch giao: {', '.join(vt['chairman_only_domains'])}.", "",
          "## 5. Mười hai ghế Thiên Não", "",
          "| Ghế | Vai | Người giữ | Bậc mô hình | Suy luận | Công cụ | Trần mỗi việc / ngày |", "|---|---|---|---|---|---|---|"]
    for s in tn["seats"]:
        b = s["budget"]
        holder = s.get("holder") or "(ghế mới)"
        st = reg.member(reg.seat_owner(s["id"])).get("status")
        L.append(f"| `{s['id']}` | {s['name']} | {holder} · {st} | {s['model_tier']} | {s['reasoning']} | {', '.join(s['tools'])} | "
                 f"{b['max_tokens_per_task']:,} token · {b['max_minutes_per_task']} phút · {b['max_ai_calls_per_day']} lượt AI/ngày · {b['max_usd_per_day']} USD/ngày |")
    L += ["", "Trần token là giới hạn mềm ghi trong chỉ thị của ghế. Giới hạn cứng do bộ máy thi hành là số lượt theo bậc mô hình mỗi ngày, "
              "số phiên AI mỗi ngày, số phút mỗi phiên và ngân sách chi (hiện bằng 0).", "",
          "## 6. Đơn vị dưới Thiên Não", "", "| Đơn vị | Loại | Không qua | Giới hạn cứng |", "|---|---|---|---|"]
    for u in tn["units"]:
        L.append(f"| `{u['id']}` | {u['kind']} | {', '.join(u.get('bypass', [])) or '—'} | {'; '.join(u['hard_limits'])} |")
    bud = tn["budget"]
    L += ["", "## 7. Ngân sách và lịch (Chủ tịch chỉnh trong `registries/thien_nao.json`)", "",
          f"- Chi tiền: {bud['usd_per_day']} USD/ngày · {bud['usd_per_month']} USD/tháng (mặc định chưa kích hoạt khoản chi mới).",
          f"- Phiên AI: tối đa {bud['ai_sessions_per_day']} phiên/ngày, mỗi phiên tối đa {bud['ai_tasks_per_session']} việc.",
          "- Lượt giao việc theo bậc mô hình mỗi ngày: " + " · ".join(f"{k} {v}" for k, v in bud["tier_calls_per_day"].items()) + ".",
          f"- Làn luật: phút {tn['schedule']['rule_lane']['minute']} mỗi {tn['schedule']['rule_lane']['every_hours']} giờ, múi giờ {tn['schedule']['timezone']}; "
          f"mỗi phiên xử lý tối đa {bud['tasks_per_rule_cycle']} việc.",
          f"- Làn AI: {', '.join(tn['schedule']['ai_lane']['times'])}.", "",
          "## 8. Tệp được bảo vệ", "",
          "Không Agent nào được sửa các tệp sau; `registries/CONSTITUTION.lock` giữ mã băm của chúng và mỗi phiên, mỗi pull request đều kiểm lại:", ""]
    L += [f"- `{p}`" for p in mx["protected_paths"]]
    L += ["", "## 9. Dừng khẩn cấp", "",
          "- Bật: Chủ tịch và các thành viên có `EMERGENCY_STOP_SET`. Cách nhanh nhất cho Chủ tịch: tạo tệp `state/STOP` trên nhánh `tn-state`.",
          "- Gỡ: chỉ Chủ tịch (`EMERGENCY_STOP_CLEAR`); với tệp `state/STOP` là xóa tệp. Bộ máy không bao giờ tự gỡ.",
          f"- Trong lúc dừng, Agent chỉ còn được: {', '.join(mx['estop_allowed'])}.", "",
          "## 10. Điều mã thi hành được và điều phải dựa vào cài đặt GitHub", "",
          "| Luật | Thi hành bằng | Mức chắc chắn |", "|---|---|---|",
          "| Agent không tự cấp quyền trong bảng quyền | `Policy.validate()` từ chối bảng có `*` hoặc cấp sẵn việc nhạy cảm; test `tests/test_policy.py` | Chắc trong phạm vi mã |",
          "| Tệp quyền bị đổi ngoài quy trình | Khóa hiến pháp + CI + kiểm sức khỏe mỗi phiên | Phát hiện được, không ngăn được việc ghi |",
          "| Không ai ngoài Chủ tịch gộp thay đổi vào nhánh chính | Quy tắc bảo vệ nhánh của GitHub (Settings → Rules) | Ngoài tầm của mã; chưa kiểm được đã bật hay chưa — việc của Chủ tịch |",
          "| Workflow chỉ ghi nhánh `tn-state` | `GitState.push()` chỉ nhận đường dẫn `state/` và một nhánh | Chắc với mã này; token của workflow vẫn có quyền ghi cả repo nếu nhánh chính chưa được bảo vệ |",
          "| Gỡ dừng khẩn cấp chỉ Chủ tịch | Bảng quyền (công tắc trong database); tệp `state/STOP` thì ai có quyền ghi repo cũng xóa được | Dựa vào quyền ghi repo |", ""]
    return "\n".join(L)


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    text = build()
    if "--check" in argv:
        ok = OUT.exists() and OUT.read_text(encoding="utf-8") == text
        print("OK — D03 khớp dữ liệu" if ok else "LỆCH — chạy lại: python scripts/tn_docs.py")
        return 0 if ok else 1
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(text, encoding="utf-8")
    print(f"Đã ghi {OUT.relative_to(ROOT)} ({len(text.splitlines())} dòng)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
