"""Server-rendered SEO landing pages for Forge Files tools.

Every tool gets a dedicated, fully-rendered HTML page (title, meta, H1, body and
FAQ are all present in the *raw* response — no JavaScript required), which is what
both Google and JS-less AI crawlers (GPTBot, ClaudeBot, PerplexityBot, ...) need.

Adding a new tool page is a one-line entry in ``TOOL_PAGES`` below. ``main.py``
serves these via the catch-all ``/{slug}`` route and lists them in ``sitemap.xml``.

The rendered HTML keeps literal placeholders such as ``{{BASE_URL}}``,
``{{ADSENSE_HEAD}}`` and ``{{GA_ANALYTICS}}`` — ``main.py`` substitutes them at
request time (mirroring the existing ``_render_page`` mechanism), so this module
has no dependency on runtime configuration and stays trivially unit-testable.
"""
from __future__ import annotations

import html
import json
import re
from typing import Dict, List, Tuple

from scripts.tool_extra import HELP, EXAMPLE_KIND, extra_html
from scripts.tool_registry import TOOLS_BY_SEO_SLUG
from scripts.content_examples import render_example

# --- constants -------------------------------------------------------------

ASSET_V = "20260928"
CONTENT_REVIEWED = "2026-09-26"
SITE = "Forge Files"
GITHUB = "https://github.com/BhurkeSiddhesh/File-Forge"

# Literal tokens substituted by main.py. Defined as plain strings (NOT inside an
# f-string) so the braces survive into the rendered output verbatim.
BASE = "{{BASE_URL}}"
ADS_HEAD = "{{ADSENSE_HEAD}}"
ADS_SLOT = "{{ADSENSE_SLOT}}"
CONSENT_BANNER = "{{CONSENT_BANNER}}"
SITE_VERIFY = "{{SITE_VERIFICATION}}"
CF_ANALYTICS = "{{CF_ANALYTICS}}"
GA_ANALYTICS = "{{GA_ANALYTICS}}"
DATAFAST_ANALYTICS = (
    '<script\n'
    '      defer\n'
    '      data-website-id="dfid_na6goRSY6Vnle9ErZzQbC"\n'
    '      data-domain="forgefiles.org"\n'
    '      src="https://datafa.st/js/script.js">\n'
    '    </script>'
)

# First-party page-view beacon for the server-rendered landing pages (which don't
# load script.js). Posts a single anonymous page_view to /api/track — the same
# first-party funnel endpoint the home app uses. No file data, cookies-only the
# anonymous ff_sid the server sets. Best-effort and silent on any failure.
#
# `ref` is document.referrer, which the server reduces to a bare host before
# storing (never a full URL). These landing pages are the ones search traffic
# actually arrives on, so this is where the acquisition signal lives.
FUNNEL_BEACON = (
    "<script>(function(){try{"
    "var p=JSON.stringify({event:'page_view',label:location.pathname||'/',"
    "ref:document.referrer||''});"
    "var u='/api/track';"
    "if(navigator.sendBeacon){navigator.sendBeacon(u,new Blob([p],{type:'application/json'}));}"
    "else{fetch(u,{method:'POST',body:p,keepalive:true,"
    "headers:{'Content-Type':'application/json'},credentials:'same-origin'});}"
    "}catch(e){}})();</script>"
)

# What each category's file picker should offer, mirroring the corresponding
# input in static/index.html. Only a filter on the picker dialog — the server
# validates the actual upload, so a loose value here can't let anything through.
_CATEGORY_ACCEPT = {
    "pdf": ".pdf",
    "image": "image/*,.heic,.heif",
    "excel": ".xlsx,.xls,.csv",
    "ppt": ".pptx",
    "word": ".docx,.doc",
}

# Tools that collect several files (merge tools).
_MULTI_FILE_SLUGS = ("merge-pdf", "merge-excel", "merge-ppt")


def _upload_box(slug: str, page: dict) -> str:
    """The landing page's primary action: an upload box, not a link to one.

    A visitor who searched "pdf to word" or "merge pdf" and landed here can
    choose or drop their file(s) immediately. The file(s) chosen here are
    carried into the app by static/seo-upload.js so conversion starts right away.

    The link is still rendered inside noscript for JS-disabled browsers.
    """
    tool = page["tool"]
    target = f"/?tool={tool}&amp;op={slug}"
    cta = html.escape(page["cta"])
    if tool not in _CATEGORY_ACCEPT:
        return f'        <p class="ffc-cta-row"><a class="cta" href="{target}">{cta}</a></p>'
    accept = _CATEGORY_ACCEPT[tool]
    is_multi = slug in _MULTI_FILE_SLUGS
    multiple_attr = " multiple" if is_multi else ""
    drop_hint = "or drop files here" if is_multi else "or drop a file here"
    return f"""        <div class="upload-cta ff-drop" data-ff-upload data-ff-target="/?tool={tool}&amp;op={slug}">
            <label class="upload-cta-label">
                <input class="upload-cta-input" type="file" aria-label="{cta}" accept="{accept}"{multiple_attr}>
                <span class="ff-btn primary">{cta}</span>
            </label>
            <p class="upload-cta-hint">{drop_hint} &middot; free, no signup,
                files deleted automatically</p>
            <p class="upload-cta-secondary"><a class="cta" href="{target}">Open the tool before choosing a file</a></p>
        </div>"""


# --- design-system shell for server-rendered content pages -------------------
# Shared by tool landing pages, the 404, the blog index and guides. Static info
# pages in static/pages/ carry the same markup literally.
DS_V = "20261006"
DS_HEAD = (
    '<link rel="stylesheet" href="/static/ds/design-system.css?v=' + DS_V + '">\n'
    '    <link rel="stylesheet" href="/static/ds/pages/content.css?v=' + DS_V + '">\n'
    '    <script>(function(){try{var t=localStorage.getItem("theme");'
    'if(t!=="dark"&&t!=="light"){var n=new Date(),m=n.getHours()*60+n.getMinutes();'
    't=(m>=1110||m<390)?"dark":"light";}document.documentElement.setAttribute("data-theme",t);}catch(e){}})();</script>'
)
_ICON = ('<svg class="{c}" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
         'stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">{d}</svg>')
