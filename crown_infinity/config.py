"""Cấu hình chạy: thư mục dữ liệu nằm NGOÀI repo/thư mục build."""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
REPO_ROOT = PACKAGE_DIR.parent


def default_data_dir() -> Path:
    env = os.environ.get("CROWN_INFINITY_DATA")
    if env:
        return Path(env).expanduser()
    if sys.platform.startswith("win"):
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "CrownInfinity"
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "crown-infinity"


@dataclass
class Config:
    data_dir: Path = field(default_factory=default_data_dir)
    host: str = "127.0.0.1"
    port: int = 8765
    allowed_hosts: tuple = ()
    secure_cookies: bool = False          # bật khi chạy sau HTTPS (reverse proxy)
    repo_root: Path = REPO_ROOT           # để đọc registries/ và tool/catalog của CROWN AGENT
    session_idle_seconds: int = 12 * 3600
    session_absolute_seconds: int = 7 * 24 * 3600
    max_body_bytes: int = 4 * 1024 * 1024
    graphify_bin: str | None = None       # đường dẫn lệnh graphify (tùy chọn)
    scheduler: bool = True                # chạy bộ lập lịch trong tiến trình serve

    def __post_init__(self):
        self.data_dir = Path(self.data_dir)
        self.repo_root = Path(self.repo_root)

    @property
    def db_path(self) -> Path:
        return self.data_dir / "crown_infinity.sqlite"

    @property
    def backup_dir(self) -> Path:
        return self.data_dir / "backups"

    @property
    def log_dir(self) -> Path:
        return self.data_dir / "logs"

    @property
    def lock_path(self) -> Path:
        return self.data_dir / "server.lock"

    def ensure_dirs(self) -> None:
        for d in (self.data_dir, self.backup_dir, self.log_dir):
            d.mkdir(parents=True, exist_ok=True)

    def host_allowlist(self) -> set[str]:
        hosts = {"127.0.0.1", "localhost", "::1", "[::1]"}
        if self.host not in ("0.0.0.0", "::"):
            hosts.add(self.host)
        hosts.update(h.lower() for h in self.allowed_hosts)
        return hosts
