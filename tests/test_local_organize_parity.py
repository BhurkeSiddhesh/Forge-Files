"""Work package 19 (audit): on-device Organize PDF equals the server's."""

from __future__ import annotations

import json
from pathlib import Path

import fitz
import pikepdf
import pytest

from conftest_parity import NODE, make_pdf, page_facts, run_local
from scripts.pdf_utils import organize_pdf

pytestmark = pytest.mark.skipif(NODE is None, reason="node not available")

SIZES = [(595, 842), (842, 595), (300, 400), (612, 792), (200, 200)]


def _local(tmp_path: Path, pdf: Path, order, **extra):
    fields = {"page_order": order}
    fields.update(extra)
    return run_local(
        tmp_path, "/api/pdf/organize",
        [{"field": "file", "path": str(pdf), "name": "My Doc.pdf", "type": "application/pdf"}], fields,
    )


def _server(tmp_path: Path, pdf: Path, order: list[int]) -> Path:
    d = tmp_path / "server"
    d.mkdir(exist_ok=True)
    return Path(organize_pdf(str(pdf), str(d), order))


@pytest.mark.parametrize(
    "order",
    [[1, 2, 3, 4, 5], [5, 4, 3, 2, 1], [3, 1, 2, 1], [2], [1, 1, 1], [5, 1], [2, 2, 3, 3, 4], [4, 5, 1]],
)
@pytest.mark.parametrize("style", ["csv", "json"])
def test_reorder_delete_duplicate_matches_the_server(tmp_path: Path, order: list[int], style: str) -> None:
    pdf = make_pdf(tmp_path / "in.pdf", sizes=SIZES, rotations={1: 90, 3: 270})
    raw = ",".join(map(str, order)) if style == "csv" else json.dumps(order)
    result, out = _local(tmp_path, pdf, raw)
    assert result["status"] == 200, result
    assert result["message"] == f"PDF organized ({len(order)} pages in output)"
    assert result["filename"] == "My Doc_forgefiles.org.pdf"
    local, server = page_facts(out), page_facts(_server(tmp_path, pdf, order))
    assert local == server
    a, b = fitz.open(str(out)), fitz.open(str(_server(tmp_path, pdf, order)))
    for i in range(len(order)):
        assert a[i].get_pixmap(dpi=30).samples == b[i].get_pixmap(dpi=30).samples, f"output page {i + 1}"


def test_input_formatting_variants_are_accepted_like_the_server(tmp_path: Path) -> None:
    pdf = make_pdf(tmp_path / "in.pdf", sizes=SIZES)
    for raw in (" 3 , 1 ,2 ", "3,1,2,", "[3, 1, 2]", "  [3,1,2]  ", "3,,1,2"):
        result, out = _local(tmp_path, pdf, raw)
        assert result["status"] == 200, raw
        assert [f["text"] for f in page_facts(out)] == ["Page 3", "Page 1", "Page 2"], raw


def test_duplicates_are_independent_pages_and_content_is_not_rasterised(tmp_path: Path) -> None:
    pdf = make_pdf(tmp_path / "in.pdf", pages=2)
    _, out = _local(tmp_path, pdf, "1,1,2")
    doc = fitz.open(str(out))
    assert len(doc) == 3
    assert [p.get_text().strip() for p in doc] == ["Page 1", "Page 1", "Page 2"]
    assert not any(p.get_images() for p in doc)
    with pikepdf.open(out) as p:
        assert p.pages[0].obj.objgen != p.pages[1].obj.objgen, "a duplicated page is its own object"


def test_links_survive_like_the_server(tmp_path: Path) -> None:
    doc = fitz.open()
    p = doc.new_page()
    p.insert_text((72, 100), "Click", fontsize=14)
    p.insert_link({"kind": fitz.LINK_URI, "from": fitz.Rect(72, 85, 120, 105), "uri": "https://example.org/"})
    doc.new_page()
    pdf = tmp_path / "linked.pdf"
    doc.save(str(pdf))
    _, out = _local(tmp_path, pdf, "2,1")
    local = [[l.get("uri") for l in pg.get_links()] for pg in fitz.open(str(out))]
    server = [[l.get("uri") for l in pg.get_links()] for pg in fitz.open(str(_server(tmp_path, pdf, [2, 1])))]
    assert local == server == [[], ["https://example.org/"]]


