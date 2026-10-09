/* Desktop app shell: loaded only in the Windows desktop app (FF_LOCAL_ONLY), never on the website.
 *
 * 1. Links to other sites open in the person's normal browser instead of replacing the app.
 * 2. Shows an "update available" card and one announcement, from /api/desktop/feed (the local
 *    server fetches the feed; this page never contacts anything but itself).
 */
(function () {
  'use strict';

  function openExternal(url) {
    var api = window.pywebview && window.pywebview.api;
    if (api && api.open_external) {
      api.open_external(url);
    } else {
      window.open(url, '_blank', 'noopener');
    }
  }

  document.addEventListener('click', function (event) {
    var link = event.target && event.target.closest && event.target.closest('a[href]');
    if (!link) return;
    var target;
    try { target = new URL(link.href, location.href); } catch (e) { return; }
    if (!/^https?:$/.test(target.protocol) || target.origin === location.origin) return;
    event.preventDefault();
    openExternal(target.href);
  }, true);

  var DISMISS_KEY = 'ff.desktop.dismissed';

  function dismissed() {
    try { return JSON.parse(localStorage.getItem(DISMISS_KEY) || '[]'); } catch (e) { return []; }
  }

  function remember(key) {
    try {
      var list = dismissed();
      list.push(key);
      localStorage.setItem(DISMISS_KEY, JSON.stringify(list.slice(-50)));
    } catch (e) { /* private mode: it just shows again next launch */ }
  }

  function node(tag, className, text) {
    var el = document.createElement(tag);
    if (className) el.className = className;
    if (text) el.textContent = text;
    return el;
  }

  function injectStyles() {
    var css =
      '.ffd-stack{position:fixed;right:16px;bottom:16px;z-index:2147483000;display:flex;flex-direction:column;gap:10px;max-width:min(360px,calc(100vw - 32px))}' +
      '.ffd-card{background:var(--bg-panel,#fff);color:var(--text-primary,#111);border:1px solid var(--border,rgba(128,128,128,.35));border-radius:14px;padding:14px 16px;box-shadow:0 8px 28px rgba(0,0,0,.18);font:14px/1.45 var(--font-sans,system-ui,sans-serif)}' +
      '.ffd-title{font-weight:600;margin:0 22px 4px 0}' +
      '.ffd-text{margin:0 0 10px;opacity:.8}' +
      '.ffd-row{display:flex;gap:8px;align-items:center;flex-wrap:wrap}' +
      '.ffd-btn{border:0;border-radius:999px;padding:7px 14px;font:inherit;font-weight:600;cursor:pointer;background:var(--accent,#006FEE);color:#fff}' +
      '.ffd-link{background:none;border:0;padding:4px 2px;font:inherit;font-size:12px;cursor:pointer;color:inherit;opacity:.65;text-decoration:underline}' +
      '.ffd-x{position:absolute;top:8px;right:10px;background:none;border:0;font-size:18px;line-height:1;cursor:pointer;color:inherit;opacity:.55}' +
      '.ffd-card{position:relative}';
    var style = document.createElement('style');
    style.textContent = css;
    document.head.appendChild(style);
  }

  function card(stack, key, title, text, actions) {
    var el = node('div', 'ffd-card');
    el.setAttribute('role', 'status');
    var close = node('button', 'ffd-x', '×');
    close.setAttribute('aria-label', 'Dismiss');
    close.addEventListener('click', function () { remember(key); el.remove(); });
    el.appendChild(close);
    el.appendChild(node('p', 'ffd-title', title));
    if (text) el.appendChild(node('p', 'ffd-text', text));
    var row = node('div', 'ffd-row');
    actions.forEach(function (a) { row.appendChild(a); });
    el.appendChild(row);
    stack.appendChild(el);
    return el;
  }

  function render(data) {
    var seen = dismissed();
    var stack = node('div', 'ffd-stack');
    var any = false;

    if (data.update && seen.indexOf('update:' + data.update.latest) === -1) {
      var get = node('button', 'ffd-btn', 'Download update');
      get.addEventListener('click', function () { openExternal(data.update.url); });
      var off = node('button', 'ffd-link', 'Turn off update checks');
      var updateCard = card(stack, 'update:' + data.update.latest,
        'Version ' + data.update.latest + ' is available',
        data.update.notes || 'You have version ' + data.current + '.', [get, off]);
      off.addEventListener('click', function () {
        fetch('/api/desktop/feed/off', { method: 'POST' }).catch(function () {});
        updateCard.remove();
      });
      any = true;
    }

    var a = data.announcement;
    if (a && seen.indexOf('ann:' + a.id) === -1) {
      var actions = [];
      if (a.url) {
        var cta = node('button', 'ffd-btn', a.cta || 'Learn more');
        cta.addEventListener('click', function () { openExternal(a.url); });
        actions.push(cta);
      }
      card(stack, 'ann:' + a.id, a.title, a.text, actions);
      any = true;
    }

    if (any) {
      injectStyles();
      document.body.appendChild(stack);
    }
  }

  function start() {
    fetch('/api/desktop/feed')
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (data) { if (data && data.enabled) render(data); })
      .catch(function () { /* offline or blocked: say nothing */ });
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
  else start();
})();
