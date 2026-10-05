// Server-processing consent gate (migration work package 00).
//
// Forge Files processes a file on the device whenever it can. When an operation
// genuinely needs the server, nothing is uploaded until the person has agreed to
// that specific operation. Cancel, Escape, dismissing the dialog, or having no
// way to ask at all all mean "no": the request is never sent.
//
// Consent is per operation. Nothing is remembered between calls, so a changed
// file, option or retry asks again.
//
// This file has no dependency on the page: the dialog is built on demand, so the
// same gate works on the main app, the bundled mobile web build and any future
// isolated route. With no DOM available it fails closed.
(function () {
    'use strict';

    // path -> {tool, reason}. `reason` is the inherent reason the tool needs the
    // server; it completes the sentence "{tool} needs Forge Files server
    // processing because ...". Never put document content, filenames or raw
    // error text here.
    var TOOLS = {
        '/api/pdf/remove-password': ['Unlock PDF', 'this tool removes PDF encryption using the server\'s PDF engine'],
        '/api/pdf/convert-to-word': ['PDF to Word', 'rebuilding an editable Word layout needs the server\'s conversion engine'],
        '/api/pdf/convert-to-word-stream': ['PDF to Word', 'rebuilding an editable Word layout needs the server\'s conversion engine'],
        '/api/pdf/compress': ['Compress PDF', 'image recompression runs on the server\'s PDF engine'],
        '/api/pdf/extract-pages': ['Extract Pages', 'this PDF could not be processed on your device'],
        '/api/pdf/split': ['Split PDF', 'this PDF could not be split on your device'],
        '/api/pdf/extract-text': ['PDF to Text', 'this PDF could not be read on your device'],
        '/api/pdf/ocr': ['OCR PDF', 'text recognition runs on the server\'s OCR models'],
        '/api/pdf/merge': ['Merge PDF', 'these PDFs could not be merged on your device'],
        '/api/pdf/rotate': ['Rotate PDF', 'this PDF could not be processed on your device'],
        '/api/pdf/protect': ['Protect PDF', 'this PDF could not be encrypted on your device'],
        '/api/pdf/watermark': ['Watermark PDF', 'this PDF could not be processed on your device'],
        '/api/pdf/to-images': ['PDF to JPG', 'this PDF could not be rendered on your device'],
        '/api/pdf/add-page-numbers': ['Add Page Numbers', 'this PDF could not be processed on your device'],
        '/api/pdf/to-excel': ['PDF to Excel', 'table detection runs on the server\'s conversion engine'],
        '/api/pdf/to-pptx': ['PDF to PowerPoint', 'slide building runs on the server\'s conversion engine'],
        '/api/pdf/to-epub': ['PDF to EPUB', 'this conversion runs on the server\'s conversion engine'],
        '/api/pdf/sign': ['Sign PDF', 'this PDF could not be signed on your device'],
        '/api/pdf/organize': ['Organize PDF', 'this PDF could not be processed on your device'],
        '/api/pdf/repair': ['Repair PDF', 'repairing damaged files uses the server\'s PDF engine'],
        '/api/pdf/create-from-text': ['Create PDF', 'this PDF could not be created on your device'],
        '/api/pdf/create-blank': ['Create PDF', 'this PDF could not be created on your device'],
        '/api/pdf/annotate': ['Annotate PDF', 'this PDF could not be annotated on your device'],
        '/api/pdf/metadata': ['Edit PDF Metadata', 'this PDF could not be processed on your device'],
        '/api/pdf/metadata/read': ['Read PDF Metadata', 'this PDF could not be read on your device'],
        '/api/image/heic-to-jpeg': ['HEIC to JPG', 'this browser cannot decode HEIC images'],
        '/api/image/resize': ['Resize Image', 'this image could not be processed on your device'],
        '/api/image/crop': ['Crop Image', 'this image could not be processed on your device'],
        '/api/image/to-pdf': ['Image to PDF', 'these images could not be processed on your device'],
        '/api/image/compress': ['Compress Image', 'this image could not be processed on your device'],
        '/api/image/convert': ['Convert Image', 'this image format could not be processed on your device'],
        '/api/image/rotate': ['Rotate Image', 'this image could not be processed on your device'],
        '/api/image/watermark': ['Watermark Image', 'this image could not be processed on your device'],
        '/api/excel/to-pdf': ['Excel to PDF', 'this conversion needs a full office layout engine to preserve pages, fonts and charts'],
        '/api/excel/csv-to-xlsx': ['CSV to Excel', 'this file could not be converted on your device'],
        '/api/excel/xlsx-to-csv': ['Excel to CSV', 'this file could not be converted on your device'],
        '/api/excel/merge': ['Merge Excel', 'merging workbooks needs the server\'s spreadsheet engine'],
        '/api/ppt/to-pdf': ['PowerPoint to PDF', 'this conversion needs a full office layout engine to preserve slides, fonts and charts'],
        '/api/ppt/to-images': ['PowerPoint to Images', 'this conversion needs a full office layout engine to preserve slides, fonts and charts'],
        '/api/ppt/merge': ['Merge PowerPoint', 'merging presentations needs the server\'s office engine'],
        '/api/word/to-pdf': ['Word to PDF', 'this conversion needs a full office layout engine to preserve pages, fonts and tables'],
        '/api/word/to-pptx': ['Word to PowerPoint', 'this conversion needs a full office layout engine'],
        '/api/workflow/execute': ['Workflow', 'at least one step of this workflow runs on the server'],
        '/premium/batch-ocr': ['Batch OCR', 'batch text recognition runs on the server\'s OCR models'],
    };

    // Bounded reason codes a local handler may attach to FFLocalUnsupported.
    // The code is mapped to fixed wording; the handler's own message is never
    // shown.
    var CODES = {
        encrypted: 'this PDF is password protected',
        resource_budget_exceeded: 'this file exceeds the safe limit for processing on this device',
        engine_unavailable: 'the on-device engine could not be loaded',
        undecodable: 'this file format could not be decoded on this device',
        unsupported_structure: 'this file uses features that cannot be processed on this device',
        font_coverage_missing: 'this document uses a script the on-device fonts do not cover',
        on_device_office_unavailable: 'on-device Office conversion is not available on this device',
    };

    function lookup(path) {
        var p = String(path || '').split('?')[0];
        return TOOLS[p] || null;
    }

    function describe(path, code) {
        var entry = lookup(path);
        return {
            known: !!entry,
            tool: entry ? entry[0] : 'This tool',
            reason: (code && CODES[code]) || (entry ? entry[1] : 'this operation needs the server'),
        };
    }

    // ── Dialog ────────────────────────────────────────────────────────────

    var STYLE_ID = 'ff-consent-style';
    var CSS = '' +
        '.ff-consent-backdrop{position:fixed;inset:0;z-index:2147483000;display:flex;align-items:center;justify-content:center;padding:16px;background:rgba(0,0,0,.55)}' +
        '.ff-consent-dialog{box-sizing:border-box;width:100%;max-width:440px;max-height:100%;overflow:auto;padding:20px;border-radius:14px;background:var(--bg-card,#fff);color:var(--text,#111);box-shadow:0 12px 40px rgba(0,0,0,.35);font:inherit}' +
        '.ff-consent-dialog h2{margin:0 0 10px;font-size:1.15rem;line-height:1.3}' +
        '.ff-consent-dialog p{margin:0 0 16px;line-height:1.5}' +
        '.ff-consent-actions{display:flex;flex-wrap:wrap;gap:10px;justify-content:flex-end}' +
        '.ff-consent-actions button{min-height:44px;padding:10px 16px;border-radius:10px;border:1px solid currentColor;background:transparent;color:inherit;font:inherit;cursor:pointer}' +
        '.ff-consent-actions button.ff-consent-ok{background:var(--accent,#2563eb);border-color:var(--accent,#2563eb);color:#fff}';

    var active = null; // the open request, if any

    function focusables(root) {
        return Array.prototype.slice.call(root.querySelectorAll('button'));
    }

    function openDialog(info, done) {
        var doc = window.document;
        if (!doc || !doc.body || typeof doc.createElement !== 'function') {
            done(false);
            return function () { };
        }
        if (!doc.getElementById(STYLE_ID)) {
            var style = doc.createElement('style');
            style.id = STYLE_ID;
            style.textContent = CSS;
            (doc.head || doc.body).appendChild(style);
        }

        var previous = doc.activeElement;
        var uid = 'ff-consent-' + Math.floor(Math.random() * 1e9);

        var backdrop = doc.createElement('div');
        backdrop.className = 'ff-consent-backdrop';

        var dialog = doc.createElement('div');
        dialog.className = 'ff-consent-dialog';
        dialog.setAttribute('role', 'alertdialog');
        dialog.setAttribute('aria-modal', 'true');
        dialog.setAttribute('aria-labelledby', uid + '-t');
        dialog.setAttribute('aria-describedby', uid + '-d');

        var title = doc.createElement('h2');
        title.id = uid + '-t';
        title.textContent = 'Process this file on the server?';

        var body = doc.createElement('p');
        body.id = uid + '-d';
        body.textContent = info.tool + ' needs Forge Files server processing because ' + info.reason +
            '. Your selected file will be uploaded for this conversion, and the result will be available to download. ' +
            'Files are handled under our privacy and retention policy.';

        var actions = doc.createElement('div');
        actions.className = 'ff-consent-actions';
        var cancel = doc.createElement('button');
        cancel.type = 'button';
        cancel.textContent = 'Cancel';
        var ok = doc.createElement('button');
        ok.type = 'button';
        ok.className = 'ff-consent-ok';
        ok.textContent = 'Continue on server';
        actions.appendChild(cancel);
        actions.appendChild(ok);

        dialog.appendChild(title);
        dialog.appendChild(body);
        dialog.appendChild(actions);
        backdrop.appendChild(dialog);

        var finished = false;
        function finish(result) {
            if (finished) return;
            finished = true;
            doc.removeEventListener('keydown', onKey, true);
            if (backdrop.parentNode) backdrop.parentNode.removeChild(backdrop);
            if (previous && typeof previous.focus === 'function') {
                try { previous.focus(); } catch (e) { /* element gone */ }
            }
            done(result);
        }
        function onKey(ev) {
            if (ev.key === 'Escape') {
                ev.preventDefault();
                ev.stopPropagation();
                finish(false);
            } else if (ev.key === 'Tab') {
                var items = focusables(dialog);
                if (!items.length) return;
                var first = items[0];
                var last = items[items.length - 1];
                if (ev.shiftKey && doc.activeElement === first) { ev.preventDefault(); last.focus(); }
                else if (!ev.shiftKey && doc.activeElement === last) { ev.preventDefault(); first.focus(); }
                else if (items.indexOf(doc.activeElement) === -1) { ev.preventDefault(); first.focus(); }
            }
        }

        cancel.addEventListener('click', function () { finish(false); });
        ok.addEventListener('click', function () { finish(true); });
        // A click on the dimmed area dismisses, and dismissal is a decline.
        backdrop.addEventListener('mousedown', function (ev) { if (ev.target === backdrop) finish(false); });
        doc.addEventListener('keydown', onKey, true);

        doc.body.appendChild(backdrop);
        cancel.focus(); // the safe choice has initial focus

        return function () { finish(false); };
    }

    /**
     * Ask whether this operation may use the server.
     *
     * Resolves true only when the person explicitly chose to continue. A second
     * request while one is open is declined rather than queued, so a double click
     * can never stack up uploads behind a dialog.
     *
     * @param {{path: string, code?: string}} req `code` is an optional bounded
     *        reason code from FFLocalUnsupported.
     * @returns {Promise<boolean>}
     */
    function request(req) {
        req = req || {};
        if (active) return Promise.resolve(false);
        var info = describe(req.path, req.code);
        return new Promise(function (resolve) {
            var custom = api.handler;
            var settle = function (result) {
                active = null;
                resolve(result === true);
            };
            active = { path: req.path };
            try {
                if (typeof custom === 'function') {
                    Promise.resolve(custom(info, req)).then(settle, function () { settle(false); });
                } else {
                    var cancelFn = openDialog(info, settle);
                    active.cancel = cancelFn;
                }
            } catch (e) {
                settle(false);
            }
        });
    }

    /** Close any open dialog as a decline (navigation, abort, route change). */
    function cancelPending() {
        if (active && typeof active.cancel === 'function') active.cancel();
    }

    var api = {
        request: request,
        cancelPending: cancelPending,
        describe: describe,
        paths: function () { return Object.keys(TOOLS); },
        codes: function () { return Object.keys(CODES); },
        // Test and embedding hook: a function (info, req) -> boolean|Promise.
        handler: null,
    };

    window.ffConsent = api;
    if (typeof window.addEventListener === 'function') {
        window.addEventListener('pagehide', cancelPending);
        window.addEventListener('popstate', cancelPending);
    }
})();
