"""Lệnh chạy phiên của Thiên Não — thứ mà bộ lập lịch gọi (lệnh DRAGON SINGULARITY mục 06).

  python scripts/run_cycle.py rule [--run-tests]       một phiên làn luật (GitHub Actions, 12 mốc/ngày)
  python scripts/run_cycle.py ai-claim                 phiên Claude nhận phiên AI + bản giao việc
  python scripts/run_cycle.py ai-finish --run-id … --token … --status SUCCEEDED|FAILED [--summary-json '{…}']
  python scripts/run_cycle.py status                   đọc trạng thái mới nhất, không ghi gì

In ra đúng MỘT dòng JSON. Mã thoát: 0 = chạy xong hoặc bỏ qua có lý do (đã chạy, đang khóa, đang dừng);
1 = phiên thất bại sau khi đã thử lại; 2 = không chạy được an toàn (trạng thái hỏng, mất khóa, sổ sai).

Không gọi AI, không đọc secret. Trạng thái nằm trên nhánh `tn-state` của chính repo này; việc ghi dùng
quyền git sẵn có của nơi gọi (GITHUB_TOKEN của workflow, hoặc quyền của phiên Claude).
"""
import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from crown import cycle as C  # noqa: E402
from crown import policy as P  # noqa: E402
from crown import registry as R  # noqa: E402
from crown import runner as RN  # noqa: E402
from crown import statesync as S  # noqa: E402
from crown.tasks import TaskError  # noqa: E402

TEST_TIMEOUT_SECONDS = 600


def parse_unittest_output(text: str, returncode: int) -> dict:
    """Đọc kết quả `python -m unittest`. Không có test nào chạy, hoặc mã thoát khác 0, đều KHÔNG tính là đạt."""
    ran = re.search(r"^Ran (\d+) tests? in", text, re.M)
    n = int(ran.group(1)) if ran else 0
    failed = sum(int(x) for x in re.findall(r"(?:failures|errors|unexpected successes)=(\d+)", text))
    last = [l for l in text.strip().splitlines() if l.strip()][-3:]
    ok = bool(ran) and n > 0 and returncode == 0 and failed == 0 and bool(re.search(r"^OK\b", text, re.M))
    return {"ran": n, "failed": failed if failed or ok else max(failed, 1), "ok": ok, "tail": " | ".join(last)[-400:]}


def run_unit_tests(root: Path = ROOT) -> dict:
    try:
        r = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-t", "."], cwd=root,
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=TEST_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        return {"ran": 0, "failed": 1, "ok": False, "tail": f"bộ test chạy quá {TEST_TIMEOUT_SECONDS} giây"}
    return parse_unittest_output(r.stderr + "\n" + r.stdout, r.returncode)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="run_cycle.py", description="Chạy một phiên của Thiên Não")
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(sp):
        sp.add_argument("--repo", default=str(ROOT), help="thư mục repo có remote `origin` (mặc định: repo chứa lệnh này)")
        sp.add_argument("--db", default=str(ROOT / "runtime" / "tn" / "state.db"), help="database cục bộ của phiên")
        sp.add_argument("--branch", default="tn-state")
        return sp

    r = common(sub.add_parser("rule"))
    r.add_argument("--trigger", choices=["schedule", "manual"])
    r.add_argument("--run-tests", action="store_true", help="chạy bộ kiểm thử và đưa kết quả vào pha kiểm sức khỏe")
    r.add_argument("--tests-json", help="tệp JSON kết quả test có sẵn: {ran, failed, ok, tail}")
    r.add_argument("--inbox", help="tệp hộp việc cục bộ (mặc định: state/inbox.json trên nhánh trạng thái)")
    a = common(sub.add_parser("ai-claim"))
    a.add_argument("--trigger", choices=["schedule", "manual"])
    f = common(sub.add_parser("ai-finish"))
    f.add_argument("--run-id", required=True)
    f.add_argument("--token", required=True, type=int)
    f.add_argument("--status", required=True, choices=["SUCCEEDED", "FAILED"])
    f.add_argument("--summary-json", default="{}")
    f.add_argument("--error")
    common(sub.add_parser("status"))
    args = p.parse_args(argv)

    trigger = getattr(args, "trigger", None) or ("schedule" if os.environ.get("DRAGON_MODE") == "scheduled" else "manual")
    code, out = 0, {"ok": True, "cmd": args.cmd}
    try:
        run = RN.Runner(args.repo, args.db, inbox_path=getattr(args, "inbox", None), branch=args.branch)
        if args.cmd == "rule":
            tests = None
            if args.tests_json:
                tests = json.loads(Path(args.tests_json).read_text(encoding="utf-8"))
            elif args.run_tests:
                tests = run_unit_tests()
            res = run.rule(trigger=trigger, tests=tests)
            code = 1 if res["status"] == "FAILED" else 0
        elif args.cmd == "ai-claim":
            res = run.ai_claim(trigger=trigger)
        elif args.cmd == "ai-finish":
            reported = json.loads(args.summary_json)
            if not isinstance(reported, dict):
                raise ValueError("--summary-json phải là một object JSON")
            res = run.ai_finish(args.run_id, args.token, args.status, reported, args.error)
        else:
            res = run.status()
        out["result"] = res
        summary = os.environ.get("GITHUB_STEP_SUMMARY")
        if summary and run.last_report:
            with open(summary, "a", encoding="utf-8") as fh:
                fh.write(run.last_report + "\n")
    except (S.StateError, RN.RunnerError, C.CycleError, R.RegistryError, P.PolicyError, TaskError, ValueError, OSError) as e:
        code, out = 2, {"ok": False, "cmd": args.cmd, "error": f"{type(e).__name__}: {e}"}
    print(json.dumps(out, ensure_ascii=False, default=str))
    return code


if __name__ == "__main__":
    sys.exit(main())
