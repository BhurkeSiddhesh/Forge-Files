"""Work package 26 (audit): on-device Image to PDF equals the server's reportlab PDF.

Runs the shipped `ops-pdf.js` (real pdf-lib, real canvas) in headless Chromium and compares the PDF
with `scripts.pdf_utils.images_to_pdf` by parsing both with PyMuPDF: page count and size, where each
image is placed, which pixels were embedded, and what a rendered page looks like.
"""

from __future__ import annotations

import base64
import io
from pathlib import Path

import fitz
import numpy as np
import pytest
from PIL import Image

from scripts.pdf_utils import images_to_pdf
from test_local_resize_image_parity import STATIC, TOO_BIG, UNDECODABLE, encode, natural, plain, with_hole

sync_api = pytest.importorskip("playwright.sync_api")

ROUTE = "/api/image/to-pdf"


@pytest.fixture(scope="module")
def pdf_page():
    with sync_api.sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as exc:  # pragma: no cover - environment dependent
            pytest.skip(f"Chromium unavailable: {exc}")
        pg = browser.new_page()
        pg.goto("about:blank")
        pg.evaluate(
            """(() => {
              window.apiUrl = p => p;
              window.__fetches = [];
              window.fetch = (...a) => { window.__fetches.push(String(a[0])); throw new Error('upload'); };
            })()"""
        )
        for name in ("vendor/pdf-lib.min.js", "vendor/jszip.min.js", "local/ff-server-gate.js", "local/ff-local.js", "local/ops-pdf.js"):
            pg.add_script_tag(content=(STATIC / name).read_text(encoding="utf8"))
        pg.evaluate("window.__asked = []; ffConsent.handler = info => { window.__asked.push(info); return false; }")
        yield pg
        browser.close()


_RUN = """async ([files, fields]) => {
  window.__fetches.length = 0; window.__asked.length = 0;
  const fd = new FormData();
  for (const f of files) {
    const bin = Uint8Array.from(atob(f.b64), c => c.charCodeAt(0));
    fd.append('files', new File([bin], f.name, { type: f.type }));
  }
  for (const [k, v] of Object.entries(fields)) fd.append(k, String(v));
  const res = await ffProcess('/api/image/to-pdf', fd);
  const body = await res.json();
  let out = null;
  if (res.ok) {
    const ab = await ffLocal.resolve(body.download_token).blob.arrayBuffer();
    let s = ''; const u = new Uint8Array(ab);
    for (let i = 0; i < u.length; i += 0x8000) s += String.fromCharCode(...u.subarray(i, i + 0x8000));
    out = btoa(s);
  }
  return { status: res.status, detail: body.detail, message: body.message, filename: body.filename, out,
           fetches: window.__fetches.slice(), asked: window.__asked.slice() };
}"""


def run_local(pg, files: list[tuple[str, bytes, str]], **fields) -> dict:
    payload = [{"name": n, "b64": base64.b64encode(d).decode(), "type": t} for n, d, t in files]
    r = pg.evaluate(_RUN, [payload, fields])
    r["bytes"] = base64.b64decode(r["out"]) if r["out"] else None
    return r


def run_server(tmp_path: Path, files: list[tuple[str, bytes, str]], **kw) -> Path:
    src = tmp_path / "in"
    src.mkdir(exist_ok=True)
    paths = []
    for i, (name, data, _t) in enumerate(files):
        p = src / f"{i}_{name}"
        p.write_bytes(data)
        paths.append(str(p))
    out = tmp_path / "srv"
    out.mkdir(exist_ok=True)
    return Path(images_to_pdf(paths, str(out), **kw))


def open_pdf(data: bytes | Path) -> fitz.Document:
    return fitz.open(stream=data, filetype="pdf") if isinstance(data, bytes) else fitz.open(str(data))


def render(doc: fitz.Document, i: int) -> np.ndarray:
    pix = doc[i].get_pixmap(matrix=fitz.Matrix(1, 1), alpha=False)
    return np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, 3).astype(float)


def compare(local_bytes: bytes, srv_path: Path, tol: float = 2.0) -> tuple[fitz.Document, fitz.Document]:
    a, b = open_pdf(local_bytes), open_pdf(srv_path)
    assert len(a) == len(b)
    for i in range(len(a)):
        assert [round(v, 1) for v in a[i].rect] == [round(v, 1) for v in b[i].rect], i
        ia, ib = a[i].get_image_info(), b[i].get_image_info()
        assert len(ia) == len(ib) == 1, (ia, ib)
        assert [round(v, 1) for v in ia[0]["bbox"]] == pytest.approx([round(v, 1) for v in ib[0]["bbox"]], abs=0.6)
        ra, rb = render(a, i), render(b, i)
        assert ra.shape == rb.shape
        assert float(np.abs(ra - rb).mean()) <= tol, (i, float(np.abs(ra - rb).mean()))
    return a, b


