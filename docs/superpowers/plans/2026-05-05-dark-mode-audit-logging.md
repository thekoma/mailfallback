# Dark Mode + Audit Logging Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add dark mode with localStorage + DB persistence, and audit logging with an admin viewer at `/admin/audit`.

**Architecture:** Dark mode toggles Pico CSS `data-theme` via a sidebar icon button, persists to localStorage (instant, no flash) and a `preferences` JSONB column on User (cross-device sync). Audit logging uses an explicit `log_action()` service function called from route handlers, stored in an `audit_logs` table, viewed at `/admin/audit` with filters and pagination.

**Tech Stack:** Python 3.12+, FastAPI, SQLAlchemy, Alembic, Jinja2, HTMX, Pico CSS, Lucide icons

---

### Task 1: Data Model — User.preferences + AuditLog table

**Files:**
- Modify: `src/mailfallback/models.py:81-98` (User class)
- Create: `alembic/versions/005_add_preferences_and_audit_logs.py`
- Modify: `alembic/env.py:11-15` (register AuditLog import)
- Test: `tests/test_models.py`

- [ ] **Step 1: Write failing tests for User.preferences and AuditLog**

Add to `tests/test_models.py`:

```python
def test_user_preferences_default(db_session, default_store):
    user = User(username="prefuser", role=UserRole.user, store_id=default_store.id)
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    assert user.preferences == {}


def test_user_preferences_stores_theme(db_session, default_store):
    user = User(username="themeuser", role=UserRole.user, store_id=default_store.id, preferences={"theme": "dark"})
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    assert user.preferences["theme"] == "dark"


def test_audit_log_creation(db_session, default_store):
    user = User(username="auditor", role=UserRole.admin, store_id=default_store.id)
    db_session.add(user)
    db_session.commit()
    log = AuditLog(
        user_id=user.id,
        username=user.username,
        action="user.create",
        resource_type="user",
        resource_id="some-id",
        resource_name="testuser",
        ip_address="127.0.0.1",
    )
    db_session.add(log)
    db_session.commit()
    db_session.refresh(log)
    assert log.action == "user.create"
    assert log.username == "auditor"
    assert log.timestamp is not None


def test_audit_log_survives_user_deletion(db_session, default_store):
    user = User(username="deleteme", role=UserRole.user, store_id=default_store.id)
    db_session.add(user)
    db_session.commit()
    log = AuditLog(
        user_id=user.id,
        username="deleteme",
        action="test.action",
        resource_type="test",
    )
    db_session.add(log)
    db_session.commit()
    db_session.delete(user)
    db_session.commit()
    db_session.refresh(log)
    assert log.user_id is None
    assert log.username == "deleteme"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_models.py::test_user_preferences_default tests/test_models.py::test_audit_log_creation -v`
Expected: FAIL with `AttributeError` / `ImportError`

- [ ] **Step 3: Add preferences column to User and AuditLog model**

In `src/mailfallback/models.py`, add `JSON` to the imports on line 6:

```python
from sqlalchemy import Boolean, Column, DateTime, Enum, ForeignKey, Integer, JSON, String, Table, Text
```

Add `preferences` column to the `User` class (after `migrating` on line 93):

```python
    preferences = Column(JSON, nullable=False, default=dict, server_default="{}")
```

Add the `AuditLog` class at the end of the file (after `StoreMigration`):

```python
class AuditLog(Base):
    __tablename__ = "audit_logs"

    id = Column(String, primary_key=True, default=_new_uuid)
    timestamp = Column(DateTime(timezone=True), nullable=False, default=_utcnow, index=True)
    user_id = Column(String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    username = Column(String, nullable=False)
    action = Column(String, nullable=False, index=True)
    resource_type = Column(String, nullable=False)
    resource_id = Column(String, nullable=True)
    resource_name = Column(String, nullable=True)
    ip_address = Column(String, nullable=True)
    details = Column(JSON, nullable=True)
```

- [ ] **Step 4: Update alembic env.py to register AuditLog**

In `alembic/env.py`, add `AuditLog` to the import on line 11-15:

```python
from mailfallback.models import (  # noqa: F401 — register models
    Account,
    AuditLog,
    MailStore,
    StoreMigration,
    SyncJob,
    User,
)
```

- [ ] **Step 5: Create Alembic migration**

Create `alembic/versions/005_add_preferences_and_audit_logs.py`:

```python
"""Add User.preferences JSONB column and audit_logs table.

Revision ID: 005
Revises: 004
Create Date: 2026-05-05
"""

import sqlalchemy as sa

from alembic import op

revision = "005"
down_revision = "004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("preferences", sa.JSON(), nullable=False, server_default="{}"))

    op.create_table(
        "audit_logs",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False, index=True),
        sa.Column("user_id", sa.String(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("username", sa.String(), nullable=False),
        sa.Column("action", sa.String(), nullable=False, index=True),
        sa.Column("resource_type", sa.String(), nullable=False),
        sa.Column("resource_id", sa.String(), nullable=True),
        sa.Column("resource_name", sa.String(), nullable=True),
        sa.Column("ip_address", sa.String(), nullable=True),
        sa.Column("details", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("audit_logs")
    op.drop_column("users", "preferences")
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/test_models.py -v`
Expected: All pass

- [ ] **Step 7: Run full test suite**

Run: `uv run pytest tests/ -v`
Expected: All 240+ tests pass

- [ ] **Step 8: Commit**

