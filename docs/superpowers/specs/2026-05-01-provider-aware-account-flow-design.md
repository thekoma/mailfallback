# Provider-Aware Account Flow

## Problem

1. **OAuth failure creates broken accounts**: When OAuth authentication fails (Microsoft/Google), the account is already committed to the database with `credentials=NULL`. No error is shown, no cleanup happens.
2. **Form ignores provider context**: The creation form and edit form show all fields (host, port, TLS, password) regardless of provider. For known providers these are fixed values — exposing them is confusing and error-prone.
3. **No re-auth mechanism**: Once an OAuth account is created, there's no way to re-authenticate from the UI if the token expires or was never obtained.
4. **Autodetect triggers too early**: `autoDetectProvider()` fires on every keystroke via `oninput`. It should fire once when the user finishes typing the email.

## Provider Categories

All providers fall into three categories based on their auth and IMAP configuration:

| Category | Providers | Auth | IMAP config | Form fields |
|----------|-----------|------|-------------|-------------|
| **OAuth** | google, microsoft | oauth2 | Fixed, auto-configured | Name + Email only |
| **Known** | yahoo, icloud, protonmail | app_password | Fixed, auto-configured | Name + Email + Password |
| **Other** | other (autodetect or manual) | app_password | User-configurable | All fields |

Source of truth for provider → category mapping: `WELL_KNOWN_PROVIDERS` in `provider_discovery.py`. The `auth_type` field in each entry determines whether it's OAuth or Known.

Users who need to override IMAP settings for a known provider should use "other" instead.

## Design

### 1. Account Creation Form (`account_form.html`)

**Autodetect trigger change**: Replace `oninput="autoDetectProvider()"` on the email field with `onblur="autoDetectProvider()"`. The function fires once when the user leaves the field with a complete email address.

**Provider-aware field visibility**: When `autoDetectProvider()` returns a result, the form adapts based on the provider category:

- **OAuth providers** (google, microsoft):
  - Hide: IMAP Connection section, Password field, Auth Mechanisms
  - Show: OAuth info box ("You will authenticate with Microsoft/Google")
  - Submit button text: "Authenticate with {Provider}"

- **Known providers** (yahoo, icloud, protonmail):
  - Hide: IMAP Connection section (host/port/TLS/check button), Auth Mechanisms
  - Show: Password field
  - Submit button text: "Create Account"

- **Other** (autodetect or manual):
  - Show: All fields (IMAP Connection, Password, Auth Mechanisms)
  - IMAP fields are auto-filled if autodetect succeeded, but remain editable
  - Submit button text: "Create Account"

The JS function `toggleAuthFields()` is replaced by a broader `updateFormForProvider()` that handles all three categories based on the provider value and auth_type returned by the discovery API.

### 2. Account State: "Unauthenticated"

No new database column. An account is unauthenticated when:
```
auth_type == "oauth2" AND credentials IS NULL
```

**Property on Account model**: Add `@property` `is_authenticated` that returns `True` unless the above condition is met.

**Dashboard (`accounts_table.html`)**: Show badge "Unauthenticated" (same style as "Suspended") when `not account.is_authenticated`.

**Detail page (`account_detail.html`)**: Show badge "Unauthenticated" in the status row.

### 3. Auth / Re-auth Button

**In the actions bar** of `account_detail.html`, for OAuth accounts (`auth_type == "oauth2"`):

- If `credentials IS NULL`: Show **"Authenticate"** button (style: `icon-btn primary`, icon: `key-round`). This is the most prominent action.
- If `credentials IS NOT NULL`: Show **"Re-authenticate"** button (style: `icon-btn`, icon: `refresh-cw`).

Both buttons link to `/auth/{provider}/start?account_id={id}`.

Non-OAuth accounts: no auth button shown (password is edited in the form).

### 4. OAuth Callback Error Handling

Wrap token exchange in `google_oauth_callback` and `microsoft_oauth_callback` with try/except:

```python
try:
    token = await exchange_google_code(code, redirect_uri)
except Exception:
    request.session["flash_error"] = "OAuth authentication failed. Please try again."
    return RedirectResponse(f"/accounts/{account_id}", status_code=303)
```

On failure:
- Account stays in database with `credentials=NULL` (unauthenticated)
- User is redirected to the account detail page
- Flash error message is shown

The `account_detail` route reads `request.session.pop("flash_error", None)` and passes it to the template.

Also handle the case where `code` query parameter is missing (user denied consent):

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
        request.session["flash_error"] = f"OAuth was denied or failed: {error or 'no code received'}"
        if account_id:
            return RedirectResponse(f"/accounts/{account_id}", status_code=303)
        return RedirectResponse("/", status_code=303)
    ...
```

### 5. Edit Form: Provider-Aware (`account_detail.html`)

The "Basic Info" `<details>` section inside "Edit Account Settings" adapts based on the account's provider category:

- **OAuth** (google, microsoft):
  - Show: Name, Email (editable)
  - Hide: Host, Port, TLS, Provider selector, Password
  - Provider shown as read-only text (not a `<select>`)
  - Note: "Authentication is managed via OAuth. Use the Re-authenticate button above."

- **Known** (yahoo, icloud, protonmail):
  - Show: Name, Email, Password (editable)
  - Hide: Host, Port, TLS, Provider selector
  - Provider shown as read-only text

- **Other**:
  - Show: All fields as today (Name, Email, Host, Port, TLS, Provider, Password)

The template uses the account's `provider` value to decide which fields to render. No JS needed — this is server-side rendering.

The `account_edit_submit` handler should ignore IMAP fields for non-other providers (defense in depth — even if someone crafts a POST request, the values won't be updated).

### 6. Sync Guards

**`scheduler.py`**: In the job scheduling loop, skip accounts where `not account.is_authenticated`:
```python
if not account.is_authenticated:
    continue
```

**`sync_worker.py`**: Before executing mbsync, check `account.is_authenticated`. If not, mark the job as failed with message "Account not authenticated".

**`dovecot.py`**: The userdb endpoint already filters on `account.enabled`. No change needed — unauthenticated accounts can still appear in Dovecot (they just have no mail yet).

### 7. Autodetect Trigger Fix

In `account_form.html`:
- Change `oninput="autoDetectProvider()"` to `onblur="autoDetectProvider()"`

In `app.js`, the `autoDetectProvider()` function:
- Remove the `_lastDomain` early-return guard (no longer needed with blur-only trigger, and it prevented retrying after a failed detection)
- Keep the rest of the function as-is

## Files Changed

| File | Change |
|------|--------|
| `models.py` | Add `is_authenticated` property to Account |
| `templates/account_form.html` | Email field: `oninput` → `onblur`. Provider-aware field visibility |
| `templates/account_detail.html` | Auth/Re-auth button. Provider-aware edit form. Flash error display. Unauthenticated badge |
| `templates/partials/accounts_table.html` | Unauthenticated badge |
| `static/js/app.js` | Replace `toggleAuthFields()` with `updateFormForProvider()`. Remove `_lastDomain` guard |
| `routers/auth.py` | try/except in OAuth callbacks. Handle `error`/missing `code` params |
| `routers/ui_accounts.py` | Read flash error in `account_detail`. Ignore IMAP fields for non-other providers in edit submit |
| `services/scheduler.py` | Skip unauthenticated accounts |
| `services/sync_worker.py` | Guard against unauthenticated accounts |

## No Migration Required

No new database columns. The unauthenticated state is derived from existing fields (`auth_type` + `credentials`).
