import zipfile
from pathlib import Path

import pikepdf
import pytest
from PIL import Image
from reportlab.pdfgen import canvas

from scripts.pdf_utils import (
    add_watermark,
    merge_pdfs,
    pdf_to_images_zip,
    sign_pdf,
)


@pytest.fixture
def two_pdfs(tmp_path):
    paths = []
    for i in range(2):
        p = tmp_path / f"src_{i}.pdf"
        c = canvas.Canvas(str(p))
        c.drawString(100, 750, f"file {i} page 1")
        c.showPage()
        c.drawString(100, 750, f"file {i} page 2")
        c.save()
        paths.append(p)
    return paths


@pytest.fixture
def signature_png(tmp_path):
    p = tmp_path / "sig.png"
    Image.new("RGBA", (200, 80), (0, 0, 0, 0)).save(p, "PNG")
    return p


def test_merge_pdfs_combines_page_counts(two_pdfs, tmp_path):
    out_str = merge_pdfs([str(p) for p in two_pdfs], str(tmp_path))
    out = Path(out_str)
    assert out.exists()
    with pikepdf.open(out) as pdf:
        assert len(pdf.pages) == 4


def test_merge_pdfs_requires_two(sample_pdf, tmp_path):
    with pytest.raises(ValueError):
        merge_pdfs([str(sample_pdf)], str(tmp_path))


def test_add_watermark_creates_valid_pdf(multi_page_pdf, tmp_path):
    out_str = add_watermark(str(multi_page_pdf), str(tmp_path), "DRAFT")
    out = Path(out_str)
    assert out.exists()
    with pikepdf.open(out) as pdf:
        assert len(pdf.pages) == 4


def test_add_watermark_rejects_empty_text(sample_pdf, tmp_path):
    with pytest.raises(ValueError):
        add_watermark(str(sample_pdf), str(tmp_path), "  ")


def test_add_watermark_rejects_bad_position(sample_pdf, tmp_path):
    with pytest.raises(ValueError):
        add_watermark(str(sample_pdf), str(tmp_path), "X", position="sideways")


@pytest.mark.parametrize("position", ["top", "center", "bottom"])
def test_horizontal_watermark_uses_exact_text_width(multi_page_pdf, tmp_path, position):
    """top/center/bottom watermarks must be centered using the label's real
    glyph width (fitz.get_text_length), not a per-character estimate that
    drifts further off the longer the text or the larger the font gets."""
    import fitz

    text = "CONFIDENTIAL"
    font_size = 36
    out = Path(add_watermark(str(multi_page_pdf), str(tmp_path), text, position=position, font_size=font_size))
    doc = fitz.open(str(out))
    try:
        page = doc[0]
        expected_width = fitz.get_text_length(text, fontname="helv", fontsize=font_size)
        expected_x = max(10, (page.rect.width - expected_width) / 2)
        spans = [
            span for block in page.get_text("dict")["blocks"]
            for line in block.get("lines", [])
            for span in line["spans"]
            if span["text"] == text
        ]
        assert spans, f"watermark text not found for position={position}"
        actual_x = spans[0]["bbox"][0]
        assert abs(actual_x - expected_x) < 1.0, (
            f"position={position}: x={actual_x} expected~={expected_x}"
        )
    finally:
        doc.close()


@pytest.mark.parametrize("position", ["top", "center", "bottom"])
def test_horizontal_watermark_shrinks_to_fit_instead_of_clipping(tmp_path, position):
    """A watermark whose glyph width exceeds the page must shrink to fit rather
    than being silently clipped by fitz.Page.insert_text() (which drops trailing
    characters with no exception or return-value signal)."""
    import fitz

    text = "CONFIDENTIAL - DO NOT DISTRIBUTE THIS FILE"
    narrow_pdf = tmp_path / "narrow.pdf"
    doc = fitz.open()
    doc.new_page(width=300, height=200)
    doc.save(str(narrow_pdf))
    doc.close()

    out = Path(add_watermark(str(narrow_pdf), str(tmp_path), text, position=position, font_size=0))
    doc = fitz.open(str(out))
    try:
        full_text = doc[0].get_text()
    finally:
        doc.close()
    assert "DISTRIBUTE THIS FILE" in full_text, (
        f"position={position}: watermark was clipped, got {full_text!r}"
    )


@pytest.mark.parametrize("position", ["top", "center", "bottom"])
def test_horizontal_watermark_too_long_even_at_floor_size_raises(tmp_path, position):
    """When the text still doesn't fit even at the minimum (8pt) font size, fail
    loudly with a ValueError rather than letting insert_text() silently clip it."""
    import fitz

    narrow_pdf = tmp_path / "narrow.pdf"
    doc = fitz.open()
    doc.new_page(width=100, height=100)
    doc.save(str(narrow_pdf))
    doc.close()

    with pytest.raises(ValueError, match="too long to fit"):
        add_watermark(str(narrow_pdf), str(tmp_path), "A" * 500, position=position, font_size=0)


@pytest.mark.parametrize("position", ["top", "center", "bottom"])
def test_horizontal_watermark_on_a_page_narrower_than_the_margin_raises(tmp_path, position):
    """A page <=20pt wide leaves no room for even the fixed left/right margin, so
    `available_width` is nonpositive; this must still raise rather than silently
    inserting (and clipping) the text at x=10 regardless of fit."""
    import fitz

    tiny_pdf = tmp_path / "tiny.pdf"
    doc = fitz.open()
    doc.new_page(width=15, height=100)
    doc.save(str(tiny_pdf))
    doc.close()

    with pytest.raises(ValueError, match="too long to fit"):
        add_watermark(str(tiny_pdf), str(tmp_path), "HELLO", position=position, font_size=0)


def test_pdf_to_images_zip_one_image_per_page(multi_page_pdf, tmp_path):
    result = pdf_to_images_zip(str(multi_page_pdf), str(tmp_path), dpi=72)
    out = Path(result["output_path"])
    assert out.exists()
    assert result["page_count"] == 4
    with zipfile.ZipFile(out) as zf:
        names = zf.namelist()
    assert len(names) == 4
    assert all(n.endswith(".jpg") for n in names)


def test_pdf_to_images_zip_rejects_bad_dpi(sample_pdf, tmp_path):
    with pytest.raises(ValueError):
        pdf_to_images_zip(str(sample_pdf), str(tmp_path), dpi=10)
    with pytest.raises(ValueError):
        pdf_to_images_zip(str(sample_pdf), str(tmp_path), dpi=1000)


def test_sign_pdf_inserts_image(multi_page_pdf, signature_png, tmp_path):
    import fitz

    with fitz.open(str(multi_page_pdf)) as src:
        page1_images_before = len(src[0].get_images(full=True))

    out_str = sign_pdf(
        str(multi_page_pdf), str(signature_png), str(tmp_path),
        page=1, x=0.65, y=0.85, width=0.2,
    )
    out = Path(out_str)
    assert out.exists()
    with fitz.open(str(out)) as result:
        assert len(result) == 4
        assert len(result[0].get_images(full=True)) == page1_images_before + 1


def test_sign_pdf_rejects_bad_page(sample_pdf, signature_png, tmp_path):
    with pytest.raises(ValueError):
        sign_pdf(str(sample_pdf), str(signature_png), str(tmp_path), page=99)


def test_sign_pdf_rejects_missing_signature(sample_pdf, tmp_path):
    with pytest.raises(ValueError):
        sign_pdf(str(sample_pdf), str(tmp_path / "nope.png"), str(tmp_path))
