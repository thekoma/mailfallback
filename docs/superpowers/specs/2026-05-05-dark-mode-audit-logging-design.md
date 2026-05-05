# Session 9 Design Spec: Dark Mode + Audit Logging

## Feature 1: Dark Mode

### Overview

Add a theme toggle to MFB that switches between light and dark mode. Pico CSS already supports `data-theme="dark"` on `<html>`, so the framework does most of the heavy lifting. The remaining work is a toggle UI, preference persistence, and fixing ~20 hardcoded color values.

### Toggle Placement

Sun/moon icon in the **sidebar brand area** (top right), next to the "MailFallBack" text.

```
┌──────────────────────────┐
│ 📬 MailFallBack     [🌙] │  ← toggle here
├──────────────────────────┤
│ 📊 Dashboard             │
│ 📧 Accounts              │
│ ...                      │
```

The toggle uses Lucide icons: `sun` for dark mode (click to go light), `moon` for light mode (click to go dark).

### Persistence: localStorage + DB Sync

1. **localStorage** (`mfb-theme`): Read on page load *before* DOM paint to prevent flash of wrong theme. This is the primary source for instant UI response.
2. **User.preferences** (JSONB column): Synced to DB when user toggles theme. On login, server reads DB preference and injects it into the template so the initial `data-theme` attribute is correct.
3. **Flow**:
   - Page load: JS reads `localStorage.getItem('mfb-theme')`, applies to `<html data-theme>` immediately
   - Toggle click: Update `<html data-theme>`, update localStorage, POST to `/api/preferences` to sync to DB
   - Login: Server reads `user.preferences.theme`, passes to template. Template renders `data-theme="{{ theme }}"` instead of hardcoded `"light"`
   - First visit (no localStorage, no DB pref): Default to `light`

### Data Model Change

Add `preferences` JSONB column to `User`:

```python
# models.py
from sqlalchemy import JSON

class User(Base):
    # ... existing fields ...
    preferences = Column(JSON, nullable=False, server_default="{}")
```

Alembic migration adds the column with `server_default="{}"`.

Initial schema for preferences JSON:
```json
{"theme": "light"}
```

This JSONB field is intentionally generic — future preferences (locale, notification settings, etc.) can be added without schema changes.

### API Endpoint

```
PATCH /api/preferences
Content-Type: application/json
Body: {"theme": "dark"}
```

