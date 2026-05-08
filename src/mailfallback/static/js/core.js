/* MFB — MailFallBack core scripts (loaded on every page) */

// Theme — apply from localStorage before paint to prevent flash
(function() {
    var saved = localStorage.getItem('mfb-theme');
    if (saved) document.documentElement.setAttribute('data-theme', saved);
})();

function toggleTheme() {
    var html = document.documentElement;
    var current = html.getAttribute('data-theme') || 'light';
    var next = current === 'dark' ? 'light' : 'dark';
    html.setAttribute('data-theme', next);
    localStorage.setItem('mfb-theme', next);
    if (typeof lucide !== 'undefined') lucide.createIcons();
    fetch('/api/preferences', {
        method: 'PATCH',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({theme: next})
    });
}

/* === Sidebar toggle === */
document.addEventListener('DOMContentLoaded', function() {
    var toggle = document.getElementById('menu-toggle');
    var sidebar = document.getElementById('sidebar');
    var overlay = document.getElementById('sidebar-overlay');
    if (!toggle || !sidebar) return;

    toggle.addEventListener('click', function() {
        sidebar.classList.toggle('open');
        overlay.classList.toggle('open');
    });
    overlay.addEventListener('click', function() {
        sidebar.classList.remove('open');
        overlay.classList.remove('open');
    });
    sidebar.addEventListener('click', function(e) {
        if (e.target.closest('a') && window.innerWidth <= 768) {
            sidebar.classList.remove('open');
            overlay.classList.remove('open');
        }
    });
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
    /* Lucide icons for HTMX-loaded content */
    document.body.addEventListener('htmx:afterSettle', function() {
        lucide.createIcons();
    });
});
