"""Fetch and verify the on-device Office engine (LibreOffice WASM).

The two large files (soffice.wasm, soffice.data, about 247 MB) are not in git.
This script makes `public/static/vendor/lo-wasm/<version>/` complete:

  * a file already present with the manifest SHA-256 is left alone;
  * otherwise the pinned npm tarball is downloaded, its sha512 integrity is
    checked against the value in MANIFEST.json, the files are extracted, and
    each one is checked against its manifest SHA-256 before it is written.

Exit status is 0 only when every manifest file is present and matches. A
mismatch is never "fixed" by keeping the bad file. Run with --check to verify
without downloading anything.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import sys
import tarfile
import urllib.request
from pathlib import Path

ENGINE_ROOT = Path(__file__).resolve().parent.parent / "static" / "vendor" / "lo-wasm"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_manifest(version_dir: Path) -> dict:
    return json.loads((version_dir / "MANIFEST.json").read_text(encoding="utf-8"))


def verify(version_dir: Path, manifest: dict) -> list[str]:
    """Return a list of problems; empty means every file is present and exact."""
    problems = []
    for name, meta in manifest["files"].items():
        path = version_dir / name
        if not path.is_file():
            problems.append(f"{name}: missing")
        elif path.stat().st_size != meta["bytes"]:
            problems.append(f"{name}: size {path.stat().st_size} != {meta['bytes']}")
        elif sha256_file(path) != meta["sha256"]:
            problems.append(f"{name}: sha256 mismatch")
    return problems


def download_tarball(url: str, integrity: str) -> bytes:
    algo, _, expected_b64 = integrity.partition("-")
    if algo != "sha512":
        raise SystemExit(f"unsupported integrity algorithm: {algo}")
    with urllib.request.urlopen(url, timeout=300) as response:  # noqa: S310 - pinned https registry URL
        data = response.read()
    actual = base64.b64encode(hashlib.sha512(data).digest()).decode("ascii")
    if actual != expected_b64:
        raise SystemExit("npm tarball integrity mismatch; refusing to extract")
    return data


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--version", default=None, help="engine version directory (default: the only one present)")
    parser.add_argument("--check", action="store_true", help="verify only; do not download")
    args = parser.parse_args(argv)

    versions = sorted(p.name for p in ENGINE_ROOT.iterdir() if p.is_dir())
    version = args.version or (versions[-1] if versions else None)
    if not version:
        print("no engine version directory found", file=sys.stderr)
        return 2
    version_dir = ENGINE_ROOT / version
    manifest = load_manifest(version_dir)

    problems = verify(version_dir, manifest)
    if not problems:
        print(f"on-device Office engine {version}: all {len(manifest['files'])} files verified")
        return 0
    if args.check:
        print("\n".join(problems), file=sys.stderr)
        return 1

    print(f"fetching {manifest['package']}@{manifest['version']} ({len(problems)} file(s) to restore)")
    tarball = download_tarball(manifest["tarball"], manifest["npm_integrity"])
    with tarfile.open(fileobj=io.BytesIO(tarball), mode="r:gz") as archive:
        for name, meta in manifest["files"].items():
            if (version_dir / name).is_file() and not any(p.startswith(name + ":") for p in problems):
                continue
            member = archive.extractfile("package/" + meta["source"])
            if member is None:
                print(f"{name}: not in tarball", file=sys.stderr)
                return 1
            body = member.read()
            if hashlib.sha256(body).hexdigest() != meta["sha256"]:
                print(f"{name}: sha256 mismatch against the manifest; not written", file=sys.stderr)
                return 1
            (version_dir / name).write_bytes(body)

    problems = verify(version_dir, manifest)
    if problems:
        print("\n".join(problems), file=sys.stderr)
        return 1
    print(f"on-device Office engine {version}: restored and verified")
    return 0


if __name__ == "__main__":
    sys.exit(main())
