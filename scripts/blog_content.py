"""Server-rendered guides with practical instructions and original examples.

Mirrors the pattern in ``seo_content.py`` (same <head>, canonical, funnel beacon,
consent banner, JSON-LD) but for editorial how-to guides rather than tool pages.
Each guide explains a task, its tradeoffs and how to check the actual result,
with links to the relevant tools and reproducible practice files.

Each guide is fully server-rendered HTML (no JS needed) so JS-less crawlers and
AI bots read the whole article from the raw response. main.py substitutes the
{{BASE_URL}}/{{ADSENSE_*}}/{{CONSENT_BANNER}}/{{SITE_VERIFICATION}}/{{CF_ANALYTICS}}/
{{GA_ANALYTICS}} tokens at request time, exactly as for tool pages.

To add a guide: append an entry to GUIDES. `primary_tool` must be a real slug in
seo_content.TOOL_PAGES; `date` is an ISO date used for Article dateModified and
should be bumped when the content meaningfully changes.
"""
from __future__ import annotations

from typing import Dict, List, Tuple

from scripts import seo_content as sc
from scripts.content_examples import render_example
from scripts.seo_content import (
    ASSET_V, SITE, GITHUB, BASE, ADS_HEAD, ADS_SLOT, CONSENT_BANNER,
    DS_HEAD, DS_NAV, DS_FOOT, DS_THEME_JS, ds_breadcrumb, ds_faq,
    SITE_VERIFY, CF_ANALYTICS, GA_ANALYTICS, DATAFAST_ANALYTICS, FUNNEL_BEACON, TOOL_PAGES,
    _attr, _plain, _jsonld,
)

BLOG_BASE = BASE + "/blog"

