// On-device PDF rendering: PDF -> JPG/PNG pages (migration work package 02).
//
// Mirrors pdf_utils.py::pdf_to_images_zip, using pdf.js (Apache-2.0, vendored
// under static/vendor/pdfjs/, same-origin) instead of PyMuPDF. Every page is
// rendered at dpi/72 scale onto an opaque white canvas, encoded, and packed into
// a ZIP.
//
// Pixel dimensions follow the same rule as the server (PDF points x dpi / 72),
// but the two rasterisers can differ by a pixel at the page edge and in
// anti-aliasing; the dimensions here are the authoritative contract.
(function () {
    'use strict';

    var L = window.ffLocal;
    if (!L) return;

    // pdf_utils.py::MAX_PDF_RENDER_PAGES / MAX_PDF_RENDER_PIXELS. The server
    // rejects beyond these, so rejecting identically keeps the routes aligned.
    var MAX_PAGES = 200;
    var MAX_PIXELS = 20000000;

    // Extra on-device limits (work package 02, section 6). Past these the device
    // is the wrong place to hold the document and the bitmaps, so the user is
    // offered the server instead. iOS Safari refuses canvases above ~16.7 MP.
    var MIB = 1024 * 1024;
    var BUDGET = {
        desktop: { bytes: 100 * MIB, pages: 200, pixels: MAX_PIXELS },
        mobile: { bytes: 25 * MIB, pages: 50, pixels: 16777216 },
    };

    var JPEG_QUALITY = 0.95; // PyMuPDF's Pixmap.tobytes("jpeg") default

    function pad3(n) {
        var t = String(n);
        while (t.length < 3) t = '0' + t;
        return t;
    }

    function parseDpi(fd) {
        var raw = fd.get('dpi');
        if (raw === null || raw === undefined || raw === '') return 150;
        var text = String(raw).trim();
        if (!/^[+-]?\d+$/.test(text)) throw new L.Error('DPI must be an integer.');
        var dpi = parseInt(text, 10);
        if (dpi < 50 || dpi > 300) throw new L.Error('DPI must be between 50 and 300.');
        return dpi;
    }

    function parseFormat(fd) {
        var fmt = String(L.str(fd, 'fmt', 'jpg')).toLowerCase();
        if (fmt !== 'jpg' && fmt !== 'jpeg' && fmt !== 'png') throw new L.Error('Format must be jpg or png.');
        return fmt === 'png' ? 'png' : 'jpg';
    }

    function makeCanvas(w, h) {
        var canvas = document.createElement('canvas');
        canvas.width = w;
        canvas.height = h;
        return canvas;
    }

    function encode(canvas, ext) {
        return new Promise(function (fulfil, fail) {
            var type = ext === 'png' ? 'image/png' : 'image/jpeg';
            canvas.toBlob(function (blob) {
                if (blob) fulfil(blob);
                else fail(new L.Unsupported('canvas could not encode the page', 'engine_unavailable'));
            }, type, ext === 'png' ? undefined : JPEG_QUALITY);
        });
    }

    // Lossless extraction of plain JPEG (DCTDecode) image XObjects, ordered by
    // object number to match pdf_utils.py::_pdf_extract_embedded_images_zip.
    // Any other encoding needs a real decoder, so the server takes over.
    async function extractEmbedded(file) {
        var budget = L.constrained() ? BUDGET.mobile : BUDGET.desktop;
        if (file.size > budget.bytes) {
            throw new L.Unsupported('input exceeds the on-device render budget', 'resource_budget_exceeded');
        }
        var PDFLib = await L.loadPdfLib();
        var doc;
        try { doc = await PDFLib.PDFDocument.load(new Uint8Array(await file.arrayBuffer()), { updateMetadata: false }); }
        catch (e) { throw new L.Unsupported('PDF cannot be parsed on-device', 'unsupported_structure'); }
        var N = PDFLib.PDFName.of;
        // Only images referenced by a page's own resources, like the server's
        // page.get_images(); orphaned or deleted-page objects are not exposed.
        var seen = {};
        var found = [];
        doc.getPages().forEach(function (page) {
            var res = page.node.Resources();
            var xo = res && res.lookupMaybe(N('XObject'), PDFLib.PDFDict);
            if (!xo) return;
            xo.entries().forEach(function (pair) {
                var ref = pair[1];
                var obj = doc.context.lookup(ref);
                var sub = obj && obj.dict && obj.dict.get(N('Subtype'));
                if (sub === N('Form')) {
                    // Images nested in forms are resolved by the server instead.
                    throw new L.Unsupported('page uses form XObjects', 'unsupported_structure');
                }
                if (obj instanceof PDFLib.PDFRawStream && sub === N('Image') && ref.objectNumber !== undefined && !seen[ref.objectNumber]) {
                    seen[ref.objectNumber] = true;
                    found.push([ref, obj]);
                }
            });
        });
        found.sort(function (a, b) { return a[0].objectNumber - b[0].objectNumber; });
        if (!found.length) throw new L.Error('No embedded images were found in this PDF.');
        if (found.length > 500) throw new L.Error('PDF has too many embedded images (max 500).');
        var JSZip = await L.loadJsZip();
        var zip = new JSZip();
        var base = L.stem(file.name);
        for (var i = 0; i < found.length; i++) {
            var st = found[i][1];
            if (st.dict.get(N('Filter')) !== N('DCTDecode')) {
                throw new L.Unsupported('embedded image needs a decoder', 'unsupported_structure');
            }
            zip.file(base + '_img_' + pad3(i + 1) + '.jpg', st.getContents());
        }
        return {
            blob: await zip.generateAsync({ type: 'blob', compression: 'DEFLATE' }),
            filename: L.brandedName(file.name, 'zip'),
            message: 'Extracted ' + found.length + ' embedded image(s)',
            extra: { page_count: found.length },
        };
    }

    L.register('/api/pdf/to-images', async function (fd, ctx) {
        ctx = ctx || {};
        var files = L.files(fd, 'file');
        if (!files.length) throw new L.Error('No file provided.');
        var file = files[0];

        if (L.str(fd, 'password', null)) {
            throw new L.Unsupported('password-protected PDFs need the server', 'encrypted');
        }
        var mode = L.str(fd, 'mode', 'pages');
        if (mode !== 'pages' && mode !== 'embedded') throw new L.Error("mode must be 'pages' or 'embedded'.");
        if (mode === 'embedded') return extractEmbedded(file);
        var dpi = parseDpi(fd);
        var ext = parseFormat(fd);

        var budget = L.constrained() ? BUDGET.mobile : BUDGET.desktop;
        if (file.size > budget.bytes) {
            throw new L.Unsupported('input exceeds the on-device render budget', 'resource_budget_exceeded');
        }

        var JSZip = await L.loadJsZip();
        var doc = await L.openPdfJs(file);

        try {
            var total = doc.numPages;
            if (total > MAX_PAGES) {
                throw new L.Error('PDF has too many pages to render at once (max ' + MAX_PAGES + ').');
            }
            if (total > budget.pages) {
                throw new L.Unsupported('page count exceeds the on-device render budget', 'resource_budget_exceeded');
            }

            // Validate every page before rendering any, as the server does.
            var scale = dpi / 72;
            for (var p = 1; p <= total; p++) {
                L.checkAbort(ctx.signal);
                var probe = await doc.getPage(p);
                var vp = probe.getViewport({ scale: scale });
                var pixels = vp.width * vp.height;
                probe.cleanup();
                if (pixels > MAX_PIXELS) {
                    throw new L.Error('Page render would exceed 20,000,000 pixels at ' + dpi + ' DPI.');
                }
                if (pixels > budget.pixels) {
                    throw new L.Unsupported('page bitmap exceeds the on-device canvas limit', 'resource_budget_exceeded');
                }
            }

            var zip = new JSZip();
            var base = L.stem(file.name);

            for (var i = 1; i <= total; i++) {
                L.checkAbort(ctx.signal);
                var page = await doc.getPage(i);
                var viewport = page.getViewport({ scale: scale });
                var canvas = makeCanvas(Math.ceil(viewport.width), Math.ceil(viewport.height));
                try {
                    var g = canvas.getContext('2d');
                    // JPEG has no alpha: render onto opaque white.
                    g.fillStyle = '#ffffff';
                    g.fillRect(0, 0, canvas.width, canvas.height);
                    // intent 'print' renders without requestAnimationFrame, which browsers
                    // pause in a background tab (a display render would stall there).
                    await page.render({ canvasContext: g, viewport: viewport, intent: 'print', background: 'rgb(255,255,255)' }).promise;
                    var blob = await encode(canvas, ext);
                    zip.file(base + '_page_' + pad3(i) + '.' + ext, new Uint8Array(await blob.arrayBuffer()));
                } finally {
                    // Release the bitmap before the next page is allocated.
                    canvas.width = 0;
                    canvas.height = 0;
                    page.cleanup();
                }
                if (ctx.onProgress) ctx.onProgress(i, total);
                await L.tick();
            }

            L.checkAbort(ctx.signal);
            var out = await zip.generateAsync({ type: 'blob', compression: 'DEFLATE' });
            return {
                blob: out,
                filename: L.brandedName(file.name, 'zip'),
                message: 'Rendered ' + total + ' page(s) to images',
                extra: { page_count: total },
            };
        } catch (err) {
            if (err instanceof L.Error || err instanceof L.Unsupported || (err && err.name === 'AbortError')) throw err;
            // A render failure on one page means the whole output is untrustworthy.
            throw new L.Unsupported('pdf.js could not render this PDF', 'unsupported_structure');
        } finally {
            try { await doc.destroy(); } catch (e) { /* already gone */ }
        }
    });

    L.render = { parseDpi: parseDpi, parseFormat: parseFormat };
})();
