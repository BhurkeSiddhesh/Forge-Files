// On-device Unlock PDF and Protect PDF, using the pinned qpdf WebAssembly build that
// local Compress already ships (vendor/qpdf). The file and the passwords stay in
// the browser tab.
//
// Routes mirror the Python endpoints and their form fields:
//   /api/pdf/remove-password  file, password
//   /api/pdf/protect          file, user_password, owner_password?, allow_print,
//                             allow_copy, allow_edit, password? (existing password)
//
// Behaviour matches scripts/pdf_utils.py:
//   * Unlock tries the supplied password and, if that is rejected, retries with an
//     empty one. That strips owner-only restrictions (a PDF that opens without a
//     password) even when the password given is wrong, but a PDF that genuinely
//     needs an open password still fails, so it never reports a false unlock.
//   * Protect writes AES-256 (the same as pikepdf's default). The owner password
//     defaults to the user password, printing is allowed/denied as a whole, and
//     "editing" covers annotations, page assembly, forms and other changes.
//     Accessibility is always allowed.
//
// A wrong password is the visitor's input being wrong, so it is reported as such
// and never handed to the server. Anything qpdf cannot safely process is declined
// to the usual "may the server take this file?" question.
(function () {
    'use strict';
    var L = window.ffLocal;
    if (!L) return;

    var INPUT_LIMIT = 80 * 1024 * 1024;
    var QPDF_VERSION = '0.3.0';
    var qpdfPromise = null;

    function loadScript(src) {
        return new Promise(function (resolve, reject) {
            var script = document.createElement('script');
            script.src = src;
            script.onload = resolve;
            script.onerror = function () { reject(new Error('Unable to load the PDF engine.')); };
            document.head.appendChild(script);
        });
    }

    async function loadQpdf() {
        if (typeof window.Module === 'function') return window.Module;
        if (!qpdfPromise) {
            qpdfPromise = loadScript(L.vendorUrl('qpdf/qpdf.js') + '?v=' + QPDF_VERSION).then(function () {
                if (typeof window.Module !== 'function') throw new Error('qpdf did not initialise.');
                return window.Module;
            });
        }
        return qpdfPromise;
    }

    function pdfFile(fd) {
        var files = L.files(fd, 'file');
        if (files.length !== 1) throw new L.Error('Please select a PDF file.');
        if (files[0].size > INPUT_LIMIT) throw new L.Unsupported('PDF exceeds local memory budget.', 'resource_budget');
        return files[0];
    }

    function truthy(v, dflt) {
        if (v === null || v === undefined || v === '') return dflt;
        return /^(true|1|on|yes)$/i.test(String(v));
    }

    // True when the file's bytes carry an /Encrypt entry (the trailer or xref-stream
    // dictionary, never inside a compressed object stream).
    function looksEncrypted(b) {
        var pat = [0x2f, 0x45, 0x6e, 0x63, 0x72, 0x79, 0x70, 0x74]; // "/Encrypt"
        for (var i = 0, n = b.length - pat.length; i <= n; i++) {
            if (b[i] !== 0x2f) continue;
            var j = 1;
            while (j < pat.length && b[i + j] === pat[j]) j++;
            if (j === pat.length) return true;
        }
        return false;
    }

    /**
     * Runs one qpdf invocation. qpdf's own diagnostics are not available here, so the
     * result is read from its exit status: 0 is success, 3 is "written with warnings"
     * (the output is still good), and 2 is an error. An error on a file that carries an
     * /Encrypt entry is a rejected password; anything else is a PDF qpdf cannot safely
     * process, which is declined to the usual server question.
     */
    async function runQpdf(inputBytes, args, signal) {
        L.checkAbort(signal);
        var createModule = await loadQpdf();
        var wasmUrl = L.vendorUrl('qpdf/qpdf.wasm');
        var wasm = await fetch(wasmUrl, { credentials: 'same-origin', signal: signal });
        if (!wasm.ok) throw new L.Unsupported('Unable to load the PDF engine.', 'engine_unavailable');
        var module = await createModule({
            noInitialRun: true,
            wasmBinary: await wasm.arrayBuffer(),
            locateFile: function () { return wasmUrl; },
            print: function () {},
            printErr: function () {},
        });
        module.FS.writeFile('/input.pdf', inputBytes);
        var code;
        try {
            code = module.callMain(args.concat(['/input.pdf', '/output.pdf']));
        } catch (err) {
            code = 2;
        }
        L.checkAbort(signal);
        var out = null;
        try { out = module.FS.readFile('/output.pdf'); } catch (e) { out = null; }
        if ((code === 0 || code === 3) && out && out.length >= 5) return { bytes: out };
        if (looksEncrypted(inputBytes)) return { badPassword: true };
        throw new L.Unsupported('qpdf cannot safely process this PDF.', 'unsupported_structure');
    }

    function pdfBlob(bytes) {
        return new Blob([bytes], { type: 'application/pdf' });
    }

    L.register('/api/pdf/remove-password', async function (fd, ctx) {
        var file = pdfFile(fd);
        var password = L.str(fd, 'password', '');
        var input = new Uint8Array(await file.arrayBuffer());
        var res = await runQpdf(input, ['--password=' + password, '--decrypt'], ctx.signal);
        if (res.badPassword && password !== '') {
            res = await runQpdf(input, ['--password=', '--decrypt'], ctx.signal);
        }
        if (res.badPassword) {
            throw new L.Error('Incorrect password. This PDF could not be unlocked.');
        }
        return { blob: pdfBlob(res.bytes), filename: L.brandedName(file.name, 'pdf'), message: 'Password removed' };
    });

    L.register('/api/pdf/protect', async function (fd, ctx) {
        var file = pdfFile(fd);
        var userPassword = L.str(fd, 'user_password', '');
        if (!userPassword) throw new L.Error('User password cannot be empty.');
        var ownerPassword = L.str(fd, 'owner_password', '') || userPassword;
        var existing = L.str(fd, 'password', '');
        var allowPrint = truthy(fd.get('allow_print'), true);
        var allowCopy = truthy(fd.get('allow_copy'), false);
        var allowEdit = truthy(fd.get('allow_edit'), false);
        var yn = function (v) { return v ? 'y' : 'n'; };

        var args = [];
        if (existing) args.push('--password=' + existing);
        args.push(
            '--encrypt', userPassword, ownerPassword, '256',
            '--print=' + (allowPrint ? 'full' : 'none'),
            '--extract=' + yn(allowCopy),
            '--annotate=' + yn(allowEdit),
            '--assemble=' + yn(allowEdit),
            '--form=' + yn(allowEdit),
            '--modify-other=' + yn(allowEdit),
            '--accessibility=y',
            '--'
        );
        var input = new Uint8Array(await file.arrayBuffer());
        var res = await runQpdf(input, args, ctx.signal);
        if (res.badPassword) {
            throw new L.Error('This PDF is already password-protected. Enter its current password to change it.');
        }
        return { blob: pdfBlob(res.bytes), filename: L.brandedName(file.name, 'pdf'), message: 'PDF protected with password' };
    });
})();
