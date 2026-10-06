/* Home page for the Forge Files design system: hero, tool search, filter chips, tool grid, command palette.
   Every tool links to the existing deep-link route (/?tool=<category>&op=<slug>) handled by script.js, so the
   tool pages themselves are unchanged. */
(function () {
    'use strict';

    // [slug (DEEP_LINK_OPS key, '' when the tool has none), app category, filter chip, title, icon, file type, description, keywords, popular]
    var T = [
        ['compress-pdf', 'pdf', 'PDF', 'Compress PDF', 'minimize-2', 'pdf', 'Reduce PDF size while preserving readable quality.', 'make pdf smaller reduce size shrink optimize', 1],
        ['merge-pdf', 'pdf', 'PDF', 'Merge PDF', 'combine', 'pdf', 'Combine several PDFs into one file.', 'combine join documents together', 1],
        ['split-pdf', 'pdf', 'PDF', 'Split PDF', 'scissors', 'pdf', 'Create a ZIP of separate PDFs.', 'separate pages divide'],
        ['unlock-pdf', 'pdf', 'PDF', 'Unlock PDF', 'lock-open', 'pdf', 'Remove a known password from a PDF.', 'password remove decrypt'],
        ['protect-pdf', 'pdf', 'PDF', 'Protect PDF', 'lock', 'pdf', 'Add password protection.', 'password encrypt secure'],
        ['rotate-pdf', 'pdf', 'PDF', 'Rotate PDF', 'rotate-cw', 'pdf', 'Rotate pages 90°, 180° or 270°.', 'turn sideways'],
        ['organize-pdf', 'pdf', 'PDF', 'Organize PDF', 'arrow-up', 'pdf', 'Reorder, delete or duplicate pages.', 'reorder pages'],
        ['extract-pdf-pages', 'pdf', 'PDF', 'Extract PDF Pages', 'scissors', 'pdf', 'Cut selected pages into a new PDF.', 'select pages keep'],
        ['pdf-page-numbers', 'pdf', 'PDF', 'Add Page Numbers', 'file-text', 'pdf', 'Insert numbered footers or headers.', 'number pages'],
        ['sign-pdf', 'pdf', 'PDF', 'Sign PDF', 'pen-line', 'pdf', 'Stamp a signature image onto a page.', 'signature'],
        ['watermark-pdf', 'pdf', 'PDF', 'Watermark PDF', 'file-text', 'pdf', 'Stamp text across every page.', 'stamp text'],
        ['ocr-pdf', 'pdf', 'PDF', 'OCR PDF', 'scan-text', 'pdf', 'Add a searchable text layer.', 'scan searchable text recognition'],
        ['edit-pdf-metadata', 'pdf', 'PDF', 'Edit Metadata', 'info', 'pdf', 'Change title, author and keywords.', 'title author keywords properties'],
        ['annotate-pdf', 'pdf', 'PDF', 'Annotate PDF', 'pen-line', 'pdf', 'Highlight, underline and add notes.', 'highlight underline note redact comment'],
        ['create-pdf', 'pdf', 'PDF', 'Create PDF', 'plus', 'pdf', 'Generate a PDF from text or blank pages.', 'new blank text generate'],
        ['repair-pdf', 'pdf', 'PDF', 'Repair PDF', 'refresh-cw', 'pdf', 'Fix corrupted or damaged PDFs.', 'fix corrupted damaged broken'],
        ['pdf-to-word', 'pdf', 'PDF', 'PDF → Word', 'file-type', 'word', 'Convert a PDF to an editable DOCX.', 'pdf docx doc convert editable', 1],
        ['pdf-to-text', 'pdf', 'PDF', 'PDF → Text', 'file-text', 'pdf', 'Export all text to a .txt file.', 'txt extract text'],
        ['pdf-to-jpg', 'pdf', 'PDF', 'PDF → JPG', 'image', 'image', 'Render each page as an image (zip).', 'images pages png'],
        ['pdf-to-excel', 'pdf', 'PDF', 'PDF → Excel', 'sheet', 'excel', 'Extract tables to a spreadsheet.', 'xlsx spreadsheet tables'],
        ['pdf-to-powerpoint', 'pdf', 'PDF', 'PDF → PowerPoint', 'presentation', 'ppt', 'Convert pages to a presentation.', 'pptx slides'],
        ['pdf-to-epub', 'pdf', 'PDF', 'PDF → EPUB', 'file-text', 'pdf', 'Turn a PDF into a reflowable ebook.', 'ebook reader'],
        ['word-to-pdf', 'word', 'Documents', 'Word → PDF', 'file-text', 'word', 'Convert a document to PDF.', 'docx doc document'],
        ['word-to-powerpoint', 'word', 'Documents', 'Word → PowerPoint', 'presentation', 'word', 'Convert pages to a presentation.', 'docx pptx slides'],
        ['excel-to-pdf', 'excel', 'Spreadsheets', 'Excel → PDF', 'table-2', 'excel', 'Convert a spreadsheet to PDF.', 'xlsx spreadsheet'],
        ['powerpoint-to-pdf', 'ppt', 'Presentations', 'PowerPoint → PDF', 'presentation', 'ppt', 'Convert slides to PDF.', 'pptx slides deck'],
        ['image-to-pdf', 'image', 'Images', 'Image → PDF', 'file-image', 'image', 'Turn images into a single PDF.', 'jpg png photo to pdf', 1],
        ['heic-to-jpeg', 'image', 'Images', 'HEIC → JPG', 'image', 'image', 'Convert iPhone photos to JPG.', 'iphone photo jpg jpeg apple', 1],
        ['resize-image', 'image', 'Images', 'Resize Image', 'scaling', 'image', 'Change image dimensions in pixels or percent.', 'scale dimensions smaller bigger photo', 1],
        ['crop-image', 'image', 'Images', 'Crop Image', 'scissors', 'image', 'Apply a cropping selection.', 'cut trim'],
        ['compress-image', 'image', 'Images', 'Compress Image', 'minimize-2', 'image', 'Reduce file size without changing format.', 'smaller photo'],
        ['convert-image', 'image', 'Images', 'Convert Image', 'image', 'image', 'JPG ↔ PNG ↔ WebP.', 'png webp format'],
        ['rotate-image', 'image', 'Images', 'Rotate Image', 'rotate-cw', 'image', 'Rotate by 90°, 180° or 270°.', 'turn'],
        ['watermark-image', 'image', 'Images', 'Watermark Image', 'image', 'image', 'Stamp text on the image.', 'stamp text'],
        ['csv-to-xlsx', 'excel', 'Spreadsheets', 'CSV → Excel', 'sheet', 'excel', 'Turn a CSV into a workbook.', 'csv workbook'],
        ['xlsx-to-csv', 'excel', 'Spreadsheets', 'Excel → CSV', 'sheet', 'excel', 'Export a sheet to CSV.', 'csv export'],
        ['merge-excel', 'excel', 'Spreadsheets', 'Merge Excel', 'combine', 'excel', 'Combine multiple .xlsx files.', 'workbooks combine'],
        ['ppt-to-images', 'ppt', 'Presentations', 'PPT → Images', 'image', 'ppt', 'Each slide as PNG or JPG (zip).', 'slides png jpg'],
        ['merge-ppt', 'ppt', 'Presentations', 'Merge PowerPoint', 'combine', 'ppt', 'Combine multiple presentations.', 'pptx combine'],
        ['', 'workflow', 'Workflows', 'Workflow builder', 'workflow', 'zip', 'Chain several operations on one file and run them in order.', 'chain steps automate pipeline multiple operations']
    ].map(function (r) {
        return { slug: r[0], cat: r[1], chip: r[2], title: r[3], icon: r[4], ft: r[5], desc: r[6], kw: r[7], popular: !!r[8] };
    });
    var CHIPS = ['All', 'PDF', 'Images', 'Documents', 'Spreadsheets', 'Presentations', 'Workflows'];
    var VERBS = ['Compress', 'Merge', 'Convert', 'Resize', 'Split'];

    function esc(s) {
        return String(s).replace(/[&<>"']/g, function (c) {
            return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
        });
    }
    function href(t) {
        return '/?tool=' + t.cat + (t.slug ? '&op=' + t.slug : '');
    }
    function rank(q) {
        q = q.trim().toLowerCase();
        if (!q) return [];
        var w = q.split(/\s+/);
        return T.map(function (t) {
            var h = (t.title + ' ' + t.kw + ' ' + t.chip).toLowerCase().replace(/→/g, 'to');
            var s = 0;
            if (t.title.toLowerCase().indexOf(q) !== -1) s += 10;
            w.forEach(function (x) { if (h.indexOf(x) !== -1) s += 2; });
            if (t.popular) s += 1;
            return { t: t, s: s };
        }).filter(function (x) { return x.s > (w.length > 1 ? 2 : 1); })
            .sort(function (a, b) { return b.s - a.s; })
            .slice(0, 6).map(function (x) { return x.t; });
    }
    function go(t) { window.location.href = href(t); }

    // ---- Tool cards -------------------------------------------------------
    function card(t, i) {
        return '<div class="ffh-tilt" style="animation-delay:' + (0.05 * (i % 12) + 0.1).toFixed(2) + 's">' +
            '<a class="ff-tool" href="' + href(t) + '">' +
            '<span class="ic" style="--ft:var(--file-' + t.ft + ');--d:' + ((t.title.length % 7) * -0.55) + 's">' + window.FFIcon(t.icon, 20) + '</span>' +
            '<span><h4>' + esc(t.title) + '</h4><p>' + esc(t.desc) + '</p></span>' +
            (window.ffLocalBadge && window.ffLocalBadge.slug(t.slug || t.cat) ? window.ffLocalBadge.html : '') + '</a></div>';
    }
    function fill(el, list) { el.innerHTML = list.map(card).join(''); }

    function bindTilt(root) {
        root.addEventListener('mousemove', function (e) {
            var c = e.target.closest && e.target.closest('.ffh-tilt');
            if (!c) return;
            var r = c.getBoundingClientRect(), x = (e.clientX - r.left) / r.width, y = (e.clientY - r.top) / r.height, s = c.style;
            s.setProperty('--ry', ((x - 0.5) * 12) + 'deg');
            s.setProperty('--rx', ((0.5 - y) * 10) + 'deg');
            s.setProperty('--gx', x * 100 + '%');
            s.setProperty('--gy', y * 100 + '%');
        });
        root.addEventListener('mouseout', function (e) {
            var c = e.target.closest && e.target.closest('.ffh-tilt');
            if (!c || c.contains(e.relatedTarget)) return;
            c.style.setProperty('--rx', '0deg');
            c.style.setProperty('--ry', '0deg');
        });
    }

    // ---- File handoff (same IndexedDB contract as seo-upload.js) -----------
    function stash(files) {
        return new Promise(function (resolve, reject) {
            if (!window.indexedDB) return reject(new Error('no indexedDB'));
            var req = indexedDB.open('ff_handoff', 1);
            req.onupgradeneeded = function () {
                if (!req.result.objectStoreNames.contains('files')) req.result.createObjectStore('files');
            };
            req.onerror = function () { reject(req.error); };
            req.onsuccess = function () {
                var db = req.result, tx = db.transaction('files', 'readwrite');
                tx.objectStore('files').put({
                    files: files.map(function (f) { return { blob: f, name: f.name, type: f.type }; }),
                    blob: files[0], name: files[0].name, type: files[0].type, at: Date.now()
                }, 'pending');
                tx.oncomplete = function () { db.close(); resolve(); };
                tx.onerror = function () { db.close(); reject(tx.error); };
            };
        });
    }
    // A dropped file goes to the page for its type with every tool for that type
    // available, and no tool pre-selected: the visitor picks what to do with it.
    function categoryForFile(f) {
        var ext = (f.name.split('.').pop() || '').toLowerCase();
        return {
            pdf: 'pdf',
            heic: 'image', heif: 'image', jpg: 'image', jpeg: 'image', png: 'image', webp: 'image', gif: 'image', bmp: 'image',
            xlsx: 'excel', xls: 'excel', csv: 'excel', pptx: 'ppt', docx: 'word', doc: 'word'
        }[ext] || null;
    }
    function acceptFiles(fileList) {
        var files = Array.prototype.slice.call(fileList || []).filter(Boolean);
        if (!files.length) return;
        var cat = categoryForFile(files[0]);
        if (!cat) {
            if (typeof ffNotify === 'function') ffNotify('That file type is not supported. Search for a tool instead.');
            return;
        }
        var url = '/?tool=' + cat;
        stash(files).then(function () { window.location.href = url + '&handoff=1'; }, function () { window.location.href = url; });
    }

    // ---- Tool search ------------------------------------------------------
    function initSearch(host) {
        host.innerHTML =
            '<div class="ff-ts" id="ffh-ts"><div class="in">' + window.FFIcon('search', 20) +
            '<input id="ffh-q" aria-label="Search tools or drop a file" placeholder="Search tools or drop a file" autocomplete="off" role="combobox" aria-expanded="false" aria-controls="ffh-results">' +
            '<input id="ffh-file" type="file" hidden multiple>' +
            '<button type="button" class="ff-btn secondary sm" id="ffh-choose">' + window.FFIcon('upload', 16) + 'Choose file</button></div>' +
            '<ul role="listbox" id="ffh-results" hidden></ul></div>';
        var ts = host.querySelector('#ffh-ts'), q = host.querySelector('#ffh-q'), ul = host.querySelector('#ffh-results'), file = host.querySelector('#ffh-file');
        var res = [], idx = 0;
        function draw() {
            var v = q.value;
            res = rank(v);
            idx = 0;
            if (!v.trim()) { ul.hidden = true; ul.innerHTML = ''; q.setAttribute('aria-expanded', 'false'); return; }
            ul.hidden = false;
            q.setAttribute('aria-expanded', 'true');
            ul.innerHTML = res.length ? res.map(function (r, n) {
                return '<li role="option" data-i="' + n + '" aria-selected="' + (n === 0) + '"' + (n === 0 ? ' class="on"' : '') + '>' + window.FFIcon(r.icon, 18) + '<span>' + esc(r.title) + '</span><small>' + esc(r.desc) + '</small></li>';
            }).join('') : '<li class="none">No tools found for “' + esc(v) + '”</li>';
        }
        function mark() {
            Array.prototype.forEach.call(ul.querySelectorAll('li[data-i]'), function (li, n) {
                li.classList.toggle('on', n === idx);
                li.setAttribute('aria-selected', n === idx);
            });
        }
        q.addEventListener('input', draw);
        q.addEventListener('keydown', function (e) {
            if (!res.length) return;
            if (e.key === 'ArrowDown') { e.preventDefault(); idx = Math.min(idx + 1, res.length - 1); mark(); }
            else if (e.key === 'ArrowUp') { e.preventDefault(); idx = Math.max(idx - 1, 0); mark(); }
            else if (e.key === 'Enter') { e.preventDefault(); go(res[idx]); }
        });
        ul.addEventListener('mousemove', function (e) {
            var li = e.target.closest('li[data-i]');
            if (li) { idx = +li.getAttribute('data-i'); mark(); }
        });
        ul.addEventListener('click', function (e) {
            var li = e.target.closest('li[data-i]');
            if (li) go(res[+li.getAttribute('data-i')]);
        });
        host.querySelector('#ffh-choose').addEventListener('click', function () { file.click(); });
        file.addEventListener('change', function () { acceptFiles(file.files); });
        ts.addEventListener('dragover', function (e) { e.preventDefault(); ts.classList.add('over'); });
        ts.addEventListener('dragleave', function () { ts.classList.remove('over'); });
        ts.addEventListener('drop', function (e) { e.preventDefault(); ts.classList.remove('over'); acceptFiles(e.dataTransfer && e.dataTransfer.files); });
    }

    // ---- Command palette (Cmd/Ctrl+K) --------------------------------------
    function initPalette() {
        var bg = document.createElement('div');
        bg.className = 'ff-ds ff-modal-bg ffh-pal-bg';
        bg.hidden = true;
        bg.innerHTML = '<div class="ff-pal" role="dialog" aria-modal="true" aria-label="Search tools"><div class="q">' + window.FFIcon('search', 18) +
            '<input type="text" aria-label="Search tools" placeholder="Search tools" autocomplete="off"></div><div class="grp">Tools</div><ul role="listbox"></ul></div>';
        document.body.appendChild(bg);
        var input = bg.querySelector('input'), ul = bg.querySelector('ul'), res = [], idx = 0;
        function draw() {
            res = input.value.trim() ? rank(input.value) : T.filter(function (t) { return t.popular; });
            idx = 0;
            ul.innerHTML = res.length ? res.map(function (r, n) {
                return '<li role="option" data-i="' + n + '"' + (n === 0 ? ' class="on"' : '') + '>' + window.FFIcon(r.icon, 18) + '<span>' + esc(r.title) + '</span><small>' + esc(r.chip) + '</small></li>';
            }).join('') : '<li class="none" style="cursor:default">No tools found</li>';
        }
        function mark() {
            Array.prototype.forEach.call(ul.querySelectorAll('li[data-i]'), function (li, n) { li.classList.toggle('on', n === idx); });
        }
        var opener = null;
        function open() { opener = document.activeElement; bg.hidden = false; input.value = ''; draw(); input.focus(); }
        function close() {
            bg.hidden = true;
            if (opener && typeof opener.focus === 'function' && document.contains(opener)) opener.focus();
            opener = null;
        }
        input.addEventListener('input', draw);
        input.addEventListener('keydown', function (e) {
            if (e.key === 'ArrowDown') { e.preventDefault(); idx = Math.min(idx + 1, res.length - 1); mark(); }
            else if (e.key === 'ArrowUp') { e.preventDefault(); idx = Math.max(idx - 1, 0); mark(); }
            else if (e.key === 'Enter' && res[idx]) { e.preventDefault(); go(res[idx]); }
        });
        ul.addEventListener('click', function (e) {
            var li = e.target.closest('li[data-i]');
            if (li) go(res[+li.getAttribute('data-i')]);
        });
        bg.addEventListener('click', function (e) { if (e.target === bg) close(); });
        document.addEventListener('keydown', function (e) {
            if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') { e.preventDefault(); open(); }
            else if (e.key === 'Escape' && !bg.hidden) close();
        });
        var btn = document.getElementById('ffh-open-pal');
        if (btn) btn.addEventListener('click', open);
    }

    // ---- Page -------------------------------------------------------------
    function init() {
        var home = document.getElementById('home-page');
        if (!home || !window.FFIcon) return;

        var rot = document.getElementById('ffh-rot'), vi = 0;
        if (rot && !(window.matchMedia && matchMedia('(prefers-reduced-motion: reduce)').matches)) {
            setInterval(function () {
                vi = (vi + 1) % VERBS.length;
                rot.innerHTML = '<b>' + VERBS[vi] + '</b>';
            }, 2200);
        }
        var trust = document.getElementById('ffh-trust');
        if (trust) {
            trust.innerHTML = [['badge-check', 'Free', 'ok', 0], ['user-x', 'No signup', 'ac', 1], ['github', 'Open source', 'vi', 2], ['shield-check', 'Private by design', 'te', 3]].map(function (x) {
                var inner = '<i class="ti ' + x[2] + '" style="--d:' + (x[3] * -1.1) + 's">' + window.FFIcon(x[0], 14) + '</i>' + x[1];
                // Open source links to the public repo; the pulsing dot marks it as live.
                if (x[1] === 'Open source') {
                    return '<span><a class="ffh-oss" href="https://github.com/BhurkeSiddhesh/File-Forge" target="_blank" rel="noopener" aria-label="Open source: view Forge Files on GitHub">' +
                        inner + '<span class="dot" aria-hidden="true"></span></a></span>';
                }
                return '<span>' + inner + '</span>';
            }).join('');
        }
        initSearch(document.getElementById('ffh-search'));

        var pop = document.getElementById('ffh-popular'), all = document.getElementById('ffh-all'), chips = document.getElementById('ffh-chips');
        fill(pop, T.filter(function (t) { return t.popular; }));
        function showCat(c) {
            fill(all, T.filter(function (t) { return c === 'All' || t.chip === c; }));
            Array.prototype.forEach.call(chips.querySelectorAll('button'), function (b) {
                var on = b.getAttribute('data-c') === c;
                b.classList.toggle('on', on);
                b.setAttribute('aria-pressed', on);
            });
        }
        chips.innerHTML = CHIPS.map(function (c) { return '<button type="button" data-c="' + c + '">' + c + '</button>'; }).join('');
        chips.addEventListener('click', function (e) {
            var b = e.target.closest('button[data-c]');
            if (b) showCat(b.getAttribute('data-c'));
        });
        showCat('All');
        bindTilt(pop);
        bindTilt(all);
        initPalette();
    }
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init); else init();
})();
