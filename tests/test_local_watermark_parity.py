"""Work package 10 (audit): on-device Watermark PDF equals the server's."""

from __future__ import annotations

from pathlib import Path

import fitz
import pikepdf
import pytest

from conftest_parity import NODE, make_pdf, run_local
from scripts.pdf_utils import add_watermark

pytestmark = pytest.mark.skipif(NODE is None, reason="node not available")

SIZES = [(595, 842), (842, 595), (300, 400)]


def _local(tmp_path: Path, pdf: Path, text="CONFIDENTIAL", **fields):
    f = {"text": text}
    f.update(fields)
    return run_local(
        tmp_path, "/api/pdf/watermark",
        [{"field": "file", "path": str(pdf), "name": "My Doc.pdf", "type": "application/pdf"}], f,
    )


def _server(tmp_path: Path, pdf: Path, text="CONFIDENTIAL", position="diagonal", opacity=0.3) -> Path:
    d = tmp_path / "server"
    d.mkdir(exist_ok=True)
    return Path(add_watermark(str(pdf), str(d), text, position, opacity))


def _mark(path: Path, text: str) -> list[dict]:
    """Where the watermark words sit on each page (independent read-back)."""
    doc = fitz.open(str(path))
    rows = []
    for page in doc:
        words = [w for w in page.get_text("words") if w[4] in text.split()]
        rows.append(
            {
                "bbox": [min(w[0] for w in words), min(w[1] for w in words), max(w[2] for w in words), max(w[3] for w in words)]
                if words else None,
                "chars": "".join(w[4] for w in words),
            }
        )
    doc.close()
    return rows


def _close(a, b, tol=4.0):
    return a is not None and b is not None and all(abs(x - y) <= tol for x, y in zip(a, b))


@pytest.mark.parametrize("position", ["top", "center", "bottom"])
def test_horizontal_positions_land_where_the_servers_do(tmp_path: Path, position: str) -> None:
    pdf = make_pdf(tmp_path / "in.pdf", sizes=SIZES)
    result, out = _local(tmp_path, pdf, position=position)
    assert result["status"] == 200, result
    local, server = _mark(out, "CONFIDENTIAL"), _mark(_server(tmp_path, pdf, position=position), "CONFIDENTIAL")
    for page, (l, s) in enumerate(zip(local, server), 1):
        assert l["chars"] == s["chars"] == "CONFIDENTIAL", (page, l, s)
        assert _close(l["bbox"], s["bbox"]), (position, page, l["bbox"], s["bbox"])


def test_diagonal_runs_up_and_right_centred_on_the_page(tmp_path: Path) -> None:
    """Deliberate difference from the server, recorded here.

    The server anchors the START of the diagonal text at the page centre, so the
    mark runs towards the top-right corner (long text could leave the page). The
    on-device handler centres it, which is what its users already get. Both run at
    45 degrees up and to the right.
    """
    pdf = make_pdf(tmp_path / "in.pdf", sizes=SIZES)
    result, out = _local(tmp_path, pdf, position="diagonal")
    assert result["status"] == 200
    doc = fitz.open(str(out))
    for page in doc:
        words = [w for w in page.get_text("words") if w[4] == "CONFIDENTIAL"]
        assert words
        x0, y0, x1, y1 = words[0][:4]
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        # The baseline passes through the page centre; the glyph body sits above it, so
        # the word's box centre is up and to the left of the centre by about half a cap height.
        assert abs(cx - page.rect.width / 2) < 20 and abs(cy - page.rect.height / 2) < 20, (page.rect, cx, cy)
        chars = [c for b in page.get_text("rawdict")["blocks"] for ln in b.get("lines", []) for sp in ln["spans"] for c in sp["chars"]]
        mark = [c for c in chars if c["c"] in "CONFIDENTIAL"][-12:]
        first, last = mark[0]["origin"], mark[-1]["origin"]
        assert first[1] > last[1] and abs((first[1] - last[1]) - (last[0] - first[0])) < 8, (first, last)
    doc.close()
    server = fitz.open(str(_server(tmp_path, pdf)))
    words = [w for w in server[0].get_text("words") if w[4] == "CONFIDENTIAL"]
    x0, y0, x1, y1 = words[0][:4]
    assert (x0 + x1) / 2 - server[0].rect.width / 2 > 60, "server mark sits well right of the page centre"
    assert server[0].rect.height / 2 - (y0 + y1) / 2 > 60, "and well above it"
    # Ours stays inside the small 300x400 page.
    lw = [w for w in fitz.open(str(out))[2].get_text("words") if w[4] == "CONFIDENTIAL"][0]
    page = server[2].rect
    assert lw[0] >= 0 and lw[2] <= page.width and lw[1] >= 0 and lw[3] <= page.height, ("local mark stays on the page", lw)
    server.close()


def test_every_page_is_marked_text_is_selectable_and_original_text_kept(tmp_path: Path) -> None:
    pdf = make_pdf(tmp_path / "in.pdf", sizes=SIZES)
    _, out = _local(tmp_path, pdf, position="center")
    doc = fitz.open(str(out))
    for i, page in enumerate(doc, 1):
        text = page.get_text()
        assert "CONFIDENTIAL" in text and f"Page {i}" in text
        assert not page.get_images(), "watermark is text, not an image"
    doc.close()