# --- guide data ------------------------------------------------------------
# Each guide: title/meta (head), h1/dek (hero), body (list of (h2, html_body)),
# faqs (list of (q, a)), primary_tool (slug -> the tool this guide funnels to),
# related (extra tool slugs to cross-link), date (ISO, for dateModified).
GUIDES: Dict[str, dict] = {
    "how-to-compress-a-pdf-without-losing-quality": {
        "title": "Compress a PDF: Size, Readability and Real Examples | " + SITE,
        "meta": "Compare real compression outputs, distinguish selectable text from scans, and choose a smaller PDF that remains readable.",
        "h1": "Compress a PDF without sacrificing the details you need",
        "dek": "A smaller attachment is useful only if the recipient can still read it. Here is a practical way to choose a compression level, with an original scan you can test yourself.",
        "primary_tool": "compress-pdf", "related": ["extract-pdf-pages", "merge-pdf"],
        "date": "2026-09-26", "published": "2026-07-20", "example": "compression",
        "body": [
            ("First, find out what kind of PDF you have", "<p>Open the document and try selecting a sentence. A digital PDF often has selectable text and separate images. A scan usually holds a picture of the whole page, including its words. Some PDFs contain both kinds of pages. This distinction matters: reducing image detail can soften scanned words, even when selectable text stays sharp.</p><p>Inspect a page with the smallest print, not only the cover. A 20-page text report may already be compact, while a single scanned page can contain a large image. A file's page count alone does not predict how much it will shrink.</p>"),
            ("Choose a level and check the result", "<ol><li>Keep the original. Open <a href=\"/compress-pdf\">Compress PDF</a>, choose your file and start with Low.</li><li>Download the output and compare its actual byte size with the destination's limit.</li><li>Inspect small text, signatures, fine lines and colored labels at 100% and 200% zoom.</li><li>If the result is still too large, try Medium from the original, then High only if the important details remain readable.</li></ol><p>Do not repeatedly compress the last output: a fresh attempt from the original avoids stacking lossy conversions. There is no universal safe setting for every scan.</p>"),
            ("What the levels actually change", "<p>In the current server utility, Low targets eligible images up to a 2,200-pixel longest side with JPEG quality 80; Medium uses 1,600 and 60; High uses 1,000 and 40. Small images and transparency take different paths, and re-encoding is skipped when it does not save space. These are implementation settings, not a promise about the final file size or visual quality.</p><p>A 1,000-pixel page image spread across a full sheet has fewer pixels available for each letter than a high-resolution scan. You may not notice that in a thumbnail, which is why downloading and zooming matters.</p>"),
            ("If the upload portal still rejects it", "<p>Check whether the portal specifies bytes, KB or MB and whether it also requires a particular format or page count. Keep a small margin below the stated size limit. If only a few pages are needed, use <a href=\"/extract-pdf-pages\">Extract Pages</a> before compression. Do not remove required evidence just to pass a size check.</p><p>When a readable document cannot meet the limit, ask whether the recipient accepts separate parts or a file link. Keep the full-resolution original for archival use and printing.</p>"),
        ],
        "faqs": [("Why did the PDF barely shrink?", "It may already be optimized or contain mostly efficient text. File size is not a quality score; compare the actual output before deciding whether compression helped."),
                 ("Is this lossless compression?", "Structural optimization can be lossless, but the image compression levels can discard detail. In scanned PDFs, that detail includes the words themselves.")],
    },
    "how-to-convert-pdf-to-word-for-free": {
        "title": "PDF to Word: Check Text, Tables and Layout | " + SITE,
        "meta": "Convert a PDF to editable Word, compare an original report and its real DOCX output, and troubleshoot scans, tables and page breaks.",
        "h1": "Convert a PDF to Word, then check what changed",
        "dek": "A DOCX extension does not guarantee a faithful, editable document. Use this workflow to choose the right conversion path and inspect the parts most likely to change.",
        "primary_tool": "pdf-to-word", "related": ["ocr-pdf", "pdf-to-text", "unlock-pdf"],
        "date": "2026-09-26", "published": "2026-07-20", "example": "word",
        "body": [
            ("Start with a selection test", "<p>Open the PDF and select a complete sentence. If you can copy sensible words into a text editor, standard PDF-to-Word conversion has text to work with. If you can only select the page as a picture, it is probably a scan. A PDF can mix digital text and scanned pages, so check more than one page.</p><p>If you still have the original Word document, edit that instead. PDF conversion reconstructs structure from positioned text; it cannot recover the author's exact styles, tracked changes or editing history.</p>"),
            ("Convert a digital document", "<ol><li>Open <a href=\"/pdf-to-word\">PDF to Word</a> and choose the PDF.</li><li>If it requires an opening password, use <a href=\"/unlock-pdf\">Unlock PDF</a> with the correct password first.</li><li>Use standard conversion for selectable text. Download the DOCX after processing completes.</li><li>Open the DOCX in your editor and change a word and a table cell to confirm editability.</li></ol><p>The practice files below let you do this without uploading a private document. They are deliberately simple; their results should not be treated as a benchmark for contracts, complex reports or handwritten scans.</p>"),
            ("If the document is a scan", "<p>Recognition must happen before image text can become editable. The AI Layout Recovery option depends on the OCR engine and models available on the server. If it is disabled or unavailable, standard conversion will not magically recognize the page image. An image retained inside a DOCX is not editable text.</p><p>For search rather than editing, <a href=\"/ocr-pdf\">OCR PDF</a> may be the better output: it retains the page image and adds recognized text behind it. Neither path guarantees correct spelling, numbers or reading order.</p>"),
            ("A five-minute comparison before sharing", "<ul><li><strong>Tables:</strong> compare quantities, decimal points and which header each cell belongs to.</li><li><strong>Reading order:</strong> read across a page with columns or a sidebar.</li><li><strong>Page breaks:</strong> check the final line of each page and the next heading.</li><li><strong>Characters:</strong> inspect accents, currency symbols and mathematical notation.</li><li><strong>Headers and footers:</strong> look for repeated text that became part of a paragraph.</li></ul><p>Fix the DOCX, then export a fresh PDF from your editor if you need a stable final layout. Retain the original alongside the corrected version until the recipient has accepted it.</p>"),
        ],
        "faqs": [("Why did the layout move?", "A PDF describes page positions rather than Word paragraphs and styles. Reconstruction, missing fonts and different page settings can change wrapping and breaks."),
                 ("Do I need Word if I only want the words?", "No. <a href=\"/pdf-to-text\">PDF to Text</a> produces a TXT file. It does not preserve images or editable table structure.")],
    },
    "how-to-convert-heic-to-jpg": {
        "title": "HEIC to JPG: Compatibility, Size and Metadata | " + SITE,
        "meta": "Convert a HEIC still image to JPG, try an original downloadable example, and check color, orientation, size and metadata.",
        "h1": "Convert HEIC to JPG for an app that cannot open it",
        "dek": "Convert the format once, check the result, and keep your original. Changing the filename extension is not a conversion.",
        "primary_tool": "heic-to-jpeg", "related": ["resize-image", "image-to-pdf", "convert-image"],
        "date": "2026-09-26", "published": "2026-07-20", "example": "heic",
        "body": [
            ("Check what the destination actually accepts", "<p>HEIC is commonly used for still images in Apple's high-efficiency workflow. Some applications and upload forms instead require JPEG, usually named .jpg or .jpeg. Those two extensions name the same format. Read the form's size and dimension requirements before converting: format, pixels and bytes are separate checks.</p><p>If the destination already accepts HEIC, conversion may be unnecessary. Keep the original because JPEG compression can discard information and cannot preserve every feature of the source container.</p>"),
            ("Convert and inspect", "<ol><li>Open <a href=\"/heic-to-jpeg\">HEIC to JPG</a> and choose the original file.</li><li>Start with JPEG quality 95 for a first comparison.</li><li>Download the JPG and open it in the app that rejected the HEIC.</li><li>Check orientation, dimensions, color and fine detail. Compare bytes with the upload limit.</li></ol><p>If it is still too large, use <a href=\"/resize-image\">Resize Image</a> to fit the required pixel dimensions rather than repeatedly saving at lower quality. A JPG can be larger than its HEIC source even when both look similar.</p>"),
            ("What this conversion does not preserve", "<p>Forge Files converts a supported still image. It does not export a Live Photo's video component, reproduce every HDR display behavior or promise identical appearance across screens. The synthetic example below tests basic decoding and output dimensions, not iPhone camera fidelity.</p><p>The server converter attempts to preserve embedded ICC color information and EXIF metadata while applying orientation to the pixels. Metadata may include camera details or location, depending on the source. Do not use format conversion as a substitute for inspecting and removing metadata before publishing a sensitive image.</p>"),
            ("Alternatives on Apple devices", "<p>On a Mac, Apple documents exporting a HEIF image from Photos or Preview to JPEG or PNG. On supported iPhones and iPads, Camera's Formats setting includes Most Compatible for future captures. Changing that setting does not convert existing files.</p><p>See <a href=\"https://support.apple.com/en-us/116944\">Apple's HEIF and HEVC guidance</a> for device-specific availability. Use a local export when you prefer not to upload the image to a server.</p>"),
        ],
        "faqs": [("Will changing .heic to .jpg work?", "No. The encoded image data must be converted. Renaming can make the file harder for apps to identify."),
                 ("Why does the JPG look different?", "Lossy compression, color-profile handling, HDR display behavior and viewer settings can affect appearance. Compare in the intended receiving app and keep the HEIC original.")],
    },
    "merge-split-or-extract-pdf-pages": {
        "title": "Merge, Split or Extract PDF Pages: Which Tool? | " + SITE,
        "meta": "Choose the right PDF page operation and verify its output using original three-page and one-page practice documents.",
        "h1": "Merge, split, extract or reorder: choose the right PDF tool",
        "dek": "These operations solve different problems. Start with the shape of the output you need, then check page count and order.",
        "primary_tool": "merge-pdf", "related": ["split-pdf", "extract-pdf-pages", "organize-pdf"],
        "date": "2026-09-26", "published": "2026-09-26", "example": "pages",
        "body": [
            ("Choose by the result", "<ul><li><a href=\"/merge-pdf\">Merge PDF</a>: several input PDFs become one PDF.</li><li><a href=\"/split-pdf\">Split PDF</a>: one input becomes several PDFs in a ZIP.</li><li><a href=\"/extract-pdf-pages\">Extract Pages</a>: selected pages become one PDF.</li><li><a href=\"/organize-pdf\">Organize PDF</a>: a typed list changes page order, drops pages or repeats them.</li></ul><p>None of these operations redacts words inside a retained page. If your goal is to hide confidential information, removing unrelated pages is not sufficient.</p>"),
            ("Work through a four-page packet", "<p>Download the three-page report and one-page appendix below. Select the report first and the appendix second in Merge PDF. Inspect the file list, run the tool, and confirm that the appendix appears as physical page 4.</p><p>To send only the report's first and third pages, use Extract Pages on the report with <code>1,3</code>. To create separate PDFs for report and appendix from the merged packet, use Split PDF with custom ranges <code>1-3,4</code>, then extract the downloaded ZIP.</p>"),
            ("Understand page numbers before typing ranges", "<p>Tools use physical positions starting at 1. A cover counts, even if its printed page number is absent. A viewer may display a label such as iii or A-1 while the tool still needs the page's position. Count from the start of the actual file.</p><p>For Organize PDF, <code>3,1,2</code> places page 3 first; <code>1,1,2</code> duplicates page 1. Inspect the output rather than assuming a typed range selected the intended chapter.</p>"),
            ("Inspect boundaries, signatures and file size", "<p>Check the first and last page of each source segment, total page count, rotation and paper size. Merging can mix portrait, landscape and differently sized pages. It does not make their layouts consistent or automatically compress them.</p><p>Editing a digitally signed PDF can invalidate its signature. Preserve originals and confirm the recipient's requirements before modifying signed documents. If size becomes a problem, compress only after assembling and verifying the packet.</p>"),
        ],
        "faqs": [("Why did I receive a ZIP?", "Split PDF creates multiple output PDFs and packages them together. Use Extract Pages if you wanted one combined file."),
                 ("Can I undo the operation?", "Keep your original files. The tools create new outputs, but server copies are temporary and should not be treated as backups.")],
    },
    "make-a-scanned-pdf-searchable": {
        "title": "Make a Scanned PDF Searchable and Check OCR | " + SITE,
        "meta": "Decide whether your PDF needs OCR, choose the matching available language, and verify search and copied text against the original scan.",
        "h1": "Make a scanned PDF searchable, then verify the words",
        "dek": "OCR adds a text layer behind the page image. The page can look unchanged while its recognized text contains errors, so appearance alone is not a reliable check.",
        "primary_tool": "ocr-pdf", "related": ["rotate-pdf", "pdf-to-text", "pdf-to-word"],
        "date": "2026-09-26", "published": "2026-09-26",
        "body": [
            ("Decide whether recognition is necessary", "<p>Search for a distinctive visible word, then try selecting and copying a sentence. If those work, the PDF already has text; it may not need another OCR pass. Test more than one page because scans and digital pages can be mixed.</p><p>You can practice with our <a href=\"/static/examples/scan.pdf\" data-ff-download download>synthetic image-only scan</a>. It deliberately has no selectable words. We do not publish an OCR accuracy score for it; recognition depends on the engine and language available on the server.</p>"),
            ("Prepare the page and run OCR", "<ol><li>Rotate sideways pages before processing. Use a sharp original rather than an image that has already been compressed repeatedly.</li><li>Open <a href=\"/ocr-pdf\">OCR PDF</a>, choose the PDF, and select its matching available language.</li><li>Wait for processing and download the searchable PDF. If the engine or model is unavailable, do not assume another language will give acceptable results.</li><li>Open the result in a viewer with search and text selection.</li></ol>"),
            ("Check the invisible layer", "<p>Search for words on several pages, including a heading and a word near the bottom. Copy a sentence into a plain text editor and compare it character by character with the scan. Check numbers and decimal points separately; a plausible word does not prove an accurate amount.</p><p>For multilingual documents, inspect every script used. Stamps, handwriting, faint print and complex layouts can confuse recognition. Keep the original and obtain human review when an error would affect a decision or submission.</p>"),
            ("Choose a different output when needed", "<p>A searchable PDF is useful for finding words while keeping the scanned appearance. It is not the same as an editable Word document, a verified transcript or a fully tagged accessible PDF. Use <a href=\"/pdf-to-text\">PDF to Text</a> for plain text or an available OCR-aware Word conversion for editing, then review those outputs too.</p><p>If search fails only in one viewer, try another viewer before rerunning OCR. If copied text is consistently wrong, improve scan quality and verify language selection rather than accepting the visible page as proof.</p>"),
        ],
        "faqs": [("Does OCR correct the original image?", "No. A searchable layer does not remove blur, stains or handwriting from the visible scan."),
                 ("Is every recognized word guaranteed correct?", "No. OCR is probabilistic. Check important fields against the original and have a qualified person review documents where errors matter.")],
    },
}


