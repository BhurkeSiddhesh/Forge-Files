"""Work package 05: metadata edited on-device, read back by independent parsers.

The real browser-side handler (pdf-lib) runs under node; pikepdf and PyMuPDF read
the output. The server's `edit_pdf_metadata` is the reference. Node has no XML
parser, so editing an *existing* XMP packet is covered in a real browser (see the
CHANGELOG); here that case must decline rather than guess.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import fitz
import pikepdf
import pytest

from scripts.pdf_utils import edit_pdf_metadata

HARNESS = Path(__file__).parent / "local_metadata_harness.mjs"
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node not available")


def _make_pdf(path: Path, with_xmp: bool = False, docinfo: dict | None = None) -> None:
    doc = fitz.open()
    for i in range(2):
        doc.new_page(width=595, height=842).insert_text((72, 100), f"Page {i + 1}", fontsize=12)
    doc.save(str(path))
    doc.close()
    if with_xmp or docinfo:
        with pikepdf.open(path, allow_overwriting_input=True) as pdf:
            if with_xmp:
                with pdf.open_metadata(set_pikepdf_as_editor=False) as meta:
                    meta["dc:format"] = "application/pdf"
                    meta["xmp:CreatorTool"] = "Original Tool"
            for k, v in (docinfo or {}).items():
                pdf.docinfo[k] = v
            pdf.save(path)


def _local(tmp_path: Path, src: Path, fields: dict) -> tuple[dict, Path]:
    fields_file = tmp_path / "fields.json"
    fields_file.write_text(json.dumps(fields))
    out = tmp_path / "local_out.pdf"
    proc = subprocess.run(
        [NODE, str(HARNESS), str(src), str(fields_file), str(out)],
        capture_output=True, text=True, encoding="utf-8", timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1]), out


def _server(tmp_path: Path, src: Path, **kw) -> Path:
    d = tmp_path / "server"
    d.mkdir(exist_ok=True)
    return Path(edit_pdf_metadata(str(src), str(d), **kw))


def _info(path: Path) -> dict:
    with pikepdf.open(path) as pdf:
        return {str(k): str(v) for k, v in pdf.docinfo.items()}


def _xmp(path: Path) -> dict:
    out = {}
    with pikepdf.open(path) as pdf:
        with pdf.open_metadata() as meta:
            for key in ("dc:title", "dc:creator", "dc:description", "pdf:Keywords", "xmp:CreatorTool"):
                try:
                    out[key] = meta[key]
                except KeyError:
                    pass
    return out


FULL = {"title": "Quarterly Report", "author": "Ann Author", "subject": "Finance", "keywords": "q3, revenue", "creator": "Forge Test"}


def test_docinfo_matches_the_server_for_a_plain_file(tmp_path: Path) -> None:
    src = tmp_path / "in.pdf"
    _make_pdf(src)
    result, out = _local(tmp_path, src, FULL)
    assert result["status"] == 200, result
    assert result["message"] == "PDF metadata updated"
    assert result["filename"] == "doc_forgefiles.org.pdf"
    local, server = _info(out), _info(_server(tmp_path, src, **FULL))
    for key in ("/Title", "/Author", "/Subject", "/Keywords", "/Creator"):
        assert local[key] == server[key], key


def test_xmp_is_written_for_a_file_that_had_none(tmp_path: Path) -> None:
    src = tmp_path / "in.pdf"
    _make_pdf(src)
    _, out = _local(tmp_path, src, FULL)
    xmp = _xmp(out)
    assert xmp["dc:title"] == "Quarterly Report"
    assert xmp["dc:description"] == "Finance"
    assert xmp["pdf:Keywords"] == "q3, revenue"
    assert xmp["xmp:CreatorTool"] == "Forge Test"
    assert xmp["dc:creator"] == ["Ann Author"]  # spec-compliant sequence (the server writes a bare string)


def test_only_supplied_fields_change_and_nothing_else_is_touched(tmp_path: Path) -> None:
    src = tmp_path / "in.pdf"
    _make_pdf(src, docinfo={"/Producer": "Acme Producer 9", "/Author": "Original Author", "/CreationDate": "D:20200101000000Z"})
    _, out = _local(tmp_path, src, {"title": "New Title"})
    info = _info(out)
    assert info["/Title"] == "New Title"
    assert info["/Author"] == "Original Author"
    assert info["/Producer"] == "Acme Producer 9", "no silent Producer rewrite"
    assert info["/CreationDate"] == "D:20200101000000Z"
    assert "/ModDate" not in info, "no silent date stamp"


def test_page_content_is_untouched(tmp_path: Path) -> None:
    src = tmp_path / "in.pdf"
    _make_pdf(src)
    _, out = _local(tmp_path, src, FULL)
    before, after = fitz.open(str(src)), fitz.open(str(out))
    assert len(after) == 2
    for i in range(2):
        assert after[i].get_text() == before[i].get_text()
        assert after[i].rect == before[i].rect
        assert after[i].get_pixmap(dpi=36).samples == before[i].get_pixmap(dpi=36).samples


def test_unicode_and_xml_special_characters_round_trip(tmp_path: Path) -> None:
    src = tmp_path / "in.pdf"
    _make_pdf(src)
    fields = {"title": "Café & <Crème> — नमस्ते", "author": "A \"Quoted\" Name"}
    result, out = _local(tmp_path, src, fields)
    assert result["status"] == 200
    assert _info(out)["/Title"] == fields["title"]
    assert _xmp(out)["dc:title"] == fields["title"]
    assert _xmp(out)["dc:creator"] == [fields["author"]]


def test_no_fields_gives_an_unchanged_valid_copy(tmp_path: Path) -> None:
    src = tmp_path / "in.pdf"
    _make_pdf(src, docinfo={"/Title": "Keep Me"})
    result, out = _local(tmp_path, src, {})
    assert result["status"] == 200
    assert _info(out)["/Title"] == "Keep Me"
    with pikepdf.open(out) as pdf:
        assert len(pdf.pages) == 2


def test_clear_all_matches_the_server_and_keeps_custom_keys(tmp_path: Path) -> None:
    src = tmp_path / "in.pdf"
    _make_pdf(src, docinfo={"/Title": "Goes", "/Subject": "Goes too", "/Custom": "stays"})
    result, out = _local(tmp_path, src, {"clear_all": "true"})
    assert result["status"] == 200
    server = _server(tmp_path, src, clear_all=True)
    assert _info(out) == _info(server)


def test_an_existing_xmp_packet_declines_under_node_and_asks_first(tmp_path: Path) -> None:
    # No DOMParser here, so editing an existing packet must not be guessed at.
    src = tmp_path / "in.pdf"
    _make_pdf(src, with_xmp=True)
    result, out = _local(tmp_path, src, {"title": "T"})
    assert result["status"] == 499
    assert result["asked"]["tool"] == "Edit PDF Metadata"
    assert result["asked"]["reason"] == "this file uses features that cannot be processed on this device"
    assert not out.exists(), "declined: no output, nothing uploaded"


def test_a_password_asks_with_the_password_reason(tmp_path: Path) -> None:
    src = tmp_path / "in.pdf"
    _make_pdf(src)
    result, _ = _local(tmp_path, src, {"title": "T", "password": "x"})
    assert result["status"] == 499
    assert result["asked"]["reason"] == "this PDF is password protected"


def test_an_encrypted_pdf_asks_with_the_password_reason(tmp_path: Path) -> None:
    src = tmp_path / "in.pdf"
    _make_pdf(src)
    enc = tmp_path / "enc.pdf"
    with pikepdf.open(src) as pdf:
        pdf.save(enc, encryption=pikepdf.Encryption(user="u", owner="o"))
    result, _ = _local(tmp_path, enc, {"title": "T"})
    assert result["status"] == 499
    assert result["asked"]["reason"] == "this PDF is password protected"


def test_a_corrupt_file_asks_before_the_server(tmp_path: Path) -> None:
    src = tmp_path / "bad.pdf"
    src.write_bytes(b"%PDF-1.4 this is not really a pdf")
    result, _ = _local(tmp_path, src, {"title": "T"})
    assert result["status"] == 499
    assert result["asked"]["reason"] == "this file uses features that cannot be processed on this device"


def test_a_title_edit_keeps_other_fields_which_the_server_currently_drops(tmp_path: Path) -> None:
    """Deliberate difference, recorded so nobody 'fixes' it towards the server.

    pikepdf's XMP-to-docinfo sync makes the server delete Author, Producer and
    CreationDate whenever any field is edited on a file without XMP. The on-device
    editor changes only what was asked for.
    """
    src = tmp_path / "in.pdf"
    _make_pdf(src, docinfo={"/Producer": "Acme 9", "/Author": "Orig", "/Custom": "keep"})
    _, out = _local(tmp_path, src, {"title": "New"})
    server = _info(_server(tmp_path, src, title="New"))
    local = _info(out)
    assert local["/Author"] == "Orig" and local["/Producer"] == "Acme 9" and local["/Custom"] == "keep"
    assert "/Author" not in server and "/Producer" not in server