```bash
git add src/mailfallback/models.py alembic/versions/005_add_preferences_and_audit_logs.py alembic/env.py tests/test_models.py
git commit -m "feat: add User.preferences JSONB column and AuditLog model"
```

---

### Task 2: Audit Service — log_action()

**Files:**
- Create: `src/mailfallback/services/audit_service.py`
- Create: `tests/test_audit_service.py`

- [ ] **Step 1: Write failing tests for audit service**

Create `tests/test_audit_service.py`:

```python
from mailfallback.models import AuditLog, UserRole
from mailfallback.services.audit_service import log_action
from mailfallback.services.user_service import create_user


def test_log_action_creates_entry(db_session, default_store):
    user = create_user(db_session, "admin", "pass", UserRole.admin, store_id=default_store.id)
    log_action(
        db_session,
        user=user,
        action="user.create",
        resource_type="user",
        resource_id="new-user-id",
        resource_name="newuser",
        ip_address="10.0.0.1",
    )
    entry = db_session.query(AuditLog).first()
    assert entry is not None
    assert entry.action == "user.create"
    assert entry.username == "admin"
    assert entry.resource_type == "user"
    assert entry.resource_id == "new-user-id"
    assert entry.resource_name == "newuser"
    assert entry.ip_address == "10.0.0.1"
    assert entry.user_id == user.id
    assert entry.timestamp is not None


def test_log_action_with_details(db_session, default_store):
    user = create_user(db_session, "admin", "pass", UserRole.admin, store_id=default_store.id)
    log_action(
        db_session,
        user=user,
        action="account.edit",
        resource_type="account",
        details={"changed": "schedule"},
    )
    entry = db_session.query(AuditLog).first()
    assert entry.details == {"changed": "schedule"}


def test_log_action_minimal(db_session, default_store):
    user = create_user(db_session, "admin", "pass", UserRole.admin, store_id=default_store.id)
    log_action(
        db_session,
        user=user,
        action="config.export",
        resource_type="config",
    )
    entry = db_session.query(AuditLog).first()
    assert entry.resource_id is None
    assert entry.resource_name is None
    assert entry.ip_address is None
    assert entry.details is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_audit_service.py -v`
Expected: FAIL with `ImportError`

- [ ] **Step 3: Implement audit service**

Create `src/mailfallback/services/audit_service.py`:

```python
from sqlalchemy.orm import Session

from mailfallback.models import AuditLog, User

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


def log_action(
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


def get_action_label(action: str) -> str:
    return ACTION_LABELS.get(action, action)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_audit_service.py -v`
Expected: All 3 pass

- [ ] **Step 5: Commit**

```bash
git add src/mailfallback/services/audit_service.py tests/test_audit_service.py
git commit -m "feat: add audit_service.log_action() with action labels"
```

---

### Task 3: Preferences API Endpoint

**Files:**
- Modify: `src/mailfallback/routers/ui_profile.py`
- Create: `tests/test_preferences.py`

- [ ] **Step 1: Write failing tests for preferences API**

Create `tests/test_preferences.py`:

```python
from mailfallback.models import User, UserRole
from mailfallback.services.user_service import create_user


def _login(client, db_session, default_store, username="admin", role=UserRole.admin):
    user = create_user(db_session, username, "pass", role, store_id=default_store.id)
    client.post("/api/auth/login", json={"username": username, "password": "pass"})  # pragma: allowlist secret
    return user


def test_patch_preferences_sets_theme(client, db_session, default_store):
    user = _login(client, db_session, default_store)
    resp = client.patch("/api/preferences", json={"theme": "dark"})
    assert resp.status_code == 204
    db_session.refresh(user)
    assert user.preferences["theme"] == "dark"


def test_patch_preferences_merges(client, db_session, default_store):
    user = _login(client, db_session, default_store)
    client.patch("/api/preferences", json={"theme": "dark"})
    client.patch("/api/preferences", json={"theme": "light"})
    db_session.refresh(user)
    assert user.preferences["theme"] == "light"


def test_patch_preferences_rejects_invalid_theme(client, db_session, default_store):
    _login(client, db_session, default_store)
    resp = client.patch("/api/preferences", json={"theme": "neon"})
    assert resp.status_code == 422


def test_patch_preferences_unauthenticated(client):
    resp = client.patch("/api/preferences", json={"theme": "dark"})
    assert resp.status_code == 401
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_preferences.py -v`
Expected: FAIL — 404 on `/api/preferences`

- [ ] **Step 3: Add PATCH /api/preferences endpoint**

Add to `src/mailfallback/routers/ui_profile.py`:

```python
from fastapi import Response
from pydantic import BaseModel, field_validator

from mailfallback.dependencies import get_current_user
```

Then add the endpoint at the end of the file:

```python
class PreferencesUpdate(BaseModel):
    theme: str | None = None

    @field_validator("theme")
    @classmethod
    def validate_theme(cls, v):
        if v is not None and v not in ("light", "dark"):
            raise ValueError("theme must be 'light' or 'dark'")
        return v


@router.patch("/api/preferences")
def update_preferences(
    body: PreferencesUpdate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    prefs = dict(user.preferences or {})
    update = body.model_dump(exclude_none=True)
    prefs.update(update)
    user.preferences = prefs
    db.commit()
    return Response(status_code=204)
```

Also add `User` to the existing imports from `mailfallback.models` if not already there. Add the `User` import:

```python
from mailfallback.models import User
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_preferences.py -v`
Expected: All 4 pass

- [ ] **Step 5: Run full test suite**

Run: `uv run pytest tests/ -v`
Expected: All pass

