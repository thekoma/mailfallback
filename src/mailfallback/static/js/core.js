/* MFB — MailFallBack core scripts (loaded on every page) */

// Theme — settled before paint by theme-init.js; the toggle pins a choice.
function toggleTheme() {
    var html = document.documentElement;
    var current = html.getAttribute('data-theme') || 'light';
    var next = current === 'dark' ? 'light' : 'dark';
    html.removeAttribute('data-theme-auto');
    html.setAttribute('data-theme', next);
    try { localStorage.setItem('mfb-theme', next); } catch (e) { /* storage blocked */ }
    if (typeof lucide !== 'undefined') lucide.createIcons();
    fetch('/api/preferences', {
        method: 'PATCH',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({theme: next})
    });
}

document.addEventListener('DOMContentLoaded', function() {
    var t = document.getElementById('theme-toggle');
    if (t) t.addEventListener('click', toggleTheme);
});

/* === Sidebar toggle (drawer below 768px) === */
document.addEventListener('DOMContentLoaded', function() {
    var toggle = document.getElementById('menu-toggle');
    var sidebar = document.getElementById('sidebar');
    var overlay = document.getElementById('sidebar-overlay');
    if (!toggle || !sidebar) return;

    function setOpen(open) {
        sidebar.classList.toggle('open', open);
        overlay.classList.toggle('open', open);
        toggle.setAttribute('aria-expanded', open ? 'true' : 'false');
    }
    toggle.addEventListener('click', function() { setOpen(!sidebar.classList.contains('open')); });
    overlay.addEventListener('click', function() { setOpen(false); });
    sidebar.addEventListener('click', function(e) {
        if (e.target.closest('a') && window.innerWidth <= 768) setOpen(false);
    });
    document.addEventListener('keydown', function(e) {
        if (e.key === 'Escape' && sidebar.classList.contains('open')) { setOpen(false); toggle.focus(); }
    });
});

/* === System health indicator (admin top bar) ===
   The partial behind it re-renders every 5 s. An open panel must survive
   that refresh, so its state lives here, not in the swapped markup. */
var _healthOpen = false;

function _healthSync() {
    var btn = document.getElementById('health-toggle');
    var panel = document.getElementById('health-panel');
    if (!btn || !panel) return;
    btn.setAttribute('aria-expanded', _healthOpen ? 'true' : 'false');
    panel.hidden = !_healthOpen;
}

document.addEventListener('click', function(e) {
    var btn = e.target.closest('#health-toggle');
    if (btn) {
        _healthOpen = !_healthOpen;
        _healthSync();
        return;
    }
    if (_healthOpen && !e.target.closest('#health-panel')) {
        _healthOpen = false;
        _healthSync();
    }
});

document.addEventListener('keydown', function(e) {
    if (e.key === 'Escape' && _healthOpen) {
        _healthOpen = false;
        _healthSync();
        var btn = document.getElementById('health-toggle');
        if (btn) btn.focus();
    }
});

document.addEventListener('htmx:beforeSwap', function(e) {
    if (e.detail.target && e.detail.target.id === 'global-status-bar') {
        var active = document.activeElement;
        e.detail.target._healthHadFocus = !!(active && active.id === 'health-toggle');
    }
});

document.addEventListener('htmx:afterSwap', function(e) {
    if (!e.detail.target || e.detail.target.id !== 'global-status-bar') return;
    // A calm answer has no panel: forget the open state, or the panel would
    // pop open unprompted the next time something happens.
    if (!document.getElementById('health-panel')) _healthOpen = false;
    _healthSync();
    if (e.detail.target._healthHadFocus) {
        var btn = document.getElementById('health-toggle');
        if (btn) btn.focus();
    }
});

