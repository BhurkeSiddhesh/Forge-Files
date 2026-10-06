"""Work package 30 (audit): on-device Watermark Image equals the server's Pillow watermark.

The two sides use different font engines (Pillow/FreeType with the server's font, Chromium with the
system sans-serif), so glyph shapes differ. What must match is where the text lands, how big it is,
its colour and opacity, and everything else about the file. Harness: test_local_resize_image_parity.py.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from scripts.image_utils import watermark_image
from test_local_resize_image_parity import (  # noqa: F401  (page is a fixture)
    TOO_BIG,
    UNDECODABLE,
    encode,
    natural,
    open_local,
    page,
    run_local,
    with_hole,
)

ROUTE = "/api/image/watermark"
W, H = 800, 500
BASE = natural(W, H)
POSITIONS = ["top-left", "top-right", "center", "bottom-left", "bottom-right", "diagonal"]


def local(page, name, data, **fields):
    return run_local(page, name, data, fields, route=ROUTE)


def server(tmp_path: Path, name: str, data: bytes, **kw) -> Path:
    src = tmp_path / name
    src.write_bytes(data)
    out = tmp_path / "srv"
    out.mkdir(exist_ok=True)
    return Path(watermark_image(str(src), str(out), **kw))


def post(auth_client, name, data, **fields):
    return auth_client.post(ROUTE, files={"file": (name, data, "image/png")}, data={k: str(v) for k, v in fields.items()})


def changed(out: Image.Image, base: Image.Image, thr: int = 30) -> np.ndarray:
    a = np.asarray(out.convert("RGB")).astype(int)
    b = np.asarray(base.convert("RGB")).astype(int)
    return np.abs(a - b).max(axis=2) > thr


def box(mask: np.ndarray) -> tuple[int, int, int, int]:
    ys, xs = np.nonzero(mask)
    assert len(xs) > 20, "no watermark pixels found"
    return int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())


def centroid(mask: np.ndarray) -> tuple[float, float]:
    ys, xs = np.nonzero(mask)
    return float(xs.mean()), float(ys.mean())


def tilt(mask: np.ndarray) -> float:
    """Direction of the text's long axis in degrees (counter-clockwise from +x), in (-90, 90]."""
    ys, xs = np.nonzero(mask)
    cov = np.cov(np.stack([xs - xs.mean(), -(ys - ys.mean())]))
    vals, vecs = np.linalg.eigh(cov)
    vx, vy = vecs[:, int(np.argmax(vals))]
    ang = float(np.degrees(np.arctan2(vy, vx)))
    return ang - 180 if ang > 90 else ang + 180 if ang <= -90 else ang


PNG = encode(BASE, "PNG")


@pytest.mark.parametrize("position", POSITIONS)
def test_text_lands_where_the_server_puts_it(page, tmp_path, position) -> None:
    res = local(page, "in.png", PNG, text="Forge Files", position=position, opacity=1.0, color="white")
    img = open_local(res)
    srv = Image.open(server(tmp_path, "in.png", PNG, text="Forge Files", position=position, opacity=1.0, color="white"))
    assert (img.format, img.size) == (srv.format, srv.size)
    lm, sm = changed(img, BASE), changed(srv, BASE)
    (lx0, ly0, lx1, ly1), (sx0, sy0, sx1, sy1) = box(lm), box(sm)
    lc, sc = centroid(lm), centroid(sm)
    # Same anchor, same font size: boxes agree to within a few percent of the image.
    assert abs(lc[0] - sc[0]) <= 0.04 * W and abs(lc[1] - sc[1]) <= 0.04 * H, (lc, sc)
    assert abs((lx1 - lx0) - (sx1 - sx0)) <= 0.2 * (sx1 - sx0), ((lx0, lx1), (sx0, sx1))
    assert abs((ly1 - ly0) - (sy1 - sy0)) <= 0.3 * (sy1 - sy0) + 4, ((ly0, ly1), (sy0, sy1))
    if position == "diagonal":
        assert abs(tilt(lm) - tilt(sm)) <= 6 and tilt(lm) > 15  # rises to the right, like the server
    # Nothing outside the watermark changed (PNG is lossless).
    far = ~np.pad(lm | sm, 0)
    grown = np.zeros_like(lm)
    ys, xs = np.nonzero(lm | sm)
    grown[max(0, ys.min() - 40): ys.max() + 40, max(0, xs.min() - 40): xs.max() + 40] = True
    assert np.array_equal(np.asarray(img.convert("RGB"))[~grown], np.asarray(BASE)[~grown])
    assert far.any()


