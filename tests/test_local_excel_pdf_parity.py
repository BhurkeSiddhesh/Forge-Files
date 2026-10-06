"""Work package 21: on-device PDF to Excel.

Runs the shipped `ops-pdf-layout.js` + `ops-pdf-excel.js` against the real vendored pdf.js and
ExcelJS in headless Chromium. The workbook is read back with openpyxl, and the same PDFs are run
through the server's `pdf_to_excel` so sheet names, shapes and cell text can be compared.
"""

from __future__ import annotations

import io
import re
import sys
from pathlib import Path

import openpyxl
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import local_browser_harness as H  # noqa: E402
import phase4_fixtures as F  # noqa: E402

ROUTE = "/api/pdf/to-excel"

REASON_SCAN = "some pages are scans that need text recognition (OCR)"
REASON_STRUCTURE = "this file uses features that cannot be processed on this device"
REASON_ENCRYPTED = "this PDF is password protected"
REASON_BIG = "this file exceeds the safe limit for processing on this device"


@pytest.fixture(scope="module")
def lp():
    server, base = H.start_server()
    with H.sync_api.sync_playwright() as pw:
        browser = H.open_browser(pw)
        page = H.LocalPage(browser, base, ["local/ops-pdf-layout.js", "local/ops-pdf-excel.js"])
        yield page
        browser.close()
    server.shutdown()


def convert(lp, name, data, **kw):
    return lp.run(ROUTE, [(name, data, "file", "application/pdf")], **kw)


def book(result) -> openpyxl.Workbook:
    return openpyxl.load_workbook(io.BytesIO(result["bytes"]))


def grid(ws):
    return [[c.value for c in row] for row in ws.iter_rows()]


def server_book(tmp_path: Path, pdf: bytes) -> tuple[openpyxl.Workbook, int]:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from scripts.pdf_utils import pdf_to_excel

    src = tmp_path / "doc.pdf"
    src.write_bytes(pdf)
    out = tmp_path / "srv"
    out.mkdir(exist_ok=True)
    result = pdf_to_excel(str(src), str(out))
    return openpyxl.load_workbook(result["output_path"]), result["tables_found"]


def as_number(text):
    """The number a server cell string stands for, or None when it is text."""
    t = str(text).strip()
    neg = t.startswith("(") and t.endswith(")")
    t = t.strip("()").replace(",", "").replace("$", "").replace("%", "")
    try:
        n = float(t)
    except ValueError:
        return None
    return -n if neg else n


def same_cell(local, server):
    if server is None or server == "":
        return local is None or local == ""
    if isinstance(local, (int, float)):
        n = as_number(server)
        return n is not None and abs(abs(n) - abs(local)) < 1e-9 or (n is not None and "%" in str(server) and abs(n / 100 - local) < 1e-9)
    return str(local) == str(server)


@pytest.fixture(scope="module")
def ruled(lp):
    r = convert(lp, "ruled.pdf", F.ruled_table_pdf())
    assert r["status"] == 200, r
    return r


# ── contract ──────────────────────────────────────────────────────────────


def test_local_success_matches_the_server_contract(ruled):
    assert ruled["message"] == "Extracted 1 table(s) to Excel"
    assert ruled["filename"] == "ruled_forgefiles.org.xlsx"
    assert ruled["body"]["tables_found"] == 1 and ruled["body"]["status"] == "success"
    assert ruled["asked"] == [] and ruled["api_requests"] == []
    assert ruled["bytes"][:2] == b"PK"


def test_no_file_is_a_400_with_no_upload(lp):
    r = lp.run(ROUTE, [])
    assert r["status"] == 400 and r["api_requests"] == [] and r["asked"] == []


# ── tables ────────────────────────────────────────────────────────────────


def test_a_ruled_table_lands_in_one_sheet_with_typed_cells(ruled):
    wb = book(ruled)
    assert wb.sheetnames == ["P1_T1"]
    assert grid(wb["P1_T1"]) == [
        ["Item", "Qty", "Unit price", "Total"],
        ["Widget", 10, 19.99, 199.9],
        ["Gadget", 5, 5.5, 27.5],
        ["Doodad", 100, 0.99, 99],
        ["Thingamajig", 1, 1250, 1250],
    ]
    assert isinstance(wb["P1_T1"]["B2"].value, int) and isinstance(wb["P1_T1"]["C2"].value, float)


def test_a_bold_header_row_stays_bold_and_columns_follow_the_page(ruled):
    ws = book(ruled)["P1_T1"]
    assert all(c.font.bold for c in ws[1])
    assert not any(c.font.bold for c in ws[2])
    # 150 / 80 / 100 / 100 pt wide in the PDF
    widths = [ws.column_dimensions[ch].width for ch in "ABCD"]
    assert widths[0] > widths[1] and widths[2] > widths[1]


