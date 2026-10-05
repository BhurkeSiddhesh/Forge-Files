// Work packages 32, 33, 34: On-device Excel and CSV operations.
//
// Tests CSV to XLSX (WP32), XLSX to CSV (WP33), and Merge Excel (WP34) running
// inside a node vm with vendored ExcelJS.
// Run with `node --test public/tests/local_excel.test.mjs`.

import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const STATIC = join(dirname(fileURLToPath(import.meta.url)), '..', 'static');

function load(options = {}) {
    const fetchCalls = [];
    const URLShim = function (...a) { return new URL(...a); };
    URLShim.createObjectURL = () => 'blob:mock/1';
    URLShim.revokeObjectURL = () => { };

    const sandbox = {
        console, Blob, File, FormData, Response, TextEncoder, TextDecoder,
        Buffer, Uint8Array, ArrayBuffer,
        FileReader: class {
            readAsArrayBuffer(b) {
                b.arrayBuffer().then(buf => { this.result = buf; this.onload && this.onload(); });
            }
            readAsText(b) {
                b.text().then(txt => { this.result = txt; this.onload && this.onload(); });
            }
        },
        Image: class { },
        URL: URLShim,
        setTimeout, clearTimeout, AbortController,
        document: {
            currentScript: null,
            head: { appendChild() { } },
            createElement: (tag) => (tag === 'canvas'
                ? { width: 0, height: 0, toBlob() { }, getContext: () => null }
                : { set src(_v) { }, onload: null, onerror: null }),
        },
        fetch(url, init) {
            fetchCalls.push({ url, init });
            return Promise.resolve(new Response('{"server":true}'));
        },
    };
    sandbox.window = sandbox;
    sandbox.self = sandbox;
    sandbox.window.apiUrl = (p) => `https://api.test${p}`;
    if (options.matchMedia) sandbox.window.matchMedia = options.matchMedia;

    vm.createContext(sandbox);

    for (const f of [
        'vendor/exceljs.min.js',
        'vendor/jszip.min.js',
        'local/ff-server-gate.js',
        'local/ff-local.js',
        'local/ops-excel.js',
    ]) {
        vm.runInContext(readFileSync(join(STATIC, f), 'utf8'), sandbox, { filename: f });
    }

    const asked = [];
    sandbox.ffConsent.handler = (info) => {
        asked.push(info);
        return options.consent === true;
    };

    return { sandbox, L: sandbox.ffLocal, fetchCalls, asked };
}

test('WP32: CSV to XLSX basic conversion and cell typing', async () => {
    const ctx = load();
    const fd = new FormData();
    const csvContent = 'Name,Age,Active,Code\nAlice,30,true,0123\nBob,25.5,false,456\n';
    fd.append('file', new Blob([csvContent], { type: 'text/csv' }), 'people.csv');

    const res = await ctx.sandbox.ffProcess('/api/excel/csv-to-xlsx', fd);
    assert.equal(res.status, 200);
    const data = await res.json();
    assert.equal(data.status, 'success');
    assert.match(data.filename, /^people_forgefiles\.org\.xlsx$/);

    const item = ctx.L.resolve(data.download_token);
    assert.ok(item && item.blob);

    const wb = new ctx.sandbox.ExcelJS.Workbook();
    await wb.xlsx.load(Buffer.from(await item.blob.arrayBuffer()));
    assert.equal(wb.worksheets.length, 1);
    const ws = wb.worksheets[0];
    assert.equal(ws.name, 'people');
    assert.equal(ws.rowCount, 3);

    // Row 1 (headers)
    assert.equal(ws.getCell('A1').value, 'Name');
    assert.equal(ws.getCell('B1').value, 'Age');

    // Row 2
    assert.equal(ws.getCell('A2').value, 'Alice');
    assert.equal(ws.getCell('B2').value, 30);
    assert.equal(typeof ws.getCell('B2').value, 'number');
    assert.equal(ws.getCell('D2').value, '0123'); // Leading zero preserved as string

    // Row 3
    assert.equal(ws.getCell('A3').value, 'Bob');
    assert.equal(ws.getCell('B3').value, 25.5);
    assert.equal(typeof ws.getCell('B3').value, 'number');
});