/* === Mailboxes list: poll faster while a sync runs === */
function startSyncPolling() {
    var wrap = document.getElementById('accounts-table-wrap');
    if (!wrap) return;
    wrap.setAttribute('hx-trigger', 'every 2s');
    htmx.process(wrap);
    wrap.addEventListener('htmx:afterSettle', function handler(e) {
        if (e.detail.xhr && e.detail.xhr.getResponseHeader('HX-Trigger') === 'sync-idle') {
            wrap.setAttribute('hx-trigger', 'every 30s');
            htmx.process(wrap);
            wrap.removeEventListener('htmx:afterSettle', handler);
        }
    });
}

/* A switch that navigates ([data-switch-href] + data-switch-href-on). */
document.addEventListener('change', function(e) {
    var sw = e.target.closest('[data-switch-href]');
    if (!sw) return;
    window.location.href = sw.checked ? sw.dataset.switchHrefOn : sw.dataset.switchHref;
});

/* === Shared utilities === */

function toggleRow(id) {
    document.getElementById(id).classList.toggle('hidden');
}

function checkDeleteConfirm(inputId, expected, buttonId) {
    document.getElementById(buttonId).disabled = document.getElementById(inputId).value !== expected;
}

/* === Dropdown menu system === */

var _activePortal = null;
var _activePortalBtn = null;

function closeDropdown() {
    if (_activePortal) {
        _activePortal.remove();
        if (_activePortalBtn) _activePortalBtn.setAttribute('aria-expanded', 'false');
        _activePortal = null;
        _activePortalBtn = null;
    }
}

function toggleDropdown(btn, event) {
    if (event) event.stopPropagation();
    if (_activePortalBtn === btn) { closeDropdown(); return; }
    closeDropdown();
    var template = btn.nextElementSibling;
    var rect = btn.getBoundingClientRect();
    var portal = template.cloneNode(true);
    portal.classList.remove('hidden');
    portal.style.position = 'fixed';
    portal.style.top = (rect.bottom + 4) + 'px';
    portal.style.right = Math.max(8, window.innerWidth - rect.right) + 'px';
    portal.style.left = 'auto';
    portal.style.zIndex = '9999';
    portal.setAttribute('role', 'menu');
    portal.querySelectorAll('.dropdown-item').forEach(function(item) { item.setAttribute('role', 'menuitem'); item.setAttribute('tabindex', '-1'); });
    document.body.appendChild(portal);
    _activePortal = portal;
    _activePortalBtn = btn;
    btn.setAttribute('aria-expanded', 'true');
    btn.setAttribute('aria-haspopup', 'menu');
    lucide.createIcons();
    htmx.process(portal);
    var first = portal.querySelector('.dropdown-item:not(.dropdown-disabled)');
    if (first) first.focus();
}

document.addEventListener('click', function(e) {
    if (_activePortal && !e.target.closest('.dropdown-menu') && !e.target.closest('.icon-btn')) {
        closeDropdown();
    }
});

document.addEventListener('keydown', function(e) {
    if (!_activePortal) return;
    if (e.key === 'Escape') { closeDropdown(); if (_activePortalBtn) _activePortalBtn.focus(); return; }
    if (e.key !== 'ArrowDown' && e.key !== 'ArrowUp') return;
    e.preventDefault();
    var items = Array.from(_activePortal.querySelectorAll('.dropdown-item:not(.dropdown-disabled)'));
    if (!items.length) return;
    var idx = items.indexOf(document.activeElement);
    if (e.key === 'ArrowDown') idx = (idx + 1) % items.length;
    else idx = (idx - 1 + items.length) % items.length;
    items[idx].focus();
});

/* === Generic show/toggle delegation (works with HTMX-swapped content) === */

document.addEventListener('click', function(e) {
    var btn = e.target.closest('[data-show-target]');
    if (!btn) return;
    var el = document.querySelector(btn.dataset.showTarget);
    if (el) el.classList.remove('hidden');
});

/* === Toast from HX-Trigger ({"notifyToast": {message, type}}) === */

document.body.addEventListener('notifyToast', function(e) {
    var d = (e && e.detail) || {};
    showToast(d.message || 'Done', d.type || 'success');
});

/* === Click-to-copy ([data-copy]) === */

