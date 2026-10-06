// Work package 03: PDF to Text on-device.
//
// The text-item to text logic and the handler's orchestration are tested here
// with a faked pdf.js. Real extraction is compared against the Python server's
// output in a browser; see the CHANGELOG entry.
import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const STATIC = join(dirname(fileURLToPath(import.meta.url)), '..', 'static');
const OPS = { paintImageXObject: 85, paintInlineImageXObject: 86, paintImageMaskXObject: 83, showText: 44 };

/** pages: [{items: [...], ops?: number[]}] */
function load(o = {}) {
    const fetchCalls = [];
    const state = { destroyed: 0, passwordSeen: false };
    const pages = o.pages || [{ items: [] }];
    const URLShim = function (...a) { return new URL(...a); };
    URLShim.createObjectURL = () => 'blob:mock/1';
    URLShim.revokeObjectURL = () => { };
    const sandbox = {
        console, Blob, FormData, Response, TextEncoder, TextDecoder, AbortController, setTimeout, clearTimeout,
        FileReader: class { }, Image: class { }, URL: URLShim,
        document: {
            currentScript: null, head: { appendChild() { } },
            createElement: (t) => (t === 'canvas' ? { toBlob() { } } : { set src(_v) { }, onload: null, onerror: null }),
        },
        fetch(url, init) { fetchCalls.push({ url, init }); return Promise.resolve(new Response('{"server":true}')); },
    };
    sandbox.window = sandbox;
    sandbox.self = sandbox;
    sandbox.window.apiUrl = (p) => `https://api.test${p}`;
    if (o.matchMedia) sandbox.window.matchMedia = o.matchMedia;
    vm.createContext(sandbox);
    for (const f of ['local/ff-server-gate.js', 'local/ff-local.js', 'local/ops-pdf-text.js']) {
        vm.runInContext(readFileSync(join(STATIC, f), 'utf8'), sandbox, { filename: f });
    }
    sandbox.pdfjsLib = {
        OPS,
        getDocument() {
            return {
                promise: o.openError ? Promise.reject(o.openError) : Promise.resolve({
                    numPages: pages.length,
                    async getPage(n) {
                        const pg = pages[n - 1];
                        return {
                            getTextContent: async () => ({ items: pg.items }),
                            getOperatorList: async () => ({ fnArray: pg.ops || [] }),
                            cleanup() { },
                        };
                    },
                    async destroy() { state.destroyed++; },
                }),
                async destroy() { },
            };
        },
    };
    const asked = [];
    sandbox.ffConsent.handler = (info) => { asked.push(info); return o.consent === true; };
    return { sandbox, L: sandbox.ffLocal, fetchCalls, asked, state };
}

// Helpers to build pdf.js text items.
const item = (str, y, extra = {}) => ({ str, transform: [1, 0, 0, 1, 72, y], height: 12, hasEOL: false, ...extra });
const eol = (str, y) => item(str, y, { hasEOL: true });
const marker = { type: 'beginMarkedContent', id: 'x' };

const blob = (n = 100) => new Blob([new Uint8Array(n)]);
function form(b, name, fields = {}) {
    const fd = new FormData();
    fd.append('file', b, name);
    for (const [k, v] of Object.entries(fields)) fd.append(k, String(v));
    return fd;
}
async function run(ctx, fields = {}, name = 'My Doc.pdf', b = blob()) {
    return ctx.sandbox.ffProcess('/api/pdf/extract-text', form(b, name, fields));
}
async function textOf(ctx, res) {
    const body = await res.json();
    return { body, text: await ctx.L.resolve(body.download_token).blob.text() };
}

// ── textFromItems ─────────────────────────────────────────────────────────

test('lines end at hasEOL and are right-trimmed', () => {
    const { L } = load();
    const t = L.text.textFromItems([item('Hello ', 700), eol('world  ', 700), eol('Second', 685)], false);
    assert.equal(t, 'Hello world\nSecond');
});

test('a baseline change without an EOL mark still starts a new line', () => {
    const { L } = load();
    assert.equal(L.text.textFromItems([item('one', 700), item('two', 650)], false), 'one\ntwo');
});