DS_NAV = (
    '<header class="ff-nav ffc-nav">\n'
    '        <a class="brand" href="/" aria-label="Forge Files home"><img src="/static/forge-files-logo-animation.gif" alt="" width="32" height="32">Forge <span class="fi">Files</span></a>\n'
    '        <a class="l" href="/">All tools</a><a class="l" href="/blog">Guides</a><a class="l" href="/about">About</a>\n'
    '        <span class="sp"></span>\n'
    '        <button type="button" class="ff-iconbtn" id="theme-toggle-btn" aria-label="Toggle dark mode">'
    + _ICON.format(c="ffc-moon", d='<path d="M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9Z"/>')
    + _ICON.format(c="ffc-sun", d='<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2m-7.07-2.93 1.41-1.41M17.66 6.34l1.41-1.41M2 12h2M20 12h2M4.93 4.93l1.41 1.41m11.32 11.32 1.41 1.41"/>')
    + '</button>\n'
    '    </header>'
)
DS_THEME_JS = (
    '<script>(function(){var b=document.getElementById("theme-toggle-btn");if(!b)return;'
    'b.addEventListener("click",function(){var r=document.documentElement,'
    'n=r.getAttribute("data-theme")==="dark"?"light":"dark";r.setAttribute("data-theme",n);'
    'try{localStorage.setItem("theme",n);}catch(e){}});})();</script>'
)
DS_FOOT = f"""<footer class="ff-foot ffc-foot">
        <div class="ffc-wrap">
            <div class="cols">
                <div class="col">
                    <h4>Popular tools</h4>
                    <a href="/merge-pdf">Merge PDF</a>
                    <a href="/compress-pdf">Compress PDF</a>
                    <a href="/pdf-to-word">PDF to Word</a>
                    <a href="/unlock-pdf">Unlock PDF</a>
                    <a href="/heic-to-jpeg">HEIC to JPG</a>
                    <a href="/image-to-pdf">Image to PDF</a>
                </div>
                <div class="col">
                    <h4>Forge Files</h4>
                    <a href="/about">About</a>
                    <a href="/faq">FAQ</a>
                    <a href="/blog">Guides &amp; practice files</a>
                    <a href="/contact">Contact</a>
                    <a href="{GITHUB}" target="_blank" rel="noopener">GitHub</a>
                </div>
                <div class="col">
                    <h4>Legal</h4>
                    <a href="/privacy">Privacy Policy</a>
                    <a href="/terms">Terms of Use</a>
                </div>
            </div>
            <p class="note">&copy; 2026 Forge Files. Public toolbox: AGPLv3, with commercial licensing available.
                Some supported files process on your device; others upload to the server. Keep originals and download results promptly.
                Processing and upload limits apply. <a href="/privacy">File handling and retention</a>.</p>
        </div>
    </footer>"""
DS_CHEV = ('<svg class="ffc-chev" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
           'stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m6 9 6 6 6-6"/></svg>')


def ds_breadcrumb(*trail: Tuple[str, str]) -> str:
    """Breadcrumb bar: ``trail`` is (label, href) pairs; the last has no link."""
    parts = ['<a href="/">Home</a>']
    for label, href in trail:
        parts.append('<span aria-hidden="true">/</span>')
        parts.append(f'<a href="{href}">{label}</a>' if href else f'<span aria-current="page">{label}</span>')
    return '<nav class="ff-bc" aria-label="Breadcrumb">' + "".join(parts) + "</nav>"


def ds_faq(faqs: List[Tuple[str, str]]) -> str:
    """Accordion for (question, answer-html) pairs."""
    items = "\n".join(
        "            <details><summary><span>" + _plain(q) + "</span>" + DS_CHEV + "</summary><p>" + a + "</p></details>"
        for q, a in faqs
    )
    return '        <div class="ff-faq">\n' + items + "\n        </div>"

# category -> (deep-link tool param, human label)
CATEGORIES = {
    "pdf": "PDF Tools",
    "image": "Image Tools",
    "excel": "Excel Tools",
    "ppt": "PowerPoint Tools",
    "word": "Word Tools",
}

_TAG_RE = re.compile(r"<[^>]+>")


def _plain(s: str) -> str:
    """Strip HTML tags + unescape entities — for use inside JSON-LD text."""
    return html.unescape(_TAG_RE.sub("", s)).strip()


def _attr(s: str) -> str:
    """Escape a value destined for an HTML attribute (title/meta/og)."""
    return html.escape(s, quote=True)


# --- tool catalogue --------------------------------------------------------
# Each entry: title, meta, h1, lede, tool (deep-link category), app
# (SoftwareApplication name), cta, how (H2 heading), steps[], benefits[] (raw
# HTML), faqs[] (question, answer-HTML), related[] (slugs).
#
# The CTA deep-links to `/?tool=<tool>&op=<slug>`. `tool` opens the category
# view; `op` is this dict's key, which static/script.js maps (via DEEP_LINK_OPS)
# to the specific action card, so someone who searched "pdf to word" lands on
# the PDF→Word tool rather than on a grid of 19 cards they have to re-scan. An
# `op` script.js doesn't recognise is ignored and the category still opens, so
# adding a page here never breaks the CTA — but do add the mapping.

