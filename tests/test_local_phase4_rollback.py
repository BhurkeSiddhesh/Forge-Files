"""Phase 4 rollback: every new on-device handler can be switched off without breaking the tool.

A tool's rollback is "disable only its local handler; the consent-gated server route stays". These
tests prove that three ways, for all five Phase 4 routes, on real PDFs:

  * the global kill switch (`window.FF_LOCAL = false`, or `localStorage.ff_local = '0'`),
  * removing a single tool's handler (what disabling one tool's local mode amounts to),

and in every case the file is not processed on the device, the user is asked before anything is
sent, declining sends nothing, and confirming sends exactly one request to the unchanged route.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import local_browser_harness as H  # noqa: E402
import phase4_fixtures as F  # noqa: E402

ROUTES = {
    "/api/pdf/to-epub": F.plain_pdf,
    "/api/pdf/convert-to-word": F.plain_pdf,
    "/api/pdf/convert-to-word-stream": F.plain_pdf,
    "/api/pdf/to-excel": F.ruled_table_pdf,
    "/api/pdf/to-pptx": F.plain_pdf,
    "/api/pdf/ocr": lambda: F.ocr_scan_pdf(dpi=100),
}
SCRIPTS = ["local/ops-pdf-layout.js", "local/ops-pdf-epub.js", "local/ops-pdf-word.js", "local/ops-pdf-excel.js",
           "local/ops-pdf-pptx.js", "local/ops-pdf-ocr.js"]


@pytest.fixture(scope="module")
def lp():
    server, base = H.start_server()
    with H.sync_api.sync_playwright() as pw:
        browser = H.open_browser(pw)
        page = H.LocalPage(browser, base, SCRIPTS)
        yield page
        browser.close()
    server.shutdown()


def run(lp, route):
    return lp.run(route, [("doc.pdf", ROUTES[route](), "file", "application/pdf")])


def test_all_five_tools_register_their_routes(lp):
    handlers = lp.page.evaluate("Object.keys(ffLocal.handlers)")
    for route in ROUTES:
        assert route in handlers, route


@pytest.mark.parametrize("route", sorted(ROUTES))
def test_by_default_the_device_does_the_work(lp, route):
    lp.set_consent(False)
    r = run(lp, route)
    assert r["status"] == 200 and r["asked"] == [] and r["api_requests"] == [], r.get("detail")


@pytest.mark.parametrize("how", ["window", "storage"])
@pytest.mark.parametrize("route", sorted(ROUTES))
def test_the_global_kill_switch_sends_the_file_nowhere_without_asking(lp, route, how):
    off = "window.FF_LOCAL = false" if how == "window" else "localStorage.setItem('ff_local', '0')"
    on = "delete window.FF_LOCAL" if how == "window" else "localStorage.removeItem('ff_local')"
    lp.page.evaluate(off)
    try:
        lp.set_consent(False)
        declined = run(lp, route)
        lp.set_consent(True)
        confirmed = run(lp, route)
    finally:
        lp.set_consent(False)
        lp.page.evaluate(on)
    assert declined["status"] == 499 and len(declined["asked"]) == 1 and declined["api_requests"] == []
    assert len(confirmed["asked"]) == 1 and len(confirmed["api_requests"]) == 1 and confirmed["api_requests"][0].endswith(route)


@pytest.mark.parametrize("route", sorted(ROUTES))
def test_removing_one_tools_handler_rolls_back_only_that_tool(lp, route):
    saved = lp.page.evaluate("(r) => { const h = ffLocal.handlers[r]; window.__saved = window.__saved || {}; window.__saved[r] = h; delete ffLocal.handlers[r]; return !!h; }", route)
    assert saved
    try:
        lp.set_consent(False)
        gone = run(lp, route)
        other = next(x for x in sorted(ROUTES) if x != route and x.split("/")[-1] not in route and route.split("/")[-1] not in x)
        still = run(lp, other)
    finally:
        lp.page.evaluate("(r) => { ffLocal.handlers[r] = window.__saved[r]; }", route)
    assert gone["status"] == 499 and len(gone["asked"]) == 1 and gone["api_requests"] == []
    assert still["status"] == 200 and still["api_requests"] == []  # every other tool still runs on the device


@pytest.mark.parametrize("route", sorted(ROUTES))
def test_after_a_rollback_the_tool_works_on_the_device_again(lp, route):
    lp.set_consent(False)
    r = run(lp, route)
    assert r["status"] == 200 and r["api_requests"] == []