def test_no_metadata_stamp(tmp_path: Path) -> None:
    pdf = make_pdf(tmp_path / "in.pdf", pages=3)
    _, out = _local(tmp_path, pdf, "3,2,1")
    with pikepdf.open(out) as p:
        info = {str(k): str(v) for k, v in p.docinfo.items()}
    assert "pdf-lib" not in " ".join(info.values()) and "/ModDate" not in info


@pytest.mark.parametrize("raw", ["", "  ", "[]", ",", ",,"])
def test_empty_order_is_the_server_error(tmp_path: Path, raw: str) -> None:
    pdf = make_pdf(tmp_path / "in.pdf", pages=3)
    result, out = _local(tmp_path, pdf, raw)
    assert result["status"] == 400
    assert result["detail"] == "page_order cannot be empty."
    assert result["asked"] is None and not out.exists()
    with pytest.raises(ValueError, match="cannot be empty"):
        organize_pdf(str(pdf), str(tmp_path), [])


@pytest.mark.parametrize("raw, order", [("0", [0]), ("6", [6]), ("1,9", [1, 9]), ("[1,-2]", [1, -2])])
def test_out_of_range_numbers_use_the_server_message(tmp_path: Path, raw: str, order: list[int]) -> None:
    pdf = make_pdf(tmp_path / "in.pdf", sizes=SIZES)
    result, out = _local(tmp_path, pdf, raw)
    with pytest.raises(ValueError) as server:
        _server(tmp_path, pdf, order)
    assert result["status"] == 400 and result["detail"] == str(server.value)
    assert result["asked"] is None and not out.exists()


@pytest.mark.parametrize("raw", ["a,b", "1,x", "1.5", "[1,", "{}"])
def test_unparseable_order_is_a_clear_error_and_never_asks(tmp_path: Path, raw: str) -> None:
    pdf = make_pdf(tmp_path / "in.pdf", pages=3)
    result, out = _local(tmp_path, pdf, raw)
    assert result["status"] == 400 and result["asked"] is None and not out.exists()


def test_a_huge_duplicate_list_asks_before_building_the_output(tmp_path: Path) -> None:
    pdf = make_pdf(tmp_path / "in.pdf", pages=2)
    result, out = _local(tmp_path, pdf, ",".join(["1"] * 2001))
    assert result["status"] == 499
    assert result["asked"]["reason"] == "this file exceeds the safe limit for processing on this device"
    assert not out.exists()
    ok, _ = _local(tmp_path, pdf, ",".join(["1"] * 2000))
    assert ok["status"] == 200


def test_password_encrypted_corrupt_and_oversize_ask_first(tmp_path: Path) -> None:
    pdf = make_pdf(tmp_path / "in.pdf", pages=2)
    result, _ = _local(tmp_path, pdf, "1", password="x")
    assert result["status"] == 499 and result["asked"]["tool"] == "Organize PDF"
    assert result["asked"]["reason"] == "this PDF is password protected"
    enc = tmp_path / "enc.pdf"
    with pikepdf.open(pdf) as p:
        p.save(enc, encryption=pikepdf.Encryption(user="u", owner="o"))
    result, _ = _local(tmp_path, enc, "1")
    assert result["status"] == 499 and result["asked"]["reason"] == "this PDF is password protected"
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"%PDF-1.4 not a pdf")
    result, _ = _local(tmp_path, bad, "1")
    assert result["status"] == 499
    big = tmp_path / "big.pdf"
    big.write_bytes(pdf.read_bytes() + b"\0" * (151 * 1024 * 1024))
    result, _ = _local(tmp_path, big, "1")
    assert result["status"] == 499