def test_a_borderless_table_gives_the_same_grid(lp, ruled):
    r = convert(lp, "b.pdf", F.borderless_table_pdf())
    assert r["message"] == "Extracted 1 table(s) to Excel"
    assert grid(book(r)["P1_T1"]) == grid(book(ruled)["P1_T1"])
    assert [str(v) for v in grid(book(r)["P1_T1"])[0]] == list(F.ROWS[0])  # every header cell is whole


def test_two_tables_on_a_page_get_two_sheets_and_no_prose(lp):
    r = convert(lp, "two.pdf", F.two_tables_pdf())
    wb = book(r)
    assert wb.sheetnames == ["P1_T1", "P1_T2"] and r["body"]["tables_found"] == 2
    assert grid(wb["P1_T1"]) == [["Region", "Q1", "Q2"], ["North", 10, 12], ["South", 7, 9]]
    assert grid(wb["P1_T2"]) == [["Product", "Units", "Revenue"], ["Alpha", 100, 2500], ["Beta", 50, 1125.5]]
    assert wb["P1_T2"]["C2"].number_format == "#,##0.00"
    text = " ".join(str(v) for ws in wb for row in grid(ws) for v in row if v is not None)
    assert "Notes between" not in text and "committee" not in text


def test_sheet_names_use_the_page_number(lp):
    r = convert(lp, "p2.pdf", F.table_on_page_two_pdf())
    wb = book(r)
    assert wb.sheetnames == ["P2_T1"]
    assert grid(wb["P2_T1"])[0] == ["Name", "Score", "Rank"] and grid(wb["P2_T1"])[3] == ["Cy", 78, 3]


def test_a_table_between_paragraphs_keeps_only_the_table(lp):
    r = convert(lp, "mix.pdf", F.ruled_table_pdf(with_paragraphs=True))
    wb = book(r)
    assert wb.sheetnames == ["P1_T1"]
    assert grid(wb["P1_T1"])[0] == ["Item", "Qty", "Unit price", "Total"] and len(grid(wb["P1_T1"])) == 5


def test_a_table_with_merged_cells_keeps_all_its_text(lp):
    r = convert(lp, "merged.pdf", F.merged_table_pdf())
    assert r["status"] == 200 and r["body"]["tables_found"] == 1
    text = " ".join(str(v) for row in grid(book(r)["P1_T1"]) for v in row if v is not None)
    for word in ("Region", "Sales (both quarters)", "North", "South", "East"):
        assert word in text


# ── cell types ────────────────────────────────────────────────────────────


def test_numbers_are_numbers_and_identifiers_stay_text(lp):
    ws = book(convert(lp, "typed.pdf", F.typed_table_pdf()))["P1_T1"]
    got = {row[0].value: (row[1].value, row[1].number_format, row[1].data_type) for row in ws.iter_rows(min_row=2)}
    assert got["Integer"][0] == 1250 and got["Integer"][2] == "n"
    assert got["Thousands"][:2] == (1250, "#,##0.00")
    assert got["Negative"][0] == -500 and "(" in got["Negative"][1]
    assert got["Percent"][:2] == (0.12, "0%")
    assert got["Leading zeros"][0] == "0012" and got["Leading zeros"][2] == "s"
    assert got["Account"][0] == "123456789012345" and got["Account"][2] == "s"  # more digits than a double keeps
    assert got["Currency"][:2] == (1250.5, '"$"#,##0.00')
    assert got["Words"][0] == "n/a"
    assert got["Empty"][0] is None


def test_text_that_looks_like_a_formula_is_never_a_formula(lp):
    ws = book(convert(lp, "f.pdf", F.formula_table_pdf()))["P1_T1"]
    for row in ws.iter_rows(min_row=2):
        cell = row[1]
        assert cell.data_type == "s", (cell.value, cell.data_type)  # a string cell, not a formula
    assert [row[1].value for row in ws.iter_rows(min_row=2)] == ["=1+1", "+SUM(A1)", "@cmd", "-5 apples"]
    assert ws["B2"].number_format == "@"


# ── no table ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize("make", [F.article_pdf, F.resume_pdf, F.plain_pdf, F.two_column_pdf])
def test_a_document_without_a_table_gets_the_text_content_sheet_and_no_invented_table(lp, make):
    pdf = make()
    r = convert(lp, "doc.pdf", pdf)
    wb = book(r)
    assert r["message"] == "Extracted 0 table(s) to Excel" and r["body"]["tables_found"] == 0
    assert wb.sheetnames == ["Text Content"]
    rows = grid(wb["Text Content"])
    assert rows[0] == ["Page", "Text"] and len(rows) > 1
    assert all(isinstance(p, int) and isinstance(t, str) and t for p, t in rows[1:])