TOOL_PAGES: Dict[str, dict] = {'unlock-pdf': {'title': 'Unlock PDF: Remove PDF Password Online Free | Forge Files',
                'h1': 'Unlock PDF: Remove a PDF Password Online',
                'tool': 'pdf',
                'app': 'Unlock PDF',
                'cta': 'Unlock a PDF now, free',
                'how': 'How to remove a PDF password',
                'steps': ['Upload your PDF (drag &amp; drop or browse).',
                          "Choose <strong>Remove Password</strong> and type the document's current password.",
                          'Download the result and compare it with your original before sharing.'],
                'related': ['pdf-to-word', 'compress-pdf', 'extract-pdf-pages', 'merge-pdf', 'protect-pdf']},
 'pdf-to-word': {'title': 'PDF to Word Converter: Free Online, No Signup | Forge Files',
                 'h1': 'PDF to Word Converter: Free &amp; Online',
                 'tool': 'pdf',
                 'app': 'PDF to Word',
                 'cta': 'Convert PDF to Word, free',
                 'how': 'How to convert PDF to Word',
                 'steps': ['Upload your PDF (drag &amp; drop or browse).',
                           'Choose <strong>Convert to Word</strong>. (If the PDF is locked, <a '
                           'href="/unlock-pdf">unlock it</a> first.)',
                           'Download the result and compare it with your original before sharing.'],
                 'related': ['unlock-pdf',
                             'compress-pdf',
                             'extract-pdf-pages',
                             'pdf-to-text',
                             'word-to-pdf']},
 'compress-pdf': {'title': 'Compress PDF Online Free: Reduce PDF File Size | Forge Files',
                  'h1': 'Compress PDF: Reduce PDF File Size Online',
                  'tool': 'pdf',
                  'app': 'Compress PDF',
                  'cta': 'Compress a PDF now, free',
                  'how': 'How to compress a PDF',
                  'steps': ['Upload your PDF (drag &amp; drop or browse).',
                            'Choose <strong>Compress PDF</strong> and pick a level: Low (best quality), '
                            'Medium (balanced), or High (smallest size).',
                            'Download the result and compare it with your original before sharing.'],
                  'related': ['merge-pdf',
                              'pdf-to-word',
                              'extract-pdf-pages',
                              'unlock-pdf',
                              'compress-image']},
 'extract-pdf-pages': {'title': 'Extract Pages from PDF: Split PDF Online Free | Forge Files',
                       'h1': 'Extract Pages from a PDF: Split PDFs Online',
                       'tool': 'pdf',
                       'app': 'Extract PDF Pages',
                       'cta': 'Extract PDF pages, free',
                       'how': 'How to extract pages from a PDF',
                       'steps': ['Upload your PDF (drag &amp; drop or browse).',
                                 'Choose <strong>Extract Pages</strong> and type the pages you want, e.g. '
                                 '<code>1,3,5-10</code>.',
                                 'Download the result and compare it with your original before sharing.'],
                       'related': ['split-pdf', 'merge-pdf', 'organize-pdf', 'compress-pdf', 'pdf-to-word']},
 'pdf-to-text': {'title': 'PDF to Text: Extract Text from PDF Free | Forge Files',
                 'h1': 'PDF to Text: Extract Text from Any PDF',
                 'tool': 'pdf',
                 'app': 'PDF to Text',
                 'cta': 'Extract PDF text, free',
                 'how': 'How to convert PDF to text',
                 'steps': ['Upload your PDF (drag &amp; drop or browse).',
                           'Choose <strong>Extract Text</strong> and set <strong>Preserve layout '
                           'structure</strong> if needed.',
                           'Download the result and compare it with your original before sharing.'],
                 'related': ['ocr-pdf', 'pdf-to-word', 'pdf-to-excel', 'extract-pdf-pages', 'compress-pdf']},
 'ocr-pdf': {'title': 'OCR PDF: Make a PDF Searchable Online Free | Forge Files',
             'h1': 'OCR PDF: Make Scanned PDFs Searchable',
             'tool': 'pdf',
             'app': 'OCR PDF',
             'cta': 'Make PDF searchable, free',
             'how': 'How to OCR a PDF',
             'steps': ['Upload your scanned or image-only PDF.',
                       'Choose <strong>OCR PDF</strong> and select the matching available language.',
                       'Download the result and compare it with your original before sharing.'],
             'related': ['make-pdf-searchable', 'pdf-to-text', 'pdf-to-word', 'compress-pdf', 'split-pdf']},
 'ocr-hindi': {'title': 'OCR Hindi PDF: Make Hindi PDFs Searchable Online | Forge Files',
               'h1': 'OCR Hindi PDF: Make Scanned Hindi PDFs Searchable',
               'tool': 'pdf',
               'app': 'OCR Hindi PDF',
               'cta': 'OCR Hindi PDF',
               'how': 'How to OCR a Hindi PDF',
               'steps': ['Upload your scanned Hindi PDF or document.',
                         'Select <strong>Hindi (हिन्दी)</strong> in the OCR language dropdown.',
                         'Download the result and compare it with your original before sharing.'],
               'related': ['ocr-marathi', 'ocr-pdf', 'make-pdf-searchable', 'pdf-to-text', 'pdf-to-word']},
 'ocr-marathi': {'title': 'OCR Marathi PDF: Make Marathi PDFs Searchable Online | Forge Files',
                 'h1': 'OCR Marathi PDF: Make Scanned Marathi PDFs Searchable',
                 'tool': 'pdf',
                 'app': 'OCR Marathi PDF',
                 'cta': 'OCR Marathi PDF',
                 'how': 'How to OCR a Marathi PDF',
                 'steps': ['Upload your scanned Marathi document or certificate.',
                           'Select <strong>Marathi (मराठी)</strong> in the OCR language dropdown.',
                           'Download the result and compare it with your original before sharing.'],
                 'related': ['ocr-hindi', 'ocr-pdf', 'make-pdf-searchable', 'pdf-to-text', 'pdf-to-word']},
 'ocr-tamil': {'title': 'OCR Tamil PDF: Make Tamil PDFs Searchable Online | Forge Files',
               'h1': 'OCR Tamil PDF: Make Scanned Tamil PDFs Searchable',
               'tool': 'pdf',
               'app': 'OCR Tamil PDF',
               'cta': 'OCR Tamil PDF',
               'how': 'How to OCR a Tamil PDF',
               'steps': ['Upload your scanned Tamil PDF file.',
                         'Choose <strong>Tamil (தமிழ்)</strong> as the OCR language.',
                         'Download the result and compare it with your original before sharing.'],
               'related': ['ocr-telugu', 'ocr-hindi', 'ocr-pdf', 'make-pdf-searchable', 'pdf-to-word']},
 'ocr-telugu': {'title': 'OCR Telugu PDF: Make Telugu PDFs Searchable Online | Forge Files',
                'h1': 'OCR Telugu PDF: Make Scanned Telugu PDFs Searchable',
                'tool': 'pdf',
                'app': 'OCR Telugu PDF',
                'cta': 'OCR Telugu PDF',
                'how': 'How to OCR a Telugu PDF',
                'steps': ['Upload your scanned Telugu PDF.',
                          'Select <strong>Telugu (తెలుగు)</strong> in the OCR language options.',
                          'Download the result and compare it with your original before sharing.'],
                'related': ['ocr-tamil', 'ocr-hindi', 'ocr-pdf', 'make-pdf-searchable', 'pdf-to-word']},
 'make-pdf-searchable': {'title': 'Make PDF Searchable Online Free | Forge Files',
                         'h1': 'Make PDF Searchable',
                         'tool': 'pdf',
                         'app': 'OCR PDF',
                         'cta': 'Make PDF searchable',
                         'how': 'How to make a PDF searchable',
                         'steps': ['Upload the scanned PDF.',
                                   'Choose <strong>OCR PDF</strong> in the PDF tools.',
                                   'Download the result and compare it with your original before sharing.'],
                         'related': ['ocr-pdf', 'pdf-to-text', 'pdf-to-word', 'compress-pdf', 'unlock-pdf']},
 'merge-pdf': {'title': 'Merge PDF: Combine PDF Files Online Free | Forge Files',
               'h1': 'Merge PDF: Combine Multiple PDFs Into One',
               'tool': 'pdf',
               'app': 'Merge PDF',
               'cta': 'Combine PDF files, free',
               'how': 'How to merge PDF files',
               'steps': ['Open the PDF tools and choose <strong>Merge PDFs</strong>.',
                         'Select two or more PDFs in the upload area (multi-select is enabled).',
                         'Download the result and compare it with your original before sharing.'],
               'related': ['compress-pdf', 'extract-pdf-pages', 'organize-pdf', 'split-pdf', 'pdf-to-word']},
 'split-pdf': {'title': 'Split PDF: Separate PDF Pages Online Free | Forge Files',
               'h1': 'Split PDF Into Separate Files',
               'tool': 'pdf',
               'app': 'Split PDF',
               'cta': 'Split a PDF now, free',
               'how': 'How to split a PDF',
               'steps': ['Upload your PDF (drag &amp; drop or browse).',
                         'Choose <strong>Split PDF</strong> and pick every page, every N pages, or custom '
                         'ranges like <code>1-5,6-10,11</code>.',
                         'Download the result and compare it with your original before sharing.'],
               'related': ['extract-pdf-pages', 'merge-pdf', 'organize-pdf', 'compress-pdf', 'pdf-to-jpg']},
 'rotate-pdf': {'title': 'Rotate PDF: Turn PDF Pages Online Free | Forge Files',
                'h1': 'Rotate PDF: Fix Sideways or Upside-Down Pages',
                'tool': 'pdf',
                'app': 'Rotate PDF',
                'cta': 'Rotate a PDF now, free',
                'how': 'How to rotate a PDF',
                'steps': ['Upload your PDF (drag &amp; drop or browse).',
                          'Choose <strong>Rotate PDF</strong>, pick an angle (90°, 180° or 270°), and '
                          'optionally list specific pages.',
                          'Download the result and compare it with your original before sharing.'],
                'related': ['organize-pdf',
                            'extract-pdf-pages',
                            'compress-pdf',
                            'merge-pdf',
                            'rotate-image']},
 'protect-pdf': {'title': 'Protect PDF: Add a Password Online Free | Forge Files',
                 'h1': 'Protect PDF: Add a Password to Your PDF',
                 'tool': 'pdf',
                 'app': 'Protect PDF',
                 'cta': 'Password-protect a PDF, free',
                 'how': 'How to password-protect a PDF',
                 'steps': ['Upload your PDF (drag &amp; drop or browse).',
                           "Choose <strong>Protect PDF</strong>, set a user password, and pick what's "
                           'allowed (printing, copying, editing).',
                           'Download the result and compare it with your original before sharing.'],
                 'related': ['unlock-pdf', 'watermark-pdf', 'compress-pdf', 'sign-pdf', 'merge-pdf']},
 'watermark-pdf': {'title': 'Watermark PDF: Add a Watermark Online Free | Forge Files',
                   'h1': 'Add a Watermark to a PDF',
                   'tool': 'pdf',
                   'app': 'Watermark PDF',
                   'cta': 'Add a PDF watermark, free',
                   'how': 'How to watermark a PDF',
                   'steps': ['Upload your PDF (drag &amp; drop or browse).',
                             'Choose <strong>Add Watermark</strong>, type your text, and set the position '
                             'and opacity.',
                             'Download the result and compare it with your original before sharing.'],
                   'related': ['protect-pdf',
                               'sign-pdf',
                               'pdf-page-numbers',
                               'compress-pdf',
                               'watermark-image']},
 'pdf-to-jpg': {'title': 'PDF to JPG: Convert PDF Pages to Images Free | Forge Files',
                'h1': 'PDF to JPG: Turn PDF Pages Into Images',
                'tool': 'pdf',
                'app': 'PDF to JPG',
                'cta': 'Convert PDF to JPG, free',
                'how': 'How to convert a PDF to JPG',
                'steps': ['Upload your PDF (drag &amp; drop or browse).',
                          'Choose <strong>PDF → JPG</strong>, then pick a resolution (72/150/300 DPI) and '
                          'format (JPG or PNG).',
                          'Download the result and compare it with your original before sharing.'],
                'related': ['image-to-pdf',
                            'pdf-to-powerpoint',
                            'compress-pdf',
                            'extract-pdf-pages',
                            'pdf-to-excel']},
 'pdf-page-numbers': {'title': 'Add Page Numbers to PDF: Free Online | Forge Files',
                      'h1': 'Add Page Numbers to a PDF',
                      'tool': 'pdf',
                      'app': 'Add Page Numbers',
                      'cta': 'Add page numbers, free',
                      'how': 'How to add page numbers to a PDF',
                      'steps': ['Upload your PDF (drag &amp; drop or browse).',
                                'Choose <strong>Add Page Numbers</strong>, then set position, number format, '
                                'start value, and pages to skip.',
                                'Download the result and compare it with your original before sharing.'],
                      'related': ['watermark-pdf', 'organize-pdf', 'merge-pdf', 'compress-pdf', 'sign-pdf']},
 'pdf-to-excel': {'title': 'PDF to Excel: Extract Tables to XLSX Free | Forge Files',
                  'h1': 'PDF to Excel: Extract Tables to a Spreadsheet',
                  'tool': 'pdf',
                  'app': 'PDF to Excel',
                  'cta': 'Convert PDF to Excel, free',
                  'how': 'How to convert PDF to Excel',
                  'steps': ['Upload your PDF (drag &amp; drop or browse).',
                            'Choose <strong>PDF → Excel</strong> to detect and extract the tables.',
                            'Download the result and compare it with your original before sharing.'],
                  'related': ['pdf-to-text',
                              'pdf-to-word',
                              'excel-to-pdf',
                              'pdf-to-powerpoint',
                              'compress-pdf']},
 'pdf-to-powerpoint': {'title': 'PDF to PowerPoint: Convert PDF to PPTX Free | Forge Files',
                       'h1': 'PDF to PowerPoint: Convert PDF to Slides',
                       'tool': 'pdf',
                       'app': 'PDF to PowerPoint',
                       'cta': 'Convert PDF to PPTX, free',
                       'how': 'How to convert PDF to PowerPoint',
                       'steps': ['Upload your PDF (drag &amp; drop or browse).',
                                 'Choose <strong>PDF → PowerPoint</strong> and pick a quality level.',
                                 'Download the result and compare it with your original before sharing.'],
                       'related': ['pdf-to-jpg',
                                   'powerpoint-to-pdf',
                                   'pdf-to-word',
                                   'pdf-to-excel',
                                   'compress-pdf']},
 'pdf-to-epub': {'title': 'PDF to EPUB: Convert PDF to Ebook Free | Forge Files',
                 'h1': 'PDF to EPUB: Convert PDF to a Reflowable Ebook',
                 'tool': 'pdf',
                 'app': 'PDF to EPUB',
                 'cta': 'Convert PDF to EPUB, free',
                 'how': 'How to convert PDF to EPUB',
                 'steps': ['Upload your PDF (drag &amp; drop or browse).',
                           'Choose <strong>PDF → EPUB</strong>.',
                           'Download the result and compare it with your original before sharing.'],
                 'related': ['pdf-to-word',
                             'pdf-to-text',
                             'pdf-to-excel',
                             'pdf-to-powerpoint',
                             'compress-pdf']},
 'sign-pdf': {'title': 'Sign PDF: Add a Signature to a PDF Free | Forge Files',
              'h1': 'Sign PDF: Stamp Your Signature on a Document',
              'tool': 'pdf',
              'app': 'Sign PDF',
              'cta': 'Sign a PDF now, free',
              'how': 'How to sign a PDF',
              'steps': ['Upload your PDF (drag &amp; drop or browse).',
                        'Choose <strong>Sign PDF</strong>, upload your signature image (a transparent PNG '
                        'works best), and pick the page, position and size.',
                        'Download the result and compare it with your original before sharing.'],
              'related': ['protect-pdf', 'watermark-pdf', 'pdf-page-numbers', 'merge-pdf', 'unlock-pdf']},
 'organize-pdf': {'title': 'Organize PDF: Reorder & Delete Pages Free | Forge Files',
                  'h1': 'Organize PDF: Reorder, Delete &amp; Duplicate Pages',
                  'tool': 'pdf',
                  'app': 'Organize PDF',
                  'cta': 'Organize a PDF now, free',
                  'how': 'How to reorder PDF pages',
                  'steps': ['Upload your PDF (drag &amp; drop or browse).',
                            'Choose <strong>Organize PDF</strong> and type the new page order, e.g. '
                            '<code>3,1,2</code>. Repeat a number to duplicate; omit one to delete it.',
                            'Download the result and compare it with your original before sharing.'],
                  'related': ['merge-pdf',
                              'extract-pdf-pages',
                              'split-pdf',
                              'rotate-pdf',
                              'pdf-page-numbers']},
 'heic-to-jpeg': {'title': 'HEIC to JPG Converter: Free Online, No Signup | Forge Files',
                  'h1': 'HEIC to JPG: Convert iPhone Photos Online',
                  'tool': 'image',
                  'app': 'HEIC to JPG',
                  'cta': 'Convert HEIC to JPG, free',
                  'how': 'How to convert HEIC to JPG',
                  'steps': ['Upload your .heic or .heif photo (drag &amp; drop or browse).',
                            'Pick a JPEG quality (95% default, visually identical) and click <strong>Convert '
                            'to JPEG</strong>.',
                            'Download the result and compare it with your original before sharing.'],
                  'related': ['resize-image',
                              'compress-image',
                              'convert-image',
                              'image-to-pdf',
                              'crop-image']},
 'resize-image': {'title': 'Resize Image: Pixels, Percent or Target KB | Forge Files',
                  'h1': 'Resize Image Online: Pixels, Percent, or Target KB',
                  'tool': 'image',
                  'app': 'Resize Image',
                  'cta': 'Resize an image now, free',
                  'how': 'How to resize an image',
                  'steps': ['Upload your image (JPG, PNG, WebP, or HEIC).',
                            'Pick a mode: exact <strong>dimensions</strong>, <strong>percentage</strong> '
                            'scale, or <strong>target file size</strong> in KB.',
                            'Download the result and compare it with your original before sharing.'],
                  'related': ['crop-image',
                              'compress-image',
                              'convert-image',
                              'heic-to-jpeg',
                              'image-to-pdf']},
 'image-to-pdf': {'title': 'Image to PDF: Convert JPG/PNG to PDF Free | Forge Files',
                  'h1': 'Image to PDF: Convert Photos to a PDF',
                  'tool': 'image',
                  'app': 'Image to PDF',
                  'cta': 'Convert image to PDF, free',
                  'how': 'How to convert an image to PDF',
                  'steps': ['Upload your image (JPG, PNG, WebP, or HEIC).',
                            'Choose <strong>Image → PDF</strong>, then pick the page size (A4, Letter, or '
                            'auto) and fit mode.',
                            'Download the result and compare it with your original before sharing.'],
                  'related': ['pdf-to-jpg', 'heic-to-jpeg', 'compress-image', 'resize-image', 'merge-pdf']},
 'compress-image': {'title': 'Compress Image: Reduce JPG/PNG Size Free | Forge Files',
                    'h1': 'Compress Image: Reduce Photo File Size',
                    'tool': 'image',
                    'app': 'Compress Image',
                    'cta': 'Compress an image, free',
                    'how': 'How to compress an image',
                    'steps': ['Upload your image (JPG, PNG, WebP, or HEIC).',
                              'Choose <strong>Compress</strong> and set the quality: lower means a smaller '
                              'file.',
                              'Download the result and compare it with your original before sharing.'],
                    'related': ['resize-image',
                                'convert-image',
                                'crop-image',
                                'compress-pdf',
                                'image-to-pdf']},
 'convert-image': {'title': 'Convert Image: JPG, PNG, WebP Converter Free | Forge Files',
                   'h1': 'Convert Image: JPG ↔ PNG ↔ WebP',
                   'tool': 'image',
                   'app': 'Convert Image',
                   'cta': 'Convert an image, free',
                   'how': 'How to convert an image format',
                   'steps': ['Upload your image (JPG, PNG, WebP, or HEIC).',
                             'Choose <strong>Convert Format</strong>, pick the target (JPG, PNG, or WebP), '
                             'and set quality if relevant.',
                             'Download the result and compare it with your original before sharing.'],
                   'related': ['compress-image',
                               'resize-image',
                               'heic-to-jpeg',
                               'crop-image',
                               'image-to-pdf']},
 'crop-image': {'title': 'Crop Image Online Free: Visual Crop Tool | Forge Files',
                'h1': 'Crop Image: Trim Photos Visually',
                'tool': 'image',
                'app': 'Crop Image',
                'cta': 'Crop an image now, free',
                'how': 'How to crop an image',
                'steps': ['Upload your image (JPG, PNG, WebP, or HEIC).',
                          'Switch to <strong>Crop</strong> mode and drag the handles over the area you want '
                          'to keep.',
                          'Download the result and compare it with your original before sharing.'],
                'related': ['resize-image',
                            'compress-image',
                            'convert-image',
                            'rotate-image',
                            'heic-to-jpeg']},
 'rotate-image': {'title': 'Rotate Image Online Free: Turn Photos | Forge Files',
                  'h1': 'Rotate Image: Fix Sideways Photos',
                  'tool': 'image',
                  'app': 'Rotate Image',
                  'cta': 'Rotate an image now, free',
                  'how': 'How to rotate an image',
                  'steps': ['Upload your image (JPG, PNG, WebP, or HEIC).',
                            'Choose <strong>Rotate</strong> and pick an angle: 90°, 180°, or 270°.',
                            'Download the result and compare it with your original before sharing.'],
                  'related': ['crop-image', 'resize-image', 'compress-image', 'rotate-pdf', 'convert-image']},
 'watermark-image': {'title': 'Watermark Image: Add Text to a Photo Free | Forge Files',
                     'h1': 'Add a Watermark to an Image',
                     'tool': 'image',
                     'app': 'Watermark Image',
                     'cta': 'Watermark an image, free',
                     'how': 'How to watermark an image',
                     'steps': ['Upload your image (JPG, PNG, WebP, or HEIC).',
                               'Choose <strong>Add Watermark</strong>, type your text, and set position, '
                               'color, and opacity.',
                               'Download the result and compare it with your original before sharing.'],
                     'related': ['watermark-pdf',
                                 'compress-image',
                                 'resize-image',
                                 'convert-image',
                                 'crop-image']},
 'excel-to-pdf': {'title': 'Excel to PDF: Convert XLSX to PDF Online Free | Forge Files',
                  'h1': 'Excel to PDF: Convert Spreadsheets to PDF',
                  'tool': 'excel',
                  'app': 'Excel to PDF',
                  'cta': 'Convert Excel to PDF, free',
                  'how': 'How to convert Excel to PDF',
                  'steps': ['Upload your Excel file (.xlsx or .xls) or CSV.',
                            'Choose <strong>Excel → PDF</strong>.',
                            'Download the result and compare it with your original before sharing.'],
                  'related': ['csv-to-xlsx', 'xlsx-to-csv', 'merge-excel', 'pdf-to-excel', 'word-to-pdf']},
 'csv-to-xlsx': {'title': 'CSV to Excel: Convert CSV to XLSX Online Free | Forge Files',
                 'h1': 'CSV to Excel: Turn a CSV Into a Workbook',
                 'tool': 'excel',
                 'app': 'CSV to XLSX',
                 'cta': 'Convert CSV to Excel, free',
                 'how': 'How to convert CSV to Excel',
                 'steps': ['Upload your .csv file (drag &amp; drop or browse).',
                           'Choose <strong>CSV → XLSX</strong> and select the delimiter (comma, semicolon, '
                           'tab, or pipe).',
                           'Download the result and compare it with your original before sharing.'],
                 'related': ['xlsx-to-csv', 'excel-to-pdf', 'merge-excel', 'pdf-to-excel', 'word-to-pdf']},
 'xlsx-to-csv': {'title': 'Excel to CSV: Convert XLSX to CSV Online Free | Forge Files',
                 'h1': 'Excel to CSV: Export a Sheet to CSV',
                 'tool': 'excel',
                 'app': 'XLSX to CSV',
                 'cta': 'Convert Excel to CSV, free',
                 'how': 'How to convert Excel to CSV',
                 'steps': ['Upload your Excel file (.xlsx or .xls).',
                           'Choose <strong>XLSX → CSV</strong> and name the sheet to export (or leave blank '
                           'for the first one).',
                           'Download the result and compare it with your original before sharing.'],
                 'related': ['csv-to-xlsx', 'excel-to-pdf', 'merge-excel', 'pdf-to-excel', 'word-to-pdf']},
 'merge-excel': {'title': 'Merge Excel: Combine Workbooks Online Free | Forge Files',
                 'h1': 'Merge Excel: Combine Multiple Workbooks',
                 'tool': 'excel',
                 'app': 'Merge Excel',
                 'cta': 'Merge Excel files, free',
                 'how': 'How to merge Excel workbooks',
                 'steps': ['Choose <strong>Merge Workbooks</strong> in the Excel tools.',
                           'Select two or more .xlsx files in the upload area.',
                           'Download the result and compare it with your original before sharing.'],
                 'related': ['excel-to-pdf', 'csv-to-xlsx', 'xlsx-to-csv', 'merge-pdf', 'pdf-to-excel']},
 'powerpoint-to-pdf': {'title': 'PowerPoint to PDF: Convert PPT to PDF Free | Forge Files',
                       'h1': 'PowerPoint to PDF: Convert Slides to PDF',
                       'tool': 'ppt',
                       'app': 'PowerPoint to PDF',
                       'cta': 'Convert PPT to PDF, free',
                       'how': 'How to convert PowerPoint to PDF',
                       'steps': ['Upload your .pptx presentation (drag &amp; drop or browse).',
                                 'Choose <strong>PPT → PDF</strong>.',
                                 'Download the result and compare it with your original before sharing.'],
                       'related': ['ppt-to-images',
                                   'merge-ppt',
                                   'pdf-to-powerpoint',
                                   'word-to-pdf',
                                   'excel-to-pdf']},
 'ppt-to-images': {'title': 'PPT to Images: PowerPoint to PNG/JPG Free | Forge Files',
                   'h1': 'PPT to Images: Each Slide as a PNG or JPG',
                   'tool': 'ppt',
                   'app': 'PPT to Images',
                   'cta': 'Convert PPT to images, free',
                   'how': 'How to convert PowerPoint to images',
                   'steps': ['Upload your .pptx presentation (drag &amp; drop or browse).',
                             'Choose <strong>PPT → Images</strong> and pick PNG or JPG.',
                             'Download the result and compare it with your original before sharing.'],
                   'related': ['powerpoint-to-pdf',
                               'merge-ppt',
                               'pdf-to-jpg',
                               'pdf-to-powerpoint',
                               'image-to-pdf']},
 'merge-ppt': {'title': 'Merge PowerPoint: Combine PPTX Online Free | Forge Files',
               'h1': 'Merge PowerPoint: Combine Presentations',
               'tool': 'ppt',
               'app': 'Merge PowerPoint',
               'cta': 'Merge PowerPoint files, free',
               'how': 'How to merge PowerPoint files',
               'steps': ['Choose <strong>Merge PPTX</strong> in the PowerPoint tools.',
                         'Select two or more .pptx files in the upload area.',
                         'Download the result and compare it with your original before sharing.'],
               'related': ['powerpoint-to-pdf',
                           'ppt-to-images',
                           'merge-pdf',
                           'pdf-to-powerpoint',
                           'merge-excel']},
 'word-to-pdf': {'title': 'Word to PDF: Convert DOCX to PDF Online Free | Forge Files',
                 'h1': 'Word to PDF: Convert DOCX to PDF',
                 'tool': 'word',
                 'app': 'Word to PDF',
                 'cta': 'Convert Word to PDF, free',
                 'how': 'How to convert Word to PDF',
                 'steps': ['Upload your Word document (.docx).',
                           'Choose <strong>Word → PDF</strong>.',
                           'Download the result and compare it with your original before sharing.'],
                 'related': ['pdf-to-word',
                             'excel-to-pdf',
                             'powerpoint-to-pdf',
                             'compress-pdf',
                             'merge-pdf']},
 'crop-pdf': {'title': 'Crop PDF: Trim PDF Page Margins Online Free | Forge Files',
 'h1': 'Crop PDF: Trim Margins From Every Page',
 'tool': 'pdf',
 'app': 'Crop PDF',
 'cta': 'Crop a PDF now, free',
 'how': 'How to crop a PDF',
 'steps': ['Upload your PDF (drag &amp; drop or browse).',
 'Choose <strong>Crop PDF</strong>, set the percentage to trim from the top, bottom, left and right, and optionally list pages.',
 'Download the result and compare it with your original before sharing.'],
 'related': ['rotate-pdf', 'extract-pdf-pages', 'remove-pdf-pages', 'compress-pdf', 'organize-pdf']},
 'remove-pdf-pages': {'title': 'Remove PDF Pages: Delete Pages Online Free | Forge Files',
 'h1': 'Remove PDF Pages: Delete Unwanted Pages',
 'tool': 'pdf',
 'app': 'Remove PDF Pages',
 'cta': 'Remove PDF pages now, free',
 'how': 'How to remove pages from a PDF',
 'steps': ['Upload your PDF (drag &amp; drop or browse).',
 'Choose <strong>Remove Pages</strong>, then click the page thumbnails to delete or type them, e.g. <code>2,4-6</code>.',
 'Download the result and compare it with your original before sharing.'],
 'related': ['extract-pdf-pages', 'organize-pdf', 'split-pdf', 'merge-pdf', 'crop-pdf']},
 'repair-pdf': {'title': 'Repair PDF: Fix a Corrupted PDF Online Free | Forge Files',
 'h1': 'Repair PDF: Recover a Damaged File',
 'tool': 'pdf',
 'app': 'Repair PDF',
 'cta': 'Repair a PDF now, free',
 'how': 'How to repair a PDF',
 'steps': ['Upload your PDF (drag &amp; drop or browse).',
 'Choose <strong>Repair PDF</strong> and start the repair attempt.',
 'Download the result and compare it with your original before sharing.'],
 'related': ['compress-pdf', 'unlock-pdf', 'pdf-to-text', 'merge-pdf', 'ocr-pdf']},
 'create-pdf': {'title': 'Create PDF: Make a PDF From Text or Blank Pages | Forge Files',
 'h1': 'Create PDF: From Text or Blank Pages',
 'tool': 'pdf',
 'app': 'Create PDF',
 'cta': 'Create a PDF now, free',
 'how': 'How to create a PDF',
 'steps': ['Open the PDF tools; no upload is needed to start.',
 'Choose <strong>Create PDF</strong>, then enter text and a title or pick a number of blank pages, and select a page size.',
 'Download the result and compare it with your original before sharing.'],
 'related': ['image-to-pdf', 'word-to-pdf', 'merge-pdf', 'pdf-page-numbers', 'watermark-pdf']},
 'annotate-pdf': {'title': 'Annotate PDF: Highlight and Add Notes Online Free | Forge Files',
 'h1': 'Annotate PDF: Highlight, Underline and Add Notes',
 'tool': 'pdf',
 'app': 'Annotate PDF',
 'cta': 'Annotate a PDF now, free',
 'how': 'How to annotate a PDF',
 'steps': ['Upload your PDF (drag &amp; drop or browse).',
 'Choose <strong>Annotate PDF</strong>, pick a type (highlight, underline, strikeout, note or redact), the page and the area in points.',
 'Download the result and compare it with your original before sharing.'],
 'related': ['sign-pdf', 'watermark-pdf', 'edit-pdf-metadata', 'protect-pdf', 'pdf-page-numbers']},
 'edit-pdf-metadata': {'title': 'Edit PDF Metadata: Change Title and Author | Forge Files',
 'h1': 'Edit PDF Metadata: Title, Author and Keywords',
 'tool': 'pdf',
 'app': 'Edit PDF Metadata',
 'cta': 'Edit PDF metadata now, free',
 'how': 'How to edit PDF metadata',
 'steps': ['Upload your PDF (drag &amp; drop or browse).',
 'Choose <strong>Edit Metadata</strong>, change the title, author, subject or keywords, or tick the option to clear existing metadata first.',
 'Download the result and compare it with your original before sharing.'],
 'related': ['protect-pdf', 'annotate-pdf', 'compress-pdf', 'sign-pdf', 'watermark-pdf']},
 'word-to-powerpoint': {'title': 'Word to PowerPoint: Convert DOCX to PPTX Free | Forge Files',
 'h1': 'Word to PowerPoint: Turn Document Pages Into Slides',
 'tool': 'word',
 'app': 'Word to PowerPoint',
 'cta': 'Convert Word to PowerPoint, free',
 'how': 'How to convert Word to PowerPoint',
 'steps': ['Upload your Word document (.docx).',
 'Choose <strong>Word → PPTX</strong> and pick a quality (96, 150 or 200 DPI).',
 'Download the result and compare it with your original before sharing.'],
 'related': ['word-to-pdf', 'pdf-to-powerpoint', 'powerpoint-to-pdf', 'merge-ppt', 'pdf-to-word']}}


