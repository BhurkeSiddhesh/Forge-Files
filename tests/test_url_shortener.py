"""URL shortener: create, redirect, validation, uniqueness, security."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

import main
from scripts import short_urls


@pytest.fixture
def client(tmp_path, monkeypatch):
    db = tmp_path / "short_urls.db"
    monkeypatch.setenv("SHORT_URL_DB_PATH", str(db))
    short_urls.close_connections()
    short_urls._initialized_paths.clear()
    monkeypatch.setattr(main.app.state, "rate_limit_enabled", False)
    # Keep main's imported module and the scripts import in sync for monkeypatches.
    assert main.short_urls is short_urls
    with TestClient(main.app) as c:
        yield c
    short_urls.close_connections()
    short_urls._initialized_paths.clear()


def test_shorten_happy_path_and_redirect(client):
    dest = "https://example.com/a/very/long/path?q=1#frag"
    resp = client.post("/api/url/shorten", json={"url": dest})
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["code"]
    assert data["url"] == dest
    assert data["short_path"] == f"/s/{data['code']}"
    assert data["short_url"].endswith(data["short_path"])
    assert data["expires_at"]

    redir = client.get(data["short_path"], follow_redirects=False)
    assert redir.status_code == 302
    assert redir.headers["location"] == dest


def test_invalid_url_rejection(client):
    for bad in ("", "   ", "not-a-url", "ftp://example.com", "/relative"):
        resp = client.post("/api/url/shorten", json={"url": bad})
        assert resp.status_code == 400, bad


def test_dangerous_schemes_rejected(client):
    for bad in (
        "javascript:alert(1)",
        "data:text/html,<script>alert(1)</script>",
        "vbscript:msgbox(1)",
        "file:///etc/passwd",
        "about:blank",
    ):
        resp = client.post("/api/url/shorten", json={"url": bad})
        assert resp.status_code == 400, bad
        assert "http" in resp.json()["detail"].lower()


def test_credentials_in_url_rejected(client):
    resp = client.post(
        "/api/url/shorten",
        json={"url": "https://user:pass@example.com/secret"},
    )
    assert resp.status_code == 400


def test_url_length_limit(client):
    too_long = "https://example.com/" + ("a" * short_urls.MAX_URL_LENGTH)
    resp = client.post("/api/url/shorten", json={"url": too_long})
    assert resp.status_code == 400


def test_unknown_short_code_404(client):
    resp = client.get("/s/abcdefg", follow_redirects=False)
    assert resp.status_code == 404


def test_invalid_short_code_shape_404(client):
    for code in ("ab", "!!!!!!!!", "ABCDEFG!"):
        resp = client.get(f"/s/{code}", follow_redirects=False)
        assert resp.status_code == 404, code


def test_collision_uniqueness(client, monkeypatch):
    codes = iter(["AAAAAAA", "AAAAAAA", "BBBBBBB"])

    def fake_generate():
        return next(codes)

    monkeypatch.setattr(short_urls, "_generate_code", fake_generate)
    r1 = client.post("/api/url/shorten", json={"url": "https://example.com/one"})
    r2 = client.post("/api/url/shorten", json={"url": "https://example.com/two"})
    assert r1.status_code == 200 and r2.status_code == 200
    assert r1.json()["code"] == "AAAAAAA"
    assert r2.json()["code"] == "BBBBBBB"


def test_explicit_code_collision_errors(client):
    code = "ZZZZZZZ"
    first = short_urls.create_short_url("https://example.com/a", code=code)
    assert first[0] == code
    with pytest.raises(ValueError, match="already in use"):
        short_urls.create_short_url("https://example.com/b", code=code)


def test_hits_increment_on_resolve(client):
    created = client.post(
        "/api/url/shorten", json={"url": "https://example.com/hits"}
    ).json()
    code = created["code"]
    client.get(f"/s/{code}", follow_redirects=False)
    client.get(f"/s/{code}", follow_redirects=False)
    path = short_urls.db_path()
    with sqlite3.connect(str(path)) as conn:
        hits = conn.execute(
            "SELECT hits FROM short_urls WHERE code = ?", (code,)
        ).fetchone()[0]
    assert hits == 2


def test_expired_link_is_gone(client):
    code, _, _ = short_urls.create_short_url("https://example.com/old", ttl_days=1)
    expired = (datetime.now(timezone.utc) - timedelta(days=2)).replace(microsecond=0).isoformat()
    conn = short_urls._writer()
    with short_urls._write_lock:
        conn.execute(
            "UPDATE short_urls SET expires_at = ? WHERE code = ?",
            (expired, code),
        )
        conn.commit()
    assert short_urls.resolve(code) is None
    resp = client.get(f"/s/{code}", follow_redirects=False)
    assert resp.status_code == 404


def test_missing_json_body(client):
    resp = client.post(
        "/api/url/shorten",
        content=b"not-json",
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 400


def test_validate_url_unit():
    assert short_urls.validate_url(" HTTPS://Example.COM/Path ") == "https://Example.COM/Path"
    with pytest.raises(ValueError):
        short_urls.validate_url("javascript:alert(1)")


def test_create_rate_limit_bucket(client, monkeypatch):
    monkeypatch.setattr(main.app.state, "rate_limit_enabled", True)
    monkeypatch.setenv("SHORT_URL_CREATE_LIMIT", "2")
    main.app.state.rate_limiter.reset()
    assert client.post("/api/url/shorten", json={"url": "https://example.com/1"}).status_code == 200
    assert client.post("/api/url/shorten", json={"url": "https://example.com/2"}).status_code == 200
    limited = client.post("/api/url/shorten", json={"url": "https://example.com/3"})
    assert limited.status_code == 429
    main.app.state.rate_limiter.reset()
    monkeypatch.setattr(main.app.state, "rate_limit_enabled", False)


def test_seo_landing_page_exists(client):
    resp = client.get("/url-shortener")
    assert resp.status_code == 200
    assert b"URL Shortener" in resp.content


def test_host_must_be_present(client):
    assert client.post("/api/url/shorten", json={"url": "http://:80/path"}).status_code == 400
    # an explicit localhost host is still allowed
    assert client.post("/api/url/shorten", json={"url": "http://localhost:8000/x"}).status_code == 200


def test_oversized_body_is_rejected_before_parsing(client):
    big = '{"url": "https://example.com", "pad": "' + "x" * 20_000 + '"}'
    resp = client.post("/api/url/shorten", content=big, headers={"Content-Type": "application/json"})
    assert resp.status_code == 413
    # chunked bodies carry no Content-Length, so the cap must also hold while streaming
    chunks = (b"x" * 4096 for _ in range(4))
    resp = client.post("/api/url/shorten", content=chunks, headers={"Content-Type": "application/json"})
    assert resp.status_code == 413


def test_loopback_base_url_refuses_unshareable_links(client, monkeypatch):
    monkeypatch.setattr(main, "BASE_URL", "http://127.0.0.1:8123")
    resp = client.post("/api/url/shorten", json={"url": "https://example.com"})
    assert resp.status_code == 503
    assert "forgefiles.org" in resp.json()["detail"]


def test_landing_page_describes_url_retention_not_file_upload(client):
    body = client.get("/url-shortener").text
    assert "destination URL" in body and "365 days" in body
    assert "uploads your file" not in body