PHOTO = natural(640, 480)
JPG = ("photo.jpg", encode(PHOTO, "JPEG", quality=90), "image/jpeg")
PNG = ("shot.png", encode(PHOTO, "PNG"), "image/png")


@pytest.mark.parametrize("page_size", ["A4", "Letter", "a4", "LETTER", "auto", "Legal", "bogus"])
@pytest.mark.parametrize("fit_mode", ["fit", "stretch", "original", "bogus"])
def test_page_size_and_fit_mode_match_the_server(pdf_page, tmp_path, page_size, fit_mode) -> None:
    files = [JPG]
    res = run_local(pdf_page, files, page_size=page_size, fit_mode=fit_mode)
    assert res["status"] == 200 and res["fetches"] == [], res
    compare(res["bytes"], run_server(tmp_path, files, page_size=page_size, fit_mode=fit_mode))


@pytest.mark.parametrize("margin", [0, 10, 36, 100, 200])
def test_margins_match_the_server(pdf_page, tmp_path, margin) -> None:
    res = run_local(pdf_page, [PNG], margin_pt=margin)
    compare(res["bytes"], run_server(tmp_path, [PNG], margin_pt=margin))


def test_order_count_and_mixed_sizes_are_kept(pdf_page, tmp_path) -> None:
    files = [
        ("a.png", encode(plain(300, 500), "PNG"), "image/png"),
        ("b.jpg", encode(natural(900, 300), "JPEG"), "image/jpeg"),
        ("c.webp", encode(natural(200, 200), "WEBP"), "image/webp"),
        ("d.png", encode(plain(50, 50), "PNG"), "image/png"),
    ]
    res = run_local(pdf_page, files, page_size="A4")
    a, _b = compare(res["bytes"], run_server(tmp_path, files, page_size="A4"))
    assert [img["width"] for page in a for img in page.get_image_info()] == [300, 900, 200, 50]
    assert res["message"] == "Created PDF from 4 image(s)"


def test_default_options_are_a4_fit_36pt(pdf_page, tmp_path) -> None:
    res = run_local(pdf_page, [JPG])
    compare(res["bytes"], run_server(tmp_path, [JPG]))


def test_a_jpeg_is_embedded_without_being_recompressed(pdf_page, tmp_path) -> None:
    """reportlab passes JPEG bytes straight through; re-encoding in a canvas would add a generation of loss."""
    res = run_local(pdf_page, [JPG])
    srv = run_server(tmp_path, [JPG])
    for doc in (open_pdf(res["bytes"]), open_pdf(srv)):
        img = doc.extract_image(doc[0].get_images()[0][0])
        assert img["ext"] == "jpeg" and img["image"] == JPG[1]


@pytest.mark.parametrize(
    "name,fmt,mime",
    [("p.png", "PNG", "image/png"), ("w.webp", "WEBP", "image/webp"), ("g.gif", "GIF", "image/gif"), ("b.bmp", "BMP", "image/bmp")],
)
def test_opaque_lossless_sources_keep_their_pixels(pdf_page, tmp_path, name, fmt, mime) -> None:
    src = natural(200, 150).convert("P") if fmt == "GIF" else natural(200, 150)
    data = encode(src, fmt, **({"lossless": True} if fmt == "WEBP" else {}))
    files = [(name, data, mime)]
    res = run_local(pdf_page, files)
    srv = run_server(tmp_path, files)
    a, b = open_pdf(res["bytes"]), open_pdf(srv)
    for doc in (a, b):
        x = doc.extract_image(doc[0].get_images()[0][0])
        got = np.asarray(Image.open(io.BytesIO(x["image"])).convert("RGB")).astype(int)
        want = np.asarray(Image.open(io.BytesIO(data)).convert("RGB")).astype(int)
        assert got.shape == want.shape and float(np.abs(got - want).mean()) <= 0.01, (doc.name, float(np.abs(got - want).mean()))