def guide_slugs() -> List[str]:
    return list(GUIDES.keys())


# --- schema ----------------------------------------------------------------

def _article_schema(slug: str, g: dict) -> dict:
    return {
        "@context": "https://schema.org",
        "@type": "Article",
        "headline": _plain(g["h1"]),
        "description": g["meta"],
        "inLanguage": "en",
        "mainEntityOfPage": {"@type": "WebPage", "@id": BLOG_BASE + "/" + slug},
        "dateModified": g["date"],
        "datePublished": g["published"],
        "author": {"@type": "Organization", "name": SITE, "url": BASE + "/about"},
        "publisher": {
            "@type": "Organization",
            "name": SITE,
            "url": BASE + "/",
            "sameAs": [GITHUB],
        },
    }


def _breadcrumb_schema(slug: str, g: dict) -> dict:
    return {
        "@context": "https://schema.org",
        "@type": "BreadcrumbList",
        "itemListElement": [
            {"@type": "ListItem", "position": 1, "name": SITE, "item": BASE + "/"},
            {"@type": "ListItem", "position": 2, "name": "Guides", "item": BLOG_BASE},
            {"@type": "ListItem", "position": 3, "name": _plain(g["h1"]),
             "item": BLOG_BASE + "/" + slug},
        ],
    }