# --- rendering -------------------------------------------------------------

# Replace inherited promotional claims with the maintained guidance. Keep the
# established slug/category/action contracts, including old deep links.
for _slug, _page in TOOL_PAGES.items():
    _summary, _example, _limits, _check = HELP[_slug]
    _page["lede"] = _summary
    _page["meta"] = _summary
    _page["benefits"] = []
    _page["faqs"] = [
        (f"What are the limits of {_page['app']}?", html.escape(_limits)),
        (f"How should I check the {_page['app']} result?", html.escape(_check)),
    ]
    _page["steps"][-1] = "Download the result and compare it with your original before sharing."
TOOL_PAGES["pdf-to-text"]["steps"][1] = "Choose <strong>Extract Text</strong> and set <strong>Preserve layout structure</strong> if needed."
TOOL_PAGES["ocr-pdf"]["steps"][1] = "Choose <strong>OCR PDF</strong> and select the matching available language."

# This is another name for the same tool, not a separate search landing page.
CANONICAL_ALIASES = {slug: "ocr-pdf" for slug in
                     ("make-pdf-searchable", "ocr-hindi", "ocr-marathi", "ocr-tamil", "ocr-telugu")}

def _jsonld(obj: dict) -> str:
    return '<script type="application/ld+json">\n' + json.dumps(obj, indent=2, ensure_ascii=False) + "\n</script>"


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


