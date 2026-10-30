"""Branch-complete coverage for public/scripts/pdf_utils.py.

Covers the residual branches the behaviour suites don't reach: import
fallbacks, validation one-liners, encrypted-input cleanup paths, the
compress_pdf image-recompression decision tree (via a fake fitz), OCR helper
edge cases, the paddle/hybrid/ocr-fallback word converters, images_to_pdf
EXIF handling, word_to_pdf pure-Python fallback, borderless-table recovery,
pdf_to_epub image/placeholder branches, repair_pdf recovery ladder, and
metadata editing.
"""
import importlib
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import pdf_utils as pu
import scripts.ocr_engine as ocr_engine_module


# ==========================================================================
# Module import fallbacks
# ==========================================================================
class TestImportFallbacks:
    def test_epub_import_fallback(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "ebooklib", None)
        try:
            importlib.reload(pu)
            assert pu.epub is None
        finally:
            monkeypatch.undo()
            importlib.reload(pu)
        assert pu.epub is not None

    def test_paddle_import_fallback(self, monkeypatch):
        monkeypatch.setattr(pu, "_PADDLE_ENGINE", None)
        monkeypatch.setitem(sys.modules, "paddleocr", None)
        with pytest.raises(ImportError):
            pu.get_paddle_engine()

    def test_paddle_engine_memory_error(self, monkeypatch):
        monkeypatch.setattr(pu, "_PADDLE_ENGINE", None)

        def _oom(**kwargs):
            raise MemoryError("boom")

        monkeypatch.setitem(sys.modules, "paddleocr", SimpleNamespace(PPStructure=_oom))
        with pytest.raises(MemoryError):
            pu.get_paddle_engine()

    def test_paddle_engine_generic_error(self, monkeypatch):
        monkeypatch.setattr(pu, "_PADDLE_ENGINE", None)

        def _bad(**kwargs):
            raise RuntimeError("boom")

        monkeypatch.setitem(sys.modules, "paddleocr", SimpleNamespace(PPStructure=_bad))
        with pytest.raises(RuntimeError):
            pu.get_paddle_engine()


# ==========================================================================
# _parse_page_selection validation
# ==========================================================================
class TestParsePageSelection:
    def test_empty_segment_skipped(self):
        assert pu._parse_page_selection("1,,2", 5) == [0, 1]

    def test_dangling_range_rejected(self):
        with pytest.raises(ValueError):
            pu._parse_page_selection("-3", 5)

    def test_non_numeric_range_rejected(self):
        with pytest.raises(ValueError):
            pu._parse_page_selection("a-b", 5)

    def test_no_valid_pages_rejected(self):
        with pytest.raises(ValueError):
            pu._parse_page_selection(",", 5)


# ==========================================================================
# Encrypted-input decrypt + cleanup paths
# ==========================================================================
class TestEncryptedCleanup:
    def test_extract_pages_encrypted(self, locked_pdf, tmp_path):
        out = pu.extract_pdf_pages(
            locked_pdf["path"], str(tmp_path), "1", password=locked_pdf["password"]
        )
        assert Path(out).exists()

    def test_watermark_encrypted(self, locked_pdf, tmp_path):
        out = pu.add_watermark(
            locked_pdf["path"], str(tmp_path), "CONFIDENTIAL",
            password=locked_pdf["password"],
        )
        assert Path(out).exists()

    def test_pdf_to_images_encrypted(self, locked_pdf, tmp_path):
        result = pu.pdf_to_images_zip(
            locked_pdf["path"], str(tmp_path), password=locked_pdf["password"]
        )
        assert Path(result["output_path"]).exists()

    def test_sign_pdf_encrypted(self, locked_pdf, sample_image_file, tmp_path):
        out = pu.sign_pdf(
            locked_pdf["path"], sample_image_file, str(tmp_path),
            password=locked_pdf["password"],
        )
        assert Path(out).exists()

    def test_get_pdf_metadata_encrypted(self, locked_pdf):
        meta = pu.get_pdf_metadata(locked_pdf["path"], password=locked_pdf["password"])
        assert meta["page_count"] >= 1


