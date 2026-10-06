"""Phase 5 (WP44, WP31, WP35, WP36, WP38): the on-device Office engine in real Chromium.

Starts the real FastAPI app (so the COOP/COEP headers under test are the ones main.py sends),
opens `/on-device-office/` in headless Chromium and converts generated Word, Excel and
PowerPoint files with the vendored LibreOffice WASM. Expected page and character counts are the
server results recorded in docs/migrations/browser-processing/evidence/ENGINE_TEST_RESULTS.md
(Word sample 2 pages / 637 chars, Excel 1 / 790, PowerPoint 3 / 96), within the 1 % quality gate.

Skipped when Playwright, a Chromium able to run pdf.js, or the engine files are missing
(`python public/scripts/fetch_office_engine.py` restores them).
"""

from __future__ import annotations

import base64
import io
import json
import socket
import subprocess
import sys
import time
import urllib.request
import zipfile
from pathlib import Path

import pytest

sync_api = pytest.importorskip("playwright.sync_api")
fitz = pytest.importorskip("fitz")
docx = pytest.importorskip("docx")
openpyxl = pytest.importorskip("openpyxl")
pptx = pytest.importorskip("pptx")

from local_browser_harness import open_browser  # noqa: E402

PUBLIC = Path(__file__).resolve().parent.parent
ENGINE = PUBLIC / "static" / "vendor" / "lo-wasm" / "2.7.2"
pytestmark = pytest.mark.skipif(
    not (ENGINE / "soffice.wasm").is_file() or not (ENGINE / "soffice.data").is_file(),
    reason="engine files not present; run public/scripts/fetch_office_engine.py",
)

CONVERT_TIMEOUT_MS = 240_000


# ── fixtures ──────────────────────────────────────────────────────────────

def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def base_url():
    port = _free_port()
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "main:app", "--port", str(port), "--log-level", "warning"],
        cwd=str(PUBLIC), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    url = f"http://127.0.0.1:{port}"
    try:
        for _ in range(60):
            try:
                if urllib.request.urlopen(url + "/on-device-office/", timeout=2).status == 200:
                    break
            except Exception:
                time.sleep(0.5)
        else:
            pytest.fail("app did not start")
        yield url
    finally:
        proc.terminate()
        proc.wait(timeout=15)


@pytest.fixture(scope="module")
def browser():
    with sync_api.sync_playwright() as pw:
        b = open_browser(pw)
        yield b
        b.close()


@pytest.fixture
def page(browser):
    ctx = browser.new_context(accept_downloads=True)
    pg = ctx.new_page()
    pg.api_requests = []
    pg.all_requests = []
    pg.on("request", lambda r: pg.all_requests.append(r.url))
    pg.on("request", lambda r: pg.api_requests.append(r.url) if "/api/" in r.url or "/premium/" in r.url else None)
    yield pg
    ctx.close()


@pytest.fixture(scope="module")
def files(tmp_path_factory):
    d = tmp_path_factory.mktemp("office")
    doc = docx.Document()
    doc.add_heading("Quarterly Report", 0)
    doc.add_paragraph("Intro paragraph with **bold** text.")
    table = doc.add_table(rows=4, cols=3)
    table.style = "Table Grid"
    for r in range(4):
        for c in range(3):
            table.cell(r, c).text = f"R{r}C{c}"
    doc.add_paragraph("Bullet", style="List Bullet")
    doc.add_page_break()
    doc.add_heading("Page two", 1)
    doc.add_paragraph("More text " * 50)
    doc.save(d / "sample.docx")

    tamil = docx.Document()
    tamil.add_paragraph("வணக்கம் உலகம்")
    tamil.save(d / "tamil.docx")

    wb = openpyxl.Workbook()
    ws = wb.active
    for r in range(1, 40):
        ws.append([f"Item {r}", r * 3, r * 1.5, f"=B{r}*C{r}"])
    wb.save(d / "sample.xlsx")

    (d / "data.csv").write_text("name,qty\nwidget,3\ngadget,4\n", encoding="utf-8")

    prs = pptx.Presentation()
    for i in range(3):
        s = prs.slides.add_slide(prs.slide_layouts[1])
        s.shapes.title.text = f"Slide {i + 1}"
        s.placeholders[1].text = "Point one\nPoint two"
    prs.save(d / "sample.pptx")

    (d / "broken.docx").write_bytes(b"this is not a zip file")
    (d / "legacy.doc").write_bytes(b"\xd0\xcf\x11\xe0" + b"\x00" * 64)
    return d