def _software_schema(slug: str, page: dict) -> dict:
    # NOTE: intentionally no aggregateRating/review — fabricating ratings without
    # a real, on-page, user-generated review mechanism violates Google's
    # structured-data policy and risks a manual action. Add them only once
    # genuine reviews exist. The publisher/sameAs/inLanguage fields below are
    # legitimate entity signals that help Google associate the tool with the
    # Forge Files brand and its GitHub presence.
    return {
        "@context": "https://schema.org",
        "@type": "SoftwareApplication",
        "name": page["app"] + " | " + SITE,
        "applicationCategory": "UtilitiesApplication",
        "operatingSystem": "Any (web browser)",
        "url": BASE + "/" + slug,
        "description": page["meta"],
        "inLanguage": "en",
        "isAccessibleForFree": True,
        "offers": {"@type": "Offer", "price": "0", "priceCurrency": "USD"},
        "publisher": {
            "@type": "Organization",
            "name": SITE,
            "url": BASE + "/",
            "sameAs": [GITHUB],
        },
    }


def _howto_schema(page: dict) -> dict:
    return {
        "@context": "https://schema.org",
        "@type": "HowTo",
        "name": _plain(page["h1"]),
        "step": [
            {"@type": "HowToStep", "position": i + 1, "text": _plain(s)}
            for i, s in enumerate(page["steps"])
        ],
    }