document.addEventListener('click', function(e) {
    var el = e.target.closest('[data-copy]');
    if (!el) return;
    var text = el.dataset.copy || el.textContent;
    var flash = function() {
        el.classList.add('copied');
        showToast('Copied to clipboard', 'success');
        setTimeout(function() { el.classList.remove('copied'); }, 1200);
    };
    if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(flash, function() { showToast('Copy failed', 'error'); });
    } else {
        showToast('Copy not supported in this browser', 'error');
    }
});

document.addEventListener('change', function(e) {
    var input = e.target.closest('[data-toggle-target]');
    if (input) {
        var form = input.closest('form');
        var target = form ? form.querySelector(input.dataset.toggleTarget) : null;
        if (target) target.hidden = !input.checked;
        return;
    }
    if (e.target.id === 'dr_backend') {
        var s3 = document.getElementById('dr-s3');
        var local = document.getElementById('dr-local');
        if (s3) s3.hidden = e.target.value !== 's3';
        if (local) local.hidden = e.target.value !== 'local';
    }
});

/* === Log modal === */

function showLogModal(btn) {
    var modal = document.getElementById('log-modal');
    document.getElementById('log-modal-body').textContent = btn.dataset.log;
    modal.showModal();
    lucide.createIcons();
}

/* === DOMContentLoaded — shared init === */

document.addEventListener('DOMContentLoaded', function() {
    /* Accordion: one section open per level, reliable animation replay */
    document.querySelectorAll('.content details').forEach(function(det) {
        det.addEventListener('toggle', function() {
            if (!this.open) return;
            var siblings = Array.from(this.parentNode.children).filter(function(el) {
                return el.tagName === 'DETAILS' && el !== det;
            });
            siblings.forEach(function(s) { s.open = false; });
            var content = this.querySelector(':scope > :not(summary)');
            if (content) {
                content.style.animation = 'none';
                content.offsetHeight;
                content.style.animation = '';
            }
        });
    });
    /* Lucide icons: initial render + HTMX-loaded content */
    lucide.createIcons();
    document.body.addEventListener('htmx:afterSettle', function() {
        lucide.createIcons();
    });
});

// Toasts — flash messages arrive via data attributes on <body>
function showToast(msg, type) {
    var c = document.getElementById('toast-container');
    var t = document.createElement('div');
    t.className = 'toast toast-' + (type || 'error');
    t.textContent = msg;
    t.onclick = function() { t.classList.add('toast-out'); setTimeout(function() { t.remove(); }, 300); };
    c.appendChild(t);
    setTimeout(function() { if (t.parentNode) { t.classList.add('toast-out'); setTimeout(function() { t.remove(); }, 300); } }, 5000);
}

document.addEventListener('DOMContentLoaded', function() {
    var b = document.body;
    if (b.dataset.flashSuccess) showToast(b.dataset.flashSuccess, 'success');
    if (b.dataset.flashError) showToast(b.dataset.flashError, 'error');
    document.querySelectorAll('[data-toast-error]').forEach(function(el) {
        showToast(el.dataset.toastError, 'error');
    });
});

/* === Empty date fields print their locale mask in the secondary ink ===
   A date input can't be styled on "no value" from CSS alone; keep a class
   current so "dd/mm/yyyy" reads as a hint, not as a date. */
(function () {
    function sync(el) { el.classList.toggle('is-empty', !el.value); }
    function scan(root) {
        (root || document).querySelectorAll('input[type="date"]').forEach(sync);
    }
    document.addEventListener('DOMContentLoaded', function () { scan(); });
    document.addEventListener('input', function (e) {
        if (e.target.matches && e.target.matches('input[type="date"]')) sync(e.target);
    });
    document.addEventListener('change', function (e) {
        if (e.target.matches && e.target.matches('input[type="date"]')) sync(e.target);
    });
    document.addEventListener('htmx:afterSwap', function (e) { scan(e.target); });
})();