def _faq_schema(faqs: List[Tuple[str, str]]) -> dict:
    return {
        "@context": "https://schema.org",
        "@type": "FAQPage",
        "mainEntity": [
            {"@type": "Question", "name": _plain(q),
             "acceptedAnswer": {"@type": "Answer", "text": _plain(a)}}
            for q, a in faqs
        ],
    }


def _related_tools_html(slugs: List[str], sep: str = " · ") -> str:
    links = []
    for s in slugs:
        t = TOOL_PAGES.get(s)
        if t:
            links.append('<a href="/' + s + '">' + t["app"] + "</a>")
    return sep.join(links)


_HEAD = """<!DOCTYPE html>
<html lang="en">

<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    {site_verify}
    <title>{title}</title>
    <meta name="description" content="{meta}">
    <link rel="canonical" href="{canonical}">
    <meta name="robots" content="index, follow, max-image-preview:large, max-snippet:-1, max-video-preview:-1">
    <meta name="theme-color" content="#ffffff">
    <meta property="og:type" content="{og_type}">
    <meta property="og:site_name" content="{site}">
    <meta property="og:title" content="{og_title}">
    <meta property="og:description" content="{og_desc}">
    <meta property="og:url" content="{canonical}">
    <meta property="og:image" content="{base}/static/og-image.png">
    <meta property="og:image:width" content="1200">
    <meta property="og:image:height" content="630">
    <meta property="og:image:alt" content="{og_title}">
    <meta name="twitter:card" content="summary_large_image">
    <meta name="twitter:title" content="{og_title}">
    <meta name="twitter:description" content="{og_desc}">
    <meta name="twitter:image" content="{base}/static/og-image.png">
    <link rel="icon" type="image/svg+xml" href="/static/favicon.svg">
    <link rel="apple-touch-icon" href="{base}/static/apple-touch-icon.png">
    {ds_head}
    {ads_head}
    {cf_analytics}
    {ga_analytics}
    {datafast_analytics}
{schema_blocks}
</head>
"""


