"""Optional Office engine download for the desktop app (scripts/desktop_engine.py)."""
import hashlib
import json

import pytest
from fastapi.testclient import TestClient

import main
from scripts import desktop_engine as engine

VERSION = "9.9.9"
WASM = b"w" * 5000
DATA = b"d" * 3000
BASE = "https://example.test/lo"


@pytest.fixture
def tree(tmp_path, monkeypatch):
    """A static dir whose manifest lists one committed file and two large ones, plus an empty data dir."""
    static = tmp_path / "static"
    folder = static / "vendor" / "lo-wasm" / VERSION
    folder.mkdir(parents=True)
    files = {
        "soffice.js": {"bytes": 10, "sha256": "0" * 64, "committed": True},
        "soffice.wasm": {"bytes": len(WASM), "sha256": hashlib.sha256(WASM).hexdigest(), "committed": False},
        "soffice.data": {"bytes": len(DATA), "sha256": hashlib.sha256(DATA).hexdigest(), "committed": False},
    }
    (folder / "MANIFEST.json").write_text(json.dumps({"files": files}))
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setitem(engine._progress, "state", "idle")
    monkeypatch.setitem(engine._progress, "done", 0)
    monkeypatch.setitem(engine._progress, "error", "")
    monkeypatch.delenv("FF_ENGINE_DOWNLOAD", raising=False)
    return static, data


def serve(monkeypatch, bodies):
    seen = []

    def fake(url, user_agent):
        seen.append((url, user_agent))
        body = bodies[url.rsplit("/", 1)[1]]
        if isinstance(body, Exception):
            raise body
        for i in range(0, len(body), 1000):
            yield body[i:i + 1000]

    monkeypatch.setattr(engine, "_stream", fake)
    return seen


def test_only_the_uncommitted_files_are_downloaded_and_status_starts_missing(tree):
    static, data = tree
    assert set(engine.large_files(engine.read_manifest(static)[1])) == {"soffice.wasm", "soffice.data"}
    st = engine.status(data, static)
    assert st["state"] == "missing" and st["total"] == len(WASM) + len(DATA) and st["choice"] is None


def test_a_download_is_verified_stored_and_served(tree, monkeypatch):
    static, data = tree
    seen = serve(monkeypatch, {"soffice.wasm": WASM, "soffice.data": DATA})
    st = engine.start(data, static, "0.1.0", base=BASE, wait=True)
    assert st["state"] == "ready" and st["done"] == st["total"] and st["choice"] == "accepted"
    assert {u for u, _ in seen} == {f"{BASE}/{VERSION}/soffice.wasm", f"{BASE}/{VERSION}/soffice.data"}
    assert all(ua == "ForgeFiles-Desktop/0.1.0" for _, ua in seen)
    assert engine.served_path(data, static, f"vendor/lo-wasm/{VERSION}/soffice.wasm").read_bytes() == WASM
    assert not list((data / "engines").rglob("*.part"))


def test_a_corrupt_file_is_refused_and_nothing_is_kept(tree, monkeypatch):
    static, data = tree
    serve(monkeypatch, {"soffice.wasm": b"x" * len(WASM), "soffice.data": DATA})
    st = engine.start(data, static, "0.1.0", base=BASE, wait=True)
    assert st["state"] == "error" and st["error"]
    assert engine.served_path(data, static, f"vendor/lo-wasm/{VERSION}/soffice.wasm") is None
    assert not list((data / "engines").rglob("*.part"))
    assert not (engine.engine_dir(data, VERSION) / "soffice.wasm").exists()


def test_a_network_failure_is_an_error_and_a_retry_resumes_from_what_is_complete(tree, monkeypatch):
    static, data = tree
    serve(monkeypatch, {"soffice.wasm": WASM, "soffice.data": OSError("HTTP 503")})
    assert engine.start(data, static, "0.1.0", base=BASE, wait=True)["state"] == "error"
    assert (engine.engine_dir(data, VERSION) / "soffice.wasm").is_file()  # the finished file is kept
    seen = serve(monkeypatch, {"soffice.wasm": WASM, "soffice.data": DATA})
    assert engine.start(data, static, "0.1.0", base=BASE, wait=True)["state"] == "ready"
    assert [u.rsplit("/", 1)[1] for u, _ in seen] == ["soffice.data"]


def test_a_file_larger_than_the_manifest_says_is_refused(tree, monkeypatch):
    static, data = tree
    serve(monkeypatch, {"soffice.wasm": WASM + b"extra", "soffice.data": DATA})
    assert engine.start(data, static, "0.1.0", base=BASE, wait=True)["state"] == "error"


