// On-device processing: the dispatcher.
//
// Historically every tool posted its file to the FastAPI backend and got back a
// `{filename, message, download_token}` JSON body plus a `/api/download/<token>`
// URL. That is still true for the conversions that genuinely need a server
// (anything LibreOffice-backed, pdf2docx, OCR, compression). But a large slice
// of the tool surface — image transforms and structural PDF edits — is a few
// hundred lines of browser API away, and shipping those to the device buys
// three things the server cannot:
//
//   1. Privacy. The file never leaves the phone or the browser tab. For a file
//      conversion site that is the single most common user objection.
//   2. Speed. On a phone, uploading 40 MB over cellular dominates the cost of
//      the conversion itself.
//   3. Headroom. The backend runs one uvicorn worker on one VM (see
//      public/main.py's worker guard) and defends itself with a 5 req/min
//      heavy-endpoint rate limit. Every operation that moves here is one that
//      no longer competes for that budget.
//
// Design rules, in priority order:
//
//   * **Never regress.** `ffProcess()` returns a real `Response`, so callers
//     keep their existing `response.ok` / `response.json()` / error handling
//     untouched. If no local handler is registered for a path, or the handler
//     throws anything that isn't a deliberate validation error, the user is
//     asked whether the server may take the file. A bug in a handler costs a
//     prompt, not a broken tool and never a silent upload.
//   * **Match the server byte-for-contract.** Handlers return the same
//     `filename` (`<stem>_forgefiles.org.<ext>`) and the same `message` strings
//     the Python endpoints return, so the UI needs no special-casing and a
//     result is indistinguishable from a server-produced one.
//   * **Refuse rather than guess.** Password-protected PDFs, formats the
//     browser cannot decode, and anything else outside a handler's competence
//     raise `FFLocalUnsupported`. That never uploads on its own: the user is
//     asked first (ff-server-gate.js), and declining stops the conversion. Only
//     after an explicit yes does the server, which still has
//     pikepdf/PyMuPDF/Pillow, get the file.
//
// Not ported (deliberately, they need the server): everything under
// /api/word/*, /api/ppt/*, /api/excel/*, /api/pdf/convert-to-word*,
// /api/pdf/to-excel, /api/pdf/to-pptx, /api/pdf/compress, /api/pdf/repair,
// /api/image/heic-to-jpeg (browsers other than Safari can't decode HEIC), and
// /api/workflow/execute (chains steps server-side).
(function () {
    'use strict';

    // ── Errors ────────────────────────────────────────────────────────────
    //
    // The distinction matters: a FFLocalError is the user's input being wrong
    // and is reported straight to them (uploading the file would only earn the
    // same rejection from the server). Anything else means *we* failed, and the
    // server gets a chance to succeed where we didn't.

    /** The caller's input is invalid. Reported as a 400, no server fallback. */
    function FFLocalError(message) {
        this.name = 'FFLocalError';
        this.message = message;
    }
    FFLocalError.prototype = Object.create(Error.prototype);

    /** Outside this handler's competence. Asks the user before using the server. */
    function FFLocalUnsupported(message, code) {
        this.name = 'FFLocalUnsupported';
        this.message = message || 'not supported on-device';
        // Bounded reason code (see ff-server-gate.js CODES) used for the consent
        // dialog wording. The message itself is never shown to the user.
        this.code = code || null;
    }
    FFLocalUnsupported.prototype = Object.create(Error.prototype);

    // ── Registry ──────────────────────────────────────────────────────────

    var HANDLERS = {};

    /**
     * Register an on-device handler for an API path.
     * @param {string} path e.g. '/api/pdf/merge'
     * @param {function(FormData): Promise<object>} handler resolving to the
     *        same shape the Python endpoint returns, minus `download_token`:
     *        `{blob, filename, message}`.
     */
    function register(path, handler) {
        HANDLERS[path] = handler;
    }

    // ── Feature gate ──────────────────────────────────────────────────────

    var CAPABLE = (function () {
        try {
            return typeof Blob === 'function' &&
                typeof Promise === 'function' &&
                typeof FormData === 'function' &&
                typeof URL !== 'undefined' && typeof URL.createObjectURL === 'function' &&
                typeof document.createElement('canvas').toBlob === 'function';
        } catch (e) {
            return false;
        }
    })();

    /**
     * On-device processing is on by default wherever the browser can do it.
     * Two escape hatches, both honoured on every call so they can be flipped
     * from the console while reproducing a bug:
     *   window.FF_LOCAL = false          — this page load
     *   localStorage.ff_local = '0'      — sticky, this browser
     */
    function enabled() {
        if (!CAPABLE) return false;
        if (window.FF_LOCAL === false) return false;
        try {
            if (window.localStorage && localStorage.getItem('ff_local') === '0') return false;
        } catch (e) { /* private mode / blocked storage — treat as unset */ }
        return true;
    }

    // ── Result registry ───────────────────────────────────────────────────
    //
    // Server results are addressed by an opaque `download_token`. Local results
    // need to be addressable the same way so `updateDownloadLink()` stays one
    // function. Tokens are prefixed so the two can never be confused, and the
    // blob is held here until it is superseded.

    var TOKEN_PREFIX = 'ffLocal:';
    var results = {};
    var seq = 0;

    /** Register a finished local result; mirrors main.py's download_fields(). */
    function publish(blob, filename) {
        var token = TOKEN_PREFIX + (++seq) + '.' + Date.now();
        results[token] = { blob: blob, filename: filename, url: null };
        return { filename: filename, download_token: token };
    }

    /** True when a token addresses a local result rather than a server one. */
    function isLocalToken(token) {
        return typeof token === 'string' && token.indexOf(TOKEN_PREFIX) === 0;
    }

    /**
     * Resolve a local token to `{blob, filename, url}`, minting the object URL
     * lazily. Returns null for server tokens (and for a token whose blob has
     * already been released), which is the caller's cue to use /api/download.
     */
    function resolve(token) {
        if (!isLocalToken(token)) return null;
        var entry = results[token];
        if (!entry) return null;
        if (!entry.url) entry.url = URL.createObjectURL(entry.blob);
        return entry;
    }

    /**
     * Drop a local result and revoke its object URL. Called when a link is
     * about to point somewhere else — without it, every result a visitor
     * produces in a session is pinned in memory for the life of the document.
     */
    function release(token) {
        var entry = results[token];
        if (!entry) return;
        if (entry.url) {
            try { URL.revokeObjectURL(entry.url); } catch (e) { /* already gone */ }
        }
        delete results[token];
    }

    // ── Dispatch ──────────────────────────────────────────────────────────

    function jsonResponse(status, body) {
        return new Response(JSON.stringify(body), {
            status: status,
            headers: { 'Content-Type': 'application/json' },
        });
    }

    /**
     * The only way to the server. Resolves null when the user agreed to this
     * upload, otherwise a ready-made "declined" Response and no request was made.
     * Fails closed: with no consent module there is nobody to ask, so the answer
     * is no.
     */
    async function consentOrDecline(path, code) {
        var gate = window.ffConsent;
        var agreed = false;
        try {
            agreed = !!(gate && await gate.request({ path: path, code: code }));
        } catch (e) {
            agreed = false;
        }
        if (agreed) return null;
        return jsonResponse(499, {
            detail: 'Cancelled. Your file was not uploaded.',
            declined: true,
        });
    }

    /**
     * Drop-in replacement for `fetch(apiUrl(path), {method:'POST', body: fd})`.
     *
     * Runs the operation on-device when a handler is registered and able;
     * otherwise performs exactly the request the caller would have made. The
     * return value is always a `Response`, so no call site needs to know which
     * of the two happened.
     */
    async function ffProcess(path, formData, init) {
        init = init || {};
        var handler = enabled() ? HANDLERS[path] : null;
        var code = null;

        if (handler) {
            try {
                var out = await handler(formData, { signal: init.signal, onProgress: init.onProgress });
                var fields = publish(out.blob, out.filename);
                return jsonResponse(200, Object.assign({
                    status: 'success',
                    message: out.message,
                    local: true,
                }, out.extra || {}, {
                    filename: fields.filename,
                    download_token: fields.download_token,
                }));
            } catch (err) {
                if (err instanceof FFLocalError) {
                    // The user's input is wrong and the server would say so too.
                    return jsonResponse(400, { detail: err.message });
                }
                // Cancelling is a decision, not a failure: never hand the file
                // to the server because the user pressed Cancel.
                if (err && err.name === 'AbortError') throw err;
                // Anything else is our problem, not theirs: log it so it is
                // findable, then ask before the server gets the file.
                if (!(err instanceof FFLocalUnsupported)) {
                    console.warn('[ff-local] ' + path + ' could not run on-device:', err);
                }
                code = err instanceof FFLocalUnsupported ? err.code : 'engine_unavailable';
            }
        }

        var declined = await consentOrDecline(path, code);
        if (declined) return declined;

        var headers = {};
        if (window.__ffSession && window.__ffSession.access_token) {
            headers.Authorization = 'Bearer ' + window.__ffSession.access_token;
        }
        return fetch(window.apiUrl(path), { method: 'POST', body: formData, headers: headers, signal: init.signal });
    }

    // ── FormData helpers ──────────────────────────────────────────────────
    //
    // Everything arrives as a string. These mirror the coercion and the
    // validation messages of main.py's Form(...) declarations and
    // validate_range(), so a rejection reads identically either side.

    function str(fd, name, dflt) {
        var v = fd.get(name);
        if (v === null || v === undefined || v === '') return dflt;
        return String(v);
    }

    function num(fd, name, dflt) {
        var v = fd.get(name);
        if (v === null || v === undefined || v === '') return dflt;
        var n = Number(v);
        if (!isFinite(n)) throw new FFLocalError(name + ' must be a number.');
        return n;
    }

    function int(fd, name, dflt) {
        var v = fd.get(name);
        if (v === null || v === undefined || v === '') return dflt;
        var n = Number(v);
        if (!isFinite(n)) throw new FFLocalError(name + ' must be an integer.');
        return Math.trunc(n);
    }

    /** main.py::validate_range — inclusive bounds, skipped for null. */
    function range(name, value, min, max) {
        if (value === null || value === undefined) return value;
        if (min !== null && min !== undefined && value < min) {
            throw new FFLocalError(name + ' must be >= ' + min + '.');
        }
        if (max !== null && max !== undefined && value > max) {
            throw new FFLocalError(name + ' must be <= ' + max + '.');
        }
        return value;
    }

    function files(fd, name) {
        var all = fd.getAll(name).filter(function (f) { return f && typeof f.name === 'string'; });
        return all;
    }

    // ── Output naming ─────────────────────────────────────────────────────

    var BRAND_SUFFIX = /_forgefiles\.org$/i;

    /**
     * scripts/utils.py::branded_filename — '<original stem>_forgefiles.org.<ext>'.
     *
     * The stem is stripped of a pre-existing brand suffix first, so feeding a
     * result back through another tool doesn't stack them ("a_forgefiles.org_
     * forgefiles.org.pdf"). The UUID prefix the Python version also strips is a
     * server-side upload artifact with no client-side equivalent.
     */
    function brandedName(originalName, ext) {
        var base = String(originalName || 'file');
        var dot = base.lastIndexOf('.');
        var stem = dot > 0 ? base.slice(0, dot) : base;
        stem = stem.replace(BRAND_SUFFIX, '');
        return stem + '_forgefiles.org.' + String(ext).replace(/^\./, '');
    }

    /** Short hex id, matching the `uuid4().hex[:n]` suffixes the server uses. */
    function hexId(n) {
        var s = '';
        while (s.length < n) s += Math.floor(Math.random() * 16).toString(16);
        return s.slice(0, n);
    }

    // ── pdf-lib, loaded on demand ─────────────────────────────────────────
    //
    // ~512 KB. Nobody who only crops an image should pay for it, so it is not
    // in index.html; the first PDF operation pulls it in and every later one
    // reuses the same promise.

    var vendorPromises = {};

    // Resolved from this script's own URL rather than hardcoded, because the
    // two builds mount these assets at different roots: `/static/local/` on the
    // website, `/local/` inside the Capacitor bundle (mobile/build-web.mjs
    // copies public/static to the app's web root). Sibling-relative is correct
    // in both, with the website layout as the fallback if `currentScript` is
    // unavailable.
    function vendorUrl(file) {
        try {
            var self = document.currentScript && document.currentScript.src;
            if (self) return new URL('../vendor/' + file, self).href;
        } catch (e) { /* fall through */ }
        return '/static/vendor/' + file;
    }

    /** Load a vendored UMD script once; resolves to its window global. */
    function loadVendor(file, globalName, version) {
        if (window[globalName]) return Promise.resolve(window[globalName]);
        if (vendorPromises[file]) return vendorPromises[file];

        vendorPromises[file] = new Promise(function (fulfil, fail) {
            var el = document.createElement('script');
            el.src = vendorUrl(file) + '?v=' + version;
            el.async = true;
            el.onload = function () {
                if (window[globalName]) fulfil(window[globalName]);
                else fail(new FFLocalUnsupported(file + ' loaded but did not register', 'engine_unavailable'));
            };
            el.onerror = function () {
                // Reset so a later attempt can retry (the app may have been
                // offline, or the asset may not be in this build).
                delete vendorPromises[file];
                fail(new FFLocalUnsupported(file + ' could not be loaded', 'engine_unavailable'));
            };
            document.head.appendChild(el);
        });

        return vendorPromises[file];
    }

    function loadPdfLib() { return loadVendor('pdf-lib.min.js', 'PDFLib', '1.17.1'); }
    function loadJsZip() { return loadVendor('jszip.min.js', 'JSZip', '3.10.2'); }

    /**
     * True on phones and in the native shell, where memory is the limit.
     * Handlers use it to pick the tighter of their two resource budgets.
     */
    function constrained() {
        if (isNative()) return true;
        try {
            return !!(window.matchMedia && window.matchMedia('(max-width: 768px)').matches);
        } catch (e) {
            return false;
        }
    }

    /** Let the browser paint and handle input between units of work. */
    function tick() {
        return new Promise(function (fulfil) { setTimeout(fulfil, 0); });
    }

    /** Throw the standard abort error when the caller's signal has fired. */
    function checkAbort(signal) {
        if (signal && signal.aborted) {
            var err = new Error('Aborted');
            err.name = 'AbortError';
            throw err;
        }
    }

    // ── Native file delivery ──────────────────────────────────────────────
    //
    // A blob URL with a `download` attribute is the right answer in a browser,
    // but inside the Capacitor WebView there is no download manager to hand it
    // to. There we write the bytes to the app's cache directory and open the
    // system share sheet, which is how a native app is expected to hand a file
    // to Files/Drive/Mail. Both plugins are optional: without them we fall back
    // to the anchor, so a build that hasn't added them still works on web.

    function isNative() {
        try {
            return !!(window.Capacitor &&
                typeof window.Capacitor.isNativePlatform === 'function' &&
                window.Capacitor.isNativePlatform());
        } catch (e) {
            return false;
        }
    }

    function blobToBase64(blob) {
        return new Promise(function (fulfil, fail) {
            var reader = new FileReader();
            reader.onload = function () {
                var out = String(reader.result || '');
                var comma = out.indexOf(',');
                fulfil(comma >= 0 ? out.slice(comma + 1) : out);
            };
            reader.onerror = function () { fail(reader.error); };
            reader.readAsDataURL(blob);
        });
    }

    /**
     * Hand `blob` to the OS. Resolves true when the share sheet was shown,
     * false when the plugins aren't present and the caller should let the
     * anchor's default action run.
     */
    async function nativeShare(blob, filename) {
        if (!isNative()) return false;
        var plugins = (window.Capacitor && window.Capacitor.Plugins) || {};
        var Filesystem = plugins.Filesystem;
        var Share = plugins.Share;
        if (!Filesystem || !Share) return false;

        var written = await Filesystem.writeFile({
            path: filename,
            data: await blobToBase64(blob),
            directory: 'CACHE',
        });
        await Share.share({ title: filename, url: written.uri });
        return true;
    }

    // ── Public surface ────────────────────────────────────────────────────

    window.ffProcess = ffProcess;
    window.ffLocal = {
        register: register,
        consentOrDecline: consentOrDecline,
        enabled: enabled,
        capable: CAPABLE,
        handlers: HANDLERS,

        isLocalToken: isLocalToken,
        resolve: resolve,
        release: release,

        Error: FFLocalError,
        Unsupported: FFLocalUnsupported,

        str: str,
        num: num,
        int: int,
        range: range,
        files: files,

        brandedName: brandedName,
        hexId: hexId,
        loadPdfLib: loadPdfLib,
        loadJsZip: loadJsZip,
        constrained: constrained,
        tick: tick,
        checkAbort: checkAbort,
        isNative: isNative,
        nativeShare: nativeShare,
    };
})();