- [ ] **Step 6: Commit**

```bash
git add src/mailfallback/routers/ui_profile.py tests/test_preferences.py
git commit -m "feat: add PATCH /api/preferences endpoint for theme persistence"
```

---

### Task 4: Dark Mode — Template + JS + CSS

**Files:**
- Modify: `src/mailfallback/templates/base.html`
- Modify: `src/mailfallback/static/js/app.js`
- Modify: `src/mailfallback/static/css/style.css`
- Modify: `src/mailfallback/routers/ui.py` (Jinja2 global for theme)

- [ ] **Step 1: Add theme to Jinja2 template context via middleware**

In `src/mailfallback/app.py`, add a middleware that reads the user's theme preference and stores it on `request.state`. Add after the `SessionMiddleware` block (after line 146):

```python
    @app.middleware("http")
    async def theme_middleware(request: Request, call_next):
        request.state.theme = "light"
        user_id = request.session.get("user_id") if hasattr(request, "session") else None
        if user_id:
            from mailfallback.models import User

            db = SessionLocal()
            try:
                user = db.query(User).filter(User.id == user_id).first()
                if user and user.preferences:
                    request.state.theme = user.preferences.get("theme", "light")
            finally:
                db.close()
        response = await call_next(request)
        return response
```

Add `Request` to imports at top of `app.py`:

```python
from starlette.requests import Request
```

Then in `src/mailfallback/routers/ui.py`, add a Jinja2 global context processor. After line 102 (`templates.env.globals["webmail_url"] = ...`), add:

```python
def _get_theme(request: Request) -> str:
    return getattr(getattr(request, "state", None), "theme", "light")

templates.env.globals["get_theme"] = _get_theme
```

- [ ] **Step 2: Update base.html — dynamic data-theme + toggle button**

In `src/mailfallback/templates/base.html`, change line 2:

```html
<html lang="en" data-theme="{{ get_theme(request) }}">
```

Update the `.sidebar-brand` div (lines 21-26) to include the toggle button:

```html
    <aside class="sidebar" id="sidebar">
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

- [ ] **Step 3: Add theme toggle JS to app.js**

Add at the very top of `src/mailfallback/static/js/app.js` (before any other code):

```javascript
// Theme — apply from localStorage before paint to prevent flash
(function() {
    var saved = localStorage.getItem('mfb-theme');
    if (saved) document.documentElement.setAttribute('data-theme', saved);
})();
```

Then add the `toggleTheme` function inside the existing `document.addEventListener('DOMContentLoaded', ...)` block, or as a top-level function (it's called from `onclick` so it must be global). Add after the IIFE above:

```javascript
function toggleTheme() {
    var html = document.documentElement;
    var current = html.getAttribute('data-theme') || 'light';
    var next = current === 'dark' ? 'light' : 'dark';
    html.setAttribute('data-theme', next);
    localStorage.setItem('mfb-theme', next);
    // Re-render Lucide icons to swap moon/sun visibility
    if (typeof lucide !== 'undefined') lucide.createIcons();
    // Sync to server (fire and forget)
    fetch('/api/preferences', {
        method: 'PATCH',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({theme: next})
    });
}
```

- [ ] **Step 4: Add dark mode CSS custom properties and overrides**

In `src/mailfallback/static/css/style.css`, add a new section at the top of the file (after line 1 `/* MFB — MailFallBack styles */`):

```css
/* === Dark mode custom properties === */
:root {
    --mfb-badge-idle-bg: #dcfce7;
    --mfb-badge-idle-color: #166534;
    --mfb-badge-idle-border: #bbf7d0;
    --mfb-badge-syncing-bg: #dbeafe;
    --mfb-badge-syncing-color: #1e40af;
    --mfb-badge-syncing-border: #93c5fd;
    --mfb-badge-error-bg: #fee2e2;
    --mfb-badge-error-color: #991b1b;
    --mfb-badge-error-border: #fecaca;
    --mfb-badge-neutral-bg: #f3f4f6;
    --mfb-badge-neutral-color: #6b7280;
    --mfb-badge-neutral-border: #e5e7eb;
    --mfb-badge-user-color: #374151;
    --mfb-badge-user-border: #d1d5db;
    --mfb-icon-btn-color: #374151;
    --mfb-danger-color: #dc2626;
    --mfb-danger-bg: #fef2f2;
    --mfb-danger-border: #fecaca;
    --mfb-success-color: #16a34a;
    --mfb-info-bg: #f0f9ff;
    --mfb-info-border: #bae6fd;
    --mfb-sync-color: #2563eb;
    --mfb-stats-dot-ok: #16a34a;
    --mfb-stats-dot-syncing: #2563eb;
    --mfb-stats-dot-error: #dc2626;
}
[data-theme="dark"] {
    --mfb-badge-idle-bg: #052e16;
    --mfb-badge-idle-color: #86efac;
    --mfb-badge-idle-border: #14532d;
    --mfb-badge-syncing-bg: #172554;
    --mfb-badge-syncing-color: #93c5fd;
    --mfb-badge-syncing-border: #1e3a5f;
    --mfb-badge-error-bg: #450a0a;
    --mfb-badge-error-color: #fca5a5;
    --mfb-badge-error-border: #7f1d1d;
    --mfb-badge-neutral-bg: #1f2937;
    --mfb-badge-neutral-color: #9ca3af;
    --mfb-badge-neutral-border: #374151;
    --mfb-badge-user-color: #d1d5db;
    --mfb-badge-user-border: #4b5563;
    --mfb-icon-btn-color: #d1d5db;
    --mfb-danger-color: #f87171;
    --mfb-danger-bg: #450a0a;
    --mfb-danger-border: #7f1d1d;
    --mfb-success-color: #4ade80;
    --mfb-info-bg: #0c1929;
    --mfb-info-border: #1e3a5f;
    --mfb-sync-color: #60a5fa;
    --mfb-stats-dot-ok: #4ade80;
    --mfb-stats-dot-syncing: #60a5fa;
    --mfb-stats-dot-error: #f87171;
}
```

Then replace all hardcoded colors in the file with the custom properties:

```css
/* Badges */
.badge-idle { background: var(--mfb-badge-idle-bg); color: var(--mfb-badge-idle-color); border-color: var(--mfb-badge-idle-border); }
.badge-syncing { background: var(--mfb-badge-syncing-bg); color: var(--mfb-badge-syncing-color); border-color: var(--mfb-badge-syncing-border); }
.badge-error { background: var(--mfb-badge-error-bg); color: var(--mfb-badge-error-color); border-color: var(--mfb-badge-error-border); }
.badge-disabled { background: var(--mfb-badge-neutral-bg); color: var(--mfb-badge-neutral-color); border-color: var(--mfb-badge-neutral-border); }
.badge-admin { background: var(--mfb-badge-syncing-bg); color: var(--mfb-badge-syncing-color); border-color: var(--mfb-badge-syncing-border); }
.badge-user { background: var(--mfb-badge-neutral-bg); color: var(--mfb-badge-user-color); border-color: var(--mfb-badge-user-border); }
.badge-role-admin { background: transparent; border: 1px solid var(--mfb-badge-syncing-border); color: var(--mfb-badge-syncing-color); }

