"""Real-Chromium harness for the on-device conversion tests (work packages 16, 42, 21, 17, 18).

The conversions run the shipped `public/static/local/*.js` against the real vendored
pdf.js in headless Chromium, served over HTTP from `public/` so the handlers resolve
their same-origin engine files exactly as the app does. Nothing is mocked except the
consent dialog (declined unless a test says otherwise) and `/api/*`, which is aborted and
recorded: a test asserts "no upload" by checking `api_requests` stayed empty.

The vendored pdf.js (6.4.299) needs `Map.prototype.getOrInsertComputed`, which ships in
Chromium 145+. `open_browser` therefore picks the newest installed Chromium that has it and
skips (with the reason) when none does, rather than reporting a false pass or a false fail.
"""

from __future__ import annotations

import base64
import http.server
import os
import re
import socketserver
import sys
import threading
from pathlib import Path

import pytest

sync_api = pytest.importorskip("playwright.sync_api")

PUBLIC = Path(__file__).resolve().parent.parent
STATIC = PUBLIC / "static"

BASE_SCRIPTS = ["local/ff-server-gate.js", "local/ff-local.js"]


class _Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **k):
        super().__init__(*a, directory=str(PUBLIC), **k)

    def log_message(self, *a):  # keep pytest output clean
        pass

    def guess_type(self, path):
        if path.endswith((".mjs", ".js")):
            return "text/javascript"
        if path.endswith(".wasm"):
            return "application/wasm"
        return super().guess_type(path)

    def do_GET(self):
        if self.path.split("?")[0] == "/blank":
            body = b"<!doctype html><meta charset=utf-8><title>harness</title>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        super().do_GET()


