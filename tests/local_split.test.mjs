// Work package 01: Split PDF on-device.
//
// Runs the real handler with the real vendored pdf-lib and JSZip inside a vm
// context, so these tests check the produced ZIP members and their pages, not
// just the dispatch contract. Run with `node --test public/tests/local_split.test.mjs`.
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
        // Language built-ins are deliberately NOT injected: the vm context's own
        // intrinsics must be the ones pdf-lib's instanceof checks see.
        console, Blob, FormData, Response, TextEncoder, TextDecoder,
        FileReader: class { }, Image: class { }, URL: URLShim, setTimeout, clearTimeout, AbortController,
        document: {
            currentScript: null, head: { appendChild() { } },
            createElement: (tag) => (tag === 'canvas'
                ? { width: 0, height: 0, toBlob() { }, getContext: () => null }
                : { set src(_v) { }, onload: null, onerror: null }),
        },
        fetch(url, init) { fetchCalls.push({ url, init }); return Promise.resolve(new Response('{"server":true}')); },
    };
    sandbox.window = sandbox;
    sandbox.self = sandbox;
    sandbox.window.apiUrl = (p) => `https://api.test${p}`;
    if (options.matchMedia) sandbox.window.matchMedia = options.matchMedia;
    vm.createContext(sandbox);
    for (const f of ['vendor/pdf-lib.min.js', 'vendor/jszip.min.js', 'local/ff-server-gate.js', 'local/ff-local.js', 'local/ops-pdf.js']) {
        vm.runInContext(readFileSync(join(STATIC, f), 'utf8'), sandbox, { filename: f });
    }
    const asked = [];
    sandbox.ffConsent.handler = (info) => { asked.push(info); return options.consent === true; };
    return { sandbox, L: sandbox.ffLocal, fetchCalls, asked };
}

/**
 * A PDF with `n` pages; page i has width 200+i so each page is identifiable.
 * Built inside the vm so pdf-lib sees arrays from its own realm.
 */
async function makePdf(sandbox, n, { rotateSecond = false } = {}) {
    const build = vm.runInContext(`(async (n, rotateSecond) => {
        const doc = await PDFLib.PDFDocument.create();
        for (let i = 0; i < n; i++) doc.addPage([200 + i, 300]);
        if (rotateSecond && n > 1) doc.getPage(1).setRotation(PDFLib.degrees(90));
        return await doc.save();
    })`, sandbox);
    return new Blob([await build(n, rotateSecond)], { type: 'application/pdf' });
}

function form(sandbox, blob, name, fields = {}) {
    const fd = new FormData();
    fd.append('file', blob, name);
    for (const [k, v] of Object.entries(fields)) fd.append(k, String(v));
    return fd;
}

/** Run the split and unpack the ZIP into {name: [page widths]}. */
async function split(ctx, pages, fields, opts = {}) {
    const { sandbox } = ctx;
    const pdf = await makePdf(sandbox, pages, opts);
    const res = await sandbox.ffProcess('/api/pdf/split', form(sandbox, pdf, opts.name || 'My Doc.pdf', fields));
    return res;
}

async function unpack(ctx, res) {
    const body = await res.json();
    const entry = ctx.L.resolve(body.download_token);
    const zip = await ctx.sandbox.JSZip.loadAsync(await entry.blob.arrayBuffer());
    const out = {};
    for (const name of Object.keys(zip.files)) {
        const part = await ctx.sandbox.PDFLib.PDFDocument.load(await zip.files[name].async('uint8array'));
        out[name] = part.getPages().map((p) => Math.round(p.getWidth()));
    }
    return { body, members: out };
}

test('each: one PDF per page, in order, pages byte-identical in size', async () => {
    const ctx = load();
    const res = await split(ctx, 5, { mode: 'each' });
    assert.equal(res.ok, true);
    const { body, members } = await unpack(ctx, res);
    assert.deepEqual(Object.keys(members), ['page-001.pdf', 'page-002.pdf', 'page-003.pdf', 'page-004.pdf', 'page-005.pdf']);
    assert.deepEqual(Object.values(members).map((w) => Array.from(w)), [[200], [201], [202], [203], [204]]);
    assert.equal(body.file_count, 5);
    assert.equal(body.message, 'PDF split into 5 file(s)');
    assert.equal(body.filename, 'My Doc_forgefiles.org.zip');
    assert.equal(body.local, true);
    assert.equal(ctx.fetchCalls.length, 0, 'a supported split uploads nothing');
    assert.equal(ctx.asked.length, 0, 'and never asks');
});

test('each is the default mode', async () => {
    const ctx = load();
    const { members } = await unpack(ctx, await split(ctx, 2, {}));
    assert.deepEqual(Object.keys(members), ['page-001.pdf', 'page-002.pdf']);
});

