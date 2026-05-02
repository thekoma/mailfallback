# Provider-Aware Account Flow — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make account creation and editing adapt to the provider (OAuth, Known, Other) — hide irrelevant fields, add auth/re-auth for OAuth, handle OAuth failures gracefully, guard sync for unauthenticated accounts.

**Architecture:** Three provider categories (OAuth, Known, Other) drive form layout and behavior. No new DB columns — unauthenticated state is `auth_type=oauth2 AND credentials=NULL`. OAuth callbacks get error handling with flash messages. JS `autoDetectProvider()` triggers on blur instead of input.

**Tech Stack:** Python/FastAPI, Jinja2, HTMX, vanilla JS, SQLAlchemy, pytest

---

### Task 1: Add `is_authenticated` property to Account model

**Files:**
- Modify: `src/mailfallback/models.py:92-123`
- Test: `tests/test_models.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_models.py`:

```python
def test_account_is_authenticated_oauth_no_creds(db_session, default_store):
    account = Account(
        name="Test",
        imap_host="imap.gmail.com",
        imap_port=993,
        auth_type=AuthType.oauth2,
        credentials=None,
        store_id=default_store.id,
        maildir_path="/data/mailboxes/test-oauth",
    )
    db_session.add(account)
    db_session.commit()
    assert account.is_authenticated is False


def test_account_is_authenticated_oauth_with_creds(db_session, default_store):
    account = Account(
        name="Test",
        imap_host="imap.gmail.com",
        imap_port=993,
        auth_type=AuthType.oauth2,
        credentials="encrypted-token-data",
        store_id=default_store.id,
        maildir_path="/data/mailboxes/test-oauth2",
    )
    db_session.add(account)
    db_session.commit()
    assert account.is_authenticated is True


def test_account_is_authenticated_password(db_session, default_store):
    account = Account(
        name="Test",
        imap_host="imap.example.com",
        imap_port=993,
        auth_type=AuthType.app_password,
        credentials=None,
        store_id=default_store.id,
        maildir_path="/data/mailboxes/test-pass",
    )
    db_session.add(account)
    db_session.commit()
    assert account.is_authenticated is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_models.py::test_account_is_authenticated_oauth_no_creds -v`
Expected: FAIL with `AttributeError: 'Account' object has no attribute 'is_authenticated'`

- [ ] **Step 3: Add the property**

In `src/mailfallback/models.py`, add after line 123 (after the `store` relationship):

```python
    @property
    def is_authenticated(self) -> bool:
        if self.auth_type == AuthType.oauth2 and not self.credentials:
            return False
        return True
```

- [ ] **Step 4: Run all three tests**

Run: `uv run pytest tests/test_models.py -k "is_authenticated" -v`
Expected: 3 PASS

- [ ] **Step 5: Run full test suite**

Run: `uv run pytest tests/ -v`
Expected: 127 PASS

---

### Task 2: Sync guards — skip unauthenticated accounts

**Files:**
- Modify: `src/mailfallback/services/scheduler.py:24-27`
- Modify: `src/mailfallback/services/sync_worker.py:120-132`
- Modify: `src/mailfallback/services/scheduler.py:48`
- Test: `tests/test_sync_worker.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_sync_worker.py`:

```python
def test_sync_blocked_unauthenticated():
    db = MagicMock()
    job = MagicMock()
    job.id = "job-1"
    job.account_id = "acc-1"
    account = MagicMock()
    account.id = "acc-1"
    account.name = "Test"
    account.suspended = False
    account.migrating = False
    account.auth_type = MagicMock()
    account.auth_type.value = "oauth2"
    account.credentials = None
    account.is_authenticated = False
    account.owners = []
    db.query.return_value.filter.return_value.first.side_effect = [job, account]

    from mailfallback.services.sync_worker import execute_sync_job

    execute_sync_job(db, "job-1")

    assert job.status.value == "failed" or job.log == "Sync blocked: account not authenticated"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_sync_worker.py::test_sync_blocked_unauthenticated -v`
Expected: FAIL — no guard for unauthenticated exists yet

- [ ] **Step 3: Add guard in sync_worker.py**

In `src/mailfallback/services/sync_worker.py`, after the `account.suspended` block (after line 125), add:

