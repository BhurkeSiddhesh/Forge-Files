// On-device Office conversion page (WP44, WP31, WP35, WP36, WP38).
//
// Runs LibreOffice compiled to WebAssembly in a worker on this cross-origin
// isolated route. Nothing here ever calls the API: a file that cannot be
// converted on this device is declined with a reason and a link back to the
// normal tool, where the shared consent dialog applies before any upload.

const ENGINE_VERSION = '2.7.2';
const BASE = '/static/vendor/lo-wasm/' + ENGINE_VERSION + '/';
const PDFJS_BASE = '/static/vendor/pdfjs/';
const HANDOFF_CHANNEL = 'ff-office-handoff';
const MAX_CANVAS_PIXELS = 16777216;
const SLIDE_SCALE = 2; // 144 DPI: a 10 in slide becomes 1440 px

const SERVER_PAGES = {
    'word-to-pdf': '/word-to-pdf',
    'excel-to-pdf': '/excel-to-pdf',
    'ppt-to-pdf': '/powerpoint-to-pdf',
    'ppt-to-images': '/ppt-to-images',
};

const $ = (id) => document.getElementById(id);
const pre = window.ffOfficePreflight;

let converter = null;
let converterPromise = null;
let cancelled = false;
let busy = false;
let downloadUrl = null;
let chosenFile = null;
let cancelReject = null;

// Cancelling terminates the engine worker, which would leave an in-flight
// engine call pending forever; racing each call against this lets the page
// settle immediately.
function raceCancel(promise) {
    if (cancelled) { promise.catch(() => { }); return Promise.reject(new DOMException('cancelled', 'AbortError')); }
    return Promise.race([promise, new Promise((_, reject) => { cancelReject = reject; })]);
}

// ── UI helpers ──────────────────────────────────────────────────────────

function show(id, on) { $(id).classList.toggle('hidden', !on); }

function setStatus(percent, message) {
    if (typeof percent === 'number') $('bar').value = Math.max(0, Math.min(100, percent));
    if (message) $('status').textContent = message;
}

function currentOp() { return $('op').value; }

function updateServerLinks() {
    const href = SERVER_PAGES[currentOp()] || '/';
    $('server-link').href = href;
    $('server-link-isolation').href = href;
    $('back-link').href = href;
}

function updateConvertEnabled() {
    $('convert').disabled = busy || !window.crossOriginIsolated || !chosenFile;
}

function resetResult() {
    if (downloadUrl) { URL.revokeObjectURL(downloadUrl); downloadUrl = null; }
    show('result', false);
    show('problem', false);
}

function focusHeading(id) {
    const el = $(id);
    if (el) el.focus({ preventScroll: false });
}

function showProblem(reason) {
    $('problem-reason').textContent = reason;
    show('progress-card', false);
    show('result', false);
    show('problem', true);
    show('setup', true);
    focusHeading('problem-h');
}

// ── File selection ──────────────────────────────────────────────────────

function chooseFile(file, op) {
    resetResult();
    chosenFile = file || null;
    if (op && SERVER_PAGES[op]) $('op').value = op;
    updateServerLinks();
    if (chosenFile) {
        const ext = pre.extOf(chosenFile.name);
        // Pick the matching conversion when the extension makes it unambiguous.
        if (!op) {
            if (ext === 'docx' || ext === 'odt') $('op').value = 'word-to-pdf';
            else if (ext === 'xlsx' || ext === 'xlsm') $('op').value = 'excel-to-pdf';
            else if (ext === 'pptx' && !/^ppt-/.test(currentOp())) $('op').value = 'ppt-to-pdf';
            updateServerLinks();
        }
    }
    updateConvertEnabled();
}

// ── Engine ──────────────────────────────────────────────────────────────

async function sha256Hex(buffer) {
    const digest = await crypto.subtle.digest('SHA-256', buffer);
    return Array.from(new Uint8Array(digest)).map((b) => b.toString(16).padStart(2, '0')).join('');
}

// Fails closed: the small committed engine files are hashed against the
// manifest before anything runs. The two large files are checked at deploy time
// by scripts/fetch_office_engine.py (hashing 247 MB in the page would double
// the memory the conversion needs).
async function verifyEngineFiles() {
    const manifest = await (await fetch(BASE + 'MANIFEST.json', { cache: 'no-cache' })).json();
    for (const [name, meta] of Object.entries(manifest.files)) {
        if (!meta.committed) continue;
        const response = await fetch(BASE + name);
        if (!response.ok) throw new Error('engine_unavailable');
        const actual = await sha256Hex(await response.arrayBuffer());
        if (actual !== meta.sha256) throw new Error('engine_hash_mismatch');
    }
}

