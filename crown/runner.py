"""Bộ chạy phiên của Thiên Não: nối chu trình 9 pha với trạng thái bền trên nhánh git.

Máy chạy theo lịch (GitHub Actions, phiên Claude) là máy tạm. Mỗi phiên:
  1. Đọc nhánh trạng thái → dựng lại database (hỏng thì dừng, không ghi đè).
  2. Nhận phiên (khóa + run_id) rồi ĐẨY NGAY để máy khác thấy khóa. Đẩy không ép: nếu bên khác
     ghi trước thì đọc lại và quyết định lại — hai máy không thể cùng thắng.
  3. Làm việc trên database cục bộ.
  4. Ghi kết quả và đẩy. Nếu nhánh đã đổi trong lúc làm: chỉ ghi tiếp khi database trên nhánh vẫn
     là bản mình đã đọc (bên kia chỉ thêm tệp phụ như state/STOP); ngược lại từ chối ghi đè.

Lỗi trong phiên: ghi FAILED, chờ backoff rồi thử lại ngay trong cùng lượt chạy, tối đa
run_max_attempts lần; hết lượt thì phiên nằm trong hàng chờ người quyết.
"""
from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from . import cycle as C
from . import statesync as S
from . import thiennao as TN

STOP_PATH = "state/STOP"
INBOX_PATH = "state/inbox.json"
MAX_CLAIM_ROUNDS = 3


