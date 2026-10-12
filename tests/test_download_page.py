"""The Windows app download page (open source, mirrored with the rest of public/)."""
import asyncio
import hashlib

import httpx
import pytest
from fastapi.testclient import TestClient

import main
from scripts import installer_mirror

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
    """/download/windows serves the installer without a GitHub page in between."""

    GH = "https://github.com/o/r/releases/download/v1.0.0/ForgeFiles-Setup-1.0.0.exe"

    @pytest.fixture(autouse=True)
    def isolated(self, monkeypatch, tmp_path):
        monkeypatch.setattr(main, "INSTALLER_DIR", tmp_path)
        monkeypatch.setattr(main, "WINDOWS_INSTALLER_URL", "")
        monkeypatch.setattr(main, "WINDOWS_INSTALLER_MIRROR", True)
        monkeypatch.setitem(main._installer_link, "url", self.GH)
        monkeypatch.setitem(main._installer_link, "at", float("inf"))

        async def no_refresh(*a, **k):  # never touch the network from a route test
            return None

        monkeypatch.setattr(main.installer_mirror, "refresh", no_refresh)

    def test_the_page_button_points_at_the_direct_route(self):
        body = client.get("/download").text
        assert 'href="/download/windows"' in body
        assert "releases/latest\">Download" not in body

    def test_serves_the_local_copy_as_an_attachment(self, tmp_path):
        (tmp_path / "ForgeFiles-Setup-1.0.0.exe").write_bytes(b"MZ-installer")
        resp = client.get("/download/windows", follow_redirects=False)
        assert resp.status_code == 200
        assert resp.content == b"MZ-installer"
        assert "ForgeFiles-Setup-1.0.0.exe" in resp.headers["content-disposition"]

    def test_redirects_to_the_release_file_until_the_local_copy_exists(self):
        resp = client.get("/download/windows", follow_redirects=False)
        assert resp.status_code == 302
        assert resp.headers["location"] == self.GH

    def test_falls_back_to_the_releases_page_when_nothing_is_known(self, monkeypatch):
        monkeypatch.setitem(main._installer_link, "url", None)

        async def offline():
            return None

        monkeypatch.setattr(main, "_release_installer_url", offline)
        resp = client.get("/download/windows", follow_redirects=False)
        assert resp.status_code == 302
        assert resp.headers["location"].endswith("/releases/latest")

    def test_mirror_can_be_switched_off(self, monkeypatch, tmp_path):
        (tmp_path / "ForgeFiles-Setup-1.0.0.exe").write_bytes(b"x")
        monkeypatch.setattr(main, "WINDOWS_INSTALLER_MIRROR", False)
        assert client.get("/download/windows", follow_redirects=False).status_code == 302

    def test_a_configured_installer_url_wins(self, monkeypatch, tmp_path):
        url = "https://downloads.example.org/ForgeFiles-Setup-latest.exe"
        (tmp_path / "ForgeFiles-Setup-1.0.0.exe").write_bytes(b"x")
        monkeypatch.setattr(main, "WINDOWS_INSTALLER_URL", url)
        resp = client.get("/download/windows", follow_redirects=False)
        assert resp.headers["location"] == url


