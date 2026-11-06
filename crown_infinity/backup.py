"""Sao lưu nhất quán (SQLite Online Backup API) và khôi phục có kiểm tra trước trên bản tạm."""
from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import zipfile
from pathlib import Path

from . import APP_VERSION
from .db import connect, latest_schema_version, migrate, schema_version, table_counts
from .util import now_iso, sha256_bytes

FORMAT = "crown-infinity-backup"
FORMAT_VERSION = 1
MEMBERS = {"metadata.json", "database.sqlite"}
MAX_DB_BYTES = 2 * 1024 * 1024 * 1024


class BackupError(RuntimeError):
    pass


def _integrity(path: Path) -> None:
    c = sqlite3.connect(str(path))
    try:
        res = c.execute("PRAGMA integrity_check").fetchone()[0]
        if res != "ok":
            raise BackupError(f"integrity_check thất bại: {res}")
        c.execute("PRAGMA foreign_keys = ON")
        bad = c.execute("PRAGMA foreign_key_check").fetchall()
        if bad:
            raise BackupError(f"foreign_key_check thất bại: {len(bad)} dòng")
    finally:
        c.close()


def create_backup(db_path, backup_dir, *, reason: str = "manual") -> Path:
    db_path, backup_dir = Path(db_path), Path(backup_dir)
    if not db_path.exists():
        raise BackupError("Chưa có cơ sở dữ liệu để sao lưu")
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = now_iso().replace(":", "").replace("-", "").replace(".", "")[:15]
    safe_reason = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in reason)[:40]
    final = backup_dir / f"crown-infinity-{stamp}-{safe_reason}.zip"
    n = 1
    while final.exists():
        final = backup_dir / f"crown-infinity-{stamp}-{safe_reason}-{n}.zip"
        n += 1
    with tempfile.TemporaryDirectory(dir=backup_dir, prefix=".tmp-") as tmp:
        snap = Path(tmp) / "database.sqlite"
        src = connect(db_path, readonly=True)
        dst = sqlite3.connect(str(snap))
        try:
            src.backup(dst)
        finally:
            dst.close()
            src.close()
        _integrity(snap)
        c = sqlite3.connect(str(snap))
        try:
            meta = {"format": FORMAT, "format_version": FORMAT_VERSION, "app_version": APP_VERSION,
                    "schema_version": schema_version(c), "created_at": now_iso(), "reason": reason,
                    "counts": table_counts(c)}
        finally:
            c.close()
        data = snap.read_bytes()
        meta["db_sha256"] = sha256_bytes(data)
        meta["db_bytes"] = len(data)
        part = Path(tmp) / "out.zip"
        with zipfile.ZipFile(part, "w", compression=zipfile.ZIP_DEFLATED) as z:
            z.writestr("metadata.json", json.dumps(meta, ensure_ascii=False, indent=2))
            z.write(snap, "database.sqlite")
        os.replace(part, final)
    return final


def verify_backup(zip_path, work_dir) -> tuple[dict, Path]:
    """Giải nén vào thư mục tạm, kiểm băm + toàn vẹn + migrate thử bản tạm. Không đụng DB đang chạy."""
    zip_path, work_dir = Path(zip_path), Path(work_dir)
    try:
        z = zipfile.ZipFile(zip_path)
    except (zipfile.BadZipFile, OSError) as exc:
        raise BackupError(f"Không mở được tệp sao lưu: {exc}") from exc
    with z:
        names = set(z.namelist())
        if names != MEMBERS:
            raise BackupError(f"Tệp sao lưu phải chỉ gồm {sorted(MEMBERS)}, thấy {sorted(names)}")
        info = z.getinfo("database.sqlite")
        if info.file_size > MAX_DB_BYTES:
            raise BackupError("Cơ sở dữ liệu trong bản sao lưu quá lớn")
        try:
            meta = json.loads(z.read("metadata.json").decode("utf-8"))
        except ValueError as exc:
            raise BackupError("metadata.json hỏng") from exc
        if meta.get("format") != FORMAT or meta.get("format_version") != FORMAT_VERSION:
            raise BackupError("Không phải bản sao lưu CROWN INFINITY hợp lệ")
        data = z.read("database.sqlite")
    if sha256_bytes(data) != meta.get("db_sha256"):
        raise BackupError("Băm cơ sở dữ liệu không khớp metadata (tệp bị hỏng hoặc bị sửa)")
    if int(meta.get("schema_version", 0)) > latest_schema_version():
        raise BackupError("Bản sao lưu từ phiên bản ứng dụng mới hơn; hãy cập nhật ứng dụng trước")
    work_dir.mkdir(parents=True, exist_ok=True)
    out = work_dir / "restore-candidate.sqlite"
    out.write_bytes(data)
    _integrity(out)
    c = sqlite3.connect(str(out))
    try:
        counts = table_counts(c)
    finally:
        c.close()
    for table, n in (meta.get("counts") or {}).items():
        if counts.get(table) != n:
            raise BackupError(f"Số dòng bảng {table} không khớp metadata ({counts.get(table)} ≠ {n})")
    migrate(out)  # đưa bản tạm lên schema hiện tại; dữ liệu đang chạy chưa bị đụng tới
    _integrity(out)
    return meta, out


