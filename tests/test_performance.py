"""
Performance benchmarks for pdf_utils operations.
Each test uses time.perf_counter() and asserts completion within reasonable limits.
"""
import os
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import time
import pytest

# Wall-clock limits are tuned for a quiet GitHub-hosted runner. CI sets PERF_TIME_SCALE
# (e.g. 3) on shared self-hosted machines, where unrelated load can add seconds.
PERF_TIME_SCALE = float(os.environ.get("PERF_TIME_SCALE", "1"))

from scripts.pdf_utils import (
    create_pdf_from_text,
    create_blank_pdf,
    images_to_pdf,
    add_page_numbers,
    extract_text_from_pdf,
    pdf_to_excel,
    protect_pdf,
    organize_pdf,
    annotate_pdf,
    rotate_pdf,
)


# ──────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────

def _make_blank_pdf(output_dir: Path, num_pages: int = 1) -> str:
    return create_blank_pdf(str(output_dir), num_pages=num_pages)


def _make_png_image(tmp_path: Path, w: int = 200, h: int = 200, name: str = "img.png") -> Path:
    from PIL import Image
    p = tmp_path / name
    img = Image.new("RGB", (w, h), color=(100, 150, 200))
    img.save(str(p), "PNG")
    return p


# ──────────────────────────────────────────────────────────────
# Benchmarks
# ──────────────────────────────────────────────────────────────

def test_perf_create_pdf_from_text_1000_lines(tmp_path):
    """create_pdf_from_text with 1000 lines: < 5 seconds."""
    out = tmp_path / "out"
    out.mkdir()
    content = "\n".join(f"Line {i}: The quick brown fox jumps over the lazy dog." for i in range(1000))

    start = time.perf_counter()
    pdf_path = create_pdf_from_text(str(out), content=content)
    elapsed = time.perf_counter() - start

    print(f"\n  create_pdf_from_text (1000 lines): {elapsed:.3f}s")
    assert Path(pdf_path).exists()
    assert elapsed < 5.0 * PERF_TIME_SCALE, f"Took {elapsed:.2f}s (limit 5s x {PERF_TIME_SCALE:g})"


def test_perf_create_blank_pdf_50_pages(tmp_path):
    """create_blank_pdf with 50 pages: < 2 seconds."""
    out = tmp_path / "out"
    out.mkdir()

    start = time.perf_counter()
    pdf_path = create_blank_pdf(str(out), num_pages=50)
    elapsed = time.perf_counter() - start

    print(f"\n  create_blank_pdf (50 pages): {elapsed:.3f}s")
    assert Path(pdf_path).exists()
    assert elapsed < 2.0 * PERF_TIME_SCALE, f"Took {elapsed:.2f}s (limit 2s x {PERF_TIME_SCALE:g})"


def test_perf_images_to_pdf_5_images(tmp_path):
    """images_to_pdf with 5 images: < 10 seconds."""
    out = tmp_path / "out"
    out.mkdir()
    images = [str(_make_png_image(tmp_path, name=f"img{i}.png")) for i in range(5)]

    start = time.perf_counter()
    pdf_path = images_to_pdf(images, str(out))
    elapsed = time.perf_counter() - start

    print(f"\n  images_to_pdf (5 images): {elapsed:.3f}s")
    assert Path(pdf_path).exists()
    assert elapsed < 10.0 * PERF_TIME_SCALE, f"Took {elapsed:.2f}s (limit 10s x {PERF_TIME_SCALE:g})"


def test_perf_add_page_numbers_10_pages(tmp_path):
    """add_page_numbers on 10-page PDF: < 5 seconds."""
    out = tmp_path / "out"
    out.mkdir()
    pdf_path = _make_blank_pdf(out, num_pages=10)

    start = time.perf_counter()
    result = add_page_numbers(pdf_path, str(out))
    elapsed = time.perf_counter() - start

    print(f"\n  add_page_numbers (10 pages): {elapsed:.3f}s")
    assert Path(result).exists()
    assert elapsed < 5.0 * PERF_TIME_SCALE, f"Took {elapsed:.2f}s (limit 5s x {PERF_TIME_SCALE:g})"


