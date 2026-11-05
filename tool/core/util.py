"""Tiện ích dùng chung của TOOL: gốc dữ liệu, ghi nguyên tử, khóa tệp, mã định danh, băm."""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import time
import unicodedata
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DATA_DIRS = ("inbox", "workbench", "catalog", "skills", "agents", "runs")


class ToolError(Exception):
    """Thao tác TOOL bị từ chối vì vi phạm luật (quyền, khóa, trạng thái, hạn mức)."""


def tool_root() -> Path:
    """Gốc dữ liệu TOOL. Biến TOOL_ROOT đổi gốc (dùng cho test và vùng thử)."""
    env = os.environ.get("TOOL_ROOT")
    return Path(env).resolve() if env else REPO_ROOT / "tool"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime | None = None) -> str:
    return (dt or utc_now()).astimezone(timezone.utc).isoformat(timespec="seconds")


def parse_iso(s: str) -> datetime:
    return datetime.fromisoformat(s)


def vn(dt_or_iso) -> str:
    if not dt_or_iso:
        return "-"
    dt = parse_iso(dt_or_iso) if isinstance(dt_or_iso, str) else dt_or_iso
    return dt.astimezone(VN_TZ).strftime("%Y-%m-%d %H:%M:%S (UTC+7)")


def stamp(dt: datetime | None = None, fmt: str = "%Y%m%d-%H%M%S") -> str:
    return (dt or utc_now()).astimezone(VN_TZ).strftime(fmt)


def new_id(prefix: str, dt: datetime | None = None, fmt: str = "%Y%m%d") -> str:
    return f"{prefix}-{stamp(dt, fmt)}-{secrets.token_hex(3)}"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def read_json(path: Path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        if default is not None:
            return default
        raise


def atomic_write_text(path: Path, text: str) -> None:
    """Ghi qua tệp tạm rồi đổi tên: người đọc không bao giờ thấy tệp ghi dở."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{secrets.token_hex(4)}.tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def atomic_write_json(path: Path, data) -> None:
    atomic_write_text(path, json.dumps(data, ensure_ascii=False, indent=1, sort_keys=False) + "\n")


def append_jsonl(path: Path, record: dict) -> None:
    """Nhật ký chỉ thêm: mỗi dòng một bản ghi, ghi một lần gọi write (nguyên tử với O_APPEND)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    line = (json.dumps(record, ensure_ascii=False) + "\n").encode("utf-8")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    try:
        os.write(fd, line)
    finally:
        os.close(fd)


def read_jsonl(path: Path) -> list[dict]:
    try:
        return [json.loads(x) for x in Path(path).read_text(encoding="utf-8").splitlines() if x.strip()]
    except FileNotFoundError:
        return []


@contextmanager
def mutex(path: Path, timeout: float = 15.0, stale_after: float = 120.0):
    """Khóa ngắn cho một lần đọc-sửa-ghi. Dựa trên mkdir nguyên tử, chạy được cả Windows.

    Khóa cũ hơn `stale_after` giây coi như bỏ quên (tiến trình chết) và bị phá.
    """
    lock = Path(str(path) + ".lock")
    lock.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout
    while True:
        try:
            os.mkdir(lock)
            break
        except FileExistsError:
            try:
                if time.time() - lock.stat().st_mtime > stale_after:
                    os.rmdir(lock)
                    continue
            except OSError:
                pass  # vừa bị gỡ hoặc còn tệp bên trong: thử lại
            if time.monotonic() > deadline:
                raise ToolError(f"Không lấy được khóa {lock.name} sau {timeout:.0f} giây") from None
            time.sleep(0.02)
    try:
        yield
    finally:
        try:
            os.rmdir(lock)
        except OSError:
            pass


def fold(text: str) -> str:
    """Chuẩn hóa để tìm kiếm: chữ thường, bỏ dấu tiếng Việt (đ→d)."""
    text = unicodedata.normalize("NFD", str(text).lower()).replace("đ", "d")
    return "".join(c for c in text if unicodedata.category(c) != "Mn")


def rel(path: Path, root: Path | None = None) -> str:
    """Đường dẫn tương đối kiểu '/' so với gốc repo (hoặc nguyên văn nếu nằm ngoài)."""
    p = Path(path).resolve()
    for base in ([root.resolve()] if root else []) + [REPO_ROOT]:
        try:
            return p.relative_to(base).as_posix()
        except ValueError:
            continue
    return p.as_posix()
