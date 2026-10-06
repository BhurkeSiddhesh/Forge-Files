"""Deterministic PDF fixtures for the Phase 4 on-device conversion tests.

Built with PyMuPDF so the source text, styles, links, images, bookmarks and geometry are
known exactly; tests then check the converted output against what was put in.
"""

from __future__ import annotations

import io
from pathlib import Path

import pymupdf as fitz
from PIL import Image, ImageDraw

FONT_DIR = Path(__file__).resolve().parent.parent / "static" / "vendor" / "pdfjs" / "standard_fonts"


def _embed_liberation(page):
    """Embed Liberation Sans (Latin, Greek, Cyrillic) so non-ASCII text has unambiguous glyphs."""
    page.insert_font(fontname="LS", fontfile=str(FONT_DIR / "LiberationSans-Regular.ttf"))
    page.insert_font(fontname="LB", fontfile=str(FONT_DIR / "LiberationSans-Bold.ttf"))


LOREM = (
    "The committee reviewed the proposal in detail and concluded that the approach was sound, "
    "although several open questions about long term maintenance remained unanswered. "
    "Members asked for a clearer budget, a staged delivery plan and a named owner for each risk "
    "before the next quarterly review takes place."
)
LOREM2 = (
    "Subsequent analysis showed that adoption had grown steadily across every region, with the "
    "strongest gains appearing where training was offered early. Support requests fell by a third "
    "once the documentation was rewritten, which freed the team to focus on reliability work."
)


def _png(size=(240, 120), colour=(200, 40, 40)) -> bytes:
    im = Image.new("RGB", size, colour)
    d = ImageDraw.Draw(im)
    d.rectangle((10, 10, size[0] - 10, size[1] - 10), outline=(255, 255, 255), width=4)
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


def article_pdf() -> bytes:
    """Three pages: running header/footer, headings, wrapped paragraphs (one continues across
    a page break), bold/italic runs, a bullet list, a link, an image, bookmarks, metadata."""
    doc = fitz.open()
    box = fitz.Rect(72, 110, 523, 700)
    for n in range(1, 4):
        p = doc.new_page()
        p.insert_text((72, 40), "Annual Report", fontsize=9, fontname="helv")
        p.insert_text((290, 810), f"{n}", fontsize=9, fontname="helv")
    p1, p2, p3 = doc[0], doc[1], doc[2]

    # Page 1: title, intro, styled line, link line, list, image
    p1.insert_text((72, 90), "Chapter One: Beginnings", fontsize=22, fontname="hebo")
    p1.insert_textbox(fitz.Rect(72, 105, 523, 215), LOREM, fontsize=11, fontname="helv", align=0)
    p1.insert_text((72, 235), "This sentence has ", fontsize=11, fontname="helv")
    p1.insert_text((176, 235), "bold words", fontsize=11, fontname="hebo")
    p1.insert_text((240, 235), " and ", fontsize=11, fontname="helv")
    p1.insert_text((264, 235), "italic words", fontsize=11, fontname="heit")
    p1.insert_text((330, 235), " in it.", fontsize=11, fontname="helv")
    p1.insert_text((72, 260), "Read more at example.com today.", fontsize=11, fontname="helv")
    lx0 = 72 + fitz.get_text_length("Read more at ", fontname="helv", fontsize=11)
    lx1 = lx0 + fitz.get_text_length("example.com", fontname="helv", fontsize=11)
    p1.insert_link({"kind": fitz.LINK_URI, "from": fitz.Rect(lx0, 249, lx1, 264), "uri": "https://example.com/more"})
    p1.insert_text((72, 295), "• First bullet point", fontsize=11, fontname="helv")
    p1.insert_text((72, 312), "• Second bullet point", fontsize=11, fontname="helv")
    p1.insert_text((72, 329), "• Third bullet point", fontsize=11, fontname="helv")
    p1.insert_image(fitz.Rect(72, 350, 312, 470), stream=_png())
    # Last paragraph runs to the bottom margin and continues on page 2.
    p1.insert_textbox(fitz.Rect(72, 495, 523, 700),
                      "A sentence that deliberately starts near the foot of the page and keeps going "
                      "until it reaches the very last line of the page where the text must continue ",
                      fontsize=11, fontname="helv", align=0)

    # Page 2: continuation, heading, paragraph
    p2.insert_textbox(fitz.Rect(72, 90, 523, 130), "on the following page without any break in the sentence.",
                      fontsize=11, fontname="helv")
    p2.insert_text((72, 190), "Chapter Two: Growth", fontsize=22, fontname="hebo")
    p2.insert_textbox(fitz.Rect(72, 205, 523, 330), LOREM2, fontsize=11, fontname="helv", align=0)

    # Page 3: sub heading + paragraph
    p3.insert_text((72, 100), "Chapter Three: Outlook", fontsize=22, fontname="hebo")
    p3.insert_text((72, 140), "A short subsection", fontsize=14, fontname="hebo")
    p3.insert_textbox(fitz.Rect(72, 150, 523, 280), LOREM, fontsize=11, fontname="helv", align=0)

    doc.set_toc([[1, "Chapter One: Beginnings", 1], [1, "Chapter Two: Growth", 2], [1, "Chapter Three: Outlook", 3]])
    doc.set_metadata({"title": "Annual Report 2026", "author": "Forge Test"})
    return doc.tobytes()


