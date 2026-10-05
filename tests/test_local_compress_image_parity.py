"""Work package 27 (audit): on-device Compress Image equals the server's Pillow compress.

Runs the shipped `ops-image.js` in headless Chromium (harness: test_local_resize_image_parity.py).
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from PIL import Image

from scripts.image_utils import compress_image
from test_local_resize_image_parity import (  # noqa: F401  (page is a fixture)
    TOO_BIG,
    UNDECODABLE,
    encode,
    natural,
    open_local,
    page,
    plain,
    run_local,
)

ROUTE = "/api/image/compress"
PHOTO = natural(1000, 750)


def local(page, name, data, **fields):
    r = run_local(page, name, data, fields, route=ROUTE)
    return r


def server(tmp_path: Path, name: str, data: bytes, **kw) -> dict:
    src = tmp_path / name
    src.write_bytes(data)
    out = tmp_path / "srv"
    out.mkdir(exist_ok=True)
    return compress_image(str(src), str(out), **kw)


def post(auth_client, name, data, **fields):
    return auth_client.post(ROUTE, files={"file": (name, data, "image/jpeg")}, data={k: str(v) for k, v in fields.items()})


@pytest.mark.parametrize("ext,pil", [("jpg", "JPEG"), ("webp", "WEBP")])
@pytest.mark.parametrize("quality", [10, 40, 70, 90])
def test_lossy_formats_shrink_and_keep_their_format(page, tmp_path, ext, pil, quality) -> None:
    data = encode(PHOTO, pil, quality=98)
    res = local(page, f"big.{ext}", data, quality=quality)
    img = open_local(res)
    srv = server(tmp_path, f"big.{ext}", data, quality=quality)
    assert img.format == Image.open(srv["output_path"]).format == pil
    assert img.size == (1000, 750)
    size = len(res["bytes"])
    assert size < len(data) and srv["compressed_size"] < len(data)
    assert res["message"] == "Image compressed"
    # Chromium's encoders are less efficient than Pillow's optimised ones, so the saving may be smaller.
    assert size <= srv["compressed_size"] * 1.5, (size, srv["compressed_size"])


def test_result_fields_are_the_servers(page, auth_client, tmp_path) -> None:
    data = encode(PHOTO, "JPEG", quality=98)
    res = page.evaluate(
        """async ([b64, route]) => {
          window.__fetches.length = 0;
          const bin = Uint8Array.from(atob(b64), c => c.charCodeAt(0));
          const fd = new FormData(); fd.append('file', new File([bin], 'a.jpg', {type: 'image/jpeg'})); fd.append('quality', '50');
          const r = await ffProcess(route, fd); return await r.json(); }""",
        [__import__("base64").b64encode(data).decode(), ROUTE],
    )
    resp = post(auth_client, "a.jpg", data, quality=50).json()
    assert set(res) >= {"status", "message", "filename", "download_token", "original_size", "compressed_size", "reduction_pct"}
    assert set(resp) <= set(res) | {"download_url"}, set(resp) - set(res)
    assert res["original_size"] == len(data) == resp["original_size"]
    assert res["filename"] == resp["filename"]
    expected = round(max(0.0, (1 - res["compressed_size"] / res["original_size"]) * 100), 1)
    assert res["reduction_pct"] == expected


@pytest.mark.parametrize("original,compressed", [(1000, 877), (1000, 875), (1000, 925), (400, 399), (3, 2), (8, 7), (40, 31), (200, 179)])
def test_reduction_percentage_rounds_like_python(page, original, compressed) -> None:
    """Python's round() is half-to-even on exact binary ties and exact on the decimal string otherwise."""
    got = page.evaluate(
        "([o, c]) => ffLocal.image.pyRound1(Math.max(0, (1 - c / o) * 100))", [original, compressed]
    )
    assert got == round(max(0.0, (1 - compressed / original) * 100), 1)


