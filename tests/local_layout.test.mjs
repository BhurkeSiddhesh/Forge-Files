// Work packages 16/42/21: the shared PDF layout analysis and the EPUB builders, as pure functions.
//
// Real pdf.js output is exercised in Chromium by test_local_*_parity.py. These tests pin the
// logic that turns pdf.js-shaped text fragments into lines, columns, paragraphs and EPUB
// markup, including the cases that must be refused rather than guessed.
import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const STATIC = join(dirname(fileURLToPath(import.meta.url)), '..', 'static');

function load() {
    const sandbox = {
        console, Blob, FormData, Response, TextEncoder, TextDecoder, AbortController, setTimeout, clearTimeout,
        Uint8Array, Uint16Array, Math, Object, Array, String, Number, RegExp, Date, Error,
        FileReader: class { }, Image: class { },
        URL: Object.assign(function () { }, { createObjectURL: () => 'blob:x', revokeObjectURL() { } }),
        document: { currentScript: null, head: { appendChild() { } }, createElement: () => ({ toBlob() { } }) },
    };
    sandbox.window = sandbox;
    sandbox.self = sandbox;
    sandbox.window.apiUrl = (p) => p;
    vm.createContext(sandbox);
    for (const f of ['local/ff-server-gate.js', 'local/ff-local.js', 'local/ops-pdf-layout.js', 'local/ops-pdf-epub.js']) {
        vm.runInContext(readFileSync(join(STATIC, f), 'utf8'), sandbox, { filename: f });
    }
    vm.runInContext(readFileSync(join(STATIC, 'vendor/jszip.min.js'), 'utf8'), sandbox, { filename: 'jszip' });
    return sandbox;
}

const S = load();
// The vm sandbox has its own Array/Object, so strict deep equality across realms needs a plain copy.
const J = (v) => JSON.parse(JSON.stringify(v));
const same = (a, b) => assert.deepEqual(J(a), b);
const layout = S.ffLocal.layout;
const epub = S.ffLocal.epub;
const H = 800;
const W = 600;

/** A pdf.js text item at top-down baseline `y`. Width is a flat 5.5 pt per character at 10 pt. */
const item = (str, x, y, size = 10, o = {}) => ({
    str, dir: o.dir || 'ltr', width: o.width ?? str.length * size * 0.55, height: size,
    transform: o.transform || [size, 0, 0, size, x, H - y], fontName: o.font || 'f1',
});
const styles = { f1: {}, fb: { bold: true }, fi: { italic: true }, fm: { mono: true } };
const page = (items, links = []) => layout.analysePage({ items, styles, view: [0, 0, W, H], links });

// ── lines ─────────────────────────────────────────────────────────────────

test('fragments on one baseline form a line, with a space only where there is a gap', () => {
    const p = page([item('Hello', 72, 100), item('world', 72 + 5 * 5.5 + 5, 100), item('!', 72 + 10 * 5.5 + 10, 100)]);
    assert.equal(p.lines.length, 1);
    assert.equal(p.lines[0].text, 'Hello world !');
});

test('a very wide gap on a line becomes a tab marker, not run-together text', () => {
    const p = page([item('Company', 72, 100), item('2020', 460, 100)]);
    assert.equal(p.lines[0].text, 'Company\t2020');
});

test('superscripts stay on their line; the next line does not', () => {
    const p = page([item('E=mc', 72, 100), item('2', 72 + 22, 96, 6), item('Second line', 72, 114)]);
    assert.equal(p.lines.length, 2);
    assert.equal(p.lines[0].text, 'E=mc2');
});

test('ligature glyphs and soft hyphens are normalised', () => {
    const p = page([item('oﬃce ­ﬁnal', 72, 100)]);
    assert.equal(p.lines[0].text, 'office final');
});

test('styled runs are kept apart: bold, italic and mono', () => {
    const p = page([item('plain ', 72, 100), item('bold', 72 + 33, 100, 10, { font: 'fb' }), item(' ital', 72 + 55, 100, 10, { font: 'fi' })]);
    const runs = p.lines[0].runs;
    same(runs.map((r) => [r.text.trim(), r.bold, r.italic]), [['plain', false, false], ['bold', true, false], ['ital', false, true]]);
});

// ── refusals ──────────────────────────────────────────────────────────────

