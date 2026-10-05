// Work package 42: the pure parts of PDF to Word, with the real vendored docx writer.
//
// Real PDFs through real pdf.js are covered in Chromium by test_local_word_parity.py. These tests
// feed synthetic page models to the document builder and read the XML back.
import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const STATIC = join(dirname(fileURLToPath(import.meta.url)), '..', 'static');

function load() {
    const sandbox = {
        console, Blob, FormData, Response, TextEncoder, TextDecoder, AbortController, setTimeout, clearTimeout, Buffer,
        Uint8Array, Uint16Array, Math, Object, Array, String, Number, RegExp, Date, Error, Promise, Symbol, Map, Set,
        FileReader: class { }, Image: class { },
        URL: Object.assign(function () { }, { createObjectURL: () => 'blob:x', revokeObjectURL() { } }),
        document: { currentScript: null, head: { appendChild() { } }, createElement: () => ({ toBlob() { } }) },
    };
    sandbox.window = sandbox;
    sandbox.self = sandbox;
    sandbox.window.apiUrl = (p) => p;
    vm.createContext(sandbox);
    for (const f of ['local/ff-server-gate.js', 'local/ff-local.js', 'local/ops-pdf-layout.js', 'local/ops-pdf-word.js', 'vendor/jszip.min.js', 'vendor/docx.iife.js']) {
        vm.runInContext(readFileSync(join(STATIC, f), 'utf8'), sandbox, { filename: f });
    }
    return sandbox;
}

const S = load();
const L = S.ffLocal;
const H = 800;
const W = 600;

const item = (str, x, y, size = 10, font = 'f1') => ({
    str, dir: 'ltr', width: str.length * size * 0.55, height: size, transform: [size, 0, 0, size, x, H - y], fontName: font,
});

/** Analyse synthetic items into a page model with the fields buildDocument reads. */
function model(items, n = 1, rules = { h: [], v: [] }) {
    const p = L.layout.analysePage({ items, styles: { f1: {} }, view: [0, 0, W, H], links: [] });
    const found = L.layout.findTables(p, rules);
    p.n = n;
    p.tables = found.tables;
    p.flow = found.flow;
    p.flowColumns = found.flowColumns;
    p.figures = [];
    return p;
}

async function build(pages) {
    const d = S.docx;
    const doc = L.word.buildDocument(d, pages, { title: 'T', author: 'A' });
    const blob = await d.Packer.toBlob(doc);
    const zip = await S.JSZip.loadAsync(await blob.arrayBuffer());
    return { zip, xml: await zip.file('word/document.xml').async('string') };
}

test('PDF font names map to a Word family, without the style suffix', () => {
    const f = (o) => L.word.wordFont(o);
    assert.equal(f({ font: 'Liberation Sans Bold' }), 'Arial');
    assert.equal(f({ font: 'ArialMT' }), 'Arial');
    assert.equal(f({ font: 'Helvetica-Bold' }), 'Arial');
    assert.equal(f({ font: 'TimesNewRomanPS-BoldMT' }), 'Times New Roman');
    assert.equal(f({ font: 'Courier' }), 'Courier New');
    assert.equal(f({ font: 'Calibri-Light' }), 'Calibri');
    assert.equal(f({ font: 'Noto Sans Devanagari Regular' }), 'Noto Sans Devanagari');
    assert.equal(f({ font: 'g_d0_f1' }), 'Arial');
    assert.equal(f({ font: 'g_d0_f1', serif: true }), 'Times New Roman');
    assert.equal(f({ font: '', mono: true }), 'Courier New');
});

test('one section per page, sized like the page, with a right tab stop for a right-aligned date', async () => {
    const p1 = model([item('Company name', 72, 100, 11), item('2020', 460, 100, 11), item('Some body text on the page.', 72, 130)]);
    const p2 = model([item('Second page text.', 72, 100)], 2);
    const { xml } = await build([p1, p2]);
    assert.equal((xml.match(/<w:sectPr/g) || []).length, 2);
    assert.match(xml, /<w:pgSz w:w="12000" w:h="16000"/); // 600 x 800 pt in twips
    assert.match(xml, /<w:tab w:val="right" w:pos="/);
    assert.match(xml, /w:lineRule="exact"/);
    assert.match(xml, /<w:rFonts w:ascii="Arial"/);
});

test('bullets and numbers become Word list paragraphs and the glyph is not in the text', async () => {
    const p = model([
        item('• first', 72, 100), item('• second', 72, 114),
        item('1. one', 72, 150), item('2. two', 72, 164),
    ]);
    const { xml } = await build([p]);
    assert.equal((xml.match(/<w:numPr>/g) || []).length, 4);
    assert.ok(!xml.includes('•'));
    assert.match(xml, />first</);
    assert.match(xml, />two</);
});

test('a ruled grid is emitted as a Word table, before the paragraph that follows it', async () => {
    const xs = [72, 172, 272];
    const ys = [100, 124, 148];
    const h = ys.map((y) => ({ y, x0: 72, x1: 272 }));
    const v = xs.map((x) => ({ x, y0: 100, y1: 148 }));
    const p = model([item('A1', 80, 116), item('B1', 180, 116), item('A2', 80, 140), item('B2', 180, 140), item('After the table', 72, 200)], 1, { h, v });
    const { xml } = await build([p]);
    assert.equal((xml.match(/<w:tbl>/g) || []).length, 1);
    assert.equal((xml.match(/<w:tc>/g) || []).length, 4);
    assert.match(xml, /<w:tcBorders>/);
    assert.ok(xml.indexOf('A1') < xml.indexOf('After the table'));
});

test('control characters are stripped so the XML stays valid', async () => {
    const p = model([item('ok\u0001text\u0008here', 72, 100)]);
    const { xml } = await build([p]);
    assert.ok(!/[\u0000-\u0008\u000B\u000C\u000E-\u001F]/.test(xml));
    assert.match(xml, /oktexthere/);
});

test('an empty page still produces a section, so the page count is kept', async () => {
    const { xml } = await build([model([], 1), model([item('text', 72, 100)], 2)]);
    assert.equal((xml.match(/<w:sectPr/g) || []).length, 2);
});
