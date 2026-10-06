"""Regression tests for review findings on the PDF feature-gap PR."""

from __future__ import annotations

import io
import os
import struct
import zipfile
from pathlib import Path

import fitz
import pikepdf
import pytest

from conftest_parity import NODE, make_pdf, run_local
from scripts.pdf_utils import add_watermark, organize_pdf, pdf_to_images_zip

pytestmark = pytest.mark.skipif(NODE is None, reason="node not available")


def _file(pdf: Path, name="d.pdf", field="file", type_="application/pdf"):
    return {"field": field, "path": str(pdf), "name": name, "type": type_}


def _texts(path: Path) -> list[str]:
    return [p.get_text().strip() for p in fitz.open(str(path))]


# ---- Remove Pages: complement is computed inside the organize operation ----

def test_remove_pages_matches_the_server(tmp_path: Path) -> None:
    pdf = make_pdf(tmp_path / "in.pdf", pages=6)
    result, out = run_local(tmp_path, "/api/pdf/organize", [_file(pdf)], {"remove_pages": "2,4-5"})
    assert result["status"] == 200, result
    srv = Path(organize_pdf(str(pdf), str(tmp_path), [], remove_pages="2,4-5"))
    assert _texts(out) == _texts(srv) == ["Page 1", "Page 3", "Page 6"]


@pytest.mark.parametrize("spec", ["1-3", "9", "x"])
def test_remove_pages_errors_match(tmp_path: Path, spec: str) -> None:
    pdf = make_pdf(tmp_path / "in.pdf", pages=3)
    result, _ = run_local(tmp_path, "/api/pdf/organize", [_file(pdf)], {"remove_pages": spec})
    assert result["status"] == 400, (spec, result)
    with pytest.raises(ValueError):
        organize_pdf(str(pdf), str(tmp_path), [], remove_pages=spec)


# ---- Watermark resource bounds ----

def _png_header(width: int, height: int) -> bytes:
    return b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR" + struct.pack(">II", width, height) + b"\x08\x06\x00\x00\x00" + b"\0\0\0\0"


def test_oversized_logo_dimensions_are_rejected_before_decoding(tmp_path: Path) -> None:
    from PIL import Image

    big = tmp_path / "wide.png"
    Image.new("L", (9000, 2)).save(big)  # tiny on disk, side > 8000
    pdf = make_pdf(tmp_path / "in.pdf", pages=1)
    with pytest.raises(ValueError, match="too large"):
        add_watermark(str(pdf), str(tmp_path), "", logo_bytes=big.read_bytes())
    local = run_local(
        tmp_path, "/api/pdf/watermark", [_file(pdf), _file(big, "wide.png", "logo", "image/png")], {"position": "center"},
    )[0]
    assert local["status"] == 400, local


def test_a_huge_page_with_tiling_fails_fast(tmp_path: Path) -> None:
    pdf = tmp_path / "huge.pdf"
    doc = pikepdf.new()
    doc.add_blank_page(page_size=(100, 100))
    doc.pages[0].MediaBox = [0, 0, 5_000_000, 5_000_000]
    doc.save(pdf)
    with pytest.raises(ValueError, match="tiled too many times"):
        add_watermark(str(pdf), str(tmp_path), "X", tile=True, font_size=8)


# ---- Embedded images: only those a page references ----

def test_orphaned_images_are_not_extracted(tmp_path: Path) -> None:
    doc = fitz.open()
    for color in [(255, 0, 0), (0, 255, 0)]:
        pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 80, 80), False)
        pix.set_rect(pix.irect, color)
        doc.new_page().insert_image(fitz.Rect(10, 10, 110, 110), stream=pix.tobytes("jpeg"))
    doc.delete_page(1)
    pdf = tmp_path / "orphan.pdf"
    doc.save(str(pdf), garbage=0)  # the deleted page's image object stays in the file
    result, out = run_local(tmp_path, "/api/pdf/to-images", [_file(pdf)], {"mode": "embedded"})
    assert result["status"] == 200, result
    assert result["message"] == "Extracted 1 embedded image(s)"
    srv = pdf_to_images_zip(str(pdf), str(tmp_path), mode="embedded")
    assert srv["page_count"] == 1
    assert len(zipfile.ZipFile(io.BytesIO(out.read_bytes())).namelist()) == 1


# ---- Compress: unsupported encodings offer the server instead of a false "no saving" ----

def test_flate_only_images_hand_over_to_the_server(tmp_path: Path) -> None:
    doc = fitz.open()
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 120, 120), False)
    pix.samples_mv[:] = os.urandom(len(pix.samples_mv))  # incompressible, > 20 KB, stored as Flate
    doc.new_page().insert_image(fitz.Rect(10, 10, 200, 200), stream=pix.tobytes("png"))
    pdf = tmp_path / "flate.pdf"
    doc.save(str(pdf))
    result, _ = run_local(tmp_path, "/api/pdf/compress", [_file(pdf)], {"mode": "images"})
    assert result["status"] == 499, result  # consent to upload was asked (and declined by the harness)
    assert result["asked"]["tool"]