def plain_pdf(paragraphs=None, title=None) -> bytes:
    """One page of wrapped paragraphs; no bookmarks, no headings unless `title`."""
    doc = fitz.open()
    p = doc.new_page()
    y = 80
    if title:
        p.insert_text((72, y), title, fontsize=20, fontname="hebo")
        y += 30
    for text in paragraphs or [LOREM, LOREM2]:
        p.insert_textbox(fitz.Rect(72, y, 523, y + 120), text, fontsize=11, fontname="helv")
        y += 130
    return doc.tobytes()


def two_column_pdf() -> bytes:
    """A full-width title over two real columns. Reading order must be left column first."""
    doc = fitz.open()
    p = doc.new_page()
    p.insert_text((72, 80), "Two Column Study", fontsize=22, fontname="hebo")
    left = " ".join(["LEFTCOL"] + [f"alpha{i} text about the first topic and its details." for i in range(14)])
    right = " ".join(["RIGHTCOL"] + [f"omega{i} text about the second topic and its details." for i in range(14)])
    p.insert_textbox(fitz.Rect(72, 110, 285, 700), left, fontsize=10, fontname="helv")
    p.insert_textbox(fitz.Rect(312, 110, 525, 700), right, fontsize=10, fontname="helv")
    return doc.tobytes()


def three_column_pdf() -> bytes:
    doc = fitz.open()
    p = doc.new_page()
    cols = [(60, 190), (215, 345), (370, 500)]
    for k, (a, b) in enumerate(cols):
        text = " ".join(f"col{k}word{i} sentence about the topic." for i in range(18))
        p.insert_textbox(fitz.Rect(a, 90, b, 700), text, fontsize=9, fontname="helv")
    return doc.tobytes()


def scanned_pdf() -> bytes:
    """Text rendered as a picture: no text layer, one big image."""
    src = fitz.open(stream=plain_pdf(), filetype="pdf")
    pix = src[0].get_pixmap(dpi=110)
    doc = fitz.open()
    p = doc.new_page()
    p.insert_image(p.rect, pixmap=pix)
    return doc.tobytes()


def blank_pdf(pages=2) -> bytes:
    doc = fitz.open()
    for _ in range(pages):
        doc.new_page()
    return doc.tobytes()


def encrypted_pdf(password="secret") -> bytes:
    doc = fitz.open(stream=plain_pdf(), filetype="pdf")
    return doc.tobytes(encryption=fitz.PDF_ENCRYPT_AES_256, user_pw=password, owner_pw=password)


def resume_pdf() -> bytes:
    """Single column CV: bold header, section heads, bullets, right aligned dates on the same line."""
    doc = fitz.open()
    p = doc.new_page()
    _embed_liberation(p)
    p.insert_text((72, 70), "Jane Q. Example", fontsize=24, fontname="LB")
    p.insert_text((72, 90), "Senior Engineer  |  jane@example.com  |  +1 555 0100", fontsize=10, fontname="LS")
    p.insert_text((72, 130), "EXPERIENCE", fontsize=13, fontname="LB")
    p.insert_text((72, 152), "Acme Corporation, Staff Engineer", fontsize=11, fontname="LB")
    p.insert_text((440, 152), "2021 – 2025", fontsize=11, fontname="LS")
    for i, t in enumerate(["Led a team of eight engineers on the billing platform",
                           "Cut p99 latency by 40% through caching and batching",
                           "Mentored four engineers into senior roles"]):
        p.insert_text((90, 172 + 17 * i), "•  " + t, fontsize=10.5, fontname="LS")
    p.insert_text((72, 250), "Globex, Engineer", fontsize=11, fontname="LB")
    p.insert_text((440, 250), "2017 – 2021", fontsize=11, fontname="LS")
    p.insert_text((72, 270), "Built the first mobile client and the public API.", fontsize=10.5, fontname="LS")
    return doc.tobytes()


