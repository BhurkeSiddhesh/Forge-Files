import importlib
import sys
from pathlib import Path

import pytest


def test_pdf_utils_import_without_optional_dependencies(monkeypatch):
    """Module import should succeed even if heavy optional deps are unavailable."""
    real_import = __import__

    def guarded_import(name, *args, **kwargs):
        if name in {"cv2", "pdf2docx", "fitz", "docxcompose", "docx"}:
            raise ImportError(f"simulated missing optional dependency: {name}")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", guarded_import)
    sys.modules.pop("scripts.pdf_utils", None)

    try:
        module = importlib.import_module("scripts.pdf_utils")
        assert hasattr(module, "remove_pdf_password")
        assert hasattr(module, "pdf_to_docx")
    finally:
        sys.modules.pop("scripts.pdf_utils", None)


class TestPdf2docxMultiprocessing:
    """pdf2docx's worker pool is a win on a big box and a loss on a small one.
    Production is an Always-Free Oracle VM (1-2 cores), where /admin/stats
    measured pdf_to_word_standard at a 2622s p95 with multiprocessing on."""

    def _module(self):
        return importlib.import_module("scripts.pdf_utils")

    def test_multiprocessing_off_on_small_boxes(self, monkeypatch):
        module = self._module()
        monkeypatch.delenv("PDF2DOCX_MULTIPROCESSING", raising=False)
        monkeypatch.setattr(module, "_available_cores", lambda: 2)
        assert module._use_multiprocessing() is False

    def test_multiprocessing_on_when_cores_are_available(self, monkeypatch):
        module = self._module()
        monkeypatch.delenv("PDF2DOCX_MULTIPROCESSING", raising=False)
        monkeypatch.setattr(module, "_available_cores", lambda: 8)
        assert module._use_multiprocessing() is True

    def test_core_count_uses_affinity_not_host_cpus(self, monkeypatch):
        """A cgroup-limited VM is the production case. os.cpu_count() reports the
        host's CPUs there — measured 4 while the process was pinned to 1 — which
        would turn the worker pool on exactly where it doesn't pay."""
        module = self._module()
        monkeypatch.setattr(module.os, "cpu_count", lambda: 32)
        monkeypatch.setattr(module.os, "sched_getaffinity", lambda pid: {0}, raising=False)
        assert module._available_cores() == 1

    def test_core_count_falls_back_where_affinity_is_unavailable(self, monkeypatch):
        """sched_getaffinity is Linux-only; Windows/macOS must still work."""
        module = self._module()
        monkeypatch.delattr(module.os, "sched_getaffinity", raising=False)
        monkeypatch.setattr(module.os, "cpu_count", lambda: 8)
        assert module._available_cores() == 8

    def test_unknown_cpu_count_is_treated_as_single_core(self, monkeypatch):
        """os.cpu_count() can return None. Default to the safe (serial) path."""
        module = self._module()
        monkeypatch.delenv("PDF2DOCX_MULTIPROCESSING", raising=False)
        monkeypatch.delattr(module.os, "sched_getaffinity", raising=False)
        monkeypatch.setattr(module.os, "cpu_count", lambda: None)
        assert module._available_cores() == 1
        assert module._use_multiprocessing() is False

    @pytest.mark.parametrize("value,expected", [
        ("1", True), ("true", True), ("YES", True), ("on", True),
        ("0", False), ("false", False), ("no", False),
    ])
    def test_env_var_overrides_the_heuristic(self, monkeypatch, value, expected):
        module = self._module()
        monkeypatch.setattr(module, "_available_cores", lambda: 1)
        monkeypatch.setenv("PDF2DOCX_MULTIPROCESSING", value)
        assert module._use_multiprocessing() is expected

    def test_blank_env_var_falls_back_to_the_heuristic(self, monkeypatch):
        module = self._module()
        monkeypatch.setattr(module, "_available_cores", lambda: 8)
        monkeypatch.setenv("PDF2DOCX_MULTIPROCESSING", "  ")
        assert module._use_multiprocessing() is True