def test_text_content_has_one_row_per_page_with_the_pages_text(lp):
    rows = grid(book(convert(lp, "a.pdf", F.article_pdf()))["Text Content"])
    assert [r[0] for r in rows[1:]] == [1, 2, 3]
    assert "Chapter One: Beginnings" in rows[1][1] and "Chapter Two: Growth" in rows[2][1]


def test_an_empty_document_gets_only_the_header_row(lp):
    wb = book(convert(lp, "blank.pdf", F.blank_pdf()))
    assert wb.sheetnames == ["Text Content"] and grid(wb["Text Content"]) == [["Page", "Text"]]


# ── against the server ────────────────────────────────────────────────────


# The server's borderless fallback is deliberately not compared: on this fixture it reads the header
# "Unit price" as "Qty U" (it clips a cell at the next column's start), so the on-device result is
# checked against the true data in test_a_borderless_table_gives_the_same_grid instead.
@pytest.mark.parametrize("make", [F.ruled_table_pdf, F.two_tables_pdf, F.table_on_page_two_pdf])
def test_same_sheets_shapes_and_cell_text_as_the_server(tmp_path, lp, make):
    pdf = make()
    local = book(convert(lp, "t.pdf", pdf))
    srv, _ = server_book(tmp_path, pdf)
    assert local.sheetnames == srv.sheetnames
    for name in srv.sheetnames:
        lg, sg = grid(local[name]), grid(srv[name])
        assert len(lg) == len(sg) and all(len(a) == len(b) for a, b in zip(lg, sg))
        for lr, sr in zip(lg, sg):
            for lc, sc in zip(lr, sr):
                assert same_cell(lc, sc), (name, lc, sc)


# ── the server path is a decision, never an accident ─────────────────────


@pytest.mark.parametrize("make, reason", [
    (F.scanned_pdf, REASON_SCAN),
    (F.encrypted_pdf, REASON_ENCRYPTED),
    (lambda: b"%PDF-1.4\nnot really a pdf", REASON_STRUCTURE),
])
def test_unsupported_input_asks_first_and_declining_uploads_nothing(lp, make, reason):
    lp.set_consent(False)
    r = convert(lp, "doc.pdf", make())
    assert r["status"] == 499
    assert [a["reason"] for a in r["asked"]] == [reason]
    assert r["api_requests"] == []


def test_a_supplied_password_asks_first(lp):
    lp.set_consent(False)
    r = lp.run(ROUTE, [("a.pdf", F.ruled_table_pdf(), "file", "application/pdf")], {"password": "secret"})
    assert [a["reason"] for a in r["asked"]] == [REASON_ENCRYPTED] and r["api_requests"] == []


def test_confirming_sends_exactly_one_request(lp):
    lp.set_consent(True)
    try:
        r = convert(lp, "scan.pdf", F.scanned_pdf())
    finally:
        lp.set_consent(False)
    assert len(r["asked"]) == 1
    assert len(r["api_requests"]) == 1 and r["api_requests"][0].endswith(ROUTE)


def test_cancelling_stops_without_asking_or_uploading(lp):
    r = convert(lp, "many.pdf", F.many_pages_pdf(80), abort_ms=60)
    assert r.get("threw") == "AbortError"
    assert r["asked"] == [] and r["api_requests"] == []


def test_progress_is_reported_per_page(lp):
    r = convert(lp, "many.pdf", F.many_pages_pdf(5))
    assert r["status"] == 200 and r["progress"][-1] == [5, 5] and len(r["progress"]) == 5


def test_mobile_budget_declines_oversized_input_before_parsing(lp):
    lp.page.evaluate("window.matchMedia = () => ({ matches: true })")
    try:
        r = convert(lp, "big.pdf", b"%PDF-1.4\n" + b"0" * (26 * 1024 * 1024))
    finally:
        lp.page.evaluate("delete window.matchMedia")
    assert [a["reason"] for a in r["asked"]] == [REASON_BIG] and r["api_requests"] == []


def test_repeat_conversions_are_independent(lp):
    a = book(convert(lp, "t.pdf", F.two_tables_pdf()))
    b = book(convert(lp, "t.pdf", F.two_tables_pdf()))
    assert a.sheetnames == b.sheetnames and [grid(a[n]) for n in a.sheetnames] == [grid(b[n]) for n in b.sheetnames]
