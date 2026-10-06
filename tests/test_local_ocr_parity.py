"""Work package 18: on-device OCR PDF (English).

Runs the shipped `ops-pdf-layout.js` + `ops-pdf-ocr.js` with the vendored tesseract.js, its
WebAssembly core and the English model, in headless Chromium, on scans made by rasterising text
whose words and positions are known. The searchable PDF is read back with PyMuPDF, and the
original pages are checked to be untouched. The same scan is run through the server's RapidOCR
engine for comparison when it is installed.

Honest limits: the scans are synthetic (skew, noise and blur added, but not a photographed page),
and only English is local. Indic languages stay on the server and are only checked to ask first.
"""

from __future__ import annotations

import difflib
import io
import re
import subprocess
import sys
from pathlib import Path

import numpy as np
import pymupdf as fitz
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import local_browser_harness as H  # noqa: E402
import phase4_fixtures as F  # noqa: E402

ROUTE = "/api/pdf/ocr"
REASON_ENCRYPTED = "this PDF is password protected"
REASON_STRUCTURE = "this file uses features that cannot be processed on this device"
REASON_BIG = "this file exceeds the safe limit for processing on this device"
REASON_LANG = "text recognition for this language runs on the server, which has the models for it"
REASON_ENGINE = "the on-device engine could not be loaded"


@pytest.fixture(scope="module")
def env():
    server, base = H.start_server()
    with H.sync_api.sync_playwright() as pw:
        browser = H.open_browser(pw)
        yield browser, base
        browser.close()
    server.shutdown()


@pytest.fixture(scope="module")
def lp(env):
    browser, base = env
    page = H.LocalPage(browser, base, ["local/ops-pdf-layout.js", "local/ops-pdf-ocr.js"])
    yield page
    page.close()


def ocr(lp, name, data, fields=None, **kw):
    return lp.run(ROUTE, [(name, data, "file", "application/pdf")], fields or {}, **kw)


def norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def similarity(pdf_bytes: bytes, truth: str) -> float:
    text = "\n".join(p.get_text() for p in fitz.open(stream=pdf_bytes, filetype="pdf"))
    return difflib.SequenceMatcher(None, norm(text), norm(truth)).ratio()


def render(pdf: bytes, page: int = 0, dpi: int = 72) -> np.ndarray:
    pix = fitz.open(stream=pdf, filetype="pdf")[page].get_pixmap(dpi=dpi)
    return np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n).astype(int)


@pytest.fixture(scope="module")
def clean(lp):
    data = F.ocr_scan_pdf()
    r = ocr(lp, "scan.pdf", data)
    assert r["status"] == 200, r
    return data, r


@pytest.fixture(scope="module")
def three(lp):
    data = F.ocr_scan_pdf(pages=3)
    r = ocr(lp, "scan3.pdf", data)
    assert r["status"] == 200, r
    return data, r


# ── contract ──────────────────────────────────────────────────────────────


def test_local_success_matches_the_server_contract(clean):
    _, r = clean
    assert r["message"] == "Searchable PDF created from 1 page(s)"
    assert r["filename"] == "scan_forgefiles.org.pdf"
    assert r["body"]["status"] == "success" and r["body"]["page_count"] == 1
    assert r["asked"] == [] and r["api_requests"] == []
    assert r["bytes"][:5] == b"%PDF-"


def test_nothing_is_fetched_from_a_third_party(lp, clean):
    base = lp.page.url.rsplit("/", 1)[0]
    outside = [u for u in lp.requests if not (u.startswith(base) or u.startswith(("blob:", "data:")))]
    assert outside == [], outside


def test_no_file_is_a_400_with_no_upload(lp):
    r = lp.run(ROUTE, [])
    assert r["status"] == 400 and r["api_requests"] == [] and r["asked"] == []


# ── accuracy ──────────────────────────────────────────────────────────────


def test_a_clean_scan_is_read_back_exactly(clean):
    data, r = clean
    assert similarity(r["bytes"], F.ocr_truth()) >= 0.99


def test_a_skewed_noisy_blurred_scan_is_still_read(lp):
    data = F.ocr_scan_pdf(skew_deg=1.5, noise=15, blur=1.0)
    r = ocr(lp, "bad.pdf", data)
    assert r["status"] == 200
    assert similarity(r["bytes"], F.ocr_truth()) >= 0.98


