"""Forge Files for Windows: run the local FastAPI app inside a native window.

Everything runs on the user's machine. The server binds to 127.0.0.1 only,
writable state lives under %LOCALAPPDATA%\\ForgeFiles, and nothing is uploaded
anywhere. Run directly (``python desktop/launcher.py``) or frozen by PyInstaller
(see ``desktop/forgefiles.spec``).
"""
from __future__ import annotations

import logging
import os
import socket
import sys
import threading
import time
import urllib.request
from pathlib import Path

APP_NAME = "ForgeFiles"
# A stable port keeps the WebView origin stable, so saved preferences
# (localStorage) survive restarts. Some machines reserve arbitrary ports (Hyper-V,
# WinNAT), so try a few before falling back to any free port.
PREFERRED_PORTS = (48731, 48732, 48733, 48734, 48735)
HOST = "127.0.0.1"


def _app_root() -> Path:
    """Directory holding main.py, scripts/ and static/."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parent.parent


def _data_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    path = Path(base) / APP_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def _can_bind(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind((HOST, port))
        except OSError:
            return False
        return True


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((HOST, 0))
        return s.getsockname()[1]


def _is_forge_files(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://{HOST}:{port}/static/config.js", timeout=2) as r:
            return r.status == 200
    except Exception:
        return False


def _add_libreoffice_to_path() -> None:
    """Use a system LibreOffice for high-fidelity Office conversion if present."""
    for root in (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)")):
        if not root:
            continue
        program = Path(root) / "LibreOffice" / "program"
        if (program / "soffice.exe").exists():
            os.environ["PATH"] = f"{program}{os.pathsep}{os.environ.get('PATH', '')}"
            return


def _app_version() -> str:
    try:
        return (_app_root() / "VERSION").read_text(encoding="utf-8").strip() or "0.0.0"
    except OSError:
        return "0.0.0"


class DesktopApi:
    """Methods the page may call as ``window.pywebview.api.<name>``."""

    def open_external(self, url: str) -> bool:
        """Open a web link in the person's normal browser (never inside the app)."""
        from urllib.parse import urlparse

        parsed = urlparse(str(url))
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            return False
        import webbrowser

        return webbrowser.open(str(url))


def _configure_environment(data: Path, port: int) -> None:
    os.environ.setdefault("FF_APP_VERSION", _app_version())
    os.environ.setdefault("FF_DATA_DIR", str(data))
    os.environ.setdefault("EVENT_DB_PATH", str(data / "events.db"))
    os.environ.setdefault("BASE_URL", f"http://{HOST}:{port}")
    # One user on their own machine: the per-IP abuse limiter only gets in the way.
    os.environ.setdefault("RATE_LIMIT_ENABLED", "0")
    # No third-party requests of any kind: strips analytics and enforces a CSP.
    os.environ.setdefault("FF_LOCAL_ONLY", "1")
    # Force (not default) every analytics/ads key off, even if the machine has one set.
    for key in ("GA_MEASUREMENT_ID", "GOOGLE_ANALYTICS_ID", "GA_API_SECRET",
                "CLOUDFLARE_ANALYTICS_TOKEN", "ADSENSE_CLIENT", "ADSENSE_SLOT", "ADSENSE_ADS_TXT"):
        os.environ[key] = ""
    os.environ.setdefault("MAX_UPLOAD_MB", "500")
    # pdf2docx's worker pool re-launches the frozen exe per worker; serial is within
    # a few percent of it on typical documents (see scripts/pdf_utils.py) and safe.
    os.environ.setdefault("PDF2DOCX_MULTIPROCESSING", "0")
    os.environ.setdefault("LOG_LEVEL", "INFO")
    _add_libreoffice_to_path()


def _configure_logging(data: Path) -> None:
    # A windowed exe has no stdout/stderr, so log to a file instead.
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[logging.FileHandler(data / "forgefiles.log", encoding="utf-8")],
        force=True,
    )


