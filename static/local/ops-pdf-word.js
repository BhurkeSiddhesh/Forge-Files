// On-device PDF to Word (migration work package 42).
//
// Routes: /api/pdf/convert-to-word (JSON) and /api/pdf/convert-to-word-stream (the SSE route the
// web UI uses). Both mirror the Python endpoints for standard (non-AI) conversion:
// `<stem>_forgefiles.org.docx` and the message "Converted to Word (Standard)".
//
// Editable output, not a page picture: real paragraphs, headings, bullet and numbered lists,
// bold/italic/size/font family, hyperlinks, right and left tab stops (a right-aligned date stays
// right-aligned), centred/right/justified alignment, indents, inline images and real Word tables.
// Each PDF page becomes one Word section sized like the page, so a page never merges into the
// next, and line spacing is "exact" to the PDF's own baseline pitch so vertical layout does not
// drift when Word substitutes a font.
//
// Asks the user before using the server (never uploads on its own) for: the AI mode, password-
// protected PDFs, scanned pages (OCR), multi-column pages, tables with merged cells, right-to-left
// or mostly rotated text, and input past the budgets.
//
// Not preserved, and not claimed: text colour, shading, vector drawings, floating text boxes and
// exact glyph positions. Pagination was not compared against a rendered copy in Word.
(function () {
    'use strict';

    var L = window.ffLocal;
    if (!L) return;

    var MIB = 1024 * 1024;
    var BUDGET = {
        desktop: { bytes: 50 * MIB, pages: 300, images: 300, imageBytes: 100 * MIB, cells: 20000 },
        mobile: { bytes: 25 * MIB, pages: 100, images: 100, imageBytes: 30 * MIB, cells: 6000 },
    };
    var DOCX_VERSION = '9.8.1';
    var MESSAGE = 'Converted to Word (Standard)';

    // ── engine ────────────────────────────────────────────────────────────

    var docxPromise = null;

    /** The docx writer is a same-origin, pinned UMD file loaded the first time it is needed. */
    function loadDocx() {
        if (window.docx && window.docx.Packer) return Promise.resolve(window.docx);
        if (docxPromise) return docxPromise;
        docxPromise = new Promise(function (ok, fail) {
            var el = document.createElement('script');
            el.src = L.vendorUrl('docx.iife.js') + '?v=' + DOCX_VERSION;
            el.async = true;
            el.onload = function () {
                if (window.docx && window.docx.Packer) ok(window.docx);
                else fail(new L.Unsupported('docx loaded but did not register', 'engine_unavailable'));
            };
            el.onerror = function () {
                docxPromise = null;
                fail(new L.Unsupported('docx could not be loaded', 'engine_unavailable'));
            };
            document.head.appendChild(el);
        });
        return docxPromise;
    }

    // ── mapping helpers ───────────────────────────────────────────────────

    function twips(pt) { return Math.round(pt * 20); }

    var STYLE_WORDS = /\s+(bold|italic|oblique|regular|roman|book|medium|semibold|demibold|light|black|heavy|narrow|condensed)\b/gi;

    /** Word font family for a PDF run. Metric-compatible substitutes map to the family Word has. */
    function wordFont(run) {
        var raw = String(run.font || '');
        var compact = raw.toLowerCase().replace(/[\s_-]/g, '');
        if (/arial|helvetica|liberationsans|nimbussans|arimo/.test(compact)) return 'Arial';
        if (/times|liberationserif|nimbusroman|tinos/.test(compact)) return 'Times New Roman';
        if (run.mono || /courier|liberationmono|nimbusmono|cousine|consolas|mono/.test(compact)) return 'Courier New';
        if (/calibri|carlito/.test(compact)) return 'Calibri';
        if (/cambria|caladea/.test(compact)) return 'Cambria';
        if (raw && !/^(g_d|f\d|cidfont|type3|unnamed)/i.test(raw)) {
            var family = raw.split(/[-,]/)[0].replace(/(PSMT|MT|PS)$/, '').replace(STYLE_WORDS, '').trim();
            if (family) return family;
        }
        return run.serif ? 'Times New Roman' : 'Arial';
    }

    function safeHref(url) {
        var u = String(url || '').trim();
        return /^(https?:|mailto:)/i.test(u) ? u : null;
    }

    /** XML 1.0 cannot carry control characters; docx does not strip them. */
    function clean(s) {
        return String(s).replace(/[\u0000-\u0008\u000B\u000C\u000E-\u001F￾￿]/g, '');
    }

    function median(values) { return L.layout.median(values); }

    function pitchOf(block) {
        var ys = block.ys || [];
        if (ys.length > 1) {
            var d = [];
            for (var i = 1; i < ys.length; i++) d.push(ys[i] - ys[i - 1]);
            var m = median(d);
            if (m > 0) return m;
        }
        return block.size * 1.2;
    }

    // ── page geometry ─────────────────────────────────────────────────────

    function pageMargins(model) {
        var x0 = Infinity;
        var x1 = -Infinity;
        var top = Infinity;
        (model.flow || []).forEach(function (l) { x0 = Math.min(x0, l.x0); x1 = Math.max(x1, l.x1); top = Math.min(top, l.y - l.size); });
        (model.tables || []).forEach(function (t) { x0 = Math.min(x0, t.bbox.x0); x1 = Math.max(x1, t.bbox.x1); top = Math.min(top, t.bbox.y0); });
        (model.figures || []).forEach(function (f) { x0 = Math.min(x0, f.x); x1 = Math.max(x1, f.x + f.w); top = Math.min(top, f.y); });
        if (!isFinite(x0)) { x0 = 72; x1 = model.width - 72; top = 72; }
        var left = Math.max(18, Math.min(x0, model.width * 0.4));
        var right = Math.max(18, Math.min(model.width - x1, model.width * 0.4));
        return { left: left, right: right, top: Math.max(18, Math.min(top - 2, model.height * 0.4)), bottom: 18 };
    }

    // ── runs and paragraphs ───────────────────────────────────────────────

    function buildRuns(d, runs, o) {
        var out = [];
        runs.forEach(function (r) {
            var text = clean(r.text);
            if (!text && !r.tab) return;
            var href = safeHref(r.href);
            var opts = {
                bold: !!r.bold && !o.noBold,
                italics: !!r.italic,
                size: Math.max(2, Math.round((r.size || o.size || 11) * 2)),
                font: wordFont(r),
            };
            opts.color = '000000'; // text colour is not tracked; Word's heading styles would otherwise add blue
            if (href) { opts.color = '0563C1'; opts.underline = { type: d.UnderlineType.SINGLE }; }
            if (r.tab) opts.children = text ? [new d.Tab(), text] : [new d.Tab()];
            else opts.text = text;
            var run = new d.TextRun(opts);
            out.push(href ? new d.ExternalHyperlink({ children: [run], link: href }) : run);
        });
        return out;
    }

    /** Tab stops for the runs of one block: right tab for a date at the margin, else left at the run. */
    function tabStopsFor(d, block, g) {
        var stops = [];
        var seen = {};
        block.runs.forEach(function (r) {
            if (!r.tab) return;
            var right = r.x1 >= g.textRight - 6;
            var pos = right ? twips(g.textRight - g.left) : twips(r.x0 - g.left);
            var key = (right ? 'r' : 'l') + pos;
            if (seen[key] || pos <= 0) return;
            seen[key] = 1;
            stops.push({ type: right ? d.TabStopType.RIGHT : d.TabStopType.LEFT, position: pos });
        });
        return stops;
    }

    function alignmentFor(d, block, g) {
        var A = d.AlignmentType;
        var hasTab = block.runs.some(function (r) { return r.tab; });
        if (block.kind === 'li' || hasTab) return undefined;
        var right = Math.max.apply(null, block.ends);
        if (block.lineCount === 1) {
            var mid = (block.x0 + right) / 2;
            var gapL = block.x0 - g.left;
            var gapR = g.textRight - right;
            if (Math.abs(mid - g.centre) <= 6 && gapL > 20 && gapR > 20) return A.CENTER;
            if (gapR <= 4 && gapL > 0.3 * (g.textRight - g.left)) return A.RIGHT;
            return undefined;
        }
        // Justified text: nearly every line but the last ends at the same right edge.
        if (block.lineCount >= 3) {
            var flush = block.ends.slice(0, -1).filter(function (x) { return Math.abs(x - right) <= 2.5; }).length;
            if (flush >= (block.lineCount - 1) * 0.8) return A.BOTH;
        }
        return undefined;
    }

    /** One text block as a Word paragraph. `g` is the page geometry plus running state. */
    function buildParagraph(d, block, g, o) {
        o = o || {};
        var pitch = pitchOf(block);
        var opts = { children: buildRuns(d, block.runs, { size: block.size }), keepNext: false, keepLines: false };
        if (!opts.children.length) return null;

        var spacing = {};
        var before = Math.max(0, Math.min(72, block.y - g.prevBottom - pitch));
        spacing.before = twips(before);
        spacing.after = 0;
        if (pitch >= block.size) { spacing.line = twips(pitch); spacing.lineRule = d.LineRuleType.EXACT; }
        opts.spacing = spacing;

        var left = Math.max(0, block.x0 - g.left);
        var right = Math.max(0, g.textRight - Math.max.apply(null, block.ends));
        var indent = {};
        if (block.kind === 'li') {
            var hang = Math.min(18, Math.max(10, block.size * 1.4));
            indent.left = twips(left + hang);
            indent.hanging = twips(hang);
            opts.numbering = { reference: block.ordered ? 'numbers' : 'bullets', level: 0, instance: block.ordered ? g.listInstance : 0 };
        } else if (left > 3) {
            indent.left = twips(left);
        }
        if (block.lineCount > 1 && right > 3) indent.right = twips(right);
        if (indent.left || indent.right || indent.hanging) opts.indent = indent;

        var align = alignmentFor(d, block, g);
        if (align) opts.alignment = align;
        var tabs = tabStopsFor(d, block, g);
        if (tabs.length) opts.tabStops = tabs;
        if (block.kind === 'h2') opts.heading = d.HeadingLevel.HEADING_1;
        else if (block.kind === 'h3') opts.heading = d.HeadingLevel.HEADING_2;
        return new d.Paragraph(opts);
    }

    // ── tables ────────────────────────────────────────────────────────────

    function cellAlignment(d, cell, bounds, table, col) {
        var A = d.AlignmentType;
        if (table.kind === 'borderless') return table.aligns && table.aligns[col] === 'right' ? A.RIGHT : undefined;
        var lines = cell.lines || [];
        if (lines.length !== 1) return undefined;
        var l = lines[0];
        var padL = l.x0 - bounds.x0;
        var padR = bounds.x1 - l.x1;
        if (padL > 6 && padR > 6 && Math.abs(padL - padR) <= 4) return A.CENTER;
        if (padR < padL - 4 && padR <= 8) return A.RIGHT;
        return undefined;
    }

    function cellParagraphs(d, cell, bounds, table, col, base) {
        var paras = [];
        var align = cellAlignment(d, cell, bounds, table, col);
        var blocks = [];
        if (cell.lines && cell.lines.length) {
            var layout = L.layout;
            blocks = layout.paragraphs(cell.lines, { base: base, right: function () { return bounds.x1; }, page: 0 });
        } else if (cell.runs && cell.runs.length) {
            blocks = [{ kind: 'p', runs: cell.runs, size: cell.runs[0].size || base, ys: [0], ends: [0], x0: 0, lineCount: 1, heading: false }];
        }
        blocks.forEach(function (b) {
            // A gap inside a cell is a space, not a tab stop.
            b.runs = b.runs.map(function (r) {
                var c = {};
                for (var k in r) c[k] = r[k];
                if (c.tab) { c.text = ' ' + c.text; c.tab = false; }
                return c;
            });
            if (b.runs.length) {
                b.runs[0].text = b.runs[0].text.replace(/^\s+/, '');
                b.runs[b.runs.length - 1].text = b.runs[b.runs.length - 1].text.replace(/\s+$/, '');
            }
            var opts = { children: buildRuns(d, b.runs, { size: b.size }), spacing: { before: 0, after: 0 } };
            if (!opts.children.length) return;
            if (align) opts.alignment = align;
            paras.push(new d.Paragraph(opts));
        });
        if (!paras.length) paras.push(new d.Paragraph({ children: [], spacing: { before: 0, after: 0 } }));
        return paras;
    }

    function buildTable(d, table, g, base) {
        var xs = table.xs;
        if (!xs || xs.length !== table.cols + 1) return null;
        var widths = [];
        for (var c = 0; c < table.cols; c++) widths.push(twips(Math.max(8, xs[c + 1] - xs[c])));
        var total = widths.reduce(function (a, b) { return a + b; }, 0);
        var ruled = table.kind === 'ruled';
        var none = { style: d.BorderStyle.NONE, size: 0, color: 'FFFFFF' };
        var line = { style: d.BorderStyle.SINGLE, size: 4, color: '000000' };
        var borders = ruled ? { top: line, bottom: line, left: line, right: line } : { top: none, bottom: none, left: none, right: none };
        var rows = [];
        for (var r = 0; r < table.rows; r++) {
            var cells = [];
            for (var cc = 0; cc < table.cols; cc++) {
                var bounds = { x0: xs[cc], x1: xs[cc + 1] };
                cells.push(new d.TableCell({
                    width: { size: widths[cc], type: d.WidthType.DXA },
                    borders: borders,
                    margins: { top: 20, bottom: 20, left: 60, right: 60 },
                    children: cellParagraphs(d, table.cells[r][cc], bounds, table, cc, base),
                }));
            }
            var rowOpts = { children: cells };
            if (ruled && table.ys) rowOpts.height = { value: twips(Math.max(8, table.ys[r + 1] - table.ys[r])), rule: d.HeightRule.AT_LEAST };
            rows.push(new d.TableRow(rowOpts));
        }
        return new d.Table({
            rows: rows,
            width: { size: total, type: d.WidthType.DXA },
            columnWidths: widths,
            layout: d.TableLayoutType.FIXED,
            indent: { size: twips(Math.max(0, xs[0] - g.left)), type: d.WidthType.DXA },
        });
    }

    // ── document ──────────────────────────────────────────────────────────

    function numberingConfig(d) {
        var A = d.AlignmentType;
        return {
            config: [
                { reference: 'bullets', levels: [{ level: 0, format: d.LevelFormat.BULLET, text: '•', alignment: A.LEFT }] },
                { reference: 'numbers', levels: [{ level: 0, format: d.LevelFormat.DECIMAL, text: '%1.', alignment: A.LEFT }] },
            ],
        };
    }

    function imageParagraph(d, fig, g, prevBottom) {
        var wPx = Math.max(1, Math.round(fig.w * 96 / 72));
        var hPx = Math.max(1, Math.round(fig.h * 96 / 72));
        var opts = {
            children: [new d.ImageRun({
                type: fig.ext === 'jpg' ? 'jpg' : 'png', data: fig.bytes, transformation: { width: wPx, height: hPx },
                altText: { title: 'Figure', description: 'Figure from page ' + (fig.page + 1), name: 'Figure' },
            })],
            spacing: { before: twips(Math.max(0, Math.min(72, fig.y - prevBottom))), after: 0 },
        };
        var left = fig.x - g.left;
        if (left > 3) opts.indent = { left: twips(left) };
        return new d.Paragraph(opts);
    }

    /**
     * Build the docx Document from analysed pages. One section per source page.
     * @param pages page models from layout.loadPage (with `.figures` already collected)
     * @param info `{title, author}`
     */
    function buildDocument(d, pages, info) {
        var layout = L.layout;
        var base = layout.bodySize(pages);
        var sections = [];
        var listInstance = 1;
        var lastKind = null;

        pages.forEach(function (p) {
            var m = pageMargins(p);
            var g = {
                left: m.left, textRight: p.width - m.right, centre: (m.left + p.width - m.right) / 2,
                prevBottom: m.top, listInstance: listInstance,
            };
            var items = [];
            var right = layout.rightMargins(p.flow, p.flowColumns);
            layout.paragraphs(p.flow, { base: base, right: right, page: p.n - 1 }).forEach(function (b) {
                items.push({ y: b.y - 0.8 * b.size, type: 'p', block: b });
            });
            (p.tables || []).forEach(function (t) { items.push({ y: t.bbox.y0, type: 't', table: t }); });
            (p.figures || []).forEach(function (f) { items.push({ y: f.y, type: 'i', fig: f }); });
            items.sort(function (a, b) { return a.y - b.y; });

            var children = [];
            items.forEach(function (it) {
                if (it.type === 'p') {
                    var b = it.block;
                    if (b.kind === 'li' && b.ordered && lastKind !== 'olist') listInstance++;
                    g.listInstance = listInstance;
                    var para = buildParagraph(d, b, g);
                    if (para) children.push(para);
                    g.prevBottom = b.ys[b.ys.length - 1];
                    lastKind = b.kind === 'li' ? (b.ordered ? 'olist' : 'ulist') : 'p';
                } else if (it.type === 't') {
                    var tbl = buildTable(d, it.table, g, base);
                    if (tbl) {
                        // A table is followed by a paragraph so Word can place the next block.
                        children.push(tbl);
                        children.push(new d.Paragraph({ children: [], spacing: { before: 0, after: 0, line: 20, lineRule: d.LineRuleType.EXACT } }));
                    }
                    g.prevBottom = it.table.bbox.y1;
                    lastKind = 't';
                } else {
                    children.push(imageParagraph(d, it.fig, g, g.prevBottom));
                    g.prevBottom = it.fig.y + it.fig.h;
                    lastKind = 'i';
                }
            });
            if (!children.length) children.push(new d.Paragraph({ children: [] }));
            sections.push({
                properties: {
                    type: d.SectionType.NEXT_PAGE,
                    page: {
                        size: { width: twips(p.width), height: twips(p.height) },
                        margin: { top: twips(m.top), bottom: twips(m.bottom), left: twips(m.left), right: twips(m.right), header: 0, footer: 0, gutter: 0 },
                    },
                },
                children: children,
            });
        });

        return new d.Document({
            creator: info.author || 'Forge Files',
            title: info.title || '',
            styles: { default: { document: { run: { font: 'Arial', size: Math.round(base * 2) } } } },
            numbering: numberingConfig(d),
            sections: sections,
        });
    }

    // ── conversion ────────────────────────────────────────────────────────

    function truthy(v) {
        var s = String(v === undefined || v === null ? '' : v).toLowerCase();
        return s === 'true' || s === '1' || s === 'on' || s === 'yes';
    }

    async function convertToWord(fd, ctx) {
        ctx = ctx || {};
        var layout = L.layout;
        if (!layout) throw new L.Unsupported('layout engine missing', 'engine_unavailable');
        var files = L.files(fd, 'file');
        if (!files.length) throw new L.Error('No file provided.');
        var file = files[0];

        if (truthy(L.str(fd, 'use_ai', 'false'))) throw new L.Unsupported('AI mode runs on the server', 'ai_layout');
        if (L.str(fd, 'password', null)) throw new L.Unsupported('password-protected PDFs need the server', 'encrypted');

        var budget = L.constrained() ? BUDGET.mobile : BUDGET.desktop;
        if (file.size > budget.bytes) throw new L.Unsupported('input exceeds the on-device Word budget', 'resource_budget_exceeded');

        var pdfjs = await L.loadPdfJs();
        var d = await loadDocx();
        var doc = await layout.openDocument(file);

        try {
            var total = doc.numPages;
            if (total > budget.pages) throw new L.Unsupported('page count exceeds the on-device Word budget', 'resource_budget_exceeded');

            var pages = [];
            var figs = { entries: [], refs: {}, bytes: 0, budget: { images: budget.images, imageBytes: budget.imageBytes } };
            var cellCount = 0;
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
                    // The server OCRs scans; an empty page here would silently drop their text.
                    if (!model.chars && model.anyRaster) throw new L.Unsupported('scanned pages need OCR', 'ocr_required');
                    if (model.flowColumns.count > 1) throw new L.Unsupported('multi-column pages need the server', 'unsupported_structure');
                    if (model.rotation) throw new L.Unsupported('rotated pages need the server', 'unsupported_structure');
                    model.tables.forEach(function (t) {
                        cellCount += t.rows * t.cols;
                        if (t.merged) throw new L.Unsupported('tables with merged cells need the server', 'unsupported_structure');
                    });
                    if (cellCount > budget.cells) throw new L.Unsupported('too many table cells', 'resource_budget_exceeded');
                    await layout.collectFigures(page, model, figs);
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

            var info = {};
            try { info = (await doc.getMetadata()).info || {}; } catch (e) { info = {}; }
            var document_ = buildDocument(d, pages, { title: String(info.Title || '').trim() || L.stem(file.name), author: String(info.Author || '').trim() });
            L.checkAbort(ctx.signal);
            var blob = await d.Packer.toBlob(document_);
            return {
                blob: new Blob([blob], { type: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document' }),
                filename: L.brandedName(file.name, 'docx'),
                message: MESSAGE,
            };
        } catch (err3) {
            if (err3 instanceof L.Error || err3 instanceof L.Unsupported || (err3 && err3.name === 'AbortError')) throw err3;
            throw new L.Unsupported('PDF could not be converted to Word on-device', 'unsupported_structure');
        } finally {
            try { await doc.destroy(); } catch (e) { /* already gone */ }
        }
    }

    L.register('/api/pdf/convert-to-word', convertToWord);

    /**
     * The UI reads this route as server-sent events. Conversion finishes first, so a refusal
     * becomes the consent question; only a finished result is streamed back.
     */
    L.register('/api/pdf/convert-to-word-stream', async function (fd, ctx) {
        var out = await convertToWord(fd, ctx);
        var fields = L.publish(out.blob, out.filename);
        var events = [
            { event: 'start' },
            { event: 'complete', message: out.message, method: 'standard', filename: fields.filename, download_token: fields.download_token },
        ];
        var body = events.map(function (e) { return 'data: ' + JSON.stringify(e) + '\n\n'; }).join('');
        return new Response(body, { status: 200, headers: { 'Content-Type': 'text/event-stream' } });
    });

    L.word = {
        loadDocx: loadDocx,
        wordFont: wordFont,
        buildDocument: buildDocument,
        buildParagraph: buildParagraph,
        buildTable: buildTable,
        pageMargins: pageMargins,
        convert: convertToWord,
    };
})();
