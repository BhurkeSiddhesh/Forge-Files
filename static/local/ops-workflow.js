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

    var MAX_WORKFLOW_STEPS = 10;

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
        if (stepList.length > MAX_WORKFLOW_STEPS) {
            throw new L.Error('Too many steps (max ' + MAX_WORKFLOW_STEPS + ')');
        }
        for (var sIdx = 0; sIdx < stepList.length; sIdx++) {
            if (!stepList[sIdx] || typeof stepList[sIdx] !== 'object') {
                throw new L.Error('Each step must be an object');
            }
        }

        // Return a ReadableStream that delivers SSE formatted events
        var encoder = new TextEncoder();
        var stream = new ReadableStream({
            start: async function (controller) {
                function send(obj) {
                    controller.enqueue(encoder.encode('data: ' + JSON.stringify(obj) + '\n\n'));
                }

                send({ event: 'start', total: stepList.length });

                var currentFile = file;
                var totalSteps = stepList.length;

                for (var i = 0; i < totalSteps; i++) {
                    L.checkAbort(ctx.signal);

                    var step = stepList[i];
                    var stepType = step.type;
                    var stepLabel = step.label || stepType;
                    var path = STEP_PATH_MAP[stepType];
                    var localHandler = path && L.handlers[path];

                    var canRunLocal = !!localHandler;

                    if (canRunLocal) {
                        send({ event: 'step_start', step: i, total: totalSteps, label: stepLabel });
                        try {
                            var stepFd = buildStepFormData(currentFile, step);
                            var out = await localHandler(stepFd, { signal: ctx.signal });
                            currentFile = new File([out.blob], out.filename, { type: out.blob.type });
                            send({ event: 'step_complete', step: i, total: totalSteps, label: stepLabel });
                            await L.tick();
                            continue; // Step finished locally, move to next step
                        } catch (err) {
                            if (err instanceof L.Error) {
                                send({ event: 'error', detail: err.message });
                                controller.close();
                                return;
                            }
                            if (err && err.name === 'AbortError') {
                                controller.error(err);
                                return;
                            }
                            // Handler threw Unsupported or runtime error -> fall through to server for remaining steps
                            canRunLocal = false;
                        }
                    }

                    // This step needs server processing!
                    var reasonText = 'Step ' + stepLabel + ' needs server processing because its conversion engine is unavailable locally';
                    var agreed = false;
                    try {
                        agreed = window.ffConsent ? await window.ffConsent.request({
                            path: '/api/workflow/execute',
                            reason: reasonText,
                            filename: currentFile.name,
                        }) : false;
                    } catch (e) {
                        agreed = false;
                    }

                    if (!agreed) {
                        send({ event: 'error', detail: 'Cancelled. Your file was not uploaded.' });
                        controller.close();
                        return;
                    }

                    // User agreed: upload current intermediate and remaining steps
                    var remainingSteps = stepList.slice(i);
                    var serverFd = new FormData();
                    serverFd.append('file', currentFile);
                    serverFd.append('steps', JSON.stringify(remainingSteps));

                    var headers = {};
                    if (window.__ffSession && window.__ffSession.access_token) {
                        headers.Authorization = 'Bearer ' + window.__ffSession.access_token;
                    }

                    try {
                        var serverRes = await fetch(window.apiUrl('/api/workflow/execute'), {
                            method: 'POST',
                            body: serverFd,
                            headers: headers,
                            signal: ctx.signal,
                        });

                        if (!serverRes.ok && !serverRes.headers.get('content-type')?.includes('text/event-stream')) {
                            var errDetail = 'Server workflow request failed';
                            try {
                                var errJson = await serverRes.json();
                                if (errJson && errJson.detail) errDetail = errJson.detail;
                            } catch (e) { /* ignore */ }
                            send({ event: 'error', detail: errDetail });
                            controller.close();
                            return;
                        }

                        // Stream and forward SSE from server with adjusted step offsets
                        var reader = serverRes.body.getReader();
                        var decoder = new TextDecoder();
                        var buffer = '';

                        while (true) {
                            var chunk = await reader.read();
                            if (chunk.done) break;
                            buffer += decoder.decode(chunk.value, { stream: true });
                            var lines = buffer.split('\n\n');
                            buffer = lines.pop();

                            for (var lIdx = 0; lIdx < lines.length; lIdx++) {
                                var line = lines[lIdx];
                                if (line.indexOf('data: ') === 0) {
                                    try {
                                        var parsed = JSON.parse(line.substring(6));
                                        if (parsed.event === 'step_start' || parsed.event === 'step_complete') {
                                            parsed.step = parsed.step + i;
                                            parsed.total = totalSteps;
                                        }
                                        send(parsed);
                                    } catch (e) {
                                        // Pass raw line if unparseable
                                        controller.enqueue(encoder.encode(line + '\n\n'));
                                    }
                                }
                            }
                        }
                    } catch (netErr) {
                        send({ event: 'error', detail: netErr.message || 'Workflow server connection failed' });
                    }

                    controller.close();
                    return;
                }

                // All steps completed locally on-device!
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
        buildStepFormData: buildStepFormData,
    };
})();