```python
    if not account.is_authenticated:
        job.status = JobStatus.failed
        job.log = "Sync blocked: account not authenticated"
        job.completed_at = datetime.now(UTC)
        db.commit()
        return
```

- [ ] **Step 4: Add guard in scheduler.py `_run_scheduled_sync`**

In `src/mailfallback/services/scheduler.py`, after the `account.suspended` check (after line 26), add:

```python
        if not account.is_authenticated:
            logger.info("Skipping sync for %s — account not authenticated", account.name)
            return
```

- [ ] **Step 5: Filter unauthenticated from scheduler job list**

In `src/mailfallback/services/scheduler.py`, line 48, update the query to also filter by auth state. Since `is_authenticated` is a Python property (not a SQL column), we filter in the loop instead. After line 54, add:

```python
        if not account.is_authenticated:
            continue
```

- [ ] **Step 6: Run tests**

Run: `uv run pytest tests/test_sync_worker.py -v`
Expected: all PASS

Run: `uv run pytest tests/ -v`
Expected: all PASS

---

### Task 3: OAuth callback error handling

**Files:**
- Modify: `src/mailfallback/routers/auth.py:54-83` (Google callback)
- Modify: `src/mailfallback/routers/auth.py:94-124` (Microsoft callback)
- Test: `tests/test_auth.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_auth.py`:

```python
def test_google_oauth_callback_denied(client, db_session, default_store):
    """OAuth denied (error param) should redirect with flash error."""
    from mailfallback.services.account_service import create_account
    from mailfallback.services.user_service import create_user
    from mailfallback.models import UserRole

    user = create_user(db_session, "admin", "pass", UserRole.admin, store_id=default_store.id)
    account = create_account(
        db_session,
        name="Test",
        imap_host="imap.gmail.com",
        imap_port=993,
        auth_type="oauth2",
        store=default_store,
        provider="google",
    )

    # Login
    client.post("/api/auth/login", json={"username": "admin", "password": "pass"})  # pragma: allowlist secret

    # Simulate denied OAuth — error param, no code
    with client:
        client.cookies.set("session", client.cookies.get("session", ""))
        # Set session oauth_account_id
        resp = client.get(f"/auth/google/callback?error=access_denied", allow_redirects=False)
        assert resp.status_code in (303, 307, 302)

    # Account should still have no credentials
    db_session.refresh(account)
    assert account.credentials is None
```

- [ ] **Step 2: Rewrite Google callback with error handling**

Replace the Google callback in `src/mailfallback/routers/auth.py` (lines 54-83):

```python
@router.get("/auth/google/callback")
async def google_oauth_callback(
    request: Request,
    code: str = None,
    error: str = None,
    db: Session = Depends(get_db),
):
    account_id = request.session.pop("oauth_account_id", None)

    if error or not code:
        msg = f"OAuth was denied or failed: {error or 'no code received'}"
        request.session["flash_error"] = msg
        if account_id:
            return RedirectResponse(f"/accounts/{account_id}", status_code=303)
        return RedirectResponse("/", status_code=303)

    redirect_uri = str(request.url_for("google_oauth_callback"))
    try:
        token = await exchange_google_code(code, redirect_uri)
    except Exception:
        request.session["flash_error"] = "OAuth authentication failed. Please try again."
        if account_id:
            return RedirectResponse(f"/accounts/{account_id}", status_code=303)
        return RedirectResponse("/", status_code=303)

    if not account_id:
        raise HTTPException(status_code=400, detail="No account in session")

    from mailfallback.models import Account, AuthType

    account = db.query(Account).filter(Account.id == account_id).first()
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")

    token_data = json.dumps(
        {
            "access_token": token["access_token"],
            "refresh_token": token.get("refresh_token", ""),
            "token_type": token.get("token_type", "Bearer"),
        }
    )
    account.credentials = encrypt_credentials(token_data, settings.secret_key)
    account.auth_type = AuthType.oauth2
    db.commit()

    return RedirectResponse(f"/accounts/{account_id}")
```

- [ ] **Step 3: Rewrite Microsoft callback with same pattern**

Replace the Microsoft callback in `src/mailfallback/routers/auth.py` (lines 94-124):

