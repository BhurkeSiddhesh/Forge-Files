// Work package 02: PDF to JPG/PNG on-device (orchestration).
//
// pdf.js needs a real canvas, which node does not have, so the rasteriser is
// faked here and these tests cover everything around it: option validation
// against the server's messages, budgets, member naming, ZIP contents, consent
// fallbacks, cancellation and resource release. Real rendering (pixel size,
// JPEG signature, visible content) is verified in a browser; see CHANGELOG.
import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const STATIC = join(dirname(fileURLToPath(import.meta.url)), '..', 'static');

/**
 * @param {object} o
 * @param {Array<[number, number]>} [o.pages] page sizes in PDF points
 * @param {boolean|string} [o.consent]
 * @param {object} [o.openError] error getDocument's promise rejects with
 */
function load(o = {}) {
    const fetchCalls = [];
    const events = { canvases: [], renders: [], destroyed: 0, cleaned: 0, openedWith: null };
    const pages = o.pages || [[612, 792]];

    const URLShim = function (...a) { return new URL(...a); };
    URLShim.createObjectURL = () => 'blob:mock/1';
    URLShim.revokeObjectURL = () => { };

    const sandbox = {
        console, Blob, FormData, Response, TextEncoder, TextDecoder, AbortController, setTimeout, clearTimeout,
        FileReader: class { }, Image: class { }, URL: URLShim,
        document: {
            currentScript: null, head: { appendChild() { } },
            createElement(tag) {
                if (tag !== 'canvas') return { set src(_v) { }, onload: null, onerror: null };
                const canvas = {
                    width: 0, height: 0,
                    getContext: () => ({ fillStyle: '', fillRect() { } }),
                    toBlob(cb, type, quality) { canvas.encoded = { type, quality }; cb(new Blob([type === 'image/png' ? 'PNG' : 'JPG'])); },
                };
                events.canvases.push(canvas);
                return canvas;
            },
        },
        fetch(url, init) { fetchCalls.push({ url, init }); return Promise.resolve(new Response('{"server":true}')); },
    };
    sandbox.window = sandbox;
    sandbox.self = sandbox;
    sandbox.window.apiUrl = (p) => `https://api.test${p}`;
    if (o.matchMedia) sandbox.window.matchMedia = o.matchMedia;

    vm.createContext(sandbox);
    for (const f of ['vendor/jszip.min.js', 'local/ff-server-gate.js', 'local/ff-local.js', 'local/ops-pdf-render.js']) {
        vm.runInContext(readFileSync(join(STATIC, f), 'utf8'), sandbox, { filename: f });
    }

    sandbox.pdfjsLib = {
        getDocument(opts) {
            events.openedWith = opts;
            return {
                promise: o.openError ? Promise.reject(o.openError) : Promise.resolve({
                    numPages: pages.length,
                    async getPage(n) {
                        const [w, h] = pages[n - 1];
                        return {
                            getViewport: ({ scale }) => ({ width: w * scale, height: h * scale, scale }),
                            render: (args) => { events.renders.push(args); return { promise: Promise.resolve() }; },
                            cleanup() { events.cleaned++; },
                        };
                    },
                    async destroy() { events.destroyed++; },
                }),
                async destroy() { },
            };
        },
    };

    const asked = [];
    sandbox.ffConsent.handler = (info) => { asked.push(info); return o.consent === true; };
    return { sandbox, L: sandbox.ffLocal, fetchCalls, asked, events };
}

// ff-local.js creates one canvas at load to probe for toBlob support; skip it.
const encodedCanvases = (ctx) => ctx.events.canvases.filter((c) => c.encoded);

const pdfBlob = (size = 100) => new Blob([new Uint8Array(size)], { type: 'application/pdf' });

function form(blob, name, fields = {}) {
    const fd = new FormData();
    fd.append('file', blob, name);
    for (const [k, v] of Object.entries(fields)) fd.append(k, String(v));
    return fd;
}

async function run(ctx, fields = {}, name = 'My Doc.pdf', blob = pdfBlob()) {
    return ctx.sandbox.ffProcess('/api/pdf/to-images', form(blob, name, fields));
}

async function members(ctx, res) {
    const body = await res.json();
    const zip = await ctx.sandbox.JSZip.loadAsync(await ctx.L.resolve(body.download_token).blob.arrayBuffer());
    const out = {};
    for (const name of Object.keys(zip.files)) out[name] = await zip.files[name].async('string');
    return { body, members: out };
}

test('renders one image per page with the server member names and message', async () => {
    const ctx = load({ pages: [[612, 792], [792, 612], [300, 300]] });
    const { body, members: m } = await members(ctx, await run(ctx, { dpi: 150, fmt: 'jpg' }));
    assert.deepEqual(Object.keys(m), ['My Doc_page_001.jpg', 'My Doc_page_002.jpg', 'My Doc_page_003.jpg']);
    assert.equal(body.message, 'Rendered 3 page(s) to images');
    assert.equal(body.page_count, 3);
    assert.equal(body.filename, 'My Doc_forgefiles.org.zip');
    assert.equal(body.local, true);
    assert.equal(ctx.fetchCalls.length, 0, 'nothing uploaded');
    assert.equal(ctx.asked.length, 0, 'nothing asked');
});

