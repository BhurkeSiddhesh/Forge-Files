"""Work package 08 (audit): on-device Merge PDF equals the server's."""

from __future__ import annotations

import re
from pathlib import Path

import fitz
import pikepdf
import pytest

from conftest_parity import NODE, make_pdf, page_facts, run_local
from scripts.pdf_utils import merge_pdfs

pytestmark = pytest.mark.skipif(NODE is None, reason="node not available")


def _files(paths: list[Path]) -> list[dict]:
    return [{"field": "files", "path": str(p), "name": p.name, "type": "application/pdf"} for p in paths]


def _server(tmp_path: Path, paths: list[Path]) -> Path:
    d = tmp_path / "server"
    d.mkdir(exist_ok=True)
    return Path(merge_pdfs([str(p) for p in paths], str(d)))


def _inputs(tmp_path: Path) -> list[Path]:
    a = make_pdf(tmp_path / "a.pdf", pages=2, sizes=[(595, 842), (842, 595)])
    b = make_pdf(tmp_path / "b.pdf", pages=3, sizes=[(300, 400), (612, 792), (200, 200)], rotations={1: 90})
    c = make_pdf(tmp_path / "c.pdf", pages=1)
    return [a, b, c]


def test_order_count_boxes_and_pixels_match_the_server(tmp_path: Path) -> None:
    paths = _inputs(tmp_path)
    result, out = run_local(tmp_path, "/api/pdf/merge", _files(paths))
    assert result["status"] == 200, result
    assert result["message"] == "PDFs merged"
    assert re.fullmatch(r"merged_[0-9a-f]{8}\.pdf", result["filename"]), result["filename"]
    local, server = page_facts(out), page_facts(_server(tmp_path, paths))
    assert len(local) == 6
    assert local == server
    a, b = fitz.open(str(out)), fitz.open(str(_server(tmp_path, paths)))
    for i in range(6):
        assert a[i].get_pixmap(dpi=36).samples == b[i].get_pixmap(dpi=36).samples, f"page {i + 1} renders differently"


def test_input_order_is_the_selection_order_not_alphabetical(tmp_path: Path) -> None:
    a, b, c = _inputs(tmp_path)
    _, out = run_local(tmp_path, "/api/pdf/merge", _files([c, a, b]))
    assert [f["text"] for f in page_facts(out)] == ["Page 1", "Page 1", "Page 2", "Page 1", "Page 2", "Page 3"]
    assert page_facts(out) == page_facts(_server(tmp_path, [c, a, b]))


def test_the_same_file_can_be_merged_with_itself(tmp_path: Path) -> None:
    a = make_pdf(tmp_path / "a.pdf", pages=2)
    _, out = run_local(tmp_path, "/api/pdf/merge", _files([a, a]))
    assert len(page_facts(out)) == 4


def test_link_annotations_survive_like_the_server(tmp_path: Path) -> None:
    doc = fitz.open()
    p = doc.new_page()
    p.insert_text((72, 100), "Click", fontsize=14)
    p.insert_link({"kind": fitz.LINK_URI, "from": fitz.Rect(72, 85, 120, 105), "uri": "https://example.org/"})
    linked = tmp_path / "linked.pdf"
    doc.save(str(linked))
    other = make_pdf(tmp_path / "o.pdf", pages=1)
    _, out = run_local(tmp_path, "/api/pdf/merge", _files([linked, other]))
    local_links = [l.get("uri") for l in fitz.open(str(out))[0].get_links()]
    server_links = [l.get("uri") for l in fitz.open(str(_server(tmp_path, [linked, other])))[0].get_links()]
    assert local_links == server_links == ["https://example.org/"]


def test_output_has_no_metadata_stamp_and_is_valid(tmp_path: Path) -> None:
    paths = _inputs(tmp_path)
    _, out = run_local(tmp_path, "/api/pdf/merge", _files(paths))
    with pikepdf.open(out) as pdf:
        assert len(pdf.pages) == 6
        info = {str(k): str(v) for k, v in pdf.docinfo.items()}
    assert "pdf-lib" not in " ".join(info.values()) and "/ModDate" not in info


def test_fewer_than_two_files_is_the_server_error_and_never_asks(tmp_path: Path) -> None:
    a = make_pdf(tmp_path / "a.pdf")
    result, out = run_local(tmp_path, "/api/pdf/merge", _files([a]))
    assert result["status"] == 400
    assert result["detail"] == "Provide at least two PDF files to merge."
    assert result["asked"] is None and not out.exists()
    with pytest.raises(ValueError, match="at least two"):
        merge_pdfs([str(a)], str(tmp_path))


def test_a_password_for_any_file_asks_with_the_password_reason(tmp_path: Path) -> None:
    a, b, _ = _inputs(tmp_path)
    for passwords in (",secret", "x,", ",,"[:1] + "y"):
        result, out = run_local(tmp_path, "/api/pdf/merge", _files([a, b]), {"passwords": passwords})
        assert result["status"] == 499, passwords
        assert result["asked"]["tool"] == "Merge PDF"
        assert result["asked"]["reason"] == "this PDF is password protected"
        assert not out.exists()
    # Blank entries mean "no password": that still merges locally.
    result, _ = run_local(tmp_path, "/api/pdf/merge", _files([a, b]), {"passwords": ","})
    assert result["status"] == 200


def test_one_encrypted_input_among_several_asks_before_the_server(tmp_path: Path) -> None:
    a, b, _ = _inputs(tmp_path)
    enc = tmp_path / "enc.pdf"
    with pikepdf.open(a) as p:
        p.save(enc, encryption=pikepdf.Encryption(user="u", owner="o"))
    result, out = run_local(tmp_path, "/api/pdf/merge", _files([b, enc]))
    assert result["status"] == 499
    assert result["asked"]["reason"] == "this PDF is password protected"
    assert not out.exists(), "declined: nothing produced, nothing uploaded"


def test_a_corrupt_input_asks_before_the_server(tmp_path: Path) -> None:
    a, _, _ = _inputs(tmp_path)
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"%PDF-1.4 not a real pdf")
    result, _ = run_local(tmp_path, "/api/pdf/merge", _files([a, bad]))
    assert result["status"] == 499
    assert result["asked"]["reason"] == "this file uses features that cannot be processed on this device"


def test_combined_size_over_budget_asks_even_when_each_file_is_small(tmp_path: Path) -> None:
    a = make_pdf(tmp_path / "a.pdf", pages=1)
    pad = b"\0" * (80 * 1024 * 1024)
    files = []
    for name in ("big1.pdf", "big2.pdf"):
        p = tmp_path / name
        p.write_bytes(a.read_bytes() + pad)  # 80 MiB each: under 150 alone, 160 together
        files.append(p)
    result, _ = run_local(tmp_path, "/api/pdf/merge", _files(files))
    assert result["status"] == 499
    assert result["asked"]["reason"] == "this file exceeds the safe limit for processing on this device"
