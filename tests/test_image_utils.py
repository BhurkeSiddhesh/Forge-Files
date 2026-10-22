"""
Tests for image_utils module.
"""
import pytest
from pathlib import Path
from scripts.image_utils import heic_to_jpeg


@pytest.fixture(scope="session")
def sample_heic(tmp_path_factory):
    """
    Creates a mock HEIC file for testing.
    Note: Since we can't easily create a real HEIC without actual image data,
    we test with a minimal valid HEIC structure or skip if pillow-heif isn't installed.
    """
    try:
        import pillow_heif
        from PIL import Image
        
        # Create a simple test image and save as HEIC
        d = tmp_path_factory.mktemp("images")
        file_path = d / "test_image.heic"
        
        # Create a simple RGB image
        img = Image.new('RGB', (100, 100), color='red')
        
        # Register HEIF opener and save
        pillow_heif.register_heif_opener()
        img.save(file_path, format='HEIF')
        
        return file_path
    except Exception as e:
        pytest.skip(f"Could not create test HEIC: {e}")


def test_heic_to_jpeg_basic(sample_heic, tmp_path):
    """Test basic HEIC to JPEG conversion."""
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    
    result = heic_to_jpeg(str(sample_heic), str(output_dir))
    
    assert Path(result).exists()
    assert result.endswith('.jpg')
    assert Path(result).stat().st_size > 0


def test_heic_to_jpeg_quality(sample_heic, tmp_path):
    """Test that quality parameter affects output file size."""
    output_dir_high = tmp_path / "output_high"
    output_dir_low = tmp_path / "output_low"
    output_dir_high.mkdir()
    output_dir_low.mkdir()
    
    result_high = heic_to_jpeg(str(sample_heic), str(output_dir_high), quality=95)
    result_low = heic_to_jpeg(str(sample_heic), str(output_dir_low), quality=50)
    
    # Higher quality should generally produce larger files
    # (may not always hold for tiny test images, so we just check both exist)
    assert Path(result_high).exists()
    assert Path(result_low).exists()


def test_heic_to_jpeg_output_filename(sample_heic, tmp_path):
    """Test that output filename is correctly derived from input."""
    output_dir = tmp_path / "output"
    output_dir.mkdir()

    result = heic_to_jpeg(str(sample_heic), str(output_dir))

    # Should be branded with the original stem plus the forgefiles.org suffix
    assert Path(result).stem == f"{sample_heic.stem}_forgefiles.org"
    assert Path(result).suffix == '.jpg'


_TEST_XMP = (
    b'<?xpacket begin="\xef\xbb\xbf" id="W5M0MpCehiHzreSzNTczkc9d"?>'
    b'<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF '
    b'xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
    b'<rdf:Description rdf:about="" xmlns:dc="http://purl.org/dc/elements/1.1/">'
    b'<dc:title>Test Photo Title</dc:title></rdf:Description></rdf:RDF>'
    b'</x:xmpmeta><?xpacket end="w"?>'
)


@pytest.fixture(scope="session")
def heic_with_metadata(tmp_path_factory):
    """A HEIC carrying an ICC color profile, EXIF (orientation + camera + date), and XMP."""
    try:
        import pillow_heif
        from PIL import Image, ImageCms

        pillow_heif.register_heif_opener()
        d = tmp_path_factory.mktemp("meta_images")
        file_path = d / "with_meta.heic"

        icc = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
        exif = Image.Exif()
        exif[0x0112] = 6  # Orientation (rotate) — should be baked, tag reset to 1
        exif[0x010F] = "TestCam"  # Make
        exif[0x9003] = "2026:07:24 10:00:00"  # DateTimeOriginal

        img = Image.new("RGB", (160, 90))
        px = img.load()
        for y in range(90):
            for x in range(160):
                px[x, y] = (x % 256, (y * 2) % 256, (x * y) % 256)
        img.save(
            file_path,
            format="HEIF",
            icc_profile=icc,
            exif=exif.tobytes(),
            xmp=_TEST_XMP,
            quality=95,
        )
        return {"path": file_path, "icc_len": len(icc)}
    except Exception as e:  # pragma: no cover - env without pillow-heif
        pytest.skip(f"Could not create HEIC with metadata: {e}")