test('every_n groups pages and names the last short group', async () => {
    const ctx = load();
    const { members } = await unpack(ctx, await split(ctx, 5, { mode: 'every_n', n: 2 }));
    assert.deepEqual(Object.keys(members), ['pages-001-002.pdf', 'pages-003-004.pdf', 'page-005.pdf']);
    assert.deepEqual(Array.from(members['pages-001-002.pdf']), [200, 201]);
    assert.deepEqual(Array.from(members['pages-003-004.pdf']), [202, 203]);
    assert.deepEqual(Array.from(members['page-005.pdf']), [204]);
});

test('ranges makes one part per comma segment', async () => {
    const ctx = load();
    const { members, body } = await unpack(ctx, await split(ctx, 6, { mode: 'ranges', ranges: '1-3, 5, 6' }));
    assert.deepEqual(Object.keys(members), ['pages-001-003.pdf', 'page-005.pdf', 'page-006.pdf']);
    assert.deepEqual(Array.from(members['pages-001-003.pdf']), [200, 201, 202]);
    assert.equal(body.file_count, 3);
});

test('overlapping ranges keep every requested part under distinct names', async () => {
    const ctx = load();
    const { members } = await unpack(ctx, await split(ctx, 4, { mode: 'ranges', ranges: '1-2,1-2,3' }));
    assert.deepEqual(Object.keys(members), ['pages-001-002.pdf', 'pages-001-002-2.pdf', 'page-003.pdf']);
});

test('page rotation survives the copy', async () => {
    const ctx = load();
    const pdf = await makePdf(ctx.sandbox, 3, { rotateSecond: true });
    const res = await ctx.sandbox.ffProcess('/api/pdf/split', form(ctx.sandbox, pdf, 'r.pdf', { mode: 'each' }));
    const entry = ctx.L.resolve((await res.json()).download_token);
    const zip = await ctx.sandbox.JSZip.loadAsync(await entry.blob.arrayBuffer());
    const second = await ctx.sandbox.PDFLib.PDFDocument.load(await zip.files['page-002.pdf'].async('uint8array'));
    assert.equal(second.getPage(0).getRotation().angle, 90);
});

test('member naming matches pdf_utils.py::_split_pdf_member_name', () => {
    const { L } = load();
    assert.equal(L.pdf.splitMemberName([0]), 'page-001.pdf');
    assert.equal(L.pdf.splitMemberName([11]), 'page-012.pdf');
    assert.equal(L.pdf.splitMemberName([0, 1, 2]), 'pages-001-003.pdf');
    assert.equal(L.pdf.splitMemberName([98, 99, 100]), 'pages-099-101.pdf');
    assert.equal(L.pdf.splitMemberName([998, 999]), 'pages-999-1000.pdf');
});

test('validation errors use the server messages and upload nothing', async () => {
    const cases = [
        [{ mode: 'bogus' }, 'mode must be one of: each, every_n, ranges'],
        [{ mode: 'every_n', n: 0 }, 'Split size must be at least 1 page.'],
        [{ mode: 'every_n' }, 'Split size must be at least 1 page.'],
        [{ mode: 'every_n', n: -3 }, 'Split size must be at least 1 page.'],
        [{ mode: 'ranges' }, 'Provide one or more page ranges to split.'],
        [{ mode: 'ranges', ranges: '  ' }, 'Provide one or more page ranges to split.'],
        [{ mode: 'ranges', ranges: ',' }, 'Provide one or more page ranges to split.'],
        [{ mode: 'ranges', ranges: '0' }, "Invalid page number: '0'"],
        [{ mode: 'ranges', ranges: '3-1' }, "Invalid page range segment: '3-1'"],
        [{ mode: 'ranges', ranges: 'a-b' }, "Invalid page range numbers: 'a-b'"],
        [{ mode: 'ranges', ranges: '1-9' }, 'Selected page number exceeds document page count (3).'],
        [{ mode: 'ranges', ranges: 'x' }, "Invalid page number: 'x'"],
    ];
    for (const [fields, detail] of cases) {
        const ctx = load();
        const res = await split(ctx, 3, fields);
        assert.equal(res.status, 400, JSON.stringify(fields));
        assert.equal((await res.json()).detail, detail, JSON.stringify(fields));
        assert.equal(ctx.fetchCalls.length, 0, JSON.stringify(fields));
        assert.equal(ctx.asked.length, 0, JSON.stringify(fields));
    }
});

test('more than 200 pages is refused with the server message', async () => {
    const ctx = load();
    const res = await split(ctx, 201, { mode: 'each' });
    assert.equal(res.status, 400);
    assert.equal((await res.json()).detail, 'PDF has too many pages to split at once (max 200).');
});

test('a password asks for the server with the password-protected reason', async () => {
    const ctx = load({ consent: false });
    const res = await split(ctx, 2, { mode: 'each', password: 'secret' });
    assert.equal(res.status, 499);
    assert.equal(ctx.asked.length, 1);
    assert.equal(ctx.asked[0].tool, 'Split PDF');
    assert.equal(ctx.asked[0].reason, 'this PDF is password protected');
    assert.equal(ctx.fetchCalls.length, 0, 'declining uploads nothing, including the password');
});