- Authenticated users only
- Merges incoming keys into existing `preferences` JSON (doesn't replace the whole object)
- Returns `204 No Content`
- Validates `theme` value is one of `["light", "dark"]`

### Template Change

`base.html` line 2:

```html
<!-- Before -->
<html lang="en" data-theme="light">

<!-- After -->
<html lang="en" data-theme="{{ theme|default('light') }}">
```

Every route that renders a template must have `theme` available. The cleanest approach: **Jinja2 context processor via middleware**. A lightweight Starlette middleware reads `user.preferences.theme` from the session user and stores it on `request.state.theme`. Then a Jinja2 global function `get_theme(request)` pulls from `request.state.theme`, falling back to `"light"`. This avoids touching every route handler's context dict.

Alternative (simpler, more repetitive): add `"theme": user.preferences.get("theme", "light") if user else "light"` to every `TemplateResponse` context. There are ~25 template-rendering call sites across 5 router files. Tedious but explicit.

**Recommended: middleware approach.** One middleware, one Jinja2 global, zero changes to existing route handlers.

### CSS Changes

All hardcoded colors in `style.css` need dark-mode variants. The approach: define CSS custom properties for each color group, then override them under `[data-theme="dark"]`.

**Colors to convert** (grouped by purpose):

| Purpose | Light Value | Dark Value |
|---------|------------|------------|
| Badge idle bg/text/border | `#dcfce7` / `#166534` / `#bbf7d0` | `#052e16` / `#86efac` / `#14532d` |
| Badge syncing/admin bg/text/border | `#dbeafe` / `#1e40af` / `#93c5fd` | `#172554` / `#93c5fd` / `#1e3a5f` |
| Badge error bg/text/border | `#fee2e2` / `#991b1b` / `#fecaca` | `#450a0a` / `#fca5a5` / `#7f1d1d` |
| Badge disabled/user bg/text/border | `#f3f4f6` / `#6b7280` / `#e5e7eb` | `#1f2937` / `#9ca3af` / `#374151` |
| Checkbox pill checked | `#dbeafe` / `#93c5fd` / `#1e40af` | `#172554` / `#1e3a5f` / `#93c5fd` |
| Icon button danger | `#dc2626` / `#fef2f2` hover | `#f87171` / `#450a0a` hover |
| Icon button success | `#16a34a` | `#4ade80` |
| Auth badge bg | `#f3f4f6` | `#1f2937` |
| Error box border/bg | `#fecaca` / `#fef2f2` | `#7f1d1d` / `#450a0a` |
| Info box bg/border | `#f0f9ff` / `#bae6fd` | `#0c1929` / `#1e3a5f` |
| Stat card error/ok text | `#dc2626` / `#16a34a` | `#f87171` / `#4ade80` |
| Danger zone border | `#fecaca` | `#7f1d1d` |

**Implementation approach**: Define custom properties at `:root` with light values, override under `[data-theme="dark"]`:

```css
:root {
    --mfb-badge-idle-bg: #dcfce7;
    --mfb-badge-idle-color: #166534;
    --mfb-badge-idle-border: #bbf7d0;
    /* ... */
}

[data-theme="dark"] {
    --mfb-badge-idle-bg: #052e16;
    --mfb-badge-idle-color: #86efac;
    --mfb-badge-idle-border: #14532d;
    /* ... */
}

.badge-idle {
    background: var(--mfb-badge-idle-bg);
    color: var(--mfb-badge-idle-color);
    border-color: var(--mfb-badge-idle-border);
}
```

### JavaScript Changes

Add to `app.js`:

```javascript
// Theme toggle — runs immediately, before DOMContentLoaded
(function() {
    const saved = localStorage.getItem('mfb-theme');
    if (saved) document.documentElement.setAttribute('data-theme', saved);
})();
```

Toggle handler (inside DOMContentLoaded):

```javascript
function toggleTheme() {
    const html = document.documentElement;
    const current = html.getAttribute('data-theme') || 'light';
    const next = current === 'dark' ? 'light' : 'dark';
    html.setAttribute('data-theme', next);
    localStorage.setItem('mfb-theme', next);
    // Update icon
    updateThemeIcon(next);
    // Sync to server
    fetch('/api/preferences', {
        method: 'PATCH',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({theme: next})
    });
}
```

### Sidebar Toggle Markup

In `base.html`, inside `.sidebar-brand`:

```html
<div class="sidebar-brand">
    <a href="/" class="no-underline">
        <i data-lucide="mail-check" class="icon-inline"></i>
        <strong class="brand-short">MailFallBack</strong>
    </a>
    <button class="theme-toggle" onclick="toggleTheme()" aria-label="Toggle dark mode" title="Toggle dark mode">
        <i data-lucide="moon" class="icon-sm theme-icon-light"></i>
        <i data-lucide="sun" class="icon-sm theme-icon-dark"></i>
    </button>
</div>
```

CSS shows/hides the correct icon based on current theme:

```css
.theme-icon-dark { display: none; }
[data-theme="dark"] .theme-icon-light { display: none; }
[data-theme="dark"] .theme-icon-dark { display: inline; }
```

### Log Viewer

The `.log-viewer` already uses a dark terminal theme (`background: #0d1117; color: #c9d1d9`). This looks fine in both light and dark mode — no changes needed.

---

## Feature 2: Audit Logging

### Overview

Track admin and account operations in a dedicated `audit_logs` PostgreSQL table. Provide an admin-only viewer at `/admin/audit` with filtering and pagination.

### Events to Capture

**Admin operations:**
- User create / edit / delete / toggle-enable / password-reset / migrate
- Store create / edit / delete
- Group create / edit / delete / add-member / remove-member / add-account / remove-account
- System settings change
- Config export / import

**Account operations:**
- Account create / edit / delete
- Sync trigger (manual)
- Store migration start
- Ownership change (add/remove owner)
- Account suspend / unsuspend

### Data Model

```python
class AuditLog(Base):
    __tablename__ = "audit_logs"

    id = Column(String, primary_key=True, default=_new_uuid)
    timestamp = Column(DateTime(timezone=True), nullable=False, default=_utcnow, index=True)
    user_id = Column(String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    username = Column(String, nullable=False)  # denormalized — survives user deletion
    action = Column(String, nullable=False, index=True)  # e.g. "user.create", "account.delete"
    resource_type = Column(String, nullable=False)  # e.g. "user", "account", "store", "group"
    resource_id = Column(String, nullable=True)  # UUID of affected resource
    resource_name = Column(String, nullable=True)  # human-readable label (username, email, store name)
    ip_address = Column(String, nullable=True)
    details = Column(JSON, nullable=True)  # extra context as needed
```

**Design decisions:**
- `username` is denormalized so audit entries remain readable even if the user is deleted
- `user_id` uses `SET NULL` on delete — the log entry survives but loses the FK link
- `action` is a dotted string (`resource.verb`) not an enum — easier to extend without migrations
- `resource_name` provides a human-readable label so the admin viewer doesn't need JOINs
- `details` JSONB is optional, for context like "schedule changed from X to Y" in the future
- No `before/after` diff columns — out of scope for v1

**Index:** `timestamp` (for pagination) and `action` (for filtering). A compound index on `(resource_type, timestamp)` may be useful later but not for v1.

### Audit Service

New file: `services/audit_service.py`

```python
async def log_action(
    db: Session,
    *,
    user: User,
    action: str,
    resource_type: str,
    resource_id: str | None = None,
    resource_name: str | None = None,
    ip_address: str | None = None,
    details: dict | None = None,
) -> None:
    entry = AuditLog(
        user_id=user.id,
        username=user.username,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        resource_name=resource_name,
        ip_address=ip_address,
        details=details,
    )
    db.add(entry)
    db.commit()
```

Called explicitly from route handlers — **not middleware**. This gives full control over what's logged and what context to include. Example call site:

```python
# In ui_admin.py, after creating a user
audit_service.log_action(
    db, user=current_user, action="user.create",
    resource_type="user", resource_id=new_user.id,
    resource_name=new_user.username,
    ip_address=request.client.host,
)
```

### IP Address Extraction

Use `request.client.host` from Starlette. For reverse proxy setups, this returns the proxy IP. Users behind nginx/traefik need to configure `X-Forwarded-For` and set `--proxy-headers` on Uvicorn — this is standard and documented.

No custom header parsing in MFB — Uvicorn handles `--forwarded-allow-ips` natively.

### Admin Viewer

**URL:** `/admin/audit`

**Sidebar link:** New entry under the Admin section, between "System" and the `<hr>`:

```
ADMIN
├── Users
├── Stores
├── Groups
├── System
└── Audit Log     ← new
```

**Page layout:** Consistent with existing admin pages (table with filters above).

**Filters:**
- **User** dropdown (all users)
- **Action type** dropdown (auto-populated from distinct actions in the table)
- **Date range** (from/to date inputs)

**Table columns:**

| Timestamp | User | Action | Resource | IP | Details |
|-----------|------|--------|----------|----|---------|

- Paginated, 50 entries per page
- Sorted by timestamp descending (newest first)
- Action displayed as a readable label: `user.create` → "Created user"
- Resource shows `resource_name` with a link to the resource if it still exists

**Filtering is server-side** — query params in the URL (`?user=&action=&from=&to=&page=1`). HTMX partial refresh for the table body when filters change.

### Action Labels

Map dotted action strings to human-readable labels:

```python
ACTION_LABELS = {
    "user.create": "Created user",
    "user.edit": "Edited user",
    "user.delete": "Deleted user",
    "user.toggle": "Toggled user status",
    "user.password_reset": "Reset password",  # pragma: allowlist secret
    "user.migrate": "Started user migration",
    "store.create": "Created store",
    "store.edit": "Edited store",
    "store.delete": "Deleted store",
    "group.create": "Created group",
    "group.edit": "Edited group",
    "group.delete": "Deleted group",
    "group.add_member": "Added group member",
    "group.remove_member": "Removed group member",
    "group.add_account": "Added account to group",
    "group.remove_account": "Removed account from group",
    "account.create": "Created account",
    "account.edit": "Edited account",
    "account.delete": "Deleted account",
    "account.sync": "Triggered sync",
    "account.migrate": "Started account migration",
    "account.add_owner": "Added account owner",
    "account.remove_owner": "Removed account owner",
    "account.suspend": "Suspended account",
    "account.unsuspend": "Unsuspended account",
    "settings.update": "Updated system settings",
    "config.export": "Exported configuration",
    "config.import": "Imported configuration",
}
```

### What's NOT in Scope

- Login/logout events
- Audit log export/download
- Retention/auto-cleanup of old audit entries
- Before/after diffs on edits
- Webhook/email notifications on audit events

---

## Files to Create

| File | Purpose |
|------|---------|
| `services/audit_service.py` | `log_action()` function |
| `routers/ui_audit.py` | Admin audit log page + HTMX filter endpoint |
| `templates/admin_audit.html` | Audit log viewer template |
| `templates/partials/audit_table.html` | HTMX partial for filtered table body |
| Alembic migration | Add `preferences` to User, add `audit_logs` table |

## Files to Modify

| File | Changes |
|------|---------|
| `models.py` | Add `preferences` JSONB column to User, add `AuditLog` model |
| `templates/base.html` | Dynamic `data-theme`, theme toggle button in sidebar brand |
| `static/css/style.css` | CSS custom properties for all hardcoded colors, dark-mode overrides, toggle button styling |
| `static/js/app.js` | Theme toggle handler, localStorage read on load, DB sync |
| `routers/ui_admin.py` | Add audit log calls to all admin operations |
| `routers/ui_accounts.py` | Add audit log calls to account operations |
| `routers/ui_profile.py` | Add `/api/preferences` endpoint |
| `routers/auth.py` | Sync theme from DB to localStorage on login |
| `routers/sync.py` | Add audit log call for manual sync trigger |
| `routers/config_io.py` | Add audit log calls for config export/import |
| `app.py` | Include `ui_audit.router` |
| All template-rendering routes | Pass `theme` to template context (or use Jinja2 global) |

## Testing

- **Dark mode:** Test preference API endpoint, test theme persistence round-trip (save → login → template renders correct theme)
- **Audit logging:** Test `log_action()` creates correct entries, test admin viewer pagination and filters, test audit entries survive user deletion (SET NULL)
