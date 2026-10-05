// Work package 21: how PDF to Excel decides what a cell is.
//
// The whole conversion (real pdf.js, real ExcelJS, openpyxl read-back) is covered in Chromium by
// test_local_excel_pdf_parity.py. These tests pin the cell-typing rules, which are the part most
// likely to turn data into the wrong thing.
import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const STATIC = join(dirname(fileURLToPath(import.meta.url)), '..', 'static');

function load() {
    const sandbox = {
        console, Blob, FormData, Response, TextEncoder, TextDecoder, AbortController, setTimeout, clearTimeout,
        Uint8Array, Uint16Array, Math, Object, Array, String, Number, RegExp, Date, Error, parseFloat, isFinite,
        FileReader: class { }, Image: class { },
        URL: Object.assign(function () { }, { createObjectURL: () => 'blob:x', revokeObjectURL() { } }),
        document: { currentScript: null, head: { appendChild() { } }, createElement: () => ({ toBlob() { } }) },
    };
    sandbox.window = sandbox;
    sandbox.self = sandbox;
    sandbox.window.apiUrl = (p) => p;
    vm.createContext(sandbox);
    for (const f of ['local/ff-server-gate.js', 'local/ff-local.js', 'local/ops-pdf-layout.js', 'local/ops-pdf-excel.js']) {
        vm.runInContext(readFileSync(join(STATIC, f), 'utf8'), sandbox, { filename: f });
    }
    return sandbox;
}

const typeCell = load().ffLocal.pdfExcel.typeCell;
const t = (s) => JSON.parse(JSON.stringify(typeCell(s)));

test('plain and grouped numbers become numbers, with a format only when one is needed', () => {
    assert.deepEqual(t('1250'), { value: 1250 });
    assert.deepEqual(t('0'), { value: 0 });
    assert.deepEqual(t('19.99'), { value: 19.99 });
    assert.deepEqual(t('1,250'), { value: 1250, numFmt: '#,##0' });
    assert.deepEqual(t('1,250.00'), { value: 1250, numFmt: '#,##0.00' });
    assert.deepEqual(t('-12'), { value: -12 });
    assert.deepEqual(t('12,34,567'), { value: 1234567, numFmt: '#,##0' }); // Indian grouping
});

test('accounting negatives keep their parentheses on screen', () => {
    assert.deepEqual(t('(500.00)'), { value: -500, numFmt: '#,##0.00;(#,##0.00)' });
    assert.deepEqual(t('(500)'), { value: -500, numFmt: '#,##0;(#,##0)' });
    assert.equal(t('(500').value, '(500'); // unbalanced is text
});

test('currency and percent keep their symbols through the number format', () => {
    assert.deepEqual(t('$1,250.50'), { value: 1250.5, numFmt: '"$"#,##0.00' });
    assert.deepEqual(t('₹1,00,000'), { value: 100000, numFmt: '"₹"#,##0.00' });
    assert.deepEqual(t('12%'), { value: 0.12, numFmt: '0%' });
    assert.deepEqual(t('12.5 %'), { value: 0.125, numFmt: '0.0%' });
});

test('identifiers and anything that is not clearly a quantity stay text', () => {
    for (const s of ['007', '0012', '00', '+91 98765 43210', '+15550100', '123456789012', '12345678901234567890', 'n/a', '12abc', '1,25', '1,2345', '3.14.15', 'Q1', '2021 – 2025', '12:30']) {
        assert.equal(t(s).value, s, s);
        assert.equal(t(s).numFmt, undefined, s);
    }
});

test('empty and whitespace-only cells are blank, and text keeps its own spacing', () => {
    assert.equal(t('').value, null);
    assert.equal(t('   ').value, null);
    assert.equal(t(null).value, null);
    assert.equal(t(' padded text ').value, ' padded text ');
    assert.equal(t('1 250').value, '1 250'); // a non-breaking space is not a thousands separator
});

test('formula-looking text is never turned into a number or formula', () => {
    for (const s of ['=1+1', '+SUM(A1)', '@cmd', '-5 apples', '=HYPERLINK("http://x","y")']) {
        assert.equal(typeof t(s).value, 'string', s);
        assert.equal(t(s).value, s);
    }
});