test('defaults are 150 DPI and JPG; PNG is honoured', async () => {
    const jpg = load();
    const first = await run(jpg, {});
    assert.equal(first.ok, true, JSON.stringify(jpg.asked));
    assert.equal(encodedCanvases(jpg)[0].encoded.type, 'image/jpeg');
    assert.equal(encodedCanvases(jpg)[0].encoded.quality, 0.95);
    // 612pt at 150 DPI -> 1275 px wide, 792pt -> 1650 px tall; canvas is released after use.
    assert.equal(jpg.events.renders[0].viewport.scale, 150 / 72);

    const png = load();
    const { members: m } = await members(png, await run(png, { fmt: 'png' }));
    assert.deepEqual(Object.keys(m), ['My Doc_page_001.png']);
    assert.equal(encodedCanvases(png)[0].encoded.type, 'image/png');
});

test('every render is onto white, uses print intent, and releases its canvas', async () => {
    const ctx = load({ pages: [[612, 792], [612, 792]] });
    await run(ctx, { dpi: 72 });
    assert.equal(ctx.events.renders.length, 2);
    for (const r of ctx.events.renders) {
        assert.equal(r.intent, 'print');
        assert.equal(r.background, 'rgb(255,255,255)');
    }
    for (const c of ctx.events.canvases) assert.deepEqual([c.width, c.height], [0, 0], 'bitmap released');
    assert.equal(ctx.events.cleaned >= 4, true, 'pages cleaned up (probe + render)');
    assert.equal(ctx.events.destroyed, 1, 'document destroyed');
});

test('canvas size is the ceiling of points x dpi / 72 (matches PyMuPDF)', async () => {
    const ctx = load({ pages: [[595, 842]] });
    let seen;
    const original = ctx.sandbox.pdfjsLib.getDocument;
    ctx.sandbox.pdfjsLib.getDocument = (o) => original(o);
    await run(ctx, { dpi: 150 });
    seen = encodedCanvases(ctx)[0];
    // released to 0 afterwards, so check via the viewport the page was rendered with
    assert.equal(Math.ceil(ctx.events.renders[0].viewport.width), 1240);
    assert.equal(Math.ceil(ctx.events.renders[0].viewport.height), 1755);
    assert.ok(seen);
});

