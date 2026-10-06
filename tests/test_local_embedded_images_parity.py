"""On-device 'extract embedded images' equals the server's, byte for byte."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import fitz
import pytest

from conftest_parity import NODE, make_pdf, run_local
from scripts.pdf_utils import pdf_to_images_zip

pytestmark = pytest.mark.skipif(NODE is None, reason="node not available")


def _jpeg(color: tuple, size=(120, 90)) -> bytes:
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, *size), False)
    pix.set_rect(pix.irect, color)
    return pix.tobytes("jpeg")


def _pdf_with_images(path: Path) -> Path:
    doc = fitz.open()
    for i, color in enumerate([(200, 30, 30), (30, 200, 30)]):
        page = doc.new_page()
        page.insert_image(fitz.Rect(50, 50, 250, 200), stream=_jpeg(color))
    doc.save(str(path))
    doc.close()
    return path


def _members(data: bytes) -> dict[str, bytes]:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        return {n: zf.read(n) for n in sorted(zf.namelist())}


def test_embedded_extraction_matches_the_server(tmp_path: Path) -> None:
    pdf = _pdf_with_images(tmp_path / "in.pdf")
    result, out = run_local(
        tmp_path, "/api/pdf/to-images",
        [{"field": "file", "path": str(pdf), "name": "Pics.pdf", "type": "application/pdf"}],
        {"mode": "embedded"},
    )
    assert result["status"] == 200, result
    assert result["message"] == "Extracted 2 embedded image(s)"
    srv_dir = tmp_path / "srv"
    srv_dir.mkdir()
    srv = pdf_to_images_zip(str(pdf), str(srv_dir), mode="embedded")
    assert srv["page_count"] == 2
    local = _members(out.read_bytes())
    server = _members(Path(srv["output_path"]).read_bytes())
    assert [n.replace("Pics", "in") for n in local] == list(server)
    assert list(local.values()) == list(server.values())


def test_pdf_without_images_is_rejected(tmp_path: Path) -> None:
    pdf = make_pdf(tmp_path / "plain.pdf", pages=2)
    result, _ = run_local(
        tmp_path, "/api/pdf/to-images",
        [{"field": "file", "path": str(pdf), "name": "plain.pdf", "type": "application/pdf"}],
        {"mode": "embedded"},
    )
    assert result["status"] == 400
    with pytest.raises(ValueError):
        pdf_to_images_zip(str(pdf), str(tmp_path), mode="embedded")


def test_unknown_mode_is_rejected(tmp_path: Path) -> None:
    pdf = make_pdf(tmp_path / "plain.pdf", pages=1)
    with pytest.raises(ValueError):
        pdf_to_images_zip(str(pdf), str(tmp_path), mode="bogus")
