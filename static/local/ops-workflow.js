// On-device and hybrid workflow execution (migration work package 40).
//
// Chains consecutive supported operations locally on-device. Intermediates stay in
// memory between steps and are never uploaded. Before any step that requires
// server conversion, the consent dialog is shown naming the step and the
// intermediate file to be uploaded. On decline, the whole workflow stops and zero
// bytes are sent to the server.
(function () {
    'use strict';

    var L = window.ffLocal;
    if (!L) return;

    // Mirrors main.py's MAX_WORKFLOW_STEPS default (env-configurable server side).
    // A deployment that raises it can set window.FF_MAX_WORKFLOW_STEPS; the server
    // still validates the steps it receives.
    var DEFAULT_MAX_WORKFLOW_STEPS = 20;
    function maxSteps() {
        var n = Number(window.FF_MAX_WORKFLOW_STEPS);
        return isFinite(n) && n >= 1 ? Math.floor(n) : DEFAULT_MAX_WORKFLOW_STEPS;
    }

    // ── Step typing (preflight) ───────────────────────────────────────────
    //
    // File classes a step accepts and produces. `null` output = unknown (the
    // chain stops being type-checked from there rather than guessing).
    var EXT_CLASS = {
        pdf: 'pdf', csv: 'csv', xlsx: 'xlsx', xls: 'xls', docx: 'docx', doc: 'doc',
        pptx: 'pptx', ppt: 'ppt', txt: 'txt', epub: 'epub', heic: 'heic', heif: 'heic',
        jpg: 'image', jpeg: 'image', png: 'image', webp: 'image', gif: 'image',
        bmp: 'image', tif: 'image', tiff: 'image',
    };
    var IMG_IN = ['image', 'heic'];
    var STEP_TYPES = {
        rotate_pdf: { in: ['pdf'], out: 'pdf' },
        organize_pdf: { in: ['pdf'], out: 'pdf' },
        add_page_numbers: { in: ['pdf'], out: 'pdf' },
        protect_pdf: { in: ['pdf'], out: 'pdf' },
        remove_password: { in: ['pdf'], out: 'pdf' },
        annotate_pdf: { in: ['pdf'], out: 'pdf' },
        edit_metadata: { in: ['pdf'], out: 'pdf' },
        compress_pdf: { in: ['pdf'], out: 'pdf' },
        repair_pdf: { in: ['pdf'], out: 'pdf' },
        extract_text: { in: ['pdf'], out: 'txt' },
        pdf_to_word: { in: ['pdf'], out: 'docx' },
        pdf_to_excel: { in: ['pdf'], out: 'xlsx' },
        pdf_to_pptx: { in: ['pdf'], out: 'pptx' },
        pdf_to_epub: { in: ['pdf'], out: 'epub' },
        resize_image: { in: IMG_IN, out: 'image' },
        crop_image: { in: IMG_IN, out: 'image' },
        rotate_image: { in: IMG_IN, out: 'image' },
        compress_image: { in: IMG_IN, out: 'image' },
        watermark_image: { in: IMG_IN, out: 'image' },
        convert_image: { in: IMG_IN, out: 'image' },
        heic_to_jpeg: { in: ['heic'], out: 'image' },
        csv_to_xlsx: { in: ['csv'], out: 'xlsx' },
        xlsx_to_csv: { in: ['xlsx'], out: 'csv' },
        excel_to_pdf: { in: ['xlsx', 'xls'], out: 'pdf' },
        ppt_to_pdf: { in: ['pptx', 'ppt'], out: 'pdf' },
        ppt_to_images: { in: ['pptx', 'ppt'], out: null },
        word_to_pdf: { in: ['docx', 'doc'], out: 'pdf' },
        word_to_pptx: { in: ['docx', 'doc'], out: 'pptx' },
    };

    function classOf(name) {
        var m = /\.([A-Za-z0-9]+)$/.exec(String(name || ''));
        return m ? (EXT_CLASS[m[1].toLowerCase()] || null) : null;
    }

    function stepName(step, i) {
        return 'Step ' + (i + 1) + ' (' + (step.label || step.type) + ')';
    }

    /**
     * Validate the whole chain before any step runs or any byte moves. Unknown
     * step types are left to the server to judge; a known step fed an
     * incompatible file class is rejected here.
     */
    function preflight(file, stepList) {
        var cls = classOf(file.name);
        for (var i = 0; i < stepList.length; i++) {
            var meta = STEP_TYPES[stepList[i].type];
            if (!meta) { cls = null; continue; }
            if (cls && meta.in.indexOf(cls) < 0) {
                throw new L.Error(stepName(stepList[i], i) + ' cannot accept a ' + cls +
                    ' file (expects ' + meta.in.join(', ') + ').');
            }
            cls = meta.out;
        }
    }

    function isLocalStep(step) {
        var path = STEP_PATH_MAP[step.type];
        return !!(path && L.enabled() && L.handlers[path]);
    }

    function authHeaders() {
        var headers = {};
        if (window.__ffSession && window.__ffSession.access_token) {
            headers.Authorization = 'Bearer ' + window.__ffSession.access_token;
        }
        return headers;
    }


    var STEP_PATH_MAP = {
        rotate_pdf: '/api/pdf/rotate',
        organize_pdf: '/api/pdf/organize',
        add_page_numbers: '/api/pdf/add-page-numbers',
        protect_pdf: '/api/pdf/protect',
        annotate_pdf: '/api/pdf/annotate',
        edit_metadata: '/api/pdf/metadata',
        extract_text: '/api/pdf/extract-text',
        resize_image: '/api/image/resize',
        crop_image: '/api/image/crop',
        rotate_image: '/api/image/rotate',
        compress_image: '/api/image/compress',
        convert_image: '/api/image/convert',
        watermark_image: '/api/image/watermark',
        csv_to_xlsx: '/api/excel/csv-to-xlsx',
        xlsx_to_csv: '/api/excel/xlsx-to-csv',
    };

    function buildStepFormData(file, step) {
        var fd = new FormData();
        fd.append('file', file);
        var c = step.config || {};
        switch (step.type) {
            case 'rotate_pdf':
                fd.append('angle', c.angle !== undefined ? c.angle : 90);
                if (c.pages) fd.append('pages', c.pages);
                if (c.password) fd.append('password', c.password);
                break;
            case 'organize_pdf':
                var po = c.page_order;
                if (Array.isArray(po)) po = po.join(',');
                if (po) fd.append('page_order', po);
                if (c.password) fd.append('password', c.password);
                break;
            case 'add_page_numbers':
                if (c.position) fd.append('position', c.position);
                if (c.start_number !== undefined) fd.append('start_number', c.start_number);
                if (c.font_size !== undefined) fd.append('font_size', c.font_size);
                if (c.skip_first !== undefined) fd.append('skip_first', c.skip_first);
                if (c.fmt) fd.append('fmt', c.fmt);
                if (c.password) fd.append('password', c.password);
                break;
            case 'protect_pdf':
                if (c.user_password) fd.append('user_password', c.user_password);
                if (c.owner_password) fd.append('owner_password', c.owner_password);
                if (c.password) fd.append('password', c.password);
                break;
            case 'annotate_pdf':
                if (c.annotations) {
                    fd.append('annotations', typeof c.annotations === 'string' ? c.annotations : JSON.stringify(c.annotations));
                }
                if (c.password) fd.append('password', c.password);
                break;
            case 'edit_metadata':
                if (c.title) fd.append('title', c.title);
                if (c.author) fd.append('author', c.author);
                if (c.subject) fd.append('subject', c.subject);
                if (c.keywords) fd.append('keywords', c.keywords);
                if (c.creator) fd.append('creator', c.creator);
                if (c.clear_all) fd.append('clear_all', 'true');
                if (c.password) fd.append('password', c.password);
                break;
            case 'extract_text':
                if (c.preserve_layout) fd.append('preserve_layout', 'true');
                if (c.password) fd.append('password', c.password);
                break;
            case 'resize_image':
                fd.append('mode', c.mode || 'percentage');
                if (c.percentage !== undefined) fd.append('percentage', c.percentage);
                if (c.width !== undefined) fd.append('width', c.width);
                if (c.height !== undefined) fd.append('height', c.height);
                break;
            case 'crop_image':
                if (c.x !== undefined) fd.append('x', c.x);
                if (c.y !== undefined) fd.append('y', c.y);
                if (c.width !== undefined) fd.append('width', c.width);
                if (c.height !== undefined) fd.append('height', c.height);
                break;
            case 'rotate_image':
                fd.append('angle', c.angle !== undefined ? c.angle : 90);
                break;
            case 'compress_image':
                if (c.quality !== undefined) fd.append('quality', c.quality);
                break;
            case 'convert_image':
                if (c.target_format) fd.append('target_format', c.target_format);
                if (c.quality !== undefined) fd.append('quality', c.quality);
                break;
            case 'watermark_image':
                if (c.text) fd.append('text', c.text);
                if (c.position) fd.append('position', c.position);
                if (c.opacity !== undefined) fd.append('opacity', c.opacity);
                if (c.color) fd.append('color', c.color);
                break;
            case 'csv_to_xlsx':
                if (c.delimiter) fd.append('delimiter', c.delimiter);
                break;
            case 'xlsx_to_csv':
                if (c.sheet) fd.append('sheet', c.sheet);
                break;
        }
        return fd;
    }

    L.register('/api/workflow/execute', async function (formData, ctx) {
        ctx = ctx || {};
        var file = formData.get('file');
        if (!file || typeof file.name !== 'string') {
            throw new L.Error('No file provided.');
        }

        var rawSteps = formData.get('steps');
        if (!rawSteps) {
            throw new L.Error('Invalid steps JSON');
        }

        var stepList;
        try {
            stepList = JSON.parse(rawSteps);
        } catch (e) {
            throw new L.Error('Invalid steps JSON');
        }

        if (!Array.isArray(stepList) || stepList.length === 0) {
            throw new L.Error('steps must be a non-empty list');
        }
        if (stepList.length > maxSteps()) {
            throw new L.Error('Too many steps (max ' + maxSteps() + ')');
        }
        for (var sIdx = 0; sIdx < stepList.length; sIdx++) {
            if (!stepList[sIdx] || typeof stepList[sIdx] !== 'object') {
                throw new L.Error('Each step must be an object');
            }
        }
        preflight(file, stepList);

        var encoder = new TextEncoder();
        var stream = new ReadableStream({
            start: async function (controller) {
                function send(obj) {
                    controller.enqueue(encoder.encode('data: ' + JSON.stringify(obj) + '\n\n'));
                }
                function fail(detail) {
                    send({ event: 'error', detail: detail });
                    controller.close();
                }

                var totalSteps = stepList.length;
                var currentFile = file;
                send({ event: 'start', total: totalSteps });

                var i = 0;
                while (i < totalSteps) {
                    L.checkAbort(ctx.signal);

                    var step = stepList[i];
                    var stepLabel = step.label || step.type;
                    var failCode = null;   // set when a local attempt fell through

                    if (isLocalStep(step)) {
                        send({ event: 'step_start', step: i, total: totalSteps, label: stepLabel });
                        try {
                            var out = await L.handlers[STEP_PATH_MAP[step.type]](
                                buildStepFormData(currentFile, step), { signal: ctx.signal });
                            currentFile = new File([out.blob], out.filename, { type: out.blob.type });
                            send({ event: 'step_complete', step: i, total: totalSteps, label: stepLabel });
                            i++;
                            await L.tick();
                            continue;
                        } catch (err) {
                            if (err instanceof L.Error) { fail(err.message); return; }
                            if (err && err.name === 'AbortError') { controller.error(err); return; }
                            failCode = err instanceof L.Unsupported ? err.code : 'engine_unavailable';
                        }
                    }

                    // Server segment: this step plus any directly following steps
                    // that have no on-device handler. Local steps after it run
                    // on-device again on the downloaded result.
                    var segEnd = i + 1;
                    while (segEnd < totalSteps && !isLocalStep(stepList[segEnd])) segEnd++;
                    var segment = stepList.slice(i, segEnd);

                    var reason = 'Step ' + stepLabel + ' needs server processing because ' +
                        (failCode && window.ffConsent && window.ffConsent.describe
                            ? window.ffConsent.describe('/api/workflow/execute', failCode).reason
                            : 'its conversion engine is unavailable locally');

                    var serverFd = new FormData();
                    serverFd.append('file', currentFile);
                    serverFd.append('steps', JSON.stringify(segment));

                    var serverRes;
                    try {
                        serverRes = await window.ffProcess('/api/workflow/execute', serverFd, {
                            signal: ctx.signal,
                            serverOnly: true,
                            consent: { reason: reason, filename: currentFile.name },
                        });
                    } catch (netErr) {
                        if (netErr && netErr.name === 'AbortError') { controller.error(netErr); return; }
                        fail(netErr.message || 'Workflow server connection failed');
                        return;
                    }

                    var ctype = serverRes.headers.get('content-type') || '';
                    if (!serverRes.ok && ctype.indexOf('text/event-stream') < 0) {
                        var errDetail = 'Server workflow request failed';
                        try {
                            var errJson = await serverRes.json();
                            if (errJson && errJson.detail) errDetail = errJson.detail;
                        } catch (e) { /* ignore */ }
                        fail(errDetail);
                        return;
                    }

                    var terminal = null;
                    try {
                        var reader = serverRes.body.getReader();
                        var decoder = new TextDecoder();
                        var buffer = '';
                        while (true) {
                            var chunk = await reader.read();
                            if (chunk.done) break;
                            buffer += decoder.decode(chunk.value, { stream: true });
                            var frames = buffer.split('\n\n');
                            buffer = frames.pop();
                            for (var f = 0; f < frames.length; f++) {
                                if (frames[f].indexOf('data: ') !== 0) continue;
                                var parsed;
                                try { parsed = JSON.parse(frames[f].substring(6)); } catch (e) { continue; }
                                if (parsed.event === 'start') continue;
                                if (parsed.event === 'step_start' || parsed.event === 'step_complete') {
                                    parsed.step = parsed.step + i;
                                    parsed.total = totalSteps;
                                    send(parsed);
                                } else if (parsed.event === 'complete' || parsed.event === 'error') {
                                    terminal = parsed;
                                } else {
                                    send(parsed);
                                }
                            }
                        }
                    } catch (streamErr) {
                        if (streamErr && streamErr.name === 'AbortError') { controller.error(streamErr); return; }
                        fail(streamErr.message || 'Workflow server connection failed');
                        return;
                    }

                    if (!terminal) { fail('Workflow ended without a result'); return; }
                    if (terminal.event === 'error') { send(terminal); controller.close(); return; }

                    if (segEnd >= totalSteps) {
                        // The server produced the final file; hand its token on as is.
                        send(terminal);
                        controller.close();
                        return;
                    }

                    // More steps follow (local ones): fetch the server result as the
                    // next intermediate. This is a download, not an upload.
                    try {
                        var dl = await fetch(window.apiUrl('/api/download/' + encodeURIComponent(terminal.download_token)), {
                            headers: authHeaders(),
                            signal: ctx.signal,
                        });
                        if (!dl.ok) throw new Error('Could not retrieve the server result');
                        var blob = await dl.blob();
                        currentFile = new File([blob], terminal.filename || currentFile.name, { type: blob.type });
                    } catch (dlErr) {
                        if (dlErr && dlErr.name === 'AbortError') { controller.error(dlErr); return; }
                        fail(dlErr.message || 'Could not retrieve the server result');
                        return;
                    }
                    i = segEnd;
                }

                // Every step ran on-device.
                var published = L.publish(currentFile, currentFile.name);
                send({
                    event: 'complete',
                    status: 'success',
                    message: 'Workflow completed successfully',
                    filename: published.filename,
                    download_token: published.download_token,
                    local: true,
                });
                controller.close();
            }
        });

        return new Response(stream, {
            status: 200,
            headers: { 'Content-Type': 'text/event-stream; charset=utf-8' },
        });
    });

    L.workflow = {
        STEP_PATH_MAP: STEP_PATH_MAP,
        STEP_TYPES: STEP_TYPES,
        buildStepFormData: buildStepFormData,
        preflight: preflight,
    };
})();
