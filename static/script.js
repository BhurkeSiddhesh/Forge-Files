
// --- Funnel analytics (first-party, privacy-first) ------------------------
// Sends anonymous funnel events to our own backend (POST /api/track), which
// records them in the same event log that powers /admin/stats — no third-party
// analytics, no cookies beyond the anonymous ff_sid session id the server already
// sets. Only a coarse stage name + a short label (a tool category) are sent;
// never a file name or file content. sendBeacon is used so events survive page
// navigation/unload and never block the UI. Every call is best-effort: if the
// beacon fails, the app carries on exactly as before.
//
// Event names must match event_log.FUNNEL_EVENTS on the server:
//   page_view · tool_open · file_processed · file_downloaded
//
// `ref` is document.referrer, sent on page_view only and reduced to a bare host
// server-side (never stored as a full URL). It has to come from here: the
// Referer header on the beacon itself is our own page, so the server has no
// other way to see which site sent the visitor.
// Valid coarse error categories
const FF_ERROR_CATEGORIES = new Set([
    'timeout', 'unsupported_file', 'invalid_file', 'libreoffice',
    'ocr', 'conversion_error', 'rate_limited', 'server_error', 'cancelled'
]);

// Forbidden keys that must never be sent to analytics
const FF_FORBIDDEN_ANALYTICS_KEYS = new Set([
    'filename', 'file_name', 'file', 'files', 'file_content', 'content', 'contents',
    'text', 'ocr_text', 'document_text', 'email', 'user_id', 'userid', 'supabase_id',
    'request_id', 'requestid', 'order_id', 'orderid', 'stack_trace', 'stacktrace',
    'traceback', 'path', 'temp_path', 'filepath', 'file_path', 'exception',
    'exception_message', 'raw_error', 'ip', 'ip_address', 'password', 'token'
]);

function ffExtractFileType(file) {
    if (!file) return null;
    let name = (typeof file === 'string') ? file : (file.name || file.type || '');
    if (name.indexOf('/') !== -1) name = name.split('/').pop().split('+')[0];
    if (name.indexOf('.') !== -1) name = name.split('.').pop();
    name = String(name).toLowerCase().replace(/[^a-z0-9]/g, '').slice(0, 10);
    if (name === 'jpeg') name = 'jpg';
    return name || null;
}

function ffSanitizeAnalyticsParams(params) {
    if (!params || typeof params !== 'object') return {};
    const sanitized = {};
    for (const key in params) {
        if (!Object.prototype.hasOwnProperty.call(params, key)) continue;
        const k = String(key).toLowerCase().trim();
        if (FF_FORBIDDEN_ANALYTICS_KEYS.has(k)) continue;
        const val = params[key];
        if (val === null || val === undefined) continue;

        if (k === 'tool_name') {
            const s = String(val).toLowerCase().replace(/[-_](?:btn|button|card|action)$/, '').replace(/[- /]/g, '_').replace(/[^a-z0-9_]/g, '').slice(0, 50);
            if (s) sanitized.tool_name = s;
        } else if (k === 'file_type') {
            const cleanType = ffExtractFileType(val);
            if (cleanType) sanitized.file_type = cleanType;
        } else if (k === 'error_category') {
            const s = String(val).toLowerCase();
            sanitized.error_category = FF_ERROR_CATEGORIES.has(s) ? s : 'conversion_error';
        } else if (k === 'processing_duration_ms') {
            const n = Number(val);
            if (!isNaN(n) && n >= 0 && n <= 86400000) sanitized.processing_duration_ms = Math.round(n);
        } else if (k === 'page_path' || k === 'page_title') {
            sanitized[k] = String(val).slice(0, 120);
        }
    }
    return sanitized;
}

function ffTrackGoogleAnalytics(event, label) {
    try {
        if (!window.ffAnalytics || typeof window.ffAnalytics.event !== 'function') return;
        if (label && typeof label === 'object') {
            const sanitized = ffSanitizeAnalyticsParams(label);
            window.ffAnalytics.event(event, sanitized);
            return;
        }
        window.ffAnalytics.event(event, label || '');
    } catch (e) { /* third-party analytics must never break the app */ }
}

function ffTrackPageView(path, title) {
    try {
        if (!window.ffAnalytics || typeof window.ffAnalytics.pageView !== 'function') return;
        const href = window.location.origin ? (window.location.origin + path) : window.location.href;
        window.ffAnalytics.pageView(path || '/', title || document.title, href);
    } catch (e) { /* third-party analytics must never break the app */ }
}

function ffTrack(event, label) {
    ffTrackGoogleAnalytics(event, label);
    try {
        let firstPartyLabel = null;
        if (label && typeof label === 'object') {
            firstPartyLabel = label.tool_name || label.file_type || label.label || null;
        } else if (label) {
            firstPartyLabel = String(label);
        }
        const payload = JSON.stringify({
            event: event,
            label: firstPartyLabel,
            ref: event === 'page_view' ? (document.referrer || '') : undefined,
        });
        const url = (typeof apiUrl === 'function') ? apiUrl('/api/track') : '/api/track';
        if (navigator && typeof navigator.sendBeacon === 'function') {
            navigator.sendBeacon(url, new Blob([payload], { type: 'application/json' }));
        } else {
            fetch(url, {
                method: 'POST', body: payload, keepalive: true,
                headers: { 'Content-Type': 'application/json' }, credentials: 'same-origin',
            });
        }
    } catch (e) { /* analytics must never break the app */ }
}
window.ffTrack = ffTrack;
window.ffTrackPageView = ffTrackPageView;
window.ffSanitizeAnalyticsParams = ffSanitizeAnalyticsParams;
window.ffExtractFileType = ffExtractFileType;

// One page_view per app load. The home app is a single page (tool drill-downs
// don't change the URL), so this fires once; the server-rendered landing pages
// send their own page_view via a tiny inline beacon.
try { ffTrack('page_view', location.pathname || '/'); } catch (e) { }

// Every tool posts through this rather than calling fetch() directly, so that
// on-device handlers (static/local/) get first refusal. The fallback matters:
// if those scripts failed to load, this is exactly the request the tool would
// have made anyway, so a missing local layer costs nothing instead of breaking
// half the tools with "ffProcess is not defined".
const FF_MAX_UPLOAD_MB = 50;
let ffUploadLimitMb = FF_MAX_UPLOAD_MB;
let ffInflightAbort = null;

function ffSetUploadLimit(limitMb) {
    const parsed = Number(limitMb);
    ffUploadLimitMb = Number.isFinite(parsed)
        ? Math.max(FF_MAX_UPLOAD_MB, Math.min(200, Math.floor(parsed)))
        : FF_MAX_UPLOAD_MB;
}

ffSetUploadLimit(window.__ffUploadLimitMbHint);
window.addEventListener('ff-entitlements', function (event) {
    ffSetUploadLimit(event && event.detail && event.detail.maxUploadMb);
});

function ffSanitizeMessage(msg) {
    const s = String(msg == null ? '' : msg);
    if (!s || /<[a-z!/]/i.test(s) || s.length > 400) {
        return 'Something went wrong. Please try again.';
    }
    return s;
}

function ffNotify(message) {
    const text = ffSanitizeMessage(message);
    let host = document.getElementById('ff-toast-host');
    if (!host && document.body) {
        host = document.createElement('div');
        host.id = 'ff-toast-host';
        host.setAttribute('aria-live', 'polite');
        document.body.appendChild(host);
        if (!document.getElementById('ff-toast-style')) {
            const st = document.createElement('style');
            st.id = 'ff-toast-style';
            st.textContent = '#ff-toast-host{position:fixed;z-index:10000;right:16px;bottom:16px;display:flex;flex-direction:column;gap:8px;max-width:min(420px,92vw)}'
                + '.ff-toast{background:#171717;color:#fff;padding:12px 14px;border-radius:10px;font:14px/1.4 system-ui,sans-serif;box-shadow:0 8px 24px rgba(0,0,0,.2)}'
                + '.ff-cancel-btn{margin-left:8px}';
            document.head.appendChild(st);
        }
    }
    if (!host) return;
    const el = document.createElement('div');
    el.className = 'ff-toast';
    el.textContent = text;
    host.appendChild(el);
    setTimeout(function () { el.remove(); }, 7000);
}

function ffCheckUploadSize(fileOrList) {
    const files = (fileOrList && fileOrList.length != null && fileOrList.size == null)
        ? Array.from(fileOrList)
        : (fileOrList ? [fileOrList] : []);
    for (const f of files) {
        if (f && typeof f.size === 'number' && f.size > ffUploadLimitMb * 1024 * 1024) {
            ffNotify('This file is too large (limit ' + ffUploadLimitMb + ' MB). Try a smaller file.');
            return false;
        }
    }
    return true;
}

async function ffMessageFromResponse(response) {
    if (response.status === 413) {
        return 'This file is too large (limit ' + ffUploadLimitMb + ' MB). Try a smaller file.';
    }
    if (response.status === 429) {
        const ra = response.headers.get('Retry-After');
        return (ra && /^\d+$/.test(ra))
            ? ('Too many requests. Please wait ' + ra + ' seconds and try again.')
            : 'Too many requests. Please wait a moment and try again.';
    }
    try {
        const data = await response.clone().json();
        let detail = data.detail || data.message || '';
        if (Array.isArray(detail)) detail = detail.map(function (x) { return x.msg || x; }).join('; ');
        return ffSanitizeMessage(detail || 'Something went wrong. Please try again.');
    } catch (_) {
        const text = await response.text();
        if (text && !/<[a-z!/]/i.test(text) && text.length < 280) return text;
        return 'Something went wrong. Please try again.';
    }
}

function ffStartInflight() {
    if (ffInflightAbort) {
        try { ffInflightAbort.abort(); } catch (e) { /* ignore */ }
    }
    ffInflightAbort = (typeof AbortController === 'function') ? new AbortController() : null;
    ffSetCancelVisible(true);
    return ffInflightAbort;
}

function ffIsAbort(error) {
    return !!(error && (error.name === 'AbortError' || /aborted/i.test(String(error.message || ''))));
}

function ffBindCancelButtons() {
    document.querySelectorAll('.status-display').forEach(function (el) {
        if (el.querySelector('.ff-cancel-btn')) return;
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'ff-cancel-btn secondary-btn';
        btn.textContent = 'Cancel';
        btn.hidden = true;
        btn.addEventListener('click', function (e) {
            e.preventDefault();
            ffCancelInflight();
        });
        el.appendChild(btn);
    });
}

function ffSetCancelVisible(on) {
    document.querySelectorAll('.ff-cancel-btn').forEach(function (btn) {
        btn.hidden = !on;
    });
}

function ffCancelInflight() {
    if (ffInflightAbort) {
        try { ffInflightAbort.abort(); } catch (e) { /* ignore */ }
        ffInflightAbort = null;
        ffSetCancelVisible(false);
        ffNotify('Conversion cancelled.');
        try { ffTrack('processing_cancelled', { tool_name: ffFunnelLabel() }); } catch (e) { }
    }
}

function ffFormDataFiles(formData) {
    const files = [];
    if (!formData || typeof formData.forEach !== 'function') return files;
    formData.forEach(function (value) {
        if (value && typeof value.size === 'number' && typeof value.name === 'string') files.push(value);
    });
    return files;
}

window.ffNotify = ffNotify;
window.ffSanitizeMessage = ffSanitizeMessage;
window.ffCheckUploadSize = ffCheckUploadSize;
window.__ffSetUploadLimit = ffSetUploadLimit;
window.ffMessageFromResponse = ffMessageFromResponse;
window.ffCancelInflight = ffCancelInflight;
window.ffIsAbort = ffIsAbort;
window.ffStartInflight = ffStartInflight;

// The one door to the server for uploads. Resolves a declined Response (and
// sends nothing) unless the person agreed to this specific operation; see
// static/local/ff-server-gate.js. With the consent module missing there is nobody to
// ask, so the answer is no.
async function ffServerFetch(path, init, code) {
    let agreed = false;
    try {
        agreed = !!(window.ffConsent && await window.ffConsent.request({ path: path, code: code }));
    } catch (e) {
        agreed = false;
    }
    if (!agreed) {
        return new Response(JSON.stringify({ detail: 'Cancelled. Your file was not uploaded.', declined: true }),
            { status: 499, headers: { 'Content-Type': 'application/json' } });
    }
    return fetch(apiUrl(path), init);
}
window.ffServerFetch = ffServerFetch;

const _ffProcessFallback = async (path, formData, init) => {
    if (!ffCheckUploadSize(ffFormDataFiles(formData))) {
        return Promise.resolve(new Response(JSON.stringify({
            detail: 'This file is too large (limit ' + ffUploadLimitMb + ' MB). Try a smaller file.',
        }), { status: 413, headers: { 'Content-Type': 'application/json' } }));
    }
    const abort = (init && init.signal) ? null : ffStartInflight();
    ffSetCancelVisible(true);
    const requestInit = Object.assign({
        method: 'POST',
        body: formData,
        signal: (init && init.signal) || (abort && abort.signal) || (ffInflightAbort && ffInflightAbort.signal) || undefined,
    }, init || {});
    requestInit.headers = Object.assign({}, requestInit.headers || {});
    if (window.__ffSession && window.__ffSession.access_token && !requestInit.headers.Authorization) {
        requestInit.headers.Authorization = 'Bearer ' + window.__ffSession.access_token;
    }
    try {
        return await ffServerFetch(path, requestInit);
    } finally {
        ffSetCancelVisible(false);
    }
};
const ffProcess = window.ffProcess
    || _ffProcessFallback;

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', ffBindCancelButtons);
} else {
    ffBindCancelButtons();
}

// Tracks which local result each download anchor is currently holding, so the
// previous one can be released when the anchor is repointed.
const ffHeldLocalTokens = new WeakMap();

// `token` is the opaque per-result download key returned as `download_token`.
// It is deliberately not the display filename: output names are deterministic
// ("resume_forgefiles.org.pdf" for every "resume.pdf"), so addressing results
// by name let anyone guess their way to a stranger's document.
//
// Results processed on-device (static/local/) carry a local token instead and
// never existed on the server. `ffLocal.resolve()` returns null for a server
// token, which is what lets one function serve both.
function ffShowSuccessUpsell(downloadEl) {
    // Highest-intent moment: the file just worked. Only when the private
    // deploy injected the payments upsell (#102 / #62).
    if (!document.getElementById('ff-upsell')) return;
    if (!downloadEl || !downloadEl.parentNode) return;
    if (downloadEl.parentNode.querySelector('.ff-success-upsell')) return;
    const a = document.createElement('a');
    a.className = 'ff-success-upsell';
    a.href = '/pricing';
    a.textContent = 'Go ad-free — remove ads on every conversion';
    downloadEl.parentNode.insertBefore(a, downloadEl.nextSibling);
}

function ffShowSuccessShare(downloadEl, filename, local) {
    // Sharing loop (#187): WhatsApp & Web Share API on conversion completion
    if (!downloadEl || !downloadEl.parentNode) return;
    let shareContainer = downloadEl.parentNode.querySelector('.ff-share-container');
    const docName = filename || (local && local.filename ? local.filename : 'document');
    if (!shareContainer) {
        shareContainer = document.createElement('div');
        shareContainer.className = 'ff-share-container';
        shareContainer.style.marginTop = '8px';
        shareContainer.style.display = 'flex';
        shareContainer.style.gap = '8px';
        shareContainer.style.alignItems = 'center';
        shareContainer.style.justifyContent = 'center';
        shareContainer.style.flexWrap = 'wrap';

        // Native Web Share API button
        const shareBtn = document.createElement('button');
        shareBtn.type = 'button';
        shareBtn.className = 'btn secondary-btn ff-share-btn';
        shareBtn.style.padding = '6px 14px';
        shareBtn.style.fontSize = '0.85rem';
        shareBtn.style.cursor = 'pointer';
        shareBtn.textContent = 'Share';

        shareBtn.onclick = async () => {
            const shareText = `Converted "${docName}" using Forge Files:`;
            const shareUrl = window.location.origin;
            if (navigator.share) {
                try {
                    await navigator.share({
                        title: 'Forge Files',
                        text: shareText,
                        url: shareUrl,
                    });
                    ffTrack('file_shared', { tool_name: ffFunnelLabel(), channel: 'native_share' });
                    return;
                } catch (err) {
                    if (err.name === 'AbortError') return;
                }
            }
            // Fallback: open WhatsApp
            const waUrl = 'https://wa.me/?text=' + encodeURIComponent(shareText + ' ' + shareUrl);
            window.open(waUrl, '_blank', 'noopener,noreferrer');
            ffTrack('file_shared', { tool_name: ffFunnelLabel(), channel: 'whatsapp_fallback' });
        };

        // WhatsApp direct link button
        const waLink = document.createElement('a');
        waLink.className = 'btn secondary-btn ff-wa-share-btn';
        waLink.style.padding = '6px 14px';
        waLink.style.fontSize = '0.85rem';
        waLink.style.textDecoration = 'none';
        waLink.style.cursor = 'pointer';
        waLink.textContent = 'WhatsApp';
        waLink.target = '_blank';
        waLink.rel = 'noopener noreferrer';
        const waText = `Converted "${docName}" using Forge Files: ${window.location.origin}`;
        waLink.href = 'https://wa.me/?text=' + encodeURIComponent(waText);
        waLink.onclick = () => {
            ffTrack('file_shared', { tool_name: ffFunnelLabel(), channel: 'whatsapp' });
        };

        shareContainer.appendChild(shareBtn);
        shareContainer.appendChild(waLink);

        const upsell = downloadEl.parentNode.querySelector('.ff-success-upsell');
        if (upsell && upsell.nextSibling) {
            downloadEl.parentNode.insertBefore(shareContainer, upsell.nextSibling);
        } else if (downloadEl.nextSibling) {
            downloadEl.parentNode.insertBefore(shareContainer, downloadEl.nextSibling);
        } else {
            downloadEl.parentNode.appendChild(shareContainer);
        }
    } else {
        const waLink = shareContainer.querySelector('.ff-wa-share-btn');
        if (waLink) {
            const waText = `Converted "${docName}" using Forge Files: ${window.location.origin}`;
            waLink.href = 'https://wa.me/?text=' + encodeURIComponent(waText);
        }
    }
}

function updateDownloadLink(element, token, filename) {
    if (!element) return;

    // Each section reuses a single anchor for every result it produces. The one
    // it pointed at before is now unreachable, so release it — without this,
    // every blob a visitor generates stays pinned for the life of the page.
    const held = ffHeldLocalTokens.get(element);
    if (held && held !== token && window.ffLocal) window.ffLocal.release(held);
    ffHeldLocalTokens.delete(element);

    const local = window.ffLocal ? window.ffLocal.resolve(token) : null;

    // A result is ready for download — the successful end of the processing
    // funnel. Fired once per result, across every tool type, since every
    // success path funnels through updateDownloadLink().
    //
    // file_processed → first-party /api/track → /admin/stats only (unchanged)
    // processing_completed → GA4 canonical key event, but ONLY for local processing.
    //   Server-side completions already fire processing_completed via backend
    //   Measurement Protocol (ga4_mp.py). If we fired again here, a single server
    //   conversion would produce two GA4 key events (double-count).
    //   Local/on-device processing never hits the backend, so the frontend must
    //   emit the canonical event — otherwise local conversions are invisible in GA4.
    ffTrack('file_processed', ffFunnelLabel());
    if (local) {
        // Local/on-device result: emit canonical processing_completed to GA4.
        // The backend has no knowledge of this conversion.
        try {
            const dlType = ffExtractFileType(local ? local.filename : null) || null;
            const localParams = ffSanitizeAnalyticsParams({
                tool_name: ffFunnelLabel(),
                file_type: dlType,
            });
            ffTrackGoogleAnalytics('processing_completed', localParams);
        } catch (e) { /* analytics must never break the app */ }
    }
    // For server-side results (local === null), processing_completed was already
    // fired by the backend Measurement Protocol — do NOT fire it here.
    ffShowSuccessUpsell(element);
    ffShowSuccessShare(element, filename, local);

    if (local) {
        ffHeldLocalTokens.set(element, token);
        element.href = local.url;
        // A blob URL carries no Content-Disposition, so without this the
        // browser saves the result under its opaque URL id instead of a name.
        element.setAttribute('download', filename || local.filename);

        element.onclick = async (e) => {
            const dlType = ffExtractFileType(local ? local.filename : filename) || 'pdf';
            ffTrack('file_downloaded', { tool_name: ffFunnelLabel(), file_type: dlType });
            // Inside the app there is no download manager to hand a blob URL
            // to; write the bytes out and offer the system share sheet. On the
            // web (and in a build without the plugins) this is a no-op and the
            // anchor's own download takes over.
            try {
                if (await window.ffLocal.nativeShare(local.blob, local.filename)) {
                    e.preventDefault();
                    return false;
                }
            } catch (error) {
                console.error('Native save failed, falling back to the link:', error);
            }
            return true;
        };
        return;
    }

    // Clear any `download` a previous local result left behind, or this
    // server download would be saved under that result's filename.
    element.removeAttribute('download');

    const url = apiUrl(`/api/download/${encodeURIComponent(token)}`);
    element.href = url;

    element.onclick = async (e) => {
        // We intercept left-clicks to provide a friendly 404 alert if the file is gone.
        // For context-menu actions (Save As...), the browser hits the href directly.
        try {
            // HEAD request is lightweight and verifies existence without downloading.
            const response = await fetch(url, { method: 'HEAD' });

            if (response.status === 404) {
                e.preventDefault();
                ffNotify("The converted file no longer exists. Please re-process.");
                return false;
            }

            // If OK, let the browser proceed with the native download via element.href.
            // This avoids loading the entire file into memory as a Blob.
            const dlType = ffExtractFileType(filename || element.getAttribute('download')) || 'pdf';
            ffTrack('file_downloaded', { tool_name: ffFunnelLabel(), file_type: dlType });
            return true;

        } catch (error) {
            console.error('Download check failed:', error);
            // On network error, still try to let the browser handle it
            return true;
        }
    };
}

