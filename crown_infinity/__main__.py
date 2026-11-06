"""Lệnh: py -m crown_infinity <lệnh>

  serve            chạy máy chủ (mặc định 127.0.0.1:8765)
  init-owner       tạo tài khoản OWNER đầu tiên (hỏi mật khẩu, không hiện ra màn hình)
  create-project   tạo dự án
  migrate          áp dụng migration (tự sao lưu trước nếu DB đã có dữ liệu)
  backup           sao lưu nhất quán
  restore          khôi phục từ bản sao lưu (máy chủ phải dừng); --verify-only để chỉ kiểm tra
  status           in đường dẫn dữ liệu, schema, số liệu
"""
from __future__ import annotations

import argparse
import getpass
import json
import logging
import os
import signal
import sys
import tempfile
import threading
from pathlib import Path

from . import APP_NAME, APP_VERSION
from .config import Config, default_data_dir


def _cfg(args) -> Config:
    kw = {"data_dir": Path(args.data_dir) if args.data_dir else default_data_dir()}
    if getattr(args, "host", None):
        kw["host"] = args.host
    if getattr(args, "port", None):
        kw["port"] = args.port
    if getattr(args, "allowed_host", None):
        kw["allowed_hosts"] = tuple(args.allowed_host)
    if getattr(args, "secure_cookies", False):
        kw["secure_cookies"] = True
    if getattr(args, "repo_root", None):
        kw["repo_root"] = Path(args.repo_root)
    return Config(**kw)


def _read_password(args) -> str:
    if args.password_stdin:
        return sys.stdin.readline().rstrip("\r\n")
    p1 = getpass.getpass("Mật khẩu (tối thiểu 10 ký tự): ")
    p2 = getpass.getpass("Nhập lại mật khẩu: ")
    if p1 != p2:
        raise SystemExit("Hai lần nhập không khớp.")
    return p1


def cmd_serve(args) -> int:
    from .app import App
    from .httpcore import Server, make_handler
    cfg = _cfg(args)
    cfg.ensure_dirs()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        handlers=[logging.FileHandler(cfg.log_dir / "server.log", encoding="utf-8")])
    if cfg.host in ("0.0.0.0", "::") and not args.allow_lan:
        raise SystemExit("Nghe trên mọi địa chỉ cần cờ --allow-lan (cảnh báo: chỉ dùng trong mạng tin cậy, "
                         "truy cập từ xa phải qua reverse proxy có HTTPS).")
    app = App(cfg)
    srv = Server((cfg.host, cfg.port), make_handler(app))
    real_port = srv.server_address[1]
    cfg.lock_path.write_text(json.dumps({"pid": os.getpid(), "port": real_port, "host": cfg.host}), encoding="utf-8")
    print(f"{APP_NAME} {APP_VERSION} đang chạy: http://{'localhost' if cfg.host == '127.0.0.1' else cfg.host}:{real_port}/",
          flush=True)
    print(f"Dữ liệu: {cfg.data_dir}  ·  tìm kiếm: {app.search_mode}  ·  dừng: Ctrl+C", flush=True)
    if app.migration.get("applied"):
        print(f"Đã áp dụng migration {app.migration['applied']} (sao lưu trước: {app.migration.get('backup')})",
              flush=True)
    stop = threading.Event()

    def _stop(*_):
        stop.set()
        threading.Thread(target=srv.shutdown, daemon=True).start()

    signal.signal(signal.SIGINT, _stop)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _stop)
    try:
        srv.serve_forever(poll_interval=0.5)
    finally:
        srv.server_close()
        try:
            cfg.lock_path.unlink()
        except FileNotFoundError:
            pass
        print("Đã dừng.", flush=True)
    return 0


def _open_db(cfg: Config):
    from .db import connect, migrate
    cfg.ensure_dirs()
    migrate(cfg.db_path, backup_dir=cfg.backup_dir)
    return connect(cfg.db_path)


def cmd_init_owner(args) -> int:
    from . import auth as AU
    from .platform import create_project, sync_modules
    cfg = _cfg(args)
    conn = _open_db(cfg)
    try:
        if AU.owner_exists(conn):
            print("Đã có OWNER. Không tạo thêm (dùng trang Quản trị để thêm người dùng).")
            return 1
        user = AU.create_user(conn, args.username, _read_password(args), "OWNER", args.display_name or "")
        sync_modules(conn)
        if not conn.execute("SELECT 1 FROM projects").fetchone():
            create_project(conn, AU.Actor("USER", user["id"], "OWNER"),
                           {"name": "Chung", "description": "Dự án mặc định"})
        print(f"Đã tạo OWNER '{user['username']}'. Dữ liệu: {cfg.data_dir}")
        return 0
    finally:
        conn.close()


