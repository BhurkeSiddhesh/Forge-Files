"""Work package 24 (audit): on-device Resize Image equals the server's Pillow resize.

Image handlers need a real canvas, so these run the shipped `ops-image.js` in headless
Chromium (Playwright) and compare against `scripts.image_utils.resize_image` and the
FastAPI route. Skipped when Playwright or its Chromium is not installed.
"""

from __future__ import annotations

import base64
import io
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw, ImageFilter

from scripts.image_utils import resize_image

sync_api = pytest.importorskip("playwright.sync_api")

STATIC = Path(__file__).resolve().parent.parent / "static"
ROUTE = "/api/image/resize"

# Bounded reason text the consent dialog shows (ff-server-gate.js CODES).
UNDECODABLE = "this file format could not be decoded on this device"
TOO_BIG = "this file exceeds the safe limit for processing on this device"


# ── browser harness ───────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def page():
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
              window.__urls = { created: 0, revoked: 0 };
              const c = URL.createObjectURL.bind(URL), r = URL.revokeObjectURL.bind(URL);
              URL.createObjectURL = o => { window.__urls.created++; return c(o); };
              URL.revokeObjectURL = u => { window.__urls.revoked++; return r(u); };
            })()"""
        )
        for name in ("local/ff-server-gate.js", "local/ff-local.js", "local/ops-image.js"):
            pg.add_script_tag(content=(STATIC / name).read_text(encoding="utf8"))
        pg.evaluate("window.__asked = []; ffConsent.handler = info => { window.__asked.push(info); return false; }")
        yield pg
        browser.close()


_RUN = """async ([name, b64, type, fields, route]) => {
  window.__fetches.length = 0; window.__asked.length = 0;
  const bin = Uint8Array.from(atob(b64), c => c.charCodeAt(0));
  const fd = new FormData();
  fd.append('file', new File([bin], name, { type }));
  for (const [k, v] of Object.entries(fields)) fd.append(k, String(v));
  const res = await ffProcess(route, fd);
  const body = await res.json();
  let out = null;
  if (res.ok) {
    const ab = await ffLocal.resolve(body.download_token).blob.arrayBuffer();
    let s = ''; const u = new Uint8Array(ab);
    for (let i = 0; i < u.length; i += 0x8000) s += String.fromCharCode(...u.subarray(i, i + 0x8000));
    out = btoa(s);
  }
  return { status: res.status, detail: body.detail, message: body.message, filename: body.filename,
           out, body, fetches: window.__fetches.slice(), asked: window.__asked.slice() };
}"""


def run_local(page, name: str, data: bytes, fields: dict, mime: str = "application/octet-stream", route: str = ROUTE) -> dict:
    r = page.evaluate(_RUN, [name, base64.b64encode(data).decode(), mime, fields, route])
    r["bytes"] = base64.b64decode(r["out"]) if r["out"] else None
    return r


# ── fixtures ──────────────────────────────────────────────────────────────


def natural(w: int, h: int, seed: int = 1) -> Image.Image:
    """Smooth noise plus hard edges, text and a filled shape: photo-like yet deterministic."""
    rng = np.random.default_rng(seed)
    noise = rng.normal(0, 1, (h, w, 3))
    im = Image.fromarray(np.clip(128 + 60 * noise, 0, 255).astype("uint8")).filter(ImageFilter.GaussianBlur(3))
    d = ImageDraw.Draw(im)
    d.rectangle((w // 5, h // 5, w // 2, h // 2), outline=(255, 255, 255), width=3)
    d.text((w // 3, h // 3), "Forge Files 123", fill=(0, 0, 0))
    d.ellipse((w // 2, h // 3, w - 40, h - 40), fill=(200, 30, 30))
    return im


def plain(w: int, h: int) -> Image.Image:
    """Any-size image with a gradient and a bar, for tests that only care about dimensions."""
    y, x = np.mgrid[0:h, 0:w]
    arr = np.stack([x * 255 // max(w, 1), y * 255 // max(h, 1), (x + y) % 256], -1).astype("uint8")
    return Image.fromarray(arr)


def with_hole(im: Image.Image) -> Image.Image:
    """RGBA copy whose central ellipse is fully transparent."""
    w, h = im.size
    rgba = im.convert("RGBA")
    mask = Image.new("L", (w, h), 255)
    ImageDraw.Draw(mask).ellipse((w // 4, h // 4, 3 * w // 4, 3 * h // 4), fill=0)
    rgba.putalpha(mask)
    return rgba


def encode(im: Image.Image, fmt: str, **kw) -> bytes:
    buf = io.BytesIO()
    im.save(buf, fmt, **kw)
    return buf.getvalue()


def over_white(im: Image.Image) -> np.ndarray:
    rgba = im.convert("RGBA")
    bg = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
    bg.alpha_composite(rgba)
    return np.asarray(bg.convert("RGB")).astype(float)


def mae(a: Image.Image, b: Image.Image) -> float:
    assert a.size == b.size, (a.size, b.size)
    return float(np.abs(over_white(a) - over_white(b)).mean())


def server(tmp_path: Path, name: str, data: bytes, **kw) -> Path:
    src = tmp_path / name
    src.write_bytes(data)
    out = tmp_path / "srv"
    out.mkdir(exist_ok=True)
    return Path(resize_image(str(src), str(out), **kw))


def open_local(r: dict) -> Image.Image:
    assert r["status"] == 200, r
    assert r["fetches"] == [], "a locally handled resize must not upload"
    img = Image.open(io.BytesIO(r["bytes"]))
    img.load()
    return img


PHOTO = natural(800, 600)
FORMATS = [("jpg", "JPEG"), ("png", "PNG"), ("webp", "WEBP")]


# ── dimensions / percentage ───────────────────────────────────────────────


@pytest.mark.parametrize("ext,pil", FORMATS)
@pytest.mark.parametrize(
    "fields",
    [
        {"mode": "dimensions", "width": 200, "height": 150},
        {"mode": "dimensions", "width": 333},
        {"mode": "dimensions", "height": 99},
        {"mode": "dimensions", "width": 123, "height": 457},  # aspect ratio is not forced
        {"mode": "dimensions", "width": 1600, "height": 1200},  # upscale
        {"mode": "percentage", "percentage": 25},
        {"mode": "percentage", "percentage": 100},
        {"mode": "percentage", "percentage": 250},
    ],
    ids=lambda f: "-".join(f"{k}{v}" for k, v in f.items() if k != "mode") + "-" + f["mode"][:3],
)
def test_output_dimensions_format_and_pixels_match_pillow(page, tmp_path, ext, pil, fields) -> None:
    data = encode(PHOTO, pil)
    local = run_local(page, f"in.{ext}", data, fields)
    img = open_local(local)
    srv = Image.open(server(tmp_path, f"in.{ext}", data, **fields))
    assert img.format == srv.format == pil
    assert img.size == srv.size
    if img.mode != srv.mode:
        # A canvas always writes PNG with an alpha channel; an opaque RGB source comes back as
        # RGBA with every pixel at 255, which looks and behaves the same.
        assert (img.mode, srv.mode) == ("RGBA", "RGB") and img.getchannel("A").getextrema() == (255, 255)
    assert mae(img, srv) < 2.5  # 0.4 to 1.9 observed (up to 6x shrink of a photo-like image) for different resamplers
    assert local["message"] == "Image Resized"


@pytest.mark.parametrize("width,pct", [(25, 116), (25, 228), (100, 29), (100, 57), (7, 143), (333, 14), (1000, 7)])
def test_percentage_truncates_exactly_like_python(page, tmp_path, width, pct) -> None:
    """`int(w * (p / 100.0))` differs from `int(w * p / 100)` in about 1 case in 600."""
    data = encode(plain(width, width), "PNG")
    fields = {"mode": "percentage", "percentage": pct}
    img = open_local(run_local(page, "sq.png", data, fields))
    srv = Image.open(server(tmp_path, "sq.png", data, **fields))
    assert img.size == srv.size == (int(width * (pct / 100.0)),) * 2


def test_percentage_sweep_has_no_off_by_one(page, tmp_path) -> None:
    """Every (width, percentage) pair where the two formulas disagree must follow Python."""
    pairs = [(w, p) for w in range(20, 60) for p in range(100, 300) if int(w * (p / 100.0)) != int(w * p / 100)]
    assert pairs, "the sweep should contain disagreeing pairs"
    for w, p in pairs[:12]:
        data = encode(Image.new("RGB", (w, 8), (10, 20, 30)), "PNG")
        img = open_local(run_local(page, "t.png", data, {"mode": "percentage", "percentage": p}))
        assert img.width == int(w * (p / 100.0)), (w, p)


# ── transparency, orientation, other input formats ────────────────────────


@pytest.mark.parametrize("ext,pil", [("png", "PNG"), ("webp", "WEBP")])
def test_transparency_is_kept(page, tmp_path, ext, pil) -> None:
    data = encode(with_hole(natural(400, 300)), pil, **({"lossless": True} if pil == "WEBP" else {}))
    fields = {"mode": "dimensions", "width": 100, "height": 75}
    img = open_local(run_local(page, f"a.{ext}", data, fields))
    srv = Image.open(server(tmp_path, f"a.{ext}", data, **fields))
    assert img.mode == srv.mode == "RGBA"
    assert img.getpixel((50, 37))[3] == 0  # the hole is still a hole
    assert img.getpixel((2, 2))[3] == 255
    assert mae(img, srv) < 2.5


def test_jpeg_input_has_no_transparency_and_png_hole_is_white_when_saved_as_jpeg(page, tmp_path) -> None:
    """Transparent PNG renamed .jpg: the server composites onto white; so must we."""
    data = encode(with_hole(natural(300, 200)), "PNG")
    fields = {"mode": "dimensions", "width": 150, "height": 100}
    img = open_local(run_local(page, "x.jpg", data, fields))
    srv = Image.open(server(tmp_path, "x.jpg", data, **fields))
    assert img.format == srv.format == "JPEG"
    px = img.getpixel((75, 50))
    assert min(px) > 240, px  # white, not black
    assert mae(img, srv) < 3


def test_exif_orientation_is_baked_in_like_pillow(page, tmp_path) -> None:
    src = natural(400, 200)
    exif = Image.Exif()
    exif[0x0112] = 6  # rotate 90 CW to display
    data = encode(src, "JPEG", exif=exif.tobytes())
    fields = {"mode": "percentage", "percentage": 50}
    img = open_local(run_local(page, "p.jpg", data, fields))
    srv = Image.open(server(tmp_path, "p.jpg", data, **fields))
    assert img.size == srv.size == (100, 200)  # portrait: rotation was applied
    assert mae(img, srv) < 4


@pytest.mark.parametrize("ext,pil", [("gif", "GIF"), ("bmp", "BMP")])
def test_formats_pillow_saves_as_jpeg_do_the_same_here(page, tmp_path, ext, pil) -> None:
    data = encode(natural(200, 150).convert("P" if pil == "GIF" else "RGB"), pil)
    fields = {"mode": "percentage", "percentage": 50}
    local = run_local(page, f"in.{ext}", data, fields)
    img = open_local(local)
    srv = server(tmp_path, f"in.{ext}", data, **fields)
    assert img.format == "JPEG" and Image.open(srv).format == "JPEG"
    assert local["filename"].endswith(".jpg") and srv.suffix == ".jpg"
    assert img.size == Image.open(srv).size == (100, 75)


# ── filenames ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "name",
    ["photo.jpg", "My Photo.final.JPEG", "файл изображение.png", "résumé scan.webp", "noextension", "a.b.c.png", "UPPER.PNG"],
)
def test_branded_filename_matches_the_server(page, tmp_path, name) -> None:
    ext = Path(name).suffix.lower().lstrip(".")
    pil = {"jpg": "JPEG", "jpeg": "JPEG", "png": "PNG", "webp": "WEBP"}.get(ext, "JPEG")
    data = encode(natural(120, 90), pil)
    fields = {"mode": "percentage", "percentage": 50}
    local = run_local(page, name, data, fields)
    assert local["status"] == 200, local
    assert local["filename"] == server(tmp_path, name, data, **fields).name


# ── validation: same words, never an upload ───────────────────────────────

INVALID = [
    {"mode": "sideways", "width": 10},
    {"mode": "dimensions"},
    {"mode": "percentage"},
    {"mode": "target_size"},
    {"mode": "dimensions", "width": 0},
    {"mode": "dimensions", "width": 8193},
    {"mode": "dimensions", "height": -5},
    {"mode": "dimensions", "width": 8192, "height": 8192},  # 67 MP
    {"mode": "dimensions", "width": 6000, "height": 4000},  # 24 MP > 20 MP cap
    {"mode": "percentage", "percentage": 0},
    {"mode": "percentage", "percentage": 501},
    {"mode": "target_size", "target_size_kb": 0},
    {"mode": "dimensions", "width": 1, "height": 1, "percentage": 0},
]


@pytest.mark.parametrize("fields", INVALID, ids=lambda f: ",".join(f"{k}={v}" for k, v in f.items()))
def test_rejections_use_the_servers_words(page, auth_client, fields) -> None:
    data = encode(natural(120, 90), "PNG")
    local = run_local(page, "in.png", data, fields)
    resp = auth_client.post(ROUTE, files={"file": ("in.png", data, "image/png")}, data={k: str(v) for k, v in fields.items()})
    assert resp.status_code >= 400 and local["status"] >= 400
    assert local["fetches"] == [] and local["asked"] == [], "validation errors must not offer or perform an upload"
    detail = resp.json()["detail"]
    if isinstance(detail, str):
        assert local["detail"] == detail


def test_zero_pixel_output_is_rejected_with_the_servers_message(page, tmp_path, auth_client) -> None:
    data = encode(Image.new("RGB", (3, 3), (1, 2, 3)), "PNG")
    fields = {"mode": "percentage", "percentage": 10}
    local = run_local(page, "tiny.png", data, fields)
    resp = auth_client.post(ROUTE, files={"file": ("tiny.png", data, "image/png")}, data={"mode": "percentage", "percentage": "10"})
    assert local["status"] == resp.status_code == 400
    assert local["detail"] == resp.json()["detail"] == "Resize output dimensions must be at least 1 pixel."


# ── target size ───────────────────────────────────────────────────────────


@pytest.mark.parametrize("ext,pil,kb", [("jpg", "JPEG", 40), ("jpg", "JPEG", 90), ("webp", "WEBP", 30)])
def test_target_size_is_met_without_over_shrinking(page, tmp_path, ext, pil, kb) -> None:
    data = encode(natural(1200, 900), pil)
    fields = {"mode": "target_size", "target_size_kb": kb}
    local = run_local(page, f"big.{ext}", data, fields)
    img = open_local(local)
    srv = Image.open(server(tmp_path, f"big.{ext}", data, **fields))
    assert len(local["bytes"]) <= kb * 1024
    assert img.format == srv.format == pil
    # Chromium's encoders are less efficient than Pillow's (optimize / method=6), so the local
    # result may need a smaller picture to fit; it must not need much smaller.
    assert img.width >= 0.8 * srv.width, (img.size, srv.size)
    assert img.width <= 1200


def test_target_already_met_returns_the_full_size_picture(page, tmp_path) -> None:
    data = encode(natural(300, 200), "JPEG")
    fields = {"mode": "target_size", "target_size_kb": 900}
    img = open_local(run_local(page, "small.jpg", data, fields))
    assert img.size == Image.open(server(tmp_path, "small.jpg", data, **fields)).size == (300, 200)


def test_unreachable_target_never_shrinks_below_the_ten_pixel_floor(page, tmp_path) -> None:
    """Noise barely compresses: each side either fits 1 KB or stops shrinking just above 10 px."""
    rng = np.random.default_rng(7)
    data = encode(Image.fromarray(rng.integers(0, 256, (300, 400, 3), dtype="uint8")), "JPEG")
    fields = {"mode": "target_size", "target_size_kb": 1}
    local = run_local(page, "n.jpg", data, fields)
    img = open_local(local)
    srv_path = server(tmp_path, "n.jpg", data, **fields)
    srv = Image.open(srv_path)
    for size, dims in ((len(local["bytes"]), img.size), (srv_path.stat().st_size, srv.size)):
        assert size <= 1024 or 10 <= min(dims) <= 11, (size, dims)


def test_tiny_target_is_met_when_the_picture_allows_it(page) -> None:
    """A smooth picture fits 1 KB once shrunk; Chromium's fixed JPEG tables just need more shrinking than Pillow's."""
    data = encode(natural(400, 300), "JPEG")
    local = run_local(page, "t.jpg", data, {"mode": "target_size", "target_size_kb": 1})
    open_local(local)
    assert len(local["bytes"]) <= 1024


def test_png_that_fits_at_full_size_is_handled_locally(page, tmp_path) -> None:
    data = encode(Image.new("RGB", (300, 200), (30, 90, 200)), "PNG")
    fields = {"mode": "target_size", "target_size_kb": 50}
    img = open_local(run_local(page, "flat.png", data, fields))
    assert img.size == Image.open(server(tmp_path, "flat.png", data, **fields)).size == (300, 200)


def test_png_that_needs_palette_reduction_goes_to_the_server_after_consent(page, tmp_path) -> None:
    """Pillow keeps 600x450 and reduces colours; shrinking the picture instead would differ."""
    data = encode(natural(600, 450), "PNG")
    fields = {"mode": "target_size", "target_size_kb": 100}
    assert len(data) > 100 * 1024
    local = run_local(page, "big.png", data, fields)
    assert local["status"] != 200
    assert len(local["asked"]) == 1 and local["asked"][0]["tool"] == "Resize Image"
    assert local["fetches"] == [], "declining must not upload"
    srv = Image.open(server(tmp_path, "big.png", data, **fields))
    assert srv.size == (600, 450)


# ── server fallback, budgets, cleanup ─────────────────────────────────────


def test_undecodable_image_asks_before_any_upload(page) -> None:
    tiff = encode(natural(80, 60), "TIFF")
    local = run_local(page, "scan.tiff", tiff, {"mode": "percentage", "percentage": 50})
    assert local["status"] != 200
    assert [a["reason"] for a in local["asked"]] == [UNDECODABLE]
    assert local["fetches"] == []


def test_garbage_bytes_with_an_image_name_ask_before_any_upload(page) -> None:
    local = run_local(page, "broken.jpg", b"not an image at all", {"mode": "percentage", "percentage": 50})
    assert [a["reason"] for a in local["asked"]] == [UNDECODABLE]
    assert local["fetches"] == []


def test_very_large_source_is_left_to_the_server(page) -> None:
    data = encode(Image.new("L", (7000, 6000), 128), "PNG")  # 42 MP, tiny on disk
    local = run_local(page, "huge.png", data, {"mode": "dimensions", "width": 700})
    assert [a["reason"] for a in local["asked"]] == [TOO_BIG]
    assert local["fetches"] == []


def test_output_above_the_safe_canvas_area_is_left_to_the_server(page) -> None:
    """17.6 MP is within the server's 20 MP cap but beyond iOS Safari's 16.7 MP canvas."""
    data = encode(Image.new("L", (4200, 4200), 128), "PNG")
    local = run_local(page, "big.png", data, {"mode": "percentage", "percentage": 100})
    assert [a["reason"] for a in local["asked"]] == [TOO_BIG]
    assert local["fetches"] == []


def test_object_urls_are_released_after_repeated_runs(page) -> None:
    before = page.evaluate("({...window.__urls})")
    data = encode(natural(160, 120), "JPEG")
    for _ in range(8):
        assert run_local(page, "r.jpg", data, {"mode": "percentage", "percentage": 50})["status"] == 200
    after = page.evaluate("({...window.__urls})")
    assert after["revoked"] - before["revoked"] >= after["created"] - before["created"] - 8  # one result URL per run is held for download
    assert page.evaluate("document.querySelectorAll('canvas').length") == 0