let selectedFile = null;
let selectedFiles = [];
let selectedImageFile = null;
let currentTool = null;

// The specific tool within the current category ('convert-word', 'excel-to-pdf'),
// derived from the action card the visitor clicked. `currentTool` alone is the
// category, which is why /admin/stats' per-tool funnel could only ever say
// "pdf" — true but useless for deciding what to fix.
let currentOp = null;

// Label for the processing funnel events: the specific tool when we know it,
// otherwise the category we opened.
function ffFunnelLabel() {
    return currentOp || currentTool || 'unknown';
}
// Exported alongside ffTrack so anything layered on top of this script can emit
// funnel events with the same label this file would have used, instead of
// guessing one or reporting none.
window.ffFunnelLabel = ffFunnelLabel;

// One delegated listener covers every action card, including the ones added
// after this file was written — no per-card wiring to keep in sync.
document.addEventListener('click', (e) => {
    const card = e.target.closest && e.target.closest('.action-card');
    if (!card || !card.id) return;
    currentOp = card.id.replace(/-btn$/, '');
    // Funnel step at tool granularity. The category-level tool_open from
    // showDrillDown() still fires; the global funnel counts distinct sessions
    // per stage, so the extra row can't inflate it.
    ffTrack('tool_open', { tool_name: currentOp });
});

// Highlights the action card the visitor picked and clears the previous pick
// within the same action list. Called only from handlers that actually opened
// the card's option panel, so the highlight never claims a tool that isn't
// active (e.g. a click that bailed with "Please select a file first").
function ffSelectActionCard(card) {
    if (!card) return;
    const scope = card.closest('.action-buttons') || document;
    scope.querySelectorAll('.action-card.selected').forEach(function (c) {
        c.classList.remove('selected');
        c.setAttribute('aria-pressed', 'false');
    });
    card.classList.add('selected');
    card.setAttribute('aria-pressed', 'true');
}

// Drops the picked-action highlight, e.g. when picking a new file closes the
// open option panels.
function ffClearActionSelection(scope) {
    (scope || document).querySelectorAll('.action-card.selected').forEach(function (c) {
        c.classList.remove('selected');
        c.setAttribute('aria-pressed', 'false');
    });
}

// Step Tracker Management across the 3-step UI workflow
function ffUpdateStepTracker(tool, stepNum) {
    const p = tool || currentTool || 'pdf';
    const ind1 = document.getElementById(`${p}-step-1-ind`);
    const ind2 = document.getElementById(`${p}-step-2-ind`);
    const ind3 = document.getElementById(`${p}-step-3-ind`);
    const idleEl = document.getElementById(`${p}-idle-placeholder`);
    const statusEl = document.getElementById(p === 'pdf' ? 'status-display' : `${p}-status-display`);
    const resultEl = document.getElementById(p === 'pdf' ? 'result-display' : `${p}-result-display`);

    if (ind1 && ind2 && ind3) {
        [ind1, ind2, ind3].forEach(el => {
            el.classList.remove('active', 'completed');
        });

        if (stepNum === 1) {
            ind1.classList.add('active');
        } else if (stepNum === 2) {
            ind1.classList.add('completed');
            ind2.classList.add('active');
        } else if (stepNum >= 3) {
            ind1.classList.add('completed');
            ind2.classList.add('completed');
            ind3.classList.add('active', 'completed');
        }
    }

    if (idleEl) {
        if (stepNum >= 3 || (statusEl && !statusEl.classList.contains('hidden')) || (resultEl && !resultEl.classList.contains('hidden'))) {
            idleEl.classList.add('hidden');
        } else {
            idleEl.classList.remove('hidden');
        }
    }
}
window.ffUpdateStepTracker = ffUpdateStepTracker;

// Navigation
// `instant` skips the 500ms home-page fade — used by the SEO deep link, where
// the visitor already chose a tool on the landing page and the animation is
// just dead time between their click and a usable upload box.
const FF_TOOL_TITLES = {
    pdf: 'PDF Tools', image: 'Image Tools', excel: 'Excel Tools',
    ppt: 'PPT Tools', word: 'Word Tools', workflow: 'Workflow'
};
const FF_HOME_TITLE = document.title;

function ffRouteForTool(tool) {
    const url = new URL(window.location.href);
    url.searchParams.set('tool', tool);
    url.searchParams.delete('op');
    url.searchParams.delete('handoff');
    return url.pathname + url.search + url.hash;
}

function showDrillDown(tool, instant, fromHistory = false) {
    if (!Object.prototype.hasOwnProperty.call(FF_TOOL_TITLES, tool)) return;
    currentTool = tool;
    currentOp = null;  // new category — the previous tool no longer applies
    let pageId;
    if (tool === 'pdf') pageId = 'pdf-page';
    else if (tool === 'image') pageId = 'image-page';
    else if (tool === 'excel') pageId = 'excel-page';
    else if (tool === 'ppt') pageId = 'ppt-page';
    else if (tool === 'word') pageId = 'word-page';
    else if (tool === 'workflow') pageId = 'workflow-page';
    else return;

    if (!fromHistory && new URLSearchParams(location.search).get('tool') !== tool) {
        history.pushState({ tool }, '', ffRouteForTool(tool));
    }
    document.title = FF_TOOL_TITLES[tool] + ' | Forge Files';
    ffUpdateStepTracker(tool, 1);

    // Funnel step: visitor opened a tool category from the home grid.
    ffTrack('tool_open', { tool_name: tool });
    // Analytics receives a fixed category path; URL query parameters may
    // contain arbitrary visitor data and must not enter third-party telemetry.
    ffTrackPageView('/app/' + encodeURIComponent(tool), document.title);

    const reveal = () => {
        document.querySelectorAll('.view').forEach(el => {
            if (el.id !== pageId) {
                el.style.display = 'none';
                el.classList.remove('active');
            }
        });
        const target = document.getElementById(pageId);
        if (target) {
            target.style.display = 'flex';
            target.style.flexDirection = 'column';
            window.scrollTo({ top: 0, behavior: 'instant' });
            target.classList.add('active');
            target.setAttribute('tabindex', '-1');
            target.focus({ preventScroll: true });
        }
    };

    document.querySelectorAll('.view').forEach(el => el.classList.remove('active'));
    reveal();
}

function showHome(fromHistory = false) {
    const homePath = /\/index\.html$/.test(location.pathname) ? location.pathname : '/';
    if (!fromHistory && (location.search || location.pathname !== homePath)) {
        history.pushState({}, '', homePath);
    }
    document.title = FF_HOME_TITLE;
    currentTool = null;
    currentOp = null;
    ffTrackPageView('/', document.title);
    document.querySelectorAll('.view').forEach(el => {
        if (el.id !== 'home-page') {
            el.classList.remove('active');
            el.style.display = 'none';
        }
    });
    resetUI();
    const home = document.getElementById('home-page');
    if (home) {
        home.style.display = 'flex';
        home.style.flexDirection = 'column';
        window.scrollTo({ top: 0, behavior: 'instant' });
        home.classList.add('active');
        home.setAttribute('tabindex', '-1');
        home.focus({ preventScroll: true });
    }
}

window.addEventListener('popstate', () => {
    const tool = new URLSearchParams(location.search).get('tool');
    if (tool && Object.prototype.hasOwnProperty.call(FF_TOOL_TITLES, tool)) {
        showDrillDown(tool, true, true);
    } else {
        showHome(true);
    }
});

// On a stacked phone layout the result follows the action catalog. Bring the
// finished download into view so users do not have to search for it.
document.querySelectorAll('.result-panel .result-display').forEach(result => {
    new MutationObserver(() => {
        if (window.innerWidth > 992 || result.classList.contains('hidden') ||
            !result.closest('.view.active')) return;
        result.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
    }).observe(result, { attributes: true, attributeFilter: ['class'] });
});

// File Selection
const dropZone = document.getElementById('drop-zone');
const fileInput = document.getElementById('file-input');
const filenameDisplay = document.getElementById('filename-display');
const fileInfo = document.getElementById('file-info');

dropZone.onclick = () => fileInput.click();

fileInput.onchange = (e) => {
    if (e.target.files.length > 0) {
        if (fileInput.multiple) {
            handleFiles(Array.from(e.target.files));
        } else {
            handleFile(e.target.files[0]);
        }
    }
};

dropZone.ondragover = (e) => {
    e.preventDefault();
    dropZone.classList.add('drag-over');
};

dropZone.ondragleave = () => {
    dropZone.classList.remove('drag-over');
};

dropZone.ondrop = (e) => {
    e.preventDefault();
    dropZone.classList.remove('drag-over');
    if (e.dataTransfer.files.length > 0) {
        if (fileInput.multiple) {
            handleFiles(Array.from(e.dataTransfer.files));
        } else {
            handleFile(e.dataTransfer.files[0]);
        }
    }
};

function hidePdfActionAreas() {
    document.getElementById('password-input-area').classList.add('hidden');
    document.getElementById('convert-password-area').classList.add('hidden');
    document.getElementById('extract-pages-area')?.classList.add('hidden');
    document.getElementById('split-pdf-area')?.classList.add('hidden');
    document.getElementById('compress-area')?.classList.add('hidden');
    document.getElementById('merge-area')?.classList.add('hidden');
    document.getElementById('watermark-area')?.classList.add('hidden');
    document.getElementById('to-images-area')?.classList.add('hidden');
    document.getElementById('sign-area')?.classList.add('hidden');
    document.getElementById('rotate-pdf-area')?.classList.add('hidden');
    document.getElementById('protect-pdf-area')?.classList.add('hidden');
    document.getElementById('extract-text-area')?.classList.add('hidden');
    document.getElementById('ocr-pdf-area')?.classList.add('hidden');
    document.getElementById('organize-pdf-area')?.classList.add('hidden');
    document.getElementById('page-numbers-area')?.classList.add('hidden');
    document.getElementById('repair-pdf-area')?.classList.add('hidden');
    document.getElementById('create-pdf-area')?.classList.add('hidden');
    document.getElementById('annotate-pdf-area')?.classList.add('hidden');
    document.getElementById('pdf-metadata-area')?.classList.add('hidden');
    document.getElementById('pdf-to-excel-area')?.classList.add('hidden');
    document.getElementById('pdf-to-pptx-area')?.classList.add('hidden');
    document.getElementById('pdf-to-epub-area')?.classList.add('hidden');
    document.getElementById('result-display').classList.add('hidden');
    // Panels are closing, so no action is picked anymore.
    ffClearActionSelection(document.getElementById('pdf-page'));
}

// Move the selected PDF option panel after its card. The action grid gives
// panels a full-width row, keeping the controls close to the chosen action.
const PDF_AREA_CARD = {
    'password-input-area': 'remove-password-btn',
    'convert-password-area': 'convert-word-btn',
    'extract-pages-area': 'extract-pages-btn',
    'split-pdf-area': 'split-pdf-btn',
    'compress-area': 'compress-pdf-btn',
    'merge-area': 'merge-pdf-btn',
    'watermark-area': 'watermark-pdf-btn',
    'to-images-area': 'to-images-pdf-btn',
    'sign-area': 'sign-pdf-btn',
    'rotate-pdf-area': 'rotate-pdf-btn',
    'protect-pdf-area': 'protect-pdf-btn',
    'extract-text-area': 'extract-text-btn',
    'ocr-pdf-area': 'ocr-pdf-btn',
    'organize-pdf-area': 'organize-pdf-btn',
    'page-numbers-area': 'page-numbers-btn',
    'repair-pdf-area': 'repair-pdf-btn',
    'create-pdf-area': 'create-pdf-btn',
    'annotate-pdf-area': 'annotate-pdf-btn',
    'pdf-metadata-area': 'pdf-metadata-btn',
    'pdf-to-excel-area': 'pdf-to-excel-btn',
    'pdf-to-pptx-area': 'pdf-to-pptx-btn',
    'pdf-to-epub-area': 'pdf-to-epub-btn',
};

function openPdfArea(areaId) {
    hidePdfActionAreas();
    const area = document.getElementById(areaId);
    if (!area) return;

    const card = document.getElementById(PDF_AREA_CARD[areaId]);
    if (card) {
        card.insertAdjacentElement('afterend', area);
        ffSelectActionCard(card);
    }

    area.classList.remove('hidden');
    area.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}

function setMergeMode(on) {
    fileInput.multiple = !!on;
    if (!on) {
        selectedFiles = [];
    } else {
        selectedFile = null;
    }
}

function handleFiles(files) {
    const pdfs = files.filter(f => f.type === 'application/pdf');
    if (pdfs.length === 0) {
        // Clear any prior selection so the UI doesn't keep an obsolete file in
        // state (otherwise a follow-up "Merge Now" click would silently re-use
        // the previously selected files).
        selectedFiles = [];
        selectedFile = null;
        fileInput.value = '';
        filenameDisplay.textContent = 'No file selected';
        fileInfo.classList.add('hidden');
        ffNotify('Please select PDF files.');
        return;
    }
    if (!ffCheckUploadSize(pdfs)) return;
    selectedFiles = pdfs;
    selectedFile = pdfs[0];
    filenameDisplay.textContent = pdfs.length === 1
        ? pdfs[0].name
        : `${pdfs.length} files: ${pdfs.map(f => f.name).join(', ')}`;
    fileInfo.classList.remove('hidden');
    document.getElementById('status-display').classList.add('hidden');
    ffUpdateStepTracker('pdf', 2);
    ffConsumePendingOp();
    try { ffTrack('file_selected', { tool_name: currentOp || currentTool || 'pdf', file_type: 'pdf' }); } catch (e) { }
}

function handleFile(file) {
    if (file.type !== 'application/pdf') {
        ffNotify('Please select a PDF file.');
        return;
    }
    if (!ffCheckUploadSize(file)) return;
    selectedFile = file;
    ffUpdatePdfCompressPreview();
    filenameDisplay.textContent = file.name;
    fileInfo.classList.remove('hidden');

    // Reset displays
    document.getElementById('status-display').classList.add('hidden');
    hidePdfActionAreas();
    const extractInput = document.getElementById('extract-pages-input');
    if (extractInput) extractInput.value = '';
    ffUpdateStepTracker('pdf', 2);
    ffConsumePendingOp();
    try { ffTrack('file_selected', { tool_name: currentOp || currentTool || 'pdf', file_type: 'pdf' }); } catch (e) { }
}

// Actions
document.getElementById('remove-password-btn').onclick = () => {
    setMergeMode(false);
    if (!selectedFile) { ffNotify('Please select a file first.'); return; }
    openPdfArea('password-input-area');
};

document.getElementById('convert-word-btn').onclick = () => {
    setMergeMode(false);
    if (!selectedFile) { ffNotify('Please select a file first.'); return; }
    openPdfArea('convert-password-area');
};

document.getElementById('extract-pages-btn').onclick = () => {
    setMergeMode(false);
    if (!selectedFile) { ffNotify('Please select a file first.'); return; }
    openPdfArea('extract-pages-area');
};

document.getElementById('split-pdf-btn').onclick = () => {
    setMergeMode(false);
    if (!selectedFile) { ffNotify('Please select a file first.'); return; }
    openPdfArea('split-pdf-area');
};

document.getElementById('compress-pdf-btn').onclick = () => {
    setMergeMode(false);
    if (!selectedFile) { ffNotify('Please select a file first.'); return; }
    openPdfArea('compress-area');
    ffUpdatePdfCompressPreview();
};

document.getElementById('merge-pdf-btn').onclick = () => {
    setMergeMode(true);
    openPdfArea('merge-area');
};

document.getElementById('watermark-pdf-btn').onclick = () => {
    setMergeMode(false);
    if (!selectedFile) { ffNotify('Please select a file first.'); return; }
    openPdfArea('watermark-area');
};

document.getElementById('to-images-pdf-btn').onclick = () => {
    setMergeMode(false);
    if (!selectedFile) { ffNotify('Please select a file first.'); return; }
    openPdfArea('to-images-area');
};

document.getElementById('sign-pdf-btn').onclick = () => {
    setMergeMode(false);
    if (!selectedFile) { ffNotify('Please select a file first.'); return; }
    openPdfArea('sign-area');
};

document.getElementById('ocr-pdf-btn').onclick = () => {
    setMergeMode(false);
    if (!selectedFile) { ffNotify('Please select a file first.'); return; }
    openPdfArea('ocr-pdf-area');
};

document.querySelectorAll('input[name="compress-level"]').forEach(function (radio) {
    radio.addEventListener('change', ffUpdatePdfCompressPreview);
});
document.querySelectorAll('input[name="compress-mode"]').forEach(function (radio) {
    radio.addEventListener('change', ffUpdatePdfCompressPreview);
});

document.getElementById('process-compress-btn').onclick = async () => {
    const level = document.querySelector('input[name="compress-level"]:checked')?.value || 'medium';
    const mode = document.querySelector('input[name="compress-mode"]:checked')?.value || 'structural';
    if (!ffCheckUploadSize(selectedFile)) return;

    const formData = new FormData();
    formData.append('file', selectedFile);
    formData.append('level', level);
    formData.append('mode', mode);

    const statusDisplay = document.getElementById('status-display');
    const statusText = document.getElementById('status-text');
    const resultDisplay = document.getElementById('result-display');

    statusDisplay.classList.remove('hidden');
    statusText.textContent = 'Compressing PDF...';
    resultDisplay.classList.add('hidden');

    const abort = ffStartInflight();
    try {
        const response = await ffProcess('/api/pdf/compress', formData, {
            signal: abort && abort.signal,
        });
        if (response.ok) {
            const data = await response.json();
            statusDisplay.classList.add('hidden');
            showCompressResult(data);
        } else {
            statusDisplay.classList.add('hidden');
            ffNotify('Error: ' + await ffMessageFromResponse(response));
        }
    } catch (error) {
        statusDisplay.classList.add('hidden');
        if (!ffIsAbort(error)) ffNotify('An error occurred: ' + error.message);
    } finally {
        ffSetCancelVisible(false);
    }
};

