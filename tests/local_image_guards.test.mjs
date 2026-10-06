// Resource and correctness guards for the on-device image paths (compress `images`
// mode and embedded-image extraction), using a mocked pdf-lib object graph.
import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const STATIC = join(dirname(fileURLToPath(import.meta.url)), '..', 'static');

function context(files) {
    const sandbox = {
        console, Blob, File, FormData, Response, Uint8Array, ArrayBuffer, setTimeout, clearTimeout,
        TextEncoder, TextDecoder, AbortController,
        URL: Object.assign(URL, { createObjectURL: () => 'blob:test', revokeObjectURL() {} }),
        fetch: async () => new Response('{}'),
        document: {
            createElement: () => ({ getContext: () => ({}), toBlob() {} }),
            head: { appendChild(el) { setTimeout(() => el.onload && el.onload(), 0); } },
        },
        localStorage: { getItem() { return null; } },
    };
    sandbox.window = sandbox;
    sandbox.self = sandbox;
    sandbox.apiUrl = (p) => p;
    sandbox.fetch = async (url) => (String(url).includes('qpdf.wasm') ? new Response(new Uint8Array([0])) : new Response('{}'));
    sandbox.Module = async () => ({
        FS: { writeFile(_p, b) { sandbox.input = b; }, readFile() { return sandbox.input; } },
        callMain() {},
    });
    vm.createContext(sandbox);
    for (const f of ['local/ff-server-gate.js', 'local/ff-local.js', ...files]) {
        vm.runInContext(readFileSync(join(STATIC, f), 'utf8'), sandbox, { filename: f });
    }
    sandbox.ffConsent.handler = () => false;
    return sandbox;
}

/** A syntactically plausible JPEG: SOI, optional EXIF orientation, SOF0 with the given size, padding. */
function jpeg(w, h, orientation) {
    const bytes = [0xff, 0xd8];
    if (orientation) {
        // APP1 "Exif\0\0", little-endian TIFF, one IFD0 entry: tag 0x0112 (Orientation)
        const tiff = [0x49, 0x49, 0x2a, 0, 8, 0, 0, 0, 1, 0, 0x12, 0x01, 3, 0, 1, 0, 0, 0, orientation, 0, 0, 0, 0, 0, 0, 0];
        const body = [0x45, 0x78, 0x69, 0x66, 0, 0, ...tiff];
        bytes.push(0xff, 0xe1, (body.length + 2) >> 8, (body.length + 2) & 255, ...body);
    }
    bytes.push(0xff, 0xc0, 0, 17, 8, h >> 8, h & 255, w >> 8, w & 255, 3, 1, 0x22, 0, 2, 0x11, 1, 3, 0x11, 1);
    while (bytes.length < 25 * 1024) bytes.push(0);
    return new Uint8Array(bytes);
}

function fakePdfLib(images) {
    class RawStream {
        constructor(dict, bytes) { this.dict = dict; this.bytes = bytes; }
        getContents() { return this.bytes; }
    }
    const dictOf = (o) => ({ get: (k) => o[k], clone() { return dictOf({ ...o }); }, set() {}, delete() {} });
    const num = (n) => ({ asNumber: () => n });
    const entries = images.map((bytes, i) => [{ objectNumber: i + 1 }, new RawStream(dictOf({
        Subtype: 'Image', Filter: 'DCTDecode', ColorSpace: 'DeviceRGB', BitsPerComponent: num(8),
        Width: num(100), Height: num(100),
    }), bytes)]);
    const doc = {
        context: { enumerateIndirectObjects: () => entries, lookup: (ref) => entries[ref.objectNumber - 1][1], assign() {} },
        getPages: () => [{ node: { Resources: () => ({ lookupMaybe: () => ({ entries: () => entries.map((e) => [null, e[0]]) }) }) } }],
        save: async () => new Uint8Array([1]),
    };
    return {
        PDFName: { of: (n) => n }, PDFNumber: { of: (n) => n }, PDFDict: class {}, PDFRawStream: RawStream,
        PDFDocument: { load: async () => doc },
    };
}

async function compress(images) {
    const s = context(['local/ops-pdf-compress.js']);
    let decoded = 0;
    s.createImageBitmap = async () => { decoded++; throw new Error('decode refused in test'); };
    s.ffLocal.loadPdfLib = async () => fakePdfLib(images);
    const fd = new FormData();
    fd.append('file', new File([new Uint8Array(100)], 'p.pdf', { type: 'application/pdf' }));
    fd.append('mode', 'images');
    const res = await s.ffProcess('/api/pdf/compress', fd);
    return { status: res.status, decoded };
}

test('a JPEG whose own header declares a huge raster is never decoded', async () => {
    const r = await compress([jpeg(60000, 60000)]);
    assert.equal(r.decoded, 0);
    assert.equal(r.status, 499, 'the server is offered instead (consent declined here)');
});

test('a JPEG with an EXIF rotation is never decoded (the PDF matrix would not match)', async () => {
    const r = await compress([jpeg(800, 600, 6)]);
    assert.equal(r.decoded, 0);
    assert.equal(r.status, 499);
});

test('a plain JPEG within the limits reaches the decoder; a decoder failure offers the server', async () => {
    const r = await compress([jpeg(800, 600)]);
    assert.equal(r.decoded, 1);
    assert.equal(r.status, 499, 'decode refused -> nothing recompressed -> server offered, not a fake success');
});

test('cancelling embedded-image extraction is not turned into a download', async () => {
    const s = context(['local/ops-pdf-render.js']);
    s.ffLocal.loadPdfLib = async () => fakePdfLib([jpeg(800, 600)]);
    s.ffLocal.loadJsZip = async () => class { file() {} async generateAsync() { return new Blob([new Uint8Array([1])]); } };
    const abort = new AbortController();
    abort.abort();
    const fd = new FormData();
    fd.append('file', new File([new Uint8Array(100)], 'p.pdf', { type: 'application/pdf' }));
    fd.append('mode', 'embedded');
    await assert.rejects(s.ffProcess('/api/pdf/to-images', fd, { signal: abort.signal }), (e) => e.name === 'AbortError');
});
