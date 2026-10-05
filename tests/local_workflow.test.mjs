// Work package 40: Workflows on-device and hybrid execution.
//
// Tests client typed step runner, consecutive on-device execution with intermediate
// files staying in memory, hybrid consent gate before server upload, decline policy,
// and step validations.
// Run with `node --test public/tests/local_workflow.test.mjs`.

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
        console, Blob, File, FormData, Response, TextEncoder, TextDecoder,
        Buffer, Uint8Array, ArrayBuffer,
        ReadableStream,
        FileReader: class {
            readAsArrayBuffer(b) {
                b.arrayBuffer().then(buf => { this.result = buf; this.onload && this.onload(); });
            }
            readAsText(b) {
                b.text().then(txt => { this.result = txt; this.onload && this.onload(); });
            }
        },
        Image: class { },
        URL: URLShim,
        setTimeout, clearTimeout, AbortController,
        document: {
            currentScript: null,
            head: { appendChild() { } },
            createElement: (tag) => (tag === 'canvas'
                ? { width: 0, height: 0, toBlob() { }, getContext: () => null }
                : { set src(_v) { }, onload: null, onerror: null }),
        },
        fetch(url, init) {
            fetchCalls.push({ url, init });
            return Promise.resolve(new Response('data: {"event":"complete","status":"success","filename":"server_res.pdf"}\n\n', {
                status: 200,
                headers: { 'Content-Type': 'text/event-stream' },
            }));
        },
    };
    sandbox.window = sandbox;
    sandbox.self = sandbox;
    sandbox.window.apiUrl = (p) => `https://api.test${p}`;
    if (options.matchMedia) sandbox.window.matchMedia = options.matchMedia;

    vm.createContext(sandbox);

    for (const f of [
        'vendor/exceljs.min.js',
        'vendor/jszip.min.js',
        'local/ff-server-gate.js',
        'local/ff-local.js',
        'local/ops-excel.js',
        'local/ops-workflow.js',
    ]) {
        vm.runInContext(readFileSync(join(STATIC, f), 'utf8'), sandbox, { filename: f });
    }

    const asked = [];
    sandbox.ffConsent.handler = (info, req) => {
        asked.push(req || info);
        return options.consent === true;
    };

    return { sandbox, L: sandbox.ffLocal, fetchCalls, asked };
}

/** Read SSE events from a Response stream. */
async function readSseEvents(res) {
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    const events = [];

    while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n\n');
        buffer = lines.pop();
        for (const line of lines) {
            if (line.startsWith('data: ')) {
                events.push(JSON.parse(line.substring(6)));
            }
        }
    }
    return events;
}

test('WP40: Step validation errors (missing file, invalid steps JSON, max steps)', async () => {
    const ctx = load();

    // Missing file
    const fd1 = new FormData();
    fd1.append('steps', JSON.stringify([{ type: 'csv_to_xlsx' }]));
    const res1 = await ctx.sandbox.ffProcess('/api/workflow/execute', fd1);
    assert.equal(res1.status, 400);
    assert.match((await res1.json()).detail, /No file provided/);

    // Invalid steps JSON
    const fd2 = new FormData();
    fd2.append('file', new Blob(['test']), 'test.csv');
    fd2.append('steps', 'not json');
    const res2 = await ctx.sandbox.ffProcess('/api/workflow/execute', fd2);
    assert.equal(res2.status, 400);
    assert.match((await res2.json()).detail, /Invalid steps JSON/);

    // Empty steps array
    const fd3 = new FormData();
    fd3.append('file', new Blob(['test']), 'test.csv');
    fd3.append('steps', '[]');
    const res3 = await ctx.sandbox.ffProcess('/api/workflow/execute', fd3);
    assert.equal(res3.status, 400);
    assert.match((await res3.json()).detail, /non-empty list/);

    // Exceeding the server default MAX_WORKFLOW_STEPS (20)
    const fd4 = new FormData();
    fd4.append('file', new Blob(['test']), 'test.csv');
    const tooMany = Array.from({ length: 21 }, () => ({ type: 'csv_to_xlsx' }));
    fd4.append('steps', JSON.stringify(tooMany));
    const res4 = await ctx.sandbox.ffProcess('/api/workflow/execute', fd4);
    assert.equal(res4.status, 400);
    assert.match((await res4.json()).detail, /Too many steps/);
});