// The "Use AI Layout Recovery" label should match what the deployed backend
// can actually deliver (e.g. on ARM, RapidOCR has no table/column layout
// recovery) rather than a single hard-coded claim. Also: once a PDF already
// has a usable text layer, the server skips OCR entirely regardless of this
// checkbox, so the label only needs to stop overpromising, not describe
// every routing case.
(async () => {
    const label = document.getElementById('ai-mode-label');
    if (!label) return;
    try {
        const response = await fetch(apiUrl('/api/ai-capabilities'));
        if (!response.ok) return;
        const data = await response.json();
        if (!data.enabled) {
            label.textContent = 'Use AI Layout Recovery (unavailable on this server)';
        } else if (data.supports_layout) {
            label.textContent = 'Use AI Layout Recovery (tables & columns, for scanned/image PDFs)';
        } else {
            label.textContent = 'Use OCR Text Recovery (for scanned/image PDFs)';
        }
    } catch (e) { /* keep the default label if this fails */ }
})();

document.getElementById('process-convert-btn').onclick = async () => {
    const useAI = document.getElementById('ai-mode-toggle').checked;

    const formData = new FormData();
    formData.append('file', selectedFile);
    formData.append('use_ai', useAI);

    // Both paths use the SSE endpoint. AI conversion reports real per-page
    // progress; the standard path has no per-page callback, so it reports
    // elapsed time instead — but it still needs the stream, because a large
    // PDF can run for minutes and a plain POST spends that time looking frozen
    // (and risks tripping proxy/browser idle timeouts).
    convertToWordWithProgress(formData, useAI);
};

function formatElapsed(seconds) {
    const m = Math.floor(seconds / 60);
    const s = seconds % 60;
    return m ? `${m}m ${s}s` : `${s}s`;
}

// Polls GET /api/jobs/{jobId} until the job resolves (done) or `maxWaitMs`
// elapses. Recovers a result that finished after the SSE stream that started
// it dropped (issue #95) — the worker on the server keeps running and
// records its outcome under jobId regardless of whether anyone is still
// listening on the stream.
async function pollJobStatus(jobId, statusText, maxWaitMs = 6 * 60 * 1000, jobLabel = 'conversion') {
    const deadline = Date.now() + maxWaitMs;
    let delay = 1500;
    while (Date.now() < deadline) {
        if (statusText) statusText.textContent = `Connection lost — checking whether the ${jobLabel} finished...`;
        try {
            const resp = await fetch(apiUrl(`/api/jobs/${encodeURIComponent(jobId)}`));
            if (resp.ok) {
                const job = await resp.json();
                if (job.status === 'done') return job.event;
            } else if (resp.status === 404) {
                // Job expired or never existed (e.g. the stream died before the
                // 'start' event ever reached the client) — nothing to recover.
                return null;
            }
        } catch (e) {
            // Still offline; keep retrying until maxWaitMs.
        }
        await new Promise(r => setTimeout(r, delay));
        delay = Math.min(delay * 1.5, 8000); // backoff, capped at 8s
    }
    return null;
}

async function convertToWordWithProgress(formData, useAI) {
    if (!ffCheckUploadSize(ffFormDataFiles(formData))) return;
    const statusDisplay = document.getElementById('status-display');
    const statusText = document.getElementById('status-text');
    const resultDisplay = document.getElementById('result-display');

    statusDisplay.classList.remove('hidden');
    statusText.textContent = useAI ? 'Starting AI conversion...' : 'Starting conversion...';
    resultDisplay.classList.add('hidden');
    const abort = ffStartInflight();

    // The standard path gets no per-page events from the server, so show real
    // elapsed time rather than a fake percentage. Knowing it's still working
    // (and roughly how long it's been) is what keeps people from giving up.
    const startedAt = Date.now();
    let ticker = null;
    let queuedActive = false;
    if (!useAI) {
        ticker = setInterval(() => {
            if (queuedActive) return;
            const elapsed = Math.round((Date.now() - startedAt) / 1000);
            statusText.textContent =
                `Converting to Word... ${formatElapsed(elapsed)} elapsed. `
                + 'Large or scanned PDFs can take several minutes.';
        }, 1000);
    }

    let jobId = null;

    const handleEvent = (event) => {
        if (event.event === 'queued') {
            queuedActive = true;
            statusDisplay.classList.add('queue-status');
            const wait = event.estimated_wait_seconds
                ? ` Estimated wait: about ${formatElapsed(event.estimated_wait_seconds)}.`
                : '';
            statusText.textContent =
                `In queue: position #${event.position} of ${event.total_queued}. `
                + `High server activity; your file will convert automatically.${wait}`;
        } else if (event.event === 'progress') {
            queuedActive = false;
            statusDisplay.classList.remove('queue-status');
            const pct = event.total > 0 ? Math.round((event.page / event.total) * 100) : 0;
            statusText.textContent = `AI conversion: page ${event.page}/${event.total} (${pct}%)`;
        } else if (event.event === 'start') {
            queuedActive = false;
            statusDisplay.classList.remove('queue-status');
            jobId = event.job_id || jobId;
            if (useAI) statusText.textContent = 'Analyzing layout with AI...';
        } else if (event.event === 'complete') {
            queuedActive = false;
            statusDisplay.classList.remove('queue-status');
            showResult(event.filename, event.message, event.download_token);
        } else if (event.event === 'error') {
            queuedActive = false;
            statusDisplay.classList.remove('queue-status');
            ffNotify('Error: ' + event.detail);
        }
    };

    try {
        const response = await ffServerFetch('/api/pdf/convert-to-word-stream', {
            method: 'POST',
            body: formData,
            signal: abort && abort.signal,
        });

        if (!response.ok) {
            ffNotify('Error: ' + await ffMessageFromResponse(response));
            return;
        }

        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';
        let streamFailed = false;

        try {
            while (true) {
                const { done, value } = await reader.read();
                if (done) break;
                buffer += decoder.decode(value, { stream: true });

                const lines = buffer.split('\n\n');
                buffer = lines.pop(); // keep incomplete chunk in buffer

                for (const line of lines) {
                    if (!line.startsWith('data: ')) continue;
                    handleEvent(JSON.parse(line.slice(6)));
                }
            }
        } catch (streamError) {
            streamFailed = true;
        }

        if (streamFailed) {
            // The connection dropped mid-stream, not the conversion itself —
            // the server-side job keeps running independently. Recover its
            // result by job_id instead of telling the user their (possibly
            // multi-minute) conversion just vanished.
            if (jobId) {
                const finalEvent = await pollJobStatus(jobId, statusText);
                if (finalEvent) {
                    handleEvent(finalEvent);
                } else {
                    ffNotify('Error: Lost connection to the server and the conversion did not complete in time. Please try again.');
                }
            } else {
                ffNotify('Error: Lost connection to the server before the conversion started. Please try again.');
            }
        }
    } catch (error) {
        if (!ffIsAbort(error)) ffNotify('Error: ' + error.message);
    } finally {
        if (ticker) clearInterval(ticker);
        statusDisplay.classList.add('hidden');
        ffSetCancelVisible(false);
    }
}

document.getElementById('process-password-btn').onclick = () => {
    const password = document.getElementById('pdf-password').value;
    if (!password) {
        ffNotify('Please enter a password.');
        return;
    }
    const formData = new FormData();
    formData.append('file', selectedFile);
    formData.append('password', password);

    processAction('/api/pdf/remove-password', 'Removing password...', formData);
};

document.getElementById('process-extract-btn').onclick = () => {
    const pages = document.getElementById('extract-pages-input').value.trim();

    if (!pages) {
        ffNotify('Please enter pages to extract (e.g., 1,3,5-7 or all).');
        return;
    }

    const formData = new FormData();
    formData.append('file', selectedFile);
    formData.append('pages', pages);

    processAction('/api/pdf/extract-pages', 'Extracting selected pages...', formData);
};

document.getElementById('process-split-btn').onclick = () => {
    const mode = document.getElementById('split-pdf-mode').value;
    const ranges = document.getElementById('split-pdf-ranges').value.trim();
    const n = document.getElementById('split-pdf-n').value;
    if (!selectedFile) { ffNotify('Please select a file first.'); return; }
    if (mode === 'ranges' && !ranges) {
        ffNotify('Please enter ranges to split (e.g., 1-2,3-5).');
        return;
    }
    if (mode === 'every_n' && (!n || Number(n) < 1)) {
        ffNotify('Please enter a valid page count per split.');
        return;
    }

    const formData = new FormData();
    formData.append('file', selectedFile);
    formData.append('mode', mode);
    if (mode === 'ranges') formData.append('ranges', ranges);
    if (mode === 'every_n') formData.append('n', n);

    processAction('/api/pdf/split', 'Splitting PDF into a ZIP...', formData);
};

document.getElementById('process-merge-btn').onclick = () => {
    if (!selectedFiles || selectedFiles.length < 2) {
        ffNotify('Please select at least two PDF files in the upload area.');
        return;
    }
    const formData = new FormData();
    selectedFiles.forEach(f => formData.append('files', f));
    processAction('/api/pdf/merge', `Merging ${selectedFiles.length} PDFs...`, formData);
};

const watermarkOpacityInput = document.getElementById('watermark-opacity');
if (watermarkOpacityInput) {
    watermarkOpacityInput.addEventListener('input', (e) => {
        document.getElementById('watermark-opacity-value').textContent = e.target.value;
    });
}

document.getElementById('process-watermark-btn').onclick = () => {
    if (!selectedFile) { ffNotify('Please select a file first.'); return; }
    const text = document.getElementById('watermark-text').value.trim();
    if (!text) { ffNotify('Please enter watermark text.'); return; }
    const position = document.getElementById('watermark-position').value;
    const opacity = document.getElementById('watermark-opacity').value;

    const formData = new FormData();
    formData.append('file', selectedFile);
    formData.append('text', text);
    formData.append('position', position);
    formData.append('opacity', opacity);

    processAction('/api/pdf/watermark', 'Adding watermark...', formData);
};

document.getElementById('process-to-images-btn').onclick = () => {
    if (!selectedFile) { ffNotify('Please select a file first.'); return; }
    const dpi = document.getElementById('to-images-dpi').value;
    const fmt = document.getElementById('to-images-format').value;

    const formData = new FormData();
    formData.append('file', selectedFile);
    formData.append('dpi', dpi);
    formData.append('fmt', fmt);

    processAction('/api/pdf/to-images', 'Rendering pages to images...', formData);
};

const signWidthInput = document.getElementById('sign-width');
if (signWidthInput) {
    signWidthInput.addEventListener('input', (e) => {
        document.getElementById('sign-width-value').textContent =
            Math.round(parseFloat(e.target.value) * 100) + '%';
    });
}

const SIGN_POSITION_PRESETS = {
    'top-right':      { x: 0.65, y: 0.05 },
    'bottom-right':   { x: 0.65, y: 0.85 },
    'bottom-center':  { x: 0.40, y: 0.85 },
    'bottom-left':    { x: 0.05, y: 0.85 },
};

document.getElementById('process-sign-btn').onclick = () => {
    if (!selectedFile) { ffNotify('Please select a PDF file first.'); return; }
    const sigInput = document.getElementById('signature-image');
    const sigFile = sigInput?.files?.[0];
    if (!sigFile) { ffNotify('Please choose a signature image.'); return; }

    const page = parseInt(document.getElementById('sign-page').value, 10) || 1;
    const positionKey = document.getElementById('sign-position').value;
    const preset = SIGN_POSITION_PRESETS[positionKey] || SIGN_POSITION_PRESETS['bottom-right'];
    const width = document.getElementById('sign-width').value;

    const formData = new FormData();
    formData.append('file', selectedFile);
    formData.append('signature', sigFile);
    formData.append('page', page);
    formData.append('x', preset.x);
    formData.append('y', preset.y);
    formData.append('width', width);

    processAction('/api/pdf/sign', 'Adding signature...', formData);
};

async function processAction(url, text, formData = null) {
    const statusDisplay = document.getElementById('status-display');
    const statusText = document.getElementById('status-text');
    const resultDisplay = document.getElementById('result-display');
    let abort = null;
    const passwordArea = document.getElementById('password-input-area');

    statusDisplay.classList.remove('hidden');
    statusText.textContent = text;
    resultDisplay.classList.add('hidden');
    if (formData === null) passwordArea.classList.add('hidden');

    if (!formData) {
        formData = new FormData();
        formData.append('file', selectedFile);
    }

    try {
        // Runs on-device when this tool has a local handler, otherwise posts to
        // the backend exactly as before — either way a Response comes back, so
        // everything below is unchanged. See static/local/ff-local.js.
        abort = ffStartInflight();
        const response = await ffProcess(url, formData, {
            signal: abort && abort.signal,
            onProgress: (done, total) => { statusText.textContent = `${text} (${done}/${total})`; },
        });

        if (response.ok) {
            const data = await response.json();
            showResult(data.filename, data.message, data.download_token);
        } else {
            // Try to parse as JSON first, fall back to text
            const contentType = response.headers.get('content-type');
            if (contentType && contentType.includes('application/json')) {
                const data = await response.json();
                if (response.status === 402 && data.detail && typeof data.detail === 'object') {
                    const upgradeUrl = data.detail.upgrade_url || '/pricing';
                    const msg = data.detail.message || 'Premium subscription required';
                    ffNotify(`${msg}. Visit ${upgradeUrl} to upgrade.`);
                } else {
                    const msg = typeof data.detail === 'string' ? data.detail : (data.detail && data.detail.message ? data.detail.message : JSON.stringify(data.detail));
                    ffNotify('Error: ' + msg);
                }
            } else {
                const text = await response.text();
                ffNotify('Error: ' + text);
            }
        }
    } catch (error) {
        // Cancel already told the user; nothing else to report.
        if (!ffIsAbort(error)) ffNotify('Error: ' + error.message);
    } finally {
        statusDisplay.classList.add('hidden');
        ffSetCancelVisible(false);
    }

}

const FF_PDF_COMPRESS_HINT = { low: [0.80, 0.90], medium: [0.50, 0.70], high: [0.30, 0.50] };

function ffUpdatePdfCompressPreview() {
    const el = document.getElementById('pdf-compress-preview');
    if (!el) return;
    const level = document.querySelector('input[name="compress-level"]:checked')?.value || 'medium';
    const mode = document.querySelector('input[name="compress-mode"]:checked')?.value || 'structural';
    if (mode === 'structural') {
        el.textContent = 'Structural mode preserves searchable text, links and vectors. Savings depend on how the PDF was originally encoded.';
        return;
    }
    const range = FF_PDF_COMPRESS_HINT[level] || FF_PDF_COMPRESS_HINT.medium;
    if (!selectedFile) {
        el.textContent = 'Raster mode estimate: Low ~10–20%, Medium ~30–50%, High ~50–70%. Text and links become page images.';
        return;
    }
    el.textContent = 'This ' + formatBytes(selectedFile.size) + ' file would typically become about '
        + formatBytes(Math.round(selectedFile.size * range[0])) + '–'
        + formatBytes(Math.round(selectedFile.size * range[1]))
        + ' at ' + level + ' compression (estimate).';
}

function ffPreviewJpegQuality(file, quality, imgId, labelId, wrapId, format = 'jpeg') {
    const wrap = document.getElementById(wrapId);
    const imgEl = document.getElementById(imgId);
    const label = document.getElementById(labelId);
    if (!file || !wrap || !imgEl || !label) return;
    if (file.type && !file.type.startsWith('image/') && !/\.(jpe?g|png|webp|gif|bmp)$/i.test(file.name || '')) {
        wrap.classList.add('hidden');
        return;
    }
    const q = Math.max(1, Math.min(100, Number(quality) || 80));
    const mime = format === 'webp' ? 'image/webp' : (format === 'png' ? 'image/png' : 'image/jpeg');
    const requestId = String(Number(wrap.dataset.requestId || 0) + 1);
    wrap.dataset.requestId = requestId;
    const url = URL.createObjectURL(file);
    const probe = new Image();
    probe.onload = function () {
        const canvas = document.createElement('canvas');
        let w = probe.naturalWidth;
        let h = probe.naturalHeight;
        const max = 720;
        if (Math.max(w, h) > max) {
            const s = max / Math.max(w, h);
            w = Math.max(1, Math.round(w * s));
            h = Math.max(1, Math.round(h * s));
        }
        canvas.width = w;
        canvas.height = h;
        const context = canvas.getContext('2d');
        if (mime === 'image/jpeg') {
            context.fillStyle = '#fff';
            context.fillRect(0, 0, w, h);
        }
        context.drawImage(probe, 0, 0, w, h);
        URL.revokeObjectURL(url);
        canvas.toBlob(function (blob) {
            if (wrap.dataset.requestId !== requestId || !blob || blob.type !== mime) return;
            if (imgEl.dataset.blobUrl) URL.revokeObjectURL(imgEl.dataset.blobUrl);
            const previewUrl = URL.createObjectURL(blob);
            imgEl.dataset.blobUrl = previewUrl;
            imgEl.src = previewUrl;
            label.textContent = 'Preview (' + q + '%)';
            wrap.classList.remove('hidden');
        }, mime, mime === 'image/png' ? undefined : (q / 100));
    };
    probe.onerror = function () {
        URL.revokeObjectURL(url);
        wrap.classList.add('hidden');
    };
    probe.src = url;
}
window.ffUpdatePdfCompressPreview = ffUpdatePdfCompressPreview;
window.ffPreviewJpegQuality = ffPreviewJpegQuality;

function formatBytes(bytes) {
    if (bytes < 1024) return bytes + ' B';
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
    return (bytes / (1024 * 1024)).toFixed(2) + ' MB';
}

function showResult(filename, message, token) {
    const resultDisplay = document.getElementById('result-display');
    const resultMessage = document.getElementById('result-message');
    const downloadLink = document.getElementById('download-link');

    // Clear any previous compress stats
    const existingStats = resultDisplay.querySelector('.compress-stats');
    if (existingStats) existingStats.remove();
    const existingBadge = resultDisplay.querySelector('.reduction-badge');
    if (existingBadge) existingBadge.remove();
    const existingNote = resultDisplay.querySelector('.compression-note');
    if (existingNote) existingNote.remove();

    resultDisplay.classList.remove('hidden');
    resultMessage.textContent = message + ': ' + filename;
    updateDownloadLink(downloadLink, token, filename);
    ffUpdateStepTracker('pdf', 3);
}

function showCompressResult(data) {
    const resultDisplay = document.getElementById('result-display');
    const resultMessage = document.getElementById('result-message');
    const downloadLink = document.getElementById('download-link');

    // Clear any previous compress stats
    const existingStats = resultDisplay.querySelector('.compress-stats');
    if (existingStats) existingStats.remove();
    const existingBadge = resultDisplay.querySelector('.reduction-badge');
    if (existingBadge) existingBadge.remove();

    resultDisplay.classList.remove('hidden');
    resultMessage.textContent = 'Compressed: ' + data.filename;
    updateDownloadLink(downloadLink, data.download_token);

    // Build size stats display
    const badge = document.createElement('div');
    badge.className = 'reduction-badge';
    badge.textContent = Number(data.reduction_pct) > 0 ? `↓ ${data.reduction_pct}% smaller` : 'No safe size reduction';

    const stats = document.createElement('div');
    stats.className = 'compress-stats';
    stats.innerHTML = `
        <div class="compress-stat">
            <span class="stat-label">Original</span>
            <span class="stat-value">${formatBytes(data.original_size)}</span>
        </div>
        <span class="compress-stat-arrow"><i class="fas fa-arrow-right"></i></span>
        <div class="compress-stat">
            <span class="stat-label">Compressed</span>
            <span class="stat-value">${formatBytes(data.compressed_size)}</span>
        </div>
    `;

    // Insert after the message, before the download button
    resultMessage.insertAdjacentElement('afterend', stats);
    stats.insertAdjacentElement('afterend', badge);
    if (data.compression_note) {
        const note = document.createElement('p');
        note.className = 'helper-text compression-note';
        note.textContent = data.compression_note;
        badge.insertAdjacentElement('afterend', note);
    }
    ffUpdateStepTracker('pdf', 3);
}

