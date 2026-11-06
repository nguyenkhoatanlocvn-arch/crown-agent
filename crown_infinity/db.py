"""Kết nối SQLite, giao dịch, migration chỉ tiến (rollback = khôi phục bản sao lưu trước migration)."""
from __future__ import annotations

import contextlib
import re
import sqlite3
from pathlib import Path

from .util import now_iso, sha256_text

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"
_MIG_RE = re.compile(r"^(\d{4})_([a-z0-9_]+)\.sql$")


class MigrationError(RuntimeError):
    pass


def connect(path, *, readonly: bool = False) -> sqlite3.Connection:
    """Mỗi luồng/yêu cầu mở kết nối riêng. Chế độ autocommit; ghi phải đi qua tx()."""
    if readonly:
        uri = Path(path).resolve().as_uri() + "?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=10, isolation_level=None)
    else:
        conn = sqlite3.connect(str(path), timeout=10, isolation_level=None)
    conn.row_factory = sqlite3.Row
    # busy_timeout phải đặt TRƯỚC mọi lệnh cần khóa (bài học từ lỗi 'database is locked' của crown/tasks.py).
    conn.execute("PRAGMA busy_timeout = 10000")
    conn.execute("PRAGMA foreign_keys = ON")
    if not readonly:
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        if mode.lower() != "wal":
            conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
    return conn


@contextlib.contextmanager
def tx(conn: sqlite3.Connection):
    """Giao dịch ghi BEGIN IMMEDIATE: tránh nâng khóa giữa chừng khi nhiều yêu cầu ghi đồng thời."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


def list_migrations(migrations_dir: Path | None = None) -> list[tuple[int, str, Path]]:
    d = Path(migrations_dir or MIGRATIONS_DIR)
    out = []
    for p in sorted(d.iterdir()):
        m = _MIG_RE.match(p.name)
        if m:
            out.append((int(m.group(1)), m.group(2), p))
    versions = [v for v, _, _ in out]
    if versions != list(range(1, len(versions) + 1)):
        raise MigrationError(f"Số migration phải liên tục từ 0001: {versions}")
    return out


def latest_schema_version(migrations_dir: Path | None = None) -> int:
    migs = list_migrations(migrations_dir)
    return migs[-1][0] if migs else 0


def schema_version(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_migrations'").fetchone()
    if not row:
        return 0
    return conn.execute("SELECT COALESCE(MAX(version),0) FROM schema_migrations").fetchone()[0]


def migrate(db_path, *, migrations_dir: Path | None = None, backup_dir: Path | None = None) -> dict:
    """Áp dụng migration còn thiếu. Nếu DB đã có dữ liệu thì sao lưu nhất quán trước khi đổi lược đồ."""
    db_path = Path(db_path)
    migs = list_migrations(migrations_dir)
    conn = connect(db_path)
    try:
        conn.execute("""CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY, name TEXT NOT NULL, checksum TEXT NOT NULL, applied_at TEXT NOT NULL)""")
        applied = {r["version"]: r for r in conn.execute("SELECT * FROM schema_migrations")}
        for v, name, path in migs:
            if v in applied and applied[v]["checksum"] != sha256_text(path.read_text(encoding="utf-8")):
                raise MigrationError(f"Migration {v:04d}_{name} đã áp dụng nhưng tệp bị sửa sau đó. Không chạy tiếp.")
        current = max(applied) if applied else 0
        known = migs[-1][0] if migs else 0
        if current > known:
            raise MigrationError(
                f"Cơ sở dữ liệu ở schema {current}, mã nguồn chỉ biết tới {known}. Hãy cập nhật ứng dụng trước.")
        pending = [m for m in migs if m[0] > current]
        backup_path = None
        if pending and current > 0 and backup_dir is not None:
            from .backup import create_backup  # tránh vòng import
            conn.close()
            backup_path = create_backup(db_path, backup_dir, reason=f"pre-migration-{current}-to-{pending[-1][0]}")
            conn = connect(db_path)
        for v, name, path in pending:
            sql = path.read_text(encoding="utf-8")
            try:
                conn.executescript(
                    "BEGIN IMMEDIATE;\n" + sql + "\n;INSERT INTO schema_migrations(version,name,checksum,applied_at) "
                    f"VALUES ({v}, '{name}', '{sha256_text(sql)}', '{now_iso()}');\nCOMMIT;")
            except sqlite3.Error as exc:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise MigrationError(f"Migration {v:04d}_{name} lỗi, đã hoàn tác: {exc}") from exc
        problems = conn.execute("PRAGMA foreign_key_check").fetchall()
        if problems:
            raise MigrationError(f"Vi phạm khóa ngoại sau migration: {len(problems)} dòng")
        return {"from": current, "to": schema_version(conn), "applied": [m[0] for m in pending],
                "backup": str(backup_path) if backup_path else None}
    finally:
        conn.close()


def table_counts(conn: sqlite3.Connection) -> dict[str, int]:
    names = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' "
        "AND name NOT LIKE 'prompt_fts%' ORDER BY name")]
    return {n: conn.execute(f'SELECT COUNT(*) FROM "{n}"').fetchone()[0] for n in names}