test('WP40: Pure local workflow execution (csv_to_xlsx -> xlsx_to_csv)', async () => {
    const ctx = load();
    const fd = new FormData();
    const csvContent = 'Fruit,Price\nApple,1.5\nBanana,0.8\n';
    fd.append('file', new Blob([csvContent], { type: 'text/csv' }), 'basket.csv');

    const steps = [
        { type: 'csv_to_xlsx', label: 'Convert CSV to XLSX', config: {} },
        { type: 'xlsx_to_csv', label: 'Export XLSX to CSV', config: {} },
    ];
    fd.append('steps', JSON.stringify(steps));

    const res = await ctx.sandbox.ffProcess('/api/workflow/execute', fd);
    assert.equal(res.status, 200);
    assert.match(res.headers.get('content-type') || '', /text\/event-stream/);

    const events = await readSseEvents(res);
    assert.ok(events.length >= 5);

    // Event sequence: start, step_start 0, step_complete 0, step_start 1, step_complete 1, complete
    assert.equal(events[0].event, 'start');
    assert.equal(events[0].total, 2);

    assert.equal(events[1].event, 'step_start');
    assert.equal(events[1].step, 0);

    assert.equal(events[2].event, 'step_complete');
    assert.equal(events[2].step, 0);

    assert.equal(events[3].event, 'step_start');
    assert.equal(events[3].step, 1);

    assert.equal(events[4].event, 'step_complete');
    assert.equal(events[4].step, 1);

    const complete = events[events.length - 1];
    assert.equal(complete.event, 'complete');
    assert.equal(complete.status, 'success');
    assert.equal(complete.local, true);
    assert.ok(complete.download_token);

    // Output is stored locally in download registry
    const finalItem = ctx.L.resolve(complete.download_token);
    assert.ok(finalItem && finalItem.blob);
    const finalCsv = await finalItem.blob.text();
    assert.match(finalCsv, /Fruit,Price/);
    assert.match(finalCsv, /Apple,1\.5/);

    // CRITICAL: Zero bytes uploaded to server!
    assert.equal(ctx.fetchCalls.length, 0);
    assert.equal(ctx.asked.length, 0);
});

test('WP40: Hybrid workflow consent prompt naming step and intermediate filename', async () => {
    // A workflow with a local step followed by a server-only step (e.g. pdf_to_word)
    const ctx = load({ consent: false });
    const fd = new FormData();
    fd.append('file', new Blob(['Col1,Col2\nVal1,Val2\n'], { type: 'text/csv' }), 'data.csv');

    const steps = [
        { type: 'csv_to_xlsx', label: 'Build Excel Sheet', config: {} },
        { type: 'excel_to_pdf', label: 'Convert to Word DOCX', config: {} }, // Server only step
    ];
    fd.append('steps', JSON.stringify(steps));

    const res = await ctx.sandbox.ffProcess('/api/workflow/execute', fd);
    assert.equal(res.status, 200);

    const events = await readSseEvents(res);

    // When consent is declined:
    // Consent dialog must have been prompted naming the server step and intermediate file
    assert.equal(ctx.asked.length, 1);
    assert.equal(ctx.asked[0].path, '/api/workflow/execute');
    assert.match(ctx.asked[0].reason, /Step Convert to Word DOCX needs server processing/);
    assert.match(ctx.asked[0].filename, /^data_forgefiles\.org\.xlsx$/);

    // Decline terminates workflow with error event without uploading anything
    const errorEvent = events.find(e => e.event === 'error');
    assert.ok(errorEvent);
    assert.match(errorEvent.detail, /Cancelled\. Your file was not uploaded\./);

    // ZERO bytes uploaded
    assert.equal(ctx.fetchCalls.length, 0);
});

test('WP40: Hybrid workflow consent agreed uploads intermediate and remaining steps', async () => {
    const ctx = load({ consent: true });
    const fd = new FormData();
    fd.append('file', new Blob(['Col1,Col2\nVal1,Val2\n'], { type: 'text/csv' }), 'input.csv');

    const steps = [
        { type: 'csv_to_xlsx', label: 'Local Sheet', config: {} },
        { type: 'server_step', label: 'Server Magic', config: {} },
    ];
    fd.append('steps', JSON.stringify(steps));

    const res = await ctx.sandbox.ffProcess('/api/workflow/execute', fd);
    assert.equal(res.status, 200);

    const events = await readSseEvents(res);
    assert.ok(events.length > 0);

    // User consented: fetch was called exactly once to send remaining steps and intermediate
    assert.equal(ctx.fetchCalls.length, 1);
    const call = ctx.fetchCalls[0];
    assert.equal(call.url, 'https://api.test/api/workflow/execute');
    assert.equal(call.init.method, 'POST');

    // The uploaded file in body must be the intermediate from step 1 (xlsx), NOT the original csv!
    const uploadedFd = call.init.body;
    const uploadedFile = uploadedFd.get('file');
    assert.match(uploadedFile.name, /^input_forgefiles\.org\.xlsx$/);

    // Remaining steps passed to server contains only the server step
    const remainingSteps = JSON.parse(uploadedFd.get('steps'));
    assert.equal(remainingSteps.length, 1);
    assert.equal(remainingSteps[0].type, 'server_step');
});

