// Send & Receive: peer-to-peer text and file transfer.
//
// The sender pastes text and/or picks files and chooses an identifier (a mobile
// number, any phrase, or a generated code). The receiver types the same
// identifier. Our server (public/transfer_signaling.py) only introduces the two
// browsers: it holds an HMAC of the identifier, asks the sender to approve the
// receiver, and relays the WebRTC handshake. After that the bytes travel over
// an encrypted RTCDataChannel, browser to browser, or through our TURN relay
// when a direct path is impossible. Nothing is uploaded or stored.
//
// Wire format on the data channel (ordered, reliable):
//   strings  JSON control messages
//            sender   → {t:'manifest', items:[{name, type, size, kind}]}
//            receiver → {t:'ready'} | {t:'cancel'}
//            sender   → {t:'start', i} … binary chunks … {t:'end', i, sha256}
//            sender   → {t:'done'}
//   binary   the bytes of the item most recently started
//
// The SHA-256 is computed while streaming on both ends, so a corrupted or
// truncated item is reported instead of silently saved.
(function () {
    'use strict';

    var CHUNK = 64 * 1024;
    var HIGH_WATER = 4 * 1024 * 1024;
    var LOW_WATER = 1024 * 1024;
    // What we are willing to assemble in memory. The mobile app writes through
    // @capacitor/filesystem in base64, so it has a lower honest ceiling (see
    // mobile/app-assets/src/native-download.js MAX_NATIVE_DOWNLOAD_BYTES).
    var MEMORY_LIMIT = 500 * 1024 * 1024;
    var NATIVE_LIMIT = 120 * 1024 * 1024;
    var CONNECT_TIMEOUT_MS = 30000;
    var DEFAULT_TRANSFER_ORIGIN = 'https://www.forgefiles.org';

    // ── SHA-256 (incremental) ─────────────────────────────────────────────
    // crypto.subtle.digest() needs the whole input at once; a transfer may be
    // gigabytes, so hash chunk by chunk instead.
    var K = new Uint32Array([
        0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
        0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
        0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
        0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
        0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
        0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
        0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
        0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2
    ]);

    function Sha256() {
        this.h = new Uint32Array([0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a,
            0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19]);
        this.block = new Uint8Array(64);
        this.used = 0;
        this.length = 0;
        this.w = new Uint32Array(64);
    }

    Sha256.prototype._compress = function (b, off) {
        var w = this.w, h = this.h, i, t1, t2;
        for (i = 0; i < 16; i++) {
            w[i] = (b[off + 4 * i] << 24) | (b[off + 4 * i + 1] << 16) | (b[off + 4 * i + 2] << 8) | b[off + 4 * i + 3];
        }
        for (i = 16; i < 64; i++) {
            var x = w[i - 15], y = w[i - 2];
            var s0 = ((x >>> 7) | (x << 25)) ^ ((x >>> 18) | (x << 14)) ^ (x >>> 3);
            var s1 = ((y >>> 17) | (y << 15)) ^ ((y >>> 19) | (y << 13)) ^ (y >>> 10);
            w[i] = (w[i - 16] + s0 + w[i - 7] + s1) | 0;
        }
        var a = h[0], c = h[2], d = h[3], e = h[4], f = h[5], g = h[6], k = h[7], bb = h[1];
        for (i = 0; i < 64; i++) {
            t1 = (k + (((e >>> 6) | (e << 26)) ^ ((e >>> 11) | (e << 21)) ^ ((e >>> 25) | (e << 7)))
                + ((e & f) ^ (~e & g)) + K[i] + w[i]) | 0;
            t2 = ((((a >>> 2) | (a << 30)) ^ ((a >>> 13) | (a << 19)) ^ ((a >>> 22) | (a << 10)))
                + ((a & bb) ^ (a & c) ^ (bb & c))) | 0;
            k = g; g = f; f = e; e = (d + t1) | 0; d = c; c = bb; bb = a; a = (t1 + t2) | 0;
        }
        h[0] += a; h[1] += bb; h[2] += c; h[3] += d; h[4] += e; h[5] += f; h[6] += g; h[7] += k;
    };

    Sha256.prototype.update = function (bytes) {
        var data = bytes instanceof Uint8Array ? bytes : new Uint8Array(bytes);
        var pos = 0, n = data.length;
        this.length += n;
        if (this.used) {
            var take = Math.min(64 - this.used, n);
            this.block.set(data.subarray(0, take), this.used);
            this.used += take;
            pos = take;
            if (this.used < 64) return this;
            this._compress(this.block, 0);
            this.used = 0;
        }
        for (; pos + 64 <= n; pos += 64) this._compress(data, pos);
        if (pos < n) {
            this.block.set(data.subarray(pos), 0);
            this.used = n - pos;
        }
        return this;
    };

    Sha256.prototype.hex = function () {
        var bits = this.length * 8;
        var pad = new Uint8Array(((this.used < 56) ? 56 : 120) - this.used + 8);
        pad[0] = 0x80;
        var hi = Math.floor(bits / 0x100000000), lo = bits >>> 0;
        var end = pad.length;
        pad[end - 8] = hi >>> 24; pad[end - 7] = hi >>> 16; pad[end - 6] = hi >>> 8; pad[end - 5] = hi;
        pad[end - 4] = lo >>> 24; pad[end - 3] = lo >>> 16; pad[end - 2] = lo >>> 8; pad[end - 1] = lo;
        this.length -= pad.length;  // padding is not message length
        this.update(pad);
        var out = '';
        for (var i = 0; i < 8; i++) out += ('00000000' + this.h[i].toString(16)).slice(-8);
        return out;
    };

    // ── Small helpers ─────────────────────────────────────────────────────

    var CODE_WORDS = ('amber anchor aspen basil birch bison breeze cedar cherry clover comet coral ' +
        'crane daisy delta ember falcon fern finch forest frost garnet gecko ginger harbor hazel heron ' +
        'iris ivory jade jasper kayak koala lagoon lark lemon lilac lotus maple meadow mint moss nova ' +
        'oak ocean olive opal orbit otter panda pearl pebble pine plum polar quartz raven reef river ' +
        'robin ruby sage salmon sierra slate solar spruce stone storm swan teal tiger topaz tulip ' +
        'valley violet walnut willow wren zebra').split(' ');

    function randomInt(max, rng) {
        var buf = new Uint32Array(1);
        (rng || window.crypto).getRandomValues(buf);
        return buf[0] % max;
    }

    /** "maple-otter-42": easy to say out loud, at least 6 characters. */
    function generateCode(rng) {
        return CODE_WORDS[randomInt(CODE_WORDS.length, rng)] + '-' +
            CODE_WORDS[randomInt(CODE_WORDS.length, rng)] + '-' + (10 + randomInt(90, rng));
    }

    /** Mirrors transfer_signaling.normalize_identifier, for instant feedback. */
    function normalizeIdentifier(raw) {
        var text = String(raw || '').trim().toLowerCase().split(/\s+/).filter(Boolean).join(' ');
        if (/^[\d\s+().-]+$/.test(text)) {
            var digits = text.replace(/\D/g, '');
            if (digits.length >= 6) text = digits;
        }
        return text.length >= 6 && text.length <= 64 ? text : null;
    }

    function deviceLabel(ua, desktopApp) {
        if (desktopApp) return 'Forge Files for Windows';
        ua = String(ua || '');
        var browser = /Edg\//.test(ua) ? 'Edge' : /OPR\//.test(ua) ? 'Opera' : /Firefox\//.test(ua) ? 'Firefox'
            : /Chrome\//.test(ua) ? 'Chrome' : /Safari\//.test(ua) ? 'Safari' : 'Browser';
        var os = /iPhone/.test(ua) ? 'iPhone' : /iPad/.test(ua) ? 'iPad' : /Android/.test(ua) ? 'Android'
            : /Windows/.test(ua) ? 'Windows' : /Mac OS X|Macintosh/.test(ua) ? 'Mac'
                : /CrOS/.test(ua) ? 'ChromeOS' : /Linux/.test(ua) ? 'Linux' : 'device';
        return browser + ' on ' + os;
    }

    function formatBytes(n) {
        if (n < 1024) return n + ' B';
        var units = ['KB', 'MB', 'GB'], i = -1;
        do { n /= 1024; i++; } while (n >= 1024 && i < units.length - 1);
        return n.toFixed(n < 10 ? 1 : 0) + ' ' + units[i];
    }

    function isNativeApp() {
        try {
            return !!(window.Capacitor && window.Capacitor.isNativePlatform && window.Capacitor.isNativePlatform());
        } catch (e) { return false; }
    }

    function signalingUrl() {
        // The desktop app has no hub of its own (it is a local server on
        // 127.0.0.1); the mobile app's API_BASE is already the production site.
        var base = window.FF_TRANSFER_ORIGIN || (window.API_BASE || '') ||
            (window.FF_DESKTOP ? DEFAULT_TRANSFER_ORIGIN : window.location.origin);
        return base.replace(/^http/i, 'ws') + '/api/transfer/ws';
    }

    var ERRORS = {
        taken: 'That identifier is already waiting for a receiver. Pick a different one.',
        invalid: 'Use at least 6 characters, or a full mobile number.',
        not_found: 'Nothing is waiting under that identifier. Check it, and make sure the sender still has the page open.',
        busy: 'That sender is already busy with someone else. Try again in a moment.',
        rate_limited: 'Too many attempts. Wait a minute and try again.',
        expired: 'This request expired. Start again.',
        too_large: 'Something went wrong talking to the server. Start again.',
        denied: 'The sender declined the request.',
        peer_left: 'The other device disconnected.',
        connect: 'Could not connect the two devices. Check both are online and try again.',
        socket: 'Could not reach the Forge Files server. Check your connection and try again.',
        hash: 'A file arrived damaged and was discarded. Please send it again.',
        cancelled: 'The transfer was cancelled.'
    };

    // ── Signaling socket ──────────────────────────────────────────────────

    function Signal(onMessage, onClose) {
        var ws = new WebSocket(signalingUrl());
        var self = this, ping = null, queue = [];
        this.closed = false;
        ws.onopen = function () {
            queue.forEach(function (m) { ws.send(m); });
            queue = [];
            // Proxies drop idle sockets; Cloudflare after 100 s.
            ping = setInterval(function () { self.send({ t: 'ping' }); }, 25000);
        };
        ws.onmessage = function (ev) {
            var msg;
            try { msg = JSON.parse(ev.data); } catch (e) { return; }
            if (msg && msg.t !== 'pong') onMessage(msg);
        };
        ws.onclose = function () {
            clearInterval(ping);
            var was = self.closed;
            self.closed = true;
            if (!was) onClose();
        };
        this.send = function (msg) {
            var text = JSON.stringify(msg);
            if (ws.readyState === 1) ws.send(text);
            else if (ws.readyState === 0) queue.push(text);
        };
        this.close = function () {
            self.closed = true;
            clearInterval(ping);
            try { ws.close(); } catch (e) { }
        };
    }

    // ── Peer connection ───────────────────────────────────────────────────

    function Peer(iceServers, initiator, signal, handlers) {
        var pc = new RTCPeerConnection({ iceServers: iceServers || [] });
        var pendingCandidates = [];
        var self = this;
        this.pc = pc;
        this.channel = null;

        var timer = setTimeout(function () { handlers.fail('connect'); }, CONNECT_TIMEOUT_MS);
        function adopt(channel) {
            channel.binaryType = 'arraybuffer';
            channel.bufferedAmountLowThreshold = LOW_WATER;
            channel.onopen = function () { clearTimeout(timer); handlers.open(channel); };
            channel.onmessage = function (ev) { handlers.message(ev.data); };
            channel.onclose = function () { handlers.closed(); };
            self.channel = channel;
        }

        pc.onicecandidate = function (ev) {
            if (ev.candidate) signal.send({ t: 'signal', data: { candidate: ev.candidate.toJSON ? ev.candidate.toJSON() : ev.candidate } });
        };
        pc.onconnectionstatechange = function () {
            if (pc.connectionState === 'failed') handlers.fail('connect');
        };
        if (initiator) {
            adopt(pc.createDataChannel('forgefiles', { ordered: true }));
            pc.createOffer().then(function (offer) {
                return pc.setLocalDescription(offer);
            }).then(function () {
                signal.send({ t: 'signal', data: { sdp: pc.localDescription.toJSON ? pc.localDescription.toJSON() : pc.localDescription } });
            }).catch(function () { handlers.fail('connect'); });
        } else {
            pc.ondatachannel = function (ev) { adopt(ev.channel); };
        }

        this.onSignal = function (data) {
            if (!data) return;
            if (data.sdp) {
                pc.setRemoteDescription(data.sdp).then(function () {
                    pendingCandidates.forEach(function (c) { pc.addIceCandidate(c).catch(function () { }); });
                    pendingCandidates = [];
                    if (data.sdp.type === 'offer') {
                        return pc.createAnswer().then(function (answer) {
                            return pc.setLocalDescription(answer);
                        }).then(function () {
                            signal.send({ t: 'signal', data: { sdp: pc.localDescription.toJSON ? pc.localDescription.toJSON() : pc.localDescription } });
                        });
                    }
                }).catch(function () { handlers.fail('connect'); });
            } else if (data.candidate) {
                if (pc.remoteDescription) pc.addIceCandidate(data.candidate).catch(function () { });
                else pendingCandidates.push(data.candidate);
            }
        };
        this.close = function () {
            clearTimeout(timer);
            try { if (self.channel) self.channel.close(); } catch (e) { }
            try { pc.close(); } catch (e) { }
        };
    }

    // ── Sending ───────────────────────────────────────────────────────────

    function waitForDrain(channel) {
        if (channel.bufferedAmount <= HIGH_WATER) return Promise.resolve();
        return new Promise(function (resolve) {
            channel.addEventListener('bufferedamountlow', function once() {
                channel.removeEventListener('bufferedamountlow', once);
                resolve();
            });
        });
    }

    function readSlice(blob, start, end) {
        var part = blob.slice(start, end);
        if (part.arrayBuffer) return part.arrayBuffer();
        return new Promise(function (resolve, reject) {
            var r = new FileReader();
            r.onload = function () { resolve(r.result); };
            r.onerror = function () { reject(r.error); };
            r.readAsArrayBuffer(part);
        });
    }

    /** Stream every item over an open channel. `state.cancelled` aborts. */
    function sendItems(channel, items, state, onProgress) {
        var total = items.reduce(function (s, it) { return s + it.blob.size; }, 0);
        // 64 KiB fits every current browser's SCTP message limit.
        var chunk = CHUNK;
        var sent = 0;
        var index = 0;
        function nextItem() {
            if (state.cancelled) return Promise.reject(new Error('cancelled'));
            if (index >= items.length) {
                channel.send(JSON.stringify({ t: 'done' }));
                return Promise.resolve();
            }
            var item = items[index], hash = new Sha256(), offset = 0;
            channel.send(JSON.stringify({ t: 'start', i: index }));
            function pump() {
                if (state.cancelled) return Promise.reject(new Error('cancelled'));
                if (offset >= item.blob.size) {
                    channel.send(JSON.stringify({ t: 'end', i: index, sha256: hash.hex() }));
                    index++;
                    return nextItem();
                }
                var end = Math.min(offset + chunk, item.blob.size);
                return waitForDrain(channel).then(function () {
                    return readSlice(item.blob, offset, end);
                }).then(function (buf) {
                    hash.update(buf);
                    channel.send(buf);
                    sent += buf.byteLength;
                    offset = end;
                    onProgress(sent, total);
                    return pump();
                });
            }
            return pump();
        }
        return nextItem();
    }

    // ── Receiving ─────────────────────────────────────────────────────────

    /**
     * Reassembles items from channel messages. `sinkFor(i, meta)` returns
     * {write(Uint8Array) → Promise, close() → Promise<result>, abort()}.
     */
    function Receiver(manifest, sinkFor, callbacks) {
        var current = null, received = 0, writes = Promise.resolve();
        var total = manifest.items.reduce(function (s, it) { return s + it.size; }, 0);
        this.handle = function (data) {
            if (typeof data === 'string') {
                var msg;
                try { msg = JSON.parse(data); } catch (e) { return; }
                if (msg.t === 'start' && manifest.items[msg.i]) {
                    current = { i: msg.i, meta: manifest.items[msg.i], hash: new Sha256(), sink: sinkFor(msg.i, manifest.items[msg.i]) };
                } else if (msg.t === 'end' && current && current.i === msg.i) {
                    var item = current;
                    current = null;
                    writes = writes.then(function () {
                        if (item.hash.hex() !== msg.sha256) {
                            item.sink.abort();
                            callbacks.error('hash');
                            return;
                        }
                        return item.sink.close().then(function (result) { callbacks.item(item.i, item.meta, result); });
                    });
                } else if (msg.t === 'done') {
                    writes.then(function () { callbacks.done(); });
                } else if (msg.t === 'cancel') {
                    callbacks.error('cancelled');
                }
                return;
            }
            if (!current) return;
            var bytes = new Uint8Array(data);
            current.hash.update(bytes);
            received += bytes.length;
            var sink = current.sink;
            writes = writes.then(function () { return sink.write(bytes); });
            callbacks.progress(received, total);
        };
    }

    function memorySink(meta) {
        var parts = [];
        return {
            write: function (bytes) { parts.push(bytes); return Promise.resolve(); },
            close: function () { return Promise.resolve(new Blob(parts, { type: meta.type || 'application/octet-stream' })); },
            abort: function () { parts = []; }
        };
    }

    function fileSink(writable) {
        return {
            write: function (bytes) { return writable.write(bytes); },
            close: function () { return writable.close().then(function () { return null; }); },
            abort: function () { try { writable.abort(); } catch (e) { } }
        };
    }

    // ── UI ────────────────────────────────────────────────────────────────

    function el(tag, attrs, children) {
        var node = document.createElement(tag);
        Object.keys(attrs || {}).forEach(function (k) {
            if (k === 'text') node.textContent = attrs[k];
            else if (k === 'className') node.className = attrs[k];
            else if (k.indexOf('on') === 0) node.addEventListener(k.slice(2), attrs[k]);
            else node.setAttribute(k, attrs[k]);
        });
        (children || []).forEach(function (c) { if (c) node.appendChild(typeof c === 'string' ? document.createTextNode(c) : c); });
        return node;
    }

    function notify(message) {
        if (typeof window.ffNotify === 'function') window.ffNotify(message);
        else window.alert(message);
    }

    function wordsBadge(words) {
        return el('p', { className: 'transfer-words', 'aria-label': 'Confirmation words' },
            words.map(function (w) { return el('span', { text: w }); }));
    }

    function progressBar() {
        var bar = el('div', { className: 'transfer-progress', role: 'progressbar', 'aria-valuemin': '0', 'aria-valuemax': '100', 'aria-valuenow': '0' },
            [el('span')]);
        var label = el('p', { className: 'helper-text', text: 'Connecting…' });
        return {
            nodes: [bar, label],
            set: function (done, total) {
                var pct = total ? Math.floor(done * 100 / total) : 100;
                bar.firstChild.style.width = pct + '%';
                bar.setAttribute('aria-valuenow', String(pct));
                label.textContent = formatBytes(done) + ' of ' + formatBytes(total) + ' (' + pct + '%)';
            },
            text: function (t) { label.textContent = t; }
        };
    }

    // Desktop: the app's promise is that nothing leaves the computer unasked.
    function desktopConsent() {
        if (!window.FF_DESKTOP) return true;
        try { if (localStorage.getItem('ff_transfer_consent') === '1') return true; } catch (e) { }
        var ok = window.confirm('Send & Receive connects to forgefiles.org to find the other device. ' +
            'Your files then go straight to that device, encrypted, and are never stored on our servers. Continue?');
        if (ok) { try { localStorage.setItem('ff_transfer_consent', '1'); } catch (e) { } }
        return ok;
    }

    function SendFlow(root) {
        var files = [];
        var textArea = root.querySelector('#transfer-text');
        var input = root.querySelector('#transfer-file-input');
        var drop = root.querySelector('#transfer-drop-zone');
        var list = root.querySelector('#transfer-file-list');
        var idInput = root.querySelector('#transfer-send-id');
        var startBtn = root.querySelector('#transfer-send-start');
        var status = root.querySelector('#transfer-send-status');
        var session = null;

        function renderFiles() {
            list.textContent = '';
            files.forEach(function (f, i) {
                list.appendChild(el('li', {}, [
                    el('span', { text: f.name + ' · ' + formatBytes(f.size) }),
                    el('button', { type: 'button', className: 'secondary-btn', 'aria-label': 'Remove ' + f.name, text: '×',
                        onclick: function () { files.splice(i, 1); renderFiles(); } })
                ]));
            });
        }
        function addFiles(fileList) {
            Array.prototype.forEach.call(fileList || [], function (f) { files.push(f); });
            renderFiles();
        }
        input.addEventListener('change', function () { addFiles(input.files); input.value = ''; });
        drop.addEventListener('click', function () { input.click(); });
        drop.addEventListener('keydown', function (e) { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); input.click(); } });
        drop.addEventListener('dragover', function (e) { e.preventDefault(); drop.classList.add('dragover'); });
        drop.addEventListener('dragleave', function () { drop.classList.remove('dragover'); });
        drop.addEventListener('drop', function (e) { e.preventDefault(); drop.classList.remove('dragover'); addFiles(e.dataTransfer.files); });
        root.querySelector('#transfer-generate').addEventListener('click', function () { idInput.value = generateCode(); });

        function reset(message) {
            if (session) session.stop();
            session = null;
            startBtn.disabled = false;
            idInput.disabled = false;
            status.textContent = '';
            if (message) notify(message);
        }

        startBtn.addEventListener('click', function () {
            var items = [];
            var text = textArea.value;
            if (text.trim()) items.push({ name: 'Text', type: 'text/plain;charset=utf-8', kind: 'text', blob: new Blob([text], { type: 'text/plain;charset=utf-8' }) });
            files.forEach(function (f) { items.push({ name: f.name, type: f.type, kind: 'file', blob: f }); });
            if (!items.length) { notify('Paste some text or add at least one file to send.'); return; }
            var identifier = idInput.value;
            if (!normalizeIdentifier(identifier)) { notify(ERRORS.invalid); idInput.focus(); return; }
            if (!desktopConsent()) return;
            startBtn.disabled = true;
            idInput.disabled = true;
            session = startSending(identifier.trim(), items);
        });

        function startSending(identifier, items) {
            var state = { cancelled: false, peer: null, finished: false };
            var signal = new Signal(onMessage, function () {
                if (!state.finished && !state.cancelled && !state.peer) reset(ERRORS.socket);
            });
            signal.send({ t: 'host', id: identifier });
            var requests = el('div', { className: 'transfer-requests' });
            var bar = progressBar();

            function stop() {
                state.cancelled = true;
                if (state.peer && state.peer.channel && state.peer.channel.readyState === 'open') {
                    try { state.peer.channel.send(JSON.stringify({ t: 'cancel' })); } catch (e) { }
                }
                if (state.peer) state.peer.close();
                signal.close();
            }

            function showWaiting() {
                status.textContent = '';
                var link = shareLink(identifier);
                var qr = qrNode(link);
                status.appendChild(el('div', { className: 'transfer-card', 'aria-live': 'polite' }, [
                    el('h4', { text: 'Waiting for the receiver' }),
                    el('p', { className: 'helper-text', text: 'On the other device, open Send & Receive, choose Receive and enter:' }),
                    el('p', { className: 'transfer-code', text: identifier }),
                    qr,
                    el('div', { className: 'transfer-actions' }, [
                        el('button', { type: 'button', className: 'secondary-btn', text: 'Copy link', onclick: function () {
                            copyText(link).then(function () { notify('Link copied.'); });
                        } }),
                        el('button', { type: 'button', className: 'secondary-btn', text: 'Cancel', onclick: function () { reset(); } })
                    ]),
                    el('p', { className: 'helper-text', text: 'Keep this page open until the transfer finishes. Nothing is uploaded: your files go directly to the other device.' }),
                    requests
                ]));
            }

            function onMessage(msg) {
                if (msg.t === 'hosted') { showWaiting(); return; }
                if (msg.t === 'error') { reset(ERRORS[msg.code] || ERRORS.socket); return; }
                if (msg.t === 'request') {
                    var row = el('div', { className: 'transfer-request', 'data-rid': msg.rid }, [
                        el('p', { text: (msg.device || 'A device') + ' wants to receive. Ask them which words they see:' }),
                        wordsBadge(msg.words || []),
                        el('div', { className: 'transfer-actions' }, [
                            el('button', { type: 'button', className: 'primary-btn', text: 'Accept', onclick: function () {
                                signal.send({ t: 'approve', rid: msg.rid });
                            } }),
                            el('button', { type: 'button', className: 'secondary-btn', text: 'Decline', onclick: function () {
                                signal.send({ t: 'deny', rid: msg.rid });
                                row.remove();
                            } })
                        ])
                    ]);
                    requests.appendChild(row);
                    return;
                }
                if (msg.t === 'request_cancelled') {
                    var gone = requests.querySelector('[data-rid="' + String(msg.rid).replace(/"/g, '') + '"]');
                    if (gone) gone.remove();
                    return;
                }
                if (msg.t === 'paired') {
                    status.textContent = '';
                    status.appendChild(el('div', { className: 'transfer-card', 'aria-live': 'polite' }, [
                        el('h4', { text: 'Sending' })].concat(bar.nodes).concat([
                        el('button', { type: 'button', className: 'secondary-btn', text: 'Cancel', onclick: function () { reset(ERRORS.cancelled); } })])));
                    state.peer = new Peer(msg.iceServers, true, signal, {
                        open: function (channel) {
                            bar.text('Connected. Waiting for the receiver to accept the files…');
                            channel.send(JSON.stringify({ t: 'manifest', items: items.map(function (it) {
                                return { name: it.name, type: it.type, size: it.blob.size, kind: it.kind };
                            }) }));
                        },
                        message: function (data) {
                            var m;
                            try { m = JSON.parse(data); } catch (e) { return; }
                            if (m.t === 'ready') {
                                sendItems(state.peer.channel, items, state, bar.set).then(function () {
                                    bar.text('Sent. Waiting for the receiver to finish saving…');
                                }).catch(function () { if (!state.cancelled) reset(ERRORS.connect); });
                            } else if (m.t === 'received') {
                                state.finished = true;
                                showDone(items.length);
                            } else if (m.t === 'cancel') {
                                reset(ERRORS.cancelled);
                            }
                        },
                        closed: function () { if (!state.finished && !state.cancelled) reset(ERRORS.peer_left); },
                        fail: function (code) { if (!state.finished && !state.cancelled) reset(ERRORS[code]); }
                    });
                    return;
                }
                if (msg.t === 'signal' && state.peer) { state.peer.onSignal(msg.data); return; }
                if (msg.t === 'peer_left' && !state.finished) reset(ERRORS.peer_left);
            }

            function showDone(count) {
                signal.close();
                if (state.peer) state.peer.close();
                status.textContent = '';
                status.appendChild(el('div', { className: 'transfer-card', 'aria-live': 'polite' }, [
                    el('h4', { text: 'Delivered' }),
                    el('p', { text: count === 1 ? 'The receiver has your item.' : 'The receiver has all ' + count + ' items.' }),
                    el('button', { type: 'button', className: 'primary-btn', text: 'Send something else', onclick: function () {
                        textArea.value = '';
                        files = [];
                        renderFiles();
                        reset();
                    } })
                ]));
                startBtn.disabled = false;
                idInput.disabled = false;
                session = null;
            }

            return { stop: stop };
        }
    }

    function shareLink(identifier) {
        // The identifier goes in the fragment, which browsers never send to a
        // server: a phone number must not end up in access logs or analytics.
        var origin = window.FF_TRANSFER_ORIGIN || (window.API_BASE || '') ||
            (window.FF_DESKTOP ? DEFAULT_TRANSFER_ORIGIN : window.location.origin);
        return origin + '/?tool=transfer&op=receive-files#code=' + encodeURIComponent(identifier);
    }

    function qrNode(text) {
        if (typeof window.qrcode !== 'function') return null;
        try {
            var qr = window.qrcode(0, 'M');
            qr.addData(text);
            qr.make();
            var wrap = el('div', { className: 'transfer-qr', role: 'img', 'aria-label': 'QR code for the receive link' });
            // createSvgTag builds markup from the QR matrix only; no user text is interpolated.
            wrap.innerHTML = qr.createSvgTag({ cellSize: 4, margin: 2, scalable: true });
            return wrap;
        } catch (e) { return null; }
    }

    function copyText(text) {
        if (navigator.clipboard && navigator.clipboard.writeText) return navigator.clipboard.writeText(text);
        var ta = el('textarea', {});
        ta.value = text;
        document.body.appendChild(ta);
        ta.select();
        try { document.execCommand('copy'); } catch (e) { }
        ta.remove();
        return Promise.resolve();
    }

    function ReceiveFlow(root) {
        var idInput = root.querySelector('#transfer-receive-id');
        var startBtn = root.querySelector('#transfer-receive-start');
        var status = root.querySelector('#transfer-receive-status');
        var session = null;
        var urls = [];

        function reset(message) {
            if (session) session.stop();
            session = null;
            startBtn.disabled = false;
            idInput.disabled = false;
            status.textContent = '';
            if (message) notify(message);
        }

        function begin() {
            var identifier = idInput.value;
            if (!normalizeIdentifier(identifier)) { notify(ERRORS.invalid); idInput.focus(); return; }
            if (!desktopConsent()) return;
            urls.forEach(function (u) { URL.revokeObjectURL(u); });
            urls = [];
            startBtn.disabled = true;
            idInput.disabled = true;
            session = startReceiving(identifier.trim());
        }
        startBtn.addEventListener('click', begin);
        idInput.addEventListener('keydown', function (e) { if (e.key === 'Enter') { e.preventDefault(); begin(); } });

        this.prefill = function (code) { if (code && !session) idInput.value = code; };

        function startReceiving(identifier) {
            var state = { cancelled: false, peer: null, finished: false, receiver: null };
            var signal = new Signal(onMessage, function () {
                if (!state.finished && !state.cancelled && !state.peer) reset(ERRORS.socket);
            });
            signal.send({ t: 'join', id: identifier, device: deviceLabel(navigator.userAgent, !!window.FF_DESKTOP) });
            var bar = progressBar();
            var results = el('div', { className: 'transfer-results' });

            function stop() {
                state.cancelled = true;
                if (state.peer && state.peer.channel && state.peer.channel.readyState === 'open') {
                    try { state.peer.channel.send(JSON.stringify({ t: 'cancel' })); } catch (e) { }
                }
                if (state.peer) state.peer.close();
                signal.close();
            }

            function card(children) {
                status.textContent = '';
                status.appendChild(el('div', { className: 'transfer-card', 'aria-live': 'polite' }, children));
            }

            function onMessage(msg) {
                if (msg.t === 'error' || msg.t === 'denied') { reset(ERRORS[msg.code || 'denied'] || ERRORS.socket); return; }
                if (msg.t === 'waiting') {
                    card([
                        el('h4', { text: 'Waiting for the sender to accept' }),
                        el('p', { className: 'helper-text', text: 'Tell the sender these words so they know it is you:' }),
                        wordsBadge(msg.words || []),
                        el('button', { type: 'button', className: 'secondary-btn', text: 'Cancel', onclick: function () { reset(); } })
                    ]);
                    return;
                }
                if (msg.t === 'approved') {
                    card([el('h4', { text: 'Connecting…' })].concat(bar.nodes));
                    state.peer = new Peer(msg.iceServers, false, signal, {
                        open: function () { bar.text('Connected. Waiting for the list of items…'); },
                        message: function (data) {
                            if (state.receiver) { state.receiver.handle(data); return; }
                            var m;
                            try { m = JSON.parse(data); } catch (e) { return; }
                            if (m.t === 'manifest' && Array.isArray(m.items)) showManifest(m);
                            else if (m.t === 'cancel') reset(ERRORS.cancelled);
                        },
                        closed: function () { if (!state.finished && !state.cancelled) reset(ERRORS.peer_left); },
                        fail: function (code) { if (!state.finished && !state.cancelled) reset(ERRORS[code]); }
                    });
                    return;
                }
                if (msg.t === 'signal' && state.peer) { state.peer.onSignal(msg.data); return; }
                if (msg.t === 'peer_left' && !state.finished && !state.receiver) reset(ERRORS.peer_left);
            }

            function showManifest(manifest) {
                var items = manifest.items.map(function (it) {
                    return {
                        name: String(it.name || 'file').slice(0, 200),
                        type: String(it.type || ''),
                        size: Math.max(0, Number(it.size) || 0),
                        kind: it.kind === 'text' ? 'text' : 'file'
                    };
                });
                manifest = { items: items };
                var limit = isNativeApp() ? NATIVE_LIMIT : MEMORY_LIMIT;
                var canStream = typeof window.showSaveFilePicker === 'function' && !isNativeApp();
                var large = items.filter(function (it) { return it.size > limit; });
                if (large.length && (!canStream || large.length > 1)) {
                    reset(canStream
                        ? 'Ask the sender to send files larger than ' + formatBytes(limit) + ' one at a time.'
                        : 'A file is larger than ' + formatBytes(limit) + ', too big to receive in this browser. Try Chrome or Edge on a computer.');
                    return;
                }
                var total = items.reduce(function (s, it) { return s + it.size; }, 0);
                card([
                    el('h4', { text: 'Incoming: ' + items.length + (items.length === 1 ? ' item' : ' items') + ' · ' + formatBytes(total) }),
                    el('ul', { className: 'transfer-file-list' }, items.map(function (it) {
                        return el('li', { text: (it.kind === 'text' ? 'Text' : it.name) + ' · ' + formatBytes(it.size) });
                    })),
                    el('div', { className: 'transfer-actions' }, [
                        el('button', { type: 'button', className: 'primary-btn', text: 'Accept', onclick: function () { accept(manifest, large[0]); } }),
                        el('button', { type: 'button', className: 'secondary-btn', text: 'Decline', onclick: function () { reset(); } })
                    ])
                ]);
            }

            function accept(manifest, large) {
                // The save dialog needs this click's user activation, so it is
                // opened here, before any bytes arrive.
                var picked = large
                    ? window.showSaveFilePicker({ suggestedName: large.name }).then(function (handle) { return handle.createWritable(); })
                    : Promise.resolve(null);
                picked.then(function (writable) {
                    card([el('h4', { text: 'Receiving' })].concat(bar.nodes).concat([results,
                        el('button', { type: 'button', className: 'secondary-btn', text: 'Cancel', onclick: function () { reset(ERRORS.cancelled); } })]));
                    state.receiver = new Receiver(manifest, function (i, meta) {
                        return meta === large && writable ? fileSink(writable) : memorySink(meta);
                    }, {
                        progress: bar.set,
                        item: function (i, meta, blob) { showItem(meta, blob); },
                        error: function (code) { reset(ERRORS[code]); },
                        done: function () {
                            state.finished = true;
                            try { state.peer.channel.send(JSON.stringify({ t: 'received' })); } catch (e) { }
                            bar.text('All items received and verified.');
                            var heading = status.querySelector('.transfer-card > h4');
                            if (heading) heading.textContent = 'Received';
                            setTimeout(function () { signal.close(); if (state.peer) state.peer.close(); }, 1000);
                            var cancelBtn = status.querySelector('.transfer-card > .secondary-btn');
                            if (cancelBtn) cancelBtn.remove();
                            status.querySelector('.transfer-card').appendChild(el('button', {
                                type: 'button', className: 'secondary-btn', text: 'Receive something else',
                                onclick: function () { session = null; reset(); }
                            }));
                            startBtn.disabled = false;
                            idInput.disabled = false;
                        }
                    });
                    state.peer.channel.send(JSON.stringify({ t: 'ready' }));
                }).catch(function () { /* save dialog dismissed: stay on the manifest */ });
            }

            function showItem(meta, blob) {
                if (meta.kind === 'text' && blob) {
                    blob.text().then(function (text) {
                        var ta = el('textarea', { className: 'transfer-text-out', readonly: 'readonly', 'aria-label': 'Received text', rows: '6' });
                        ta.value = text;
                        results.appendChild(el('div', { className: 'transfer-result' }, [ta,
                            el('button', { type: 'button', className: 'primary-btn', text: 'Copy text', onclick: function () {
                                copyText(text).then(function () { notify('Copied.'); });
                            } })]));
                    });
                    return;
                }
                if (!blob) {
                    results.appendChild(el('p', { className: 'transfer-result', text: meta.name + ' saved.' }));
                    return;
                }
                var url = URL.createObjectURL(blob);
                urls.push(url);
                // .download-btn: the mobile app's native-download overlay takes
                // these over and saves through the share sheet.
                results.appendChild(el('div', { className: 'transfer-result' }, [
                    el('a', { className: 'primary-btn download-btn', href: url, download: meta.name, text: 'Save ' + meta.name + ' (' + formatBytes(meta.size) + ')' })
                ]));
            }

            return { stop: stop };
        }
    }

    // ── Wiring ────────────────────────────────────────────────────────────

    function init() {
        var root = document.getElementById('transfer-page');
        if (!root || !('RTCPeerConnection' in window) || !('WebSocket' in window)) {
            if (root) {
                var warn = root.querySelector('#transfer-unsupported');
                if (warn) warn.hidden = false;
            }
            return;
        }
        var sendArea = root.querySelector('#transfer-send-area');
        var receiveArea = root.querySelector('#transfer-receive-area');
        new SendFlow(root);
        var receive = new ReceiveFlow(root);

        function open(which) {
            var card = root.querySelector(which === 'send' ? '#send-files-btn' : '#receive-files-btn');
            if (typeof window.ffSelectActionCard === 'function') window.ffSelectActionCard(card);
            sendArea.classList.toggle('hidden', which !== 'send');
            receiveArea.classList.toggle('hidden', which !== 'receive');
            if (which === 'receive') {
                var m = /(?:^|[#&])code=([^&]+)/.exec(window.location.hash || '');
                if (m) {
                    try { receive.prefill(decodeURIComponent(m[1])); } catch (e) { }
                }
                var input = root.querySelector('#transfer-receive-id');
                if (input) input.focus();
            }
        }
        root.querySelector('#send-files-btn').addEventListener('click', function () { open('send'); });
        root.querySelector('#receive-files-btn').addEventListener('click', function () { open('receive'); });
        ['#send-files-btn', '#receive-files-btn'].forEach(function (sel) {
            root.querySelector(sel).addEventListener('keydown', function (e) {
                if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); e.currentTarget.click(); }
            });
        });
    }

    window.FFTransfer = {
        Sha256: Sha256,
        Receiver: Receiver,
        sendItems: sendItems,
        generateCode: generateCode,
        normalizeIdentifier: normalizeIdentifier,
        deviceLabel: deviceLabel,
        formatBytes: formatBytes,
        shareLink: shareLink
    };

    // index.html loads this after the page markup and before script.js, whose
    // deep-link bootstrap clicks the Send/Receive card straight away; the
    // listeners have to exist by then, so don't wait for DOMContentLoaded.
    if (typeof document !== 'undefined' && document.addEventListener) {
        if (document.getElementById('transfer-page') || document.readyState !== 'loading') init();
        else document.addEventListener('DOMContentLoaded', init);
    }
})();
