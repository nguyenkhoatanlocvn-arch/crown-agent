"""Đồng bộ trạng thái TOOL giữa các phiên qua git — khóa ghi dùng được cả khi hai Agent ở hai máy khác nhau.

Mỗi phiên Claude chạy trong một bản sao repo riêng, nên khóa tệp cục bộ không đủ. Ở đây mỗi thay đổi là một
giao dịch: kéo bản mới → sửa → commit → đẩy. `git push` chỉ nhận khi lịch sử nối tiếp, nên nếu hai phiên cùng
sửa một tệp (cùng nhận một việc, cùng phát hành một phiên bản, cùng giữ khóa phiên) thì đúng một bên đẩy được.

Bên thua (hoặc giao dịch lỗi giữa chừng) được đưa về ĐÚNG bản chung mới nhất:
- chỉ những tệp chính giao dịch đã ghi mới bị hoàn lại; tệp khác trong bản sao (kể cả việc dở ngoài tool/) giữ nguyên;
- phần bị hủy không mất: nó được giữ dưới một ref cứu hộ `refs/tool-discarded/<thời điểm>` để lấy lại khi cần;
- nếu không đưa được về bản chung, lệnh báo lỗi rõ ràng thay vì im lặng để bản sao lệch.

Mọi phiên đồng bộ trên MỘT nhánh chung (mặc định `master`, đổi bằng tool/config/sync.json). Bản sao đang ở nhánh
khác, đang rebase/merge dở hoặc HEAD rời thì bị từ chối: hai phiên ở hai nhánh sẽ không bao giờ thấy khóa của nhau.
"""
from __future__ import annotations

import subprocess
from contextlib import contextmanager
from pathlib import Path

from .util import REPO_ROOT, ToolError, read_json, stamp

SHARED_BRANCH = "master"


class SyncFailed(ToolError):
    """Giao dịch không thành và đã được xử lý xong (hoàn lại, hoặc báo rõ cần xử lý tay). Không hoàn lại lần nữa."""


class SyncConflict(SyncFailed):
    """Phiên khác đã ghi trước vào cùng chỗ. Thay đổi cục bộ của giao dịch đã được hoàn lại (và giữ ở ref cứu hộ)."""