function resetUI() {
    selectedFile = null;
    selectedFiles = [];
    selectedImageFile = null;
    currentTool = null;
    fileInput.value = '';
    fileInput.multiple = false;
    filenameDisplay.textContent = 'No file selected';
    fileInfo.classList.add('hidden');
    document.getElementById('password-input-area').classList.add('hidden');
    document.getElementById('convert-password-area').classList.add('hidden');
    document.getElementById('extract-pages-area')?.classList.add('hidden');
    document.getElementById('split-pdf-area')?.classList.add('hidden');
    document.getElementById('compress-area')?.classList.add('hidden');
    document.getElementById('merge-area')?.classList.add('hidden');
    document.getElementById('watermark-area')?.classList.add('hidden');
    document.getElementById('to-images-area')?.classList.add('hidden');
    document.getElementById('sign-area')?.classList.add('hidden');
    document.getElementById('rotate-pdf-area')?.classList.add('hidden');
    document.getElementById('protect-pdf-area')?.classList.add('hidden');
    document.getElementById('extract-text-area')?.classList.add('hidden');
    document.getElementById('ocr-pdf-area')?.classList.add('hidden');
    document.getElementById('organize-pdf-area')?.classList.add('hidden');
    document.getElementById('page-numbers-area')?.classList.add('hidden');
    document.getElementById('repair-pdf-area')?.classList.add('hidden');
    document.getElementById('create-pdf-area')?.classList.add('hidden');
    document.getElementById('annotate-pdf-area')?.classList.add('hidden');
    document.getElementById('pdf-metadata-area')?.classList.add('hidden');
    document.getElementById('pdf-to-excel-area')?.classList.add('hidden');
    document.getElementById('pdf-to-pptx-area')?.classList.add('hidden');
    document.getElementById('pdf-to-epub-area')?.classList.add('hidden');
    document.getElementById('status-display').classList.add('hidden');
    document.getElementById('result-display').classList.add('hidden');
    const extractInput = document.getElementById('extract-pages-input');
    if (extractInput) extractInput.value = '';
    const splitRanges = document.getElementById('split-pdf-ranges');
    if (splitRanges) splitRanges.value = '';
    const splitN = document.getElementById('split-pdf-n');
    if (splitN) splitN.value = '1';

    // Reset image tools
    const imageFileInput = document.getElementById('image-file-input');
    const imageFilenameDisplay = document.getElementById('image-filename-display');
    const imageFileInfo = document.getElementById('image-file-info');
    if (imageFileInput) imageFileInput.value = '';
    if (imageFilenameDisplay) imageFilenameDisplay.textContent = 'No file selected';
    if (imageFileInfo) imageFileInfo.classList.add('hidden');
    document.getElementById('image-status-display')?.classList.add('hidden');
    document.getElementById('image-result-display')?.classList.add('hidden');

    ['pdf', 'image', 'excel', 'ppt', 'word'].forEach(t => ffUpdateStepTracker(t, 1));
}

// === Image Tools ===

const imageDropZone = document.getElementById('image-drop-zone');
const imageFileInput = document.getElementById('image-file-input');
const imageFilenameDisplay = document.getElementById('image-filename-display');
const imageFileInfo = document.getElementById('image-file-info');
const qualitySlider = document.getElementById('quality-slider');
const qualityValue = document.getElementById('quality-value');

if (imageDropZone) {
    imageDropZone.onclick = () => imageFileInput.click();

    imageFileInput.onchange = (e) => {
        if (e.target.files.length > 0) {
            handleImageFile(e.target.files[0]);
        }
    };

    imageDropZone.ondragover = (e) => {
        e.preventDefault();
        imageDropZone.classList.add('drag-over');
    };

    imageDropZone.ondragleave = () => {
        imageDropZone.classList.remove('drag-over');
    };

    imageDropZone.ondrop = (e) => {
        e.preventDefault();
        imageDropZone.classList.remove('drag-over');
        if (e.dataTransfer.files.length > 0) {
            handleImageFile(e.dataTransfer.files[0]);
        }
    };
}

if (qualitySlider) {
    qualitySlider.oninput = () => {
        qualityValue.textContent = qualitySlider.value;
        if (selectedImageFile) {
            ffPreviewJpegQuality(selectedImageFile, qualitySlider.value,
                'jpeg-quality-preview-img', 'jpeg-quality-preview-label', 'jpeg-quality-preview');
        }
    };
}

function handleImageFile(file) {
    const validExts = ['.heic', '.heif', '.jpg', '.jpeg', '.png', '.webp', '.bmp', '.tiff', '.tif', '.gif'];
    const ext = '.' + file.name.split('.').pop().toLowerCase();

    if (!file.type.startsWith('image/') && !validExts.includes(ext)) {
        ffNotify('Please select an image file (HEIC, JPG, PNG, WebP, BMP, TIFF, GIF).');
        return;
    }
    selectedImageFile = file;
    imageFilenameDisplay.textContent = file.name;
    imageFileInfo.classList.remove('hidden');
    try { ffTrack('file_selected', { tool_name: currentOp || currentTool || 'image', file_type: ffExtractFileType(file) || 'jpg' }); } catch (e) { }
    document.getElementById('image-status-display').classList.add('hidden');
    document.getElementById('image-result-display').classList.add('hidden');
    hideImageActionAreas();
    ffUpdateStepTracker('image', 2);
    ffConsumePendingOp();
}

function hideImageActionAreas() {
    ['convert-options', 'resize-options', 'crop-options', 'rotate-image-area', 'compress-image-area', 'convert-format-area', 'watermark-image-area', 'image-to-pdf-area']
        .forEach(id => document.getElementById(id)?.classList.add('hidden'));
    destroyCropper();
    ffClearActionSelection(document.getElementById('image-page'));
}

// Convert to JPEG
document.getElementById('process-convert-jpeg-btn')?.addEventListener('click', () => {
    if (!selectedImageFile) {
        ffNotify('Please select a file first.');
        return;
    }
    const quality = qualitySlider ? parseInt(qualitySlider.value) : 95;
    const formData = new FormData();
    formData.append('file', selectedImageFile);
    formData.append('quality', quality);
    processImageAction('/api/image/heic-to-jpeg', 'Converting to JPEG...', formData);
});

async function processImageAction(url, text, formData) {
    const statusDisplay = document.getElementById('image-status-display');
    const statusText = document.getElementById('image-status-text');
    const resultDisplay = document.getElementById('image-result-display');

    statusDisplay.classList.remove('hidden');
    statusText.textContent = text;
    resultDisplay.classList.add('hidden');

    try {
        const response = await ffProcess(url, formData);

        if (response.ok) {
            const data = await response.json();
            showImageResult(data.filename, data.message, data.download_token, data);
        } else {
            const contentType = response.headers.get('content-type');
            if (contentType && contentType.includes('application/json')) {
                const data = await response.json();
                ffNotify('Error: ' + data.detail);
            } else {
                const text = await response.text();
                ffNotify('Error: ' + text);
            }
        }
    } catch (error) {
        ffNotify('Error: ' + error.message);
    } finally {
        statusDisplay.classList.add('hidden');
    }
}

function showImageResult(filename, message, token, data = {}) {
    const resultDisplay = document.getElementById('image-result-display');
    const resultMessage = document.getElementById('image-result-message');
    const downloadLink = document.getElementById('image-download-link');

    resultDisplay.classList.remove('hidden');
    resultMessage.textContent = message + ': ' + filename;
    resultDisplay.querySelector('.image-compress-stats')?.remove();
    if (Number.isFinite(data.original_size) && Number.isFinite(data.compressed_size)) {
        const stats = document.createElement('p');
        stats.className = 'helper-text image-compress-stats';
        stats.textContent = `${formatBytes(data.original_size)} original → ${formatBytes(data.compressed_size)} download`;
        if (data.compressed_size < data.original_size) stats.textContent += ` (${data.reduction_pct}% smaller)`;
        resultMessage.insertAdjacentElement('afterend', stats);
    }
    updateDownloadLink(downloadLink, token, filename);
    ffUpdateStepTracker('image', 3);
}

// --- Image Resize & Crop Functions ---

let cropper = null;

function destroyCropper() {
    if (cropper) {
        cropper.destroy();
        cropper = null;
    }
}

// Lazy-load Cropper.js only when the crop tool is first used, so its CSS+JS are
// not on the homepage's critical render path (protects LCP/INP). Cached after first load.
let cropperLibPromise = null;
function ensureCropperLoaded() {
    if (window.Cropper) return Promise.resolve();
    if (cropperLibPromise) return cropperLibPromise;
    cropperLibPromise = new Promise((resolve, reject) => {
        const css = document.createElement('link');
        css.rel = 'stylesheet';
        css.href = 'https://cdnjs.cloudflare.com/ajax/libs/cropperjs/1.5.13/cropper.min.css';
        document.head.appendChild(css);
        const js = document.createElement('script');
        js.src = 'https://cdnjs.cloudflare.com/ajax/libs/cropperjs/1.5.13/cropper.min.js';
        js.onload = () => resolve();
        js.onerror = () => { cropperLibPromise = null; reject(new Error('Failed to load image cropper')); };
        document.head.appendChild(js);
    });
    return cropperLibPromise;
}

async function initCropper() {
    if (!selectedImageFile) return;

    try {
        await ensureCropperLoaded();
    } catch (e) {
        ffNotify('Could not load the cropping tool. Check your connection and try again.');
        return;
    }

    const image = document.getElementById('crop-image-preview');
    const container = document.getElementById('crop-editor-container');
    const statusDisplay = document.getElementById('image-status-display');
    const statusText = document.getElementById('image-status-text');

    // Check for HEIC/HEIF
    const ext = '.' + selectedImageFile.name.split('.').pop().toLowerCase();
    const isHeic = ext === '.heic' || ext === '.heif' || selectedImageFile.type === 'image/heic' || selectedImageFile.type === 'image/heif';

    if (isHeic) {
        // Show loading state
        if (statusDisplay) {
            statusDisplay.classList.remove('hidden');
            statusText.innerText = "Generating preview...";
        }
        container.classList.add('hidden'); // Hide until ready

        try {
            const formData = new FormData();
            formData.append('file', selectedImageFile);
            formData.append('quality', 80); // Faster preview

            const response = await ffProcess('/api/image/heic-to-jpeg', formData);

            if (!response.ok) {
                const err = await response.json();
                throw new Error(err.detail || "Preview generation failed");
            }

            const data = await response.json();

            // Set up onload before setting src
            image.onload = () => {
                if (statusDisplay) statusDisplay.classList.add('hidden');
                container.classList.remove('hidden');

                destroyCropper();
                cropper = new Cropper(image, {
                    viewMode: 1,
                    autoCropArea: 0.8,
                    movable: false,
                    zoomable: true,
                    rotatable: false,
                    scalable: false,
                });
            };
            const localPreview = window.ffLocal && window.ffLocal.resolve(data.download_token);
            image.src = localPreview ? localPreview.url : apiUrl(`/api/download/${encodeURIComponent(data.download_token)}`);

        } catch (e) {
            console.error(e);
            ffNotify("Could not load HEIC preview: " + e.message);
            if (statusDisplay) statusDisplay.classList.add('hidden');
        }

    } else {
        // Standard flow for supported images (JPG, PNG)
        const reader = new FileReader();
        reader.onload = (e) => {
            image.src = e.target.result;
            container.classList.remove('hidden');

            // Destroy existing to avoid duplicates
            destroyCropper();

            cropper = new Cropper(image, {
                viewMode: 1,
                autoCropArea: 0.8,
                movable: false,
                zoomable: true,
                rotatable: false,
                scalable: false,
            });
        };
        reader.readAsDataURL(selectedImageFile);
    }
}

function toggleResizeInputs() {
    const method = document.getElementById('resize-method').value;
    document.getElementById('input-dimensions').classList.add('hidden');
    document.getElementById('input-percentage').classList.add('hidden');
    document.getElementById('input-target-size').classList.add('hidden');

    if (method === 'dimensions') {
        document.getElementById('input-dimensions').classList.remove('hidden');
    } else if (method === 'percentage') {
        document.getElementById('input-percentage').classList.remove('hidden');
    } else if (method === 'target_size') {
        document.getElementById('input-target-size').classList.remove('hidden');
    }
}

async function resizeImage() {
    if (!selectedImageFile) {
        ffNotify("Please select an image file first.");
        return;
    }
    const file = selectedImageFile;

    const mode = document.getElementById('resize-method').value;
    const formData = new FormData();
    formData.append('file', file);
    formData.append('mode', mode);

    if (mode === 'dimensions') {
        const width = document.getElementById('resize-width').value;
        const height = document.getElementById('resize-height').value;
        if (!width && !height) {
            ffNotify("Please enter at least width or height.");
            return;
        }
        if (width) formData.append('width', width);
        if (height) formData.append('height', height);
    } else if (mode === 'percentage') {
        const percentage = document.getElementById('scale-slider').value;
        formData.append('percentage', percentage);
    } else if (mode === 'target_size') {
        const targetSize = document.getElementById('target-size-kb').value;
        if (!targetSize) {
            ffNotify("Please enter a target size.");
            return;
        }
        formData.append('target_size_kb', targetSize);
    }

    const statusDisplay = document.getElementById('image-status-display');
    const resultDisplay = document.getElementById('image-result-display');
    const statusText = document.getElementById('image-status-text');
    const resultMessage = document.getElementById('image-result-message');
    const downloadLink = document.getElementById('image-download-link');

    // Reset UI
    statusDisplay.classList.remove('hidden');
    resultDisplay.classList.add('hidden');
    statusText.innerText = "Resizing image...";

    try {
        const response = await ffProcess('/api/image/resize', formData);

        const data = await response.json();

        if (response.ok) {
            statusDisplay.classList.add('hidden');
            resultDisplay.classList.remove('hidden');
            resultMessage.innerText = `${data.message}: ${data.filename}`;
            updateDownloadLink(downloadLink, data.download_token, data.filename);
            downloadLink.innerText = `Download ${data.filename}`;
        } else {
            throw new Error(data.detail || 'Resize failed');
        }
    } catch (error) {
        console.error('Error:', error);
        statusDisplay.classList.add('hidden');
        ffNotify("An error occurred: " + error.message);
    }
}

async function cropImage() {
    if (!cropper) {
        ffNotify("Please start cropping first.");
        return;
    }

    // Get crop data (x, y, width, height)
    const data = cropper.getData(true); // true for rounded integers

    const formData = new FormData();
    formData.append('file', selectedImageFile);
    formData.append('x', data.x);
    formData.append('y', data.y);
    formData.append('width', data.width);
    formData.append('height', data.height);

    const statusDisplay = document.getElementById('image-status-display');
    const resultDisplay = document.getElementById('image-result-display');
    const statusText = document.getElementById('image-status-text');
    const resultMessage = document.getElementById('image-result-message');
    const downloadLink = document.getElementById('image-download-link');

    // Reset UI
    statusDisplay.classList.remove('hidden');
    resultDisplay.classList.add('hidden');
    statusText.innerText = "Cropping image...";

    try {
        const response = await ffProcess('/api/image/crop', formData);

        const respData = await response.json();

        if (response.ok) {
            statusDisplay.classList.add('hidden');
            resultDisplay.classList.remove('hidden');
            resultMessage.innerText = `${respData.message}: ${respData.filename}`;
            updateDownloadLink(downloadLink, respData.download_token, respData.filename);
            downloadLink.innerText = `Download ${respData.filename}`;
        } else {
            throw new Error(respData.detail || 'Crop failed');
        }
    } catch (error) {
        console.error('Error:', error);
        statusDisplay.classList.add('hidden');
        ffNotify("An error occurred: " + error.message);
    }
}

// === Workflow Builder ===

let workflowFile = null;
let workflowSteps = [];
let currentConfigStepIndex = null;

// Initialize workflow builder when DOM is ready
document.addEventListener('DOMContentLoaded', initWorkflowBuilder);

function initWorkflowBuilder() {
    const dropZone = document.getElementById('workflow-drop-zone');
    const fileInput = document.getElementById('workflow-file-input');
    const canvas = document.getElementById('workflow-canvas');
    const stepItems = document.querySelectorAll('.step-item');

    if (!dropZone || !fileInput || !canvas) return;

    // File drop handling
    dropZone.onclick = () => fileInput.click();

    fileInput.onchange = (e) => {
        if (e.target.files.length > 0) {
            handleWorkflowFile(e.target.files[0]);
        }
    };

    dropZone.ondragover = (e) => {
        e.preventDefault();
        dropZone.classList.add('drag-over');
    };

    dropZone.ondragleave = () => dropZone.classList.remove('drag-over');

    dropZone.ondrop = (e) => {
        e.preventDefault();
        dropZone.classList.remove('drag-over');
        if (e.dataTransfer.files.length > 0) {
            handleWorkflowFile(e.dataTransfer.files[0]);
        }
    };

    // Step palette drag start
    stepItems.forEach(item => {
        item.ondragstart = (e) => {
            e.dataTransfer.setData('step-type', item.dataset.stepType);
            e.dataTransfer.setData('step-label', item.dataset.stepLabel);
            e.dataTransfer.setData('step-icon', item.dataset.stepIcon);
            item.style.opacity = '0.5';
        };
        item.ondragend = () => {
            item.style.opacity = '1';
        };

        // A11y: Click to add step
        item.onclick = () => {
            addStepToWorkflow(item.dataset.stepType, item.dataset.stepLabel, item.dataset.stepIcon);
        };
    });

    // Canvas drop handling
    canvas.ondragover = (e) => {
        e.preventDefault();
        canvas.classList.add('drag-over');
    };

    canvas.ondragleave = () => canvas.classList.remove('drag-over');

    canvas.ondrop = (e) => {
        e.preventDefault();
        canvas.classList.remove('drag-over');

        const stepType = e.dataTransfer.getData('step-type');
        const stepLabel = e.dataTransfer.getData('step-label');
        const stepIcon = e.dataTransfer.getData('step-icon');

        if (stepType) {
            addStepToWorkflow(stepType, stepLabel, stepIcon);
        }
    };
}

function handleWorkflowFile(file) {
    workflowFile = file;
    document.getElementById('workflow-filename-display').textContent = file.name;
    document.getElementById('workflow-file-info').classList.remove('hidden');

    // Reset status displays
    document.getElementById('workflow-status-display').classList.add('hidden');
    document.getElementById('workflow-result-display').classList.add('hidden');
}

function addStepToWorkflow(type, label, icon) {
    const step = {
        id: Date.now(),
        type: type,
        label: label,
        icon: icon,
        config: {}
    };

    // Steps that need configuration
    if (type === 'remove_password') {
        step.config.password = '';
    } else if (type === 'resize_image') {
        step.config.mode = 'percentage';
        step.config.percentage = 50;
    } else if (type === 'compress_pdf') {
        step.config.level = 'medium';
    } else if (type === 'crop_image') {
        step.config.x = 0;
        step.config.y = 0;
        step.config.width = 100;
        step.config.height = 100;
    } else if (type === 'rotate_pdf') {
        step.config.angle = 90;
        step.config.pages = '';
        step.config.password = '';
    } else if (type === 'protect_pdf') {
        step.config.user_password = '';
        step.config.owner_password = '';
        step.config.password = '';
    } else if (type === 'pdf_to_excel') {
        step.config.password = '';
    } else if (type === 'word_to_pptx') {
        step.config.dpi = 150;
    } else if (type === 'pdf_to_pptx') {
        step.config.dpi = 150;
        step.config.password = '';
    } else if (type === 'pdf_to_epub') {
        step.config.password = '';
    } else if (type === 'extract_text') {
        step.config.preserve_layout = false;
        step.config.password = '';
    } else if (type === 'organize_pdf') {
        step.config.page_order = '';
        step.config.password = '';
    } else if (type === 'add_page_numbers') {
        step.config.position = 'bottom-center';
        step.config.fmt = 'decimal';
        step.config.start_number = 1;
        step.config.font_size = 12;
        step.config.skip_first = 0;
        step.config.password = '';
    } else if (type === 'annotate_pdf') {
        step.config.annot_type = 'highlight';
        step.config.page = 1;
        step.config.rect = '50,700,300,730';
        step.config.content = '';
        step.config.password = '';
    } else if (type === 'edit_metadata') {
        step.config.title = '';
        step.config.author = '';
        step.config.subject = '';
        step.config.keywords = '';
        step.config.creator = '';
        step.config.clear_all = false;
        step.config.password = '';
    }

    workflowSteps.push(step);
    renderWorkflowSteps();

    // If step needs config, open modal — keep this in sync with needsConfig().
    if (needsConfig(type)) {
        openConfigModal(workflowSteps.length - 1);
    }
}