/* Checkbox pills */
.checkbox-pill:has(input:checked) { background: var(--mfb-badge-syncing-bg); border-color: var(--mfb-badge-syncing-border); color: var(--mfb-badge-syncing-color); }

/* Icon buttons */
button.icon-btn, a.icon-btn { color: var(--mfb-icon-btn-color); }
button.icon-btn.danger, a.icon-btn.danger { color: var(--mfb-danger-color); border-color: var(--mfb-danger-color); }
button.icon-btn.danger:hover, a.icon-btn.danger:hover { background: var(--mfb-danger-bg); }
button.icon-btn.success, a.icon-btn.success { color: var(--mfb-success-color); border-color: var(--mfb-success-color); }

/* Auth badge */
.auth-badge { background: var(--mfb-badge-neutral-bg); }

/* Error/info boxes */
.error-box { border: 1px solid var(--mfb-danger-border); background: var(--mfb-danger-bg); }
.info-box { background: var(--mfb-info-bg); border: 1px solid var(--mfb-info-border); }

/* Stat cards */
.stat-card.stat-error .stat-value { color: var(--mfb-danger-color); }
.stat-card.stat-ok .stat-value { color: var(--mfb-success-color); }

/* Danger zone */
.danger-zone { border-top: 2px solid var(--mfb-danger-border); }

/* Sync status */
.sync-syncing { color: var(--mfb-sync-color); }

/* Stats dots */
.stats-dot-ok { background: var(--mfb-stats-dot-ok); }
.stats-dot-syncing { background: var(--mfb-stats-dot-syncing); }
.stats-dot-error { background: var(--mfb-stats-dot-error); }

/* Log modal */
.log-modal { border: 1px solid var(--pico-muted-border-color); }
```

Add theme toggle button styling:

```css
/* === Theme toggle === */
.theme-toggle {
    background: none;
    border: none;
    cursor: pointer;
    padding: 0.25rem;
    margin: 0;
    width: auto;
    color: var(--pico-muted-color);
    opacity: 0.6;
}
.theme-toggle:hover { opacity: 1; }
.theme-icon-dark { display: none; }
[data-theme="dark"] .theme-icon-light { display: none; }
[data-theme="dark"] .theme-icon-dark { display: inline; }
```

Update `.sidebar-brand` to use flexbox with space-between:

```css
.sidebar-brand {
    padding: 1rem 1.2rem;
    border-bottom: 1px solid var(--pico-muted-border-color);
    display: flex;
    align-items: center;
    justify-content: space-between;
}
```

- [ ] **Step 5: Run lint and tests**

Run: `uv run ruff check src/ tests/ && uv run pytest tests/ -v`
Expected: Clean lint, all tests pass

- [ ] **Step 6: Commit**

```bash
git add src/mailfallback/templates/base.html src/mailfallback/static/js/app.js src/mailfallback/static/css/style.css src/mailfallback/app.py src/mailfallback/routers/ui.py
git commit -m "feat: add dark mode with toggle, localStorage, and CSS custom properties"
```

---

### Task 5: Dark Mode — Visual Verification

**Files:** None (browser testing only)

- [ ] **Step 1: Start dev server and open browser**

Run: `docker compose up -d --build` or `uv run uvicorn mailfallback.app:app --reload`

- [ ] **Step 2: Verify light mode is default**

Navigate to the login page. Confirm the page renders in light mode. The sidebar brand area should show a moon icon.

- [ ] **Step 3: Log in and toggle to dark mode**

Log in as admin. Click the moon icon in the sidebar brand area. Verify:
- Page switches to dark mode immediately
- Moon icon changes to sun icon
- All badges (idle, syncing, error, admin, user) use dark-appropriate colors
- Stat cards on dashboard have readable text
- Error/info boxes have dark backgrounds
- Danger zone border is visible but not garish
- Log viewer terminal still looks correct (already dark)

- [ ] **Step 4: Verify persistence**

Reload the page. Confirm dark mode persists (no flash of light mode). Open a new tab. Confirm dark mode is applied immediately.

- [ ] **Step 5: Verify light mode toggle**

Click the sun icon. Confirm page returns to light mode. Reload. Confirm light mode persists.

---

### Task 6: Audit Log Admin Page — Router + Templates

**Files:**
- Create: `src/mailfallback/routers/ui_audit.py`
- Create: `src/mailfallback/templates/admin_audit.html`
- Create: `src/mailfallback/templates/partials/audit_table.html`
- Modify: `src/mailfallback/app.py` (include router)
- Modify: `src/mailfallback/templates/base.html` (sidebar link)
- Create: `tests/test_audit_ui.py`

- [ ] **Step 1: Write failing tests for audit admin page**

Create `tests/test_audit_ui.py`:

```python
from mailfallback.models import AuditLog, UserRole
from mailfallback.services.audit_service import log_action
from mailfallback.services.user_service import create_user


