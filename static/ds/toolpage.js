/* Tool pages (design system): keeps the page heading and breadcrumb in step with the selected tool card. */
(function () {
    'use strict';

    function parts(page) {
        return {
            title: page.querySelector('.ffp-title'),
            desc: page.querySelector('.ffp-desc'),
            crumb: page.querySelector('.ffp-crumb')
        };
    }

    // Remember the category defaults the first time a page is touched so they can be restored.
    function remember(page) {
        if (page.hasAttribute('data-ffp-ready')) return;
        var p = parts(page);
        page.setAttribute('data-ffp-ready', '1');
        page._ffpDefaults = {
            title: p.title ? p.title.textContent : '',
            desc: p.desc ? p.desc.textContent : '',
            crumb: p.crumb ? p.crumb.textContent : ''
        };
    }

    function apply(card) {
        var page = card.closest('.view.ffp');
        if (!page) return;
        remember(page);
        var h = card.querySelector('h4'), d = card.querySelector('p');
        if (!h) return;
        var p = parts(page);
        if (p.title) p.title.textContent = h.textContent;
        if (p.crumb) p.crumb.textContent = h.textContent;
        if (p.desc && d) p.desc.textContent = d.textContent;
    }

    function restoreDefaults() {
        Array.prototype.forEach.call(document.querySelectorAll('.view.ffp'), function (page) {
            var d = page._ffpDefaults;
            if (!d) return;
            var p = parts(page);
            if (p.title) p.title.textContent = d.title;
            if (p.desc) p.desc.textContent = d.desc;
            if (p.crumb) p.crumb.textContent = d.crumb;
        });
    }

    document.addEventListener('click', function (e) {
        var card = e.target.closest && e.target.closest('.action-card');
        if (card) apply(card);
    }, true);

    // Browser Back/Forward reopens a category without a selected tool, so the
    // heading must return to the category default instead of the previous tool.
    window.addEventListener('popstate', restoreDefaults);

    // A deep link (/?tool=image&op=heic-to-jpeg) marks its card before this runs.
    function boot() {
        Array.prototype.forEach.call(document.querySelectorAll('.view.ffp'), remember);
        var card = document.querySelector('.action-card.deep-link-target');
        if (card) apply(card);
    }
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot); else boot();
})();
