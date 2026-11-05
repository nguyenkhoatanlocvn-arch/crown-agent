"""Phiên tự tích hợp của Thiên Công AT (mặc định phút 05 mỗi giờ, Asia/Ho_Chi_Minh).

Bộ lập lịch bền vững là tác vụ định kỳ của Claude (chạy trên máy chủ, không cần mở giao diện hay bật laptop).
Tệp này lo phần mỗi phiên phải làm đúng: chống chạy chồng (khóa phiên), tạm dừng, phân biệt lượt do lịch kích hoạt
với lượt chạy tay, đọc việc tồn, tìm thay đổi trên GitHub, loại trùng, chọn một ứng viên, giới hạn một bản phát hành
tự động mỗi phiên, và ghi bản ghi phiên vào runs/sessions/.
"""
from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable

from .identity import CHAIRMAN, Directory
from .jobs import JobStore
from .util import (VN_TZ, ToolError, atomic_write_json, iso, mutex, new_id, parse_iso, read_json, safe_id, tool_root,
                   utc_now)

DEFAULT_SCHEDULE = {"cron": "5 * * * *", "tz": "Asia/Ho_Chi_Minh", "minute": 5, "window_minutes": 20,
                    "paused": False, "max_session_minutes": 50, "trigger_id": None, "worker_trigger_id": None}
RESULTS = {"NO_CHANGE", "RELEASED", "ROLLED_BACK", "WORKED", "NO_RELEASE", "SKIPPED_OVERLAP", "PAUSED", "ERROR"}
TRIGGERS = {"scheduler", "manual", "web"}


def _git(*args: str, cwd: Path | None = None, timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=timeout)


def _vkey(name: str) -> tuple:
    return tuple(int(x) for x in re.findall(r"\d+", name)) or (0,)


def fingerprint(src: dict) -> str:
    """Dấu vân tay hiện tại của một nguồn GitHub. Ném ToolError khi không đọc được (mạng, repo mất)."""
    url, mode = src["url"], src.get("mode", "head")
    if mode == "tags":
        r = _git("ls-remote", "--tags", "--refs", url, src.get("tag_pattern", "*"))
        if r.returncode != 0:
            raise ToolError(f"không đọc được tag của {url}: {r.stderr.strip()[-200:]}")
        tags = [(ln.split("\t")[1].removeprefix("refs/tags/"), ln.split("\t")[0]) for ln in r.stdout.splitlines() if "\t" in ln]
        stable = [t for t in tags if not re.search(r"(rc|dev|alpha|beta|pre)", t[0], re.I)] or tags
        if not stable:
            raise ToolError(f"{url} không có tag khớp '{src.get('tag_pattern', '*')}'")
        name, sha = max(stable, key=lambda t: _vkey(t[0]))
        return f"tag:{name}@{sha[:12]}"
    r = _git("ls-remote", url, "HEAD")
    if r.returncode != 0 or not r.stdout.strip():
        raise ToolError(f"không đọc được HEAD của {url}: {r.stderr.strip()[-200:]}")
    head = r.stdout.split()[0]
    if mode == "path" and src.get("path"):
        tmp = Path(tempfile.mkdtemp(prefix="tool-watch-"))
        try:
            c = _git("clone", "-q", "--depth", "1", "--filter=blob:none", "--no-checkout", url, str(tmp / "r"), timeout=300)
            if c.returncode == 0:
                t = _git("rev-parse", f"HEAD:{src['path']}", cwd=tmp / "r")
                if t.returncode == 0:
                    return f"tree:{t.stdout.strip()}"  # chỉ băm của đúng đường dẫn: commit ở chỗ khác không tính là thay đổi
                raise ToolError(f"{url} không còn đường dẫn '{src['path']}'")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    return f"head:{head}"


def classify_trigger(now: datetime, schedule: dict, marker: str | None) -> tuple[str, str]:
    """Xác định lượt này do đâu kích hoạt. Trả (trigger, căn cứ)."""
    marker = (marker or "").strip()
    if marker.upper().startswith("WEB"):
        return "web", f"có dấu lệnh từ web: {marker[:80]}"
    if marker:
        return "manual", f"có dấu chạy tay: {marker[:80]}"
    local = now.astimezone(VN_TZ)
    start, win = int(schedule.get("minute", 5)), int(schedule.get("window_minutes", 20))
    if start <= local.minute < start + win:
        return "scheduler", (f"không có dấu chạy tay và bắt đầu lúc {local:%H:%M} — trong khung phút "
                             f"{start:02d}–{start + win - 1:02d} của lịch {schedule.get('cron')}")
    return "manual", (f"không có dấu, nhưng bắt đầu lúc {local:%H:%M} — ngoài khung của lịch; "
                      f"coi là bấm chạy tay trong ứng dụng")


