"""
Image conversion utilities for Forge Files.
"""
import io
import shutil
from pathlib import Path
from PIL import Image, ImageOps, ImageDraw, ImageFont
import pillow_heif

from scripts.utils import branded_filename, original_stem, try_font

# Register HEIF opener with Pillow
pillow_heif.register_heif_opener()

MAX_RESIZE_DIMENSION = 8192
MAX_RESIZE_PIXELS = 20_000_000


def _flatten_to_rgb(img: Image.Image, background=(255, 255, 255)) -> Image.Image:
    """
    Convert `img` to RGB for JPEG output, compositing any transparency onto a
    solid background first.

    ``Image.convert("RGB")`` does NOT do this: it silently discards the alpha
    channel and keeps whatever RGB values happen to sit underneath it. Many
    PNG/WebP encoders zero out (or leave garbage in) the color channels of
    fully-transparent pixels since they don't matter visually — so a naive
    convert renders a solid black (or arbitrary palette-color) hole wherever
    the source image was meant to look empty. Compositing onto `background`
    first (how browsers and image editors flatten transparency) avoids that.
    """
    if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
        rgba = img.convert("RGBA")
        flattened = Image.new("RGB", rgba.size, background)
        flattened.paste(rgba, mask=rgba.split()[3])
        # Image.new() starts with an empty .info; carry the source's forward
        # (ICC profile in particular) so callers downstream of this function
        # can still see it, matching every other transform in this module.
        flattened.info = img.info.copy()
        return flattened
    if img.mode != "RGB":
        return img.convert("RGB")
    return img


def _prepare_image(img: Image.Image) -> Image.Image:
    """
    Prepare image for processing: normalize orientation and convert to RGB.

    Args:
        img: PIL Image object.

    Returns:
        Normalized and RGB-converted image.
    """
    # Normalize orientation (handle EXIF tags)
    img = ImageOps.exif_transpose(img)

    # Convert RGBA/palette/grayscale-alpha to RGB for JPEG compatibility,
    # flattening any transparency onto white instead of dropping it.
    if img.mode != "RGB":
        img = _flatten_to_rgb(img)

    return img


def validate_resize_dimensions(width: int, height: int) -> None:
    """Reject resize outputs large enough to exhaust memory."""
    if width < 1 or height < 1:
        raise ValueError("Resize output dimensions must be at least 1 pixel.")
    if width > MAX_RESIZE_DIMENSION or height > MAX_RESIZE_DIMENSION:
        raise ValueError(f"Resize output width and height must be <= {MAX_RESIZE_DIMENSION}px.")
    if width * height > MAX_RESIZE_PIXELS:
        raise ValueError(f"Resize output must be <= {MAX_RESIZE_PIXELS:,} pixels.")


# EXIF Orientation tag id.
_EXIF_ORIENTATION = 0x0112


def _preserved_save_kwargs(img: Image.Image) -> dict:
    """
    Collect the source ICC color profile, EXIF, and XMP metadata so they survive
    a save to JPEG, PNG, or WebP. (Pillow's PNG encoder has no write-side XMP
    support, so the ``xmp`` entry is a harmless no-op there; ICC and EXIF still
    round-trip for all three formats.)

    HEIC photos from modern phones are frequently authored in a wide-gamut color
    space (e.g. Display P3). If the ICC profile is dropped, viewers fall back to
    interpreting the pixels as sRGB, which visibly shifts colors. Likewise the
    EXIF block carries capture date, camera make/model, GPS, etc., and the XMP
    packet carries title/rating/keywords/copyright, that users expect to keep.

    The image is expected to be orientation-normalized already (``exif_transpose``
    applied), so the EXIF Orientation tag is reset to ``1``: the rotation is baked
    into the pixels, and leaving a non-identity Orientation tag would make a viewer
    rotate the already-correct image a second time.

    Returns a dict of ``Image.save`` keyword args (``icc_profile`` / ``exif`` /
    ``xmp``), each key present only when there is real data to write.
    """
    kwargs: dict = {}

    icc = img.info.get("icc_profile")
    if icc:
        kwargs["icc_profile"] = icc

    try:
        exif = img.getexif()
    except Exception:
        exif = None
    if exif:
        # Pixels are already physically rotated; neutralize the Orientation tag so
        # viewers don't double-rotate. Re-serialize from the parsed object so this
        # holds even on libheif builds that don't normalize orientation on decode.
        if _EXIF_ORIENTATION in exif:
            exif[_EXIF_ORIENTATION] = 1
        try:
            exif_bytes = exif.tobytes()
        except Exception:
            exif_bytes = b""
        if exif_bytes:
            kwargs["exif"] = exif_bytes

    xmp = img.info.get("xmp")
    if xmp:
        kwargs["xmp"] = xmp

    return kwargs