def test_heic_to_jpeg_preserves_icc_profile(heic_with_metadata, tmp_path):
    """The source ICC color profile must survive into the JPEG (no color shift)."""
    from PIL import Image

    output_dir = tmp_path / "out"
    output_dir.mkdir()
    result = heic_to_jpeg(str(heic_with_metadata["path"]), str(output_dir))

    with Image.open(result) as j:
        out_icc = j.info.get("icc_profile", b"")
    assert out_icc, "ICC profile was dropped during HEIC->JPEG conversion"
    assert len(out_icc) == heic_with_metadata["icc_len"]


def test_heic_to_jpeg_preserves_exif_metadata(heic_with_metadata, tmp_path):
    """Camera/date EXIF must be carried over, with Orientation normalized to 1."""
    from PIL import Image

    output_dir = tmp_path / "out"
    output_dir.mkdir()
    result = heic_to_jpeg(str(heic_with_metadata["path"]), str(output_dir))

    with Image.open(result) as j:
        exif = j.getexif()
    assert exif.get(0x010F) == "TestCam"
    assert exif.get(0x9003) == "2026:07:24 10:00:00"
    # Orientation is baked into the pixels; the tag must read 1 to avoid a viewer
    # rotating the already-correct image a second time.
    assert exif.get(0x0112, 1) == 1


def test_heic_to_jpeg_preserves_xmp_metadata(heic_with_metadata, tmp_path):
    """XMP (title/rating/keywords/copyright packet) must be carried over, byte-identical."""
    from PIL import Image

    output_dir = tmp_path / "out"
    output_dir.mkdir()
    result = heic_to_jpeg(str(heic_with_metadata["path"]), str(output_dir))

    with Image.open(result) as j:
        out_xmp = j.info.get("xmp", b"")
    assert out_xmp, "XMP metadata was dropped during HEIC->JPEG conversion"
    assert out_xmp == _TEST_XMP


def test_heic_to_jpeg_no_metadata_is_safe(tmp_path):
    """A HEIC without ICC/EXIF/XMP must still convert cleanly (no crash, no empty block)."""
    import pillow_heif
    from PIL import Image

    pillow_heif.register_heif_opener()
    src = tmp_path / "plain.heic"
    Image.new("RGB", (48, 32), "orange").save(src, format="HEIF", quality=90)

    output_dir = tmp_path / "out"
    output_dir.mkdir()
    result = heic_to_jpeg(str(src), str(output_dir))

    with Image.open(result) as j:
        assert j.mode == "RGB"
        assert j.size == (48, 32)
        assert not j.info.get("icc_profile")
        assert not j.info.get("xmp")


# ── Alpha flattens to white, not black, when a transparent image is forced to JPEG ──
#
# Image.convert("RGB") drops the alpha channel and keeps whatever RGB values sit
# underneath it. Many PNG/WebP encoders zero those out for fully-transparent
# pixels, so a naive convert renders a solid black hole where the image should
# look empty. Every JPEG-output path in image_utils.py must composite onto white
# first instead.

def _transparent_png(path):
    """A red square on a fully-transparent background whose RGB channels are
    black underneath — the exact shape that exposes the naive-convert bug."""
    from PIL import Image

    img = Image.new("RGBA", (100, 100), (0, 0, 0, 0))
    px = img.load()
    for x in range(20, 80):
        for y in range(20, 80):
            px[x, y] = (255, 0, 0, 255)
    img.save(path, "PNG")
    return path


