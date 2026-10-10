"""Update check and announcements for the desktop app (scripts/desktop_feed.py)."""
import json
from datetime import date

import pytest
from fastapi.testclient import TestClient

import main
from scripts import desktop_feed as feed

TODAY = date(2026, 10, 9)


def make(**overrides):
    raw = {
        "latest_version": "0.2.0",
        "download_url": "https://www.forgefiles.org/download",
        "notes": "Faster.",
        "announcements": [{"id": "launch-1", "title": "New tool", "text": "Try it", "url": "https://example.com/x"}],
    }
    raw.update(overrides)
    return feed.sanitize_feed(raw)


class TestVersions:
    def test_compare_numerically_not_as_text(self):
        assert feed.parse_version("0.10.0") > feed.parse_version("0.9.0")

    @pytest.mark.parametrize("bad", [None, "", "abc", "1..2", "1.2.3.4.5", 5])
    def test_bad_versions_are_ignored(self, bad):
        assert feed.parse_version(bad) is None


class TestUpdate:
    def test_newer_version_is_offered(self):
        update = feed.build_response(make(), "0.1.0", TODAY)["update"]
        assert update["latest"] == "0.2.0" and update["url"].startswith("https://")

    def test_same_or_older_is_not_offered(self):
        assert feed.build_response(make(), "0.2.0", TODAY)["update"] is None
        assert feed.build_response(make(), "0.3.0", TODAY)["update"] is None

    def test_no_update_without_a_download_link(self):
        assert feed.build_response(make(download_url="http://insecure.example"), "0.1.0", TODAY)["update"] is None


class TestAnnouncements:
    def test_first_valid_announcement_is_shown(self):
        assert feed.build_response(make(), "0.1.0", TODAY)["announcement"]["id"] == "launch-1"

    def test_expired_and_not_yet_started_are_skipped(self):
        old = {"id": "old", "title": "Old", "expires": "2026-01-01"}
        future = {"id": "soon", "title": "Soon", "starts": "2027-01-01"}
        live = {"id": "live", "title": "Live"}
        got = feed.build_response(make(announcements=[old, future, live]), "0.1.0", TODAY)
        assert got["announcement"]["id"] == "live"

    def test_html_is_stripped_and_bad_links_dropped(self):
        item = {"id": "x", "title": "<b>Hi</b><script>alert(1)</script>", "url": "javascript:alert(1)"}
        got = feed.build_response(make(announcements=[item]), "0.1.0", TODAY)["announcement"]
        assert "<" not in got["title"] and got["url"] is None

    @pytest.mark.parametrize("url", ["http://x.com", "https://user:pw@x.com", "ftp://x.com", "//x.com"])
    def test_only_plain_https_links_pass(self, url):
        assert feed._https_url(url) is None

    def test_garbage_feed_is_harmless(self):
        assert feed.build_response(feed.sanitize_feed("nonsense"), "0.1.0", TODAY)["announcement"] is None
        assert feed.build_response(feed.sanitize_feed({"announcements": "x"}), "0.1.0", TODAY)["update"] is None
        for bad in (5, {"a": 1}, None, True):
            assert feed.sanitize_feed({"announcements": bad})["announcements"] == []


class TestFetching:
    @pytest.fixture(autouse=True)
    def _fresh(self, tmp_path, monkeypatch):
        feed._memory_cache.update(at=0.0, feed=None)
        monkeypatch.delenv("FF_FEED", raising=False)
        self.dir = tmp_path

    @pytest.mark.anyio
    async def test_fetch_then_fall_back_to_the_last_good_copy_offline(self, monkeypatch):
        raw = {"latest_version": "0.5.0", "download_url": "https://www.forgefiles.org/download"}

        async def online(url, version):
            return raw

        monkeypatch.setattr(feed, "_download", online)
        got = await feed.get_response("0.1.0", self.dir)
        assert got["update"]["latest"] == "0.5.0"
        assert json.loads((self.dir / "feed-cache.json").read_text())["latest_version"] == "0.5.0"

        async def offline(url, version):
            return None

        monkeypatch.setattr(feed, "_download", offline)
        feed._memory_cache.update(at=0.0, feed=None)  # a fresh launch: memory is empty
        assert (await feed.get_response("0.1.0", self.dir))["update"]["latest"] == "0.5.0"

    @pytest.mark.anyio
    async def test_offline_with_no_cache_says_nothing(self, monkeypatch):
        async def offline(url, version):
            return None

        monkeypatch.setattr(feed, "_download", offline)
        got = await feed.get_response("0.1.0", self.dir)
        assert got["update"] is None and got["announcement"] is None

    @pytest.mark.anyio
    async def test_turned_off_never_downloads(self, monkeypatch):
        async def boom(url, version):
            raise AssertionError("must not download when turned off")

        monkeypatch.setattr(feed, "_download", boom)
        feed.disable_feed(self.dir)
        assert (await feed.get_response("0.1.0", self.dir))["enabled"] is False

    def test_env_switch_turns_it_off(self, monkeypatch):
        monkeypatch.setenv("FF_FEED", "0")
        assert feed.feed_enabled(self.dir) is False


class TestEndpoints:
    def test_website_has_no_feed_endpoint(self):
        assert TestClient(main.app).get("/api/desktop/feed").status_code == 404
        assert TestClient(main.app).post("/api/desktop/feed/off").status_code == 404

    def test_desktop_serves_it_and_injects_the_shell_script(self, monkeypatch, tmp_path):
        monkeypatch.setattr(main, "LOCAL_ONLY", True)
        monkeypatch.setattr(main, "BASE_URL", "http://testserver")
        monkeypatch.setattr(main, "_DATA_DIR", tmp_path)
        monkeypatch.setenv("FF_APP_VERSION", "0.1.0")

        async def fake(version, data_dir):
            return {"enabled": True, "current": version, "update": None, "announcement": None}

        monkeypatch.setattr(main.desktop_feed, "get_response", fake)
        main._render_page.cache_clear()
        try:
            client = TestClient(main.app)
            assert client.get("/api/desktop/feed").json()["current"] == "0.1.0"
            assert "/static/local/desktop-shell.js" in client.get("/").text
        finally:
            main._render_page.cache_clear()

    def test_website_pages_do_not_load_the_shell_script(self):
        main._render_page.cache_clear()
        assert "desktop-shell.js" not in TestClient(main.app).get("/").text
