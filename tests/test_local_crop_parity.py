"""Crop PDF: on-device and server set the same CropBox, including rotated pages."""

from __future__ import annotations

from pathlib import Path

import fitz
import pikepdf
import pytest

from conftest_parity import NODE, make_pdf, run_local
from scripts.pdf_utils import crop_pdf

pytestmark = pytest.mark.skipif(NODE is None, reason="node not available")

SIZES = [(600, 800), (600, 800), (600, 800), (600, 800)]


def _local(tmp_path: Path, pdf: Path, **fields):
    return run_local(
        tmp_path, "/api/pdf/crop",
        [{"field": "file", "path": str(pdf), "name": "My Doc.pdf", "type": "application/pdf"}], fields,
    )


def _boxes(path: Path) -> list[list[float]]:
    with pikepdf.open(path) as pdf:
        return [[float(v) for v in p.cropbox] for p in pdf.pages]


def _same(a, b, tol=0.01):
    return all(abs(x - y) <= tol for pa, pb in zip(a, b) for x, y in zip(pa, pb)) and len(a) == len(b)


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_cropbox_matches_the_server_for_every_rotation(tmp_path: Path, rotation: int) -> None:
    pdf = make_pdf(tmp_path / "in.pdf", sizes=SIZES, rotations={0: rotation, 1: rotation})
    fields = {"top": "10", "bottom": "5", "left": "20", "right": "2.5"}
    result, out = _local(tmp_path, pdf, **fields)
    assert result["status"] == 200, result
    assert result["message"] == "PDF cropped"
    assert result["filename"] == "My Doc_forgefiles.org.pdf"
    srv = Path(crop_pdf(str(pdf), str(tmp_path), 10, 5, 20, 2.5))
    assert _same(_boxes(out), _boxes(srv)), (_boxes(out), _boxes(srv))


def test_margins_are_applied_to_the_displayed_page(tmp_path: Path) -> None:
    """Cropping 50% off the displayed top halves the displayed height whatever the rotation."""
    pdf = make_pdf(tmp_path / "in.pdf", sizes=[(600, 800)], rotations={0: 90})
    _, out = _local(tmp_path, pdf, top="50")
    page = fitz.open(str(out))[0]
    assert abs(page.rect.height - 300) < 0.01 and abs(page.rect.width - 800) < 0.01  # 90deg: displayed 800x600


def test_page_selection_limits_the_crop(tmp_path: Path) -> None:
    pdf = make_pdf(tmp_path / "in.pdf", sizes=SIZES)
    result, out = _local(tmp_path, pdf, top="10", pages="2-3")
    assert result["status"] == 200, result
    srv = Path(crop_pdf(str(pdf), str(tmp_path), top=10, pages="2-3"))
    boxes = _boxes(out)
    assert _same(boxes, _boxes(srv))
    assert boxes[0] == [0, 0, 600, 800] and boxes[1] != [0, 0, 600, 800] and boxes[3] == [0, 0, 600, 800]


@pytest.mark.parametrize("bad", [
    {}, {"top": "-1"}, {"top": "91"}, {"top": "50", "bottom": "50"}, {"left": "60", "right": "40"}, {"top": "abc"},
])
def test_invalid_margins_are_rejected_by_both(tmp_path: Path, bad: dict) -> None:
    pdf = make_pdf(tmp_path / "in.pdf", pages=1)
    result, _ = _local(tmp_path, pdf, **bad)
    assert result["status"] == 400, (bad, result)
    with pytest.raises(ValueError):
        crop_pdf(str(pdf), str(tmp_path), *[bad.get(k, 0) for k in ("top", "bottom", "left", "right")])
