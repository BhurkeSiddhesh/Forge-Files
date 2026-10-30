/* URL Shortener UI. Posts to /api/url/shorten and shows a copyable short link. */
(function () {
    'use strict';

    var SHORTEN_PATH = '/api/url/shorten';

    function $(id) {
        return document.getElementById(id);
    }

    function api(path) {
        return (typeof window.apiUrl === 'function') ? window.apiUrl(path) : path;
    }

    function setStatus(el, text, isError) {
        if (!el) return;
        el.textContent = text || '';
        el.classList.toggle('error', !!isError);
    }

    function openArea() {
        var area = $('url-shortener-area');
        var card = $('url-shortener-btn');
        if (!area) return;
        area.classList.remove('hidden');
        if (typeof window.ffSelectActionCard === 'function' && card) {
            window.ffSelectActionCard(card);
        } else if (card) {
            card.classList.add('selected');
        }
        var input = $('url-shortener-input');
        if (input) {
            setTimeout(function () { input.focus(); }, 0);
        }
    }

    function showResult(shortUrl) {
        var box = $('url-shortener-result');
        var out = $('url-shortener-output');
        if (!box || !out) return;
        out.value = shortUrl;
        box.hidden = false;
        box.classList.remove('hidden');
    }

    function hideResult() {
        var box = $('url-shortener-result');
        var out = $('url-shortener-output');
        if (out) out.value = '';
        if (box) {
            box.hidden = true;
            box.classList.add('hidden');
        }
    }

    async function shorten() {
        var input = $('url-shortener-input');
        var status = $('url-shortener-status');
        var btn = $('url-shortener-submit');
        var url = (input && input.value || '').trim();
        // A failed attempt must not leave the previous link copyable next to its error.
        hideResult();
        if (!url) {
            setStatus(status, 'Paste an http or https URL first.', true);
            if (input) input.focus();
            return;
        }
        setStatus(status, 'Creating short link…', false);
        if (btn) btn.disabled = true;
        try {
            // Sending the URL to the server needs the person's consent first, like every
            // other server operation; ffServerFetch answers 499 if they decline.
            var init = {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                credentials: 'same-origin',
                body: JSON.stringify({ url: url })
            };
            var resp = (typeof window.ffServerFetch === 'function')
                ? await window.ffServerFetch(SHORTEN_PATH, init)
                : new Response(JSON.stringify({ detail: 'Cancelled. Your URL was not sent.', declined: true }), { status: 499 });
            var data = null;
            try { data = await resp.json(); } catch (e) { data = null; }
            if (!resp.ok) {
                var detail = (data && (data.detail || data.message)) || ('Could not shorten URL (' + resp.status + ').');
                if (Array.isArray(detail)) {
                    detail = detail.map(function (d) { return d.msg || JSON.stringify(d); }).join(' ');
                }
                setStatus(status, String(detail), true);
                return;
            }
            var shortUrl = data && (data.short_url || data.short_path);
            if (!shortUrl) {
                setStatus(status, 'Unexpected response from the server.', true);
                return;
            }
            showResult(shortUrl);
            var note = data.message || 'Short link created.';
            if (String(shortUrl).length >= url.length) {
                note = 'Short link created, but your URL was already as short as it gets.';
            }
            setStatus(status, note, false);
        } catch (err) {
            setStatus(status, 'Network error. Try again.', true);
        } finally {
            if (btn) btn.disabled = false;
        }
    }

    async function copyResult() {
        var out = $('url-shortener-output');
        var status = $('url-shortener-status');
        if (!out || !out.value) return;
        try {
            if (navigator.clipboard && navigator.clipboard.writeText) {
                await navigator.clipboard.writeText(out.value);
            } else {
                out.select();
                document.execCommand('copy');
            }
            setStatus(status, 'Copied to clipboard.', false);
        } catch (e) {
            setStatus(status, 'Could not copy automatically — select the link and copy it.', true);
        }
    }

    function init() {
        var root = $('url-shortener-page');
        if (!root || root.dataset.ffUrlShortenerBound) return;
        root.dataset.ffUrlShortenerBound = '1';

        var card = $('url-shortener-btn');
        if (card) {
            card.addEventListener('click', openArea);
            card.addEventListener('keydown', function (e) {
                if (e.key === 'Enter' || e.key === ' ') {
                    e.preventDefault();
                    openArea();
                }
            });
        }
        var submit = $('url-shortener-submit');
        if (submit) submit.addEventListener('click', function () { shorten(); });
        var input = $('url-shortener-input');
        if (input) {
            input.addEventListener('keydown', function (e) {
                if (e.key === 'Enter') {
                    e.preventDefault();
                    shorten();
                }
            });
        }
        var copy = $('url-shortener-copy');
        if (copy) copy.addEventListener('click', function () { copyResult(); });
    }

    if (document.getElementById('url-shortener-page') || document.readyState !== 'loading') {
        init();
    } else {
        document.addEventListener('DOMContentLoaded', init);
    }
})();
