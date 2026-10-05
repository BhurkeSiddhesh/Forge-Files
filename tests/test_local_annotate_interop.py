"""Work package 04: annotations added on-device are valid to an independent parser.

The real browser-side handler (pdf-lib) runs under node; PyMuPDF and pikepdf, which
did not write the file, then read it back. The server's own `annotate_pdf` is the
reference for subtype, colour, rectangle and quad points.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import fitz
import pikepdf
import pytest

from scripts.pdf_utils import annotate_pdf

HARNESS = Path(__file__).parent / "local_annotate_harness.mjs"
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node not available")


def _make_pdf(path: Path, pages: int = 2, rotate_page2: bool = False) -> None:
    doc = fitz.open()
    for i in range(pages):
        page = doc.new_page(width=595, height=842)
        page.insert_text((72, 100), f"Page {i + 1} sample body text", fontsize=12)
    if rotate_page2 and pages > 1:
        doc[1].set_rotation(90)
    doc.save(str(path))
    doc.close()


def _run_local(tmp_path: Path, annotations, pdf: Path) -> tuple[dict, Path]:
    ann_file = tmp_path / "ann.json"
    ann_file.write_text(json.dumps(annotations) if not isinstance(annotations, str) else annotations)
    out = tmp_path / "local_out.pdf"
    proc = subprocess.run(
        [NODE, str(HARNESS), str(pdf), str(ann_file), str(out)],
        capture_output=True, text=True, encoding="utf-8", timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1]), out


def _summary(pdf_path: Path) -> list[dict]:
    doc = fitz.open(str(pdf_path))
    rows = []
    for page in doc:
        for annot in page.annots() or []:
            rows.append(
                {
                    "page": page.number + 1,
                    "type": annot.type[1],
                    "stroke": [round(c, 2) for c in (annot.colors.get("stroke") or [])],
                    "rect": [round(v) for v in annot.rect],
                    "vertices": [(round(x), round(y)) for x, y in (annot.vertices or [])],
                    "content": annot.info.get("content", ""),
                }
            )
    doc.close()
    return rows


MARKUP = [
    {"type": "highlight", "page": 1, "rect": [50, 700, 300, 730]},
    {"type": "underline", "page": 1, "rect": [50, 600, 300, 620]},
    {"type": "strikeout", "page": 2, "rect": [60, 500, 250, 520]},
    {"type": "highlight", "page": 2, "rect": [10, 10, 100, 40], "color": [1, 0.5, 0]},
]


def test_markup_matches_the_server_subtype_colour_and_quad_points(tmp_path: Path) -> None:
    src = tmp_path / "in.pdf"
    _make_pdf(src)
    result, out = _run_local(tmp_path, MARKUP, src)
    assert result["status"] == 200, result
    assert result["message"] == "Added 4 annotation(s)"

    server_dir = tmp_path / "server"
    server_dir.mkdir()
    server_out = Path(annotate_pdf(str(src), str(server_dir), MARKUP))

    local_rows, server_rows = _summary(out), _summary(server_out)
    assert len(local_rows) == len(server_rows) == 4
    for local, server in zip(local_rows, server_rows):
        assert local["type"] == server["type"]
        assert local["page"] == server["page"]
        assert local["stroke"] == server["stroke"], (local, server)
        # Quad points are the marked area; PyMuPDF's rect adds its own padding.
        assert local["vertices"] == server["vertices"], (local, server)


def test_the_output_is_a_valid_pdf_with_one_annotation_array_entry_each(tmp_path: Path) -> None:
    src = tmp_path / "in.pdf"
    _make_pdf(src)
    _, out = _run_local(tmp_path, MARKUP, src)
    with pikepdf.open(out) as pdf:
        assert len(pdf.pages) == 2
        counts = [len(page.get("/Annots", [])) for page in pdf.pages]
        assert counts == [2, 2]
        for page in pdf.pages:
            for annot in page.Annots:
                assert annot.Subtype in ("/Highlight", "/Underline", "/StrikeOut")
                assert "/AP" in annot and "/N" in annot.AP  # draws even without regeneration
                assert annot.F == 4  # print flag


def test_highlight_actually_paints_yellow_over_the_text(tmp_path: Path) -> None:
    src = tmp_path / "in.pdf"
    _make_pdf(src)
    ann = [{"type": "highlight", "page": 1, "rect": [50, 90, 300, 110]}]
    _, out = _run_local(tmp_path, ann, src)
    doc = fitz.open(str(out))
    pix = doc[0].get_pixmap(dpi=72)
    r, g, b = pix.pixel(200, 100)[:3]
    assert r > 200 and g > 200 and b < 120, (r, g, b)  # yellow behind the text, not white
    assert doc[0].get_text().count("Page 1 sample body text") == 1, "text is still extractable"
    doc.close()


def test_note_keeps_content_and_position(tmp_path: Path) -> None:
    src = tmp_path / "in.pdf"
    _make_pdf(src)
    ann = [{"type": "note", "page": 1, "rect": [50, 700, 80, 720], "content": "Check café figure"}]
    result, out = _run_local(tmp_path, ann, src)
    assert result["status"] == 200
    (row,) = _summary(out)
    assert row["type"] == "Text"
    assert row["content"] == "Check café figure"
    assert row["rect"][:2] == [50, 700] and row["rect"][2:] == [66, 716]


def test_text_box_is_extractable_page_text_inside_its_rectangle(tmp_path: Path) -> None:
    src = tmp_path / "in.pdf"
    _make_pdf(src)
    ann = [{"type": "text", "page": 1, "rect": [50, 300, 300, 360], "content": "Approved by finance team"}]
    result, out = _run_local(tmp_path, ann, src)
    assert result["status"] == 200, result
    doc = fitz.open(str(out))
    words = doc[0].get_text("words")
    box = [w for w in words if w[4] in ("Approved", "finance")]
    assert box, "drawn text is extractable"
    for x0, y0, x1, y1, *_ in box:
        assert 50 <= x0 and x1 <= 300 and 300 <= y0 and y1 <= 360
    doc.close()


def test_text_that_does_not_fit_is_an_error_not_silent_loss(tmp_path: Path) -> None:
    src = tmp_path / "in.pdf"
    _make_pdf(src)
    ann = [{"type": "text", "page": 1, "rect": [50, 300, 120, 312], "content": "This sentence is far too long for the box"}]
    result, _ = _run_local(tmp_path, ann, src)
    assert result["status"] == 400
    assert "does not fit" in result["detail"]


def test_redact_is_left_to_the_server_and_nothing_is_drawn(tmp_path: Path) -> None:
    src = tmp_path / "in.pdf"
    _make_pdf(src)
    ann = [{"type": "redact", "page": 1, "rect": [60, 85, 300, 105]}]
    result, out = _run_local(tmp_path, ann, src)
    assert result["status"] == 499  # declined consent: no upload, no output
    assert result["asked"]["reason"].startswith("redaction must permanently remove")
    assert not out.exists()


def test_rotated_pages_are_left_to_the_server(tmp_path: Path) -> None:
    src = tmp_path / "in.pdf"
    _make_pdf(src, rotate_page2=True)
    result, _ = _run_local(tmp_path, [{"type": "highlight", "page": 2, "rect": [10, 10, 100, 40]}], src)
    assert result["status"] == 499
    assert result["asked"]["tool"] == "Annotate PDF"
    # ...but a rotated document is fine when only an unrotated page is annotated.
    ok, out = _run_local(tmp_path, [{"type": "highlight", "page": 1, "rect": [10, 10, 100, 40]}], src)
    assert ok["status"] == 200 and out.exists()


@pytest.mark.parametrize(
    "annotations, detail",
    [
        ("not json", "annotations must be valid JSON."),
        ('{"a": 1}', "annotations must be a JSON array."),
        ([{"type": "stamp", "page": 1}], "Unknown annotation type 'stamp'. Must be one of: highlight, note, redact, strikeout, text, underline"),
        ([{"type": "highlight", "page": 3, "rect": [0, 0, 5, 5]}], "Page 3 is out of range (document has 2 pages)."),
        ([{"type": "highlight", "page": 0, "rect": [0, 0, 5, 5]}], "Page 0 is out of range (document has 2 pages)."),
        ([{"type": "highlight", "page": "x", "rect": [0, 0, 5, 5]}], "Annotation page must be a whole number."),
        ([{"type": "highlight", "page": 1, "rect": [0, 0, 5]}], "Annotation rect must be four numbers: [x0, y0, x1, y1]."),
        (["highlight"], "Each annotation must be an object."),
    ],
)
def test_validation_errors_are_clear_and_nothing_is_written(tmp_path: Path, annotations, detail) -> None:
    src = tmp_path / "in.pdf"
    _make_pdf(src)
    result, out = _run_local(tmp_path, annotations, src)
    assert result["status"] == 400
    assert result["detail"] == detail
    assert result["asked"] is None, "bad input must not open the server dialog"
    assert not out.exists()


def test_the_server_rejects_the_same_inputs(tmp_path: Path) -> None:
    """Parity: the cases above are errors on the server too."""
    src = tmp_path / "in.pdf"
    _make_pdf(src)
    for bad in (
        [{"type": "stamp", "page": 1}],
        [{"type": "highlight", "page": 3, "rect": [0, 0, 5, 5]}],
    ):
        with pytest.raises(ValueError):
            annotate_pdf(str(src), str(tmp_path), bad)


def test_original_content_and_page_count_are_preserved(tmp_path: Path) -> None:
    src = tmp_path / "in.pdf"
    _make_pdf(src, pages=3)
    _, out = _run_local(tmp_path, MARKUP[:1], src)
    before, after = fitz.open(str(src)), fitz.open(str(out))
    assert len(after) == 3
    for i in range(3):
        assert after[i].get_text() == before[i].get_text()
        assert after[i].rect == before[i].rect