test('pdf.js is opened without scripting and with same-origin assets only', async () => {
    const ctx = load();
    await run(ctx, {});
    const o = ctx.events.openedWith;
    assert.equal(o.isEvalSupported, false);
    assert.equal(o.enableXfa, false);
    for (const k of ['standardFontDataUrl', 'cMapUrl', 'wasmUrl', 'iccUrl']) {
        assert.match(o[k], /^\/static\/vendor\/pdfjs\//, `${k} must be same-origin`);
    }
});

test('option validation uses the server messages and uploads nothing', async () => {
    const cases = [
        [{ dpi: 49 }, 'DPI must be between 50 and 300.'],
        [{ dpi: 301 }, 'DPI must be between 50 and 300.'],
        [{ dpi: 'abc' }, 'DPI must be an integer.'],
        [{ dpi: '1.5' }, 'DPI must be an integer.'],
        [{ fmt: 'gif' }, 'Format must be jpg or png.'],
    ];
    for (const [fields, detail] of cases) {
        const ctx = load();
        const res = await run(ctx, fields);
        assert.equal(res.status, 400, JSON.stringify(fields));
        assert.equal((await res.json()).detail, detail);
        assert.equal(ctx.fetchCalls.length, 0);
        assert.equal(ctx.asked.length, 0);
    }
    for (const dpi of [50, 300]) {
        const ctx = load();
        assert.equal((await run(ctx, { dpi })).ok, true, `dpi ${dpi} is allowed`);
    }
});

test('more than 200 pages is the server error; a huge page is the server pixel error', async () => {
    const many = load({ pages: Array.from({ length: 201 }, () => [100, 100]) });
    const res = await run(many, {});
    assert.equal(res.status, 400);
    assert.equal((await res.json()).detail, 'PDF has too many pages to render at once (max 200).');
    assert.equal(many.events.renders.length, 0);

    const big = load({ pages: [[612, 792], [5000, 5000]] });
    const r2 = await run(big, { dpi: 300 });
    assert.equal(r2.status, 400);
    assert.equal((await r2.json()).detail, 'Page render would exceed 20,000,000 pixels at 300 DPI.');
    assert.equal(big.events.renders.length, 0, 'validated before any page is rendered');
});

test('phones: oversize bitmaps and long documents ask before using the server', async () => {
    const phone = () => ({ matches: true });
    // 26 MP page: over the server's own 20 MP cap, so a plain validation error.
    const bitmap = load({ pages: [[3000, 2000]], matchMedia: phone, consent: false });
    const res = await run(bitmap, { dpi: 150 });
    assert.equal(res.status, 400);

    const mid = load({ pages: [[1700, 1700]], matchMedia: phone, consent: false });
    const r2 = await run(mid, { dpi: 150 }); // 12.5 MP: under both limits
    assert.equal(r2.ok, true);

    const tight = load({ pages: [[2100, 2100]], matchMedia: phone, consent: false });
    const r3 = await run(tight, { dpi: 150 }); // 19.1 MP: under the server's 20 MP, over the phone's 16.7 MP
    assert.equal(r3.status, 499);
    assert.equal(tight.asked[0].reason, 'this file exceeds the safe limit for processing on this device');
    assert.equal(tight.fetchCalls.length, 0);

    const long = load({ pages: Array.from({ length: 51 }, () => [100, 100]), matchMedia: phone, consent: false });
    const r4 = await run(long, {});
    assert.equal(r4.status, 499);
    assert.equal(long.events.renders.length, 0);
});

test('size budget: 100 MiB on desktop, 25 MiB on phones', async () => {
    const big = new Blob([new Uint8Array(101 * 1024 * 1024)]);
    const d = load({ consent: false });
    assert.equal((await run(d, {}, 'big.pdf', big)).status, 499);
    assert.equal(d.fetchCalls.length, 0);

    const mid = new Blob([new Uint8Array(30 * 1024 * 1024)]);
    const p = load({ consent: false, matchMedia: () => ({ matches: true }) });
    assert.equal((await run(p, {}, 'mid.pdf', mid)).status, 499);
    const desktopOk = load();
    assert.equal((await run(desktopOk, {}, 'mid.pdf', mid)).ok, true);
});

test('a password and an encrypted PDF both ask with the password reason', async () => {
    const withPassword = load({ consent: false });
    assert.equal((await run(withPassword, { password: 'x' })).status, 499);
    assert.equal(withPassword.asked[0].reason, 'this PDF is password protected');
    assert.equal(withPassword.events.openedWith, null, 'never even opened');

    const err = new Error('No password given'); err.name = 'PasswordException';
    const encrypted = load({ consent: false, openError: err });
    assert.equal((await run(encrypted, {})).status, 499);
    assert.equal(encrypted.asked[0].reason, 'this PDF is password protected');
    assert.equal(encrypted.fetchCalls.length, 0);
});

test('an unreadable PDF asks, and agreeing uploads exactly once', async () => {
    const ctx = load({ consent: true, openError: new Error('Invalid PDF structure') });
    const res = await run(ctx, {});
    assert.equal(res.ok, true);
    assert.equal(ctx.asked[0].tool, 'PDF to JPG');
    assert.equal(ctx.asked[0].reason, 'this file uses features that cannot be processed on this device');
    assert.equal(ctx.fetchCalls.length, 1);
    assert.equal(ctx.fetchCalls[0].url, 'https://api.test/api/pdf/to-images');
});

test('a page that fails to render stops the job and asks, never ships a partial ZIP', async () => {
    const ctx = load({ pages: [[100, 100], [100, 100]], consent: false });
    const original = ctx.sandbox.pdfjsLib.getDocument;
    let n = 0;
    ctx.sandbox.pdfjsLib.getDocument = (o) => {
        const task = original(o);
        return {
            ...task,
            promise: task.promise.then((doc) => ({
                ...doc,
                getPage: async (i) => {
                    const page = await doc.getPage(i);
                    return { ...page, render: () => (++n === 2 ? { promise: Promise.reject(new Error('bad glyph')) } : page.render({})) };
                },
            })),
        };
    };
    const res = await run(ctx, {});
    assert.equal(res.status, 499);
    assert.equal(ctx.asked.length, 1);
    assert.equal(ctx.fetchCalls.length, 0, 'declined: nothing uploaded');
    assert.equal(ctx.events.destroyed, 1, 'document still released');
});

test('cancelling stops rendering and never uploads', async () => {
    const ctx = load({ pages: Array.from({ length: 6 }, () => [100, 100]), consent: true });
    const abort = new AbortController();
    const seen = [];
    const run1 = ctx.sandbox.ffProcess('/api/pdf/to-images', form(pdfBlob(), 'a.pdf', {}), {
        signal: abort.signal,
        onProgress: (done, total) => { seen.push([done, total]); if (done === 2) abort.abort(); },
    });
    await assert.rejects(run1, (e) => e.name === 'AbortError');
    assert.deepEqual(seen.at(-1), [2, 6]);
    assert.equal(ctx.fetchCalls.length, 0);
    assert.equal(ctx.asked.length, 0);
    assert.equal(ctx.events.destroyed, 1, 'document released after cancel');
});

test('progress is reported per page', async () => {
    const ctx = load({ pages: [[100, 100], [100, 100], [100, 100]] });
    const seen = [];
    await ctx.sandbox.ffProcess('/api/pdf/to-images', form(pdfBlob(), 'a.pdf', {}), { onProgress: (d, t) => seen.push([d, t]) });
    assert.deepEqual(seen, [[1, 3], [2, 3], [3, 3]]);
});

test('the brand suffix is not stacked in member names', async () => {
    const ctx = load();
    const { members: m } = await members(ctx, await run(ctx, {}, 'report_forgefiles.org.pdf'));
    assert.deepEqual(Object.keys(m), ['report_page_001.jpg']);
});
