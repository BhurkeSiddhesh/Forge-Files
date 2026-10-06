"""Logo watermark: on-device and server place the same logo in the same spot."""

from __future__ import annotations

import io
from pathlib import Path

import fitz
import pytest
from PIL import Image

from conftest_parity import NODE, make_pdf, run_local
from scripts.pdf_utils import add_watermark

pytestmark = pytest.mark.skipif(NODE is None, reason="node not available")


def _logo(path: Path, fmt="PNG", size=(200, 100), color=(255, 0, 0)) -> Path:
    Image.new("RGB", size, color).save(path, format=fmt)
    return path


def _local(tmp_path: Path, pdf: Path, logo: Path, **fields):
    return run_local(
        tmp_path, "/api/pdf/watermark",
        [
            {"field": "file", "path": str(pdf), "name": "d.pdf", "type": "application/pdf"},
            {"field": "logo", "path": str(logo), "name": logo.name, "type": "image/png"},
        ],
        fields,
    )


def _server(tmp_path: Path, pdf: Path, logo: Path, **kw) -> Path:
    d = tmp_path / "server"
    d.mkdir(exist_ok=True)
    return Path(add_watermark(str(pdf), str(d), "", logo_bytes=logo.read_bytes(), **kw))


def _red(path: Path):
    """(count, bbox) of strongly red pixels on page 1 at 36 dpi."""
    pix = fitz.open(str(path))[0].get_pixmap(dpi=36)
    xs, ys = [], []
    for y in range(pix.height):
        for x in range(pix.width):
            r, g, b = pix.pixel(x, y)[:3]
            if r > 200 and g < 80 and b < 80:
                xs.append(x)
                ys.append(y)
    return len(xs), ((min(xs), min(ys), max(xs), max(ys)) if xs else None)


@pytest.mark.parametrize("fields", [
    {"position": "center", "opacity": "1"},
    {"position": "top", "opacity": "1", "logo_scale": "0.3"},
    {"position": "diagonal", "opacity": "1"},
    {"position": "center", "opacity": "1", "tile": "true", "logo_scale": "0.2"},
    {"position": "diagonal", "opacity": "1", "tile": "true", "logo_scale": "0.2"},
])
def test_logo_lands_where_the_server_puts_it(tmp_path: Path, fields: dict) -> None:
    pdf = make_pdf(tmp_path / "in.pdf", pages=1)
    logo = _logo(tmp_path / "logo.png")
    result, out = _local(tmp_path, pdf, logo, **fields)
    assert result["status"] == 200, result
    kw = {
        "position": fields["position"], "opacity": float(fields["opacity"]), "tile": fields.get("tile") == "true",
        "logo_scale": float(fields.get("logo_scale", 0.4)),
    }
    n_l, box_l = _red(out)
    n_s, box_s = _red(_server(tmp_path, pdf, logo, **kw))
    assert n_l > 0 and n_s > 0
    assert abs(n_l - n_s) <= max(12, 0.08 * n_s), (n_l, n_s)
    assert all(abs(a - b) <= 2 for a, b in zip(box_l, box_s)), (box_l, box_s)


def test_logo_under_content_is_hidden(tmp_path: Path) -> None:
    doc = fitz.open()
    page = doc.new_page(width=300, height=300)
    page.draw_rect(page.rect, color=(1, 1, 1), fill=(1, 1, 1))
    pdf = tmp_path / "cover.pdf"
    doc.save(str(pdf))
    logo = _logo(tmp_path / "logo.png")
    _, out = _local(tmp_path, pdf, logo, layer="under", opacity="1")
    assert _red(out)[0] == 0
    assert _red(_server(tmp_path, pdf, logo, layer="under", opacity=1.0))[0] == 0
    _, out = _local(tmp_path, pdf, logo, layer="over", opacity="1")
    assert _red(out)[0] > 0


def test_jpeg_logo_and_bad_logo(tmp_path: Path) -> None:
    pdf = make_pdf(tmp_path / "in.pdf", pages=1)
    jpg = _logo(tmp_path / "logo.jpg", fmt="JPEG")
    result, _ = _local(tmp_path, pdf, jpg, position="center", opacity="1")
    assert result["status"] == 200, result
    bad = tmp_path / "bad.png"
    bad.write_bytes(b"not an image at all")
    result, _ = _local(tmp_path, pdf, bad)
    assert result["status"] == 400
    with pytest.raises(ValueError):
        add_watermark(str(pdf), str(tmp_path), "", logo_bytes=b"not an image at all")
    with pytest.raises(ValueError):
        add_watermark(str(pdf), str(tmp_path), "", logo_bytes=jpg.read_bytes(), logo_scale=5)
