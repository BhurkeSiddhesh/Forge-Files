// Work packages 42 and 21: table detection in the shared layout module, as pure functions.
//
// Real pdf.js output (vector paths and text from real PDFs) is covered in Chromium by
// test_local_word_parity.py and test_local_excel_pdf_parity.py. These tests pin the geometry
// rules with synthetic input: ruling lines, ruled grids, merged cells, borderless alignment, and
// the rule that prose columns are never taken for a table.
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
        Uint8Array, Uint16Array, Math, Object, Array, String, Number, RegExp, Date, Error,
        FileReader: class { }, Image: class { },
        URL: Object.assign(function () { }, { createObjectURL: () => 'blob:x', revokeObjectURL() { } }),
        document: { currentScript: null, head: { appendChild() { } }, createElement: () => ({ toBlob() { } }) },
    };
    sandbox.window = sandbox;
    sandbox.self = sandbox;
    sandbox.window.apiUrl = (p) => p;
    vm.createContext(sandbox);
    for (const f of ['local/ff-server-gate.js', 'local/ff-local.js', 'local/ops-pdf-layout.js']) {
        vm.runInContext(readFileSync(join(STATIC, f), 'utf8'), sandbox, { filename: f });
    }
    return sandbox;
}

const layout = load().ffLocal.layout;
const J = (v) => JSON.parse(JSON.stringify(v)); // the vm sandbox has its own Array/Object
const same = (a, b) => assert.deepEqual(J(a), b);
const H = 800;
const W = 600;

const item = (str, x, y, size = 10) => ({
    str, dir: 'ltr', width: str.length * size * 0.55, height: size, transform: [size, 0, 0, size, x, H - y], fontName: 'f1',
});
const page = (items) => layout.analysePage({ items, styles: { f1: {} }, view: [0, 0, W, H], links: [] });
const atom = (str, x, y, size = 10) => ({ str, x, x1: x + str.length * 5.5, y, size, bold: false, italic: false, mono: false, serif: false, font: '', href: null });
const OPS = { save: 1, restore: 2, transform: 3, constructPath: 4, stroke: 20, fill: 22, fillStroke: 24, endPath: 28, paintImageXObject: 85 };

function gridRules(xs, ys) {
    return {
        h: ys.map((y) => ({ y, x0: xs[0], x1: xs[xs.length - 1] })),
        v: xs.map((x) => ({ x, y0: ys[0], y1: ys[ys.length - 1] })),
    };
}

function tableItems(rows, colX, rightAlign = []) {
    const items = [];
    rows.forEach((row, r) => row.forEach((val, c) => {
        const w = val.length * 5.5;
        items.push(item(val, rightAlign.includes(c) ? colX[c] - w : colX[c], 100 + r * 16));
    }));
    return items;
}

function columnItems(n) {
    const items = [];
    for (let i = 0; i < n; i++) {
        items.push(item(`left column line ${i} with some words`, 72, 100 + i * 14));
        items.push(item(`right column line ${i} with other words`, 330, 100 + i * 14));
    }
    return items;
}

// ── operators and paths ───────────────────────────────────────────────────

test('path data is parsed into sub-paths and curves are marked', () => {
    const subs = layout.parsePath([0, 10, 10, 1, 50, 10, 1, 50, 30, 1, 10, 30, 4, 4]);
    assert.equal(subs.length, 1);
    assert.equal(subs[0].pts.length, 4);
    assert.equal(subs[0].closed, true);
    assert.equal(layout.parsePath([0, 0, 0, 2, 1, 1, 2, 2, 3, 3])[0].curved, true);
});

test('lines, stroked rectangles and thin filled bars become ruling lines in top-down space', () => {
    const list = {
        fnArray: [OPS.constructPath, OPS.save, OPS.transform, OPS.constructPath, OPS.restore, OPS.constructPath],
        argsArray: [
            [OPS.stroke, [[0, 100, 700, 1, 300, 700]], []],
            null, [1, 0, 0, 1, 10, 20],
            [OPS.stroke, [[0, 50, 100, 1, 150, 100, 1, 150, 200, 1, 50, 200, 4]], []],
            null,
            [OPS.fill, [[0, 72, 600, 1, 400, 600, 1, 400, 601, 1, 72, 601, 4]], []],
        ],
    };
    const r = layout.scanOperators({ OPS }, list, 800, [0, 0]);
    assert.equal(r.rules.h.length, 4); // the line, the rectangle's top and bottom, the bar
    assert.equal(r.rules.v.length, 2); // the rectangle's sides
    assert.equal(Math.round(r.rules.h[0].y), 100); // 800 - 700
    same(r.rules.v.map((s) => Math.round(s.x)).sort((a, b) => a - b), [60, 160]); // 50 and 150, translated by 10
    assert.equal(Math.round(Math.min(...r.rules.v.map((s) => s.y0))), 580); // 800 - (200 + 20)
});

// ── ruled tables ──────────────────────────────────────────────────────────

