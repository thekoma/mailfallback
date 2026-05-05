/* MFB — MailFallBack scripts */

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

/* === Account form (account_form.html) === */

var _discoverAbort = null;

function _setAuthMode(mode) {
    var form = document.getElementById('account-form');
    if (!form) return;
    form.dataset.auth = mode;

    var passwordSection = document.getElementById('password-section');
    var oauthSwitch = document.getElementById('oauth-switch');
    var authType = document.getElementById('auth_type');
    var credField = document.getElementById('credentials');
    var submitText = document.getElementById('submit-text');

    if (mode === 'password') {
        if (passwordSection) passwordSection.classList.remove('hidden');
        if (oauthSwitch) oauthSwitch.classList.add('hidden');
        if (authType) authType.value = 'app_password';
        if (credField) credField.required = true;
        if (submitText) submitText.textContent = 'Add Account';
    } else {
        if (passwordSection) passwordSection.classList.add('hidden');
        if (oauthSwitch) oauthSwitch.classList.remove('hidden');
        if (authType) authType.value = 'oauth2';
        if (credField) credField.required = false;
        var pName = mode === 'microsoft' ? 'Microsoft' : 'Google';
        if (submitText) submitText.textContent = 'Continue with ' + pName;
    }
}

function autoDetectProvider() {
    var email = document.getElementById('email_address').value;
    var domain = (email.split('@')[1] || '').toLowerCase();
    if (!domain) return;

    if (_discoverAbort) _discoverAbort.abort();
    _discoverAbort = new AbortController();

    var nameField = document.getElementById('name');
    if (!nameField.value) {
        nameField.value = email.split('@')[0].replace(/[._]/g, ' ')
            .replace(/\b\w/g, function(c) { return c.toUpperCase(); });
    }

    fetch('/api/sync/discover/' + encodeURIComponent(domain), {signal: _discoverAbort.signal})
        .then(function(r) { return r.json(); })
        .then(function(data) {
            if (!data.ok) {
                _updateDisclosureLabel('confirm');
                return;
            }
            document.getElementById('imap_host').value = data.host;
            document.getElementById('imap_port').value = data.port;
            document.getElementById('tls_type').value = data.tls;
            document.getElementById('provider').value = data.provider || 'other';

            _updateDisclosureLabel('detected', data.host, data.port, data.tls);

            var oauthProv = data.oauth_provider;
            if (oauthProv && window._oauthAvailable && window._oauthAvailable[oauthProv]) {
                _setAuthMode(oauthProv);
            } else {
                _setAuthMode('password');
            }
        })
        .catch(function(e) {
            if (e.name !== 'AbortError') _updateDisclosureLabel('confirm');
        });
}

function _updateDisclosureLabel(state, host, port, tls) {
    var details = document.getElementById('server-settings');
    var label = document.getElementById('server-settings-label');
    if (!details || !label) return;

    var icon = '<i data-lucide="server" class="icon-md icon-inline"></i>';
    if (state === 'detected') {
        var secLabel = tls === 'IMAPS' ? 'SSL/TLS' : tls;
        label.innerHTML = icon + '<strong>Server settings</strong>' +
            ' <span class="text-muted text-small">— auto-detected: ' +
            host + ':' + port + ' (' + secLabel + ')</span>';
        details.open = false;
    } else if (state === 'confirm') {
        label.innerHTML = icon +
            '<strong>Server settings</strong>' +
            ' <span class="text-small sync-syncing">— please confirm</span>';
        details.open = true;
    } else if (state === 'modified') {
        label.innerHTML = icon +
            '<strong>Server settings</strong>' +
            ' <span class="text-small">— modified</span>';
        var testLink = document.getElementById('test-without-saving');
        if (testLink) testLink.classList.remove('hidden');
    }
    lucide.createIcons();
}

function _onServerFieldEdit() {
    var details = document.getElementById('server-settings');
    if (details && details.open) {
        _updateDisclosureLabel('modified');
    }
}

function testWithoutSaving() {
    var result = document.getElementById('check-result');
    result.textContent = 'Testing...';
    result.className = 'text-small text-muted';

    var payload = {
        imap_host: document.getElementById('imap_host').value,
        imap_port: parseInt(document.getElementById('imap_port').value),
        tls_type: document.getElementById('tls_type').value,
    };
    var emailEl = document.getElementById('email_address');
    var passEl = document.getElementById('credentials');
    if (emailEl && emailEl.value) payload.username = emailEl.value;
    if (passEl && passEl.value) payload.password = passEl.value;

    fetch('/api/sync/test-connection', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(payload)
    }).then(function(r) { return r.json(); }).then(function(data) {
        if (data.ok) {
            var msg = 'Connection OK';
            var cls = 'text-small alert-success';
            if (data.login_ok === true) msg += ' — Login successful';
            else if (data.login_ok === false) {
                msg += ' — Login failed: ' + data.login_message;
                cls = 'text-small alert-error';
            }
            result.textContent = msg;
            result.className = cls;
        } else {
            result.textContent = data.message;
            result.className = 'text-small alert-error';
        }
    }).catch(function(e) {
        result.textContent = 'Error: ' + e;
        result.className = 'text-small alert-error';
    });
}