def _run_server(port: int) -> int:
    """Child-process entry: serve the app until the parent window goes away."""
    import uvicorn

    root = _app_root()
    sys.path.insert(0, str(root))
    os.chdir(root)
    import main as app_main  # noqa: E402  (needs the env + sys.path set above)

    server = uvicorn.Server(
        uvicorn.Config(app_main.app, host=HOST, port=port, log_config=None, access_log=False)
    )

    def watch_parent() -> None:
        # The parent keeps our stdin open; EOF means it exited (even if it crashed).
        try:
            sys.stdin.buffer.read()
        finally:
            server.should_exit = True

    threading.Thread(target=watch_parent, daemon=True).start()
    server.run()
    return 0


def _spawn_server(port: int):
    import subprocess

    if getattr(sys, "frozen", False):
        cmd = [sys.executable, "--serve", str(port)]
    else:
        cmd = [sys.executable, str(Path(__file__).resolve()), "--serve", str(port)]
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return subprocess.Popen(
        cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=flags,
    )


def _wait_until_up(port: int, timeout: float = 60.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _is_forge_files(port):
            return True
        time.sleep(0.25)
    return False


SPLASH_HTML = """<!doctype html><meta charset=utf-8><title>Forge Files</title>
<body style="margin:0;height:100vh;display:grid;place-items:center;background:#16120e;
color:#f3ece3;font:16px system-ui,sans-serif"><div style="text-align:center">
<div style="font-size:28px;font-weight:600;letter-spacing:.02em">Forge Files</div>
<div style="margin-top:12px;opacity:.7">Starting up&hellip;</div></div></body>"""


def main() -> int:
    import webview

    data = _data_dir()
    _configure_logging(data)
    log = logging.getLogger("forgefiles.desktop")

    # Reuse a running instance rather than starting a second server.
    for candidate in PREFERRED_PORTS:
        if not _can_bind(candidate) and _is_forge_files(candidate):
            log.info("Existing instance on port %s; opening a window on it", candidate)
            return _run_window(webview, SPLASH_HTML, lambda w: w.load_url(f"http://{HOST}:{candidate}/"))

    port = next((p for p in PREFERRED_PORTS if _can_bind(p)), None) or _free_port()
    _configure_environment(data, port)
    state: dict = {}

    def boot(window) -> None:
        try:
            state["server"] = _spawn_server(port)
        except Exception:
            log.exception("Failed to start the server")
            window.load_html("<h2 style='font-family:sans-serif'>Forge Files could not start.</h2>"
                             f"<p style='font-family:sans-serif'>See {data / 'forgefiles.log'}</p>")
            return
        if _wait_until_up(port):
            window.load_url(f"http://{HOST}:{port}/")
        else:
            log.error("Server did not start within 60s")
            window.load_html("<h2 style='font-family:sans-serif'>Forge Files took too long to start.</h2>"
                             f"<p style='font-family:sans-serif'>See {data / 'forgefiles.log'}</p>")

    code = _run_window(webview, SPLASH_HTML, boot, storage=data / "webview")
    proc = state.get("server")
    if proc is not None:
        proc.terminate()
    return code


def _run_window(webview, html: str, on_ready, storage: Path | None = None) -> int:
    webview.settings["ALLOW_DOWNLOADS"] = True
    window = webview.create_window(
        "Forge Files", html=html, width=1280, height=860, min_size=(900, 640),
        js_api=DesktopApi(),
    )
    # storage_path persists cookies/localStorage between launches.
    webview.start(on_ready, window, private_mode=False,
                  storage_path=str(storage) if storage else None)
    return 0


if __name__ == "__main__":
    # Libraries such as pdf2docx start worker processes. In a frozen exe each worker
    # re-runs this file, so without this call every worker would open its own window.
    import multiprocessing

    multiprocessing.freeze_support()
    if len(sys.argv) == 3 and sys.argv[1] == "--serve":
        _configure_logging(_data_dir())
        sys.exit(_run_server(int(sys.argv[2])))
    sys.exit(main())
