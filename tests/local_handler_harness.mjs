// Generic CLI for parity audits of on-device handlers (work packages 07+).
//
//   node local_handler_harness.mjs <api-path> <spec.json> <out-file>
//
// spec.json: {"files": [{"field": "file", "path": "...", "name": "a.pdf", "type": "application/pdf"}],
//             "fields": {"pages": "1-2"}}
// Runs the real handler (real pdf-lib; no canvas) with consent always declined and
// no network. Prints one JSON line {status, detail?, message?, filename?, asked?};
// on success the result bytes are written to <out-file>.
import vm from 'node:vm';
import { readFileSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const STATIC = join(dirname(fileURLToPath(import.meta.url)), '..', 'static');
const [, , apiPath, specPath, outPath] = process.argv;
const spec = JSON.parse(readFileSync(specPath, 'utf8'));

const URLShim = function (...a) { return new URL(...a); };
URLShim.createObjectURL = () => 'blob:mock/1';
URLShim.revokeObjectURL = () => { };
const sandbox = {
    console, Blob, FormData, Response, TextEncoder, TextDecoder, AbortController, setTimeout, clearTimeout,
    FileReader: class { }, Image: class { }, URL: URLShim,
    document: {
        currentScript: null, head: { appendChild() { } },
        createElement: (t) => (t === 'canvas' ? { toBlob() { }, getContext: () => null } : { set src(_v) { }, onload: null, onerror: null }),
    },
    fetch() { throw new Error('the harness must never upload'); },
};
sandbox.window = sandbox;
sandbox.self = sandbox;
sandbox.window.apiUrl = (p) => p;
vm.createContext(sandbox);
for (const f of ['vendor/pdf-lib.min.js', 'vendor/jszip.min.js', 'local/ff-server-gate.js', 'local/ff-local.js',
    'local/ops-image.js', 'local/ops-pdf.js', 'local/ops-pdf-render.js']) {
    vm.runInContext(readFileSync(join(STATIC, f), 'utf8'), sandbox, { filename: f });
}
let asked = null;
sandbox.ffConsent.handler = (info) => { asked = info; return false; };

const fd = new FormData();
for (const f of spec.files || []) {
    fd.append(f.field, new Blob([readFileSync(f.path)], { type: f.type || 'application/octet-stream' }), f.name || 'upload');
}
for (const [k, v] of Object.entries(spec.fields || {})) fd.append(k, String(v));
const res = await sandbox.ffProcess(apiPath, fd);
const body = await res.json();
const out = { status: res.status, detail: body.detail, message: body.message, asked };
if (res.ok) {
    writeFileSync(outPath, Buffer.from(await sandbox.ffLocal.resolve(body.download_token).blob.arrayBuffer()));
    out.filename = body.filename;
    out.extra = Object.fromEntries(Object.entries(body).filter(([k]) => !['status', 'message', 'filename', 'download_token', 'local'].includes(k)));
}
console.log(JSON.stringify(out));
