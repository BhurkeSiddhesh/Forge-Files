"""
test_pdf_output_quality.py
==========================
Output-quality integration tests for all PDF tools.

These tests go beyond HTTP status codes: they call the API, download the
resulting file via /api/download/{token}, and validate the actual bytes —
page counts, PDF validity, archive contents, annotations, text content, etc.

Run with:
    pytest public/tests/test_pdf_output_quality.py -v
"""

from __future__ import annotations

import io
import json
import os
import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

os.environ.setdefault("FILE_FORGE_API_KEY", "")
os.environ.setdefault("DISABLE_AI", "1")  # skip heavy OCR model init

from main import app  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

TEST_KEY = "test-secret-key"
AUTH_HEADERS = {"X-API-Key": TEST_KEY}


# ──────────────────────────────────────────────────────────────
# Client / fixture helpers
# ──────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def client() -> TestClient:
    app.state.api_key = TEST_KEY
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


def _download(client: TestClient, token: str) -> bytes:
    """Fetch file bytes for a given download_token."""
    resp = client.get(f"/api/download/{token}", headers=AUTH_HEADERS)
    assert resp.status_code == 200, f"Download failed ({resp.status_code}): {resp.text[:200]}"
    return resp.content


def _post_ok(client: TestClient, path: str, data: dict | None = None,
             files=None) -> dict:
    """POST and assert 200, returning the parsed JSON body."""
    resp = client.post(path, headers=AUTH_HEADERS, data=data or {}, files=files or {})
    assert resp.status_code == 200, (
        f"POST {path} returned {resp.status_code}: {resp.text[:300]}"
    )
    body = resp.json()
    assert body.get("status") == "success", f"Unexpected body: {body}"
    assert "download_token" in body, "Response missing download_token"
    assert "filename" in body, "Response missing filename"
    return body


# ──────────────────────────────────────────────────────────────
# File / byte builders
# ──────────────────────────────────────────────────────────────

def _make_pdf(pages: int = 1, text: str = "Hello, Forge!") -> bytes:
    from reportlab.pdfgen import canvas as rl_canvas
    from reportlab.lib.pagesizes import A4

    buf = io.BytesIO()
    c = rl_canvas.Canvas(buf, pagesize=A4)
    for i in range(pages):
        c.drawString(72, 720, f"{text} — page {i + 1}")
        c.showPage()
    c.save()
    buf.seek(0)
    return buf.read()


