// On-device visual signature (migration work package 06).
//
// Mirrors pdf_utils.py::sign_pdf: stamps a PNG or JPEG image onto one page. This
// is a VISUAL signature, a picture of a signature. It is not a certificate-based
// digital signature and proves nothing about who signed or whether the file was
// altered afterwards.
//
// Placement is the server's: x, y and width are fractions of the page (origin
// top-left), the box height follows the image's aspect ratio, the box is clamped
// to the page, and the image is scaled to fit inside it keeping proportions,
// centred.
(function () {
    'use strict';

    var L = window.ffLocal;
    if (!L) return;

    var MIB = 1024 * 1024;
    var MAX_SIGNATURE_BYTES = 10 * MIB;

    function bytesOf(file) {
        if (file.arrayBuffer) return file.arrayBuffer();
        return new Promise(function (fulfil, fail) {
            var reader = new FileReader();
            reader.onload = function () { fulfil(reader.result); };
            reader.onerror = function () { fail(reader.error); };
            reader.readAsArrayBuffer(file);
        });
    }

    /** 'png', 'jpg' or null, by magic bytes rather than by trusting the name. */
    function imageKind(bytes) {
        if (bytes.length > 8 && bytes[0] === 0x89 && bytes[1] === 0x50 && bytes[2] === 0x4e && bytes[3] === 0x47) return 'png';
        if (bytes.length > 3 && bytes[0] === 0xff && bytes[1] === 0xd8 && bytes[2] === 0xff) return 'jpg';
        return null;
    }

    function number(fd, name, dflt) {
        var raw = fd.get(name);
        if (raw === null || raw === undefined || raw === '') return dflt;
        var n = Number(String(raw).trim());
        if (!isFinite(n)) throw new L.Error('page must be an integer; x, y, width must be numbers.');
        return n;
    }

    L.register('/api/pdf/sign', async function (fd, ctx) {
        ctx = ctx || {};
        var pdfs = L.files(fd, 'file');
        if (!pdfs.length) throw new L.Error('No file provided.');
        var sigs = L.files(fd, 'signature');
        var file = pdfs[0];

        if (L.str(fd, 'password', null)) {
            throw new L.Unsupported('password-protected PDFs need the server', 'encrypted');
        }

        // Same order as the server: signature type first, then the numbers.
        if (!sigs.length) throw new L.Error('Signature must be a PNG or JPEG image.');
        var sigFile = sigs[0];
        var type = String(sigFile.type || '').toLowerCase();
        if (type && ['image/png', 'image/jpeg', 'image/jpg'].indexOf(type) < 0) {
            throw new L.Error('Signature must be a PNG or JPEG image.');
        }

        var rawPage = fd.get('page');
        if (rawPage !== null && rawPage !== undefined && rawPage !== '' && !/^[+-]?\d+$/.test(String(rawPage).trim())) {
            throw new L.Error('page must be an integer; x, y, width must be numbers.');
        }
        var page = number(fd, 'page', 1);
        var x = number(fd, 'x', 0.65);
        var y = number(fd, 'y', 0.85);
        var width = number(fd, 'width', 0.2);
        if (page < 1) throw new L.Error('Page number must be >= 1.');
        if (!(x >= 0 && x <= 1 && y >= 0 && y <= 1)) throw new L.Error('x and y must be between 0 and 1.');
        if (!(width >= 0.05 && width <= 1)) throw new L.Error('width must be between 0.05 and 1.0.');

        var limit = L.constrained() ? 50 * MIB : 150 * MIB;
        if (file.size > limit || sigFile.size > MAX_SIGNATURE_BYTES) {
            throw new L.Unsupported('input exceeds the on-device signing budget', 'resource_budget_exceeded');
        }

        var sigBytes = new Uint8Array(await bytesOf(sigFile));
        var kind = imageKind(sigBytes);
        if (!kind) throw new L.Error('Signature must be a PNG or JPEG image.');

        var PDFLib = await L.loadPdfLib();
        var doc;
        try {
            doc = await PDFLib.PDFDocument.load(new Uint8Array(await bytesOf(file)));
        } catch (err) {
            var enc = (err && err.name || '').indexOf('Encrypted') >= 0 || /encrypt/i.test((err && err.message) || '');
            throw new L.Unsupported('pdf-lib could not open this PDF', enc ? 'encrypted' : 'unsupported_structure');
        }
        if (!doc.catalog || doc.getPageCount() < 1) {
            throw new L.Unsupported('PDF structure could not be read', 'unsupported_structure');
        }
        if (page > doc.getPageCount()) {
            throw new L.Error('Page ' + page + ' exceeds document page count (' + doc.getPageCount() + ').');
        }
        L.checkAbort(ctx.signal);

        var target = doc.getPage(page - 1);
        if ((target.getRotation().angle || 0) % 360 !== 0) {
            // Mapping placement through a rotated page is not done on-device.
            throw new L.Unsupported('signing a rotated page is not supported on-device', 'unsupported_structure');
        }

        var image;
        try {
            image = kind === 'png' ? await doc.embedPng(sigBytes) : await doc.embedJpg(sigBytes);
        } catch (err) {
            throw new L.Unsupported('signature image could not be decoded', 'undecodable');
        }

        // Box, in the server's page space (top-left origin, y down).
        var crop = target.getCropBox();
        var pw = crop.width, ph = crop.height;
        var boxW = pw * width;
        var aspect = image.width ? image.height / image.width : 0.4;
        var boxH = boxW * aspect;
        var x0 = pw * x, y0 = ph * y;
        var x1 = Math.min(pw, x0 + boxW);
        var y1 = Math.min(ph, y0 + boxH);
        var rw = x1 - x0, rh = y1 - y0;

        // keep_proportion: scale to fit the (possibly clamped) box, centred.
        var scale = Math.min(rw / image.width, rh / image.height);
        var dw = image.width * scale, dh = image.height * scale;
        var left = x0 + (rw - dw) / 2;
        var top = y0 + (rh - dh) / 2;

        target.drawImage(image, {
            x: crop.x + left,
            y: crop.y + ph - top - dh,
            width: dw,
            height: dh,
        });

        L.checkAbort(ctx.signal);
        return {
            blob: new Blob([await doc.save()], { type: 'application/pdf' }),
            filename: L.brandedName(file.name, 'pdf'),
            message: 'Signature added',
        };
    });

    L.sign = { imageKind: imageKind };
})();