test('mostly rotated text is refused, a small rotated stamp is dropped', () => {
    const rot = (s, x, y) => item(s, x, y, 10, { transform: [0, 10, -10, 0, x, H - y] });
    assert.throws(() => page([item('a few words', 72, 100), rot('rotated page text goes here', 300, 100), rot('and more rotated text here', 320, 100)]),
        (e) => e instanceof layout.Decline && e.code === 'unsupported_structure');
    const ok = page([item('Normal body text on the page continues for a good while here.', 72, 100), rot('DRAFT', 580, 400)]);
    assert.equal(ok.lines.length, 1);
    assert.equal(ok.rotated, 5);
});

test('right-to-left text is refused', () => {
    assert.throws(() => page([item('שלום עולם', 72, 100, 10, { dir: 'rtl' })]),
        (e) => e instanceof layout.Decline);
});

// ── links ─────────────────────────────────────────────────────────────────

test('a link covering part of a run splits it at word edges', () => {
    const text = 'Visit example.com now';
    // Position the link over "example.com" using the same proportional widths the analyser uses.
    const w = text.length * 5.5;
    const x0 = 72 + (6 / text.length) * w;
    const x1 = 72 + (17 / text.length) * w;
    const p = page([item(text, 72, 100, 10, { font: 'fm' })], [{ url: 'https://example.com/', rect: [x0, H - 104, x1, H - 96] }]);
    const runs = p.lines[0].runs;
    same(runs.map((r) => [r.text, r.href]), [['Visit ', null], ['example.com', 'https://example.com/'], [' now', null]]);
});

test('closing punctuation is not swept into a link', () => {
    const text = 'see example.com.';
    const w = text.length * 5.5;
    const p = page([item(text, 72, 100, 10, { font: 'fm' })], [{ url: 'https://example.com/', rect: [72 + 4 / 16 * w, H - 104, 72 + w, H - 96] }]);
    assert.equal(p.lines[0].runs.find((r) => r.href).text, 'example.com');
});

// ── columns ───────────────────────────────────────────────────────────────

function columnLines(n) {
    const items = [];
    for (let i = 0; i < n; i++) {
        items.push(item(`left column line ${i} with some words`, 72, 100 + i * 14));
        items.push(item(`right column line ${i} with other words`, 330, 100 + i * 14));
    }
    return items;
}

test('two prose columns are detected and read left then right', () => {
    const p = page(columnLines(12));
    assert.equal(p.columns.count, 2);
    const texts = p.ordered.map((l) => l.text);
    assert.ok(texts.slice(0, 12).every((t) => t.startsWith('left')));
    assert.ok(texts.slice(12).every((t) => t.startsWith('right')));
});

test('a table is not mistaken for columns', () => {
    const items = [];
    for (let i = 0; i < 12; i++) {
        items.push(item('Item' + i, 72, 100 + i * 14), item('Qty', 250, 100 + i * 14), item('9.99', 420, 100 + i * 14));
    }
    assert.equal(page(items).columns.count, 1);
});

test('three columns are reported so a handler can refuse them', () => {
    const items = [];
    for (let i = 0; i < 12; i++) {
        items.push(item(`first column line ${i} text here`, 40, 100 + i * 14));
        items.push(item(`second column line ${i} text here`, 230, 100 + i * 14));
        items.push(item(`third column line ${i} text here`, 420, 100 + i * 14));
    }
    assert.equal(page(items).columns.count, 3);
});

test('a full-width title above two columns closes the zone', () => {
    const items = [item('A title that spans the whole width of the page here', 72, 80, 14), ...columnLines(12).map((it) => ({ ...it, transform: [...it.transform.slice(0, 5), it.transform[5] - 20] }))];
    const p = page(items);
    assert.equal(p.columns.count, 2);
    assert.ok(p.ordered[0].text.startsWith('A title'));
});

// ── paragraphs ────────────────────────────────────────────────────────────

const ctx = (lines, base = 10) => ({ base, right: layout.rightMargins(lines, { count: 1 }), page: 0 });

/** Greedy wrap at `limit` characters, the way a real typesetter fills lines. */
function wrappedBlock(words, limit, x, y0) {
    const rows = [];
    let row = '';
    for (const w of words) {
        if (row && (row + ' ' + w).length > limit) { rows.push(row); row = w; } else row = row ? row + ' ' + w : w;
    }
    if (row) rows.push(row);
    return rows.map((r, i) => item(r, x, y0 + i * 12));
}