class GitSync:
    def __init__(self, repo: Path | None = None, remote: str = "origin", branch: str | None = None,
                 author: str = "Thien Cong <noreply@anthropic.com>"):
        self.repo = Path(repo) if repo else REPO_ROOT
        self.remote = remote
        self.author = author
        cfg = read_json(self.repo / "tool" / "config" / "sync.json", default={})
        self.branch = branch or cfg.get("branch") or SHARED_BRANCH
        self._base: str | None = None

    def _git(self, *args: str, check: bool = True, timeout: int = 180) -> subprocess.CompletedProcess:
        # Luôn kèm danh tính: rebase/commit trong máy mới (chưa cấu hình git) vẫn chạy được.
        ident = ["-c", "user.name=" + self.author.split(" <")[0], "-c", "user.email=" + self.author.split("<")[1].rstrip(">")]
        r = subprocess.run(["git", "-C", str(self.repo), *ident, *args], capture_output=True, text=True, timeout=timeout)
        if check and r.returncode != 0:
            raise ToolError(f"git {' '.join(args[:3])} lỗi: {(r.stderr or r.stdout).strip()[-400:]}")
        return r

    def available(self) -> bool:
        r = self._git("remote", "get-url", self.remote, check=False)
        return r.returncode == 0

    def head(self) -> str:
        return self._git("rev-parse", "HEAD").stdout.strip()

    def _remote_tip(self) -> str | None:
        r = self._git("rev-parse", "-q", "--verify", f"{self.remote}/{self.branch}", check=False)
        return r.stdout.strip() or None

    def dirty(self, paths: list[str]) -> list[str]:
        out = self._git("status", "--porcelain", "--", *paths).stdout
        return [x[3:] for x in out.splitlines() if x.strip()]

    def _require_ready(self) -> None:
        """Bản sao phải ở đúng nhánh chung và không đang rebase/merge dở (của người khác) — nếu không thì từ chối."""
        cur = self._git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
        if cur != self.branch:
            raise ToolError(f"Bản sao đang ở '{cur}', không phải nhánh chung '{self.branch}' — trạng thái TOOL chỉ đồng bộ "
                            f"trên một nhánh. Chuyển về '{self.branch}' rồi chạy lại.")
        gitdir = Path(self._git("rev-parse", "--absolute-git-dir").stdout.strip())
        for mark in ("rebase-merge", "rebase-apply", "MERGE_HEAD", "CHERRY_PICK_HEAD"):
            if (gitdir / mark).exists():
                raise ToolError("Bản sao đang giữa một lần rebase/merge chưa xong — xử lý xong việc đó rồi chạy lại (TOOL không tự hủy nó)")

    def pull(self) -> None:
        """Đưa bản sao lên bản chung mới nhất. Tệp đang sửa dở ở chỗ khác (ngoài phần bản chung vừa đổi) không cản trở."""
        self._git("fetch", "-q", self.remote, self.branch)
        tip = self._remote_tip()
        if tip is None or self.head() == tip:
            return
        if self._git("merge-base", "--is-ancestor", "HEAD", tip, check=False).returncode == 0:
            r = self._git("merge", "-q", "--ff-only", tip, check=False)  # không có commit riêng: chỉ cần tiến lên
            if r.returncode != 0:
                raise SyncConflict("Không lên được bản chung mới nhất: có tệp đang sửa dở trùng với thay đổi mới trên bản chung "
                                   f"({(r.stderr or r.stdout).strip()[-200:]}) — commit hoặc cất tệp đó rồi chạy lại")
            return
        r = self._git("rebase", "-q", "--autostash", tip, check=False)
        if r.returncode != 0:
            self._git("rebase", "--abort", check=False)
            raise SyncConflict("Bản cục bộ lệch với bản chung và không tự nối được — cần xử lý tay")

    def commit(self, paths: list[str], message: str) -> bool:
        self._git("add", "-A", "--", *paths)
        if self._git("diff", "--cached", "--quiet", "--", *paths, check=False).returncode == 0:
            return False
        self._git("commit", "-q", "-m", message, "--", *paths)
        return True

    def _push_once(self) -> bool:
        return self._git("push", "-q", self.remote, f"HEAD:{self.branch}", check=False).returncode == 0

    def _settle(self, paths: list[str]) -> None:
        """Trước giao dịch: việc dở của chính phiên này trong `paths` được commit và ĐẨY lên trước.

        Nhờ vậy khi giao dịch bắt đầu, bản sao trùng với bản chung; nếu sau đó thua, chỉ cần bỏ đúng commit của
        giao dịch là về được bản chung (không còn commit cục bộ nào chắn đường).
        """
        self.commit(paths, "tool: lưu thay đổi cục bộ của phiên trước giao dịch")
        self.pull()
        for _ in range(3):
            if self.head() == self._remote_tip() or self._push_once():
                return
            self.pull()
        raise SyncConflict("Không đẩy được thay đổi cục bộ có sẵn lên bản chung — chưa làm gì thêm; thử lại sau")

    def _drop(self, base: str, paths: list[str], why: str) -> str | None:
        """Bỏ đúng phần giao dịch đã ghi kể từ `base`. Trả tên ref cứu hộ giữ phần bị bỏ (None nếu không có gì)."""
        self._git("rebase", "--abort", check=False)
        self._git("add", "-A", "--", *paths, check=False)
        if self._git("diff", "--cached", "--quiet", "--", *paths, check=False).returncode != 0:
            self._git("commit", "-q", "-m", f"tool: (bị hủy) {why}"[:200], "--", *paths)
        head = self.head()
        if head == base:
            return None
        ref = f"refs/tool-discarded/{stamp()}-{head[:8]}"
        self._git("update-ref", ref, head)
        # --keep: chỉ đụng tới tệp khác nhau giữa HEAD và base (tức tệp của giao dịch); tệp dở khác giữ nguyên.
        r = self._git("reset", "-q", "--keep", base, check=False)
        if r.returncode != 0:
            raise SyncFailed(f"Không hoàn lại được giao dịch ({(r.stderr or r.stdout).strip()[-200:]}). Phần đang dở được giữ ở {ref}. "
                             f"Cần xử lý tay trước khi chạy tiếp.")
        return ref

    def _catch_up(self) -> None:
        """Đưa bản sao lên bản chung mới nhất đã tải về. Không lên được thì báo lỗi — không để lệch trong im lặng."""
        tip = self._remote_tip()
        if tip is None or self.head() == tip:
            return
        r = self._git("merge", "-q", "--ff-only", f"{self.remote}/{self.branch}", check=False)
        if r.returncode != 0:
            raise SyncFailed(f"Giao dịch đã được hoàn lại nhưng bản sao chưa lên được bản chung mới nhất "
                             f"({(r.stderr or r.stdout).strip()[-200:]}) — chạy `git pull --rebase` rồi thử lại")

    def _lose(self, base: str, paths: list[str], why: str, files: list[str] | None = None) -> SyncConflict:
        ref = self._drop(base, paths, why)
        self._catch_up()
        where = (", ".join(files) if files else "cùng vùng dữ liệu")
        return SyncConflict(f"Phiên khác đã ghi trước vào: {where} — thay đổi của phiên này đã được hủy"
                            + (f" (bản bị hủy giữ ở {ref})" if ref else ""))

    def push(self, base: str, paths: list[str], retries: int = 3) -> None:
        """Đẩy commit của giao dịch. `base` = commit ngay dưới nó; được cập nhật mỗi lần nối lại lên bản chung mới."""
        self._base = base
        for _ in range(retries):
            if self._push_once():
                return
            fetched = self._git("fetch", "-q", self.remote, self.branch, check=False)
            if fetched.returncode != 0:  # mất mạng/remote: không biết ai thắng → không giữ thay đổi chưa được xác nhận
                ref = self._drop(base, paths, "không đẩy được và không tải được bản chung")
                raise SyncFailed("Không đẩy được và không tải được bản chung (mạng/remote) — thay đổi của phiên này đã được hoàn lại"
                                 + (f"; bản bị hủy giữ ở {ref}" if ref else ""))
            if self._git("rebase", "-q", "--autostash", f"{self.remote}/{self.branch}", check=False).returncode != 0:
                files = self._git("diff", "--name-only", "--diff-filter=U", check=False).stdout.split()
                raise self._lose(base, paths, "thua khi đẩy", files)
            base = self._base = self._remote_tip() or base  # nối xong: commit của giao dịch giờ nằm trên bản chung mới
        raise self._lose(base, paths, "không đẩy được sau nhiều lần thử")

    @contextmanager
    def tx(self, message: str, paths: list[str] | None = None):
        """Giao dịch: kéo → (thân lệnh sửa tệp) → commit → đẩy. Lỗi hoặc xung đột thì hoàn lại đúng phần giao dịch đã ghi."""
        paths = paths or ["tool"]
        self._require_ready()
        self._settle(paths)
        base = self.head()
        try:
            yield
        except BaseException:
            self._drop(base, paths, "lệnh lỗi giữa giao dịch")
            raise
        try:
            if self.commit(paths, message):
                self.push(base, paths)
        except SyncFailed:
            raise  # đã hoàn lại (hoặc đã báo cần xử lý tay) ở bên trong
        except BaseException:
            self._drop(self._base or base, paths, "lỗi khi commit/đẩy")
            raise
        finally:
            self._base = None
