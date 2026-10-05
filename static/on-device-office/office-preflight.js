// On-device Office engine: file-type and script-coverage preflight (WP44).
//
// The engine ships no Tamil, Telugu, CJK or other non-Latin/Devanagari fonts, and
// lab testing showed those scripts render as empty boxes. Before the 247 MB
// engine is loaded, the document text is scanned and, if it contains a script
// that is not known to render, on-device conversion is declined with a plain
// reason so the person can use the server instead. Nothing here uploads.
(function (root, factory) {
    var api = factory();
    if (typeof module === 'object' && module.exports) module.exports = api;
    root.ffOfficePreflight = api;
})(typeof self !== 'undefined' ? self : this, function () {
    'use strict';

    var MAX_INPUT_BYTES = 50 * 1024 * 1024;
    var MAX_XML_BYTES = 64 * 1024 * 1024;

    // op -> accepted extensions. Binary legacy formats (.doc/.xls/.ppt) and RTF
    // are left to the server because their text cannot be scanned reliably. CSV is
    // also server-only: the engine opens it without importing any cells (tested).
    var OPS = {
        'word-to-pdf': { label: 'Word to PDF', kind: 'word', exts: ['docx', 'odt'], out: 'pdf' },
        'excel-to-pdf': { label: 'Excel to PDF', kind: 'excel', exts: ['xlsx', 'xlsm'], out: 'pdf' },
        'ppt-to-pdf': { label: 'PowerPoint to PDF', kind: 'ppt', exts: ['pptx'], out: 'pdf' },
        'ppt-to-images': { label: 'PowerPoint to Images', kind: 'ppt', exts: ['pptx'], out: 'images' },
    };

    // Scripts the lab proved render correctly with the bundled fonts. Everything
    // else that carries letters is declined rather than risk empty boxes.
    var VERIFIED = /[\p{Script=Latin}\p{Script=Devanagari}]/u;
    var LETTER = /\p{L}/u;
    var NAMED = [
        ['Tamil', /\p{Script=Tamil}/u],
        ['Telugu', /\p{Script=Telugu}/u],
        ['Chinese, Japanese or Korean', /[\p{Script=Han}\p{Script=Hiragana}\p{Script=Katakana}\p{Script=Hangul}\p{Script=Bopomofo}]/u],
        ['Kannada', /\p{Script=Kannada}/u],
        ['Malayalam', /\p{Script=Malayalam}/u],
        ['Bengali', /\p{Script=Bengali}/u],
        ['Gujarati', /\p{Script=Gujarati}/u],
        ['Gurmukhi', /\p{Script=Gurmukhi}/u],
        ['Arabic', /\p{Script=Arabic}/u],
        ['Hebrew', /\p{Script=Hebrew}/u],
        ['Thai', /\p{Script=Thai}/u],
        ['Cyrillic', /\p{Script=Cyrillic}/u],
        ['Greek', /\p{Script=Greek}/u],
    ];

    function extOf(name) {
        var m = /\.([A-Za-z0-9]+)$/.exec(String(name || ''));
        return m ? m[1].toLowerCase() : '';
    }

    /** Names of letter scripts in `text` that the on-device fonts do not cover. */
    function uncoveredScripts(text) {
        var found = [];
        var other = false;
        var seen = {};
        for (var ch of String(text)) {
            if (!LETTER.test(ch) || VERIFIED.test(ch)) continue;
            var named = false;
            for (var i = 0; i < NAMED.length; i++) {
                if (NAMED[i][1].test(ch)) {
                    if (!seen[NAMED[i][0]]) { seen[NAMED[i][0]] = true; found.push(NAMED[i][0]); }
                    named = true;
                    break;
                }
            }
            if (!named) other = true;
        }
        if (other) found.push('another script');
        return found;
    }

    function xmlText(xml, tag) {
        // Concatenate text nodes of <tag>…</tag>, separating runs with a space so
        // adjacent words are not merged. Entities are left as-is: they are ASCII.
        var re = new RegExp('<' + tag + '(?:\\s[^>]*)?>([^<]*)</' + tag + '>', 'g');
        var out = [];
        var m;
        while ((m = re.exec(xml))) out.push(m[1]);
        return out.join(' ');
    }

    function stripTags(xml) { return xml.replace(/<[^>]*>/g, ' '); }

    async function zipEntries(JSZip, buffer, wanted) {
        var zip = await JSZip.loadAsync(buffer);
        var texts = [];
        var names = Object.keys(zip.files).filter(function (n) { return wanted.test(n) && !zip.files[n].dir; });
        for (var i = 0; i < names.length; i++) {
            var entry = zip.files[names[i]];
            var size = entry._data && entry._data.uncompressedSize;
            if (typeof size === 'number' && size > MAX_XML_BYTES) {
                var e = new Error('resource_budget_exceeded');
                e.code = 'resource_budget_exceeded';
                throw e;
            }
            texts.push(await entry.async('string'));
        }
        return texts;
    }

    /**
     * Decide whether this file may be converted on this device.
     *
     * Resolves {ok:true} or {ok:false, code, reason}. `code` is one of
     * unsupported_type, resource_budget_exceeded, undecodable, font_coverage_missing.
     * `reason` is fixed wording; it never contains document content.
     */
    async function preflight(file, op, deps) {
        var spec = OPS[op];
        if (!spec) return { ok: false, code: 'unsupported_type', reason: 'This tool is not available on this device.' };
        var ext = extOf(file && file.name);
        if (spec.exts.indexOf(ext) === -1) {
            return {
                ok: false, code: 'unsupported_type',
                reason: 'On-device conversion supports .' + spec.exts.join(', .') + ' files. Other formats need the server.',
            };
        }
        if (!file.size) return { ok: false, code: 'undecodable', reason: 'This file is empty.' };
        if (file.size > MAX_INPUT_BYTES) {
            return { ok: false, code: 'resource_budget_exceeded', reason: 'This file is larger than the 50 MB limit for on-device conversion.' };
        }
        var text = '';
        try {
            var buf = await file.arrayBuffer();
            var parts;
            if (spec.kind === 'word' && ext === 'docx') {
                parts = (await zipEntries(deps.JSZip, buf, /^word\/(document|header\d*|footer\d*|footnotes|endnotes)\.xml$/))
                    .map(function (x) { return xmlText(x, 'w:t'); });
            } else if (spec.kind === 'word') { // odt
                parts = (await zipEntries(deps.JSZip, buf, /^(content|styles)\.xml$/)).map(stripTags);
            } else if (spec.kind === 'excel') {
                parts = (await zipEntries(deps.JSZip, buf, /^xl\/(sharedStrings|worksheets\/sheet\d+|comments\d*)\.xml$/))
                    .map(function (x) { return xmlText(x, 't'); });
            } else {
                parts = (await zipEntries(deps.JSZip, buf, /^ppt\/(slides\/slide\d+|slideLayouts\/slideLayout\d+|slideMasters\/slideMaster\d+)\.xml$/))
                    .map(function (x) { return xmlText(x, 'a:t'); });
            }
            text = parts.join(' ');
        } catch (err) {
            if (err && err.code === 'resource_budget_exceeded') {
                return { ok: false, code: 'resource_budget_exceeded', reason: 'This document is too large to process safely on this device.' };
            }
            return { ok: false, code: 'undecodable', reason: 'This file could not be read on this device. It may be damaged or password protected.' };
        }
        var bad = uncoveredScripts(text);
        if (bad.length) {
            return {
                ok: false, code: 'font_coverage_missing', scripts: bad,
                reason: 'This document contains ' + bad.join(', ') + ' text, which this device\'s built-in fonts cannot show. The server has the fonts.',
            };
        }
        return { ok: true };
    }

    return {
        OPS: OPS, MAX_INPUT_BYTES: MAX_INPUT_BYTES, extOf: extOf,
        uncoveredScripts: uncoveredScripts, preflight: preflight,
    };
});