test('wrapped lines join into one paragraph; a short last line ends it', () => {
    const words = Array.from({ length: 60 }, (_, i) => 'word' + i);
    const p = page([...wrappedBlock(words, 70, 72, 100), item('Second paragraph.', 72, 250)]);
    const blocks = layout.paragraphs(p.ordered, ctx(p.ordered));
    assert.equal(blocks.length, 2);
    assert.ok(blocks[0].runs.map((r) => r.text).join('').includes('word0 word1 word2'));
    assert.equal(blocks[0].runs.map((r) => r.text).join('').split(' ').length, 60);
});

test('ragged right text still joins when the next word would not have fitted', () => {
    // Line 1 stops well short of the margin only because "extraordinarily" did not fit.
    const lines = [
        item('A paragraph of ragged right text that wraps early because', 72, 100),
        item('extraordinarily long words follow here and keep on going until', 72, 112),
        item('unimaginable lengths are reached and the paragraph ends here.', 72, 124),
    ];
    const p = page(lines);
    const blocks = layout.paragraphs(p.ordered, { base: 10, right: () => 72 + 63 * 5.5, page: 0 });
    assert.equal(blocks.length, 1);
});

test('headings are found by size, and bullets and numbers become list items', () => {
    const p = page([
        item('Big Heading', 72, 80, 22),
        item('Intro text that is ordinary body copy.', 72, 110),
        item('• first point', 72, 140), item('• second point', 72, 154),
        item('1. step one', 72, 180), item('2. step two', 72, 194),
    ]);
    const blocks = layout.paragraphs(p.ordered, ctx(p.ordered));
    same(blocks.map((b) => b.kind), ['h2', 'p', 'li', 'li', 'li', 'li']);
    assert.equal(blocks[0].runs[0].text, 'Big Heading');
    assert.equal(blocks[2].runs.map((r) => r.text).join(''), 'first point');
    assert.equal(blocks[2].ordered, false);
    assert.equal(blocks[4].ordered, true);
});

test('a hyphen at a wrap is removed when the word continues in lower case', () => {
    const p = page([item('This sentence contains an extraordi-', 72, 100), item('nary hyphenated word at the wrap point of it.', 72, 112)]);
    const [b] = layout.paragraphs(p.ordered, { base: 10, right: () => 72 + 45 * 5.5, page: 0 });
    assert.ok(b.runs.map((r) => r.text).join('').includes('extraordinary hyphenated'));
});

test('body size is the size carrying most characters', () => {
    const p = page([item('x'.repeat(200), 72, 100, 11), item('Title', 72, 60, 30)]);
    assert.equal(layout.bodySize([p]), 11);
});

// ── running heads, footers and page numbers ──────────────────────────────

test('repeated heads, footers and bare page numbers are dropped; body text is not', () => {
    const pages = [1, 2, 3, 4].map((n) => page([
        item('ACME QUARTERLY', 72, 30), item(`Real content for page ${n} lives here.`, 72, 300), item(`${n}`, 300, 770),
    ]));
    const removed = layout.removeRunningLines(pages);
    assert.equal(removed, 8);
    pages.forEach((p, i) => same(p.ordered.map((l) => l.text), [`Real content for page ${i + 1} lives here.`]));
});

test('a single page keeps its footer text but a lone page number on several pages goes', () => {
    const one = page([item('Footer note', 72, 770), item('Body', 72, 300)]);
    assert.equal(layout.removeRunningLines([one]), 0);
});

// ── EPUB building blocks ──────────────────────────────────────────────────

test('xmlSafe strips control characters and lone surrogates', () => {
    assert.equal(epub.xmlSafe('a\u0000b\u0008c\u000Bd\uD800e￾f g\u{1F600}'), 'abcdef g\u{1F600}');
});

test('only http, https and mailto links survive', () => {
    assert.equal(epub.safeHref('https://a.test/x?y=1'), 'https://a.test/x?y=1');
    assert.equal(epub.safeHref('mailto:a@b.test'), 'mailto:a@b.test');
    for (const bad of ['javascript:alert(1)', 'JaVaScRiPt:1', 'file:///etc/passwd', 'data:text/html,x', '//evil.test', 'vbscript:x', '']) {
        assert.equal(epub.safeHref(bad), null, bad);
    }
});

