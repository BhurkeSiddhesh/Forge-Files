// Opt-in "Convert on this device" for Word, Excel and PowerPoint (WP44).
//
// Server conversion stays the default. On a desktop browser, each Office tool
// also offers to run the same LibreOffice engine on the device, on the isolated
// /on-device-office/ page. The tool page cannot host the engine itself (it needs
// cross-origin isolation, which the ad-supported pages must not have), so the
// selected file is handed over through a same-origin BroadcastChannel keyed by
// a one-time random token. Nothing here touches the API.
(function () {
    'use strict';

    var CHANNEL = 'ff-office-handoff';
    var WAIT_MS = 60000;

    function isPhoneOrApp() {
        if (window.Capacitor) return true;
        var ua = navigator.userAgent || '';
        if (/Android|iPhone|iPad|iPod|Mobi/i.test(ua)) return true;
        // iPadOS reports a Mac user agent but has touch points.
        if (/Macintosh/i.test(ua) && navigator.maxTouchPoints > 1) return true;
        return false;
    }

    // Phones and the Capacitor app are excluded until memory and isolation are
    // verified on devices; so is anything without the features the engine needs.
    function eligible() {
        if (isPhoneOrApp()) return false;
        if (!window.isSecureContext) return false;
        if (typeof Worker !== 'function' || typeof BroadcastChannel !== 'function' || typeof WebAssembly !== 'object') return false;
        if (typeof navigator.deviceMemory === 'number' && navigator.deviceMemory < 4) return false;
        return true;
    }

    function token() {
        var bytes = new Uint8Array(16);
        crypto.getRandomValues(bytes);
        return Array.prototype.map.call(bytes, function (b) { return b.toString(16).padStart(2, '0'); }).join('');
    }

    function notify(msg) {
        if (typeof window.ffNotify === 'function') window.ffNotify(msg); else window.alert(msg);
    }

    function hand(op, kind) {
        var file = typeof window.ffOfficeSelectedFile === 'function' ? window.ffOfficeSelectedFile(kind) : null;
        if (!file) { notify('Please select a file first.'); return; }
        var h = token();
        var channel = new BroadcastChannel(CHANNEL);
        var timer = setTimeout(function () { channel.close(); }, WAIT_MS);
        channel.onmessage = function (event) {
            var msg = event.data || {};
            if (msg.type === 'ready' && msg.token === h) {
                channel.postMessage({ type: 'file', token: h, file: file, name: file.name, op: op });
                clearTimeout(timer);
                channel.close();
            }
        };
        // noopener: the isolated page is a separate browsing context group anyway.
        window.open('/on-device-office/?op=' + encodeURIComponent(op) + '&h=' + h, '_blank', 'noopener');
    }

    function init() {
        var blocks = document.querySelectorAll('[data-on-device-office]');
        if (!blocks.length) return;
        var ok = eligible();
        blocks.forEach(function (block) {
            var op = block.getAttribute('data-on-device-office');
            var kind = block.getAttribute('data-kind');
            block.hidden = !ok;
            var button = block.querySelector('button');
            if (button) button.addEventListener('click', function () { hand(op, kind); });
        });
        // On-device slide images are PNG only, so the option goes away for JPG.
        var fmt = document.getElementById('ppt-images-format');
        var imageBlock = document.querySelector('[data-on-device-office="ppt-to-images"]');
        if (fmt && imageBlock && ok) {
            var canDraw = typeof Map.prototype.getOrInsertComputed === 'function';
            var sync = function () { imageBlock.hidden = !canDraw || fmt.value !== 'png'; };
            fmt.addEventListener('change', sync);
            sync();
        }
    }

    window.ffOfficeEntry = { eligible: eligible };
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init); else init();
})();
