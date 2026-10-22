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
    r"""(?:<[a-z]+\b[^>]*\bsrc|<link\b[^>]*\bhref)=["']https?://(?!testserver|www\.forgefiles\.org)[^"']+["']""", re.I
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
    monkeypatch.setattr(main, "BASE_URL", "http://testserver")
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


def test_the_only_outside_connection_is_the_send_and_receive_hub(local_client):
    """Send & Receive signals through the website; nothing else may be reached,
    and the hub is a WebSocket origin only (no https fetches)."""
    csp = local_client.get("/").headers["content-security-policy"]
    connect = next(d for d in csp.split(";") if d.strip().startswith("connect-src")).split()[1:]
    assert set(connect) == {"'self'", "blob:", "data:", "wss://www.forgefiles.org"}


def test_desktop_pages_point_send_and_receive_at_the_website(local_client):
    assert 'window.FF_TRANSFER_ORIGIN="https://www.forgefiles.org"' in local_client.get("/").text


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


class TestOnlyTheAppsOwnPageMayCallTheLocalServer:
    """Any website in the person's browser can fire requests at 127.0.0.1."""

    def test_the_apps_own_requests_pass(self, local_client):
        assert local_client.get("/").status_code == 200
        assert local_client.get("/", headers={"origin": "http://testserver"}).status_code == 200

    def test_a_cross_site_post_is_refused(self, local_client):
        resp = local_client.post("/api/desktop/feed/off", headers={"origin": "https://evil.example"})
        assert resp.status_code == 403

    def test_the_null_origin_is_refused(self, local_client):
        assert local_client.post("/api/desktop/feed/off", headers={"origin": "null"}).status_code == 403

    def test_a_rebinding_host_header_is_refused(self, local_client):
        assert local_client.get("/", headers={"host": "evil.example"}).status_code == 403

    def test_the_website_is_unaffected(self, monkeypatch):
        monkeypatch.setattr(main, "LOCAL_ONLY", False)
        resp = TestClient(main.app).get("/", headers={"origin": "https://elsewhere.example"})
        assert resp.status_code == 200



class TestDesktopModeGuardrailsAndConfiguration:
    def test_desktop_mode_injects_window_ff_desktop_and_version(self, local_client, monkeypatch):
        monkeypatch.setenv("FF_APP_VERSION", "1.2.3")
        main._render_page.cache_clear()
        try:
            resp = local_client.get("/")
            assert resp.status_code == 200
            assert 'window.FF_DESKTOP=true' in resp.text
            assert 'window.FF_LOCAL_ONLY=true' in resp.text
            assert 'window.FF_APP_VERSION="1.2.3"' in resp.text
            assert "desktop-shell.js" in resp.text
        finally:
            main._render_page.cache_clear()

    def test_website_mode_does_not_inject_ff_desktop(self, monkeypatch):
        monkeypatch.setattr(main, "LOCAL_ONLY", False)
        main._render_page.cache_clear()
        try:
            resp = TestClient(main.app).get("/")
            assert resp.status_code == 200
            assert "window.FF_DESKTOP=true" not in resp.text
            assert "desktop-shell.js" not in resp.text
        finally:
            main._render_page.cache_clear()

    def test_desktop_feed_force_and_on_endpoints(self, local_client, monkeypatch, tmp_path):
        monkeypatch.setattr(main, "_DATA_DIR", tmp_path)
        # Enable feed
        on_resp = local_client.post("/api/desktop/feed/on")
        assert on_resp.status_code == 200
        assert on_resp.json() == {"enabled": True}

        # Check with force=1
        async def mock_get_response(version, data_dir, force=False):
            return {"enabled": True, "current": version, "update": None, "announcement": None, "forced": force}

        monkeypatch.setattr(main.desktop_feed, "get_response", mock_get_response)
        feed_resp = local_client.get("/api/desktop/feed?force=1")
        assert feed_resp.status_code == 200
        assert feed_resp.json()["forced"] is True