function renderWorkflowSteps() {
    const container = document.getElementById('workflow-steps-container');
    const placeholder = document.querySelector('.canvas-placeholder');

    if (workflowSteps.length === 0) {
        container.classList.add('hidden');
        placeholder.style.display = 'flex';
        return;
    }

    placeholder.style.display = 'none';
    container.classList.remove('hidden');
    container.innerHTML = '';

    workflowSteps.forEach((step, index) => {
        // Add arrow before step (except first)
        if (index > 0) {
            const arrow = document.createElement('span');
            arrow.className = 'step-arrow';
            arrow.dataset.arrowIndex = index - 1; // Arrow between step[index-1] and step[index]
            arrow.innerHTML = '<i class="fas fa-arrow-right"></i>';
            container.appendChild(arrow);
        }

        const stepCard = document.createElement('div');
        stepCard.className = 'workflow-step-card';
        stepCard.dataset.stepIndex = index;
        stepCard.innerHTML = `
            <i class="fas ${step.icon}"></i>
            <span class="step-label">${step.label}</span>
            ${needsConfig(step.type) ? `<button class="config-btn" onclick="openConfigModal(${index})"><i class="fas fa-cog"></i></button>` : ''}
            <button type="button" class="move-step" onclick="moveStep(${index}, -1)" aria-label="Move step up" ${index === 0 ? 'disabled' : ''}>&uarr;</button>
            <button type="button" class="move-step" onclick="moveStep(${index}, 1)" aria-label="Move step down" ${index === workflowSteps.length - 1 ? 'disabled' : ''}>&darr;</button>
            <button class="remove-step" onclick="removeStep(${index})"><i class="fas fa-times"></i></button>
        `;
        container.appendChild(stepCard);
    });
    if (workflowUndo) {
        const undo = document.createElement('button');
        undo.type = 'button';
        undo.className = 'secondary-btn undo-step';
        undo.textContent = 'Undo remove';
        undo.addEventListener('click', undoRemoveStep);
        container.appendChild(undo);
    }
}

function needsConfig(type) {
    return [
        'remove_password', 'resize_image', 'compress_pdf',
        'rotate_image', 'compress_image', 'convert_image', 'watermark_image',
        'csv_to_xlsx', 'xlsx_to_csv', 'ppt_to_images',
        'crop_image', 'rotate_pdf', 'protect_pdf', 'pdf_to_excel', 'pdf_to_pptx', 'pdf_to_epub', 'word_to_pptx',
        'extract_text', 'organize_pdf', 'add_page_numbers', 'annotate_pdf', 'edit_metadata',
    ].includes(type);
}

let workflowUndo = null;

function removeStep(index) {
    workflowUndo = { index: index, step: workflowSteps[index] };
    workflowSteps.splice(index, 1);
    renderWorkflowSteps();
}

function undoRemoveStep() {
    if (!workflowUndo) return;
    const at = Math.min(workflowUndo.index, workflowSteps.length);
    workflowSteps.splice(at, 0, workflowUndo.step);
    workflowUndo = null;
    renderWorkflowSteps();
}

function moveStep(index, dir) {
    const j = index + dir;
    if (j < 0 || j >= workflowSteps.length) return;
    const tmp = workflowSteps[index];
    workflowSteps[index] = workflowSteps[j];
    workflowSteps[j] = tmp;
    renderWorkflowSteps();
}
window.moveStep = moveStep;
window.undoRemoveStep = undoRemoveStep;

// Escape values destined for innerHTML attribute interpolation. Any string that
// originated from a user-typed input (watermark text, sheet name, password) must
// pass through this before being templated into an HTML string, otherwise a
// payload like `"><img src=x onerror=ffNotify(1)>` breaks out of the value="..."
// attribute and executes script when the modal is re-opened.
function escapeAttr(s) {
    return String(s ?? '').replace(/[&<>"']/g, c => ({
        '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
    }[c]));
}

function openConfigModal(index) {
    currentConfigStepIndex = index;
    const step = workflowSteps[index];
    const modal = document.getElementById('step-config-modal');
    const title = document.getElementById('config-modal-title');
    const body = document.getElementById('config-modal-body');

    title.textContent = `Configure: ${step.label}`;

    if (step.type === 'remove_password') {
        body.innerHTML = `
            <label>
                <span style="display:block; margin-bottom:0.5rem; color:var(--text-muted)">PDF Password</span>
                <input type="password" id="config-password" placeholder="Enter password" value="${escapeAttr(step.config.password)}">
            </label>
        `;
    } else if (step.type === 'resize_image') {
        body.innerHTML = `
            <label>
                <span style="display:block; margin-bottom:0.5rem; color:var(--text-muted)">Resize Percentage</span>
                <input type="number" id="config-percentage" placeholder="e.g., 50" value="${step.config.percentage || 50}" min="1" max="200">
            </label>
        `;
    } else if (step.type === 'compress_pdf') {
        const lvl = step.config.level || 'medium';
        body.innerHTML = `
            <label>
                <span style="display:block; margin-bottom:0.5rem; color:var(--text-muted)">Compression Level</span>
                <select id="config-compress-level">
                    <option value="low" ${lvl === 'low' ? 'selected' : ''}>Low: Best Quality</option>
                    <option value="medium" ${lvl === 'medium' ? 'selected' : ''}>Medium: Balanced</option>
                    <option value="high" ${lvl === 'high' ? 'selected' : ''}>High: Smallest Size</option>
                </select>
            </label>
        `;
    } else if (step.type === 'rotate_image') {
        const a = step.config.angle ?? 90;
        body.innerHTML = `
            <label><span style="display:block; margin-bottom:0.5rem; color:var(--text-muted)">Rotation angle</span>
            <select id="config-angle">
                <option value="90" ${a == 90 ? 'selected' : ''}>90°</option>
                <option value="180" ${a == 180 ? 'selected' : ''}>180°</option>
                <option value="270" ${a == 270 ? 'selected' : ''}>270°</option>
            </select></label>`;
    } else if (step.type === 'compress_image') {
        body.innerHTML = `
            <label><span style="display:block; margin-bottom:0.5rem; color:var(--text-muted)">Quality (1-100)</span>
            <input type="number" id="config-quality" min="10" max="95" value="${step.config.quality ?? 70}"></label>`;
    } else if (step.type === 'convert_image') {
        const t = step.config.target_format || 'jpg';
        body.innerHTML = `
            <label><span style="display:block; margin-bottom:0.5rem; color:var(--text-muted)">Target format</span>
            <select id="config-target-format">
                <option value="jpg" ${t === 'jpg' ? 'selected' : ''}>JPG</option>
                <option value="png" ${t === 'png' ? 'selected' : ''}>PNG</option>
                <option value="webp" ${t === 'webp' ? 'selected' : ''}>WebP</option>
            </select></label>
            <label><span style="display:block; margin:0.75rem 0 0.5rem; color:var(--text-muted)">Quality</span>
            <input type="number" id="config-quality" min="10" max="100" value="${step.config.quality ?? 90}"></label>`;
    } else if (step.type === 'watermark_image') {
        const pos = step.config.position || 'bottom-right';
        body.innerHTML = `
            <label><span style="display:block; margin-bottom:0.5rem; color:var(--text-muted)">Text</span>
            <input type="text" id="config-wm-text" value="${escapeAttr(step.config.text)}"></label>
            <label><span style="display:block; margin:0.75rem 0 0.5rem; color:var(--text-muted)">Position</span>
            <select id="config-wm-position">
                ${['top-left','top-right','center','bottom-left','bottom-right','diagonal']
                    .map(p => `<option value="${p}" ${p === pos ? 'selected' : ''}>${p}</option>`).join('')}
            </select></label>
            <label><span style="display:block; margin:0.75rem 0 0.5rem; color:var(--text-muted)">Opacity (0.05-1.0)</span>
            <input type="number" id="config-wm-opacity" min="0.05" max="1" step="0.05" value="${step.config.opacity ?? 0.4}"></label>`;
    } else if (step.type === 'csv_to_xlsx') {
        const d = step.config.delimiter || ',';
        body.innerHTML = `
            <label><span style="display:block; margin-bottom:0.5rem; color:var(--text-muted)">Delimiter</span>
            <select id="config-delimiter">
                <option value="," ${d === ',' ? 'selected' : ''}>Comma</option>
                <option value=";" ${d === ';' ? 'selected' : ''}>Semicolon</option>
                <option value="\\t" ${d === '\\t' ? 'selected' : ''}>Tab</option>
                <option value="|" ${d === '|' ? 'selected' : ''}>Pipe</option>
            </select></label>`;
    } else if (step.type === 'xlsx_to_csv') {
        body.innerHTML = `
            <label><span style="display:block; margin-bottom:0.5rem; color:var(--text-muted)">Sheet name (blank = first)</span>
            <input type="text" id="config-sheet" value="${escapeAttr(step.config.sheet)}"></label>`;
    } else if (step.type === 'ppt_to_images') {
        const f = step.config.fmt || 'png';
        body.innerHTML = `
            <label><span style="display:block; margin-bottom:0.5rem; color:var(--text-muted)">Image format</span>
            <select id="config-fmt">
                <option value="png" ${f === 'png' ? 'selected' : ''}>PNG</option>
                <option value="jpg" ${f === 'jpg' ? 'selected' : ''}>JPG</option>
            </select></label>`;
    } else if (step.type === 'crop_image') {
        body.innerHTML = `
            <label><span style="display:block; margin-bottom:0.5rem; color:var(--text-muted)">X</span>
            <input type="number" id="config-crop-x" min="0" value="${step.config.x ?? 0}"></label>
            <label><span style="display:block; margin:0.75rem 0 0.5rem; color:var(--text-muted)">Y</span>
            <input type="number" id="config-crop-y" min="0" value="${step.config.y ?? 0}"></label>
            <label><span style="display:block; margin:0.75rem 0 0.5rem; color:var(--text-muted)">Width</span>
            <input type="number" id="config-crop-width" min="1" value="${step.config.width ?? 100}"></label>
            <label><span style="display:block; margin:0.75rem 0 0.5rem; color:var(--text-muted)">Height</span>
            <input type="number" id="config-crop-height" min="1" value="${step.config.height ?? 100}"></label>`;
    } else if (step.type === 'rotate_pdf') {
        const a = step.config.angle ?? 90;
        body.innerHTML = `
            <label><span style="display:block; margin-bottom:0.5rem; color:var(--text-muted)">Rotation angle</span>
            <select id="config-rotate-pdf-angle">
                <option value="90" ${a == 90 ? 'selected' : ''}>90°</option>
                <option value="180" ${a == 180 ? 'selected' : ''}>180°</option>
                <option value="270" ${a == 270 ? 'selected' : ''}>270°</option>
                <option value="-90" ${a == -90 ? 'selected' : ''}>-90°</option>
            </select></label>
            <label><span style="display:block; margin:0.75rem 0 0.5rem; color:var(--text-muted)">Pages (blank = all, e.g. "1,3-5")</span>
            <input type="text" id="config-rotate-pdf-pages" value="${escapeAttr(step.config.pages)}"></label>
            <label><span style="display:block; margin:0.75rem 0 0.5rem; color:var(--text-muted)">Source PDF password (if protected)</span>
            <input type="password" id="config-rotate-pdf-password" value="${escapeAttr(step.config.password)}"></label>`;
    } else if (step.type === 'protect_pdf') {
        body.innerHTML = `
            <label><span style="display:block; margin-bottom:0.5rem; color:var(--text-muted)">New password (required)</span>
            <input type="password" id="config-protect-user-password" value="${escapeAttr(step.config.user_password)}"></label>
            <label><span style="display:block; margin:0.75rem 0 0.5rem; color:var(--text-muted)">Owner password (optional)</span>
            <input type="password" id="config-protect-owner-password" value="${escapeAttr(step.config.owner_password)}"></label>
            <label><span style="display:block; margin:0.75rem 0 0.5rem; color:var(--text-muted)">Source PDF password (if already protected)</span>
            <input type="password" id="config-protect-password" value="${escapeAttr(step.config.password)}"></label>`;
    } else if (step.type === 'pdf_to_excel') {
        body.innerHTML = `
            <label><span style="display:block; margin-bottom:0.5rem; color:var(--text-muted)">Source PDF password (if protected)</span>
            <input type="password" id="config-pdf-to-excel-password" value="${escapeAttr(step.config.password)}"></label>`;
    } else if (step.type === 'word_to_pptx') {
        const dpi = step.config.dpi ?? 150;
        body.innerHTML = `
            <label><span style="display:block; margin-bottom:0.5rem; color:var(--text-muted)">Rendering DPI</span>
            <select id="config-word-to-pptx-dpi">
                <option value="96" ${dpi == 96 ? 'selected' : ''}>96 DPI (fast)</option>
                <option value="150" ${dpi == 150 ? 'selected' : ''}>150 DPI (balanced)</option>
                <option value="300" ${dpi == 300 ? 'selected' : ''}>300 DPI (high quality)</option>
            </select></label>`;
    } else if (step.type === 'pdf_to_pptx') {
        const dpi = step.config.dpi ?? 150;
        body.innerHTML = `
            <label><span style="display:block; margin-bottom:0.5rem; color:var(--text-muted)">Rendering DPI</span>
            <select id="config-pdf-to-pptx-dpi">
                <option value="96" ${dpi == 96 ? 'selected' : ''}>96 DPI (fast)</option>
                <option value="150" ${dpi == 150 ? 'selected' : ''}>150 DPI (balanced)</option>
                <option value="300" ${dpi == 300 ? 'selected' : ''}>300 DPI (high quality)</option>
            </select></label>
            <label><span style="display:block; margin:0.75rem 0 0.5rem; color:var(--text-muted)">Source PDF password (if protected)</span>
            <input type="password" id="config-pdf-to-pptx-password" value="${escapeAttr(step.config.password)}"></label>`;
    } else if (step.type === 'pdf_to_epub') {
        body.innerHTML = `
            <label><span style="display:block; margin-bottom:0.5rem; color:var(--text-muted)">Source PDF password (if protected)</span>
            <input type="password" id="config-pdf-to-epub-password" value="${escapeAttr(step.config.password)}"></label>`;
    } else if (step.type === 'extract_text') {
        body.innerHTML = `
            <label><input type="checkbox" id="config-extract-text-preserve" ${step.config.preserve_layout ? 'checked' : ''}>
            <span>Preserve original layout</span></label>
            <label><span style="display:block; margin:0.75rem 0 0.5rem; color:var(--text-muted)">Source PDF password (if protected)</span>
            <input type="password" id="config-extract-text-password" value="${escapeAttr(step.config.password)}"></label>`;
    } else if (step.type === 'organize_pdf') {
        body.innerHTML = `
            <label><span style="display:block; margin-bottom:0.5rem; color:var(--text-muted)">Page order (comma-separated, e.g. "3,1,2"; repeat to duplicate, omit to delete)</span>
            <input type="text" id="config-organize-page-order" value="${escapeAttr(step.config.page_order)}"></label>
            <label><span style="display:block; margin:0.75rem 0 0.5rem; color:var(--text-muted)">Source PDF password (if protected)</span>
            <input type="password" id="config-organize-password" value="${escapeAttr(step.config.password)}"></label>`;
    } else if (step.type === 'add_page_numbers') {
        const pos = step.config.position || 'bottom-center';
        const fmt = step.config.fmt || 'decimal';
        body.innerHTML = `
            <label><span style="display:block; margin-bottom:0.5rem; color:var(--text-muted)">Position</span>
            <select id="config-pagenum-position">
                ${['bottom-center','bottom-left','bottom-right','top-center','top-left','top-right']
                    .map(p => `<option value="${p}" ${p === pos ? 'selected' : ''}>${p}</option>`).join('')}
            </select></label>
            <label><span style="display:block; margin:0.75rem 0 0.5rem; color:var(--text-muted)">Number format</span>
            <select id="config-pagenum-fmt">
                <option value="decimal" ${fmt === 'decimal' ? 'selected' : ''}>1, 2, 3 ...</option>
                <option value="roman" ${fmt === 'roman' ? 'selected' : ''}>I, II, III ...</option>
                <option value="alpha" ${fmt === 'alpha' ? 'selected' : ''}>A, B, C ...</option>
            </select></label>
            <label><span style="display:block; margin:0.75rem 0 0.5rem; color:var(--text-muted)">Start number</span>
            <input type="number" id="config-pagenum-start" min="1" value="${step.config.start_number ?? 1}"></label>
            <label><span style="display:block; margin:0.75rem 0 0.5rem; color:var(--text-muted)">Font size</span>
            <input type="number" id="config-pagenum-fontsize" min="6" max="48" value="${step.config.font_size ?? 12}"></label>
            <label><span style="display:block; margin:0.75rem 0 0.5rem; color:var(--text-muted)">Skip first N pages</span>
            <input type="number" id="config-pagenum-skip" min="0" value="${step.config.skip_first ?? 0}"></label>
            <label><span style="display:block; margin:0.75rem 0 0.5rem; color:var(--text-muted)">Source PDF password (if protected)</span>
            <input type="password" id="config-pagenum-password" value="${escapeAttr(step.config.password)}"></label>`;
    } else if (step.type === 'annotate_pdf') {
        const t = step.config.annot_type || 'highlight';
        body.innerHTML = `
            <label><span style="display:block; margin-bottom:0.5rem; color:var(--text-muted)">Annotation type</span>
            <select id="config-annotate-type">
                ${['highlight','underline','strikeout','note','text','redact']
                    .map(v => `<option value="${v}" ${v === t ? 'selected' : ''}>${v}</option>`).join('')}
            </select></label>
            <label><span style="display:block; margin:0.75rem 0 0.5rem; color:var(--text-muted)">Page number</span>
            <input type="number" id="config-annotate-page" min="1" value="${step.config.page ?? 1}"></label>
            <label><span style="display:block; margin:0.75rem 0 0.5rem; color:var(--text-muted)">Rect (x0,y0,x1,y1 in PDF points)</span>
            <input type="text" id="config-annotate-rect" value="${escapeAttr(step.config.rect)}"></label>
            <label><span style="display:block; margin:0.75rem 0 0.5rem; color:var(--text-muted)">Note/text content (for Note/Text types)</span>
            <input type="text" id="config-annotate-content" value="${escapeAttr(step.config.content)}"></label>
            <label><span style="display:block; margin:0.75rem 0 0.5rem; color:var(--text-muted)">Source PDF password (if protected)</span>
            <input type="password" id="config-annotate-password" value="${escapeAttr(step.config.password)}"></label>`;
    } else if (step.type === 'edit_metadata') {
        body.innerHTML = `
            <label><span style="display:block; margin-bottom:0.5rem; color:var(--text-muted)">Title</span>
            <input type="text" id="config-metadata-title" value="${escapeAttr(step.config.title)}"></label>
            <label><span style="display:block; margin:0.75rem 0 0.5rem; color:var(--text-muted)">Author</span>
            <input type="text" id="config-metadata-author" value="${escapeAttr(step.config.author)}"></label>
            <label><span style="display:block; margin:0.75rem 0 0.5rem; color:var(--text-muted)">Subject</span>
            <input type="text" id="config-metadata-subject" value="${escapeAttr(step.config.subject)}"></label>
            <label><span style="display:block; margin:0.75rem 0 0.5rem; color:var(--text-muted)">Keywords</span>
            <input type="text" id="config-metadata-keywords" value="${escapeAttr(step.config.keywords)}"></label>
            <label><span style="display:block; margin:0.75rem 0 0.5rem; color:var(--text-muted)">Creator</span>
            <input type="text" id="config-metadata-creator" value="${escapeAttr(step.config.creator)}"></label>
            <label><input type="checkbox" id="config-metadata-clear-all" ${step.config.clear_all ? 'checked' : ''}>
            <span>Clear all metadata</span></label>
            <label><span style="display:block; margin:0.75rem 0 0.5rem; color:var(--text-muted)">Source PDF password (if protected)</span>
            <input type="password" id="config-metadata-password" value="${escapeAttr(step.config.password)}"></label>`;
    }

    modal.classList.remove('hidden');
}

function closeConfigModal() {
    document.getElementById('step-config-modal').classList.add('hidden');
    currentConfigStepIndex = null;
}