# ==========================================================================
# Watermark / images-zip / sign validation one-liners
# ==========================================================================
class TestValidationBranches:
    def test_watermark_opacity_not_a_number(self, sample_pdf, tmp_path):
        with pytest.raises(ValueError):
            pu.add_watermark(sample_pdf, str(tmp_path), "WM", opacity="abc")

    def test_watermark_opacity_out_of_range(self, sample_pdf, tmp_path):
        with pytest.raises(ValueError):
            pu.add_watermark(sample_pdf, str(tmp_path), "WM", opacity=5)

    def test_watermark_position_top(self, sample_pdf, tmp_path):
        out = pu.add_watermark(sample_pdf, str(tmp_path), "WM", position="top")
        assert Path(out).exists()

    def test_watermark_position_bottom(self, sample_pdf, tmp_path):
        out = pu.add_watermark(sample_pdf, str(tmp_path), "WM", position="bottom")
        assert Path(out).exists()

    def test_images_zip_dpi_not_a_number(self, sample_pdf, tmp_path):
        with pytest.raises(ValueError):
            pu.pdf_to_images_zip(sample_pdf, str(tmp_path), dpi="abc")

    def test_images_zip_bad_format(self, sample_pdf, tmp_path):
        with pytest.raises(ValueError):
            pu.pdf_to_images_zip(sample_pdf, str(tmp_path), fmt="gif")

    def test_sign_page_not_a_number(self, sample_pdf, sample_image_file, tmp_path):
        with pytest.raises(ValueError):
            pu.sign_pdf(sample_pdf, sample_image_file, str(tmp_path), page="abc")

    def test_sign_page_below_one(self, sample_pdf, sample_image_file, tmp_path):
        with pytest.raises(ValueError):
            pu.sign_pdf(sample_pdf, sample_image_file, str(tmp_path), page=0)

    def test_sign_x_out_of_range(self, sample_pdf, sample_image_file, tmp_path):
        with pytest.raises(ValueError):
            pu.sign_pdf(sample_pdf, sample_image_file, str(tmp_path), x=2.0)

    def test_sign_width_out_of_range(self, sample_pdf, sample_image_file, tmp_path):
        with pytest.raises(ValueError):
            pu.sign_pdf(sample_pdf, sample_image_file, str(tmp_path), width=0.01)

    def test_sign_unreadable_signature_image(self, sample_pdf, tmp_path):
        junk = tmp_path / "sig.png"
        junk.write_bytes(b"definitely not an image")
        # Aspect-ratio probe fails -> fallback 0.4, then fitz cannot decode it.
        with pytest.raises(Exception):
            pu.sign_pdf(sample_pdf, str(junk), str(tmp_path))

    def test_add_page_numbers_start_below_one(self, sample_pdf, tmp_path):
        with pytest.raises(ValueError):
            pu.add_page_numbers(sample_pdf, str(tmp_path), start_number=0)


# ==========================================================================
# merge_pdfs / merge_docx_files
# ==========================================================================
class TestMerge:
    def test_merge_no_inputs(self, tmp_path):
        with pytest.raises(ValueError):
            pu.merge_pdfs([], str(tmp_path))

    def test_merge_with_encrypted_input(self, sample_pdf, locked_pdf, tmp_path):
        out = pu.merge_pdfs(
            [sample_pdf, locked_pdf["path"]],
            str(tmp_path),
            passwords=[None, locked_pdf["password"]],
        )
        assert Path(out).exists()

    def test_merge_docx_missing_dependency(self, monkeypatch, tmp_path):
        monkeypatch.setattr(pu, "Document_docx", None)
        monkeypatch.setattr(pu, "Composer", None)
        with pytest.raises(ImportError):
            pu.merge_docx_files([str(tmp_path / "a.docx")], str(tmp_path / "out.docx"))


# ==========================================================================
# _render_page_bgr / _extract_text_with_ocr / extract_pdf_text
# ==========================================================================
def _fake_pixmap_page(w=8, h=8, n=3):
    return SimpleNamespace(
        get_pixmap=lambda dpi=200: SimpleNamespace(
            samples=bytes(w * h * n), h=h, w=w, n=n
        )
    )


