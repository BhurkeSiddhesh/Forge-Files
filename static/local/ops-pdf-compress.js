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

    // Vector-preserving mode: re-encode embedded DCT (JPEG) image XObjects in
    // place, leaving page content streams (text, links, vectors) untouched, then
    // run the structural pass. Mirrors pdf_utils.py::compress_pdf on the server.
    var IMAGE_SETTINGS = {
        low: { maxDim: 2400, quality: 0.85 },
        medium: { maxDim: 1600, quality: 0.70 },
        high: { maxDim: 1000, quality: 0.50 },
    };
    var MIN_IMAGE_BYTES = 20 * 1024;
    var MAX_IMAGE_PIXELS = 50 * 1000 * 1000;
    var MAX_IMAGE_SIDE = 20000;

    /**
     * Width, height and EXIF orientation read straight from the JPEG bytes. The PDF
     * dictionary is not trusted: the decoder sizes its raster from the SOF header.
     */
    function jpegInfo(b) {
        if (b.length < 4 || b[0] !== 0xff || b[1] !== 0xd8) return null;
        var info = { w: 0, h: 0, orientation: 1 };
        var i = 2;
        while (i + 4 <= b.length) {
            if (b[i] !== 0xff) { i++; continue; }
            var m = b[i + 1];
            if (m === 0xff) { i++; continue; }
            if (m === 0xd8 || m === 0x01 || (m >= 0xd0 && m <= 0xd7)) { i += 2; continue; }
            if (m === 0xd9) break;
            var len = (b[i + 2] << 8) | b[i + 3];
            if (len < 2) return null;
            if (m === 0xe1 && len >= 16 && b[i + 4] === 0x45 && b[i + 5] === 0x78 && b[i + 6] === 0x69 && b[i + 7] === 0x66) {
                var t = i + 10; // TIFF header after "Exif\0\0"
                var le = b[t] === 0x49;
                var u16 = function (o) { return le ? (b[o] | (b[o + 1] << 8)) : ((b[o] << 8) | b[o + 1]); };
                var u32 = function (o) { return le ? (b[o] | (b[o + 1] << 8) | (b[o + 2] << 16) | (b[o + 3] << 24)) >>> 0 : ((b[o] << 24) | (b[o + 1] << 16) | (b[o + 2] << 8) | b[o + 3]) >>> 0; };
                var ifd = t + u32(t + 4);
                if (ifd + 2 <= b.length) {
                    var n = u16(ifd);
                    for (var k = 0; k < n && ifd + 2 + k * 12 + 12 <= b.length; k++) {
                        var e = ifd + 2 + k * 12;
                        if (u16(e) === 0x0112) info.orientation = u16(e + 8);
                    }
                }
            }
            if (m >= 0xc0 && m <= 0xcf && m !== 0xc4 && m !== 0xc8 && m !== 0xcc) {
                if (i + 9 > b.length) return null;
                info.h = (b[i + 5] << 8) | b[i + 6];
                info.w = (b[i + 7] << 8) | b[i + 8];
                return info;
            }
            i += 2 + len;
        }
        return null;
    }

    async function downsampleImages(file, level, signal, onProgress) {
        var cfg = IMAGE_SETTINGS[level] || IMAGE_SETTINGS.medium;
        var PDFLib = await L.loadPdfLib();
        var doc;
        try {
            doc = await PDFLib.PDFDocument.load(new Uint8Array(await file.arrayBuffer()), { updateMetadata: false });
        } catch (err) {
            throw new L.Unsupported('PDF cannot be parsed for image recompression.', 'unsupported_structure');
        }
        var N = PDFLib.PDFName.of;
        var entries = doc.context.enumerateIndirectObjects().filter(function (e) {
            var obj = e[1];
            return obj instanceof PDFLib.PDFRawStream && obj.dict.get(N('Subtype')) === N('Image');
        });
        var replaced = 0;
        var unsupported = 0;
        for (var i = 0; i < entries.length; i++) {
            L.checkAbort(signal);
            var ref = entries[i][0], stream = entries[i][1], dict = stream.dict;
            var filter = dict.get(N('Filter'));
            var cs = dict.get(N('ColorSpace'));
            var bpc = dict.get(N('BitsPerComponent'));
            var raw = stream.getContents();
            if (raw.length < MIN_IMAGE_BYTES) continue;
            // Only plain 8-bit RGB/Gray JPEGs without masks or decode arrays. Any other
            // sizeable image (Flate, JPX, indexed, masked...) is counted so the server,
            // which can decode those, is offered when nothing else was recompressed.
            if (filter !== N('DCTDecode') || (cs !== N('DeviceRGB') && cs !== N('DeviceGray'))
                || (bpc && bpc.asNumber && bpc.asNumber() !== 8)
                || dict.get(N('Decode')) || dict.get(N('Mask')) || dict.get(N('ImageMask')) || dict.get(N('SMask'))) {
                unsupported++;
                continue;
            }
            // Declared size is checked before the browser is asked to decode anything.
            var declW = dict.get(N('Width')), declH = dict.get(N('Height'));
            var pw = declW && declW.asNumber ? declW.asNumber() : 0, ph = declH && declH.asNumber ? declH.asNumber() : 0;
            if (!pw || !ph || pw * ph > MAX_IMAGE_PIXELS || Math.max(pw, ph) > MAX_IMAGE_SIDE) {
                unsupported++;
                continue;
            }
            var head = jpegInfo(raw);
            // The JPEG's own header decides the decoded raster size, and an EXIF
            // orientation would be applied by the decoder but not by the PDF page matrix.
            if (!head || !head.w || !head.h || head.w * head.h > MAX_IMAGE_PIXELS
                || Math.max(head.w, head.h) > MAX_IMAGE_SIDE || head.orientation !== 1) {
                unsupported++;
                continue;
            }
            var bitmap;
            try { bitmap = await createImageBitmap(new Blob([raw], { type: 'image/jpeg' }), { imageOrientation: 'none' }); }
            catch (err) { continue; }
            var scale = Math.min(1, cfg.maxDim / Math.max(bitmap.width, bitmap.height));
            var w = Math.max(1, Math.round(bitmap.width * scale));
            var h = Math.max(1, Math.round(bitmap.height * scale));
            var canvas = document.createElement('canvas');
            canvas.width = w; canvas.height = h;
            var ctx = canvas.getContext('2d', { alpha: false });
            ctx.fillStyle = '#fff';
            ctx.fillRect(0, 0, w, h);
            ctx.drawImage(bitmap, 0, 0, w, h);
            if (bitmap.close) bitmap.close();
            var blob = await canvasJpeg(canvas, cfg.quality);
            canvas.width = canvas.height = 1;
            var bytes = new Uint8Array(await blob.arrayBuffer());
            // Never inflate: keep the original stream unless we actually saved.
            if (bytes.length >= raw.length * 0.95) continue;
            var nd = dict.clone();
            nd.set(N('Width'), PDFLib.PDFNumber.of(w));
            nd.set(N('Height'), PDFLib.PDFNumber.of(h));
            nd.set(N('ColorSpace'), N('DeviceRGB'));
            nd.set(N('BitsPerComponent'), PDFLib.PDFNumber.of(8));
            nd.set(N('Length'), PDFLib.PDFNumber.of(bytes.length));
            nd.delete(N('DecodeParms'));
            doc.context.assign(ref, PDFLib.PDFRawStream.of(nd, bytes));
            replaced++;
            if (onProgress) onProgress({ completed: i + 1, total: entries.length, phase: 'compress' });
            await L.tick();
        }
        if (!replaced && unsupported) {
            throw new L.Unsupported('embedded images use encodings the on-device engine cannot recompress', 'unsupported_structure');
        }
        if (!replaced) return { bytes: new Uint8Array(await file.arrayBuffer()), imagesRecompressed: 0, noSaving: false, passthrough: true };
        var saved = await doc.save({ useObjectStreams: false, updateFieldAppearances: false });
        return { bytes: saved, imagesRecompressed: replaced };
    }

    async function images(file, level, signal, onProgress) {
        var step = await downsampleImages(file, level, signal, onProgress);
        var inter = new File([step.bytes], file.name, { type: 'application/pdf' });
        var result;
        try { result = await structural(inter, signal); }
        catch (err) {
            if (err && err.name === 'AbortError') throw err;
            result = { bytes: step.bytes };
        }
        var chosen = result.bytes.length < file.size ? result.bytes : null;
        if (!chosen) return { bytes: new Uint8Array(await file.arrayBuffer()), noSaving: true, imagesRecompressed: step.imagesRecompressed };
        return { bytes: chosen, imagesRecompressed: step.imagesRecompressed };
    }

    L.register('/api/pdf/compress', async function (fd, ctx) {
        var file = ensureFile(fd);
        var level = L.str(fd, 'level', 'medium');
        if (['low', 'medium', 'high'].indexOf(level) < 0) throw new L.Error('Invalid compression level.');
        if (L.str(fd, 'password', '')) throw new L.Unsupported('Encrypted PDFs use the server engine.', 'encrypted_pdf');
        var mode = L.str(fd, 'mode', 'structural');
        var result;
        if (mode === 'lossy') result = await lossy(file, level, ctx.signal, ctx.onProgress);
        else if (mode === 'images') result = await images(file, level, ctx.signal, ctx.onProgress);
        else if (mode === 'structural') result = await structural(file, ctx.signal);
        else throw new L.Error('Invalid compression mode.');
        var size = result.bytes.length;
        var reduction = file.size ? Math.max(0, (file.size - size) * 100 / file.size) : 0;
        var note = result.noSaving
            ? 'No safe size reduction was available; the original PDF bytes were preserved.'
            : (mode === 'lossy'
                ? 'Pages were rasterised; text selection, links and accessibility structure are not preserved.'
                : (mode === 'images'
                    ? (result.imagesRecompressed || 0) + ' embedded image(s) were downsampled; text, links and vectors were preserved.'
                    : 'Searchable text, links and vectors were preserved.'));
        return {
            blob: new Blob([result.bytes], { type: 'application/pdf' }),
            filename: L.brandedName(file.name, 'pdf'),
            message: 'PDF compressed successfully',
            extra: {
                original_size: file.size,
                compressed_size: size,
                reduction_pct: Math.round(reduction * 10) / 10,
                compression_mode: mode,
                rasterized: mode === 'lossy',
                dpi: result.dpi || null,
                compression_note: note,
                images_recompressed: result.imagesRecompressed == null ? null : result.imagesRecompressed,
            },
        };
    });
})();