def _server_running(lock_path: Path) -> bool:
    if not lock_path.exists():
        return False
    try:
        pid = int(json.loads(lock_path.read_text(encoding="utf-8")).get("pid", 0))
    except (ValueError, OSError):
        return False
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes
        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if handle:
            ctypes.windll.kernel32.CloseHandle(handle)
            return True
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


LOCK_BUSY = ("Máy chủ CROWN INFINITY đang chạy (hoặc tiến trình khác đang dùng thư mục dữ liệu này). "
             "Dừng nó trước. Khóa: {path}")


def acquire_lock(lock_path, info: dict) -> None:
    """Khóa độc quyền thư mục dữ liệu (tạo tệp O_EXCL). Khóa của tiến trình đã chết được thu hồi.

    Dùng cho cả `serve` và `restore` để hai việc không chạy chồng (kiểm định 2026-11-06, lỗi #6).
    """
    lock_path = Path(lock_path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    for _ in range(3):
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            if _server_running(lock_path):
                raise BackupError(LOCK_BUSY.format(path=lock_path))
            try:
                lock_path.unlink()
            except FileNotFoundError:
                pass
            continue
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"pid": os.getpid(), **info}, f)
        return
    raise BackupError(LOCK_BUSY.format(path=lock_path))


def update_lock(lock_path, info: dict) -> None:
    Path(lock_path).write_text(json.dumps({"pid": os.getpid(), **info}), encoding="utf-8")


def release_lock(lock_path) -> None:
    """Chỉ xóa khóa nếu đúng là khóa của tiến trình này."""
    lock_path = Path(lock_path)
    try:
        if int(json.loads(lock_path.read_text(encoding="utf-8")).get("pid", 0)) == os.getpid():
            lock_path.unlink()
    except (OSError, ValueError):
        pass


def restore_backup(zip_path, db_path, backup_dir, lock_path) -> dict:
    """Khôi phục: kiểm trên bản tạm → sao lưu dữ liệu hiện tại → thay thế nguyên tử. Máy chủ phải đang dừng."""
    db_path, backup_dir, lock_path = Path(db_path), Path(backup_dir), Path(lock_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    acquire_lock(lock_path, {"kind": "restore"})  # giữ khóa suốt lúc khôi phục: máy chủ không khởi động chen vào
    try:
        return _restore_locked(zip_path, db_path, backup_dir)
    finally:
        release_lock(lock_path)


def _restore_locked(zip_path, db_path: Path, backup_dir: Path) -> dict:
    with tempfile.TemporaryDirectory(dir=db_path.parent, prefix=".restore-") as tmp:
        meta, candidate = verify_backup(zip_path, Path(tmp))
        safety = None
        if db_path.exists():
            c = connect(db_path)
            try:
                c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            finally:
                c.close()
            safety = create_backup(db_path, backup_dir, reason="pre-restore")
        for suffix in ("-wal", "-shm"):
            p = Path(str(db_path) + suffix)
            if p.exists():
                p.unlink()
        os.replace(candidate, db_path)
    return {"restored_from": str(zip_path), "metadata": meta, "safety_backup": str(safety) if safety else None}


def list_backups(backup_dir) -> list[dict]:
    d = Path(backup_dir)
    if not d.exists():
        return []
    out = []
    for p in sorted(d.glob("crown-infinity-*.zip"), reverse=True):
        st = p.stat()
        out.append({"name": p.name, "bytes": st.st_size})
    return out


def copy_db_for_test(src_db, dst_db) -> None:
    """Sao chép nhất quán (dùng cho kiểm thử migration trên bản sao)."""
    src = connect(src_db, readonly=True)
    dst = sqlite3.connect(str(dst_db))
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()


__all__ = ["BackupError", "acquire_lock", "release_lock", "update_lock", "create_backup", "verify_backup", "restore_backup", "list_backups", "copy_db_for_test"]
