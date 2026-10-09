"""Local-only mode (FF_LOCAL_ONLY=1): the desktop app must never call out."""
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import main

STATIC = Path(main.BASE_DIR) / "static"
# What the browser fetches by itself: src= on any tag, and href= on <link> (stylesheets,
# preconnect, preload). Plain <a href> links do nothing until a person clicks them.
AUTO_LOADED = re.compile(
    r"""(?:<[a-z]+\b[^>]*\bsrc|<link\b[^>]*\bhref)=["']https?://(?!www\.forgefiles\.org)[^"']+["']""", re.I
)


def test_localize_strips_third_party_scripts_only():
    html = (
        '<head><script defer data-x="1" src="https://datafa.st/js/script.js">\n</script>'
        '<script src="/static/script.js"></script><script>var a = 1;</script></head>'
    )
    out = main._localize(html)
    assert "datafa.st" not in out
    assert '/static/script.js' in out and "var a = 1;" in out


def test_font_awesome_and_cropper_are_served_from_this_app():
    index = (STATIC / "index.html").read_text(encoding="utf-8")
    script = (STATIC / "script.js").read_text(encoding="utf-8")
    assert "cdnjs.cloudflare.com" not in index
    assert "cdnjs.cloudflare.com" not in script
    for rel in (
        "vendor/fontawesome/css/all.min.css",
        "vendor/fontawesome/webfonts/fa-solid-900.woff2",
        "vendor/cropperjs/cropper.min.js",
        "vendor/cropperjs/cropper.min.css",
    ):
        assert (STATIC / rel).is_file(), rel


@pytest.fixture
def local_client(monkeypatch):
    monkeypatch.setattr(main, "LOCAL_ONLY", True)
    main._render_page.cache_clear()
    main._render_tool_page.cache_clear()
    yield TestClient(main.app)
    main._render_page.cache_clear()
    main._render_tool_page.cache_clear()


@pytest.mark.parametrize("path", ["/", "/about", "/privacy", "/merge-pdf", "/compress-pdf"])
def test_local_pages_reference_no_third_party_assets(local_client, path):
    resp = local_client.get(path)
    assert resp.status_code == 200
    assert AUTO_LOADED.findall(resp.text) == []
    assert 'src="https://datafa.st' not in resp.text


def test_local_only_sends_a_csp_that_blocks_outside_connections(local_client):
    csp = local_client.get("/").headers["content-security-policy"]
    assert "connect-src 'self'" in csp
    assert "default-src 'self'" in csp


def test_no_stylesheet_or_script_imports_a_font_cdn():
    """Fonts are self-hosted (static/vendor/fonts); a CDN import would break the
    desktop app's no-third-party guarantee and its offline use."""
    offenders = [
        str(path.relative_to(STATIC))
        for path in STATIC.rglob("*")
        if path.suffix in {".css", ".js", ".html"}
        and "vendor" not in path.parts
        and "examples" not in path.parts
        and ("fonts.googleapis.com" in path.read_text(encoding="utf-8", errors="ignore")
             or "fonts.gstatic.com" in path.read_text(encoding="utf-8", errors="ignore"))
    ]
    assert offenders == []