def many_pages_pdf(pages=60) -> bytes:
    """Many text pages, for cancellation and progress tests."""
    doc = fitz.open()
    for n in range(pages):
        p = doc.new_page()
        p.insert_text((72, 80), f"Section {n + 1}", fontsize=18, fontname="hebo")
        p.insert_textbox(fitz.Rect(72, 100, 523, 700), (LOREM + " ") * 4, fontsize=11, fontname="helv")
    return doc.tobytes()


def hostile_link_pdf() -> bytes:
    """Links with schemes an ebook must never carry, next to one it may."""
    doc = fitz.open()
    p = doc.new_page()
    p.insert_text((72, 100), "Safe link here", fontsize=12, fontname="helv")
    p.insert_text((72, 130), "Script link here", fontsize=12, fontname="helv")
    p.insert_text((72, 160), "File link here", fontsize=12, fontname="helv")
    for y, uri in ((100, "https://example.org/ok"), (130, "javascript:alert(1)"), (160, "file:///etc/passwd")):
        p.insert_link({"kind": fitz.LINK_URI, "from": fitz.Rect(72, y - 12, 160, y + 4), "uri": uri})
    return doc.tobytes()


def unicode_pdf() -> bytes:
    """Accented Latin, Greek and Cyrillic in an embedded font."""
    doc = fitz.open()
    p = doc.new_page()
    _embed_liberation(p)
    p.insert_text((72, 100), "Café crème – naïve façade", fontsize=14, fontname="LS")
    p.insert_text((72, 130), "Привет мир Γειά σου", fontsize=14, fontname="LS")
    return doc.tobytes()


ROWS = [
    ("Item", "Qty", "Unit price", "Total"),
    ("Widget", "10", "19.99", "199.90"),
    ("Gadget", "5", "5.50", "27.50"),
    ("Doodad", "100", "0.99", "99.00"),
    ("Thingamajig", "1", "1250.00", "1250.00"),
]


def _table_text(page, x0, y0, widths, row_h, rows, ruled):
    """Write `rows` into a grid; text is left aligned in column 0 and right aligned elsewhere."""
    for r, row in enumerate(rows):
        x = x0
        for c, val in enumerate(row):
            font = "hebo" if r == 0 else "helv"
            y = y0 + r * row_h + row_h - 7
            if c == 0:
                page.insert_text((x + 4, y), val, fontsize=10, fontname=font)
            else:
                w = fitz.get_text_length(val, fontname=font, fontsize=10)
                page.insert_text((x + widths[c] - 4 - w, y), val, fontsize=10, fontname=font)
            x += widths[c]


def ruled_table_pdf(with_paragraphs=False) -> bytes:
    """A 5 x 4 table drawn with cell borders, optionally between two paragraphs."""
    doc = fitz.open()
    p = doc.new_page()
    y0 = 200 if with_paragraphs else 120
    if with_paragraphs:
        p.insert_textbox(fitz.Rect(72, 80, 523, 190), "Quarterly order summary. " + LOREM, fontsize=11, fontname="helv")
    widths = [150, 80, 100, 100]
    row_h = 24
    x = 72
    for w in widths:
        for r in range(len(ROWS)):
            p.draw_rect(fitz.Rect(x, y0 + r * row_h, x + w, y0 + (r + 1) * row_h), color=(0, 0, 0), width=0.8)
        x += w
    _table_text(p, 72, y0, widths, row_h, ROWS, True)
    if with_paragraphs:
        p.insert_textbox(fitz.Rect(72, y0 + 150, 523, y0 + 270), LOREM2, fontsize=11, fontname="helv")
    return doc.tobytes()


def borderless_table_pdf() -> bytes:
    """The same data with no lines at all: columns are only implied by alignment."""
    doc = fitz.open()
    p = doc.new_page()
    p.insert_text((72, 90), "Order lines", fontsize=16, fontname="hebo")
    _table_text(p, 72, 110, [150, 80, 100, 100], 22, ROWS, False)
    return doc.tobytes()


