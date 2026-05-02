# User-Selectable Storage

## Problem

Storage assignment is admin-only. Users cannot choose where their mail is stored. Since accounts are decoupled from users (many-to-many via `account_owners`), there's no technical reason to restrict storage choice to admins.

## Design

### Data Model

New many-to-many table `user_allowed_stores`:

```
user_allowed_stores
├── user_id   FK → users.id     (composite PK)
└── store_id  FK → mail_stores.id  (composite PK)
```

Defined as `sqlalchemy.Table()` in `models.py`, same pattern as `account_owners`.

Relationships:
- `User.allowed_stores` → relationship with `MailStore` via `user_allowed_stores`
- `MailStore.allowed_users` → back_populates

No Alembic migration — direct model change, DB recreated if needed (pre-release).

### Invariant

`user.store_id` (the default store) must always be in the user's allowlist. Enforced by backend on every update.

### Backend

**store_service.py — new functions:**

- `get_allowed_stores(db, user) -> list[MailStore]` — returns enabled stores in user's allowlist.
- `set_allowed_stores(db, user_id, store_ids: list[str])` — replaces the allowlist. If current `user.store_id` is not in the new list, sets it to the first store in the list.
- `get_selectable_stores(db, user) -> list[MailStore] | None` — returns `None` if user has only 1 allowed store (implicit, no dropdown needed). Returns the list if >1.

**Account creation (ui_accounts.py):**

- `account_form()` passes `selectable_stores` to template context.
- `account_form_submit()` reads optional `store_id` from form. Validates it's in user's allowlist. Falls back to `user.store_id` if not provided or invalid.

**Profile (new route in ui.py or ui_accounts.py):**

- `POST /profile/store` — changes `user.store_id`. Validates new store is in allowlist. Applies only to future accounts.

**Admin user management (ui_admin.py):**

- When creating a user with a store, that store is auto-added to the allowlist.
- New route `POST /admin/users/{id}/allowed-stores` — receives list of store IDs, calls `set_allowed_stores()`.

### UI

**Admin — user management (`admin_users.html`):**

- New expander row per user: "Allowed Stores" — checkboxes for all enabled stores.
- Save via form POST. Checked stores become the allowlist.
- Creating a user with a store auto-includes it in the allowlist.

**User — profile (`profile.html`):**

- If user has >1 allowed store: dropdown "Default Store for new accounts" + Save button.
- If user has 1 store: show store name as read-only text.
- No migration option — change only affects future accounts.

**User — account creation (`account_form.html`):**

- If >1 allowed store: dropdown "Store" in Basic Info section, after email field.
- If 1 store: informational text as today ("Store: NAS Primary (...)").
- Dropdown shows only stores from user's allowlist.

**User — account detail (`account_detail.html`):**

- Show store name for all users (currently admin-only). Read-only.
- No store change from account detail — admin uses Migrate Store for that.

### Out of Scope

- Account migration when user changes default store.
- Store change from account detail page.
- Store visibility by role (public/private flags).