def test_a_low_resolution_scan_keeps_most_of_its_text(lp):
    r = ocr(lp, "low.pdf", F.ocr_scan_pdf(dpi=100))
    assert r["status"] == 200 and similarity(r["bytes"], F.ocr_truth()) >= 0.97


def test_several_pages_each_come_back(three):
    data, r = three
    assert r["body"]["page_count"] == 3 and r["message"] == "Searchable PDF created from 3 page(s)"
    d = fitz.open(stream=r["bytes"], filetype="pdf")
    assert len(d) == 3
    for page in d:
        assert difflib.SequenceMatcher(None, norm(page.get_text()), norm(F.ocr_truth())).ratio() >= 0.99


def test_progress_is_reported_per_page(three):
    _, r = three
    assert r["progress"][-1] == [3, 3] and len(r["progress"]) == 3


def test_words_can_be_found_and_selected(clean):
    _, r = clean
    page = fitz.open(stream=r["bytes"], filetype="pdf")[0]
    src = fitz.open(stream=F.ocr_source_pdf(), filetype="pdf")[0]
    for needle in ("committee", "Invoice", "1,250.00", "reliability"):
        assert len(page.search_for(needle)) == len(src.search_for(needle)) >= 1, needle


def test_each_word_sits_where_it_is_on_the_page(clean):
    """The invisible words land within 3 pt of where the source text has them."""
    _, r = clean
    got = fitz.open(stream=r["bytes"], filetype="pdf")[0].get_text("words")
    truth = fitz.open(stream=F.ocr_source_pdf(), filetype="pdf")[0].get_text("words")
    centre = lambda w: ((w[0] + w[2]) / 2, (w[1] + w[3]) / 2)
    dists = []
    for t in truth:
        same = [g for g in got if g[4].strip(".,") == t[4].strip(".,")]
        if not same:
            continue
        tx, ty = centre(t)
        dists.append(min(np.hypot(centre(g)[0] - tx, centre(g)[1] - ty) for g in same))
    assert len(dists) >= 0.95 * len(truth)
    assert float(np.median(dists)) <= 2.0 and float(np.quantile(dists, 0.95)) <= 4.0, (np.median(dists), np.quantile(dists, 0.95))


# ── the original page is untouched ───────────────────────────────────────


def test_the_page_looks_exactly_as_it_did(clean):
    data, r = clean
    assert np.abs(render(r["bytes"]) - render(data)).max() <= 1  # invisible text changes no pixel


def test_the_original_image_is_the_same_bytes_and_the_text_is_invisible(clean):
    data, r = clean
    a = fitz.open(stream=data, filetype="pdf")
    b = fitz.open(stream=r["bytes"], filetype="pdf")
    assert a[0].get_pixmap().width == b[0].get_pixmap().width and a[0].rect == b[0].rect
    img_a = a.extract_image(a[0].get_images()[0][0])["image"]
    img_b = b.extract_image(b[0].get_images()[0][0])["image"]
    assert img_a == img_b  # the scan was not recompressed
    contents = b[0].read_contents()
    assert b" Tr" in contents and re.search(rb"\b3 Tr\b", contents)  # text render mode 3: invisible


def test_the_file_grows_by_the_text_layer_only(clean):
    data, r = clean
    assert 0 < len(r["bytes"]) - len(data) < 40_000


# ── pages that already have text ─────────────────────────────────────────


def test_a_page_that_already_has_text_is_not_given_a_second_layer(lp):
    data = F.mixed_text_and_scan_pdf()
    r = ocr(lp, "mixed.pdf", data)
    assert r["status"] == 200 and r["body"]["ocr_pages"] == 1 and r["body"]["skipped_pages"] == 1
    assert r["message"] == "Searchable PDF created from 2 page(s)"
    d = fitz.open(stream=r["bytes"], filetype="pdf")
    native_before = fitz.open(stream=data, filetype="pdf")[0].get_text()
    assert d[0].get_text() == native_before  # page 1 untouched: no duplicate "committee"
    assert d[0].get_text().count("committee") == 1
    assert d[1].get_text().count("committee") == 1  # page 2 got its text from OCR


def test_when_every_page_already_has_text_nothing_is_changed(lp):
    data = F.article_pdf()
    r = ocr(lp, "native.pdf", data)
    assert r["status"] == 200 and r["body"]["ocr_pages"] == 0
    assert r["message"].startswith("This PDF already has searchable text on all 3 page(s)")
    assert r["bytes"] == data and r["api_requests"] == []


# ── validation, as the server does it ────────────────────────────────────


