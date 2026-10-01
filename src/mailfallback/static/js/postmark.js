/* MFB — postmarks.
   1. Localise: the server prints a postmark in UTC ("21:04 UTC"); here it is
      re-set in the viewer's own zone, same "DD MON YYYY" / "HH:MM" form.
   2. Stamp down: when a re-rendered postmark carries a NEWER copy time than
      the one it replaces (a sync just finished), it strikes once — the one
      authored motion in the interface. Off under prefers-reduced-motion. */
(function () {
    'use strict';
    var MONTHS = ['JAN', 'FEB', 'MAR', 'APR', 'MAY', 'JUN', 'JUL', 'AUG', 'SEP', 'OCT', 'NOV', 'DEC'];
    var seen = Object.create(null);  // postmark key -> last copy time (ms)

    function pad(n) { return (n < 10 ? '0' : '') + n; }

    function localise(scope) {
        (scope || document).querySelectorAll('time.postmark-when, time.postmark-line').forEach(function (t) {
            var d = new Date(t.getAttribute('datetime'));
            if (isNaN(d.getTime())) return;
            var date = t.querySelector('.postmark-date');
            var time = t.querySelector('.postmark-time');
            if (date) date.textContent = pad(d.getDate()) + ' ' + MONTHS[d.getMonth()] + ' ' + d.getFullYear();
            if (time) time.textContent = pad(d.getHours()) + ':' + pad(d.getMinutes());
            t.title = d.toLocaleString();
        });
    }

    function keyOf(pm) {
        var holder = pm.closest('[data-postmark-key]');
        return holder ? holder.getAttribute('data-postmark-key') : null;
    }

    function remember(scope) {
        (scope || document).querySelectorAll('.postmark[data-postmark]').forEach(function (pm) {
            var key = keyOf(pm);
            if (!key) return;
            var ms = Date.parse(pm.getAttribute('data-postmark')) || 0;
            var before = seen[key];
            seen[key] = ms;
            if (before === undefined || ms <= before) return;
            if (window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
            pm.classList.remove('is-stamping');
            void pm.offsetWidth;  // restart the animation on a reused node
            pm.classList.add('is-stamping');
            pm.addEventListener('animationend', function done() {
                pm.classList.remove('is-stamping');
                pm.removeEventListener('animationend', done);
            });
        });
    }

    document.addEventListener('DOMContentLoaded', function () {
        localise(document);
        remember(document);
    });
    // outerHTML swaps replace the node the event names, so look everywhere:
    // a page holds a handful of postmarks at most.
    document.addEventListener('htmx:afterSettle', function () {
        localise(document);
        remember(document);
    });
})();
