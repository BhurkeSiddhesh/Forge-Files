// Phase 5: server-only Office tools must never upload without consent.
//
// Word/Excel/PowerPoint to PDF, PowerPoint to Images, PDF to Word, Word to
// PowerPoint and Merge PowerPoint have no on-device handler. Declining the
// consent dialog must send no request; agreeing must send exactly one.
// Run with `node --test public/tests/local_office_consent.test.mjs`.

import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const STATIC = join(dirname(fileURLToPath(import.meta.url)), '..', 'static');

const OFFICE = {
    '/api/word/to-pdf': 'Word to PDF',
    '/api/word/to-pptx': 'Word to PowerPoint',
    '/api/excel/to-pdf': 'Excel to PDF',
    '/api/ppt/to-pdf': 'PowerPoint to PDF',
    '/api/ppt/to-images': 'PowerPoint to Images',
    '/api/ppt/merge': 'Merge PowerPoint',
    '/api/pdf/convert-to-word': 'PDF to Word',
    '/api/pdf/convert-to-word-stream': 'PDF to Word',
};

function load(consent) {
    const fetchCalls = [];
    const asked = [];
    const sandbox = {
        console, Blob, File, FormData, Response, TextEncoder, TextDecoder, Uint8Array,
        URL, setTimeout, clearTimeout, AbortController,
        document: { currentScript: null, head: { appendChild() { } } },
        fetch(url, init) {
            fetchCalls.push({ url, init });
            return Promise.resolve(new Response('{"status":"success"}'));
        },
    };
    sandbox.window = sandbox;
    sandbox.self = sandbox;
    sandbox.window.apiUrl = (p) => `https://api.test${p}`;
    vm.createContext(sandbox);
    for (const f of ['local/ff-server-gate.js', 'local/ff-local.js']) {
        vm.runInContext(readFileSync(join(STATIC, f), 'utf8'), sandbox, { filename: f });
    }
    sandbox.ffConsent.handler = (info) => { asked.push(info); return consent; };
    return { sandbox, fetchCalls, asked };
}

function form() {
    const fd = new FormData();
    fd.append('file', new Blob(['x'], { type: 'application/octet-stream' }), 'doc.bin');
    return fd;
}

for (const [path, tool] of Object.entries(OFFICE)) {
    test(`${tool} (${path}): decline sends no request`, async () => {
        const ctx = load(false);
        const res = await ctx.sandbox.ffProcess(path, form());
        assert.equal(ctx.fetchCalls.length, 0, 'declined consent must not upload');
        assert.equal(ctx.asked.length, 1);
        assert.equal(ctx.asked[0].tool, tool);
        assert.ok(res.status >= 400);
        assert.equal((await res.json()).declined, true);
    });

    test(`${tool} (${path}): consent sends exactly one request`, async () => {
        const ctx = load(true);
        const res = await ctx.sandbox.ffProcess(path, form());
        assert.equal(ctx.asked.length, 1);
        assert.equal(ctx.fetchCalls.length, 1);
        assert.equal(ctx.fetchCalls[0].url, `https://api.test${path}`);
        assert.equal(res.status, 200);
    });
}

test('no consent handler and no DOM fails closed', async () => {
    const ctx = load(true);
    ctx.sandbox.ffConsent.handler = null;
    const res = await ctx.sandbox.ffProcess('/api/word/to-pdf', form());
    assert.equal(ctx.fetchCalls.length, 0);
    assert.ok(res.status >= 400);
});