def heic_to_jpeg(input_path: str, output_dir: str, quality: int = 95) -> str:
    """
    Converts HEIC/HEIF image to JPEG format, preserving the ICC color profile and
    EXIF metadata (orientation is baked into the pixels).

    Args:
        input_path: Path to the input HEIC/HEIF file.
        output_dir: Directory to save the converted JPEG file.
        quality: JPEG quality (1-100, default 95).

    Returns:
        Path to the converted JPEG file.
    """
    input_file = Path(input_path)
    output_file = Path(output_dir) / branded_filename(input_file, "jpg")

    with Image.open(input_file) as img:
        # Bake orientation into the pixels first.
        img = ImageOps.exif_transpose(img)
        # Capture color profile + metadata before the RGB conversion below, which
        # may not carry ``info`` forward on every Pillow version.
        save_kwargs = _preserved_save_kwargs(img)
        # Convert RGBA / palette / LA to RGB for JPEG compatibility, flattening
        # any transparency onto white instead of dropping it.
        img = _flatten_to_rgb(img)
        img.save(output_file, "JPEG", quality=quality, optimize=True, **save_kwargs)

    return str(output_file)


def resize_image(input_path: str, output_dir: str, mode: str, 
                 width: int = None, height: int = None, 
                 percentage: int = None, target_size_kb: int = None,
                 quality: int = 95) -> str:
    """
    Resizes an image based on the specified mode.

    Args:
        input_path: Path to the input image.
        output_dir: Directory to save the resized image.
        mode: 'dimensions', 'percentage', or 'target_size'.
        width: Target width (optional, for 'dimensions').
        height: Target height (optional, for 'dimensions').
        percentage: Scale percentage (optional, for 'percentage').
        target_size_kb: Target file size in KB (optional, for 'target_size').
        quality: JPEG quality.

    Returns:
        Path to the resized image.
    """
    input_file = Path(input_path)
    fmt = input_file.suffix.lower().lstrip(".")
    if fmt not in _FORMAT_EXT:
        fmt = "jpg"
    output_file = Path(output_dir) / branded_filename(input_file, _FORMAT_EXT[fmt])
    
    with Image.open(input_file) as img:
        img = ImageOps.exif_transpose(img)
        original_width, original_height = img.size

        if mode == 'dimensions':
            if not width and not height:
                raise ValueError("Width or height must be provided for dimensions mode.")

            # Calculate missing dimension if only one is provided to maintain aspect ratio
            if width and not height:
                ratio = width / original_width
                new_width = width
                new_height = int(original_height * ratio)
            elif height and not width:
                ratio = height / original_height
                new_height = height
                new_width = int(original_width * ratio)
            else:
                new_width = width
                new_height = height

            validate_resize_dimensions(new_width, new_height)
            img = img.resize((new_width, new_height), Image.Resampling.LANCZOS)
            _save_pil(img, output_file, fmt, quality=quality)

        elif mode == 'percentage':
            if not percentage:
                raise ValueError("Percentage must be provided for percentage mode.")

            scale = percentage / 100.0
            new_width = int(original_width * scale)
            new_height = int(original_height * scale)

            validate_resize_dimensions(new_width, new_height)
            img = img.resize((new_width, new_height), Image.Resampling.LANCZOS)
            _save_pil(img, output_file, fmt, quality=quality)

        elif mode == 'target_size':
            if not target_size_kb:
                raise ValueError("Target size must be provided for target_size mode.")

            target_bytes = target_size_kb * 1024
            import io

            # First, try with high quality
            _save_pil(img, output_file, fmt, quality=quality)
            current_size = output_file.stat().st_size

            if current_size <= target_bytes:
                # Already under target with high quality
                return str(output_file)

            best_quality = 30
            if fmt in ("jpg", "jpeg", "webp"):
                min_quality = 30
                max_quality = 95
                best_quality = min_quality

                while min_quality <= max_quality:
                    mid_quality = (min_quality + max_quality) // 2
                    buffer = io.BytesIO()
                    _save_pil(img, buffer, fmt, quality=mid_quality)
                    test_size = buffer.tell()

                    if test_size <= target_bytes:
                        best_quality = mid_quality
                        min_quality = mid_quality + 1
                    else:
                        max_quality = mid_quality - 1

            # Save with best quality found
            _save_pil(img, output_file, fmt, quality=best_quality)

            # If still too large, progressively resize dimensions in memory and write to disk once
            if output_file.stat().st_size > target_bytes:
                buffer = io.BytesIO()
                _save_pil(img, buffer, fmt, quality=best_quality)
                scale_factor = 0.9
                scaled_img = img
                while buffer.tell() > target_bytes:
                    current_width, current_height = scaled_img.size
                    new_width = int(current_width * scale_factor)
                    new_height = int(current_height * scale_factor)

                    if new_width < 10 or new_height < 10:
                        break

                    scaled_img = scaled_img.resize((new_width, new_height), Image.Resampling.LANCZOS)
                    buffer = io.BytesIO()
                    _save_pil(scaled_img, buffer, fmt, quality=best_quality)

                with open(output_file, "wb") as f:
                    f.write(buffer.getvalue())

        else:
            raise ValueError(f"Unknown resize mode: {mode}")

    return str(output_file)


