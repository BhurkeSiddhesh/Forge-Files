// "Local" tag for tools that run on-device.
//
// The source of truth is the dispatcher's registry (ffLocal.handlers), so a tool
// is tagged exactly when a handler for its endpoint exists and local processing
// is enabled. This file only maps tools to endpoints; it never decides on its own
// that something is local. Must load after the ops-*.js files and before
// ds/home.js and script.js.
(function () {
    'use strict';

    // Action-card id (tool pages) -> API path it posts to.
    var CARD = {
        'convert-word-btn': '/api/pdf/convert-to-word',
        'extract-pages-btn': '/api/pdf/extract-pages',
        'split-pdf-btn': '/api/pdf/split',
        'compress-pdf-btn': '/api/pdf/compress',
        'merge-pdf-btn': '/api/pdf/merge',
        'watermark-pdf-btn': '/api/pdf/watermark',
        'to-images-pdf-btn': '/api/pdf/to-images',
        'sign-pdf-btn': '/api/pdf/sign',
        'rotate-pdf-btn': '/api/pdf/rotate',
        'extract-text-btn': '/api/pdf/extract-text',
        'ocr-pdf-btn': '/api/pdf/ocr',
        'crop-pdf-btn': '/api/pdf/crop',
        'remove-pages-btn': '/api/pdf/organize',
        'organize-pdf-btn': '/api/pdf/organize',
        'page-numbers-btn': '/api/pdf/add-page-numbers',
        'create-pdf-btn': '/api/pdf/create-from-text',
        'annotate-pdf-btn': '/api/pdf/annotate',
        'pdf-metadata-btn': '/api/pdf/metadata',
        'pdf-to-excel-btn': '/api/pdf/to-excel',
        'pdf-to-pptx-btn': '/api/pdf/to-pptx',
        'pdf-to-epub-btn': '/api/pdf/to-epub',
        'convert-jpeg-btn': '/api/image/heic-to-jpeg',
        'resize-btn': '/api/image/resize',
        'crop-btn': '/api/image/crop',
        'rotate-image-btn': '/api/image/rotate',
        'compress-image-btn': '/api/image/compress',
        'convert-format-btn': '/api/image/convert',
        'watermark-image-btn': '/api/image/watermark',
        'image-to-pdf-btn': '/api/image/to-pdf',
        'csv-to-xlsx-btn': '/api/excel/csv-to-xlsx',
        'xlsx-to-csv-btn': '/api/excel/xlsx-to-csv',
        'merge-excel-btn': '/api/excel/merge'
    };

    // Home-page slug (see ds/home.js) -> action-card id. The workflow builder has
    // no slug and is looked up as 'workflow'.
    var SLUG = {
        'compress-pdf': 'compress-pdf-btn', 'merge-pdf': 'merge-pdf-btn', 'split-pdf': 'split-pdf-btn',
        'rotate-pdf': 'rotate-pdf-btn', 'organize-pdf': 'organize-pdf-btn', 'extract-pdf-pages': 'extract-pages-btn',
        'pdf-page-numbers': 'page-numbers-btn', 'sign-pdf': 'sign-pdf-btn', 'watermark-pdf': 'watermark-pdf-btn',
        'ocr-pdf': 'ocr-pdf-btn', 'edit-pdf-metadata': 'pdf-metadata-btn', 'annotate-pdf': 'annotate-pdf-btn',
        'create-pdf': 'create-pdf-btn', 'pdf-to-word': 'convert-word-btn', 'pdf-to-text': 'extract-text-btn',
        'pdf-to-jpg': 'to-images-pdf-btn', 'pdf-to-excel': 'pdf-to-excel-btn', 'pdf-to-powerpoint': 'pdf-to-pptx-btn',
        'pdf-to-epub': 'pdf-to-epub-btn', 'image-to-pdf': 'image-to-pdf-btn', 'heic-to-jpeg': 'convert-jpeg-btn',
        'resize-image': 'resize-btn', 'crop-image': 'crop-btn', 'compress-image': 'compress-image-btn',
        'convert-image': 'convert-format-btn', 'rotate-image': 'rotate-image-btn', 'watermark-image': 'watermark-image-btn',
        'csv-to-xlsx': 'csv-to-xlsx-btn', 'xlsx-to-csv': 'xlsx-to-csv-btn', 'merge-excel': 'merge-excel-btn'
    };

    function registered(path) {
        var L = window.ffLocal;
        return !!(L && L.handlers && L.handlers[path] && (!L.enabled || L.enabled()));
    }

    var api = {
        card: function (id) { return !!CARD[id] && registered(CARD[id]); },
        // `key` is a home-page slug, or 'workflow' for the workflow builder.
        slug: function (key) {
            if (key === 'workflow') return registered('/api/workflow/execute');
            return !!SLUG[key] && api.card(SLUG[key]);
        },
        html: '<span class="ff-local-tag" title="Runs in your browser. Your file is not uploaded.">Local</span>',
        // Tags the action cards already in the page (tool pages).
        decorate: function () {
            Array.prototype.forEach.call(document.querySelectorAll('.action-card[id]'), function (el) {
                if (el.querySelector('.ff-local-tag') || !api.card(el.id)) return;
                el.insertAdjacentHTML('beforeend', api.html);
            });
        }
    };
    window.ffLocalBadge = api;

    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', api.decorate);
    else api.decorate();
})();
