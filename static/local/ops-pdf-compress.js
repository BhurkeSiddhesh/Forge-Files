// On-device PDF compression (WP14/WP20).
// Structural mode uses pinned qpdf WASM. Lossy mode is an explicit raster
// choice using the already pinned PDF.js + pdf-lib engines.
(function () {
    'use strict';
    var L = window.ffLocal;
    if (!L) return;

    var INPUT_LIMIT = 80 * 1024 * 1024;
    var PAGE_LIMIT = 100;
    var PIXEL_LIMIT = 80 * 1000 * 1000;
    var qpdfPromise = null;

    function loadScript(src) {
        return new Promise(function (resolve, reject) {
            var script = document.createElement('script');
            script.src = src;
            script.onload = resolve;
            script.onerror = function () { reject(new Error('Unable to load compression engine.')); };
            document.head.appendChild(script);
        });
    }

    async function loadQpdf() {
        if (typeof window.Module === 'function') return window.Module;
        if (!qpdfPromise) {
            qpdfPromise = loadScript(L.vendorUrl('qpdf/qpdf.js') + '?v=0.3.0').then(function () {
                if (typeof window.Module !== 'function') throw new Error('qpdf did not initialise.');
                return window.Module;
            });
        }
        return qpdfPromise;
    }

    function ensureFile(fd) {
        var files = L.files(fd, 'file');
        if (files.length !== 1) throw new L.Error('Please select a PDF file.');
        var file = files[0];
        if (file.size > INPUT_LIMIT) throw new L.Unsupported('PDF exceeds local memory budget.', 'resource_budget');
        return file;
    }

    async function structural(file, signal) {
        L.checkAbort(signal);
        var createModule = await loadQpdf();
        var wasmUrl = L.vendorUrl('qpdf/qpdf.wasm');
        var wasm = await fetch(wasmUrl, { credentials: 'same-origin', signal: signal });
        if (!wasm.ok) throw new Error('Unable to load qpdf WASM.');
        var module = await createModule({
            noInitialRun: true,
            wasmBinary: await wasm.arrayBuffer(),
            locateFile: function () { return wasmUrl; },
            print: function () {},
            printErr: function () {},
        });
        var input = new Uint8Array(await file.arrayBuffer());
        module.FS.writeFile('/input.pdf', input);
        try {
            module.callMain([
                '/input.pdf', '--object-streams=generate', '--compress-streams=y',
                '--recompress-flate', '--compression-level=9', '/output.pdf'
            ]);
        } catch (err) {
            throw new L.Unsupported('qpdf cannot safely process this PDF.', 'unsupported_structure');
        }
        L.checkAbort(signal);
        var out = module.FS.readFile('/output.pdf');
        if (!out || out.length < 5) throw new L.Unsupported('No valid compressed PDF was produced.', 'unsupported_structure');
        // A structural pass can legitimately find no saving. Return the original
        // rather than making the file larger, and report the measured result.
        var chosen = out.length < input.length ? out : input;
        return { bytes: chosen, noSaving: out.length >= input.length };
    }

    function canvasJpeg(canvas, quality) {
        return new Promise(function (resolve, reject) {
            canvas.toBlob(function (blob) { blob ? resolve(blob) : reject(new Error('JPEG encoding failed.')); }, 'image/jpeg', quality);
        });
    }

    async function lossy(file, level, signal, onProgress) {
        var settings = {
            low: { dpi: 144, quality: 0.82 },
            medium: { dpi: 120, quality: 0.70 },
            high: { dpi: 96, quality: 0.55 },
        }[level] || { dpi: 120, quality: 0.70 };
        var source = await L.openPdfJs(file);
        if (source.numPages > PAGE_LIMIT) throw new L.Unsupported('PDF exceeds the local page budget.', 'resource_budget');
        var PDFLib = await L.loadPdfLib();
        var output = await PDFLib.PDFDocument.create({ updateMetadata: false });
        var pixels = 0;
        for (var i = 1; i <= source.numPages; i++) {
            L.checkAbort(signal);
            var page = await source.getPage(i);
            var points = page.getViewport({ scale: 1 });
            var viewport = page.getViewport({ scale: settings.dpi / 72 });
            pixels += viewport.width * viewport.height;
            if (pixels > PIXEL_LIMIT) throw new L.Unsupported('PDF exceeds the local pixel budget.', 'resource_budget');
            var canvas = document.createElement('canvas');
            canvas.width = Math.ceil(viewport.width);
            canvas.height = Math.ceil(viewport.height);
            var ctx = canvas.getContext('2d', { alpha: false });
            ctx.fillStyle = '#fff';
            ctx.fillRect(0, 0, canvas.width, canvas.height);
            await page.render({ canvasContext: ctx, viewport: viewport }).promise;
            var jpeg = await canvasJpeg(canvas, settings.quality);
            var image = await output.embedJpg(await jpeg.arrayBuffer());
            var dst = output.addPage([points.width, points.height]);
            dst.drawImage(image, { x: 0, y: 0, width: points.width, height: points.height });
            canvas.width = canvas.height = 1;
            if (onProgress) onProgress({ completed: i, total: source.numPages, phase: 'compress' });
            await L.tick();
        }
        var bytes = await output.save({ useObjectStreams: true, addDefaultPage: false, updateFieldAppearances: false });
        try { await source.destroy(); } catch (e) { /* already released */ }
        return { bytes: bytes, dpi: settings.dpi, quality: settings.quality };
    }

    L.register('/api/pdf/compress', async function (fd, ctx) {
        var file = ensureFile(fd);
        var level = L.str(fd, 'level', 'medium');
        if (['low', 'medium', 'high'].indexOf(level) < 0) throw new L.Error('Invalid compression level.');
        if (L.str(fd, 'password', '')) throw new L.Unsupported('Encrypted PDFs use the server engine.', 'encrypted_pdf');
        var mode = L.str(fd, 'mode', 'structural');
        var result;
        if (mode === 'lossy') {
            result = await lossy(file, level, ctx.signal, ctx.onProgress);
            // Rasterising a small or already-lean PDF can grow it; never hand
            // back a bigger file than the original.
            if (result.bytes.length >= file.size) {
                result = { bytes: new Uint8Array(await file.arrayBuffer()), noSaving: true };
            }
        }
        else if (mode === 'structural') result = await structural(file, ctx.signal);
        else throw new L.Error('Invalid compression mode.');
        var size = result.bytes.length;
        var reduction = file.size ? Math.max(0, (file.size - size) * 100 / file.size) : 0;
        var note = result.noSaving
            ? 'No safe size reduction was available; the original PDF bytes were preserved.'
            : (mode === 'lossy'
                ? 'Pages were rasterised; text selection, links and accessibility structure are not preserved.'
                : 'Searchable text, links and vectors were preserved.');
        return {
            blob: new Blob([result.bytes], { type: 'application/pdf' }),
            filename: L.brandedName(file.name, 'pdf'),
            message: 'PDF compressed successfully',
            extra: {
                original_size: file.size,
                compressed_size: size,
                reduction_pct: Math.round(reduction * 10) / 10,
                compression_mode: mode,
                rasterized: mode === 'lossy' && !result.noSaving,
                dpi: result.dpi || null,
                compression_note: note,
            },
        };
    });
})();