def render_guide(slug: str) -> str:
    g = GUIDES[slug]
    canonical = BLOG_BASE + "/" + slug
    og_title = g["title"].split(" | ")[0]

    schema_blocks = "\n".join([
        _jsonld(_article_schema(slug, g)),
        _jsonld(_breadcrumb_schema(slug, g)),
        _jsonld(_faq_schema(g["faqs"])),
    ])
    head = _HEAD.format(
        site_verify=SITE_VERIFY, title=_attr(g["title"]), meta=_attr(g["meta"]),
        canonical=canonical, og_type="article", site=SITE, og_title=_attr(og_title),
        og_desc=_attr(g["meta"]), base=BASE, asset_v=ASSET_V, ads_head=ADS_HEAD,
        cf_analytics=CF_ANALYTICS, ga_analytics=GA_ANALYTICS, ds_head=DS_HEAD,
        datafast_analytics=DATAFAST_ANALYTICS, schema_blocks=schema_blocks,
    )

    sections = "\n".join(
        f'        <section class="sec" id="section-{i}"><i>{i:02d}</i><div><h2>{h2}</h2>\n        {body}</div></section>'
        for i, (h2, body) in enumerate(g["body"], 1)
    )
    contents = "".join(
        f'<a href="#section-{i}"><i>{i:02d}</i>{_attr(h2)}</a>' for i, (h2, _) in enumerate(g["body"], 1)
    )
    example = render_example(g["example"]) if g.get("example") else ""
    faqs = ds_faq(g["faqs"])
    tool = TOOL_PAGES[g["primary_tool"]]
    cta_href = "/" + g["primary_tool"]

    return f"""{head}
<body class="ff-ds ffc">
    <a class="skip-content" href="#main-content">Skip to content</a>
    {DS_NAV}
    <main class="ffc-wrap ffc-main ffc-wide" id="main-content">
        {ds_breadcrumb(("Guides", "/blog"), (_attr(g["h1"]), ""))}

        <h1 class="ffc-h1">{g['h1']}</h1>
        <p class="ffc-lede">{g['dek']}</p>
        <p class="ffc-meta content-meta">Published by <a href="/about">Forge Files</a> · Updated <time datetime="{g['date']}">{g['date']}</time></p>

        <div class="ff-art">
            <nav class="toc article-contents" aria-label="In this guide"><b>In this guide</b>{contents}</nav>
            <div class="ffc-artbody">
        <div class="cta"><a class="ff-btn primary" href="{cta_href}">Open the free {tool['app']} tool &rarr;</a></div>

{sections}

{example}

        <h2 class="ffc-section-h">Frequently asked questions</h2>
{faqs}

        <aside class="processing-note ffc-note"><h2>About this guide</h2><p>These instructions describe Forge Files' current controls and known conversion limits.
            Examples use original synthetic files, with actual outputs and reproducible measurements where shown.
            AI-assisted drafting was checked against the implementation and the supplied examples; no external expert review is claimed.
            <a href="/contact">Report an error or a confusing step</a>.</p></aside>

        {ADS_SLOT}

        <h2 class="ffc-section-h">Related free tools</h2>
        <div class="ffc-related">{_related_tools_html([g['primary_tool']] + g['related'], sep='')}</div>
            </div>
        </div>
    </main>
    {DS_FOOT}
    {CONSENT_BANNER}
    {FUNNEL_BEACON}
    {DS_THEME_JS}
</body>

</html>
"""


