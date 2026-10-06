// On-device PDF to EPUB (migration work package 16).
//
// Route: /api/pdf/to-epub. Same response contract as the Python endpoint
// (pdf_utils.py::pdf_to_epub): a branded `.epub` named `<stem>_forgefiles.org.epub`
// and the message "PDF converted to EPUB".
//
// Output is a reflowable EPUB 3 package (with an EPUB 2 NCX for older readers):
//   * paragraphs rebuilt from lines, not one <p> per line,
//   * headings by font size (same thresholds as the server: 1.45x -> h2, 1.15x -> h3),
//   * bold, italic and monospace runs, bullet and numbered lists,
//   * hyperlinks (http, https, mailto only),
//   * embedded images (de-duplicated by PDF object, scan-backgrounds skipped),
//   * a table of contents from the PDF bookmarks, else from headings,
//   * a small JPEG cover of page 1.
//
// Differences from the server, deliberate and recorded in the CHANGELOG:
//   * no "Page N" heading injected into every chapter, and chapters follow the
//     document's structure rather than its page breaks;
//   * running heads, footers and page numbers are dropped (they would otherwise
//     land in the middle of paragraphs that continue across a page);
//   * the cover is a 600 px JPEG, not a 150 dpi page raster.
//
// Declined to the server (always after asking): password-protected PDFs, scanned
// pages (the server runs OCR), right-to-left or mostly rotated text, three or
// more text columns, and anything past the on-device size budgets.
(function () {
    'use strict';

    var L = window.ffLocal;
    if (!L) return;

    var MIB = 1024 * 1024;
    var BUDGET = {
        desktop: { bytes: 100 * MIB, pages: 1000, imageBytes: 150 * MIB, images: 600 },
        mobile: { bytes: 25 * MIB, pages: 300, imageBytes: 40 * MIB, images: 150 },
    };
    var COVER_WIDTH = 600;
    var CHAPTER_SPLIT_BYTES = 250 * 1024; // keep each XHTML file small enough for e-readers

    // ── XML helpers ───────────────────────────────────────────────────────

    /** Strip characters XML 1.0 cannot carry (control codes, lone surrogates). */
    function xmlSafe(s) {
        var out = '';
        s = String(s);
        for (var i = 0; i < s.length; i++) {
            var c = s.charCodeAt(i);
            if (c === 9 || c === 10 || c === 13 || (c >= 0x20 && c <= 0xD7FF) || (c >= 0xE000 && c <= 0xFFFD)) {
                out += s.charAt(i);
            } else if (c >= 0xD800 && c <= 0xDBFF) {
                var d = s.charCodeAt(i + 1);
                if (d >= 0xDC00 && d <= 0xDFFF) { out += s.charAt(i) + s.charAt(i + 1); i++; }
            }
        }
        return out;
    }

    function esc(s) {
        return xmlSafe(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    }

    function safeHref(url) {
        var u = String(url || '').trim();
        return /^(https?:|mailto:)/i.test(u) ? u : null;
    }

    function runsToHtml(runs, noBold) {
        return runs.map(function (r) {
            // A wide gap in the PDF (a tab stop, a right-aligned date) becomes an em space.
            var text = (r.tab ? ' ' : '') + r.text;
            if (!text) return '';
            // Spaces belong outside the styling: "<strong>bold</strong> and", not "<strong>bold </strong>and".
            var m = /^(\s*)([\s\S]*?)(\s*)$/.exec(text);
            if (!m[2]) return esc(text);
            var h = esc(m[2]);
            if (r.mono) h = '<code>' + h + '</code>';
            if (r.bold && !noBold) h = '<strong>' + h + '</strong>';
            if (r.italic) h = '<em>' + h + '</em>';
            var href = safeHref(r.href);
            if (href) h = '<a href="' + esc(href) + '">' + h + '</a>';
            return esc(m[1]) + h + esc(m[3]);
        }).join('');
    }

    function blockPlain(block) {
        return block.runs.map(function (r) { return r.text; }).join('').replace(/\s+/g, ' ').trim();
    }

    // ── blocks -> chapters ────────────────────────────────────────────────

    /** Merge a paragraph that continues across a page break into the previous one. */
    function joinAcrossPages(blocks) {
        var out = [];
        blocks.forEach(function (b) {
            var prev = out[out.length - 1];
            if (prev && prev.kind === 'p' && b.kind === 'p' && b.page === prev.page + 1 &&
                !/[.!?:;"'”’)\]…]$/.test(blockPlain(prev)) && /^[a-z(“"']/.test(blockPlain(b))) {
                var tail = prev.runs[prev.runs.length - 1];
                if (/[A-Za-z]-$/.test(tail.text) && /^[a-z]/.test(b.runs[0].text)) tail.text = tail.text.slice(0, -1);
                else tail.text += ' ';
                prev.runs = prev.runs.concat(b.runs);
                prev.cont = b.cont;
                return;
            }
            out.push(b);
        });
        return out;
    }

    function blockHtml(b, imageFile) {
        if (b.kind === 'img') {
            return '<figure class="fig"><img src="' + esc(imageFile(b)) + '" alt="Figure from page ' + (b.page + 1) + '"/></figure>';
        }
        var heading = b.kind === 'h2' || b.kind === 'h3';
        var inner = runsToHtml(b.runs, heading); // a heading is bold already
        if (!inner.replace(/<[^>]+>/g, '').trim()) return '';
        if (heading) return '<' + b.kind + '>' + inner + '</' + b.kind + '>';
        if (b.kind === 'li') return '<li>' + inner + '</li>';
        return '<p>' + inner + '</p>';
    }

    /** Render blocks, wrapping consecutive list items in <ul>/<ol>. */
    function blocksHtml(blocks, imageFile) {
        var html = [];
        var openList = null;
        blocks.forEach(function (b) {
            var item = blockHtml(b, imageFile);
            if (!item) return;
            if (b.kind === 'li') {
                var tag = b.ordered ? 'ol' : 'ul';
                if (openList && openList !== tag) { html.push('</' + openList + '>'); openList = null; }
                if (!openList) { html.push('<' + tag + '>'); openList = tag; }
            } else if (openList) {
                html.push('</' + openList + '>');
                openList = null;
            }
            html.push(item);
        });
        if (openList) html.push('</' + openList + '>');
        return html.join('\n');
    }

    /**
     * Split the block stream into chapters. Bookmarks win; else headings; else
     * one chapter. Any chapter over the size cap is cut at a block boundary.
     * @returns [{title, blocks}]
     */
    function makeChapters(blocks, bookmarks, bookTitle) {
        var chapters = [];
        var marks = [];
        var seen = {};
        (bookmarks || []).slice().sort(function (a, b) { return a.page - b.page; }).forEach(function (m) {
            if (!seen[m.page]) { seen[m.page] = 1; marks.push(m); }
        });

        if (marks.length >= 2) {
            var lead = blocks.filter(function (b) { return b.page < marks[0].page; });
            if (lead.length) chapters.push({ title: bookTitle, blocks: lead });
            marks.forEach(function (m, i) {
                var end = i + 1 < marks.length ? marks[i + 1].page : Infinity;
                var part = blocks.filter(function (b) { return b.page >= m.page && b.page < end; });
                if (part.length) chapters.push({ title: m.title, blocks: part });
            });
        } else {
            var heads = blocks.filter(function (b) { return b.kind === 'h2'; });
            if (heads.length >= 2) {
                var cur = null;
                blocks.forEach(function (b) {
                    if (b.kind === 'h2') {
                        cur = { title: blockPlain(b).slice(0, 120) || bookTitle, blocks: [] };
                        chapters.push(cur);
                    } else if (!cur) {
                        cur = { title: bookTitle, blocks: [] };
                        chapters.push(cur);
                    }
                    cur.blocks.push(b);
                });
            } else {
                chapters.push({ title: bookTitle, blocks: blocks.slice() });
            }
        }

        var out = [];
        chapters.forEach(function (ch) {
            var size = 0;
            var part = [];
            var n = 1;
            ch.blocks.forEach(function (b) {
                var w = b.kind === 'img' ? 200 : (b.chars || 0) + 40;
                if (size + w > CHAPTER_SPLIT_BYTES && part.length) {
                    out.push({ title: n === 1 ? ch.title : ch.title + ' (part ' + n + ')', blocks: part });
                    part = [];
                    size = 0;
                    n++;
                }
                part.push(b);
                size += w;
            });
            if (part.length) out.push({ title: n === 1 ? ch.title : ch.title + ' (part ' + n + ')', blocks: part });
        });
        return out;
    }

    // ── package ───────────────────────────────────────────────────────────

    var CSS = [
        'body { font-family: serif; line-height: 1.45; margin: 1em; }',
        'h2, h3 { font-family: sans-serif; line-height: 1.2; margin: 1.4em 0 0.5em; }',
        'p { margin: 0 0 0.8em; text-align: left; }',
        'ul, ol { margin: 0 0 0.8em 1.4em; padding: 0; }',
        'li { margin: 0.2em 0; }',
        'code { font-family: monospace; font-size: 0.92em; }',
        'figure.fig { margin: 1.2em 0; text-align: center; }',
        'figure.fig img { max-width: 100%; height: auto; }',
    ].join('\n');

    function uuid() {
        var c = window.crypto || self.crypto;
        if (c && c.randomUUID) return c.randomUUID();
        var b = new Uint8Array(16);
        if (c && c.getRandomValues) c.getRandomValues(b); else for (var i = 0; i < 16; i++) b[i] = Math.floor(Math.random() * 256);
        b[6] = (b[6] & 0x0f) | 0x40;
        b[8] = (b[8] & 0x3f) | 0x80;
        var h = Array.prototype.map.call(b, function (x) { return (x < 16 ? '0' : '') + x.toString(16); }).join('');
        return h.slice(0, 8) + '-' + h.slice(8, 12) + '-' + h.slice(12, 16) + '-' + h.slice(16, 20) + '-' + h.slice(20);
    }

    function isoNow() {
        return new Date().toISOString().replace(/\.\d+Z$/, 'Z');
    }

    function validLang(lang) {
        var l = String(lang || '').trim();
        return /^[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8})*$/.test(l) ? l : 'en';
    }

    /**
     * Build the EPUB zip. `book`: {title, author, language, chapters:[{title, blocks}],
     * images:[{name, mime, bytes}], cover:{bytes}|null, id}
     */
    function buildPackage(JSZip, book) {
        var zip = new JSZip();
        zip.file('mimetype', 'application/epub+zip', { compression: 'STORE' });
        zip.file('META-INF/container.xml',
            '<?xml version="1.0" encoding="UTF-8"?>\n' +
            '<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">\n' +
            '  <rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles>\n' +
            '</container>');

        var lang = validLang(book.language);
        var imageFile = function (b) { return 'images/' + b.file; };
        var files = [];
        book.chapters.forEach(function (ch, i) {
            var name = 'chapter_' + (i + 1) + '.xhtml';
            files.push({ name: name, id: 'chapter_' + (i + 1), title: ch.title });
            zip.file('OEBPS/' + name,
                '<?xml version="1.0" encoding="UTF-8"?>\n' +
                '<!DOCTYPE html>\n' +
                '<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" lang="' + esc(lang) + '" xml:lang="' + esc(lang) + '">\n' +
                '<head><meta charset="utf-8"/><title>' + esc(ch.title) + '</title><link rel="stylesheet" type="text/css" href="style.css"/></head>\n' +
                '<body>\n' + blocksHtml(ch.blocks, imageFile) + '\n</body>\n</html>');
        });
        zip.file('OEBPS/style.css', CSS);

        var navItems = files.map(function (f) { return '<li><a href="' + f.name + '">' + esc(f.title) + '</a></li>'; }).join('\n');
        zip.file('OEBPS/nav.xhtml',
            '<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE html>\n' +
            '<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" lang="' + esc(lang) + '" xml:lang="' + esc(lang) + '">\n' +
            '<head><meta charset="utf-8"/><title>' + esc(book.title) + '</title></head>\n' +
            '<body><nav epub:type="toc" id="toc"><h1>Contents</h1>\n<ol>\n' + navItems + '\n</ol></nav></body>\n</html>');

        var points = files.map(function (f, i) {
            return '<navPoint id="np' + (i + 1) + '" playOrder="' + (i + 1) + '"><navLabel><text>' + esc(f.title) +
                '</text></navLabel><content src="' + f.name + '"/></navPoint>';
        }).join('\n');
        zip.file('OEBPS/toc.ncx',
            '<?xml version="1.0" encoding="UTF-8"?>\n' +
            '<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">\n' +
            '<head><meta name="dtb:uid" content="urn:uuid:' + esc(book.id) + '"/><meta name="dtb:depth" content="1"/>' +
            '<meta name="dtb:totalPageCount" content="0"/><meta name="dtb:maxPageNumber" content="0"/></head>\n' +
            '<docTitle><text>' + esc(book.title) + '</text></docTitle>\n<navMap>\n' + points + '\n</navMap>\n</ncx>');

        var manifest = [
            '<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>',
            '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>',
            '<item id="css" href="style.css" media-type="text/css"/>',
        ];
        files.forEach(function (f) { manifest.push('<item id="' + f.id + '" href="' + f.name + '" media-type="application/xhtml+xml"/>'); });
        book.images.forEach(function (im, i) {
            zip.file('OEBPS/images/' + im.file, im.bytes);
            manifest.push('<item id="img' + (i + 1) + '" href="images/' + im.file + '" media-type="' + im.mime + '"/>');
        });
        var coverMeta = '';
        if (book.cover) {
            zip.file('OEBPS/images/cover.jpg', book.cover.bytes);
            manifest.push('<item id="cover-image" href="images/cover.jpg" media-type="image/jpeg" properties="cover-image"/>');
            coverMeta = '<meta name="cover" content="cover-image"/>';
        }
        var spine = files.map(function (f) { return '<itemref idref="' + f.id + '"/>'; }).join('\n');
        zip.file('OEBPS/content.opf',
            '<?xml version="1.0" encoding="UTF-8"?>\n' +
            '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="bookid" xml:lang="' + esc(lang) + '">\n' +
            '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">\n' +
            '<dc:identifier id="bookid">urn:uuid:' + esc(book.id) + '</dc:identifier>\n' +
            '<dc:title>' + esc(book.title) + '</dc:title>\n' +
            '<dc:language>' + esc(lang) + '</dc:language>\n' +
            (book.author ? '<dc:creator>' + esc(book.author) + '</dc:creator>\n' : '') +
            '<meta property="dcterms:modified">' + isoNow() + '</meta>\n' + coverMeta + '\n</metadata>\n' +
            '<manifest>\n' + manifest.join('\n') + '\n</manifest>\n' +
            '<spine toc="ncx">\n' + spine + '\n</spine>\n</package>');
        return zip;
    }

    // ── cover ─────────────────────────────────────────────────────────────

    async function renderCover(page) {
        var vp1 = page.getViewport({ scale: 1 });
        var scale = COVER_WIDTH / vp1.width;
        var vp = page.getViewport({ scale: scale });
        var canvas = L.layout.makeCanvas(Math.ceil(vp.width), Math.ceil(vp.height));
        try {
            var g = canvas.getContext('2d');
            g.fillStyle = '#ffffff';
            g.fillRect(0, 0, canvas.width, canvas.height);
            await page.render({ canvasContext: g, viewport: vp, intent: 'print', background: 'rgb(255,255,255)' }).promise;
            var blob = await L.layout.canvasBlob(canvas, 'image/jpeg', 0.8);
            return new Uint8Array(await blob.arrayBuffer());
        } finally {
            canvas.width = 0;
            canvas.height = 0;
        }
    }

    // ── handler ───────────────────────────────────────────────────────────

    function truthy(v, dflt) {
        var s = String(v === undefined || v === null || v === '' ? dflt : v).toLowerCase();
        return !(s === 'false' || s === '0' || s === 'no' || s === 'off');
    }

    /** Insert placed images into a page's block stream at the right reading position. */
    function placeImages(pageBlocks, images, columns) {
        var out = pageBlocks.slice();
        images.sort(function (a, b) { return a.y - b.y; }).forEach(function (img) {
            var col = columns.count === 2 ? (img.x + img.w / 2 < columns.gutter ? 0 : 1) : 0;
            var at = -1;
            for (var i = 0; i < out.length; i++) {
                var b = out[i];
                if (b.kind === 'img') continue;
                var bcol = columns.count === 2 ? (b.x0 < columns.gutter ? 0 : 1) : 0;
                if (bcol === col && b.y > img.y) { at = i; break; }
            }
            if (at < 0) {
                // After the last block of this column.
                at = out.length;
                if (columns.count === 2) {
                    for (var j = out.length - 1; j >= 0; j--) {
                        var bj = out[j];
                        if (bj.kind !== 'img' && (bj.x0 < columns.gutter ? 0 : 1) === col) { at = j + 1; break; }
                    }
                }
            }
            out.splice(at, 0, img);
        });
        return out;
    }

    L.register('/api/pdf/to-epub', async function (fd, ctx) {
        ctx = ctx || {};
        var layout = L.layout;
        if (!layout) throw new L.Unsupported('layout engine missing', 'engine_unavailable');
        var files = L.files(fd, 'file');
        if (!files.length) throw new L.Error('No file provided.');
        var file = files[0];

        if (L.str(fd, 'password', null)) {
            throw new L.Unsupported('password-protected PDFs need the server', 'encrypted');
        }
        var wantCover = truthy(L.str(fd, 'cover', 'true'), 'true');

        var budget = L.constrained() ? BUDGET.mobile : BUDGET.desktop;
        if (file.size > budget.bytes) {
            throw new L.Unsupported('input exceeds the on-device EPUB budget', 'resource_budget_exceeded');
        }

        var pdfjs = await L.loadPdfJs();
        var JSZip = await L.loadJsZip();
        var doc = await layout.openDocument(file);

        try {
            var total = doc.numPages;
            if (total > budget.pages) {
                throw new L.Unsupported('page count exceeds the on-device EPUB budget', 'resource_budget_exceeded');
            }

            // Pass 1: read every page. Images are fetched now, while the page is open.
            var pages = [];
            var scanned = false;
            var figs = { entries: [], refs: {}, bytes: 0, budget: { images: budget.images, imageBytes: budget.imageBytes } };
            var coverBytes = null;
            for (var n = 1; n <= total; n++) {
                L.checkAbort(ctx.signal);
                var model;
                try {
                    model = await layout.loadPage(pdfjs, doc, n);
                } catch (err) {
                    if (err instanceof layout.Decline) throw new L.Unsupported(err.message, err.code);
                    throw err;
                }
                var page = model.pageObjs;
                model.pageObjs = null;
                try {
                    if (model.columns.count > 2) throw new L.Unsupported('three or more text columns', 'unsupported_structure');
                    if (!model.chars && model.anyRaster) scanned = true;
                    if (scanned) break;

                    await layout.collectFigures(page, model, figs);
                    // Last: rendering a page releases its decoded image objects.
                    if (n === 1 && wantCover) {
                        try { coverBytes = await renderCover(page); } catch (e) { coverBytes = null; }
                    }
                } catch (err2) {
                    if (err2 instanceof layout.Decline) throw new L.Unsupported(err2.message, err2.code);
                    throw err2;
                } finally {
                    page.cleanup();
                }
                pages.push(model);
                if (ctx.onProgress) ctx.onProgress(n, total);
                await L.tick();
            }
            // The server would OCR these pages; skipping them would silently drop their text.
            if (scanned) throw new L.Unsupported('scanned pages need OCR', 'ocr_required');

            L.checkAbort(ctx.signal);
            layout.removeRunningLines(pages);
            var base = layout.bodySize(pages);

            var blocks = [];
            pages.forEach(function (p) {
                var right = layout.rightMargins(p.ordered, p.columns);
                var pageBlocks = layout.paragraphs(p.ordered, { base: base, right: right, page: p.n - 1 });
                blocks = blocks.concat(placeImages(pageBlocks, p.figures || [], p.columns));
            });
            blocks = joinAcrossPages(blocks);

            var info = {};
            try { info = (await doc.getMetadata()).info || {}; } catch (e) { info = {}; }
            var title = String(info.Title || '').trim() || L.stem(file.name);
            var author = String(info.Author || '').trim();

            if (!blocks.length) {
                blocks = [{ kind: 'p', runs: [{ text: '(No text found in document)' }], page: 0, chars: 28 }];
            }
            var bookmarks = await layout.outline(doc);
            var chapters = makeChapters(blocks, bookmarks, title);

            var zip = buildPackage(JSZip, {
                id: uuid(), title: title, author: author, language: info.Language,
                chapters: chapters, images: figs.entries, cover: coverBytes ? { bytes: coverBytes } : null,
            });
            L.checkAbort(ctx.signal);
            var out = await zip.generateAsync({ type: 'blob', mimeType: 'application/epub+zip', compression: 'DEFLATE' });
            return {
                blob: out,
                filename: L.brandedName(file.name, 'epub'),
                message: 'PDF converted to EPUB',
            };
        } catch (err) {
            if (err instanceof L.Error || err instanceof L.Unsupported || (err && err.name === 'AbortError')) throw err;
            throw new L.Unsupported('PDF could not be converted to EPUB on-device', 'unsupported_structure');
        } finally {
            try { await doc.destroy(); } catch (e) { /* already gone */ }
        }
    });

    L.epub = {
        xmlSafe: xmlSafe,
        safeHref: safeHref,
        runsToHtml: runsToHtml,
        blocksHtml: blocksHtml,
        makeChapters: makeChapters,
        joinAcrossPages: joinAcrossPages,
        buildPackage: buildPackage,
        placeImages: placeImages,
    };
})();