def merged_table_pdf() -> bytes:
    """A table whose header cell spans two columns (no rule between them in the header row)."""
    doc = fitz.open()
    p = doc.new_page()
    x = [72, 222, 322, 422]
    y = [120, 144, 168, 192, 216]
    for yy in y:
        p.draw_line((x[0], yy), (x[-1], yy), width=0.8)
    for i, xx in enumerate(x):
        top = y[1] if i == 2 else y[0]  # the rule between cols 2 and 3 starts below the header
        p.draw_line((xx, top), (xx, y[-1]), width=0.8)
    p.insert_text((76, 137), "Region", fontsize=10, fontname="hebo")
    p.insert_text((226, 137), "Sales (both quarters)", fontsize=10, fontname="hebo")
    for r, row in enumerate([("North", "10", "12"), ("South", "7", "9"), ("East", "3", "4")]):
        for c, v in enumerate(row):
            p.insert_text((x[c] + 4, y[r + 1] + 16), v, fontsize=10, fontname="helv")
    return doc.tobytes()


def narrow_block_pdf() -> bytes:
    """A full-width paragraph and a narrower block of wrapped text below it."""
    doc = fitz.open()
    p = doc.new_page()
    p.insert_textbox(fitz.Rect(72, 80, 523, 190), LOREM, fontsize=11, fontname="helv")
    p.insert_textbox(fitz.Rect(72, 210, 320, 330), LOREM2, fontsize=11, fontname="helv")
    return doc.tobytes()



def _ruled_grid(page, x0, y0, widths, row_h, rows, right_from=1, bold_header=True):
    """Draw a bordered grid of `rows` with text (left aligned first column, right aligned after)."""
    n = len(rows)
    x = x0
    for w in widths:
        for r in range(n):
            page.draw_rect(fitz.Rect(x, y0 + r * row_h, x + w, y0 + (r + 1) * row_h), color=(0, 0, 0), width=0.8)
        x += w
    for r, row in enumerate(rows):
        x = x0
        for c, val in enumerate(row):
            font = "hebo" if (r == 0 and bold_header) else "helv"
            y = y0 + r * row_h + row_h - 7
            if c < right_from:
                page.insert_text((x + 4, y), val, fontsize=10, fontname=font)
            else:
                page.insert_text((x + widths[c] - 4 - fitz.get_text_length(val, fontname=font, fontsize=10), y), val, fontsize=10, fontname=font)
            x += widths[c]


def two_tables_pdf() -> bytes:
    """Two separate ruled tables on one page, with a paragraph between them."""
    doc = fitz.open()
    p = doc.new_page()
    _ruled_grid(p, 72, 80, [150, 100, 100], 22, [("Region", "Q1", "Q2"), ("North", "10", "12"), ("South", "7", "9")])
    p.insert_textbox(fitz.Rect(72, 170, 523, 260), "Notes between the two tables. " + LOREM, fontsize=11, fontname="helv")
    _ruled_grid(p, 72, 280, [150, 100, 100], 22, [("Product", "Units", "Revenue"), ("Alpha", "100", "2,500.00"), ("Beta", "50", "1,125.50")])
    return doc.tobytes()


def table_on_page_two_pdf() -> bytes:
    """Page 1 is prose, page 2 holds the only table."""
    doc = fitz.open()
    p1 = doc.new_page()
    p1.insert_textbox(fitz.Rect(72, 80, 523, 200), LOREM, fontsize=11, fontname="helv")
    p2 = doc.new_page()
    _ruled_grid(p2, 72, 100, [150, 100, 100], 22, [("Name", "Score", "Rank"), ("Ann", "91", "1"), ("Bob", "85", "2"), ("Cy", "78", "3")])
    return doc.tobytes()


TYPED_ROWS = [
    ("Case", "Value"),
    ("Integer", "1250"),
    ("Thousands", "1,250.00"),
    ("Negative", "(500.00)"),
    ("Percent", "12%"),
    ("Leading zeros", "0012"),
    ("Account", "123456789012345"),
    ("Currency", "$1,250.50"),
    ("Words", "n/a"),
    ("Empty", ""),
]


def typed_table_pdf() -> bytes:
    """A ruled table whose second column holds numbers, identifiers and text."""
    doc = fitz.open()
    p = doc.new_page()
    _ruled_grid(p, 72, 80, [150, 150], 22, TYPED_ROWS)
    return doc.tobytes()


FORMULA_ROWS = [("Label", "Text"), ("one", "=1+1"), ("two", "+SUM(A1)"), ("three", "@cmd"), ("four", "-5 apples")]


def formula_table_pdf() -> bytes:
    """Cells whose text starts like a spreadsheet formula."""
    doc = fitz.open()
    p = doc.new_page()
    _ruled_grid(p, 72, 80, [150, 150], 22, FORMULA_ROWS, right_from=9)
    return doc.tobytes()