function checkParameters() { testWithoutSaving(); }

function _renderFormError(msg) {
    var el = document.getElementById('form-errors');
    if (!el) return;
    el.innerHTML = '<div class="error-box"><p class="mb-0">' +
        '<i data-lucide="alert-circle" class="icon-md icon-inline"></i>' +
        msg + '</p></div>';
    lucide.createIcons();
    el.scrollIntoView({behavior: 'smooth', block: 'center'});
}

function _clearFormError() {
    var el = document.getElementById('form-errors');
    if (el) el.innerHTML = '';
}

function _classifyError(data) {
    var raw = (data.login_message || data.message || '').toLowerCase();
    if (data.login_ok === false || raw.indexOf('authenticationfailed') !== -1 ||
        raw.indexOf('login failed') !== -1 || raw.indexOf('invalid credentials') !== -1) {
        var email = document.getElementById('email_address').value;
        return 'We couldn\'t sign in to <strong>' + email + '</strong>. ' +
            'Many providers — including Gmail, iCloud, Yahoo, and Outlook — require an ' +
            '<strong>app password</strong> instead of your normal one. ' +
            '<a href="https://support.google.com/accounts/answer/185833" target="_blank">' +
            'How to create an app password →</a>';
    }
    if (raw.indexOf('errno') !== -1 || raw.indexOf('refused') !== -1 ||
        raw.indexOf('unreachable') !== -1 || raw.indexOf('getaddrinfo') !== -1) {
        var host = document.getElementById('imap_host').value;
        return 'Couldn\'t reach <strong>' + host + '</strong>. Check the hostname or your ' +
            'network connection. <a href="#" onclick="document.getElementById(\'server-settings\')' +
            '.open=true;return false">Edit server settings</a>';
    }
    return 'Something went wrong: ' + (data.message || data.login_message || 'Unknown error') +
        '. <a href="#" onclick="document.getElementById(\'server-settings\').open=true;' +
        'return false">Edit server settings</a>';
}

function _createAccountAndRedirect(payload, oauthProvider) {
    var btn = document.getElementById('submit-btn');
    var btnText = document.getElementById('submit-text');

    return fetch('/api/accounts', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(payload)
    }).then(function(r) {
        return r.json().then(function(d) { return {status: r.status, data: d}; });
    }).then(function(resp) {
        if (resp.status >= 400) {
            _renderFormError('Couldn\'t create the account. ' +
                (resp.data.detail || 'Please try again.'));
            btn.disabled = false;
            btnText.textContent = 'Add Account';
            return;
        }
        if (oauthProvider) {
            window.location.href = '/auth/' + oauthProvider +
                '/start?account_id=' + resp.data.id;
        } else {
            window.location.href = '/accounts/' + resp.data.id;
        }
    });
}

function handleAccountSubmit(e) {
    e.preventDefault();
    _clearFormError();

    var form = document.getElementById('account-form');
    var btn = document.getElementById('submit-btn');
    var btnText = document.getElementById('submit-text');
    var authMode = form.dataset.auth || 'password';
    btn.disabled = true;

    var storeEl = document.getElementById('store_id');
    var basePayload = {
        name: document.getElementById('name').value,
        email_address: document.getElementById('email_address').value,
        imap_host: document.getElementById('imap_host').value,
        imap_port: parseInt(document.getElementById('imap_port').value),
        tls_type: document.getElementById('tls_type').value,
        provider: document.getElementById('provider').value,
    };
    if (storeEl) basePayload.store_id = storeEl.value;

    if (authMode !== 'password') {
        btnText.textContent = 'Connecting…';
        basePayload.auth_type = 'oauth2';
        _createAccountAndRedirect(basePayload, authMode).catch(function(err) {
            _renderFormError('Something went wrong: ' + err.message);
            btn.disabled = false;
            btnText.textContent = 'Add Account';
        });
        return false;
    }

    btnText.textContent = 'Connecting to your mail server…';
    basePayload.auth_type = 'app_password';
    basePayload.credentials = document.getElementById('credentials').value;

    fetch('/api/sync/test-connection', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
            imap_host: basePayload.imap_host,
            imap_port: basePayload.imap_port,
            tls_type: basePayload.tls_type,
            username: basePayload.email_address,
            password: basePayload.credentials,
        })
    }).then(function(r) { return r.json(); }).then(function(data) {
        if (!data.ok || data.login_ok === false) {
            _renderFormError(_classifyError(data));
            btn.disabled = false;
            btnText.textContent = 'Add Account';
            return;
        }
        btnText.textContent = 'Creating account…';
        return _createAccountAndRedirect(basePayload, null);
    }).catch(function(err) {
        _renderFormError('Something went wrong: ' + err.message);
        btn.disabled = false;
        btnText.textContent = 'Add Account';
    });

    return false;
}