test('an unreadable file asks before the server, and agreeing uploads once', async () => {
    const ctx = load({ consent: true });
    const junk = new Blob(['this is not a pdf at all'], { type: 'application/pdf' });
    const res = await ctx.sandbox.ffProcess('/api/pdf/split', form(ctx.sandbox, junk, 'junk.pdf', { mode: 'each' }));
    assert.equal(res.ok, true);
    assert.equal(ctx.asked.length, 1);
    assert.equal(ctx.asked[0].reason, 'this file uses features that cannot be processed on this device');
    assert.equal(ctx.fetchCalls.length, 1);
    assert.equal(ctx.fetchCalls[0].url, 'https://api.test/api/pdf/split');
});

test('over the memory budget asks before the server and decline uploads nothing', async () => {
    const ctx = load({ consent: false });
    const big = new Blob([new Uint8Array(151 * 1024 * 1024)], { type: 'application/pdf' });
    const res = await ctx.sandbox.ffProcess('/api/pdf/split', form(ctx.sandbox, big, 'big.pdf', { mode: 'each' }));
    assert.equal(res.status, 499);
    assert.equal(ctx.asked[0].reason, 'this file exceeds the safe limit for processing on this device');
    assert.equal(ctx.fetchCalls.length, 0);
});

test('phones get the tighter 50 MiB budget', async () => {
    const ctx = load({ consent: false, matchMedia: () => ({ matches: true }) });
    const mid = new Blob([new Uint8Array(60 * 1024 * 1024)], { type: 'application/pdf' });
    const res = await ctx.sandbox.ffProcess('/api/pdf/split', form(ctx.sandbox, mid, 'mid.pdf', { mode: 'each' }));
    assert.equal(res.status, 499);
    assert.equal(ctx.asked[0].reason, 'this file exceeds the safe limit for processing on this device');
});

test('cancelling stops the split and never falls back to the server', async () => {
    const ctx = load({ consent: true });
    const pdf = await makePdf(ctx.sandbox, 10);
    const abort = new AbortController();
    const seen = [];
    const run = ctx.sandbox.ffProcess('/api/pdf/split', form(ctx.sandbox, pdf, 'a.pdf', { mode: 'each' }), {
        signal: abort.signal,
        onProgress: (done, total) => { seen.push([done, total]); if (done === 3) abort.abort(); },
    });
    await assert.rejects(run, (e) => e.name === 'AbortError');
    assert.deepEqual(seen.at(-1), [3, 10]);
    assert.equal(ctx.fetchCalls.length, 0, 'cancel is not a reason to upload');
    assert.equal(ctx.asked.length, 0, 'and not a reason to ask');
});

test('progress is reported once per part', async () => {
    const ctx = load();
    const pdf = await makePdf(ctx.sandbox, 4);
    const seen = [];
    await ctx.sandbox.ffProcess('/api/pdf/split', form(ctx.sandbox, pdf, 'a.pdf', { mode: 'every_n', n: 2 }), {
        onProgress: (done, total) => seen.push([done, total]),
    });
    assert.deepEqual(seen, [[1, 2], [2, 2]]);
});

test('a missing file is a validation error', async () => {
    const ctx = load();
    const res = await ctx.sandbox.ffProcess('/api/pdf/split', new FormData());
    assert.equal(res.status, 400);
    assert.equal((await res.json()).detail, 'No file provided.');
});

// ── Merge PDF (work package 08): progress and cancellation ────────────────

function mergeForm(sandbox, blobs) {
    const fd = new FormData();
    blobs.forEach((b, i) => fd.append('files', b, `f${i}.pdf`));
    return fd;
}

test('merge reports progress per input and returns the merged PDF', async () => {
    const ctx = load();
    const blobs = [await makePdf(ctx.sandbox, 2), await makePdf(ctx.sandbox, 3), await makePdf(ctx.sandbox, 1)];
    const seen = [];
    const res = await ctx.sandbox.ffProcess('/api/pdf/merge', mergeForm(ctx.sandbox, blobs), { onProgress: (d, t) => seen.push([d, t]) });
    assert.equal(res.ok, true);
    assert.deepEqual(seen, [[1, 3], [2, 3], [3, 3]]);
    const body = await res.json();
    const merged = await ctx.sandbox.PDFLib.PDFDocument.load(new (vm.runInContext('Uint8Array', ctx.sandbox))(await ctx.L.resolve(body.download_token).blob.arrayBuffer()));
    assert.equal(merged.getPageCount(), 6);
    assert.equal(ctx.fetchCalls.length, 0);
});

test('cancelling a merge stops it and never uploads', async () => {
    const ctx = load({ consent: true });
    const blobs = [];
    for (let i = 0; i < 5; i++) blobs.push(await makePdf(ctx.sandbox, 1));
    const abort = new AbortController();
    const run = ctx.sandbox.ffProcess('/api/pdf/merge', mergeForm(ctx.sandbox, blobs), {
        signal: abort.signal,
        onProgress: (d) => { if (d === 2) abort.abort(); },
    });
    await assert.rejects(run, (e) => e.name === 'AbortError');
    assert.equal(ctx.fetchCalls.length, 0);
    assert.equal(ctx.asked.length, 0);
});