/* === Declarative event wiring (no inline handlers in templates) ===
   Templates name a global function and its arguments in data attributes;
   one delegated listener per event type calls it.
     data-click="fn"  data-change="fn"  data-input="fn"  data-blur="fn"
     data-submit="fn"           (a false return cancels the submit)
     data-enter="fn"            (keydown Enter on the element, default prevented)
     data-args='["a", 1]'       arguments; "$el" = the element, "$event" = the
                                event, "$data:name" = the element's data-name
     data-prevent               preventDefault on click (was "return false")
     data-close-dropdown        close the open row menu first
     data-confirm="text"        on a form: confirm before submit; on a
                                clickable: confirm before acting
     data-submit-form="sel"     click submits the form matched by sel
     data-autosubmit            change submits the closest form
     data-mirror="sel"          change copies this value into sel
     data-reveal="sel"          change shows sel (inside the closest form)
                                while this checkbox is checked
     data-close-dialog="sel"    click closes that <dialog>
     data-backdrop-close        click on the <dialog> backdrop closes it
     data-toast / data-toast-kind  on any element: show a toast on load */
(function () {
    function args(el, e) {
        var raw = el.getAttribute('data-args');
        var list = raw ? JSON.parse(raw) : ['$el'];
        return list.map(function (a) {
            if (a === '$el') return el;
            if (a === '$event') return e;
            if (typeof a === 'string' && a.indexOf('$data:') === 0) return el.dataset[a.slice(6)];
            return a;
        });
    }
    function call(el, name, e) {
        var fn = window[name];
        if (typeof fn !== 'function') { console.warn('No handler', name); return undefined; }
        return fn.apply(el, args(el, e));
    }
    document.addEventListener('click', function (e) {
        var dlg = e.target.closest('dialog[data-backdrop-close]');
        if (dlg && e.target === dlg) { dlg.close(); return; }
        var el = e.target.closest('[data-click], [data-submit-form], [data-close-dialog], a[data-confirm], button[data-confirm]:not([type="submit"])');
        if (!el) return;
        if (el.hasAttribute('data-prevent')) e.preventDefault();
        if (el.hasAttribute('data-close-dropdown') && typeof closeDropdown === 'function') closeDropdown();
        if (el.dataset.confirm && !el.closest('form[data-confirm]') && !confirm(el.dataset.confirm)) { e.preventDefault(); return; }
        if (el.dataset.closeDialog) { var d = document.querySelector(el.dataset.closeDialog); if (d) d.close(); }
        if (el.dataset.submitForm) { var f = document.querySelector(el.dataset.submitForm); if (f) f.submit(); }
        if (el.dataset.click) call(el, el.dataset.click, e);
    });
    document.addEventListener('click', function (e) {
        // A submit button carrying its own confirm (outside a confirming form).
        var b = e.target.closest('button[type="submit"][data-confirm], input[type="submit"][data-confirm]');
        if (b && !confirm(b.dataset.confirm)) e.preventDefault();
    });
    document.addEventListener('submit', function (e) {
        var f = e.target;
        if (f.matches('form[data-confirm]') && !confirm(f.dataset.confirm)) { e.preventDefault(); return; }
        if (f.dataset.submit && call(f, f.dataset.submit, e) === false) e.preventDefault();
    });
    document.addEventListener('change', function (e) {
        var el = e.target;
        if (!el.closest) return;
        if (el.hasAttribute('data-autosubmit') && el.form) el.form.submit();
        if (el.dataset.mirror) { var m = document.querySelector(el.dataset.mirror); if (m) m.value = el.value; }
        if (el.dataset.reveal) {
            var scope = el.closest('form') || document;
            scope.querySelectorAll(el.dataset.reveal).forEach(function (t) { t.hidden = !el.checked; });
        }
        if (el.dataset.change) call(el, el.dataset.change, e);
    });
    document.addEventListener('input', function (e) {
        var el = e.target;
        if (el.dataset && el.dataset.input) call(el, el.dataset.input, e);
    });
    document.addEventListener('focusout', function (e) {
        var el = e.target;
        if (el.dataset && el.dataset.blur) call(el, el.dataset.blur, e);
    });
    document.addEventListener('keydown', function (e) {
        var el = e.target;
        if (e.key === 'Enter' && el.dataset && el.dataset.enter) { e.preventDefault(); call(el, el.dataset.enter, e); }
    });
    document.addEventListener('DOMContentLoaded', function () {
        document.querySelectorAll('[data-toast]').forEach(function (el) {
            if (typeof showToast === 'function') showToast(el.dataset.toast, el.dataset.toastKind || 'success');
        });
    });
})();

