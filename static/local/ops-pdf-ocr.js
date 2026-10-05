// On-device OCR PDF (migration work package 18), English.
//
// Route: /api/pdf/ocr (file, lang default "en", password). Same contract as the Python endpoint
// (pdf_utils.py::ocr_pdf_to_searchable_pdf): the output is the ORIGINAL PDF with an invisible text
// layer added, so the pages look exactly as they did and become searchable, selectable and
// copyable. `<stem>_forgefiles.org.pdf`, "Searchable PDF created from N page(s)", `page_count`.
//
// Engine: tesseract.js 7 (Apache-2.0) with the WebAssembly core and the English model, all vendored
// same-origin under static/vendor/tesseract/ and loaded only when this tool first runs. Every asset
// is fetched, checked against the SHA-256 in MANIFEST.json, and the two code files are then run from
// blob URLs of exactly those verified bytes, so nothing unverified is executed and nothing comes
// from a CDN. Recognition runs in tesseract's own Web Worker, one page at a time, at 200 dpi like
// the server; the page is released before the next is drawn.
//
// Scope, declared rather than hidden:
//   * English only. Hindi, Marathi, Tamil and Telugu measured below parity in the lab, so they ask
//     before going to the server (their models and fonts live there).
//   * A page that already has a text layer is left alone (the server would OCR it anyway and add a
//     second, duplicate layer). If every page has text, nothing is added and the message says so.
//   * Rotated pages ask first: placing text on them needs the inverse rotation, which is not built.
//   * Password-protected PDFs, input past the budgets (desktop 100 MiB / 50 pages, phone-width
//     20 MiB / 20 pages, 90 s per page) and any engine failure ask before the server is used.
(function () {
    'use strict';

    var L = window.ffLocal;
    if (!L) return;

    var MIB = 1024 * 1024;
    var OCR_DPI = 200; // pdf_utils.py renders OCR input at 200
    var MAX_PAGES = 200;
    var MAX_PIXELS = 20000000;
    var SUPPORTED = ['en', 'hi', 'mr', 'ta', 'te']; // ocr_engine.py::SUPPORTED_OCR_LANGUAGES
    var BUDGET = {
        desktop: { bytes: 100 * MIB, pages: 50, pixels: MAX_PIXELS, pageMs: 90000 },
        mobile: { bytes: 20 * MIB, pages: 20, pixels: 16777216, pageMs: 60000 },
    };
    var MIN_CONFIDENCE = 25; // drop tesseract's guesses on specks and noise

    // WebAssembly SIMD feature probe (the standard 30-byte module): picks the faster core.
    var SIMD_PROBE = new Uint8Array([0, 97, 115, 109, 1, 0, 0, 0, 1, 5, 1, 96, 0, 1, 123, 3, 2, 1, 0, 10, 10, 1, 8, 0, 65, 0, 253, 15, 253, 98, 11]);

    function hasSimd() {
        try { return WebAssembly.validate(SIMD_PROBE); } catch (e) { return false; }
    }

    // ── verified engine assets ────────────────────────────────────────────

    var enginePromise = null;

    function hex(buf) {
        return Array.prototype.map.call(new Uint8Array(buf), function (b) { return (b < 16 ? '0' : '') + b.toString(16); }).join('');
    }

    /** Fetch one vendored file and fail closed unless its SHA-256 matches the manifest. */
    async function fetchVerified(rel, manifest) {
        var entry = manifest.files[rel];
        if (!entry) throw new L.Unsupported('OCR asset not in manifest: ' + rel, 'engine_unavailable');
        var res;
        try {
            res = await fetch(L.vendorUrl('tesseract/' + rel) + '?v=' + entry.version, { cache: 'force-cache' });
        } catch (e) {
            throw new L.Unsupported('OCR asset could not be fetched: ' + rel, 'engine_unavailable');
        }
        if (!res.ok) throw new L.Unsupported('OCR asset missing: ' + rel, 'engine_unavailable');
        var bytes = await res.arrayBuffer();
        if (!window.crypto || !window.crypto.subtle) throw new L.Unsupported('cannot verify OCR assets here', 'engine_unavailable');
        var digest = hex(await window.crypto.subtle.digest('SHA-256', bytes));
        if (digest !== entry.sha256 || bytes.byteLength !== entry.bytes) {
            throw new L.Unsupported('OCR asset failed its integrity check: ' + rel, 'engine_unavailable');
        }
        return bytes;
    }

    function blobUrl(bytes, type) {
        return URL.createObjectURL(new Blob([bytes], { type: type }));
    }

    function runScript(url) {
        return new Promise(function (ok, fail) {
            var el = document.createElement('script');
            el.src = url;
            el.async = true;
            el.onload = function () { ok(); };
            el.onerror = function () { fail(new L.Unsupported('OCR library could not start', 'engine_unavailable')); };
            document.head.appendChild(el);
        });
    }

    /**
     * Load and verify the engine once per page. Resolves `{Tesseract, workerUrl, coreUrl, langPath}`.
     * The two code files (the worker and the WebAssembly core) are run from blob URLs of the exact
     * bytes that passed the hash check. The language model is data, not code, and tesseract.js 7.0.0
     * cannot take it from memory (its initialiser reads a language entry's bytes as its name), so
     * the worker fetches it from our own origin once its hash has been checked here.
     * A failure is not cached, so a later attempt (the app was offline) can succeed.
     */
    function loadEngine() {
        if (enginePromise) return enginePromise;
        enginePromise = (async function () {
            var manifestRes = await fetch(L.vendorUrl('tesseract/MANIFEST.json'), { cache: 'no-cache' });
            if (!manifestRes.ok) throw new L.Unsupported('OCR manifest missing', 'engine_unavailable');
            var manifest = await manifestRes.json();
            var coreName = hasSimd() ? 'core/tesseract-core-simd-lstm.wasm.js' : 'core/tesseract-core-lstm.wasm.js';
            var results = await Promise.all([
                fetchVerified('tesseract.min.js', manifest), fetchVerified('worker.min.js', manifest),
                fetchVerified(coreName, manifest), fetchVerified('lang/eng.traineddata.gz', manifest),
            ]);
            var libUrl = blobUrl(results[0], 'text/javascript');
            try { await runScript(libUrl); } finally { URL.revokeObjectURL(libUrl); }
            if (!window.Tesseract) throw new L.Unsupported('OCR library did not register', 'engine_unavailable');
            return {
                Tesseract: window.Tesseract,
                workerUrl: blobUrl(results[1], 'text/javascript'),
                // tesseract.js treats a path ending in "js" as the exact core file to import.
                coreUrl: blobUrl(results[2], 'text/javascript') + '#core.js',
                // A worker started from a blob URL cannot resolve a relative path, so this is absolute.
                langPath: new URL(L.vendorUrl('tesseract/lang'), window.location.href).href,
            };
        })().catch(function (err) {
            enginePromise = null;
            throw err;
        });
        return enginePromise;
    }

    // ── text and geometry ─────────────────────────────────────────────────

    /** Text the standard Helvetica font can carry: ligatures spelled out, the rest dropped to "?". */
    function encodable(font, text) {
        var map = { 'ﬁ': 'fi', 'ﬂ': 'fl', 'ﬀ': 'ff', '−': '-', ' ': ' ' };
        var set = null;
        try { set = font.getCharacterSet(); } catch (e) { set = null; }
        var out = '';
        for (var ch of String(text)) {
            var cp = ch.codePointAt(0);
            var c = map[ch] || ch;
            if (c.length === 1 && set && set.indexOf(cp) < 0 && !map[ch]) c = '?';
            else if (c.length === 1 && cp < 32) c = '';
            out += c;
        }
        return out;
    }

    /** Words of one OCR page with their line baseline: [{text, x0, x1, base, size}] in image pixels. */
    function wordsOf(data) {
        var out = [];
        (data.blocks || []).forEach(function (block) {
            (block.paragraphs || []).forEach(function (para) {
                (para.lines || []).forEach(function (line) {
                    var lb = line.bbox;
                    var height = lb.y1 - lb.y0;
                    var bl = line.baseline;
                    (line.words || []).forEach(function (w) {
                        var text = String(w.text || '').trim();
                        if (!text || w.confidence < MIN_CONFIDENCE) return;
                        var cx = (w.bbox.x0 + w.bbox.x1) / 2;
                        var base;
                        if (bl && bl.has_baseline && bl.x1 !== bl.x0) {
                            // baseline y is relative to the line box in tesseract's output
                            base = bl.y0 + (bl.y1 - bl.y0) * ((cx - bl.x0) / (bl.x1 - bl.x0));
                        } else {
                            base = lb.y1 - 0.2 * height;
                        }
                        out.push({ text: text, x0: w.bbox.x0, x1: w.bbox.x1, base: base, lineTop: lb.y0, lineBottom: lb.y1 });
                    });
                });
            });
        });
        return out;
    }

    /**
     * Add one page's recognised words as invisible text (render mode 3) over the untouched page.
     * Each word is placed at its own box and stretched (Tz) to its measured width, so selection
     * follows the picture closely even though the font is Helvetica.
     */
    function addTextLayer(PDFLib, pdfPage, font, words, imgW, imgH) {
        var cb = pdfPage.getCropBox();
        var sx = cb.width / imgW;
        var sy = cb.height / imgH;
        var n = 0;
        // Register the font on this page once; the key is what the Tf operator names.
        var fontKey = pdfPage.node.newFontDictionary(font.name, font.ref);
        words.forEach(function (w) {
            var text = encodable(font, w.text);
            if (!text.trim()) return;
            var width = (w.x1 - w.x0) * sx;
            var lineH = (w.lineBottom - w.lineTop) * sy;
            var size = Math.max(4, Math.min(200, lineH * 0.85));
            var natural = font.widthOfTextAtSize(text, size);
            if (!(width > 0) || !(natural > 0)) return;
            var scale = Math.max(20, Math.min(400, width / natural * 100));
            // baseline y: image pixels run down, PDF user space runs up from the crop box origin.
            var x = cb.x + w.x0 * sx;
            var y = cb.y + cb.height - w.base * sy;
            pdfPage.pushOperators(
                PDFLib.pushGraphicsState(),
                PDFLib.beginText(),
                PDFLib.setTextRenderingMode(PDFLib.TextRenderingMode.Invisible),
                PDFLib.setFontAndSize(fontKey, size),
                PDFLib.PDFOperator.of('Tz', [PDFLib.PDFNumber.of(scale)]),
                PDFLib.moveText(x, y),
                PDFLib.showText(font.encodeText(text)),
                PDFLib.endText(),
                PDFLib.popGraphicsState()
            );
            n++;
        });
        return n;
    }

    // ── handler ───────────────────────────────────────────────────────────

    function pageHasText(content) {
        return (content.items || []).some(function (it) { return it && typeof it.str === 'string' && it.str.trim(); });
    }

    L.register('/api/pdf/ocr', async function (fd, ctx) {
        ctx = ctx || {};
        var layout = L.layout;
        if (!layout) throw new L.Unsupported('layout engine missing', 'engine_unavailable');
        var files = L.files(fd, 'file');
        if (!files.length) throw new L.Error('No file provided.');
        var file = files[0];

        var rawLang = L.str(fd, 'lang', 'en');
        var lang = String(rawLang).trim().toLowerCase() || 'en';
        if (SUPPORTED.indexOf(lang) < 0) {
            throw new L.Error('Unsupported OCR language: \'' + rawLang + '\'. Supported languages are: ' + SUPPORTED.slice().sort().join(', ') + '.');
        }
        if (lang !== 'en') throw new L.Unsupported('only English OCR runs on-device', 'ocr_language');
        if (L.str(fd, 'password', null)) throw new L.Unsupported('password-protected PDFs need the server', 'encrypted');

        var budget = L.constrained() ? BUDGET.mobile : BUDGET.desktop;
        if (file.size > budget.bytes) throw new L.Unsupported('input exceeds the on-device OCR budget', 'resource_budget_exceeded');

        var pdfjs = await L.loadPdfJs();
        var PDFLib = await L.loadPdfLib();
        var doc = await layout.openDocument(file);
        var worker = null;
        var aborted = null;

        try {
            var total = doc.numPages;
            if (total > MAX_PAGES) throw new L.Error('PDF has too many pages to render at once (max ' + MAX_PAGES + ').');
            if (total > budget.pages) throw new L.Unsupported('page count exceeds the on-device OCR budget', 'resource_budget_exceeded');

            // Same up-front checks as the server: every page's render size at 200 dpi, before any work.
            var scale = OCR_DPI / 72;
            var plan = [];
            for (var p = 1; p <= total; p++) {
                L.checkAbort(ctx.signal);
                var probe = await doc.getPage(p);
                var vp = probe.getViewport({ scale: 1 });
                var pixels = vp.width * vp.height * scale * scale;
                if (pixels > MAX_PIXELS) { probe.cleanup(); throw new L.Error('Page render would exceed 20,000,000 pixels at ' + OCR_DPI + ' DPI.'); }
                if (pixels > budget.pixels) { probe.cleanup(); throw new L.Unsupported('page bitmap exceeds the on-device canvas limit', 'resource_budget_exceeded'); }
                var content = await probe.getTextContent();
                plan.push({ w: vp.width, h: vp.height, rotate: probe.rotate || 0, text: pageHasText(content) });
                probe.cleanup();
            }
            var todo = plan.map(function (pg, i) { return pg.text ? -1 : i; }).filter(function (i) { return i >= 0; });
            var skipped = total - todo.length;
            if (todo.some(function (i) { return plan[i].rotate; })) throw new L.Unsupported('rotated pages need the server', 'unsupported_structure');

            var bytes = new Uint8Array(await file.arrayBuffer());
            if (!todo.length) {
                // Every page already carries text: nothing to recognise, nothing is changed.
                return {
                    blob: new Blob([bytes], { type: 'application/pdf' }),
                    filename: L.brandedName(file.name, 'pdf'),
                    message: 'This PDF already has searchable text on all ' + total + ' page(s); no OCR was needed',
                    extra: { page_count: total, ocr_pages: 0, skipped_pages: skipped },
                };
            }

            var pdf;
            try { pdf = await PDFLib.PDFDocument.load(bytes); } catch (e) { throw new L.Unsupported('pdf-lib could not open this PDF', 'unsupported_structure'); }
            var font = await pdf.embedFont(PDFLib.StandardFonts.Helvetica);
            var pdfPages = pdf.getPages();
            if (pdfPages.length !== total) throw new L.Unsupported('page counts disagree', 'unsupported_structure');

            var engine = await loadEngine();
            L.checkAbort(ctx.signal);
            worker = await engine.Tesseract.createWorker('eng', 1, {
                workerPath: engine.workerUrl,
                corePath: engine.coreUrl,
                langPath: engine.langPath,
                workerBlobURL: false,
                cacheMethod: 'none',
                gzip: true,
            });
            if (ctx.signal) {
                ctx.signal.addEventListener('abort', function () {
                    aborted = true;
                    if (worker) worker.terminate();
                }, { once: true });
            }

            var inserted = 0;
            for (var k = 0; k < todo.length; k++) {
                L.checkAbort(ctx.signal);
                var index = todo[k];
                var page = await doc.getPage(index + 1);
                var canvas = layout.makeCanvas(Math.ceil(plan[index].w * scale - 1e-6), Math.ceil(plan[index].h * scale - 1e-6));
                var data = null;
                try {
                    var g = canvas.getContext('2d');
                    g.fillStyle = '#ffffff';
                    g.fillRect(0, 0, canvas.width, canvas.height);
                    await page.render({ canvasContext: g, viewport: page.getViewport({ scale: scale }), intent: 'print', background: 'rgb(255,255,255)' }).promise;
                    var timer;
                    var result = await Promise.race([
                        worker.recognize(canvas, {}, { blocks: true }),
                        new Promise(function (ok, fail) {
                            timer = setTimeout(function () { fail(new L.Unsupported('OCR took too long on this device', 'resource_budget_exceeded')); }, budget.pageMs);
                        }),
                    ]).finally(function () { clearTimeout(timer); });
                    data = result.data;
                    inserted += addTextLayer(PDFLib, pdfPages[index], font, wordsOf(data), canvas.width, canvas.height);
                } finally {
                    canvas.width = 0;
                    canvas.height = 0;
                    page.cleanup();
                }
                if (ctx.onProgress) ctx.onProgress(index + 1, total);
                await L.tick();
            }
            if (aborted) L.checkAbort(ctx.signal);

            if (inserted === 0) throw new L.Error('No text could be recognized in this PDF.');
            L.checkAbort(ctx.signal);
            var out = await pdf.save();
            return {
                blob: new Blob([out], { type: 'application/pdf' }),
                filename: L.brandedName(file.name, 'pdf'),
                message: 'Searchable PDF created from ' + total + ' page(s)',
                extra: { page_count: total, ocr_pages: todo.length, skipped_pages: skipped },
            };
        } catch (err) {
            if (aborted) { var e2 = new Error('Aborted'); e2.name = 'AbortError'; throw e2; }
            if (err instanceof L.Error || err instanceof L.Unsupported || (err && err.name === 'AbortError')) throw err;
            throw new L.Unsupported('OCR could not run on this device', 'local_processing_failed');
        } finally {
            if (worker) { try { await worker.terminate(); } catch (e) { /* already gone */ } }
            try { await doc.destroy(); } catch (e) { /* already gone */ }
        }
    });

    L.ocr = { encodable: encodable, wordsOf: wordsOf, addTextLayer: addTextLayer, loadEngine: loadEngine };
})();