def crop_image(input_path: str, output_dir: str, 
               x: int, y: int, width: int, height: int, 
               quality: int = 95) -> str:
    """
    Crops an image based on coordinates.

    Args:
        input_path: Path to the input image.
        output_dir: Directory to save the cropped image.
        x: X coordinate of the top-left corner.
        y: Y coordinate of the top-left corner.
        width: Width of the crop box.
        height: Height of the crop box.
        quality: Image quality.

    Returns:
        Path to the cropped image.
    """
    input_file = Path(input_path)
    fmt = input_file.suffix.lower().lstrip(".")
    if fmt not in _FORMAT_EXT:
        fmt = "jpg"
    output_file = Path(output_dir) / branded_filename(input_file, _FORMAT_EXT[fmt])
    
    with Image.open(input_file) as img:
        img = ImageOps.exif_transpose(img)

        # Ensure crop box is within bounds
        img_width, img_height = img.size
        x = max(0, x)
        y = max(0, y)
        right = min(img_width, x + width)
        lower = min(img_height, y + height)

        cropped_img = img.crop((x, y, right, lower))
        _save_pil(cropped_img, output_file, fmt, quality=quality)

    return str(output_file)


_FORMAT_EXT = {"jpg": "jpg", "jpeg": "jpg", "png": "png", "webp": "webp"}
_FORMAT_PIL = {"jpg": "JPEG", "jpeg": "JPEG", "png": "PNG", "webp": "WEBP"}


def _save_pil(img: Image.Image, output_file: Path, fmt: str, quality: int = 90) -> None:
    """Save a PIL image in the chosen format with sane defaults."""
    pil_fmt = _FORMAT_PIL[fmt]
    # Capture ICC/EXIF/XMP from `img` as handed in, before any flatten/quantize
    # below replaces it with a fresh Image.new()-backed object — same ordering
    # `heic_to_jpeg` already relies on. Every call site has already run
    # `ImageOps.exif_transpose`, so the EXIF Orientation tag is correctly reset
    # to 1 here too.
    save_kwargs = _preserved_save_kwargs(img)
    if pil_fmt == "JPEG":
        img = _flatten_to_rgb(img)
        img.save(output_file, pil_fmt, quality=quality, optimize=True, **save_kwargs)
    elif pil_fmt == "PNG":
        if quality < 90 and img.mode in ("RGB", "RGBA"):
            colors = max(16, min(256, int(quality * 2.56)))
            img_to_save = img.quantize(colors=colors)
        else:
            img_to_save = img
        # Pillow's PNG encoder has no write-side support for an `xmp` kwarg
        # (it only reads back `XML:com.adobe.xmp` on decode); passing it here
        # is a documented no-op, not a regression — PNG's XMP channel was
        # already unpreserved before this change.
        img_to_save.save(output_file, pil_fmt, optimize=True, compress_level=9, **save_kwargs)
    else:  # WEBP
        img.save(output_file, pil_fmt, quality=quality, method=6, **save_kwargs)


def rotate_image(input_path: str, output_dir: str, angle: float, quality: int = 95) -> str:
    """Rotate an image counter-clockwise by `angle` degrees (90/180/270 are lossless-friendly)."""
    try:
        angle = float(angle)
    except (TypeError, ValueError):
        raise ValueError("angle must be a number.")

    input_file = Path(input_path)
    fmt = (input_file.suffix.lower().lstrip(".") or "jpg")
    if fmt not in _FORMAT_EXT:
        fmt = "jpg"
    output_file = Path(output_dir) / branded_filename(input_file, _FORMAT_EXT[fmt])

    with Image.open(input_file) as img:
        img = ImageOps.exif_transpose(img)
        # expand=True so rotated bounds are not clipped.
        rotated = img.rotate(angle, resample=Image.Resampling.BICUBIC, expand=True)
        _save_pil(rotated, output_file, fmt, quality=quality)

    return str(output_file)


