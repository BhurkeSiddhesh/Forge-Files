// On-device PDF to Excel (migration work package 21).
//
// Route: /api/pdf/to-excel. Same response contract as the Python endpoint
// (pdf_utils.py::pdf_to_excel): `<stem>_forgefiles.org.xlsx`, the message
// "Extracted N table(s) to Excel" and a `tables_found` field. One sheet per table, named
// `P{page}_T{n}` (n counts the tables on that page, top to bottom). A PDF with no table at all gets
// the server's fallback: one "Text Content" sheet with a Page / Text row per page.
//
// Tables come from the shared layout module: ruled tables (a grid of drawn lines) and borderless
// ones (columns that only line up by alignment, including right-aligned numbers). pdf.js text and
// vector paths in, ExcelJS (already vendored) out.
//
// Differences from the server, deliberate and recorded in the CHANGELOG: cells are typed, so
// 1,250.00 is the number 1250, (500.00) is -500 and 12% is 0.12 (the server stores everything as
// text); text that must stay text does: leading-zero identifiers (007), long digit strings, and
// anything that starts with = + - @ is never a formula. A header row that is entirely bold is
// bold, and column widths follow the page.
//
// Asks the user first (never uploads on its own) for: password-protected PDFs, scanned pages
// (the server runs OCR), right-to-left or mostly rotated text, and input past the budgets.
// Merged cells are not reproduced: their text lands in the cell it sits in.
(function () {
    'use strict';

    var L = window.ffLocal;
    if (!L) return;

    var MIB = 1024 * 1024;
    var BUDGET = {
        desktop: { bytes: 50 * MIB, pages: 300, cells: 200000 },
        mobile: { bytes: 25 * MIB, pages: 100, cells: 50000 },
    };
    var XLSX_MIME = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet';
    var CELL_LIMIT = 32000; // Excel's hard limit is 32,767 characters per cell

    // ── cell typing ───────────────────────────────────────────────────────

    var CURRENCY = { '$': '"$"#,##0.00', '₹': '"₹"#,##0.00', '€': '"€"#,##0.00', '£': '"£"#,##0.00' };

    /**
     * Decide what a cell's text is. Returns `{value, numFmt?}`: a number when the text is clearly
     * one, otherwise the text itself (or null for an empty cell).
     */
    function typeCell(raw) {
        var text = String(raw === undefined || raw === null ? '' : raw).replace(/ /g, ' ');
        var t = text.trim();
        if (!t) return { value: null };

        // Identifiers that look numeric but are not quantities: 007, 0012, long digit runs, +91 numbers.
        if (/^0\d+$/.test(t) || /^\+?\d{12,}$/.test(t) || /^\+\d/.test(t)) return { value: text };

        // Percent: 12%, 12.5 %, (3%)
        var pct = /^(\()?(-)?(\d+(?:\.\d+)?)\s?%(\))?$/.exec(t);
        if (pct && (!!pct[1] === !!pct[4])) {
            var p = parseFloat(pct[3]) / 100;
            var places = (pct[3].split('.')[1] || '').length;
            return { value: (pct[1] || pct[2]) ? -p : p, numFmt: places ? '0.' + new Array(places + 1).join('0') + '%' : '0%' };
        }

        // Money and plain numbers with thousands separators, optional sign or accounting parentheses.
        var m = /^(\()?(-|−)?([$₹€£])?\s?(\d{1,3}(?:,\d{2,3})+|\d+)(\.\d+)?(\))?$/.exec(t);
        if (m && (!!m[1] === !!m[6])) {
            var grouped = m[4];
            // "1,25" or "12,34,567" style groupings are only numbers in the Indian system.
            if (grouped.indexOf(',') >= 0 && !/^\d{1,3}(,\d{3})*$|^\d{1,2}(,\d{2})*,\d{3}$/.test(grouped)) return { value: text };
            var n = parseFloat(grouped.replace(/,/g, '') + (m[5] || ''));
            if (!isFinite(n)) return { value: text };
            var neg = !!m[1] || !!m[2];
            var out = { value: neg ? -n : n };
            // Accounting negatives keep their parentheses on screen.
            if (m[1]) out.numFmt = m[5] ? '#,##0.' + new Array(m[5].length).join('0') + ';(#,##0.' + new Array(m[5].length).join('0') + ')' : '#,##0;(#,##0)';
            else if (m[3]) out.numFmt = CURRENCY[m[3]];
            else if (m[5]) out.numFmt = grouped.indexOf(',') >= 0 ? '#,##0.' + new Array(m[5].length).join('0') : undefined;
            else if (grouped.indexOf(',') >= 0) out.numFmt = '#,##0';
            if (out.numFmt === undefined) delete out.numFmt;
            return out;
        }
        return { value: text };
    }

    // ── table to sheet ────────────────────────────────────────────────────

    function cellRuns(cell) {
        var runs = [];
        if (cell.lines) cell.lines.forEach(function (l) { runs = runs.concat(l.runs); });
        else if (cell.runs) runs = cell.runs;
        return runs;
    }

    function addTableSheet(wb, name, table) {
        var ws = wb.addWorksheet(name);
        for (var r = 0; r < table.rows; r++) {
            var row = ws.getRow(r + 1);
            for (var c = 0; c < table.cols; c++) {
                var cell = table.cells[r][c];
                var typed = typeCell(cell.text);
                var target = row.getCell(c + 1);
                if (typed.value !== null) {
                    target.value = typeof typed.value === 'string' ? typed.value.slice(0, CELL_LIMIT) : typed.value;
                    if (typed.numFmt) target.numFmt = typed.numFmt;
                    // A string that begins like a formula stays plain text, in the cell format too.
                    if (typeof typed.value === 'string' && /^[=+\-@]/.test(typed.value.trim())) target.numFmt = '@';
                }
                if (/\n/.test(cell.text)) target.alignment = { wrapText: true, vertical: 'top' };
                var runs = cellRuns(cell);
                var bold = runs.length > 0 && runs.every(function (x) { return x.bold; });
                if (bold) target.font = { bold: true };
            }
        }
        if (table.xs && table.xs.length === table.cols + 1) {
            for (var k = 0; k < table.cols; k++) {
                ws.getColumn(k + 1).width = Math.max(8, Math.min(70, Math.round((table.xs[k + 1] - table.xs[k]) / 5.25)));
            }
        }
        return ws;
    }

    // ── conversion ────────────────────────────────────────────────────────

    L.register('/api/pdf/to-excel', async function (fd, ctx) {
        ctx = ctx || {};
        var layout = L.layout;
        if (!layout) throw new L.Unsupported('layout engine missing', 'engine_unavailable');
        var files = L.files(fd, 'file');
        if (!files.length) throw new L.Error('No file provided.');
        var file = files[0];

        if (L.str(fd, 'password', null)) throw new L.Unsupported('password-protected PDFs need the server', 'encrypted');

        var budget = L.constrained() ? BUDGET.mobile : BUDGET.desktop;
        if (file.size > budget.bytes) throw new L.Unsupported('input exceeds the on-device Excel budget', 'resource_budget_exceeded');

        var pdfjs = await L.loadPdfJs();
        var ExcelJS = await L.loadExcelJs();
        var doc = await layout.openDocument(file);

        try {
            var total = doc.numPages;
            if (total > budget.pages) throw new L.Unsupported('page count exceeds the on-device Excel budget', 'resource_budget_exceeded');

            var pageTables = [];
            var pageText = [];
            var cells = 0;
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
                page.cleanup();
                // The server OCRs scans; an empty result here would silently drop their content.
                if (!model.chars && model.anyRaster) throw new L.Unsupported('scanned pages need OCR', 'ocr_required');
                model.tables.forEach(function (t) { cells += t.rows * t.cols; });
                if (cells > budget.cells) throw new L.Unsupported('too many table cells for this device', 'resource_budget_exceeded');
                pageTables.push(model.tables);
                pageText.push(model.ordered.map(function (l) { return l.text.replace(/\t/g, ' '); }).join('\n').trim());
                if (ctx.onProgress) ctx.onProgress(n, total);
                await L.tick();
            }

            var wb = new ExcelJS.Workbook();
            var found = 0;
            pageTables.forEach(function (tables, i) {
                tables.forEach(function (t, k) {
                    found++;
                    addTableSheet(wb, ('P' + (i + 1) + '_T' + (k + 1)).slice(0, 31), t);
                });
            });
            if (!found) {
                var ws = wb.addWorksheet('Text Content');
                ws.addRow(['Page', 'Text']);
                pageText.forEach(function (text, i) {
                    if (!text) return;
                    // One row per page; a very long page is continued on further rows.
                    for (var at = 0; at < text.length; at += CELL_LIMIT) ws.addRow([i + 1, text.slice(at, at + CELL_LIMIT)]);
                });
            }

            L.checkAbort(ctx.signal);
            var buffer = await wb.xlsx.writeBuffer();
            return {
                blob: new Blob([buffer], { type: XLSX_MIME }),
                filename: L.brandedName(file.name, 'xlsx'),
                message: 'Extracted ' + found + ' table(s) to Excel',
                extra: { tables_found: found },
            };
        } catch (err2) {
            if (err2 instanceof L.Error || err2 instanceof L.Unsupported || (err2 && err2.name === 'AbortError')) throw err2;
            throw new L.Unsupported('tables could not be read on-device', 'unsupported_structure');
        } finally {
            try { await doc.destroy(); } catch (e) { /* already gone */ }
        }
    });

    L.pdfExcel = { typeCell: typeCell };
})();
