"""Work package 09 (audit): on-device Rotate PDF equals the server's."""

from __future__ import annotations

from pathlib import Path

import fitz
import pikepdf
import pytest

from conftest_parity import NODE, make_pdf, page_facts, run_local
from scripts.pdf_utils import rotate_pdf

pytestmark = pytest.mark.skipif(NODE is None, reason="node not available")

SIZES = [(595, 842), (842, 595), (300, 400), (612, 792)]


def _local(tmp_path: Path, pdf: Path, angle, pages=None, **extra):
    fields = {"angle": angle}
    if pages is not None:
        fields["pages"] = pages
    fields.update(extra)
    return run_local(
        tmp_path, "/api/pdf/rotate",
        [{"field": "file", "path": str(pdf), "name": "My Doc.pdf", "type": "application/pdf"}], fields,
    )


def _server(tmp_path: Path, pdf: Path, angle: int, pages=None) -> Path:
    d = tmp_path / "server"
    d.mkdir(exist_ok=True)
    return Path(rotate_pdf(str(pdf), str(d), angle, pages))


@pytest.mark.parametrize("angle", [90, 180, 270, -90, -180, -270])
@pytest.mark.parametrize("pages", [None, "1", "2-3", "1,4", "all"])
def test_every_angle_and_selection_matches_the_server(tmp_path: Path, angle: int, pages) -> None:
    pdf = make_pdf(tmp_path / "in.pdf", sizes=SIZES, rotations={1: 90, 2: 270})
    result, out = _local(tmp_path, pdf, angle, pages)
    assert result["status"] == 200, result
    assert result["message"] == f"PDF rotated by {angle}°"
    assert result["filename"] == "My Doc_forgefiles.org.pdf"
    assert page_facts(out) == page_facts(_server(tmp_path, pdf, angle, pages))


def test_rendered_pages_are_identical_to_the_servers(tmp_path: Path) -> None:
    pdf = make_pdf(tmp_path / "in.pdf", sizes=SIZES)
    _, out = _local(tmp_path, pdf, 90, "2,3")
    a, b = fitz.open(str(out)), fitz.open(str(_server(tmp_path, pdf, 90, "2,3")))
    for i in range(4):
        assert a[i].get_pixmap(dpi=36).samples == b[i].get_pixmap(dpi=36).samples, f"page {i + 1}"


def test_only_selected_pages_change_and_content_is_not_rasterised(tmp_path: Path) -> None:
    pdf = make_pdf(tmp_path / "in.pdf", sizes=SIZES)
    _, out = _local(tmp_path, pdf, 90, "2")
    before, after = page_facts(pdf), page_facts(out)
    assert [f["rotation"] for f in after] == [0, 90, 0, 0]
    for i in (0, 2, 3):
        assert after[i] == before[i]
    assert [f["text"] for f in after] == [f["text"] for f in before]
    assert not any(p.get_images() for p in fitz.open(str(out)))


def test_four_quarter_turns_return_to_the_original(tmp_path: Path) -> None:
    pdf = make_pdf(tmp_path / "in.pdf", sizes=SIZES)
    current = pdf
    for n in range(4):
        d = tmp_path / f"step{n}"
        d.mkdir()
        _, current = _local(d, current, 90)
    assert [f["rotation"] for f in page_facts(current)] == [0, 0, 0, 0]


def test_existing_document_info_is_preserved_like_the_server(tmp_path: Path) -> None:
    pdf = make_pdf(tmp_path / "in.pdf")
    with pikepdf.open(pdf, allow_overwriting_input=True) as p:
        p.docinfo["/Title"] = "Keep"
        p.docinfo["/Producer"] = "Acme 9"
        p.save(pdf)
    _, out = _local(tmp_path, pdf, 90)
    with pikepdf.open(out) as p:
        info = {str(k): str(v) for k, v in p.docinfo.items()}
    assert info["/Title"] == "Keep" and info["/Producer"] == "Acme 9"
    assert "/ModDate" not in info


def test_empty_pages_field_means_all_pages(tmp_path: Path) -> None:
    pdf = make_pdf(tmp_path / "in.pdf", pages=3)
    result, out = _local(tmp_path, pdf, 180, "")
    assert result["status"] == 200
    assert [f["rotation"] for f in page_facts(out)] == [180, 180, 180]


def test_inherited_page_tree_rotation_is_respected(tmp_path: Path) -> None:
    """Rotation set on the /Pages node applies to its pages; rotating adds to it (as on the server)."""
    pdf = make_pdf(tmp_path / "in.pdf", pages=2)
    with pikepdf.open(pdf, allow_overwriting_input=True) as p:
        for page in p.pages:
            if "/Rotate" in page:
                del page["/Rotate"]  # let the pages inherit instead of carrying their own
        p.Root.Pages["/Rotate"] = 90
        p.save(pdf)
    assert [f["rotation"] for f in page_facts(pdf)] == [90, 90]
    _, out = _local(tmp_path, pdf, 90, "1")
    assert [f["rotation"] for f in page_facts(out)] == [180, 90]
    assert [f["rotation"] for f in page_facts(_server(tmp_path, pdf, 90, "1"))] == [180, 90]


@pytest.mark.parametrize("angle", ["45", "0", "360", "abc", "91", "-45", "1.5"])
def test_invalid_angles_fail_clearly_and_never_ask(tmp_path: Path, angle: str) -> None:
    pdf = make_pdf(tmp_path / "in.pdf")
    result, out = _local(tmp_path, pdf, angle)
    assert result["status"] == 400
    assert result["asked"] is None and not out.exists()
    if angle in ("45", "0", "360", "91", "-45"):
        with pytest.raises(ValueError) as server:
            _server(tmp_path, pdf, int(angle))
        assert result["detail"] == str(server.value)


@pytest.mark.parametrize("pages", ["0", "9", "3-1", "x", "1-"])
def test_invalid_selections_use_the_server_message(tmp_path: Path, pages: str) -> None:
    pdf = make_pdf(tmp_path / "in.pdf", pages=4)
    result, out = _local(tmp_path, pdf, 90, pages)
    with pytest.raises(ValueError) as server:
        _server(tmp_path, pdf, 90, pages)
    assert result["status"] == 400 and result["detail"] == str(server.value)
    assert result["asked"] is None and not out.exists()


def test_password_encrypted_corrupt_and_oversize_ask_first(tmp_path: Path) -> None:
    pdf = make_pdf(tmp_path / "in.pdf")
    result, _ = _local(tmp_path, pdf, 90, password="x")
    assert result["status"] == 499 and result["asked"]["reason"] == "this PDF is password protected"
    enc = tmp_path / "enc.pdf"
    with pikepdf.open(pdf) as p:
        p.save(enc, encryption=pikepdf.Encryption(user="u", owner="o"))
    result, _ = _local(tmp_path, enc, 90)
    assert result["status"] == 499 and result["asked"]["tool"] == "Rotate PDF"
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"%PDF-1.4 not a pdf")
    result, _ = _local(tmp_path, bad, 90)
    assert result["status"] == 499
    assert result["asked"]["reason"] == "this file uses features that cannot be processed on this device"
    big = tmp_path / "big.pdf"
    big.write_bytes(pdf.read_bytes() + b"\0" * (151 * 1024 * 1024))
    result, _ = _local(tmp_path, big, 90)
    assert result["status"] == 499
    assert result["asked"]["reason"] == "this file exceeds the safe limit for processing on this device"
