"""Keeps a copy of the newest Windows installer on this server's own disk.

The installer is a GitHub release asset. /download/windows serves the local copy
when there is one, so people download from forgefiles.org instead of github.com;
until the copy exists (or if anything here fails) the route falls back to the
release link, so a download never breaks because of this module.

Only the single newest installer is kept, and a newer one deployed directly to the
server (by CI) is never replaced by an older release. The download is written to a ``.part``
file, checked against the size (and SHA-256, when GitHub reports one) and then
renamed into place, so a half-finished or corrupt file is never served.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
import time
from pathlib import Path

import httpx

logger = logging.getLogger("file_forge.installer_mirror")

INSTALLER_RE = re.compile(r"^ForgeFiles-Setup-[\w.\-]+\.exe$")
CHECK_EVERY_SECONDS = 600
MAX_INSTALLER_BYTES = 1_500_000_000  # refuse anything larger than this
_GITHUB_PREFIX = "https://github.com/"

_lock = asyncio.Lock()
_last_check = 0.0


def cached_installer(directory: Path) -> Path | None:
    """The newest complete installer in ``directory``, if any."""
    try:
        files = [p for p in directory.glob("ForgeFiles-Setup-*.exe") if INSTALLER_RE.match(p.name)]
    except OSError:
        return None
    return max(files, key=lambda p: p.stat().st_mtime, default=None)


async def latest_asset(repo: str, http: httpx.AsyncClient) -> dict | None:
    resp = await http.get(
        f"https://api.github.com/repos/{repo}/releases/latest",
        headers={"Accept": "application/vnd.github+json", "User-Agent": "ForgeFiles-Website"},
    )
    resp.raise_for_status()
    for asset in resp.json().get("assets", []):
        url = asset.get("browser_download_url", "")
        if INSTALLER_RE.match(asset.get("name", "")) and url.startswith(_GITHUB_PREFIX):
            digest = asset.get("digest") or ""
            return {
                "name": asset["name"],
                "url": url,
                "size": int(asset.get("size") or 0),
                "sha256": digest[7:] if digest.startswith("sha256:") else None,
            }
    return None


def _sidecar(installer: Path) -> Path:
    return installer.with_name(installer.name + ".sha256")


def version_of(name: str) -> tuple:
    """'ForgeFiles-Setup-1.2.3.exe' -> (1, 2, 3); anything else -> ()."""
    match = re.fullmatch(r"ForgeFiles-Setup-(\d+(?:\.\d+)*)\.exe", name)
    return tuple(int(part) for part in match.group(1).split(".")) if match else ()


async def _download(asset: dict, directory: Path, http: httpx.AsyncClient) -> None:
    if not 0 < asset["size"] <= MAX_INSTALLER_BYTES:
        raise ValueError(f"unexpected installer size {asset['size']}")
    directory.mkdir(parents=True, exist_ok=True)
    final = directory / asset["name"]
    part = directory / (asset["name"] + ".part")
    digest = hashlib.sha256()
    written = 0
    try:
        async with http.stream("GET", asset["url"], follow_redirects=True) as resp:
            resp.raise_for_status()
            with part.open("wb") as out:
                async for chunk in resp.aiter_bytes(1 << 20):
                    written += len(chunk)
                    if written > asset["size"]:
                        raise ValueError("installer is larger than GitHub reported")
                    digest.update(chunk)
                    out.write(chunk)
        if written != asset["size"]:
            raise ValueError(f"incomplete download ({written} of {asset['size']} bytes)")
        if asset["sha256"] and digest.hexdigest() != asset["sha256"]:
            raise ValueError("checksum does not match the release")
        os.replace(part, final)
        # Remember what the file was verified against, so a re-uploaded asset with
        # the same name and size is noticed later without re-hashing 500 MB.
        _sidecar(final).write_text(digest.hexdigest(), encoding="ascii")
    finally:
        part.unlink(missing_ok=True)
    keep = {final.name, _sidecar(final).name}
    for old in [*directory.glob("ForgeFiles-Setup-*.exe"), *directory.glob("ForgeFiles-Setup-*.exe.sha256")]:
        if old.name not in keep:
            old.unlink(missing_ok=True)


async def refresh(repo: str, directory: Path, *, force: bool = False) -> None:
    """Fetch the newest installer if the local copy is missing or out of date.

    Safe to call on every request: it checks GitHub at most every
    ``CHECK_EVERY_SECONDS``, runs one download at a time, and never raises.
    """
    global _last_check
    if _lock.locked() or (not force and time.monotonic() - _last_check < CHECK_EVERY_SECONDS):
        return
    async with _lock:
        _last_check = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, read=120.0)) as http:
                asset = await latest_asset(repo, http)
                if not asset:
                    return
                local = cached_installer(directory)
                if local and version_of(local.name) > version_of(asset["name"]):
                    return  # a newer build was deployed directly; never downgrade it
                have = directory / asset["name"]
                if have.is_file() and have.stat().st_size == asset["size"]:
                    sidecar = _sidecar(have)
                    verified = sidecar.read_text(encoding="ascii").strip() if sidecar.is_file() else None
                    if not asset["sha256"] or verified is None or verified == asset["sha256"]:
                        return  # up to date (or nothing to compare against)
                await _download(asset, directory, http)
                logger.info("Mirrored Windows installer %s", asset["name"])
        except Exception:
            logger.warning("Could not mirror the Windows installer", exc_info=True)
