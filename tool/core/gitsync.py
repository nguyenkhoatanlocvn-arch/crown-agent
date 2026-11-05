"""Đồng bộ trạng thái TOOL giữa các phiên qua git — khóa ghi dùng được cả khi hai Agent ở hai máy khác nhau.

Mỗi phiên Claude chạy trong một bản sao repo riêng, nên khóa tệp cục bộ không đủ. Ở đây mỗi thay đổi là một
giao dịch: kéo bản mới → sửa → commit → đẩy. `git push` chỉ nhận khi lịch sử nối tiếp, nên nếu hai phiên cùng
sửa một tệp (cùng nhận một việc, cùng phát hành một phiên bản, cùng giữ khóa phiên) thì đúng một bên đẩy được;
bên thua bị hủy thay đổi cục bộ trong vùng `paths`, nhận bản của bên thắng, và báo SyncConflict.
"""
from __future__ import annotations

import subprocess
from contextlib import contextmanager
from pathlib import Path

from .util import REPO_ROOT, ToolError


class SyncConflict(ToolError):
    """Phiên khác đã ghi trước vào cùng chỗ. Thay đổi cục bộ đã được hủy."""


class GitSync:
    def __init__(self, repo: Path | None = None, remote: str = "origin", branch: str | None = None,
                 author: str = "Thien Cong <noreply@anthropic.com>"):
        self.repo = Path(repo) if repo else REPO_ROOT
        self.remote = remote
        self.author = author
        self.branch = branch or self._git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip()

    def _git(self, *args: str, check: bool = True, timeout: int = 180) -> subprocess.CompletedProcess:
        r = subprocess.run(["git", "-C", str(self.repo), *args], capture_output=True, text=True, timeout=timeout)
        if check and r.returncode != 0:
            raise ToolError(f"git {' '.join(args[:3])} lỗi: {(r.stderr or r.stdout).strip()[-400:]}")
        return r

    def available(self) -> bool:
        r = self._git("remote", "get-url", self.remote, check=False)
        return r.returncode == 0

    def head(self) -> str:
        return self._git("rev-parse", "HEAD").stdout.strip()

    def dirty(self, paths: list[str]) -> list[str]:
        out = self._git("status", "--porcelain", "--", *paths).stdout
        return [x[3:] for x in out.splitlines() if x.strip()]

    def pull(self) -> None:
        self._git("fetch", "-q", self.remote, self.branch)
        r = self._git("rebase", "-q", f"{self.remote}/{self.branch}", check=False)
        if r.returncode != 0:
            self._git("rebase", "--abort", check=False)
            raise SyncConflict("Bản cục bộ lệch với bản chung và không tự nối được — cần xử lý tay")

    def commit(self, paths: list[str], message: str) -> bool:
        self._git("add", "-A", "--", *paths)
        if self._git("diff", "--cached", "--quiet", "--", *paths, check=False).returncode == 0:
            return False
        self._git("-c", "user.name=" + self.author.split(" <")[0], "-c",
                  "user.email=" + self.author.split("<")[1].rstrip(">"), "commit", "-q", "-m", message, "--", *paths)
        return True

    def _discard(self, start: str, paths: list[str]) -> None:
        """Hủy mọi thay đổi cục bộ trong `paths` kể từ `start`, rồi nhận bản của remote."""
        self._git("rebase", "--abort", check=False)
        self._git("reset", "-q", "--mixed", start)
        self._git("checkout", "-q", "--", *paths, check=False)
        self._git("clean", "-fdq", "--", *paths)
        self._git("merge", "-q", "--ff-only", f"{self.remote}/{self.branch}", check=False)

    def push(self, start: str, paths: list[str], retries: int = 3) -> None:
        for _ in range(retries):
            if self._git("push", "-q", self.remote, f"HEAD:{self.branch}", check=False).returncode == 0:
                return
            self._git("fetch", "-q", self.remote, self.branch)
            if self._git("rebase", "-q", f"{self.remote}/{self.branch}", check=False).returncode != 0:
                files = self._git("diff", "--name-only", "--diff-filter=U", check=False).stdout.split()
                self._discard(start, paths)
                raise SyncConflict("Phiên khác đã ghi trước vào: " + (", ".join(files) or "cùng vùng dữ liệu")
                                   + " — thay đổi của phiên này đã được hủy")
        self._discard(start, paths)
        raise SyncConflict("Không đẩy được sau nhiều lần thử — thay đổi của phiên này đã được hủy")

    @contextmanager
    def tx(self, message: str, paths: list[str] | None = None):
        """Giao dịch: kéo → (thân lệnh sửa tệp) → commit → đẩy. Lỗi hoặc xung đột thì hủy thay đổi trong paths."""
        paths = paths or ["tool"]
        left = self.dirty(paths)
        if left:
            raise ToolError("Còn thay đổi chưa commit trong vùng đồng bộ: " + ", ".join(left[:5]))
        self.pull()
        start = self.head()
        try:
            yield
        except BaseException:
            self._discard(start, paths)
            raise
        if self.commit(paths, message):
            self.push(start, paths)