test('a ruled grid becomes a table with cell text assigned by position', () => {
    const atoms = [atom('A1', 80, 116), atom('B1', 180, 116), atom('A2', 80, 140), atom('B2', 180, 140), atom('outside', 80, 300)];
    const [t] = layout.detectRuledTables(gridRules([72, 172, 272], [100, 124, 148]), atoms);
    assert.equal(t.rows, 2);
    assert.equal(t.cols, 2);
    assert.equal(t.merged, false);
    same(t.cells.map((row) => row.map((c) => c.text)), [['A1', 'B1'], ['A2', 'B2']]);
    assert.equal(t.atoms.length, 4); // text outside the grid is not taken
});

test('a missing interior rule marks the table merged', () => {
    const h = [100, 124, 148, 172].map((y) => ({ y, x0: 72, x1: 372 }));
    const v = [{ x: 72, y0: 100, y1: 172 }, { x: 172, y0: 100, y1: 172 }, { x: 272, y0: 124, y1: 172 }, { x: 372, y0: 100, y1: 172 }];
    const [t] = layout.detectRuledTables({ h, v }, [atom('Head', 80, 116)]);
    assert.equal(t.merged, true);
});

test('a single box or a pair of rules is not a table', () => {
    assert.equal(layout.detectRuledTables(gridRules([72, 300], [100, 200]), [atom('x', 80, 150)]).length, 0);
    assert.equal(layout.detectRuledTables({ h: [{ y: 100, x0: 72, x1: 400 }, { y: 140, x0: 72, x1: 400 }], v: [] }, []).length, 0);
});

// ── borderless tables ─────────────────────────────────────────────────────

test('aligned columns without any lines are found, right-aligned numbers included', () => {
    const rows = [['Item', 'Qty', 'Price'], ['Widget', '10', '19.99'], ['Gadget', '5', '5.50'], ['Doodad', '100', '0.99']];
    const p = page(tableItems(rows, [72, 250, 420], [1, 2]));
    const found = layout.findTables({ atoms: p.atoms, width: W, height: H }, { h: [], v: [] });
    assert.equal(found.tables.length, 1);
    const t = found.tables[0];
    assert.equal(t.kind, 'borderless');
    same(t.cells.map((r) => r.map((c) => c.text)), rows);
    same(t.aligns, ['left', 'right', 'right']);
    assert.equal(t.xs.length, 4);
    assert.equal(found.flow.length, 0);
});

test('two gapped lines are layout; three aligned ones are a table', () => {
    const two = page([item('Acme', 72, 100), item('2021', 450, 100), item('Globex', 72, 120), item('2017', 450, 120)]);
    assert.equal(layout.findTables({ atoms: two.atoms, width: W, height: H }, { h: [], v: [] }).tables.length, 0);
    const three = page([
        item('Acme', 72, 100), item('2021', 450, 100), item('Globex', 72, 116), item('2017', 450, 116), item('Initech', 72, 132), item('2012', 450, 132),
    ]);
    assert.equal(layout.findTables({ atoms: three.atoms, width: W, height: H }, { h: [], v: [] }).tables.length, 1);
});

test('prose columns are reported as columns and never as a borderless table', () => {
    const p = page(columnItems(12));
    const found = layout.findTables({ atoms: p.atoms, width: W, height: H }, { h: [], v: [] });
    assert.equal(found.tables.length, 0);
    assert.equal(found.flowColumns.count, 2);
});

test('table text is taken out of the flow, the rest stays', () => {
    const rows = [['Item', 'Qty'], ['Widget', '10'], ['Gadget', '5']];
    const p = page([item('Heading above', 72, 70), ...tableItems(rows, [72, 300]), item('A closing paragraph below the table.', 72, 220)]);
    const found = layout.findTables({ atoms: p.atoms, width: W, height: H }, { h: [], v: [] });
    assert.equal(found.tables.length, 1);
    same(found.flow.map((l) => l.text), ['Heading above', 'A closing paragraph below the table.']);
});

// ── paragraphs in narrow blocks ───────────────────────────────────────────

function wrapped(words, limit, x, y0) {
    const rows = [];
    let row = '';
    for (const w of words) {
        if (row && (row + ' ' + w).length > limit) { rows.push(row); row = w; } else row = row ? row + ' ' + w : w;
    }
    if (row) rows.push(row);
    return rows.map((r, i) => item(r, x, y0 + i * 12));
}

test('a block narrower than the page still reflows as one paragraph', () => {
    const wide = wrapped(Array.from({ length: 50 }, (_, i) => 'alpha' + i), 70, 72, 100);
    const narrow = wrapped(Array.from({ length: 40 }, (_, i) => 'omega' + i), 36, 72, 100 + wide.length * 12 + 40);
    const p = page([...wide, ...narrow]);
    const blocks = layout.paragraphs(p.ordered, { base: 10, right: layout.rightMargins(p.ordered, { count: 1 }), page: 0 });
    assert.equal(blocks.length, 2);
});
