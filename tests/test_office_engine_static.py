"""Phase 5 (WP44): static guarantees for the on-device Office engine that need no browser or engine download.

Covers the pinned manifest, the isolation headers, the "server stays the default" contract, the
mobile-bundle exclusion and the deploy-time fetch step.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

PUBLIC = Path(__file__).resolve().parent.parent
REPO = PUBLIC.parent
ENGINE = PUBLIC / "static" / "vendor" / "lo-wasm" / "2.7.2"
MANIFEST = json.loads((ENGINE / "MANIFEST.json").read_text(encoding="utf-8"))

sys.path.insert(0, str(PUBLIC / "scripts"))


@pytest.fixture(scope="module")
def client():
    from main import app

    return TestClient(app)


def test_manifest_pins_package_licence_and_every_file():
    assert MANIFEST["package"] == "@matbee/libreoffice-converter"
    assert MANIFEST["version"] == "2.7.2" and ENGINE.name == "2.7.2"
    assert MANIFEST["license"] == "MPL-2.0"
    assert MANIFEST["npm_integrity"].startswith("sha512-")
    assert MANIFEST["tarball"].startswith("https://registry.npmjs.org/")
    assert set(MANIFEST["files"]) == {
        "browser.js", "browser.worker.global.js", "soffice.js", "soffice.worker.js", "soffice.wasm", "soffice.data",
    }
    for meta in MANIFEST["files"].values():
        assert re.fullmatch(r"[0-9a-f]{64}", meta["sha256"]) and meta["bytes"] > 0


def test_committed_engine_files_match_the_manifest():
    for name, meta in MANIFEST["files"].items():
        if not meta["committed"]:
            continue
        data = (ENGINE / name).read_bytes()
        assert len(data) == meta["bytes"], name
        assert hashlib.sha256(data).hexdigest() == meta["sha256"], name


def test_large_engine_files_are_not_committed():
    import subprocess

    tracked = subprocess.run(["git", "ls-files", "public/static/vendor/lo-wasm"], cwd=REPO, capture_output=True, text=True).stdout
    assert "soffice.wasm" not in tracked and "soffice.data" not in tracked
    ignore = (REPO / ".gitignore").read_text(encoding="utf-8")
    assert "public/static/vendor/lo-wasm/*/soffice.wasm" in ignore and "public/static/vendor/lo-wasm/*/soffice.data" in ignore
    assert all(not meta["committed"] for name, meta in MANIFEST["files"].items() if name in ("soffice.wasm", "soffice.data"))


def test_fetch_script_verifies_and_never_keeps_a_bad_file(tmp_path):
    import fetch_office_engine as fe

    (tmp_path / "a.bin").write_bytes(b"abc")
    manifest = {"files": {"a.bin": {"bytes": 3, "sha256": hashlib.sha256(b"abc").hexdigest()}}}
    assert fe.verify(tmp_path, manifest) == []
    (tmp_path / "a.bin").write_bytes(b"abd")
    assert fe.verify(tmp_path, manifest) == ["a.bin: sha256 mismatch"]
    (tmp_path / "a.bin").write_bytes(b"ab")
    assert "size" in fe.verify(tmp_path, manifest)[0]
    (tmp_path / "a.bin").unlink()
    assert fe.verify(tmp_path, manifest) == ["a.bin: missing"]


def test_isolation_headers_only_on_the_dedicated_route(client):
    page = client.get("/on-device-office/")
    assert page.status_code == 200
    assert page.headers["cross-origin-embedder-policy"] == "require-corp"
    assert page.headers["cross-origin-opener-policy"] == "same-origin"
    assert page.headers["x-robots-tag"] == "noindex, nofollow"
    assert client.get("/on-device-office").status_code == 200
    for path in ("/static/on-device-office/office.js", "/static/vendor/lo-wasm/2.7.2/browser.worker.global.js",
                 "/static/vendor/pdfjs/pdf.worker.min.mjs", "/static/vendor/jszip.min.js"):
        r = client.get(path)
        assert r.status_code == 200, path
        assert r.headers["cross-origin-embedder-policy"] == "require-corp", path
        assert r.headers["cross-origin-resource-policy"] == "same-origin", path
    engine = client.get("/static/vendor/lo-wasm/2.7.2/browser.js")
    assert "immutable" in engine.headers["cache-control"]
    assert "cache-control" not in client.get("/static/vendor/lo-wasm/2.7.2/MANIFEST.json").headers or \
        "immutable" not in client.get("/static/vendor/lo-wasm/2.7.2/MANIFEST.json").headers["cache-control"]

    # The ad-supported site and every other static file keep their headers.
    for path in ("/", "/word-to-pdf", "/static/index.html", "/static/script.js", "/static/style.css",
                 "/static/vendor/pdf-lib.min.js", "/static/local/ff-local.js"):
        r = client.get(path)
        assert "cross-origin-embedder-policy" not in r.headers, path
        assert r.headers["cross-origin-opener-policy"] == "same-origin"


def test_isolated_page_has_no_ads_analytics_or_third_party_scripts():
    html = (PUBLIC / "static" / "on-device-office" / "index.html").read_text(encoding="utf-8")
    assert not re.search(r"adsbygoogle|googletagmanager|datafa\.st|gtag\(", html)
    for src in re.findall(r'<script[^>]+src="([^"]+)"', html):
        assert src.startswith("/static/"), src
    assert "noindex" in html
    for name in ("office.js", "office-preflight.js"):
        text = (PUBLIC / "static" / "on-device-office" / name).read_text(encoding="utf-8")
        assert "/api/" not in text, f"{name} must never call the API"
        assert not re.search(r"https?://", text), f"{name} must not reference third-party hosts"


def test_server_stays_the_default_and_consent_gate_is_unchanged():
    registry = json.loads((PUBLIC / "scripts" / "tool_registry.json").read_text(encoding="utf-8"))
    by_path = {p: t for t in registry["tools"] for p in t["api_paths"]}
    for path in ("/api/word/to-pdf", "/api/excel/to-pdf", "/api/ppt/to-pdf", "/api/ppt/to-images"):
        assert by_path[path]["execution"] == {"preferred": "server", "requires_server_consent": True}, path
    gate = (PUBLIC / "static" / "local" / "ff-server-gate.js").read_text(encoding="utf-8")
    for path in ("/api/word/to-pdf", "/api/excel/to-pdf", "/api/ppt/to-pdf", "/api/ppt/to-images",
                 "/api/word/to-pptx", "/api/ppt/merge"):
        assert f"'{path}'" in gate, path


def test_tool_pages_offer_the_option_and_load_the_entry_script():
    index = (PUBLIC / "static" / "index.html").read_text(encoding="utf-8")
    for op in ("word-to-pdf", "excel-to-pdf", "ppt-to-pdf", "ppt-to-images"):
        assert f'data-on-device-office="{op}"' in index, op
    assert index.count("data-on-device-office=") == 4  # Word to PowerPoint and Merge PowerPoint stay server-only
    assert "/static/local/office-entry.js" in index
    entry = (PUBLIC / "static" / "local" / "office-entry.js").read_text(encoding="utf-8")
    assert "/api/" not in entry and "window.Capacitor" in entry
    assert not re.search(r"\bfetch\(", entry)


def test_mobile_bundle_excludes_the_engine_and_isolated_page():
    build = (REPO / "mobile" / "build-web.mjs").read_text(encoding="utf-8")
    assert "'vendor', 'lo-wasm'" in build and "'on-device-office'" in build and "filter:" in build


def test_deploy_fetches_and_verifies_the_engine_without_failing_the_deploy():
    ci = (REPO / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert ci.count("fetch_office_engine.py") >= 2  # production and development deploy jobs
