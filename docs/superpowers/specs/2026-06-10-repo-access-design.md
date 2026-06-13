# Repository Access Control — Design

**Date:** 2026-06-10
**Status:** Approved
**Scope:** Per-user repository access modeled on the existing mail-store
`allowed_stores` pattern: which repositories a non-admin user may select when
configuring a mailbox backup policy. Final item (point 5) of the first-hand
testing feedback that produced the repo-UX cycle (PR #175).

## Context

Today repositories are admin-managed but **policy configuration is
owner-accessible**: `account_backup_configure` only checks account ownership,
and the account detail page loads `db.query(Repository).all()` — every owner
sees every repository and can point a backup policy at any of them.

Mail stores already solve this shape: `User.store_id` (1:1 home store) plus
`User.allowed_stores` (M2M restricting what a non-admin can select), managed
as checkboxes in the admin Users page, with a startup backfill and route-level
enforcement (`store not in user.allowed_stores` for non-admins).

## Decisions (approved during brainstorming)

- Branch: new cycle from main (PR #175 merged first, commit c19e980).
- **Only an allowed set** — no per-user default repository (YAGNI).
- **Multi-owner accounts**: the allowed set of the user performing the action
  applies (same as stores).
- **Grandfathering**: existing policies pointing at repositories outside the
  configuring user's allowed set keep working. Enforcement applies to new
  selections/changes only; re-submitting the policy's *current* repository is
  allowed, switching to a different non-allowed one is rejected.
- **User-side management** (mirror of allowed stores), not repository-side
  lists and not group-based — matches the established pattern.
- GitGuardian failures on PR #175 were 6 false positives on fake test
  secrets; resolved in the GitGuardian dashboard, out of code scope.

## Data model — migration 017

New association table:

```
user_allowed_repositories(
    user_id       String FK users.id ON DELETE CASCADE, PK
    repository_id String FK backup_destinations.id ON DELETE CASCADE, PK
)
```

Plus `User.allowed_repositories = relationship("Repository", secondary=...)`.

**Backfill inside migration 017**: every existing user is granted every
existing repository — behavior at upgrade time is unchanged; the admin prunes
afterwards. New users start with an **empty set** (no automatic grant at user
creation; admins always bypass). The backfill runs in the migration itself
(INSERT SELECT cross join), not in app startup — unlike the stores backfill
there is no natural per-user value to derive at runtime, and a one-shot
data migration is sufficient.

## Enforcement (admins bypass everywhere)

1. **`account_backup_configure`** (routers/ui_backup.py): for non-admin users,
   the selected `destination_id` must be in `user.allowed_repositories` **or**
   equal to the account's existing policy destination (grandfathering). A
   rejected selection flashes an error and redirects (303), nothing saved.
2. **Account detail context** (routers/ui_accounts.py, `backup_destinations`
   key): non-admins get only their allowed repositories. If the account's
   existing policy points at a repository outside the set, that repository is
   appended to the list so the select renders it (marked, see UI below) and
   the current configuration remains visible and editable in its other fields.
3. **Attached-prefix restores**: unchanged — attachments are created by admins
   and already scoped to the account.

## UI

- **Admin Users page** (`templates/admin_users.html`, expanded user row): an
  "Allowed repositories" checkbox group identical in markup and behavior to
  the existing "Allowed stores" group. POST
  `/admin/users/{user_id}/allowed-repositories` →
  `set_allowed_repositories(db, user_id, repo_ids)` (service function beside
  `set_allowed_stores`; no home-store-style guards needed) → audit action
  `user.set_allowed_repositories`.
- **Account backup policy select** (`partials/account_backup.html`): options
  come from the filtered context. A grandfathered current repository renders
  with a `(current — not in your allowed set)` suffix on the option label so
  the state is visible without blocking the form.
- No changes to the Repositories admin page.

## Config export / DR

`user_allowed_repositories` joins `_EXPORT_TABLES` in
`config_backup_service` (right after `user_allowed_stores`) so a disaster
recovery restore brings the grants back. The generic FK remap already covers
its columns.

## Testing

- Migration/model: table + relationship covered by test_alembic_sync as
  usual. The backfill is verified by a dedicated test that builds a temporary
  file-based SQLite database, migrates it to revision 016, seeds two users and
  two repositories, runs `upgrade 017`, and asserts all four grant rows exist.
- Service: `set_allowed_repositories` sets/replaces the set; unknown ids are
  ignored; audit logged at the route.
- Enforcement: non-admin selecting a non-allowed repo → rejected, nothing
  saved; admin selecting any repo → accepted; grandfathering: existing policy
  on a non-allowed repo survives, re-submitting the same destination passes,
  switching to another non-allowed one is rejected.
- Context filter: non-admin sees only allowed (plus grandfathered current);
  admin sees all.
- DR: export contains the table; import restores grants (IDs/remap).

## Out of scope

- Group-based repository access.
- Per-user default repository.
- Repository-side user lists.
- Restricting which users can *see* the admin Repositories page (stays
  admin-only as today).
