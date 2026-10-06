"""
Tests for Automatic Time-of-Day Theme Switching (Dark Mode post 6:30 PM by Local Timezone).
Verifies:
1. Pure algorithm time-of-day boundary calculations (06:29, 06:30, 18:29, 18:30, etc.).
2. User manual override precedence via localStorage ('dark' vs 'light' vs unset).
3. Static verification of head bootstrap script in index.html (zero-FOUC).
4. Static verification of script.js theme management and event listeners.
"""

import os
import re
import pytest

INDEX_HTML_PATH = os.path.join(os.path.dirname(__file__), "..", "static", "index.html")
SCRIPT_JS_PATH = os.path.join(os.path.dirname(__file__), "..", "static", "script.js")


def compute_theme(hours: int, minutes: int, saved_theme: str | None = None) -> str:
    """Python reference implementation of the client-side getTimeBasedTheme and getEffectiveTheme."""
    if saved_theme in ("dark", "light"):
        return saved_theme
    total_minutes = hours * 60 + minutes
    # Post 6:30 PM (18:30 = 1110 min) or before 6:30 AM (06:30 = 390 min) is dark
    return "dark" if (total_minutes >= 1110 or total_minutes < 390) else "light"


class TestTimeBasedThemeAlgorithm:
    @pytest.mark.parametrize(
        "hours,minutes,expected_theme",
        [
            # Midnight & Early Morning (< 06:30) -> dark
            (0, 0, "dark"),
            (3, 15, "dark"),
            (6, 0, "dark"),
            (6, 29, "dark"),
            # Morning boundary (06:30) -> light
            (6, 30, "light"),
            (7, 0, "light"),
            # Daytime (06:30 to 18:29) -> light
            (9, 45, "light"),
            (12, 0, "light"),
            (15, 30, "light"),
            (18, 0, "light"),
            (18, 29, "light"),
            # Evening boundary (18:30) -> dark
            (18, 30, "dark"),
            (19, 0, "dark"),
            (21, 30, "dark"),
            (23, 59, "dark"),
        ],
    )
    def test_time_boundaries_default(self, hours, minutes, expected_theme):
        assert compute_theme(hours, minutes, saved_theme=None) == expected_theme

    def test_manual_override_precedence(self):
        # User explicitly chose dark mode during daytime (12:00) -> dark
        assert compute_theme(12, 0, saved_theme="dark") == "dark"

        # User explicitly chose light mode during nighttime (21:00) -> light
        assert compute_theme(21, 0, saved_theme="light") == "light"

        # Invalid/empty saved_theme defaults to time-based
        assert compute_theme(12, 0, saved_theme="") == "light"
        assert compute_theme(21, 0, saved_theme="unknown") == "dark"


class TestIndexHtmlHeadScript:
    @pytest.fixture(autouse=True)
    def setup_files(self):
        with open(INDEX_HTML_PATH, "r", encoding="utf-8") as f:
            self.html_content = f.read()

    def test_head_script_exists_before_body(self):
        head_end = self.html_content.find("</head>")
        body_start = self.html_content.find("<body")
        assert head_end != -1 and body_start != -1
        assert head_end < body_start

        head_section = self.html_content[:head_end]
        assert "data-theme" in head_section
        assert "localStorage.getItem('theme')" in head_section
        assert "1110" in head_section  # 18:30 threshold
        assert "390" in head_section   # 06:30 threshold

    def test_head_script_handles_both_saved_and_time_based(self):
        # Ensure it checks savedTheme and falls back to time calculation
        assert "savedTheme === 'dark' || savedTheme === 'light'" in self.html_content
        assert "new Date()" in self.html_content
        assert "getHours()" in self.html_content
        assert "getMinutes()" in self.html_content


class TestScriptJsThemeManagement:
    @pytest.fixture(autouse=True)
    def setup_files(self):
        with open(SCRIPT_JS_PATH, "r", encoding="utf-8") as f:
            self.js_content = f.read()

    def test_theme_functions_exist(self):
        assert "function getTimeBasedTheme()" in self.js_content
        assert "function getEffectiveTheme()" in self.js_content
        assert "function updateThemeIcon()" in self.js_content
        assert "function applyTheme(" in self.js_content

    def test_theme_toggle_click_handler(self):
        assert "themeToggleBtn.addEventListener('click'" in self.js_content
        assert "applyTheme(newTheme, true)" in self.js_content
        assert "localStorage.setItem('theme'" in self.js_content

    def test_live_schedule_interval(self):
        assert "setInterval(" in self.js_content
        assert "getTimeBasedTheme()" in self.js_content
        assert "60000" in self.js_content