def render_blog_index() -> str:
    canonical = BLOG_BASE
    title = "Guides: How to Work With PDFs, Images & Documents | " + SITE
    meta = ("Free step-by-step guides for compressing PDFs, converting PDF to "
            "Word, turning iPhone HEIC photos into JPG, and more, from the "
            "open-source Forge Files toolbox.")
    schema_blocks = _jsonld({
        "@context": "https://schema.org",
        "@type": "CollectionPage",
        "name": "Forge Files Guides",
        "description": meta,
        "url": canonical,
        "inLanguage": "en",
    })
    head = _HEAD.format(
        site_verify=SITE_VERIFY, title=_attr(title), meta=_attr(meta),
        canonical=canonical, og_type="website", site=SITE, og_title=_attr(title.split(" | ")[0]),
        og_desc=_attr(meta), base=BASE, asset_v=ASSET_V, ads_head="",
        cf_analytics=CF_ANALYTICS, ga_analytics=GA_ANALYTICS, ds_head=DS_HEAD,
        datafast_analytics=DATAFAST_ANALYTICS, schema_blocks=schema_blocks,
    )
    cards = "\n".join(
        f'            <li><a class="ff-gcard" href="/blog/{slug}"><h4>{_attr(g["h1"])}</h4>'
        f'<p>{_attr(g["meta"])}</p><span class="m"><span class="ff-chip">Updated {g["date"]}</span></span></a></li>'
        for slug, g in GUIDES.items()
    )
    return f"""{head}
<body class="ff-ds ffc">
    <a class="skip-content" href="#main-content">Skip to content</a>
    {DS_NAV}
    <main class="ffc-wrap ffc-main ffc-wide" id="main-content">
        {ds_breadcrumb(("Guides", ""))}

        <h1 class="ffc-h1">Forge Files Guides</h1>
        <p class="ffc-lede">Know what changes before you share it.</p>
        <div class="ff-prose">
        <p>A smaller scan can lose fine print. A PDF converted to Word can shift tables or leave scanned text as an image.
            Try a practice file, compare the source with the output, and keep your original.</p>
        <p>Choose a guide below for the right tool, original downloadable examples, and checks to make before sharing your result.</p>
        </div>

        <ul class="ffc-gridwrap guide-list">
{cards}
        </ul>

        <p class="ffc-cta-row"><a class="ff-btn primary" href="/">Browse all free tools &rarr;</a></p>
    </main>
    {DS_FOOT}
    {CONSENT_BANNER}
    {FUNNEL_BEACON}
    {DS_THEME_JS}
</body>

</html>
"""
