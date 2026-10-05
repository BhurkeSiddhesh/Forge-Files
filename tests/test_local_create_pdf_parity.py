"""Work package 22 (audit): on-device Create PDF (from text, blank) equals the server's."""

from __future__ import annotations

import re
from pathlib import Path

import fitz
import pikepdf
import pytest

from conftest_parity import NODE, run_local
from scripts.pdf_utils import create_blank_pdf, create_pdf_from_text

pytestmark = pytest.mark.skipif(NODE is None, reason="node not available")

LOREM = (
    "Forge Files turns documents into the format you need without sending them anywhere. "
    "This paragraph is long enough to wrap across several lines so that line breaking, "
    "leading and the page margin are all exercised by the comparison below."
)
TEXT = "\n".join(
    ["Title line", "", LOREM, LOREM, "", "Short para.", "Ampersand & angle <brackets> > stay literal."]
    + [f"Line {i}: " + LOREM for i in range(1, 25)]
)


def _text(tmp_path: Path, content: str, **fields):
    f = {"content": content}
    f.update(fields)
    return run_local(tmp_path, "/api/pdf/create-from-text", [], f)


def _blank(tmp_path: Path, **fields):
    return run_local(tmp_path, "/api/pdf/create-blank", [], fields)


def _server_text(tmp_path: Path, content: str, **kw) -> Path:
    d = tmp_path / "server"
    d.mkdir(exist_ok=True)
    return Path(create_pdf_from_text(str(d), content, **kw))


def _words(path: Path) -> list[list[str]]:
    doc = fitz.open(str(path))
    out = [page.get_text().split() for page in doc]
    doc.close()
    return out


def _lines(path: Path) -> list[str]:
    doc = fitz.open(str(path))
    out = [ln for page in doc for ln in page.get_text().splitlines() if ln.strip()]
    doc.close()
    return out


def _first_line_box(path: Path) -> list[float]:
    doc = fitz.open(str(path))
    words = doc[0].get_text("words")
    doc.close()
    return list(words[0][:4])


# ── from text ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize("page_size", ["A4", "Letter", "a4", "letter", "Legal", "bogus"])
def test_page_size_matches_the_server(tmp_path: Path, page_size: str) -> None:
    result, out = _text(tmp_path, TEXT, page_size=page_size)
    assert result["status"] == 200, result
    server = _server_text(tmp_path, TEXT, page_size=page_size)
    a, b = fitz.open(str(out)), fitz.open(str(server))
    assert [tuple(round(v, 1) for v in p.rect) for p in a] == [tuple(round(v, 1) for v in p.rect) for p in b]


@pytest.mark.parametrize(
    "kw",
    [
        {},
        {"font_size": 10},
        {"font_size": 18},
        {"margin_pt": 36},
        {"margin_pt": 100, "font_size": 14, "page_size": "Letter"},
    ],
)
def test_text_flow_matches_the_server(tmp_path: Path, kw: dict) -> None:
    result, out = _text(tmp_path, TEXT, **kw)
    assert result["status"] == 200, result
    assert result["message"] == "PDF created from text"
    server = _server_text(tmp_path, TEXT, **kw)
    local_words, server_words = _words(out), _words(server)
    # Same words in the same order, laid out line by line almost identically.
    assert [w for p in local_words for w in p] == [w for p in server_words for w in p]
    assert abs(len(local_words) - len(server_words)) <= 1, (kw, len(local_words), len(server_words))
    local_lines, server_lines = _lines(out), _lines(server)
    same = sum(1 for a, b in zip(local_lines, server_lines) if a == b)
    # reportlab lets a line overshoot the frame by an amount that varies with size and
    # margin; a handful of borderline lines can wrap one word differently.
    assert same / max(len(server_lines), 1) >= 0.9, (kw, same, len(server_lines))
    assert abs(_first_line_box(out)[0] - _first_line_box(server)[0]) < 1.0, "left margin"
    assert abs(_first_line_box(out)[1] - _first_line_box(server)[1]) < 3.0, "top margin"


def test_wrapping_never_exceeds_the_text_width(tmp_path: Path) -> None:
    _, out = _text(tmp_path, TEXT, margin_pt=72)
    doc = fitz.open(str(out))
    for page in doc:
        for w in page.get_text("words"):
            assert 72 - 0.5 <= w[0] and w[2] <= page.rect.width - 72 + 0.5, (page.number, w)
            assert w[1] >= 72 - 12 and w[3] <= page.rect.height - 72 + 12
    doc.close()


def test_text_is_selectable_real_text_not_an_image(tmp_path: Path) -> None:
    _, out = _text(tmp_path, "Hello selectable world")
    doc = fitz.open(str(out))
    assert "Hello selectable world" in doc[0].get_text()
    assert not doc[0].get_images()
    doc.close()


