/* Desktop app shell: loaded only in the Windows desktop app (FF_LOCAL_ONLY), never on the website.
 *
 * 1. Links to other sites open in the person's normal browser instead of replacing the app.
 * 2. Shows an "update available" card and one announcement, from /api/desktop/feed (the local
 *    server fetches the feed; this page never contacts anything but itself).
 * 3. Provides an on-demand "Check for updates" option with offline support.
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

  var stylesInjected = false;
  function injectStyles() {
    if (stylesInjected) return;
    stylesInjected = true;
    var css =
      '.ffd-stack{position:fixed;right:16px;bottom:16px;z-index:2147483000;display:flex;flex-direction:column;gap:10px;max-width:min(360px,calc(100vw - 32px))}' +
      '.ffd-card{background:var(--bg-panel,#fff);color:var(--text-primary,#111);border:1px solid var(--border,rgba(128,128,128,.35));border-radius:14px;padding:14px 16px;box-shadow:0 8px 28px rgba(0,0,0,.18);font:14px/1.45 var(--font-sans,system-ui,sans-serif)}' +
      '.ffd-title{font-weight:600;margin:0 22px 4px 0}' +
      '.ffd-text{margin:0 0 10px;opacity:.8}' +
      '.ffd-row{display:flex;gap:8px;align-items:center;flex-wrap:wrap}' +
      '.ffd-btn{border:0;border-radius:999px;padding:7px 14px;font:inherit;font-weight:600;cursor:pointer;background:var(--accent,#006FEE);color:#fff}' +
      '.ffd-link{background:none;border:0;padding:4px 2px;font:inherit;font-size:12px;cursor:pointer;color:inherit;opacity:.65;text-decoration:underline}' +
      '.ffd-x{position:absolute;top:8px;right:10px;background:none;border:0;font-size:18px;line-height:1;cursor:pointer;color:inherit;opacity:.55}' +
      '.ffd-footer-item{display:inline-flex;align-items:center;gap:6px;font-size:0.8125rem;margin-left:8px;opacity:.85}' +
      '.ffd-footer-btn{background:none;border:0;padding:0;font:inherit;color:var(--accent,#006FEE);cursor:pointer;text-decoration:underline}' +
      '.ffd-card{position:relative}';
    var style = document.createElement('style');
    style.textContent = css;
    document.head.appendChild(style);
  }

  function getStack() {
    injectStyles();
    var stack = document.querySelector('.ffd-stack');
    if (!stack) {
      stack = node('div', 'ffd-stack');
      document.body.appendChild(stack);
    }
    return stack;
  }

  function card(stack, key, title, text, actions, autoDismissMs) {
    var el = node('div', 'ffd-card');
    el.setAttribute('role', 'status');
    var close = node('button', 'ffd-x', '×');
    close.setAttribute('aria-label', 'Dismiss');
    close.addEventListener('click', function () {
      if (key) remember(key);
      el.remove();
    });
    el.appendChild(close);
    el.appendChild(node('p', 'ffd-title', title));
    if (text) el.appendChild(node('p', 'ffd-text', text));
    if (actions && actions.length) {
      var row = node('div', 'ffd-row');
      actions.forEach(function (a) { row.appendChild(a); });
      el.appendChild(row);
    }
    stack.appendChild(el);
    if (autoDismissMs) {
      setTimeout(function () {
        if (el.parentNode) el.remove();
      }, autoDismissMs);
    }
    return el;
  }

  function render(data, manual) {
    var seen = dismissed();
    var stack = getStack();

    if (data.update && (manual || seen.indexOf('update:' + data.update.latest) === -1)) {
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
    } else if (manual && !data.update) {
      card(stack, null, 'Up to date', 'Forge Files Desktop (v' + (data.current || '0.0.0') + ') is the latest version.', [], 5000);
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
    }
  }

  function checkUpdates(manual) {
    var stack = manual ? getStack() : null;
    var checkingToast = null;
    if (manual) {
      checkingToast = card(stack, null, 'Checking for updates...', 'Connecting to Forge Files update feed...', []);
    }
    var url = '/api/desktop/feed' + (manual ? '?force=1' : '');
    fetch(url)
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (data) {
        if (checkingToast) checkingToast.remove();
        if (!data) {
          if (manual) card(getStack(), null, 'Could not check for updates', 'You are working offline.', [], 5000);
          return;
        }
        if (!data.enabled) {
          if (manual) {
            var onBtn = node('button', 'ffd-btn', 'Turn on update checks');
            var offCard = card(getStack(), null, 'Update checks are off', 'Automatic update checks are disabled for this device.', [onBtn]);
            onBtn.addEventListener('click', function () {
              fetch('/api/desktop/feed/on', { method: 'POST' })
                .then(function () {
                  offCard.remove();
                  checkUpdates(true);
                })
                .catch(function () {});
            });
          }
          return;
        }
        render(data, manual);
      })
      .catch(function () {
        if (checkingToast) checkingToast.remove();
        if (manual) {
          card(getStack(), null, 'Could not check for updates', 'You are working offline.', [], 5000);
        }
      });
  }

  function injectFooterControls() {
    var currentVer = window.FF_APP_VERSION || '0.0.0';
    var footerNote = document.querySelector('footer.site-footer p.note') || document.querySelector('footer.site-footer');
    if (!footerNote || document.getElementById('ffd-footer-ctrl')) return;

    var span = node('span', 'ffd-footer-item');
    span.id = 'ffd-footer-ctrl';
    span.appendChild(document.createTextNode('• Forge Files Desktop v' + currentVer + ' • '));

    var btn = node('button', 'ffd-footer-btn', 'Check for updates');
    btn.type = 'button';
    btn.setAttribute('aria-label', 'Check for Forge Files Desktop updates');
    btn.addEventListener('click', function (e) {
      e.preventDefault();
      checkUpdates(true);
    });
    span.appendChild(btn);
    footerNote.appendChild(span);
  }

  window.ffCheckDesktopUpdates = function () {
    checkUpdates(true);
  };

  function start() {
    injectStyles();
    injectFooterControls();
    checkUpdates(false);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start);
  } else {
    start();
  }
})();
