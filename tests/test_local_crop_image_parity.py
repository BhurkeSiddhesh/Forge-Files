"""Work package 25 (audit): on-device Crop Image equals the server's Pillow crop.

Runs the shipped `ops-image.js` in headless Chromium (see test_local_resize_image_parity.py for the
harness). Skipped when Playwright or its Chromium is not installed.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from scripts.image_utils import crop_image
from test_local_resize_image_parity import (  # noqa: F401  (page is a fixture)
    TOO_BIG,
    UNDECODABLE,
    encode,
    natural,
    open_local,
    page,
    plain,
    run_local,
    with_hole,
)

ROUTE = "/api/image/crop"
PHOTO = natural(640, 480)
FORMATS = [("jpg", "JPEG"), ("png", "PNG"), ("webp", "WEBP")]


def local(page, name, data, **fields):
    return run_local(page, name, data, fields, route=ROUTE)


def server(tmp_path: Path, name: str, data: bytes, **kw) -> Path:
    src = tmp_path / name
    src.write_bytes(data)
    out = tmp_path / "srv"
    out.mkdir(exist_ok=True)
    return Path(crop_image(str(src), str(out), **kw))


def rgb(im: Image.Image) -> np.ndarray:
    rgba = im.convert("RGBA")
    bg = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
    bg.alpha_composite(rgba)
    return np.asarray(bg.convert("RGB")).astype(float)


@pytest.mark.parametrize("ext,pil", FORMATS)
@pytest.mark.parametrize(
    "box",
    [(0, 0, 640, 480), (10, 20, 100, 50), (0, 0, 1, 1), (639, 479, 1, 1), (600, 440, 200, 200), (320, 0, 5000, 5000), (100, 100, 333, 7)],
    ids=lambda b: "x{}y{}w{}h{}".format(*b),
)
def test_crop_box_and_pixels_match_pillow(page, tmp_path, ext, pil, box) -> None:
    x, y, w, h = box
    data = encode(PHOTO, pil, **({"lossless": True} if pil == "WEBP" else {}))
    res = local(page, f"in.{ext}", data, x=x, y=y, width=w, height=h)
    img = open_local(res)
    srv = Image.open(server(tmp_path, f"in.{ext}", data, x=x, y=y, width=w, height=h))
    assert (img.format, img.size) == (srv.format, srv.size) == (pil, (min(640, x + w) - x, min(480, y + h) - y))
    assert res["message"] == "Image Cropped"
    # Same pixels as the source region: lossless formats exactly, JPEG within codec noise.
    expect = rgb(PHOTO.crop((x, y, x + img.width, y + img.height)))
    tol = {"JPEG": 6.0, "WEBP": 2.5, "PNG": 0.01}[pil]
    assert float(np.abs(rgb(img) - expect).mean()) <= tol
    assert float(np.abs(rgb(img) - rgb(srv)).mean()) <= tol + 1


def test_transparency_is_kept_in_the_cropped_region(page, tmp_path) -> None:
    data = encode(with_hole(natural(400, 300)), "PNG")
    box = dict(x=50, y=40, width=300, height=200)
    img = open_local(local(page, "a.png", data, **box))
    srv = Image.open(server(tmp_path, "a.png", data, **box))
    assert img.mode == srv.mode == "RGBA" and img.size == srv.size == (300, 200)
    a, b = np.asarray(img), np.asarray(srv)
    assert np.array_equal(a[..., 3], b[..., 3])  # alpha identical
    solid = a[..., 3] > 0  # a canvas zeroes the colour of fully transparent pixels; Pillow keeps it. Invisible.
    assert np.array_equal(a[solid], b[solid])
    assert img.getpixel((150, 100))[3] == 0


def test_png_over_jpeg_name_is_flattened_to_white(page) -> None:
    data = encode(with_hole(natural(300, 200)), "PNG")
    img = open_local(local(page, "x.jpg", data, x=0, y=0, width=300, height=200))
    assert img.format == "JPEG" and min(img.getpixel((150, 100))) > 240


def test_exif_orientation_applies_before_the_box_is_read(page, tmp_path) -> None:
    exif = Image.Exif()
    exif[0x0112] = 6  # stored 400x200, displayed 200x400
    data = encode(natural(400, 200), "JPEG", exif=exif.tobytes())
    box = dict(x=0, y=300, width=500, height=500)  # only valid in the rotated frame
    img = open_local(local(page, "p.jpg", data, **box))
    srv = Image.open(server(tmp_path, "p.jpg", data, **box))
    assert img.size == srv.size == (200, 100)
    assert float(np.abs(rgb(img) - rgb(srv)).mean()) < 6


@pytest.mark.parametrize("name", ["photo.jpg", "My Photo.final.JPEG", "файл.png", "noextension", "a.b.c.webp"])
def test_branded_filename_matches_the_server(page, tmp_path, name) -> None:
    ext = Path(name).suffix.lower().lstrip(".")
    pil = {"jpg": "JPEG", "jpeg": "JPEG", "png": "PNG", "webp": "WEBP"}.get(ext, "JPEG")
    data = encode(natural(120, 90), pil)
    box = dict(x=1, y=1, width=50, height=50)
    res = local(page, name, data, **box)
    assert res["status"] == 200, res
    assert res["filename"] == server(tmp_path, name, data, **box).name


@pytest.mark.parametrize("ext,pil", [("gif", "GIF"), ("bmp", "BMP")])
def test_gif_and_bmp_are_saved_as_jpeg_like_the_server(page, tmp_path, ext, pil) -> None:
    data = encode(natural(200, 150).convert("P" if pil == "GIF" else "RGB"), pil)
    box = dict(x=10, y=10, width=80, height=60)
    res = local(page, f"in.{ext}", data, **box)
    img = open_local(res)
    srv = server(tmp_path, f"in.{ext}", data, **box)
    assert img.format == "JPEG" and srv.suffix == ".jpg" and res["filename"].endswith(".jpg")
    assert img.size == Image.open(srv).size == (80, 60)


INVALID = [
    {"x": -1, "y": 0, "width": 10, "height": 10},
    {"x": 0, "y": -5, "width": 10, "height": 10},
    {"x": 0, "y": 0, "width": 0, "height": 10},
    {"x": 0, "y": 0, "width": 10, "height": 0},
    {"x": 0, "y": 0, "width": -3, "height": 10},
]


@pytest.mark.parametrize("fields", INVALID, ids=lambda f: ",".join(f"{k}={v}" for k, v in f.items()))
def test_rejections_use_the_servers_words(page, auth_client, fields) -> None:
    data = encode(natural(120, 90), "PNG")
    res = local(page, "in.png", data, **fields)
    resp = auth_client.post(ROUTE, files={"file": ("in.png", data, "image/png")}, data={k: str(v) for k, v in fields.items()})
    assert resp.status_code >= 400 and res["status"] >= 400
    assert res["fetches"] == [] and res["asked"] == []
    assert res["detail"] == resp.json()["detail"]


@pytest.mark.parametrize(
    "fields",
    [{"x": 120, "y": 0, "width": 10, "height": 10}, {"x": 0, "y": 90, "width": 10, "height": 10}, {"x": 500, "y": 500, "width": 5, "height": 5}],
)
def test_box_wholly_outside_the_image_fails_both_sides_without_upload(page, tmp_path, fields) -> None:
    data = encode(natural(120, 90), "PNG")
    res = local(page, "in.png", data, **fields)
    assert res["status"] == 400 and res["fetches"] == [] and res["asked"] == []
    with pytest.raises(Exception):
        server(tmp_path, "in.png", data, **fields)


def test_missing_options_are_rejected_without_upload(page) -> None:
    data = encode(natural(120, 90), "PNG")
    res = local(page, "in.png", data, x=0, y=0, width=10)
    assert res["status"] == 400 and res["detail"] == "x, y, width and height are required." and res["fetches"] == []


def test_undecodable_and_garbage_ask_before_any_upload(page) -> None:
    for name, data in (("scan.tiff", encode(natural(80, 60), "TIFF")), ("broken.jpg", b"nope")):
        res = local(page, name, data, x=0, y=0, width=10, height=10)
        assert [a["reason"] for a in res["asked"]] == [UNDECODABLE] and res["fetches"] == []


def test_very_large_source_is_left_to_the_server(page) -> None:
    data = encode(Image.new("L", (7000, 6000), 128), "PNG")
    res = local(page, "huge.png", data, x=0, y=0, width=100, height=100)
    assert [a["reason"] for a in res["asked"]] == [TOO_BIG] and res["fetches"] == []


def test_crop_region_above_the_safe_canvas_area_is_left_to_the_server(page) -> None:
    data = encode(Image.new("L", (4200, 4200), 128), "PNG")
    res = local(page, "big.png", data, x=0, y=0, width=4200, height=4200)
    assert [a["reason"] for a in res["asked"]] == [TOO_BIG] and res["fetches"] == []


def test_a_large_source_with_a_small_box_stays_local(page) -> None:
    data = encode(plain(5000, 3000), "PNG")  # 15 MP: inside both budgets
    img = open_local(local(page, "wide.png", data, x=4000, y=2000, width=300, height=200))
    assert img.size == (300, 200)
    assert np.array_equal(np.asarray(img.convert("RGB")), np.asarray(plain(5000, 3000).crop((4000, 2000, 4300, 2200))))