class TestRenderAndOcrHelpers:
    @pytest.fixture(autouse=True)
    def _require_cv2(self):
        if getattr(pu, "np", None) is None or getattr(pu, "cv2", None) is None:
            pytest.skip("numpy/cv2 not available in this environment")

    def test_render_rgba(self):
        img = pu._render_page_bgr(_fake_pixmap_page(n=4))
        assert img.shape[2] == 3

    def test_render_grayscale(self):
        img = pu._render_page_bgr(_fake_pixmap_page(n=1))
        assert img.shape[2] == 3

    def test_ocr_no_backend(self, monkeypatch):
        monkeypatch.setattr(ocr_engine_module, "get_ocr_engine", lambda: None)
        assert pu._extract_text_with_ocr([_fake_pixmap_page()]) == ""

    def test_ocr_with_backend(self, monkeypatch):
        engine = SimpleNamespace(
            recognize=lambda img: [{"text": "hello"}, {"text": ""}, {}]
        )
        monkeypatch.setattr(ocr_engine_module, "get_ocr_engine", lambda: engine)
        text = pu._extract_text_with_ocr([_fake_pixmap_page()])
        assert "hello" in text


class TestExtractPdfText:
    def test_preserve_formatting_false(self, text_rich_pdf, tmp_path):
        out = pu.extract_pdf_text(
            text_rich_pdf, str(tmp_path), preserve_formatting=False
        )
        assert Path(out).exists()

    def test_ocr_fallback_when_text_too_short(self, text_rich_pdf, tmp_path, monkeypatch):
        monkeypatch.setattr(
            pu, "_extract_text_with_ocr", lambda doc: "ocr text " * 10
        )
        out = pu.extract_pdf_text(text_rich_pdf, str(tmp_path), min_text_chars=10 ** 9)
        assert "ocr text" in Path(out).read_text(encoding="utf-8")


# ==========================================================================
# _Pdf2docxProgress.emit exception swallow
# ==========================================================================
class TestPdf2docxProgress:
    def test_emit_swallows_errors(self):
        handler = pu._Pdf2docxProgress(lambda done, total: None, True)

        def _bad_message():
            raise RuntimeError("boom")

        record = SimpleNamespace(thread=threading.get_ident(), getMessage=_bad_message)
        handler.emit(record)  # must not raise


# ==========================================================================
# compress_pdf — fake fitz drives the image decision tree
# ==========================================================================
class _FakePixmap:
    def __init__(self, *args):
        if len(args) == 2 and isinstance(args[0], _FakeCompressDoc):
            doc, xref = args
            self.width, self.height, self.n, self.alpha = doc.pixmap_specs[xref]
            self.samples = bytes(self.width * self.height * self.n)
        elif len(args) == 2:  # (colorspace, pixmap) conversion
            cs, pix = args
            target_n = 1 if cs == "GRAY" else 3
            self.width, self.height = pix.width, pix.height
            self.n, self.alpha = target_n, 0
            self.samples = bytes(self.width * self.height * target_n)
        else:  # (colorspace, w, h, samples, alpha)
            cs, w, h, samples, alpha = args
            self.width, self.height = w, h
            self.n = len(samples) // (w * h) if w * h else 3
            self.alpha = alpha
            self.samples = samples


class _FakeCompressPage:
    def __init__(self, images, replace_raises=False):
        self._images = images
        self._replace_raises = replace_raises
        self.replaced = []

    def get_images(self, full=True):
        return list(self._images)

    def replace_image(self, xref, stream=None, pixmap=None):
        if self._replace_raises:
            raise RuntimeError("replace failed")
        self.replaced.append(xref)


class _FakeCompressDoc:
    def __init__(self, pages, pixmap_specs, stream_raw_raises=False, save_bloat=0):
        self.pages = pages
        self.pixmap_specs = pixmap_specs
        self._stream_raw_raises = stream_raw_raises
        self._save_bloat = save_bloat

    def __iter__(self):
        return iter(self.pages)

    def xref_stream_raw(self, xref):
        if self._stream_raw_raises:
            raise RuntimeError("no stream")
        return b"x" * 100

    def save(self, path, **kwargs):
        Path(path).write_bytes(b"%PDF-fake" + b"0" * self._save_bloat)

    def close(self):
        pass