def _breadcrumb_schema(slug: str, page: dict) -> dict:
    return {
        "@context": "https://schema.org",
        "@type": "BreadcrumbList",
        "itemListElement": [
            {"@type": "ListItem", "position": 1, "name": SITE, "item": BASE + "/"},
            {"@type": "ListItem", "position": 2, "name": _plain(page["app"]),
             "item": BASE + "/" + slug},
        ],
    }


def _related_html(related: List[str], sep: str = " · ") -> str:
    links = []
    for r in related:
        r = CANONICAL_ALIASES.get(r, r)
        target = TOOL_PAGES.get(r)
        if target:
            link = '<a href="/' + r + '">' + target["app"] + "</a>"
            if link not in links:
                links.append(link)
    return sep.join(links)


def _security_section(page: dict) -> str:
    """Explain the actual processing path without promising absolute privacy."""
    slug = next(s for s, p in TOOL_PAGES.items() if p is page)
    mode = TOOLS_BY_SEO_SLUG[slug]["processing"]
    handling = ("Supported files are processed on your device. Unsupported cases may be uploaded to the server as a fallback."
                if mode == "local_or_server" else
                "This operation uploads your file over HTTPS for automated server processing.")
    return (f'<aside class="processing-note ffc-note"><h2>File handling</h2><p>{handling} '
            'Server files are temporary; download results promptly and keep your originals. '
            '<a href="/privacy">Processing, retention and analytics details</a>.</p></aside>')