def _login_admin(client, db_session, default_store):
    user = create_user(db_session, "admin", "pass", UserRole.admin, store_id=default_store.id)
    client.post("/api/auth/login", json={"username": "admin", "password": "pass"})  # pragma: allowlist secret
    return user


def test_audit_page_requires_admin(client, db_session, default_store):
    create_user(db_session, "regular", "pass", UserRole.user, store_id=default_store.id)  # pragma: allowlist secret
    client.post("/api/auth/login", json={"username": "regular", "password": "pass"})  # pragma: allowlist secret
    resp = client.get("/admin/audit", follow_redirects=False)
    assert resp.status_code in (302, 307)


def test_audit_page_loads_for_admin(client, db_session, default_store):
    _login_admin(client, db_session, default_store)
    resp = client.get("/admin/audit")
    assert resp.status_code == 200
    assert "Audit Log" in resp.text


def test_audit_page_shows_entries(client, db_session, default_store):
    user = _login_admin(client, db_session, default_store)
    log_action(db_session, user=user, action="user.create", resource_type="user", resource_name="newuser")
    resp = client.get("/admin/audit")
    assert resp.status_code == 200
    assert "Created user" in resp.text
    assert "newuser" in resp.text


def test_audit_page_filters_by_action(client, db_session, default_store):
    user = _login_admin(client, db_session, default_store)
    log_action(db_session, user=user, action="user.create", resource_type="user", resource_name="u1")
    log_action(db_session, user=user, action="store.create", resource_type="store", resource_name="s1")
    resp = client.get("/admin/audit?action=user.create")
    assert resp.status_code == 200
    assert "u1" in resp.text
    assert "s1" not in resp.text


def test_audit_table_partial(client, db_session, default_store):
    user = _login_admin(client, db_session, default_store)
    log_action(db_session, user=user, action="user.create", resource_type="user", resource_name="u1")
    resp = client.get("/admin/audit/table?action=user.create", headers={"HX-Request": "true"})
    assert resp.status_code == 200
    assert "u1" in resp.text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_audit_ui.py -v`
Expected: FAIL — 404 on `/admin/audit`

- [ ] **Step 3: Create the audit router**

Create `src/mailfallback/routers/ui_audit.py`:

```python
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from mailfallback.dependencies import get_db
from mailfallback.models import AuditLog
from mailfallback.routers.ui import _get_session_user, templates
from mailfallback.services.audit_service import get_action_label
from mailfallback.services.user_service import list_users

router = APIRouter(tags=["ui"])

PAGE_SIZE = 50


def _build_query(db: Session, params):
    q = db.query(AuditLog).order_by(AuditLog.timestamp.desc())
    if params.get("user"):
        q = q.filter(AuditLog.username == params["user"])
    if params.get("action"):
        q = q.filter(AuditLog.action == params["action"])
    if params.get("from"):
        q = q.filter(AuditLog.timestamp >= params["from"])
    if params.get("to"):
        q = q.filter(AuditLog.timestamp <= params["to"])
    return q


@router.get("/admin/audit", response_class=HTMLResponse)
def admin_audit_page(request: Request, db: Session = Depends(get_db)):
    user = _get_session_user(request, db)
    if not user or user.role.value != "admin":
        return RedirectResponse("/")

    params = dict(request.query_params)
    page = int(params.pop("page", 1))
    q = _build_query(db, params)
    total = q.count()
    entries = q.offset((page - 1) * PAGE_SIZE).limit(PAGE_SIZE).all()
    total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)

    all_users = list_users(db)
    distinct_actions = [r[0] for r in db.query(AuditLog.action).distinct().order_by(AuditLog.action).all()]

    return templates.TemplateResponse(
        request=request,
        name="admin_audit.html",
        context={
            "user": user,
            "entries": entries,
            "page": page,
            "total_pages": total_pages,
            "total": total,
            "all_users": all_users,
            "distinct_actions": distinct_actions,
            "get_action_label": get_action_label,
            "filters": params,
        },
    )


@router.get("/admin/audit/table", response_class=HTMLResponse)
def admin_audit_table(request: Request, db: Session = Depends(get_db)):
    user = _get_session_user(request, db)
    if not user or user.role.value != "admin":
        return HTMLResponse("")

    params = dict(request.query_params)
    page = int(params.pop("page", 1))
    q = _build_query(db, params)
    total = q.count()
    entries = q.offset((page - 1) * PAGE_SIZE).limit(PAGE_SIZE).all()
    total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)

    return templates.TemplateResponse(
        request=request,
        name="partials/audit_table.html",
        context={
            "entries": entries,
            "page": page,
            "total_pages": total_pages,
            "total": total,
            "get_action_label": get_action_label,
            "filters": params,
        },
    )
