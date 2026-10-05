"""Work package 07 (audit): on-device Extract Pages equals the server's.

The real handler runs under node; PyMuPDF reads both outputs back independently.
"""

from __future__ import annotations

from pathlib import Path

import pikepdf
import pytest

from conftest_parity import NODE, make_pdf, page_facts, run_local
from scripts.pdf_utils import extract_pdf_pages

pytestmark = pytest.mark.skipif(NODE is None, reason="node not available")

SIZES = [(595, 842), (842, 595), (300, 400), (612, 792), (200, 200)]


def _local(tmp_path: Path, pdf: Path, pages: str | None, **extra):
    fields = {} if pages is None else {"pages": pages}
    fields.update(extra)
    return run_local(
        tmp_path, "/api/pdf/extract-pages",
        [{"field": "file", "path": str(pdf), "name": "My Doc.pdf", "type": "application/pdf"}], fields,
    )


def _server(tmp_path: Path, pdf: Path, pages: str) -> Path:
    d = tmp_path / "server"
    d.mkdir(exist_ok=True)
    return Path(extract_pdf_pages(str(pdf), str(d), pages))


@pytest.mark.parametrize(
    "pages",
    ["1", "2-4", "1,3,5", "5,1,3", "1-2,2-3", "all", "ALL", " 2 , 4 ", "3,3,3", "1-5", "2,1-2"],
)
def test_selection_order_and_page_boxes_match_the_server(tmp_path: Path, pages: str) -> None:
    pdf = make_pdf(tmp_path / "in.pdf", sizes=SIZES, rotations={2: 90})
    result, out = _local(tmp_path, pdf, pages)
    assert result["status"] == 200, result
    assert result["message"] == "Pages extracted"
    assert result["filename"] == "My Doc_forgefiles.org.pdf"
    assert page_facts(out) == page_facts(_server(tmp_path, pdf, pages))


@pytest.mark.parametrize(
    "pages",
    ["", "   ", "0", "6", "1-9", "3-1", "a", "1-", "-2", "1.5", "1;2", "1-2-3"],
)
def test_invalid_selections_fail_with_the_server_message(tmp_path: Path, pages: str) -> None:
    pdf = make_pdf(tmp_path / "in.pdf", sizes=SIZES)
    result, out = _local(tmp_path, pdf, pages)
    with pytest.raises(ValueError) as server_error:
        _server(tmp_path, pdf, pages)
    assert result["status"] == 400, (pages, result)
    assert result["detail"] == str(server_error.value), pages
    assert result["asked"] is None, "bad input must not open the server dialog"
    assert not out.exists()


def test_missing_pages_field_is_an_error_not_an_upload(tmp_path: Path) -> None:
    pdf = make_pdf(tmp_path / "in.pdf")
    result, out = _local(tmp_path, pdf, None)
    assert result["status"] == 400 and result["asked"] is None and not out.exists()


def test_content_is_preserved_not_rasterised(tmp_path: Path) -> None:
    pdf = make_pdf(tmp_path / "in.pdf", sizes=SIZES)
    _, out = _local(tmp_path, pdf, "2,4")
    import fitz

    src, dst = fitz.open(str(pdf)), fitz.open(str(out))
    assert len(dst) == 2
    assert dst[0].get_text().strip() == "Page 2" and dst[1].get_text().strip() == "Page 4"
    assert dst[0].get_pixmap(dpi=40).samples == src[1].get_pixmap(dpi=40).samples
    assert dst[1].get_pixmap(dpi=40).samples == src[3].get_pixmap(dpi=40).samples
    assert not any(img for p in dst for img in p.get_images()), "no page was turned into an image"


def test_output_carries_no_foreign_producer_stamp(tmp_path: Path) -> None:
    pdf = make_pdf(tmp_path / "in.pdf")
    _, out = _local(tmp_path, pdf, "1-2")
    with pikepdf.open(out) as p:
        info = {str(k): str(v) for k, v in p.docinfo.items()}
    assert "pdf-lib" not in " ".join(info.values()), info
    assert "/ModDate" not in info and "/CreationDate" not in info, info


def test_password_encrypted_and_corrupt_inputs_ask_first(tmp_path: Path) -> None:
    pdf = make_pdf(tmp_path / "in.pdf")
    result, out = _local(tmp_path, pdf, "1", password="x")
    assert result["status"] == 499 and result["asked"]["reason"] == "this PDF is password protected"

    enc = tmp_path / "enc.pdf"
    with pikepdf.open(pdf) as p:
        p.save(enc, encryption=pikepdf.Encryption(user="u", owner="o"))
    result, _ = _local(tmp_path, enc, "1")
    assert result["status"] == 499 and result["asked"]["tool"] == "Extract Pages"
    assert result["asked"]["reason"] == "this PDF is password protected"

    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"%PDF-1.4 definitely not a pdf")
    result, _ = _local(tmp_path, bad, "1")
    assert result["status"] == 499
    assert result["asked"]["reason"] == "this file uses features that cannot be processed on this device"
    assert not out.exists()


def test_over_budget_input_asks_before_the_server(tmp_path: Path) -> None:
    big = tmp_path / "big.pdf"
    make_pdf(big, pages=1)
    with open(big, "ab") as f:
        f.write(b"\0" * (151 * 1024 * 1024))
    result, _ = _local(tmp_path, big, "1")
    assert result["status"] == 499
    assert result["asked"]["reason"] == "this file exceeds the safe limit for processing on this device"