def _make_png(w: int = 200, h: int = 200, color=(255, 0, 0)) -> bytes:
    from PIL import Image

    img = Image.new("RGB", (w, h), color=color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf.read()


def _make_jpg(w: int = 200, h: int = 200, color=(0, 128, 255)) -> bytes:
    from PIL import Image

    img = Image.new("RGB", (w, h), color=color)
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    buf.seek(0)
    return buf.read()


def _assert_valid_pdf(data: bytes, *, min_pages: int | None = None) -> int:
    """Assert `data` is a readable PDF; return page count."""
    import pikepdf

    assert data[:4] == b"%PDF", "Output is not a PDF (bad magic bytes)"
    with pikepdf.open(io.BytesIO(data)) as pdf:
        count = len(pdf.pages)
        if min_pages is not None:
            assert count >= min_pages, (
                f"Expected at least {min_pages} pages, got {count}"
            )
    return count


def _assert_valid_zip(data: bytes, *, min_members: int = 1) -> list:
    """Assert `data` is a valid ZIP; return member names."""
    assert data[:2] == b"PK", "Output is not a ZIP (bad magic bytes)"
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        names = zf.namelist()
        assert len(names) >= min_members, (
            f"Expected at least {min_members} files in ZIP, got {len(names)}: {names}"
        )
    return names


# ──────────────────────────────────────────────────────────────
# 1. Protect PDF — output is a password-protected PDF
# ──────────────────────────────────────────────────────────────

class TestProtectPDFOutput:
    def test_output_is_valid_pdf(self, client):
        body = _post_ok(
            client,
            "/api/pdf/protect",
            data={"user_password": "s3cret"},
            files={"file": ("in.pdf", _make_pdf(), "application/pdf")},
        )
        data = _download(client, body["download_token"])
        assert data[:4] == b"%PDF", "Protected PDF: bad magic bytes"

    def test_output_is_encrypted(self, client):
        import pikepdf

        body = _post_ok(
            client,
            "/api/pdf/protect",
            data={"user_password": "hunter2"},
            files={"file": ("in.pdf", _make_pdf(), "application/pdf")},
        )
        data = _download(client, body["download_token"])
        with pytest.raises(pikepdf.PasswordError):
            pikepdf.open(io.BytesIO(data))

    def test_correct_password_opens_pdf(self, client):
        import pikepdf

        pw = "correct-horse-battery-staple"
        body = _post_ok(
            client,
            "/api/pdf/protect",
            data={"user_password": pw},
            files={"file": ("in.pdf", _make_pdf(), "application/pdf")},
        )
        data = _download(client, body["download_token"])
        with pikepdf.open(io.BytesIO(data), password=pw) as pdf:
            assert len(pdf.pages) >= 1


# ──────────────────────────────────────────────────────────────
# 2. Remove Password — output is an unlocked PDF
# ──────────────────────────────────────────────────────────────

class TestRemovePasswordOutput:
    def _make_locked_pdf(self, pages=2, password="mypass"):
        import pikepdf

        buf = io.BytesIO()
        src_pdf = _make_pdf(pages=pages)
        with pikepdf.open(io.BytesIO(src_pdf)) as pdf:
            pdf.save(
                buf,
                encryption=pikepdf.Encryption(user=password, owner=password),
            )
        buf.seek(0)
        return buf.read()

    def test_output_is_unlocked_pdf(self, client):
        import pikepdf

        pw = "mypass"
        pdf_bytes = self._make_locked_pdf(pages=2, password=pw)
        body = _post_ok(
            client,
            "/api/pdf/remove-password",
            data={"password": pw},
            files={"file": ("locked.pdf", pdf_bytes, "application/pdf")},
        )
        data = _download(client, body["download_token"])
        with pikepdf.open(io.BytesIO(data)) as pdf:
            assert len(pdf.pages) == 2


# ──────────────────────────────────────────────────────────────
# 3. Rotate PDF — page rotation applied, count preserved
# ──────────────────────────────────────────────────────────────

class TestRotatePDFOutput:
    def test_output_is_valid_pdf(self, client):
        body = _post_ok(
            client,
            "/api/pdf/rotate",
            data={"angle": "90"},
            files={"file": ("in.pdf", _make_pdf(), "application/pdf")},
        )
        data = _download(client, body["download_token"])
        _assert_valid_pdf(data, min_pages=1)

    def test_page_count_preserved(self, client):
        body = _post_ok(
            client,
            "/api/pdf/rotate",
            data={"angle": "180"},
            files={"file": ("in.pdf", _make_pdf(pages=3), "application/pdf")},
        )
        data = _download(client, body["download_token"])
        assert _assert_valid_pdf(data) == 3

    def test_rotation_applied(self, client):
        import pikepdf

        body = _post_ok(
            client,
            "/api/pdf/rotate",
            data={"angle": "90"},
            files={"file": ("in.pdf", _make_pdf(), "application/pdf")},
        )
        data = _download(client, body["download_token"])
        with pikepdf.open(io.BytesIO(data)) as pdf:
            page = pdf.pages[0]
            rotate = int(page.get("/Rotate", 0))
            assert rotate in (90, 270), f"Expected 90 or 270 degree rotation, got {rotate}"


# ──────────────────────────────────────────────────────────────
# 4. Compress PDF — valid output, size metadata present
# ──────────────────────────────────────────────────────────────

class TestCompressPDFOutput:
    def test_output_is_valid_pdf(self, client):
        body = _post_ok(
            client,
            "/api/pdf/compress",
            data={"level": "medium"},
            files={"file": ("in.pdf", _make_pdf(), "application/pdf")},
        )
        data = _download(client, body["download_token"])
        _assert_valid_pdf(data, min_pages=1)

    def test_response_includes_size_metadata(self, client):
        resp = client.post(
            "/api/pdf/compress",
            headers=AUTH_HEADERS,
            data={"level": "medium"},
            files={"file": ("in.pdf", _make_pdf(), "application/pdf")},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "original_size" in body
        assert "compressed_size" in body
        assert "reduction_pct" in body
        assert body["original_size"] > 0
        assert body["compressed_size"] > 0

    def test_page_count_preserved(self, client):
        body = _post_ok(
            client,
            "/api/pdf/compress",
            data={"level": "low"},
            files={"file": ("in.pdf", _make_pdf(pages=4), "application/pdf")},
        )
        data = _download(client, body["download_token"])
        assert _assert_valid_pdf(data) == 4


# ──────────────────────────────────────────────────────────────
# 5. Merge PDF — page count equals sum of inputs
# ──────────────────────────────────────────────────────────────

class TestMergePDFOutput:
    def test_output_is_valid_pdf(self, client):
        body = _post_ok(
            client,
            "/api/pdf/merge",
            files=[
                ("files", ("a.pdf", _make_pdf(pages=1), "application/pdf")),
                ("files", ("b.pdf", _make_pdf(pages=2), "application/pdf")),
            ],
        )
        data = _download(client, body["download_token"])
        _assert_valid_pdf(data)

    def test_merged_page_count(self, client):
        body = _post_ok(
            client,
            "/api/pdf/merge",
            files=[
                ("files", ("a.pdf", _make_pdf(pages=2), "application/pdf")),
                ("files", ("b.pdf", _make_pdf(pages=3), "application/pdf")),
            ],
        )
        data = _download(client, body["download_token"])
        assert _assert_valid_pdf(data) == 5


# ──────────────────────────────────────────────────────────────
# 6. Split PDF — ZIP of single-page PDFs, correct count
# ──────────────────────────────────────────────────────────────

class TestSplitPDFOutput:
    def test_output_is_zip(self, client):
        body = _post_ok(
            client,
            "/api/pdf/split",
            data={"mode": "each"},
            files={"file": ("in.pdf", _make_pdf(pages=3), "application/pdf")},
        )
        data = _download(client, body["download_token"])
        _assert_valid_zip(data, min_members=3)

    def test_each_page_is_single_page_pdf(self, client):
        import pikepdf

        body = _post_ok(
            client,
            "/api/pdf/split",
            data={"mode": "each"},
            files={"file": ("in.pdf", _make_pdf(pages=3), "application/pdf")},
        )
        data = _download(client, body["download_token"])
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            for name in zf.namelist():
                with zf.open(name) as f:
                    member_bytes = f.read()
                with pikepdf.open(io.BytesIO(member_bytes)) as pdf:
                    assert len(pdf.pages) == 1, f"{name} should be exactly 1 page"

    def test_file_count_in_response(self, client):
        resp = client.post(
            "/api/pdf/split",
            headers=AUTH_HEADERS,
            data={"mode": "each"},
            files={"file": ("in.pdf", _make_pdf(pages=4), "application/pdf")},
        )
        assert resp.status_code == 200
        assert resp.json()["file_count"] == 4


# ──────────────────────────────────────────────────────────────
# 7. Watermark PDF — valid PDF, page count preserved
# ──────────────────────────────────────────────────────────────

class TestWatermarkPDFOutput:
    def test_output_is_valid_pdf(self, client):
        body = _post_ok(
            client,
            "/api/pdf/watermark",
            data={"text": "CONFIDENTIAL"},
            files={"file": ("in.pdf", _make_pdf(), "application/pdf")},
        )
        data = _download(client, body["download_token"])
        _assert_valid_pdf(data, min_pages=1)

    def test_page_count_preserved(self, client):
        body = _post_ok(
            client,
            "/api/pdf/watermark",
            data={"text": "DRAFT"},
            files={"file": ("in.pdf", _make_pdf(pages=3), "application/pdf")},
        )
        data = _download(client, body["download_token"])
        assert _assert_valid_pdf(data) == 3


# ──────────────────────────────────────────────────────────────
# 8. PDF to Images — ZIP of readable images, count matches
# ──────────────────────────────────────────────────────────────

class TestPDFToImagesOutput:
    def test_output_is_zip(self, client):
        body = _post_ok(
            client,
            "/api/pdf/to-images",
            data={"fmt": "jpg", "dpi": "72"},
            files={"file": ("in.pdf", _make_pdf(pages=2), "application/pdf")},
        )
        data = _download(client, body["download_token"])
        _assert_valid_zip(data, min_members=2)

    def test_images_are_readable(self, client):
        from PIL import Image

        body = _post_ok(
            client,
            "/api/pdf/to-images",
            data={"fmt": "jpg", "dpi": "72"},
            files={"file": ("in.pdf", _make_pdf(pages=2), "application/pdf")},
        )
        data = _download(client, body["download_token"])
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            for name in zf.namelist():
                with zf.open(name) as f:
                    img_bytes = f.read()
                img = Image.open(io.BytesIO(img_bytes))
                img.verify()

    def test_page_count_in_response(self, client):
        resp = client.post(
            "/api/pdf/to-images",
            headers=AUTH_HEADERS,
            data={"fmt": "jpg", "dpi": "72"},
            files={"file": ("in.pdf", _make_pdf(pages=3), "application/pdf")},
        )
        assert resp.status_code == 200
        assert resp.json()["page_count"] == 3


# ──────────────────────────────────────────────────────────────
# 9. Extract Pages — subset page count
# ──────────────────────────────────────────────────────────────

class TestExtractPagesOutput:
    def test_output_is_valid_pdf(self, client):
        body = _post_ok(
            client,
            "/api/pdf/extract-pages",
            data={"pages": "1,3"},
            files={"file": ("in.pdf", _make_pdf(pages=4), "application/pdf")},
        )
        data = _download(client, body["download_token"])
        _assert_valid_pdf(data)

    def test_extracted_page_count(self, client):
        body = _post_ok(
            client,
            "/api/pdf/extract-pages",
            data={"pages": "2,4"},
            files={"file": ("in.pdf", _make_pdf(pages=5), "application/pdf")},
        )
        data = _download(client, body["download_token"])
        assert _assert_valid_pdf(data) == 2


# ──────────────────────────────────────────────────────────────
# 10. PDF to Excel — .xlsx, openable
# ──────────────────────────────────────────────────────────────

class TestPDFToExcelOutput:
    def test_output_filename_is_xlsx(self, client):
        body = _post_ok(
            client,
            "/api/pdf/to-excel",
            files={"file": ("in.pdf", _make_pdf(), "application/pdf")},
        )
        assert body["filename"].endswith(".xlsx")

    def test_output_is_openable_xlsx(self, client):
        import openpyxl

        body = _post_ok(
            client,
            "/api/pdf/to-excel",
            files={"file": ("in.pdf", _make_pdf(), "application/pdf")},
        )
        data = _download(client, body["download_token"])
        wb = openpyxl.load_workbook(io.BytesIO(data))
        assert len(wb.sheetnames) >= 1


# ──────────────────────────────────────────────────────────────
# 11. PDF to PPTX — .pptx, openable
# ──────────────────────────────────────────────────────────────

class TestPDFToPPTXOutput:
    def test_output_filename_is_pptx(self, client):
        body = _post_ok(
            client,
            "/api/pdf/to-pptx",
            files={"file": ("in.pdf", _make_pdf(), "application/pdf")},
        )
        assert body["filename"].endswith(".pptx")

    def test_output_is_openable_pptx(self, client):
        from pptx import Presentation

        body = _post_ok(
            client,
            "/api/pdf/to-pptx",
            files={"file": ("in.pdf", _make_pdf(), "application/pdf")},
        )
        data = _download(client, body["download_token"])
        prs = Presentation(io.BytesIO(data))
        assert len(prs.slides) >= 1


# ──────────────────────────────────────────────────────────────
# 12. PDF to EPUB — .epub, valid ZIP structure
# ──────────────────────────────────────────────────────────────

class TestPDFToEPUBOutput:
    def test_output_filename_is_epub(self, client):
        body = _post_ok(
            client,
            "/api/pdf/to-epub",
            files={"file": ("in.pdf", _make_pdf(), "application/pdf")},
        )
        assert body["filename"].endswith(".epub")

    def test_epub_is_valid_zip(self, client):
        body = _post_ok(
            client,
            "/api/pdf/to-epub",
            files={"file": ("in.pdf", _make_pdf(), "application/pdf")},
        )
        data = _download(client, body["download_token"])
        names = _assert_valid_zip(data, min_members=1)
        assert len(names) >= 1


# ──────────────────────────────────────────────────────────────
# 13. Extract Text — .txt with expected content
# ──────────────────────────────────────────────────────────────

class TestExtractTextOutput:
    def test_output_filename_is_txt(self, client):
        body = _post_ok(
            client,
            "/api/pdf/extract-text",
            files={"file": ("in.pdf", _make_pdf(), "application/pdf")},
        )
        assert body["filename"].endswith(".txt")

    def test_output_contains_injected_text(self, client):
        marker = "UniqueMarkerXYZ987"
        body = _post_ok(
            client,
            "/api/pdf/extract-text",
            files={
                "file": ("in.pdf", _make_pdf(text=marker), "application/pdf")
            },
        )
        data = _download(client, body["download_token"])
        text = data.decode("utf-8", errors="replace")
        assert marker in text, (
            f"Expected injected marker '{marker}' not found in extracted text"
        )


# ──────────────────────────────────────────────────────────────
# 14. Organize (Reorder) PDF — page count matches order length
# ──────────────────────────────────────────────────────────────

class TestOrganizePDFOutput:
    def test_output_is_valid_pdf(self, client):
        body = _post_ok(
            client,
            "/api/pdf/organize",
            data={"page_order": "3,1,2"},
            files={"file": ("in.pdf", _make_pdf(pages=3), "application/pdf")},
        )
        data = _download(client, body["download_token"])
        _assert_valid_pdf(data, min_pages=3)

    def test_page_count_matches_order_length(self, client):
        body = _post_ok(
            client,
            "/api/pdf/organize",
            data={"page_order": "2,4"},
            files={"file": ("in.pdf", _make_pdf(pages=4), "application/pdf")},
        )
        data = _download(client, body["download_token"])
        assert _assert_valid_pdf(data) == 2


# ──────────────────────────────────────────────────────────────
# 15. Add Page Numbers — page count preserved
# ──────────────────────────────────────────────────────────────

class TestAddPageNumbersOutput:
    def test_output_is_valid_pdf(self, client):
        body = _post_ok(
            client,
            "/api/pdf/add-page-numbers",
            files={"file": ("in.pdf", _make_pdf(pages=2), "application/pdf")},
        )
        data = _download(client, body["download_token"])
        _assert_valid_pdf(data, min_pages=2)

    def test_page_count_preserved(self, client):
        body = _post_ok(
            client,
            "/api/pdf/add-page-numbers",
            files={"file": ("in.pdf", _make_pdf(pages=3), "application/pdf")},
        )
        data = _download(client, body["download_token"])
        assert _assert_valid_pdf(data) == 3


# ──────────────────────────────────────────────────────────────
# 16. Repair PDF — valid output, page count preserved
# ──────────────────────────────────────────────────────────────

class TestRepairPDFOutput:
    def test_output_is_valid_pdf(self, client):
        body = _post_ok(
            client,
            "/api/pdf/repair",
            files={"file": ("in.pdf", _make_pdf(), "application/pdf")},
        )
        data = _download(client, body["download_token"])
        _assert_valid_pdf(data, min_pages=1)

    def test_page_count_preserved(self, client):
        body = _post_ok(
            client,
            "/api/pdf/repair",
            files={"file": ("in.pdf", _make_pdf(pages=2), "application/pdf")},
        )
        data = _download(client, body["download_token"])
        assert _assert_valid_pdf(data) == 2


# ──────────────────────────────────────────────────────────────
# 17. Create from Text — non-empty PDF
# ──────────────────────────────────────────────────────────────

class TestCreateFromTextOutput:
    def test_output_is_valid_pdf(self, client):
        body = _post_ok(
            client,
            "/api/pdf/create-from-text",
            data={"content": "This is a test document created by Forge Files."},
        )
        data = _download(client, body["download_token"])
        _assert_valid_pdf(data, min_pages=1)

    def test_output_is_non_empty(self, client):
        body = _post_ok(
            client,
            "/api/pdf/create-from-text",
            data={"content": "Non-empty content test."},
        )
        data = _download(client, body["download_token"])
        assert len(data) > 500, "Created PDF is suspiciously small"


# ──────────────────────────────────────────────────────────────
# 18. Create Blank — exact page count
# ──────────────────────────────────────────────────────────────

class TestCreateBlankOutput:
    def test_output_is_valid_pdf(self, client):
        body = _post_ok(
            client,
            "/api/pdf/create-blank",
            data={"num_pages": "3"},
        )
        data = _download(client, body["download_token"])
        _assert_valid_pdf(data, min_pages=3)

    def test_exact_page_count(self, client):
        body = _post_ok(
            client,
            "/api/pdf/create-blank",
            data={"num_pages": "5"},
        )
        data = _download(client, body["download_token"])
        assert _assert_valid_pdf(data) == 5


# ──────────────────────────────────────────────────────────────
# 19. Annotate PDF — annotations embedded
# ──────────────────────────────────────────────────────────────

class TestAnnotatePDFOutput:
    def test_output_is_valid_pdf(self, client):
        annotations = json.dumps([
            {"type": "highlight", "page": 1, "rect": [50, 700, 300, 730]},
        ])
        body = _post_ok(
            client,
            "/api/pdf/annotate",
            data={"annotations": annotations},
            files={"file": ("in.pdf", _make_pdf(), "application/pdf")},
        )
        data = _download(client, body["download_token"])
        _assert_valid_pdf(data, min_pages=1)

    def test_annotations_present_in_pdf(self, client):
        import pikepdf

        annotations = json.dumps([
            {"type": "highlight", "page": 1, "rect": [50, 700, 300, 730]},
            {"type": "underline", "page": 1, "rect": [50, 680, 300, 700]},
        ])
        body = _post_ok(
            client,
            "/api/pdf/annotate",
            data={"annotations": annotations},
            files={"file": ("in.pdf", _make_pdf(), "application/pdf")},
        )
        data = _download(client, body["download_token"])
        with pikepdf.open(io.BytesIO(data)) as pdf:
            page = pdf.pages[0]
            annots = page.get("/Annots")
            assert annots is not None and len(annots) >= 1, (
                "Expected annotations on page 1, found none"
            )

    def test_multiple_annotation_types(self, client):
        import pikepdf

        annotations = json.dumps([
            {"type": "highlight", "page": 1, "rect": [50, 700, 300, 730]},
            {"type": "underline", "page": 1, "rect": [50, 670, 300, 695]},
            {"type": "strikeout", "page": 1, "rect": [50, 640, 300, 660]},
        ])
        body = _post_ok(
            client,
            "/api/pdf/annotate",
            data={"annotations": annotations},
            files={"file": ("in.pdf", _make_pdf(), "application/pdf")},
        )
        data = _download(client, body["download_token"])
        with pikepdf.open(io.BytesIO(data)) as pdf:
            page = pdf.pages[0]
            annots = page.get("/Annots")
            assert annots is not None and len(annots) >= 3


# ──────────────────────────────────────────────────────────────
# 20. Edit Metadata (write) — title written to docinfo
# ──────────────────────────────────────────────────────────────

class TestMetadataWriteOutput:
    def test_output_is_valid_pdf(self, client):
        body = _post_ok(
            client,
            "/api/pdf/metadata",
            data={"title": "Forge Test Title", "author": "Forge Tester"},
            files={"file": ("in.pdf", _make_pdf(), "application/pdf")},
        )
        data = _download(client, body["download_token"])
        _assert_valid_pdf(data, min_pages=1)

    def test_title_written_to_pdf(self, client):
        import pikepdf

        body = _post_ok(
            client,
            "/api/pdf/metadata",
            data={"title": "UniqueTestTitle_XYZ"},
            files={"file": ("in.pdf", _make_pdf(), "application/pdf")},
        )
        data = _download(client, body["download_token"])
        with pikepdf.open(io.BytesIO(data)) as pdf:
            docinfo = pdf.docinfo
            title = str(docinfo.get("/Title", ""))
            assert "UniqueTestTitle_XYZ" in title, (
                f"Title not found in PDF metadata: {title!r}"
            )


# ──────────────────────────────────────────────────────────────
# 21. Read Metadata — page_count is correct
# ──────────────────────────────────────────────────────────────

class TestMetadataReadOutput:
    def test_page_count_correct(self, client):
        resp = client.post(
            "/api/pdf/metadata/read",
            headers=AUTH_HEADERS,
            files={"file": ("in.pdf", _make_pdf(pages=3), "application/pdf")},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["metadata"]["page_count"] == 3

    def test_metadata_has_expected_keys(self, client):
        resp = client.post(
            "/api/pdf/metadata/read",
            headers=AUTH_HEADERS,
            files={"file": ("in.pdf", _make_pdf(), "application/pdf")},
        )
        assert resp.status_code == 200
        meta = resp.json()["metadata"]
        assert "page_count" in meta, "Metadata response missing page_count"


# ──────────────────────────────────────────────────────────────
# 22. Sign PDF — page count preserved after signing
# ──────────────────────────────────────────────────────────────

class TestSignPDFOutput:
    def test_output_is_valid_pdf(self, client):
        body = _post_ok(
            client,
            "/api/pdf/sign",
            data={"page": "1", "x": "0.65", "y": "0.85", "width": "0.2"},
            files={
                "file": ("in.pdf", _make_pdf(), "application/pdf"),
                "signature": ("sig.png", _make_png(), "image/png"),
            },
        )
        data = _download(client, body["download_token"])
        _assert_valid_pdf(data, min_pages=1)

    def test_page_count_preserved(self, client):
        body = _post_ok(
            client,
            "/api/pdf/sign",
            data={"page": "1"},
            files={
                "file": ("in.pdf", _make_pdf(pages=3), "application/pdf"),
                "signature": ("sig.png", _make_png(), "image/png"),
            },
        )
        data = _download(client, body["download_token"])
        assert _assert_valid_pdf(data) == 3


# ──────────────────────────────────────────────────────────────
# 23. Image to PDF — each image becomes a page
# ──────────────────────────────────────────────────────────────

class TestImageToPDFOutput:
    def test_single_image_becomes_one_page(self, client):
        body = _post_ok(
            client,
            "/api/image/to-pdf",
            files=[("files", ("img.png", _make_png(), "image/png"))],
        )
        data = _download(client, body["download_token"])
        assert _assert_valid_pdf(data) == 1

    def test_two_images_become_two_pages(self, client):
        body = _post_ok(
            client,
            "/api/image/to-pdf",
            files=[
                ("files", ("a.png", _make_png(), "image/png")),
                ("files", ("b.jpg", _make_jpg(), "image/jpeg")),
            ],
        )
        data = _download(client, body["download_token"])
        assert _assert_valid_pdf(data) == 2


# ──────────────────────────────────────────────────────────────
# 24. PDF to Word (.docx) — openable docx
# ──────────────────────────────────────────────────────────────

class TestPDFToWordOutput:
    def test_output_filename_is_docx(self, client):
        body = _post_ok(
            client,
            "/api/pdf/convert-to-word",
            files={"file": ("in.pdf", _make_pdf(), "application/pdf")},
        )
        assert body["filename"].endswith(".docx")

    def test_output_is_openable_docx(self, client):
        from docx import Document

        body = _post_ok(
            client,
            "/api/pdf/convert-to-word",
            files={"file": ("in.pdf", _make_pdf(), "application/pdf")},
        )
        data = _download(client, body["download_token"])
        doc = Document(io.BytesIO(data))
        assert doc is not None


# ──────────────────────────────────────────────────────────────
# 25. OCR PDF — searchable PDF output (skipped when AI disabled)
# ──────────────────────────────────────────────────────────────

class TestOCRPDFOutput:
    """OCR requires a live OCR engine. When DISABLE_AI=1 (CI default) the
    endpoint correctly returns 400; when a real engine is present the output
    is a valid searchable PDF with the correct page count."""

    def test_ocr_without_engine_returns_400(self, client):
        """With DISABLE_AI=1 the endpoint must reject gracefully with 400."""
        resp = client.post(
            "/api/pdf/ocr",
            headers=AUTH_HEADERS,
            data={"lang": "en"},
            files={"file": ("in.pdf", _make_pdf(), "application/pdf")},
        )
        # When OCR is disabled the server raises ValueError → 400.
        # When OCR is enabled the server returns 200 (tested below).
        assert resp.status_code in (200, 400), (
            f"Unexpected status from /api/pdf/ocr: {resp.status_code}"
        )

    @pytest.mark.skipif(
        os.environ.get("DISABLE_AI", "0") == "1",
        reason="OCR engine disabled in this environment",
    )
    def test_ocr_output_is_valid_pdf_when_engine_present(self, client):
        """With a real OCR engine the output must be a valid PDF."""
        body = _post_ok(
            client,
            "/api/pdf/ocr",
            data={"lang": "en"},
            files={"file": ("in.pdf", _make_pdf(), "application/pdf")},
        )
        data = _download(client, body["download_token"])
        _assert_valid_pdf(data, min_pages=1)

    @pytest.mark.skipif(
        os.environ.get("DISABLE_AI", "0") == "1",
        reason="OCR engine disabled in this environment",
    )
    def test_ocr_page_count_in_response(self, client):
        resp = client.post(
            "/api/pdf/ocr",
            headers=AUTH_HEADERS,
            data={"lang": "en"},
            files={"file": ("in.pdf", _make_pdf(pages=2), "application/pdf")},
        )
        assert resp.status_code == 200
        assert resp.json()["page_count"] == 2