```python
@router.get("/auth/microsoft/callback")
async def microsoft_oauth_callback(
    request: Request,
    code: str = None,
    error: str = None,
    db: Session = Depends(get_db),
):
    account_id = request.session.pop("oauth_account_id", None)

    if error or not code:
        msg = f"OAuth was denied or failed: {error or 'no code received'}"
        request.session["flash_error"] = msg
        if account_id:
            return RedirectResponse(f"/accounts/{account_id}", status_code=303)
        return RedirectResponse("/", status_code=303)

    redirect_uri = str(request.url_for("microsoft_oauth_callback"))
    try:
        token = await exchange_microsoft_code(code, redirect_uri)
    except Exception:
        request.session["flash_error"] = "OAuth authentication failed. Please try again."
        if account_id:
            return RedirectResponse(f"/accounts/{account_id}", status_code=303)
        return RedirectResponse("/", status_code=303)

    if not account_id:
        raise HTTPException(status_code=400, detail="No account in session")

    from mailfallback.models import Account, AuthType

    account = db.query(Account).filter(Account.id == account_id).first()
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")

    token_data = json.dumps(
        {
            "access_token": token["access_token"],
            "refresh_token": token.get("refresh_token", ""),
            "token_type": token.get("token_type", "Bearer"),
            "provider": "microsoft",
        }
    )
    account.credentials = encrypt_credentials(token_data, settings.secret_key)
    account.auth_type = AuthType.oauth2
    db.commit()

    return RedirectResponse(f"/accounts/{account_id}")
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_auth.py -v`
Expected: all PASS

Run: `uv run pytest tests/ -v`
Expected: all PASS

---

### Task 4: Flash error display + unauthenticated badge in account detail

**Files:**
- Modify: `src/mailfallback/routers/ui_accounts.py:186-220` (account_detail route)
- Modify: `src/mailfallback/templates/account_detail.html`

- [ ] **Step 1: Read flash error in `account_detail` route**

In `src/mailfallback/routers/ui_accounts.py`, in the `account_detail` function, add flash error reading. After `stores = ...` (line 207), add:

```python
    flash_error = request.session.pop("flash_error", None)
```

And add `"flash_error": flash_error` to the context dict (line 213-220).

- [ ] **Step 2: Add flash error display in template**

In `src/mailfallback/templates/account_detail.html`, after the `<h2>` header block and before the info table, add:

```html
{% if flash_error %}
<p class="alert-error"><i data-lucide="alert-circle" class="icon-md icon-inline"></i>{{ flash_error }}</p>
{% endif %}
```

- [ ] **Step 3: Add unauthenticated badge in status row**

In `src/mailfallback/templates/account_detail.html`, in the Status `<td>` (around line 100-114), add before the `{% if account.migrating %}` check:

```html
            {% if not account.is_authenticated %}
            <span class="badge badge-error"><i data-lucide="key-round" class="icon-sm"></i> Unauthenticated</span>
            {% elif account.migrating %}
```

(Change `{% if account.migrating %}` to `{% elif account.migrating %}`)

- [ ] **Step 4: Add Auth/Re-auth button in actions bar**

In `src/mailfallback/templates/account_detail.html`, in the actions div (around line 17-65), after the Sync/Stop button block and before the Hide/Show button, add:

```html
        {% if account.auth_type.value == "oauth2" %}
        <a href="/auth/{{ account.provider }}/start?account_id={{ account.id }}" class="icon-btn {% if not account.is_authenticated %}primary{% endif %}">
            <i data-lucide="key-round" class="icon-md"></i> {% if account.is_authenticated %}Re-authenticate{% else %}Authenticate{% endif %}
        </a>
        {% endif %}
```

- [ ] **Step 5: Run tests**

Run: `uv run pytest tests/ -v`
Expected: all PASS

---

### Task 5: Unauthenticated badge in dashboard table

**Files:**
- Modify: `src/mailfallback/templates/partials/accounts_table.html`

- [ ] **Step 1: Add unauthenticated badge**

In `src/mailfallback/templates/partials/accounts_table.html`, in the Status `<td>` (around line 58-74), add before `{% if account.migrating %}`:

```html
                {% if not account.is_authenticated %}
                <span class="badge badge-error"><i data-lucide="key-round" class="icon-sm"></i> Unauthenticated</span>
                {% elif account.migrating %}
```

(Change `{% if account.migrating %}` to `{% elif account.migrating %}`)