def test_title_becomes_document_title_and_no_foreign_producer(tmp_path: Path) -> None:
    _, out = _text(tmp_path, "x", title="Quarterly Plan")
    with pikepdf.open(out) as p:
        info = {str(k): str(v) for k, v in p.docinfo.items()}
    assert info["/Title"] == "Quarterly Plan"
    assert "pdf-lib" not in " ".join(info.values()) and "/ModDate" not in info


@pytest.mark.parametrize(
    "title",
    ["Document", "My Report 2026", "a/b\\c:d*e?f", "  spaced  ", "", "   ", "x" * 80, "Café 中文 नमस्ते", "report.final-v2_draft"],
)
def test_filename_follows_the_servers_rule(tmp_path: Path, title: str) -> None:
    result, _ = _text(tmp_path, "x", title=title)
    server = _server_text(tmp_path, "x", title=title)
    pattern = re.sub(r"[0-9a-f]{6}\.pdf$", r"[0-9a-f]{6}\\.pdf", re.escape(server.name[:-10])) + r"[0-9a-f]{6}\.pdf"
    assert re.fullmatch(re.escape(server.name[:-10]) + r"[0-9a-f]{6}\.pdf", result["filename"]), (title, result["filename"], server.name)
    assert pattern


@pytest.mark.parametrize("content", ["", "   ", "\n\n  \n"])
def test_empty_content_is_the_server_error_and_never_asks(tmp_path: Path, content: str) -> None:
    result, out = _text(tmp_path, content)
    assert result["status"] == 400 and result["detail"] == "Content cannot be empty."
    assert result["asked"] is None and not out.exists()
    with pytest.raises(ValueError, match="cannot be empty"):
        create_pdf_from_text(str(tmp_path), content)


def test_accented_latin_text_is_created_locally(tmp_path: Path) -> None:
    result, out = _text(tmp_path, "Café naïve über £ ©")
    assert result["status"] == 200
    assert "Café" in fitz.open(str(out))[0].get_text()


def test_text_the_built_in_font_cannot_draw_asks_with_the_font_reason(tmp_path: Path) -> None:
    result, out = _text(tmp_path, "नमस्ते world")
    assert result["status"] == 499
    assert result["asked"]["tool"] == "Create PDF"
    assert result["asked"]["reason"] == "this document uses a script the on-device fonts do not cover"
    assert not out.exists()


@pytest.mark.parametrize("fields", [{"font_size": 0}, {"font_size": -5}, {"font_size": 500}, {"margin_pt": -10}, {"margin_pt": 400}])
def test_unreasonable_layout_values_ask_instead_of_hanging_or_garbling(tmp_path: Path, fields: dict) -> None:
    result, out = _text(tmp_path, "Some text to lay out", **fields)
    assert result["status"] == 499, (fields, result)
    assert not out.exists()


def test_huge_inputs_ask_before_building_the_document(tmp_path: Path) -> None:
    result, out = _text(tmp_path, "word " * 1_200_000)  # 6 MB of text
    assert result["status"] == 499
    assert result["asked"]["reason"] == "this file exceeds the safe limit for processing on this device"
    assert not out.exists()
    result, _ = _text(tmp_path, "\n".join(["line"] * 8000), font_size=72)  # far more than the page budget
    assert result["status"] == 499


# ── blank ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("num_pages", [1, 2, 10, 100])
@pytest.mark.parametrize("page_size", ["A4", "Letter", "unknown"])
def test_blank_pdf_matches_the_server(tmp_path: Path, num_pages: int, page_size: str) -> None:
    result, out = _blank(tmp_path, num_pages=num_pages, page_size=page_size)
    assert result["status"] == 200, result
    assert result["message"] == f"Created blank PDF with {num_pages} page(s)"
    assert re.fullmatch(rf"blank_{num_pages}pages_[0-9a-f]{{6}}\.pdf", result["filename"])
    d = tmp_path / "server"
    d.mkdir(exist_ok=True)
    server = fitz.open(create_blank_pdf(str(d), num_pages, page_size))
    local = fitz.open(str(out))
    assert len(local) == len(server) == num_pages
    assert [tuple(round(v, 1) for v in p.rect) for p in local] == [tuple(round(v, 1) for v in p.rect) for p in server]
    assert all(not p.get_text().strip() and not p.get_images() for p in local)


@pytest.mark.parametrize("num_pages", [0, 101, -1])
def test_blank_page_count_limits_use_the_server_message(tmp_path: Path, num_pages: int) -> None:
    result, out = _blank(tmp_path, num_pages=num_pages)
    assert result["status"] == 400 and result["detail"] == "num_pages must be between 1 and 100."
    assert result["asked"] is None and not out.exists()
    with pytest.raises(ValueError, match="between 1 and 100"):
        create_blank_pdf(str(tmp_path), num_pages)