class TestPdf2docxRetryIsNarrow:
    """The serial retry costs a second full conversion, so it must only fire for
    the fork/spawn failures it was written for. A missing libGL.so.1 behind
    pdf2docx's cv2 import used to double an already-slow run before failing."""

    @pytest.fixture(autouse=True)
    def _fresh_fork_state(self, monkeypatch):
        """_multiprocessing_broken is process-wide and sticky by design: any earlier test in the same
        xdist worker that hit a real fork failure leaves it True, and these tests assume a first
        attempt with multiprocessing on."""
        monkeypatch.setattr(importlib.import_module("scripts.pdf_utils"), "_multiprocessing_broken", False)

    def _run_with_converter(self, monkeypatch, converter_cls):
        module = importlib.import_module("scripts.pdf_utils")
        fake_pdf2docx = type(sys)("pdf2docx")
        fake_pdf2docx.Converter = converter_cls
        monkeypatch.setitem(sys.modules, "pdf2docx", fake_pdf2docx)
        monkeypatch.setattr(module, "_use_multiprocessing", lambda: True)
        module._convert_pdf2docx("in.pdf", Path("out.docx"))

    def test_import_error_is_not_retried(self, monkeypatch):
        calls = []

        class Converter:
            def __init__(self, path):
                pass

            def convert(self, out, multi_processing=False):
                calls.append(multi_processing)
                raise ImportError("libGL.so.1: cannot open shared object file")

            def close(self):
                pass

        with pytest.raises(ImportError):
            self._run_with_converter(monkeypatch, Converter)
        assert calls == [True], "an ImportError must surface on the first attempt"

    def test_fork_failure_still_retries_serially(self, monkeypatch):
        calls = []

        class Converter:
            def __init__(self, path):
                pass

            def convert(self, out, multi_processing=False):
                calls.append(multi_processing)
                if multi_processing:
                    raise OSError("cannot fork")

            def close(self):
                pass

        self._run_with_converter(monkeypatch, Converter)
        assert calls == [True, False], "fork failures should fall back to serial"

    def test_serial_failure_is_not_retried(self, monkeypatch):
        """With multiprocessing already off there is no fallback left to try."""
        calls = []

        class Converter:
            def __init__(self, path):
                pass

            def convert(self, out, multi_processing=False):
                calls.append(multi_processing)
                raise OSError("boom")

            def close(self):
                pass

        module = importlib.import_module("scripts.pdf_utils")
        fake_pdf2docx = type(sys)("pdf2docx")
        fake_pdf2docx.Converter = Converter
        monkeypatch.setitem(sys.modules, "pdf2docx", fake_pdf2docx)
        monkeypatch.setattr(module, "_use_multiprocessing", lambda: False)

        with pytest.raises(OSError):
            module._convert_pdf2docx("in.pdf", Path("out.docx"))
        assert calls == [False]

    def test_converter_is_closed_even_when_the_retry_fails(self, monkeypatch):
        """Regression guard for the existing finally block: pdf2docx holds the
        source PDF open via PyMuPDF, so a leak here blocks cleanup."""
        closed = []

        class Converter:
            def __init__(self, path):
                pass

            def convert(self, out, multi_processing=False):
                raise ImportError("libGL.so.1")

            def close(self):
                closed.append(True)

        with pytest.raises(ImportError):
            self._run_with_converter(monkeypatch, Converter)
        assert closed == [True]


def test_resolve_models_dir_prefers_repo_models(monkeypatch):
    module = importlib.import_module("scripts.pdf_utils")
    fake_root = Path("/tmp/project") if Path("/tmp").exists() else Path("C:/tmp/project")
    fake_script = fake_root / "scripts" / "pdf_utils.py"
    monkeypatch.setattr(module, "__file__", str(fake_script))

    resolved = module._resolve_models_dir()
    assert Path(resolved).resolve() == (fake_root / "models").resolve()
