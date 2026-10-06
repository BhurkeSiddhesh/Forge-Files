"""Work package 06: a visual signature stamped on-device lands where the server's does.

The real browser-side handler (pdf-lib) runs under node; PyMuPDF, which did not
write the file, reads the placed image back. The server's `sign_pdf` is the
reference for placement.
"""

from __future__ import annotations

import io
import json
import shutil
import subprocess
from pathlib import Path

import fitz
import pikepdf
import pytest
from PIL import Image

from scripts.pdf_utils import sign_pdf

HARNESS = Path(__file__).parent / "local_sign_harness.mjs"
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node not available")


def _pdf(path: Path, pages: int = 2, rotate_page2: bool = False, cropbox: tuple | None = None) -> None:
    doc = fitz.open()
    for i in range(pages):
        page = doc.new_page(width=595, height=842)
        page.insert_text((72, 100), f"Page {i + 1}", fontsize=12)
    if rotate_page2:
        doc[1].set_rotation(90)
    if cropbox:
        doc[0].set_cropbox(fitz.Rect(*cropbox))
    doc.save(str(path))
    doc.close()


def _png(path: Path, size=(200, 80), alpha=True) -> None:
    img = Image.new("RGBA" if alpha else "RGB", size, (220, 20, 20, 255) if alpha else (220, 20, 20))
    if alpha:
        for x in range(size[0] // 2):  # left half transparent
            for y in range(size[1]):
                img.putpixel((x, y), (0, 0, 0, 0))
    img.save(path)


def _jpg(path: Path, size=(160, 160)) -> None:
    Image.new("RGB", size, (20, 20, 220)).save(path, "JPEG")


def _local(tmp_path: Path, pdf: Path, sig: Path | None, fields: dict) -> tuple[dict, Path]:
    ff = tmp_path / "fields.json"
    ff.write_text(json.dumps(fields))
    out = tmp_path / "local_out.pdf"
    proc = subprocess.run(
        [NODE, str(HARNESS), str(pdf), str(sig) if sig else "-", str(ff), str(out)],
        capture_output=True, text=True, encoding="utf-8", timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1]), out


def _server(tmp_path: Path, pdf: Path, sig: Path, **kw) -> Path:
    d = tmp_path / "server"
    d.mkdir(exist_ok=True)
    return Path(sign_pdf(str(pdf), str(sig), str(d), **kw))


def _placed(path: Path, page_no: int = 1) -> list[tuple[float, ...]]:
    doc = fitz.open(str(path))
    rects = [tuple(i["bbox"]) for i in doc[page_no - 1].get_image_info()]
    doc.close()
    return rects


def _close(a, b, tol=0.6):
    return all(abs(x - y) <= tol for x, y in zip(a, b))


@pytest.mark.parametrize(
    "kw",
    [
        {},
        {"page": 2, "x": 0.1, "y": 0.1, "width": 0.3},
        {"x": 0.5, "y": 0.5, "width": 0.5},
        {"x": 0.95, "y": 0.95, "width": 0.4},  # clamped at the page edge
        {"x": 0.0, "y": 0.0, "width": 1.0},
        {"x": 0.3, "y": 0.2, "width": 0.05},
    ],
)
def test_placement_matches_the_server(tmp_path: Path, kw: dict) -> None:
    pdf, sig = tmp_path / "in.pdf", tmp_path / "sig.png"
    _pdf(pdf)
    _png(sig)
    result, out = _local(tmp_path, pdf, sig, kw)
    assert result["status"] == 200, result
    assert result["message"] == "Signature added"
    assert result["filename"] == "doc_forgefiles.org.pdf"
    page = kw.get("page", 1)
    (local,) = _placed(out, page)
    (server,) = _placed(_server(tmp_path, pdf, sig, **kw), page)
    assert _close(local, server), (local, server)


def test_tall_and_jpeg_signatures_keep_their_proportions(tmp_path: Path) -> None:
    pdf = tmp_path / "in.pdf"
    _pdf(pdf)
    tall = tmp_path / "tall.png"
    _png(tall, size=(60, 240))
    _, out = _local(tmp_path, pdf, tall, {"x": 0.2, "y": 0.2, "width": 0.2})
    (loc,) = _placed(out)
    (srv,) = _placed(_server(tmp_path, pdf, tall, x=0.2, y=0.2, width=0.2))
    assert _close(loc, srv)
    assert abs((loc[3] - loc[1]) / (loc[2] - loc[0]) - 4.0) < 0.02

    jpg = tmp_path / "s.jpg"
    _jpg(jpg)
    result, out = _local(tmp_path, pdf, jpg, {"__type": "image/jpeg", "x": 0.4, "y": 0.4})
    assert result["status"] == 200
    (loc,) = _placed(out)
    (srv,) = _placed(_server(tmp_path, pdf, jpg, x=0.4, y=0.4))
    assert _close(loc, srv)


def test_the_signature_is_visible_and_transparency_is_kept(tmp_path: Path) -> None:
    pdf, sig = tmp_path / "in.pdf", tmp_path / "sig.png"
    _pdf(pdf, pages=1)
    _png(sig)
    _, out = _local(tmp_path, pdf, sig, {"x": 0.1, "y": 0.4, "width": 0.4})
    doc = fitz.open(str(out))
    (x0, y0, x1, y1) = _placed(out)[0]
    pix = doc[0].get_pixmap(dpi=72)
    left = pix.pixel(int(x0 + (x1 - x0) * 0.25), int((y0 + y1) / 2))[:3]
    right = pix.pixel(int(x0 + (x1 - x0) * 0.75), int((y0 + y1) / 2))[:3]
    assert right[0] > 180 and right[1] < 80, right  # opaque red half
    assert left == (255, 255, 255), left  # transparent half shows the white page
    assert doc[0].get_text().count("Page 1") == 1, "existing text untouched"
    doc.close()
    with pikepdf.open(out) as p:
        images = [o for o in p.pages[0].Resources.XObject.values() if o.get("/Subtype") == "/Image"]
        assert any("/SMask" in i for i in images), "alpha channel preserved as a soft mask"


