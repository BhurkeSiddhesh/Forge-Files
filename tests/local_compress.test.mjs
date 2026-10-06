import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const STATIC = join(dirname(fileURLToPath(import.meta.url)), '..', 'static');

function context() {
    const sandbox = {
        console, Blob, File, FormData, Response, Uint8Array, ArrayBuffer,
        setTimeout, clearTimeout, TextEncoder, TextDecoder, AbortController,
        URL: Object.assign(URL, { createObjectURL: () => 'blob:test', revokeObjectURL() {} }),
        fetch: async (url) => url.includes('qpdf.wasm') ? new Response(new Uint8Array([0])) : new Response('{}'),
        document: {
            createElement(tag) {
                if (tag === 'canvas') return { toBlob() {}, getContext() { return {}; } };
                return { set src(v) { this._src = v; }, onload: null, onerror: null };
            },
            head: { appendChild(el) { setTimeout(() => el.onload && el.onload(), 0); } },
        },
        localStorage: { getItem() { return null; } },
    };
    sandbox.window = sandbox;
    sandbox.self = sandbox;
    sandbox.apiUrl = p => p;
    sandbox.Module = async () => ({
        FS: {
            writeFile(_p, bytes) { sandbox.input = bytes; },
            readFile() { return sandbox.input.slice(0, Math.max(5, sandbox.input.length - 4)); },
        },
        callMain(args) { sandbox.args = args; },
    });
    vm.createContext(sandbox);
    for (const file of ['local/ff-server-gate.js', 'local/ff-local.js', 'local/ops-pdf-compress.js']) {
        vm.runInContext(readFileSync(join(STATIC, file), 'utf8'), sandbox, { filename: file });
    }
    sandbox.ffConsent.handler = () => false;
    return sandbox;
}

test('WP14 structural compression stays local and reports measured sizes', async () => {
    const s = context();
    const fd = new FormData();
    fd.append('file', new File([new Uint8Array(100)], 'report.pdf', { type: 'application/pdf' }));
    fd.append('mode', 'structural');
    const res = await s.ffProcess('/api/pdf/compress', fd);
    assert.equal(res.status, 200);
    const body = await res.json();
    assert.equal(body.local, true);
    assert.equal(body.original_size, 100);
    assert.equal(body.compressed_size, 96);
    assert.equal(body.compression_mode, 'structural');
    assert.equal(Array.from(s.args.slice(1, -1)).join('|'), '--object-streams=generate|--compress-streams=y|--recompress-flate|--compression-level=9');
});

test('WP20 UI makes rasterisation an explicit warning-bearing choice', () => {
    const html = readFileSync(join(STATIC, 'index.html'), 'utf8');
    assert.match(html, /name="compress-mode" value="lossy"/);
    assert.match(html, /Text selection, search, links and accessibility structure will be lost/);
});

test('images mode is offered in the UI and keeps text sharp', () => {
    const html = readFileSync(join(STATIC, 'index.html'), 'utf8');
    assert.match(html, /name="compress-mode" value="images"/);
    const src = readFileSync(join(STATIC, 'local/ops-pdf-compress.js'), 'utf8');
    assert.match(src, /mode === 'images'/);
    assert.match(src, /bytes\.length >= raw\.length \* 0\.95/); // non-inflation guard
});

test('images mode falls back to the original bytes when nothing can be saved', async () => {
    const s = context();
    s.createImageBitmap = async () => { throw new Error('no decode'); };
    s.ffLocal.loadPdfLib = async () => ({
        PDFName: { of: n => n }, PDFRawStream: class {}, PDFNumber: { of: n => n },
        PDFDocument: { load: async () => ({ context: { enumerateIndirectObjects: () => [] } }) },
    });
    const fd = new FormData();
    fd.append('file', new File([new Uint8Array(100)], 'photos.pdf', { type: 'application/pdf' }));
    fd.append('mode', 'images');
    const res = await s.ffProcess('/api/pdf/compress', fd);
    const body = await res.json();
    assert.equal(body.compression_mode, 'images');
    assert.ok(body.compressed_size <= 100);
});
