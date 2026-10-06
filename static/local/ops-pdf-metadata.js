// On-device PDF metadata editing (migration work package 05).
//
// Mirrors pdf_utils.py::edit_pdf_metadata: title, author, subject, keywords and
// creator are written to BOTH the classic document-information dictionary and
// the XMP packet, only for fields the user supplied, and nothing else about the
// file changes (no Producer or date rewrite, page content byte-identical).
//
// `clear_all` removes the XMP properties and, as on the server, leaves the
// document-information dictionary alone except for fields set in the same call.
//
// Existing XMP is edited as XML (DOMParser) so unrelated properties survive. If
// the browser cannot parse the existing packet, the handler declines and the
// user is asked before the server (pikepdf) is used.
//
// Only /api/pdf/metadata is handled. /api/pdf/metadata/read returns data rather
// than a file and has no UI, so it is left on the server for now.
(function () {
    'use strict';

    var L = window.ffLocal;
    if (!L) return;

    var NS = {
        rdf: 'http://www.w3.org/1999/02/22-rdf-syntax-ns#',
        dc: 'http://purl.org/dc/elements/1.1/',
        pdf: 'http://ns.adobe.com/pdf/1.3/',
        xmp: 'http://ns.adobe.com/xap/1.0/',
        xml: 'http://www.w3.org/XML/1998/namespace',
        xmlns: 'http://www.w3.org/2000/xmlns/',
    };
    var MIB = 1024 * 1024;
    var CLEARED_INFO = ['Title', 'Author', 'Subject', 'Keywords', 'Creator', 'Producer', 'CreationDate', 'ModDate'];

    // form field -> {docinfo key, XMP property}
    var FIELDS = [
        { field: 'title', info: 'Title', ns: 'dc', prefix: 'dc', name: 'title', kind: 'alt' },
        { field: 'author', info: 'Author', ns: 'dc', prefix: 'dc', name: 'creator', kind: 'seq' },
        { field: 'subject', info: 'Subject', ns: 'dc', prefix: 'dc', name: 'description', kind: 'alt' },
        { field: 'keywords', info: 'Keywords', ns: 'pdf', prefix: 'pdf', name: 'Keywords', kind: 'simple' },
        { field: 'creator', info: 'Creator', ns: 'xmp', prefix: 'xmp', name: 'CreatorTool', kind: 'simple' },
    ];

    function bytesOf(file) {
        if (file.arrayBuffer) return file.arrayBuffer();
        return new Promise(function (fulfil, fail) {
            var reader = new FileReader();
            reader.onload = function () { fulfil(reader.result); };
            reader.onerror = function () { fail(reader.error); };
            reader.readAsArrayBuffer(file);
        });
    }

    function truthy(v) {
        v = String(v === null || v === undefined ? '' : v).trim().toLowerCase();
        return v === 'true' || v === '1' || v === 'on' || v === 'yes';
    }

    function escapeXml(s) {
        return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    }

    // A control character in a value would make the XMP unparseable.
    function cleanForXml(s) {
        return String(s).replace(/[\u0000-\u0008\u000B\u000C\u000E-\u001F]/g, '');
    }

    function propertyXml(f, value) {
        var v = escapeXml(cleanForXml(value));
        var tag = f.prefix + ':' + f.name;
        var decl = ' xmlns:' + f.prefix + '="' + NS[f.ns] + '"';
        if (f.kind === 'alt') {
            return '<' + tag + decl + '><rdf:Alt><rdf:li xml:lang="x-default">' + v + '</rdf:li></rdf:Alt></' + tag + '>';
        }
        if (f.kind === 'seq') {
            return '<' + tag + decl + '><rdf:Seq><rdf:li>' + v + '</rdf:li></rdf:Seq></' + tag + '>';
        }
        return '<' + tag + decl + '>' + v + '</' + tag + '>';
    }

    /** A fresh XMP packet for a file that has none. */
    function newPacket(values) {
        var body = FIELDS.filter(function (f) { return values[f.field] !== undefined; })
            .map(function (f) { return propertyXml(f, values[f.field]); }).join('');
        return '<?xpacket begin="﻿" id="W5M0MpCehiHzreSzNTczkc9d"?>\n' +
            '<x:xmpmeta xmlns:x="adobe:ns:meta/" x:xmptk="Forge Files">\n' +
            ' <rdf:RDF xmlns:rdf="' + NS.rdf + '">\n' +
            ' <rdf:Description rdf:about="">' + body + '</rdf:Description>\n' +
            ' </rdf:RDF>\n</x:xmpmeta>\n<?xpacket end="w"?>';
    }

    function childrenNamed(parent, ns, name) {
        var out = [];
        for (var n = parent.firstChild; n; n = n.nextSibling) {
            if (n.nodeType === 1 && n.namespaceURI === ns && n.localName === name) out.push(n);
        }
        return out;
    }

    /** Edit an existing packet; returns the new packet text. Throws Unsupported if it cannot be done safely. */
    function editPacket(text, values, clearAll) {
        if (typeof DOMParser === 'undefined' || typeof XMLSerializer === 'undefined') {
            throw new L.Unsupported('no XML parser available for the existing XMP', 'unsupported_structure');
        }
        var xml = new DOMParser().parseFromString(text.replace(/^﻿/, ''), 'application/xml');
        if (!xml.documentElement || xml.getElementsByTagName('parsererror').length) {
            throw new L.Unsupported('existing XMP is not well-formed', 'unsupported_structure');
        }
        var descs = xml.getElementsByTagNameNS(NS.rdf, 'Description');
        var desc = descs.length ? descs[0] : null;
        if (!desc) {
            // A packet with no description: add one inside rdf:RDF if present.
            var rdf = xml.getElementsByTagNameNS(NS.rdf, 'RDF')[0];
            if (!rdf) throw new L.Unsupported('existing XMP has no rdf:RDF', 'unsupported_structure');
            desc = xml.createElementNS(NS.rdf, 'rdf:Description');
            desc.setAttributeNS(NS.rdf, 'rdf:about', '');
            rdf.appendChild(desc);
            descs = [desc];
        }

        if (clearAll) {
            for (var d = 0; d < descs.length; d++) {
                var node = descs[d];
                while (node.firstChild) node.removeChild(node.firstChild);
                for (var a = node.attributes.length - 1; a >= 0; a--) {
                    var attr = node.attributes[a];
                    var keep = attr.namespaceURI === NS.xmlns || (attr.namespaceURI === NS.rdf && attr.localName === 'about');
                    if (!keep) node.removeAttributeNode(attr);
                }
            }
        }

        FIELDS.forEach(function (f) {
            if (values[f.field] === undefined) return;
            // Remove every existing form of this property (element or attribute, any Description).
            for (var d2 = 0; d2 < descs.length; d2++) {
                childrenNamed(descs[d2], NS[f.ns], f.name).forEach(function (el) { descs[d2].removeChild(el); });
                if (descs[d2].hasAttributeNS(NS[f.ns], f.name)) descs[d2].removeAttributeNS(NS[f.ns], f.name);
            }
            var holder = new DOMParser().parseFromString(
                '<root xmlns:rdf="' + NS.rdf + '">' + propertyXml(f, values[f.field]) + '</root>', 'application/xml');
            desc.appendChild(xml.importNode(holder.documentElement.firstChild, true));
        });

        var out = new XMLSerializer().serializeToString(xml);
        return out;
    }

    function readXmp(PDFLib, doc) {
        var ref = doc.catalog.get(PDFLib.PDFName.of('Metadata'));
        if (!ref) return null;
        var stream = doc.context.lookup(ref);
        if (!stream) return null;
        try {
            var data = PDFLib.decodePDFRawStream ? PDFLib.decodePDFRawStream(stream).decode() : stream.getContents();
            return new TextDecoder('utf-8').decode(data);
        } catch (e) {
            throw new L.Unsupported('existing XMP stream could not be decoded', 'unsupported_structure');
        }
    }

    function writeXmp(PDFLib, doc, packet) {
        var stream = doc.context.stream(new Uint8Array(new TextEncoder().encode(packet)), { Type: 'Metadata', Subtype: 'XML' });
        doc.catalog.set(PDFLib.PDFName.of('Metadata'), doc.context.register(stream));
    }

    L.register('/api/pdf/metadata', async function (fd, ctx) {
        ctx = ctx || {};
        var files = L.files(fd, 'file');
        if (!files.length) throw new L.Error('No file provided.');
        var file = files[0];

        if (L.str(fd, 'password', null)) {
            throw new L.Unsupported('password-protected PDFs need the server', 'encrypted');
        }

        // Only fields actually supplied are written (the server skips None).
        var values = {};
        FIELDS.forEach(function (f) {
            var v = fd.get(f.field);
            if (v !== null && v !== undefined) values[f.field] = String(v);
        });
        var clearAll = truthy(fd.get('clear_all'));

        var limit = L.constrained() ? 50 * MIB : 150 * MIB;
        if (file.size > limit) {
            throw new L.Unsupported('input exceeds the on-device metadata budget', 'resource_budget_exceeded');
        }

        var PDFLib = await L.loadPdfLib();
        var doc;
        try {
            // updateMetadata:false stops pdf-lib stamping its own Producer and dates.
            doc = await PDFLib.PDFDocument.load(new Uint8Array(await bytesOf(file)), { updateMetadata: false });
        } catch (err) {
            var enc = (err && err.name || '').indexOf('Encrypted') >= 0 || /encrypt/i.test((err && err.message) || '');
            throw new L.Unsupported('pdf-lib could not open this PDF', enc ? 'encrypted' : 'unsupported_structure');
        }
        // pdf-lib is lenient with damaged files and can hand back an empty shell.
        if (!doc.catalog || doc.getPageCount() < 1) {
            throw new L.Unsupported('PDF structure could not be read', 'unsupported_structure');
        }
        L.checkAbort(ctx.signal);

        // XMP first: it is the step that can decline, and nothing is committed until save().
        var existing = readXmp(PDFLib, doc);
        var touchesXmp = clearAll || Object.keys(values).length > 0;
        if (touchesXmp) {
            if (existing === null) {
                if (Object.keys(values).length) writeXmp(PDFLib, doc, newPacket(values));
            } else {
                writeXmp(PDFLib, doc, editPacket(existing, values, clearAll));
            }
        }

        // Classic docinfo, for compatibility with readers that ignore XMP.
        var info = doc.getInfoDict();
        if (clearAll) {
            // The server's clear_all ends up removing these standard entries (its
            // XMP-to-docinfo sync deletes whatever XMP no longer carries). Custom
            // keys are kept, as there.
            CLEARED_INFO.forEach(function (key) { info.delete(PDFLib.PDFName.of(key)); });
        }
        FIELDS.forEach(function (f) {
            if (values[f.field] !== undefined) {
                info.set(PDFLib.PDFName.of(f.info), PDFLib.PDFHexString.fromText(values[f.field]));
            }
        });

        return {
            blob: new Blob([await doc.save({ updateFieldAppearances: false })], { type: 'application/pdf' }),
            filename: L.brandedName(file.name, 'pdf'),
            message: 'PDF metadata updated',
        };
    });

    L.metadata = { newPacket: newPacket, editPacket: editPacket };
})();