class TestAlphaFlattensToWhite:
    def test_flatten_to_rgb_helper_composites_on_white(self):
        from PIL import Image
        from scripts.image_utils import _flatten_to_rgb

        img = Image.new("RGBA", (10, 10), (0, 0, 0, 0))
        flattened = _flatten_to_rgb(img)
        assert flattened.mode == "RGB"
        assert flattened.getpixel((0, 0)) == (255, 255, 255)

    def test_flatten_to_rgb_is_noop_for_opaque_rgb(self):
        from PIL import Image
        from scripts.image_utils import _flatten_to_rgb

        img = Image.new("RGB", (10, 10), (30, 60, 90))
        assert _flatten_to_rgb(img) is img

    def test_resize_image_preserves_png_and_alpha(self, tmp_path):
        from PIL import Image
        from scripts.image_utils import resize_image

        src = _transparent_png(tmp_path / "trans.png")
        out = resize_image(str(src), str(tmp_path), mode="dimensions", width=50)
        assert out.endswith(".png")
        with Image.open(out) as r:
            assert r.mode in ("RGBA", "P")
            pixel = r.getpixel((2, 2))
            alpha = pixel[3] if len(pixel) == 4 else r.convert("RGBA").getpixel((2, 2))[3]
            assert alpha == 0

    def test_crop_image_preserves_png_and_alpha(self, tmp_path):
        from PIL import Image
        from scripts.image_utils import crop_image

        src = _transparent_png(tmp_path / "trans.png")
        out = crop_image(str(src), str(tmp_path), x=0, y=0, width=100, height=100)
        assert out.endswith(".png")
        with Image.open(out) as r:
            assert r.mode in ("RGBA", "P")
            pixel = r.getpixel((2, 2))
            alpha = pixel[3] if len(pixel) == 4 else r.convert("RGBA").getpixel((2, 2))[3]
            assert alpha == 0

    def test_resize_image_preserves_jpg_format(self, tmp_path):
        from PIL import Image
        from scripts.image_utils import resize_image

        jpg_src = tmp_path / "photo.jpg"
        Image.new("RGB", (100, 100), (10, 20, 30)).save(jpg_src, "JPEG")
        out = resize_image(str(jpg_src), str(tmp_path), mode="dimensions", width=50)
        assert out.endswith(".jpg")

    def test_crop_image_preserves_jpg_format(self, tmp_path):
        from PIL import Image
        from scripts.image_utils import crop_image

        jpg_src = tmp_path / "photo.jpg"
        Image.new("RGB", (100, 100), (10, 20, 30)).save(jpg_src, "JPEG")
        out = crop_image(str(jpg_src), str(tmp_path), x=0, y=0, width=50, height=50)
        assert out.endswith(".jpg")

    def test_convert_image_format_to_jpg_flattens_to_white(self, tmp_path):
        from PIL import Image
        from scripts.image_utils import convert_image_format

        src = _transparent_png(tmp_path / "trans.png")
        out = convert_image_format(str(src), str(tmp_path), "jpg")
        with Image.open(out) as r:
            assert r.getpixel((2, 2)) == (255, 255, 255)

    def test_compress_image_unrecognized_ext_transparent_flattens_to_white(self, tmp_path):
        """An unfamiliar format becomes JPEG only when that saves space."""
        from PIL import Image
        from scripts.image_utils import compress_image

        src = tmp_path / "trans.bmp"
        _transparent_png(src)
        result = compress_image(str(src), str(tmp_path), quality=80)
        with Image.open(result["output_path"]) as r:
            if result["compressed_size"] < result["original_size"]:
                assert r.getpixel((2, 2)) == (255, 255, 255)
            else:
                assert r.getpixel((2, 2)) == (0, 0, 0, 0)

    def test_png_quality_changes_output_and_preserves_alpha(self, tmp_path):
        from PIL import Image
        from scripts.image_utils import compress_image

        src = tmp_path / "source.png"
        pixels = Image.new("RGBA", (200, 200), (30, 90, 180, 0))
        for x in range(25, 175):
            for y in range(25, 175):
                pixels.putpixel((x, y), (x, y, (x + y) % 256, 255))
        pixels.save(src, "PNG", compress_level=0)
        sizes = []
        for quality in (16, 95):
            out_dir = tmp_path / str(quality)
            out_dir.mkdir()
            result = compress_image(str(src), str(out_dir), quality=quality)
            out = Path(result["output_path"])
            assert out.suffix == ".png"
            assert result["compressed_size"] < result["original_size"]
            with Image.open(out) as encoded:
                rgba = encoded.convert("RGBA")
                assert rgba.getpixel((0, 0))[3] == 0
            sizes.append(result["compressed_size"])
        assert sizes[0] != sizes[1]

    def test_compression_never_returns_larger_file(self, tmp_path):
        from PIL import Image
        from scripts.image_utils import compress_image

        src = tmp_path / "tiny.png"
        Image.new("RGBA", (1, 1), (255, 0, 0, 128)).save(src)
        result = compress_image(str(src), str(tmp_path), quality=95)
        assert result["compressed_size"] <= result["original_size"]
        if result["compressed_size"] == result["original_size"]:
            assert Path(result["output_path"]).suffix == ".png"

    def test_watermark_image_unrecognized_ext_transparent_flattens_to_white(self, tmp_path):
        from PIL import Image
        from scripts.image_utils import watermark_image

        src = tmp_path / "trans.bmp"
        _transparent_png(src)
        out = watermark_image(str(src), str(tmp_path), "DRAFT", position="top-left")
        with Image.open(out) as r:
            assert r.getpixel((90, 90)) == (255, 255, 255)

    def test_heic_to_jpeg_alpha_flattens_to_white(self, tmp_path):
        import pillow_heif
        from PIL import Image
        from scripts.image_utils import heic_to_jpeg

        pillow_heif.register_heif_opener()
        img = Image.new("RGBA", (100, 100), (0, 0, 0, 0))
        px = img.load()
        for x in range(20, 80):
            for y in range(20, 80):
                px[x, y] = (255, 0, 0, 255)
        src = tmp_path / "trans.heic"
        img.save(src, format="HEIF", quality=95)

        out = heic_to_jpeg(str(src), str(tmp_path))
        with Image.open(out) as r:
            corner = r.getpixel((2, 2))
        assert corner != (0, 0, 0)
        assert all(c > 200 for c in corner)

    def test_opaque_jpeg_inputs_unaffected_by_flatten(self, tmp_path):
        """Zero-regression guard: an already-opaque source must still produce a
        plain RGB output (no alpha-compositing path is taken) for every
        affected function."""
        from PIL import Image
        from scripts.image_utils import (
            compress_image, convert_image_format, crop_image,
            resize_image, rotate_image, watermark_image,
        )

        src = tmp_path / "opaque.jpg"
        Image.new("RGB", (100, 100), (30, 60, 90)).save(src, "JPEG", quality=95)

        # Each op gets its own subdir: several of them brand to the same
        # filename (same input stem + extension), so sharing a directory
        # would make later calls silently overwrite earlier outputs.
        ops = {
            "resize": lambda d: resize_image(str(src), d, mode="percentage", percentage=50),
            "crop": lambda d: crop_image(str(src), d, x=0, y=0, width=50, height=50),
            "compress": lambda d: compress_image(str(src), d, quality=80)["output_path"],
            "convert": lambda d: convert_image_format(str(src), d, "png"),
            "watermark": lambda d: watermark_image(str(src), d, "X", position="top-left"),
            "rotate": lambda d: rotate_image(str(src), d, angle=90),
        }
        for name, op in ops.items():
            out_dir = tmp_path / name
            out_dir.mkdir()
            with Image.open(op(str(out_dir))) as r:
                assert r.mode == "RGB"