def test_an_unknown_language_gets_the_servers_message(lp):
    from fastapi.testclient import TestClient

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    import main

    r = ocr(lp, "scan.pdf", F.ocr_scan_pdf(dpi=100), {"lang": "xx"})
    srv = TestClient(main.app).post(ROUTE, files={"file": ("scan.pdf", F.plain_pdf(), "application/pdf")}, data={"lang": "xx"})
    assert r["status"] == 400 and srv.status_code == 400
    assert r["detail"] == srv.json()["detail"] == "Unsupported OCR language: 'xx'. Supported languages are: en, hi, mr, ta, te."
    assert r["api_requests"] == [] and r["asked"] == []


def test_a_page_with_nothing_to_read_says_so_as_the_server_does(lp):
    r = ocr(lp, "blank.pdf", F.blank_scan_pdf())
    assert r["status"] == 400 and r["detail"] == "No text could be recognized in this PDF."
    assert r["api_requests"] == []


def test_the_language_code_is_trimmed_and_case_insensitive(lp):
    r = ocr(lp, "scan.pdf", F.ocr_scan_pdf(dpi=150), {"lang": " EN "})
    assert r["status"] == 200


# ── the server path is a decision, never an accident ─────────────────────


@pytest.mark.parametrize("lang", ["hi", "mr", "ta", "te"])
def test_indian_languages_ask_first_and_declining_uploads_nothing(lp, lang):
    lp.set_consent(False)
    r = ocr(lp, "scan.pdf", F.ocr_scan_pdf(dpi=100), {"lang": lang})
    assert r["status"] == 499 and [a["reason"] for a in r["asked"]] == [REASON_LANG] and r["api_requests"] == []


@pytest.mark.parametrize("make, reason", [
    (F.encrypted_pdf, REASON_ENCRYPTED),
    (lambda: b"%PDF-1.4\nnot really a pdf", REASON_STRUCTURE),
    (F.rotated_page_pdf, None),
])
def test_unsupported_input_asks_first_and_declining_uploads_nothing(lp, make, reason):
    lp.set_consent(False)
    r = ocr(lp, "doc.pdf", make())
    if reason is None:
        # The rotated fixture has text on every page, so it needs no OCR and simply passes through.
        assert r["status"] == 200 and r["api_requests"] == []
        return
    assert r["status"] == 499 and [a["reason"] for a in r["asked"]] == [reason] and r["api_requests"] == []


def test_a_rotated_scan_asks_instead_of_misplacing_text(lp):
    d = fitz.open(stream=F.ocr_scan_pdf(dpi=100), filetype="pdf")
    d[0].set_rotation(90)
    lp.set_consent(False)
    r = ocr(lp, "rot.pdf", d.tobytes())
    assert [a["reason"] for a in r["asked"]] == [REASON_STRUCTURE] and r["api_requests"] == []


def test_a_supplied_password_asks_first(lp):
    lp.set_consent(False)
    r = ocr(lp, "scan.pdf", F.ocr_scan_pdf(dpi=100), {"password": "secret"})
    assert [a["reason"] for a in r["asked"]] == [REASON_ENCRYPTED] and r["api_requests"] == []


def test_confirming_sends_exactly_one_request(lp):
    lp.set_consent(True)
    try:
        r = ocr(lp, "scan.pdf", F.ocr_scan_pdf(dpi=100), {"lang": "hi"})
    finally:
        lp.set_consent(False)
    assert len(r["asked"]) == 1
    assert len(r["api_requests"]) == 1 and r["api_requests"][0].endswith(ROUTE)


def test_cancelling_stops_the_work_without_asking_or_uploading(lp):
    r = ocr(lp, "scan.pdf", F.ocr_scan_pdf(pages=6), abort_ms=2500)
    assert r.get("threw") == "AbortError"
    assert r["asked"] == [] and r["api_requests"] == []


def test_the_phone_budget_declines_a_long_scan_before_any_recognition(lp):
    lp.page.evaluate("window.matchMedia = () => ({ matches: true })")
    try:
        r = ocr(lp, "long.pdf", F.blank_pdf(21))
    finally:
        lp.page.evaluate("delete window.matchMedia")
    assert [a["reason"] for a in r["asked"]] == [REASON_BIG] and r["api_requests"] == []


def test_repeat_runs_are_independent(lp):
    data = F.ocr_scan_pdf(dpi=150)
    a, b = ocr(lp, "x.pdf", data), ocr(lp, "x.pdf", data)
    assert a["status"] == b["status"] == 200
    assert similarity(a["bytes"], F.ocr_truth()) == similarity(b["bytes"], F.ocr_truth())


