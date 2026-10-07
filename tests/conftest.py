import pytest
from pathlib import Path
from reportlab.pdfgen import canvas
import pikepdf

@pytest.fixture(scope="session")
def sample_pdf(tmp_path_factory):
    """Creates a simple PDF file for testing."""
    # Create a temporary directory for the session
    d = tmp_path_factory.mktemp("data")
    file_path = d / "sample.pdf"

    # Generate PDF using ReportLab
    c = canvas.Canvas(str(file_path))
    c.drawString(100, 750, "Hello, this is a test PDF.")
    c.save()

    return file_path

@pytest.fixture(scope="session")
def multi_page_pdf(tmp_path_factory):
    """Creates a multi-page PDF file for extraction tests."""
    d = tmp_path_factory.mktemp("multi_data")
    file_path = d / "multi_sample.pdf"

    c = canvas.Canvas(str(file_path))
    for i in range(1, 5):
        c.drawString(100, 750, f"Page {i}")
        c.showPage()
    c.save()

    return file_path

@pytest.fixture(scope="session")
def text_rich_pdf(tmp_path_factory):
    """Creates a PDF with enough real text per page to pass the
    text-layer-detection heuristic in pdf_to_word_ai. Includes an "AI" token
    and multi-word phrases so tests can catch spacing/character
    regressions in whichever conversion path handles it."""
    d = tmp_path_factory.mktemp("text_rich")
    file_path = d / "resume.pdf"

    c = canvas.Canvas(str(file_path))
    lines = [
        "Jordan Rivera",
        "Senior Analytics Consultant",
        "Experienced in leading AI driven transformation programs across finance and retail.",
        "- Led a cross functional team of ten analysts delivering forecasting models.",
        "- Partnered with engineering to ship an AI powered recommendation engine.",
        "- Presented quarterly insights to executive leadership and the board.",
    ]
    y = 750
    for line in lines:
        c.drawString(72, y, line)
        y -= 20
    c.save()

    return file_path


@pytest.fixture(scope="session")
def scanned_like_pdf(tmp_path_factory):
    """Creates a PDF with an embedded image and no embedded text, simulating
    a scanned page (should still be routed to OCR)."""
    from PIL import Image

    d = tmp_path_factory.mktemp("scanned_like")
    img_path = d / "page.png"
    Image.new("RGB", (200, 200), color="white").save(img_path)

    file_path = d / "scanned.pdf"
    c = canvas.Canvas(str(file_path))
    c.drawImage(str(img_path), 0, 0, width=200, height=200)
    c.showPage()
    c.save()

    return file_path


@pytest.fixture(scope="session")
def locked_pdf(tmp_path_factory, sample_pdf):
    """Creates a password-protected PDF file."""
    d = tmp_path_factory.mktemp("locked_data")
    file_path = d / "locked.pdf"
    password = "secret_password"  # ggignore

    with pikepdf.open(sample_pdf) as pdf:
        # Encrypt the PDF
        pdf.save(
            file_path,
            encryption=pikepdf.Encryption(
                user=password,
                owner=password
            )
        )

    return {"path": file_path, "password": password}


@pytest.fixture(scope="session")
def real_resume_pdf():
    """A real, densely-formatted 2-page PDF (bold/italic runs, bullet lists,
    right-aligned date columns, hyperlinks) checked in under tests/fixtures/,
    unlike every other fixture above which is generated at test time. Exists
    to catch layout/reading-order/hyperlink regressions that simple
    reportlab-drawn PDFs can't surface. Intentionally never mutated in
    place — tests needing to modify it should copy into tmp_path first."""
    return Path(__file__).parent / "fixtures" / "sample_resume.pdf"


@pytest.fixture(scope="session")
def sample_heic(tmp_path_factory):
    """Creates a sample HEIC file for testing."""
    try:
        import pillow_heif
        from PIL import Image
        
        pillow_heif.register_heif_opener()
        
        d = tmp_path_factory.mktemp("images")
        file_path = d / "test_image.heic"
        
        # Create a simple RGB image and save as HEIC
        img = Image.new('RGB', (100, 100), color='blue')
        img.save(file_path, format='HEIF')
        
        return file_path
    except Exception as e:
        pytest.skip(f"Could not create test HEIC: {e}")

@pytest.fixture(scope="session")
def sample_image_file(tmp_path_factory):
    """Creates a sample JPEG image for testing."""
    d = tmp_path_factory.mktemp("img_gen")
    file_path = d / "test.jpg"
    from PIL import Image
    img = Image.new('RGB', (100, 100), color='green')
    img.save(file_path, "JPEG")
    return file_path

# Client fixture
import main as main_module
from main import app
from fastapi.testclient import TestClient

@pytest.fixture(autouse=True)
def close_event_log_connection():
    """Drop the event log's shared write connection between tests.

    It is keyed by EVENT_DB_PATH and most tests point that at their own temp
    DB, so a handle left open would otherwise outlive the tmp_path it belongs
    to (and, on Windows, keep the file locked).
    """
    from scripts import event_log as _event_log

    yield
    _event_log.close_connections()


@pytest.fixture(autouse=True)
def disable_rate_limit():
    """Disable rate limiting for tests (dedicated tests re-enable it)."""
    previous = app.state.rate_limit_enabled
    app.state.rate_limit_enabled = False
    yield
    app.state.rate_limit_enabled = previous
    app.state.rate_limiter.reset()

@pytest.fixture
def auth_client():
    """Returns a TestClient. (Auth was removed — the app is fully public;
    the fixture keeps its old name to avoid churn across the test files.)"""
    return TestClient(app, base_url="https://testserver")


def result_path(output_dir, payload):
    """Where a finished result lives on disk, given an API response body.

    Results are written to "<outputs>/<download_token>/<branded name>": each one
    gets its own directory named by an unguessable token, so two people
    converting "report.pdf" at the same time can't collide and the branded name
    on its own addresses nothing. See main.new_result_dir / main.download_fields.
    """
    return Path(output_dir) / payload["download_token"] / payload["filename"]


# These modules read repo-root files (public/static, mobile/, .gitignore,
# .github/workflows/ci.yml) that exist only in the private monorepo layout.
# The public mirror flattens public/ to its root, so they cannot run there;
# the private CI still runs them in full.
_PRIVATE_LAYOUT_ONLY = {
    "test_local_excel_interop.py",
    "test_local_workflow_interop.py",
    "test_office_engine_static.py",
}


def pytest_collection_modifyitems(config, items):
    private_root = Path(__file__).resolve().parent.parent.parent
    if (private_root / "public" / "static").is_dir():
        return
    skip = pytest.mark.skip(reason="requires the private monorepo layout (not the public mirror)")
    for item in items:
        if Path(str(item.fspath)).name in _PRIVATE_LAYOUT_ONLY:
            item.add_marker(skip)