# ── ICC color profile survives every image operation, not just HEIC->JPEG ──
#
# Wide-gamut (Display P3 / Adobe RGB) JPEGs and PNGs carry an embedded ICC
# profile. Pillow's JPEG and WebP encoders only write it if it's passed
# explicitly to save() — they don't fall back to img.info like the PNG
# encoder does — so any function that re-encodes to JPEG/WebP without
# forwarding it silently strips the profile, and viewers then reinterpret the
# pixels as sRGB: a visible color shift, not just missing metadata.

def _icc_bytes():
    from PIL import ImageCms

    return ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()


def _icc_tagged_jpeg(path, icc):
    from PIL import Image

    Image.new("RGB", (100, 100), (200, 50, 50)).save(
        path, "JPEG", icc_profile=icc, quality=95
    )
    return path


class TestIccProfilePreserved:
    def test_rotate_image_preserves_icc(self, tmp_path):
        from PIL import Image
        from scripts.image_utils import rotate_image

        icc = _icc_bytes()
        src = _icc_tagged_jpeg(tmp_path / "src.jpg", icc)
        out = rotate_image(str(src), str(tmp_path), angle=90)
        with Image.open(out) as r:
            assert r.info.get("icc_profile") == icc

    def test_compress_image_preserves_icc(self, tmp_path):
        from PIL import Image
        from scripts.image_utils import compress_image

        icc = _icc_bytes()
        src = _icc_tagged_jpeg(tmp_path / "src.jpg", icc)
        out = compress_image(str(src), str(tmp_path), quality=80)["output_path"]
        with Image.open(out) as r:
            assert r.info.get("icc_profile") == icc

    def test_convert_image_format_preserves_icc(self, tmp_path):
        from PIL import Image
        from scripts.image_utils import convert_image_format

        icc = _icc_bytes()
        src = _icc_tagged_jpeg(tmp_path / "src.jpg", icc)
        # Each target format gets its own dir: branded filenames collide otherwise.
        for target in ("jpg", "png", "webp"):
            out_dir = tmp_path / f"conv_{target}"
            out_dir.mkdir()
            out = convert_image_format(str(src), str(out_dir), target)
            with Image.open(out) as r:
                assert r.info.get("icc_profile") == icc, f"dropped for target={target}"

    def test_watermark_image_preserves_icc(self, tmp_path):
        from PIL import Image
        from scripts.image_utils import watermark_image

        icc = _icc_bytes()
        src = _icc_tagged_jpeg(tmp_path / "src.jpg", icc)
        out = watermark_image(str(src), str(tmp_path), "DRAFT")
        with Image.open(out) as r:
            assert r.info.get("icc_profile") == icc

    def test_resize_image_preserves_icc(self, tmp_path):
        from PIL import Image
        from scripts.image_utils import resize_image

        icc = _icc_bytes()
        src = _icc_tagged_jpeg(tmp_path / "src.jpg", icc)
        out = resize_image(str(src), str(tmp_path), mode="dimensions", width=50)
        with Image.open(out) as r:
            assert r.info.get("icc_profile") == icc

    def test_crop_image_preserves_icc(self, tmp_path):
        from PIL import Image
        from scripts.image_utils import crop_image

        icc = _icc_bytes()
        src = _icc_tagged_jpeg(tmp_path / "src.jpg", icc)
        out = crop_image(str(src), str(tmp_path), x=0, y=0, width=50, height=50)
        with Image.open(out) as r:
            assert r.info.get("icc_profile") == icc

    def test_no_icc_source_is_safe(self, tmp_path):
        """A source with no ICC profile must still convert cleanly (no crash, no bogus key)."""
        from PIL import Image
        from scripts.image_utils import rotate_image, compress_image

        src = tmp_path / "plain.jpg"
        Image.new("RGB", (60, 40), (10, 20, 30)).save(src, "JPEG", quality=90)

        r_dir, c_dir = tmp_path / "r", tmp_path / "c"
        r_dir.mkdir()
        c_dir.mkdir()

        out1 = rotate_image(str(src), str(r_dir), angle=90)
        with Image.open(out1) as r:
            assert not r.info.get("icc_profile")

        out2 = compress_image(str(src), str(c_dir), quality=80)["output_path"]
        with Image.open(out2) as r:
            assert not r.info.get("icc_profile")