# ── helpers ───────────────────────────────────────────────────────────────

def open_tool(page, base, op):
    page.goto(f"{base}/on-device-office/?op={op}")
    assert page.evaluate("crossOriginIsolated") is True


def run(page, path: Path, timeout=CONVERT_TIMEOUT_MS):
    page.set_input_files("#file", str(path))
    page.click("#convert")
    page.wait_for_selector("#result:not(.hidden), #problem:not(.hidden)", timeout=timeout)


def download_bytes(page) -> bytes:
    b64 = page.evaluate(
        """async () => {
            const r = await fetch(document.getElementById('download').href);
            const b = new Uint8Array(await r.arrayBuffer());
            let s = '';
            for (let i = 0; i < b.length; i += 8192) s += String.fromCharCode.apply(null, b.subarray(i, i + 8192));
            return btoa(s);
        }"""
    )
    return base64.b64decode(b64)


def pdf_stats(data: bytes):
    d = fitz.open(stream=data, filetype="pdf")
    return len(d), sum(len(p.get_text()) for p in d), "".join(p.get_text() for p in d)


def within_one_percent(actual: int, expected: int) -> bool:
    return abs(actual - expected) <= max(1, expected * 0.01)


# ── isolation and headers ─────────────────────────────────────────────────

def test_isolated_route_headers_and_main_site_unchanged(base_url):
    iso = urllib.request.urlopen(base_url + "/on-device-office/")
    assert iso.headers["Cross-Origin-Opener-Policy"] == "same-origin"
    assert iso.headers["Cross-Origin-Embedder-Policy"] == "require-corp"
    assert "noindex" in iso.headers["X-Robots-Tag"]
    html = iso.read().decode()
    assert "adsbygoogle" not in html and "googletagmanager" not in html and "datafa.st" not in html

    for rel in ("/static/vendor/lo-wasm/2.7.2/browser.worker.global.js", "/static/vendor/lo-wasm/2.7.2/soffice.wasm"):
        r = urllib.request.urlopen(urllib.request.Request(base_url + rel, method="HEAD"))
        assert r.headers["Cross-Origin-Embedder-Policy"] == "require-corp", rel
        assert "immutable" in r.headers["Cache-Control"], rel

    main = urllib.request.urlopen(base_url + "/")
    assert main.headers.get("Cross-Origin-Embedder-Policy") is None, "COEP must not reach the ad-supported site"
    css = urllib.request.urlopen(base_url + "/static/style.css")
    assert css.headers.get("Cross-Origin-Embedder-Policy") is None


# ── conversions ───────────────────────────────────────────────────────────

def test_word_to_pdf_matches_server(page, base_url, files):
    open_tool(page, base_url, "word-to-pdf")
    run(page, files / "sample.docx")
    assert page.is_visible("#result"), page.inner_text("#problem-reason") if page.is_visible("#problem") else ""
    assert page.get_attribute("#download", "download") == "sample_forgefiles.org.pdf"
    assert page.inner_text("#result-message") == "Word document converted to PDF on this device: sample_forgefiles.org.pdf"
    pages, chars, text = pdf_stats(download_bytes(page))
    assert pages == 2
    assert within_one_percent(chars, 637)
    assert "R3C2" in text and "Page two" in text  # table cells and the page break survive as real text
    assert page.api_requests == []


def test_excel_to_pdf_calculates_formulas(page, base_url, files):
    open_tool(page, base_url, "excel-to-pdf")
    run(page, files / "sample.xlsx")
    assert page.is_visible("#result")
    pages, chars, text = pdf_stats(download_bytes(page))
    assert pages == 1
    assert within_one_percent(chars, 790)
    assert "4.5" in text and "6844.5" in text  # D1 = 3*1.5 and D39 = 117*58.5 are computed, not 0
    assert page.api_requests == []


def test_csv_is_declined_to_the_server(page, base_url, files):
    # LibreOffice-in-WASM opens CSV without importing any cells (page shows only the
    # sheet name), so CSV is not offered on-device rather than producing an empty PDF.
    open_tool(page, base_url, "excel-to-pdf")
    run(page, files / "data.csv", timeout=30_000)
    assert page.is_visible("#problem") and "supports .xlsx, .xlsm" in page.inner_text("#problem-reason")
    assert not any(u.endswith("soffice.wasm") for u in page.all_requests)
    assert page.api_requests == []