class TestInstallerMirror:
    """scripts/installer_mirror.py, against a fake GitHub."""

    BODY = b"MZ" + b"\x00" * 4096

    def _github(self, *, size=None, digest=None, body=None, name="ForgeFiles-Setup-2.0.0.exe"):
        body = self.BODY if body is None else body
        asset_url = f"https://github.com/o/r/releases/download/v2/{name}"
        asset = {"name": name, "browser_download_url": asset_url, "size": len(body) if size is None else size}
        if digest:
            asset["digest"] = digest

        def handler(request):
            if request.url.host == "api.github.com":
                return httpx.Response(200, json={"assets": [asset]})
            return httpx.Response(200, content=body)

        return httpx.MockTransport(handler)

    def _patch(self, monkeypatch, transport):
        real = httpx.AsyncClient
        monkeypatch.setattr(
            installer_mirror.httpx, "AsyncClient", lambda **kw: real(transport=transport, **kw)
        )
        monkeypatch.setattr(installer_mirror, "_last_check", 0.0)

    def test_downloads_the_newest_installer_and_drops_old_ones(self, monkeypatch, tmp_path):
        (tmp_path / "ForgeFiles-Setup-1.0.0.exe").write_bytes(b"old")
        self._patch(monkeypatch, self._github(digest="sha256:" + hashlib.sha256(self.BODY).hexdigest()))
        asyncio.run(installer_mirror.refresh("o/r", tmp_path, force=True))
        assert sorted(p.name for p in tmp_path.iterdir()) == [
            "ForgeFiles-Setup-2.0.0.exe",
            "ForgeFiles-Setup-2.0.0.exe.sha256",
        ]
        assert installer_mirror.cached_installer(tmp_path).read_bytes() == self.BODY

    def test_a_wrong_checksum_is_never_served(self, monkeypatch, tmp_path):
        self._patch(monkeypatch, self._github(digest="sha256:" + "0" * 64))
        asyncio.run(installer_mirror.refresh("o/r", tmp_path, force=True))
        assert list(tmp_path.iterdir()) == []

    def test_a_short_download_is_never_served(self, monkeypatch, tmp_path):
        self._patch(monkeypatch, self._github(size=len(self.BODY) + 10))
        asyncio.run(installer_mirror.refresh("o/r", tmp_path, force=True))
        assert list(tmp_path.iterdir()) == []

    def test_an_oversized_installer_is_refused(self, monkeypatch, tmp_path):
        monkeypatch.setattr(installer_mirror, "MAX_INSTALLER_BYTES", 100)
        self._patch(monkeypatch, self._github())
        asyncio.run(installer_mirror.refresh("o/r", tmp_path, force=True))
        assert list(tmp_path.iterdir()) == []

    def test_an_up_to_date_copy_is_not_downloaded_again(self, monkeypatch, tmp_path):
        (tmp_path / "ForgeFiles-Setup-2.0.0.exe").write_bytes(self.BODY)
        # same size, different bytes: a re-download would change the file
        self._patch(monkeypatch, self._github(size=len(self.BODY), body=b"different"))
        asyncio.run(installer_mirror.refresh("o/r", tmp_path, force=True))
        assert (tmp_path / "ForgeFiles-Setup-2.0.0.exe").read_bytes() == self.BODY

    def test_failures_never_raise(self, monkeypatch, tmp_path):
        def boom(request):
            raise httpx.ConnectError("offline")

        self._patch(monkeypatch, httpx.MockTransport(boom))
        asyncio.run(installer_mirror.refresh("o/r", tmp_path, force=True))
        assert installer_mirror.cached_installer(tmp_path) is None


class TestReviewFollowUps:
    def test_a_missing_release_is_cached_too(self, monkeypatch):
        calls = []

        async def none_yet(repo, http):
            calls.append(repo)
            return None

        monkeypatch.setattr(main.installer_mirror, "latest_asset", none_yet)
        monkeypatch.setitem(main._installer_link, "at", float("-inf"))
        monkeypatch.setitem(main._installer_link, "url", None)
        for _ in range(3):
            assert asyncio.run(main._release_installer_url()) is None
        assert len(calls) == 1  # not one GitHub request per click

    def test_the_page_still_links_to_the_checksum_on_the_releases_page(self):
        body = client.get("/download").text
        assert "releases/latest\">releases page</a>" in body

    def test_a_newer_deployed_installer_is_never_replaced_by_an_older_release(self, monkeypatch, tmp_path):
        (tmp_path / "ForgeFiles-Setup-3.0.0.exe").write_bytes(b"deployed-by-ci")
        TestInstallerMirror()._patch(monkeypatch, TestInstallerMirror()._github(name="ForgeFiles-Setup-2.0.0.exe"))
        asyncio.run(installer_mirror.refresh("o/r", tmp_path, force=True))
        assert [p.name for p in tmp_path.iterdir()] == ["ForgeFiles-Setup-3.0.0.exe"]

    def test_a_reuploaded_asset_with_the_same_name_and_size_is_fetched_again(self, monkeypatch, tmp_path):
        body = TestInstallerMirror.BODY
        exe = tmp_path / "ForgeFiles-Setup-2.0.0.exe"
        exe.write_bytes(b"" * len(body))  # same size, different bytes
        (tmp_path / "ForgeFiles-Setup-2.0.0.exe.sha256").write_text("0" * 64)
        digest = "sha256:" + hashlib.sha256(body).hexdigest()
        TestInstallerMirror()._patch(monkeypatch, TestInstallerMirror()._github(digest=digest))
        asyncio.run(installer_mirror.refresh("o/r", tmp_path, force=True))
        assert exe.read_bytes() == body
