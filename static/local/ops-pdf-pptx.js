// On-device PDF to PowerPoint (migration work package 17), raster-slide mode.
//
// Route: /api/pdf/to-pptx (file, dpi default 150, password). Same contract as the Python endpoint
// (pdf_utils.py::pdf_to_pptx): every page is rendered to a lossless PNG at `dpi`, one slide per
// page, the deck is sized once to the bounding box of all pages (PowerPoint allows one slide size
// per deck) and each page picture sits centred on its slide at its native size, so a mixed-size
// PDF is not stretched or clipped. `<stem>_forgefiles.org.pptx`, "PDF converted to PowerPoint".
//
// This is image-based, and it says so: the slides are pictures of the pages, not editable text
// or shapes. What it adds over the server (declared, not hidden): a fully transparent text layer
// laid over each picture at the text's own position, so the slides are searchable, selectable and
// readable by assistive technology. Turn it off with `searchable_text=false`. A page that is
// rotated, or whose text cannot be ordered, still gets its picture, just without the text layer.
//
// Because the result is a picture, nothing needs OCR and nothing about the page can be lost: a
// scanned PDF converts here exactly as it does on the server. Password-protected PDFs, and input
// past the device budgets, ask before going to the server.
(function () {
    'use strict';

    var L = window.ffLocal;
    if (!L) return;

    // pdf_utils.py::MAX_PDF_RENDER_PAGES / MAX_PDF_RENDER_PIXELS: the server rejects beyond these.
    var MAX_PAGES = 200;
    var MAX_PIXELS = 20000000;
    var MIB = 1024 * 1024;
    var BUDGET = {
        desktop: { bytes: 100 * MIB, pages: MAX_PAGES, pixels: MAX_PIXELS, out: 150 * MIB },
        mobile: { bytes: 25 * MIB, pages: 50, pixels: 16777216, out: 60 * MIB },
    };
    var PPTXGEN_VERSION = '4.0.1';
    var MAX_TEXT_SHAPES = 1500; // per slide, so a dense page cannot bloat the deck

    var pptxPromise = null;

    /** PptxGenJS is a same-origin, pinned file that needs the global JSZip, loaded on first use. */
    function loadPptx() {
        if (window.PptxGenJS) return Promise.resolve(window.PptxGenJS);
        if (pptxPromise) return pptxPromise;
        pptxPromise = L.loadJsZip().then(function () {
            return new Promise(function (ok, fail) {
                var el = document.createElement('script');
                el.src = L.vendorUrl('pptxgen.min.js') + '?v=' + PPTXGEN_VERSION;
                el.async = true;
                el.onload = function () {
                    if (window.PptxGenJS) ok(window.PptxGenJS);
                    else fail(new L.Unsupported('pptxgenjs loaded but did not register', 'engine_unavailable'));
                };
                el.onerror = function () { fail(new L.Unsupported('pptxgenjs could not be loaded', 'engine_unavailable')); };
                document.head.appendChild(el);
            });
        }).catch(function (err) {
            pptxPromise = null;
            throw err;
        });
        return pptxPromise;
    }

    function truthy(v, dflt) {
        var s = String(v === undefined || v === null || v === '' ? dflt : v).toLowerCase();
        return !(s === 'false' || s === '0' || s === 'no' || s === 'off');
    }

    function blobToDataUrl(blob) {
        return new Promise(function (ok, fail) {
            var reader = new FileReader();
            reader.onload = function () { ok(reader.result); };
            reader.onerror = function () { fail(reader.error); };
            reader.readAsDataURL(blob);
        });
    }

    L.register('/api/pdf/to-pptx', async function (fd, ctx) {
        ctx = ctx || {};
        var layout = L.layout;
        if (!layout) throw new L.Unsupported('layout engine missing', 'engine_unavailable');
        var files = L.files(fd, 'file');
        if (!files.length) throw new L.Error('No file provided.');
        var file = files[0];

        // Same order as the server: the route's range check, then the converter's own.
        var dpi = L.int(fd, 'dpi', 150);
        if (dpi < 30) throw new L.Error('dpi must be >= 30 (got ' + dpi + ')');
        if (dpi > 600) throw new L.Error('dpi must be <= 600 (got ' + dpi + ')');
        if (dpi < 72 || dpi > 300) throw new L.Error('DPI must be an integer between 72 and 300.');

        if (L.str(fd, 'password', null)) throw new L.Unsupported('password-protected PDFs need the server', 'encrypted');
        var searchable = truthy(L.str(fd, 'searchable_text', 'true'), 'true');

        var budget = L.constrained() ? BUDGET.mobile : BUDGET.desktop;
        if (file.size > budget.bytes) throw new L.Unsupported('input exceeds the on-device PowerPoint budget', 'resource_budget_exceeded');

        var pdfjs = await L.loadPdfJs();
        var Pptx = await loadPptx();
        var doc = await layout.openDocument(file);

        try {
            var total = doc.numPages;
            if (total > MAX_PAGES) throw new L.Error('PDF has too many pages to render at once (max ' + MAX_PAGES + ').');
            if (total > budget.pages) throw new L.Unsupported('page count exceeds the on-device PowerPoint budget', 'resource_budget_exceeded');

            // Validate every page before rendering any, as the server does, and size the deck.
            var scale = dpi / 72;
            var sizes = [];
            var slideW = 0;
            var slideH = 0;
            for (var p = 1; p <= total; p++) {
                L.checkAbort(ctx.signal);
                var probe = await doc.getPage(p);
                var vp1 = probe.getViewport({ scale: 1 });
                probe.cleanup();
                var pixels = vp1.width * vp1.height * scale * scale;
                if (pixels > MAX_PIXELS) throw new L.Error('Page render would exceed 20,000,000 pixels at ' + dpi + ' DPI.');
                if (pixels > budget.pixels) throw new L.Unsupported('page bitmap exceeds the on-device canvas limit', 'resource_budget_exceeded');
                sizes.push({ w: vp1.width, h: vp1.height });
                slideW = Math.max(slideW, vp1.width);
                slideH = Math.max(slideH, vp1.height);
            }

            var info = {};
            try { info = (await doc.getMetadata()).info || {}; } catch (e) { info = {}; }
            var pptx = new Pptx();
            pptx.defineLayout({ name: 'PDF_PAGES', width: slideW / 72, height: slideH / 72 });
            pptx.layout = 'PDF_PAGES';
            pptx.title = String(info.Title || '').trim() || L.stem(file.name);
            pptx.author = String(info.Author || '').trim() || 'Forge Files';
            pptx.company = 'Forge Files';
            pptx.subject = 'PDF converted to PowerPoint';

            var outBytes = 0;
            for (var n = 1; n <= total; n++) {
                L.checkAbort(ctx.signal);
                var size = sizes[n - 1];
                var left = (slideW - size.w) / 2;
                var top = (slideH - size.h) / 2;
                var page;
                var lines = [];
                if (searchable) {
                    // The text layer is a bonus: if it cannot be built, the picture is still exact.
                    try {
                        var model = await layout.loadPage(pdfjs, doc, n);
                        page = model.pageObjs;
                        model.pageObjs = null;
                        if (!model.rotation) lines = model.ordered;
                    } catch (e) {
                        if (!(e instanceof layout.Decline)) throw e;
                        page = await doc.getPage(n);
                    }
                } else {
                    page = await doc.getPage(n);
                }

                // 792 pt * 150 / 72 is exactly 1650; the tiny epsilon keeps floating point from rounding it up to 1651.
                var canvas = layout.makeCanvas(Math.ceil(size.w * scale - 1e-6), Math.ceil(size.h * scale - 1e-6));
                var dataUrl;
                try {
                    var g = canvas.getContext('2d');
                    g.fillStyle = '#ffffff';
                    g.fillRect(0, 0, canvas.width, canvas.height);
                    // intent 'print' renders without requestAnimationFrame, which browsers pause in a background tab.
                    var viewport = page.getViewport({ scale: scale });
                    await page.render({ canvasContext: g, viewport: viewport, intent: 'print', background: 'rgb(255,255,255)' }).promise;
                    var png = await layout.canvasBlob(canvas, 'image/png');
                    outBytes += png.size;
                    if (outBytes > budget.out) throw new L.Unsupported('slides exceed the on-device size budget', 'resource_budget_exceeded');
                    dataUrl = await blobToDataUrl(png);
                } finally {
                    canvas.width = 0;
                    canvas.height = 0;
                    page.cleanup();
                }

                var slide = pptx.addSlide();
                slide.addImage({
                    data: dataUrl.replace(/^data:/, ''), x: left / 72, y: top / 72, w: size.w / 72, h: size.h / 72,
                    altText: 'Page ' + n + ' of ' + total,
                });
                lines.slice(0, MAX_TEXT_SHAPES).forEach(function (line) {
                    var text = line.text.replace(/\t/g, ' ').trim();
                    if (!text) return;
                    slide.addText(text, {
                        // Arial's ascent is 0.905 em, so this puts the text's baseline on the PDF's baseline.
                        x: (left + line.x0) / 72, y: (top + line.y - 0.905 * line.size) / 72,
                        w: Math.max(1, line.x1 - line.x0 + 2) / 72, h: line.size * 1.2 / 72,
                        fontSize: Math.max(1, line.size), fontFace: 'Arial',
                        color: 'FFFFFF', transparency: 100, margin: 0, wrap: false, valign: 'top', fit: 'none',
                    });
                });
                if (ctx.onProgress) ctx.onProgress(n, total);
                await L.tick();
            }

            L.checkAbort(ctx.signal);
            var out = await pptx.write({ outputType: 'blob', compression: true });
            return {
                blob: new Blob([out], { type: 'application/vnd.openxmlformats-officedocument.presentationml.presentation' }),
                filename: L.brandedName(file.name, 'pptx'),
                message: 'PDF converted to PowerPoint',
            };
        } catch (err) {
            if (err instanceof L.Error || err instanceof L.Unsupported || (err && err.name === 'AbortError')) throw err;
            // A render failure on one page means the whole deck is untrustworthy.
            throw new L.Unsupported('PDF could not be rendered to slides on-device', 'unsupported_structure');
        } finally {
            try { await doc.destroy(); } catch (e) { /* already gone */ }
        }
    });
})();
