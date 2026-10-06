// HEIC/HEIF to JPEG on-device via pinned heic-to/libheif (WP23).
(function () {
    'use strict';
    var L = window.ffLocal;
    if (!L) return;
    var ENGINE = null;
    var MAX_INPUT = 40 * 1024 * 1024;
    var MAX_PIXELS = 40 * 1000 * 1000;

    function loadEngine() {
        if (window.HeicTo) return Promise.resolve(window.HeicTo);
        if (!ENGINE) ENGINE = new Promise(function (resolve, reject) {
            var script = document.createElement('script');
            script.src = L.vendorUrl('libheif/heic-to.js') + '?v=1.6.5';
            script.onload = function () { window.HeicTo ? resolve(window.HeicTo) : reject(new Error('HEIC engine did not initialise.')); };
            script.onerror = function () { reject(new Error('Unable to load the HEIC engine.')); };
            document.head.appendChild(script);
        });
        return ENGINE;
    }

    L.register('/api/image/heic-to-jpeg', async function (fd, ctx) {
        var files = L.files(fd, 'file');
        if (files.length !== 1) throw new L.Error('Please select a HEIC image.');
        var file = files[0];
        if (file.size > MAX_INPUT) throw new L.Unsupported('HEIC exceeds local memory budget.', 'resource_budget');
        var quality = L.range('quality', L.int(fd, 'quality', 95), 1, 100);
        L.checkAbort(ctx.signal);
        var engine = await loadEngine();
        if (!(await engine.isHeic(file))) throw new L.Error('The selected file is not a supported HEIC or HEIF image.');
        var jpeg;
        try {
            jpeg = await engine({ blob: file, type: 'image/jpeg', quality: quality / 100 });
        } catch (err) {
            throw new L.Unsupported('This HEIC profile cannot be decoded reliably on this device.', 'unsupported_image_codec');
        }
        L.checkAbort(ctx.signal);
        var bitmap = await createImageBitmap(jpeg);
        var pixels = bitmap.width * bitmap.height;
        var width = bitmap.width;
        var height = bitmap.height;
        bitmap.close();
        if (pixels > MAX_PIXELS) throw new L.Unsupported('Decoded image exceeds local pixel budget.', 'resource_budget');
        return {
            blob: jpeg,
            filename: L.brandedName(file.name, 'jpg'),
            message: 'Converted to JPEG',
            extra: { width: width, height: height, output_size: jpeg.size, quality: quality },
        };
    });
})();