function togglePasswordVisibility() {
    var input = document.getElementById('credentials');
    var icon = document.getElementById('pass-toggle-icon');
    if (input.type === 'password') {
        input.type = 'text';
        icon.setAttribute('data-lucide', 'eye-off');
    } else {
        input.type = 'password';
        icon.setAttribute('data-lucide', 'eye');
    }
    lucide.createIcons();
}

function updateSubfoldersHint() {
    var sel = document.getElementById('subfolders');
    var hint = document.getElementById('subfolders-hint');
    if (!sel || !hint) return;
    var hints = {
        'Verbatim': 'Recommended. Folders as real directories.',
        'Maildir++': 'Does NOT support dots in IMAP folder names.',
        'Legacy': 'Traditional format.'
    };
    hint.textContent = hints[sel.value] || '';
}

/* === Account form init === */

function initAccountForm() {
    ['imap_host', 'imap_port', 'tls_type'].forEach(function(id) {
        var el = document.getElementById(id);
        if (el) el.addEventListener('input', _onServerFieldEdit);
    });
}

/* === Account detail (account_detail.html) === */

function testConnectionFromHero(host, port, tls) {
    var result = document.createElement('p');
    result.className = 'text-small text-muted mt-025';
    result.textContent = 'Testing connection…';
    var btn = event.target.closest('.icon-btn');
    btn.parentNode.appendChild(result);
    btn.disabled = true;

    fetch('/api/sync/test-connection', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({imap_host: host, imap_port: port, tls_type: tls})
    }).then(function(r) { return r.json(); }).then(function(data) {
        if (data.ok) {
            result.className = 'text-small alert-success mt-025';
            result.textContent = 'Connection OK — server is reachable';
        } else {
            result.className = 'text-small alert-error mt-025';
            result.textContent = data.message || 'Connection failed';
        }
        btn.disabled = false;
    }).catch(function(e) {
        result.className = 'text-small alert-error mt-025';
        result.textContent = 'Error: ' + e;
        btn.disabled = false;
    });
}

function initAccountDetail() {
    var page = document.getElementById('account-page');
    if (!page) return;
    var accountId = page.dataset.accountId;

    /* Diagnostic mode: auto-open History when last sync failed */
    var heroPanel = document.getElementById('hero-panel');
    if (heroPanel && (heroPanel.classList.contains('hero-error') || heroPanel.classList.contains('hero-sign-in-needed'))) {
        var historySection = document.getElementById('history-section');
        if (historySection) {
            var historyDetails = historySection.closest('details');
            if (historyDetails) historyDetails.open = true;
        }
    }

    /* Restore section open/close state from localStorage */
    var storageKey = 'mfb-sections-' + accountId;
    var savedState = {};
    try { savedState = JSON.parse(localStorage.getItem(storageKey) || '{}'); } catch(e) {}

    document.querySelectorAll('#account-page details[id]').forEach(function(det) {
        if (savedState[det.id] !== undefined) {
            det.open = savedState[det.id];
        }
        det.addEventListener('toggle', function() {
            try {
                var state = JSON.parse(localStorage.getItem(storageKey) || '{}');
                state[det.id] = det.open;
                localStorage.setItem(storageKey, JSON.stringify(state));
            } catch(e) {}
        });
    });

    /* sync-finished event: refresh history */
    document.body.addEventListener('sync-finished', function() {
        htmx.ajax('GET', '/accounts/' + accountId + '/partials/history', '#history-section');
    });
}

/* === Stats toggle (partials/account_stats.html) === */

function initStatsToggle(statsDetails) {
    statsDetails.addEventListener('toggle', function() {
        document.getElementById('stats-inline').style.display = this.open ? 'none' : '';
    });
}

/* === DOMContentLoaded — page-specific init === */

document.addEventListener('DOMContentLoaded', function() {
    /* Account form */
    if (document.getElementById('account-form')) {
        initAccountForm();
    }
    /* Account detail */
    if (document.getElementById('account-page')) {
        initAccountDetail();
    }
    /* Stats toggle */
    var statsDetails = document.getElementById('stats-details');
    if (statsDetails) {
        initStatsToggle(statsDetails);
    }
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

function showLogModal(btn) {
    var modal = document.getElementById('log-modal');
    document.getElementById('log-modal-body').textContent = btn.dataset.log;
    modal.showModal();
    lucide.createIcons();
}
