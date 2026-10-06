// On-device Excel and CSV operations (migration work packages 32, 33, 34).
//
//   * /api/excel/csv-to-xlsx: streaming CSV parser + ExcelJS workbook (WP32)
//   * /api/excel/xlsx-to-csv: ExcelJS parser + RFC 4180 CSV serializer (WP33)
//   * /api/excel/merge: multi-workbook sheet-combining with unique names (WP34)
//
// Matches Python excel_utils.py contracts, validation errors, and branded filenames.
// Formulas without cached values, workbooks with macros, or over-budget files
// raise FFLocalUnsupported, asking the user before the server is used.
(function () {
    'use strict';

    var L = window.ffLocal;
    if (!L) return;

    var MIB = 1024 * 1024;

    // Resource budgets
    var BUDGETS = {
        csvToXlsxDesktop: 100 * MIB,
        csvToXlsxPhone: 25 * MIB,
        xlsxToCsvDesktop: 50 * MIB,
        xlsxToCsvPhone: 15 * MIB,
        mergeDesktop: 100 * MIB,
        mergePhone: 30 * MIB,
        maxRows: 100000,
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

    /**
     * Decode CSV bytes without ever guessing. A UTF-8/UTF-16 BOM is honoured;
     * otherwise the bytes must be valid UTF-8. Anything else (legacy code pages,
     * BOM-less UTF-16, binary) raises Unsupported('undecodable') so the user is
     * asked before the server is used, rather than converting mojibake.
     */
    async function decodeCsv(file) {
        var bytes = new Uint8Array(await bytesOf(file));
        var label = 'utf-8';
        var offset = 0;
        if (bytes.length >= 3 && bytes[0] === 0xEF && bytes[1] === 0xBB && bytes[2] === 0xBF) {
            offset = 3;
        } else if (bytes.length >= 2 && bytes[0] === 0xFF && bytes[1] === 0xFE) {
            label = 'utf-16le'; offset = 2;
        } else if (bytes.length >= 2 && bytes[0] === 0xFE && bytes[1] === 0xFF) {
            label = 'utf-16be'; offset = 2;
        } else if (bytes.indexOf(0) >= 0) {
            throw new L.Unsupported('CSV contains NUL bytes (binary or BOM-less UTF-16)', 'undecodable');
        }
        var text;
        try {
            text = new TextDecoder(label, { fatal: true }).decode(offset ? bytes.subarray(offset) : bytes);
        } catch (err) {
            throw new L.Unsupported('CSV is not valid ' + label, 'undecodable');
        }
        if (text.indexOf('\uFFFD') >= 0) {
            throw new L.Unsupported('CSV contains replacement characters', 'undecodable');
        }
        return text;
    }

    /** Parse CSV text into 2D array, handling BOM, delimiters, quotes, newlines. */
    function parseCsv(text, delimiter) {
        if (!delimiter) delimiter = ',';
        if (text.charCodeAt(0) === 0xFEFF) text = text.slice(1);
        var rows = [];
        var row = [];
        var cell = '';
        var inQuotes = false;
        var i = 0;
        var len = text.length;

        while (i < len) {
            var ch = text[i];
            if (inQuotes) {
                if (ch === '"') {
                    if (i + 1 < len && text[i + 1] === '"') {
                        cell += '"';
                        i++;
                    } else {
                        inQuotes = false;
                    }
                } else {
                    cell += ch;
                }
            } else {
                if (ch === '"') {
                    inQuotes = true;
                } else if (ch === delimiter) {
                    row.push(cell);
                    cell = '';
                } else if (ch === '\r') {
                    if (i + 1 < len && text[i + 1] === '\n') i++;
                    row.push(cell);
                    rows.push(row);
                    row = [];
                    cell = '';
                } else if (ch === '\n') {
                    row.push(cell);
                    rows.push(row);
                    row = [];
                    cell = '';
                } else {
                    cell += ch;
                }
            }
            i++;
        }
        if (cell !== '' || row.length > 0) {
            row.push(cell);
            rows.push(row);
        }
        return rows;
    }

    /** Escape a cell string for RFC 4180 CSV export. */
    function escapeCsvCell(val, delimiter) {
        if (val === null || val === undefined) return '';
        var s = String(val);
        if (s.indexOf(delimiter) >= 0 || s.indexOf('"') >= 0 || s.indexOf('\n') >= 0 || s.indexOf('\r') >= 0) {
            return '"' + s.replace(/"/g, '""') + '"';
        }
        return s;
    }

    /** Extract a cell's primitive value; detects formula cells missing cached values. */
    function cellValue(cell) {
        var v = cell.value;
        if (v === null || v === undefined) return '';
        if (typeof v === 'object') {
            // Formula cell
            if ('formula' in v) {
                if (v.result === undefined || v.result === null) {
                    // Acceptance criteria: formula cells never silently export blank
                    throw new L.Unsupported('Formula cell missing cached value requires recalculation', 'unsupported_structure');
                }
                v = v.result;
            } else if (Array.isArray(v.richText)) {
                return v.richText.map(function (t) { return t.text || ''; }).join('');
            } else if (v instanceof Date) {
                return isNaN(v.getTime()) ? '' : v.toISOString();
            } else if ('error' in v) {
                return String(v.error);
            } else if ('text' in v) {
                return String(v.text);
            }
        }
        return v === null || v === undefined ? '' : String(v);
    }

    /**
     * Merge is a values-and-number-formats operation. Workbooks carrying parts
     * ExcelJS would silently drop (macros, charts, drawings, external links,
     * pivots) are refused so the user is asked before the server is used.
     */
    var COMPLEX_PARTS = /^xl\/(vbaProject\.bin|charts\/|chartsheets\/|externalLinks\/|pivotTables\/|pivotCache\/|drawings\/[^/]*\.xml$|embeddings\/)/i;

    async function assertSimpleWorkbook(bytes) {
        var JSZip = await L.loadJsZip();
        var zip;
        try {
            zip = await JSZip.loadAsync(bytes);
        } catch (err) {
            throw new L.Unsupported('Could not open workbook package', 'unsupported_structure');
        }
        var names = Object.keys(zip.files);
        for (var n = 0; n < names.length; n++) {
            if (COMPLEX_PARTS.test(names[n])) {
                throw new L.Unsupported('Workbook contains ' + names[n], 'unsupported_structure');
            }
        }
        var ct = zip.file('[Content_Types].xml');
        if (ct) {
            var xml = await ct.async('string');
            if (/macroEnabled|vbaProject/i.test(xml)) {
                throw new L.Unsupported('Workbook contains macros', 'unsupported_structure');
            }
        }
    }

    /** Value-only copy of a cell: a formula becomes its cached result. */
    function valueOnly(cell) {
        var v = cell.value;
        if (v && typeof v === 'object' && !(v instanceof Date) && ('formula' in v || 'sharedFormula' in v)) {
            if (v.result === undefined || v.result === null) {
                throw new L.Unsupported('Formula cell missing cached value requires recalculation', 'unsupported_structure');
            }
            return v.result;
        }
        return v;
    }

    // ── WP32: CSV to XLSX ───────────────────────────────────────────────────

    L.register('/api/excel/csv-to-xlsx', async function (formData, ctx) {
        ctx = ctx || {};
        var file = formData.get('file');
        if (!file || typeof file.name !== 'string') {
            throw new L.Error('No file provided.');
        }

        var delimiter = L.str(formData, 'delimiter', ',');
        if (delimiter === '\\t') delimiter = '\t';
        if (!delimiter || delimiter.length !== 1) {
            throw new L.Error("delimiter must be a single character (use ',' '\\t' ';' '|').");
        }

        var maxBytes = L.constrained() ? BUDGETS.csvToXlsxPhone : BUDGETS.csvToXlsxDesktop;
        if (file.size > maxBytes) {
            throw new L.Unsupported('CSV file exceeds device budget', 'resource_budget_exceeded');
        }

        L.checkAbort(ctx.signal);

        var text;
        try {
            text = await decodeCsv(file);
        } catch (err) {
            if (err instanceof L.Unsupported) throw err;
            throw new L.Unsupported('Could not decode CSV text', 'undecodable');
        }

        var rows = parseCsv(text, delimiter);
        if (rows.length > BUDGETS.maxRows) {
            throw new L.Unsupported('CSV row count exceeds safe on-device limit', 'resource_budget_exceeded');
        }

        var ExcelJS = await L.loadExcelJs();
        var wb = new ExcelJS.Workbook();
        var sheetTitle = (L.stem(file.name) || 'Sheet1').slice(0, 31);
        var ws = wb.addWorksheet(sheetTitle);

        for (var r = 0; r < rows.length; r++) {
            if (r % 500 === 0) {
                L.checkAbort(ctx.signal);
                if (ctx.onProgress) ctx.onProgress(r, rows.length);
                await L.tick();
            }
            var rowData = rows[r];
            var wsRow = ws.addRow();
            for (var c = 0; c < rowData.length; c++) {
                var val = rowData[c];
                var cell = wsRow.getCell(c + 1);
                // Formula injection guard (WP32 lab criteria): cells starting with = + - @
                if (typeof val === 'string' && /^[=+\-@]/.test(val)) {
                    cell.value = val;
                    cell.numFmt = '@'; // Format as text
                } else if (val !== '' && !isNaN(Number(val)) && !/^[+0][0-9]/.test(val)) {
                    cell.value = Number(val);
                } else {
                    cell.value = val;
                }
            }
        }

        if (ctx.onProgress) ctx.onProgress(rows.length, rows.length);
        L.checkAbort(ctx.signal);

        var buf = await wb.xlsx.writeBuffer();
        return {
            blob: new Blob([buf], { type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' }),
            filename: L.brandedName(file.name, 'xlsx'),
            message: 'CSV converted to XLSX',
        };
    });

    // ── WP33: XLSX to CSV ───────────────────────────────────────────────────

    L.register('/api/excel/xlsx-to-csv', async function (formData, ctx) {
        ctx = ctx || {};
        var file = formData.get('file');
        if (!file || typeof file.name !== 'string') {
            throw new L.Error('No file provided.');
        }

        var sheet = L.str(formData, 'sheet', null);
        var maxBytes = L.constrained() ? BUDGETS.xlsxToCsvPhone : BUDGETS.xlsxToCsvDesktop;
        if (file.size > maxBytes) {
            throw new L.Unsupported('XLSX file exceeds device budget', 'resource_budget_exceeded');
        }

        L.checkAbort(ctx.signal);

        var ExcelJS = await L.loadExcelJs();
        var wb = new ExcelJS.Workbook();
        var buf;
        try {
            buf = await bytesOf(file);
            await wb.xlsx.load(buf);
        } catch (err) {
            throw new L.Unsupported('Could not load Excel workbook', 'unsupported_structure');
        }

        var available = wb.worksheets.map(function (s) { return s.name; });
        var ws;
        if (sheet) {
            ws = wb.getWorksheet(sheet);
            if (!ws) {
                // Match Python's exact ValueError format: f"Sheet '{sheet}' not found. Available: {wb.sheetnames}"
                throw new L.Error("Sheet '" + sheet + "' not found. Available: " + JSON.stringify(available));
            }
        } else {
            ws = wb.worksheets[0];
            if (!ws) throw new L.Error('Workbook contains no sheets.');
        }

        L.checkAbort(ctx.signal);

        var csvLines = [];
        var rowCount = ws.rowCount;
        var colCount = ws.columnCount;

        for (var r = 1; r <= rowCount; r++) {
            if (r % 500 === 0) {
                L.checkAbort(ctx.signal);
                if (ctx.onProgress) ctx.onProgress(r, rowCount);
                await L.tick();
            }
            var row = ws.getRow(r);
            var lineCells = [];
            for (var c = 1; c <= colCount; c++) {
                var cell = row.getCell(c);
                var val = cellValue(cell);
                lineCells.push(escapeCsvCell(val, ','));
            }
            csvLines.push(lineCells.join(','));
        }

        if (ctx.onProgress) ctx.onProgress(rowCount, rowCount);
        L.checkAbort(ctx.signal);

        var csvOutput = csvLines.join('\r\n') + '\r\n';
        return {
            blob: new Blob([csvOutput], { type: 'text/csv;charset=utf-8' }),
            filename: L.brandedName(file.name, 'csv'),
            message: 'XLSX converted to CSV',
        };
    });

    // ── WP34: Merge Excel ───────────────────────────────────────────────────

    L.register('/api/excel/merge', async function (formData, ctx) {
        ctx = ctx || {};
        var fileList = L.files(formData, 'files');
        if (!fileList || fileList.length < 2) {
            throw new L.Error('Provide at least two Excel files to merge.');
        }

        var maxBytes = L.constrained() ? BUDGETS.mergePhone : BUDGETS.mergeDesktop;
        var totalBytes = fileList.reduce(function (sum, f) { return sum + (f.size || 0); }, 0);
        if (totalBytes > maxBytes) {
            throw new L.Unsupported('Files exceed device budget for Excel merge', 'resource_budget_exceeded');
        }

        var ExcelJS = await L.loadExcelJs();
        var outWb = new ExcelJS.Workbook();
        var usedNames = new Set();

        function uniqueName(base) {
            base = (base || 'Sheet').slice(0, 25);
            var candidate = base;
            var i = 1;
            while (usedNames.has(candidate.toLowerCase()) || candidate.length > 31) {
                i++;
                var suffix = '_' + i;
                candidate = base.slice(0, 31 - suffix.length) + suffix;
            }
            usedNames.add(candidate.toLowerCase());
            return candidate;
        }

        for (var fIdx = 0; fIdx < fileList.length; fIdx++) {
            var file = fileList[fIdx];
            L.checkAbort(ctx.signal);

            // Lab acceptance criteria: macros, charts and external links need the server.
            if (/\.xlsm$/i.test(file.name)) {
                throw new L.Unsupported('Workbooks with macros require server processing', 'unsupported_structure');
            }

            var wb = new ExcelJS.Workbook();
            var bytes = await bytesOf(file);
            await assertSimpleWorkbook(bytes);
            try {
                await wb.xlsx.load(bytes);
            } catch (err) {
                throw new L.Unsupported('Could not load Excel workbook', 'unsupported_structure');
            }

            if (wb.vbaProject) {
                throw new L.Unsupported('Workbooks with macros require server processing', 'unsupported_structure');
            }

            var stem = L.stem(file.name);
            wb.eachSheet(function (sheet) {
                var targetName = uniqueName(stem + '_' + sheet.name);
                var dstSheet = outWb.addWorksheet(targetName);
                sheet.eachRow({ includeEmpty: true }, function (row, rn) {
                    var dstRow = dstSheet.getRow(rn);
                    row.eachCell({ includeEmpty: true }, function (cell, cn) {
                        var dstCell = dstRow.getCell(cn);
                        dstCell.value = valueOnly(cell);
                        if (cell.numFmt) dstCell.numFmt = cell.numFmt;
                    });
                });
            });

            if (ctx.onProgress) ctx.onProgress(fIdx + 1, fileList.length);
            await L.tick();
        }

        if (outWb.worksheets.length === 0) {
            outWb.addWorksheet('Sheet1');
        }

        L.checkAbort(ctx.signal);
        var buffer = await outWb.xlsx.writeBuffer();

        return {
            blob: new Blob([buffer], { type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' }),
            filename: 'merged_' + L.hexId(8) + '.xlsx',
            message: 'Excel files merged',
        };
    });

    L.excel = {
        parseCsv: parseCsv,
        escapeCsvCell: escapeCsvCell,
        cellValue: cellValue,
    };
})();