test('items on the same baseline are joined without inserting separators', () => {
    const { L } = load();
    assert.equal(L.text.textFromItems([item('Hel', 700), item('lo', 700.2), eol('!', 700)], false), 'Hello!');
});

test('marked-content markers are ignored', () => {
    const { L } = load();
    assert.equal(L.text.textFromItems([marker, eol('x', 700), { type: 'endMarkedContent' }], false), 'x');
});

test('plain mode drops blank lines; layout mode keeps one for a gap', () => {
    const { L } = load();
    const items = [eol('Title', 700), item('', 650, { hasEOL: true }), eol('Body', 620)];
    assert.equal(L.text.textFromItems(items, false), 'Title\nBody');
    assert.equal(L.text.textFromItems(items, true), 'Title\n\nBody');
    const many = [eol('A', 700), item('', 660, { hasEOL: true }), item('', 640, { hasEOL: true }), eol('B', 600)];
    assert.equal(L.text.textFromItems(many, true), 'A\n\nB', 'gaps collapse to a single blank line');
});

test('layout mode inserts a paragraph gap from a large baseline jump', () => {
    const { L } = load();
    assert.equal(L.text.textFromItems([eol('One', 700), eol('Two', 686), eol('Three', 600)], true), 'One\nTwo\n\nThree');
    assert.equal(L.text.textFromItems([eol('One', 700), eol('Two', 686), eol('Three', 600)], false), 'One\nTwo\nThree');
});

test('empty input gives empty text', () => {
    const { L } = load();
    assert.equal(L.text.textFromItems([], false), '');
    assert.equal(L.text.textFromItems([item('   ', 700)], false), '');
});

test('Indic and accented text passes through untouched', () => {
    const { L } = load();
    const s = 'नमस्ते दुनिया café ﬁ';
    assert.equal(L.text.textFromItems([eol(s, 700)], false), s);
});

// ── handler ───────────────────────────────────────────────────────────────

test('server output format: page headers, blank-line separators, empty pages omitted', async () => {
    const ctx = load({ pages: [{ items: [eol('First', 700)] }, { items: [] }, { items: [eol('Third', 700)] }] });
    const { body, text } = await textOf(ctx, await run(ctx));
    assert.equal(text, '--- Page 1 ---\nFirst\n\n--- Page 3 ---\nThird');
    assert.equal(body.message, 'Text extracted from 3 page(s)');
    assert.equal(body.page_count, 3);
    assert.equal(body.filename, 'My Doc_forgefiles.org.txt');
    assert.equal(ctx.fetchCalls.length, 0);
    assert.equal(ctx.asked.length, 0);
    assert.equal(ctx.state.destroyed, 1);
});

test('a document with no text and no images says so, like the server', async () => {
    const ctx = load({ pages: [{ items: [] }, { items: [] }] });
    const { text } = await textOf(ctx, await run(ctx));
    assert.equal(text, '(No text found in document)');
});

test('a scanned page asks for the server (OCR) and never returns empty text', async () => {
    const ctx = load({ pages: [{ items: [eol('Text page', 700)] }, { items: [], ops: [OPS.paintImageXObject] }], consent: false });
    const res = await run(ctx);
    assert.equal(res.status, 499);
    assert.equal(ctx.asked[0].tool, 'PDF to Text');
    assert.equal(ctx.asked[0].reason, 'some pages are scans that need text recognition (OCR)');
    assert.equal(ctx.fetchCalls.length, 0);
    assert.equal(ctx.state.destroyed, 1);
});

test('a text page that also has images is not a scan', async () => {
    const ctx = load({ pages: [{ items: [eol('Caption', 700)], ops: [OPS.paintImageXObject] }] });
    const res = await run(ctx);
    assert.equal(res.ok, true);
    assert.equal(ctx.asked.length, 0);
});

test('a blank page without images is just skipped', async () => {
    const ctx = load({ pages: [{ items: [], ops: [OPS.showText] }, { items: [eol('x', 700)] }] });
    const { text } = await textOf(ctx, await run(ctx));
    assert.equal(text, '--- Page 2 ---\nx');
});