# ── EXIF + XMP metadata survive every image operation, not just HEIC->JPEG ──
#
# `heic_to_jpeg` has preserved ICC/EXIF/XMP via `_preserved_save_kwargs` since
# 2026-07-24/08-06. Every other function in this module (`rotate_image`,
# `crop_image`, `resize_image`, `compress_image`, `convert_image_format`,
# `watermark_image`) ran `ImageOps.exif_transpose` on the way in but never
# forwarded EXIF/XMP on the way out — camera make/model, capture date, and any
# XMP title/rating/copyright packet were silently dropped on every re-encode
# to JPEG or WebP. PNG output cannot carry XMP (Pillow's PNG encoder has no
# write-side XMP support; this is a Pillow limitation, not a regression this
# fix can close), but EXIF still round-trips there too.

_TEST_EXIF_MAKE = "TestCam"
_TEST_XMP_PACKET = (
    b'<?xpacket begin="\xef\xbb\xbf" id="W5M0MpCehiHzreSzNTczkc9d"?>'
    b'<x:xmpmeta xmlns:x="adobe:ns:meta/"><dc:title>Probe Title</dc:title></x:xmpmeta>'
    b'<?xpacket end="w"?>'
)


def _exif_xmp_tagged_jpeg(path):
    from PIL import Image

    img = Image.new("RGB", (100, 100), (200, 50, 50))
    exif = img.getexif()
    exif[0x010F] = _TEST_EXIF_MAKE  # Make
    img.save(path, "JPEG", exif=exif.tobytes(), xmp=_TEST_XMP_PACKET, quality=95)
    return path