def test_powerpoint_to_pdf_matches_server(page, base_url, files):
    open_tool(page, base_url, "ppt-to-pdf")
    run(page, files / "sample.pptx")
    assert page.is_visible("#result")
    pages, chars, text = pdf_stats(download_bytes(page))
    assert pages == 3
    assert within_one_percent(chars, 96)
    assert "Slide 1" in text and "Slide 3" in text
    assert page.api_requests == []


def test_powerpoint_to_images_returns_every_slide(page, base_url, files):
    open_tool(page, base_url, "ppt-to-images")
    run(page, files / "sample.pptx")
    assert page.is_visible("#result"), page.inner_text("#problem-reason") if page.is_visible("#problem") else ""
    assert page.get_attribute("#download", "download") == "sample_forgefiles.org.zip"
    assert page.inner_text("#result-message").startswith("Rendered 3 slide(s)")
    zf = zipfile.ZipFile(io.BytesIO(download_bytes(page)))
    assert [i.filename for i in zf.infolist()] == [f"sample_slide_00{n}.png" for n in (1, 2, 3)]
    from PIL import Image

    for info in zf.infolist():
        img = Image.open(io.BytesIO(zf.read(info)))
        assert img.format == "PNG" and img.width >= 1000 and img.width > img.height
        assert len(set(img.convert("L").resize((64, 36)).getdata())) > 3, "slide image must not be blank"
    assert page.api_requests == []


def test_no_third_party_requests_and_engine_is_reused(page, base_url, files):
    open_tool(page, base_url, "word-to-pdf")
    run(page, files / "sample.docx")
    assert page.is_visible("#result")
    origin = base_url
    assert all(u.startswith(origin) or u.startswith("blob:") or u.startswith("data:") for u in page.all_requests), [
        u for u in page.all_requests if not (u.startswith(origin) or u.startswith("blob:") or u.startswith("data:"))
    ]
    wasm_before = sum(1 for u in page.all_requests if u.endswith("soffice.wasm"))
    assert wasm_before == 1
    # Second conversion in the same page: the warm engine is reused, nothing is downloaded again.
    page.click("#again")
    run(page, files / "sample.docx")
    assert page.is_visible("#result")
    assert sum(1 for u in page.all_requests if u.endswith("soffice.wasm")) == 1
    assert page.api_requests == []


# ── declines: nothing runs, nothing uploads ───────────────────────────────

def test_tamil_document_is_declined_before_the_engine_loads(page, base_url, files):
    open_tool(page, base_url, "word-to-pdf")
    run(page, files / "tamil.docx", timeout=30_000)
    assert page.is_visible("#problem")
    assert "Tamil" in page.inner_text("#problem-reason")
    assert page.get_attribute("#server-link", "href") == "/word-to-pdf"
    assert not any(u.endswith("soffice.wasm") or u.endswith("soffice.data") for u in page.all_requests)
    assert page.api_requests == []


@pytest.mark.parametrize("name,expect", [
    ("broken.docx", "could not be read"),
    ("legacy.doc", "supports .docx"),
])
def test_unreadable_and_unsupported_files_are_declined(page, base_url, files, name, expect):
    open_tool(page, base_url, "word-to-pdf")
    run(page, files / name, timeout=30_000)
    assert page.is_visible("#problem")
    assert expect in page.inner_text("#problem-reason")
    assert not any(u.endswith("soffice.wasm") for u in page.all_requests)
    assert page.api_requests == []


def test_over_budget_file_is_declined(page, base_url, tmp_path):
    big = tmp_path / "big.docx"
    with big.open("wb") as fh:
        fh.truncate(51 * 1024 * 1024)
    open_tool(page, base_url, "word-to-pdf")
    run(page, big, timeout=60_000)
    assert "50 MB" in page.inner_text("#problem-reason")
    assert not any(u.endswith("soffice.wasm") for u in page.all_requests)


