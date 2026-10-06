// Visual page picker shared by the page-based PDF tools (extract, rotate,
// organize, remove pages). It renders a thumbnail per page with the vendored
// pdf.js and keeps the tool's existing text input in sync, so the form submit
// code and the server contract ("1,3-5", "3,1,2") are unchanged and the input
// stays usable when thumbnails cannot be drawn (encrypted or damaged PDFs).
//
//   select mode: click a thumbnail to toggle it; the input holds the ranges.
//   order mode:  drag thumbnails (or use the arrow buttons) to reorder, the
//                x button drops a page; the input holds the page order.
//
// Everything runs in the browser; the PDF is never uploaded for previews.
(function () {
    'use strict';

    var MAX_THUMBS = 200;
    var THUMB_WIDTH = 110;

    var TOOLS = {
        'extract-pages-area': { input: 'extract-pages-input', mode: 'select' },
        'rotate-pdf-area': { input: 'rotate-pdf-pages', mode: 'select', blankMeansAll: true },
        'remove-pages-area': { input: 'remove-pages-input', mode: 'select' },
        'crop-pdf-area': { input: 'crop-pdf-pages', mode: 'select', blankMeansAll: true },
        'organize-pdf-area': { input: 'organize-page-order', mode: 'order' },
    };

    /** [1,2,3,5,7,8] -> "1-3,5,7-8". */
    function compressRanges(pages) {
        var sorted = pages.slice().sort(function (a, b) { return a - b; });
        var out = [];
        var i = 0;
        while (i < sorted.length) {
            var j = i;
            while (j + 1 < sorted.length && sorted[j + 1] === sorted[j] + 1) j++;
            out.push(j > i ? sorted[i] + '-' + sorted[j] : String(sorted[i]));
            i = j + 1;
        }
        return out.join(',');
    }

    /** "1,3-5" -> [1,3,4,5]; null when the text is not a plain page list. */
    function parseRanges(text, total) {
        var pages = [];
        var parts = String(text || '').split(',');
        for (var i = 0; i < parts.length; i++) {
            var part = parts[i].trim();
            if (!part) continue;
            var m = /^(\d+)(?:\s*-\s*(\d+))?$/.exec(part);
            if (!m) return null;
            var a = parseInt(m[1], 10);
            var b = m[2] ? parseInt(m[2], 10) : a;
            if (a < 1 || b < a || b > total) return null;
            for (var p = a; p <= b; p++) if (pages.indexOf(p) < 0) pages.push(p);
        }
        return pages;
    }

    /** "3,1,2" -> [3,1,2]; null when invalid. */
    function parseOrder(text, total) {
        var parts = String(text || '').split(',');
        var out = [];
        for (var i = 0; i < parts.length; i++) {
            var part = parts[i].trim();
            if (!part) continue;
            if (!/^\d+$/.test(part)) return null;
            var n = parseInt(part, 10);
            if (n < 1 || n > total) return null;
            out.push(n);
        }
        return out;
    }

    var mounts = {};

    function el(tag, className, text) {
        var node = document.createElement(tag);
        if (className) node.className = className;
        if (text !== undefined) node.textContent = text;
        return node;
    }

    async function renderThumbnails(file, token, state, onThumb) {
        var L = window.ffLocal;
        if (!L || !L.openPdfJs) throw new Error('Page previews are unavailable.');
        var doc = await L.openPdfJs(file);
        try {
            // The real page count drives selection and order state; only the number of
            // thumbnails drawn is capped, so pages past the cap are never dropped.
            state.total = doc.numPages;
            state.limit = Math.min(doc.numPages, MAX_THUMBS);
            state.truncated = doc.numPages > MAX_THUMBS;
            state.onTotal(state.total);
            for (var i = 1; i <= state.limit; i++) {
                if (state.token !== token) return;
                var page = await doc.getPage(i);
                var base = page.getViewport({ scale: 1 });
                var viewport = page.getViewport({ scale: THUMB_WIDTH / base.width });
                var canvas = document.createElement('canvas');
                canvas.width = Math.ceil(viewport.width);
                canvas.height = Math.ceil(viewport.height);
                var ctx = canvas.getContext('2d', { alpha: false });
                ctx.fillStyle = '#fff';
                ctx.fillRect(0, 0, canvas.width, canvas.height);
                await page.render({ canvasContext: ctx, viewport: viewport, intent: 'print' }).promise;
                page.cleanup();
                onThumb(i, canvas.toDataURL('image/jpeg', 0.7), canvas.width, canvas.height);
                if (L.tick) await L.tick();
            }
        } finally {
            try { await doc.destroy(); } catch (e) { /* already released */ }
        }
    }

    /** Page 1 with the four crop margins shaded, updated as the inputs change. */
    var cropMounts = {};
    async function mountCropPreview(file) {
        var box = document.getElementById('crop-pdf-preview');
        if (!box || !file || (cropMounts.file === file)) return;
        cropMounts.file = file;
        box.textContent = '';
        var L = window.ffLocal;
        try {
            var doc = await L.openPdfJs(file);
            var page = await doc.getPage(1);
            var base = page.getViewport({ scale: 1 });
            var viewport = page.getViewport({ scale: 220 / base.width });
            var canvas = document.createElement('canvas');
            canvas.width = Math.ceil(viewport.width);
            canvas.height = Math.ceil(viewport.height);
            var ctx = canvas.getContext('2d', { alpha: false });
            ctx.fillStyle = '#fff';
            ctx.fillRect(0, 0, canvas.width, canvas.height);
            await page.render({ canvasContext: ctx, viewport: viewport, intent: 'print' }).promise;
            try { await doc.destroy(); } catch (e) { /* already released */ }
            if (cropMounts.file !== file) return;
            var stage = el('div', 'ff-crop-stage');
            stage.style.width = canvas.width + 'px';
            stage.style.height = canvas.height + 'px';
            stage.appendChild(canvas);
            var shades = {};
            ['top', 'bottom', 'left', 'right'].forEach(function (edge) {
                shades[edge] = el('div', 'ff-crop-shade ff-crop-shade-' + edge);
                stage.appendChild(shades[edge]);
            });
            box.appendChild(stage);
            var update = function () {
                ['top', 'bottom', 'left', 'right'].forEach(function (edge) {
                    var v = parseFloat(document.getElementById('crop-pdf-' + edge).value);
                    v = isFinite(v) ? Math.min(Math.max(v, 0), 90) : 0;
                    shades[edge].style[edge === 'top' || edge === 'bottom' ? 'height' : 'width'] = v + '%';
                });
            };
            ['top', 'bottom', 'left', 'right'].forEach(function (edge) {
                document.getElementById('crop-pdf-' + edge).addEventListener('input', update);
            });
            update();
        } catch (e) {
            box.textContent = '';
        }
    }

    function mount(areaId, file) {
        if (areaId === 'crop-pdf-area') mountCropPreview(file);
        var cfg = TOOLS[areaId];
        var area = document.getElementById(areaId);
        var input = cfg && document.getElementById(cfg.input);
        if (!cfg || !area || !input || !file) return;

        var existing = mounts[areaId];
        if (existing && existing.file === file) return;
        if (existing) { existing.state.token = null; existing.root.remove(); }

        var root = el('div', 'ff-page-grid');
        root.setAttribute('role', 'group');
        root.setAttribute('aria-label', cfg.mode === 'order' ? 'Page order' : 'Choose pages');
        var status = el('p', 'ff-page-grid-status helper-text', 'Loading page previews…');
        status.setAttribute('role', 'status');
        var grid = el('div', 'ff-page-grid-items');
        var bar = el('div', 'ff-page-grid-bar');
        root.appendChild(status);
        root.appendChild(bar);
        root.appendChild(grid);
        input.parentNode.insertBefore(root, input);

        var thumbs = {};          // page number -> {src, w, h}
        var selected = [];        // select mode
        var order = [];           // order mode
        var token = {};
        var state = { token: token, total: 0, limit: 0, truncated: false, onTotal: function () {} };
        mounts[areaId] = { file: file, root: root, state: state };

        function writeInput() {
            if (cfg.mode === 'order') input.value = order.join(',');
            else if (cfg.blankMeansAll && selected.length === state.total) input.value = '';
            else input.value = compressRanges(selected);
            input.dispatchEvent(new Event('input', { bubbles: true }));
        }

        function pageItem(p, position) {
            var item = el('div', 'ff-page-grid-item');
            var t = thumbs[p];
            var btn = el('button', 'ff-page-grid-thumb');
            btn.type = 'button';
            if (t) {
                var img = el('img');
                img.src = t.src;
                img.alt = '';
                img.width = t.w;
                img.height = t.h;
                btn.appendChild(img);
            } else {
                btn.appendChild(el('span', 'ff-page-grid-blank', '…'));
            }
            btn.appendChild(el('span', 'ff-page-grid-num', String(p)));
            if (cfg.mode === 'select') {
                var on = selected.indexOf(p) >= 0;
                item.classList.toggle('is-selected', on);
                btn.setAttribute('aria-pressed', on ? 'true' : 'false');
                btn.setAttribute('aria-label', 'Page ' + p);
                btn.addEventListener('click', function () {
                    var at = selected.indexOf(p);
                    // These tools treat an empty field as "all pages", so the last selected
                    // page cannot be unselected (the grid would say none while all change).
                    if (at >= 0 && cfg.blankMeansAll && selected.length === 1) return;
                    if (at >= 0) selected.splice(at, 1); else selected.push(p);
                    writeInput();
                    draw();
                });
            } else {
                btn.setAttribute('aria-label', 'Page ' + p + ', position ' + (position + 1));
                item.draggable = true;
                item.addEventListener('dragstart', function (e) {
                    e.dataTransfer.setData('text/plain', String(position));
                    e.dataTransfer.effectAllowed = 'move';
                });
                item.addEventListener('dragover', function (e) { e.preventDefault(); });
                item.addEventListener('drop', function (e) {
                    e.preventDefault();
                    var from = parseInt(e.dataTransfer.getData('text/plain'), 10);
                    if (isNaN(from) || from === position) return;
                    order.splice(position, 0, order.splice(from, 1)[0]);
                    writeInput();
                    draw();
                });
            }
            item.appendChild(btn);
            if (cfg.mode === 'order') {
                var tools = el('div', 'ff-page-grid-tools');
                [['←', 'Move page ' + p + ' earlier', -1], ['→', 'Move page ' + p + ' later', 1]].forEach(function (d) {
                    var b = el('button', 'ff-page-grid-tool', d[0]);
                    b.type = 'button';
                    b.setAttribute('aria-label', d[1]);
                    b.disabled = (position + d[2] < 0) || (position + d[2] >= order.length);
                    b.addEventListener('click', function () {
                        order.splice(position + d[2], 0, order.splice(position, 1)[0]);
                        writeInput();
                        draw();
                    });
                    tools.appendChild(b);
                });
                var del = el('button', 'ff-page-grid-tool', '×');
                del.type = 'button';
                del.setAttribute('aria-label', 'Remove page ' + p + ' from the output');
                del.addEventListener('click', function () {
                    order.splice(position, 1);
                    writeInput();
                    draw();
                });
                tools.appendChild(del);
                item.appendChild(tools);
            }
            return item;
        }

        function draw() {
            grid.textContent = '';
            var list = cfg.mode === 'order' ? order : Array.from({ length: state.total }, function (_, i) { return i + 1; });
            list.forEach(function (p, pos) { grid.appendChild(pageItem(p, pos)); });
        }

        function addBarButton(label, fn) {
            var b = el('button', 'ff-page-grid-bar-btn', label);
            b.type = 'button';
            b.addEventListener('click', fn);
            bar.appendChild(b);
        }
        if (cfg.mode === 'select') {
            addBarButton('Select all', function () {
                selected = Array.from({ length: state.total }, function (_, i) { return i + 1; });
                writeInput(); draw();
            });
            if (!cfg.blankMeansAll) addBarButton('Clear', function () { selected = []; writeInput(); draw(); });
        } else {
            addBarButton('Reset order', function () {
                order = Array.from({ length: state.total }, function (_, i) { return i + 1; });
                writeInput(); draw();
            });
        }

        // Typing in the text box redraws the grid so both always agree.
        input.addEventListener('input', function onInput(e) {
            if (mounts[areaId] === undefined || mounts[areaId].root !== root) {
                input.removeEventListener('input', onInput);
                return;
            }
            if (e.isTrusted === false) return;
            if (cfg.mode === 'order') {
                var o = parseOrder(input.value, state.total);
                if (o) { order = o; draw(); }
            } else {
                var s = (/^\s*all\s*$/i.test(input.value) || (cfg.blankMeansAll && !input.value.trim()))
                    ? Array.from({ length: state.total }, function (_, i) { return i + 1; })
                    : parseRanges(input.value, state.total);
                if (s) { selected = s; draw(); }
            }
        });

        state.onTotal = function (total) {
            if (cfg.mode === 'order') {
                var typed = parseOrder(input.value, total);
                order = typed && typed.length ? typed : Array.from({ length: total }, function (_, i) { return i + 1; });
                if (!typed || !typed.length) writeInput();
            } else {
                var typedPages = input.value.trim() ? parseRanges(input.value, total) : null;
                selected = typedPages || (cfg.blankMeansAll
                    ? Array.from({ length: total }, function (_, i) { return i + 1; }) : []);
            }
            draw();
        };

        renderThumbnails(file, token, state, function (p, src, w, h) {
            thumbs[p] = { src: src, w: w, h: h };
            status.textContent = 'Page previews: ' + p + ' of ' + state.limit + (state.truncated ? ' (first ' + MAX_THUMBS + ' of ' + state.total + ' pages shown; the rest are kept)' : '');
            draw();
            if (p === state.limit) status.textContent = cfg.mode === 'order'
                ? 'Drag pages, or use the arrows, to reorder. × removes a page.'
                : 'Click pages to select them.';
        }).catch(function () {
            if (state.token !== token) return;
            status.textContent = 'Page previews are unavailable for this file; type the pages instead.';
            grid.textContent = '';
            bar.textContent = '';
        });
    }

    function unmountAll() {
        Object.keys(mounts).forEach(function (id) {
            mounts[id].state.token = null;
            mounts[id].root.remove();
            delete mounts[id];
        });
    }

    window.ffPageGrid = {
        supports: function (areaId) { return !!TOOLS[areaId]; },
        mount: mount,
        unmountAll: unmountAll,
        compressRanges: compressRanges,
        parseRanges: parseRanges,
        parseOrder: parseOrder,
    };
})();
