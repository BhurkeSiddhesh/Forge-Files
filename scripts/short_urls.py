"""Persistent short-link store for the free URL shortener.

Mappings live in a local SQLite database (default ``data/short_urls.db``,
override with ``SHORT_URL_DB_PATH``), mirroring ``event_log.py``. Only
``http``/``https`` destinations are accepted; schemes such as ``javascript:``
and ``data:`` are rejected. Short codes are collision-safe random tokens.
"""

from __future__ import annotations

import logging
import os
import re
import secrets
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional, Tuple
from urllib.parse import urlsplit, urlunsplit

logger = logging.getLogger("file_forge.short_urls")

_BASE_DIR = Path(__file__).resolve().parent.parent
_DATA_DIR = Path(os.environ.get("FF_DATA_DIR") or _BASE_DIR)

MAX_URL_LENGTH = 2048
CODE_LENGTH = 5
# Links issued before the length dropped to 5 had 7 characters and must keep resolving.
LEGACY_CODE_LENGTH = 7
# Alphabet without ambiguous 0/O/1/l/I characters.
_CODE_ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_CODE_RE = re.compile(
    rf"^(?:[{re.escape(_CODE_ALPHABET)}]{{{CODE_LENGTH}}}"
    rf"|[{re.escape(_CODE_ALPHABET)}]{{{LEGACY_CODE_LENGTH}}})$"
)
ALLOWED_SCHEMES = frozenset({"http", "https"})
DEFAULT_TTL_DAYS = int(os.environ.get("SHORT_URL_TTL_DAYS", "365"))
MAX_CREATE_ATTEMPTS = 12

_SCHEMA = """
CREATE TABLE IF NOT EXISTS short_urls (
    code       TEXT PRIMARY KEY NOT NULL,
    url        TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT,
    hits       INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_short_urls_expires ON short_urls (expires_at);
"""

_init_lock = threading.Lock()
_initialized_paths: set = set()
_write_lock = threading.Lock()
_conn: Optional[sqlite3.Connection] = None
_conn_path: Optional[str] = None


def db_path() -> Path:
    override = os.environ.get("SHORT_URL_DB_PATH")
    if override:
        return Path(override)
    return _DATA_DIR / "data" / "short_urls.db"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def _ensure_db(path: Optional[Path] = None) -> Path:
    target = path or db_path()
    key = str(target.resolve()) if target.exists() or target.parent.exists() else str(target)
    with _init_lock:
        if key in _initialized_paths and target.exists():
            return target
        target.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(str(target), timeout=30) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(_SCHEMA)
            conn.commit()
        _initialized_paths.add(key)
    return target