```

- [ ] **Step 4: Create the audit page template**

Create `src/mailfallback/templates/admin_audit.html`:

```html
{% extends "base.html" %}
{% block title %}Audit Log — MFB{% endblock %}
{% block content %}
<h2><i data-lucide="scroll-text" class="icon-xl icon-inline"></i>Audit Log</h2>

<form class="flex gap-1 items-end flex-wrap mb-0" hx-get="/admin/audit/table" hx-target="#audit-table-wrap" hx-push-url="true">
    <div>
        <label class="text-xs">User</label>
        <select name="user" style="margin-bottom:0;min-width:140px">
            <option value="">All users</option>
            {% for u in all_users %}
            <option value="{{ u.username }}" {% if filters.get('user') == u.username %}selected{% endif %}>{{ u.username }}</option>
            {% endfor %}
        </select>
    </div>
    <div>
        <label class="text-xs">Action</label>
        <select name="action" style="margin-bottom:0;min-width:160px">
            <option value="">All actions</option>
            {% for a in distinct_actions %}
            <option value="{{ a }}" {% if filters.get('action') == a %}selected{% endif %}>{{ get_action_label(a) }}</option>
            {% endfor %}
        </select>
    </div>
    <div>
        <label class="text-xs">From</label>
        <input type="date" name="from" value="{{ filters.get('from', '') }}" style="margin-bottom:0">
    </div>
    <div>
        <label class="text-xs">To</label>
        <input type="date" name="to" value="{{ filters.get('to', '') }}" style="margin-bottom:0">
    </div>
    <button type="submit" class="icon-btn primary" title="Filter"><i data-lucide="filter" class="icon-md"></i></button>
</form>

<div id="audit-table-wrap">
    {% include "partials/audit_table.html" %}
</div>
{% endblock %}
```

- [ ] **Step 5: Create the audit table partial**

Create `src/mailfallback/templates/partials/audit_table.html`:

```html
<p class="text-muted text-xs mt-05">{{ total }} entries</p>
<div class="table-wrap">
<table>
    <thead>
        <tr>
            <th>Timestamp</th>
            <th>User</th>
            <th>Action</th>
            <th>Resource</th>
            <th>IP</th>
        </tr>
    </thead>
    <tbody>
        {% for e in entries %}
        <tr>
            <td class="text-xs">{{ e.timestamp.strftime('%Y-%m-%d %H:%M:%S') }}</td>
            <td>{{ e.username }}</td>
            <td>{{ get_action_label(e.action) }}</td>
            <td>{% if e.resource_name %}{{ e.resource_name }}{% elif e.resource_id %}<code class="text-xs">{{ e.resource_id[:8] }}</code>{% else %}—{% endif %}</td>
            <td class="text-xs text-muted">{{ e.ip_address or '—' }}</td>
        </tr>
        {% endfor %}
        {% if not entries %}
        <tr><td colspan="5" class="text-center text-muted">No audit entries found</td></tr>
        {% endif %}
    </tbody>
</table>
</div>

{% if total_pages > 1 %}
<nav class="flex gap-05 items-center mt-05">
    {% if page > 1 %}
    <a href="?page={{ page - 1 }}{% for k, v in filters.items() %}&{{ k }}={{ v }}{% endfor %}" class="icon-btn"><i data-lucide="chevron-left" class="icon-sm"></i></a>
    {% endif %}
    <span class="text-xs text-muted">Page {{ page }} of {{ total_pages }}</span>
    {% if page < total_pages %}
    <a href="?page={{ page + 1 }}{% for k, v in filters.items() %}&{{ k }}={{ v }}{% endfor %}" class="icon-btn"><i data-lucide="chevron-right" class="icon-sm"></i></a>
    {% endif %}
</nav>
{% endif %}
```

- [ ] **Step 6: Register the router in app.py**

In `src/mailfallback/app.py`, add the import (after line 26 `ui_profile,`):

```python
from mailfallback.routers import (
    ...
    ui_audit,
    ui_profile,
)
```

And add the router inclusion (after `app.include_router(ui_profile.router)` around line 155):

```python
    app.include_router(ui_audit.router)
```

- [ ] **Step 7: Add sidebar link in base.html**

In `src/mailfallback/templates/base.html`, add the audit log link in the admin section (after the System link, before `{% endif %}` for the admin block):

```html
            <a href="/admin/audit" {% if request.url.path.startswith("/admin/audit") %}class="active"{% endif %}><i data-lucide="scroll-text" class="icon-nav"></i>Audit Log</a>