- [ ] **Step 2: Disable Sync Now in dropdown for unauthenticated**

In the dropdown menu (around line 91), add `not account.is_authenticated` to the disabled condition:

```html
                            <a class="dropdown-item {% if account.migrating or account.suspended or not account.is_authenticated %}dropdown-disabled{% endif %}"
                                {% if not account.migrating and not account.suspended and account.is_authenticated %}
```

- [ ] **Step 3: Run tests**

Run: `uv run pytest tests/ -v`
Expected: all PASS

---

### Task 6: Provider-aware edit form in account detail

**Files:**
- Modify: `src/mailfallback/templates/account_detail.html` (Basic Info details section)
- Modify: `src/mailfallback/routers/ui_accounts.py:223-274` (account_edit_submit)

- [ ] **Step 1: Replace Basic Info section with provider-aware template**

In `src/mailfallback/templates/account_detail.html`, replace the entire `<details>` for Basic Info (the one with `<summary>...Basic Info...`) with:

```html
        <details>
            <summary><i data-lucide="info" class="icon-md icon-inline"></i><strong>Connection Info</strong></summary>
            <div class="grid-2 mt-1">
                <div>
                    <label for="name">Account Name</label>
                    <input type="text" id="name" name="name" value="{{ account.name }}">
                </div>
                <div>
                    <label for="email_address">Email Address</label>
                    <input type="email" id="email_address" name="email_address" value="{{ account.email_address }}">
                </div>
                {% if account.provider in ("google", "microsoft", "yahoo", "icloud", "protonmail") %}
                <div>
                    <label>Provider</label>
                    <input type="text" value="{{ account.provider|capitalize }}" disabled>
                </div>
                {% endif %}
                {% if account.provider not in ("google", "microsoft", "yahoo", "icloud", "protonmail") %}
                <div>
                    <label for="imap_host">IMAP Host</label>
                    <input type="text" id="imap_host" name="imap_host" value="{{ account.imap_host }}">
                </div>
                <div>
                    <label for="imap_port">IMAP Port</label>
                    <input type="number" id="imap_port" name="imap_port" value="{{ account.imap_port }}">
                </div>
                <div>
                    <label for="tls_type">TLS Type</label>
                    <select id="tls_type" name="tls_type">
                        <option value="IMAPS" {% if account.tls_type == "IMAPS" %}selected{% endif %}>IMAPS (SSL)</option>
                        <option value="STARTTLS" {% if account.tls_type == "STARTTLS" %}selected{% endif %}>STARTTLS</option>
                        <option value="None" {% if account.tls_type == "None" %}selected{% endif %}>None (insecure)</option>
                    </select>
                </div>
                <div>
                    <label for="provider">Provider</label>
                    <select id="provider" name="provider">
                        <option value="google" {% if account.provider == "google" %}selected{% endif %}>Google</option>
                        <option value="microsoft" {% if account.provider == "microsoft" %}selected{% endif %}>Microsoft</option>
                        <option value="yahoo" {% if account.provider == "yahoo" %}selected{% endif %}>Yahoo</option>
                        <option value="icloud" {% if account.provider == "icloud" %}selected{% endif %}>iCloud</option>
                        <option value="protonmail" {% if account.provider == "protonmail" %}selected{% endif %}>Protonmail</option>
                        <option value="other" {% if account.provider == "other" %}selected{% endif %}>Other</option>
                    </select>
                </div>
                {% endif %}
                {% if account.auth_type.value != "oauth2" %}
                <div>
                    <label for="credentials">New Password (leave blank to keep)</label>
                    <input type="password" id="credentials" name="credentials">
                </div>
                {% else %}
                <div>
                    <p class="text-muted text-small mt-1">
                        <i data-lucide="info" class="icon-sm icon-inline"></i>
                        Authentication is managed via OAuth. Use the Re-authenticate button above.
                    </p>
                </div>
                {% endif %}
            </div>
        </details>
```

- [ ] **Step 2: Guard edit submit against IMAP fields for known providers**

In `src/mailfallback/routers/ui_accounts.py`, in `account_edit_submit`, after fetching the account (line 246-248), add provider filtering:

