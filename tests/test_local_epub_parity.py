"""Work package 16: on-device PDF to EPUB.

Runs the shipped `ops-pdf-layout.js` + `ops-pdf-epub.js` against the real vendored pdf.js in
headless Chromium and checks the EPUB with independent parsers (zipfile, ElementTree,
ebooklib, Pillow, PyMuPDF and EPUBCheck) rather than trusting that a download was produced.
"""

from __future__ import annotations

import io
import json
import os
import re
import shutil
import subprocess
import sys
import zipfile
from collections import Counter
from pathlib import Path
from xml.etree import ElementTree as ET

import pymupdf as fitz
import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
import local_browser_harness as H  # noqa: E402
import phase4_fixtures as F  # noqa: E402

ROUTE = "/api/pdf/to-epub"
NS = {"x": "http://www.w3.org/1999/xhtml", "o": "http://www.idpf.org/2007/opf", "dc": "http://purl.org/dc/elements/1.1/",
      "n": "http://www.daisy.org/z3986/2005/ncx/", "c": "urn:oasis:names:tc:opendocument:xmlns:container"}

REASON_SCAN = "some pages are scans that need text recognition (OCR)"
REASON_STRUCTURE = "this file uses features that cannot be processed on this device"
REASON_ENCRYPTED = "this PDF is password protected"
REASON_BIG = "this file exceeds the safe limit for processing on this device"


@pytest.fixture(scope="module")
def lp():
    server, base = H.start_server()
    with H.sync_api.sync_playwright() as pw:
        browser = H.open_browser(pw)
        page = H.LocalPage(browser, base, ["local/ops-pdf-layout.js", "local/ops-pdf-epub.js"])
        yield page
        browser.close()
    server.shutdown()


def convert(lp, name: str, data: bytes, **kw):
    return lp.run(ROUTE, [(name, data, "file", "application/pdf")], **kw)


@pytest.fixture(scope="module")
def article(lp):
    r = convert(lp, "article.pdf", F.article_pdf())
    assert r["status"] == 200, r
    return r


def xhtml(z: zipfile.ZipFile, name: str) -> ET.Element:
    return ET.fromstring(z.read(name))


def chapter_names(z):
    root = ET.fromstring(z.read("OEBPS/content.opf"))
    manifest = {i.get("id"): i.get("href") for i in root.find("o:manifest", NS)}
    return ["OEBPS/" + manifest[r.get("idref")] for r in root.find("o:spine", NS)]


def body_text(z) -> str:
    out = []
    for name in chapter_names(z):
        out.append(" ".join("".join(el.itertext()).strip() for el in xhtml(z, name).find("x:body", NS)))
    return " ".join(out)


def words(text: str) -> Counter:
    return Counter(re.findall(r"[\w'’-]+", text.lower()))


def zip_of(result) -> zipfile.ZipFile:
    return zipfile.ZipFile(io.BytesIO(result["bytes"]))


def epubcheck_jar():
    jar = os.environ.get("EPUBCHECK_JAR")
    if jar and Path(jar).exists():
        return jar
    try:  # the pip package `epubcheck` bundles the jar
        import epubcheck  # type: ignore

        cand = Path(epubcheck.__file__).parent / "epubcheck.jar"
        if cand.exists():
            return str(cand)
    except Exception:
        pass
    return None


# ── contract ──────────────────────────────────────────────────────────────


def test_local_success_matches_the_server_contract(lp, article):
    assert article["message"] == "PDF converted to EPUB"
    assert article["filename"] == "article_forgefiles.org.epub"
    assert article["body"]["status"] == "success"
    assert article["asked"] == []
    assert article["api_requests"] == []  # nothing left the page


def test_filename_is_not_double_branded(lp):
    r = convert(lp, "report_forgefiles.org.pdf", F.plain_pdf())
    assert r["filename"] == "report_forgefiles.org.epub"


def test_no_file_is_a_400_with_no_upload(lp):
    r = lp.run(ROUTE, [])
    assert r["status"] == 400 and r["api_requests"] == [] and r["asked"] == []


# ── package validity ──────────────────────────────────────────────────────