class _Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def start_server() -> tuple[_Server, str]:
    srv = _Server(("127.0.0.1", 0), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


def _browser_roots() -> list[Path]:
    env = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if env and env != "0":
        return [Path(env)]
    if sys.platform == "win32":
        return [Path(os.environ.get("LOCALAPPDATA", "")) / "ms-playwright"]
    if sys.platform == "darwin":
        return [Path.home() / "Library" / "Caches" / "ms-playwright"]
    return [Path.home() / ".cache" / "ms-playwright"]


def _chromium_executables() -> list[Path]:
    found = []
    for root in _browser_roots():
        for d in root.glob("chromium-*"):
            m = re.match(r"chromium-(\d+)$", d.name)
            if not m:
                continue
            for rel in ("chrome-win64/chrome.exe", "chrome-win/chrome.exe", "chrome-linux/chrome",
                        "chrome-linux64/chrome", "chrome-mac/Chromium.app/Contents/MacOS/Chromium",
                        "chrome-mac-arm64/Chromium.app/Contents/MacOS/Chromium"):
                exe = d / rel
                if exe.exists():
                    found.append((int(m.group(1)), exe))
    return [e for _, e in sorted(found, reverse=True)]


_NEEDS = "typeof Map.prototype.getOrInsertComputed === 'function'"


def open_browser(pw):
    """Launch a Chromium that can run the vendored pdf.js, or skip."""
    tried = []
    for exe in [None] + _chromium_executables():
        try:
            browser = pw.chromium.launch() if exe is None else pw.chromium.launch(executable_path=str(exe))
        except Exception as exc:  # pragma: no cover - environment dependent
            tried.append(f"{exe or 'default'}: {exc}")
            continue
        page = browser.new_page()
        ok = page.evaluate(_NEEDS)
        page.close()
        if ok:
            return browser
        tried.append(f"{exe or 'default'}: {browser.version} lacks Map.getOrInsertComputed")
        browser.close()
    pytest.skip("no Chromium able to run the vendored pdf.js (needs 145+): " + "; ".join(tried))


_RUN = """async ([route, files, fields, abortMs]) => {
  window.__asked.length = 0;
  const fd = new FormData();
  for (const f of files) {
    const bin = Uint8Array.from(atob(f.b64), c => c.charCodeAt(0));
    fd.append(f.field || 'file', new File([bin], f.name, { type: f.type || 'application/octet-stream' }));
  }
  for (const [k, v] of Object.entries(fields)) fd.append(k, String(v));
  const init = { onProgress: (i, n) => window.__progress.push([i, n]) };
  window.__progress.length = 0;
  let ctl = null;
  if (abortMs !== null) { ctl = new AbortController(); init.signal = ctl.signal; setTimeout(() => ctl.abort(), abortMs); }
  let res;
  try { res = await ffProcess(route, fd, init); }
  catch (e) { return { threw: e.name || String(e), asked: window.__asked.slice(), progress: window.__progress.slice() }; }
  const ct = res.headers.get('Content-Type') || '';
  let body, events = null;
  if (ct.includes('event-stream')) {
    // The web UI reads some routes as server-sent events; the last `complete` event carries the result.
    const text = await res.text();
    events = text.split('\\n\\n').filter(f => f.startsWith('data: ')).map(f => JSON.parse(f.slice(6)));
    body = Object.assign({ status: 'success' }, events.find(e => e.event === 'complete') || {});
    const bad = events.find(e => e.event === 'error');
    if (bad) body = { detail: bad.detail };
  } else {
    body = await res.json();
  }
  let out = null;
  if (res.ok && body.download_token) {
    const entry = ffLocal.resolve(body.download_token);
    if (entry) {
      const u = new Uint8Array(await entry.blob.arrayBuffer());
      let s = '';
      for (let i = 0; i < u.length; i += 0x8000) s += String.fromCharCode(...u.subarray(i, i + 0x8000));
      out = btoa(s);
      ffLocal.release(body.download_token);
    }
  }
  return { status: res.status, detail: body.detail, message: body.message, filename: body.filename, out, body, events,
           contentType: ct, asked: window.__asked.slice(), progress: window.__progress.slice() };
}"""


class LocalPage:
    """One page with the shipped local scripts loaded and `/api/*` blocked and recorded."""

    def __init__(self, browser, base: str, scripts: list[str], consent: bool = False, init_script: str | None = None):
        self.page = browser.new_page()
        if init_script:
            self.page.add_init_script(init_script)
        self.api_requests: list[str] = []
        self.console: list[str] = []
        self.page.on("console", lambda m: self.console.append(m.text) if m.type in ("error", "warning") else None)
        self.requests: list[str] = []
        self.page.on("request", lambda r: self.requests.append(r.url))
        self.page.route("**/api/**", self._block)
        self.page.goto(base + "/blank")
        self.page.evaluate(
            "window.apiUrl = p => p; window.__asked = []; window.__progress = [];"
        )
        for name in BASE_SCRIPTS + scripts:
            self.page.add_script_tag(content=(STATIC / name).read_text(encoding="utf8"))
        self.page.evaluate(
            "(c) => { ffConsent.handler = info => { window.__asked.push(info); return c; }; }", consent
        )

    def _block(self, route):
        self.api_requests.append(route.request.url)
        route.abort()

    def set_consent(self, agree: bool):
        self.page.evaluate("(c) => { ffConsent.handler = info => { window.__asked.push(info); return c; }; }", agree)

    def run(self, route: str, files, fields: dict | None = None, abort_ms: int | None = None) -> dict:
        """`files`: [(name, bytes)] or [(name, bytes, field, mime)]."""
        payload = []
        for f in files:
            name, data = f[0], f[1]
            payload.append({
                "name": name, "b64": base64.b64encode(data).decode(),
                "field": f[2] if len(f) > 2 else "file", "type": f[3] if len(f) > 3 else "application/octet-stream",
            })
        self.api_requests.clear()
        r = self.page.evaluate(_RUN, [route, payload, fields or {}, abort_ms])
        r["bytes"] = base64.b64decode(r["out"]) if r.get("out") else None
        r["api_requests"] = list(self.api_requests)
        return r

    def close(self):
        self.page.close()