```python
    known_providers = {"google", "microsoft", "yahoo", "icloud", "protonmail"}
    if account.provider in known_providers:
        for key in ("imap_host", "imap_port", "tls_type", "provider"):
            updates.pop(key, None)
    if account.auth_type.value == "oauth2":
        updates.pop("credentials", None)
```

- [ ] **Step 3: Run tests**

Run: `uv run pytest tests/ -v`
Expected: all PASS

---

### Task 7: Provider-aware creation form

**Files:**
- Modify: `src/mailfallback/templates/account_form.html`
- Modify: `src/mailfallback/static/js/app.js`

- [ ] **Step 1: Change email field trigger from oninput to onblur**

In `src/mailfallback/templates/account_form.html`, line 24, change:

```html
                    oninput="autoDetectProvider()">
```
to:
```html
                    onblur="autoDetectProvider()">
```

- [ ] **Step 2: Add provider-category data attributes to form sections**

In `src/mailfallback/templates/account_form.html`, add `id="imap-section"` to the IMAP Connection `<details>` (line 37):

```html
    <details open id="imap-section">
```

Add `id="auth-section"` to the Authentication `<details>` (line 68):

```html
    <details open id="auth-section">
```

- [ ] **Step 3: Replace `toggleAuthFields` and `autoDetectProvider` in app.js**

In `src/mailfallback/static/js/app.js`, replace the `autoDetectProvider()` function (and remove the `_lastDomain` variable) with:

```javascript
function autoDetectProvider() {
    var email = document.getElementById('email_address').value;
    var domain = (email.split('@')[1] || '').toLowerCase();
    if (!domain) return;

    fetch('/api/sync/discover/' + encodeURIComponent(domain))
        .then(function(r) { return r.json(); })
        .then(function(data) {
            if (!data.ok) {
                updateFormForProvider('other', null, null);
                return;
            }
            document.getElementById('imap_host').value = data.host;
            document.getElementById('imap_port').value = data.port;
            document.getElementById('tls_type').value = data.tls;
            document.getElementById('provider').value = data.provider || 'other';
            _currentOauthProvider = data.oauth_provider || null;
            _providerName = data.name || domain;

            var nameField = document.getElementById('name');
            if (!nameField.value || nameField.value === nameField.defaultValue) {
                var local = email.split('@')[0] || '';
                var suggested = local.replace(/[._]/g, ' ').replace(/\b\w/g, function(c) { return c.toUpperCase(); });
                nameField.value = suggested + ' (' + data.name + ')';
            }

            updateFormForProvider(data.provider || 'other', data.auth_type, data);
        });
}
```

Replace `toggleAuthFields()` and `setFieldsReadonly()` with a single `updateFormForProvider()`:

```javascript
var _oauthProviders = ['google', 'microsoft'];
var _knownProviders = ['google', 'microsoft', 'yahoo', 'icloud', 'protonmail'];

function updateFormForProvider(provider, authType, data) {
    var isOauth = _oauthProviders.indexOf(provider) !== -1;
    var isKnown = _knownProviders.indexOf(provider) !== -1;

    var imapSection = document.getElementById('imap-section');
    var authSection = document.getElementById('auth-section');
    var passwordSection = document.getElementById('password-section');
    var oauthSection = document.getElementById('oauth-section');
    var authMechsSection = document.getElementById('auth-mechs-section');
    var authTypeSelect = document.getElementById('auth_type');
    var submitText = document.getElementById('submit-text');
    var warning = document.getElementById('provider-warning');

    /* IMAP section: hide for known providers */
    if (imapSection) {
        if (isKnown) {
            imapSection.open = false;
            imapSection.classList.add('hidden');
        } else {
            imapSection.classList.remove('hidden');
        }
    }

    /* Auth type: force oauth2 for OAuth providers, app_password for others */
    if (isOauth) {
        authTypeSelect.value = 'oauth2';
    } else {
        authTypeSelect.value = authType || 'app_password';
    }

    /* Password vs OAuth sections */
    passwordSection.classList.toggle('hidden', isOauth);
    oauthSection.classList.toggle('hidden', !isOauth);
    authMechsSection.classList.toggle('hidden', isKnown);

    /* Auth section: hide entirely for known non-OAuth (yahoo, icloud, protonmail) */
    if (authSection) {
        if (isKnown && !isOauth) {
            /* Show password inside auth section but hide the auth type selector and mechs */
            authSection.open = true;
            authSection.classList.remove('hidden');
            authTypeSelect.parentNode.classList.add('hidden');
        } else {
            authTypeSelect.parentNode.classList.remove('hidden');
            authSection.classList.remove('hidden');
        }
    }

    /* OAuth info boxes */
    var googleInfo = document.getElementById('oauth-google-info');
    var msInfo = document.getElementById('oauth-microsoft-info');
    if (googleInfo && msInfo) {
        var isMicrosoft = provider === 'microsoft';
        googleInfo.classList.toggle('hidden', !isOauth || isMicrosoft);
        msInfo.classList.toggle('hidden', !isOauth || !isMicrosoft);
    }

    /* Submit button text */
    if (submitText) {
        if (isOauth) {
            var pName = provider === 'microsoft' ? 'Microsoft' : 'Google';
            submitText.textContent = 'Authenticate with ' + pName;
        } else {
            submitText.textContent = 'Create Account';
        }
    }

    /* Provider warning / note */
    if (warning && data && data.note) {
        var logoHtml = _providerLogoHtml(data.provider || 'other');
        var noteEl = document.createElement('p');
        noteEl.className = 'mb-0';
        noteEl.textContent = data.note;
        warning.innerHTML = '<article class="error-box"><div class="flex gap-05 items-center">' + logoHtml + '</div></article>';
        warning.querySelector('.flex').appendChild(noteEl);
        warning.classList.remove('hidden');
        lucide.createIcons();
    } else if (warning) {
        warning.classList.add('hidden');
    }
}
```

