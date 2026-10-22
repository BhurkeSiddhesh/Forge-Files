import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import { createHash, randomBytes } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const STATIC = join(dirname(fileURLToPath(import.meta.url)), '..', 'static');

function load(extra = {}) {
    const s = {
        console, Blob, Uint8Array, Uint32Array, ArrayBuffer, Promise, setTimeout, clearTimeout,
        TextEncoder, TextDecoder, crypto: globalThis.crypto,
        location: { origin: 'https://www.forgefiles.org', hash: '' },
        ...extra,
    };
    s.window = s;
    vm.createContext(s);
    vm.runInContext(readFileSync(join(STATIC, 'local', 'transfer.js'), 'utf8'), s);
    return s.FFTransfer;
}

const T = load();
const sha = (buf) => createHash('sha256').update(buf).digest('hex');

test('SHA-256 matches known vectors', () => {
    assert.equal(new T.Sha256().hex(), 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855');
    assert.equal(new T.Sha256().update(new TextEncoder().encode('abc')).hex(),
        'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad');
    const longer = new TextEncoder().encode('abcdbcdecdefdefgefghfghighijhijkijkljklmklmnlmnomnopnopq');
    assert.equal(new T.Sha256().update(longer).hex(), '248d6a61d20638b8e5c026930c3e6039a33ce45964ff2167f6ecedd419db06c1');
});

test('SHA-256 is identical however the input is chunked', () => {
    for (const size of [0, 1, 55, 56, 63, 64, 65, 119, 120, 1000, 200003]) {
        const data = randomBytes(size);
        const h = new T.Sha256();
        let i = 0;
        while (i < size) {
            const step = 1 + Math.floor(Math.random() * 97);
            h.update(new Uint8Array(data.subarray(i, i + step)));
            i += step;
        }
        assert.equal(h.hex(), sha(data), `size ${size}`);
    }
});

// A data channel pair in memory: what one end sends, the other receives.
function channelTo(receiver, corrupt) {
    return {
        bufferedAmount: 0,
        send(data) {
            if (typeof data === 'string') return receiver.handle(data);
            const copy = new Uint8Array(data.slice(0));
            if (corrupt && copy.length) copy[0] ^= 1;
            receiver.handle(copy.buffer);
        },
        addEventListener() {}, removeEventListener() {},
    };
}

async function roundTrip(items, corrupt) {
    const manifest = { items: items.map((it) => ({ name: it.name, type: it.type, size: it.blob.size, kind: it.kind })) };
    const got = [];
    const outcome = await new Promise((resolve) => {
        const receiver = new T.Receiver(manifest, (_i, meta) => {
            const parts = [];
            return {
                write: async (bytes) => { parts.push(bytes); },
                close: async () => new Blob(parts, { type: meta.type }),
                abort() {},
            };
        }, {
            progress() {},
            item: (i, meta, blob) => got.push({ i, meta, blob }),
            error: (code) => resolve({ error: code }),
            done: () => resolve({ ok: true }),
        });
        const channel = channelTo(receiver, corrupt);
        T.sendItems(channel, items, { cancelled: false }, () => {});
    });
    return { outcome, got };
}

test('text and files arrive intact and in order', async () => {
    const big = randomBytes(300 * 1024 + 17);
    const items = [
        { name: 'Text', type: 'text/plain', kind: 'text', blob: new Blob(['héllo — नमस्ते'], { type: 'text/plain' }) },
        { name: 'photo.jpg', type: 'image/jpeg', kind: 'file', blob: new Blob([big], { type: 'image/jpeg' }) },
        { name: 'empty.pdf', type: 'application/pdf', kind: 'file', blob: new Blob([]) },
    ];
    const { outcome, got } = await roundTrip(items);
    assert.deepEqual(outcome, { ok: true });
    assert.equal(got.length, 3);
    assert.equal(await got[0].blob.text(), 'héllo — नमस्ते');
    assert.equal(sha(Buffer.from(await got[1].blob.arrayBuffer())), sha(big));
    assert.equal(got[2].blob.size, 0);
});

test('bytes corrupted in transit are rejected, not saved', async () => {
    const items = [{ name: 'a.bin', type: '', kind: 'file', blob: new Blob([randomBytes(5000)]) }];
    const { outcome, got } = await roundTrip(items, true);
    assert.deepEqual(outcome, { error: 'hash' });
    assert.equal(got.length, 0);
});

test('a hash mismatch from the sender reports an error', async () => {
    const manifest = { items: [{ name: 'x', type: '', size: 3, kind: 'file' }] };
    const result = await new Promise((resolve) => {
        const r = new T.Receiver(manifest, () => ({ write: async () => {}, close: async () => new Blob([]), abort() {} }), {
            progress() {}, item: () => resolve('saved'), error: resolve, done() {},
        });
        r.handle(JSON.stringify({ t: 'start', i: 0 }));
        r.handle(new Uint8Array([1, 2, 3]).buffer);
        r.handle(JSON.stringify({ t: 'end', i: 0, sha256: '0'.repeat(64) }));
    });
    assert.equal(result, 'hash');
});

test('the memory limit applies to the items combined, not to each one', () => {
    const MB = 1024 * 1024;
    const ten = Array.from({ length: 10 }, (_, i) => ({ name: `f${i}`, size: 100 * MB }));
    assert.equal(T.memoryPlan(ten, 120 * MB, false).ok, false);
    // Streaming saves only the largest; the other nine still exceed the limit.
    assert.equal(T.memoryPlan(ten, 500 * MB, true).ok, false);
    const fits = [{ name: 'a', size: 50 * MB }, { name: 'b', size: 60 * MB }];
    const small = T.memoryPlan(fits, 120 * MB, false);
    assert.equal(small.ok, true);
    assert.equal(small.streamed, null);
    assert.equal(small.inMemory, 110 * MB);
    const oneBig = [{ name: 'movie', size: 4000 * MB }, { name: 'note', size: 1 }];
    const plan = T.memoryPlan(oneBig, 500 * MB, true);
    assert.equal(plan.ok, true);
    assert.equal(plan.streamed.name, 'movie');
});

test('cancelling a receiver aborts the open file and runs no queued work', async () => {
    let aborted = false, saved = false, done = false;
    const manifest = { items: [{ name: 'x', type: '', size: 6, kind: 'file' }] };
    const r = new T.Receiver(manifest, () => ({
        write: () => new Promise((res) => setTimeout(res, 20)),
        close: async () => { saved = true; return null; },
        abort() { aborted = true; },
    }), { progress() {}, item() {}, error() {}, done: () => { done = true; } });
    r.handle(JSON.stringify({ t: 'start', i: 0 }));
    r.handle(new Uint8Array([1, 2, 3]).buffer);
    r.cancel();
    r.handle(new Uint8Array([4, 5, 6]).buffer);
    r.handle(JSON.stringify({ t: 'end', i: 0, sha256: 'x' }));
    r.handle(JSON.stringify({ t: 'done' }));
    await new Promise((res) => setTimeout(res, 80));
    assert.equal(aborted, true);
    assert.equal(saved, false);
    assert.equal(done, false);
});

test('identifier normalization matches the server', () => {
    assert.equal(T.normalizeIdentifier('+91 98200-12345'), '919820012345');
    assert.equal(T.normalizeIdentifier('(022) 555 0199'), '0225550199');
    assert.equal(T.normalizeIdentifier('  Blue   TIGER '), 'blue tiger');
    assert.equal(T.normalizeIdentifier('short'), null);
    assert.equal(T.normalizeIdentifier('x'.repeat(65)), null);
});

test('generated codes are valid identifiers', () => {
    for (let i = 0; i < 50; i++) {
        const code = T.generateCode();
        assert.match(code, /^[a-z]+-[a-z]+-\d{2}$/);
        assert.equal(T.normalizeIdentifier(code), code);
    }
});

test('device labels', () => {
    assert.equal(T.deviceLabel('Mozilla/5.0 (Linux; Android 14) AppleWebKit Chrome/130 Mobile Safari/537.36'), 'Chrome on Android');
    assert.equal(T.deviceLabel('Mozilla/5.0 (iPhone; CPU iPhone OS 17_0) AppleWebKit Version/17.0 Mobile Safari/604.1'), 'Safari on iPhone');
    assert.equal(T.deviceLabel('Mozilla/5.0 (Windows NT 10.0) Chrome/130 Safari/537.36 Edg/130'), 'Edge on Windows');
    assert.equal(T.deviceLabel('anything', true), 'Forge Files for Windows');
});

test('share link keeps the identifier out of what the server sees', () => {
    const link = T.shareLink('+91 98200 12345');
    const url = new URL(link);
    assert.equal(url.origin, 'https://www.forgefiles.org');
    assert.equal(url.search, '?tool=transfer&op=receive-files');
    assert.ok(!url.search.includes('98200'));
    assert.ok(url.hash.includes('98200'));
});

test('the desktop app signals through the website', () => {
    const t = load({ FF_DESKTOP: true, location: { origin: 'http://127.0.0.1:8765', hash: '' } });
    assert.ok(t.shareLink('maple-otter-42').startsWith('https://www.forgefiles.org/'));
});

// Sender and receiver wired both ways: data forward, acks back.
async function flowControlled(size, sinkWrite, holdUntilSenderIdle = false) {
    const items = [{ name: 'big.bin', type: '', kind: 'file', blob: new Blob([new Uint8Array(size)]) }];
    const manifest = { items: [{ name: 'big.bin', type: '', size, kind: 'file' }] };
    const state = { cancelled: false };
    let sent = 0, written = 0, maxPending = 0, aborted = false;
    // Writes stay blocked until the sender has produced nothing for 100 ms,
    // i.e. until it has either sent everything or stopped itself.
    let gate = Promise.resolve();
    if (holdUntilSenderIdle) {
        gate = new Promise((open) => {
            let last = -1;
            const poll = setInterval(() => {
                if (sent === last) { clearInterval(poll); open(); }
                last = sent;
            }, 100);
        });
    }
    const outcome = await new Promise((resolve) => {
        const receiver = new T.Receiver(manifest, () => ({
            write: async (bytes) => { await gate; await sinkWrite(bytes); written += bytes.length; },
            close: async () => null,
            abort() { aborted = true; },
        }), {
            progress() {},
            ack: (n) => setTimeout(() => T.ackReceived(state, n), 0),
            item() {},
            error: (code) => { state.cancelled = true; T.ackReceived(state, 0); resolve({ error: code }); },
            done: () => resolve({ ok: true }),
        });
        const channel = {
            bufferedAmount: 0,
            send(data) {
                if (typeof data !== 'string') {
                    sent += data.byteLength;
                    maxPending = Math.max(maxPending, sent - written);
                }
                receiver.handle(typeof data === 'string' ? data : data.slice(0));
            },
            addEventListener() {}, removeEventListener() {},
        };
        T.sendItems(channel, items, state, () => {}).catch(() => {});
    });
    return { outcome, maxPending, aborted };
}

test('a stalled receiver stops the sender at the ack window', async () => {
    // Nothing is written until the sender goes idle. Without flow control it
    // pushes all 24 MB into the receiver's queue; with it, it stops at WINDOW.
    const { outcome, maxPending } = await flowControlled(24 * 1024 * 1024, async () => {}, true);
    assert.deepEqual(outcome, { ok: true });
    assert.equal(T.WINDOW, 16 * 1024 * 1024);
    assert.ok(maxPending <= 16 * 1024 * 1024 + 64 * 1024, `pending peaked at ${maxPending}`);
});

test('a failing sink ends the transfer with an error and aborts the file', async () => {
    let calls = 0;
    const failing = async () => { if (++calls === 3) throw new Error('disk full'); };
    const { outcome, aborted } = await flowControlled(1024 * 1024, failing);
    assert.deepEqual(outcome, { error: 'write' });
    assert.equal(aborted, true);
});

test('index.html removes the share code from the address before analytics loads', () => {
    const html = readFileSync(join(STATIC, 'index.html'), 'utf8');
    const strip = html.indexOf('window.FF_TRANSFER_CODE=');
    assert.ok(strip > 0);
    for (const tracker of ['{{CF_ANALYTICS}}', '{{GA_ANALYTICS}}', 'datafa.st', '/static/script.js']) {
        assert.ok(strip < html.indexOf(tracker), tracker);
    }
    const source = html.slice(html.lastIndexOf('<script>', strip) + 8, html.indexOf('</script>', strip));
    const replaced = [];
    const s = {
        location: { hash: '#code=%2B91%2098200%2012345', pathname: '/', search: '?tool=transfer&op=receive-files' },
        history: { state: null, replaceState: (_st, _t, url) => replaced.push(url) },
    };
    s.window = s;
    vm.createContext(s);
    vm.runInContext(source, s);
    assert.equal(s.FF_TRANSFER_CODE, '+91 98200 12345');
    assert.deepEqual(replaced, ['/?tool=transfer&op=receive-files']);
});