test('WP32: CSV to XLSX delimiter support (semicolon, tab, pipe)', async () => {
    const ctx = load();

    // Semicolon
    const fd1 = new FormData();
    fd1.append('file', new Blob(['col1;col2\nval1;val2'], { type: 'text/csv' }), 'semi.csv');
    fd1.append('delimiter', ';');
    const res1 = await ctx.sandbox.ffProcess('/api/excel/csv-to-xlsx', fd1);
    assert.equal(res1.status, 200);
    const d1 = await res1.json();
    const wb1 = new ctx.sandbox.ExcelJS.Workbook();
    await wb1.xlsx.load(Buffer.from(await ctx.L.resolve(d1.download_token).blob.arrayBuffer()));
    assert.equal(wb1.worksheets[0].getCell('B1').value, 'col2');

    // Tab
    const fd2 = new FormData();
    fd2.append('file', new Blob(['col1\tcol2\nval1\tval2'], { type: 'text/csv' }), 'tab.csv');
    fd2.append('delimiter', '\\t');
    const res2 = await ctx.sandbox.ffProcess('/api/excel/csv-to-xlsx', fd2);
    assert.equal(res2.status, 200);
    const d2 = await res2.json();
    const wb2 = new ctx.sandbox.ExcelJS.Workbook();
    await wb2.xlsx.load(Buffer.from(await ctx.L.resolve(d2.download_token).blob.arrayBuffer()));
    assert.equal(wb2.worksheets[0].getCell('B1').value, 'col2');

    // Invalid delimiter rejected with 400
    const fd3 = new FormData();
    fd3.append('file', new Blob(['a,b'], { type: 'text/csv' }), 'test.csv');
    fd3.append('delimiter', 'invalid');
    const res3 = await ctx.sandbox.ffProcess('/api/excel/csv-to-xlsx', fd3);
    assert.equal(res3.status, 400);
});

test('WP32: CSV to XLSX formula-injection guard and UTF-8 BOM', async () => {
    const ctx = load();
    const fd = new FormData();
    // Prepend UTF-8 BOM \uFEFF and include formula-injection attempts
    const bomCsv = '\uFEFFFormula,Plus,Minus,At,Normal\n=1+1,+2-1,-5+3,@SUM(A1),hello\n';
    fd.append('file', new Blob([bomCsv], { type: 'text/csv' }), 'safe.csv');

    const res = await ctx.sandbox.ffProcess('/api/excel/csv-to-xlsx', fd);
    assert.equal(res.status, 200);
    const data = await res.json();
    const wb = new ctx.sandbox.ExcelJS.Workbook();
    await wb.xlsx.load(Buffer.from(await ctx.L.resolve(data.download_token).blob.arrayBuffer()));
    const ws = wb.worksheets[0];

    // BOM was cleanly stripped: header 1 is 'Formula', not '\uFEFFFormula'
    assert.equal(ws.getCell('A1').value, 'Formula');

    // Formula injection cells are treated as text and formatted with @
    assert.equal(ws.getCell('A2').value, '=1+1');
    assert.equal(ws.getCell('A2').numFmt, '@');

    assert.equal(ws.getCell('B2').value, '+2-1');
    assert.equal(ws.getCell('B2').numFmt, '@');

    assert.equal(ws.getCell('C2').value, '-5+3');
    assert.equal(ws.getCell('C2').numFmt, '@');

    assert.equal(ws.getCell('D2').value, '@SUM(A1)');
    assert.equal(ws.getCell('D2').numFmt, '@');

    assert.equal(ws.getCell('E2').value, 'hello');
});