- [ ] **Step 4: Remove old `_lastDomain`, `toggleAuthFields`, `setFieldsReadonly`**

In `app.js`, remove:
- `var _lastDomain = '';` (line 38)
- The entire `setFieldsReadonly()` function
- The entire `toggleAuthFields()` function

Update `initAccountForm()` to call `updateFormForProvider` on load if provider is already set:

```javascript
function initAccountForm() {
    updateSubfoldersHint();
    /* If editing with a known provider, update form state */
    var provider = document.getElementById('provider');
    if (provider && provider.value !== 'other') {
        var authType = document.getElementById('auth_type');
        updateFormForProvider(provider.value, authType ? authType.value : 'app_password', null);
    }
}
```

- [ ] **Step 5: Update `account_form.html` auth_type onchange**

In `src/mailfallback/templates/account_form.html`, line 73, change:

```html
                <select id="auth_type" name="auth_type" onchange="toggleAuthFields()">
```
to:
```html
                <select id="auth_type" name="auth_type" onchange="updateFormForProvider(document.getElementById('provider').value, this.value, null)">
```

- [ ] **Step 6: Run tests**

Run: `uv run pytest tests/ -v`
Expected: all PASS

---

### Task 8: Deploy and verify in browser

**Files:** None (testing only)

- [ ] **Step 1: Rebuild and deploy**

```bash
docker compose up -d --build mailfallback
```

Wait for health check: `curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/healthz`

- [ ] **Step 2: Test account creation with Microsoft email**

Navigate to `/accounts/new`, enter a `@live.it` email. On blur, verify:
- IMAP Connection section is hidden
- OAuth info box shows "Microsoft"
- Submit button says "Authenticate with Microsoft"

- [ ] **Step 3: Test account creation with Yahoo email**

Enter a `@yahoo.com` email. On blur, verify:
- IMAP Connection section is hidden
- Password field is visible
- Auth mechanisms hidden
- Submit button says "Create Account"

- [ ] **Step 4: Test account creation with custom domain**

Enter an email `@molotovguerrilla.org`. On blur, verify:
- IMAP fields auto-fill if autodetect finds settings
- All fields remain visible and editable

- [ ] **Step 5: Test existing OAuth account detail page**

Navigate to the Live account detail. Verify:
- "Re-authenticate" button visible in actions bar
- Edit form shows only Name + Email (no host/port/TLS/password)
- OAuth note shown instead of password field

- [ ] **Step 6: Test unauthenticated badge**

Create a test OAuth account without completing OAuth. Verify:
- Dashboard shows "Unauthenticated" badge
- Detail page shows "Unauthenticated" badge
- "Authenticate" button (primary style) is prominent
- Sync Now is disabled in dropdown