# ---- Review round 2 ----

def test_oversized_pages_are_not_searched_quadratically() -> None:
    from scripts.pdf_utils import _split_groups_by_size

    calls = []

    def build(indices):
        calls.append(len(indices))
        return b"x" * (200 * len(indices))

    parts = _split_groups_by_size(40, 100, build)  # every single page is already over the limit
    assert [p[0] for p in parts] == [[i] for i in range(40)]
    assert calls == [1] * 40  # one serialisation per page, no half-document candidates


def test_extraction_rejects_images_declaring_a_huge_raster(tmp_path: Path) -> None:
    doc = pikepdf.new()
    page = doc.add_blank_page(page_size=(200, 200))
    img = pikepdf.Stream(doc, b"\x00")
    img.Type, img.Subtype = pikepdf.Name.XObject, pikepdf.Name.Image
    img.Width, img.Height, img.BitsPerComponent = 20000, 20000, 8
    img.ColorSpace = pikepdf.Name.DeviceGray
    page.Resources = pikepdf.Dictionary(XObject=pikepdf.Dictionary(Im0=img))
    page.Contents = doc.make_stream(b"q 100 0 0 100 0 0 cm /Im0 Do Q")
    pdf = tmp_path / "huge_img.pdf"
    doc.save(pdf)
    with pytest.raises(ValueError, match="too large"):
        pdf_to_images_zip(str(pdf), str(tmp_path), mode="embedded")


def test_extraction_budgets_decoded_bytes_not_just_pixels(tmp_path: Path) -> None:
    doc = pikepdf.new()
    page = doc.add_blank_page(page_size=(200, 200))
    img = pikepdf.Stream(doc, b"\x00")
    img.Type, img.Subtype = pikepdf.Name.XObject, pikepdf.Name.Image
    img.Width, img.Height, img.BitsPerComponent = 9000, 9000, 16  # 81 MP, but ~486 MB decoded as RGB
    img.ColorSpace = pikepdf.Name.DeviceRGB
    page.Resources = pikepdf.Dictionary(XObject=pikepdf.Dictionary(Im0=img))
    page.Contents = doc.make_stream(b"q 100 0 0 100 0 0 cm /Im0 Do Q")
    pdf = tmp_path / "wide_gamut.pdf"
    doc.save(pdf)
    with pytest.raises(ValueError, match="too large"):
        pdf_to_images_zip(str(pdf), str(tmp_path), mode="embedded")


def test_remove_pages_message_matches_between_engines(tmp_path: Path) -> None:
    pdf = make_pdf(tmp_path / "in.pdf", pages=3)
    result, _ = run_local(tmp_path, "/api/pdf/organize", [_file(pdf)], {"remove_pages": "2"})
    assert result["message"] == "Pages removed"


@pytest.mark.parametrize("rotation", [90, 180, 270])
@pytest.mark.parametrize("position", ["center", "top"])
def test_logo_is_placed_on_the_displayed_page_for_rotated_pages(tmp_path: Path, rotation: int, position: str) -> None:
    from PIL import Image

    logo = tmp_path / "logo.png"
    Image.new("RGB", (200, 100), (255, 0, 0)).save(logo)
    pdf = make_pdf(tmp_path / "in.pdf", pages=1, rotations={0: rotation})
    out = Path(add_watermark(str(pdf), str(tmp_path), "", position=position, opacity=1.0,
                             logo_bytes=logo.read_bytes(), logo_scale=0.3))
    page = fitz.open(str(out))[0]
    pix = page.get_pixmap(dpi=36)
    xs, ys = [], []
    for y in range(pix.height):
        for x in range(pix.width):
            r, g, b = pix.pixel(x, y)[:3]
            if r > 200 and g < 80 and b < 80:
                xs.append(x)
                ys.append(y)
    assert xs, "logo not visible"
    w, h = max(xs) - min(xs) + 1, max(ys) - min(ys) + 1
    assert abs(w / h - 2) < 0.15, (w, h)  # upright 2:1 on screen, not turned or squashed
    cx = (min(xs) + max(xs)) / 2 * 2          # back to points (36 dpi = half scale)
    cy = (min(ys) + max(ys)) / 2 * 2
    assert abs(cx - page.rect.width / 2) < 4, (cx, page.rect.width)
    expected_cy = page.rect.height * (0.5 if position == "center" else 0.1)
    assert abs(cy - max(expected_cy, h)) < 8 or position == "top" and cy < page.rect.height * 0.25, (cy, expected_cy)
