"""Optional on-device Office engine (LibreOffice WASM) for the Windows desktop app.

The two big engine files (about 247 MB) are not in the installer; that keeps the download small. After
the person agrees once, the local server fetches them in the background from the website, checks every
byte against the SHA-256 values in the bundled ``MANIFEST.json`` and keeps them in the app's data
folder (``<data>/engines/lo-wasm/<version>/``). ``main.py`` serves them from there at the same URL the
website uses, so the Office tools work unchanged.

What it sends: a plain GET per file, user agent ``ForgeFiles-Desktop/<version>``, nothing else: no
cookies, no identifiers, never a file name or contents. Nothing is requested until the person says yes,
and ``FF_ENGINE_DOWNLOAD=0`` switches it off entirely.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
from pathlib import Path

import httpx

logger = logging.getLogger("file_forge.desktop_engine")

DEFAULT_BASE = "https://www.forgefiles.org/static/vendor/lo-wasm"
REQUEST_TIMEOUT_SECONDS = 30.0
CHUNK_BYTES = 1 << 20

_lock = threading.Lock()
_progress: dict = {"state": "idle", "done": 0, "error": ""}


def _disabled() -> bool:
    return os.environ.get("FF_ENGINE_DOWNLOAD", "1").strip().lower() in ("0", "false", "no", "off")


def read_manifest(static_dir: Path) -> tuple[str, dict] | None:
    """(version, manifest) of the newest bundled engine manifest, or None."""
    root = Path(static_dir) / "vendor" / "lo-wasm"
    try:
        versions = sorted(p.name for p in root.iterdir() if (p / "MANIFEST.json").is_file())
        if not versions:
            return None
        return versions[-1], json.loads((root / versions[-1] / "MANIFEST.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def large_files(manifest: dict) -> dict:
    """The files the installer leaves out: everything the manifest marks as not committed."""
    return {name: meta for name, meta in manifest.get("files", {}).items() if not meta.get("committed", True)}


def engine_dir(data_dir: Path, version: str) -> Path:
    return Path(data_dir) / "engines" / "lo-wasm" / version


def served_path(data_dir: Path, static_dir: Path, relative: str) -> Path | None:
    """Where a large engine file lives on disk, for ``vendor/lo-wasm/<version>/<name>``; None if not ready."""
    parts = relative.replace("\\", "/").split("/")
    if len(parts) != 4 or parts[:2] != ["vendor", "lo-wasm"]:
        return None
    version, name = parts[2], parts[3]
    found = read_manifest(static_dir)
    if not found or found[0] != version or name not in large_files(found[1]):
        return None
    path = engine_dir(data_dir, version) / name
    try:
        return path if path.is_file() and path.stat().st_size == found[1]["files"][name]["bytes"] else None
    except OSError:
        return None


def _choice(data_dir: Path) -> str | None:
    try:
        value = json.loads((Path(data_dir) / "settings.json").read_text(encoding="utf-8")).get("engine")
        return value if value in ("accepted", "declined") else None
    except (OSError, ValueError, AttributeError):
        return None


def set_choice(data_dir: Path, value: str) -> None:
    path = Path(data_dir) / "settings.json"
    try:
        settings = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(settings, dict):
            settings = {}
    except (OSError, ValueError):
        settings = {}
    settings["engine"] = value
    path.write_text(json.dumps(settings), encoding="utf-8")


def _complete(data_dir: Path, version: str, files: dict) -> bool:
    folder = engine_dir(data_dir, version)
    try:
        return bool(files) and all((folder / n).is_file() and (folder / n).stat().st_size == m["bytes"] for n, m in files.items())
    except OSError:
        return False


def status(data_dir: Path, static_dir: Path) -> dict:
    """What the page needs: whether the engine is there, how far a download is, and what the person chose."""
    found = read_manifest(static_dir)
    files = large_files(found[1]) if found else {}
    total = sum(m["bytes"] for m in files.values())
    if not files:
        return {"state": "bundled", "total": 0, "done": 0, "choice": None, "error": ""}
    if _complete(data_dir, found[0], files):
        state = "ready"
    elif _disabled():
        state = "disabled"
    elif _progress["state"] == "downloading":
        state = "downloading"
    elif _progress["state"] == "error":
        state = "error"
    else:
        state = "missing"
    done = total if state == "ready" else min(_progress["done"], total)
    return {"state": state, "total": total, "done": done, "choice": _choice(data_dir), "error": _progress["error"] if state == "error" else ""}


def _stream(url: str, user_agent: str):
    """Yield the body of ``url`` in chunks; a test replaces this."""
    with httpx.stream("GET", url, headers={"User-Agent": user_agent}, timeout=REQUEST_TIMEOUT_SECONDS, follow_redirects=True) as response:
        if response.status_code != 200:
            raise OSError(f"HTTP {response.status_code}")
        yield from response.iter_bytes(CHUNK_BYTES)


def _fetch_all(data_dir: Path, version: str, files: dict, base: str, app_version: str) -> None:
    folder = engine_dir(data_dir, version)
    folder.mkdir(parents=True, exist_ok=True)
    base_done = sum(m["bytes"] for n, m in files.items() if (folder / n).is_file() and (folder / n).stat().st_size == m["bytes"])
    _progress.update(done=base_done)
    for name, meta in files.items():
        target = folder / name
        if target.is_file() and target.stat().st_size == meta["bytes"]:
            continue
        part = folder / (name + ".part")
        digest, size = hashlib.sha256(), 0
        try:
            with part.open("wb") as handle:
                for chunk in _stream(f"{base.rstrip('/')}/{version}/{name}", f"ForgeFiles-Desktop/{app_version}"):
                    size += len(chunk)
                    if size > meta["bytes"]:
                        raise OSError("larger than expected")
                    digest.update(chunk)
                    handle.write(chunk)
                    _progress["done"] = base_done + size
            if size != meta["bytes"] or digest.hexdigest() != meta["sha256"]:
                raise OSError(f"{name} did not match its checksum")
            os.replace(part, target)
        finally:
            part.unlink(missing_ok=True)
        base_done += meta["bytes"]
        _progress["done"] = base_done


def _run(data_dir: Path, version: str, files: dict, base: str, app_version: str) -> None:
    try:
        _fetch_all(data_dir, version, files, base, app_version)
        _progress.update(state="idle", error="")
    except (OSError, httpx.HTTPError) as exc:
        logger.warning("Office engine download failed: %s", exc)
        _progress.update(state="error", error="Could not download the engine. Check your connection and try again.")


def start(data_dir: Path, static_dir: Path, app_version: str, base: str | None = None, wait: bool = False) -> dict:
    """Record the person's yes and begin downloading in the background (once). Returns the status."""
    found = read_manifest(static_dir)
    files = large_files(found[1]) if found else {}
    if files and not _disabled():
        set_choice(data_dir, "accepted")
        with _lock:
            if _progress["state"] != "downloading" and not _complete(data_dir, found[0], files):
                _progress.update(state="downloading", done=0, error="")
                args = (Path(data_dir), found[0], files, base or os.environ.get("FF_ENGINE_BASE") or DEFAULT_BASE, app_version)
                worker = threading.Thread(target=_run, args=args, name="ff-engine-download", daemon=True)
                worker.start()
                if wait:
                    worker.join()
    return status(data_dir, static_dir)