class RunnerError(Exception):
    """Bộ chạy không ghi được trạng thái một cách an toàn."""


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class Runner:
    def __init__(self, repo: str | Path, db_path: str | Path, *, clock: Callable[[], datetime] = _utc_now,
                 sleep: Callable[[float], None] = time.sleep, inbox_path: str | Path | None = None,
                 branch: str = "tn-state", remote: str = "origin",
                 crash_after: str | None = None, fail_once_in: str | None = None, fail_always_in: str | None = None):
        self.repo = Path(repo)
        self.db_path = Path(db_path)
        self.clock = clock
        self._sleep = sleep
        self.inbox_path = inbox_path
        self.state = S.GitState(self.repo, branch=branch, remote=remote)
        self.faults = TN.Faults(crash_after, fail_once_in, fail_always_in)   # chỉ kiểm thử mới đặt
        self.slept = 0.0
        self.stop_text: str | None = None
        self._inbox_text: str | None = None
        self._base_sha: str | None = None        # mã băm bản database gần nhất mình đọc hoặc ghi
        self._loaded = False
        self.last_report: str | None = None      # báo cáo phiên vừa ghi (để in ra nhật ký của bộ lập lịch)
        self.session_file = Path(str(self.db_path) + ".session.json")   # nối các lệnh của một phiên AI (nhiều tiến trình)

    # ---------- đọc / ghi nhánh trạng thái ----------
    def _open_session(self, files: dict | None) -> dict | None:
        """Máy này đang giữ một phiên đã nhận khóa mà chưa đóng? (lần ghi gần nhất là lần nhận phiên, khóa còn hạn,
        và database trên kho vẫn đúng là bản đó). Nạp lại lúc này sẽ xóa việc đang làm dở trong database cục bộ."""
        if not self.session_file.exists() or not self.db_path.exists() or files is None:
            return None
        try:
            sess = json.loads(self.session_file.read_text(encoding="utf-8"))
            if not sess.get("open") or datetime.fromisoformat(sess["lock_expires_at"]) <= self.clock().astimezone(timezone.utc):
                return None
            if _sha(S.unpack(files)) != sess.get("base_sha"):
                return None
        except (ValueError, KeyError, TypeError, OSError):
            return None
        return sess

    def load(self) -> None:
        """Đọc nhánh trạng thái và dựng lại database cục bộ. Trạng thái hỏng → StateError, không làm gì thêm."""
        files = self.state.pull()
        sess = self._open_session(files)
        if sess:
            raise RunnerError(f"Máy này đang mở phiên {sess['run_id']} (khóa tới {sess['lock_expires_at']}). Không nạp lại để khỏi xóa "
                              f"việc đang làm dở: hãy đóng phiên bằng ai-finish, hoặc chờ khóa hết hạn.")
        for suffix in ("", "-wal", "-shm", "-journal"):
            q = Path(str(self.db_path) + suffix)
            if q.exists():
                q.unlink()
        if files is None:
            self._base_sha, self.stop_text, self._inbox_text = None, None, None
        else:
            dump = S.unpack(files)
            S.restore_sql(dump, self.db_path)
            self._base_sha = _sha(dump)
            self.stop_text = files.get(STOP_PATH)
            self._inbox_text = files.get(INBOX_PATH)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._loaded = True

    def _tn(self) -> TN.ThienNao:
        tn = TN.ThienNao(self.db_path, clock=self.clock, inbox_path=self.inbox_path, inbox_text=self._inbox_text,
                         faults=self.faults)
        tn.stop_file = self.stop_text
        return tn

    def _save(self, tn: TN.ThienNao, run_id: str, message: str) -> bool:
        dump = S.dump_sql(self.db_path)
        files = S.pack(dump, run_id=run_id, extra=tn.export(run_id))
        if self.state.push(files, message):
            self._base_sha = _sha(dump)
            self.last_report = files.get("state/REPORT.md")
            lock = tn.cycle.lock()
            self.session_file.write_text(json.dumps({
                "run_id": run_id, "base_sha": self._base_sha, "head": self.state.parent,
                "open": lock["run_id"] == run_id, "lock_expires_at": lock["expires_at"]}), encoding="utf-8")
            return True
        return False

    def _attach(self, run_id: str) -> None:
        """Tiến trình mới nối vào phiên đang mở (ai-claim và ai-finish là hai lệnh riêng). Chỉ nối khi database
        trên kho vẫn đúng là bản phiên này đã ghi lúc nhận việc; ngược lại bên khác đã ghi → không ghi đè."""
        if not self.session_file.exists() or not self.db_path.exists():
            raise RunnerError(f"Không thấy phiên {run_id} đang mở trên máy này (thiếu database hoặc tệp phiên)")
        sess = json.loads(self.session_file.read_text(encoding="utf-8"))
        files = self.state.pull()
        remote_sha = _sha(S.unpack(files)) if files is not None else None
        if sess.get("run_id") != run_id or remote_sha != sess.get("base_sha"):
            raise RunnerError(f"Trạng thái trên kho không còn là bản phiên {run_id} đã ghi lúc nhận việc — không ghi đè")
        self._base_sha = remote_sha
        self.stop_text = files.get(STOP_PATH)
        self._loaded = True

    def _save_final(self, tn: TN.ThienNao, run_id: str, message: str) -> None:
        """Ghi kết quả phiên. Nhánh đã đổi trong lúc làm → chỉ ghi nếu database trên nhánh vẫn là bản mình đã đọc."""
        for _ in range(MAX_CLAIM_ROUNDS):
            if self._save(tn, run_id, message):
                return
            files = self.state.pull()
            remote_sha = _sha(S.unpack(files)) if files is not None else None
            if remote_sha != self._base_sha:
                raise RunnerError(f"Trạng thái trên kho đã bị bên khác ghi trong lúc phiên {run_id} đang làm — không ghi đè")
            self.stop_text = files.get(STOP_PATH) if files else None
            tn.stop_file = self.stop_text
        raise RunnerError(f"Không ghi được kết quả phiên {run_id} sau {MAX_CLAIM_ROUNDS} lần")

    # ---------- nhận phiên ----------
    def claim(self, lane: str, trigger: str = "schedule", *, before=None, after=None) -> dict:
        """Nhận phiên của làn `lane` và đẩy ngay. `before(tn)` có thể trả về lý do bỏ qua;
        `after(tn, b)` làm phần mở phiên (trong cùng lần ghi) và trả về kết quả thay cho `b`."""
        for _ in range(MAX_CLAIM_ROUNDS):
            if not self._loaded:
                self.load()
            tn = self._tn()
            try:
                skip = before(tn) if before else None
                if skip is not None:
                    return skip
                b = tn.cycle.begin(lane, trigger, external_stop=self.stop_text)
                out = b
                if b["action"] != "SKIP" and after:
                    out = after(tn, b)
                if not b["persist"]:
                    return out
                what = "nhận phiên" if b["action"] != "SKIP" else f"bỏ qua ({b['reason']})"
                if self._save(tn, b["run_id"], f"{b['run_id']}: {what}"):
                    return out
            finally:
                tn.close()
            self._loaded = False                 # bên khác ghi trước → đọc lại rồi quyết định lại
        raise RunnerError(f"Không nhận được phiên sau {MAX_CLAIM_ROUNDS} lần đọc lại — nhánh trạng thái bị ghi liên tục")

    # ---------- làn luật ----------
    def claim_rule(self, trigger: str = "schedule") -> dict:
        return self.claim("rule", trigger)

    def work_rule(self, b: dict, tests: dict | None = None) -> dict:
        tn = self._tn()
        try:
            error = None
            try:
                summary = tn.run(b, tests=tests)
                status = "SUCCEEDED"
            except Exception as e:               # SimulatedCrash là BaseException → không bị bắt ở đây
                status, error = "FAILED", f"{type(e).__name__}: {e}"
                summary = tn.summary(b)
            row = tn.cycle.finish(b["run_id"], b["token"], status, summary=summary, error=error)
            self._save_final(tn, b["run_id"], f"{b['run_id']}: {status} (lần {row['attempt']})")
        finally:
            tn.close()
        return {"status": row["status"], "run_id": b["run_id"], "attempt": row["attempt"], "action": b["action"],
                "mode": b["mode"], "recovered": b["recovered"], "missed": b["missed"],
                "skipped_phases": summary.get("skipped_phases", []), "error": error, "summary": summary}

    def rule(self, trigger: str = "schedule", tests: dict | None = None) -> dict:
        """Một phiên làn luật trọn vẹn, có thử lại trong cùng lượt chạy."""
        recovered: list[str] = []
        missed: list[str] = []
        res: dict = {}
        cfg = C.DEFAULT_SCHEDULE
        for _ in range(int(cfg["run_max_attempts"]) * 2 + 2):      # trần cứng số vòng — không lặp vô hạn
            b = self.claim_rule(trigger)
            if b["action"] == "SKIP":
                if b["reason"] == "BACKOFF" and res:               # còn trong thời gian chờ của chính lượt này
                    wait = max(1.0, (datetime.fromisoformat(b["retry_at"]) - self.clock().astimezone(timezone.utc)).total_seconds())
                    self._sleep(wait)
                    self.slept += wait
                    continue
                if res and b["reason"] in ("EXHAUSTED", "DUPLICATE"):
                    return res
                return {"status": "SKIP", "reason": b["reason"], "run_id": b["run_id"], "recovered": b["recovered"],
                        "missed": b["missed"], "held_by": b.get("held_by"), "retry_at": b.get("retry_at")}
            recovered += [r for r in b["recovered"] if r not in recovered]
            missed += [m for m in b["missed"] if m not in missed]
            res = self.work_rule(b, tests)
            res["recovered"], res["missed"] = recovered, missed
            tn = self._tn()
            try:
                max_att, wait = int(tn.cycle.cfg["run_max_attempts"]), C.backoff_seconds(res["attempt"], tn.cycle.cfg)
            finally:
                tn.close()
            if res["status"] == "SUCCEEDED" or res["attempt"] >= max_att:
                return res
            self._sleep(wait)
            self.slept += wait
        return res

    # ---------- làn AI ----------
    def ai_claim(self, trigger: str = "schedule") -> dict:
        """Nhận phiên AI và trả bản giao việc. Phiên Claude làm việc theo bản này rồi gọi ai_finish()."""
        return self.claim("ai", trigger, before=lambda tn: tn.ai_budget_block(), after=lambda tn, b: tn.ai_open(b))

    def ai_finish(self, run_id: str, token: int, status: str = "SUCCEEDED", reported: dict | None = None,
                  error: str | None = None) -> dict:
        """Đóng phiên AI. Khóa đã hết hạn hoặc đã về tay phiên khác → CycleError, không ghi gì lên kho."""
        if not self._loaded:
            self._attach(run_id)
        tn = self._tn()
        try:
            summary = tn.ai_close(run_id, token, reported)
            row = tn.cycle.finish(run_id, token, status, summary=summary, error=error)
            self._save_final(tn, run_id, f"{run_id}: {status} (phiên AI)")
        finally:
            tn.close()
        return {"status": row["status"], "run_id": run_id, "attempt": row["attempt"], "summary": summary}

    # ---------- chỉ đọc ----------
    def status(self) -> dict:
        """Đọc trạng thái mới nhất trên kho, không ghi gì."""
        files = self.state.pull()
        if files is None:
            return {"exists": False}
        S.unpack(files)                          # kiểm mã băm
        latest = json.loads(files["state/latest.json"]) if "state/latest.json" in files else {}
        return {"exists": True, "stop_file": STOP_PATH in files, "run": latest.get("run", {}),
                "metrics": latest.get("metrics", {}), "board_counts": latest.get("board", {}).get("counts", {}),
                "head": self.state.parent}
