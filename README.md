# Forge Files

**Forge Files** is a free, open-source, all-in-one web file toolbox. Upload a file, process it in seconds, and download the result — no signup, no watermarks, no software installation.

**Live App:** [https://forgefiles.org](https://forgefiles.org)

<!-- mirror-smoke-test: this line only verifies the private->public mirror workflow -->

> **Privacy by design — and verifiable.** Your upload is deleted as soon as processing finishes, the result is deleted the moment you download it, and a background sweeper purges anything older than an hour. Unlike closed-source tools that just *claim* they don't keep your files, every line of code that handles your documents is in this repository. Don't trust us? Read the code — or self-host it.

![CodeRabbit Pull Request Reviews](https://img.shields.io/coderabbit/prs/github/BhurkeSiddhesh/Forge-Files?utm_source=oss&utm_medium=github&utm_campaign=BhurkeSiddhesh%2FForge-Files&labelColor=171717&color=FF570A&link=https%3A%2F%2Fcoderabbit.ai&label=CodeRabbit+Reviews)

---

## Features

### PDF Tools
| Feature | Description |
|---|---|
| **Remove Password** | Unlock password-protected PDF files instantly |
| **Compress PDF** | Reduce file size with three quality presets (low / medium / high) |
| **Merge PDF** | Combine multiple PDFs into one, with per-file password support |
| **Split PDF** | Extract specific pages or page ranges into a new file |
| **Rotate PDF** | Rotate all pages or a specific range by 90 / 180 / 270° |
| **Protect PDF** | Add a password and fine-grained permissions (print, copy, edit) |
| **Watermark PDF** | Stamp diagonal or positional text watermarks onto every page |
| **Sign PDF** | Place a signature image (PNG/JPEG) at a precise position on any page |
| **Organize PDF** | Reorder, delete, or duplicate pages via a custom page order |
| **Add Page Numbers** | Insert decimal, Roman, or alphabetic page numbers at any position |
| **Convert to Word (Standard)** | Fast PDF → DOCX conversion using `pdf2docx` |
| **Convert to Word (AI Layout Recovery)** | High-fidelity conversion via the configured OCR backend — PaddleOCR (x86) preserves tables/columns; RapidOCR (ARM64-compatible) reconstructs text in reading order |
| **PDF to Images** | Render every page to JPEG or PNG and download as a ZIP |
| **PDF to Excel** | Extract tables from a PDF into an XLSX workbook |
| **PDF to PowerPoint** | Convert PDF pages into a PPTX presentation (one slide per page) |
| **PDF to EPUB** | Convert a PDF into a reflowable EPUB ebook |
| **PDF to Text** | Extract all text content to a plain `.txt` file, with optional layout preservation |
| **Repair PDF** | Attempt to recover and re-save a corrupted or malformed PDF |
| **Create PDF from Text** | Generate a new PDF from plain text content |
| **Create Blank PDF** | Create an empty PDF with a specified number of pages |
| **Annotate PDF** | Add highlights, underlines, strikethroughs, notes, text overlays, or redactions |
| **Edit PDF Metadata** | Read or write title, author, subject, keywords, and creator fields |

### Image Tools
| Feature | Description |
|---|---|
| **HEIC → JPEG** | Convert Apple HEIC/HEIF photos to universally compatible JPEG |
| **Resize Image** | Resize by exact dimensions, percentage, or target file size (KB) |
| **Crop Image** | Crop images using a visual drag-and-drop cropper (powered by Cropper.js) |
| **Rotate Image** | Rotate by any angle with quality control |
| **Compress Image** | Reduce image file size by adjusting JPEG/WebP quality |
| **Convert Image** | Convert between JPEG, PNG, WebP, BMP, TIFF, and GIF |
| **Watermark Image** | Stamp text at a corner, center, or tiled position with opacity and color control |
| **Images to PDF** | Pack one or more images into a single PDF (configurable page size and fit mode) |

### Excel & Spreadsheet Tools
| Feature | Description |
|---|---|
| **Excel to PDF** | Convert XLS/XLSX spreadsheets to PDF |
| **CSV to XLSX** | Import a CSV file into an Excel workbook, with configurable delimiter |
| **XLSX to CSV** | Export an Excel sheet to a flat CSV file |
| **Merge Excel** | Combine multiple XLSX workbooks into a single file |

### PowerPoint Tools
| Feature | Description |
|---|---|
| **PowerPoint to PDF** | Convert PPT/PPTX presentations to PDF |
| **PPT to Images** | Render each slide to PNG or JPEG and download as a ZIP |
| **Merge PowerPoint** | Combine multiple PPTX files into one presentation |

### Word Tools
| Feature | Description |
|---|---|
| **Word to PDF** | Convert DOCX/DOC documents to PDF |

### Workflow Builder
Chain multiple operations into a single pipeline with real-time progress streaming via Server-Sent Events (SSE). Example: unlock a PDF, then convert it to Word — all in one step.

---

## Tech Stack

- **Backend**: Python 3.10+, [FastAPI](https://fastapi.tiangolo.com/), Uvicorn
- **PDF Processing**: `pikepdf`, `pdf2docx`, `PyMuPDF`
- **OCR / AI Layout**: `RapidOCR` (default, ONNX Runtime, ARM64-compatible) with optional `PaddleOCR` PPStructure backend (x86 only)
- **Image Processing**: `Pillow`, `pillow-heif`
- **Excel / PPT / Word**: `openpyxl`, `python-pptx`, `reportlab`
- **Frontend**: Vanilla HTML/CSS/JS, [Cropper.js](https://fengyuanchen.github.io/cropperjs/) (lazy-loaded)
- **Deployment**: [Render](https://render.com/), Docker

---

## Project Structure

```
Forge-Files/
├── main.py                  # FastAPI app — all API and SEO routes
├── requirements.txt         # Python dependencies (RapidOCR default backend)
├── requirements-ai-paddle.txt # Optional PaddleOCR backend (x86 only)
├── Dockerfile               # Container build config
├── render.yaml              # One-click Render deployment config
├── SEO.md                   # SEO architecture docs (tool pages, AI crawlers, AdSense)
├── scripts/
│   ├── pdf_utils.py         # All PDF processing logic
│   ├── ocr_engine.py        # OCR backend abstraction (rapidocr / paddle / none)
│   ├── image_utils.py       # Image conversion, resize, crop, watermark, etc.
│   ├── excel_utils.py       # Excel/CSV processing
│   ├── ppt_utils.py         # PowerPoint processing
│   ├── word_utils.py        # Word-to-PDF conversion
│   ├── seo_content.py       # Server-rendered SEO landing pages (32 tools)
│   ├── utils.py             # Shared helpers
│   ├── security_utils.py    # Input sanitization utilities
│   └── fix_models.py        # PaddleOCR ONNX model setup script
├── static/
│   ├── index.html           # Single-page frontend application
│   ├── script.js            # All frontend logic
│   ├── style.css            # Styles
│   └── pages/              # Static content pages (about, faq, contact, privacy, terms)
├── models/                  # Vendored ONNX models for the optional Paddle backend
├── uploads/                 # Temporary upload staging (auto-cleared)
├── outputs/                 # Processed files (auto-deleted after download)
└── tests/
    ├── conftest.py          # Fixtures (mock PDFs, test client setup)
    ├── test_main.py         # Integration tests for all API endpoints
    ├── test_auth.py         # SEO routes, 404 handling, AI crawler, AdSense tests
    ├── test_pdf_utils.py    # Unit tests for PDF processing logic
    ├── test_image_utils.py  # Unit tests for image processing logic
    └── ...                  # Additional benchmarks and edge-case tests
```

---

## API Reference

Forge Files uses FastAPI, which automatically generates an interactive OpenAPI documentation.

Once the server is running, visit **http://127.0.0.1:8001/docs** to explore all endpoints, test them interactively, and view detailed request/response schemas.

---

## Getting Started (Local Development)

### Prerequisites

- Python 3.10+
- pip

### Installation

```bash
git clone https://github.com/BhurkeSiddhesh/Forge-Files.git
cd Forge-Files
pip install -r requirements.txt
```

### Run the Server

```bash
python main.py
```

The app will be available at [http://127.0.0.1:8001](http://127.0.0.1:8001).

### Environment Variables

| Variable | Description | Default |
|---|---|---|
| `PORT` | Port the server listens on | `8001` |
| `BASE_URL` | Public URL used in canonical links, sitemap.xml, robots.txt and llms.txt | `https://www.forgefiles.org` |
| `MAX_UPLOAD_MB` | Maximum upload size in MB | `50` |
| `RATE_LIMIT_PER_MIN` | Max processing requests per IP per minute | `20` |
| `FILE_TTL_SECONDS` | Stale uploads/outputs older than this are auto-purged | `3600` |
| `DISABLE_AI` | Set to `1` to disable AI Layout Recovery and OCR (for low-memory hosts); overrides `OCR_BACKEND` | `0` |
| `OCR_BACKEND` | OCR engine: `rapidocr` (ARM64-compatible), `paddle` (x86 only, best layout recovery — needs `requirements-ai-paddle.txt`), or `none` | `rapidocr` |
| `WARMUP_AI` | Set to `1` to pre-load the configured OCR backend at startup (fails fast if the engine or its models are broken) | `0` |
| `ADSENSE_ADS_TXT` | If set, served verbatim at `/ads.txt` (for AdSense) | _(none)_ |
| `ADSENSE_CLIENT` | AdSense publisher ID (e.g. `ca-pub-…`). When set, ads load asynchronously in CLS-safe reserved containers. Unset = no ad markup at all. | _(none)_ |
| `ADSENSE_SLOT` | Optional numeric `data-ad-slot` for the in-content ad unit | _(none)_ |
| `GOOGLE_SITE_VERIFICATION` | Google Search Console verification token (just the token, not the full meta tag). When set, a `google-site-verification` meta tag is added to every page. | _(none)_ |
| `BING_SITE_VERIFICATION` | Bing Webmaster Tools verification token (`msvalidate.01`). When set, the verification meta tag is added to every page. | _(none)_ |

> **Setting `BASE_URL` in production:** If you deploy to a custom domain (e.g. `https://forgefiles.org`), set `BASE_URL=https://forgefiles.org` as an environment variable on your host. Canonical links, `sitemap.xml`, and `robots.txt` all read this value — without it they default to the Render subdomain.

---

## Running Tests

This project uses `pytest`. Run the full test suite with:

```bash
DISABLE_AI=1 python -m pytest
```

Key test files:
- [`tests/conftest.py`](tests/conftest.py) — fixtures including auto-generated mock PDFs
- [`tests/test_main.py`](tests/test_main.py) — integration tests for all API endpoints
- [`tests/test_auth.py`](tests/test_auth.py) — SEO routes, 404 handling, AI crawler config, AdSense gate
- [`tests/test_pdf_utils.py`](tests/test_pdf_utils.py) — unit tests for PDF processing logic
- [`tests/test_image_utils.py`](tests/test_image_utils.py) — unit tests for image processing logic

Tests create and clean up all temporary files automatically.

---

## Deployment

### Deploy to Render (Recommended)

1. **Fork** this repository and log in to [Render](https://render.com/).
2. **Connect** your GitHub repository.
3. Render will auto-detect [`render.yaml`](render.yaml) and configure the service.
4. Click **Deploy**.

> [!IMPORTANT]
> The included [`render.yaml`](render.yaml) targets the **Free tier** (zero cost) with `DISABLE_AI=1`: every tool works except the optional **AI Layout Recovery** mode of PDF→Word, which needs more than 1 GB of RAM. To enable AI mode, switch to the **Starter** plan and remove the `DISABLE_AI` env var (optionally set `WARMUP_AI=1`).

### AI Layout Recovery Mode

AI features run on a pluggable OCR backend, selected by the `OCR_BACKEND` env var (see [`scripts/ocr_engine.py`](scripts/ocr_engine.py)):

| Backend | Platforms | PDF→Word (AI mode) | PDF→Text OCR fallback |
|---|---|---|---|
| `rapidocr` (default) | x86_64 **and** ARM64 (e.g. Oracle Cloud A1/Ampere) | Text reconstructed in reading order (no tables/columns) | ✅ |
| `paddle` | x86_64 only | Full layout recovery — tables, columns, figures | ✅ |
| `none` / `DISABLE_AI=1` | any | Disabled (clear error; standard converter still works) | Skipped |

RapidOCR runs the same PP-OCR model family via ONNX Runtime, so plain-text OCR accuracy is equivalent to Paddle — only PPStructure's table/column layout recovery is x86-exclusive.

```bash
# ARM64 or x86 AI image (RapidOCR, default) — warm-up verifies the engine at build time
docker build --build-arg WARMUP_AI=1 -t file-forge .

# x86 best-quality AI image (PaddleOCR PPStructure)
docker build --build-arg OCR_BACKEND=paddle --build-arg WARMUP_AI=1 -t file-forge-paddle .

# Lightweight no-AI image (pair with DISABLE_AI=1 at runtime)
docker build --build-arg OCR_BACKEND=none -t file-forge-lite .
```

The **Paddle backend** (PDF→Word via PPStructure) uses four ONNX models under `models/`:

| Model | Path in `models/` | Purpose |
|---|---|---|
| Detection | `det/en/en_PP-OCRv3_det_infer/` | Text region detection |
| Recognition | `rec/en/en_PP-OCRv3_rec_infer/` | Text recognition (OCR) |
| Layout | `layout/picodet_lcnet_x1_0_fgd_layout_infer/` | Page layout analysis |
| Table | `table/en_ppstructure_mobile_v2.0_SLANet_inference/` | Table structure recovery |

The model binaries are not tracked in git. Paddle Docker builds run
`python scripts/fix_models.py --output-dir models` to download, safely extract,
convert, and validate them before the image is published. For a non-Docker
Paddle setup, run the same command once during installation. Network access is
required only while provisioning; both Paddle and RapidOCR work fully offline
at runtime, so document conversion remains private.

To enable AI mode on Render:
1. Upgrade your service to **Starter** in the Render dashboard (RapidOCR is much lighter than Paddle, but 512 MB free-tier RAM is still tight for OCR on real documents).
2. Remove (or set to `0`) the `DISABLE_AI` environment variable — the default `rapidocr` backend activates.
3. Optionally set `WARMUP_AI=1` to pre-load the engine at startup (eliminates first-request latency).
4. Set `BASE_URL=https://forgefiles.org` (or your custom domain).
5. Redeploy.

### Deploy with Docker

```bash
docker build -t file-forge .
docker run -p 8001:8001 file-forge
```

### GitHub Pages

GitHub Pages is **not supported** — it only serves static files and cannot run the Python backend. Use Render, Railway, Fly.io, or any platform that supports Docker or Python WSGI/ASGI apps.

---

## License

This project is dual-licensed:

1. **Open Source (AGPLv3)**: The code is fully open source under the **GNU Affero General Public License v3.0**. Anyone can use it for free, *provided* they also open source their own project/modifications under the same AGPLv3 license. See the [LICENSE](LICENSE) file for details.
2. **Commercial License**: For companies or individuals who wish to use the software in a commercial, closed-source product or service without being bound by the AGPLv3 open-source requirements, a commercial license must be purchased. See [COMMERCIAL_LICENSE.md](COMMERCIAL_LICENSE.md) for details.
