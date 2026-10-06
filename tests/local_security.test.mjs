import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const STATIC = join(dirname(fileURLToPath(import.meta.url)), '..', 'static');

// A stand-in for qpdf: records its argv and returns whatever exit code the test sets.
function context({ exit = 0, writeOutput = true } = {}) {
    const s = {
        console, Blob, File, FormData, Response, Uint8Array, ArrayBuffer, setTimeout, clearTimeout,
        TextEncoder, TextDecoder, AbortController,
        URL: Object.assign(URL, { createObjectURL: () => 'blob:test', revokeObjectURL() {} }),
        fetch: async (url) => (String(url).includes('qpdf.wasm') ? new Response(new Uint8Array([0])) : new Response('{}')),
        document: {
            createElement(tag) {
                if (tag === 'canvas') return { toBlob() {}, getContext() { return {}; } };
                return { set src(v) { this._src = v; }, onload: null, onerror: null };
            },
            head: { appendChild(el) { setTimeout(() => el.onload && el.onload(), 0); } },
        },
        localStorage: { getItem() { return null; } },
        runs: [],
    };
    s.window = s;
    s.self = s;
    s.apiUrl = (p) => p;
    s.Module = async () => ({
        FS: {
            writeFile(_p, bytes) { s.input = bytes; },
            readFile() {
                if (!writeOutput) throw new Error('no output');
                return new Uint8Array([37, 80, 68, 70, 45, 1]);
            },
        },
        callMain(args) { s.runs.push(args); return typeof exit === 'function' ? exit(args, s.runs.length) : exit; },
    });
    vm.createContext(s);
    for (const f of ['local/ff-server-gate.js', 'local/ff-local.js', 'local/ops-pdf-security.js']) {
        vm.runInContext(readFileSync(join(STATIC, f), 'utf8'), s, { filename: f });
    }
    s.ffConsent.handler = () => false; // declining: nothing may reach the server
    return s;
}

const pdf = (bytes) => new File([new Uint8Array(bytes)], 'doc.pdf', { type: 'application/pdf' });
const encrypted = [37, 80, 68, 70, 47, 69, 110, 99, 114, 121, 112, 116, 32, 53, 32, 48, 32, 82];
const plain = [37, 80, 68, 70, 45, 49, 46, 55];

test('protect encrypts locally with AES-256 and maps the permission boxes', async () => {
    const s = context();
    const fd = new FormData();
    fd.append('file', pdf(plain));
    fd.append('user_password', 'open');
    fd.append('allow_print', 'true');
    fd.append('allow_copy', 'false');
    fd.append('allow_edit', 'false');
    const res = await s.ffProcess('/api/pdf/protect', fd);
    const body = await res.json();
    assert.equal(res.status, 200);
    assert.equal(body.local, true);
    assert.equal(body.message, 'PDF protected with password');
    assert.match(body.filename, /^doc_forgefiles\.org\.pdf$/);
    const args = s.runs[0];
    assert.deepEqual(Array.from(args.slice(0, 4)), ['--encrypt', 'open', 'open', '256']); // owner defaults to user
    for (const a of ['--print=full', '--extract=n', '--annotate=n', '--assemble=n', '--form=n', '--modify-other=n', '--accessibility=y']) {
        assert.ok(args.includes(a), a);
    }
});

test('protect passes an existing password and editing permissions through', async () => {
    const s = context();
    const fd = new FormData();
    fd.append('file', pdf(encrypted));
    fd.append('user_password', 'new');
    fd.append('owner_password', 'boss');
    fd.append('password', 'old');
    fd.append('allow_print', 'false');
    fd.append('allow_copy', 'true');
    fd.append('allow_edit', 'true');
    await s.ffProcess('/api/pdf/protect', fd);
    const args = s.runs[0];
    assert.equal(args[0], '--password=old');
    assert.deepEqual(Array.from(args.slice(1, 5)), ['--encrypt', 'new', 'boss', '256']);
    for (const a of ['--print=none', '--extract=y', '--annotate=y', '--modify-other=y']) assert.ok(args.includes(a), a);
});

test('protect needs a user password and never uploads', async () => {
    const s = context();
    const fd = new FormData();
    fd.append('file', pdf(plain));
    fd.append('user_password', '');
    const res = await s.ffProcess('/api/pdf/protect', fd);
    assert.equal(res.status, 400);
    assert.equal((await res.json()).detail, 'User password cannot be empty.');
    assert.equal(s.runs.length, 0);
});

test('unlock tries the password, then retries empty for owner-only PDFs', async () => {
    const s = context({ exit: (_a, n) => (n === 1 ? 2 : 0) });
    const fd = new FormData();
    fd.append('file', pdf(encrypted));
    fd.append('password', 'wrong');
    const res = await s.ffProcess('/api/pdf/remove-password', fd);
    assert.equal(res.status, 200);
    assert.equal((await res.json()).message, 'Password removed');
    assert.deepEqual(Array.from(s.runs, (r) => r[0]), ['--password=wrong', '--password=']);
    assert.ok(s.runs[0].includes('--decrypt'));
});

test('a PDF that needs a password still fails on a wrong one, locally', async () => {
    const s = context({ exit: 2, writeOutput: false });
    const fd = new FormData();
    fd.append('file', pdf(encrypted));
    fd.append('password', 'wrong');
    const res = await s.ffProcess('/api/pdf/remove-password', fd);
    assert.equal(res.status, 400);
    assert.match((await res.json()).detail, /Incorrect password/);
});

test('exit status 3 (written with warnings) counts as success', async () => {
    const s = context({ exit: 3 });
    const fd = new FormData();
    fd.append('file', pdf(plain));
    fd.append('password', 'x');
    const res = await s.ffProcess('/api/pdf/remove-password', fd);
    assert.equal(res.status, 200);
});

test('a PDF qpdf cannot process asks before the server gets it', async () => {
    const s = context({ exit: 2, writeOutput: false });
    const fd = new FormData();
    fd.append('file', pdf(plain));
    fd.append('password', 'x');
    const res = await s.ffProcess('/api/pdf/remove-password', fd);
    assert.equal(res.status, 499); // consent declined: not uploaded
});
