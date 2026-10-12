"""The Windows app download page (open source, mirrored with the rest of public/)."""
from fastapi.testclient import TestClient

import main

client = TestClient(main.app)


def test_download_page_explains_the_unsigned_installer():
    resp = client.get("/download")
    assert resp.status_code == 200
    assert "Run anyway" in resp.text
    assert "SHA-256" in resp.text
    assert "{{" not in resp.text


def test_download_page_is_in_the_sitemap():
    assert "/download</loc>" in client.get("/sitemap.xml").text


PAGES_WITH_HEADER = ["/", "/about", "/faq", "/contact", "/privacy", "/terms", "/merge-pdf", "/compress-pdf", "/blog"]


class TestHeaderButton:
    """The "Download for Windows" header button on the website."""

    def test_every_website_header_has_the_button_and_no_raw_placeholder(self):
        for path in PAGES_WITH_HEADER:
            body = client.get(path).text
            assert 'href="/download"' in body, path
            assert "Download for Windows" in body, path
            assert "{{DOWNLOAD_CTA}}" not in body, path

    def test_it_only_shows_for_windows_desktop_visitors(self):
        body = client.get("/").text
        assert "hidden>" in body  # hidden until the script reveals it
        assert "/Windows/.test(u)" in body and "Mobi|Android|Phone" in body
        assert "@media (min-width:900px)" in body

    def test_the_download_page_itself_has_no_button_or_placeholder(self):
        body = client.get("/download").text
        assert "Download for Windows" not in body and "{{DOWNLOAD_CTA}}" not in body

    def test_the_desktop_app_never_shows_it(self, monkeypatch):
        monkeypatch.setattr(main, "LOCAL_ONLY", True)
        monkeypatch.setattr(main, "BASE_URL", "http://testserver")
        main._render_page.cache_clear()
        main._render_tool_page.cache_clear()
        try:
            for path in ("/", "/about", "/merge-pdf"):
                body = client.get(path).text
                assert "Download for Windows" not in body, path
                assert "{{DOWNLOAD_CTA}}" not in body, path
        finally:
            main._render_page.cache_clear()
            main._render_tool_page.cache_clear()



class TestDirectInstallerDownload:
    """/download/windows sends the browser straight to the installer file."""

    def setup_method(self):
        main._installer_cache.update(at=0.0, url=None)

    def test_the_page_button_points_at_the_direct_route(self):
        body = client.get("/download").text
        assert 'href="/download/windows"' in body
        assert "releases/latest\">Download" not in body

    def test_redirects_to_the_installer_asset(self, monkeypatch):
        url = "https://github.com/o/r/releases/download/v1.0.0/ForgeFiles-Setup-1.0.0.exe"

        async def fake():
            return url

        monkeypatch.setattr(main, "_latest_installer_url", fake)
        resp = client.get("/download/windows", follow_redirects=False)
        assert resp.status_code == 302
        assert resp.headers["location"] == url

    def test_falls_back_to_the_releases_page_when_lookup_fails(self, monkeypatch):
        async def fake():
            return None

        monkeypatch.setattr(main, "_latest_installer_url", fake)
        resp = client.get("/download/windows", follow_redirects=False)
        assert resp.status_code == 302
        assert resp.headers["location"].endswith("/releases/latest")

    def test_a_configured_installer_url_wins_over_the_github_lookup(self, monkeypatch):
        url = "https://downloads.example.org/ForgeFiles-Setup-latest.exe"
        monkeypatch.setattr(main, "WINDOWS_INSTALLER_URL", url)
        resp = client.get("/download/windows", follow_redirects=False)
        assert resp.headers["location"] == url
