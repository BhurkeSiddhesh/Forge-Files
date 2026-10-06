"""Work package 15 (audit): on-device Add Page Numbers equals the server's."""

from __future__ import annotations

from pathlib import Path

import fitz
import pikepdf
import pytest

from conftest_parity import NODE, make_pdf, run_local
from scripts.pdf_utils import add_page_numbers

pytestmark = pytest.mark.skipif(NODE is None, reason="node not available")

SIZES = [(595, 842), (842, 595), (300, 400), (612, 792)]
POSITIONS = ["bottom-center", "bottom-left", "bottom-right", "top-center", "top-left", "top-right"]


def _local(tmp_path: Path, pdf: Path, **fields):
    return run_local(
        tmp_path, "/api/pdf/add-page-numbers",
        [{"field": "file", "path": str(pdf), "name": "My Doc.pdf", "type": "application/pdf"}], fields,
    )


def _server(tmp_path: Path, pdf: Path, **kw) -> Path:
    d = tmp_path / "server"
    d.mkdir(exist_ok=True)
    return Path(add_page_numbers(str(pdf), str(d), **kw))


def _numbers(path: Path) -> list[dict | None]:
    """The page-number word on each page: text and box (anything that is not the 'Page N' body text)."""
    doc = fitz.open(str(path))
    rows = []
    for i, page in enumerate(doc, 1):
        body = {"Page", str(i)}
        words = [w for w in page.get_text("words") if not (w[4] in body and w[1] < 130 and w[1] > 80)]
        rows.append({"text": words[0][4], "bbox": list(words[0][:4])} if words else None)
    doc.close()
    return rows


def _close(a, b, tol=1.0):
    return all(abs(x - y) <= tol for x, y in zip(a, b))


@pytest.mark.parametrize("position", POSITIONS)
@pytest.mark.parametrize("fmt", ["decimal", "roman", "alpha"])
def test_every_position_and_format_matches_the_server(tmp_path: Path, position: str, fmt: str) -> None:
    pdf = make_pdf(tmp_path / "in.pdf", sizes=SIZES)
    result, out = _local(tmp_path, pdf, position=position, fmt=fmt)
    assert result["status"] == 200, result
    assert result["message"] == "Page numbers added"
    assert result["filename"] == "My Doc_forgefiles.org.pdf"
    local, server = _numbers(out), _numbers(_server(tmp_path, pdf, position=position, fmt=fmt))
    for page, (l, s) in enumerate(zip(local, server), 1):
        assert l["text"] == s["text"], (page, l, s)
        assert _close(l["bbox"], s["bbox"]), (position, fmt, page, l["bbox"], s["bbox"])


@pytest.mark.parametrize(
    "kw",
    [
        {"start_number": 5},
        {"start_number": 1, "skip_first": 2},
        {"start_number": 10, "skip_first": 1, "fmt": "roman"},
        {"start_number": 24, "fmt": "alpha"},  # runs past Z: falls back to digits
        {"start_number": 3990, "fmt": "roman"},
        {"font_size": 4},
        {"font_size": 72, "position": "top-right"},
        {"skip_first": 99},
        {"skip_first": -2},
    ],
)
def test_numbering_options_match_the_server(tmp_path: Path, kw: dict) -> None:
    pdf = make_pdf(tmp_path / "in.pdf", sizes=SIZES)
    result, out = _local(tmp_path, pdf, **kw)
    assert result["status"] == 200, result
    local, server = _numbers(out), _numbers(_server(tmp_path, pdf, **kw))
    assert [None if r is None else r["text"] for r in local] == [None if r is None else r["text"] for r in server]
    for l, s in zip(local, server):
        if l and s:
            assert _close(l["bbox"], s["bbox"]), (kw, l, s)


def test_numbers_are_selectable_text_and_original_text_is_kept(tmp_path: Path) -> None:
    pdf = make_pdf(tmp_path / "in.pdf", sizes=SIZES)
    _, out = _local(tmp_path, pdf)
    doc = fitz.open(str(out))
    for i, page in enumerate(doc, 1):
        text = page.get_text()
        assert f"Page {i}" in text and text.strip().endswith(str(i))
        assert not page.get_images()
    doc.close()


def test_cropped_pages_are_numbered_inside_the_visible_area(tmp_path: Path) -> None:
    doc = fitz.open()
    doc.new_page(width=595, height=842).set_cropbox(fitz.Rect(100, 150, 500, 700))
    pdf = tmp_path / "crop.pdf"
    doc.save(str(pdf))
    for position in ("bottom-right", "top-left", "bottom-center"):
        result, out = _local(tmp_path, pdf, position=position)
        assert result["status"] == 200
        (l,), (s,) = _numbers(out), _numbers(_server(tmp_path, pdf, position=position))
        assert _close(l["bbox"], s["bbox"]), (position, l, s)


def test_rotated_pages_are_left_to_the_server(tmp_path: Path) -> None:
    pdf = make_pdf(tmp_path / "in.pdf", sizes=SIZES, rotations={1: 90})
    result, out = _local(tmp_path, pdf)
    assert result["status"] == 499
    assert result["asked"]["tool"] == "Add Page Numbers"
    assert not out.exists()


def test_document_info_is_kept_and_not_stamped(tmp_path: Path) -> None:
    pdf = make_pdf(tmp_path / "in.pdf", pages=2)
    with pikepdf.open(pdf, allow_overwriting_input=True) as p:
        p.docinfo["/Title"] = "Keep"
        p.save(pdf)
    _, out = _local(tmp_path, pdf)
    with pikepdf.open(out) as p:
        info = {str(k): str(v) for k, v in p.docinfo.items()}
    assert info["/Title"] == "Keep" and "pdf-lib" not in " ".join(info.values()) and "/ModDate" not in info


@pytest.mark.parametrize(
    "fields",
    [
        {"position": "left"},
        {"fmt": "hex"},
        {"start_number": 0},
        {"font_size": 3},
        {"font_size": 73},
    ],
)
def test_invalid_options_match_the_server_message_and_never_ask(tmp_path: Path, fields: dict) -> None:
    pdf = make_pdf(tmp_path / "in.pdf", pages=2)
    result, out = _local(tmp_path, pdf, **fields)
    with pytest.raises(ValueError) as server:
        _server(tmp_path, pdf, **fields)
    assert result["status"] == 400 and result["detail"] == str(server.value), fields
    assert result["asked"] is None and not out.exists()


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


@pytest.mark.parametrize("kw", [{"template": "p{n}/{total}"}, {"template": "p{n}/{total}", "end_page": 2, "skip_first": 1}])
def test_template_and_end_page_match_the_server(tmp_path: Path, kw: dict) -> None:
    pdf = make_pdf(tmp_path / "in.pdf", sizes=SIZES)
    result, out = _local(tmp_path, pdf, **kw)
    assert result["status"] == 200, result
    local, server = _numbers(out), _numbers(_server(tmp_path, pdf, **kw))
    for page, (l, s) in enumerate(zip(local, server), 1):
        assert (l or {}).get("text") == (s or {}).get("text"), (page, l, s)
    if "end_page" not in kw:
        assert local[0]["text"] == "p1/4"


def test_template_without_n_is_rejected(tmp_path: Path) -> None:
    pdf = make_pdf(tmp_path / "in.pdf", sizes=SIZES)
    result, _ = _local(tmp_path, pdf, template="Page")
    assert result["status"] == 400
    with pytest.raises(ValueError):
        _server(tmp_path, pdf, template="Page")