# Practical instructions and examples precede FAQs and any ad slot.


def render_tool_page(slug: str) -> str:
    """Render a full HTML page for ``slug``. Returns HTML with the literal
    placeholders ``{{BASE_URL}}``/``{{ADSENSE_HEAD}}``/``{{ADSENSE_SLOT}}`` left
    intact for ``main.py`` to substitute."""
    page = TOOL_PAGES[slug]
    canonical_slug = CANONICAL_ALIASES.get(slug, slug)
    example_html = render_example(EXAMPLE_KIND[slug]) if slug in EXAMPLE_KIND else ""
    if slug == "ocr-pdf":
        example_html += ('<section id="languages"><h2>Language availability</h2>'
                         '<p>English is the free hosted OCR path and the language offered in the current free mobile app. '
                         'The private server requires premium access for Hindi, Marathi, Tamil and Telugu; '
                         'those options also need the matching OCR models. A visible language name does not guarantee access or engine availability.</p>'
                         '<p>For self-hosting, availability follows your OCR backend and model configuration. '
                         'Check recognized words in each script against the original.</p></section>')
    output_format = html.escape(TOOLS_BY_SEO_SLUG[slug]["output"])
    og_title = page["title"].split(" | ")[0]
    og_desc = page["meta"]

    steps_html = "\n".join("            <li>" + s + "</li>" for s in page["steps"])
    faq_html = ds_faq(page["faqs"])

    schema_blocks = "\n".join([
        _jsonld(_faq_schema(page["faqs"])),
        _jsonld(_software_schema(slug, page)),
        _jsonld(_howto_schema(page)),
        _jsonld(_breadcrumb_schema(slug, page)),
    ])

    return f"""<!DOCTYPE html>
<html lang="en">

<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    {SITE_VERIFY}
    <title>{_attr(page['title'])}</title>
    <meta name="description" content="{_attr(page['meta'])}">
    <link rel="canonical" href="{BASE}/{canonical_slug}">
    <meta name="robots" content="index, follow, max-image-preview:large, max-snippet:-1, max-video-preview:-1">
    <meta name="theme-color" content="#ffffff">
    <meta property="og:type" content="website">
    <meta property="og:site_name" content="{SITE}">
    <meta property="og:title" content="{_attr(og_title)}">
    <meta property="og:description" content="{_attr(og_desc)}">
    <meta property="og:url" content="{BASE}/{canonical_slug}">
    <meta property="og:image" content="{BASE}/static/og-image.png">
    <meta property="og:image:width" content="1200">
    <meta property="og:image:height" content="630">
    <meta property="og:image:alt" content="{_attr(og_title)}">
    <meta name="twitter:card" content="summary_large_image">
    <meta name="twitter:title" content="{_attr(og_title)}">
    <meta name="twitter:description" content="{_attr(og_desc)}">
    <meta name="twitter:image" content="{BASE}/static/og-image.png">
    <link rel="icon" type="image/svg+xml" href="/static/favicon.svg">
    <link rel="apple-touch-icon" href="{BASE}/static/apple-touch-icon.png">
    <link rel="preconnect" href="https://cdnjs.cloudflare.com" crossorigin>
    {DS_HEAD}
    {ADS_HEAD}
    {CF_ANALYTICS}
    {GA_ANALYTICS}
    {DATAFAST_ANALYTICS}
{schema_blocks}
</head>

<body class="ff-ds ffc">
    <a class="skip-content" href="#main-content">Skip to content</a>
    {DS_NAV}
    <main class="ffc-wrap ffc-main" id="main-content">
        {ds_breadcrumb((page['app'], ''))}
        <h1 class="ffc-h1">{page['h1']}</h1>
        <p class="ffc-lede">{page['lede']}</p>
        <p class="ffc-meta content-meta">Output: <strong>{output_format}</strong> · Free tool · Upload and processing limits apply</p>

{_upload_box(slug, page)}

        <h2 class="ffc-section-h">{page['how']}</h2>
        <ol class="ffc-steps">
{steps_html}
        </ol>

{extra_html(slug)}

{example_html}

        <h2 class="ffc-section-h">Frequently asked questions</h2>
{faq_html}

{_security_section(page)}

        <p class="ffc-meta content-meta">Guidance updated <time datetime="{CONTENT_REVIEWED}">{CONTENT_REVIEWED}</time>.
            <a href="/contact">Report an incorrect instruction or conversion problem</a>.</p>

        {ADS_SLOT}

        <h2 class="ffc-section-h">More free tools</h2>
        <div class="ffc-related">{_related_html(page['related'], sep='')}</div>
    </main>
    {DS_FOOT}
    {CONSENT_BANNER}
    {FUNNEL_BEACON}
    {DS_THEME_JS}
    <script src="/static/seo-upload.js?v={ASSET_V}" defer></script>
</body>

</html>
"""