function saveStepConfig() {
    if (currentConfigStepIndex === null) return;

    const step = workflowSteps[currentConfigStepIndex];

    if (step.type === 'remove_password') {
        step.config.password = document.getElementById('config-password').value;
    } else if (step.type === 'resize_image') {
        step.config.percentage = parseInt(document.getElementById('config-percentage').value) || 50;
    } else if (step.type === 'compress_pdf') {
        step.config.level = document.getElementById('config-compress-level').value;
    } else if (step.type === 'rotate_image') {
        step.config.angle = parseInt(document.getElementById('config-angle').value) || 90;
    } else if (step.type === 'compress_image') {
        step.config.quality = parseInt(document.getElementById('config-quality').value) || 70;
    } else if (step.type === 'convert_image') {
        step.config.target_format = document.getElementById('config-target-format').value;
        step.config.quality = parseInt(document.getElementById('config-quality').value) || 90;
    } else if (step.type === 'watermark_image') {
        step.config.text = document.getElementById('config-wm-text').value;
        step.config.position = document.getElementById('config-wm-position').value;
        step.config.opacity = parseFloat(document.getElementById('config-wm-opacity').value) || 0.4;
    } else if (step.type === 'csv_to_xlsx') {
        step.config.delimiter = document.getElementById('config-delimiter').value;
    } else if (step.type === 'xlsx_to_csv') {
        step.config.sheet = document.getElementById('config-sheet').value;
    } else if (step.type === 'ppt_to_images') {
        step.config.fmt = document.getElementById('config-fmt').value;
    } else if (step.type === 'crop_image') {
        step.config.x = parseInt(document.getElementById('config-crop-x').value) || 0;
        step.config.y = parseInt(document.getElementById('config-crop-y').value) || 0;
        step.config.width = parseInt(document.getElementById('config-crop-width').value) || 100;
        step.config.height = parseInt(document.getElementById('config-crop-height').value) || 100;
    } else if (step.type === 'rotate_pdf') {
        step.config.angle = parseInt(document.getElementById('config-rotate-pdf-angle').value) || 90;
        step.config.pages = document.getElementById('config-rotate-pdf-pages').value;
        step.config.password = document.getElementById('config-rotate-pdf-password').value;
    } else if (step.type === 'protect_pdf') {
        step.config.user_password = document.getElementById('config-protect-user-password').value;
        step.config.owner_password = document.getElementById('config-protect-owner-password').value;
        step.config.password = document.getElementById('config-protect-password').value;
    } else if (step.type === 'pdf_to_excel') {
        step.config.password = document.getElementById('config-pdf-to-excel-password').value;
    } else if (step.type === 'word_to_pptx') {
        step.config.dpi = parseInt(document.getElementById('config-word-to-pptx-dpi').value) || 150;
    } else if (step.type === 'pdf_to_pptx') {
        step.config.dpi = parseInt(document.getElementById('config-pdf-to-pptx-dpi').value) || 150;
        step.config.password = document.getElementById('config-pdf-to-pptx-password').value;
    } else if (step.type === 'pdf_to_epub') {
        step.config.password = document.getElementById('config-pdf-to-epub-password').value;
    } else if (step.type === 'extract_text') {
        step.config.preserve_layout = document.getElementById('config-extract-text-preserve').checked;
        step.config.password = document.getElementById('config-extract-text-password').value;
    } else if (step.type === 'organize_pdf') {
        step.config.page_order = document.getElementById('config-organize-page-order').value;
        step.config.password = document.getElementById('config-organize-password').value;
    } else if (step.type === 'add_page_numbers') {
        step.config.position = document.getElementById('config-pagenum-position').value;
        step.config.fmt = document.getElementById('config-pagenum-fmt').value;
        step.config.start_number = parseInt(document.getElementById('config-pagenum-start').value) || 1;
        step.config.font_size = parseInt(document.getElementById('config-pagenum-fontsize').value) || 12;
        step.config.skip_first = parseInt(document.getElementById('config-pagenum-skip').value) || 0;
        step.config.password = document.getElementById('config-pagenum-password').value;
    } else if (step.type === 'annotate_pdf') {
        step.config.annot_type = document.getElementById('config-annotate-type').value;
        step.config.page = parseInt(document.getElementById('config-annotate-page').value) || 1;
        step.config.rect = document.getElementById('config-annotate-rect').value;
        step.config.content = document.getElementById('config-annotate-content').value;
        step.config.password = document.getElementById('config-annotate-password').value;
    } else if (step.type === 'edit_metadata') {
        step.config.title = document.getElementById('config-metadata-title').value;
        step.config.author = document.getElementById('config-metadata-author').value;
        step.config.subject = document.getElementById('config-metadata-subject').value;
        step.config.keywords = document.getElementById('config-metadata-keywords').value;
        step.config.creator = document.getElementById('config-metadata-creator').value;
        step.config.clear_all = document.getElementById('config-metadata-clear-all').checked;
        step.config.password = document.getElementById('config-metadata-password').value;
    }

    closeConfigModal();
    renderWorkflowSteps();
}

// Translates the form-friendly shape a step's config is edited in (e.g. a
// comma-separated "page_order" string, flat annotation fields) into the
// shape the /api/workflow/execute dispatcher expects.
function buildStepConfigPayload(step) {
    if (step.type === 'organize_pdf') {
        const pageOrder = (step.config.page_order || '')
            .split(',')
            .map(v => parseInt(v.trim(), 10))
            .filter(v => !isNaN(v));
        return { page_order: pageOrder, password: step.config.password || null };
    }
    if (step.type === 'annotate_pdf') {
        const rectParts = (step.config.rect || '')
            .split(',')
            .map(v => parseFloat(v.trim()))
            .filter(v => !isNaN(v));
        const annotation = {
            type: step.config.annot_type || 'highlight',
            page: step.config.page || 1,
            rect: rectParts.length === 4 ? rectParts : [50, 700, 300, 730],
        };
        if (step.config.content) annotation.content = step.config.content;
        return { annotations: [annotation], password: step.config.password || null };
    }
    if (step.type === 'edit_metadata') {
        const blankToNull = v => (v === '' || v == null) ? null : v;
        return {
            title: blankToNull(step.config.title),
            author: blankToNull(step.config.author),
            subject: blankToNull(step.config.subject),
            keywords: blankToNull(step.config.keywords),
            creator: blankToNull(step.config.creator),
            clear_all: !!step.config.clear_all,
            password: step.config.password || null,
        };
    }
    return step.config;
}

async function runWorkflow() {
    if (!workflowFile) {
        ffNotify('Please select an input file first.');
        return;
    }

    if (workflowSteps.length === 0) {
        ffNotify('Please add at least one step to your workflow.');
        return;
    }

    // Validate required configs
    for (const step of workflowSteps) {
        if (step.type === 'remove_password' && !step.config.password) {
            ffNotify(`Please configure the password for "${step.label}" step.`);
            return;
        }
        if (step.type === 'protect_pdf' && !step.config.user_password) {
            ffNotify(`Please set a new password for "${step.label}" step.`);
            return;
        }
        if (step.type === 'organize_pdf' && !(step.config.page_order || '').trim()) {
            ffNotify(`Please set a page order for "${step.label}" step.`);
            return;
        }
    }

    if (!ffCheckUploadSize(workflowFile)) return;

    const statusDisplay = document.getElementById('workflow-status-display');
    const statusText = document.getElementById('workflow-status-text');
    const resultDisplay = document.getElementById('workflow-result-display');

    statusDisplay.classList.remove('hidden');
    resultDisplay.classList.add('hidden');

    // Initialize all steps as pending
    setAllStepsPending();
    updateStatusText('Starting workflow...', 0, workflowSteps.length);

    const formData = new FormData();
    formData.append('file', workflowFile);
    formData.append('steps', JSON.stringify(workflowSteps.map(s => ({
        type: s.type,
        label: s.label,
        config: buildStepConfigPayload(s)
    }))));

    const abort = ffStartInflight();
    let jobId = null;
    let terminalSeen = false;

    const acceptWorkflowEvent = (data) => {
        if (data.event === 'start') {
            jobId = data.job_id || jobId;
            return;
        }
        if (data.event === 'complete' || data.event === 'error') terminalSeen = true;
        handleWorkflowEvent(data, statusDisplay, resultDisplay);
    };

    try {
        const response = await ffProcess('/api/workflow/execute', formData, {
            signal: abort && abort.signal,
        });

        if (!response.ok && !response.headers.get('content-type')?.includes('text/event-stream')) {
            throw new Error(await ffMessageFromResponse(response));
        }

        // Read SSE stream
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';

        let streamError = null;
        try {
            while (true) {
                const { value, done } = await reader.read();
                if (done) break;

                buffer += decoder.decode(value, { stream: true });

                // Process complete SSE messages
                const lines = buffer.split('\n\n');
                buffer = lines.pop(); // Keep incomplete message in buffer

                for (const line of lines) {
                    if (line.startsWith('data: ')) {
                        try {
                            acceptWorkflowEvent(JSON.parse(line.substring(6)));
                        } catch (e) {
                            console.error('Failed to parse SSE data:', e);
                        }
                    }
                }
            }
        } catch (error) {
            streamError = error;
        }

        if (!terminalSeen && !(abort && abort.signal.aborted)) {
            if (!jobId) throw streamError || new Error('The workflow stream ended before it started.');
            const finalEvent = await pollJobStatus(jobId, statusText, 6 * 60 * 1000, 'workflow');
            if (!finalEvent) {
                throw streamError || new Error('The workflow did not complete in time. Please try again.');
            }
            acceptWorkflowEvent(finalEvent);
        }
    } catch (error) {
        console.error('Error:', error);
        statusDisplay.classList.add('hidden');
        clearStepStates();
        if (!ffIsAbort(error)) ffNotify('Workflow error: ' + error.message);
    } finally {
        ffSetCancelVisible(false);
    }
}

// Minimum time a step stays visibly "processing", in ms. The server used to
// sleep 1s before every step so the animation couldn't be missed, which cost
// every workflow N seconds of real latency on the app's most expensive
// endpoint. Holding the class here instead keeps the feedback legible without
// the server ever waiting — and a step that finishes in 40ms is honest.
const MIN_STEP_VISIBLE_MS = 250;
const stepStartedAt = new Map();

function handleWorkflowEvent(data, statusDisplay, resultDisplay) {
    switch (data.event) {
        case 'step_start':
            stepStartedAt.set(data.step, Date.now());
            setStepProcessing(data.step);
            updateStatusText(`Processing: ${data.label}`, data.step + 1, data.total);
            break;

        case 'step_complete': {
            const started = stepStartedAt.get(data.step);
            stepStartedAt.delete(data.step);
            const elapsed = started === undefined ? MIN_STEP_VISIBLE_MS : Date.now() - started;
            const hold = Math.max(0, MIN_STEP_VISIBLE_MS - elapsed);
            if (hold === 0) {
                setStepCompleted(data.step);
            } else {
                setTimeout(() => setStepCompleted(data.step), hold);
            }
            break;
        }

        case 'complete':
            statusDisplay.classList.add('hidden');
            resultDisplay.classList.remove('hidden');
            document.getElementById('workflow-result-message').textContent = `${data.message}: ${data.filename}`;
            updateDownloadLink(document.getElementById('workflow-download-link'), data.download_token);
            // Keep completed states visible for a moment
            setTimeout(() => clearStepStates(), 3000);
            break;

        case 'error':
            statusDisplay.classList.add('hidden');
            clearStepStates();
            ffNotify('Workflow error: ' + data.detail);
            break;
    }
}

function setAllStepsPending() {
    const cards = document.querySelectorAll('.workflow-step-card');
    const arrows = document.querySelectorAll('.step-arrow');

    cards.forEach(card => {
        card.classList.remove('processing', 'completed');
        card.classList.add('pending');
    });

    arrows.forEach(arrow => {
        arrow.classList.remove('processing', 'completed');
    });
}

function setStepProcessing(index) {
    const card = document.querySelector(`.workflow-step-card[data-step-index="${index}"]`);
    if (card) {
        card.classList.remove('pending', 'completed');
        card.classList.add('processing');
    }

    // Highlight arrow leading to this step
    if (index > 0) {
        const arrow = document.querySelector(`.step-arrow[data-arrow-index="${index - 1}"]`);
        if (arrow) {
            arrow.classList.add('processing');
        }
    }
}

function setStepCompleted(index) {
    const card = document.querySelector(`.workflow-step-card[data-step-index="${index}"]`);
    if (card) {
        card.classList.remove('pending', 'processing');
        card.classList.add('completed');
    }

    // Mark arrow as completed
    if (index > 0) {
        const arrow = document.querySelector(`.step-arrow[data-arrow-index="${index - 1}"]`);
        if (arrow) {
            arrow.classList.remove('processing');
            arrow.classList.add('completed');
        }
    }
}

function clearStepStates() {
    const cards = document.querySelectorAll('.workflow-step-card');
    const arrows = document.querySelectorAll('.step-arrow');

    cards.forEach(card => {
        card.classList.remove('pending', 'processing', 'completed');
    });

    arrows.forEach(arrow => {
        arrow.classList.remove('processing', 'completed');
    });
}

function updateStatusText(message, currentStep, totalSteps) {
    const statusText = document.getElementById('workflow-status-text');
    statusText.innerHTML = `
        <span>${message}</span>
        <span class="workflow-step-progress">Step ${currentStep} of ${totalSteps}</span>
    `;
}

// Reset workflow UI
function resetWorkflowUI() {
    workflowFile = null;
    workflowSteps = [];
    const fileInput = document.getElementById('workflow-file-input');
    if (fileInput) fileInput.value = '';
    const filenameDisplay = document.getElementById('workflow-filename-display');
    if (filenameDisplay) filenameDisplay.textContent = 'No file selected';
    const fileInfo = document.getElementById('workflow-file-info');
    if (fileInfo) fileInfo.classList.add('hidden');
    renderWorkflowSteps();
    document.getElementById('workflow-status-display')?.classList.add('hidden');
    document.getElementById('workflow-result-display')?.classList.add('hidden');
}

// Extend resetUI to include workflow reset
const originalResetUI = resetUI;
resetUI = function () {
    originalResetUI();
    resetWorkflowUI();
    selectedExcelFile = null;
    selectedExcelFiles = [];
    if (excelFileInput) { excelFileInput.value = ''; excelFileInput.multiple = false; }
    if (excelFilenameDisplay) excelFilenameDisplay.textContent = 'No file selected';
    document.getElementById('excel-file-info')?.classList.add('hidden');
    hideExcelActionAreas();
    document.getElementById('excel-status-display')?.classList.add('hidden');
    document.getElementById('excel-result-display')?.classList.add('hidden');

    selectedPptFile = null;
    selectedPptFiles = [];
    if (pptFileInput) { pptFileInput.value = ''; pptFileInput.multiple = false; }
    if (pptFilenameDisplay) pptFilenameDisplay.textContent = 'No file selected';
    document.getElementById('ppt-file-info')?.classList.add('hidden');
    hidePptActionAreas();
    document.getElementById('ppt-status-display')?.classList.add('hidden');
    document.getElementById('ppt-result-display')?.classList.add('hidden');
};

// === Image Page: new feature handlers (rotate, compress, convert format, watermark) ===

function showImageOptionPanel(id) {
    if (!selectedImageFile) { ffNotify('Please select an image first.'); return false; }
    hideImageActionAreas();
    document.getElementById('image-result-display')?.classList.add('hidden');
    document.getElementById(id).classList.remove('hidden');
    return true;
}

document.getElementById('convert-jpeg-btn')?.addEventListener('click', e => {
    if (!showImageOptionPanel('convert-options')) return;
    ffSelectActionCard(e.currentTarget);
    ffPreviewJpegQuality(selectedImageFile, qualitySlider.value,
        'jpeg-quality-preview-img', 'jpeg-quality-preview-label', 'jpeg-quality-preview');
});
document.getElementById('resize-btn')?.addEventListener('click', e => {
    if (showImageOptionPanel('resize-options')) ffSelectActionCard(e.currentTarget);
});
document.getElementById('crop-btn')?.addEventListener('click', e => {
    if (!showImageOptionPanel('crop-options')) return;
    ffSelectActionCard(e.currentTarget);
    initCropper();
});
document.getElementById('process-resize-image-btn')?.addEventListener('click', resizeImage);
document.getElementById('process-crop-image-btn')?.addEventListener('click', cropImage);

document.getElementById('rotate-image-btn')?.addEventListener('click', (e) => {
    if (showImageOptionPanel('rotate-image-area')) ffSelectActionCard(e.currentTarget);
});
document.getElementById('compress-image-btn')?.addEventListener('click', (e) => {
    if (!showImageOptionPanel('compress-image-area')) return;
    ffSelectActionCard(e.currentTarget);
    if (selectedImageFile && compressImgQ) {
        const fmt = /\.png$/i.test(selectedImageFile.name) ? 'png' : (/\.webp$/i.test(selectedImageFile.name) ? 'webp' : 'jpeg');
        ffPreviewJpegQuality(selectedImageFile, compressImgQ.value,
            'compress-image-preview-img', 'compress-image-preview-label', 'compress-image-preview', fmt);
    }
});
document.getElementById('convert-format-btn')?.addEventListener('click', (e) => {
    if (showImageOptionPanel('convert-format-area')) ffSelectActionCard(e.currentTarget);
});
document.getElementById('watermark-image-btn')?.addEventListener('click', (e) => {
    if (showImageOptionPanel('watermark-image-area')) ffSelectActionCard(e.currentTarget);
});

const compressImgQ = document.getElementById('compress-image-quality');
if (compressImgQ) compressImgQ.addEventListener('input', e => {
    document.getElementById('compress-image-quality-value').textContent = e.target.value;
    if (selectedImageFile) {
        const fmt = /\.png$/i.test(selectedImageFile.name) ? 'png' : (/\.webp$/i.test(selectedImageFile.name) ? 'webp' : 'jpeg');
        ffPreviewJpegQuality(selectedImageFile, e.target.value,
            'compress-image-preview-img', 'compress-image-preview-label', 'compress-image-preview', fmt);
    }
});
const convertFmtQ = document.getElementById('convert-format-quality');
if (convertFmtQ) convertFmtQ.addEventListener('input', e => {
    document.getElementById('convert-format-quality-value').textContent = e.target.value;
});
const wmImgOpacity = document.getElementById('watermark-image-opacity');
if (wmImgOpacity) wmImgOpacity.addEventListener('input', e => {
    document.getElementById('watermark-image-opacity-value').textContent = e.target.value;
});

document.getElementById('process-rotate-image-btn')?.addEventListener('click', () => {
    if (!selectedImageFile) { ffNotify('Please select an image first.'); return; }
    const angle = document.getElementById('rotate-angle').value;
    const fd = new FormData();
    fd.append('file', selectedImageFile);
    fd.append('angle', angle);
    processImageAction('/api/image/rotate', `Rotating ${angle}°...`, fd);
});

document.getElementById('process-compress-image-btn')?.addEventListener('click', () => {
    if (!selectedImageFile) { ffNotify('Please select an image first.'); return; }
    const quality = document.getElementById('compress-image-quality').value;
    const fd = new FormData();
    fd.append('file', selectedImageFile);
    fd.append('quality', quality);
    processImageAction('/api/image/compress', 'Compressing image...', fd);
});

document.getElementById('process-convert-format-btn')?.addEventListener('click', () => {
    if (!selectedImageFile) { ffNotify('Please select an image first.'); return; }
    const target_format = document.getElementById('convert-target-format').value;
    const quality = document.getElementById('convert-format-quality').value;
    const fd = new FormData();
    fd.append('file', selectedImageFile);
    fd.append('target_format', target_format);
    fd.append('quality', quality);
    processImageAction('/api/image/convert', `Converting to ${target_format.toUpperCase()}...`, fd);
});

document.getElementById('process-watermark-image-btn')?.addEventListener('click', () => {
    if (!selectedImageFile) { ffNotify('Please select an image first.'); return; }
    const text = document.getElementById('watermark-image-text').value.trim();
    if (!text) { ffNotify('Please enter watermark text.'); return; }
    const position = document.getElementById('watermark-image-position').value;
    const opacity = document.getElementById('watermark-image-opacity').value;
    const color = document.getElementById('watermark-image-color').value;

    const fd = new FormData();
    fd.append('file', selectedImageFile);
    fd.append('text', text);
    fd.append('position', position);
    fd.append('opacity', opacity);
    fd.append('color', color);
    processImageAction('/api/image/watermark', 'Adding watermark...', fd);
});