async function loadEngine(onProgress) {
    if (converter && converter.isReady()) return converter;
    if (converterPromise) return converterPromise;
    converterPromise = (async () => {
        await verifyEngineFiles();
        const mod = await import(BASE + 'browser.js');
        const conv = new mod.WorkerBrowserConverter({
            sofficeJs: BASE + 'soffice.js',
            sofficeWasm: BASE + 'soffice.wasm',
            sofficeData: BASE + 'soffice.data',
            sofficeWorkerJs: BASE + 'soffice.worker.js',
            browserWorkerJs: BASE + 'browser.worker.global.js',
            onProgress,
        });
        converter = conv;
        await conv.initialize();
        return conv;
    })();
    try {
        return await converterPromise;
    } catch (err) {
        converterPromise = null;
        converter = null;
        throw err;
    }
}

async function stopEngine() {
    const conv = converter;
    converter = null;
    converterPromise = null;
    if (conv) { try { await conv.destroy(); } catch (e) { /* already gone */ } }
}

async function checkStorage() {
    // The engine is cached by the browser; refuse early when there is clearly no room.
    if (navigator.storage && navigator.storage.estimate) {
        const { quota, usage } = await navigator.storage.estimate();
        if (typeof quota === 'number' && typeof usage === 'number' && quota - usage < 300 * 1024 * 1024) {
            const err = new Error('storage');
            err.code = 'storage';
            throw err;
        }
    }
}

// ── Output helpers ──────────────────────────────────────────────────────

function stemOf(name) {
    return String(name || 'document')
        .replace(/^.*[\\/]/, '')
        .replace(/\.[^.]+$/, '')
        .replace(/_forgefiles\.org$/i, '') || 'document';
}

function toBytes(result) {
    const data = result && result.data ? result.data : result;
    return data instanceof Uint8Array ? data : new Uint8Array(data);
}

function isPdf(bytes) {
    return bytes.length > 5 && bytes[0] === 0x25 && bytes[1] === 0x50 && bytes[2] === 0x44 && bytes[3] === 0x46 && bytes[4] === 0x2d;
}

async function loadPdfJs() {
    const mod = await import(PDFJS_BASE + 'pdf.min.mjs');
    mod.GlobalWorkerOptions.workerSrc = PDFJS_BASE + 'pdf.worker.min.mjs';
    return mod;
}

async function pdfToSlidePngs(pdfBytes, stem) {
    const pdfjs = await loadPdfJs();
    const task = pdfjs.getDocument({
        data: pdfBytes.slice(),
        standardFontDataUrl: PDFJS_BASE + 'standard_fonts/',
        cMapUrl: PDFJS_BASE + 'cmaps/',
        cMapPacked: true,
        wasmUrl: PDFJS_BASE + 'wasm/',
        iccUrl: PDFJS_BASE + 'iccs/',
    });
    const doc = await task.promise;
    const entries = [];
    try {
        for (let i = 1; i <= doc.numPages; i++) {
            if (cancelled) throw new DOMException('cancelled', 'AbortError');
            setStatus(80 + Math.round((i / doc.numPages) * 18), 'Rendering slide ' + i + ' of ' + doc.numPages + '…');
            const page = await doc.getPage(i);
            let viewport = page.getViewport({ scale: SLIDE_SCALE });
            if (viewport.width * viewport.height > MAX_CANVAS_PIXELS) {
                const shrink = Math.sqrt(MAX_CANVAS_PIXELS / (viewport.width * viewport.height));
                viewport = page.getViewport({ scale: SLIDE_SCALE * shrink });
            }
            const canvas = document.createElement('canvas');
            canvas.width = Math.max(1, Math.floor(viewport.width));
            canvas.height = Math.max(1, Math.floor(viewport.height));
            const ctx = canvas.getContext('2d');
            ctx.fillStyle = '#ffffff';
            ctx.fillRect(0, 0, canvas.width, canvas.height);
            await page.render({ canvasContext: ctx, viewport }).promise;
            const blob = await new Promise((resolve) => canvas.toBlob(resolve, 'image/png'));
            if (!blob) throw new Error('png_encode_failed');
            entries.push({ name: stem + '_slide_' + String(i).padStart(3, '0') + '.png', blob });
            canvas.width = canvas.height = 0;
            page.cleanup();
        }
    } finally {
        await task.destroy();
    }
    return entries;
}

// ── Conversion ──────────────────────────────────────────────────────────

