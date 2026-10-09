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
