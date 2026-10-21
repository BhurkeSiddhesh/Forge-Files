"""Update check and announcements for the Windows desktop app.

The desktop app is otherwise fully offline. This module is the one deliberate,
narrow exception: it downloads a single small public JSON file (the "feed") so
the app can tell people a new version exists and show one announcement.

What it sends: a plain GET with the user agent ``ForgeFiles-Desktop/<version>``.
No cookies, no identifiers, no file names or contents. The page itself never
makes this request (its Content-Security-Policy forbids it); the local server
does, and hands the result to the page over 127.0.0.1.

Feed format (all fields optional; anything malformed is ignored)::

    {
      "schema": 1,
      "latest_version": "0.2.0",
      "download_url": "https://www.forgefiles.org/download",
      "notes": "Faster PDF to Word.",
      "announcements": [
        {"id": "launch-1", "title": "New: Foo", "text": "One line.",
         "url": "https://example.com", "cta": "Learn more",
         "starts": "2026-10-01", "expires": "2026-12-31"}
      ]
    }
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from datetime import date
from pathlib import Path

import httpx

logger = logging.getLogger("file_forge.desktop_feed")

DEFAULT_FEED_URL = "https://www.forgefiles.org/static/desktop/feed.json"
CACHE_TTL_SECONDS = 6 * 3600
REQUEST_TIMEOUT_SECONDS = 5.0
MAX_FEED_BYTES = 64 * 1024

_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_TAG_RE = re.compile(r"<[^>]*>")
_SPACE_RE = re.compile(r"\s+")

_memory_cache: dict = {"at": 0.0, "feed": None}


def parse_version(value) -> tuple | None:
    """'1.2.3' -> (1, 2, 3); anything else -> None."""
    if not isinstance(value, str):
        return None
    value = value.strip().lstrip("vV")
    if not re.fullmatch(r"\d+(\.\d+){0,3}", value):
        return None
    return tuple(int(part) for part in value.split("."))


def _text(value, limit: int) -> str:
    if not isinstance(value, str):
        return ""
    return _SPACE_RE.sub(" ", _TAG_RE.sub("", value)).strip()[:limit]


def _https_url(value) -> str | None:
    """Only plain https links, with no credentials, are ever passed on."""
    if not isinstance(value, str):
        return None
    value = value.strip()
    match = re.fullmatch(r"https://([^/\s:@?#]+)(:\d+)?([/?#]\S*)?", value)
    if not match or len(value) > 500:
        return None
    return value


def _day(value) -> date | None:
    try:
        return date.fromisoformat(value) if isinstance(value, str) else None
    except ValueError:
        return None


def sanitize_feed(raw) -> dict:
    """Reduce an untrusted feed to the few fields the app understands."""
    if not isinstance(raw, dict):
        return {}
    feed: dict = {}
    if parse_version(raw.get("latest_version")):
        feed["latest_version"] = raw["latest_version"].strip().lstrip("vV")
    url = _https_url(raw.get("download_url"))
    if url:
        feed["download_url"] = url
    notes = _text(raw.get("notes"), 300)
    if notes:
        feed["notes"] = notes
    announcements = []
    items = raw.get("announcements")
    for item in (items if isinstance(items, list) else [])[:10]:
        if not isinstance(item, dict):
            continue
        ident = item.get("id")
        title = _text(item.get("title"), 80)
        if not (isinstance(ident, str) and _ID_RE.fullmatch(ident) and title):
            continue
        announcements.append({
            "id": ident,
            "title": title,
            "text": _text(item.get("text"), 240),
            "url": _https_url(item.get("url")),
            "cta": _text(item.get("cta"), 30) or "Learn more",
            "starts": _day(item.get("starts")),
            "expires": _day(item.get("expires")),
        })
    feed["announcements"] = announcements
    return feed


def build_response(feed: dict | None, current_version: str, today: date | None = None) -> dict:
    """What the page receives: whether an update exists and which announcement to show."""
    today = today or date.today()
    feed = feed or {}
    update = None
    latest, current = parse_version(feed.get("latest_version")), parse_version(current_version)
    if latest and current and latest > current and feed.get("download_url"):
        update = {
            "latest": feed["latest_version"],
            "url": feed["download_url"],
            "notes": feed.get("notes", ""),
        }
    announcement = None
    for item in feed.get("announcements", []):
        if item["starts"] and today < item["starts"]:
            continue
        if item["expires"] and today > item["expires"]:
            continue
        announcement = {k: item[k] for k in ("id", "title", "text", "url", "cta")}
        break
    return {"enabled": True, "current": current_version, "update": update, "announcement": announcement}


def feed_enabled(data_dir: Path) -> bool:
    """Off if FF_FEED=0, or if the person turned it off in the app."""
    if os.environ.get("FF_FEED", "1").strip().lower() in ("0", "false", "no", "off"):
        return False
    try:
        settings = json.loads((Path(data_dir) / "settings.json").read_text(encoding="utf-8"))
        return settings.get("feed", True) is not False
    except (OSError, ValueError):
        return True


def disable_feed(data_dir: Path) -> None:
    path = Path(data_dir) / "settings.json"
    try:
        settings = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(settings, dict):
            settings = {}
    except (OSError, ValueError):
        settings = {}
    settings["feed"] = False
    path.write_text(json.dumps(settings), encoding="utf-8")
    _memory_cache.update(at=0.0, feed=None)


def enable_feed(data_dir: Path) -> None:
    path = Path(data_dir) / "settings.json"
    try:
        settings = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(settings, dict):
            settings = {}
    except (OSError, ValueError):
        settings = {}
    settings["feed"] = True
    path.write_text(json.dumps(settings), encoding="utf-8")
    _memory_cache.update(at=0.0, feed=None)


def _read_disk_cache(data_dir: Path) -> dict | None:
    try:
        return sanitize_feed(json.loads((Path(data_dir) / "feed-cache.json").read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return None


async def _download(url: str, current_version: str) -> dict | None:
    try:
        async with httpx.AsyncClient(
            timeout=REQUEST_TIMEOUT_SECONDS,
            headers={"User-Agent": f"ForgeFiles-Desktop/{current_version}"},
            follow_redirects=True,
        ) as client:
            response = await client.get(url)
        if response.status_code != 200 or len(response.content) > MAX_FEED_BYTES:
            return None
        return response.json()
    except (httpx.HTTPError, ValueError):
        return None


async def get_response(current_version: str, data_dir: Path, force: bool = False) -> dict:
    """Fetch (at most every few hours), fall back to the last good copy, never raise."""
    if not feed_enabled(data_dir):
        return {"enabled": False, "current": current_version, "update": None, "announcement": None}
    now = time.time()
    if not force and _memory_cache["feed"] is not None and now - _memory_cache["at"] < CACHE_TTL_SECONDS:
        return build_response(_memory_cache["feed"], current_version)

    raw = await _download(os.environ.get("FF_FEED_URL") or DEFAULT_FEED_URL, current_version)
    if raw is not None:
        feed = sanitize_feed(raw)
        _memory_cache.update(at=now, feed=feed)
        try:
            (Path(data_dir) / "feed-cache.json").write_text(json.dumps(raw)[:MAX_FEED_BYTES], encoding="utf-8")
        except OSError:
            logger.debug("Could not write feed cache", exc_info=True)
        return build_response(feed, current_version)

    # Offline or the file is missing: use whatever worked last time.
    feed = _memory_cache["feed"] or _read_disk_cache(data_dir)
    if feed is not None:
        _memory_cache.update(at=now - CACHE_TTL_SECONDS + 600, feed=feed)  # retry in ~10 minutes
    return build_response(feed, current_version)
