"""Authored, tool-specific use cases and limitations. No quality guarantees."""
from __future__ import annotations

from html import escape

# Summary, example, limitation, result check. Keep these tied to actual controls.
HELP: dict[str, tuple[str, str, str, str]] = {
    "unlock-pdf": (
        "Save an unencrypted copy of a PDF you are authorized to modify, using its current opening password when required.",
        "For a statement you read repeatedly, enter the existing password and keep the downloaded copy in a protected folder. It will no longer ask for that password.",
        "This does not recover a forgotten opening password. Removing encryption also removes that protection from the new copy; it does not grant permission to redistribute it.",
        "Open the result in a separate viewer. If the password is rejected, check case, whitespace and the selected file. Keep the encrypted original until you have checked every page."),
    "pdf-to-word": (
        "Reconstruct a PDF as an editable DOCX. Standard mode uses existing text; scans require an available OCR path.",
        "Try the workshop report below. Open its converted Word document and edit a table quantity; a readable preview alone does not prove editability.",
        "PDF stores positioned content rather than the original Word structure. Columns, equations and page breaks may change. Standard conversion does not recognize scan images; AI Layout Recovery depends on server availability.",
        "Compare numbers, table cells and reading order. If the result is empty or contains images, check whether text can be selected in the source. Image-only pages need OCR when available."),
    "compress-pdf": (
        "Reduce PDF size by optimizing structure and eligible embedded images. Low, Medium and High trade image detail for size.",
        "Start with Low for a scanned application. Compare downloaded bytes with the portal limit before trying Medium. Inspect fine print and signatures at 200% zoom after each attempt.",
        "A scan is a picture of text, so image compression can soften its words. Already optimized files may shrink little. A target size or reduction percentage is not guaranteed.",
        "Keep the original for archival storage or printing. If the output is still too large, remove unnecessary pages or use an accepted file-sharing method instead of repeatedly compressing it."),
    "extract-pdf-pages": (
        "Keep selected pages in one new PDF using their physical positions, starting at page 1.",
        "For a seven-page file, 1,3,5-7 keeps five pages. A cover counts as page 1 even when printed numbering starts later. The example below keeps report pages 1 and 3.",
        "Extraction does not redact content on retained pages. Use Split PDF for separate output files. A page outside the source range cannot be extracted.",
        "Count the output pages and inspect the first and last retained pages. Use positions in the viewer rather than numbers printed in the footer."),
    "pdf-to-text": (
        "Export a PDF's words as plain text. Recognition of image-only pages depends on an available OCR engine.",
        "Try selecting a sentence before processing. Use Preserve layout structure when spacing matters, then compare the TXT reading order with the original columns.",
        "TXT cannot preserve images, fonts or editable table cells. Scans may require OCR; unusual character encoding can produce missing characters.",
        "Check accents, totals and headings. If columns interleave, extract the sections you need separately or try PDF to Word. Review OCR text against the image."),
    "ocr-pdf": (
        "Add a searchable text layer to a scanned PDF while retaining its page images. Results depend on scan quality and OCR availability.",
        "Choose a clearly printed scan and the matching available language. Search the result for a word mid-paragraph, then paste a sentence into a text editor.",
        "OCR is an estimate, not a verified transcription. Handwriting, skew and faint marks reduce reliability. Searchability does not reconstruct Word layout or certify accessibility compliance.",
        "Compare names, dates and decimal points with the original. Rotate sideways pages first. If the engine is unavailable, retry later or use a local OCR tool."),
    "make-pdf-searchable": (
        "Make an image-only PDF searchable using the OCR PDF tool, which keeps the visible page image.",
        "Test your viewer's search first. If a visible word cannot be found or selected, the page may be a scan requiring recognition.",
        "This uses the same operation as OCR PDF. It does not improve the scan or verify recognized words. The chosen language must be available on the server.",
        "Search several pages and copy a sentence. If words are inaccurate, obtain a sharper scan and check the selected language."),
    "merge-pdf": (
        "Combine two or more PDFs in the displayed file order without adding a watermark.",
        "Select the three-page report followed by the one-page appendix below. The output should have four pages with the appendix last. Check the file list before starting.",
        "Merging does not standardize paper sizes or reduce image size. Locked files may need unlocking first. Modifying a digitally signed document can invalidate its signature.",
        "Check the total page count and each boundary between files. Use Organize PDF to change page order afterward. Upload and processing limits still apply."),
    "split-pdf": (
        "Create a ZIP of separate PDFs: one per page, groups of N pages, or custom ranges.",
        "For six pages, custom ranges 1-2,3-6 create two PDFs inside the ZIP. Every page instead creates six files. Extract Pages is the choice for one combined output.",
        "Splitting uses physical page positions including covers, not chapter detection. Open the ZIP before trying to use its individual PDFs.",
        "Unzip and inspect the start and end of each part. Check every range ends within the source page count."),
    "rotate-pdf": (
        "Save right-angle rotations into a new PDF for the whole document or selected pages.",
        "If only page 2 is sideways, enter 2 and choose 90°. Other pages stay as they were. A reader's temporary view rotation may not be saved into the file.",
        "This does not deskew slightly tilted scans, crop margins or recognize text. Existing page rotation affects which angle gives the desired result.",
        "Close the preview and reopen the downloaded PDF. Inspect both portrait and landscape pages. Rescan or use an image editor for small-angle skew."),
    "protect-pdf": (
        "Encrypt a PDF with an opening password and optional reader permissions.",
        "Set an opening password and try opening the result without it before sharing. Send the password through a separate communication channel.",
        "Copying and printing restrictions depend on the reader honoring them and cannot prevent screenshots. The tool cannot recover a forgotten password.",
        "Test a wrong password and the correct one in another viewer. Keep an accessible original securely and check encryption support if a recipient cannot open it."),
    "watermark-pdf": (
        "Place a text watermark on PDF pages with a selected position and opacity.",
        "For a review copy, use DRAFT at low opacity. Check a dense page as well as a blank page so the mark does not hide totals or instructions.",
        "A watermark is a visual label, not encryption, redaction or proof of ownership. It may be removable and does not hide underlying information.",
        "Inspect portrait and landscape pages for clipping. Reduce opacity or change position if content is obscured. Use password protection separately for access control."),
    "pdf-to-jpg": (
        "Render PDF pages as JPG or PNG images packaged in a ZIP.",
        "Try 150 DPI for screen previews or 300 DPI for more detail at a larger size. PNG suits fine diagrams; JPG often suits photographic pages.",
        "The result is pixels: words are not selectable and links are not interactive. High resolutions use more memory and are subject to render limits.",
        "Extract the ZIP, count its images and inspect small labels. Use PDF to Text or Word if you need editable words. Lower DPI if the render exceeds limits."),
    "pdf-page-numbers": (
        "Add visible page numbers with a chosen position, format, starting value and number of leading pages to skip.",
        "For a report with one cover, skip one page and start at 1. Compare the first numbered page and last page to catch an offset.",
        "This adds text; it does not remove existing numbers or change viewer page-label metadata. Existing footers may overlap the new numbers.",
        "Inspect long footnotes and different paper sizes. Choose another position if text collides and confirm the cover remains unnumbered."),
    "pdf-to-excel": (
        "Extract detected tables into an XLSX workbook for checking and editing.",
        "Start with a digital PDF with clear rows and columns. Compare its first row, last row and a total with the output before calculating.",
        "PDF tables are not spreadsheets. Scans, merged cells and irregular columns can be reconstructed incorrectly. Original formulas cannot be recovered from displayed values.",
        "Check decimal separators, negative signs and leading zeros. If no table is found, check for selectable text and regular columns. Use the original spreadsheet when available."),
    "pdf-to-powerpoint": (
        "Place a rendered image of each PDF page onto a PowerPoint slide.",
        "Convert a PDF handout to present it, then add your own annotations on top of the page images in PowerPoint.",
        "Text and charts inside each image do not become editable slide objects. Animations, slide masters and speaker notes cannot be recovered from a PDF.",
        "Check slide count, cropping and small text in slideshow mode. Obtain the original presentation if you need to edit individual objects."),
    "pdf-to-epub": (
        "Rebuild extractable PDF content into a reflowable EPUB for an ebook reader.",
        "Try a simple single-column PDF. Open the EPUB at two font sizes and check reading order instead of expecting the same page boundaries.",
        "PDFs lack reliable chapter structure. Equations, columns and scans may convert poorly. Reflow changes pagination and is not a faithful page-image archive.",
        "Check chapter starts, captions and text around images. Image-only scans need usable recognized text first. Keep the PDF when exact layout is essential."),
    "sign-pdf": (
        "Place an image of your signature onto a selected PDF page and position.",
        "Use a transparent PNG cropped close to the signature. Select the physical page number and inspect the output against the signature line.",
        "This is a visual stamp, not a certificate-based digital signature, identity verification or tamper-evident signing service.",
        "Check size, background and placement at full zoom. Ask the recipient which signing method they accept; use a certificate-based service when that is required."),
    "organize-pdf": (
        "Reorder, omit or duplicate PDF pages by entering their positions in the desired output order.",
        "For three pages, 3,1,2 puts page 3 first. Enter 1,3 to omit page 2 or 1,1,2 to repeat page 1. Numbering starts at 1.",
        "This uses a typed list, not a visual sorter or automatic content detection. Omitting a page affects only the new copy and does not redact retained content.",
        "Count the output pages against your list and inspect them in order. Use Rotate PDF separately if orientation also needs correction."),
    "heic-to-jpeg": (
        "Convert a supported HEIC or HEIF still image into JPEG for compatible apps and upload forms.",
        "Try the synthetic sample below, using JPEG quality 95. Inspect colors, fine edges and orientation before converting important photographs.",
        "JPEG is lossy. This is not a Live Photo video export or a guarantee of HDR fidelity. A JPG may be larger than its HEIC source.",
        "Keep the HEIC original. Embedded EXIF and ICC metadata may be preserved, so conversion is not metadata removal. Check the actual file format if decoding fails."),
    "resize-image": (
        "Change image dimensions by pixels or percentage, or try an approximate target file size.",
        "For a 600-pixel-wide upload, enter the width and preserve aspect ratio unless both dimensions are mandatory. Check the downloaded dimensions.",
        "Pixel dimensions and byte size are separate requirements. A target KB size is not guaranteed for every image. Enlarging cannot restore missing detail.",
        "Check dimensions, format and bytes together. Review a portal's minimum resolution and aspect ratio before reducing quality further."),
    "image-to-pdf": (
        "Place image content onto PDF pages to make a document a PDF viewer can open.",
        "Choose a clear JPG or PNG, set page size and orientation, and inspect the margins. Convert unsupported HEIC input to JPG first.",
        "An image inside a PDF is still an image: its words do not become searchable. Aspect ratio affects fit and high-resolution pictures can make large PDFs.",
        "Inspect every edge for cropping and check readability. Use OCR on the resulting PDF when available if you need searchable text."),
    "compress-image": (
        "Re-encode an image at a chosen quality to reduce byte size when possible.",
        "Lower quality gradually on a copy. Compare facial detail, small text and sharp edges at 100% zoom rather than in a thumbnail.",
        "Already compressed images may shrink little. Repeated lossy saves accumulate artifacts. Transparency and metadata behavior depend on format and processing path.",
        "Compare downloaded bytes with the original. Resize unnecessarily large dimensions instead of repeatedly compressing. Keep a lossless original for future edits."),
    "convert-image": (
        "Convert supported images among JPEG, PNG and WebP formats.",
        "Choose PNG for transparent artwork or JPG for widely supported photo uploads. Test a transparent logo before converting other artwork.",
        "Renaming an extension does not convert data. JPEG cannot store transparency and lossy formats may change detail. Decoder support varies by browser and server.",
        "Check the output's actual type and inspect transparent edges against both light and dark backgrounds. Keep PNG or supported WebP when alpha transparency matters."),
    "crop-image": (
        "Keep a rectangular area of an image using a crop selection and optional aspect ratio.",
        "Use a 1:1 selection for a square profile image. Cropping selects which pixels remain; resizing changes their dimensions.",
        "Cropping cannot recover missing pixels and is not a general metadata-removal tool. A strict upload portal may still require resizing afterward.",
        "Inspect all four edges and check the output dimensions. Return to the original for a wider crop if the subject is clipped."),
    "rotate-image": (
        "Turn an image by a right angle and save its corrected orientation in a new file.",
        "Choose 90° for a sideways photo or 180° for an upside-down one, then open it in another viewer.",
        "This does not straighten a slightly slanted horizon or correct perspective. Lossy output formats can change detail during saving.",
        "Check orientation, width and height after reopening. Use a perspective or crop editor for camera-angle problems rather than right-angle rotation."),
    "watermark-image": (
        "Draw a text watermark onto an image at a chosen position, size and opacity.",
        "Place a short attribution near a clear edge and inspect the output at full size. Contrast changes between light and dark parts of a photograph.",
        "The mark changes pixels and can cover detail. It does not prevent copying, prove ownership or guarantee resistance to removal.",
        "Reduce text size if it clips at an edge. Keep an unmarked original and move the watermark if it covers the subject."),
    "excel-to-pdf": (
        "Render spreadsheet sheets into PDF pages for a static view of the workbook.",
        "Try a sheet with headings, quantities and a total. Compare wide columns and long cell contents with the spreadsheet.",
        "Rendering is best effort: charts, merged cells, fonts and print layouts can differ. A PDF does not retain interactive formulas or filters.",
        "Review every sheet and its rightmost columns. For strict print fidelity, compare a PDF export from the original spreadsheet application."),
    "csv-to-xlsx": (
        "Import delimited text into an XLSX workbook using the delimiter that matches the CSV.",
        "If the first line is item;quantity;unit, select semicolon. If records land in one column, check the delimiter.",
        "CSV has no reliable data-type schema. Leading zeros, date-like values, encoding and quoted separators need checking; intended types cannot always be inferred.",
        "Compare row and column counts, non-English characters and an identifier such as 00123. Inspect quoted values containing separators before using the workbook."),
    "xlsx-to-csv": (
        "Export a selected workbook sheet to a plain CSV file for another system.",
        "Select the required sheet. Inspect the CSV in a text editor as well as a spreadsheet app, checking separators and the first and last records.",
        "CSV cannot preserve multiple sheets, styling or charts. Formula results depend on available calculated values; CSV is not a lossless workbook backup.",
        "Confirm the receiving system's delimiter and encoding requirements. Keep the XLSX original and verify dates and identifiers before importing."),
    "merge-excel": (
        "Collect sheets from multiple XLSX workbooks into one workbook.",
        "Select separate monthly files and inspect their sheets after merging. This collects sheets, rather than joining matching rows or reconciling records.",
        "Formulas, external links, charts and duplicate sheet names need review. Combining workbooks does not standardize columns or remove duplicate records.",
        "Compare sheet count and important totals with each source. For one combined data table, standardize columns and use a tool designed to append rows."),
    "powerpoint-to-pdf": (
        "Render a PPTX presentation into static PDF pages for viewing and printing.",
        "Compare a chart slide, an image slide and a slide with unusual fonts before sharing the PDF handout.",
        "Animations, transitions and interactive playback are omitted. Fonts, SmartArt, gradients and layout can differ by renderer.",
        "Check slide count and text wrapping. Compare an export from the original presentation application when exact appearance is essential."),
    "ppt-to-images": (
        "Render presentation slides as individual PNG or JPG images in a ZIP.",
        "PNG suits text-heavy diagrams; JPG often suits photographic slides. Extract the ZIP and inspect its smallest labels.",
        "Images flatten slide objects. Words are not editable, links are not clickable and animations are omitted. Unsupported fonts or slide features can change appearance.",
        "Count images and compare their edges with the slides. Use images exported by the original application if rendering differs materially."),
    "merge-ppt": (
        "Append slides from several PPTX presentations into one deck.",
        "Select an introduction deck followed by the main report. Inspect their boundary and run the result in slideshow mode before presenting.",
        "Themes, slide masters, linked media and complex objects may not transfer exactly. Merging does not unify typography or remove duplicate title slides.",
        "Compare slide count and review charts and images from each source. Keep separate decks to recover any object that transfers incorrectly."),
    "word-to-pdf": (
        "Convert a DOCX document to fixed PDF pages for sharing and printing.",
        "Inspect the final lines, page breaks, tables and headers of a CV or report after conversion. The PDF freezes the converter's rendered layout.",
        "Missing fonts and complex Word features can shift layout. Creating a PDF does not make content impossible to edit or certify an archival or accessibility standard.",
        "Compare page count and inspect another viewer. Simplify the DOCX or export from the original authoring application if layout differs."),
}