@pytest.mark.parametrize("opacity", [0.05, 0.3, 0.7, 1.0])
def test_opacity_is_applied(tmp_path: Path, opacity: float) -> None:
    pdf = make_pdf(tmp_path / "in.pdf", pages=1)
    result, out = _local(tmp_path, pdf, opacity=opacity)
    assert result["status"] == 200
    with pikepdf.open(out) as p:
        states = p.pages[0].Resources.ExtGState
        values = {round(float(v.get("/ca", 1)), 2) for v in states.values()}
    assert round(opacity, 2) in values, (opacity, values)


def test_rendered_page_is_visibly_marked_and_close_to_the_servers(tmp_path: Path) -> None:
    pdf = make_pdf(tmp_path / "in.pdf", pages=1)
    _, out = _local(tmp_path, pdf, position="center", opacity=0.5)
    base = fitz.open(str(pdf))[0].get_pixmap(dpi=72)
    marked = fitz.open(str(out))[0].get_pixmap(dpi=72)
    server = fitz.open(str(_server(tmp_path, pdf, position="center", opacity=0.5)))[0].get_pixmap(dpi=72)
    changed = sum(1 for a, b in zip(base.samples, marked.samples) if a != b)
    changed_server = sum(1 for a, b in zip(base.samples, server.samples) if a != b)
    assert changed > 500, "the watermark is actually drawn"
    assert 0.6 < changed / changed_server < 1.6, (changed, changed_server)


def test_only_content_added_not_rewritten(tmp_path: Path) -> None:
    pdf = make_pdf(tmp_path / "in.pdf", sizes=SIZES, rotations={})
    _, out = _local(tmp_path, pdf)
    a, b = fitz.open(str(pdf)), fitz.open(str(out))
    assert len(a) == len(b) == 3
    assert [p.rect for p in a] == [p.rect for p in b]


@pytest.mark.parametrize(
    "fields, detail",
    [
        ({"text": ""}, "Watermark text cannot be empty."),
        ({"text": "   "}, "Watermark text cannot be empty."),
        ({"position": "left"}, "Position must be one of: diagonal, top, center, bottom."),
        ({"opacity": 0.01}, "Opacity must be between 0.1 and 1.0."),
        ({"opacity": 1.5}, "Opacity must be between 0.1 and 1.0."),
    ],
)
def test_invalid_input_matches_the_server_and_never_asks(tmp_path: Path, fields: dict, detail: str) -> None:
    pdf = make_pdf(tmp_path / "in.pdf", pages=1)
    text = fields.pop("text", "CONFIDENTIAL")
    result, out = _local(tmp_path, pdf, text=text, **fields)
    assert result["status"] == 400 and result["detail"] == detail
    assert result["asked"] is None and not out.exists()
    with pytest.raises(ValueError) as server:
        add_watermark(str(pdf), str(tmp_path), text, fields.get("position", "diagonal"), fields.get("opacity", 0.3))
    assert str(server.value) == detail


def test_non_latin_text_asks_with_the_font_reason_instead_of_failing(tmp_path: Path) -> None:
    pdf = make_pdf(tmp_path / "in.pdf", pages=1)
    result, out = _local(tmp_path, pdf, text="गोपनीय")
    assert result["status"] == 499
    assert result["asked"]["tool"] == "Watermark PDF"
    assert result["asked"]["reason"] == "this document uses a script the on-device fonts do not cover"
    assert not out.exists()


def test_a_rotated_page_is_left_to_the_server(tmp_path: Path) -> None:
    pdf = make_pdf(tmp_path / "in.pdf", sizes=SIZES, rotations={1: 90})
    result, _ = _local(tmp_path, pdf)
    assert result["status"] == 499
    assert result["asked"]["reason"] == "this file uses features that cannot be processed on this device"


def test_cropped_pages_are_marked_inside_the_visible_area(tmp_path: Path) -> None:
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    page.set_cropbox(fitz.Rect(100, 150, 500, 700))
    pdf = tmp_path / "crop.pdf"
    doc.save(str(pdf))
    result, out = _local(tmp_path, pdf, position="center")
    assert result["status"] == 200
    local, server = _mark(out, "CONFIDENTIAL"), _mark(_server(tmp_path, pdf, position="center"), "CONFIDENTIAL")
    assert _close(local[0]["bbox"], server[0]["bbox"]), (local, server)


def test_password_encrypted_corrupt_and_oversize_ask_first(tmp_path: Path) -> None:
    pdf = make_pdf(tmp_path / "in.pdf", pages=1)
    result, _ = _local(tmp_path, pdf, password="x")
    assert result["status"] == 499 and result["asked"]["reason"] == "this PDF is password protected"
    enc = tmp_path / "enc.pdf"
    with pikepdf.open(pdf) as p:
        p.save(enc, encryption=pikepdf.Encryption(user="u", owner="o"))
    result, _ = _local(tmp_path, enc)
    assert result["status"] == 499 and result["asked"]["reason"] == "this PDF is password protected"
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"%PDF-1.4 not a pdf")
    result, _ = _local(tmp_path, bad)
    assert result["status"] == 499
    big = tmp_path / "big.pdf"
    big.write_bytes(pdf.read_bytes() + b"\0" * (151 * 1024 * 1024))
    result, _ = _local(tmp_path, big)
    assert result["status"] == 499
    assert result["asked"]["reason"] == "this file exceeds the safe limit for processing on this device"