def test_package_structure(article):
    z = zip_of(article)
    first = z.infolist()[0]
    assert first.filename == "mimetype"
    assert first.compress_type == zipfile.ZIP_STORED
    assert z.read("mimetype") == b"application/epub+zip"

    container = ET.fromstring(z.read("META-INF/container.xml"))
    assert container.find(".//c:rootfile", NS).get("full-path") == "OEBPS/content.opf"

    opf = ET.fromstring(z.read("OEBPS/content.opf"))
    meta = opf.find("o:metadata", NS)
    assert meta.find("dc:title", NS).text == "Annual Report 2026"
    assert meta.find("dc:creator", NS).text == "Forge Test"
    assert re.match(r"urn:uuid:[0-9a-f-]{36}$", meta.find("dc:identifier", NS).text)
    assert opf.get("version") == "3.0"

    names = set(z.namelist())
    manifest = {i.get("id"): i for i in opf.find("o:manifest", NS)}
    for item in manifest.values():
        assert "OEBPS/" + item.get("href") in names, item.attrib
    for ref in opf.find("o:spine", NS):
        assert ref.get("idref") in manifest
    assert sum(1 for i in manifest.values() if i.get("properties") == "nav") == 1

    for name in names:
        if name.endswith((".xhtml", ".opf", ".ncx", ".xml")):
            ET.fromstring(z.read(name))  # well-formed XML everywhere


def test_toc_follows_the_pdf_bookmarks(article):
    z = zip_of(article)
    expect = ["Chapter One: Beginnings", "Chapter Two: Growth", "Chapter Three: Outlook"]
    nav = xhtml(z, "OEBPS/nav.xhtml")
    assert ["".join(a.itertext()) for a in nav.iter("{%s}a" % NS["x"])] == expect
    ncx = ET.fromstring(z.read("OEBPS/toc.ncx"))
    assert [t.text for t in ncx.iterfind(".//n:navPoint/n:navLabel/n:text", NS)] == expect
    assert len(chapter_names(z)) == 3


def test_cover_is_a_small_jpeg(article):
    z = zip_of(article)
    raw = z.read("OEBPS/images/cover.jpg")
    img = Image.open(io.BytesIO(raw))
    assert img.format == "JPEG" and img.width <= 600
    assert len(raw) < 80_000


def test_epubcheck_accepts_the_output(tmp_path, article):
    jar = epubcheck_jar()
    if not jar or not shutil.which("java"):
        pytest.skip("EPUBCheck not available (set EPUBCHECK_JAR or pip install epubcheck, and have Java)")
    path = tmp_path / "out.epub"
    path.write_bytes(article["bytes"])
    out = tmp_path / "report.json"
    subprocess.run(["java", "-jar", jar, str(path), "--json", str(out)], capture_output=True, timeout=120)
    report = json.loads(out.read_text(encoding="utf8"))
    bad = [m for m in report["messages"] if m["severity"] in ("FATAL", "ERROR", "WARNING")]
    assert not bad, bad


def test_ebooklib_reads_it(article):
    ebooklib = pytest.importorskip("ebooklib.epub")
    book = ebooklib.read_epub(io.BytesIO(article["bytes"]))
    assert book.get_metadata("DC", "title")[0][0] == "Annual Report 2026"
    assert len(list(book.get_items_of_type(9))) >= 3  # ITEM_DOCUMENT


def test_images_are_real_images(article):
    z = zip_of(article)
    pics = [n for n in z.namelist() if n.startswith("OEBPS/images/") and not n.endswith("/") and "cover" not in n]
    assert len(pics) == 1
    img = Image.open(io.BytesIO(z.read(pics[0])))
    img.load()
    assert img.size == (240, 120)
    assert img.convert("RGB").getpixel((60, 60)) == (200, 40, 40)  # the fixture's fill colour survived


# ── reading semantics ─────────────────────────────────────────────────────


