/* Desktop app shell: loaded only in the Windows desktop app (FF_LOCAL_ONLY), never on the website.
 *
 * 1. Links to other sites open in the person's normal browser instead of replacing the app.
 * 2. Shows an update bell next to the theme toggle (with a popover) and one announcement, from /api/desktop/feed (the local
 *    server fetches the feed; this page never contacts anything but itself).
 * 3. Offers the optional Office engine (about 247 MB, not in the installer) and shows its download progress.
 * 4. Provides an on-demand "Check for updates" option with offline support.
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
      '.ffd-card{position:relative}' +
      '.ffd-bar{width:100%;height:8px;margin:0 0 10px;accent-color:var(--accent,#006FEE)}' +
      '.ffd-bell{position:relative;flex:none}' +
      '.ffd-bell[hidden]{display:none}' +
      '.ffd-dot{position:absolute;top:9px;right:10px;width:9px;height:9px;border-radius:50%;background:#f31260;border:2px solid var(--bg-panel,#fff);box-sizing:content-box}' +
      '.ffd-pop{position:fixed;z-index:2147483001;width:min(320px,calc(100vw - 24px))}' +
      '.ffd-pop .ffd-card{box-shadow:0 12px 36px rgba(0,0,0,.28)}';
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

  var BELL_SVG = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9"/><path d="M10.3 21a1.94 1.94 0 0 0 3.4 0"/></svg>';
  var pendingUpdate = null;
  var bellBtn = null;
  var popover = null;

  function closeUpdatePopover() {
    if (popover) { popover.remove(); popover = null; }
    if (bellBtn) bellBtn.setAttribute('aria-expanded', 'false');
    document.removeEventListener('mousedown', onOutside, true);
    document.removeEventListener('keydown', onEscape, true);
  }

  function onOutside(e) {
    if (popover && !popover.contains(e.target) && !(bellBtn && bellBtn.contains(e.target))) closeUpdatePopover();
  }

  function onEscape(e) {
    if (e.key === 'Escape') { closeUpdatePopover(); if (bellBtn) bellBtn.focus(); }
  }

  function openUpdatePopover() {
    if (!pendingUpdate || !bellBtn || popover) return;
    var info = pendingUpdate;
    var get = node('button', 'ffd-btn', 'Download update');
    get.addEventListener('click', function () { openExternal(info.url); });
    var off = node('button', 'ffd-link', 'Turn off update checks');
    off.addEventListener('click', function () {
      fetch('/api/desktop/feed/off', { method: 'POST' }).catch(function () {});
      closeUpdatePopover();
      setUpdate(null);
    });
    popover = node('div', 'ffd-pop');
    popover.setAttribute('role', 'dialog');
    popover.setAttribute('aria-label', 'Update available');
    var body = card(popover, null, 'Version ' + info.latest + ' is available',
      info.notes || 'You have version ' + (info.current || '0.0.0') + '.', [get, off]);
    body.removeAttribute('role');
    injectStyles();
    document.body.appendChild(popover);
    var r = bellBtn.getBoundingClientRect();
    popover.style.top = Math.round(r.bottom + 8) + 'px';
    popover.style.right = Math.max(12, Math.round(window.innerWidth - r.right)) + 'px';
    bellBtn.setAttribute('aria-expanded', 'true');
    remember('update:' + info.latest);
    var dot = bellBtn.querySelector('.ffd-dot');
    if (dot) dot.hidden = true;
    document.addEventListener('mousedown', onOutside, true);
    document.addEventListener('keydown', onEscape, true);
    get.focus();
  }

  // The bell sits right beside the theme toggle on every page; it stays as long as an update is waiting.
  function ensureBell() {
    if (bellBtn && document.body.contains(bellBtn)) return bellBtn;
    var theme = document.getElementById('theme-toggle-btn');
    if (!theme || !theme.parentNode) return null;
    injectStyles();
    bellBtn = node('button', 'ff-iconbtn ffd-bell');
    bellBtn.type = 'button';
    bellBtn.id = 'ffd-update-btn';
    bellBtn.hidden = true;
    bellBtn.setAttribute('aria-haspopup', 'dialog');
    bellBtn.setAttribute('aria-expanded', 'false');
    bellBtn.innerHTML = BELL_SVG;
    bellBtn.appendChild(node('span', 'ffd-dot'));
    bellBtn.addEventListener('click', function () {
      if (popover) closeUpdatePopover(); else openUpdatePopover();
    });
    theme.parentNode.insertBefore(bellBtn, theme);
    return bellBtn;
  }

  function setUpdate(update, current) {
    pendingUpdate = update ? { latest: update.latest, url: update.url, notes: update.notes, current: current } : null;
    var btn = ensureBell();
    if (!btn) return;
    if (!pendingUpdate) {
      closeUpdatePopover();
      btn.hidden = true;
      return;
    }
    btn.hidden = false;
    btn.title = 'Version ' + update.latest + ' is available';
    btn.setAttribute('aria-label', 'Update available: version ' + update.latest);
    btn.querySelector('.ffd-dot').hidden = dismissed().indexOf('update:' + update.latest) !== -1;
  }

  // The Office engine is not part of the installer. After one yes, the local server downloads it in the
  // background (scripts/desktop_engine.py); this only asks, shows progress and reports the result.
  var engineCard = null;
  var enginePoll = null;

  function megabytes(n) { return Math.round(n / 1e6) + ' MB'; }

  function closeEngineCard() {
    if (engineCard) { engineCard.remove(); engineCard = null; }
  }

  function engineShow(title, text, actions, progress) {
    closeEngineCard();
    engineCard = card(getStack(), null, title, text, actions);
    engineCard.querySelector('.ffd-x').addEventListener('click', function () { engineCard = null; });
    if (progress) {
      var bar = node('progress', 'ffd-bar');
      bar.max = progress.total;
      bar.value = progress.done;
      engineCard.insertBefore(bar, engineCard.querySelector('.ffd-row'));
    }
  }

  function engineCall(path) {
    return fetch('/api/desktop/engine' + path, path ? { method: 'POST' } : undefined)
      .then(function (r) { return r.ok ? r.json() : null; })
      .catch(function () { return null; });
  }

  function startEngineDownload() {
    engineCall('/download').then(handleEngine);
  }

  function handleEngine(st) {
    if (enginePoll) { clearTimeout(enginePoll); enginePoll = null; }
    if (!st) return;
    var size = megabytes(st.total || 0);
    if (st.state === 'downloading') {
      engineShow('Downloading the Office engine', megabytes(st.done) + ' of ' + size + '. You can keep working; it continues in the background.', [], st);
      enginePoll = setTimeout(function () { engineCall('').then(handleEngine); }, 1500);
    } else if (st.state === 'ready') {
      var wasShown = !!engineCard;
      closeEngineCard();
      if (wasShown) card(getStack(), null, 'Office engine ready', 'Word, Excel and PowerPoint to PDF now work offline.', [], 6000);
    } else if (st.state === 'error') {
      var retry = node('button', 'ffd-btn', 'Try again');
      retry.addEventListener('click', startEngineDownload);
      engineShow('Could not download the Office engine', st.error || 'Check your connection and try again.', [retry]);
    } else if (st.state === 'missing') {
      if (st.choice === 'accepted') { startEngineDownload(); return; }
      if (st.choice === 'declined') return;
      var yes = node('button', 'ffd-btn', 'Download (' + size + ')');
      yes.addEventListener('click', startEngineDownload);
      var never = node('button', 'ffd-link', "Don't ask again");
      never.addEventListener('click', function () { closeEngineCard(); engineCall('/decline'); });
      engineShow('Add the Office engine?',
        'Word, Excel and PowerPoint to PDF run on your computer with an extra ' + size + ' engine that is not in the installer. ' +
        'It downloads once from forgefiles.org (nothing about your files is sent).', [yes, never]);
    }
  }

  function checkEngine() {
    engineCall('').then(handleEngine); // asks at most once per launch; closing the card just hides it
  }

  function render(data, manual) {
    var seen = dismissed();
    var stack = getStack();

    if (data.update) {
      setUpdate(data.update, data.current);
      if (manual) openUpdatePopover();
    } else {
      setUpdate(null);
      if (manual) {
        card(stack, null, 'Up to date', 'Forge Files Desktop (v' + (data.current || '0.0.0') + ') is the latest release. Website changes reach the desktop app in its next release.', [], 6000);
      }
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
          setUpdate(null);
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
    checkEngine();
    // A window can stay open for days; look again now and then (the server caches the feed for 6 hours).
    setInterval(function () { checkUpdates(false); }, 60 * 60 * 1000);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start);
  } else {
    start();
  }
})();
