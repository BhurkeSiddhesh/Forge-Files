// On-device PDF text extraction (migration work package 03).
//
// Mirrors pdf_utils.py::extract_text_from_pdf for PDFs that already carry a
// text layer, using pdf.js text content. Output format is the server's:
//
//   --- Page 1 ---
//   <page text>
//
//   --- Page 3 ---
//   <page text>
//
// Pages with no text are left out, and a document with no text at all yields
// "(No text found in document)". A page that has no text but does contain
// images is a scan. The server runs OCR on those; this handler cannot, so it
// declines (reason `ocr_required`) and the user is asked before the file goes
// to the server. It never returns an empty result for a scan.
(function () {
    'use strict';

    var L = window.ffLocal;
    if (!L) return;

    var MIB = 1024 * 1024;
    var BUDGET = {
        desktop: { bytes: 100 * MIB, pages: 1000 },
        mobile: { bytes: 25 * MIB, pages: 300 },
    };

    /**
     * Turn one page's pdf.js text items into text.
     *
     * pdf.js marks the end of each visual line with `hasEOL`; a change in
     * baseline without that mark also starts a new line. With `layout` true a
     * vertical gap larger than about one extra line becomes a blank line, so
     * paragraphs and blocks stay visibly separate.
     */
    function textFromItems(items, layout) {
        var lines = [];
        var current = '';
        var lastY = null;
        var lastHeight = 0;
        var pendingGap = false;

        function flush() {
            lines.push(current.replace(/\s+$/, ''));
            current = '';
        }

        for (var i = 0; i < items.length; i++) {
            var item = items[i];
            if (!item || typeof item.str !== 'string') continue; // marked-content markers

            var y = item.transform ? item.transform[5] : null;
            var h = item.height || lastHeight || 0;

            if (y !== null && lastY !== null && Math.abs(y - lastY) > Math.max(1, h * 0.5)) {
                if (current) flush();
                if (layout && h && Math.abs(y - lastY) > h * 2.2) pendingGap = true;
            }
            if (pendingGap && lines.length && lines[lines.length - 1] !== '') {
                lines.push('');
                pendingGap = false;
            }

            current += item.str;
            if (y !== null) lastY = y;
            if (h) lastHeight = h;

            if (item.hasEOL) {
                flush();
                // Next line decides below whether a gap precedes it.
            }
        }
        if (current) flush();

        // Plain mode matches `page.get_text()`: lines only, no blank lines (pdf.js
        // emits empty lines at large gaps, which PyMuPDF does not). Layout mode
        // keeps paragraph gaps, collapsed to one blank line.
        if (!layout) lines = lines.filter(function (line) { return line !== ''; });
        return lines.join('\n').replace(/\n{3,}/g, '\n\n').replace(/^\s+|\s+$/g, '');
    }

    var IMAGE_OPS = ['paintImageXObject', 'paintInlineImageXObject', 'paintImageMaskXObject', 'paintJpegXObject', 'paintImageXObjectRepeat'];

    async function pageHasImages(pdfjs, page) {
        try {
            var list = await page.getOperatorList();
            var ids = IMAGE_OPS.map(function (name) { return pdfjs.OPS && pdfjs.OPS[name]; })
                .filter(function (id) { return id !== undefined; });
            for (var i = 0; i < list.fnArray.length; i++) {
                if (ids.indexOf(list.fnArray[i]) >= 0) return true;
            }
        } catch (e) { /* treat as no images */ }
        return false;
    }

    L.register('/api/pdf/extract-text', async function (fd, ctx) {
        ctx = ctx || {};
        var files = L.files(fd, 'file');
        if (!files.length) throw new L.Error('No file provided.');
        var file = files[0];

        if (L.str(fd, 'password', null)) {
            throw new L.Unsupported('password-protected PDFs need the server', 'encrypted');
        }
        var layout = String(L.str(fd, 'preserve_layout', 'false')).toLowerCase();
        layout = layout === 'true' || layout === '1' || layout === 'on' || layout === 'yes';

        var budget = L.constrained() ? BUDGET.mobile : BUDGET.desktop;
        if (file.size > budget.bytes) {
            throw new L.Unsupported('input exceeds the on-device text budget', 'resource_budget_exceeded');
        }

        var pdfjs = await L.loadPdfJs();
        var doc = await L.openPdfJs(file);

        try {
            var total = doc.numPages;
            if (total > budget.pages) {
                throw new L.Unsupported('page count exceeds the on-device text budget', 'resource_budget_exceeded');
            }

            var sections = [];
            var scanned = false;
            for (var i = 1; i <= total; i++) {
                L.checkAbort(ctx.signal);
                var page = await doc.getPage(i);
                var content = await page.getTextContent();
                var text = textFromItems(content.items || [], layout);
                if (!text && await pageHasImages(pdfjs, page)) scanned = true;
                page.cleanup();
                if (text) sections.push('--- Page ' + i + ' ---\n' + text);
                if (ctx.onProgress) ctx.onProgress(i, total);
                await L.tick();
            }

            // The server would OCR these pages; returning what we have would
            // silently drop their text.
            if (scanned) throw new L.Unsupported('scanned pages need OCR', 'ocr_required');

            var full = sections.length ? sections.join('\n\n') : '(No text found in document)';
            return {
                blob: new Blob([full], { type: 'text/plain;charset=utf-8' }),
                filename: L.brandedName(file.name, 'txt'),
                message: 'Text extracted from ' + total + ' page(s)',
                extra: { page_count: total },
            };
        } catch (err) {
            if (err instanceof L.Error || err instanceof L.Unsupported || (err && err.name === 'AbortError')) throw err;
            throw new L.Unsupported('pdf.js could not read this PDF', 'unsupported_structure');
        } finally {
            try { await doc.destroy(); } catch (e) { /* already gone */ }
        }
    });

    L.text = { textFromItems: textFromItems };
})();