async function runConversion() {
    const file = chosenFile;
    const op = currentOp();
    if (!file || busy) return;
    resetResult();
    if (!window.crossOriginIsolated) {
        showProblem('On-device conversion needs a browser feature that is not active in this window.');
        return;
    }

    busy = true;
    cancelled = false;
    updateConvertEnabled();
    show('cancel', true);
    show('progress-card', true);
    setStatus(0, 'Checking the document…');

    try {
        const check = await pre.preflight(file, op, { JSZip: window.JSZip });
        if (!check.ok) { showProblem(check.reason); return; }
        // Slide images are drawn with the vendored pdf.js, which needs a newer browser engine.
        if (pre.OPS[op].out === 'images' && typeof Map.prototype.getOrInsertComputed !== 'function') {
            showProblem('This browser is too old to draw slide images on this device. Update it, or use the server.');
            return;
        }
        await checkStorage();

        setStatus(2, 'Preparing the on-device engine…');
        const conv = await raceCancel(loadEngine((info) => {
            if (cancelled) return;
            const pct = typeof info.percent === 'number' ? Math.round(info.percent * 0.7) : undefined;
            setStatus(pct, info.message || 'Loading the on-device engine…');
        }));
        if (cancelled) throw new DOMException('cancelled', 'AbortError');

        setStatus(72, 'Converting…');
        const input = new Uint8Array(await file.arrayBuffer());
        const result = await raceCancel(conv.convert(input, { outputFormat: 'pdf' }, file.name));
        if (cancelled) throw new DOMException('cancelled', 'AbortError');
        const pdf = toBytes(result);
        if (!isPdf(pdf)) throw new Error('not_a_pdf');

        const stem = stemOf(file.name);
        let blob;
        let name;
        let message;
        if (pre.OPS[op].out === 'images') {
            const slides = await pdfToSlidePngs(pdf, stem);
            if (!slides.length) throw new Error('no_slides');
            const zip = new window.JSZip();
            for (const s of slides) zip.file(s.name, s.blob);
            blob = await zip.generateAsync({ type: 'blob', compression: 'DEFLATE' });
            name = stem + '_forgefiles.org.zip';
            message = 'Rendered ' + slides.length + ' slide(s)';
        } else {
            blob = new Blob([pdf], { type: 'application/pdf' });
            name = stem + '_forgefiles.org.pdf';
            message = { 'word-to-pdf': 'Word document converted to PDF', 'excel-to-pdf': 'Excel file converted to PDF', 'ppt-to-pdf': 'PowerPoint converted to PDF' }[op];
        }

        downloadUrl = URL.createObjectURL(blob);
        const link = $('download');
        link.href = downloadUrl;
        link.download = name;
        $('result-message').textContent = message + ' on this device: ' + name;
        setStatus(100, 'Done');
        show('progress-card', false);
        show('result', true);
        focusHeading('result-h');
    } catch (err) {
        if (cancelled || (err && err.name === 'AbortError')) {
            show('progress-card', false);
            setStatus(0, '');
            $('status').textContent = '';
            await stopEngine();
            $('problem-reason').textContent = 'Cancelled. Your file was not uploaded and nothing was saved.';
            show('problem', true);
            focusHeading('problem-h');
        } else if (err && err.code === 'storage') {
            showProblem('There is not enough free browser storage for the on-device engine (about 300 MB needed).');
        } else if (err && /engine_hash_mismatch|engine_unavailable/.test(String(err.message))) {
            showProblem('The on-device engine could not be loaded or verified, so nothing was run.');
        } else {
            console.warn('[on-device-office] conversion failed:', err);
            showProblem('This file could not be converted on this device.');
        }
    } finally {
        busy = false;
        show('cancel', false);
        updateConvertEnabled();
    }
}

// ── Wiring ──────────────────────────────────────────────────────────────

function wire() {
    const params = new URLSearchParams(location.search);
    const op = params.get('op');
    if (op && SERVER_PAGES[op]) $('op').value = op;
    updateServerLinks();

    if (!window.crossOriginIsolated) {
        show('isolation-warning', true);
        $('isolation-detail').textContent = window.isSecureContext
            ? ' This browser or window did not enable cross-origin isolation.'
            : ' This page must be opened over HTTPS.';
    }

    $('op').addEventListener('change', () => { resetResult(); updateServerLinks(); });
    $('file').addEventListener('change', (e) => chooseFile(e.target.files[0] || null));
    $('convert').addEventListener('click', runConversion);
    $('cancel').addEventListener('click', () => {
        cancelled = true;
        $('status').textContent = 'Cancelling…';
        if (cancelReject) cancelReject(new DOMException('cancelled', 'AbortError'));
    });
    $('again').addEventListener('click', () => { resetResult(); $('file').value = ''; chooseFile(null); $('file').focus(); });
    $('problem-dismiss').addEventListener('click', () => { resetResult(); $('file').focus(); });

    const drop = $('drop');
    ['dragenter', 'dragover'].forEach((t) => drop.addEventListener(t, (e) => { e.preventDefault(); drop.classList.add('over'); }));
    ['dragleave', 'drop'].forEach((t) => drop.addEventListener(t, (e) => { e.preventDefault(); drop.classList.remove('over'); }));
    drop.addEventListener('drop', (e) => {
        const f = e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0];
        if (f) chooseFile(f);
    });

    // Hand-off from the tool page. window.opener is not available across the
    // isolation boundary, so a same-origin BroadcastChannel carries the file.
    const token = params.get('h');
    if (token && typeof BroadcastChannel === 'function') {
        const channel = new BroadcastChannel(HANDOFF_CHANNEL);
        channel.onmessage = (event) => {
            const msg = event.data || {};
            if (msg.type === 'file' && msg.token === token && msg.file instanceof Blob) {
                const file = msg.file instanceof File ? msg.file : new File([msg.file], msg.name || 'document');
                chooseFile(file, msg.op);
                channel.close();
            }
        };
        channel.postMessage({ type: 'ready', token });
    }
    updateConvertEnabled();
}

wire();