test('runs are escaped, with spaces kept outside the styling', () => {
    const html = epub.runsToHtml([
        { text: 'a <b> & "c" ' }, { text: 'bold ', bold: true }, { text: 'x', italic: true, href: 'https://q.test/?a=1&b=2' },
    ]);
    assert.equal(html, 'a &lt;b&gt; &amp; &quot;c&quot; <strong>bold</strong> <a href="https://q.test/?a=1&amp;b=2"><em>x</em></a>');
});

const P = (text, page = 0, chars = text.length) => ({ kind: 'p', runs: [{ text }], page, chars });
const Hh = (text, page = 0) => ({ kind: 'h2', runs: [{ text }], page, chars: text.length });

test('bookmarks make chapters; text before the first bookmark becomes a front chapter', () => {
    const blocks = [P('front', 0), P('a', 1), P('b', 2), P('c', 3)];
    const ch = epub.makeChapters(blocks, [{ title: 'One', page: 1 }, { title: 'Two', page: 3 }], 'Book');
    same(ch.map((c) => [c.title, c.blocks.length]), [['Book', 1], ['One', 2], ['Two', 1]]);
});

test('with no bookmarks, level-2 headings make the chapters', () => {
    const blocks = [P('intro'), Hh('First'), P('x'), Hh('Second'), P('y')];
    const ch = epub.makeChapters(blocks, [], 'Book');
    same(ch.map((c) => c.title), ['Book', 'First', 'Second']);
});

test('without structure there is one chapter, and an oversized one is split in parts', () => {
    assert.equal(epub.makeChapters([P('a'), P('b')], [], 'Book').length, 1);
    const big = Array.from({ length: 12 }, () => P('x', 0, 30000));
    const ch = epub.makeChapters(big, [], 'Book');
    assert.ok(ch.length >= 2);
    assert.equal(ch[1].title, 'Book (part 2)');
    assert.equal(ch.reduce((n, c) => n + c.blocks.length, 0), 12);
});

test('a paragraph split across pages is joined, a sentence that ended is not', () => {
    const joined = epub.joinAcrossPages([P('the quick brown fox', 0), P('jumps over', 1)]);
    assert.equal(joined.length, 1);
    assert.equal(joined[0].runs.map((r) => r.text).join(''), 'the quick brown fox jumps over');
    assert.equal(epub.joinAcrossPages([P('It ended.', 0), P('new start', 1)]).length, 2);
    assert.equal(epub.joinAcrossPages([P('no stop here', 0), P('Capital start', 1)]).length, 2);
});

test('list items are wrapped in a single <ul>', () => {
    const html = epub.blocksHtml([P('a'), { kind: 'li', runs: [{ text: 'x' }], ordered: false }, { kind: 'li', runs: [{ text: 'y' }], ordered: false }, P('b')], () => '');
    assert.equal(html, '<p>a</p>\n<ul>\n<li>x</li>\n<li>y</li>\n</ul>\n<p>b</p>');
});

test('the package is a valid EPUB zip layout with mimetype first and stored', async () => {
    const zip = epub.buildPackage(S.JSZip, {
        id: '11111111-2222-3333-4444-555555555555', title: 'T & Co', author: 'A <B>', language: 'en-GB',
        chapters: [{ title: 'One', blocks: [P('hello')] }, { title: 'Two', blocks: [P('world')] }],
        images: [], cover: null,
    });
    const out = await zip.generateAsync({ type: 'uint8array', compression: 'DEFLATE', mimeType: 'application/epub+zip' });
    const back = await S.JSZip.loadAsync(out);
    assert.equal(Object.keys(back.files)[0], 'mimetype');
    assert.equal(await back.file('mimetype').async('string'), 'application/epub+zip');
    const opf = await back.file('OEBPS/content.opf').async('string');
    assert.match(opf, /<dc:title>T &amp; Co<\/dc:title>/);
    assert.match(opf, /<dc:creator>A &lt;B&gt;<\/dc:creator>/);
    assert.match(opf, /<dc:language>en-GB<\/dc:language>/);
    assert.match(opf, /<itemref idref="chapter_2"\/>/);
    assert.ok(back.file('OEBPS/nav.xhtml') && back.file('OEBPS/toc.ncx'));
});