```

- [ ] **Step 8: Run tests to verify they pass**

Run: `uv run pytest tests/test_audit_ui.py -v`
Expected: All 5 pass

- [ ] **Step 9: Run full test suite**

Run: `uv run pytest tests/ -v`
Expected: All pass

- [ ] **Step 10: Commit**

```bash
git add src/mailfallback/routers/ui_audit.py src/mailfallback/templates/admin_audit.html src/mailfallback/templates/partials/audit_table.html src/mailfallback/app.py src/mailfallback/templates/base.html tests/test_audit_ui.py
git commit -m "feat: add admin audit log page with filters and pagination"
```

---

### Task 7: Wire Audit Logging into Admin Routes

**Files:**
- Modify: `src/mailfallback/routers/ui_admin.py`
- Modify: `src/mailfallback/routers/config_io.py`

- [ ] **Step 1: Add audit logging to admin user operations**

In `src/mailfallback/routers/ui_admin.py`, add the import at the top:

```python
from mailfallback.services.audit_service import log_action
```

Add `log_action` calls to each operation. Here are all the insertion points:

**admin_create_user** (after line 260, after `set_allowed_stores`):
```python
    log_action(db, user=user, action="user.create", resource_type="user", resource_id=new_user.id, resource_name=new_user.username, ip_address=request.client.host)
```

**admin_edit_user** (after line 104, after `update_user`):
```python
    target = db.query(User).filter(User.id == target_user_id).first()
    log_action(db, user=user, action="user.edit", resource_type="user", resource_id=target_user_id, resource_name=target.username if target else target_user_id, ip_address=request.client.host)
```

**admin_toggle_user** (after line 122, after `update_user`):
```python
        action = "user.toggle"
        log_action(db, user=user, action=action, resource_type="user", resource_id=target_user_id, resource_name=target.username, ip_address=request.client.host)
```

**admin_delete_user** (after line 138, after `delete_user`):
```python
    log_action(db, user=user, action="user.delete", resource_type="user", resource_id=target_user_id, ip_address=request.client.host)
```

**admin_change_user_password** (after line 81, after `change_password`):
```python
    target = db.query(User).filter(User.id == target_user_id).first()
    log_action(db, user=user, action="user.password_reset", resource_type="user", resource_id=target_user_id, resource_name=target.username if target else target_user_id, ip_address=request.client.host)
```

**admin_migrate_user** (after line 156, after `initiate_home_migration` succeeds):
```python
    log_action(db, user=user, action="user.migrate", resource_type="user", resource_id=target_user_id, ip_address=request.client.host)
```

**admin_create_store** (after line 351, after `create_store`):
```python
    store = create_store(db, form["name"], form["path"])
    log_action(db, user=user, action="store.create", resource_type="store", resource_id=store.id, resource_name=store.name, ip_address=request.client.host)
```
Note: `create_store` must return the store object. Check the service — if it doesn't, query after creation.

**admin_toggle_store** (after line 362, after `update_store`):
```python
        log_action(db, user=user, action="store.edit", resource_type="store", resource_id=store_id, resource_name=store.name, ip_address=request.client.host)
```

**admin_rename_store** (after line 374, after `update_store`):
```python
        log_action(db, user=user, action="store.edit", resource_type="store", resource_id=store_id, resource_name=name, ip_address=request.client.host)
```

**admin_delete_store** (after line 392, when `ok` is True):
```python
    if not ok:
        return RedirectResponse(f"/admin/stores?error={error}", status_code=303)
    log_action(db, user=user, action="store.delete", resource_type="store", resource_id=store_id, ip_address=request.client.host)
```

**admin_create_group** (after line 491, after `create_group`):
```python
    group = create_group(db, form["name"], owner_id, sso_sync=sso_sync)
    log_action(db, user=user, action="group.create", resource_type="group", resource_id=group.id, resource_name=group.name, ip_address=request.client.host)
```
Note: `create_group` must return the group object. Check the service — if it doesn't, query after creation.

**admin_edit_group** (after line 510, after `db.commit()`):
```python
    log_action(db, user=user, action="group.edit", resource_type="group", resource_id=group_id, resource_name=group.name, ip_address=request.client.host)
```

**admin_delete_group_route** (after line 519, after `delete_group`):
```python
    group = db.query(Group).filter(Group.id == group_id).first()
    group_name = group.name if group else group_id
    delete_group(db, group_id)
    log_action(db, user=user, action="group.delete", resource_type="group", resource_id=group_id, resource_name=group_name, ip_address=request.client.host)
