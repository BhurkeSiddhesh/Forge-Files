// On-device PDF operations — the pdf-lib equivalents of the structural half of
// scripts/pdf_utils.py (page shuffling, stamping, and PDF creation).
//
// Only operations that rearrange or draw on pages live here. Anything that has
// to *understand* a page — pdf2docx, OCR, to-excel, to-pptx, compression,
// repair — stays on the server, where pikepdf/PyMuPDF/pdf2docx are.
//
// Encrypted PDFs are always deferred: pdf-lib can detect encryption but not
// decrypt it, and the server's `_get_decrypted_pdf_path()` can.
(function () {
    'use strict';

    var L = window.ffLocal;
    if (!L) return;

    // reportlab's exact page sizes, so a locally-built PDF measures the same as
    // a server-built one (A4 is 210x297mm at 72dpi, not pdf-lib's rounded pair).
    var MM = 72 / 25.4;
    var PAGE_SIZES = {
        a4: [210 * MM, 297 * MM],
        letter: [612.0, 792.0],
    };

    function pageSize(name) {
        return PAGE_SIZES[String(name || 'a4').toLowerCase()] || PAGE_SIZES.a4;
    }

    // ── Loading ───────────────────────────────────────────────────────────

    function isEncryptionError(err) {
        var name = (err && err.name) || '';
        var msg = (err && err.message) || '';
        return name.indexOf('Encrypted') >= 0 || /encrypt/i.test(msg);
    }

    /** Read a File into the ArrayBuffer pdf-lib wants. */
    function bytesOf(file) {
        if (file.arrayBuffer) return file.arrayBuffer();
        return new Promise(function (fulfil, fail) {
            var reader = new FileReader();
            reader.onload = function () { fulfil(reader.result); };
            reader.onerror = function () { fail(reader.error); };
            reader.readAsArrayBuffer(file);
        });
    }

    /**
     * Load a PDF, deferring to the server for anything pdf-lib can't own.
     * A supplied password is by itself enough to defer — we cannot verify it,
     * and silently ignoring it would produce a result the user didn't ask for.
     */
    async function loadDoc(PDFLib, file, password) {
        if (password) throw new L.Unsupported('password-protected PDFs need the server', 'encrypted');
        // Memory budget: the input, pdf-lib's object graph and the output are all
        // held at once. Past this the server is offered instead (after consent).
        if (file.size > (L.constrained() ? 50 : 150) * 1024 * 1024) {
            throw new L.Unsupported('input exceeds the on-device PDF budget', 'resource_budget_exceeded');
        }
        var doc;
        try {
            // updateMetadata:false: pdf-lib would otherwise stamp its own Producer
            // and modification date onto a file the user only asked us to rearrange.
            doc = await PDFLib.PDFDocument.load(new Uint8Array(await bytesOf(file)), { updateMetadata: false });
        } catch (err) {
            if (isEncryptionError(err)) {
                throw new L.Unsupported('PDF is encrypted', 'encrypted');
            }
            // Malformed/damaged input: the server has a repair path, we don't.
            throw new L.Unsupported('pdf-lib could not parse this PDF', 'unsupported_structure');
        }
        // pdf-lib is lenient with damaged files and can return an empty shell.
        if (!doc.catalog || doc.getPageCount() < 1) {
            throw new L.Unsupported('PDF structure could not be read', 'unsupported_structure');
        }
        return doc;
    }

    async function save(doc) {
        return new Blob([await doc.save()], { type: 'application/pdf' });
    }

    function only(fd) {
        var all = L.files(fd, 'file');
        if (!all.length) throw new L.Error('No file provided.');
        return all[0];
    }

    // ── Page selection ────────────────────────────────────────────────────

    /**
     * pdf_utils.py::_parse_page_selection — '1,3-5' or 'all' to zero-based
     * indices, preserving the order given and dropping repeats. The error
     * strings are copied verbatim so a bad range reads the same either side.
     */
    function parsePageSelection(pages, totalPages) {
        if (pages === null || pages === undefined) {
            throw new L.Error("No pages selected. Please provide page numbers or 'all'.");
        }
        var normalized = String(pages).trim().toLowerCase();
        if (!normalized) {
            throw new L.Error("No pages selected. Please provide page numbers or 'all'.");
        }
        if (normalized === 'all') {
            var every = [];
            for (var p = 0; p < totalPages; p++) every.push(p);
            return every;
        }

        var indices = [];
        var seen = {};
        var parts = normalized.split(',');

        for (var i = 0; i < parts.length; i++) {
            var segment = parts[i].trim();
            if (!segment) continue;

            if (segment.indexOf('-') >= 0) {
                var at = segment.indexOf('-');
                var startStr = segment.slice(0, at);
                var endStr = segment.slice(at + 1);
                if (!startStr || !endStr) {
                    throw new L.Error("Invalid page range segment: '" + segment + "'");
                }
                if (!/^\d+$/.test(startStr) || !/^\d+$/.test(endStr)) {
                    throw new L.Error("Invalid page range numbers: '" + segment + "'");
                }
                var start = parseInt(startStr, 10);
                var end = parseInt(endStr, 10);
                if (start < 1 || end < 1 || start > end) {
                    throw new L.Error("Invalid page range segment: '" + segment + "'");
                }
                for (var n = start; n <= end; n++) {
                    if (!seen[n]) { seen[n] = true; indices.push(n - 1); }
                }
            } else {
                if (!/^\d+$/.test(segment)) {
                    throw new L.Error("Invalid page number: '" + segment + "'");
                }
                var num = parseInt(segment, 10);
                if (num < 1) throw new L.Error("Invalid page number: '" + segment + "'");
                if (!seen[num]) { seen[num] = true; indices.push(num - 1); }
            }
        }

        if (!indices.length) throw new L.Error('No valid pages selected.');
        if (Math.max.apply(null, indices) >= totalPages) {
            throw new L.Error('Selected page number exceeds document page count (' + totalPages + ').');
        }
        return indices;
    }

    // ── /api/pdf/merge ────────────────────────────────────────────────────

    L.register('/api/pdf/merge', async function (fd, ctx) {
        ctx = ctx || {};
        var inputs = L.files(fd, 'files');
        if (inputs.length < 2) throw new L.Error('Provide at least two PDF files to merge.');

        // Any per-file password means at least one input is encrypted.
        var passwords = L.str(fd, 'passwords', '');
        if (passwords && passwords.split(',').some(function (p) { return p; })) {
            throw new L.Unsupported('password-protected PDFs need the server', 'encrypted');
        }

        // The merged document holds every input's pages at once, so the budget
        // is on the combined size, not on each file.
        var total = inputs.reduce(function (sum, f) { return sum + f.size; }, 0);
        if (total > (L.constrained() ? 50 : 150) * 1024 * 1024) {
            throw new L.Unsupported('combined input exceeds the on-device merge budget', 'resource_budget_exceeded');
        }

        var PDFLib = await L.loadPdfLib();
        var merged = await PDFLib.PDFDocument.create({ updateMetadata: false });

        // One source document is alive at a time; its pages are copied into
        // `merged` and the source is then released.
        for (var i = 0; i < inputs.length; i++) {
            L.checkAbort(ctx.signal);
            var src = await loadDoc(PDFLib, inputs[i], null);
            var copied = await merged.copyPages(src, src.getPageIndices());
            copied.forEach(function (page) { merged.addPage(page); });
            src = null;
            if (ctx.onProgress) ctx.onProgress(i + 1, inputs.length);
            await L.tick();
        }

        L.checkAbort(ctx.signal);
        return {
            blob: await save(merged),
            filename: 'merged_' + L.hexId(8) + '.pdf',
            message: 'PDFs merged',
        };
    });

    // ── /api/pdf/extract-pages ────────────────────────────────────────────

    L.register('/api/pdf/extract-pages', async function (fd) {
        var file = only(fd);
        var PDFLib = await L.loadPdfLib();
        var src = await loadDoc(PDFLib, file, L.str(fd, 'password', null));

        var indices = parsePageSelection(L.str(fd, 'pages', null), src.getPageCount());
        var out = await PDFLib.PDFDocument.create({ updateMetadata: false });
        var copied = await out.copyPages(src, indices);
        copied.forEach(function (page) { out.addPage(page); });

        return {
            blob: await save(out),
            filename: L.brandedName(file.name, 'pdf'),
            message: 'Pages extracted',
        };
    });

    // ── /api/pdf/split ────────────────────────────────────────────────────
    //
    // Mirrors pdf_utils.py::split_pdf_to_zip. Pages are copied structurally (no
    // rasterising), one output PDF per group, packed into a ZIP.

    // pdf_utils.py::MAX_PDF_RENDER_PAGES - the server refuses larger documents,
    // so refusing here too keeps the two routes indistinguishable.
    var SPLIT_MAX_PAGES = 200;
    // Local memory budgets (work package 01 section 5). Above these the browser
    // is not a safe place to hold input, parts and archive at once, so the
    // operation is offered to the server instead (after the user agrees).
    var SPLIT_MAX_BYTES_DESKTOP = 150 * 1024 * 1024;
    var SPLIT_MAX_BYTES_MOBILE = 50 * 1024 * 1024;

    function pad3(n) {
        var t = String(n);
        while (t.length < 3) t = '0' + t;
        return t;
    }

    /** pdf_utils.py::_split_pdf_member_name */
    function splitMemberName(indices) {
        var start = indices[0] + 1;
        var end = indices[indices.length - 1] + 1;
        if (indices.length === 1) return 'page-' + pad3(start) + '.pdf';
        return 'pages-' + pad3(start) + '-' + pad3(end) + '.pdf';
    }

    /** pdf_utils.py::_split_pdf_groups - zero-based page groups per mode. */
    function splitGroups(total, mode, ranges, n) {
        if (total > SPLIT_MAX_PAGES) {
            throw new L.Error('PDF has too many pages to split at once (max ' + SPLIT_MAX_PAGES + ').');
        }
        mode = String(mode === null || mode === undefined || mode === '' ? 'each' : mode).trim().toLowerCase();
        var groups = [];
        var i;

        if (mode === 'each') {
            for (i = 0; i < total; i++) groups.push([i]);
            return groups;
        }

        if (mode === 'every_n') {
            var size = Number(n);
            size = isFinite(size) ? Math.trunc(size) : 0;
            if (size < 1) throw new L.Error('Split size must be at least 1 page.');
            for (var start = 0; start < total; start += size) {
                var group = [];
                for (i = start; i < Math.min(start + size, total); i++) group.push(i);
                groups.push(group);
            }
            return groups;
        }

        if (mode === 'ranges') {
            if (!ranges || !String(ranges).trim()) {
                throw new L.Error('Provide one or more page ranges to split.');
            }
            String(ranges).split(',').forEach(function (segment) {
                segment = segment.trim();
                if (segment) groups.push(parsePageSelection(segment, total));
            });
            if (!groups.length) throw new L.Error('Provide one or more page ranges to split.');
            return groups;
        }

        throw new L.Error('mode must be one of: each, every_n, ranges');
    }

    /**
     * Overlapping ranges ("1-2,1-2") would name two members identically; ZIP
     * extractors then overwrite one with the other. Suffix repeats so every
     * requested part survives, in the order requested.
     */
    function uniqueMemberNames(groups) {
        var used = {};
        return groups.map(function (indices) {
            var name = splitMemberName(indices);
            if (used[name]) {
                var base = name.replace(/\.pdf$/, '');
                var k = used[name] + 1;
                while (used[base + '-' + k + '.pdf']) k++;
                used[name] = k;
                name = base + '-' + k + '.pdf';
            }
            used[name] = used[name] || 1;
            return name;
        });
    }

    L.register('/api/pdf/split', async function (fd, ctx) {
        ctx = ctx || {};
        var file = only(fd);

        if (L.str(fd, 'password', null)) {
            throw new L.Unsupported('password-protected PDFs need the server', 'encrypted');
        }

        // Validate the options before reading a byte of the file.
        var mode = L.str(fd, 'mode', 'each');
        var ranges = L.str(fd, 'ranges', null);
        var n = L.int(fd, 'n', null);
        var modeKey = String(mode).trim().toLowerCase();
        if (['each', 'every_n', 'ranges'].indexOf(modeKey) < 0) {
            throw new L.Error('mode must be one of: each, every_n, ranges');
        }
        if (modeKey === 'every_n' && !(n >= 1)) throw new L.Error('Split size must be at least 1 page.');
        if (modeKey === 'ranges' && (!ranges || !ranges.trim())) {
            throw new L.Error('Provide one or more page ranges to split.');
        }

        var limit = L.constrained() ? SPLIT_MAX_BYTES_MOBILE : SPLIT_MAX_BYTES_DESKTOP;
        if (file.size > limit) {
            throw new L.Unsupported('input exceeds the on-device split budget', 'resource_budget_exceeded');
        }

        var PDFLib = await L.loadPdfLib();
        var JSZip = await L.loadJsZip();
        var src = await loadDoc(PDFLib, file, null);

        var groups = splitGroups(src.getPageCount(), mode, ranges, n);
        var names = uniqueMemberNames(groups);
        var zip = new JSZip();

        for (var g = 0; g < groups.length; g++) {
            L.checkAbort(ctx.signal);
            var part = await PDFLib.PDFDocument.create({ updateMetadata: false });
            var copied = await part.copyPages(src, groups[g]);
            copied.forEach(function (page) { part.addPage(page); });
            zip.file(names[g], await part.save());
            if (ctx.onProgress) ctx.onProgress(g + 1, groups.length);
            await L.tick();
        }

        L.checkAbort(ctx.signal);
        var blob = await zip.generateAsync({ type: 'blob', compression: 'DEFLATE' });
        return {
            blob: blob,
            filename: L.brandedName(file.name, 'zip'),
            message: 'PDF split into ' + groups.length + ' file(s)',
            extra: { file_count: groups.length },
        };
    });

    // ── /api/pdf/rotate ───────────────────────────────────────────────────

    L.register('/api/pdf/rotate', async function (fd) {
        var angle = L.int(fd, 'angle', null);
        if ([90, 180, 270, -90, -180, -270].indexOf(angle) < 0) {
            throw new L.Error('Angle must be 90, 180, 270, -90, -180, or -270 degrees.');
        }

        var file = only(fd);
        var PDFLib = await L.loadPdfLib();
        var doc = await loadDoc(PDFLib, file, L.str(fd, 'password', null));

        var pages = L.str(fd, 'pages', null);
        var indices = pages === null
            ? doc.getPageIndices()
            : parsePageSelection(pages, doc.getPageCount());

        indices.forEach(function (idx) {
            var page = doc.getPage(idx);
            var current = page.getRotation().angle || 0;
            // JS % keeps the sign of the dividend where Python's does not, so
            // -90 on an unrotated page would give -90 rather than 270.
            var next = ((current + angle) % 360 + 360) % 360;
            page.setRotation(PDFLib.degrees(next));
        });

        return {
            blob: await save(doc),
            filename: L.brandedName(file.name, 'pdf'),
            message: 'PDF rotated by ' + angle + '°',
        };
    });

    // ── /api/pdf/organize ─────────────────────────────────────────────────

    L.register('/api/pdf/organize', async function (fd) {
        var raw = String(L.str(fd, 'page_order', '') || '').trim();
        var order;
        if (raw.charAt(0) === '[') {
            try {
                order = JSON.parse(raw);
            } catch (e) {
                throw new L.Error('page_order is not a valid list of page numbers.');
            }
        } else {
            order = raw.split(',')
                .map(function (s) { return s.trim(); })
                .filter(function (s) { return s; })
                .map(function (s) {
                    if (!/^\d+$/.test(s)) throw new L.Error("invalid literal for int(): '" + s + "'");
                    return parseInt(s, 10);
                });
        }
        if (!order || !order.length) throw new L.Error('page_order cannot be empty.');

        var file = only(fd);
        var PDFLib = await L.loadPdfLib();
        var src = await loadDoc(PDFLib, file, L.str(fd, 'password', null));
        var total = src.getPageCount();

        order.forEach(function (pnum) {
            if (typeof pnum !== 'number' || !Number.isInteger(pnum) || pnum < 1 || pnum > total) {
                throw new L.Error('Page number ' + pnum + ' is out of range (document has ' + total + ' pages).');
            }
        });

        // copyPages() with a repeated index returns independent copies, which is
        // what makes "1,1,2" duplicate rather than alias a single page.
        var out = await PDFLib.PDFDocument.create({ updateMetadata: false });
        var copied = await out.copyPages(src, order.map(function (p) { return p - 1; }));
        copied.forEach(function (page) { out.addPage(page); });

        return {
            blob: await save(out),
            filename: L.brandedName(file.name, 'pdf'),
            message: 'PDF organized (' + order.length + ' pages in output)',
        };
    });

    // ── /api/pdf/add-page-numbers ─────────────────────────────────────────

    var NUMBER_POSITIONS = [
        'bottom-center', 'bottom-left', 'bottom-right',
        'top-center', 'top-left', 'top-right',
    ];

    function toRoman(n) {
        var table = [[1000, 'M'], [900, 'CM'], [500, 'D'], [400, 'CD'],
        [100, 'C'], [90, 'XC'], [50, 'L'], [40, 'XL'],
        [10, 'X'], [9, 'IX'], [5, 'V'], [4, 'IV'], [1, 'I']];
        var out = '';
        for (var i = 0; i < table.length; i++) {
            while (n >= table[i][0]) { out += table[i][1]; n -= table[i][0]; }
        }
        return out;
    }

    function pageLabel(fmt, pageNum) {
        if (fmt === 'roman') return toRoman(pageNum);
        if (fmt === 'alpha') return pageNum <= 26 ? String.fromCharCode(64 + pageNum) : String(pageNum);
        return String(pageNum);
    }

    L.register('/api/pdf/add-page-numbers', async function (fd) {
        var position = L.str(fd, 'position', 'bottom-center');
        if (NUMBER_POSITIONS.indexOf(position) < 0) {
            throw new L.Error('position must be one of: ' + NUMBER_POSITIONS.slice().sort().join(', '));
        }
        var fmt = L.str(fd, 'fmt', 'decimal');
        if (['decimal', 'roman', 'alpha'].indexOf(fmt) < 0) {
            throw new L.Error("fmt must be 'decimal', 'roman', or 'alpha'.");
        }
        var startNumber = L.int(fd, 'start_number', 1);
        if (startNumber < 1) throw new L.Error('start_number must be >= 1.');
        var fontSize = L.int(fd, 'font_size', 12);
        if (fontSize < 4 || fontSize > 72) throw new L.Error('font_size must be between 4 and 72.');
        var skipFirst = L.int(fd, 'skip_first', 0);

        var file = only(fd);
        var PDFLib = await L.loadPdfLib();
        var doc = await loadDoc(PDFLib, file, L.str(fd, 'password', null));
        var font = await doc.embedFont(PDFLib.StandardFonts.Helvetica);

        var pages = doc.getPages();
        for (var i = 0; i < pages.length; i++) {
            if (i < skipFirst) continue;
            var label = pageLabel(fmt, startNumber + (i - skipFirst));
            var size = pages[i].getSize();
            var margin = 20;

            // add_page_numbers() works in PyMuPDF's top-left origin; pdf-lib
            // uses PDF's native bottom-left, so each y is mirrored about the
            // page height. The x formulas are origin-independent and are the
            // server's own (deliberately rough) width estimates, kept as-is so
            // the numbers land in the same place.
            var y = position.indexOf('bottom') === 0
                ? margin                                   // was height - margin
                : size.height - margin - fontSize;         // was margin + fontSize
            var x;
            if (position.indexOf('left') >= 0) x = margin;
            else if (position.indexOf('right') >= 0) x = size.width - margin - fontSize * label.length * 0.5;
            else x = size.width / 2 - fontSize * label.length * 0.25;

            pages[i].drawText(label, {
                x: x, y: y, size: fontSize, font: font,
                color: PDFLib.rgb(0, 0, 0),
            });
        }

        return {
            blob: await save(doc),
            filename: L.brandedName(file.name, 'pdf'),
            message: 'Page numbers added',
        };
    });

    // ── /api/pdf/watermark ────────────────────────────────────────────────

    L.register('/api/pdf/watermark', async function (fd) {
        var text = L.str(fd, 'text', '');
        if (!text || !text.trim()) throw new L.Error('Watermark text cannot be empty.');

        var position = L.str(fd, 'position', 'diagonal');
        if (['diagonal', 'top', 'center', 'bottom'].indexOf(position) < 0) {
            throw new L.Error('Position must be one of: diagonal, top, center, bottom.');
        }
        var opacity = L.num(fd, 'opacity', 0.3);
        if (!(opacity >= 0.05 && opacity <= 1.0)) {
            throw new L.Error('Opacity must be between 0.1 and 1.0.');
        }

        var file = only(fd);
        var PDFLib = await L.loadPdfLib();
        var doc = await loadDoc(PDFLib, file, L.str(fd, 'password', null));
        var font = await doc.embedFont(PDFLib.StandardFonts.Helvetica);
        var grey = PDFLib.rgb(0.5, 0.5, 0.5);

        doc.getPages().forEach(function (page) {
            var size = page.getSize();
            var fontSize = Math.max(24, Math.trunc(size.width / 12));

            if (position === 'diagonal') {
                // add_watermark() rotates about the page centre. pdf-lib rotates
                // about the text origin instead, so step back half the string's
                // width along the 45° axis to land the middle of the text on the
                // middle of the page.
                var width = font.widthOfTextAtSize(text, fontSize);
                var half = width / 2;
                var diag = Math.SQRT1_2; // cos(45°) == sin(45°)
                page.drawText(text, {
                    x: size.width / 2 - half * diag,
                    y: size.height / 2 - half * diag,
                    size: fontSize, font: font, color: grey, opacity: opacity,
                    rotate: PDFLib.degrees(45),
                });
                return;
            }

            var fromTop = position === 'top' ? size.height * 0.1
                : position === 'center' ? size.height / 2
                    : size.height * 0.9;
            // The server's own rough width estimate, kept so placement matches.
            var estimated = fontSize * 0.5 * text.length;
            page.drawText(text, {
                x: Math.max(10, (size.width - estimated) / 2),
                y: size.height - fromTop,
                size: fontSize, font: font, color: grey, opacity: opacity,
            });
        });

        return {
            blob: await save(doc),
            filename: L.brandedName(file.name, 'pdf'),
            message: 'Watermark added',
        };
    });

    // ── /api/image/to-pdf ─────────────────────────────────────────────────

    /**
     * Get embeddable bytes plus the oriented pixel size for one image.
     *
     * PNGs go in untouched so transparency survives (they carry no EXIF
     * orientation worth honouring). Everything else is redrawn through a canvas
     * first: that bakes in EXIF orientation — which `_oriented_for_pdf()` does
     * server-side, and which pdf-lib's raw JPEG embedding would otherwise
     * ignore — and converts formats pdf-lib can't embed at all (WebP, BMP, GIF).
     */
    function embeddable(file) {
        return new Promise(function (fulfil, fail) {
            var url = URL.createObjectURL(file);
            var img = new Image();
            img.onload = function () {
                URL.revokeObjectURL(url);
                var w = img.naturalWidth, h = img.naturalHeight;
                if (!w || !h) {
                    fail(new L.Error('Image ' + file.name + ' has zero-dimension (width=' + w + ', height=' + h + ').'));
                    return;
                }
                if (file.type === 'image/png') {
                    file.arrayBuffer().then(function (buf) {
                        fulfil({ kind: 'png', bytes: buf, width: w, height: h });
                    }, fail);
                    return;
                }
                var canvas = document.createElement('canvas');
                canvas.width = w;
                canvas.height = h;
                canvas.getContext('2d').drawImage(img, 0, 0);
                canvas.toBlob(function (blob) {
                    if (!blob) {
                        fail(new L.Unsupported('canvas could not re-encode ' + file.name));
                        return;
                    }
                    blob.arrayBuffer().then(function (buf) {
                        fulfil({ kind: 'jpg', bytes: buf, width: w, height: h });
                    }, fail);
                }, 'image/jpeg', 0.95);
            };
            img.onerror = function () {
                URL.revokeObjectURL(url);
                fail(new L.Unsupported('browser cannot decode ' + file.name));
            };
            img.src = url;
        });
    }

    L.register('/api/image/to-pdf', async function (fd) {
        var inputs = L.files(fd, 'file').concat(L.files(fd, 'files'));
        if (!inputs.length) throw new L.Error('At least one image file is required.');

        var marginPt = L.range('margin_pt', L.int(fd, 'margin_pt', 36), 0, 200);
        var sizeName = String(L.str(fd, 'page_size', 'A4')).toLowerCase();
        var fitMode = L.str(fd, 'fit_mode', 'fit');

        var PDFLib = await L.loadPdfLib();
        var doc = await PDFLib.PDFDocument.create({ updateMetadata: false });

        for (var i = 0; i < inputs.length; i++) {
            var img = await embeddable(inputs[i]);
            var embedded = img.kind === 'png'
                ? await doc.embedPng(img.bytes)
                : await doc.embedJpg(img.bytes);

            // 'auto' uses the image's own pixel dimensions as points, exactly as
            // images_to_pdf() does.
            var dims = sizeName === 'auto' ? [img.width, img.height] : pageSize(sizeName);
            var pw = dims[0], ph = dims[1];
            var availableW = pw - 2 * marginPt;
            var availableH = ph - 2 * marginPt;

            var drawW, drawH;
            if (fitMode === 'original') {
                drawW = Math.min(img.width, availableW);
                drawH = drawW * (img.height / img.width);
            } else {
                // Note images_to_pdf() treats 'stretch' the same as 'fit' — it
                // never distorts. Matched here rather than "fixed", so the two
                // paths agree.
                var scale = Math.min(availableW / img.width, availableH / img.height);
                drawW = img.width * scale;
                drawH = img.height * scale;
            }

            doc.addPage([pw, ph]).drawImage(embedded, {
                x: marginPt + (availableW - drawW) / 2,
                y: marginPt + (availableH - drawH) / 2,
                width: drawW,
                height: drawH,
            });
        }

        return {
            blob: await save(doc),
            filename: 'images_to_pdf_' + L.hexId(8) + '.pdf',
            message: 'Created PDF from ' + inputs.length + ' image(s)',
        };
    });

    // ── /api/pdf/create-blank ─────────────────────────────────────────────

    L.register('/api/pdf/create-blank', async function (fd) {
        var numPages = L.int(fd, 'num_pages', 1);
        if (!(numPages >= 1 && numPages <= 100)) {
            throw new L.Error('num_pages must be between 1 and 100.');
        }
        var dims = pageSize(L.str(fd, 'page_size', 'A4'));

        var PDFLib = await L.loadPdfLib();
        var doc = await PDFLib.PDFDocument.create({ updateMetadata: false });
        for (var i = 0; i < numPages; i++) doc.addPage([dims[0], dims[1]]);

        return {
            blob: await save(doc),
            filename: 'blank_' + numPages + 'pages_' + L.hexId(6) + '.pdf',
            message: 'Created blank PDF with ' + numPages + ' page(s)',
        };
    });

    // ── /api/pdf/create-from-text ─────────────────────────────────────────

    /** create_pdf_from_text()'s filename rule: keep alnum/space/_/-, cap at 50. */
    function safeTitle(title) {
        var kept = String(title === null || title === undefined ? '' : title)
            .split('')
            .filter(function (ch) { return /[0-9A-Za-z]/.test(ch) || ch === ' ' || ch === '_' || ch === '-'; })
            .join('')
            .slice(0, 50);
        return (kept || 'document').replace(/ /g, '_');
    }

    /** Greedy word wrap against the embedded font's real metrics. */
    function wrap(font, text, fontSize, maxWidth) {
        var words = text.split(/\s+/).filter(function (w) { return w; });
        if (!words.length) return [];
        var lines = [];
        var line = words[0];
        for (var i = 1; i < words.length; i++) {
            var candidate = line + ' ' + words[i];
            if (font.widthOfTextAtSize(candidate, fontSize) <= maxWidth) {
                line = candidate;
            } else {
                lines.push(line);
                line = words[i];
            }
        }
        lines.push(line);
        return lines;
    }

    L.register('/api/pdf/create-from-text', async function (fd) {
        var content = L.str(fd, 'content', '');
        if (!content || !content.trim()) throw new L.Error('Content cannot be empty.');

        var title = L.str(fd, 'title', 'Document');
        var fontSize = L.int(fd, 'font_size', 12);
        var marginPt = L.int(fd, 'margin_pt', 72);
        var dims = pageSize(L.str(fd, 'page_size', 'A4'));

        var PDFLib = await L.loadPdfLib();
        var doc = await PDFLib.PDFDocument.create({ updateMetadata: false });
        doc.setTitle(title);
        var font = await doc.embedFont(PDFLib.StandardFonts.Helvetica);

        // Mirrors the reportlab ParagraphStyle: leading 1.4x, 6pt after each
        // paragraph, and a half-line gap for a blank source line.
        var leading = fontSize * 1.4;
        var spaceAfter = 6;
        var maxWidth = dims[0] - 2 * marginPt;

        var page = doc.addPage([dims[0], dims[1]]);
        var y = dims[1] - marginPt;

        function newPage() {
            page = doc.addPage([dims[0], dims[1]]);
            y = dims[1] - marginPt;
        }

        try {
            var paragraphs = content.split('\n');
            for (var p = 0; p < paragraphs.length; p++) {
                if (!paragraphs[p].trim()) {
                    y -= fontSize * 0.5;
                    continue;
                }
                var lines = wrap(font, paragraphs[p], fontSize, maxWidth);
                for (var i = 0; i < lines.length; i++) {
                    if (y - leading < marginPt) newPage();
                    y -= leading;
                    page.drawText(lines[i], {
                        x: marginPt, y: y, size: fontSize, font: font,
                        color: PDFLib.rgb(0, 0, 0),
                    });
                }
                y -= spaceAfter;
            }
        } catch (err) {
            // StandardFonts.Helvetica is WinAnsi-only; text outside it (emoji,
            // CJK, most non-Latin scripts) throws here. reportlab's Helvetica
            // has the same limit, but the server has fonts we don't, so let it
            // try rather than shipping a mangled document.
            throw new L.Unsupported('text contains characters the built-in font cannot encode');
        }

        return {
            blob: await save(doc),
            filename: safeTitle(title) + '_' + L.hexId(6) + '.pdf',
            message: 'PDF created from text',
        };
    });

    // Exposed for the unit tests, which exercise the pure logic directly.
    L.pdf = {
        parsePageSelection: parsePageSelection,
        splitGroups: splitGroups,
        splitMemberName: splitMemberName,
        uniqueMemberNames: uniqueMemberNames,
        toRoman: toRoman,
        pageLabel: pageLabel,
        safeTitle: safeTitle,
        pageSize: pageSize,
    };
})();
