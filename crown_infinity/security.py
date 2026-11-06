"""Băm mật khẩu (scrypt, dự phòng PBKDF2), token phiên, giới hạn đăng nhập."""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import threading
import time

MIN_PASSWORD_LEN = 10
_SCRYPT = {"n": 2 ** 14, "r": 8, "p": 1, "dklen": 32}
_PBKDF2_ITER = 600_000


def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode("ascii")


def _unb64(s: str) -> bytes:
    return base64.b64decode(s.encode("ascii"))


def hash_password(password: str) -> str:
    if len(password) < MIN_PASSWORD_LEN:
        raise ValueError(f"Mật khẩu cần tối thiểu {MIN_PASSWORD_LEN} ký tự")
    salt = secrets.token_bytes(16)
    if hasattr(hashlib, "scrypt"):
        dk = hashlib.scrypt(password.encode("utf-8"), salt=salt, maxmem=64 * 1024 * 1024, **_SCRYPT)
        return f"scrypt${_SCRYPT['n']}${_SCRYPT['r']}${_SCRYPT['p']}${_b64(salt)}${_b64(dk)}"
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _PBKDF2_ITER)
    return f"pbkdf2_sha256${_PBKDF2_ITER}${_b64(salt)}${_b64(dk)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        parts = stored.split("$")
        if parts[0] == "scrypt":
            n, r, p = int(parts[1]), int(parts[2]), int(parts[3])
            salt, expected = _unb64(parts[4]), _unb64(parts[5])
            dk = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=n, r=r, p=p,
                                dklen=len(expected), maxmem=64 * 1024 * 1024)
        elif parts[0] == "pbkdf2_sha256":
            it, salt, expected = int(parts[1]), _unb64(parts[2]), _unb64(parts[3])
            dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, it)
        else:
            return False
    except (ValueError, IndexError):
        return False
    return hmac.compare_digest(dk, expected)


# Băm giả để thời gian trả lời khi sai tên đăng nhập gần bằng khi sai mật khẩu.
_DUMMY_HASH = None


def dummy_verify(password: str) -> None:
    global _DUMMY_HASH
    if _DUMMY_HASH is None:
        _DUMMY_HASH = hash_password("dummy-password-not-used")
    verify_password(password, _DUMMY_HASH)


def new_token() -> str:
    return secrets.token_urlsafe(32)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def safe_equal(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


class LoginLimiter:
    """Tối đa `max_failures` lần sai trong `window` giây cho mỗi (tên, IP) và mỗi IP. Bộ nhớ trong tiến trình."""

    def __init__(self, max_failures: int = 5, window: int = 15 * 60, ip_max: int = 30):
        self.max_failures, self.window, self.ip_max = max_failures, window, ip_max
        self._fails: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def _recent(self, key: str, now: float) -> list[float]:
        items = [t for t in self._fails.get(key, []) if now - t < self.window]
        self._fails[key] = items
        return items

    def blocked(self, username: str, ip: str) -> int:
        """Trả về số giây còn bị khóa (0 = không bị khóa)."""
        now = time.monotonic()
        with self._lock:
            for key, limit in ((f"u:{username.lower()}|{ip}", self.max_failures), (f"ip:{ip}", self.ip_max)):
                items = self._recent(key, now)
                if len(items) >= limit:
                    return int(self.window - (now - items[0])) + 1
        return 0

    def fail(self, username: str, ip: str) -> None:
        now = time.monotonic()
        with self._lock:
            for key in (f"u:{username.lower()}|{ip}", f"ip:{ip}"):
                self._recent(key, now).append(now)

    def success(self, username: str, ip: str) -> None:
        with self._lock:
            self._fails.pop(f"u:{username.lower()}|{ip}", None)