class Scheduler:
    def __init__(self, root: Path | None = None, directory: Directory | None = None,
                 clock: Callable[[], datetime] = utc_now):
        self.root = Path(root) if root else tool_root()
        self.dir = directory or Directory(self.root)
        self.clock = clock
        self.lock_path = self.root / "runs" / "locks" / "at-session.json"
        self.sessions = self.root / "runs" / "sessions"
        self.sessions.mkdir(parents=True, exist_ok=True)

    # ---------- cấu hình ----------
    def schedule(self) -> dict:
        return dict(DEFAULT_SCHEDULE, **read_json(self.root / "config" / "schedule.json", default={}))

    def set_schedule(self, actor: str, **fields) -> dict:
        self.dir.require_executive(actor, "đổi lịch AT")
        allowed = {"paused", "trigger_id", "worker_trigger_id", "cron", "minute", "max_session_minutes"}
        bad = set(fields) - allowed
        if bad:
            raise ToolError(f"Không đổi được: {', '.join(sorted(bad))}")
        p = self.root / "config" / "schedule.json"
        with mutex(p):
            s = self.schedule()
            s.update(fields, updated_at=iso(self.clock()), updated_by=actor)
            atomic_write_json(p, s)
            return s

    # ---------- phiên ----------
    def get(self, run_id: str) -> dict:
        p = self.sessions / f"{safe_id(run_id, 'Mã phiên')}.json"
        if not p.is_file():
            raise ToolError(f"Không có phiên {run_id}")
        return read_json(p)

    def _save(self, s: dict) -> dict:
        atomic_write_json(self.sessions / f"{s['run_id']}.json", s)
        return s

    def list(self, limit: int = 30) -> list[dict]:
        out = [read_json(p) for p in self.sessions.glob("*.json")]
        out.sort(key=lambda s: s["started_at"], reverse=True)
        return out[:limit]

    def begin(self, agent: str, marker: str | None = None, trigger: str | None = None) -> dict:
        """Mở phiên. Luôn ghi bản ghi phiên; result = None nghĩa là được làm việc, ngược lại phải dừng ngay."""
        self.dir.require_executive(agent, "mở phiên AT")
        now = self.clock()
        sched = self.schedule()
        marked = bool((marker or "").strip())
        trig, basis = classify_trigger(now, sched, marker)
        if trigger:
            if trigger not in TRIGGERS:
                raise ToolError(f"--trigger phải là một trong: {', '.join(sorted(TRIGGERS))}")
            if trigger != "scheduler" and not marked:
                raise ToolError("Lượt chạy tay hoặc từ web phải kèm --marker ghi rõ ai/cái gì kích hoạt")
            trig, basis = trigger, f"chỉ định: {trigger}" + (f" · {marker.strip()[:80]}" if marked else "")
        s = {"run_id": new_id("AT", now, "%Y%m%d-%H%M"), "agent": agent, "trigger": trig, "trigger_basis": basis,
             "started_at": iso(now), "finished_at": None, "result": None, "plan": None, "releases": [],
             "notes": [], "github": None}
        with mutex(self.lock_path):
            lock = read_json(self.lock_path, default={"state": "FREE"})
            # Tạm dừng chặn MỌI phiên không có dấu chạy tay/web — kể cả phiên bắt đầu trễ ngoài khung phút của lịch.
            if sched.get("paused") and (trig == "scheduler" or not marked):
                s.update(result="PAUSED", finished_at=iso(now), notes=["Lịch đang tạm dừng — phiên kết thúc ngay"])
                return self._save(s)
            if lock.get("state") == "HELD" and parse_iso(lock["expires_at"]) > now:
                s.update(result="SKIPPED_OVERLAP", finished_at=iso(now),
                         notes=[f"Phiên {lock['run_id']} của {lock['holder']} còn chạy tới {lock['expires_at']} — không chạy chồng"])
                return self._save(s)
            if lock.get("state") == "HELD":
                s["notes"].append(f"Khóa của phiên {lock['run_id']} đã hết hạn mà không được nhả — phiên này tiếp quản")
            atomic_write_json(self.lock_path, {
                "state": "HELD", "run_id": s["run_id"], "holder": agent, "trigger": trig, "started_at": iso(now),
                "expires_at": iso(now + timedelta(minutes=int(sched["max_session_minutes"])))})
            return self._save(s)

    def _mine(self, run_id: str) -> dict:
        s = self.get(run_id)
        if s["result"] is not None:
            raise ToolError(f"Phiên {run_id} đã kết thúc ({s['result']})")
        lock = read_json(self.lock_path, default={"state": "FREE"})
        if lock.get("state") != "HELD" or lock.get("run_id") != run_id:
            raise ToolError(f"Phiên {run_id} không còn giữ khóa phiên — dừng để không ghi chồng")
        if parse_iso(lock["expires_at"]) <= self.clock():
            raise ToolError(f"Phiên {run_id} đã quá hạn thời gian của phiên — dừng")
        return s

    def attach_github(self, run_id: str, github: dict) -> dict:
        """Lưu kết quả so GitHub vào bản ghi phiên để bước lập kế hoạch dùng lại, không gọi mạng lần hai."""
        with mutex(self.lock_path):
            s = self._mine(run_id)
            s["github"] = github
            return self._save(s)

    def note(self, run_id: str, text: str) -> dict:
        with mutex(self.lock_path):
            s = self._mine(run_id)
            s["notes"].append(text)
            return self._save(s)

    def reserve_release(self, run_id: str, actor: str, tool_id: str, version: str) -> dict:
        """Giữ một suất phát hành tự động của phiên TRƯỚC khi phát hành. Gọi bởi Catalog.publish(auto=True).

        Hạn mức được đếm ngay tại đây, dưới khóa phiên: phát hành tự động không thể vượt hạn mức bằng cách
        "quên" ghi sổ. Phiên phải đang sống (chưa kết thúc, còn giữ khóa) và người phát hành là chủ phiên.
        """
        with mutex(self.lock_path):
            s = self._mine(run_id)
            if s["agent"] != actor:
                raise ToolError(f"Phiên {run_id} là của {s['agent']} — {actor} không phát hành tự động trong phiên này")
            cap = int(self.dir.limits.get("max_auto_releases_per_session", 1))
            if len(s["releases"]) >= cap:
                raise ToolError(f"Phiên {run_id} đã phát hành tự động {cap} bản — hết hạn mức của phiên")
            s["releases"].append({"tool": tool_id, "version": version, "smoke": None, "at": iso(self.clock())})
            return self._save(s)

    def cancel_release(self, run_id: str, tool_id: str, version: str) -> None:
        """Trả lại suất khi bản phát hành không thành (không qua cổng kiểm thử, bị bên khác giành trước...)."""
        with mutex(self.lock_path):
            s = self.get(run_id)
            s["releases"] = [r for r in s["releases"] if not (r["tool"] == tool_id and r["version"] == version
                                                              and r.get("smoke") is None)]
            self._save(s)

    def register_release(self, run_id: str, tool_id: str, version: str, smoke: str) -> dict:
        """Ghi kết quả gọi thử từ kho chung cho bản đã phát hành trong phiên (suất do publish giữ sẵn)."""
        with mutex(self.lock_path):
            s = self._mine(run_id)
            rel = next((r for r in s["releases"] if r["tool"] == tool_id and r["version"] == version), None)
            if rel is None:
                raise ToolError(f"Phiên {run_id} chưa phát hành {tool_id}@{version} — không có gì để ghi kết quả gọi thử")
            rel.update(smoke=smoke, smoke_at=iso(self.clock()))
            return self._save(s)

    def end(self, run_id: str, result: str, note: str = "", agent: str | None = None) -> dict:
        if result not in RESULTS:
            raise ToolError(f"Kết quả phiên phải là một trong: {', '.join(sorted(RESULTS))}")
        with mutex(self.lock_path):
            s = self.get(run_id)
            if agent != CHAIRMAN and agent != s["agent"]:  # chỉ chủ phiên (hoặc Chủ tịch) đóng phiên và nhả khóa
                raise ToolError(f"Phiên {run_id} là của {s['agent']} — {agent or '(không nêu tên)'} không được kết thúc phiên này")
            if s["result"] is not None:
                raise ToolError(f"Phiên {run_id} đã kết thúc ({s['result']})")
            if result == "RELEASED" and not s["releases"]:
                raise ToolError("Kết quả RELEASED cần ít nhất một bản phát hành đã ghi")
            lock = read_json(self.lock_path, default={"state": "FREE"})
            if lock.get("run_id") == run_id:
                atomic_write_json(self.lock_path, {"state": "FREE", "last_run": run_id, "released_at": iso(self.clock())})
            if note.strip():
                s["notes"].append(note.strip())
            s.update(result=result, finished_at=iso(self.clock()))
            return self._save(s)

    # ---------- kế hoạch: việc tồn + GitHub ----------
    def watch_sources(self) -> list[dict]:
        return read_json(self.root / "config" / "watch.json", default={"sources": []})["sources"]

    def check_github(self, sources: list[dict] | None = None) -> dict:
        """So dấu vân tay từng nguồn với lần trước. Lần đầu thấy một nguồn = mốc gốc, không tính là thay đổi."""
        p = self.root / "runs" / "watch_state.json"
        with mutex(p):
            st = read_json(p, default={"sources": {}, "processed": []})
            checked, changed, errors = [], [], []
            for src in (self.watch_sources() if sources is None else sources):
                try:
                    fp = fingerprint(src)
                except (ToolError, subprocess.TimeoutExpired) as e:
                    errors.append({"id": src["id"], "error": str(e)[:200]})
                    continue
                prev = st["sources"].get(src["id"])
                checked.append({"id": src["id"], "fingerprint": fp})
                if prev is None:
                    st["sources"][src["id"]] = {"fingerprint": fp, "baseline_at": iso(self.clock()), "seen_at": iso(self.clock())}
                elif prev["fingerprint"] != fp:
                    key = hashlib.sha256(f"{src['id']}|{fp}".encode()).hexdigest()[:16]
                    changed.append({"id": src["id"], "from": prev["fingerprint"], "to": fp, "key": key,
                                    "duplicate": key in st["processed"], "integration": src.get("integration")})
                    prev["pending"] = fp
                    prev["seen_at"] = iso(self.clock())
                else:
                    prev["seen_at"] = iso(self.clock())
            atomic_write_json(p, st)
        return {"checked": checked, "changed": changed, "errors": errors, "at": iso(self.clock())}

    def mark_candidate(self, source_id: str, key: str, outcome: str, note: str = "") -> None:
        """Ghi một ứng viên đã xử lý (dù đạt hay không) để phiên sau không làm lại."""
        p = self.root / "runs" / "watch_state.json"
        with mutex(p):
            st = read_json(p, default={"sources": {}, "processed": []})
            src = st["sources"].get(source_id)
            if src is None:
                raise ToolError(f"Không có nguồn '{source_id}' trong sổ theo dõi GitHub")
            if key not in st["processed"]:
                st["processed"].append(key)
            # Chỉ chốt đúng thay đổi vừa xử lý. Nếu nguồn đã đổi tiếp (pending mới hơn), giữ lại để phiên sau xét.
            pend = src.get("pending")
            if pend and hashlib.sha256(f"{source_id}|{pend}".encode()).hexdigest()[:16] == key:
                src["fingerprint"] = src.pop("pending")
            src.update(last_outcome=outcome, last_note=note, last_at=iso(self.clock()))
            atomic_write_json(p, st)

    def plan(self, run_id: str, github: dict | None = None, requests: list[dict] | None = None) -> dict:
        """Chọn đúng MỘT việc cho phiên: lệnh từ web → việc tồn → ứng viên GitHub mới → không có thay đổi."""
        jobs = JobStore(self.root, self.dir, self.clock)
        backlog = jobs.backlog()
        fresh = [c for c in (github or {}).get("changed", []) if not c["duplicate"]]
        plan = {"backlog": [{"job_id": j["job_id"], "title": j["title"], "state": j["state"],
                             "next_step": (jobs.next_step(j) or {}).get("id")} for j in backlog],
                "requests": [r.get("id") for r in (requests or [])],
                "github_changed": [c["id"] for c in (github or {}).get("changed", [])],
                "github_duplicates": [c["id"] for c in (github or {}).get("changed", []) if c["duplicate"]]}
        if requests:
            plan["action"] = {"do": "WEB_REQUEST", "request": requests[0].get("id")}
        elif backlog:
            j = backlog[0]
            plan["action"] = {"do": "RESUME_JOB", "job_id": j["job_id"], "next_step": (jobs.next_step(j) or {}).get("id"),
                              "previous_holder": (j.get("lease") or {}).get("holder")}
        elif fresh:
            plan["action"] = {"do": "EVALUATE_SOURCE", **fresh[0]}
        else:
            plan["action"] = {"do": "NO_CHANGE"}
        with mutex(self.lock_path):
            s = self._mine(run_id)
            s.update(plan=plan, github=github)
            self._save(s)
        return plan