def _writer() -> sqlite3.Connection:
    global _conn, _conn_path
    path = str(_ensure_db())
    with _write_lock:
        if _conn is not None and _conn_path == path:
            return _conn
        if _conn is not None:
            try:
                _conn.close()
            except sqlite3.Error:
                pass
        _conn = sqlite3.connect(path, timeout=30, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn_path = path
        return _conn


def close_connections() -> None:
    """Close the long-lived writer (called from application lifespan)."""
    global _conn, _conn_path
    with _write_lock:
        if _conn is not None:
            try:
                _conn.close()
            except sqlite3.Error as exc:
                logger.warning("short_urls close failed: %s", exc)
            _conn = None
            _conn_path = None


def validate_url(raw: str) -> str:
    """Normalize and validate a destination URL.

    Raises ``ValueError`` with a user-facing message on rejection.
    """
    if raw is None:
        raise ValueError("URL is required.")
    url = str(raw).strip()
    if not url:
        raise ValueError("URL is required.")
    if len(url) > MAX_URL_LENGTH:
        raise ValueError(f"URL must be at most {MAX_URL_LENGTH} characters.")
    if any(ch.isspace() for ch in url):
        raise ValueError("URL must not contain whitespace.")
    if "\\" in url:
        raise ValueError("URL contains invalid characters.")

    parts = urlsplit(url)
    scheme = (parts.scheme or "").lower()
    if scheme not in ALLOWED_SCHEMES:
        raise ValueError("Only http and https URLs are allowed.")
    if not parts.netloc:
        raise ValueError("URL must include a host.")
    try:
        port = parts.port  # raises ValueError for a non-numeric or out-of-range port
    except ValueError:
        raise ValueError("URL has an invalid port.") from None
    if port == 0:
        raise ValueError("URL has an invalid port.")
    if parts.username is not None or parts.password is not None:
        raise ValueError("URLs with embedded credentials are not allowed.")
    # "http://:80/x" has a netloc but no hostname; an explicit localhost is allowed.
    if not parts.hostname:
        raise ValueError("URL must include a host.")
    # Reject control characters that survived stripping.
    if any(ord(ch) < 32 for ch in url):
        raise ValueError("URL contains invalid characters.")

    # Rebuild with normalized scheme; keep path/query/fragment as provided.
    normalized = urlunsplit(
        (scheme, parts.netloc, parts.path or "", parts.query, parts.fragment)
    )
    if len(normalized) > MAX_URL_LENGTH:
        raise ValueError(f"URL must be at most {MAX_URL_LENGTH} characters.")
    return normalized


def _generate_code() -> str:
    return "".join(secrets.choice(_CODE_ALPHABET) for _ in range(CODE_LENGTH))


def is_valid_code(code: str) -> bool:
    return bool(code) and bool(_CODE_RE.fullmatch(code))


def create_short_url(
    raw_url: str,
    *,
    ttl_days: Optional[int] = None,
    code: Optional[str] = None,
) -> Tuple[str, str, Optional[str]]:
    """Create a short mapping.

    Returns ``(code, url, expires_at_iso)``.
    """
    url = validate_url(raw_url)
    days = DEFAULT_TTL_DAYS if ttl_days is None else int(ttl_days)
    if days < 1 or days > 3650:
        raise ValueError("TTL must be between 1 and 3650 days.")
    now = _utc_now()
    expires = _iso(now + timedelta(days=days))
    created = _iso(now)

    if code is not None:
        if not is_valid_code(code):
            raise ValueError("Invalid short code.")
        preferred = code
    else:
        preferred = None

    conn = _writer()
    with _write_lock:
        for _ in range(MAX_CREATE_ATTEMPTS):
            candidate = preferred or _generate_code()
            try:
                conn.execute(
                    "INSERT INTO short_urls (code, url, created_at, expires_at, hits) "
                    "VALUES (?, ?, ?, ?, 0)",
                    (candidate, url, created, expires),
                )
                conn.commit()
                return candidate, url, expires
            except sqlite3.IntegrityError:
                if preferred is not None:
                    raise ValueError("That short code is already in use.") from None
                continue
        raise RuntimeError("Could not allocate a unique short code.")


def resolve(code: str, *, record_hit: bool = True) -> Optional[str]:
    """Return the destination URL for ``code``, or ``None`` if missing/expired."""
    if not is_valid_code(code):
        return None
    conn = _writer()
    now = _iso(_utc_now())
    with _write_lock:
        row = conn.execute(
            "SELECT url, expires_at FROM short_urls WHERE code = ?",
            (code,),
        ).fetchone()
        if row is None:
            return None
        expires_at = row["expires_at"]
        if expires_at and expires_at < now:
            conn.execute("DELETE FROM short_urls WHERE code = ?", (code,))
            conn.commit()
            return None
        if record_hit:
            conn.execute(
                "UPDATE short_urls SET hits = hits + 1 WHERE code = ?",
                (code,),
            )
            conn.commit()
        return row["url"]


def prune_expired() -> int:
    """Delete expired rows. Returns the number of rows removed."""
    conn = _writer()
    now = _iso(_utc_now())
    with _write_lock:
        cur = conn.execute(
            "DELETE FROM short_urls WHERE expires_at IS NOT NULL AND expires_at < ?",
            (now,),
        )
        conn.commit()
        return cur.rowcount or 0


def reset_for_tests() -> None:
    """Drop all rows and reset connection state (tests only)."""
    close_connections()
    path = db_path()
    if path.exists():
        try:
            path.unlink()
        except OSError:
            pass
    _initialized_paths.clear()
