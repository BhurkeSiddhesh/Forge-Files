"""Work package 28 (audit): on-device Convert Image equals the server's Pillow conversion.

Runs the shipped `ops-image.js` in headless Chromium (harness: test_local_resize_image_parity.py).
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from scripts.image_utils import convert_image_format
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

ROUTE = "/api/image/convert"
PHOTO = natural(320, 240)
PIL = {"jpg": "JPEG", "jpeg": "JPEG", "png": "PNG", "webp": "WEBP"}


def local(page, name, data, **fields):
    return run_local(page, name, data, fields, route=ROUTE)


def server(tmp_path: Path, name: str, data: bytes, **kw) -> Path:
    src = tmp_path / name
    src.write_bytes(data)
    out = tmp_path / "srv"
    out.mkdir(exist_ok=True)
    return Path(convert_image_format(str(src), str(out), **kw))


def post(auth_client, name, data, **fields):
    return auth_client.post(ROUTE, files={"file": (name, data, "image/png")}, data={k: str(v) for k, v in fields.items()})


def over_white(im: Image.Image) -> np.ndarray:
    rgba = im.convert("RGBA")
    bg = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
    bg.alpha_composite(rgba)
    return np.asarray(bg.convert("RGB")).astype(float)


@pytest.mark.parametrize("src", ["jpg", "png", "webp"])
@pytest.mark.parametrize("target", ["jpg", "jpeg", "png", "webp", "PNG", "Jpg"])
def test_every_source_to_every_target(page, tmp_path, src, target) -> None:
    data = encode(PHOTO, PIL[src])
    q = 95 if target.lower() == "png" else 90
    res = local(page, f"in.{src}", data, target_format=target, quality=q)
    img = open_local(res)
    srv_path = server(tmp_path, f"in.{src}", data, target_format=target, quality=q)
    srv = Image.open(srv_path)
    assert (img.format, img.size) == (srv.format, srv.size)
    assert res["filename"] == srv_path.name
    assert res["message"] == f"Converted to {target.upper()}"
    assert float(np.abs(over_white(img) - over_white(srv)).mean()) <= 10  # both decode the same source


@pytest.mark.parametrize("target", ["png", "webp"])
def test_alpha_survives_conversion_to_alpha_formats(page, tmp_path, target) -> None:
    data = encode(with_hole(natural(300, 200)), "PNG")
    img = open_local(local(page, "a.png", data, target_format=target, quality=95))
    srv = Image.open(server(tmp_path, "a.png", data, target_format=target, quality=95))
    assert img.mode == srv.mode == "RGBA"
    assert img.getpixel((150, 100))[3] == 0 and img.getpixel((2, 2))[3] == 255


def test_alpha_is_flattened_onto_white_for_jpeg(page, tmp_path) -> None:
    data = encode(with_hole(natural(300, 200)), "PNG")
    img = open_local(local(page, "a.png", data, target_format="jpg"))
    srv = Image.open(server(tmp_path, "a.png", data, target_format="jpg"))
    assert img.format == srv.format == "JPEG"
    assert min(img.getpixel((150, 100))) > 240 and min(srv.getpixel((150, 100))) > 240


def test_palette_gif_with_transparency_becomes_rgba_png(page, tmp_path) -> None:
    im = Image.new("P", (120, 90), 1)
    im.putpalette([255, 0, 0, 0, 0, 255] + [0] * 756)
    data = encode(im, "GIF", transparency=0)
    img = open_local(local(page, "sticker.gif", data, target_format="png", quality=95))
    srv = Image.open(server(tmp_path, "sticker.gif", data, target_format="png", quality=95))
    assert img.size == srv.size and img.mode == "RGBA"


@pytest.mark.parametrize("ext,pil", [("gif", "GIF"), ("bmp", "BMP")])
def test_gif_and_bmp_sources_convert_to_png_and_jpeg(page, tmp_path, ext, pil) -> None:
    data = encode(natural(160, 120).convert("P" if pil == "GIF" else "RGB"), pil)
    for target in ("png", "jpg"):
        img = open_local(local(page, f"in.{ext}", data, target_format=target, quality=95))
        srv = Image.open(server(tmp_path, f"in.{ext}", data, target_format=target, quality=95))
        assert (img.format, img.size) == (srv.format, srv.size)


def test_exif_orientation_is_baked_in(page, tmp_path) -> None:
    exif = Image.Exif()
    exif[0x0112] = 6
    data = encode(natural(300, 200), "JPEG", exif=exif.tobytes())
    img = open_local(local(page, "p.jpg", data, target_format="png", quality=95))
    srv = Image.open(server(tmp_path, "p.jpg", data, target_format="png", quality=95))
    assert img.size == srv.size == (200, 300)


def test_default_quality_is_ninety(page) -> None:
    data = encode(PHOTO, "PNG")
    default = local(page, "a.png", data, target_format="jpg")
    assert default["bytes"] == local(page, "a.png", data, target_format="jpg", quality=90)["bytes"]


@pytest.mark.parametrize(
    "fields",
    [
        {"target_format": "gif"},
        {"target_format": ""},
        {"target_format": "constructor"},
        {"target_format": "__proto__"},
        {"target_format": "png", "quality": 0},
        {"target_format": "png", "quality": 96},
    ],
    ids=lambda f: ",".join(f"{k}={v}" for k, v in f.items()),
)
def test_rejections_use_the_servers_words(page, auth_client, fields) -> None:
    data = encode(natural(120, 90), "PNG")
    res = local(page, "in.png", data, **fields)
    resp = post(auth_client, "in.png", data, **fields)
    assert resp.status_code >= 400 and res["status"] == 400
    assert res["fetches"] == [] and res["asked"] == []
    detail = resp.json()["detail"]
    if isinstance(detail, str):
        assert res["detail"] == detail


def test_inherited_object_names_in_a_filename_are_not_formats(page, tmp_path) -> None:
    data = encode(natural(120, 90), "PNG")
    res = local(page, "photo.constructor", data, target_format="webp")
    srv = server(tmp_path, "photo.constructor", data, target_format="webp")
    assert res["status"] == 200 and res["filename"] == srv.name


@pytest.mark.parametrize("name", ["photo.jpg", "My Photo.final.JPEG", "файл.png", "noextension", "a.b.c.webp"])
def test_branded_filename_matches_the_server(page, tmp_path, name) -> None:
    ext = Path(name).suffix.lower().lstrip(".")
    data = encode(natural(120, 90), PIL.get(ext, "JPEG"))
    res = local(page, name, data, target_format="webp")
    assert res["status"] == 200 and res["filename"] == server(tmp_path, name, data, target_format="webp").name


def test_png_below_quality_90_needs_the_servers_palette_reduction(page, tmp_path) -> None:
    data = encode(natural(200, 150), "JPEG")
    res = local(page, "a.jpg", data, target_format="png", quality=60)
    assert res["status"] != 200 and len(res["asked"]) == 1 and res["fetches"] == []
    assert Image.open(server(tmp_path, "a.jpg", data, target_format="png", quality=60)).mode == "P"


def test_undecodable_and_oversized_inputs_ask_before_any_upload(page) -> None:
    tiff = local(page, "scan.tiff", encode(natural(80, 60), "TIFF"), target_format="jpg")
    assert [a["reason"] for a in tiff["asked"]] == [UNDECODABLE] and tiff["fetches"] == []
    huge = local(page, "huge.png", encode(Image.new("L", (7000, 6000), 128), "PNG"), target_format="jpg")
    assert [a["reason"] for a in huge["asked"]] == [TOO_BIG] and huge["fetches"] == []
    big = local(page, "big.png", encode(Image.new("L", (4200, 4200), 128), "PNG"), target_format="webp")
    assert [a["reason"] for a in big["asked"]] == [TOO_BIG] and big["fetches"] == []
