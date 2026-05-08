/* MFB — Account form scripts (account_form.html) */

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

function _buildLabelNodes(suffix, suffixClass) {
    var icon = document.createElement('i');
    icon.setAttribute('data-lucide', 'server');
    icon.className = 'icon-md icon-inline';
    var strong = document.createElement('strong');
    strong.textContent = 'Server settings';
    var span = document.createElement('span');
    span.className = suffixClass;
    span.textContent = suffix;
    return [icon, document.createTextNode(' '), strong, document.createTextNode(' '), span];
}

function _updateDisclosureLabel(state, host, port, tls) {
    var details = document.getElementById('server-settings');
    var label = document.getElementById('server-settings-label');
    if (!details || !label) return;

    label.innerHTML = '';
    if (state === 'detected') {
        var secLabel = tls === 'IMAPS' ? 'SSL/TLS' : tls;
        var suffix = '— auto-detected: ' + host + ':' + port + ' (' + secLabel + ')';
        _buildLabelNodes(suffix, 'text-muted text-small').forEach(function(n) { label.appendChild(n); });
        details.open = false;
    } else if (state === 'confirm') {
        _buildLabelNodes('— please confirm', 'text-small sync-syncing').forEach(function(n) { label.appendChild(n); });
        details.open = true;
    } else if (state === 'modified') {
        _buildLabelNodes('— modified', 'text-small').forEach(function(n) { label.appendChild(n); });
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

function _renderFormError(nodes) {
    var el = document.getElementById('form-errors');
    if (!el) return;
    el.innerHTML = '';
    var box = document.createElement('div');
    box.className = 'error-box';
    var p = document.createElement('p');
    p.className = 'mb-0';
    var icon = document.createElement('i');
    icon.setAttribute('data-lucide', 'alert-circle');
    icon.className = 'icon-md icon-inline';
    p.appendChild(icon);
    if (typeof nodes === 'string') {
        p.appendChild(document.createTextNode(nodes));
    } else {
        nodes.forEach(function(n) { p.appendChild(n); });
    }
    box.appendChild(p);
    el.appendChild(box);
    lucide.createIcons();
    el.scrollIntoView({behavior: 'smooth', block: 'center'});
}

function _clearFormError() {
    var el = document.getElementById('form-errors');
    if (el) el.innerHTML = '';
}

function _editSettingsLink() {
    var a = document.createElement('a');
    a.href = '#';
    a.textContent = 'Edit server settings';
    a.addEventListener('click', function(e) {
        e.preventDefault();
        document.getElementById('server-settings').open = true;
    });
    return a;
}

function _classifyError(data) {
    var raw = (data.login_message || data.message || '').toLowerCase();
    if (data.login_ok === false || raw.indexOf('authenticationfailed') !== -1 ||
        raw.indexOf('login failed') !== -1 || raw.indexOf('invalid credentials') !== -1) {
        var email = document.getElementById('email_address').value;
        var b1 = document.createElement('strong');
        b1.textContent = email;
        var b2 = document.createElement('strong');
        b2.textContent = 'app password';
        var a = document.createElement('a');
        a.href = 'https://support.google.com/accounts/answer/185833';
        a.target = '_blank';
        a.textContent = 'How to create an app password →';
        return [
            document.createTextNode('We couldn’t sign in to '), b1,
            document.createTextNode('. Many providers — including Gmail, iCloud, Yahoo, and Outlook — require an '),
            b2, document.createTextNode(' instead of your normal one. '), a
        ];
    }
    if (raw.indexOf('errno') !== -1 || raw.indexOf('refused') !== -1 ||
        raw.indexOf('unreachable') !== -1 || raw.indexOf('getaddrinfo') !== -1) {
        var host = document.getElementById('imap_host').value;
        var bh = document.createElement('strong');
        bh.textContent = host;
        return [
            document.createTextNode('Couldn’t reach '), bh,
            document.createTextNode('. Check the hostname or your network connection. '),
            _editSettingsLink()
        ];
    }
    return [
        document.createTextNode('Something went wrong: ' + (data.message || data.login_message || 'Unknown error') + '. '),
        _editSettingsLink()
    ];
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

document.addEventListener('DOMContentLoaded', function() {
    if (document.getElementById('account-form')) {
        ['imap_host', 'imap_port', 'tls_type'].forEach(function(id) {
            var el = document.getElementById(id);
            if (el) el.addEventListener('input', _onServerFieldEdit);
        });
    }
});