def test_already_optimised_file_is_kept_byte_for_byte(page, tmp_path) -> None:
    data = encode(PHOTO.resize((200, 150)), "JPEG", quality=8)
    res = local(page, "tiny.jpg", data, quality=95)
    srv = server(tmp_path, "tiny.jpg", data, quality=95)
    assert res["status"] == 200 and res["fetches"] == []
    assert res["message"] == "Already optimized; original kept"
    assert res["bytes"] == data
    assert srv["compressed_size"] == srv["original_size"] == len(data) and srv["reduction_pct"] == 0.0
    assert res["body"]["reduction_pct"] == 0.0 and res["body"]["compressed_size"] == len(data)


def test_png_at_default_quality_is_palette_reduced_by_the_server_so_it_asks(page, tmp_path) -> None:
    """Default quality is 70, below 90: Pillow quantises PNG to a palette, which a canvas cannot do."""
    data = encode(natural(500, 400), "PNG")
    res = local(page, "shot.png", data)
    assert res["status"] != 200 and len(res["asked"]) == 1 and res["fetches"] == []
    assert Image.open(server(tmp_path, "shot.png", data)["output_path"]).mode == "P"


def test_png_at_high_quality_never_returns_a_larger_file(page, tmp_path) -> None:
    data = encode(natural(500, 400), "PNG")
    res = local(page, "shot.png", data, quality=95)
    img = open_local(res)
    assert img.format == "PNG" and len(res["bytes"]) <= len(data)
    assert list(img.convert("RGB").getdata()) == list(Image.open(io.BytesIO(data)).convert("RGB").getdata())  # lossless


def test_default_quality_is_seventy(page) -> None:
    data = encode(PHOTO, "JPEG", quality=98)
    default = local(page, "d.jpg", data)
    explicit = local(page, "d.jpg", data, quality=70)
    assert default["bytes"] == explicit["bytes"]


def test_exif_orientation_is_baked_in_when_the_file_is_recompressed(page) -> None:
    exif = Image.Exif()
    exif[0x0112] = 6
    data = encode(natural(900, 600), "JPEG", quality=98, exif=exif.tobytes())
    img = open_local(local(page, "p.jpg", data, quality=40))
    assert img.size == (600, 900)


@pytest.mark.parametrize("quality", [0, -1, 96, 100])
def test_quality_range_uses_the_servers_words(page, auth_client, quality) -> None:
    data = encode(PHOTO, "JPEG")
    res = local(page, "a.jpg", data, quality=quality)
    resp = post(auth_client, "a.jpg", data, quality=quality)
    assert resp.status_code == 422 and res["status"] == 400
    assert res["detail"] == resp.json()["detail"] and res["fetches"] == [] and res["asked"] == []


@pytest.mark.parametrize("name", ["photo.jpg", "My Photo.final.JPEG", "файл.webp", "a.b.c.jpg", "UPPER.JPG"])
def test_branded_filename_matches_the_server(page, tmp_path, name) -> None:
    ext = Path(name).suffix.lower().lstrip(".")
    pil = {"jpg": "JPEG", "jpeg": "JPEG", "webp": "WEBP"}[ext]
    data = encode(PHOTO, pil, quality=98)
    res = local(page, name, data, quality=50)
    assert res["status"] == 200, res
    assert Path(server(tmp_path, name, data, quality=50)["output_path"]).name == res["filename"]


def test_kept_original_uses_the_original_extension_in_the_name(page, tmp_path) -> None:
    data = encode(PHOTO.resize((120, 90)).convert("P"), "GIF")
    res = local(page, "anim.gif", data, quality=95)
    srv = server(tmp_path, "anim.gif", data, quality=95)
    assert res["status"] == 200 and res["bytes"] == data
    assert res["filename"] == Path(srv["output_path"]).name and res["filename"].endswith(".gif")


def test_undecodable_and_oversized_inputs_ask_before_any_upload(page) -> None:
    tiff = local(page, "scan.tiff", encode(natural(80, 60), "TIFF"), quality=50)
    assert [a["reason"] for a in tiff["asked"]] == [UNDECODABLE] and tiff["fetches"] == []
    huge = local(page, "huge.jpg", encode(Image.new("L", (7000, 6000), 128), "JPEG"), quality=50)
    assert [a["reason"] for a in huge["asked"]] == [TOO_BIG] and huge["fetches"] == []