def test_perf_extract_text_10_pages(tmp_path):
    """extract_text_from_pdf on 10-page PDF: < 5 seconds."""
    out = tmp_path / "out"
    out.mkdir()
    # Create a 10-page text PDF for meaningful extraction
    content = "\n".join(f"Page line {i}: sample text for extraction." for i in range(50))
    src = create_pdf_from_text(str(out), content=content)

    start = time.perf_counter()
    result = extract_text_from_pdf(src, str(out))
    elapsed = time.perf_counter() - start

    print(f"\n  extract_text_from_pdf (10-page equivalent): {elapsed:.3f}s")
    assert Path(result["output_path"]).exists()
    assert elapsed < 5.0 * PERF_TIME_SCALE, f"Took {elapsed:.2f}s (limit 5s x {PERF_TIME_SCALE:g})"


def test_perf_pdf_to_excel_5_pages(tmp_path):
    """pdf_to_excel on 5-page PDF: < 10 seconds."""
    out = tmp_path / "out"
    out.mkdir()
    pdf_path = _make_blank_pdf(out, num_pages=5)

    start = time.perf_counter()
    result = pdf_to_excel(pdf_path, str(out))
    elapsed = time.perf_counter() - start

    print(f"\n  pdf_to_excel (5 pages): {elapsed:.3f}s")
    assert Path(result["output_path"]).exists()
    assert elapsed < 10.0 * PERF_TIME_SCALE, f"Took {elapsed:.2f}s (limit 10s x {PERF_TIME_SCALE:g})"


def test_perf_protect_pdf(tmp_path):
    """protect_pdf: < 5 seconds."""
    out = tmp_path / "out"
    out.mkdir()
    pdf_path = _make_blank_pdf(out, num_pages=3)

    start = time.perf_counter()
    result = protect_pdf(pdf_path, str(out), user_password="benchpw")  # ggignore
    elapsed = time.perf_counter() - start

    print(f"\n  protect_pdf: {elapsed:.3f}s")
    assert Path(result).exists()
    assert elapsed < 5.0 * PERF_TIME_SCALE, f"Took {elapsed:.2f}s (limit 5s x {PERF_TIME_SCALE:g})"


def test_perf_organize_pdf_20_pages(tmp_path):
    """organize_pdf reorder 20 pages: < 5 seconds."""
    out = tmp_path / "out"
    out.mkdir()
    pdf_path = _make_blank_pdf(out, num_pages=20)
    reversed_order = list(range(20, 0, -1))

    start = time.perf_counter()
    result = organize_pdf(pdf_path, str(out), page_order=reversed_order)
    elapsed = time.perf_counter() - start

    print(f"\n  organize_pdf (20 pages reversed): {elapsed:.3f}s")
    assert Path(result).exists()
    assert elapsed < 5.0 * PERF_TIME_SCALE, f"Took {elapsed:.2f}s (limit 5s x {PERF_TIME_SCALE:g})"


def test_perf_annotate_pdf_10_annotations(tmp_path):
    """annotate_pdf with 10 annotations: < 5 seconds."""
    out = tmp_path / "out"
    out.mkdir()
    # Create a multi-page PDF so we can spread annotations
    content = "Annotation benchmark test.\n" * 20
    pdf_path = create_pdf_from_text(str(out), content=content)
    annotations = [
        {"type": "highlight", "page": 1, "rect": [50, 700 - i * 20, 300, 720 - i * 20]}
        for i in range(10)
    ]

    start = time.perf_counter()
    result = annotate_pdf(pdf_path, str(out), annotations)
    elapsed = time.perf_counter() - start

    print(f"\n  annotate_pdf (10 annotations): {elapsed:.3f}s")
    assert Path(result).exists()
    assert elapsed < 5.0 * PERF_TIME_SCALE, f"Took {elapsed:.2f}s (limit 5s x {PERF_TIME_SCALE:g})"


def test_perf_rotate_pdf_10_pages(tmp_path):
    """rotate_pdf 10 pages: < 5 seconds."""
    out = tmp_path / "out"
    out.mkdir()
    pdf_path = _make_blank_pdf(out, num_pages=10)

    start = time.perf_counter()
    result = rotate_pdf(pdf_path, str(out), angle=90)
    elapsed = time.perf_counter() - start

    print(f"\n  rotate_pdf (10 pages, 90°): {elapsed:.3f}s")
    assert Path(result).exists()
    assert elapsed < 5.0 * PERF_TIME_SCALE, f"Took {elapsed:.2f}s (limit 5s x {PERF_TIME_SCALE:g})"