@pytest.mark.parametrize("color,rgb", [("white", (255, 255, 255)), ("black", (0, 0, 0)), ("red", (220, 30, 30)), ("blue", (30, 30, 220)), ("RED", (220, 30, 30)), ("green", (255, 255, 255)), ("", (255, 255, 255))])
def test_colour_names_map_like_the_server(page, tmp_path, color, rgb) -> None:
    flat = Image.new("RGB", (400, 200), (128, 128, 128))
    data = encode(flat, "PNG")
    res = local(page, "f.png", data, text="MMMM", position="center", opacity=1.0, color=color)
    img = open_local(res)
    srv = Image.open(server(tmp_path, "f.png", data, text="MMMM", position="center", opacity=1.0, color=color))
    for im in (img, srv):
        solid = np.asarray(im.convert("RGB")).reshape(-1, 3)
        solid = solid[(np.abs(solid - 128).max(axis=1) > 100) | (np.abs(solid - np.array(rgb)).max(axis=1) < 3)]
        near = solid[np.abs(solid - np.array(rgb)).max(axis=1) < 3]
        assert len(near) > 50, (color, im.size)


@pytest.mark.parametrize("opacity", [0.05, 0.1, 0.4, 0.75, 1.0])
def test_opacity_blends_the_same_amount(page, tmp_path, opacity) -> None:
    flat = Image.new("RGB", (400, 200), (0, 0, 0))
    data = encode(flat, "PNG")
    kw = dict(text="MMMM", position="center", opacity=opacity, color="white")
    img = open_local(local(page, "f.png", data, **kw))
    srv = Image.open(server(tmp_path, "f.png", data, **kw))
    peak = lambda im: int(np.asarray(im.convert("L")).max())  # noqa: E731
    # Fully covered glyph pixels reach opacity * 255 on black, within the server's int() truncation.
    assert abs(peak(img) - peak(srv)) <= 8 and abs(peak(img) - round(255 * opacity)) <= 8, (peak(img), peak(srv))


def test_default_options_are_bottom_right_white_forty_percent(page, tmp_path) -> None:
    res = local(page, "in.png", PNG, text="Forge Files")
    img = open_local(res)
    srv = Image.open(server(tmp_path, "in.png", PNG, text="Forge Files"))
    lc, sc = centroid(changed(img, BASE, 15)), centroid(changed(srv, BASE, 15))
    assert lc[0] > W / 2 and lc[1] > H / 2 and sc[0] > W / 2 and sc[1] > H / 2
    assert abs(lc[0] - sc[0]) <= 0.04 * W and abs(lc[1] - sc[1]) <= 0.04 * H
    assert res["message"] == "Watermark added"


@pytest.mark.parametrize("ext,pil", [("jpg", "JPEG"), ("png", "PNG"), ("webp", "WEBP")])
def test_format_size_and_name_are_preserved(page, tmp_path, ext, pil) -> None:
    data = encode(BASE, pil)
    res = local(page, f"My Photo.{ext}", data, text="© Forge", position="top-left")
    img = open_local(res)
    srv_path = server(tmp_path, f"My Photo.{ext}", data, text="© Forge", position="top-left")
    assert (img.format, img.size) == (Image.open(srv_path).format, Image.open(srv_path).size) == (pil, (W, H))
    assert res["filename"] == srv_path.name


