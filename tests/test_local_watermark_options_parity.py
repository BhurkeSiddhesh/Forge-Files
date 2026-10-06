"""Watermark colour / size / tile / layer: on-device and server agree."""

from __future__ import annotations

from pathlib import Path

import fitz
import pytest

from conftest_parity import NODE, make_pdf, run_local
from scripts.pdf_utils import add_watermark

pytestmark = pytest.mark.skipif(NODE is None, reason="node not available")


def _local(tmp_path: Path, pdf: Path, **fields):
    f = {"text": "DRAFT", **fields}
    return run_local(
        tmp_path, "/api/pdf/watermark",
        [{"field": "file", "path": str(pdf), "name": "d.pdf", "type": "application/pdf"}], f,
    )


def _server(tmp_path: Path, pdf: Path, **kw) -> Path:
    d = tmp_path / "server"
    d.mkdir(exist_ok=True)
    return Path(add_watermark(str(pdf), str(d), "DRAFT", **kw))


def _covered_pdf(path: Path) -> Path:
    """A page whose content is an opaque white rectangle over the whole page."""
    doc = fitz.open()
    page = doc.new_page(width=300, height=300)
    page.draw_rect(page.rect, color=(1, 1, 1), fill=(1, 1, 1))
    doc.save(str(path))
    doc.close()
    return path


def _non_white(path: Path) -> int:
    pix = fitz.open(str(path))[0].get_pixmap(dpi=36)
    return sum(1 for i in range(0, len(pix.samples), pix.n) if pix.samples[i:i + 3] != b"\xff\xff\xff")


def _span_color(path: Path) -> int:
    for block in fitz.open(str(path))[0].get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            for span in line["spans"]:
                if "DRAFT" in span["text"]:
                    return span["color"]
    raise AssertionError("watermark text not found")


def test_colour_and_size_match_the_server(tmp_path: Path) -> None:
    pdf = make_pdf(tmp_path / "in.pdf", pages=1)
    result, out = _local(tmp_path, pdf, color="#ff0000", font_size="40", position="center")
    assert result["status"] == 200, result
    server = _server(tmp_path, pdf, color="#ff0000", font_size=40, position="center")
    assert _span_color(out) == _span_color(server) == 0xFF0000


@pytest.mark.parametrize("position", ["diagonal", "center"])
def test_tiling_repeats_the_text_on_both_engines(tmp_path: Path, position: str) -> None:
    pdf = make_pdf(tmp_path / "in.pdf", pages=1)
    result, out = _local(tmp_path, pdf, tile="true", font_size="24", position=position)
    assert result["status"] == 200, result
    server = _server(tmp_path, pdf, tile=True, font_size=24, position=position)
    n_local = fitz.open(str(out))[0].get_text().count("DRAFT")
    n_server = fitz.open(str(server))[0].get_text().count("DRAFT")
    assert n_local > 3 and n_server > 3
    assert abs(n_local - n_server) <= 2


def test_under_layer_hides_behind_content_and_over_does_not(tmp_path: Path) -> None:
    pdf = _covered_pdf(tmp_path / "cover.pdf")
    for layer, visible in (("over", True), ("under", False)):
        result, out = _local(tmp_path, pdf, layer=layer, color="#000000")
        assert result["status"] == 200, result
        srv = _server(tmp_path, pdf, layer=layer, color="#000000")
        assert (_non_white(out) > 0) is visible, ("local", layer)
        assert (_non_white(srv) > 0) is visible, ("server", layer)


@pytest.mark.parametrize("bad", [{"color": "red"}, {"font_size": "5"}, {"layer": "middle"}])
def test_invalid_options_are_rejected(tmp_path: Path, bad: dict) -> None:
    pdf = make_pdf(tmp_path / "in.pdf", pages=1)
    result, _ = _local(tmp_path, pdf, **bad)
    assert result["status"] == 400
    kw = {"color": bad.get("color", "#808080"), "font_size": int(bad.get("font_size", 0)), "layer": bad.get("layer", "over")}
    with pytest.raises(ValueError):
        _server(tmp_path, pdf, **kw)
