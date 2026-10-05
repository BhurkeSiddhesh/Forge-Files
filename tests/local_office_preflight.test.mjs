// Phase 5 (WP44): script-coverage and file-type preflight for the on-device Office engine.
// Run with `node --test public/tests/local_office_preflight.test.mjs`.

import test from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const require = createRequire(import.meta.url);
const STATIC = join(dirname(fileURLToPath(import.meta.url)), '..', 'static');
const pre = require(join(STATIC, 'on-device-office', 'office-preflight.js'));
const JSZip = require(join(STATIC, 'vendor', 'jszip.min.js'));

async function zipFile(name, entries) {
    const zip = new JSZip();
    for (const [path, body] of Object.entries(entries)) zip.file(path, body);
    const bytes = await zip.generateAsync({ type: 'uint8array' });
    return new File([bytes], name);
}

const docx = (text) => zipFile('a.docx', { 'word/document.xml': `<w:document><w:body><w:p><w:r><w:t>${text}</w:t></w:r></w:p></w:body></w:document>` });
const run = (file, op) => pre.preflight(file, op, { JSZip });

test('verified scripts pass: Latin with accents, Devanagari, digits, symbols', async () => {
    const res = await run(await docx('Résumé Ünïcode नमस्ते मराठी 12,345 ₹ € — “quotes” ✓'), 'word-to-pdf');
    assert.deepEqual(res, { ok: true });
});

for (const [label, text] of [
    ['Tamil', 'வணக்கம்'], ['Telugu', 'నమస్కారం'], ['Japanese', 'こんにちは 日本語'], ['Korean', '안녕하세요'],
    ['Arabic', 'مرحبا'], ['Hebrew', 'שלום'], ['Thai', 'สวัสดี'], ['Cyrillic', 'Привет'], ['Greek', 'Γειά σου'],
    ['Bengali', 'নমস্কার'], ['Kannada', 'ನಮಸ್ಕಾರ'],
]) {
    test(`${label} text is declined with font_coverage_missing`, async () => {
        const res = await run(await docx(`Hello ${text}`), 'word-to-pdf');
        assert.equal(res.ok, false);
        assert.equal(res.code, 'font_coverage_missing');
        assert.ok(res.scripts.length >= 1);
        assert.ok(!res.reason.includes(text), 'the reason must never echo document content');
    });
}

test('mixed Latin and Tamil names Tamil and the reason is fixed wording', async () => {
    const res = await run(await docx('Hello வணக்கம்'), 'word-to-pdf');
    assert.deepEqual(res.scripts, ['Tamil']);
    assert.match(res.reason, /Tamil text/);
});

test('Excel shared strings, PowerPoint slides and ODT content are scanned', async () => {
    const xlsx = await zipFile('a.xlsx', { 'xl/sharedStrings.xml': '<sst><si><t>வணக்கம்</t></si></sst>' });
    assert.equal((await run(xlsx, 'excel-to-pdf')).code, 'font_coverage_missing');
    const pptx = await zipFile('a.pptx', { 'ppt/slides/slide1.xml': '<p:sld><a:t>日本語</a:t></p:sld>' });
    assert.equal((await run(pptx, 'ppt-to-pdf')).code, 'font_coverage_missing');
    assert.equal((await run(pptx, 'ppt-to-images')).code, 'font_coverage_missing');
    const odt = await zipFile('a.odt', { 'content.xml': '<office:text><text:p>Привет</text:p></office:text>' });
    assert.equal((await run(odt, 'word-to-pdf')).code, 'font_coverage_missing');
});

test('headers, footers and slide masters are scanned too', async () => {
    const d = await zipFile('a.docx', {
        'word/document.xml': '<w:t>Hello</w:t>',
        'word/header1.xml': '<w:hdr><w:t>வணக்கம்</w:t></w:hdr>',
    });
    assert.equal((await run(d, 'word-to-pdf')).code, 'font_coverage_missing');
    const p = await zipFile('a.pptx', {
        'ppt/slides/slide1.xml': '<a:t>Hi</a:t>',
        'ppt/slideMasters/slideMaster1.xml': '<a:t>日本語</a:t>',
    });
    assert.equal((await run(p, 'ppt-to-pdf')).code, 'font_coverage_missing');
});

test('unsupported extensions are declined without reading the file', async () => {
    for (const [name, op] of [['a.doc', 'word-to-pdf'], ['a.rtf', 'word-to-pdf'], ['a.xls', 'excel-to-pdf'],
        ['a.csv', 'excel-to-pdf'], ['a.ppt', 'ppt-to-pdf'], ['a.docx', 'ppt-to-pdf'], ['noext', 'word-to-pdf']]) {
        const res = await run(new File(['x'], name), op);
        assert.equal(res.code, 'unsupported_type', name);
    }
    assert.equal((await run(new File(['x'], 'a.docx'), 'no-such-op')).code, 'unsupported_type');
});

test('empty, oversized and unreadable files are declined', async () => {
    assert.equal((await run(new File([], 'a.docx'), 'word-to-pdf')).code, 'undecodable');
    const big = { name: 'a.docx', size: pre.MAX_INPUT_BYTES + 1, arrayBuffer() { throw new Error('must not be read'); } };
    assert.equal((await run(big, 'word-to-pdf')).code, 'resource_budget_exceeded');
    assert.equal((await run(new File(['not a zip'], 'a.docx'), 'word-to-pdf')).code, 'undecodable');
    const encrypted = new File([new Uint8Array([0xd0, 0xcf, 0x11, 0xe0, 0, 0, 0, 0])], 'a.docx');
    assert.equal((await run(encrypted, 'word-to-pdf')).code, 'undecodable');
});

test('every conversion declares an output kind and extensions', () => {
    assert.deepEqual(Object.keys(pre.OPS).sort(), ['excel-to-pdf', 'ppt-to-images', 'ppt-to-pdf', 'word-to-pdf']);
    assert.equal(pre.OPS['ppt-to-images'].out, 'images');
});