def test_headings_runs_links_lists_and_figures(article):
    z = zip_of(article)
    ch1 = xhtml(z, "OEBPS/chapter_1.xhtml").find("x:body", NS)
    tags = [el.tag.split("}")[1] for el in ch1]
    assert tags[0] == "h2" and "".join(ch1[0].itertext()) == "Chapter One: Beginnings"
    assert "ul" in tags and "figure" in tags
    strong = [e.text for e in ch1.iter("{%s}strong" % NS["x"])]
    em = [e.text for e in ch1.iter("{%s}em" % NS["x"])]
    assert strong == ["bold words"] and em == ["italic words"]
    links = [(a.get("href"), "".join(a.itertext())) for a in ch1.iter("{%s}a" % NS["x"])]
    assert links == [("https://example.com/more", "example.com")]
    assert [li.text for li in ch1.iter("{%s}li" % NS["x"])] == ["First bullet point", "Second bullet point", "Third bullet point"]
    ch3 = xhtml(z, "OEBPS/chapter_3.xhtml").find("x:body", NS)
    assert [e.tag.split("}")[1] for e in ch3][:2] == ["h2", "h3"]  # the sub heading is one level down


def test_paragraphs_are_reflowed_not_one_per_line(article):
    z = zip_of(article)
    ch1 = xhtml(z, "OEBPS/chapter_1.xhtml").find("x:body", NS)
    paras = ["".join(p.itertext()) for p in ch1.iter("{%s}p" % NS["x"])]
    assert paras[0].startswith("The committee reviewed the proposal") and paras[0].endswith("takes place.")
    assert "\n" not in paras[0]
    assert len(paras) == 4  # intro, styled line, link line, and the paragraph that crosses the page


def test_a_sentence_crossing_a_page_break_is_joined(article):
    text = body_text(zip_of(article))
    assert "where the text must continue on the following page without any break in the sentence." in text


def test_no_injected_page_headings_running_heads_or_page_numbers(article):
    z = zip_of(article)
    text = body_text(z)
    assert not re.search(r"\bPage \d\b", text)
    assert "Annual Report" not in text  # the running head repeated on every page
    for name in chapter_names(z):
        for el in xhtml(z, name).iter():
            if el.tag.endswith("}p"):
                assert "".join(el.itertext()).strip() not in {"1", "2", "3"}  # bare page numbers


def test_every_source_word_is_kept(article):
    """Recall and precision of words against PyMuPDF's own reading of the source."""
    src = fitz.open(stream=F.article_pdf(), filetype="pdf")
    expected = words(" ".join(p.get_text() for p in src))
    for token in ("annual", "report", "1", "2", "3"):  # running head and page numbers are dropped on purpose
        expected.pop(token, None)
    got = words(body_text(zip_of(article)))
    got.pop("annual", None)
    got.pop("report", None)
    total = sum(expected.values())
    assert sum((expected - got).values()) / total <= 0.005, (expected - got)
    assert sum((got - expected).values()) / total <= 0.005, (got - expected)


def test_text_matches_what_the_server_produces(tmp_path, lp):
    """Same words as the Python endpoint for the same PDF (it adds 'Page N' headings; we do not)."""
    pytest.importorskip("ebooklib")
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from scripts.pdf_utils import pdf_to_epub

    data = F.plain_pdf(title="A Plain Document")
    src = tmp_path / "plain.pdf"
    src.write_bytes(data)
    out_dir = tmp_path / "srv"
    out_dir.mkdir()
    zs = zipfile.ZipFile(pdf_to_epub(str(src), str(out_dir)))
    server_words = Counter()
    for n in zs.namelist():
        if n.endswith(".xhtml") and "nav" not in n and "cover" not in n:
            server_words += words(re.sub(r"<[^>]+>", " ", zs.read(n).decode("utf8")))
    for k in ("page", "1"):
        server_words.pop(k, None)
    r = convert(lp, "plain.pdf", data)
    assert words(body_text(zip_of(r))) == server_words


def test_two_columns_read_left_then_right(lp):
    r = convert(lp, "cols.pdf", F.two_column_pdf())
    assert r["status"] == 200
    text = body_text(zip_of(r))
    assert text.index("Two Column Study") < text.index("LEFTCOL") < text.index("alpha13") < text.index("RIGHTCOL") < text.index("omega13")


