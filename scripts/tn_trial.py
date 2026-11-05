"""Chạy thử chu trình Thiên Não trong môi trường an toàn (lệnh DRAGON SINGULARITY mục 20 bước 8).

Dựng một "GitHub giả" là kho git trần trong thư mục tạm, đẩy mã hiện tại vào đó, rồi chạy các phiên như bộ
lập lịch sẽ chạy. Không đụng GitHub thật, không gọi AI, không cần mạng.

  python scripts/tn_trial.py [--out BAO_CAO.md] [--keep]

Phiên 1 chạy bằng đúng lệnh của workflow (đồng hồ thật, bộ test thật). Các phiên sau dùng đồng hồ giả để
nhảy tới mốc kế tiếp. Phần việc cần AI do kịch bản đóng thế và được ghi nhãn MÔ PHỎNG.
Mã thoát 0 nếu mọi mục kiểm đạt.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from crown import cycle as C  # noqa: E402
from crown import runner as RN  # noqa: E402
from crown import statesync as S  # noqa: E402
from crown import thiennao as TN  # noqa: E402
from crown.vua import Vua  # noqa: E402

UTC = timezone.utc
INBOX = {"items": [{
    "key": "trial-web-in-git", "name": "[CHẠY THỬ] Đưa mã web đang chạy vào repo",
    "objective": "Ba tệp của web Hoàng Thành nằm trong repo, kèm sha256",
    "acceptance": "sha256 trong repo khớp bản đang phát hành", "seat": "TN-05", "project": "HOANG_THANH",
    "priority": "P1", "risk_level": "LOW", "work_type": "CODE", "action_class": "CODE_BRANCH_WRITE"}]}


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


class Clock:
    def __init__(self, now):
        self.now = now

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += timedelta(seconds=seconds)


class Trial:
    def __init__(self, tmp: Path):
        self.tmp = tmp
        self.rows: list[tuple[str, bool, str]] = []
        self.remote = tmp / "github-gia.git"
        git(tmp, "init", "-q", "--bare", "-b", "master", str(self.remote))
        git(ROOT, "push", "-q", str(self.remote), "HEAD:refs/heads/master")
        self.clock = Clock(datetime.now(UTC))
        self.inbox = tmp / "inbox.json"
        self.inbox.write_text(json.dumps(INBOX, ensure_ascii=False), encoding="utf-8")
        self.n = 0

    def clone(self, name=None):
        self.n += 1
        d = self.tmp / (name or f"may-{self.n}")
        if not d.exists():
            git(self.tmp, "clone", "-q", str(self.remote), str(d))
        return d

    def runner(self, **kw):
        d = self.clone()
        return RN.Runner(d, self.tmp / (d.name + ".db"), clock=self.clock, sleep=self.clock.sleep, inbox_path=self.inbox, **kw)

    def check(self, name: str, ok: bool, seen: str):
        self.rows.append((name, bool(ok), seen))
        print(("ĐẠT   " if ok else "TRƯỢT ") + name + " — " + seen, flush=True)

    def files(self):
        return S.GitState(self.clone("nguoi-doc")).pull()

    def latest(self):
        return json.loads(self.files()["state/latest.json"])

    def commits(self):
        return int(git(self.remote, "rev-list", "--count", "refs/heads/tn-state"))

    def next_rule_slot(self):
        nxt = C.slots_between("rule", self.clock.now, self.clock.now + timedelta(hours=3), C.DEFAULT_SCHEDULE)[0]
        self.clock.now = nxt.astimezone(UTC) + timedelta(seconds=5)
        return C.slot_id(nxt)

    def next_ai_slot(self):
        nxt = C.slots_between("ai", self.clock.now, self.clock.now + timedelta(hours=7), C.DEFAULT_SCHEDULE)[0]
        self.clock.now = nxt.astimezone(UTC) + timedelta(seconds=3)
        return C.slot_id(nxt)

    def simulated_agent_vua(self, db, brief):
        """MÔ PHỎNG: đóng thế Agent Vua + thợ + người kiểm A20, đi đúng luật của sổ nhiệm vụ."""
        v = Vua(db, clock=self.clock)
        try:
            for t in brief["tasks"]:
                rid, owner = t["request_id"], t["OWNER_AGENT"]
                c = v.claim(request_id=rid, key="S1", worker=owner)
                v.evidence(request_id=rid, key="S1", worker=owner, token=c["token"], evidence=["MÔ PHỎNG: sha256 khớp 3/3 tệp"])
                v.move(request_id=rid, key="S1", to="REVIEW", actor=owner, token=c["token"])
                v.review(request_id=rid, key="S1", reviewer="A20", verdict="PASS",
                         checks=[{"text": "sha256 khớp", "passed": True, "evidence": "MÔ PHỎNG"}])
                r = v.claim(request_id=rid, key="ROOT", worker="VUA_CEO")
                v.evidence(request_id=rid, key="ROOT", worker="VUA_CEO", token=r["token"], evidence=["MÔ PHỎNG: việc con đã VERIFIED_DONE"])
                v.move(request_id=rid, key="ROOT", to="REVIEW", actor="VUA_CEO", token=r["token"])
                v.review(request_id=rid, key="ROOT", reviewer="A20", verdict="PASS",
                         checks=[{"text": "việc con xong", "passed": True, "evidence": "MÔ PHỎNG"}])
        finally:
            v.close()

    # ---------- kịch bản ----------
    def run(self, with_tests: bool):
        # 1. Đúng lệnh của workflow, đồng hồ thật.
        gha = self.clone("gha-runner")
        cmd = [sys.executable, str(gha / "scripts" / "run_cycle.py"), "rule"] + (["--run-tests"] if with_tests else [])
        env = {**os.environ, "DRAGON_MODE": "scheduled"}
        env.pop("GITHUB_STEP_SUMMARY", None)
        p = subprocess.run(cmd, cwd=gha, capture_output=True, text=True, encoding="utf-8", env=env, timeout=900)
        out = json.loads(p.stdout.strip().splitlines()[-1]) if p.stdout.strip() else {"ok": False, "error": p.stderr[-300:]}
        res = out.get("result", {})
        health = res.get("summary", {}).get("health", {})
        self.check("1. Phiên theo lệnh của workflow chạy xong 9 pha", p.returncode == 0 and res.get("status") == "SUCCEEDED"
                   and res.get("summary", {}).get("phases_done") == C.PHASES, f"mã thoát {p.returncode}, {res.get('status')}, {res.get('run_id')}")
        want = "PASS" if with_tests else "SKIP"
        tests = self.latest()["phase_details"]["SYSTEM_AUDIT"].get("tests")
        self.check(f"   Kiểm sức khỏe: sổ đăng ký, bảng quyền, sổ nhiệm vụ đạt; bộ test {want}",
                   (health.get("registry"), health.get("policy"), health.get("ledger"), health.get("tests")) == ("PASS", "PASS", "PASS", want),
                   f"{health} · test: {tests}")
        first = res.get("run_id", "")
        self.clock.now = datetime.now(UTC)

        # 2. Cùng mốc bị gọi lần hai (bấm tay) từ máy khác.
        n = self.commits()
        dup = self.runner().rule(trigger="manual")
        self.check("2. Cùng mốc gọi lần hai bị từ chối, không ghi gì", (dup["status"], dup.get("reason")) == ("SKIP", "DUPLICATE") and self.commits() == n,
                   f"{dup['status']}/{dup.get('reason')}, số commit trạng thái {n} → {self.commits()}")

        # 3. Phiên chết giữa chừng sau khi đã giao việc.
        slot_b = self.next_rule_slot()
        crashed = False
        try:
            self.runner(crash_after="DELEGATION").rule()
        except TN.SimulatedCrash:
            crashed = True
        self.check("3. Phiên kế bị giết sau pha giao việc (giả lập)", crashed, f"mốc {slot_b}")

        # 4. Phiên sau phát hiện, đánh dấu, làm tiếp, không tạo việc trùng.
        slot_c = self.next_rule_slot()
        rec = self.runner().rule()
        lt = self.latest()
        mine = [t for t in lt["board"]["tasks"] if t["TASK_NAME"].startswith("[CHẠY THỬ]")]
        self.check("4. Phiên sau khôi phục: phiên chết thành INTERRUPTED, việc không bị tạo trùng",
                   rec["status"] == "SUCCEEDED" and rec["recovered"] == [f"rule-{slot_b}"] and len(mine) == 1,
                   f"{rec['status']}, khôi phục {rec['recovered']}, số việc chạy thử {len(mine)}")
        t = mine[0] if mine else {}
        self.check("   Planner → model router → Agent Vua: việc đã giao đúng người, đúng mô hình",
                   (t.get("STATUS"), t.get("OWNER_AGENT"), t.get("ASSIGNED_MODEL")) == ("ASSIGNED", "A18", "sonnet"),
                   f"{t.get('STATUS')}, {t.get('OWNER_AGENT')}, {t.get('ASSIGNED_MODEL')}")

        # 5. Hai máy tranh nhau cùng một mốc.
        self.next_rule_slot()
        a, b = self.runner(), self.runner()
        a.load(); b.load()
        ba, bb = a.claim_rule(), b.claim_rule()
        done = a.work_rule(ba) if ba["action"] != "SKIP" else {"status": "?"}
        self.check("5. Hai máy cùng nhận một mốc: chỉ một máy được chạy",
                   (ba["action"], bb["action"], bb.get("reason"), done["status"]) == ("RUN", "SKIP", "LOCKED", "SUCCEEDED"),
                   f"máy A {ba['action']}, máy B {bb['action']}/{bb.get('reason')}, A kết thúc {done['status']}")

        # 6. Làn AI: nhận bản giao việc, làm qua sổ, có người kiểm khác người làm.
        slot_ai = self.next_ai_slot()
        ai = self.runner()
        brief = ai.ai_claim()
        names = [x["TASK_NAME"] for x in brief.get("tasks", [])]
        self.check("6. Phiên AI nhận bản giao việc có luật cứng và đúng việc đã giao",
                   brief["action"] == "RUN" and names == [t.get("TASK_NAME")] and bool(brief["hard_rules"]),
                   f"{brief['action']} {brief.get('run_id')}, việc: {names}, {len(brief.get('hard_rules', []))} luật cứng")
        self.clock.sleep(600)
        blocked = self.runner().rule(trigger="manual")
        self.check("   Trong lúc phiên AI giữ khóa, phiên luật không chen vào", (blocked["status"], blocked.get("reason")) == ("SKIP", "LOCKED"),
                   f"{blocked['status']}/{blocked.get('reason')}")
        self.simulated_agent_vua(ai.db_path, brief)
        fin = ai.ai_finish(brief["run_id"], brief["token"], "SUCCEEDED", {"note": "MÔ PHỎNG — không có AI nào được gọi"})
        lt = self.latest()
        t2 = next((x for x in lt["board"]["tasks"] if x["TASK_ID"] == t.get("TASK_ID")), {})
        self.check("   Việc hoàn thành sau khi người kiểm độc lập duyệt (MÔ PHỎNG phần AI)",
                   fin["status"] == "SUCCEEDED" and t2.get("STATUS") == "COMPLETED" and t2.get("QUALITY_SCORE") == 1.0,
                   f"phiên {fin['status']}, việc {t2.get('STATUS')}, điểm {t2.get('QUALITY_SCORE')}, phiên AI đã dùng hôm nay {lt['budget']['usage'].get('ai_sessions')}")

        # 7. Dừng khẩn cấp bằng tệp, rồi gỡ.
        st = S.GitState(self.clone("chu-tich"))
        st.pull()
        st.push({"state/STOP": "Chạy thử: Chủ tịch dừng để kiểm tra\n"}, "STOP")
        self.next_rule_slot()
        stopped = self.runner().rule()
        self.check("7. Có tệp state/STOP: phiên không làm việc và không tự gỡ tệp",
                   (stopped["status"], stopped.get("reason")) == ("SKIP", "ESTOP") and "state/STOP" in self.files(),
                   f"{stopped['status']}/{stopped.get('reason')}")
        boss = self.clone("chu-tich")                 # Chủ tịch gỡ tệp bằng một commit xóa (như bấm xóa tệp trên GitHub)
        git(boss, "fetch", "-q", "origin", "tn-state")
        git(boss, "checkout", "-q", "-B", "tn-state", "FETCH_HEAD")
        git(boss, "rm", "-q", "state/STOP")
        git(boss, "-c", "user.name=Chu tich", "-c", "user.email=chairman@example.invalid", "commit", "-q", "-m", "Gỡ dừng khẩn cấp")
        git(boss, "push", "-q", "origin", "tn-state")
        self.next_rule_slot()
        resumed = self.runner().rule()
        self.check("   Gỡ tệp dừng: phiên kế chạy lại bình thường", resumed["status"] == "SUCCEEDED", resumed["status"])

        # 8. Trạng thái bị sửa tay.
        st = S.GitState(self.clone("pha-hoai"))
        files = st.pull()
        st.push({"state/tn_state.sql": files["state/tn_state.sql"] + "-- sửa tay\n"}, "phá")
        n = self.commits()
        self.next_rule_slot()
        refused = ""
        try:
            self.runner().rule()
        except S.StateError as e:
            refused = str(e)
        self.check("8. Trạng thái bị sửa tay: phiên dừng, không ghi đè", "sha256" in refused and self.commits() == n, refused[:90])
        git(self.remote, "update-ref", "refs/heads/tn-state", git(self.remote, "rev-parse", "refs/heads/tn-state~1"))
        return first

    def report(self, first: str, with_tests: bool) -> str:
        lt = self.latest()
        m = lt["metrics"]
        head = git(ROOT, "rev-parse", "--short", "HEAD")
        ok = sum(1 for _, o, _ in self.rows if o)
        L = [f"# Chạy thử chu trình Thiên Não — {datetime.now(UTC).astimezone(timezone(timedelta(hours=7))).strftime('%d/%m/%Y %H:%M')} (giờ Việt Nam)", "",
             f"- Mã: commit `{head}` · lệnh: `python scripts/tn_trial.py`" + ("" if with_tests else " --no-tests"),
             "- Môi trường: kho git trần trong thư mục tạm đóng vai GitHub. Không đụng GitHub thật, không gọi AI, không cần mạng.",
             f"- Phiên 1 (`{first}`) chạy bằng đúng lệnh của workflow với đồng hồ thật; các phiên sau dùng đồng hồ giả để nhảy mốc.",
             "- Phần việc cần AI do kịch bản đóng thế — nhãn MÔ PHỎNG. Kết quả dưới đây chứng minh bộ máy, không chứng minh chất lượng AI.", "",
             f"## Kết quả: {ok}/{len(self.rows)} mục đạt", "", "| Mục kiểm | Kết quả | Quan sát được |", "|---|---|---|"]
        L += [f"| {n.strip()} | {'ĐẠT' if o else 'TRƯỢT'} | {s.replace('|', '/')} |" for n, o, s in self.rows]
        L += ["", "## Số đo sau chạy thử (mẫu rất nhỏ — chỉ để thấy cách đo)", "",
              "| Làn | Mốc | Thành công | Gián đoạn | Không chạy | Khoảng tin cậy 95% | Đã chứng minh 99,99%? |", "|---|---|---|---|---|---|---|"]
        for lane in C.LANES:
            x = m[lane]
            ci = "—" if not x["ci95"] else f"{x['ci95'][0] * 100:.1f}% – {x['ci95'][1] * 100:.1f}%"
            L.append(f"| {lane} | {x['slots']} | {x['succeeded']} | {x['interrupted']} | {x['missed']} | {ci} | {'có' if x['target_9999_demonstrated'] else 'chưa'} |")
        L += ["", f"Số commit trên nhánh trạng thái giả lập: {self.commits()}. Bảng nhiệm vụ cuối: {lt['board']['counts']}.", ""]
        return "\n".join(L)


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--out")
    p.add_argument("--keep", action="store_true", help="giữ thư mục tạm để xem")
    p.add_argument("--no-tests", action="store_true", help="phiên 1 không chạy bộ test (nhanh hơn)")
    a = p.parse_args(argv)
    tmp = Path(tempfile.mkdtemp(prefix="tn-trial-"))
    try:
        t = Trial(tmp)
        first = t.run(with_tests=not a.no_tests)
        md = t.report(first, with_tests=not a.no_tests)
        if a.out:
            Path(a.out).write_text(md, encoding="utf-8")
        print("\n" + md)
        return 0 if all(o for _, o, _ in t.rows) else 1
    finally:
        if a.keep:
            print(f"Thư mục tạm: {tmp}")
        else:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
