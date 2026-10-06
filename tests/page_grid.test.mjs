import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const STATIC = join(dirname(fileURLToPath(import.meta.url)), '..', 'static');

function load() {
    const sandbox = { console, document: {}, Event: class {} };
    sandbox.window = sandbox;
    vm.createContext(sandbox);
    vm.runInContext(readFileSync(join(STATIC, 'ff-page-grid.js'), 'utf8'), sandbox, { filename: 'ff-page-grid.js' });
    return sandbox.ffPageGrid;
}

test('compressRanges collapses consecutive pages', () => {
    const g = load();
    assert.equal(g.compressRanges([1, 2, 3, 5, 7, 8]), '1-3,5,7-8');
    assert.equal(g.compressRanges([4]), '4');
    assert.equal(g.compressRanges([6, 2, 3, 1]), '1-3,6');
    assert.equal(g.compressRanges([]), '');
});

test('parseRanges accepts page lists and rejects anything else', () => {
    const g = load();
    assert.deepEqual(Array.from(g.parseRanges('1,3-5', 9)), [1, 3, 4, 5]);
    assert.deepEqual(Array.from(g.parseRanges('2, 2', 9)), [2]);
    assert.deepEqual(Array.from(g.parseRanges('', 9)), []);
    assert.equal(g.parseRanges('0', 9), null);
    assert.equal(g.parseRanges('5-3', 9), null);
    assert.equal(g.parseRanges('10', 9), null);
    assert.equal(g.parseRanges('1,x', 9), null);
});

test('parseOrder keeps order and duplicates', () => {
    const g = load();
    assert.deepEqual(Array.from(g.parseOrder('3,1,2,1', 3)), [3, 1, 2, 1]);
    assert.equal(g.parseOrder('4', 3), null);
    assert.equal(g.parseOrder('1-2', 3), null);
});

test('only the page-based tools get a grid', () => {
    const g = load();
    for (const id of ['extract-pages-area', 'rotate-pdf-area', 'organize-pdf-area', 'remove-pages-area']) assert.ok(g.supports(id), id);
    assert.ok(!g.supports('watermark-area'));
});

test('index.html loads the grid and defines every panel it targets', () => {
    const html = readFileSync(join(STATIC, 'index.html'), 'utf8');
    assert.match(html, /ff-page-grid\.js/);
    for (const id of ['extract-pages-input', 'rotate-pdf-pages', 'organize-page-order', 'remove-pages-input']) {
        assert.ok(html.includes('id="' + id + '"'), id);
    }
});

test('resetUI closes the Remove Pages and Crop panels and unmounts the grids', () => {
    const js = readFileSync(join(STATIC, 'script.js'), 'utf8');
    const reset = js.slice(js.indexOf('function resetUI()'));
    const body = reset.slice(0, reset.search(/\r?\n\}/));
    for (const id of ['remove-pages-area', 'crop-pdf-area']) assert.ok(body.includes(id), id);
    assert.ok(body.includes('ffPageGrid'));
});