def compress_image(input_path: str, output_dir: str, quality: int = 70) -> dict:
    """Compress at the requested quality, preserving the original format and keeping original if encoding grows it."""
    try:
        quality = int(quality)
    except (TypeError, ValueError):
        raise ValueError("quality must be an integer.")
    if not 1 <= quality <= 100:
        raise ValueError("quality must be between 1 and 100.")

    input_file = Path(input_path)
    fmt = input_file.suffix.lower().lstrip(".")
    if fmt not in _FORMAT_EXT:
        fmt = "jpg"
    output_fmt = fmt
    output_file = Path(output_dir) / branded_filename(input_file, _FORMAT_EXT[output_fmt])

    original_size = input_file.stat().st_size
    with Image.open(input_file) as img:
        img = ImageOps.exif_transpose(img)
        _save_pil(img, output_file, output_fmt, quality=quality)

    if output_file.stat().st_size >= original_size:
        original_output = Path(output_dir) / branded_filename(input_file, input_file.suffix.lstrip(".").lower())
        if original_output != output_file:
            output_file.unlink()
        shutil.copyfile(input_file, original_output)
        output_file = original_output

    compressed_size = output_file.stat().st_size
    reduction = max(0.0, (1 - compressed_size / original_size) * 100) if original_size else 0.0
    return {
        "output_path": str(output_file),
        "original_size": original_size,
        "compressed_size": compressed_size,
        "reduction_pct": round(reduction, 1),
    }


def convert_image_format(input_path: str, output_dir: str, target_format: str, quality: int = 90) -> str:
    """Convert an image to JPG/PNG/WebP."""
    target_format = (target_format or "").lower()
    if target_format not in _FORMAT_EXT:
        raise ValueError("target_format must be one of: jpg, png, webp.")

    input_file = Path(input_path)
    output_file = Path(output_dir) / branded_filename(input_file, _FORMAT_EXT[target_format])

    with Image.open(input_file) as img:
        img = ImageOps.exif_transpose(img)
        # PNG/WebP can keep alpha; JPEG cannot, so _save_pil flattens it onto
        # white for that target instead of dropping it.
        if target_format == "png" and img.mode == "P":
            img = img.convert("RGBA")
        _save_pil(img, output_file, target_format, quality=quality)

    return str(output_file)


def watermark_image(
    input_path: str,
    output_dir: str,
    text: str,
    position: str = "bottom-right",
    opacity: float = 0.4,
    color: str = "white",
    quality: int = 95,
) -> str:
    """Stamp a text watermark on an image. position: top-left/top-right/center/bottom-left/bottom-right/diagonal."""
    if not text or not text.strip():
        raise ValueError("Watermark text cannot be empty.")
    try:
        opacity = float(opacity)
    except (TypeError, ValueError):
        raise ValueError("opacity must be a number between 0.05 and 1.0.")
    if not 0.05 <= opacity <= 1.0:
        raise ValueError("opacity must be between 0.05 and 1.0.")
    if position not in ("top-left", "top-right", "center", "bottom-left", "bottom-right", "diagonal"):
        raise ValueError("position must be one of: top-left, top-right, center, bottom-left, bottom-right, diagonal.")

    color_map = {"white": (255, 255, 255), "black": (0, 0, 0), "red": (220, 30, 30), "blue": (30, 30, 220)}
    rgb = color_map.get((color or "white").lower(), (255, 255, 255))
    alpha = int(255 * opacity)

    input_file = Path(input_path)
    fmt = input_file.suffix.lower().lstrip(".")
    if fmt not in _FORMAT_EXT:
        fmt = "jpg"
    output_file = Path(output_dir) / branded_filename(input_file, _FORMAT_EXT[fmt])

    with Image.open(input_file) as img:
        img = ImageOps.exif_transpose(img).convert("RGBA")
        w, h = img.size

        # Pick a font size proportional to the smaller dimension.
        font_size = max(20, int(min(w, h) / 20))
        font = try_font(font_size)

        overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)

        # Measure text.
        try:
            bbox = draw.textbbox((0, 0), text, font=font)
            tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        except AttributeError:
            tw, th = font.getsize(text)

        margin = max(10, int(min(w, h) * 0.02))

        if position == "diagonal":
            # Render text on a transparent layer, rotate 30°, paste centered.
            text_layer = Image.new("RGBA", (tw + 20, th + 20), (0, 0, 0, 0))
            ImageDraw.Draw(text_layer).text((10, 10), text, font=font, fill=(*rgb, alpha))
            rotated = text_layer.rotate(30, resample=Image.Resampling.BICUBIC, expand=True)
            rx, ry = rotated.size
            overlay.paste(rotated, ((w - rx) // 2, (h - ry) // 2), rotated)
        else:
            if position == "top-left":
                pos = (margin, margin)
            elif position == "top-right":
                pos = (w - tw - margin, margin)
            elif position == "center":
                pos = ((w - tw) // 2, (h - th) // 2)
            elif position == "bottom-left":
                pos = (margin, h - th - margin)
            else:  # bottom-right
                pos = (w - tw - margin, h - th - margin)
            draw.text(pos, text, font=font, fill=(*rgb, alpha))

        composed = Image.alpha_composite(img, overlay)
        _save_pil(composed, output_file, fmt, quality=quality)

    return str(output_file)