// === Excel Page ===

let selectedExcelFile = null;
let selectedExcelFiles = [];

const excelDropZone = document.getElementById('excel-drop-zone');
const excelFileInput = document.getElementById('excel-file-input');
const excelFilenameDisplay = document.getElementById('excel-filename-display');
const excelFileInfo = document.getElementById('excel-file-info');

function handleExcelFiles(files) {
    if (excelFileInput.multiple) {
        const xlsxs = files.filter(f => f.name.toLowerCase().endsWith('.xlsx'));
        if (xlsxs.length === 0) {
            selectedExcelFiles = [];
            selectedExcelFile = null;
            excelFileInput.value = '';
            excelFilenameDisplay.textContent = 'No file selected';
            excelFileInfo.classList.add('hidden');
            ffNotify('Please select .xlsx files.');
            return;
        }
        selectedExcelFiles = xlsxs;
        selectedExcelFile = xlsxs[0];
        excelFilenameDisplay.textContent = xlsxs.length === 1
            ? xlsxs[0].name
            : `${xlsxs.length} files: ${xlsxs.map(f => f.name).join(', ')}`;
    } else {
        selectedExcelFile = files[0];
        selectedExcelFiles = [files[0]];
        excelFilenameDisplay.textContent = files[0].name;
    }
    excelFileInfo.classList.remove('hidden');
    document.getElementById('excel-status-display').classList.add('hidden');
    document.getElementById('excel-result-display').classList.add('hidden');
    ffUpdateStepTracker('excel', 2);
    ffConsumePendingOp();
    try { ffTrack('file_selected', { tool_name: currentOp || currentTool || 'excel', file_type: ffExtractFileType(files && files[0]) || 'xlsx' }); } catch (e) { }
}

if (excelDropZone) {
    excelDropZone.onclick = () => excelFileInput.click();
    excelFileInput.onchange = e => { if (e.target.files.length) handleExcelFiles(Array.from(e.target.files)); };
    excelDropZone.ondragover = e => { e.preventDefault(); excelDropZone.classList.add('drag-over'); };
    excelDropZone.ondragleave = () => excelDropZone.classList.remove('drag-over');
    excelDropZone.ondrop = e => {
        e.preventDefault();
        excelDropZone.classList.remove('drag-over');
        if (e.dataTransfer.files.length) handleExcelFiles(Array.from(e.dataTransfer.files));
    };
}

function hideExcelActionAreas() {
    ['excel-to-pdf-area', 'csv-to-xlsx-area', 'xlsx-to-csv-area', 'merge-excel-area']
        .forEach(id => document.getElementById(id)?.classList.add('hidden'));
    document.getElementById('excel-result-display')?.classList.add('hidden');
    ffClearActionSelection(document.getElementById('excel-page'));
}

function setExcelMergeMode(on) {
    if (excelFileInput) excelFileInput.multiple = !!on;
    if (!on) selectedExcelFiles = [];
}

document.getElementById('excel-to-pdf-btn')?.addEventListener('click', (e) => {
    setExcelMergeMode(false);
    if (!selectedExcelFile) { ffNotify('Please select a file.'); return; }
    hideExcelActionAreas();
    document.getElementById('excel-to-pdf-area').classList.remove('hidden');
    ffSelectActionCard(e.currentTarget);
});
document.getElementById('csv-to-xlsx-btn')?.addEventListener('click', (e) => {
    setExcelMergeMode(false);
    if (!selectedExcelFile) { ffNotify('Please select a CSV file.'); return; }
    hideExcelActionAreas();
    document.getElementById('csv-to-xlsx-area').classList.remove('hidden');
    ffSelectActionCard(e.currentTarget);
});
document.getElementById('xlsx-to-csv-btn')?.addEventListener('click', (e) => {
    setExcelMergeMode(false);
    if (!selectedExcelFile) { ffNotify('Please select a file.'); return; }
    hideExcelActionAreas();
    document.getElementById('xlsx-to-csv-area').classList.remove('hidden');
    ffSelectActionCard(e.currentTarget);
});
document.getElementById('merge-excel-btn')?.addEventListener('click', (e) => {
    setExcelMergeMode(true);
    hideExcelActionAreas();
    document.getElementById('merge-excel-area').classList.remove('hidden');
    ffSelectActionCard(e.currentTarget);
});

async function processExcelAction(url, text, formData) {
    const statusDisplay = document.getElementById('excel-status-display');
    const statusText = document.getElementById('excel-status-text');
    const resultDisplay = document.getElementById('excel-result-display');

    statusDisplay.classList.remove('hidden');
    statusText.textContent = text;
    resultDisplay.classList.add('hidden');

    try {
        const response = await ffProcess(url, formData);
        if (response.ok) {
            const data = await response.json();
            resultDisplay.classList.remove('hidden');
            document.getElementById('excel-result-message').textContent = `${data.message}: ${data.filename}`;
            updateDownloadLink(document.getElementById('excel-download-link'), data.download_token);
            ffUpdateStepTracker('excel', 3);
        } else {
            const data = await response.json().catch(() => ({ detail: 'Failed' }));
            ffNotify('Error: ' + (data.detail || 'Failed'));
        }
    } catch (e) {
        ffNotify('Error: ' + e.message);
    } finally {
        statusDisplay.classList.add('hidden');
    }
}

document.getElementById('process-excel-to-pdf-btn')?.addEventListener('click', () => {
    if (!selectedExcelFile) { ffNotify('Please select a file.'); return; }
    const fd = new FormData();
    fd.append('file', selectedExcelFile);
    processExcelAction('/api/excel/to-pdf', 'Converting Excel to PDF...', fd);
});
document.getElementById('process-csv-to-xlsx-btn')?.addEventListener('click', () => {
    if (!selectedExcelFile) { ffNotify('Please select a CSV file.'); return; }
    const fd = new FormData();
    fd.append('file', selectedExcelFile);
    fd.append('delimiter', document.getElementById('csv-delimiter').value);
    processExcelAction('/api/excel/csv-to-xlsx', 'Converting CSV to XLSX...', fd);
});
document.getElementById('process-xlsx-to-csv-btn')?.addEventListener('click', () => {
    if (!selectedExcelFile) { ffNotify('Please select an XLSX file.'); return; }
    const fd = new FormData();
    fd.append('file', selectedExcelFile);
    const sheet = document.getElementById('xlsx-sheet-name').value.trim();
    if (sheet) fd.append('sheet', sheet);
    processExcelAction('/api/excel/xlsx-to-csv', 'Exporting CSV...', fd);
});
document.getElementById('process-merge-excel-btn')?.addEventListener('click', () => {
    if (!selectedExcelFiles || selectedExcelFiles.length < 2) {
        ffNotify('Please select at least two .xlsx files.'); return;
    }
    const fd = new FormData();
    selectedExcelFiles.forEach(f => fd.append('files', f));
    processExcelAction('/api/excel/merge', `Merging ${selectedExcelFiles.length} workbooks...`, fd);
});

// === PPT Page ===

let selectedPptFile = null;
let selectedPptFiles = [];

const pptDropZone = document.getElementById('ppt-drop-zone');
const pptFileInput = document.getElementById('ppt-file-input');
const pptFilenameDisplay = document.getElementById('ppt-filename-display');
const pptFileInfo = document.getElementById('ppt-file-info');

function handlePptFiles(files) {
    if (pptFileInput.multiple) {
        const pptxs = files.filter(f => f.name.toLowerCase().endsWith('.pptx'));
        if (pptxs.length === 0) {
            selectedPptFiles = [];
            selectedPptFile = null;
            pptFileInput.value = '';
            pptFilenameDisplay.textContent = 'No file selected';
            pptFileInfo.classList.add('hidden');
            ffNotify('Please select .pptx files.');
            return;
        }
        selectedPptFiles = pptxs;
        selectedPptFile = pptxs[0];
        pptFilenameDisplay.textContent = pptxs.length === 1
            ? pptxs[0].name
            : `${pptxs.length} files: ${pptxs.map(f => f.name).join(', ')}`;
    } else {
        if (!files[0].name.toLowerCase().endsWith('.pptx')) {
            ffNotify('Please select a .pptx file.'); return;
        }
        selectedPptFile = files[0];
        selectedPptFiles = [files[0]];
        pptFilenameDisplay.textContent = files[0].name;
    }
    pptFileInfo.classList.remove('hidden');
    document.getElementById('ppt-status-display').classList.add('hidden');
    document.getElementById('ppt-result-display').classList.add('hidden');
    ffUpdateStepTracker('ppt', 2);
    ffConsumePendingOp();
    try { ffTrack('file_selected', { tool_name: currentOp || currentTool || 'ppt', file_type: ffExtractFileType(files && files[0]) || 'pptx' }); } catch (e) { }
}

if (pptDropZone) {
    pptDropZone.onclick = () => pptFileInput.click();
    pptFileInput.onchange = e => { if (e.target.files.length) handlePptFiles(Array.from(e.target.files)); };
    pptDropZone.ondragover = e => { e.preventDefault(); pptDropZone.classList.add('drag-over'); };
    pptDropZone.ondragleave = () => pptDropZone.classList.remove('drag-over');
    pptDropZone.ondrop = e => {
        e.preventDefault();
        pptDropZone.classList.remove('drag-over');
        if (e.dataTransfer.files.length) handlePptFiles(Array.from(e.dataTransfer.files));
    };
}

function hidePptActionAreas() {
    ['ppt-to-pdf-area', 'ppt-to-images-area', 'merge-ppt-area']
        .forEach(id => document.getElementById(id)?.classList.add('hidden'));
    document.getElementById('ppt-result-display')?.classList.add('hidden');
    ffClearActionSelection(document.getElementById('ppt-page'));
}

function setPptMergeMode(on) {
    if (pptFileInput) pptFileInput.multiple = !!on;
    if (!on) selectedPptFiles = [];
}

document.getElementById('ppt-to-pdf-btn')?.addEventListener('click', (e) => {
    setPptMergeMode(false);
    if (!selectedPptFile) { ffNotify('Please select a PPTX file.'); return; }
    hidePptActionAreas();
    document.getElementById('ppt-to-pdf-area').classList.remove('hidden');
    ffSelectActionCard(e.currentTarget);
});
document.getElementById('ppt-to-images-btn')?.addEventListener('click', (e) => {
    setPptMergeMode(false);
    if (!selectedPptFile) { ffNotify('Please select a PPTX file.'); return; }
    hidePptActionAreas();
    document.getElementById('ppt-to-images-area').classList.remove('hidden');
    ffSelectActionCard(e.currentTarget);
});
document.getElementById('merge-ppt-btn')?.addEventListener('click', (e) => {
    setPptMergeMode(true);
    hidePptActionAreas();
    document.getElementById('merge-ppt-area').classList.remove('hidden');
    ffSelectActionCard(e.currentTarget);
});

async function processPptAction(url, text, formData) {
    const statusDisplay = document.getElementById('ppt-status-display');
    const statusText = document.getElementById('ppt-status-text');
    const resultDisplay = document.getElementById('ppt-result-display');

    statusDisplay.classList.remove('hidden');
    statusText.textContent = text;
    resultDisplay.classList.add('hidden');

    try {
        const response = await ffServerFetch(url, { method: 'POST', body: formData });
        if (response.ok) {
            const data = await response.json();
            resultDisplay.classList.remove('hidden');
            document.getElementById('ppt-result-message').textContent = `${data.message}: ${data.filename}`;
            updateDownloadLink(document.getElementById('ppt-download-link'), data.download_token);
            ffUpdateStepTracker('ppt', 3);
        } else {
            const data = await response.json().catch(() => ({ detail: 'Failed' }));
            ffNotify('Error: ' + (data.detail || 'Failed'));
        }
    } catch (e) {
        ffNotify('Error: ' + e.message);
    } finally {
        statusDisplay.classList.add('hidden');
    }
}

document.getElementById('process-ppt-to-pdf-btn')?.addEventListener('click', () => {
    if (!selectedPptFile) { ffNotify('Please select a PPTX file.'); return; }
    const fd = new FormData();
    fd.append('file', selectedPptFile);
    processPptAction('/api/ppt/to-pdf', 'Converting PPT to PDF...', fd);
});
document.getElementById('process-ppt-to-images-btn')?.addEventListener('click', () => {
    if (!selectedPptFile) { ffNotify('Please select a PPTX file.'); return; }
    const fd = new FormData();
    fd.append('file', selectedPptFile);
    fd.append('fmt', document.getElementById('ppt-images-format').value);
    processPptAction('/api/ppt/to-images', 'Rendering slides...', fd);
});
document.getElementById('process-merge-ppt-btn')?.addEventListener('click', () => {
    if (!selectedPptFiles || selectedPptFiles.length < 2) {
        ffNotify('Please select at least two .pptx files.'); return;
    }
    const fd = new FormData();
    selectedPptFiles.forEach(f => fd.append('files', f));
    processPptAction('/api/ppt/merge', `Merging ${selectedPptFiles.length} presentations...`, fd);
});

// === New PDF Feature Handlers ===

// Helper: show a PDF option panel (same pattern as existing sign/watermark/etc. buttons)
function showPdfOptionPanel(areaId) {
    if (!selectedFile) { ffNotify('Please select a PDF file first.'); return false; }
    openPdfArea(areaId);
    return true;
}

// --- Rotate PDF ---
document.getElementById('rotate-pdf-btn')?.addEventListener('click', () => {
    showPdfOptionPanel('rotate-pdf-area');
});
document.getElementById('process-rotate-pdf-btn')?.addEventListener('click', () => {
    if (!selectedFile) { ffNotify('Please select a PDF file first.'); return; }
    const angle = document.getElementById('rotate-pdf-angle').value;
    const pages = document.getElementById('rotate-pdf-pages').value.trim();
    const fd = new FormData();
    fd.append('file', selectedFile);
    fd.append('angle', angle);
    if (pages) fd.append('pages', pages);
    processAction('/api/pdf/rotate', `Rotating PDF ${angle}°...`, fd);
});

// --- Protect PDF ---
document.getElementById('protect-pdf-btn')?.addEventListener('click', () => {
    showPdfOptionPanel('protect-pdf-area');
});
document.getElementById('process-protect-pdf-btn')?.addEventListener('click', () => {
    if (!selectedFile) { ffNotify('Please select a PDF file first.'); return; }
    const userPwd = document.getElementById('protect-user-password').value;
    if (!userPwd) { ffNotify('Please enter a user password.'); return; }
    const ownerPwd = document.getElementById('protect-owner-password').value;
    const allowPrint = document.getElementById('protect-allow-print').checked;
    const allowCopy = document.getElementById('protect-allow-copy').checked;
    const allowEdit = document.getElementById('protect-allow-edit').checked;
    const fd = new FormData();
    fd.append('file', selectedFile);
    fd.append('user_password', userPwd);
    if (ownerPwd) fd.append('owner_password', ownerPwd);
    fd.append('allow_print', allowPrint);
    fd.append('allow_copy', allowCopy);
    fd.append('allow_edit', allowEdit);
    processAction('/api/pdf/protect', 'Protecting PDF...', fd);
});

// --- Extract Text ---
document.getElementById('extract-text-btn')?.addEventListener('click', () => {
    showPdfOptionPanel('extract-text-area');
});
document.getElementById('process-extract-text-btn')?.addEventListener('click', () => {
    if (!selectedFile) { ffNotify('Please select a PDF file first.'); return; }
    const preserveLayout = document.getElementById('extract-text-layout').checked;
    const fd = new FormData();
    fd.append('file', selectedFile);
    fd.append('preserve_layout', preserveLayout);
    processAction('/api/pdf/extract-text', 'Extracting text...', fd);
});

// --- OCR PDF ---
document.getElementById('ocr-pdf-btn')?.addEventListener('click', () => {
    showPdfOptionPanel('ocr-pdf-area');
});
document.getElementById('process-ocr-pdf-btn')?.addEventListener('click', () => {
    if (!selectedFile) { ffNotify('Please select a PDF file first.'); return; }
    const fd = new FormData();
    fd.append('file', selectedFile);
    fd.append('lang', document.getElementById('ocr-pdf-lang').value || 'en');
    processAction('/api/pdf/ocr', 'Creating searchable PDF...', fd);
});

// --- Organize PDF ---
document.getElementById('organize-pdf-btn')?.addEventListener('click', () => {
    showPdfOptionPanel('organize-pdf-area');
});
document.getElementById('process-organize-pdf-btn')?.addEventListener('click', () => {
    if (!selectedFile) { ffNotify('Please select a PDF file first.'); return; }
    const order = document.getElementById('organize-page-order').value.trim();
    if (!order) { ffNotify('Please enter a page order (e.g. 1,3,2).'); return; }
    const fd = new FormData();
    fd.append('file', selectedFile);
    fd.append('page_order', order);
    processAction('/api/pdf/organize', 'Organizing pages...', fd);
});

// --- Add Page Numbers ---
document.getElementById('page-numbers-btn')?.addEventListener('click', () => {
    showPdfOptionPanel('page-numbers-area');
});
document.getElementById('process-page-numbers-btn')?.addEventListener('click', () => {
    if (!selectedFile) { ffNotify('Please select a PDF file first.'); return; }
    const position = document.getElementById('page-numbers-position').value;
    const format = document.getElementById('page-numbers-format').value;
    const start = document.getElementById('page-numbers-start').value;
    const skip = document.getElementById('page-numbers-skip').value;
    const fd = new FormData();
    fd.append('file', selectedFile);
    fd.append('position', position);
    // fmt / start_number / skip_first are the names the endpoint declares.
    // Sent as format/start/skip they were dropped, so the numbering format,
    // start number and skip-first controls did nothing — every document got
    // decimal numbers from 1 on every page.
    fd.append('fmt', format);
    fd.append('start_number', start);
    fd.append('skip_first', skip);
    processAction('/api/pdf/add-page-numbers', 'Adding page numbers...', fd);
});

// --- Repair PDF ---
document.getElementById('repair-pdf-btn')?.addEventListener('click', () => {
    if (!selectedFile) { ffNotify('Please select a PDF file first.'); return; }
    openPdfArea('repair-pdf-area');
});
document.getElementById('process-repair-pdf-btn')?.addEventListener('click', () => {
    if (!selectedFile) { ffNotify('Please select a PDF file first.'); return; }
    const fd = new FormData();
    fd.append('file', selectedFile);
    processAction('/api/pdf/repair', 'Repairing PDF...', fd);
});

// --- Create PDF ---
document.getElementById('create-pdf-btn')?.addEventListener('click', () => {
    openPdfArea('create-pdf-area');
});

function toggleCreatePdfMode() {
    const mode = document.querySelector('input[name="create-pdf-mode"]:checked')?.value;
    const textOpts = document.getElementById('create-pdf-text-opts');
    const blankOpts = document.getElementById('create-pdf-blank-opts');
    if (mode === 'blank') {
        textOpts?.classList.add('hidden');
        blankOpts?.classList.remove('hidden');
    } else {
        textOpts?.classList.remove('hidden');
        blankOpts?.classList.add('hidden');
    }
}

document.getElementById('process-create-pdf-btn')?.addEventListener('click', () => {
    const mode = document.querySelector('input[name="create-pdf-mode"]:checked')?.value || 'text';
    const pagesize = document.getElementById('create-pdf-pagesize').value;
    const fd = new FormData();
    // Same mismatch as image-to-pdf: the endpoints declare page_size and
    // num_pages, so "Letter" and the page count were both being dropped.
    fd.append('page_size', pagesize);
    if (mode === 'text') {
        const content = document.getElementById('create-pdf-content').value;
        const title = document.getElementById('create-pdf-title').value;
        if (!content.trim()) { ffNotify('Please enter some text content.'); return; }
        fd.append('content', content);
        if (title) fd.append('title', title);
        processAction('/api/pdf/create-from-text', 'Creating PDF from text...', fd);
    } else {
        const pages = document.getElementById('create-pdf-pages').value;
        fd.append('num_pages', pages);
        processAction('/api/pdf/create-blank', 'Creating blank PDF...', fd);
    }
});