test('preserve_layout is read from the form', async () => {
    const items = [eol('A', 700), item('', 650, { hasEOL: true }), eol('B', 620)];
    const plain = load({ pages: [{ items }] });
    assert.equal((await textOf(plain, await run(plain, { preserve_layout: false }))).text, '--- Page 1 ---\nA\nB');
    const layout = load({ pages: [{ items }] });
    assert.equal((await textOf(layout, await run(layout, { preserve_layout: true }))).text, '--- Page 1 ---\nA\n\nB');
});

test('a password asks with the password reason and is never sent anywhere', async () => {
    const ctx = load({ consent: false });
    const res = await run(ctx, { password: 'secret' });
    assert.equal(res.status, 499);
    assert.equal(ctx.asked[0].reason, 'this PDF is password protected');
    assert.equal(ctx.fetchCalls.length, 0);
});

test('an encrypted PDF asks with the password reason', async () => {
    const err = new Error('No password given'); err.name = 'PasswordException';
    const ctx = load({ consent: false, openError: err });
    assert.equal((await run(ctx)).status, 499);
    assert.equal(ctx.asked[0].reason, 'this PDF is password protected');
});

test('an unreadable PDF asks, and agreeing uploads exactly once', async () => {
    const ctx = load({ consent: true, openError: new Error('Invalid PDF structure') });
    const res = await run(ctx);
    assert.equal(res.ok, true);
    assert.equal(ctx.fetchCalls.length, 1);
    assert.equal(ctx.fetchCalls[0].url, 'https://api.test/api/pdf/extract-text');
});

test('budgets: 100 MiB and 1000 pages on desktop, 25 MiB and 300 pages on phones', async () => {
    const big = load({ consent: false });
    assert.equal((await run(big, {}, 'b.pdf', new Blob([new Uint8Array(101 * 1024 * 1024)]))).status, 499);
    const phone = () => ({ matches: true });
    const mid = load({ consent: false, matchMedia: phone });
    assert.equal((await run(mid, {}, 'm.pdf', new Blob([new Uint8Array(30 * 1024 * 1024)]))).status, 499);
    const long = load({ consent: false, matchMedia: phone, pages: Array.from({ length: 301 }, () => ({ items: [] })) });
    assert.equal((await run(long)).status, 499);
    assert.equal(long.asked[0].reason, 'this file exceeds the safe limit for processing on this device');
    const fine = load({ matchMedia: phone, pages: Array.from({ length: 300 }, () => ({ items: [eol('x', 700)] })) });
    assert.equal((await run(fine)).ok, true);
});

test('cancelling stops extraction and never uploads', async () => {
    const ctx = load({ pages: Array.from({ length: 6 }, () => ({ items: [eol('x', 700)] })), consent: true });
    const abort = new AbortController();
    const seen = [];
    const p = ctx.sandbox.ffProcess('/api/pdf/extract-text', form(blob(), 'a.pdf'), {
        signal: abort.signal,
        onProgress: (d, t) => { seen.push([d, t]); if (d === 2) abort.abort(); },
    });
    await assert.rejects(p, (e) => e.name === 'AbortError');
    assert.deepEqual(seen.at(-1), [2, 6]);
    assert.equal(ctx.fetchCalls.length, 0);
    assert.equal(ctx.asked.length, 0);
    assert.equal(ctx.state.destroyed, 1);
});

test('progress is reported per page', async () => {
    const ctx = load({ pages: [{ items: [eol('a', 700)] }, { items: [eol('b', 700)] }] });
    const seen = [];
    await ctx.sandbox.ffProcess('/api/pdf/extract-text', form(blob(), 'a.pdf'), { onProgress: (d, t) => seen.push([d, t]) });
    assert.deepEqual(seen, [[1, 2], [2, 2]]);
});

test('a missing file is a validation error', async () => {
    const ctx = load();
    const res = await ctx.sandbox.ffProcess('/api/pdf/extract-text', new FormData());
    assert.equal(res.status, 400);
    assert.equal((await res.json()).detail, 'No file provided.');
});
