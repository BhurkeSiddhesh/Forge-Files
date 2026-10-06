"""The Annotate PDF form must post what /api/pdf/annotate accepts (it once sent
loose fields and the server answered HTTP 422 'annotations' required)."""

import io
import json
from pathlib import Path

import fitz
from fastapi.testclient import TestClient

import main

SCRIPT = (Path(__file__).resolve().parent.parent / "static" / "script.js").read_text(encoding="utf-8")


def test_ui_posts_a_json_annotations_array_not_loose_fields():
    block = SCRIPT[SCRIPT.index("process-annotate-pdf-btn"):SCRIPT.index("// --- PDF Metadata ---")]
    assert "fd.append('annotations', JSON.stringify([annotation]))" in block
    assert "annot_type" not in block and "fd.append('x0'" not in block


def test_endpoint_requires_the_annotations_field_and_accepts_the_ui_payload():
    doc = fitz.open()
    doc.new_page(width=595, height=842)
    pdf = doc.tobytes()
    client = TestClient(main.app)
    missing = client.post("/api/pdf/annotate", files={"file": ("a.pdf", io.BytesIO(pdf), "application/pdf")})
    assert missing.status_code == 422
    payload = json.dumps([{"type": "highlight", "page": 1, "rect": [50, 700, 300, 730]}])
    ok = client.post(
        "/api/pdf/annotate",
        files={"file": ("a.pdf", io.BytesIO(pdf), "application/pdf")},
        data={"annotations": payload},
    )
    assert ok.status_code == 200
    assert ok.json()["message"] == "Added 1 annotation(s)"
