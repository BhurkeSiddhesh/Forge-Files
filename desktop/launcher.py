"""Forge Files for Windows: run the local FastAPI app inside a native window.

Everything runs on the user's machine. The server binds to 127.0.0.1 only,
writable state lives under %LOCALAPPDATA%\\ForgeFiles, and nothing is uploaded
anywhere. Run directly (``python desktop/launcher.py``) or frozen by PyInstaller
(see ``desktop/forgefiles.spec``).
"""
from __future__ import annotations

import base64
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
    os.environ["FF_LOCAL_ONLY"] = "1"  # not a default: a stray FF_LOCAL_ONLY=0 must not weaken the privacy guarantee
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


def _log_level() -> int:
    level = getattr(logging, os.environ.get("LOG_LEVEL", "INFO").strip().upper(), None)
    return level if isinstance(level, int) else logging.INFO


def _configure_logging(data: Path) -> None:
    # A windowed exe has no stdout/stderr, so log to a file instead.
    logging.basicConfig(
        level=_log_level(),
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


def _wait_until_up(port: int, timeout: float = 60.0, proc=None) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc is not None and proc.poll() is not None:
            logging.getLogger("forgefiles.desktop").error("Server process exited with code %s", proc.returncode)
            return False
        if _is_forge_files(port):
            return True
        time.sleep(0.25)
    return False


def _focus_existing_window() -> None:
    try:
        import ctypes

        user32 = ctypes.windll.user32
        hwnd = user32.FindWindowW(None, "Forge Files")
        if hwnd:
            user32.ShowWindow(hwnd, 9)  # SW_RESTORE
            user32.SetForegroundWindow(hwnd)
    except Exception:  # best effort only
        logging.getLogger("forgefiles.desktop").debug("Could not focus the window", exc_info=True)


def _splash_html() -> str:
    """Generate the startup splash HTML embedding the animated logo and progress bar."""
    logo_src = "/static/forge-files-logo-animation.gif"
    try:
        gif_file = _app_root() / "static" / "forge-files-logo-animation.gif"
        if gif_file.is_file():
            encoded = base64.b64encode(gif_file.read_bytes()).decode("ascii")
            logo_src = f"data:image/gif;base64,{encoded}"
    except Exception:
        pass

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Forge Files</title>
<style>
* {{ box-sizing: border-box; }}
html, body {{
    margin: 0;
    padding: 0;
    width: 100vw;
    height: 100vh;
    overflow: hidden;
    background: radial-gradient(circle at 50% 45%, #1e1b4b 0%, #0f172a 70%, #090d16 100%);
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    color: #f3ece3;
    display: flex;
    align-items: center;
    justify-content: center;
    user-select: none;
    -webkit-user-select: none;
}}
.startup-splash-content {{
    display: flex;
    flex-direction: column;
    align-items: center;
    text-align: center;
    padding: 1.5rem;
    animation: startupFadeIn 0.35s ease-out;
}}
@keyframes startupFadeIn {{
    from {{ opacity: 0; transform: scale(0.95); }}
    to {{ opacity: 1; transform: scale(1); }}
}}
.startup-splash-badge {{
    width: 112px;
    height: 112px;
    border-radius: 26px;
    box-shadow: 0 16px 40px -8px rgba(99, 102, 241, 0.5), 0 0 0 1.5px rgba(129, 140, 248, 0.35);
    overflow: hidden;
    margin-bottom: 1.25rem;
    background: rgba(15, 23, 42, 0.8);
    display: flex;
    align-items: center;
    justify-content: center;
}}
.startup-splash-gif {{
    width: 100%;
    height: 100%;
    object-fit: cover;
    display: block;
}}
.startup-splash-brand {{
    font-size: 1.85rem;
    font-weight: 700;
    color: #ffffff;
    letter-spacing: -0.03em;
    margin-bottom: 0.35rem;
}}
.startup-splash-brand .accent {{
    background: linear-gradient(135deg, #6366f1 0%, #a855f7 50%, #ec4899 100%);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
}}
.startup-splash-tagline {{
    font-size: 0.85rem;
    color: #94a3b8;
    font-weight: 500;
    letter-spacing: -0.01em;
    margin-bottom: 1.5rem;
}}
.startup-splash-loader {{
    width: 130px;
    height: 3px;
    background: rgba(255, 255, 255, 0.12);
    border-radius: 999px;
    overflow: hidden;
}}
.startup-splash-progress {{
    width: 100%;
    height: 100%;
    background: linear-gradient(90deg, #6366f1, #a855f7, #38bdf8);
    border-radius: 999px;
    animation: startupProgress 1.4s ease-in-out infinite;
}}
@keyframes startupProgress {{
    0% {{ transform: translateX(-100%); }}
    50% {{ transform: translateX(0); }}
    100% {{ transform: translateX(100%); }}
}}
</style>
</head>
<body>
<div class="startup-splash-content">
    <div class="startup-splash-badge">
        <img src="{logo_src}" alt="Forge Files" class="startup-splash-gif" width="112" height="112">
    </div>
    <div class="startup-splash-brand">Forge <span class="accent">Files</span></div>
    <div class="startup-splash-tagline">Fast, Private &amp; Open Document Engine</div>
    <div class="startup-splash-loader" aria-hidden="true"><div class="startup-splash-progress"></div></div>
</div>
</body>
</html>"""


def _error_html(title: str, message: str) -> str:
    return f"""<!doctype html><meta charset=utf-8><title>Forge Files</title>
<body style="margin:0;height:100vh;display:flex;align-items:center;justify-content:center;
background:radial-gradient(circle at 50% 45%, #1e1b4b 0%, #0f172a 70%, #090d16 100%);
color:#f3ece3;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;padding:1.5rem">
<div style="text-align:center;max-width:440px;background:rgba(15,23,42,0.85);padding:2rem;border-radius:18px;
border:1px solid rgba(239,68,68,0.3);box-shadow:0 16px 40px -8px rgba(0,0,0,0.5)">
<h2 style="margin:0 0 10px;font-size:1.4rem;color:#f87171">{title}</h2>
<p style="margin:0;font-size:0.95rem;color:#94a3b8;line-height:1.5">{message}</p>
</div></body>"""


SPLASH_HTML = _splash_html()


def main() -> int:
    import webview

    data = _data_dir()
    _configure_logging(data)
    log = logging.getLogger("forgefiles.desktop")

    # Reuse a running instance rather than starting a second server.
    for candidate in PREFERRED_PORTS:
        if not _can_bind(candidate) and _is_forge_files(candidate):
            # A second window would die with the first one's server, so bring the
            # existing window forward and exit instead.
            log.info("Already running on port %s; focusing the existing window", candidate)
            _focus_existing_window()
            return 0

    port = next((p for p in PREFERRED_PORTS if _can_bind(p)), None) or _free_port()
    _configure_environment(data, port)
    state: dict = {}

    def boot(window) -> None:
        try:
            state["server"] = _spawn_server(port)
        except Exception:
            log.exception("Failed to start the server")
            window.load_html(_error_html("Forge Files could not start", f"See {data / 'forgefiles.log'}"))
            return
        if _wait_until_up(port, proc=state["server"]):
            window.load_url(f"http://{HOST}:{port}/")
        else:
            log.error("Server did not start within 60s")
            window.load_html(_error_html("Forge Files took too long to start", f"See {data / 'forgefiles.log'}"))

    code = _run_window(webview, _splash_html(), boot, storage=data / "webview")
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
        try:
            code = _run_server(int(sys.argv[2]))
        except BaseException:  # the parent only sees "did not start"; leave the reason in the log
            logging.getLogger("forgefiles.server").exception("Server failed to start")
            code = 1
        sys.exit(code)
    sys.exit(main())
