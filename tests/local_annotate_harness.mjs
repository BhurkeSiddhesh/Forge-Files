// CLI used by test_local_annotate_interop.py: runs the real on-device annotate
// handler (real pdf-lib) on a PDF and writes the result, so an independent PDF
// library can inspect it.
//
//   node local_annotate_harness.mjs <in.pdf> <annotations.json> <out.pdf>
//
// Prints one JSON line: {status, detail?, message?, asked?}. Consent is always
// declined, so a decline shows up as status 499 with the reason asked.
import vm from 'node:vm';
import { readFileSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const STATIC = join(dirname(fileURLToPath(import.meta.url)), '..', 'static');
const [, , input, annotationsPath, output] = process.argv;

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
    fetch() { throw new Error('the harness must never upload'); },
};
sandbox.window = sandbox;
sandbox.self = sandbox;
sandbox.window.apiUrl = (p) => p;
vm.createContext(sandbox);
for (const f of ['vendor/pdf-lib.min.js', 'local/ff-server-gate.js', 'local/ff-local.js', 'local/ops-pdf.js', 'local/ops-pdf-annotate.js']) {
    vm.runInContext(readFileSync(join(STATIC, f), 'utf8'), sandbox, { filename: f });
}
let asked = null;
sandbox.ffConsent.handler = (info) => { asked = info; return false; };

const fd = new FormData();
fd.append('file', new Blob([readFileSync(input)], { type: 'application/pdf' }), 'doc.pdf');
fd.append('annotations', readFileSync(annotationsPath, 'utf8'));
const res = await sandbox.ffProcess('/api/pdf/annotate', fd);
const body = await res.json();
const out = { status: res.status, detail: body.detail, message: body.message, asked };
if (res.ok) {
    writeFileSync(output, Buffer.from(await sandbox.ffLocal.resolve(body.download_token).blob.arrayBuffer()));
    out.filename = body.filename;
}
console.log(JSON.stringify(out));