def cmd_create_project(args) -> int:
    from . import auth as AU
    from .platform import create_project
    cfg = _cfg(args)
    conn = _open_db(cfg)
    try:
        p = create_project(conn, AU.SYSTEM, {"name": args.name, "description": args.description or ""})
        print(json.dumps(p, ensure_ascii=False))
        return 0
    finally:
        conn.close()


def cmd_migrate(args) -> int:
    from .db import migrate
    cfg = _cfg(args)
    cfg.ensure_dirs()
    print(json.dumps(migrate(cfg.db_path, backup_dir=cfg.backup_dir), ensure_ascii=False))
    return 0


def cmd_backup(args) -> int:
    from .backup import create_backup
    cfg = _cfg(args)
    p = create_backup(cfg.db_path, Path(args.out) if args.out else cfg.backup_dir, reason=args.reason)
    print(p)
    return 0


def cmd_restore(args) -> int:
    from .backup import BackupError, restore_backup, verify_backup
    cfg = _cfg(args)
    try:
        if args.verify_only:
            with tempfile.TemporaryDirectory() as tmp:
                meta, _ = verify_backup(args.file, Path(tmp))
            print("HỢP LỆ:", json.dumps(meta, ensure_ascii=False))
            return 0
        res = restore_backup(args.file, cfg.db_path, cfg.backup_dir, cfg.lock_path)
    except BackupError as exc:
        print(f"LỖI: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(res, ensure_ascii=False))
    return 0


def cmd_status(args) -> int:
    from .db import connect, latest_schema_version, schema_version, table_counts
    cfg = _cfg(args)
    out = {"app": APP_NAME, "version": APP_VERSION, "data_dir": str(cfg.data_dir), "db": str(cfg.db_path),
           "db_exists": cfg.db_path.exists(), "code_schema": latest_schema_version(),
           "server_lock": cfg.lock_path.read_text(encoding="utf-8") if cfg.lock_path.exists() else None}
    if cfg.db_path.exists():
        c = connect(cfg.db_path, readonly=True)
        try:
            out["db_schema"] = schema_version(c)
            out["counts"] = table_counts(c)
        finally:
            c.close()
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


def main(argv=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
            sys.stderr.reconfigure(encoding="utf-8")
        except (OSError, ValueError):
            pass
    ap = argparse.ArgumentParser(prog="crown_infinity", description=f"{APP_NAME} {APP_VERSION}")
    ap.add_argument("--data-dir", help="Thư mục dữ liệu (mặc định %%LOCALAPPDATA%%\\CrownInfinity hoặc "
                                       "~/.local/share/crown-infinity; hoặc biến CROWN_INFINITY_DATA)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve")
    s.add_argument("--host", default=None)
    s.add_argument("--port", type=int, default=None)
    s.add_argument("--allowed-host", action="append", help="Tên host được phép thêm (reverse proxy)")
    s.add_argument("--allow-lan", action="store_true")
    s.add_argument("--secure-cookies", action="store_true", help="Bật khi chạy sau HTTPS")
    s.add_argument("--repo-root", help="Thư mục repo crown-agent (đọc registries/, tool/catalog)")
    s.set_defaults(fn=cmd_serve)
    s = sub.add_parser("init-owner")
    s.add_argument("--username", required=True)
    s.add_argument("--display-name")
    s.add_argument("--password-stdin", action="store_true", help="Đọc mật khẩu từ stdin (tự động hóa)")
    s.set_defaults(fn=cmd_init_owner)
    s = sub.add_parser("create-project")
    s.add_argument("--name", required=True)
    s.add_argument("--description")
    s.set_defaults(fn=cmd_create_project)
    sub.add_parser("migrate").set_defaults(fn=cmd_migrate)
    s = sub.add_parser("backup")
    s.add_argument("--out")
    s.add_argument("--reason", default="manual")
    s.set_defaults(fn=cmd_backup)
    s = sub.add_parser("restore")
    s.add_argument("file")
    s.add_argument("--verify-only", action="store_true")
    s.set_defaults(fn=cmd_restore)
    sub.add_parser("status").set_defaults(fn=cmd_status)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