// --- Annotate PDF ---
document.getElementById('annotate-pdf-btn')?.addEventListener('click', () => {
    showPdfOptionPanel('annotate-pdf-area');
});
document.getElementById('process-annotate-pdf-btn')?.addEventListener('click', () => {
    if (!selectedFile) { ffNotify('Please select a PDF file first.'); return; }
    const annotType = document.getElementById('annot-type').value;
    const page = document.getElementById('annot-page').value;
    const x0 = document.getElementById('annot-x0').value;
    const y0 = document.getElementById('annot-y0').value;
    const x1 = document.getElementById('annot-x1').value;
    const y1 = document.getElementById('annot-y1').value;
    const content = document.getElementById('annot-content').value;
    // The endpoint takes a JSON array of annotations (it used to be sent the
    // fields individually, which the server rejected with HTTP 422).
    const annotation = {
        type: annotType,
        page: parseInt(page, 10) || 1,
        rect: [x0, y0, x1, y1].map(Number),
    };
    if (content) annotation.content = content;
    const fd = new FormData();
    fd.append('file', selectedFile);
    fd.append('annotations', JSON.stringify([annotation]));
    processAction('/api/pdf/annotate', 'Adding annotation...', fd);
});

// --- PDF Metadata ---
document.getElementById('pdf-metadata-btn')?.addEventListener('click', () => {
    showPdfOptionPanel('pdf-metadata-area');
});
document.getElementById('process-pdf-metadata-btn')?.addEventListener('click', () => {
    if (!selectedFile) { ffNotify('Please select a PDF file first.'); return; }
    const title = document.getElementById('meta-title').value;
    const author = document.getElementById('meta-author').value;
    const subject = document.getElementById('meta-subject').value;
    const keywords = document.getElementById('meta-keywords').value;
    const clearAll = document.getElementById('meta-clear-all').checked;
    const fd = new FormData();
    fd.append('file', selectedFile);
    if (title) fd.append('title', title);
    if (author) fd.append('author', author);
    if (subject) fd.append('subject', subject);
    if (keywords) fd.append('keywords', keywords);
    fd.append('clear_all', clearAll);
    processAction('/api/pdf/metadata', 'Updating metadata...', fd);
});

// --- PDF to Excel ---
document.getElementById('pdf-to-excel-btn')?.addEventListener('click', () => {
    showPdfOptionPanel('pdf-to-excel-area');
});
document.getElementById('process-pdf-to-excel-btn')?.addEventListener('click', () => {
    if (!selectedFile) { ffNotify('Please select a PDF file first.'); return; }
    const fd = new FormData();
    fd.append('file', selectedFile);
    processAction('/api/pdf/to-excel', 'Extracting tables to Excel...', fd);
});

// --- PDF to PowerPoint ---
document.getElementById('pdf-to-pptx-btn')?.addEventListener('click', () => {
    showPdfOptionPanel('pdf-to-pptx-area');
});
document.getElementById('process-pdf-to-pptx-btn')?.addEventListener('click', () => {
    if (!selectedFile) { ffNotify('Please select a PDF file first.'); return; }
    const dpi = document.getElementById('pdf-to-pptx-dpi').value;
    const fd = new FormData();
    fd.append('file', selectedFile);
    fd.append('dpi', dpi);
    processAction('/api/pdf/to-pptx', 'Converting to PowerPoint...', fd);
});

// --- PDF to EPUB ---
document.getElementById('pdf-to-epub-btn')?.addEventListener('click', () => {
    showPdfOptionPanel('pdf-to-epub-area');
});
document.getElementById('process-pdf-to-epub-btn')?.addEventListener('click', () => {
    if (!selectedFile) { ffNotify('Please select a PDF file first.'); return; }
    const fd = new FormData();
    fd.append('file', selectedFile);
    processAction('/api/pdf/to-epub', 'Converting to EPUB...', fd);
});

// === Word Tools Page ===

let selectedWordFile = null;

const wordDropZone = document.getElementById('word-drop-zone');
const wordFileInput = document.getElementById('word-file-input');

function handleWordFile(file) {
    selectedWordFile = file;
    document.getElementById('word-filename-display').textContent = selectedWordFile.name;
    document.getElementById('word-file-info').classList.remove('hidden');
    document.getElementById('word-status-display').classList.add('hidden');
    document.getElementById('word-result-display').classList.add('hidden');
    ffUpdateStepTracker('word', 2);
    ffConsumePendingOp();
    try { ffTrack('file_selected', { tool_name: currentOp || currentTool || 'word', file_type: ffExtractFileType(file) || 'docx' }); } catch (e) { }
}

if (wordDropZone) {
    wordDropZone.onclick = () => wordFileInput.click();
    wordFileInput.onchange = e => {
        if (e.target.files.length) handleWordFile(e.target.files[0]);
    };
    wordDropZone.ondragover = e => { e.preventDefault(); wordDropZone.classList.add('drag-over'); };
    wordDropZone.ondragleave = () => wordDropZone.classList.remove('drag-over');
    wordDropZone.ondrop = e => {
        e.preventDefault();
        wordDropZone.classList.remove('drag-over');
        if (e.dataTransfer.files.length) handleWordFile(e.dataTransfer.files[0]);
    };
}

async function processWordAction(url, statusText, formData) {
    const statusDisplay = document.getElementById('word-status-display');
    const statusTextEl = document.getElementById('word-status-text');
    const resultDisplay = document.getElementById('word-result-display');

    statusDisplay.classList.remove('hidden');
    statusTextEl.textContent = statusText;
    resultDisplay.classList.add('hidden');

    try {
        const response = await ffServerFetch(url, { method: 'POST', body: formData });
        if (response.ok) {
            const data = await response.json();
            resultDisplay.classList.remove('hidden');
            document.getElementById('word-result-message').textContent = `${data.message}: ${data.filename}`;
            updateDownloadLink(document.getElementById('word-download-link'), data.download_token);
            ffUpdateStepTracker('word', 3);
        } else {
            const data = await response.json().catch(() => ({ detail: 'Failed' }));
            ffNotify('Error: ' + (data.detail || 'Failed'));
        }
    } catch (e) {
        ffNotify('Error: ' + e.message);
    } finally {
        statusDisplay.classList.add('hidden');
    }
}

document.getElementById('word-to-pdf-btn')?.addEventListener('click', (e) => {
    if (!selectedWordFile) { ffNotify('Please select a Word file first.'); return; }
    document.getElementById('word-to-pptx-area')?.classList.add('hidden');
    document.getElementById('word-to-pdf-area').classList.remove('hidden');
    ffSelectActionCard(e.currentTarget);
});
document.getElementById('process-word-to-pdf-btn')?.addEventListener('click', () => {
    if (!selectedWordFile) { ffNotify('Please select a Word file first.'); return; }
    const fd = new FormData();
    fd.append('file', selectedWordFile);
    processWordAction('/api/word/to-pdf', 'Converting Word to PDF...', fd);
});

// --- Word to PowerPoint ---
document.getElementById('word-to-pptx-btn')?.addEventListener('click', (e) => {
    if (!selectedWordFile) { ffNotify('Please select a Word file first.'); return; }
    document.getElementById('word-to-pdf-area')?.classList.add('hidden');
    document.getElementById('word-to-pptx-area').classList.remove('hidden');
    ffSelectActionCard(e.currentTarget);
});
document.getElementById('process-word-to-pptx-btn')?.addEventListener('click', () => {
    if (!selectedWordFile) { ffNotify('Please select a Word file first.'); return; }
    const dpi = document.getElementById('word-to-pptx-dpi').value;
    const fd = new FormData();
    fd.append('file', selectedWordFile);
    fd.append('dpi', dpi);
    processWordAction('/api/word/to-pptx', 'Converting Word to PowerPoint...', fd);
});

// --- Image to PDF ---
document.getElementById('image-to-pdf-btn')?.addEventListener('click', (e) => {
    if (showImageOptionPanel('image-to-pdf-area')) ffSelectActionCard(e.currentTarget);
});
document.getElementById('process-image-to-pdf-btn')?.addEventListener('click', () => {
    if (!selectedImageFile) { ffNotify('Please select an image first.'); return; }
    const pagesize = document.getElementById('image-to-pdf-pagesize').value;
    const fit = document.getElementById('image-to-pdf-fit').value;
    const fd = new FormData();
    // Every name here has to be the one /api/image/to-pdf actually declares.
    // None of them were: the file went as `file` against a required `files`,
    // so this tool answered 422 on every click and had never worked at all,
    // and pagesize/fit were ignored on top of that. Left alone, the on-device
    // path would quietly start working while the server path stayed broken.
    fd.append('files', selectedImageFile);
    fd.append('page_size', pagesize);
    fd.append('fit_mode', fit);
    processImageAction('/api/image/to-pdf', 'Converting image to PDF...', fd);
});

// Global Accessibility: Handle keyboard activation for role="button"
document.addEventListener('keydown', (e) => {
    if ((e.key === 'Enter' || e.key === ' ') && e.target.getAttribute('role') === 'button') {
        e.preventDefault();
        e.target.click();
    }
});

// Deep-link support: /?tool=pdf opens the PDF tools directly, and the optional
// &op=<seo-slug> preselects the specific tool the visitor actually searched for
// (used by the CTA on every SEO landing page — see scripts/seo_content.py).
//
// Without `op`, someone arriving from /pdf-to-word landed on the PDF category
// and had to find "PDF to Word" a second time among 19 action cards — the
// single biggest drop-off in the funnel. Keys here are the seo_content.py
// TOOL_PAGES slugs; `card` is the action-card id in index.html.
const DEEP_LINK_OPS = {
    // PDF
    'unlock-pdf': { card: 'remove-password-btn' },
    'pdf-to-word': { card: 'convert-word-btn' },
    'compress-pdf': { card: 'compress-pdf-btn' },
    'extract-pdf-pages': { card: 'extract-pages-btn' },
    'split-pdf': { card: 'split-pdf-btn' },
    'pdf-to-text': { card: 'extract-text-btn' },
    'ocr-pdf': { card: 'ocr-pdf-btn' },
    'make-pdf-searchable': { card: 'ocr-pdf-btn' },
    'ocr-hindi': { card: 'ocr-pdf-btn', lang: 'hi' },
    'ocr-marathi': { card: 'ocr-pdf-btn', lang: 'mr' },
    'ocr-tamil': { card: 'ocr-pdf-btn', lang: 'ta' },
    'ocr-telugu': { card: 'ocr-pdf-btn', lang: 'te' },
    'merge-pdf': { card: 'merge-pdf-btn' },
    'rotate-pdf': { card: 'rotate-pdf-btn' },
    'protect-pdf': { card: 'protect-pdf-btn' },
    'watermark-pdf': { card: 'watermark-pdf-btn' },
    'pdf-to-jpg': { card: 'to-images-pdf-btn' },
    'pdf-page-numbers': { card: 'page-numbers-btn' },
    'pdf-to-excel': { card: 'pdf-to-excel-btn' },
    'pdf-to-powerpoint': { card: 'pdf-to-pptx-btn' },
    'pdf-to-epub': { card: 'pdf-to-epub-btn' },
    'sign-pdf': { card: 'sign-pdf-btn' },
    'organize-pdf': { card: 'organize-pdf-btn' },
    // Image
    'heic-to-jpeg': { card: 'convert-jpeg-btn' },
    'resize-image': { card: 'resize-btn' },
    'crop-image': { card: 'crop-btn' },
    'image-to-pdf': { card: 'image-to-pdf-btn' },
    'compress-image': { card: 'compress-image-btn' },
    'convert-image': { card: 'convert-format-btn' },
    'rotate-image': { card: 'rotate-image-btn' },
    'watermark-image': { card: 'watermark-image-btn' },
    // Excel
    'excel-to-pdf': { card: 'excel-to-pdf-btn' },
    'csv-to-xlsx': { card: 'csv-to-xlsx-btn' },
    'xlsx-to-csv': { card: 'xlsx-to-csv-btn' },
    'merge-excel': { card: 'merge-excel-btn' },
    // PowerPoint
    'powerpoint-to-pdf': { card: 'ppt-to-pdf-btn' },
    'ppt-to-images': { card: 'ppt-to-images-btn' },
    'merge-ppt': { card: 'merge-ppt-btn' },
    // Word
    'word-to-pdf': { card: 'word-to-pdf-btn' },
};

// Cards that don't need a file selected first (they collect their own files).
const DEEP_LINK_NO_FILE_CARDS = ['merge-pdf-btn', 'merge-excel-btn', 'merge-ppt-btn'];

// The action card a deep link asked for, held until the visitor picks a file.
// Most card handlers ffNotify("Please select a file first.") when clicked with no
// file, so we highlight the card on arrival and open it once a file exists.
let ffPendingOp = null;

function ffHighlightCard(cardId) {
    const card = document.getElementById(cardId);
    if (!card) return;
    card.classList.add('deep-link-target');
    // Nudge it into view behind the drop zone, without stealing the top of the
    // page from the upload box — that has to stay the first thing they see.
    try { card.scrollIntoView({ block: 'nearest', behavior: 'instant' }); } catch (e) { }
}

// Called by every category's file-accepted path. Opens the deep-linked tool as
// soon as the visitor has a file, so landing → upload → correct tool is one
// continuous motion with nothing to hunt for.
function ffConsumePendingOp() {
    if (!ffPendingOp) return;
    const pending = ffPendingOp;
    ffPendingOp = null;
    const cardId = typeof pending === 'object' ? pending.card : pending;
    const lang = typeof pending === 'object' ? pending.lang : null;
    if (lang) {
        const langSelect = document.getElementById('ocr-pdf-lang');
        if (langSelect) langSelect.value = lang;
    }
    const card = document.getElementById(cardId);
    if (!card) return;
    card.classList.remove('deep-link-target');
    setTimeout(() => {
        card.click();
        if (lang) {
            const langSelect = document.getElementById('ocr-pdf-lang');
            if (langSelect) langSelect.value = lang;
        }
    }, 0);
}
window.ffConsumePendingOp = ffConsumePendingOp;

// Initialize before the deep-link bootstrap calls ffClaimHandoff. Function
// declarations are hoisted, but reading a later const would throw on arrival.
const FF_CATEGORY_INPUTS = {
    pdf: 'file-input',
    image: 'image-file-input',
    excel: 'excel-file-input',
    ppt: 'ppt-file-input',
    word: 'word-file-input',
    workflow: 'workflow-file-input',
};

(function () {
    const params = new URLSearchParams(window.location.search);
    const requestedTool = params.get('tool');
    if (!requestedTool || !['pdf', 'image', 'workflow', 'excel', 'ppt', 'word'].includes(requestedTool)) return;

    showDrillDown(requestedTool, true, true);

    // `op` is only ever resolved through DEEP_LINK_OPS — never used to look up
    // an element id directly, so an arbitrary ?op= value can't reach the DOM.
    const op = DEEP_LINK_OPS[params.get('op')];
    if (!op) return;

    // No tool_open for the specific op here on purpose: it's fired by the
    // delegated action-card listener when the card is actually opened (below
    // for merge tools, or after the file lands for everything else). A visitor
    // who deep-links and then leaves without uploading should count as opening
    // the category and nothing more.
    if (DEEP_LINK_NO_FILE_CARDS.includes(op.card)) {
        const card = document.getElementById(op.card);
        if (card) setTimeout(() => card.click(), 0);
    } else {
        ffPendingOp = op.lang ? { card: op.card, lang: op.lang } : op.card;
        ffHighlightCard(op.card);
        if (op.lang) {
            const langSelect = document.getElementById('ocr-pdf-lang');
            if (langSelect) langSelect.value = op.lang;
        }
    }

    // ?handoff=1 means the visitor already chose a file on the SEO landing
    // page and static/seo-upload.js stashed it in IndexedDB. Pick it up and
    // feed it to this category's file input exactly as if it had been chosen
    // here, so landing → upload → result is one motion with no second file
    // picker. Any failure just leaves the normal empty upload box in place.
    if (params.get('handoff') === '1') {
        ffClaimHandoff(requestedTool);
    }
})();

function ffClaimHandoff(tool) {
    const inputId = FF_CATEGORY_INPUTS[tool];
    if (!inputId || !window.indexedDB || typeof DataTransfer === 'undefined') return;
    const input = document.getElementById(inputId);
    if (!input) return;

    let db;
    const req = indexedDB.open('ff_handoff', 1);
    // The landing page creates the store; if it never ran there is nothing to
    // claim, so don't create it here just to find it empty.
    req.onupgradeneeded = () => {
        try { req.transaction.abort(); } catch (e) { }
    };
    req.onerror = () => { };
    req.onsuccess = () => {
        db = req.result;
        if (!db.objectStoreNames.contains('files')) { db.close(); return; }
        let record;
        const tx = db.transaction('files', 'readwrite');
        const store = tx.objectStore('files');
        const get = store.get('pending');
        get.onsuccess = () => {
            record = get.result;
            // Consumed on read: a stale file resurfacing on a later visit
            // would silently convert the wrong document.
            store.delete('pending');
        };
        tx.oncomplete = () => {
            db.close();
            if (!record) return;
            const fileItems = Array.isArray(record.files) && record.files.length
                ? record.files
                : (record.blob ? [{ blob: record.blob, name: record.name, type: record.type }] : []);
            if (!fileItems.length) return;

            try {
                if (fileItems.length > 1) {
                    input.multiple = true;
                }
                const dt = new DataTransfer();
                for (const item of fileItems) {
                    const file = new File([item.blob], item.name || 'upload',
                        { type: item.type || item.blob?.type || '' });
                    dt.items.add(file);
                }
                input.files = dt.files;
                input.dispatchEvent(new Event('change', { bubbles: true }));
            } catch (e) {
                // Leaves the empty upload box — the pre-handoff behaviour.
            }
        };
        tx.onerror = () => { db.close(); };
    };
}

// Theme Toggle & Automatic Time-of-Day Logic
const themeToggleBtn = document.getElementById('theme-toggle-btn');
const themeIcon = document.getElementById('theme-icon');

function getTimeBasedTheme() {
    const now = new Date();
    const minutes = now.getHours() * 60 + now.getMinutes();
    // Post 6:30 PM (18:30 = 1110 min) or before 6:30 AM (06:30 = 390 min) is dark
    return (minutes >= 1110 || minutes < 390) ? 'dark' : 'light';
}

function getEffectiveTheme() {
    try {
        const savedTheme = localStorage.getItem('theme');
        if (savedTheme === 'dark' || savedTheme === 'light') {
            return savedTheme;
        }
    } catch (e) {}
    return getTimeBasedTheme();
}

function updateThemeIcon() {
    if (!themeIcon) return;
    const currentTheme = document.documentElement.getAttribute('data-theme') || getEffectiveTheme();
    if (currentTheme === 'dark') {
        themeIcon.classList.remove('fa-moon');
        themeIcon.classList.add('fa-sun');
        if (themeToggleBtn) themeToggleBtn.setAttribute('aria-label', 'Switch to Light Mode');
    } else {
        themeIcon.classList.remove('fa-sun');
        themeIcon.classList.add('fa-moon');
        if (themeToggleBtn) themeToggleBtn.setAttribute('aria-label', 'Switch to Dark Mode');
    }
}

function applyTheme(theme, saveManual = false) {
    document.documentElement.setAttribute('data-theme', theme);
    if (saveManual) {
        try {
            localStorage.setItem('theme', theme);
        } catch (e) {}
    }
    updateThemeIcon();
}

// Set initial theme & icon
const initialTheme = document.documentElement.getAttribute('data-theme') || getEffectiveTheme();
applyTheme(initialTheme, false);

if (themeToggleBtn) {
    themeToggleBtn.addEventListener('click', () => {
        const currentTheme = document.documentElement.getAttribute('data-theme') || getEffectiveTheme();
        const newTheme = currentTheme === 'dark' ? 'light' : 'dark';
        applyTheme(newTheme, true);
    });
}

// Live schedule: Check and update theme dynamically if no manual preference is saved
setInterval(() => {
    try {
        const savedTheme = localStorage.getItem('theme');
        if (!savedTheme) {
            const timeTheme = getTimeBasedTheme();
            const currentTheme = document.documentElement.getAttribute('data-theme');
            if (currentTheme !== timeTheme) {
                applyTheme(timeTheme, false);
            }
        }
    } catch (e) {}
}, 60000);

