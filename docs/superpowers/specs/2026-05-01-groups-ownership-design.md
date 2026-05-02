# Groups, Ownership & SSO Sync

## Problem

Account visibility is tied solely to ownership (account_owners). There's no way to share account visibility with multiple users without making them all owners. Ownership transfer requires admin intervention. SSO group claims are parsed but only used for role assignment, not for account access.

## Goals

1. Let owners and admins manage account ownership (add/remove co-owners)
2. Introduce MFB groups as a visibility mechanism — accounts assigned to a group are visible to all group members
3. Group membership can be managed manually by admins and group owners, and auto-synced from SSO claims on login

## Data Model

### New tables

```
groups
├── id          UUID PK
├── name        String UNIQUE NOT NULL
├── owner_id    FK → users.id (manages the group)
├── sso_sync    Boolean DEFAULT false
├── created_at  DateTime

group_members
├── group_id    FK → groups.id  ┐ composite PK
├── user_id     FK → users.id  ┘

account_groups
├── account_id  FK → accounts.id  ┐ composite PK
├── group_id    FK → groups.id    ┘
```

### SQLAlchemy relationships

```python
class Group(Base):
    owner = relationship("User")
    members = relationship("User", secondary=group_members)
    accounts = relationship("Account", secondary=account_groups)

class User:
    groups = relationship("Group", secondary=group_members)

class Account:
    visible_to_groups = relationship("Group", secondary=account_groups)
```

No Alembic migration — direct model change, DB recreated or table created manually (pre-release).

## Visibility Rules

An account is visible to a user if ANY of:
- User is an **owner** (via `account_owners`)
- User is a **member of a group** that has the account (via `group_members` + `account_groups`)
- User is an **admin** (sees everything)

## Permission Model

| Action | Owner | Group member | Admin |
|--------|-------|-------------|-------|
| View in dashboard | yes | yes | yes |
| View in Dovecot/Roundcube | yes | yes | yes |
| Edit settings | yes | yes | yes |
| Trigger sync | yes | yes | yes |
| Suspend/resume | yes | yes | yes |
| Delete account | yes | no | yes |
| Manage owners | yes | no | yes |

## Backend

### New: `group_service.py`

- `create_group(db, name, owner_id, sso_sync=False) -> Group`
- `update_group(db, group_id, **kwargs) -> Group`
- `delete_group(db, group_id)`
- `add_member(db, group_id, user_id)` / `remove_member(db, group_id, user_id)`
- `set_group_accounts(db, group_id, account_ids: list[str])` — replaces the account list
- `get_user_groups(db, user) -> list[Group]` — groups user belongs to
- `can_manage_group(user, group) -> bool` — True if admin or group owner
- `sync_sso_groups(db, user, sso_group_names: list[str])` — for each MFB group with `sso_sync=True`: if name in sso_group_names, add user; if name not in sso_group_names, remove user. Groups without `sso_sync=True` are never touched.

### Modified: `account_service.py`

**`get_accounts_for_user(db, user)`** — returns accounts where user is owner OR member of a group that has the account. Admins see all. Deduplicated.

**`get_account(db, account_id, user)`** — same logic: user can access if owner or group member or admin.

**New: `is_account_owner(user, account) -> bool`** — checks account_owners only. Used for delete permission and ownership management.

**New: `add_owner(db, account_id, user_id)` / `remove_owner(db, account_id, user_id)`** — manage account_owners entries.

### Modified: `routers/dovecot.py`

Query for user's accounts includes group path:
```sql
SELECT DISTINCT accounts.* FROM accounts
LEFT JOIN account_owners ON ...
LEFT JOIN (account_groups JOIN group_members ON ...) ON ...
WHERE (account_owners.user_id = :uid OR group_members.user_id = :uid)
  AND accounts.enabled = true AND stores.enabled = true
```

### Modified: `routers/auth.py` (OIDC callback)

After user login/provision, call `sync_sso_groups(db, user, groups)` where `groups` comes from the OIDC `groups` claim (already parsed).

## UI

### Admin: `/admin/groups` page

- Table: name, owner, members count, accounts count, SSO sync badge, actions
- Create group form: name, owner (dropdown), SSO sync checkbox
- Expander per group:
  - Members: checkboxes for all users
  - Accounts: checkboxes for all accounts
  - SSO sync toggle
  - Save button

### Group owner view

Same page but filtered to show only groups the user owns. Same edit capabilities (members, accounts).

Non-admin non-owner users do not see `/admin/groups`.

### Account detail: ownership section

In the Edit Account Settings area, new section "Ownership & Visibility":
- **Owners:** list of current owners with remove button (admin or current owner only). Dropdown to add owner.
- **Groups:** list of groups that see this account (read-only for non-admin, admin can modify via group page).

### Profile page

New row in profile table: "Groups" — comma-separated list of group names the user belongs to. Read-only.

### Dashboard

No changes to dashboard layout. `get_accounts_for_user` already powers it — the function change is transparent to the template.

## SSO Sync Behavior

On OIDC login callback, after user provisioning/update:

1. Get `groups` claim from OIDC token (already parsed as `list[str]`)
2. Call `sync_sso_groups(db, user, groups)`
3. For each MFB Group with `sso_sync=True`:
   - If group.name is in SSO groups AND user is not a member → add member
   - If group.name is NOT in SSO groups AND user is a member → remove member
4. Groups with `sso_sync=False` are never modified by SSO login

This means:
- Admin creates group "pippo" with `sso_sync=True`
- Andrea logs in via SSO with groups=["pippo"] → auto-added to group "pippo"
- Giovanni logs in without "pippo" → not added
- Later Giovanni logs in with "pippo" in claims → auto-added
- If Andrea logs in again without "pippo" → auto-removed (because `sso_sync=True`)
- Groups with `sso_sync=False` are managed manually only — SSO has no effect

## Out of Scope

- Per-action granular permissions (e.g. read-only group access)
- Nested groups
- Bulk import of SSO groups
- Group-based store assignment