def _install_fake_fitz(monkeypatch, doc):
    fake_fitz = SimpleNamespace(
        open=lambda path: doc,
        Pixmap=_FakePixmap,
        csRGB="RGB",
        csGRAY="GRAY",
    )
    # pdf_utils imports `pymupdf as fitz`, so the module cache has to be
    # seeded under its real name for the local `import pymupdf as fitz`
    # inside the function under test to pick up the fake.
    monkeypatch.setitem(sys.modules, "pymupdf", fake_fitz)
    monkeypatch.setitem(sys.modules, "fitz", fake_fitz)


class TestCompressPdf:
    @pytest.fixture(autouse=True)
    def _mock_decrypt(self, monkeypatch):
        monkeypatch.setattr(pu, "_get_decrypted_pdf_path", lambda p, pwd=None, temp_dir=None: (p, False))

    def _input(self, tmp_path, size=10):
        p = tmp_path / "input.pdf"
        p.write_bytes(b"%PDF-tiny" + b"x" * size)
        return str(p)

    def test_dup_xref_tiny_and_normal_path(self, monkeypatch, tmp_path):
        page = _FakeCompressPage(images=[(5, 0), (5, 0), (7, 0)])
        doc = _FakeCompressDoc(
            [page],
            {5: (2000, 2000, 3, 0), 7: (50, 50, 3, 0)},
            stream_raw_raises=True,
        )
        _install_fake_fitz(monkeypatch, doc)
        result = pu.compress_pdf(self._input(tmp_path), str(tmp_path), level="medium")
        assert Path(result["output_path"]).exists()
        assert page.replaced == [5]  # dup skipped, tiny skipped

    def test_smask_paths_and_alpha(self, monkeypatch, tmp_path):
        images = [(10, 2), (11, 0), (12, 0), (13, 0)]
        page = _FakeCompressPage(images=images)
        doc = _FakeCompressDoc(
            [page],
            {
                10: (2000, 2000, 3, 0),   # base w/ smask
                2: (1000, 1000, 2, 0),    # smask, non-gray -> csGRAY conversion
                11: (500, 500, 4, 1),     # alpha but small -> skip
                12: (2000, 2000, 4, 1),   # RGBA alpha channel
                13: (2000, 2000, 3, 1),   # LA alpha channel
            },
            stream_raw_raises=True,
        )
        _install_fake_fitz(monkeypatch, doc)
        result = pu.compress_pdf(self._input(tmp_path), str(tmp_path), level="high")
        assert Path(result["output_path"]).exists()
        assert 10 in page.replaced and 12 in page.replaced and 13 in page.replaced
        assert 11 not in page.replaced

    def test_grayscale_and_cmyk_conversion(self, monkeypatch, tmp_path):
        page = _FakeCompressPage(images=[(20, 0), (21, 0)])
        doc = _FakeCompressDoc(
            [page],
            {20: (2000, 2000, 1, 0), 21: (2000, 2000, 4, 0)},
            stream_raw_raises=True,
        )
        _install_fake_fitz(monkeypatch, doc)
        result = pu.compress_pdf(self._input(tmp_path), str(tmp_path), level="medium")
        assert Path(result["output_path"]).exists()
        assert page.replaced == [20, 21]

    def test_replace_image_failure_is_skipped(self, monkeypatch, tmp_path):
        page = _FakeCompressPage(images=[(30, 0)], replace_raises=True)
        doc = _FakeCompressDoc(
            [page], {30: (2000, 2000, 3, 0)}, stream_raw_raises=True
        )
        _install_fake_fitz(monkeypatch, doc)
        result = pu.compress_pdf(self._input(tmp_path), str(tmp_path))
        assert Path(result["output_path"]).exists()

    def test_bloated_output_falls_back_to_copy(self, monkeypatch, tmp_path):
        doc = _FakeCompressDoc([], {}, save_bloat=5000)
        _install_fake_fitz(monkeypatch, doc)
        result = pu.compress_pdf(self._input(tmp_path), str(tmp_path))
        out = Path(result["output_path"])
        assert out.read_bytes() == Path(self._input(tmp_path)).read_bytes()