def test_tampered_engine_file_fails_closed(page, base_url, files):
    real = (ENGINE / "browser.js").read_bytes()
    page.route("**/lo-wasm/2.7.2/browser.js",
               lambda route: route.fulfill(status=200, body=real + b"\n/* tampered */", content_type="text/javascript",
                                           headers={"Cross-Origin-Embedder-Policy": "require-corp",
                                                    "Cross-Origin-Resource-Policy": "same-origin"}))
    open_tool(page, base_url, "word-to-pdf")
    run(page, files / "sample.docx", timeout=60_000)
    assert page.is_visible("#problem")
    assert "could not be loaded or verified" in page.inner_text("#problem-reason")
    assert not any(u.endswith("soffice.wasm") for u in page.all_requests)
    assert page.api_requests == []


def test_cancel_stops_without_upload_and_page_recovers(page, base_url, files):
    open_tool(page, base_url, "word-to-pdf")
    page.set_input_files("#file", str(files / "sample.docx"))
    page.click("#convert")
    page.wait_for_selector("#cancel:not(.hidden)", timeout=5000)
    page.click("#cancel")
    page.wait_for_selector("#problem:not(.hidden)", timeout=30_000)
    assert "Cancelled" in page.inner_text("#problem-reason")
    assert page.is_enabled("#convert")
    assert page.api_requests == []
    # And it can be used again afterwards.
    page.click("#problem-dismiss")
    page.click("#convert")
    page.wait_for_selector("#result:not(.hidden)", timeout=CONVERT_TIMEOUT_MS)
    assert page.is_visible("#result")


# ── hand-off and entry points ─────────────────────────────────────────────

def test_file_handoff_from_a_tool_page_over_broadcast_channel(browser, base_url, files):
    ctx = browser.new_context()
    try:
        opener = ctx.new_page()
        opener.goto(base_url + "/static/on-device-office/office-preflight.js")  # any same-origin document
        opener.evaluate(
            """() => {
                window.__sent = false;
                window.__ch = new BroadcastChannel('ff-office-handoff');
                window.__ch.onmessage = (e) => {
                    if (e.data.type === 'ready' && e.data.token === 'tok123') {
                        window.__ch.postMessage({ type: 'file', token: 'tok123', file: new File(['hello'], 'memo.docx'),
                                                  name: 'memo.docx', op: 'word-to-pdf' });
                        window.__sent = true;
                    }
                };
            }"""
        )
        iso = ctx.new_page()
        iso.goto(base_url + "/on-device-office/?op=word-to-pdf&h=tok123")
        iso.wait_for_function("document.getElementById('convert').disabled === false", timeout=10_000)
        assert opener.evaluate("window.__sent") is True
        # A different token must not be accepted.
        stranger = ctx.new_page()
        stranger.goto(base_url + "/on-device-office/?op=word-to-pdf&h=other")
        stranger.wait_for_timeout(1000)
        assert stranger.is_disabled("#convert")
    finally:
        ctx.close()


def test_entry_buttons_visible_on_desktop_hidden_on_phones(browser, base_url):
    desktop = browser.new_context()
    try:
        pg = desktop.new_page()
        pg.goto(base_url + "/")
        pg.wait_for_function("window.ffOfficeEntry !== undefined")
        hidden = pg.evaluate("[...document.querySelectorAll('[data-on-device-office]')].map(e => [e.dataset.onDeviceOffice, e.hidden])")
        assert sorted(hidden) == [["excel-to-pdf", False], ["ppt-to-images", False], ["ppt-to-pdf", False], ["word-to-pdf", False]]
        pg.evaluate("() => { const f = document.getElementById('ppt-images-format'); f.value = 'jpg'; f.dispatchEvent(new Event('change')); }")
        assert pg.evaluate("document.querySelector('[data-on-device-office=ppt-to-images]').hidden") is True
    finally:
        desktop.close()

    phone = browser.new_context(
        user_agent="Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Mobile Safari/537.36",
        viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True,
    )
    try:
        pg = phone.new_page()
        pg.goto(base_url + "/")
        pg.wait_for_function("window.ffOfficeEntry !== undefined")
        assert pg.evaluate("[...document.querySelectorAll('[data-on-device-office]')].every(e => e.hidden)")
    finally:
        phone.close()


def test_manifest_matches_files_on_disk():
    manifest = json.loads((ENGINE / "MANIFEST.json").read_text(encoding="utf-8"))
    sys.path.insert(0, str(PUBLIC / "scripts"))
    import fetch_office_engine

    assert fetch_office_engine.verify(ENGINE, manifest) == []
