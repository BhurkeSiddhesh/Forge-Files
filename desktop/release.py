"""Prepare a desktop release: one command keeps the installer version and the update feed in step.

    python desktop/release.py 0.2.0 --notes "Faster PDF to Word."

Run from ``public/``. It sets ``desktop/VERSION`` and ``static/desktop/feed.json``
(``latest_version``, ``notes``) to the same version. Commit the result, take it through
development and main like any other change, then tag that commit ``v0.2.0``: CI builds the
installer from the tag and refuses to build if the tag and the feed disagree. The apps already
installed see the bell appear once the website serves the new feed.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

PUBLIC_ROOT = Path(__file__).resolve().parent.parent
VERSION_FILE = PUBLIC_ROOT / "desktop" / "VERSION"
FEED_FILE = PUBLIC_ROOT / "static" / "desktop" / "feed.json"
_SEMVER = re.compile(r"\d+(\.\d+){1,3}")


def _parts(version: str) -> tuple[int, ...]:
    return tuple(int(p) for p in version.split("."))


def prepare(version: str, notes: str | None = None, root: Path = PUBLIC_ROOT) -> str:
    """Write ``version`` to VERSION and the feed. Returns the version; raises ValueError if unusable."""
    version = version.strip().lstrip("vV")
    if not _SEMVER.fullmatch(version):
        raise ValueError(f"{version!r} is not a version like 1.2.3")
    version_file = root / "desktop" / "VERSION"
    feed_file = root / "static" / "desktop" / "feed.json"
    current = version_file.read_text(encoding="utf-8").strip() if version_file.exists() else "0.0.0"
    if _SEMVER.fullmatch(current) and _parts(version) <= _parts(current):
        raise ValueError(f"{version} is not newer than the current version {current}")
    feed = json.loads(feed_file.read_text(encoding="utf-8")) if feed_file.exists() else {"schema": 1}
    feed["latest_version"] = version
    if notes is not None:
        feed["notes"] = notes
    version_file.write_text(version + "\n", encoding="utf-8")
    feed_file.write_text(json.dumps(feed, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return version


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("version", help="new version, e.g. 0.2.0 (a leading v is ignored)")
    parser.add_argument("--notes", help="one sentence shown in the update popover (max 300 characters)")
    args = parser.parse_args(argv)
    try:
        version = prepare(args.version, args.notes)
    except (ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(
        f"Set desktop/VERSION and static/desktop/feed.json to {version}.\n"
        f"Next: commit, promote through development and main, then `git tag v{version} <that commit> && git push origin v{version}`."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