/* === System health: a failed poll says so ===
   htmx swaps nothing on a 5xx or a network error, so without this the bar
   would keep showing the last answer — or "Checking status…" forever. */
(function () {
    function unavailable(e) {
        var bar = e.detail && e.detail.elt;
        if (!bar || bar.id !== 'global-status-bar') return;
        var span = document.createElement('span');
        span.className = 'health-calm health-unavailable';
        span.setAttribute('data-health', 'unavailable');
        var mark = document.createElement('span');
        mark.className = 'mark mark-muted';
        mark.setAttribute('aria-hidden', 'true');
        span.appendChild(mark);
        span.appendChild(document.createTextNode(' Status unavailable'));
        bar.replaceChildren(span);
        _healthOpen = false;
    }
    document.addEventListener('htmx:responseError', unavailable);
    document.addEventListener('htmx:sendError', unavailable);
})();

/* === htmx request follow-ups, declared in data attributes ===
   One delegated pair of listeners replaces every hx-on script:
     data-after-reload               reload the page when the request ends
     data-after-go="/url"            navigate there when it ends
     data-after-call="fn"            call a global function when it ends
     data-after-refresh-panel="url"  re-render #hero-panel from url
     data-after-require-json         only follow up when the response is JSON
                                     (a failed request answers HTML or nothing)
     data-after-warning-toast        toast the JSON response's "warning"
     data-query-if="#checkbox"  +  data-query="k=v"
                                     append k=v to the request while it's checked
     data-busy-label="Deleting…"     disable and relabel (spinner + text) on send */
document.addEventListener('htmx:configRequest', function (e) {
    var el = e.detail.elt;
    if (!el || !el.dataset || !el.dataset.queryIf) return;
    var box = document.querySelector(el.dataset.queryIf);
    if (!box || !box.checked) return;
    // Into the URL itself, as the old inline script did — the endpoint reads
    // it from the query string whatever the verb.
    var q = el.dataset.query || '';
    if (q) e.detail.path += (e.detail.path.indexOf('?') === -1 ? '?' : '&') + q;
});
document.addEventListener('htmx:beforeRequest', function (e) {
    var el = e.detail.elt;
    if (!el || !el.dataset || !el.dataset.busyLabel) return;
    el.disabled = true;
    var icon = document.createElement('i');
    icon.setAttribute('data-lucide', 'loader');
    icon.className = 'icon-md spin';
    el.replaceChildren(icon, document.createTextNode(' ' + el.dataset.busyLabel));
    if (typeof lucide !== 'undefined') lucide.createIcons();
});
document.addEventListener('htmx:afterRequest', function (e) {
    var el = e.detail.elt;
    if (!el || !el.dataset) return;
    var d = el.dataset;
    if (!('afterReload' in d || 'afterGo' in d || 'afterCall' in d || 'afterRefreshPanel' in d)) return;
    var body = null;
    if ('afterRequireJson' in d || 'afterWarningToast' in d) {
        try { body = JSON.parse(e.detail.xhr.responseText); } catch (err) { body = null; }
        if ('afterRequireJson' in d && body === null) return;
    }
    if ('afterWarningToast' in d && body && body.warning) showToast(String(body.warning), 'error');
    if (d.afterRefreshPanel) htmx.ajax('GET', d.afterRefreshPanel, {target: '#hero-panel', swap: 'outerHTML'});
    if (d.afterCall && typeof window[d.afterCall] === 'function') window[d.afterCall]();
    if ('afterReload' in d) location.reload();
    if (d.afterGo) window.location = d.afterGo;
});