test('WP32: CSV to XLSX RFC 4180 multiline and escaped quotes', async () => {
    const ctx = load();
    const fd = new FormData();
    const multilineCsv = 'ID,Description\n1,"Line 1\nLine 2"\n2,"He said ""Hello!"""\n';
    fd.append('file', new Blob([multilineCsv], { type: 'text/csv' }), 'quotes.csv');

    const res = await ctx.sandbox.ffProcess('/api/excel/csv-to-xlsx', fd);
    assert.equal(res.status, 200);
    const data = await res.json();
    const wb = new ctx.sandbox.ExcelJS.Workbook();
    await wb.xlsx.load(Buffer.from(await ctx.L.resolve(data.download_token).blob.arrayBuffer()));
    const ws = wb.worksheets[0];

    assert.equal(ws.getCell('B2').value, 'Line 1\nLine 2');
    assert.equal(ws.getCell('B3').value, 'He said "Hello!"');
});

async function makeXlsx(ctx, sheetName, rows) {
    const build = vm.runInContext(`(async (sheetName, jsonStr) => {
        const rows = JSON.parse(jsonStr);
        const wb = new ExcelJS.Workbook();
        const ws = wb.addWorksheet(sheetName);
        for (const r of rows) ws.addRow(r);
        return await wb.xlsx.writeBuffer();
    })`, ctx.sandbox);
    return await build(sheetName, JSON.stringify(rows));
}

