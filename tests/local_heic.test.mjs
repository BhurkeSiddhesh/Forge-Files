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
        fetch: async () => new Response('{}'),
        createImageBitmap: async () => ({ width: 1200, height: 800, close() {} }),
        document: {
            createElement(tag) { return tag === 'canvas' ? { toBlob() {}, getContext() { return {}; } } : {}; },
            head: { appendChild() {} },
        },
        localStorage: { getItem() { return null; } },
    };
    const engine = async ({ quality }) => new Blob([new Uint8Array(64)], { type: 'image/jpeg' });
    engine.isHeic = async () => true;
    sandbox.HeicTo = engine;
    sandbox.window = sandbox;
    sandbox.self = sandbox;
    sandbox.apiUrl = p => p;
    vm.createContext(sandbox);
    for (const file of ['local/ff-server-gate.js', 'local/ff-local.js', 'local/ops-heic.js']) {
        vm.runInContext(readFileSync(join(STATIC, file), 'utf8'), sandbox, { filename: file });
    }
    sandbox.ffConsent.handler = () => false;
    return sandbox;
}

test('WP23 supported HEIC converts locally with dimensions and server contract', async () => {
    const s = context();
    const fd = new FormData();
    fd.append('file', new File([new Uint8Array(128)], 'phone.heic', { type: 'image/heic' }));
    fd.append('quality', '90');
    const res = await s.ffProcess('/api/image/heic-to-jpeg', fd);
    assert.equal(res.status, 200);
    const body = await res.json();
    assert.equal(body.local, true);
    assert.equal(body.message, 'Converted to JPEG');
    assert.equal(body.filename, 'phone_forgefiles.org.jpg');
    assert.equal(body.width, 1200);
    assert.equal(body.height, 800);
});

test('WP23 invalid quality is rejected without upload', async () => {
    const s = context();
    const fd = new FormData();
    fd.append('file', new File([new Uint8Array(128)], 'phone.heic', { type: 'image/heic' }));
    fd.append('quality', '101');
    const res = await s.ffProcess('/api/image/heic-to-jpeg', fd);
    assert.equal(res.status, 400);
});
