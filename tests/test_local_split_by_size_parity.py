"""Split by maximum size: both engines cover every page and respect the limit."""

from __future__ import annotations

import io
import os
import zipfile
from pathlib import Path

import fitz
import pytest

from conftest_parity import NODE, run_local
from scripts.pdf_utils import split_pdf_to_zip

pytestmark = pytest.mark.skipif(NODE is None, reason="node not available")

LIMIT_MB = 0.12  # ~125 KB


def _noisy_pdf(path: Path, pages: int = 8, side: int = 100) -> Path:
    """Each page carries ~40 KB of incompressible noise, so sizes are meaningful."""
    doc = fitz.open()
    for i in range(pages):
        page = doc.new_page(width=300, height=300)
        page.insert_text((20, 40), f"Page {i + 1}")
        pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, side, side), False)
        pix.set_rect(pix.irect, (0, 0, 0))
        pix.samples_mv[:] = os.urandom(len(pix.samples_mv))
        page.insert_image(fitz.Rect(20, 60, 280, 280), stream=pix.tobytes("png"))
    doc.save(str(path))
    doc.close()
    return path


def _check(data: bytes, total: int, limit: int) -> int:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        names = sorted(zf.namelist())
        seen = []
        for name in names:
            blob = zf.read(name)
            pdf = fitz.open(stream=blob, filetype="pdf")
            texts = [p.get_text() for p in pdf]
            seen += [int(t.split()[1]) for t in texts]
            if len(texts) > 1:
                assert len(blob) <= limit, (name, len(blob), limit)
    assert seen == list(range(1, total + 1))
    return len(names)


def test_both_engines_pack_pages_within_the_limit(tmp_path: Path) -> None:
    pdf = _noisy_pdf(tmp_path / "in.pdf")
    limit = int(LIMIT_MB * 1024 * 1024)
    result, out = run_local(
        tmp_path, "/api/pdf/split",
        [{"field": "file", "path": str(pdf), "name": "n.pdf", "type": "application/pdf"}],
        {"mode": "by_size", "max_mb": str(LIMIT_MB)},
    )
    assert result["status"] == 200, result
    local_parts = _check(out.read_bytes(), 8, limit)
    assert 1 < local_parts < 8
    srv = split_pdf_to_zip(str(pdf), str(tmp_path), mode="by_size", max_mb=LIMIT_MB)
    server_parts = _check(Path(srv["output_path"]).read_bytes(), 8, limit)
    assert srv["file_count"] == server_parts
    assert abs(local_parts - server_parts) <= 1


def test_an_oversized_single_page_still_gets_its_own_part(tmp_path: Path) -> None:
    pdf = _noisy_pdf(tmp_path / "in.pdf", pages=3, side=200)  # ~120 KB per page
    srv = split_pdf_to_zip(str(pdf), str(tmp_path), mode="by_size", max_mb=0.1)
    assert srv["file_count"] == 3


@pytest.mark.parametrize("bad", [None, "abc", 0, 501])
def test_invalid_max_mb_is_rejected(tmp_path: Path, bad) -> None:
    pdf = _noisy_pdf(tmp_path / "in.pdf", pages=2)
    with pytest.raises(ValueError):
        split_pdf_to_zip(str(pdf), str(tmp_path), mode="by_size", max_mb=bad)
