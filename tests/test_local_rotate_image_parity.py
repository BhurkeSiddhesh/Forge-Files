"""Work package 29 (audit): on-device Rotate Image equals the server's Pillow rotate.

Runs the shipped `ops-image.js` in headless Chromium (harness: test_local_resize_image_parity.py).
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from scripts.image_utils import rotate_image
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

ROUTE = "/api/image/rotate"
PHOTO = natural(300, 200)
FORMATS = [("jpg", "JPEG"), ("png", "PNG"), ("webp", "WEBP")]


def local(page, name, data, **fields):
    return run_local(page, name, data, fields, route=ROUTE)


def server(tmp_path: Path, name: str, data: bytes, **kw) -> Path:
    src = tmp_path / name
    src.write_bytes(data)
    out = tmp_path / "srv"
    out.mkdir(exist_ok=True)
    return Path(rotate_image(str(src), str(out), **kw))


def post(auth_client, name, data, **fields):
    return auth_client.post(ROUTE, files={"file": (name, data, "image/png")}, data={k: str(v) for k, v in fields.items()})


def rgba(im: Image.Image) -> np.ndarray:
    return np.asarray(im.convert("RGBA")).astype(float)


def mae(a: Image.Image, b: Image.Image) -> float:
    assert a.size == b.size, (a.size, b.size)
    return float(np.abs(rgba(a) - rgba(b)).mean())


@pytest.mark.parametrize("angle", [90, 180, 270, -90, 360, 450, 0])
def test_right_angles_are_pixel_exact_for_png(page, tmp_path, angle) -> None:
    data = encode(PHOTO, "PNG")
    img = open_local(local(page, "in.png", data, angle=angle))
    srv = Image.open(server(tmp_path, "in.png", data, angle=angle))
    assert img.size == srv.size
    solid = np.asarray(img.convert("RGB")).astype(int)
    assert np.array_equal(solid, np.asarray(srv.convert("RGB")).astype(int))


@pytest.mark.parametrize("ext,pil", FORMATS)
@pytest.mark.parametrize("angle", [30, 45, -20, 15.5, 100, 1, 179.5])
def test_arbitrary_angles_have_the_same_bounds_and_corners(page, tmp_path, ext, pil, angle) -> None:
    data = encode(PHOTO, pil)
    img = open_local(local(page, f"in.{ext}", data, angle=angle))
    srv = Image.open(server(tmp_path, f"in.{ext}", data, angle=angle))
    assert (img.format, img.size) == (srv.format, srv.size)
    # Expanded corners: black for an opaque source (Pillow's fill), exactly like the server.
    a, b = rgba(img), rgba(srv)
    for yx in ((0, 0), (0, -1), (-1, 0), (-1, -1)):
        assert np.abs(a[yx] - b[yx]).max() <= 40, (yx, a[yx], b[yx])
    assert mae(img, srv) < 8  # different interpolation at the rotated edges and in the photo texture


@pytest.mark.parametrize("ext,pil", [("png", "PNG"), ("webp", "WEBP")])
def test_transparent_source_keeps_transparent_corners(page, tmp_path, ext, pil) -> None:
    data = encode(with_hole(natural(200, 120)), pil, **({"lossless": True} if pil == "WEBP" else {}))
    img = open_local(local(page, f"a.{ext}", data, angle=30))
    srv = Image.open(server(tmp_path, f"a.{ext}", data, angle=30))
    assert img.size == srv.size and img.mode == srv.mode == "RGBA"
    assert img.getpixel((0, 0))[3] == srv.getpixel((0, 0))[3] == 0
    assert img.getpixel((img.width // 2, img.height // 2))[3] == 0  # the original hole is still a hole


def test_transparent_png_saved_as_jpeg_is_white_in_corners(page, tmp_path) -> None:
    data = encode(with_hole(natural(200, 120)), "PNG")
    img = open_local(local(page, "x.jpg", data, angle=30))
    srv = Image.open(server(tmp_path, "x.jpg", data, angle=30))
    assert img.format == srv.format == "JPEG" and img.size == srv.size
    assert min(img.getpixel((0, 0))) > 235 and min(srv.getpixel((0, 0))) > 235


def test_exif_orientation_is_applied_exactly_once_before_the_angle(page, tmp_path) -> None:
    exif = Image.Exif()
    exif[0x0112] = 6
    data = encode(natural(300, 200), "JPEG", exif=exif.tobytes())
    img = open_local(local(page, "p.jpg", data, angle=90))
    srv = Image.open(server(tmp_path, "p.jpg", data, angle=90))
    assert img.size == srv.size == (300, 200)  # 200x300 after EXIF, then 90 turns it back to landscape
    assert mae(img, srv) < 8


@pytest.mark.parametrize("angle", ["90", "-45", "12.5", "360", "0.5", "1e2"])
def test_message_and_filename_match_the_route(page, auth_client, angle) -> None:
    data = encode(natural(120, 90), "PNG")
    res = local(page, "My Pic.final.PNG", data, angle=angle)
    resp = post(auth_client, "My Pic.final.PNG", data, angle=angle)
    assert resp.status_code == 200 and res["status"] == 200
    assert res["message"] == resp.json()["message"]
    assert res["filename"] == resp.json()["filename"]


def test_default_angle_is_ninety(page, tmp_path) -> None:
    data = encode(PHOTO, "PNG")
    img = open_local(local(page, "in.png", data))
    assert img.size == (200, 300)


@pytest.mark.parametrize("name", ["photo.jpg", "файл.png", "noextension", "a.b.c.webp", "UPPER.JPEG"])
def test_branded_filename_matches_the_server(page, tmp_path, name) -> None:
    ext = Path(name).suffix.lower().lstrip(".")
    pil = {"jpg": "JPEG", "jpeg": "JPEG", "png": "PNG", "webp": "WEBP"}.get(ext, "JPEG")
    data = encode(natural(120, 90), pil)
    res = local(page, name, data, angle=90)
    assert res["status"] == 200, res
    assert res["filename"] == server(tmp_path, name, data, angle=90).name


@pytest.mark.parametrize("quality", [0, 96, 100, -3])
def test_quality_range_uses_the_servers_words(page, auth_client, quality) -> None:
    data = encode(natural(120, 90), "PNG")
    res = local(page, "in.png", data, angle=90, quality=quality)
    resp = post(auth_client, "in.png", data, angle=90, quality=quality)
    assert resp.status_code == 422 and res["status"] == 400
    assert res["detail"] == resp.json()["detail"] and res["fetches"] == []


def test_png_below_quality_90_needs_the_servers_palette_reduction(page, tmp_path) -> None:
    """Pillow quantises PNG output below quality 90; a canvas cannot, so it asks instead of returning a different file."""
    data = encode(natural(120, 90), "PNG")
    res = local(page, "in.png", data, angle=90, quality=60)
    assert res["status"] != 200 and len(res["asked"]) == 1 and res["fetches"] == []
    assert Image.open(server(tmp_path, "in.png", data, angle=90, quality=60)).mode == "P"


def test_non_numeric_angle_is_rejected_without_upload(page) -> None:
    res = local(page, "in.png", encode(plain(60, 40), "PNG"), angle="sideways")
    assert res["status"] == 400 and res["fetches"] == [] and res["asked"] == []


def test_undecodable_input_asks_before_any_upload(page) -> None:
    res = local(page, "scan.tiff", encode(natural(80, 60), "TIFF"), angle=90)
    assert [a["reason"] for a in res["asked"]] == [UNDECODABLE] and res["fetches"] == []


def test_very_large_source_and_large_output_are_left_to_the_server(page) -> None:
    huge = local(page, "huge.png", encode(Image.new("L", (7000, 6000), 128), "PNG"), angle=90)
    assert [a["reason"] for a in huge["asked"]] == [TOO_BIG] and huge["fetches"] == []
    big = local(page, "big.png", encode(Image.new("L", (3000, 3000), 128), "PNG"), angle=45)  # 4243 px square: 18 MP
    assert [a["reason"] for a in big["asked"]] == [TOO_BIG] and big["fetches"] == []


def test_palette_image_at_a_non_right_angle_goes_to_the_server_but_right_angles_stay_local(page, tmp_path) -> None:
    """Pillow rotates mode P with nearest-neighbour and an index-0 fill; only exact turns are safe here."""
    data = encode(natural(120, 90).quantize(32), "PNG")
    odd = local(page, "pal.png", data, angle=30, quality=95)
    assert odd["status"] != 200 and len(odd["asked"]) == 1 and odd["fetches"] == []
    img = open_local(local(page, "pal.png", data, angle=90, quality=95))
    assert img.size == Image.open(server(tmp_path, "pal.png", data, angle=90, quality=95)).size == (90, 120)