def test_transparency_is_kept_and_text_over_transparent_pixels_stays_semi_transparent(page, tmp_path) -> None:
    data = encode(with_hole(BASE), "PNG")
    kw = dict(text="Forge Files", position="center", opacity=0.5, color="red")
    img = open_local(local(page, "a.png", data, **kw))
    srv = Image.open(server(tmp_path, "a.png", data, **kw))
    assert img.mode == srv.mode == "RGBA"
    a, b = np.asarray(img)[..., 3], np.asarray(srv)[..., 3]
    assert abs(float((a > 0).mean()) - float((b > 0).mean())) < 0.03  # the hole is as transparent as the server leaves it
    assert int(a.max()) == 255


def test_png_over_jpeg_name_is_flattened(page, tmp_path) -> None:
    data = encode(with_hole(BASE), "PNG")
    img = open_local(local(page, "x.jpg", data, text="Forge", position="bottom-left"))
    assert img.format == "JPEG" and min(img.getpixel((W // 2, H // 2))) > 235


def test_exif_orientation_is_baked_in(page, tmp_path) -> None:
    exif = Image.Exif()
    exif[0x0112] = 6
    data = encode(natural(500, 300), "JPEG", exif=exif.tobytes())
    img = open_local(local(page, "p.jpg", data, text="Hi", position="center"))
    srv = Image.open(server(tmp_path, "p.jpg", data, text="Hi", position="center"))
    assert img.size == srv.size == (300, 500)


@pytest.mark.parametrize("text", ["Café déjà vu", "Привет мир", "フォージ ファイル", "फ़ोर्ज फ़ाइल्स", "😀 ok"])
def test_non_latin_text_is_drawn_not_dropped(page, text) -> None:
    img = open_local(local(page, "in.png", PNG, text=text, position="center", opacity=1.0, color="white"))
    mask = changed(img, BASE)
    x0, y0, x1, y1 = box(mask)
    assert (x1 - x0) > 40 and (y1 - y0) >= 15, (x0, y0, x1, y1)


def test_multiline_text_goes_to_the_server(page) -> None:
    res = local(page, "in.png", PNG, text="Line one\nLine two", position="center")
    assert res["status"] != 200 and len(res["asked"]) == 1 and res["fetches"] == []


INVALID = [
    {"text": ""},
    {"text": "   "},
    {"text": "x", "position": "middle"},
    {"text": "x", "opacity": 0.04},
    {"text": "x", "opacity": 0},
    {"text": "x", "opacity": 1.5},
    {"text": "x", "opacity": -1},
    {"text": "x", "opacity": 2},
]


@pytest.mark.parametrize("fields", INVALID, ids=lambda f: ",".join(f"{k}={v!r}" for k, v in f.items()))
def test_rejections_use_the_servers_words(page, auth_client, fields) -> None:
    data = encode(natural(120, 90), "PNG")
    res = local(page, "in.png", data, **fields)
    resp = post(auth_client, "in.png", data, **fields)
    assert resp.status_code >= 400 and res["status"] == 400
    assert res["fetches"] == [] and res["asked"] == []
    detail = resp.json()["detail"]
    if isinstance(detail, str):
        assert res["detail"] == detail


def test_the_servers_check_order_decides_which_error_is_reported(page, auth_client) -> None:
    data = encode(natural(120, 90), "PNG")
    both = dict(text=" ", position="middle", opacity=9)
    res = local(page, "in.png", data, **both)
    resp = post(auth_client, "in.png", data, **both)
    assert res["detail"] == resp.json()["detail"]


def test_undecodable_and_oversized_inputs_ask_before_any_upload(page) -> None:
    tiff = local(page, "scan.tiff", encode(natural(80, 60), "TIFF"), text="x")
    assert [a["reason"] for a in tiff["asked"]] == [UNDECODABLE] and tiff["fetches"] == []
    huge = local(page, "huge.png", encode(Image.new("L", (7000, 6000), 128), "PNG"), text="x")
    assert [a["reason"] for a in huge["asked"]] == [TOO_BIG] and huge["fetches"] == []
    big = local(page, "big.png", encode(Image.new("L", (4200, 4200), 128), "PNG"), text="x")
    assert [a["reason"] for a in big["asked"]] == [TOO_BIG] and big["fetches"] == []
