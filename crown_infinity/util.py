"""Tiện ích dùng chung: thời gian, ID, băm, gấp dấu tiếng Việt."""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import re
import secrets
import unicodedata


def now_iso() -> str:
    """Thời điểm hiện tại, UTC, độ chính xác mili giây."""
    t = _dt.datetime.now(_dt.timezone.utc)
    return t.strftime("%Y-%m-%dT%H:%M:%S.") + f"{t.microsecond // 1000:03d}Z"


def parse_iso(value: str) -> _dt.datetime:
    return _dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=_dt.timezone.utc)


def iso_after(seconds: int) -> str:
    t = _dt.datetime.now(_dt.timezone.utc) + _dt.timedelta(seconds=seconds)
    return t.strftime("%Y-%m-%dT%H:%M:%S.") + f"{t.microsecond // 1000:03d}Z"


def new_id(prefix: str) -> str:
    return f"{prefix}-{secrets.token_hex(6)}"


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def dumps(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


_D_MAP = str.maketrans({"đ": "d", "Đ": "d"})


def fold(text: str) -> str:
    """Gấp chữ để tìm kiếm không phân biệt dấu/hoa thường: 'Phân Tích Đơn' -> 'phan tich don'."""
    if not text:
        return ""
    decomposed = unicodedata.normalize("NFD", text.translate(_D_MAP))
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return unicodedata.normalize("NFC", stripped).casefold()


_TOKEN_RE = re.compile(r"\w+", re.UNICODE)


def tokens(text: str) -> list[str]:
    return _TOKEN_RE.findall(fold(text))