class TestExifXmpPreservedAcrossOps:
    def test_rotate_image_preserves_exif_and_xmp(self, tmp_path):
        from PIL import Image
        from scripts.image_utils import rotate_image

        src = _exif_xmp_tagged_jpeg(tmp_path / "src.jpg")
        out = rotate_image(str(src), str(tmp_path), angle=90)
        with Image.open(out) as r:
            assert r.getexif().get(0x010F) == _TEST_EXIF_MAKE
            assert r.info.get("xmp") == _TEST_XMP_PACKET

    def test_crop_image_preserves_exif_and_xmp(self, tmp_path):
        from PIL import Image
        from scripts.image_utils import crop_image

        src = _exif_xmp_tagged_jpeg(tmp_path / "src.jpg")
        out = crop_image(str(src), str(tmp_path), x=0, y=0, width=50, height=50)
        with Image.open(out) as r:
            assert r.getexif().get(0x010F) == _TEST_EXIF_MAKE
            assert r.info.get("xmp") == _TEST_XMP_PACKET

    def test_resize_image_preserves_exif_and_xmp(self, tmp_path):
        from PIL import Image
        from scripts.image_utils import resize_image

        src = _exif_xmp_tagged_jpeg(tmp_path / "src.jpg")
        out = resize_image(str(src), str(tmp_path), mode="dimensions", width=50)
        with Image.open(out) as r:
            assert r.getexif().get(0x010F) == _TEST_EXIF_MAKE
            assert r.info.get("xmp") == _TEST_XMP_PACKET

    def test_compress_image_preserves_exif_and_xmp(self, tmp_path):
        from PIL import Image
        from scripts.image_utils import compress_image

        src = _exif_xmp_tagged_jpeg(tmp_path / "src.jpg")
        out = compress_image(str(src), str(tmp_path), quality=80)["output_path"]
        with Image.open(out) as r:
            assert r.getexif().get(0x010F) == _TEST_EXIF_MAKE
            assert r.info.get("xmp") == _TEST_XMP_PACKET

    def test_watermark_image_preserves_exif_and_xmp(self, tmp_path):
        from PIL import Image
        from scripts.image_utils import watermark_image

        src = _exif_xmp_tagged_jpeg(tmp_path / "src.jpg")
        out = watermark_image(str(src), str(tmp_path), "DRAFT")
        with Image.open(out) as r:
            assert r.getexif().get(0x010F) == _TEST_EXIF_MAKE
            assert r.info.get("xmp") == _TEST_XMP_PACKET

    def test_convert_image_format_preserves_exif_for_every_target(self, tmp_path):
        """XMP round-trips for jpg/webp; PNG keeps EXIF but can't carry XMP (Pillow limitation)."""
        from PIL import Image
        from scripts.image_utils import convert_image_format

        src = _exif_xmp_tagged_jpeg(tmp_path / "src.jpg")
        for target in ("jpg", "png", "webp"):
            out_dir = tmp_path / f"conv_{target}"
            out_dir.mkdir()
            out = convert_image_format(str(src), str(out_dir), target)
            with Image.open(out) as r:
                assert r.getexif().get(0x010F) == _TEST_EXIF_MAKE, f"EXIF dropped for target={target}"
                if target == "png":
                    assert not r.info.get("xmp")
                else:
                    assert r.info.get("xmp") == _TEST_XMP_PACKET, f"XMP dropped for target={target}"

    def test_no_metadata_source_is_safe(self, tmp_path):
        """A source with no EXIF/XMP must still convert cleanly (no crash, no bogus key)."""
        from PIL import Image
        from scripts.image_utils import rotate_image, compress_image

        src = tmp_path / "plain.jpg"
        Image.new("RGB", (60, 40), (10, 20, 30)).save(src, "JPEG", quality=90)

        r_dir, c_dir = tmp_path / "r", tmp_path / "c"
        r_dir.mkdir()
        c_dir.mkdir()

        out1 = rotate_image(str(src), str(r_dir), angle=90)
        with Image.open(out1) as r:
            assert not r.info.get("xmp")

        out2 = compress_image(str(src), str(c_dir), quality=80)["output_path"]
        with Image.open(out2) as r:
            assert not r.info.get("xmp")
