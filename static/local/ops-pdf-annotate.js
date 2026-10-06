// On-device PDF annotation (migration work package 04).
//
// Mirrors pdf_utils.py::annotate_pdf for the annotation types a browser can add
// faithfully with pdf-lib: highlight, underline, strikeout, note and text.
// Coordinates are the server's: PyMuPDF page space, origin top-left, y down, in
// points.
//
// `redact` is deliberately NOT handled here. A redaction must permanently remove
// the content underneath; drawing a black box over it leaves the text
// extractable. The handler declines (`redaction_server`) so the server, which
// really removes it, is used after the user agrees.
(function () {
    'use strict';

    var L = window.ffLocal;
    if (!L) return;

    var VALID_TYPES = ['highlight', 'underline', 'strikeout', 'note', 'text', 'redact'];
    var MIB = 1024 * 1024;

    // PyMuPDF's default stroke colours for each markup type.
    var DEFAULT_COLOR = {
        highlight: [1, 1, 0],
        underline: [0, 1, 0],
        strikeout: [1, 0, 0],
        note: [1, 1, 0],
    };

    function bytesOf(file) {
        if (file.arrayBuffer) return file.arrayBuffer();
        return new Promise(function (fulfil, fail) {
            var reader = new FileReader();
            reader.onload = function () { fulfil(reader.result); };
            reader.onerror = function () { fail(reader.error); };
            reader.readAsArrayBuffer(file);
        });
    }

    function isNumber(v) {
        return typeof v === 'number' && isFinite(v);
    }

    /** Validate one annotation exactly as the server does, normalising its fields. */
    function normalise(ann, totalPages) {
        if (!ann || typeof ann !== 'object' || Array.isArray(ann)) {
            throw new L.Error('Each annotation must be an object.');
        }
        var type = String(ann.type === undefined || ann.type === null ? '' : ann.type).toLowerCase();
        if (VALID_TYPES.indexOf(type) < 0) {
            throw new L.Error("Unknown annotation type '" + type + "'. Must be one of: " + VALID_TYPES.slice().sort().join(', '));
        }

        var rawPage = ann.page === undefined || ann.page === null ? 1 : ann.page;
        var page = typeof rawPage === 'number' ? Math.trunc(rawPage) : parseInt(String(rawPage).trim(), 10);
        if (!isFinite(page) || (typeof rawPage === 'string' && !/^[+-]?\d+$/.test(rawPage.trim()))) {
            throw new L.Error('Annotation page must be a whole number.');
        }
        if (page < 1 || page > totalPages) {
            throw new L.Error('Page ' + page + ' is out of range (document has ' + totalPages + ' pages).');
        }

        var rect = ann.rect === undefined || ann.rect === null ? [0, 0, 100, 20] : ann.rect;
        if (!Array.isArray(rect) || rect.length !== 4 || !rect.every(isNumber)) {
            throw new L.Error('Annotation rect must be four numbers: [x0, y0, x1, y1].');
        }

        var color = ann.color === undefined || ann.color === null ? DEFAULT_COLOR[type] : ann.color;
        if (color) {
            if (!Array.isArray(color) || color.length !== 3 || !color.every(function (c) { return isNumber(c) && c >= 0 && c <= 1; })) {
                throw new L.Error('Annotation color must be three numbers between 0 and 1.');
            }
        }

        var content = ann.content === undefined || ann.content === null ? '' : String(ann.content);
        return { type: type, page: page, rect: rect, color: color, content: content };
    }

    /** Top-left/bottom-right order regardless of how the corners were given. */
    function ordered(rect) {
        return {
            x0: Math.min(rect[0], rect[2]), y0: Math.min(rect[1], rect[3]),
            x1: Math.max(rect[0], rect[2]), y1: Math.max(rect[1], rect[3]),
        };
    }

    /** PyMuPDF space (top-left, y down) to PDF user space on an unrotated page. */
    function toPdf(cropBox, x, y) {
        return [cropBox.x + x, cropBox.y + cropBox.height - y];
    }

    function appearanceStream(PDFLib, context, bbox, ops, blendMultiply) {
        var N = PDFLib.PDFName.of;
        var resources = context.obj(blendMultiply
            ? { ExtGState: { GS0: { Type: 'ExtGState', BM: 'Multiply' } } }
            : {});
        var stream = context.flateStream(ops, {
            Type: 'XObject',
            Subtype: 'Form',
            BBox: bbox,
            Resources: resources,
        });
        return context.register(stream);
    }

    function colourOps(c) {
        return c.map(function (v) { return Number(v.toFixed(4)); }).join(' ');
    }

    function addMarkup(PDFLib, doc, page, ann) {
        var context = doc.context;
        var N = PDFLib.PDFName.of;
        var cb = page.getCropBox();
        var r = ordered(ann.rect);
        var tl = toPdf(cb, r.x0, r.y0);
        var tr = toPdf(cb, r.x1, r.y0);
        var bl = toPdf(cb, r.x0, r.y1);
        var br = toPdf(cb, r.x1, r.y1);
        var llx = bl[0], lly = bl[1], urx = tr[0], ury = tr[1];
        var subtype = { highlight: 'Highlight', underline: 'Underline', strikeout: 'StrikeOut' }[ann.type];
        var c = ann.color;

        var ops;
        if (ann.type === 'highlight') {
            ops = '/GS0 gs ' + colourOps(c) + ' rg ' + llx + ' ' + lly + ' ' + (urx - llx) + ' ' + (ury - lly) + ' re f';
        } else {
            var y = ann.type === 'underline' ? lly + 1 : (lly + ury) / 2;
            ops = colourOps(c) + ' RG 1 w ' + llx + ' ' + y + ' m ' + urx + ' ' + y + ' l S';
        }
        var ap = appearanceStream(PDFLib, context, [llx, lly, urx, ury], ops, ann.type === 'highlight');

        var dict = context.obj({
            Type: 'Annot',
            Subtype: subtype,
            Rect: [llx, lly, urx, ury],
            // Acrobat's quad order: top-left, top-right, bottom-left, bottom-right.
            QuadPoints: [tl[0], tl[1], tr[0], tr[1], bl[0], bl[1], br[0], br[1]],
            C: c,
            CA: 1,
            F: 4,
            AP: { N: ap },
        });
        page.node.addAnnot(context.register(dict));
    }

    function addNote(PDFLib, doc, page, ann) {
        var context = doc.context;
        var cb = page.getCropBox();
        var r = ordered(ann.rect);
        var tl = toPdf(cb, r.x0, r.y0);
        // PyMuPDF's add_text_annot(point, text): a 16x16 icon whose top-left is the point.
        var dict = context.obj({
            Type: 'Annot',
            Subtype: 'Text',
            Rect: [tl[0], tl[1] - 16, tl[0] + 16, tl[1]],
            Contents: PDFLib.PDFHexString.fromText(ann.content),
            Name: 'Note',
            C: ann.color,
            F: 28,
            Open: false,
        });
        page.node.addAnnot(context.register(dict));
    }

    /** Break text into lines no wider than maxWidth, honouring existing newlines. */
    function wrap(font, text, size, maxWidth) {
        var out = [];
        String(text).split(/\r?\n/).forEach(function (paragraph) {
            var words = paragraph.split(/\s+/).filter(Boolean);
            if (!words.length) { out.push(''); return; }
            var line = words[0];
            for (var i = 1; i < words.length; i++) {
                var next = line + ' ' + words[i];
                if (font.widthOfTextAtSize(next, size) <= maxWidth) line = next;
                else { out.push(line); line = words[i]; }
            }
            out.push(line);
        });
        return out;
    }

    var TEXT_SIZE = 11;
    var LINE_HEIGHT = TEXT_SIZE * 1.2;

    function addText(PDFLib, page, font, ann) {
        var cb = page.getCropBox();
        var r = ordered(ann.rect);
        if (!ann.content) return;
        var lines;
        try {
            lines = wrap(font, ann.content, TEXT_SIZE, r.x1 - r.x0);
            // Encoding failures (characters outside Helvetica's WinAnsi set) surface here.
            lines.forEach(function (line) { font.widthOfTextAtSize(line, TEXT_SIZE); });
        } catch (e) {
            throw new L.Unsupported('text contains characters the built-in font cannot encode', 'unsupported_structure');
        }
        if (lines.length * LINE_HEIGHT > (r.y1 - r.y0) + 0.01) {
            throw new L.Error('Text does not fit in the selected rectangle. Make the rectangle larger or the text shorter.');
        }
        var top = toPdf(cb, r.x0, r.y0);
        lines.forEach(function (line, i) {
            if (!line) return;
            page.drawText(line, {
                x: top[0], y: top[1] - TEXT_SIZE * 1.1 - i * LINE_HEIGHT, // 1.1: glyph ascent, so ink stays inside the box
                size: TEXT_SIZE, font: font, color: PDFLib.rgb(0, 0, 0),
            });
        });
    }

    L.register('/api/pdf/annotate', async function (fd, ctx) {
        ctx = ctx || {};
        var files = L.files(fd, 'file');
        if (!files.length) throw new L.Error('No file provided.');
        var file = files[0];

        if (L.str(fd, 'password', null)) {
            throw new L.Unsupported('password-protected PDFs need the server', 'encrypted');
        }

        var raw = fd.get('annotations');
        if (raw === null || raw === undefined || String(raw) === '') {
            throw new L.Error('annotations is required.');
        }
        var list;
        try {
            list = JSON.parse(String(raw));
        } catch (e) {
            throw new L.Error('annotations must be valid JSON.');
        }
        if (!Array.isArray(list)) throw new L.Error('annotations must be a JSON array.');

        var limit = L.constrained() ? 50 * MIB : 150 * MIB;
        if (file.size > limit) {
            throw new L.Unsupported('input exceeds the on-device annotate budget', 'resource_budget_exceeded');
        }

        var PDFLib = await L.loadPdfLib();
        var doc;
        try {
            doc = await PDFLib.PDFDocument.load(new Uint8Array(await bytesOf(file)));
        } catch (err) {
            var enc = (err && err.name || '').indexOf('Encrypted') >= 0 || /encrypt/i.test((err && err.message) || '');
            throw new L.Unsupported('pdf-lib could not open this PDF', enc ? 'encrypted' : 'unsupported_structure');
        }

        if (!doc.catalog || doc.getPageCount() < 1) {
            throw new L.Unsupported('PDF structure could not be read', 'unsupported_structure');
        }
        var total = doc.getPageCount();
        var items = list.map(function (ann) { return normalise(ann, total); });

        // Input errors are reported before asking about the server.
        if (items.some(function (a) { return a.type === 'redact'; })) {
            throw new L.Unsupported('redaction must remove content, which needs the server', 'redaction_server');
        }
        var rotated = items.some(function (a) { return (doc.getPage(a.page - 1).getRotation().angle || 0) % 360 !== 0; });
        if (rotated) {
            throw new L.Unsupported('annotating rotated pages is not supported on-device', 'unsupported_structure');
        }

        var font = items.some(function (a) { return a.type === 'text'; })
            ? await doc.embedFont(PDFLib.StandardFonts.Helvetica) : null;

        for (var i = 0; i < items.length; i++) {
            L.checkAbort(ctx.signal);
            var ann = items[i];
            var page = doc.getPage(ann.page - 1);
            if (ann.type === 'note') addNote(PDFLib, doc, page, ann);
            else if (ann.type === 'text') addText(PDFLib, page, font, ann);
            else addMarkup(PDFLib, doc, page, ann);
            if (ctx.onProgress) ctx.onProgress(i + 1, items.length);
        }

        L.checkAbort(ctx.signal);
        return {
            blob: new Blob([await doc.save()], { type: 'application/pdf' }),
            filename: L.brandedName(file.name, 'pdf'),
            message: 'Added ' + list.length + ' annotation(s)',
        };
    });

    L.annotate = { normalise: normalise };
})();
