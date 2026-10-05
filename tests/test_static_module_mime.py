"""Vendored ES modules must be served as JavaScript or browsers refuse to import them."""

from fastapi.testclient import TestClient

import main


def test_mjs_and_wasm_have_executable_mime_types():
    client = TestClient(main.app)
    mjs = client.get("/static/vendor/pdfjs/pdf.min.mjs")
    assert mjs.status_code == 200
    assert mjs.headers["content-type"].split(";")[0] == "text/javascript"
    wasm = client.get("/static/vendor/pdfjs/wasm/openjpeg.wasm")
    assert wasm.status_code == 200
    assert wasm.headers["content-type"].split(";")[0] == "application/wasm"


def test_module_types_survive_a_mimetypes_reset():
    import mimetypes

    mimetypes.init()  # what a stray library import can do after main registered types
    client = TestClient(main.app)
    mjs = client.get("/static/vendor/pdfjs/pdf.worker.min.mjs")
    assert mjs.headers["content-type"].split(";")[0] == "text/javascript"
