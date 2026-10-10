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
    """The "Get the Windows app" header button on the website."""

    def test_every_website_header_has_the_button_and_no_raw_placeholder(self):
        for path in PAGES_WITH_HEADER:
            body = client.get(path).text
            assert 'href="/download"' in body, path
            assert "Get the Windows app" in body, path
            assert "{{DOWNLOAD_CTA}}" not in body, path

    def test_it_only_shows_for_windows_desktop_visitors(self):
        body = client.get("/").text
        assert "hidden>Get the Windows app" in body  # hidden until the script reveals it
        assert "/Windows/.test(u)" in body and "Mobi|Android|Phone" in body
        assert "@media (min-width:900px)" in body

    def test_the_download_page_itself_has_no_button_or_placeholder(self):
        body = client.get("/download").text
        assert "Get the Windows app" not in body and "{{DOWNLOAD_CTA}}" not in body

    def test_the_desktop_app_never_shows_it(self, monkeypatch):
        monkeypatch.setattr(main, "LOCAL_ONLY", True)
        monkeypatch.setattr(main, "BASE_URL", "http://testserver")
        main._render_page.cache_clear()
        main._render_tool_page.cache_clear()
        try:
            for path in ("/", "/about", "/merge-pdf"):
                body = client.get(path).text
                assert "Get the Windows app" not in body, path
                assert "{{DOWNLOAD_CTA}}" not in body, path
        finally:
            main._render_page.cache_clear()
            main._render_tool_page.cache_clear()