def test_only_the_target_page_changes(tmp_path: Path) -> None:
    pdf, sig = tmp_path / "in.pdf", tmp_path / "sig.png"
    _pdf(pdf, pages=3)
    _png(sig)
    _, out = _local(tmp_path, pdf, sig, {"page": 2})
    doc, base = fitz.open(str(out)), fitz.open(str(pdf))
    assert len(doc) == 3
    assert [len(doc[i].get_image_info()) for i in range(3)] == [0, 1, 0]
    for i in (0, 2):
        assert doc[i].get_pixmap(dpi=36).samples == base[i].get_pixmap(dpi=36).samples


def test_cropbox_offset_matches_the_server(tmp_path: Path) -> None:
    pdf, sig = tmp_path / "in.pdf", tmp_path / "sig.png"
    _pdf(pdf, cropbox=(50, 80, 450, 700))
    _png(sig)
    _, out = _local(tmp_path, pdf, sig, {"x": 0.5, "y": 0.5, "width": 0.3})
    (loc,) = _placed(out)
    (srv,) = _placed(_server(tmp_path, pdf, sig, x=0.5, y=0.5, width=0.3))
    assert _close(loc, srv), (loc, srv)


def test_rotated_target_page_is_left_to_the_server(tmp_path: Path) -> None:
    pdf, sig = tmp_path / "in.pdf", tmp_path / "sig.png"
    _pdf(pdf, rotate_page2=True)
    _png(sig)
    result, out = _local(tmp_path, pdf, sig, {"page": 2})
    assert result["status"] == 499
    assert result["asked"]["tool"] == "Sign PDF"
    assert not out.exists()
    ok, _ = _local(tmp_path, pdf, sig, {"page": 1})
    assert ok["status"] == 200


@pytest.mark.parametrize(
    "fields, detail",
    [
        ({"page": 0}, "Page number must be >= 1."),
        ({"page": 3}, "Page 3 exceeds document page count (2)."),
        ({"x": 1.5}, "x and y must be between 0 and 1."),
        ({"y": -0.1}, "x and y must be between 0 and 1."),
        ({"width": 0.01}, "width must be between 0.05 and 1.0."),
        ({"width": 1.5}, "width must be between 0.05 and 1.0."),
        ({"page": "abc"}, "page must be an integer; x, y, width must be numbers."),
        ({"x": "abc"}, "page must be an integer; x, y, width must be numbers."),
        ({"__type": "image/gif"}, "Signature must be a PNG or JPEG image."),
    ],
)
def test_validation_matches_the_server_and_never_asks(tmp_path: Path, fields: dict, detail: str) -> None:
    pdf, sig = tmp_path / "in.pdf", tmp_path / "sig.png"
    _pdf(pdf)
    _png(sig)
    result, out = _local(tmp_path, pdf, sig, fields)
    assert result["status"] == 400
    assert result["detail"] == detail
    assert result["asked"] is None
    assert not out.exists()


def test_a_non_image_with_an_image_type_is_rejected(tmp_path: Path) -> None:
    pdf, fake = tmp_path / "in.pdf", tmp_path / "sig.png"
    _pdf(pdf)
    fake.write_bytes(b"this is not an image")
    result, _ = _local(tmp_path, pdf, fake, {})
    assert result["status"] == 400
    assert result["detail"] == "Signature must be a PNG or JPEG image."


def test_missing_signature_is_a_clear_error(tmp_path: Path) -> None:
    pdf = tmp_path / "in.pdf"
    _pdf(pdf)
    result, _ = _local(tmp_path, pdf, None, {})
    assert result["status"] == 400
    assert result["detail"] == "Signature must be a PNG or JPEG image."


def test_password_and_encrypted_pdfs_ask_with_the_password_reason(tmp_path: Path) -> None:
    pdf, sig = tmp_path / "in.pdf", tmp_path / "sig.png"
    _pdf(pdf)
    _png(sig)
    result, _ = _local(tmp_path, pdf, sig, {"password": "x"})
    assert result["status"] == 499 and result["asked"]["reason"] == "this PDF is password protected"
    enc = tmp_path / "enc.pdf"
    with pikepdf.open(pdf) as p:
        p.save(enc, encryption=pikepdf.Encryption(user="u", owner="o"))
    result, _ = _local(tmp_path, enc, sig, {})
    assert result["status"] == 499 and result["asked"]["reason"] == "this PDF is password protected"


def test_a_corrupt_pdf_asks_before_the_server(tmp_path: Path) -> None:
    bad, sig = tmp_path / "bad.pdf", tmp_path / "sig.png"
    bad.write_bytes(b"%PDF-1.4 not really a pdf")
    _png(sig)
    result, _ = _local(tmp_path, bad, sig, {})
    assert result["status"] == 499
    assert result["asked"]["reason"] == "this file uses features that cannot be processed on this device"


def test_the_form_says_this_is_a_visual_signature() -> None:
    html = (Path(__file__).resolve().parent.parent / "static" / "index.html").read_text(encoding="utf-8")
    assert "not a certificate-based digital signature" in html