def test_the_off_switch_prevents_any_request(tree, monkeypatch):
    static, data = tree
    seen = serve(monkeypatch, {"soffice.wasm": WASM, "soffice.data": DATA})
    monkeypatch.setenv("FF_ENGINE_DOWNLOAD", "0")
    assert engine.start(data, static, "0.1.0", wait=True)["state"] == "disabled"
    assert seen == []


def test_declining_is_remembered_and_does_not_download(tree, monkeypatch):
    static, data = tree
    seen = serve(monkeypatch, {"soffice.wasm": WASM, "soffice.data": DATA})
    engine.set_choice(data, "declined")
    assert engine.status(data, static)["choice"] == "declined" and seen == []


@pytest.mark.parametrize("relative", [
    "vendor/lo-wasm/9.9.9/soffice.js",
    "vendor/lo-wasm/1.0.0/soffice.wasm",
    "vendor/lo-wasm/9.9.9/../soffice.wasm",
    "vendor/pdfjs/x.wasm",
    "vendor/lo-wasm/9.9.9",
])
def test_only_the_manifests_large_files_are_ever_served_from_the_data_folder(tree, relative):
    static, data = tree
    folder = engine.engine_dir(data, VERSION)
    folder.mkdir(parents=True)
    (folder / "soffice.wasm").write_bytes(WASM)
    (folder / "soffice.js").write_bytes(b"x")
    assert engine.served_path(data, static, relative) is None


def test_a_truncated_file_on_disk_is_not_served(tree):
    static, data = tree
    folder = engine.engine_dir(data, VERSION)
    folder.mkdir(parents=True)
    (folder / "soffice.wasm").write_bytes(WASM[:100])
    assert engine.served_path(data, static, f"vendor/lo-wasm/{VERSION}/soffice.wasm") is None
    assert engine.status(data, static)["state"] == "missing"


class _Inline:
    """Stands in for threading.Thread so the endpoint test runs the download to completion."""

    def __init__(self, target, args, **kwargs):
        self._target, self._args = target, args

    def start(self):
        self._target(*self._args)

    def join(self):
        pass


class TestEndpointsAndServing:
    @pytest.fixture
    def client(self, tree, monkeypatch):
        static, data = tree
        monkeypatch.setattr(main, "LOCAL_ONLY", True)
        monkeypatch.setattr(main, "BASE_URL", "http://testserver")
        monkeypatch.setattr(main, "_DATA_DIR", data)
        monkeypatch.setattr(main, "BASE_DIR", static.parent)
        return TestClient(main.app), static, data

    def test_the_website_has_no_engine_endpoints(self, monkeypatch):
        monkeypatch.setattr(main, "LOCAL_ONLY", False)
        client = TestClient(main.app)
        assert client.get("/api/desktop/engine").status_code == 404
        assert client.post("/api/desktop/engine/download").status_code == 404
        assert client.post("/api/desktop/engine/decline").status_code == 404

    def test_status_decline_and_download_flow(self, client, monkeypatch):
        c, static, data = client
        assert c.get("/api/desktop/engine").json()["state"] == "missing"
        assert c.post("/api/desktop/engine/decline").json()["choice"] == "declined"
        seen = serve(monkeypatch, {"soffice.wasm": WASM, "soffice.data": DATA})
        monkeypatch.setattr(engine.threading, "Thread", _Inline)
        assert c.post("/api/desktop/engine/download").json()["state"] == "ready"
        assert len(seen) == 2

    def test_a_downloaded_engine_file_is_served_at_the_websites_url(self, client):
        c, static, data = client
        url = f"/static/vendor/lo-wasm/{VERSION}/soffice.wasm"
        assert c.get(url).status_code == 404
        folder = engine.engine_dir(data, VERSION)
        folder.mkdir(parents=True)
        (folder / "soffice.wasm").write_bytes(WASM)
        resp = c.get(url)
        assert resp.status_code == 200 and resp.content == WASM
        assert resp.headers["content-type"] == "application/wasm"
        assert resp.headers["cross-origin-embedder-policy"] == "require-corp"

    def test_the_website_never_serves_from_a_data_folder(self, client, monkeypatch):
        c, static, data = client
        folder = engine.engine_dir(data, VERSION)
        folder.mkdir(parents=True)
        (folder / "soffice.wasm").write_bytes(WASM)
        monkeypatch.setattr(main, "LOCAL_ONLY", False)
        assert TestClient(main.app).get(f"/static/vendor/lo-wasm/{VERSION}/soffice.wasm").status_code == 404