_LANGUAGES = {
    "ocr-hindi": ("Hindi", "Devanagari", "a printed Hindi notice", "vowel marks, joined letters and names"),
    "ocr-marathi": ("Marathi", "Devanagari", "a printed Marathi bill", "Marathi-specific letters, names and amounts"),
    "ocr-tamil": ("Tamil", "Tamil", "a printed Tamil notice", "vowel signs, letter combinations and numerals"),
    "ocr-telugu": ("Telugu", "Telugu", "a printed Telugu form", "vowel signs, joined letters and reference numbers"),
}
for _slug, (_language, _script, _example, _checks) in _LANGUAGES.items():
    HELP[_slug] = (
        f"Add searchable {_script} text to a scanned {_language} PDF when the server's {_language} OCR option is available.",
        f"Start with {_example}, select {_language}, and search for a visible word after processing. Copy a sentence into a Unicode-capable editor.",
        "A language selection does not guarantee recognition accuracy. Mixed scripts, stamps, handwriting and low-resolution scans need manual checking. Unavailable engines produce an error, not a verified transcription.",
        f"Compare {_checks} with the scan. Check a word on each page. Keep the original and use a qualified reviewer when transcription errors would matter.",
    )

HELP.update({
    "crop-pdf": (
        "Trim a percentage from the top, bottom, left and right edges of every page, or only the pages you list.",
        "To cut a scanner's dark border, trim 3% from each edge and check the page-1 preview. Enter 1,3-5 to leave other pages untouched.",
        "Cropping changes the visible page area and is not redaction, so content outside it may remain in the file. Margins are percentages of the page, not exact millimetres.",
        "Open the downloaded file in a separate viewer and check text close to each edge on several pages. Keep the original in case a margin was cut too far."),
    "remove-pdf-pages": (
        "Delete selected pages by clicking their thumbnails or typing positions such as 2,4-6, and save the rest as a new PDF.",
        "To drop a blank page and an ad page from a ten-page file, type 3,7. Numbering is the physical position, starting at 1, not a printed page number.",
        "Removing a page affects only the new copy and is not redaction of content on pages you keep. A page outside the document range cannot be removed.",
        "Count the output pages against the original minus the removed ones, then check the pages on either side of each removal."),
    "repair-pdf": (
        "Attempt to rebuild a damaged PDF structure and save a recovered copy. Repair is best-effort and has no guaranteed outcome.",
        "Use it when a viewer reports a damaged cross-reference table or refuses to open a download that was interrupted. Check whether every page is present in the result.",
        "A file cut off mid-transfer cannot regain the bytes it lost, and heavily corrupted files may fail outright. The repaired copy may drop broken objects.",
        "Open the repaired file in two viewers and page through it. If content is missing, download the original again from its source, and keep the damaged file."),
    "create-pdf": (
        "Create a new PDF from typed text with a title, or generate blank pages, in a page size you choose.",
        "Paste a short note, set a title and choose A4 to get a plain printable document. Choose blank pages when you need a fixed number of empty sheets to combine later.",
        "Text is set in a plain style without rich formatting, images or tables. Use Word to PDF to keep the layout of a formatted document. The blank page count is limited.",
        "Open the PDF and check line breaks, accented characters and the total page count before sharing it."),
    "annotate-pdf": (
        "Add a highlight, underline, strikeout, sticky note or redaction rectangle to a chosen page and area, then save a new PDF.",
        "To highlight a sentence, choose Highlight, enter the page, and give the area as x0,y0,x1,y1 in points. A sticky note takes its text from the note field.",
        "Positions are typed as coordinates in points, not drawn with a mouse, so placement may take a trial. Check redaction output carefully before relying on it to remove sensitive content.",
        "Zoom in on the annotated page and check that the mark covers the intended words. For redaction, try to select or search the covered text in the downloaded file."),
    "edit-pdf-metadata": (
        "Change the title, author, subject and keywords stored in a PDF's document properties, or clear existing metadata first.",
        "Set a clear title before uploading a report to a portal, since viewers and search tools often show the title property instead of the file name.",
        "This edits document properties only. It does not change page content, and clearing metadata does not remove names or text printed on the pages themselves.",
        "Reopen the downloaded file and check its properties dialog. Keep the original in case you need the previous values."),
    "word-to-powerpoint": (
        "Turn each page of a DOCX into a presentation slide, at 96, 150 or 200 DPI.",
        "Choose 150 DPI for a balanced result. Each page becomes a slide image, so use 200 DPI when small print must stay readable on screen.",
        "Slides are page images, so text and tables are not editable shapes in PowerPoint. Higher quality makes a larger file. Document animations and comments do not carry over.",
        "Check slide count, order and small text in slideshow mode. Use Word to PDF when you only need a faithful fixed-layout copy."),
})


EXAMPLE_KIND = {"compress-pdf": "compression", "pdf-to-word": "word", "heic-to-jpeg": "heic",
                "merge-pdf": "pages", "extract-pdf-pages": "pages"}


def _render(slug: str) -> str:
    _, example, _, _ = HELP[slug]
    return f'<h2>A practical starting point</h2><p>{escape(example)}</p>'


EXTRA = {slug: _render(slug) for slug in HELP}


def extra_html(slug: str) -> str:
    return EXTRA.get(slug, "")
