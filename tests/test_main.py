from fastapi.testclient import TestClient
from main import app
from conftest import result_path
from unittest.mock import patch
import pytest
import os
import pikepdf
import zipfile

# Removed global client = TestClient(app)

@pytest.fixture
def mock_dirs(tmp_path):
    """Patches UPLOAD_DIR and OUTPUT_DIR to use temporary directories."""
    upload_dir = tmp_path / "uploads"
    output_dir = tmp_path / "outputs"
    upload_dir.mkdir()
    output_dir.mkdir()

    # Patch the variables in main.py
    with patch("main.UPLOAD_DIR", upload_dir), patch("main.OUTPUT_DIR", output_dir):
        yield {"upload": upload_dir, "output": output_dir}

def test_read_index(auth_client):
    response = auth_client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]

def test_read_index_head(auth_client):
    response = auth_client.head("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]

def test_api_remove_password(locked_pdf, mock_dirs, auth_client):
    file_path = locked_pdf["path"]
    password = locked_pdf["password"]

    with open(file_path, "rb") as f:
        files = {"file": (file_path.name, f, "application/pdf")}
        data = {"password": password}
        response = auth_client.post("/api/pdf/remove-password", files=files, data=data)

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"

    # Verify file exists in mock output dir
    output_filename = data["filename"]
    assert result_path(mock_dirs["output"], data).exists()

def test_api_remove_password_wrong_password(locked_pdf, mock_dirs, auth_client):
    file_path = locked_pdf["path"]
    wrong_password = "wrong"  # ggignore

    with open(file_path, "rb") as f:
        files = {"file": (file_path.name, f, "application/pdf")}
        data = {"password": wrong_password}
        response = auth_client.post("/api/pdf/remove-password", files=files, data=data)

    assert response.status_code == 400

def test_api_convert_to_word(sample_pdf, mock_dirs, auth_client):
    file_path = sample_pdf

    with open(file_path, "rb") as f:
        files = {"file": (file_path.name, f, "application/pdf")}
        response = auth_client.post("/api/pdf/convert-to-word", files=files)

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"

    # Verify file exists in mock output dir
    output_filename = data["filename"]
    assert result_path(mock_dirs["output"], data).exists()

def test_api_extract_pages(multi_page_pdf, mock_dirs, auth_client):
    file_path = multi_page_pdf

    with open(file_path, "rb") as f:
        files = {"file": (file_path.name, f, "application/pdf")}
        data = {"pages": "2-3"}
        response = auth_client.post("/api/pdf/extract-pages", files=files, data=data)

    assert response.status_code == 200
    resp_data = response.json()
    assert resp_data["status"] == "success"
    output_filename = resp_data["filename"]
    output_path = result_path(mock_dirs["output"], resp_data)
    assert output_path.exists()

    with pikepdf.open(output_path) as pdf:
        assert len(pdf.pages) == 2


def test_api_split_pdf_returns_zip(multi_page_pdf, mock_dirs, auth_client):
    with open(multi_page_pdf, "rb") as f:
        files = {"file": (multi_page_pdf.name, f, "application/pdf")}
        response = auth_client.post(
            "/api/pdf/split",
            files=files,
            data={"mode": "ranges", "ranges": "1-2,3-4"},
        )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert data["file_count"] == 2
    output_path = result_path(mock_dirs["output"], data)
    with zipfile.ZipFile(output_path) as zf:
        assert zf.namelist() == ["pages-001-002.pdf", "pages-003-004.pdf"]

def test_download_file(sample_pdf, mock_dirs, auth_client):
    # First generate the file
    with open(sample_pdf, "rb") as f:
        files = {"file": (sample_pdf.name, f, "application/pdf")}
        response = auth_client.post("/api/pdf/convert-to-word", files=files)

    payload = response.json()
    filename = payload["filename"]

    # Download it by its opaque token; the branded name is only the save-as label.
    response = auth_client.get(f"/api/download/{payload['download_token']}")
    assert response.status_code == 200
    assert response.headers["content-disposition"] == f'attachment; filename="{filename}"'

def test_download_file_not_found(mock_dirs, auth_client):
    response = auth_client.get("/api/download/nonexistent.file")
    assert response.status_code == 404


def test_api_heic_to_jpeg(sample_heic, mock_dirs, auth_client):
    """Test HEIC to JPEG conversion endpoint."""
    file_path = sample_heic

    with open(file_path, "rb") as f:
        files = {"file": (file_path.name, f, "image/heic")}
        data = {"quality": 90}
        response = auth_client.post("/api/image/heic-to-jpeg", files=files, data=data)

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert data["filename"].endswith(".jpg")

    # Verify file exists in mock output dir
    output_filename = data["filename"]
    assert result_path(mock_dirs["output"], data).exists()


def test_api_resize_image(sample_image_file, mock_dirs, auth_client):
    """Test image resizing endpoint."""
    with open(sample_image_file, "rb") as f:
        files = {"file": (sample_image_file.name, f, "image/jpeg")}
        data = {"mode": "dimensions", "width": 50, "height": 50}
        response = auth_client.post("/api/image/resize", files=files, data=data)

    assert response.status_code == 200
    resp_data = response.json()
    assert resp_data["status"] == "success"
    assert "forgefiles.org" in resp_data["filename"]
    assert result_path(mock_dirs["output"], resp_data).exists()


def test_api_crop_image(sample_image_file, mock_dirs, auth_client):
    """Test image cropping endpoint."""
    with open(sample_image_file, "rb") as f:
        files = {"file": (sample_image_file.name, f, "image/jpeg")}
        data = {"x": 10, "y": 10, "width": 30, "height": 30}
        response = auth_client.post("/api/image/crop", files=files, data=data)

    assert response.status_code == 200
    resp_data = response.json()
    assert resp_data["status"] == "success"
    assert "forgefiles.org" in resp_data["filename"]
    assert result_path(mock_dirs["output"], resp_data).exists()

def test_download_file_deletes_after_download(sample_pdf, mock_dirs, auth_client) -> None:
    """
    Verifies that a file is successfully served and then automatically deleted
    from the output directory after download.
    """
    # First generate the file
    with open(sample_pdf, "rb") as f:
        files = {"file": (sample_pdf.name, f, "application/pdf")}
        response = auth_client.post("/api/pdf/convert-to-word", files=files)
    
    assert response.status_code == 200
    payload = response.json()
    file_path = result_path(mock_dirs["output"], payload)

    assert file_path.exists()

    # Download it
    response = auth_client.get(f"/api/download/{payload['download_token']}")
    assert response.status_code == 200

    # Check if the file is deleted, along with its per-result directory
    assert not file_path.exists()
    assert not file_path.parent.exists()

    # ...and the token is retired, so replaying the URL 404s.
    replay = auth_client.get(f"/api/download/{payload['download_token']}")
    assert replay.status_code == 404


# ---------------------------------------------------------------------------
# /api/pdf/compress endpoint tests
# ---------------------------------------------------------------------------

def test_api_compress_pdf(sample_pdf, mock_dirs, auth_client):
    """Compress endpoint returns success and size statistics."""
    with open(sample_pdf, "rb") as f:
        files = {"file": (sample_pdf.name, f, "application/pdf")}
        data = {"level": "low"}
        response = auth_client.post("/api/pdf/compress", files=files, data=data)

    assert response.status_code == 200
    resp_data = response.json()
    assert resp_data["status"] == "success"
    assert "filename" in resp_data
    assert "original_size" in resp_data
    assert "compressed_size" in resp_data
    assert "reduction_pct" in resp_data
    assert result_path(mock_dirs["output"], resp_data).exists()


def test_api_compress_pdf_medium_level(sample_pdf, mock_dirs, auth_client):
    """Compress endpoint works with level='medium'."""
    with open(sample_pdf, "rb") as f:
        files = {"file": (sample_pdf.name, f, "application/pdf")}
        data = {"level": "medium"}
        response = auth_client.post("/api/pdf/compress", files=files, data=data)

    assert response.status_code == 200
    assert response.json()["status"] == "success"


def test_api_compress_pdf_public_no_auth_needed(sample_pdf, mock_dirs):
    """Compress endpoint is public — no API key or login required."""
    from fastapi.testclient import TestClient
    plain_client = TestClient(app)
    with patch("main.UPLOAD_DIR", mock_dirs["upload"]), patch("main.OUTPUT_DIR", mock_dirs["output"]):
        with open(sample_pdf, "rb") as f:
            files = {"file": (sample_pdf.name, f, "application/pdf")}
            response = plain_client.post("/api/pdf/compress", files=files, data={"level": "low"})
    assert response.status_code == 200
    assert response.json()["status"] == "success"


def test_api_compress_pdf_with_password(locked_pdf, mock_dirs, auth_client):
    """Compress endpoint decrypts and compresses a password-protected PDF."""
    file_path = locked_pdf["path"]
    password = locked_pdf["password"]
    with open(file_path, "rb") as f:
        files = {"file": (file_path.name, f, "application/pdf")}
        data = {"level": "low", "password": password}
        response = auth_client.post("/api/pdf/compress", files=files, data=data)

    assert response.status_code == 200
    assert response.json()["status"] == "success"


def test_api_compress_pdf_path_traversal_sanitized(sample_pdf, mock_dirs, auth_client):
    """Filename with path traversal components is sanitized before saving."""
    with open(sample_pdf, "rb") as f:
        files = {"file": ("../../evil.pdf", f, "application/pdf")}
        data = {"level": "low"}
        response = auth_client.post("/api/pdf/compress", files=files, data=data)

    assert response.status_code == 200
    filename = response.json()["filename"]
    # Should not contain directory separators
    assert "/" not in filename
    assert "\\" not in filename


# ---------------------------------------------------------------------------
# /api/pdf/extract-text endpoint tests
# ---------------------------------------------------------------------------

def test_api_extract_text(sample_pdf, mock_dirs, auth_client):
    """Extract text endpoint returns a downloadable TXT file."""
    with open(sample_pdf, "rb") as f:
        files = {"file": (sample_pdf.name, f, "application/pdf")}
        data = {"preserve_formatting": "true", "use_ocr": "false"}
        response = auth_client.post("/api/pdf/extract-text", files=files, data=data)

    assert response.status_code == 200
    resp_data = response.json()
    assert resp_data["status"] == "success"
    assert resp_data["filename"].endswith("_forgefiles.org.txt")
    output_path = result_path(mock_dirs["output"], resp_data)
    assert output_path.exists()
    assert "Hello, this is a test PDF." in output_path.read_text(encoding="utf-8")


def test_api_extract_text_public_no_auth_needed(sample_pdf, mock_dirs):
    """Extract text endpoint is public — no API key or login required."""
    from fastapi.testclient import TestClient
    plain_client = TestClient(app)
    with patch("main.UPLOAD_DIR", mock_dirs["upload"]), patch("main.OUTPUT_DIR", mock_dirs["output"]):
        with open(sample_pdf, "rb") as f:
            files = {"file": (sample_pdf.name, f, "application/pdf")}
            response = plain_client.post("/api/pdf/extract-text", files=files, data={"use_ocr": "false"})
    assert response.status_code == 200
    assert response.json()["status"] == "success"


def test_api_extract_text_path_traversal_sanitized(sample_pdf, mock_dirs, auth_client):
    """Path traversal in filename is sanitized for extract-text endpoint."""
    with open(sample_pdf, "rb") as f:
        files = {"file": ("../../evil.pdf", f, "application/pdf")}
        response = auth_client.post("/api/pdf/extract-text", files=files, data={"use_ocr": "false"})

    assert response.status_code == 200
    filename = response.json()["filename"]
    assert "/" not in filename
    assert "\\" not in filename


def test_api_ocr_pdf_creates_searchable_pdf(scanned_like_pdf, mock_dirs, auth_client, monkeypatch):
    import scripts.ocr_engine as ocr_engine

    class FakeEngine:
        def recognize(self, image):
            return [{
                "text": "Endpoint searchable phrase",
                "bbox": [[200, 200], [1400, 200], [1400, 360], [200, 360]],
            }]

    monkeypatch.setattr(ocr_engine, "get_ocr_engine", lambda *a, **k: FakeEngine())
    with open(scanned_like_pdf, "rb") as f:
        files = {"file": (scanned_like_pdf.name, f, "application/pdf")}
        response = auth_client.post("/api/pdf/ocr", files=files)

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    output_path = result_path(mock_dirs["output"], data)
    with pikepdf.open(output_path) as pdf:
        assert len(pdf.pages) == 1


# ---------------------------------------------------------------------------
# /api/workflow/execute endpoint tests
# ---------------------------------------------------------------------------

def test_api_workflow_invalid_steps_json(sample_pdf, mock_dirs, auth_client):
    """Workflow endpoint returns 400 for invalid JSON in steps field."""
    with open(sample_pdf, "rb") as f:
        files = {"file": (sample_pdf.name, f, "application/pdf")}
        data = {"steps": "not valid json"}
        response = auth_client.post("/api/workflow/execute", files=files, data=data)

    assert response.status_code == 400


@pytest.mark.parametrize("steps", ["5", '"x"', '{"a": 1}', "[]", "[1, 2]", "null"])
def test_api_workflow_non_list_or_malformed_steps_is_400(sample_pdf, mock_dirs, auth_client, steps):
    """A `steps` value that is valid JSON but not a non-empty list of objects
    must 400 up front rather than crash mid-SSE-stream (#79)."""
    with open(sample_pdf, "rb") as f:
        files = {"file": (sample_pdf.name, f, "application/pdf")}
        data = {"steps": steps}
        response = auth_client.post("/api/workflow/execute", files=files, data=data)

    assert response.status_code == 400


def test_api_workflow_too_many_steps_is_400(sample_pdf, mock_dirs, auth_client):
    """A step list beyond MAX_WORKFLOW_STEPS must 400, not run unbounded (#79)."""
    import json

    from main import MAX_WORKFLOW_STEPS

    steps = json.dumps([{"type": "pdf_to_word"}] * (MAX_WORKFLOW_STEPS + 1))

    with open(sample_pdf, "rb") as f:
        files = {"file": (sample_pdf.name, f, "application/pdf")}
        data = {"steps": steps}
        response = auth_client.post("/api/workflow/execute", files=files, data=data)

    assert response.status_code == 400


def test_api_workflow_single_pdf_to_word_step(sample_pdf, mock_dirs, auth_client):
    """Workflow with a single pdf_to_word step streams events and completes."""
    import json

    steps = json.dumps([{"type": "pdf_to_word", "config": {"use_ai": False}, "label": "Convert to Word"}])

    with open(sample_pdf, "rb") as f:
        files = {"file": (sample_pdf.name, f, "application/pdf")}
        data = {"steps": steps}
        response = auth_client.post("/api/workflow/execute", files=files, data=data)

    assert response.status_code == 200
    # SSE response — check that a 'complete' event is present in the body
    body = response.text
    assert "complete" in body


def test_api_workflow_unknown_step_type(sample_pdf, mock_dirs, auth_client):
    """Workflow with an unknown step type sends an error event."""
    import json

    steps = json.dumps([{"type": "does_not_exist", "config": {}}])

    with open(sample_pdf, "rb") as f:
        files = {"file": (sample_pdf.name, f, "application/pdf")}
        data = {"steps": steps}
        response = auth_client.post("/api/workflow/execute", files=files, data=data)

    assert response.status_code == 200
    body = response.text
    assert "error" in body


def test_api_workflow_compress_step(sample_pdf, mock_dirs, auth_client):
    """Workflow with a compress_pdf step completes successfully."""
    import json

    steps = json.dumps([{"type": "compress_pdf", "config": {"level": "low"}, "label": "Compress"}])

    with open(sample_pdf, "rb") as f:
        files = {"file": (sample_pdf.name, f, "application/pdf")}
        data = {"steps": steps}
        response = auth_client.post("/api/workflow/execute", files=files, data=data)

    assert response.status_code == 200
    assert "complete" in response.text


def test_api_workflow_extract_text_step(sample_pdf, mock_dirs, auth_client):
    """Workflow with an extract_text step completes successfully."""
    import json

    steps = json.dumps([
        {
            "type": "extract_text",
            "config": {"preserve_formatting": True, "use_ocr": False},
            "label": "Extract Text",
        }
    ])

    with open(sample_pdf, "rb") as f:
        files = {"file": (sample_pdf.name, f, "application/pdf")}
        data = {"steps": steps}
        response = auth_client.post("/api/workflow/execute", files=files, data=data)

    assert response.status_code == 200
    assert "complete" in response.text
    assert "_forgefiles.org.txt" in response.text


def test_api_workflow_resize_image_step(sample_image_file, mock_dirs, auth_client):
    """Workflow with a resize_image step completes successfully."""
    import json

    steps = json.dumps([{"type": "resize_image", "config": {"mode": "percentage", "percentage": 50}}])

    with open(sample_image_file, "rb") as f:
        files = {"file": (sample_image_file.name, f, "image/jpeg")}
        data = {"steps": steps}
        response = auth_client.post("/api/workflow/execute", files=files, data=data)

    assert response.status_code == 200
    assert "complete" in response.text


def test_api_workflow_crop_image_step(sample_image_file, mock_dirs, auth_client):
    """Workflow with a crop_image step completes successfully."""
    import json

    steps = json.dumps([{"type": "crop_image", "config": {"x": 0, "y": 0, "width": 50, "height": 50}}])

    with open(sample_image_file, "rb") as f:
        files = {"file": (sample_image_file.name, f, "image/jpeg")}
        data = {"steps": steps}
        response = auth_client.post("/api/workflow/execute", files=files, data=data)

    assert response.status_code == 200
    assert "complete" in response.text


# ---------------------------------------------------------------------------
# delete_file_after_download unit tests
# ---------------------------------------------------------------------------

def test_delete_file_after_download_removes_file(tmp_path):
    """delete_file_after_download removes the whole per-result directory."""
    from main import delete_file_after_download
    result_dir = tmp_path / "sometoken"
    result_dir.mkdir()
    test_file = result_dir / "to_delete.txt"
    test_file.write_text("hello")

    delete_file_after_download("sometoken", test_file)

    assert not test_file.exists()
    assert not result_dir.exists()


def test_delete_file_after_download_missing_file_is_silent(tmp_path):
    """delete_file_after_download does not raise when file does not exist."""
    from main import delete_file_after_download
    missing = tmp_path / "gone" / "nonexistent.txt"

    # Should not raise
    delete_file_after_download("gone", missing)


# ---------------------------------------------------------------------------
# Path traversal sanitization for other endpoints
# ---------------------------------------------------------------------------

def test_api_remove_password_path_traversal_sanitized(locked_pdf, mock_dirs, auth_client):
    """Path traversal in filename is sanitized for remove-password endpoint."""
    file_path = locked_pdf["path"]
    password = locked_pdf["password"]

    with open(file_path, "rb") as f:
        files = {"file": ("../../evil.pdf", f, "application/pdf")}
        data = {"password": password}
        response = auth_client.post("/api/pdf/remove-password", files=files, data=data)

    assert response.status_code == 200
    filename = response.json()["filename"]
    assert "/" not in filename
    assert "\\" not in filename
