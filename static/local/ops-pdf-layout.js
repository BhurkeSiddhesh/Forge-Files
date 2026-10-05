// Shared PDF layout analysis for the on-device semantic conversions
// (migration work packages 16, 42, 21).
//
// pdf.js hands back positioned text fragments. Turning them into something a
// reader can use (lines, paragraphs, headings, bullet lists, columns, links,
// images) is the same job for PDF to EPUB, PDF to Word and PDF to Excel, so it
// lives here once. Nothing in this file touches the DOM except `loadPage`'s
// image encoding; `analysePage` and everything below it are pure functions of
// pdf.js-shaped input, which is what the Node tests drive.
//
// Coordinates: pdf.js reports PDF user space (origin bottom-left, y up). Every
// model below is converted to "top-down" space (origin top-left, y down) so
// that sorting by `y` is reading order.
//
// What this does NOT do, and says so rather than guess (each case is a
// `Layout.Decline` the handler turns into `ffLocal.Unsupported`, which asks the
// user before the file goes to the server):
//   * right-to-left text (visual order cannot be recovered from fragments),
//   * pages that are mostly rotated text,
//   * three or more text columns,
//   * pages with no text layer that contain images (scans need OCR).
(function () {
    'use strict';

    var L = window.ffLocal;
    if (!L) return;

    /** A file this module cannot lay out faithfully. `code` is a gate reason code. */
    function Decline(message, code) {
        this.name = 'LayoutDecline';
        this.message = message;
        this.code = code || 'unsupported_structure';
    }
    Decline.prototype = Object.create(Error.prototype);

    var LIGATURES = { 'ﬀ': 'ff', 'ﬁ': 'fi', 'ﬂ': 'fl', 'ﬃ': 'ffi', 'ﬄ': 'ffl', 'ﬅ': 'st', 'ﬆ': 'st' };
    var BULLET_RE = /^\s*([•●○◦▪▫■□‣⁃∙·\-–—*])\s+(?=\S)/;
    var ORDERED_RE = /^\s*(\(?\d{1,3}[.)]|\(?[a-zA-Z][.)])\s+(?=\S)/;

    function normaliseText(s) {
        return String(s).replace(/[ﬀ-ﬆ]/g, function (c) { return LIGATURES[c]; })
            .replace(/­/g, '').replace(/\u0000/g, '');
    }

    function median(values) {
        if (!values.length) return 0;
        var v = values.slice().sort(function (a, b) { return a - b; });
        var m = v.length >> 1;
        return v.length % 2 ? v[m] : (v[m - 1] + v[m]) / 2;
    }

    // ── fonts ─────────────────────────────────────────────────────────────

    var BOLD_NAME = /bold|black|heavy|semibold|demi/i;
    var ITALIC_NAME = /italic|oblique|slanted/i;

    /**
     * Style for each pdf.js font id on a page. The font object only exists once
     * the operator list has been read, so callers do that first. A missing font
     * (never raised in practice) is just unstyled.
     */
    function fontStyles(page, fontNames) {
        var out = {};
        fontNames.forEach(function (id) {
            var f = null;
            try { f = page.commonObjs.get(id); } catch (e) { /* not resolved */ }
            var name = (f && (f.name || f.loadedName)) || '';
            out[id] = {
                bold: !!(f && f.bold) || BOLD_NAME.test(name),
                italic: !!(f && f.italic) || ITALIC_NAME.test(name),
                mono: !!(f && (f.isMonospace || f.fallbackName === 'monospace')) || /courier|mono|consolas/i.test(name),
                serif: !!(f && (f.isSerifFont || f.fallbackName === 'serif')),
                name: name.replace(/^[A-Z]{6}\+/, ''),
            };
        });
        return out;
    }

    // ── atoms and lines ───────────────────────────────────────────────────

    /**
     * pdf.js text items -> atoms with top-down geometry and style. Zero-width
     * and empty fragments are layout markers, not text, and are dropped.
     * Returns `{atoms, rotated, rtl}`; the caller decides what rotated/rtl mean.
     */
    function atomsFromItems(items, styles, height, offsetX) {
        var atoms = [];
        var rotated = 0;
        var rtl = 0;
        offsetX = offsetX || 0;
        for (var i = 0; i < items.length; i++) {
            var it = items[i];
            if (!it || typeof it.str !== 'string' || !it.str.length || !it.transform) continue;
            var t = it.transform;
            var size = Math.hypot(t[2], t[3]) || Math.abs(it.height) || 0;
            if (!size) continue;
            var text = normaliseText(it.str);
            if (!text.length) continue;
            if (Math.abs(t[1]) > 0.25 * Math.abs(t[0]) && text.trim()) {
                rotated += text.length;
                continue;
            }
            if (it.dir === 'rtl' && text.trim()) rtl += text.length;
            var st = styles[it.fontName] || {};
            atoms.push({
                str: text,
                x: t[4] - offsetX,
                x1: t[4] - offsetX + (it.width || 0),
                y: height - t[5], // baseline, top-down
                size: size,
                bold: !!st.bold, italic: !!st.italic, mono: !!st.mono, serif: !!st.serif,
                font: st.name || '',
                href: null,
            });
        }
        return { atoms: atoms, rotated: rotated, rtl: rtl };
    }

    // Relative advance widths (Helvetica, per 1000 em) for ASCII 32..126. Real fonts differ,
    // but only the proportions across one fragment matter: they place a link boundary inside
    // a run far better than assuming every character is the same width.
    var ASCII_WIDTHS = [278, 278, 355, 556, 556, 889, 667, 191, 333, 333, 389, 584, 278, 333, 278, 278,
        556, 556, 556, 556, 556, 556, 556, 556, 556, 556, 278, 278, 584, 584, 584, 556,
        1015, 667, 667, 722, 722, 667, 611, 778, 722, 278, 500, 667, 556, 833, 722, 778,
        667, 778, 722, 667, 611, 722, 667, 944, 667, 667, 611, 278, 278, 278, 469, 556,
        333, 556, 556, 500, 556, 556, 278, 556, 556, 222, 222, 500, 222, 833, 556, 556,
        556, 556, 333, 500, 278, 556, 500, 722, 500, 500, 500, 334, 260, 334, 584];

    function charWeight(ch, mono) {
        if (mono) return 600;
        var c = ch.charCodeAt(0);
        if (c >= 32 && c <= 126) return ASCII_WIDTHS[c - 32];
        if (c >= 0x2E80) return 1000; // CJK and wide symbols
        return 556;
    }

    /** Index in `str` of horizontal position `x` within a fragment spanning [x0, x1]. */
    function indexAt(str, mono, x0, x1, x, atEnd) {
        var total = 0;
        var w = [];
        for (var i = 0; i < str.length; i++) { w.push(charWeight(str.charAt(i), mono)); total += w[i]; }
        if (!total) return 0;
        var scale = (x1 - x0) / total;
        var cursor = x0;
        for (var j = 0; j < str.length; j++) {
            var mid = cursor + w[j] * scale / 2;
            if (atEnd ? mid > x : mid >= x) return j;
            cursor += w[j] * scale;
        }
        return str.length;
    }

    /** Pull a cut that lands inside a word (within 3 characters) to the word's edge. */
    function snapToWord(str, idx, preferStart) {
        if (idx <= 0 || idx >= str.length) return idx;
        if (/\s/.test(str.charAt(idx - 1)) || /\s/.test(str.charAt(idx))) return idx;
        for (var d = 1; d <= 3; d++) {
            var back = idx - d;
            var fwd = idx + d;
            if (preferStart) {
                if (back >= 0 && (back === 0 || /\s/.test(str.charAt(back - 1)))) return back;
                if (fwd < str.length && /\s/.test(str.charAt(fwd - 1))) return fwd;
            } else {
                if (fwd <= str.length && (fwd === str.length || /\s/.test(str.charAt(fwd)))) return fwd;
                if (back > 0 && /\s/.test(str.charAt(back))) return back;
            }
        }
        return idx;
    }

    /**
     * Cut atoms where a link annotation covers only part of their text. pdf.js
     * reports one fragment per font run, but a link often covers just "example.com"
     * inside it, so the boundary is found by position across the fragment using
     * proportional character widths, then pulled to the nearest word edge.
     */
    function applyLinks(atoms, links) {
        if (!links || !links.length) return atoms;
        var out = [];
        atoms.forEach(function (a) {
            var pieces = [{ s: 0, e: a.str.length, href: null }];
            links.forEach(function (lk) {
                // Baseline must sit inside the link box (with a little slack for descenders).
                if (a.y < lk.top - 0.2 * a.size || a.y > lk.bottom + 0.6 * a.size) return;
                var w = a.x1 - a.x;
                if (w <= 0 || lk.x1 <= a.x || lk.x0 >= a.x1) return;
                var s = lk.x0 <= a.x ? 0 : indexAt(a.str, a.mono, a.x, a.x1, lk.x0, false);
                var e = lk.x1 >= a.x1 ? a.str.length : indexAt(a.str, a.mono, a.x, a.x1, lk.x1, true);
                s = snapToWord(a.str, s, true);
                e = snapToWord(a.str, e, false);
                // A sentence's closing punctuation is not part of the address.
                while (e > s && /[.,;:!?)\]]/.test(a.str.charAt(e - 1))) e--;
                if (e <= s) return;
                var next = [];
                pieces.forEach(function (p) {
                    if (e <= p.s || s >= p.e) { next.push(p); return; }
                    if (s > p.s) next.push({ s: p.s, e: s, href: p.href });
                    next.push({ s: Math.max(s, p.s), e: Math.min(e, p.e), href: lk.url });
                    if (e < p.e) next.push({ s: e, e: p.e, href: p.href });
                });
                pieces = next;
            });
            if (pieces.length === 1 && !pieces[0].href) { out.push(a); return; }
            var total = 0;
            var cum = [0];
            for (var i = 0; i < a.str.length; i++) {
                total += charWeight(a.str.charAt(i), a.mono);
                cum.push(total);
            }
            var scale = (a.x1 - a.x) / (total || 1);
            pieces.forEach(function (p) {
                var copy = {};
                for (var k in a) copy[k] = a[k];
                copy.str = a.str.slice(p.s, p.e);
                copy.x = a.x + cum[p.s] * scale;
                copy.x1 = a.x + cum[p.e] * scale;
                copy.href = p.href;
                if (copy.str.length) out.push(copy);
            });
        });
        return out;
    }

    /** Group atoms into lines by shared baseline, then order each line left to right. */
    function groupLines(atoms) {
        var sorted = atoms.slice().sort(function (p, q) { return p.y - q.y || p.x - q.x; });
        var lines = [];
        sorted.forEach(function (a) {
            var line = lines.length ? lines[lines.length - 1] : null;
            if (line && Math.abs(a.y - line.y) <= 0.4 * Math.max(a.size, line.size)) {
                line.atoms.push(a);
                if (a.size > line.size && a.str.trim()) line.size = a.size;
                line.y = (line.y * (line.atoms.length - 1) + a.y) / line.atoms.length;
            } else {
                lines.push({ y: a.y, size: a.size, atoms: [a] });
            }
        });
        return lines.map(finishLine).filter(Boolean);
    }

    /** Merge a line's atoms into styled runs and compute its extents. */
    function finishLine(line) {
        var atoms = line.atoms.sort(function (p, q) { return p.x - q.x; });
        var runs = [];
        var prev = null; // previous fragment that carried text
        var pendingTab = false;
        var pendingSpace = false;
        var chars = 0;
        var sizeSum = 0;
        atoms.forEach(function (a) {
            if (!a.str.trim()) {
                // A space fragment. pdf.js gives a real gap (a tab stop, a right-aligned date) a
                // space fragment as wide as the gap; an ordinary word space is a fraction of the size.
                if (a.x1 - a.x > 2.5 * a.size) pendingTab = true; else if (prev) pendingSpace = true;
                return;
            }
            var text = a.str;
            var tab = pendingTab;
            if (prev && !tab) {
                var gap = a.x - prev.x1;
                var spaced = pendingSpace || /\s$/.test(prev.str) || /^\s/.test(text);
                if (gap > 2.5 * Math.max(prev.size, a.size)) tab = true;
                else if (!spaced && gap > 0.18 * Math.min(prev.size, a.size)) text = ' ' + text;
                else if (pendingSpace && !/^\s/.test(text) && !/\s$/.test(prev.str)) text = ' ' + text;
            }
            pendingTab = false;
            pendingSpace = false;
            var run = runs[runs.length - 1];
            if (tab) {
                text = text.replace(/^\s+/, '');
                if (run) run.text = run.text.replace(/\s+$/, '');
            }
            if (run && !tab && run.bold === a.bold && run.italic === a.italic && run.mono === a.mono &&
                run.href === a.href && Math.abs(run.size - a.size) < 0.6) {
                run.text += text;
                run.x1 = a.x1;
            } else {
                runs.push({ text: text, bold: a.bold, italic: a.italic, mono: a.mono, serif: a.serif, size: a.size,
                    href: a.href, font: a.font, tab: tab && !!run, x0: a.x, x1: a.x1 });
            }
            var n = a.str.trim().length;
            chars += n;
            sizeSum += n * a.size;
            prev = a;
        });
        if (!chars) return null;
        // Leading/trailing whitespace on a line carries no information.
        runs[0].text = runs[0].text.replace(/^\s+/, '');
        runs[runs.length - 1].text = runs[runs.length - 1].text.replace(/\s+$/, '');
        runs = runs.filter(function (r) { return r.text.length; });
        var first = atoms.filter(function (a) { return a.str.trim(); });
        return {
            y: line.y,
            size: sizeSum / chars,
            maxSize: line.size,
            x0: first[0].x,
            x1: first[first.length - 1].x1,
            runs: runs,
            atoms: atoms,
            chars: chars,
            text: runs.map(function (r) { return (r.tab ? '\t' : '') + r.text; }).join(''),
        };
    }

    // ── columns ───────────────────────────────────────────────────────────

    /**
     * Find text columns. Returns `{count, gutter}`. Two columns are only
     * reported for body text: a wide empty vertical band that nearly every line
     * leaves free, with long lines on both sides. A table's column gaps look
     * the same geometrically but its cells are short, so tables are not columns.
     * Works on atoms (single fragments), because one line's atoms from both
     * columns merge into a single styled run across the gutter.
     */
    function detectColumns(lines, width) {
        var body = lines.filter(function (l) { return l.chars >= 3; });
        if (body.length < 8) return { count: 1, gutter: null };
        var minX = Infinity;
        var maxX = -Infinity;
        body.forEach(function (l) { minX = Math.min(minX, l.x0); maxX = Math.max(maxX, l.x1); });
        var span = maxX - minX;
        if (span < width * 0.4) return { count: 1, gutter: null };

        var bins = Math.ceil(span) + 2;
        var crossing = new Uint16Array(bins);
        body.forEach(function (l) {
            l.atoms.forEach(function (a) {
                if (!a.str.trim()) return;
                var lo = Math.max(0, Math.floor(a.x - minX));
                var hi = Math.min(bins - 1, Math.ceil(a.x1 - minX));
                for (var x = lo; x <= hi; x++) crossing[x] = Math.min(65535, crossing[x] + 1);
            });
        });
        var limit = Math.max(1, Math.floor(body.length * 0.08));
        var gutters = [];
        var run = 0;
        for (var x = 0; x <= bins; x++) {
            if (x < bins && crossing[x] <= limit) { run++; continue; }
            if (run >= 12) {
                var centre = minX + x - run / 2;
                if (centre > minX + span * 0.15 && centre < minX + span * 0.85) gutters.push({ x: centre, w: run });
            }
            run = 0;
        }
        if (!gutters.length) return { count: 1, gutter: null };

        function sides(g) {
            var left = [];
            var right = [];
            body.forEach(function (l) {
                var lc = 0;
                var rc = 0;
                l.atoms.forEach(function (a) {
                    var n = a.str.trim().length;
                    if ((a.x + a.x1) / 2 < g) lc += n; else rc += n;
                });
                if (lc) left.push(lc);
                if (rc) right.push(rc);
            });
            return { left: left, right: right };
        }

        // Prose columns have long lines on both sides of every gutter; table cells do not.
        var real = gutters.filter(function (gt) {
            var sd = sides(gt.x);
            return sd.left.length >= 5 && sd.right.length >= 5 && median(sd.left) >= 14 && median(sd.right) >= 14;
        });
        if (real.length === 0) return { count: 1, gutter: null };
        if (real.length > 1) return { count: real.length + 1, gutter: real[0].x };
        return { count: 2, gutter: real[0].x };
    }

    /** Rebuild a line from a subset of its atoms (one column's share of a line). */
    function lineFromAtoms(line, atoms) {
        return finishLine({ y: line.y, size: line.maxSize || line.size, atoms: atoms.slice() });
    }

    /**
     * Put lines in reading order. A two-column page reads the left column then
     * the right one, with full-width lines (titles) closing the zone above them.
     */
    function readingOrder(lines, columns) {
        if (columns.count !== 2) return lines;
        var g = columns.gutter;
        var out = [];
        var left = [];
        var right = [];
        function flush() {
            Array.prototype.push.apply(out, left);
            Array.prototype.push.apply(out, right);
            left = [];
            right = [];
        }
        lines.forEach(function (line) {
            var spans = line.atoms.some(function (a) { return a.str.trim() && a.x < g - 2 && a.x1 > g + 2; });
            if (spans) {
                flush();
                out.push(line);
                return;
            }
            var la = line.atoms.filter(function (a) { return (a.x + a.x1) / 2 < g; });
            var ra = line.atoms.filter(function (a) { return (a.x + a.x1) / 2 >= g; });
            var l = la.length ? lineFromAtoms(line, la) : null;
            var r = ra.length ? lineFromAtoms(line, ra) : null;
            if (l) left.push(l);
            if (r) right.push(r);
        });
        flush();
        return out;
    }

    // ── paragraphs ────────────────────────────────────────────────────────

    /** The body font size of a set of pages: the size carrying the most characters. */
    function bodySize(pages) {
        var counts = {};
        pages.forEach(function (p) {
            p.lines.forEach(function (l) {
                l.runs.forEach(function (r) {
                    var k = Math.round(r.size * 10) / 10;
                    counts[k] = (counts[k] || 0) + r.text.length;
                });
            });
        });
        var best = 12;
        var most = 0;
        Object.keys(counts).forEach(function (k) {
            if (counts[k] > most) { most = counts[k]; best = parseFloat(k); }
        });
        return best;
    }

    function startsList(text) {
        if (BULLET_RE.test(text)) return { ordered: false, marker: BULLET_RE.exec(text)[0] };
        if (ORDERED_RE.test(text)) return { ordered: true, marker: ORDERED_RE.exec(text)[0] };
        return null;
    }

    function stripMarker(runs, marker) {
        var out = runs.map(function (r) { var c = {}; for (var k in r) c[k] = r[k]; return c; });
        var left = marker.length;
        for (var i = 0; i < out.length && left > 0; i++) {
            var cut = Math.min(left, out[i].text.length);
            out[i].text = out[i].text.slice(cut);
            left -= cut;
        }
        return out.filter(function (r) { return r.text.length; });
    }

    /**
     * Assemble a page's lines into blocks: headings, paragraphs and list items.
     * Lines join into one paragraph while they continue the same text block
     * (similar left edge, normal leading, previous line reached the right
     * margin). A line that stops short ends its paragraph, which keeps
     * addresses, headings and verse as separate lines instead of one run-on.
     */
    function paragraphs(lines, ctx) {
        var base = ctx.base;
        var rightOf = typeof ctx.right === 'function' ? ctx.right : function () { return ctx.right; };
        var blocks = [];
        var cur = null;

        // Each contiguous run of lines (same left edge, no gap) wraps at its own right edge, so a
        // narrower block (a quotation, a column inside a box) is not mistaken for hard returns.
        var localRight = [];
        var runStart = 0;
        function closeRun(end) {
            var widest = 0;
            for (var k = runStart; k < end; k++) widest = Math.max(widest, lines[k].x1);
            for (var j = runStart; j < end; j++) localRight[j] = end - runStart > 1 ? widest : 0;
        }
        for (var q = 0; q < lines.length; q++) {
            if (q > 0) {
                var before = lines[q - 1];
                var step = lines[q].y - before.y;
                var big = Math.max(before.size, lines[q].size);
                if (step > 1.7 * big || step < 0 || Math.abs(lines[q].x0 - lines[runStart].x0) > 4 * big) {
                    closeRun(q);
                    runStart = q;
                }
            }
        }
        closeRun(lines.length);

        function close() {
            if (cur) blocks.push(cur);
            cur = null;
        }

        for (var i = 0; i < lines.length; i++) {
            var line = lines[i];
            var prev = i ? lines[i - 1] : null;
            var list = startsList(line.text);
            var ratio = line.size / base;
            var headingish = line.chars <= 140 && ratio >= 1.15;

            var pitch = prev ? line.y - prev.y : 0;
            var leading = Math.max(prev ? prev.size : line.size, line.size);
            // A new block starts when any of these says the text is not continuing.
            var newBlock = !cur || !prev ||
                !!list ||                                          // bullet / number
                !cur.cont ||                                       // previous line stopped short
                pitch > 1.7 * leading || pitch < 0 ||              // gap, or a jump back up the page
                line.x0 < cur.x0 - 2.2 * line.size ||              // outdent
                // indent: a new paragraph, except the hanging indent of a wrapped list item
                (line.x0 > cur.x0 + (cur.kind === 'li' ? 3.5 : 1.6) * line.size) ||
                ((headingish || cur.heading) && Math.abs(line.size - cur.size) > 0.6);

            if (newBlock) {
                close();
                var runs = line.runs;
                var kind = 'p';
                var ordered = false;
                if (list) { kind = 'li'; ordered = list.ordered; runs = stripMarker(runs, list.marker); }
                else if (headingish) kind = ratio >= 1.45 ? 'h2' : 'h3';
                cur = { kind: kind, ordered: ordered, runs: runs.slice(), x0: line.x0, size: line.size, y: line.y,
                    page: ctx.page, heading: kind[0] === 'h', cont: false, lineCount: 1, chars: line.chars, x1: line.x1, y1: line.y,
                    ends: [line.x1], ys: [line.y] };
            } else {
                var tail = cur.runs[cur.runs.length - 1];
                var head = line.runs[0];
                var joiner = ' ';
                if (/-$/.test(tail.text) && /^[a-z]/.test(head.text) && /[A-Za-z]-$/.test(tail.text)) {
                    tail.text = tail.text.slice(0, -1); // soft line-break hyphen
                    joiner = '';
                }
                if (joiner) tail.text += joiner;
                line.runs.forEach(function (r) {
                    var last = cur.runs[cur.runs.length - 1];
                    if (!r.tab && last.bold === r.bold && last.italic === r.italic && last.mono === r.mono && last.href === r.href && Math.abs(last.size - r.size) < 0.6) {
                        last.text += r.text;
                        last.x1 = r.x1;
                    } else {
                        cur.runs.push({ text: r.text, bold: r.bold, italic: r.italic, mono: r.mono, serif: r.serif, size: r.size, href: r.href,
                            font: r.font, tab: !!r.tab, x0: r.x0, x1: r.x1 });
                    }
                });
                cur.lineCount++;
                cur.ends.push(line.x1);
                cur.ys.push(line.y);
                cur.chars += line.chars;
                cur.x1 = Math.max(cur.x1, line.x1);
                cur.y1 = line.y;
            }
            // The text continues onto the next line only if that line's first word would not have
            // fitted here: then the break was a wrap, not a hard return.
            cur.cont = !cur.heading && wrapped(line, lines[i + 1], localRight[i] || rightOf(line));
        }
        close();
        return blocks;
    }

    /** Width in points of the first word of a line, from proportional character widths. */
    function firstWordWidth(line) {
        var a = line.atoms && line.atoms[0];
        var text = a ? a.str : line.text;
        var size = a ? a.size : line.size;
        var mono = !!(a && a.mono);
        var w = 0;
        for (var i = 0; i < text.length; i++) {
            if (/\s/.test(text.charAt(i))) { if (w) break; else continue; }
            w += charWeight(text.charAt(i), mono);
        }
        return w * size / 1000;
    }

    /**
     * True when `line` was broken by wrapping: the next line's first word would not have
     * fitted in the space left before the right margin. False for a hard return (a short last
     * line), a missing next line, or a next line that starts a different block.
     */
    function wrapped(line, next, right) {
        if (!next) return false;
        // A word broken with a hyphen at the margin continues on the next line.
        if (/[A-Za-z]-$/.test(line.text) && /^[a-z]/.test(next.text) && next.y - line.y > 0 && next.y - line.y < 1.7 * Math.max(line.size, next.size)) return true;
        if (!right) return false;
        if (next.y - line.y < 0 || next.y - line.y > 1.7 * Math.max(line.size, next.size)) return false;
        var room = right - line.x1;
        return firstWordWidth(next) + 0.28 * line.size > room - 0.35 * line.size;
    }

    /**
     * The right margin as a function of a line, so each column of a two-column page is measured
     * on its own side. Where the longer lines end (90th percentile) is the margin.
     */
    function rightMargins(lines, columns) {
        function margin(set) {
            var ends = set.filter(function (l) { return l.chars >= 25; }).map(function (l) { return l.x1; });
            if (!ends.length) ends = set.map(function (l) { return l.x1; });
            if (!ends.length) return 0;
            ends.sort(function (a, b) { return a - b; });
            return ends[Math.floor(ends.length * 0.9)];
        }
        if (columns && columns.count === 2) {
            var g = columns.gutter;
            var l = margin(lines.filter(function (x) { return x.x0 < g; }));
            var r = margin(lines.filter(function (x) { return x.x0 >= g; }));
            return function (line) { return line.x0 < g ? l : r; };
        }
        var all = margin(lines);
        return function () { return all; };
    }

    /** The right text margin of a page: where the longer lines end. */
    function rightMargin(lines) {
        var ends = lines.filter(function (l) { return l.chars >= 25; }).map(function (l) { return l.x1; });
        if (!ends.length) ends = lines.map(function (l) { return l.x1; });
        if (!ends.length) return 0;
        ends.sort(function (a, b) { return a - b; });
        return ends[Math.floor(ends.length * 0.9)];
    }

    // ── page model ────────────────────────────────────────────────────────

    /**
     * Pure analysis of one page's pdf.js output.
     * @param {object} src `{items, styles, view:[x0,y0,x1,y1], links:[{url, rect}]}`
     */
    function analysePage(src) {
        var view = src.view || [0, 0, 612, 792];
        var width = view[2] - view[0];
        var height = view[3] - view[1];
        var parsed = atomsFromItems(src.items || [], src.styles || {}, height + view[1], view[0]);
        var totalChars = 0;
        parsed.atoms.forEach(function (a) { totalChars += a.str.trim().length; });
        if (parsed.rtl && parsed.rtl > totalChars * 0.2) {
            throw new Decline('right-to-left text cannot be ordered on-device', 'unsupported_structure');
        }
        if (parsed.rotated > (totalChars + parsed.rotated) * 0.3) {
            throw new Decline('rotated text cannot be ordered on-device', 'unsupported_structure');
        }
        var links = (src.links || []).map(function (a) {
            var r = a.rect;
            return {
                url: a.url,
                x0: Math.min(r[0], r[2]) - view[0], x1: Math.max(r[0], r[2]) - view[0],
                top: height + view[1] - Math.max(r[1], r[3]), bottom: height + view[1] - Math.min(r[1], r[3]),
            };
        });
        var atoms = applyLinks(parsed.atoms, links);
        var lines = groupLines(atoms);
        var columns = detectColumns(lines, width);
        return {
            width: width,
            height: height,
            columns: columns,
            lines: lines,
            atoms: atoms,
            ordered: readingOrder(lines, columns),
            chars: totalChars,
            rotated: parsed.rotated,
        };
    }

    // ── headers, footers, page numbers ────────────────────────────────────

    function shape(text) {
        return text.replace(/\d+/g, '#').replace(/\s+/g, ' ').trim().toLowerCase();
    }

    var PAGE_NUMBER = /^(page\s+)?([0-9]{1,4}|[ivxlcdm]{1,7})(\s*(\/|of)\s*[0-9]{1,4})?$|^[-–—]\s*[0-9]{1,4}\s*[-–—]$/i;

    /**
     * Drop running heads, footers and page numbers: lines in the top or bottom
     * margin that repeat across pages (digits ignored), or are a bare page
     * number. They interrupt paragraphs that continue across a page break and
     * mean nothing in a reflowed document.
     */
    function removeRunningLines(pages) {
        var band = 0.09;
        var counts = {};
        pages.forEach(function (p) {
            var seen = {};
            p.lines.forEach(function (l) {
                if (!inBand(l, p, band)) return;
                var key = shape(l.text);
                if (key && !seen[key]) { seen[key] = 1; counts[key] = (counts[key] || 0) + 1; }
            });
        });
        var need = Math.max(3, Math.ceil(pages.length * 0.4));
        var removed = 0;
        pages.forEach(function (p) {
            function drops(l) {
                return inBand(l, p, band) && (PAGE_NUMBER.test(l.text.trim()) && pages.length > 1 ||
                    pages.length >= 3 && counts[shape(l.text)] >= need);
            }
            var gone = p.lines.filter(drops);
            removed += gone.length;
            // `ordered` may hold re-cut copies of a line (two-column pages): match by position and text.
            p.lines = p.lines.filter(function (l) { return !drops(l); });
            p.ordered = p.ordered.filter(function (l) { return !drops(l); });
        });
        return removed;
    }

    function inBand(line, page, band) {
        return line.y < page.height * band || line.y > page.height * (1 - band);
    }

    // ── document-level helpers ────────────────────────────────────────────

    /** Resolve the PDF outline to `[{title, page (0-based)}]` for its top level. */
    async function outline(doc) {
        var out = [];
        var tree = null;
        try { tree = await doc.getOutline(); } catch (e) { return out; }
        if (!tree) return out;
        for (var i = 0; i < tree.length; i++) {
            var node = tree[i];
            var title = String(node.title || '').replace(/\s+/g, ' ').trim();
            if (!title) continue;
            try {
                var dest = node.dest;
                if (typeof dest === 'string') dest = await doc.getDestination(dest);
                if (!Array.isArray(dest) || !dest.length) continue;
                var page = typeof dest[0] === 'object' ? await doc.getPageIndex(dest[0]) : dest[0];
                if (typeof page === 'number' && page >= 0 && page < doc.numPages) out.push({ title: title, page: page });
            } catch (e) { /* unresolvable bookmark: skip it */ }
        }
        return out;
    }

    var IMAGE_OPS = ['paintImageXObject', 'paintInlineImageXObject', 'paintImageMaskXObject', 'paintJpegXObject'];

    function mulMatrix(a, b) { // a applied after b
        return [
            a[0] * b[0] + a[2] * b[1], a[1] * b[0] + a[3] * b[1],
            a[0] * b[2] + a[2] * b[3], a[1] * b[2] + a[3] * b[3],
            a[0] * b[4] + a[2] * b[5] + a[4], a[1] * b[4] + a[3] * b[5] + a[5],
        ];
    }

    /**
     * pdf.js 6 path data: a flat array of `0 x y` (move), `1 x y` (line), `2 x1 y1 x2 y2 x3 y3`
     * (curve), `3 x1 y1 x2 y2` (quadratic) and `4` (close). Returns sub-paths of points.
     */
    function parsePath(data) {
        var subs = [];
        var cur = null;
        var i = 0;
        while (i < data.length) {
            var op = data[i++];
            if (op === 0) { cur = { pts: [[data[i], data[i + 1]]], closed: false, curved: false }; subs.push(cur); i += 2; }
            else if (op === 1) {
                if (!cur) { cur = { pts: [], closed: false, curved: false }; subs.push(cur); }
                cur.pts.push([data[i], data[i + 1]]);
                i += 2;
            } else if (op === 2) { i += 6; if (cur) cur.curved = true; }
            else if (op === 3) { i += 4; if (cur) cur.curved = true; }
            else if (op === 4) { if (cur) cur.closed = true; }
            else break;
        }
        return subs;
    }

    /**
     * Read the operator list once: placed images (id, size, box in top-down
     * space), whether the page paints any raster at all, and the straight
     * ruling lines and thin filled bars that draw table borders. Tracks the CTM
     * through save/restore/transform so each shape gets its real placement.
     * @returns {{images, anyRaster, rules:{h:[{y,x0,x1}], v:[{x,y0,y1}]}}}
     */
    function scanOperators(pdfjs, list, height, offset) {
        var OPS = pdfjs.OPS || {};
        var stack = [];
        var m = [1, 0, 0, 1, 0, 0];
        var images = [];
        var anyRaster = false;
        var rules = { h: [], v: [] };
        var top = height + offset[1];

        function names(arr) { return arr.map(function (n) { return OPS[n]; }).filter(function (v) { return v !== undefined; }); }
        var strokeOps = names(['stroke', 'closeStroke', 'fillStroke', 'eoFillStroke', 'closeFillStroke', 'closeEOFillStroke']);
        var fillOps = names(['fill', 'eoFill', 'fillStroke', 'eoFillStroke', 'closeFillStroke', 'closeEOFillStroke']);

        function td(pt) { // path point -> top-down page coordinates
            return [m[0] * pt[0] + m[2] * pt[1] + m[4] - offset[0], top - (m[1] * pt[0] + m[3] * pt[1] + m[5])];
        }
        function addSegment(p, q) {
            var dx = Math.abs(q[0] - p[0]);
            var dy = Math.abs(q[1] - p[1]);
            if (dy <= 0.8 && dx >= 6) rules.h.push({ y: (p[1] + q[1]) / 2, x0: Math.min(p[0], q[0]), x1: Math.max(p[0], q[0]) });
            else if (dx <= 0.8 && dy >= 6) rules.v.push({ x: (p[0] + q[0]) / 2, y0: Math.min(p[1], q[1]), y1: Math.max(p[1], q[1]) });
        }

        for (var i = 0; i < list.fnArray.length; i++) {
            var fn = list.fnArray[i];
            var args = list.argsArray[i];
            if (fn === OPS.save) stack.push(m.slice());
            else if (fn === OPS.restore) m = stack.pop() || [1, 0, 0, 1, 0, 0];
            else if (fn === OPS.transform) m = mulMatrix(m, args);
            else if (fn === OPS.constructPath && args && Array.isArray(args[1])) {
                var paint = args[0];
                var stroke = strokeOps.indexOf(paint) >= 0;
                var fill = fillOps.indexOf(paint) >= 0;
                if (!stroke && !fill) continue;
                args[1].forEach(function (data) {
                    parsePath(data).forEach(function (sp) {
                        if (sp.curved || sp.pts.length < 2) return;
                        var pts = sp.pts.map(td);
                        if (fill && !stroke && pts.length >= 4) {
                            // A thin filled rectangle is how many generators draw a table rule.
                            var xs = pts.map(function (q) { return q[0]; });
                            var ys = pts.map(function (q) { return q[1]; });
                            var x0 = Math.min.apply(null, xs);
                            var x1 = Math.max.apply(null, xs);
                            var y0 = Math.min.apply(null, ys);
                            var y1 = Math.max.apply(null, ys);
                            if (y1 - y0 <= 2.5 && x1 - x0 >= 6) rules.h.push({ y: (y0 + y1) / 2, x0: x0, x1: x1 });
                            else if (x1 - x0 <= 2.5 && y1 - y0 >= 6) rules.v.push({ x: (x0 + x1) / 2, y0: y0, y1: y1 });
                            return;
                        }
                        if (!stroke) return;
                        for (var k = 1; k < pts.length; k++) addSegment(pts[k - 1], pts[k]);
                        if (sp.closed && pts.length > 2) addSegment(pts[pts.length - 1], pts[0]);
                    });
                });
            } else if (fn === OPS.paintImageXObject || fn === OPS.paintJpegXObject) {
                anyRaster = true;
                var x0 = m[4];
                var y0 = m[5];
                var w = Math.hypot(m[0], m[1]);
                var h = Math.hypot(m[2], m[3]);
                images.push({ id: args[0], pw: args[1], ph: args[2], x: x0 - offset[0], y: height + offset[1] - (y0 + h), w: w, h: h });
            } else if (IMAGE_OPS.some(function (n) { return OPS[n] === fn; })) {
                anyRaster = true;
            }
        }
        return { images: images, anyRaster: anyRaster, rules: rules };
    }

    // ── tables ────────────────────────────────────────────────────────────

    function clusterValues(values, tol) {
        var v = values.slice().sort(function (p, q) { return p - q; });
        var out = [];
        v.forEach(function (x) {
            var last = out[out.length - 1];
            if (last && x - last.sum / last.n <= tol) { last.sum += x; last.n++; last.hi = x; }
            else out.push({ sum: x, n: 1, hi: x });
        });
        return out.map(function (c) { return c.sum / c.n; });
    }

    /** Merge collinear, overlapping or touching ruling segments. */
    function mergeRules(list, key, lo, hi) {
        var groups = [];
        list.slice().sort(function (p, q) { return p[key] - q[key]; }).forEach(function (r) {
            var g = groups[groups.length - 1];
            if (g && Math.abs(r[key] - g.pos / g.n) <= 1.5) { g.items.push(r); g.pos += r[key]; g.n++; }
            else groups.push({ pos: r[key], n: 1, items: [r] });
        });
        var out = [];
        groups.forEach(function (g) {
            var items = g.items.sort(function (p, q) { return p[lo] - q[lo]; });
            var cur = null;
            items.forEach(function (r) {
                if (cur && r[lo] <= cur[hi] + 3) cur[hi] = Math.max(cur[hi], r[hi]);
                else {
                    cur = {}; cur[key] = g.pos / g.n; cur[lo] = r[lo]; cur[hi] = r[hi];
                    out.push(cur);
                }
            });
        });
        return out;
    }

    function covers(segs, key, pos, lo, hi, from, to, need) {
        // Fraction of [from, to] covered by segments on line `pos` (within 2 pt).
        var covered = 0;
        segs.forEach(function (s) {
            if (Math.abs(s[key] - pos) > 2) return;
            var a = Math.max(from, s[lo]);
            var b = Math.min(to, s[hi]);
            if (b > a) covered += b - a;
        });
        return covered >= need * (to - from);
    }

    /**
     * Ruled tables: connected grids of horizontal and vertical lines with at least a 2 x 2 body.
     * A cell boundary that has no line marks the table `merged` (cells span rows or columns), which
     * the callers treat as too complex to reproduce faithfully.
     */
    function detectRuledTables(rules, atoms) {
        var H = mergeRules(rules.h || [], 'y', 'x0', 'x1');
        var V = mergeRules(rules.v || [], 'x', 'y0', 'y1');
        if (H.length < 3 || V.length < 3) return [];

        // Union-find over lines that touch.
        var nodes = H.map(function (h) { return { t: 'h', s: h }; }).concat(V.map(function (v) { return { t: 'v', s: v }; }));
        var parent = nodes.map(function (_, i) { return i; });
        function find(i) { while (parent[i] !== i) { parent[i] = parent[parent[i]]; i = parent[i]; } return i; }
        for (var i = 0; i < H.length; i++) {
            for (var j = 0; j < V.length; j++) {
                var h = H[i];
                var v = V[j];
                if (v.x >= h.x0 - 2.5 && v.x <= h.x1 + 2.5 && h.y >= v.y0 - 2.5 && h.y <= v.y1 + 2.5) {
                    parent[find(i)] = find(H.length + j);
                }
            }
        }
        var comps = {};
        nodes.forEach(function (nd, idx) { (comps[find(idx)] = comps[find(idx)] || []).push(nd); });

        var tables = [];
        Object.keys(comps).forEach(function (key) {
            var hs = comps[key].filter(function (n) { return n.t === 'h'; }).map(function (n) { return n.s; });
            var vs = comps[key].filter(function (n) { return n.t === 'v'; }).map(function (n) { return n.s; });
            var ys = clusterValues(hs.map(function (r) { return r.y; }), 2);
            var xs = clusterValues(vs.map(function (r) { return r.x; }), 2);
            var rows = ys.length - 1;
            var cols = xs.length - 1;
            if (rows < 1 || cols < 1 || rows * cols < 4) return;

            // A boundary with no line means a merged cell.
            var merged = false;
            for (var r = 0; r < rows && !merged; r++) {
                for (var c = 0; c < cols && !merged; c++) {
                    if (r + 1 < rows && !covers(hs, 'y', ys[r + 1], 'x0', 'x1', xs[c] + 1, xs[c + 1] - 1, 0.8)) merged = true;
                    if (c + 1 < cols && !covers(vs, 'x', xs[c + 1], 'y0', 'y1', ys[r] + 1, ys[r + 1] - 1, 0.8)) merged = true;
                }
            }

            var cellAtoms = [];
            for (var rr = 0; rr < rows; rr++) { cellAtoms.push([]); for (var cc = 0; cc < cols; cc++) cellAtoms[rr].push([]); }
            var inside = [];
            atoms.forEach(function (a) {
                if (!a.str.trim()) return;
                var cx = (a.x + a.x1) / 2;
                var cy = a.y - 0.3 * a.size;
                if (cx < xs[0] || cx > xs[cols] || cy < ys[0] || cy > ys[rows]) return;
                var ri = -1;
                var ci = -1;
                for (var q = 0; q < rows; q++) if (cy >= ys[q] && cy < ys[q + 1]) { ri = q; break; }
                for (var w = 0; w < cols; w++) if (cx >= xs[w] && cx < xs[w + 1]) { ci = w; break; }
                if (ri < 0 || ci < 0) return;
                cellAtoms[ri][ci].push(a);
                inside.push(a);
            });
            if (!inside.length) return;

            var cells = cellAtoms.map(function (row) {
                return row.map(function (list) {
                    var lines = groupLines(list);
                    return { lines: lines, text: lines.map(function (l) { return l.text.replace(/\t/g, ' '); }).join('\n') };
                });
            });
            tables.push({
                kind: 'ruled', merged: merged, rows: rows, cols: cols, xs: xs, ys: ys, cells: cells,
                bbox: { x0: xs[0], y0: ys[0], x1: xs[cols], y1: ys[rows] }, atoms: inside,
            });
        });
        return tables;
    }

    /**
     * Borderless tables: three or more consecutive lines whose gaps (tab runs) line up into the same
     * columns, by left edge or, for right-aligned numbers, by right edge.
     */
    function detectBorderlessTables(lines) {
        function segments(line) {
            var segs = [];
            line.runs.forEach(function (r) {
                if (r.tab || !segs.length) segs.push({ x0: r.x0, x1: r.x1, text: r.text, runs: [r] });
                else { var g = segs[segs.length - 1]; g.x1 = r.x1; g.text += r.text; g.runs.push(r); }
            });
            return segs;
        }
        var tables = [];
        var i = 0;
        while (i < lines.length) {
            var segs = segments(lines[i]);
            if (segs.length < 2) { i++; continue; }
            var j = i + 1;
            while (j < lines.length && segments(lines[j]).length >= 2 &&
                lines[j].y - lines[j - 1].y > 0 && lines[j].y - lines[j - 1].y < 2.4 * Math.max(lines[j].size, lines[j - 1].size)) j++;
            var run = lines.slice(i, j);
            if (run.length >= 3) {
                var t = alignColumns(run.map(segments));
                if (t) {
                    t.lines = run;
                    t.bbox = {
                        x0: Math.min.apply(null, run.map(function (l) { return l.x0; })), y0: run[0].y - run[0].size,
                        x1: Math.max.apply(null, run.map(function (l) { return l.x1; })), y1: run[run.length - 1].y,
                    };
                    tables.push(t);
                }
            }
            i = Math.max(j, i + 1);
        }
        return tables;
    }

    function alignColumns(rows) {
        var starts = clusterValues([].concat.apply([], rows.map(function (r) { return r.map(function (g) { return g.x0; }); })), 4);
        var ends = clusterValues([].concat.apply([], rows.map(function (r) { return r.map(function (g) { return g.x1; }); })), 4);
        function nearest(list, v) {
            var best = -1;
            var d = 4.01;
            list.forEach(function (c, k) { if (Math.abs(c - v) < d) { d = Math.abs(c - v); best = k; } });
            return best;
        }
        // A start (or end) position is a column edge when most rows use it.
        function popular(list, pick) {
            return list.map(function (c, k) {
                var n = 0;
                rows.forEach(function (r) { if (r.some(function (g) { return nearest(list, pick(g)) === k; })) n++; });
                return n;
            });
        }
        var sCount = popular(starts, function (g) { return g.x0; });
        var eCount = popular(ends, function (g) { return g.x1; });
        var need = Math.ceil(rows.length * 0.6);
        var leftCols = starts.filter(function (_, k) { return sCount[k] >= need; });
        var rightCols = ends.filter(function (_, k) { return eCount[k] >= need; });

        // Every segment must belong to a left-aligned or right-aligned column.
        var anchors = [];
        leftCols.forEach(function (x) { anchors.push({ edge: 'l', x: x }); });
        rightCols.forEach(function (x) { anchors.push({ edge: 'r', x: x }); });
        var orphans = 0;
        var total = 0;
        var grid = rows.map(function (r) {
            var row = [];
            r.forEach(function (g) {
                total++;
                var best = null;
                anchors.forEach(function (an) {
                    var d = Math.abs((an.edge === 'l' ? g.x0 : g.x1) - an.x);
                    if (d <= 4 && (!best || d < best.d)) best = { an: an, d: d };
                });
                if (!best) { orphans++; return; }
                row.push({ an: best.an, g: g });
            });
            return row;
        });
        if (orphans > 0.2 * total) return null;

        // Order the distinct columns by their centre and build the cell grid.
        var colList = [];
        anchors.forEach(function (an) {
            var users = [];
            grid.forEach(function (row) { row.forEach(function (c) { if (c.an === an) users.push(c.g); }); });
            if (!users.length) return;
            var centre = users.reduce(function (sum, g) { return sum + (g.x0 + g.x1) / 2; }, 0) / users.length;
            colList.push({ an: an, centre: centre });
        });
        colList.sort(function (p, q) { return p.centre - q.centre; });
        // Two anchors (left and right edge of the same text column) can describe one column.
        var merged = [];
        colList.forEach(function (c) {
            var prev = merged[merged.length - 1];
            if (prev && Math.abs(prev.centre - c.centre) < 12) return;
            merged.push(c);
        });
        if (merged.length < 2) return null;
        var cells = grid.map(function (row) {
            var out = merged.map(function () { return { text: '', runs: [] }; });
            row.forEach(function (c) {
                var k = 0;
                var d = Infinity;
                merged.forEach(function (m, idx) {
                    var gd = Math.abs((c.g.x0 + c.g.x1) / 2 - m.centre);
                    if (gd < d) { d = gd; k = idx; }
                });
                out[k].text += (out[k].text ? ' ' : '') + c.g.text;
                out[k].runs = out[k].runs.concat(c.g.runs);
            });
            return out;
        });
        // Column boundaries sit midway between neighbouring columns' extents.
        var extents = merged.map(function () { return { lo: Infinity, hi: -Infinity }; });
        grid.forEach(function (row) {
            row.forEach(function (c) {
                var k = 0;
                var d = Infinity;
                merged.forEach(function (m, idx) {
                    var gd = Math.abs((c.g.x0 + c.g.x1) / 2 - m.centre);
                    if (gd < d) { d = gd; k = idx; }
                });
                extents[k].lo = Math.min(extents[k].lo, c.g.x0);
                extents[k].hi = Math.max(extents[k].hi, c.g.x1);
            });
        });
        var xs = [extents[0].lo - 3];
        for (var e = 1; e < extents.length; e++) xs.push((extents[e - 1].hi + extents[e].lo) / 2);
        xs.push(extents[extents.length - 1].hi + 3);
        var aligns = merged.map(function (m) { return m.an.edge === 'r' ? 'right' : 'left'; });
        return { kind: 'borderless', merged: false, rows: cells.length, cols: merged.length, cells: cells, xs: xs, aligns: aligns };
    }

    /**
     * Find every table on a page and the lines that remain once table text is taken out.
     * Ruled tables win; borderless detection runs on what is left.
     */
    function findTables(model, rules) {
        var ruled = detectRuledTables(rules || { h: [], v: [] }, model.atoms || []);
        var taken = {};
        ruled.forEach(function (t) { t.atoms.forEach(function (a) { taken[a.x + ',' + a.y + ',' + a.str] = 1; }); });
        var rest = (model.atoms || []).filter(function (a) { return !taken[a.x + ',' + a.y + ',' + a.str]; });
        var flowLines = groupLines(rest);
        // Prose columns look like an aligned table to the borderless detector, so columns win:
        // a page with real text columns reports them (callers decline or reorder) and gets no
        // borderless table.
        var cols = detectColumns(flowLines, model.width);
        var loose = cols.count > 1 ? [] : detectBorderlessTables(flowLines);
        if (loose.length) {
            var gone = [];
            loose.forEach(function (t) { gone = gone.concat(t.lines); });
            flowLines = flowLines.filter(function (l) { return gone.indexOf(l) < 0; });
            cols = detectColumns(flowLines, model.width);
        }
        var tables = ruled.concat(loose).sort(function (p, q) { return p.bbox.y0 - q.bbox.y0; });
        return { tables: tables, flow: readingOrder(flowLines, cols), flowColumns: cols };
    }

    // ── figures ───────────────────────────────────────────────────────────

    var MIN_FIGURE_SIDE = 40; // the server skips anything smaller
    var MAX_FIGURE_PIXELS = 40000000;

    function makeCanvas(w, h) {
        var c = document.createElement('canvas');
        c.width = w;
        c.height = h;
        return c;
    }

    function canvasBlob(canvas, type, quality) {
        return new Promise(function (ok, fail) {
            canvas.toBlob(function (b) { if (b) ok(b); else fail(new Error('encode failed')); }, type, quality);
        });
    }

    /** A pdf.js decoded image (ImageBitmap, or raw RGB/RGBA bytes) drawn onto a canvas. */
    function imageToCanvas(img) {
        var w = img.width;
        var h = img.height;
        var canvas = makeCanvas(w, h);
        var g = canvas.getContext('2d');
        if (img.bitmap) {
            g.drawImage(img.bitmap, 0, 0);
        } else if (img.data && img.data.length >= w * h * 3) {
            var rgba = new Uint8ClampedArray(w * h * 4);
            var rgb = img.data.length !== w * h * 4;
            for (var i = 0, j = 0, k = 0; i < w * h; i++) {
                rgba[j++] = img.data[k++]; rgba[j++] = img.data[k++]; rgba[j++] = img.data[k++];
                rgba[j++] = rgb ? 255 : img.data[k++];
            }
            g.putImageData(new ImageData(rgba, w, h), 0, 0);
        } else {
            return null;
        }
        return canvas;
    }

    /** Encode as PNG, or as JPEG when that is much smaller (photographs). */
    async function encodeFigure(canvas) {
        var png = await canvasBlob(canvas, 'image/png');
        if (png.size <= 150 * 1024) return { blob: png, mime: 'image/png', ext: 'png' };
        var flat = makeCanvas(canvas.width, canvas.height); // JPEG has no alpha: flatten onto white
        var g = flat.getContext('2d');
        g.fillStyle = '#ffffff';
        g.fillRect(0, 0, flat.width, flat.height);
        g.drawImage(canvas, 0, 0);
        var jpg = await canvasBlob(flat, 'image/jpeg', 0.88);
        flat.width = 0;
        flat.height = 0;
        if (jpg.size < png.size * 0.6) return { blob: jpg, mime: 'image/jpeg', ext: 'jpg' };
        return { blob: png, mime: 'image/png', ext: 'png' };
    }

    /**
     * Fetch and encode a page's real figures into `model.figures` while the page is open.
     * Skips tiny images, images that cover most of a page that also has text (a scan layer),
     * and repeats of an image already encoded (matched by PDF object). Throws a Decline when
     * the image budget is exceeded.
     * @param state `{entries:[], refs:{}, bytes:0, budget:{images, imageBytes}}`, shared across pages
     */
    async function collectFigures(page, model, state) {
        var area = model.width * model.height;
        model.figures = [];
        for (var k = 0; k < model.images.length; k++) {
            var im = model.images[k];
            if (im.pw < MIN_FIGURE_SIDE || im.ph < MIN_FIGURE_SIDE || im.pw * im.ph > MAX_FIGURE_PIXELS) continue;
            if (model.chars && im.w * im.h > 0.85 * area) continue;
            var obj = null;
            try { obj = page.objs.get(im.id); } catch (e) { obj = null; }
            if (!obj) continue;
            var key = obj.ref ? String(obj.ref) : null;
            var entry = key ? state.refs[key] : null;
            if (!entry) {
                if (state.entries.length >= state.budget.images) throw new Decline('too many images', 'resource_budget_exceeded');
                var canvas = imageToCanvas(obj);
                if (!canvas) continue;
                try {
                    var enc = await encodeFigure(canvas);
                    var bytes = new Uint8Array(await enc.blob.arrayBuffer());
                    state.bytes += bytes.length;
                    if (state.bytes > state.budget.imageBytes) throw new Decline('images exceed the budget', 'resource_budget_exceeded');
                    entry = { file: 'page_' + model.n + '_img_' + (k + 1) + '.' + enc.ext, mime: enc.mime, ext: enc.ext, bytes: bytes };
                    state.entries.push(entry);
                    if (key) state.refs[key] = entry;
                } finally {
                    canvas.width = 0;
                    canvas.height = 0;
                }
            }
            model.figures.push({ kind: 'img', file: entry.file, mime: entry.mime, ext: entry.ext, bytes: entry.bytes,
                page: model.n - 1, x: im.x, y: im.y, w: im.w, h: im.h, pw: im.pw, ph: im.ph });
        }
        return model.figures;
    }

    /**
     * Open a PDF with pdf.js for layout work. Same sandboxing as `ffLocal.openPdfJs` (no script
     * execution, no XFA, every font, CMap and decoder from our own origin) plus
     * `fontExtraProperties`, which makes pdf.js report real font names. Without it an embedded
     * font's bold and italic are invisible, because only the name says so.
     * Encrypted input becomes `Unsupported('encrypted')`, anything unreadable `Unsupported('unsupported_structure')`.
     */
    async function openDocument(file) {
        var pdfjs = await L.loadPdfJs();
        var buf = file.arrayBuffer ? await file.arrayBuffer() : await new Promise(function (ok, fail) {
            var reader = new FileReader();
            reader.onload = function () { ok(reader.result); };
            reader.onerror = function () { fail(reader.error); };
            reader.readAsArrayBuffer(file);
        });
        var task = pdfjs.getDocument({
            data: new Uint8Array(buf),
            isEvalSupported: false,
            enableXfa: false,
            stopAtErrors: false,
            fontExtraProperties: true,
            standardFontDataUrl: L.vendorUrl('pdfjs/standard_fonts/'),
            cMapUrl: L.vendorUrl('pdfjs/cmaps/'),
            cMapPacked: true,
            wasmUrl: L.vendorUrl('pdfjs/wasm/'),
            iccUrl: L.vendorUrl('pdfjs/iccs/'),
        });
        try {
            return await task.promise;
        } catch (err) {
            try { await task.destroy(); } catch (e) { /* already gone */ }
            if (err && (err.name === 'PasswordException' || /password/i.test(String(err.message || '')))) {
                throw new L.Unsupported('PDF is encrypted', 'encrypted');
            }
            throw new L.Unsupported('pdf.js could not open this PDF', 'unsupported_structure');
        }
    }

    /**
     * Load one page and run the pure analysis.
     * @returns page model `{n, ...analysePage, images, anyRaster}`
     */
    async function loadPage(pdfjs, doc, n) {
        var page = await doc.getPage(n);
        try {
            var view = page.view;
            var content = await page.getTextContent();
            var list = await page.getOperatorList();
            var fontIds = {};
            (content.items || []).forEach(function (it) { if (it && it.fontName) fontIds[it.fontName] = 1; });
            var styles = fontStyles(page, Object.keys(fontIds));
            var ann = [];
            try { ann = await page.getAnnotations(); } catch (e) { ann = []; }
            var links = ann.filter(function (a) { return a.subtype === 'Link' && a.url && a.rect; })
                .map(function (a) { return { url: a.url, rect: a.rect }; });
            var model = analysePage({ items: content.items, styles: styles, view: view, links: links });
            var ops = scanOperators(pdfjs, list, view[3] - view[1], [view[0], view[1]]);
            model.n = n;
            model.rotation = ((page.rotate || 0) % 360 + 360) % 360;
            model.images = ops.images;
            model.anyRaster = ops.anyRaster;
            model.rules = ops.rules;
            var found = findTables(model, ops.rules);
            model.tables = found.tables;
            model.flow = found.flow;
            model.flowColumns = found.flowColumns;
            model.pageObjs = page; // callers fetch image bitmaps while the page is open
            return model;
        } catch (err) {
            page.cleanup();
            throw err;
        }
    }

    L.layout = {
        Decline: Decline,
        median: median,
        normaliseText: normaliseText,
        fontStyles: fontStyles,
        atomsFromItems: atomsFromItems,
        applyLinks: applyLinks,
        groupLines: groupLines,
        detectColumns: detectColumns,
        readingOrder: readingOrder,
        bodySize: bodySize,
        rightMargin: rightMargin,
        rightMargins: rightMargins,
        wrapped: wrapped,
        paragraphs: paragraphs,
        analysePage: analysePage,
        removeRunningLines: removeRunningLines,
        outline: outline,
        scanOperators: scanOperators,
        parsePath: parsePath,
        detectRuledTables: detectRuledTables,
        detectBorderlessTables: detectBorderlessTables,
        findTables: findTables,
        loadPage: loadPage,
        openDocument: openDocument,
        collectFigures: collectFigures,
        canvasBlob: canvasBlob,
        makeCanvas: makeCanvas,
    };
})();