test('WP33: XLSX to CSV basic export and RFC 4180 escaping', async () => {
    const ctx = load();

    const xlsxBuf = await makeXlsx(ctx, 'Summary', [
        ['Name', 'Quote', 'Count'],
        ['Apple', 'Fresh, crisp', 10],
        ['Banana', 'Say "yum"', 20],
    ]);

    const fd = new FormData();
    fd.append('file', new Blob([xlsxBuf], { type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' }), 'fruits.xlsx');

    const res = await ctx.sandbox.ffProcess('/api/excel/xlsx-to-csv', fd);
    assert.equal(res.status, 200);
    const data = await res.json();
    assert.match(data.filename, /^fruits_forgefiles\.org\.csv$/);

    const item = ctx.L.resolve(data.download_token);
    const csvText = await item.blob.text();
    const expected =
        'Name,Quote,Count\r\n' +
        'Apple,"Fresh, crisp",10\r\n' +
        'Banana,"Say ""yum""",20\r\n';
    assert.equal(csvText, expected);
});

test('WP33: XLSX to CSV sheet selection and non-existent sheet error', async () => {
    const ctx = load();

    const createMulti = vm.runInContext(`(async () => {
        const wb = new ExcelJS.Workbook();
        const s1 = wb.addWorksheet('First');
        s1.addRow(['1A', '1B']);
        const s2 = wb.addWorksheet('Second');
        s2.addRow(['2A', '2B']);
        return await wb.xlsx.writeBuffer();
    })`, ctx.sandbox);
    const xlsxBuf = await createMulti();

    // Select second sheet
    const fd1 = new FormData();
    fd1.append('file', new Blob([xlsxBuf]), 'multi.xlsx');
    fd1.append('sheet', 'Second');
    const res1 = await ctx.sandbox.ffProcess('/api/excel/xlsx-to-csv', fd1);
    assert.equal(res1.status, 200);
    const csvText = await ctx.L.resolve((await res1.json()).download_token).blob.text();
    assert.equal(csvText.trim(), '2A,2B');

    // Select invalid sheet -> 400 error matching Python ValueError
    const fd2 = new FormData();
    fd2.append('file', new Blob([xlsxBuf]), 'multi.xlsx');
    fd2.append('sheet', 'NonExistent');
    const res2 = await ctx.sandbox.ffProcess('/api/excel/xlsx-to-csv', fd2);
    assert.equal(res2.status, 400);
    const err = await res2.json();
    assert.match(err.detail, /Sheet 'NonExistent' not found/);
});

test('WP33: XLSX to CSV formula cell handling (cached vs missing)', async () => {
    const ctx = load();

    // 1. Workbook with cached formula result
    const buildCached = vm.runInContext(`(async () => {
        const wb = new ExcelJS.Workbook();
        const ws = wb.addWorksheet('Formulas');
        ws.getCell('A1').value = 10;
        ws.getCell('B1').value = 20;
        ws.getCell('C1').value = { formula: 'A1+B1', result: 30 };
        return await wb.xlsx.writeBuffer();
    })`, ctx.sandbox);
    const buf1 = await buildCached();

    const fd1 = new FormData();
    fd1.append('file', new Blob([buf1]), 'cached.xlsx');
    const res1 = await ctx.sandbox.ffProcess('/api/excel/xlsx-to-csv', fd1);
    assert.equal(res1.status, 200);
    const csv1 = await ctx.L.resolve((await res1.json()).download_token).blob.text();
    assert.equal(csv1.trim(), '10,20,30');

    // 2. Workbook with missing cached formula result -> lab acceptance criteria:
    // never silently export blank; decline or fall through to server with consent
    const ctxConsent = load({ consent: false });
    const buildUncached = vm.runInContext(`(async () => {
        const wb = new ExcelJS.Workbook();
        const ws = wb.addWorksheet('Uncached');
        ws.getCell('A1').value = { formula: 'SUM(1,2)', result: undefined };
        return await wb.xlsx.writeBuffer();
    })`, ctxConsent.sandbox);
    const buf2 = await buildUncached();

    const fd2 = new FormData();
    fd2.append('file', new Blob([buf2]), 'uncached.xlsx');
    const res2 = await ctxConsent.sandbox.ffProcess('/api/excel/xlsx-to-csv', fd2);
    assert.equal(res2.status, 499);
    assert.equal(ctxConsent.asked.length, 1);
    assert.equal(ctxConsent.fetchCalls.length, 0); // No upload when declined
});

test('WP34: Merge Excel combining workbooks into unique sheets', async () => {
    const ctx = load();

    const buf1 = await makeXlsx(ctx, 'Data', [['Book1', 100]]);
    const buf2 = await makeXlsx(ctx, 'Data', [['Book2', 200]]);

    const fd = new FormData();
    fd.append('files', new Blob([buf1]), 'FileA.xlsx');
    fd.append('files', new Blob([buf2]), 'FileB.xlsx');

    const res = await ctx.sandbox.ffProcess('/api/excel/merge', fd);
    assert.equal(res.status, 200);
    const data = await res.json();
    assert.match(data.filename, /^merged_[a-f0-9]{8}\.xlsx$/);

    const mergedWb = new ctx.sandbox.ExcelJS.Workbook();
    await mergedWb.xlsx.load(Buffer.from(await ctx.L.resolve(data.download_token).blob.arrayBuffer()));
    assert.equal(mergedWb.worksheets.length, 2);

    const sheetNames = mergedWb.worksheets.map(s => s.name);
    // Sheet names must be <= 31 chars and unique
    assert.equal(sheetNames[0], 'FileA_Data');
    assert.equal(sheetNames[1], 'FileB_Data');
    assert.equal(mergedWb.worksheets[0].getCell('A1').value, 'Book1');
    assert.equal(mergedWb.worksheets[1].getCell('A1').value, 'Book2');
});

test('WP34: Merge Excel macro rejection (.xlsm)', async () => {
    const ctx = load({ consent: false });
    const fd = new FormData();
    fd.append('files', new Blob(['dummy']), 'macro.xlsm');
    fd.append('files', new Blob(['dummy']), 'normal.xlsx');

    const res = await ctx.sandbox.ffProcess('/api/excel/merge', fd);
    assert.equal(res.status, 499);
    assert.equal(ctx.asked.length, 1);
    assert.equal(ctx.fetchCalls.length, 0); // Declined -> zero bytes uploaded
});

test('WP34: Merge Excel requires at least two files', async () => {
    const ctx = load();
    const fd = new FormData();
    fd.append('files', new Blob(['dummy']), 'one.xlsx');

    const res = await ctx.sandbox.ffProcess('/api/excel/merge', fd);
    assert.equal(res.status, 400);
    const err = await res.json();
    assert.match(err.detail, /at least two Excel files/);
});

async function xlsxBuffer(ctx, build) {
    const wb = new ctx.sandbox.ExcelJS.Workbook();
    const ws = wb.addWorksheet('S');
    build(ws);
    return Buffer.from(await wb.xlsx.writeBuffer());
}

async function withPart(ctx, buf, name, body) {
    const zip = await ctx.sandbox.JSZip.loadAsync(buf);
    zip.file(name, body);
    return Buffer.from(await zip.generateAsync({ type: 'nodebuffer' }));
}

test('WP34: Merge Excel refuses charts and external links, copies formulas as values', async () => {
    const ctx = load();
    const plain = await xlsxBuffer(ctx, ws => { ws.getCell('A1').value = { formula: '1+1', result: 2 }; });
    const other = await xlsxBuffer(ctx, ws => { ws.getCell('A1').value = 'x'; });

    // Value-only: the merged cell holds the cached result, not the formula.
    const ok = new FormData();
    ok.append('files', new Blob([plain]), 'a.xlsx');
    ok.append('files', new Blob([other]), 'b.xlsx');
    const res = await ctx.sandbox.ffProcess('/api/excel/merge', ok);
    assert.equal(res.status, 200);
    const item = ctx.L.resolve((await res.json()).download_token);
    const wb = new ctx.sandbox.ExcelJS.Workbook();
    await wb.xlsx.load(Buffer.from(await item.blob.arrayBuffer()));
    assert.equal(wb.worksheets[0].getCell('A1').value, 2);

    for (const part of ['xl/charts/chart1.xml', 'xl/externalLinks/externalLink1.xml']) {
        const bad = await withPart(ctx, plain, part, '<x/>');
        const fd = new FormData();
        fd.append('files', new Blob([bad]), 'c.xlsx');
        fd.append('files', new Blob([other]), 'b.xlsx');
        const declined = await ctx.sandbox.ffProcess('/api/excel/merge', fd);
        assert.equal(declined.status, 499, part);
    }
    assert.equal(ctx.fetchCalls.length, 0);
    assert.equal(ctx.asked.length, 2);
});

test('WP32: CSV encoding is validated, never guessed', async () => {
    const ctx = load();
    // Latin-1 bytes are not valid UTF-8: ask, never convert mojibake.
    const fd = new FormData();
    fd.append('file', new Blob([Buffer.from([0x63, 0x61, 0x66, 0xE9, 0x0A, 0x31, 0x0A])]), 'latin.csv');
    const res = await ctx.sandbox.ffProcess('/api/excel/csv-to-xlsx', fd);
    assert.equal(res.status, 499);
    assert.equal(ctx.fetchCalls.length, 0);

    // UTF-16LE with a BOM decodes locally.
    const utf16 = Buffer.concat([Buffer.from([0xFF, 0xFE]), Buffer.from('a,b\n1,2\n', 'utf16le')]);
    const fd2 = new FormData();
    fd2.append('file', new Blob([utf16]), 'wide.csv');
    const ok = await ctx.sandbox.ffProcess('/api/excel/csv-to-xlsx', fd2);
    assert.equal(ok.status, 200);
    const item = ctx.L.resolve((await ok.json()).download_token);
    const wb = new ctx.sandbox.ExcelJS.Workbook();
    await wb.xlsx.load(Buffer.from(await item.blob.arrayBuffer()));
    assert.equal(wb.worksheets[0].getCell('A1').value, 'a');
    assert.equal(wb.worksheets[0].getCell('B2').value, 2);
});
