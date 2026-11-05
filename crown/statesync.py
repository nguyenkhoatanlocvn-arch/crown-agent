"""Trạng thái bền của Thiên Não trên nhánh git (mặc định `tn-state`).

Vì sao: máy chạy theo lịch (GitHub Actions, phiên Claude) là máy tạm, mất đĩa sau
mỗi phiên. Trạng thái là một cơ sở dữ liệu SQLite; giữa các phiên nó được lưu
thành bản kết xuất văn bản (đọc được, so sánh được) trên một nhánh riêng.

Chống hai phiên cùng ghi: commit mới luôn có cha là đầu nhánh đã đọc, và đẩy
KHÔNG ép. Nếu nhánh đã đổi từ lúc đọc, GitHub từ chối → push() trả False và bên
thua phải đọc lại. Đây là khóa so-sánh-rồi-ghi, không phụ thuộc đồng hồ.

Chỉ dùng lệnh git ở mức thấp (hash-object, update-index, commit-tree): không
checkout, không đụng thư mục mã, không đụng nhánh khác.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

DUMP_PATH = "state/tn_state.sql"
META_PATH = "state/meta.json"
STATE_PREFIX = "state/"
SCHEMA_VERSION = 1


class StateError(Exception):
    """Không đọc/ghi được trạng thái, hoặc trạng thái hỏng."""


# ---------- SQLite ↔ văn bản ----------
def dump_sql(db_path: str | Path) -> str:
    con = sqlite3.connect(str(db_path))
    try:
        return "\n".join(con.iterdump()) + "\n"
    finally:
        con.close()


def restore_sql(text: str, db_path: str | Path) -> None:
    """Dựng lại cơ sở dữ liệu từ bản kết xuất. Thay hẳn tệp cũ nếu có."""
    p = Path(db_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    for suffix in ("", "-wal", "-shm", "-journal"):
        q = Path(str(p) + suffix)
        if q.exists():
            q.unlink()
    con = sqlite3.connect(str(p))
    try:
        con.executescript(text)
        con.commit()
    except sqlite3.Error as e:
        raise StateError(f"Bản kết xuất trạng thái không khôi phục được: {e}") from e
    finally:
        con.close()


def pack(dump_text: str, *, run_id: str, extra: dict[str, str] | None = None) -> dict[str, str]:
    """Gói bản kết xuất + tệp mô tả (mã băm) + các tệp đọc được cho người/web."""
    meta = {"schema": SCHEMA_VERSION, "sha256": hashlib.sha256(dump_text.encode("utf-8")).hexdigest(),
            "bytes": len(dump_text.encode("utf-8")), "run_id": run_id,
            "written_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    files = {DUMP_PATH: dump_text, META_PATH: json.dumps(meta, ensure_ascii=False, indent=2) + "\n"}
    for path, text in (extra or {}).items():
        if not path.startswith(STATE_PREFIX) or path in files:
            raise StateError(f"Tệp phụ phải nằm trong {STATE_PREFIX} và không trùng tệp chính: {path}")
        files[path] = text
    return files


def unpack(files: dict[str, str]) -> str:
    """Lấy bản kết xuất và kiểm mã băm. Hỏng → StateError (không chạy tiếp trên dữ liệu hỏng)."""
    if DUMP_PATH not in files or META_PATH not in files:
        raise StateError(f"Nhánh trạng thái thiếu {DUMP_PATH} hoặc {META_PATH}")
    try:
        meta = json.loads(files[META_PATH])
    except json.JSONDecodeError as e:
        raise StateError(f"{META_PATH} không phải JSON hợp lệ") from e
    if meta.get("schema") != SCHEMA_VERSION:
        raise StateError(f"Phiên bản lược đồ trạng thái {meta.get('schema')} khác bản mã hiểu ({SCHEMA_VERSION})")
    got = hashlib.sha256(files[DUMP_PATH].encode("utf-8")).hexdigest()
    if got != meta.get("sha256"):
        raise StateError(f"Trạng thái hỏng: sha256 bản kết xuất {got[:12]}… khác {str(meta.get('sha256'))[:12]}… trong meta")
    return files[DUMP_PATH]


# ---------- nhánh git ----------
class GitState:
    def __init__(self, repo: str | Path, branch: str = "tn-state", remote: str = "origin",
                 author: tuple[str, str] = ("TN-ENGINE", "tn-engine@users.noreply.github.com"), timeout: int = 120):
        self.repo = Path(repo)
        self.branch = branch
        self.remote = remote
        self.author = author
        self.timeout = timeout
        self.parent: str | None = None      # đầu nhánh đã đọc lần gần nhất
        self._pulled = False
        # Dấu riêng của người ghi. Hai máy cùng nội dung, cùng giây sẽ tạo ra commit giống hệt nhau; git coi
        # commit thứ hai là "đã có" và cả hai cùng thắng. Dấu này làm mỗi commit là duy nhất.
        self.writer = uuid.uuid4().hex[:12]

    def _git(self, *args, env: dict | None = None, stdin: str | None = None, check: bool = True):
        e = dict(os.environ)
        e.update({"GIT_TERMINAL_PROMPT": "0", "GIT_AUTHOR_NAME": self.author[0], "GIT_AUTHOR_EMAIL": self.author[1],
                  "GIT_COMMITTER_NAME": self.author[0], "GIT_COMMITTER_EMAIL": self.author[1]})
        e.update(env or {})
        try:
            r = subprocess.run(["git", *args], cwd=self.repo, env=e, input=stdin, capture_output=True,
                               text=True, encoding="utf-8", timeout=self.timeout)
        except (OSError, subprocess.TimeoutExpired) as ex:
            raise StateError(f"git {args[0]} không chạy được: {ex}") from ex
        if check and r.returncode != 0:
            raise StateError(f"git {' '.join(args[:3])} lỗi {r.returncode}: {r.stderr.strip()[:300]}")
        return r

    @property
    def _ref(self) -> str:
        return f"refs/heads/{self.branch}"

    def pull(self) -> dict[str, str] | None:
        """Đọc đầu nhánh trạng thái. None = nhánh chưa tồn tại (lần đầu). Lỗi mạng → StateError."""
        r = self._git("ls-remote", "--exit-code", self.remote, self._ref, check=False)
        if r.returncode == 2:           # git: không có ref khớp
            self.parent, self._pulled = None, True
            return None
        if r.returncode != 0:
            raise StateError(f"Không tới được kho trạng thái ({self.remote}): {r.stderr.strip()[:300]}")
        sha = r.stdout.split()[0]
        self._git("fetch", "-q", self.remote, self._ref)
        self.parent, self._pulled = sha, True
        names = [n for n in self._git("ls-tree", "-r", "--name-only", sha).stdout.splitlines()
                 if n.startswith(STATE_PREFIX)]
        return {n: self._git("show", f"{sha}:{n}").stdout for n in names}

    def push(self, files: dict[str, str], message: str) -> bool:
        """Ghi các tệp trạng thái thành một commit nối tiếp đầu nhánh đã đọc rồi đẩy không ép.
        Trả False nếu nhánh đã đổi (bên khác ghi trước) — phải pull() lại rồi mới ghi."""
        if not self._pulled:
            raise StateError("Phải pull() trước khi push() để biết đầu nhánh")
        for path in files:
            if not path.startswith(STATE_PREFIX) or ".." in path.split("/"):
                raise StateError(f"Chỉ được ghi trong {STATE_PREFIX}: {path}")
        with tempfile.TemporaryDirectory() as td:
            env = {"GIT_INDEX_FILE": str(Path(td) / "index")}
            if self.parent:
                self._git("read-tree", self.parent, env=env)
            for path, text in sorted(files.items()):
                blob = self._git("hash-object", "-w", "--stdin", stdin=text).stdout.strip()
                self._git("update-index", "--add", "--cacheinfo", f"100644,{blob},{path}", env=env)
            tree = self._git("write-tree", env=env).stdout.strip()
        if self.parent and tree == self._git("rev-parse", f"{self.parent}^{{tree}}").stdout.strip():
            return True                 # không có gì đổi → không tạo commit
        args = ["commit-tree", tree, "-m", f"{message}\n\nWriter: {self.writer}"] + (["-p", self.parent] if self.parent else [])
        commit = self._git(*args).stdout.strip()
        r = self._git("push", "-q", self.remote, f"{commit}:{self._ref}", check=False)
        if r.returncode != 0:
            self._pulled = False        # buộc đọc lại
            err = r.stderr.lower()
            if any(k in err for k in ("rejected", "non-fast-forward", "fetch first", "stale info")):
                return False
            raise StateError(f"Đẩy trạng thái lỗi: {r.stderr.strip()[:300]}")
        self.parent = commit
        return True