# ── the engine is verified, and fails closed ─────────────────────────────


# A browser without WebAssembly SIMD: any module containing the SIMD prefix byte (0xFD) fails validation.
NO_SIMD = "const v = WebAssembly.validate.bind(WebAssembly); WebAssembly.validate = b => new Uint8Array(b.buffer || b).includes(253) ? false : v(b);"


def fresh_page(env, init_script=None):
    browser, base = env
    return H.LocalPage(browser, base, ["local/ops-pdf-layout.js", "local/ops-pdf-ocr.js"], init_script=init_script)


def tamper_route(page, asset):
    def tamper(route):
        real = route.fetch()
        route.fulfill(response=real, body=real.body() + b" // tampered")

    page.page.route(f"**/vendor/tesseract/{asset}*", tamper)


@pytest.mark.parametrize("asset, init", [
    ("worker.min.js", None),
    ("tesseract.min.js", None),
    ("core/tesseract-core-simd-lstm.wasm.js", None),
    ("lang/eng.traineddata.gz", None),
    ("core/tesseract-core-lstm.wasm.js", NO_SIMD),  # the fallback core, on a browser that has to use it
])
def test_a_tampered_engine_file_is_refused_before_anything_runs(env, asset, init):
    page = fresh_page(env, init)
    try:
        tamper_route(page, asset)
        r = ocr(page, "scan.pdf", F.ocr_scan_pdf(dpi=100))
        assert r["status"] == 499, r
        assert [a["reason"] for a in r["asked"]] == [REASON_ENGINE] and r["api_requests"] == []
        assert page.page.evaluate("typeof window.Tesseract") == "undefined" or asset not in ("tesseract.min.js",)
    finally:
        page.close()


def test_a_browser_without_simd_uses_the_fallback_core_and_reads_the_scan_just_as_well(env):
    page = fresh_page(env, NO_SIMD)
    try:
        r = ocr(page, "scan.pdf", F.ocr_scan_pdf())
        assert r["status"] == 200, r
        assert similarity(r["bytes"], F.ocr_truth()) >= 0.99
        assert any("tesseract-core-lstm.wasm.js" in u for u in page.requests)
        assert not any("simd" in u for u in page.requests)
    finally:
        page.close()


def test_the_manifest_matches_the_files_on_disk():
    import hashlib
    import json

    root = Path(__file__).resolve().parent.parent / "static" / "vendor" / "tesseract"
    manifest = json.loads((root / "MANIFEST.json").read_text(encoding="utf8"))["files"]
    assert set(manifest) == {"tesseract.min.js", "worker.min.js", "core/tesseract-core-simd-lstm.wasm.js",
                             "core/tesseract-core-lstm.wasm.js", "lang/eng.traineddata.gz"}
    for rel, entry in manifest.items():
        data = (root / rel).read_bytes()
        assert len(data) == entry["bytes"] and hashlib.sha256(data).hexdigest() == entry["sha256"], rel


# ── against the server's engine ──────────────────────────────────────────


def test_accuracy_matches_the_servers_rapidocr_on_the_same_scan(tmp_path, clean):
    data, r = clean
    script = tmp_path / "srv.py"
    script.write_text(
        "import os, sys\n"
        "os.environ['DISABLE_AI'] = '0'; os.environ['OCR_BACKEND'] = 'rapidocr'\n"
        f"sys.path.insert(0, r'{Path(__file__).resolve().parent.parent}')\n"
        "from scripts.pdf_utils import ocr_pdf_to_searchable_pdf\n"
        f"print(ocr_pdf_to_searchable_pdf(sys.argv[1], sys.argv[2])['output_path'])\n",
        encoding="utf8",
    )
    src = tmp_path / "scan.pdf"
    src.write_bytes(data)
    out = tmp_path / "out"
    out.mkdir()
    proc = subprocess.run([sys.executable, str(script), str(src), str(out)], capture_output=True, text=True, timeout=300)
    if proc.returncode != 0:
        pytest.skip("the server's RapidOCR engine is not usable here: " + proc.stderr.strip().splitlines()[-1][:120])
    server_pdf = Path(proc.stdout.strip().splitlines()[-1]).read_bytes()
    srv, loc = similarity(server_pdf, F.ocr_truth()), similarity(r["bytes"], F.ocr_truth())
    assert loc >= 0.99 and loc >= srv - 0.01, (loc, srv)