```
Note: Read group name before deleting.

- [ ] **Step 2: Add audit logging to config export/import**

In `src/mailfallback/routers/config_io.py`, add import and `Request` dependency:

```python
from fastapi import APIRouter, Depends, Request
from mailfallback.services.audit_service import log_action
```

Update `export_config` signature to accept `Request`:
```python
@router.get("/export")
def export_config(request: Request, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    log_action(db, user=admin, action="config.export", resource_type="config", ip_address=request.client.host)
    accounts = db.query(Account).all()
    ...
```

Update `import_config` to log:
```python
@router.post("/import")
def import_config(body: ConfigImport, request: Request, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    ...
    db.commit()
    log_action(db, user=admin, action="config.import", resource_type="config", ip_address=request.client.host, details={"count": count})
    return {"imported": count}
```

- [ ] **Step 3: Run lint and tests**

Run: `uv run ruff check src/ tests/ && uv run pytest tests/ -v`
Expected: Clean lint, all tests pass

- [ ] **Step 4: Commit**

```bash
git add src/mailfallback/routers/ui_admin.py src/mailfallback/routers/config_io.py
git commit -m "feat: wire audit logging into admin and config operations"
```

---

### Task 8: Wire Audit Logging into Account Routes

**Files:**
- Modify: `src/mailfallback/routers/ui_accounts.py`
- Modify: `src/mailfallback/routers/accounts.py` (API-level delete + owner routes)
- Modify: `src/mailfallback/routers/sync.py`

- [ ] **Step 1: Add audit logging to account operations**

In `src/mailfallback/routers/ui_accounts.py`, add the import:

```python
from mailfallback.services.audit_service import log_action
```

Add `log_action` calls:

**account_create** (after line 351, after `assign_owner`):
```python
    log_action(db, user=user, action="account.create", resource_type="account", resource_id=account.id, resource_name=account.email_address or account.name, ip_address=request.client.host)
```

**account_edit** (after line 472, after `update_account`):
```python
    log_action(db, user=user, action="account.edit", resource_type="account", resource_id=account_id, resource_name=account.email_address or account.name, ip_address=request.client.host)
```

**account_delete** (find the delete handler — look for the `delete` route):
```python
    log_action(db, user=user, action="account.delete", resource_type="account", resource_id=account_id, resource_name=account.email_address or account.name, ip_address=request.client.host)
```

**account_toggle_visible** (after line 487, after `update_account`):
```python
        log_action(db, user=user, action="account.edit", resource_type="account", resource_id=account_id, resource_name=account.email_address or account.name, ip_address=request.client.host, details={"toggled": "visibility"})
```

**account_toggle_suspend** (after line 502, after `update_account`):
```python
        action = "account.unsuspend" if account.suspended else "account.suspend"
        log_action(db, user=user, action=action, resource_type="account", resource_id=account_id, resource_name=account.email_address or account.name, ip_address=request.client.host)
```
Note: Check the suspend state BEFORE the toggle to determine the correct action name.

**account_migrate** (after line 520, after `initiate_account_migration`):
```python
    log_action(db, user=user, action="account.migrate", resource_type="account", resource_id=account_id, ip_address=request.client.host)
```

**account_add_owner** (after line 604, after `assign_owner`):
```python
    log_action(db, user=user, action="account.add_owner", resource_type="account", resource_id=account_id, resource_name=form["user_id"], ip_address=request.client.host)
```

**account_remove_owner** (after line 619, after `remove_owner`):
```python
    log_action(db, user=user, action="account.remove_owner", resource_type="account", resource_id=account_id, resource_name=form["user_id"], ip_address=request.client.host)
```

- [ ] **Step 2: Add audit logging to API-level account routes**

In `src/mailfallback/routers/accounts.py`, add imports:

```python
from fastapi import APIRouter, Depends, HTTPException, Request
from mailfallback.services.audit_service import log_action
```

In `delete` (after line 168, after `delete_account` succeeds, before the return):
```python
    log_action(db, user=user, action="account.delete", resource_type="account", resource_id=account_id, resource_name=account.email_address or account.name, ip_address=request.client.host)
```
Add `request: Request` to the function signature.

In `assign_owner` (after line 181, after `account_service.assign_owner`):
```python
    log_action(db, user=admin, action="account.add_owner", resource_type="account", resource_id=account_id, resource_name=body.user_id, ip_address=request.client.host)
```
Add `request: Request` to the function signature.

In `remove_owner` (after line 194, after `account_service.remove_owner`):
```python
    log_action(db, user=admin, action="account.remove_owner", resource_type="account", resource_id=account_id, resource_name=user_id, ip_address=request.client.host)
```
Add `request: Request` to the function signature.

- [ ] **Step 3: Add audit logging to sync trigger**

In `src/mailfallback/routers/sync.py`, add import:

```python
from mailfallback.services.audit_service import log_action
```

In `trigger_sync` (after line 89, after `submit_sync_job`):
```python
    log_action(db, user=user, action="account.sync", resource_type="account", resource_id=account_id, resource_name=account.email_address, ip_address=None)
```
Note: `trigger_sync` uses `Depends(get_current_user)` not `_get_session_user`, and doesn't have `request` by default. Add `request: Request` to the function signature and import `Request` from `fastapi`.

- [ ] **Step 4: Run lint and tests**

Run: `uv run ruff check src/ tests/ && uv run pytest tests/ -v`
Expected: Clean lint, all tests pass

- [ ] **Step 5: Commit**

```bash
git add src/mailfallback/routers/ui_accounts.py src/mailfallback/routers/accounts.py src/mailfallback/routers/sync.py
git commit -m "feat: wire audit logging into account and sync operations"
```

---

### Task 9: End-to-End Verification

**Files:** None (manual testing only)

- [ ] **Step 1: Apply database migration on running Docker**

Run: `docker compose exec mailfallback uv run alembic upgrade head`
Expected: Migration 005 applied successfully

- [ ] **Step 2: Verify dark mode end-to-end**

1. Navigate to dashboard in browser
2. Click moon icon → page goes dark, icon changes to sun
3. Reload → dark mode persists (no flash)
4. Check all admin pages: Users, Stores, Groups, Settings, Audit Log
5. Check account detail page with all hero states
6. Toggle back to light mode, reload, confirm persistence

- [ ] **Step 3: Verify audit logging end-to-end**

1. Create a new user via admin → check `/admin/audit` for "Created user" entry
2. Edit the user → check for "Edited user" entry
3. Toggle user enabled → check for "Toggled user status" entry
4. Create an account → check for "Created account" entry
5. Trigger a manual sync → check for "Triggered sync" entry
6. Filter by action type → only matching entries shown
7. Filter by user → only that user's entries shown

- [ ] **Step 4: Run full test suite one final time**

Run: `uv run pytest tests/ -v`
Expected: All tests pass

- [ ] **Step 5: Commit any remaining fixes**

If any visual or test issues were found, fix and commit.

- [ ] **Step 6: Final commit with all changes**

```bash
git add -A
git status
# Only commit if there are changes
git commit -m "feat: session 9 — dark mode + audit logging"
```