function serverFetch(events, tokenBytes) {
    return (url) => {
        if (String(url).includes('/api/download/')) {
            return Promise.resolve(new Response(tokenBytes, { status: 200 }));
        }
        const sse = events.map(e => 'data: ' + JSON.stringify(e) + '\n\n').join('');
        return Promise.resolve(new Response(sse, { status: 200, headers: { 'Content-Type': 'text/event-stream' } }));
    };
}

test('WP40: 11-20 step chains are accepted locally (backend default is 20)', async () => {
    const ctx = load();
    const fd = new FormData();
    fd.append('file', new Blob(['a,b\n1,2\n']), 'x.csv');
    const steps = [];
    for (let i = 0; i < 10; i++) steps.push({ type: 'csv_to_xlsx' }, { type: 'xlsx_to_csv' });
    fd.append('steps', JSON.stringify(steps));
    const res = await ctx.sandbox.ffProcess('/api/workflow/execute', fd);
    assert.equal(res.status, 200);
    const events = await readSseEvents(res);
    assert.equal(events.at(-1).event, 'complete');
    assert.equal(ctx.fetchCalls.length, 0);
});

test('WP40: preflight rejects incompatible chains before any step or upload', async () => {
    const ctx = load({ consent: true });
    const fd = new FormData();
    fd.append('file', new Blob(['a,b\n1,2\n']), 'x.csv');
    fd.append('steps', JSON.stringify([
        { type: 'csv_to_xlsx' },
        { type: 'rotate_pdf', label: 'Rotate' },   // xlsx -> rotate_pdf is invalid
    ]));
    const res = await ctx.sandbox.ffProcess('/api/workflow/execute', fd);
    assert.equal(res.status, 400);
    assert.match((await res.json()).detail, /Step 2 \(Rotate\) cannot accept a xlsx file/);
    assert.equal(ctx.fetchCalls.length, 0);
    assert.equal(ctx.asked.length, 0);
});

test('WP40: server-then-local runs the server step first, then continues on-device', async () => {
    const ctx = load({ consent: true });
    const wb = new ctx.sandbox.ExcelJS.Workbook();
    wb.addWorksheet('S').getCell('A1').value = 'hello';
    const xlsx = Buffer.from(await wb.xlsx.writeBuffer());
    // server step "pdf_to_excel" yields an xlsx; local xlsx_to_csv follows.
    const respond = serverFetch([
        { event: 'start', total: 1 },
        { event: 'step_start', step: 0, total: 1, label: 'S' },
        { event: 'step_complete', step: 0, total: 1, label: 'S' },
        { event: 'complete', filename: 'srv.xlsx', download_token: 'tok1' },
    ], xlsx);
    ctx.sandbox.fetch = (url, init) => {
        ctx.fetchCalls.push({ url, init });
        return respond(url);
    };
    const fd = new FormData();
    fd.append('file', new Blob(['%PDF-1.4']), 'in.pdf');
    fd.append('steps', JSON.stringify([
        { type: 'pdf_to_excel', label: 'S' },
        { type: 'xlsx_to_csv', label: 'Out' },
    ]));
    const res = await ctx.sandbox.ffProcess('/api/workflow/execute', fd);
    const events = await readSseEvents(res);
    assert.equal(events.at(-1).event, 'complete');
    assert.equal(events.at(-1).local, true);
    const csv = await ctx.L.resolve(events.at(-1).download_token).blob.text();
    assert.match(csv, /hello/);

    const uploads = ctx.fetchCalls.filter(c => !String(c.url).includes('/api/download/'));
    assert.equal(uploads.length, 1);
    assert.equal(uploads[0].init.body.get('file').name, 'in.pdf');
    assert.equal(JSON.parse(uploads[0].init.body.get('steps')).length, 1);
    assert.equal(ctx.asked.length, 1);
    assert.match(ctx.asked[0].filename, /in\.pdf/);
    const stepIdx = events.filter(e => e.event === 'step_start').map(e => e.step);
    assert.deepEqual(stepIdx, [0, 1]);
});

test('WP40: declining the server step in server-then-local uploads nothing', async () => {
    const ctx = load({ consent: false });
    const fd = new FormData();
    fd.append('file', new Blob(['%PDF-1.4']), 'in.pdf');
    fd.append('steps', JSON.stringify([{ type: 'pdf_to_excel' }, { type: 'xlsx_to_csv' }]));
    const res = await ctx.sandbox.ffProcess('/api/workflow/execute', fd);
    const events = await readSseEvents(res);
    assert.match(events.find(e => e.event === 'error').detail, /Your file was not uploaded/);
    assert.equal(ctx.fetchCalls.length, 0);
});