def render_404_page() -> str:
    """Branded hard-404 body. Served with HTTP status 404 by main.py."""
    popular = ["merge-pdf", "compress-pdf", "pdf-to-word", "unlock-pdf",
               "heic-to-jpeg", "image-to-pdf", "excel-to-pdf", "word-to-pdf"]
    links = "\n".join(
        '            <a href="/' + s + '">' + TOOL_PAGES[s]["app"] + "</a>"
        for s in popular if s in TOOL_PAGES
    )
    return f"""<!DOCTYPE html>
<html lang="en">

<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Page not found (404) | {SITE}</title>
    <meta name="robots" content="noindex">
    <link rel="icon" type="image/svg+xml" href="/static/favicon.svg">
    <link rel="apple-touch-icon" href="{BASE}/static/apple-touch-icon.png">
    {DS_HEAD}
    {GA_ANALYTICS}
    {DATAFAST_ANALYTICS}
</head>

<body class="ff-ds ffc">
    {DS_NAV}
    <main class="ffc-wrap ffc-main ffc-404" id="main-content">
        {ds_breadcrumb(("404", ""))}
        <h1 class="ffc-h1">404: Page not found</h1>
        <p class="ffc-lede">That page doesn't exist (or moved). All {SITE} tools are free, with no signup and files
            deleted automatically. Try one of these popular tools:</p>
        <div class="ffc-related">
{links}
        </div>
        <p class="ffc-cta-row"><a class="ff-btn primary" href="/">Go to all Forge Files tools</a></p>
    </main>
    {DS_FOOT}
    {DS_THEME_JS}
</body>

</html>
"""


# Slug -> sitemap priority. Home is handled separately in main.py.
def all_tool_slugs() -> List[str]:
    return list(TOOL_PAGES.keys())
