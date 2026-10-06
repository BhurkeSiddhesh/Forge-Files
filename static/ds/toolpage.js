/* Tool pages (design system): keeps the page heading and breadcrumb in step with the selected tool card. */
(function () {
    'use strict';

    function apply(card) {
        var page = card.closest('.view.ffp');
        if (!page) return;
        var h = card.querySelector('h4'), p = card.querySelector('p');
        if (!h) return;
        var title = page.querySelector('.ffp-title'), desc = page.querySelector('.ffp-desc'), crumb = page.querySelector('.ffp-crumb');
        if (title) title.textContent = h.textContent;
        if (crumb) crumb.textContent = h.textContent;
        if (desc && p) desc.textContent = p.textContent;
    }

    document.addEventListener('click', function (e) {
        var card = e.target.closest && e.target.closest('.action-card');
        if (card) apply(card);
    }, true);

    // A deep link (/?tool=image&op=heic-to-jpeg) marks its card before this runs.
    function boot() {
        var card = document.querySelector('.action-card.deep-link-target');
        if (card) apply(card);
    }
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot); else boot();
})();
