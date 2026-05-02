# UUID-Based Maildir with API-Driven Dovecot Namespaces

## Summary

Decouple maildir storage from user identity. Each account gets a UUID-based path (`/store/<account-uuid>/`), and the user→account association lives entirely in the `account_owners` DB table. Dovecot discovers which mailboxes to show each user at login via an HTTP call to MFB's internal API, making MFB the control plane for IMAP visibility.

## Goals

- **Reassign accounts** between users with zero file operations (DB-only)
- **Share accounts** between multiple users via `account_owners` insert
- **MFB as control plane** — all Dovecot visibility logic in testable Python
- **Extensible** — future features (per-account ACL, temporary access, folder filtering, audit, quotas) without touching Dovecot config

## Data Model Changes

### Account

- `maildir_path` changes semantics: `derive_maildir_path(store_path, account.id)` → `f"{store_path}/{account_id}"`
- New `store_id` FK to `mail_stores` — tracks which store this account's mail lives on
- `sanitize_email()` no longer used for path derivation

### User

- `store_id` stays — determines where this user's Dovecot home lives AND serves as default store selection when creating new accounts
- Dovecot home path derived from store: `store.path + "/.dovecot-home/" + username`
- `migrating` flag stays (used during store migrations)

### MailStore

- No schema changes. Associated with both users (Dovecot home + default) and accounts (mail storage).

### StoreMigration

- Changes from per-user to per-account granularity.
- Supports two migration types:
  - **Account migration**: moves an account's maildir from one store to another. Updates `account.store_id` and `account.maildir_path`.
  - **User home migration**: moves a user's Dovecot home from one store to another. Updates `user.store_id`.
- Admin-only operation.

### Store Deletion

A store cannot be deleted while it contains data. When admin requests deletion of a non-empty store:

1. MFB lists all contents: accounts with `store_id = this_store` and users with `store_id = this_store`
2. Admin selects a destination store for each item (accounts and user homes)
3. MFB migrates all data (file move + DB update) to the selected destinations
4. Once the store is empty, deletion proceeds

### Account Creation

User selects a store from available stores when creating an account. `User.store_id` serves as the default selection.

### Relationships

```
MailStore 1──N User     (Dovecot home + default store for new accounts)
MailStore 1──N Account  (where mail lives)
Account   N──M User    (via account_owners)
                │
                └── maildir_path = store.path/<account-uuid>/
```

## Dovecot Integration (Option B: Lua + HTTP)

### Internal API Endpoint

`GET /api/internal/dovecot/userdb/{username}`

Protected by `X-API-Key` header (same `DOVECOT_API_KEY` used for doveadm). Not session-authenticated.

If the user has no accounts, returns an empty `namespaces` array and a home directory only.

Response:

```json
{
  "uid": 1000,
  "gid": 1000,
  "home": "<user_store_path>/.dovecot-home/<username>",
  "namespaces": [
    {
      "name": "acc_1",
      "prefix": "",
      "mail_driver": "maildir",
      "mail_path": "/store/<uuid1>",
      "mailbox_list_layout": "fs",
      "inbox": true
    },
    {
      "name": "acc_2",
      "prefix": "Personal (me@gmail.com)/",
      "mail_driver": "maildir",
      "mail_path": "/store/<uuid2>",
      "mailbox_list_layout": "fs",
      "inbox": false
    }
  ]
}
```

Note: Dovecot 2.4 uses separate `mail_driver`, `mail_path`, `mailbox_list_layout` fields instead of the old `location = "maildir:/path:LAYOUT=fs"` string. The Lua script also sets `namespace = "acc_1 acc_2"` (space-separated) to create the namespaces.

The `home` path is derived from the user's store (`user.store.path + "/.dovecot-home/" + username`), not hardcoded.

Business logic in Python: account ordering, inbox designation, prefix formatting, future filtering/ACL rules. All testable with pytest.

### Lua Userdb Script

~30 lines, volume-mounted to `/etc/dovecot/conf.d/`. At login:

1. Calls MFB via `dovecot.http.client` (connect_timeout="5s", request_timeout="10s")
2. Parses JSON response with `require "json"` (luajson, bundled in official image)
3. Returns extra fields: `namespace` (space-separated names), then per-namespace `mail_driver`, `mail_path`, `mailbox_list_layout`, `prefix`, `separator`, `inbox`

### Passdb Stays SQL

Password verification has no complex logic — keeps the current SQL query against `users` table.

### Home Directory

`<user_store_path>/.dovecot-home/<username>/` — lightweight directory for Dovecot indices, sieve, cache. Does not contain mail. Created automatically by Dovecot on first login. Path is DB-driven via `user.store_id`. Migratable between stores by admin, same as account maildir.

### Namespace Layout

First account (ordered by `created_at`) becomes the inbox namespace. Others get prefixed namespaces:

```
INBOX                          ← first account
Sent
Drafts
Personal (me@gmail.com)/       ← second account
  INBOX
  Sent
  Drafts
```

### Stats Collection

`stats_service` changes: queries doveadm per namespace instead of filtering by email prefix. `iterate_query` returns username + namespace pairs.

## mbsync and Sync Flow

### Path Derivation

- Before: `derive_maildir_path(store_path, username, email)` → `/store/username/sanitized_email/`
- After: `derive_maildir_path(store_path, account_id)` → `/store/<account-uuid>/`

### sync_worker.py

No changes — already path-agnostic.

### mbsync_config.py

Path generation changes to use account UUID instead of username/email.

### Account Creation Flow

1. User selects store (defaults to `user.store_id`, can pick from available stores)
2. MFB derives `maildir_path` from `store.path + "/" + account.id`
3. Sets `account.store_id` to the selected store
4. Associates account to creator via `account_owners`

Path is determined at creation and never changes (except store migration).

### Account Assign/Unassign

Pure DB operations:

- **Assign**: `INSERT INTO account_owners (account_id, user_id)`
- **Unassign**: `DELETE FROM account_owners WHERE account_id = ? AND user_id = ?`
- No filesystem operations
- Dovecot reflects change on user's next login

## Schema Migration (Alembic)

Clean schema — no data migration needed (volumes can be wiped and rebuilt from scratch).

1. Add `store_id` FK on `Account` (NOT NULL, no default)
2. `User.store_id` stays (Dovecot home + default for new accounts)
3. `StoreMigration`: `user_id` field repurposed — add `account_id` field, support both account and user home migrations

## Testing

### API Endpoint

Standard pytest: mock DB, verify JSON response for 0/1/N accounts, shared accounts, disabled users, migrating accounts.

### Lua Script

Integration test with Docker: start Dovecot + MFB in compose, login via `imaplib`, verify correct namespaces appear.

### Store Deletion

Test the drain flow: create store with accounts and users, request deletion, verify migration prompts, migrate, verify deletion succeeds only when empty.

### Regression

Existing tests updated for new UUID-based paths and `Account.store_id`.

## Roundcube

No configuration changes. Roundcube connects to Dovecot via IMAP and lists whatever Dovecot shows. With multiple namespaces, folders appear with namespace prefixes. `subscriptions_option` plugin with `use_subscriptions=false` continues to work across all namespaces.

## Feasibility Validation

Before full implementation, validate Dovecot 2.4 namespace mechanics:

1. Test that Lua userdb can return dynamic namespace extra fields
2. Test `dovecot.http.client` availability in official Docker image
3. Test that multiple namespaces appear correctly in an IMAP client

Option A (SQL fixed slots) serves as fallback if Lua proves unreliable.
