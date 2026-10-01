/* MFB — theme before first paint (loaded blocking in <head>).
   Order: the user's saved preference (the server's data-theme, copied from
   their profile at login) > on logged-out pages only, a choice made in this
   browser (localStorage) > the OS (prefers-color-scheme). The saved preference must win: a
   stale localStorage value from another session or another user on the same
   browser would otherwise override it forever.
   The CSS tokens already follow prefers-color-scheme on their own; this
   only pins the attribute so every [data-theme] rule agrees with them. */
(function () {
    var root = document.documentElement;
    if (root.getAttribute('data-theme')) return;
    var saved = null;
    // Logged-in pages (data-auth) never read it: the toggle saves the choice
    // to the user's profile, and a key left by another user on this browser
    // must not leak into a profile that never chose a theme.
    if (!root.hasAttribute('data-auth')) {
        try { saved = localStorage.getItem('mfb-theme'); } catch (e) { /* storage blocked */ }
    }
    if (saved === 'light' || saved === 'dark') {
        root.setAttribute('data-theme', saved);
        return;
    }
    var mq = window.matchMedia ? window.matchMedia('(prefers-color-scheme: dark)') : null;
    root.setAttribute('data-theme', mq && mq.matches ? 'dark' : 'light');
    root.setAttribute('data-theme-auto', '');
    if (mq && mq.addEventListener) {
        mq.addEventListener('change', function (e) {
            if (root.hasAttribute('data-theme-auto')) {
                root.setAttribute('data-theme', e.matches ? 'dark' : 'light');
            }
        });
    }
})();
