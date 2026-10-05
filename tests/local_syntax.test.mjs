// Every on-device script must parse. They are classic scripts loaded by <script> tags, so one syntax
// error (an unescaped apostrophe in a message, say) silently disables that file, and for
// ff-server-gate.js or ff-local.js it would disable every tool's consent gate or dispatcher.
import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import { readdirSync, readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const LOCAL = join(dirname(fileURLToPath(import.meta.url)), '..', 'static', 'local');
const files = readdirSync(LOCAL).filter((f) => f.endsWith('.js')).sort();

test('there are on-device scripts to check', () => {
    assert.ok(files.length >= 10, files.join(', '));
    for (const required of ['ff-local.js', 'ff-server-gate.js', 'ops-pdf-layout.js']) assert.ok(files.includes(required), required);
});

for (const f of files) {
    test(`${f} parses`, () => {
        assert.doesNotThrow(() => new vm.Script(readFileSync(join(LOCAL, f), 'utf8'), { filename: f }));
    });
}

test('every script the page needs is actually loaded by index.html', () => {
    const html = readFileSync(join(LOCAL, '..', 'index.html'), 'utf8');
    for (const f of files) assert.ok(html.includes('/static/local/' + f), `${f} is not referenced by index.html`);
});