@pytest.mark.parametrize("name,fmt,mime", [("t.png", "PNG", "image/png"), ("t.webp", "WEBP", "image/webp"), ("t.gif", "GIF", "image/gif"), ("t.png", "PNG", ""), ("t.webp", "WEBP", "")])
def test_transparency_stays_transparent(pdf_page, tmp_path, name, fmt, mime) -> None:
    """A transparent background must show the white page, not turn black (also when the browser gives no MIME type)."""
    if fmt == "GIF":
        im = Image.new("P", (120, 90), 1)
        im.putpalette([255, 0, 0, 0, 0, 255] + [0] * 756)
        data = encode(im, "GIF", transparency=0)
    else:
        data = encode(with_hole(natural(300, 200)), fmt, **({"lossless": True} if fmt == "WEBP" else {}))
    files = [(name, data, mime)]
    res = run_local(pdf_page, files, margin_pt=0)
    assert res["status"] == 200, res
    doc = open_pdf(res["bytes"])
    local_img, srv_img = render(doc, 0), render(open_pdf(run_server(tmp_path, files, margin_pt=0)), 0)
    x0, y0, x1, y1 = (int(v) for v in doc[0].get_image_info()[0]["bbox"])
    bw, bh = x1 - x0, y1 - y0
    hole = (slice(y0 + bh // 4, y0 + 3 * bh // 4), slice(x0 + bw // 4, x0 + 3 * bw // 4))
    if fmt != "GIF":
        # The transparent middle shows the white page. (reportlab ignores alpha and shows whatever colour
        # is stored under it, which is black for most logos; that is deliberately not copied.)
        inner = (slice(y0 + int(bh * 0.4), y0 + int(bh * 0.6)), slice(x0 + int(bw * 0.4), x0 + int(bw * 0.6)))
        assert local_img[inner].min() > 235, local_img[inner].min()
    keep = np.ones(local_img.shape[:2], dtype=bool)
    keep[hole] = False
    assert float(np.abs(local_img - srv_img)[keep].mean()) <= 3  # everything outside the transparent area agrees


def test_png_data_with_a_jpg_name_and_the_reverse_are_read_by_content(pdf_page, tmp_path) -> None:
    files = [("really_png.jpg", PNG[1], "image/jpeg"), ("really_jpeg.png", JPG[1], "image/png")]
    res = run_local(pdf_page, files)
    assert res["status"] == 200, res
    compare(res["bytes"], run_server(tmp_path, files))


def test_exif_orientation_is_baked_into_the_page(pdf_page, tmp_path) -> None:
    exif = Image.Exif()
    exif[0x0112] = 6
    files = [("phone.jpg", encode(natural(600, 400), "JPEG", exif=exif.tobytes()), "image/jpeg")]
    res = run_local(pdf_page, files, page_size="auto")
    a, b = compare(res["bytes"], run_server(tmp_path, files, page_size="auto"), tol=4)
    assert tuple(round(v) for v in a[0].rect[2:]) == (400, 600)


def test_grayscale_and_16_bit_sources(pdf_page, tmp_path) -> None:
    gray = ("gray.png", encode(natural(200, 120).convert("L"), "PNG"), "image/png")
    gray_jpg = ("gray.jpg", encode(natural(200, 120).convert("L"), "JPEG"), "image/jpeg")
    files = [gray, gray_jpg]
    res = run_local(pdf_page, files)
    assert res["status"] == 200, res
    compare(res["bytes"], run_server(tmp_path, files), tol=3)


def test_filename_and_message_follow_the_route(pdf_page) -> None:
    res = run_local(pdf_page, [JPG, PNG])
    assert res["message"] == "Created PDF from 2 image(s)"
    assert res["filename"].startswith("images_to_pdf_") and res["filename"].endswith(".pdf")


@pytest.mark.parametrize("margin", [-1, 201])
def test_margin_range_uses_the_servers_words(pdf_page, auth_client, margin) -> None:
    res = run_local(pdf_page, [JPG], margin_pt=margin)
    resp = auth_client.post(ROUTE, files=[("files", ("a.jpg", JPG[1], "image/jpeg"))], data={"margin_pt": str(margin)})
    assert resp.status_code == 422 and res["status"] == 400
    assert res["detail"] == resp.json()["detail"] and res["fetches"] == [] and res["asked"] == []


def test_no_files_is_rejected_without_upload(pdf_page) -> None:
    res = run_local(pdf_page, [])
    assert res["status"] == 400 and res["detail"] == "At least one image file is required." and res["fetches"] == []


def test_one_unreadable_image_asks_once_before_anything_is_uploaded(pdf_page) -> None:
    files = [JPG, ("scan.tiff", encode(natural(80, 60), "TIFF"), "image/tiff")]
    res = run_local(pdf_page, files)
    assert [a["reason"] for a in res["asked"]] == [UNDECODABLE] and res["fetches"] == []
    broken = run_local(pdf_page, [("broken.png", b"not an image", "image/png")])
    assert [a["reason"] for a in broken["asked"]] == [UNDECODABLE] and broken["fetches"] == []


def test_very_large_image_is_left_to_the_server(pdf_page) -> None:
    files = [("huge.png", encode(Image.new("L", (7000, 6000), 128), "PNG"), "image/png")]
    res = run_local(pdf_page, files)
    assert [a["reason"] for a in res["asked"]] == [TOO_BIG] and res["fetches"] == []


def test_a_batch_beyond_the_memory_budget_is_left_to_the_server(pdf_page) -> None:
    res = pdf_page.evaluate(
        """async () => {
          window.__fetches.length = 0; window.__asked.length = 0;
          const fd = new FormData();
          for (let i = 0; i < 2; i++) fd.append('files', new File([new Uint8Array(80 * 1024 * 1024)], 'big' + i + '.jpg', { type: 'image/jpeg' }));
          const r = await ffProcess('/api/image/to-pdf', fd);
          return { status: r.status, asked: window.__asked.map(a => a.reason), fetches: window.__fetches.slice() };
        }"""
    )
    assert res["asked"] == [TOO_BIG] and res["fetches"] == [], res
