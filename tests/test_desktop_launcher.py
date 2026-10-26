from __future__ import annotations

import sys
from pathlib import Path

# Ensure public/ is on sys.path
PUBLIC_ROOT = Path(__file__).resolve().parent.parent
if str(PUBLIC_ROOT) not in sys.path:
    sys.path.insert(0, str(PUBLIC_ROOT))

from desktop.launcher import _error_html, _splash_html, SPLASH_HTML


def test_splash_html_structure():
    html = _splash_html()
    assert "<!doctype html>" in html.lower()
    assert "Forge Files" in html
    assert "startup-splash-badge" in html
    assert "startup-splash-brand" in html
    assert "Fast, Private &amp; Open Document Engine" in html
    assert "startup-splash-loader" in html
    assert "startup-splash-progress" in html
    assert "radial-gradient" in html
    assert "startupProgress" in html


def test_splash_html_embeds_logo_gif():
    html = _splash_html()
    # When forge-files-logo-animation.gif exists, it embeds the data URI
    gif_file = PUBLIC_ROOT / "static" / "forge-files-logo-animation.gif"
    if gif_file.is_file():
        assert "data:image/gif;base64," in html


def test_splash_html_constant_matches_function():
    assert len(SPLASH_HTML) > 1000
    assert "startup-splash-badge" in SPLASH_HTML


def test_error_html_formatting():
    err = _error_html("Launch Failed", "Could not bind to port")
    assert "Launch Failed" in err
    assert "Could not bind to port" in err
    assert "radial-gradient" in err


def test_error_html_retry_button_is_opt_in():
    assert "pywebview.api.retry()" in _error_html("t", "m", retry=True)
    assert "Try again" not in _error_html("t", "m")


def test_desktop_api_retry_runs_callback():
    import threading

    from desktop.launcher import DesktopApi

    api = DesktopApi()
    assert api.retry() is False
    done = threading.Event()
    api.on_retry = done.set
    assert api.retry() is True
    assert done.wait(2)


def test_single_instance_mutex_detects_second_holder():
    import sys

    import pytest

    if sys.platform != "win32":
        pytest.skip("named mutex is Windows only")
    from desktop.launcher import _acquire_single_instance

    first, first_running = _acquire_single_instance()
    second, second_running = _acquire_single_instance()
    assert first_running is False
    assert second_running is True
    assert first and second


def test_startup_timeout_covers_slow_cold_starts():
    from desktop.launcher import STARTUP_TIMEOUT

    assert STARTUP_TIMEOUT >= 180
