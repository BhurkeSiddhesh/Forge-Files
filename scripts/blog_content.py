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
    "how-to-resize-an-image-for-upload": {
        "title": "Resize Image for Upload Limits (KB & Pixels) | " + SITE,
        "meta": "Learn the difference between pixel dimensions and file size (KB), and how to safely resize or crop your image to meet strict portal requirements.",
        "h1": "How to resize an image for strict upload limits",
        "dek": "When a portal rejects your photo because it's 'too large', it could mean the file size (KB) or the physical dimensions (pixels). Here is how to fix both without stretching your image.",
        "primary_tool": "resize-image", "related": ["crop-image", "compress-image", "heic-to-jpeg"],
        "date": "2026-10-26", "published": "2026-10-26",
        "body": [
            ("1. Find out what the portal actually wants", "<p>Upload systems often have two distinct limits: <strong>Resolution (pixels)</strong>, such as 'must be exactly 600x600 pixels', and <strong>File Size (bytes)</strong>, such as 'must be under 200 KB'. Fixing one does not automatically fix the other. When you see an error that an image is 'too large', you must first diagnose which limit you have exceeded. Most government forms, job applications, and university portals will explicitly state these limits in their guidelines, usually hidden in small print near the upload button.</p><p>If you only shrink the KB size, your image dimensions might still be too wide, leading to immediate rejection by the portal's automated cropping tool. Conversely, if you only resize the pixels to the exact required dimensions, a complex photo with many colors and sharp details might still hold too much data and exceed the KB limit. The key to a successful upload is ensuring your image satisfies both requirements simultaneously.</p><p>Before you begin modifying your original photo, always create a duplicate. Modifying an image is a destructive process, meaning you lose the original data. Keeping an unedited backup ensures you can always start over if you make a mistake or need a different size in the future. For example, if you need a lower resolution for an initial application but the higher resolution for a later background check, you will need that original file.</p>"),
            ("2. Fix the shape first (Aspect Ratio)", "<p>If a portal asks for a square photo (like a passport scan) but your original is a wide rectangle (like a landscape photo), do not just type the new dimensions into a resizer. That will squash or stretch your image, which can cause it to be rejected. Stretching distorts faces and text, which is an immediate red flag for identity verification systems. A squashed face in a passport photo is not a valid representation.</p><p>Instead, use a <a href=\"/crop-image\">Crop tool</a> first to cut away the extra background until the shape matches what the portal expects. Only then should you resize it. For example, if you need a 2x2 inch passport photo, crop the image to a perfect square first. This guarantees that when you later resize it to exactly 600x600 pixels, the image will scale down proportionally without any distortion.</p><p>Pay attention to what you are cropping out. Ensure the subject remains centered and that essential details, such as the top of the head or the bottom of the chin in a portrait, are not accidentally removed. Proper framing is just as important as technical dimensions.</p>"),
            ("3. Resize the pixels", "<p>Once the shape is correct, you can adjust the dimensions. Open the <a href=\"/resize-image\">Resize Image</a> tool and upload your file.</p><p>If the portal gave you exact numbers (e.g. '800x600'), select the <strong>Dimensions</strong> mode and enter them. The tool will mathematically recalculate the pixels to perfectly match the target size. If you prepared the aspect ratio correctly in the previous step, the image will scale perfectly without distortion.</p><p>If it only said 'too large', try using the <strong>Percentage</strong> mode and scale it down to 50% to see if that works. Scaling by percentage is a safer guess when exact dimensions are not provided, as it guarantees the aspect ratio is preserved. Always review the final image at 100% zoom to ensure it remains legible and clear after shrinking. Keep iterating until the portal accepts it.</p>"),
            ("4. Target the file size (KB)", "<p>If your pixels are correct but the file size is still too heavy, you need to compress the image data. The <a href=\"/resize-image\">Resize Image</a> tool has a <strong>Target File Size</strong> mode specifically for this purpose.</p><p>Enter the maximum KB allowed (e.g. 190 if the limit is 200 KB). The tool will attempt to iteratively compress the image quality to hit that target while keeping your new pixel dimensions intact. This process reduces the file size by finding and discarding redundant visual information. While it is lossy compression, a target like 200 KB for a standard web image usually results in an imperceptible difference to the human eye.</p><p>If the resulting image looks heavily pixelated or blocky, you might be trying to compress it too much. In that case, you may need to reconsider the physical pixel dimensions. A 4000x3000 pixel photo compressed to 100 KB will look terrible, but an 800x600 photo compressed to 100 KB will look perfectly clear.</p>"),
            ("5. Important considerations before uploading", "<p>Before you finally hit submit on that portal, do a final review of your processed image. Open the file on your computer and zoom in on any text or important features. If a human reviewer cannot read the text or recognize the face, it doesn't matter that the file size is perfect—it will be rejected. <strong>Readability always trumps file size.</strong></p><p>Furthermore, ensure the final file format is accepted by the portal. JPEG is the most universally accepted format for photos, while PNG is better for graphics or documents with sharp text and solid colors. If you started with a HEIC photo from an iPhone, you must convert it first, as most older systems cannot read HEIC files natively. You can use the <a href=\"/heic-to-jpeg\">HEIC to JPEG</a> tool if necessary.</p><p>Finally, always process sensitive documents locally if possible. Using web-based tools that process images directly in your browser ensures your private documents never leave your device.</p>"),
        ],
        "faqs": [("Will resizing an image make it blurry?", "Enlarging an image (scaling over 100%) will make it blurry because the computer has to guess the missing pixels. Shrinking an image keeps it sharp, but aggressive KB compression can introduce 'artifacts' or blocky areas. Always check the final image at 100% zoom."),
                 ("Why did the KB size stay the same after resizing?", "If you only changed the dimensions slightly, the underlying image format might not have needed less data. Use a specific Target KB or use a compression tool to force a smaller file size."),
                 ("Should I use JPEG or PNG?", "JPEG is best for photographs as it compresses complex color gradients efficiently. PNG is better for scanned documents with sharp text, logos, or screenshots, as it does not introduce compression artifacts around sharp edges."),
                 ("Can I fix an image that was stretched?", "Usually, no. Once an image is stretched and saved, the original pixel proportions are lost. It is much easier to start over from the original backup and crop the aspect ratio correctly before resizing.")],
    },
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
    "how-to-extract-tables-from-pdf-to-excel": {
        "title": "How to Extract Tables from a PDF to Excel | " + SITE,
        "meta": "Extract tables from a PDF to Excel. Compare copying and pasting manually to using an automated tool that preserves borders, typed cells and alignment.",
        "h1": "Extract Tables from a PDF to Excel Without Losing Formatting",
        "dek": "Copying a table straight out of a PDF often merges columns and breaks numbers. Here is how to keep your data organized and properly formatted.",
        "primary_tool": "pdf-to-excel", "related": ["merge-excel", "csv-to-xlsx", "xlsx-to-csv"],
        "date": "2026-10-08", "published": "2026-10-08",
        "body": [
            ("The problem with PDF tables", "<p>A PDF is designed to look exactly the same on every device, whether it is a phone screen or a printed page. To achieve this, a PDF does not store structured data like an Excel spreadsheet does. Instead, it stores a list of instructions: draw a line here, place the letter 'A' at these exact coordinates, and fill this rectangle with gray.</p><p>When you see a table in a PDF, your brain recognizes the grid. But the computer only sees isolated lines and text floating in space. Because the document has no concept of 'rows' or 'columns', highlighting and copying the text usually results in a jumbled mess when you paste it into a spreadsheet. Numbers merge together, headers misalign, and empty cells are completely ignored.</p><p>Fixing this manually is not just frustrating—it is risky. Rebuilding a financial statement or inventory list by copying and pasting invites typographical errors that can invalidate the entire dataset. You need a method that actually understands the visual grid and reconstructs it into rows and columns.</p>"),
            ("Method 1: Excel's built-in Data from Picture", "<p>If you only have a small table—perhaps a dozen rows—you can use a relatively new feature built directly into modern versions of Microsoft Excel. Rather than pasting text, you take a screenshot of the PDF table and ask Excel to analyze the image.</p><ol><li>Take a clear screenshot of the table in the PDF.</li><li>In Excel, go to the <strong>Data</strong> tab.</li><li>Click <strong>From Picture</strong> and select your screenshot.</li><li>Wait for Excel to analyze the image.</li></ol><p>Excel will attempt to recognize the text and the grid, presenting a preview where you can review any low-confidence cells. This method is fast, but it has a significant drawback: it relies entirely on optical character recognition (OCR), even if the original PDF contained perfectly selectable digital text. OCR is inherently probabilistic. It might mistake a '1' for an 'l', an '8' for a 'B', or a '0' for an 'O'. You must carefully proofread every single cell to ensure your data is accurate.</p>"),
            ("Method 2: Excel Power Query", "<p>For larger documents or recurring tasks, Excel's Power Query provides a more robust solution, though it comes with a steeper learning curve. Power Query can connect directly to a PDF file and extract tabular data.</p><ol><li>In Excel, navigate to the <strong>Data</strong> tab.</li><li>Select <strong>Get Data</strong> &gt; <strong>From File</strong> &gt; <strong>From PDF</strong>.</li><li>Locate and select your PDF file.</li></ol><p>The Navigator window will open, displaying a list of tables and pages Excel found in the document. You can select the tables you need and click <strong>Load</strong> to import them, or <strong>Transform Data</strong> to clean up the formatting before loading. Power Query is excellent for clean, digitally created PDFs with clear borders. However, it can struggle with borderless tables (where columns are aligned only by whitespace) or complex layouts with merged cells. It also requires the desktop version of Excel for Windows.</p>"),
            ("Method 3: Dedicated automated extraction", "<p>When you need to extract tables quickly without installing software or configuring queries, a dedicated conversion tool is often the best approach. An automated extractor analyzes both the drawn lines (ruled tables) and the alignment of the text (borderless tables) to reconstruct the grid.</p><ol><li>Open <a href=\"/pdf-to-excel\">PDF to Excel</a> and choose your document.</li><li>Wait for the analysis to finish.</li><li>Download the spreadsheet and open it. Check that the columns match your original document.</li></ol><p>Forge Files processes supported documents entirely on your device, meaning the file never leaves your browser. This is particularly important for financial statements or confidential reports. Furthermore, the on-device extraction is smart enough to preserve data types. A price formatted as <code>$1,250.00</code> or a percentage like <code>12.5%</code> is recognized and stored as a proper numeric value in Excel, not just plain text. This allows you to immediately sum columns or apply formulas without having to clean the data first.</p>"),
            ("Dealing with scanned documents", "<p>All the methods discussed above assume your PDF contains digital text. If your PDF is a scanned document—essentially a photograph of a piece of paper—the extraction process becomes more complicated. A scanned PDF does not contain text; it only contains pixels. Automated extractors cannot read the numbers or see the grid lines.</p><p>If you have a scanned PDF, you must first run it through <a href=\"/ocr-pdf\">OCR PDF</a> (Optical Character Recognition) to add an invisible layer of searchable text over the image. Once the text is readable, you can attempt to extract the tables. Alternatively, you may need to rely on specialized OCR software designed specifically for tabular data extraction from images. Always thoroughly verify the output of any OCR process, as image quality, skew, and blur can significantly impact accuracy.</p>"),
            ("Best practices for verifying your data", "<p>No automated tool is flawless. The complex and unstructured nature of PDF files means you should always verify extracted data before using it in a professional setting.</p><ul><li><strong>Check the totals:</strong> If your table includes a 'Total' row, use an Excel formula to sum the extracted column and verify it matches the extracted total. This is the fastest way to catch missing or misinterpreted numbers.</li><li><strong>Inspect merged cells:</strong> Merged headers or data cells are notoriously difficult to extract. Check that the data hasn't been duplicated across columns or shifted to the wrong row.</li><li><strong>Look for split tables:</strong> If a table spans multiple pages in the PDF, it will often be extracted as multiple separate tables or sheets in Excel. You will need to manually append the data.</li><li><strong>Verify number formats:</strong> Ensure that decimal points and thousands separators have been interpreted correctly, especially if the PDF uses European formatting (e.g., <code>1.250,00</code>).</li></ul><p>By understanding the limitations of the PDF format and choosing the right extraction method for your document, you can save hours of manual data entry while maintaining the integrity of your spreadsheets.</p>")
        ],
        "faqs": [("Why did all my data paste into one column?", "PDFs do not store grid structures. They only store the visual position of text. When you paste into Excel, it treats the visual lines of text as single paragraphs, resulting in a single column."),
                 ("Can I extract a table that doesn't have borders?", "Yes, advanced tools can extract 'borderless' tables by analyzing the whitespace and alignment of the text to infer the column structure."),
                 ("Can I extract just one table from a large report?", "Yes. If you only want one page, use <a href=\"/extract-pdf-pages\">Extract Pages</a> first, then convert only that page to Excel. This saves time and avoids extracting irrelevant data.")],
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