def mixed_sizes_pdf() -> bytes:
    """Three pages of different sizes: Letter portrait, A4 landscape, and a small card."""
    doc = fitz.open()
    for w, h, label in ((612, 792, "LETTER PORTRAIT"), (842, 595, "A4 LANDSCAPE"), (300, 200, "SMALL CARD")):
        p = doc.new_page(width=w, height=h)
        p.draw_rect(fitz.Rect(10, 10, w - 10, h - 10), color=(0.8, 0.1, 0.1), width=3)
        p.insert_text((30, 60), label, fontsize=18, fontname="hebo")
        p.insert_text((30, 90), f"{w} x {h} points", fontsize=11, fontname="helv")
    return doc.tobytes()


def rotated_page_pdf() -> bytes:
    """Page 1 is a normal page, page 2 carries /Rotate 90."""
    doc = fitz.open()
    p1 = doc.new_page()
    p1.insert_text((72, 100), "Upright page text", fontsize=14, fontname="helv")
    p2 = doc.new_page()
    p2.insert_text((72, 100), "Rotated page text", fontsize=14, fontname="helv")
    p2.set_rotation(90)
    return doc.tobytes()



OCR_HEADING = "Quarterly Report 2026"
OCR_PARAGRAPHS = [
    LOREM,
    "Invoice 10432 was issued on 12 March for a total of 1,250.00 and paid in full within thirty days.",
    LOREM2,
]


def ocr_truth(pages: int = 1) -> str:
    """The text the scan fixtures contain, in reading order (what OCR should recover)."""
    one = OCR_HEADING + "\n" + "\n".join(OCR_PARAGRAPHS)
    return "\n".join(one for _ in range(pages))


def ocr_source_pdf(pages: int = 1) -> bytes:
    """The text PDF the scan fixtures are rasterised from (ground truth for words and positions)."""
    src = fitz.open()
    for _ in range(pages):
        p = src.new_page()
        p.insert_text((72, 90), OCR_HEADING, fontsize=20, fontname="hebo")
        y = 120
        for text in OCR_PARAGRAPHS:
            p.insert_textbox(fitz.Rect(72, y, 523, y + 110), text, fontsize=11, fontname="helv")
            y += 100
    return src.tobytes()


def ocr_scan_pdf(dpi: int = 300, pages: int = 1, skew_deg: float = 0.0, noise: float = 0.0, blur: float = 0.0) -> bytes:
    """Image-only PDF made by rasterising known text, optionally skewed, noisy and blurred like a scan.
    Stored as JPEG, the way scanners store pages."""
    import numpy as np
    from PIL import ImageFilter

    src = fitz.open(stream=ocr_source_pdf(pages), filetype="pdf")
    out = fitz.open()
    for page in src:
        pix = page.get_pixmap(dpi=dpi)
        im = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        if skew_deg:
            im = im.rotate(skew_deg, resample=Image.BICUBIC, expand=False, fillcolor=(255, 255, 255))
        if blur:
            im = im.filter(ImageFilter.GaussianBlur(blur))
        if noise:
            arr = np.asarray(im).astype(float)
            arr += np.random.default_rng(7).normal(0, noise, arr.shape)
            im = Image.fromarray(np.clip(arr, 0, 255).astype("uint8"))
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=82)
        q = out.new_page(width=page.rect.width, height=page.rect.height)
        q.insert_image(q.rect, stream=buf.getvalue())
    return out.tobytes(garbage=3, deflate=True)


def mixed_text_and_scan_pdf() -> bytes:
    """Page 1 has a real text layer, page 2 is a scan of the same text."""
    native = fitz.open(stream=ocr_source_pdf(1), filetype="pdf")
    scan = fitz.open(stream=ocr_scan_pdf(), filetype="pdf")
    native.insert_pdf(scan)
    return native.tobytes()


def blank_scan_pdf() -> bytes:
    """An image-only page that is plain white: there is nothing to recognise."""
    out = fitz.open()
    q = out.new_page()
    buf = io.BytesIO()
    Image.new("RGB", (800, 1100), (255, 255, 255)).save(buf, "JPEG", quality=80)
    q.insert_image(q.rect, stream=buf.getvalue())
    return out.tobytes(garbage=3, deflate=True)


def ocr_long_scan_pdf(pages: int = 21) -> bytes:
    """More pages than the phone budget allows, each a low-resolution scan."""
    return ocr_scan_pdf(dpi=100, pages=pages)
