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
    if getattr(args, "graphify", None):
        kw["graphify_bin"] = args.graphify
    if getattr(args, "no_scheduler", False):
        kw["scheduler"] = False
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
    from .backup import BackupError, acquire_lock, release_lock, update_lock
    try:
        acquire_lock(cfg.lock_path, {"kind": "serve", "host": cfg.host})
    except BackupError as exc:
        raise SystemExit(str(exc))
    try:
        app = App(cfg)
        srv = Server((cfg.host, cfg.port), make_handler(app))
    except BaseException:
        release_lock(cfg.lock_path)
        raise
    real_port = srv.server_address[1]
    update_lock(cfg.lock_path, {"kind": "serve", "port": real_port, "host": cfg.host})
    sched = None
    if cfg.scheduler:
        from .ops import Scheduler
        sched = Scheduler(cfg.db_path, cfg.repo_root)
        sched.start()
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
        if sched:
            sched.stop()
        srv.server_close()
        release_lock(cfg.lock_path)
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


def _owner_and_project(conn, username: str, project: str, create: bool = False):
    from . import auth as AU
    from .platform import create_project
    u = conn.execute("SELECT * FROM users WHERE username=? COLLATE NOCASE AND role='OWNER' AND is_active=1",
                     (username,)).fetchone()
    if not u:
        raise SystemExit(f"Không có OWNER đang hoạt động tên '{username}'")
    actor = AU.Actor("USER", u["id"], "OWNER", u["display_name"])
    p = conn.execute("SELECT id FROM projects WHERE id=? OR name=? ORDER BY id=? DESC LIMIT 1",
                     (project, project, project)).fetchone()
    if not p:
        if not create:
            raise SystemExit(f"Không có dự án '{project}'")
        return actor, create_project(conn, actor, {"name": project})["id"]
    return actor, p["id"]


def cmd_graph_build(args) -> int:
    from .graphmap import build_map
    from .httpcore import ApiError
    cfg = _cfg(args)
    conn = _open_db(cfg)
    try:
        actor, pid = _owner_and_project(conn, args.as_user, args.project)
        try:
            info = build_map(conn, actor, cfg.repo_root, pid, args.scope, args.graphify)
        except ApiError as exc:
            print(f"LỖI {exc.code}: {exc.message}", file=sys.stderr)
            if exc.details:
                print(json.dumps(exc.details, ensure_ascii=False)[:2000], file=sys.stderr)
            return 2
        print(json.dumps(info, ensure_ascii=False, indent=2))
        return 0
    finally:
        conn.close()


def cmd_decision_add(args) -> int:
    from .decisions import create_decision
    cfg = _cfg(args)
    conn = _open_db(cfg)
    try:
        actor, pid = _owner_and_project(conn, args.as_user, args.project, create=True)
        resp = create_decision(conn, actor, {"project_id": pid, "title": args.title, "content": args.content,
                                             "source": args.source, "decided_at": args.decided_at or ""})
        print(resp.body.decode("utf-8"))
        return 0
    finally:
        conn.close()


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
    s.add_argument("--graphify", help="Đường dẫn lệnh graphify (gói graphifyy) để dựng bản đồ mã")
    s.add_argument("--no-scheduler", action="store_true", help="Không chạy bộ lập lịch rà soát trong tiến trình này")
    s.set_defaults(fn=cmd_serve)
    s = sub.add_parser("graph-build", help="Dựng bản đồ mã bằng Graphify ở commit HEAD và lưu vào database")
    s.add_argument("--project", required=True, help="Tên hoặc mã dự án")
    s.add_argument("--scope", default="crown_infinity")
    s.add_argument("--graphify")
    s.add_argument("--repo-root")
    s.add_argument("--as-user", required=True, help="Tên đăng nhập OWNER chịu trách nhiệm")
    s.set_defaults(fn=cmd_graph_build)
    s = sub.add_parser("decision-add", help="OWNER ghi một quyết định đã xác nhận từ dòng lệnh")
    s.add_argument("--project", required=True, help="Tên hoặc mã dự án (tạo mới nếu chưa có tên này)")
    s.add_argument("--title", required=True)
    s.add_argument("--content", required=True)
    s.add_argument("--source", required=True)
    s.add_argument("--decided-at")
    s.add_argument("--as-user", required=True, help="Tên đăng nhập OWNER xác nhận")
    s.set_defaults(fn=cmd_decision_add)
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