def test_right_aligned_dates_stay_on_their_line(lp):
    r = convert(lp, "cv.pdf", F.resume_pdf())
    text = body_text(zip_of(r))
    assert re.search(r"Acme Corporation, Staff Engineer\s+2021\s+–\s+2025", text)
    assert re.search(r"Globex, Engineer\s+2017\s+–\s+2021", text)


def test_unicode_text_survives(lp):
    r = convert(lp, "uni.pdf", F.unicode_pdf())
    assert r["status"] == 200
    text = body_text(zip_of(r))
    assert "Café crème – naïve façade" in text
    assert "Привет мир" in text and "Γειά" in text


def test_only_safe_link_schemes_are_emitted(lp):
    r = convert(lp, "links.pdf", F.hostile_link_pdf())
    z = zip_of(r)
    hrefs = [a.get("href") for n in chapter_names(z) for a in xhtml(z, n).iter("{%s}a" % NS["x"])]
    assert hrefs == ["https://example.org/ok"]
    assert b"javascript:" not in b"".join(z.read(n) for n in z.namelist() if n.endswith(".xhtml"))


def test_document_without_text_says_so(lp):
    r = convert(lp, "blank.pdf", F.blank_pdf())
    assert r["status"] == 200
    assert "(No text found in document)" in body_text(zip_of(r))


def test_repeat_conversions_are_independent(lp):
    a = convert(lp, "plain.pdf", F.plain_pdf())
    b = convert(lp, "plain.pdf", F.plain_pdf())
    za, zb = zip_of(a), zip_of(b)
    assert [za.read(n) for n in chapter_names(za)] == [zb.read(n) for n in chapter_names(zb)]
    ida = ET.fromstring(za.read("OEBPS/content.opf")).find("o:metadata/dc:identifier", NS).text
    idb = ET.fromstring(zb.read("OEBPS/content.opf")).find("o:metadata/dc:identifier", NS).text
    assert ida != idb  # a fresh book id each time


def test_progress_is_reported_per_page(lp):
    r = convert(lp, "many.pdf", F.many_pages_pdf(6))
    assert r["status"] == 200
    assert r["progress"][-1] == [6, 6] and len(r["progress"]) == 6


# ── the server path is a decision, never an accident ─────────────────────


@pytest.mark.parametrize("make, reason", [
    (F.scanned_pdf, REASON_SCAN),
    (F.three_column_pdf, REASON_STRUCTURE),
    (F.encrypted_pdf, REASON_ENCRYPTED),
    (lambda: b"%PDF-1.4\nthis is not really a pdf", REASON_STRUCTURE),
])
def test_unsupported_input_asks_first_and_declining_uploads_nothing(lp, make, reason):
    lp.set_consent(False)
    r = convert(lp, "doc.pdf", make())
    assert r["status"] == 499
    assert [a["reason"] for a in r["asked"]] == [reason]
    assert r["api_requests"] == []


def test_confirming_sends_exactly_one_request(lp):
    lp.set_consent(True)
    try:
        r = convert(lp, "scan.pdf", F.scanned_pdf())
    finally:
        lp.set_consent(False)
    assert len(r["asked"]) == 1
    assert len(r["api_requests"]) == 1 and r["api_requests"][0].endswith(ROUTE)


def test_a_supplied_password_goes_to_the_server_after_asking(lp):
    lp.set_consent(False)
    r = lp.run(ROUTE, [("a.pdf", F.plain_pdf(), "file", "application/pdf")], {"password": "secret"})
    assert [a["reason"] for a in r["asked"]] == [REASON_ENCRYPTED] and r["api_requests"] == []


def test_cancelling_stops_without_asking_or_uploading(lp):
    r = convert(lp, "many.pdf", F.many_pages_pdf(80), abort_ms=40)
    assert r.get("threw") == "AbortError"
    assert r["asked"] == [] and r["api_requests"] == []


def test_mobile_budget_declines_oversized_input_before_parsing(lp):
    lp.page.evaluate("window.matchMedia = () => ({ matches: true })")
    try:
        r = convert(lp, "big.pdf", b"%PDF-1.4\n" + b"0" * (26 * 1024 * 1024))
    finally:
        lp.page.evaluate("delete window.matchMedia")
    assert [a["reason"] for a in r["asked"]] == [REASON_BIG] and r["api_requests"] == []
