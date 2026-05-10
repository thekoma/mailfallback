# MailFallback Information Architecture & Route Inventory

**Audit Date:** 2026-05-10
**Scope:** Complete UI and API route mapping with navigation hierarchy

---

## 1. Top-Level Navigation (Sidebar)

The sidebar is defined in `base.html` and displays conditionally based on user authentication and role. All users see:

| Label | URL | Visibility | Section |
|-------|-----|------------|---------|
| Dashboard | `/` | All authenticated users | Main |
| Accounts | `/accounts` | All authenticated users | Main |
| Restore | `/restore` | All authenticated users | Main |
| Webmail | `{{ webmail_url }}` (external) | When enabled in config | Main |
| **Admin Section** (label divider) | — | Admin only | Admin |
| Users | `/admin/users` | Admin only | Admin |
| Stores | `/admin/stores` | Admin only | Admin |
| Backups | `/admin/backup` | Admin only | Admin |
| Groups | `/admin/groups` | Admin only | Admin |
| System | `/settings` | Admin only | Admin |
| Audit Log | `/admin/audit` | Admin only | Admin |
| **User Section** (divider) | — | All users | Profile |
| [User Name] | `/profile` | All authenticated users | Profile |
| Logout | POST `/api/auth/logout` | All authenticated users | Profile |

**Note:** `/login` is shown when `not user` (unauthenticated); displays login form with optional OIDC button if enabled.

---

## 2. Route Map by Router

### **ui.py** (Main UI Routes)

| Method | Path | Handler | Template | Purpose |
|--------|------|---------|----------|---------|
| GET | `/login` | `login_page` | `login.html` | Show login form with OIDC option |
| POST | `/login` | `login_submit` | `login.html` | Validate credentials and create session |
| GET | `/` | `dashboard` | `dashboard.html` | Show user stats, accounts overview, recent sync jobs |
| GET | `/accounts` | `accounts_page` | `accounts.html` | List accounts (user's own or all if admin) |
| GET | `/partials/accounts-table` | `accounts_table_partial` | `partials/accounts_table.html` | HTMX: Refresh accounts table with sync status |
| GET | `/accounts/{account_id}/sync-status` | `account_sync_status` | `partials/sync_status.html` | HTMX: Sync state indicator |
| GET | `/partials/system-status` | `system_status_partial` | `partials/system_status.html` | HTMX: Admin system health bar (periodic poll) |

### **ui_accounts.py** (Account Management)

| Method | Path | Handler | Template | Purpose |
|--------|------|---------|----------|---------|
| GET | `/accounts/new` | `account_form` | `account_form.html` | Show form to add new account (email/IMAP/OAuth) |
| POST | `/accounts/new` | `account_form_submit` | — | Create account; validate IMAP credentials; redirect to detail |
| GET | `/accounts/{account_id}` | `account_detail` | `account_detail.html` | Show account detail: sync status, folder stats, backup config, migration |
| GET | `/accounts/{account_id}/partials/stats` | `account_stats_partial` | `partials/account_stats.html` | HTMX: Folder-level message/size breakdown |
| GET | `/accounts/{account_id}/partials/live-log` | `account_live_log_partial` | `partials/sync_live_log.html` | HTMX: Live sync output if running; final log if complete |
| GET | `/accounts/{account_id}/partials/sync-panel` | `account_sync_panel` | `partials/sync_panel.html` | HTMX: Hero panel (syncing/idle/error/first-sync/empty/paused/sign-in-needed/migrating) |
| GET | `/accounts/{account_id}/partials/history` | `account_history_partial` | `partials/account_history.html` | HTMX: Recent 150 sync jobs for this account |
| POST | `/accounts/{account_id}/edit` | `account_edit_submit` | — | Update account (IMAP host, port, schedule, extra config) |
| POST | `/accounts/{account_id}/toggle-visible` | `account_toggle_visible` | — | Enable/disable account visibility |
| POST | `/accounts/{account_id}/toggle-suspend` | `account_toggle_suspend` | — | Suspend/unsuspend sync (audit logged) |
| POST | `/accounts/{account_id}/migrate` | `account_migrate` | — | Admin: initiate store migration in background thread |
| GET | `/accounts/{account_id}/migration-progress` | `account_migration_progress` | `partials/migration_progress.html` | HTMX: Progress bar for account store migration |
| POST | `/accounts/{account_id}/cancel-migration` | `account_cancel_migration` | — | Admin: stop account migration |
| POST | `/accounts/{account_id}/add-owner` | `account_add_owner` | — | Admin: assign user as co-owner |
| POST | `/accounts/{account_id}/remove-owner` | `account_remove_owner` | — | Remove user from account ownership |

### **ui_admin.py** (Admin: Users, Stores, Groups, System)

| Method | Path | Handler | Template | Purpose |
|--------|------|---------|----------|---------|
| GET | `/admin/users` | `admin_users_page` | `admin_users.html` | List users; show migration & store assignment state |
| POST | `/admin/users/new` | `admin_create_user` | — | Create user with password and role |
| POST | `/admin/users/{user_id}/edit` | `admin_edit_user` | — | Rename user or change role (admin ↔ user) |
| POST | `/admin/users/{user_id}/password` | `admin_change_user_password` | — | Admin password reset (auth-protected with cooldown) |
| POST | `/admin/users/{user_id}/toggle` | `admin_toggle_user` | — | Enable/disable user account |
| POST | `/admin/users/{user_id}/delete` | `admin_delete_user` | — | Delete user (cannot self-delete) |
| POST | `/admin/users/{user_id}/migrate` | `admin_migrate_user` | — | Start home directory migration to new store |
| GET | `/admin/users/{user_id}/migration-progress` | `migration_progress` | `partials/migration_progress.html` | HTMX: Progress bar |
| POST | `/admin/users/{user_id}/cancel-migration` | `admin_cancel_migration` | — | Stop user home migration |
| POST | `/admin/users/{user_id}/allowed-stores` | `admin_set_allowed_stores` | — | Restrict which stores user can see/use |
| GET | `/admin/stores` | `admin_stores_page` | `admin_stores.html` | List stores with disk usage, orphaned dirs, drain status |
| POST | `/admin/stores/new` | `admin_create_store` | — | Add new storage path with name |
| POST | `/admin/stores/{store_id}/toggle` | `admin_toggle_store` | — | Enable/disable store |
| POST | `/admin/stores/{store_id}/rename` | `admin_rename_store` | — | Change store display name |
| POST | `/admin/stores/{store_id}/set-default` | `admin_set_default_store` | — | Set as default for new users |
| POST | `/admin/stores/{store_id}/delete` | `admin_delete_store` | — | Remove store (must be empty) |
| POST | `/admin/stores/{store_id}/drain` | `admin_drain_store` | — | Migrate all accounts to another store |
| GET | `/admin/stores/{store_id}/drain-progress` | `admin_drain_progress` | `partials/drain_progress.html` | HTMX: Multi-migration progress |
| POST | `/admin/stores/{store_id}/cleanup-orphans` | `admin_cleanup_orphans` | — | Delete user home dirs for deleted users |
| GET | `/admin/groups` | `admin_groups_page` | `admin_groups.html` | List groups; admins see all, users see only owned groups |
| POST | `/admin/groups/new` | `admin_create_group` | — | Create group with optional SSO sync flag |
| POST | `/admin/groups/{group_id}/edit` | `admin_edit_group` | — | Assign members and accounts to group |
| POST | `/admin/groups/{group_id}/delete` | `admin_delete_group_route` | — | Remove group |
| GET | `/settings` | `settings_page` | `settings.html` | Admin dashboard: totals, scheduler status, feature flags, system health |
| POST | `/admin/dovecot/health-check` | `admin_dovecot_health` | — | Test Dovecot IMAP health; redirect with status |
| POST | `/admin/dovecot/fts-reindex` | `admin_fts_reindex` | — | Trigger full-text search reindex task |
| POST | `/admin/dovecot/force-resync` | `admin_force_resync` | — | Force all accounts to re-sync |

### **ui_audit.py** (Audit Log)

| Method | Path | Handler | Template | Purpose |
|--------|------|---------|----------|---------|
| GET | `/admin/audit` | `admin_audit_page` | `admin_audit.html` | Paginated audit log with filters (user, action, date range) |
| GET | `/admin/audit/table` | `admin_audit_table` | `partials/audit_table.html` | HTMX: Audit table with dynamic filtering |

### **ui_backup.py** (Backup Destinations & Account Backups)

| Method | Path | Handler | Template | Purpose |
|--------|------|---------|----------|---------|
| GET | `/admin/backup` | `admin_backup_page` | `admin_backup.html` | List all backup destinations; show account counts per destination |
| POST | `/admin/backup/new` | `admin_create_backup_destination` | — | Create S3 or local restic depot (test before save) |
| POST | `/admin/backup/{dest_id}/edit` | `admin_edit_backup_destination` | — | Update S3 credentials or local path |
| POST | `/admin/backup/{dest_id}/delete` | `admin_delete_backup_destination` | — | Remove if no accounts reference it |
| POST | `/admin/backup/{dest_id}/test` | `admin_test_backup_destination` | — | Verify connection to S3 or local path |
| POST | `/accounts/{account_id}/backup/configure` | `account_backup_configure` | — | Assign account to destination; set schedule & retention |
| POST | `/accounts/{account_id}/backup/now` | `account_backup_now` | — | Manually trigger backup for account |
| GET | `/accounts/{account_id}/backup/snapshots` | `account_backup_snapshots` | `partials/backup_snapshots.html` | HTMX: List restic snapshots; show restore buttons |
| POST | `/accounts/{account_id}/backup/restore/{snapshot_id}` | `account_backup_restore` | — | Extract snapshot to temp dir; create suspended account referencing it |

### **ui_restore.py** (Restore UI)

| Method | Path | Handler | Template | Purpose |
|--------|------|---------|----------|---------|
| GET | `/restore` | `restore_page` | `restore.html` | Show running and completed restore jobs; account selector |
| GET | `/restore/partials/running` | `restore_running_partial` | `partials/restore_running.html` | HTMX: In-progress restores with progress bars |
| GET | `/restore/partials/folders` | `restore_folders_partial` | `partials/restore_folders.html` | HTMX: List mailboxes in source account |
| GET | `/restore/partials/separator-warning` | `restore_separator_warning_partial` | — | HTMX: Warn if destination uses "." as folder separator |
| GET | `/restore/partials/messages` | `restore_messages_partial` | `partials/restore_messages.html` | HTMX: Search & display message headers from source folders |
| GET | `/restore/partials/progress` | `restore_progress_partial` | `partials/restore_progress.html` | HTMX: Individual job progress (counts, status) |

### **ui_profile.py** (User Profile)

| Method | Path | Handler | Template | Purpose |
|--------|------|---------|----------|---------|
| GET | `/profile` | `profile_page` | `profile.html` | User settings: store selection, groups membership, password change |
| POST | `/profile/store` | `profile_change_store` | — | Migrate user home to different allowed store |
| POST | `/profile/password` | `profile_change_password` | — | Change own password (verify current password) |
| PATCH | `/api/preferences` | `update_preferences` | — | JSON: Update theme (light/dark) preference |

### **auth.py** (Authentication & OAuth)

| Method | Path | Handler | Template | Purpose |
|--------|------|---------|----------|---------|
| POST | `/api/auth/login` | `login` | — | JSON: Authenticate user; return role |
| POST | `/api/auth/logout` | `logout` | — | Clear session; if HTMX, redirect header |
| GET | `/auth/google/start` | `google_oauth_start` | — | Redirect to Google OAuth consent screen |
| GET | `/auth/google/callback` | `google_oauth_callback` | — | Exchange code for token; store in account.credentials |
| GET | `/auth/microsoft/start` | `microsoft_oauth_start` | — | Redirect to Microsoft OAuth consent screen |
| GET | `/auth/microsoft/callback` | `microsoft_oauth_callback` | — | Exchange code for token; store in account.credentials |
| GET | `/auth/oidc/login` | `oidc_login` | — | OIDC redirect (if enabled) |
| GET | `/auth/oidc/callback` | `oidc_callback` | — | Create or update user from OIDC claims; sync groups |

### **restore.py** (REST API: Restore & Browse)

**Prefix: `/api/restore`** — JSON endpoints for restore job management

| Method | Path | Handler | Purpose |
|--------|------|---------|---------|
| POST | `` | `create_restore` | Create and submit restore job; return job_id |
| GET | `/{job_id}` | `get_restore` | Get restore job status, message counts, error |
| POST | `/{job_id}/cancel` | `cancel_restore` | Request cancellation of running job |

**Prefix: `/api`** (browse_router) — JSON endpoints for mailbox/message browsing

| Method | Path | Handler | Purpose |
|--------|------|---------|---------|
| GET | `/accounts/{account_id}/mailboxes` | `list_mailboxes` | List all folders in account via Dovecot IMAP |
| GET | `/accounts/{account_id}/mailboxes/{folder:path}/messages` | `list_messages` | Paginate messages in folder (50/page max) |
| GET | `/accounts/{account_id}/mailboxes/{folder:path}/search` | `search_messages` | Text search within folder (max 100 results) |

---

## 3. Page Hierarchy Diagram

```
LOGIN (/login)
  └─ OIDC (if enabled)

AUTHENTICATED ROOT
  ├─ DASHBOARD (/)
  │   ├─ System Status Bar (admin only, HTMX poll)
  │   ├─ Stats Cards (accounts, messages, storage)
  │   └─ Recent Sync Jobs list
  │
  ├─ ACCOUNTS (/accounts)
  │   ├─ Account List [HTMX table refresh on sync changes]
  │   │
  │   ├─ NEW ACCOUNT (/accounts/new)
  │   │   ├─ Form: Email, IMAP Host, Auth Type
  │   │   ├─ OAuth: Google → /auth/google/start
  │   │   └─ OAuth: Microsoft → /auth/microsoft/start
  │   │
  │   └─ ACCOUNT DETAIL (/accounts/{account_id})
  │       ├─ Sync Panel [HTMX with live log if syncing]
  │       │   ├─ Hero state (idle/syncing/error/empty/paused/first-sync/migrating)
  │       │   └─ Live log output or last error
  │       ├─ Folder Stats [HTMX]
  │       ├─ Sync History [HTMX: 150 jobs]
  │       ├─ Account Settings
  │       │   ├─ IMAP config
  │       │   ├─ Sync schedule
  │       │   └─ Advanced mbsync settings
  │       ├─ Backup Config (if backup destination configured)
  │       │   ├─ Snapshots list [HTMX]
  │       │   └─ Restore from snapshot
  │       ├─ User Ownership (admin can add/remove owners)
  │       └─ Store Migration (admin can move account to different store)
  │
  ├─ RESTORE (/restore)
  │   ├─ Running Jobs [HTMX poll]
  │   ├─ Completed/Failed Jobs list
  │   ├─ Source Account selector → folders [HTMX]
  │   │   ├─ Folder Browser [HTMX]
  │   │   └─ Message Search [HTMX]
  │   └─ Target Account selector
  │       └─ Restore Mode & Folder Mapping
  │
  ├─ PROFILE (/profile)
  │   ├─ Store Selection (if user has multiple allowed stores)
  │   ├─ Group Membership (read-only display)
  │   └─ Change Password form
  │
  ├─ ADMIN → USERS (/admin/users) [admin only]
  │   ├─ User list with role, enabled status
  │   ├─ New User form
  │   ├─ Per-user actions:
  │   │   ├─ Edit username/role
  │   │   ├─ Change password
  │   │   ├─ Enable/disable
  │   │   ├─ Delete
  │   │   ├─ Migrate home to store
  │   │   └─ Set allowed stores
  │   └─ Migration progress [HTMX]
  │
  ├─ ADMIN → STORES (/admin/stores) [admin only]
  │   ├─ Store list with usage & orphan dirs
  │   ├─ New Store form
  │   ├─ Per-store actions:
  │   │   ├─ Enable/disable
  │   │   ├─ Rename
  │   │   ├─ Set as default
  │   │   ├─ Delete (if empty)
  │   │   ├─ Drain to target store [HTMX progress]
  │   │   └─ Cleanup orphaned user homes
  │
  ├─ ADMIN → BACKUPS (/admin/backup) [admin only]
  │   ├─ Backup Destination list (S3 or local path)
  │   ├─ New Destination form
  │   ├─ Per-destination actions:
  │   │   ├─ Edit config
  │   │   ├─ Test connection
  │   │   └─ Delete (if no accounts reference it)
  │   └─ Account count per destination
  │
  ├─ ADMIN → GROUPS (/admin/groups) [admin only / group owners]
  │   ├─ Group list
  │   ├─ New Group form
  │   └─ Per-group:
  │       ├─ Edit members
  │       ├─ Assign accounts
  │       └─ Delete
  │
  ├─ ADMIN → SYSTEM (/settings) [admin only]
  │   ├─ System stats (users, accounts, stores)
  │   ├─ Scheduler status
  │   ├─ Feature flags (OIDC, Webmail, Tika enabled)
  │   └─ System actions:
  │       ├─ Dovecot health check
  │       ├─ FTS reindex
  │       └─ Force resync
  │
  └─ ADMIN → AUDIT LOG (/admin/audit) [admin only]
      ├─ Filtered log (user, action, date range)
      └─ Pagination with HTMX

LOGOUT (/api/auth/logout)
```

---

## 4. Per-Screen Feature Inventory

### Dashboard (`/`)

**Purpose:** Provide quick overview of mail backup status. Show total accounts, messages, storage consumed; highlight accounts needing attention (errors, stale syncs); list recent sync jobs.

**Widgets/Sections:**
- System Status bar (admin only, HTMX polled every 5s): Dovecot health, FTS reindex, force-resync, active syncs, restore jobs, backup jobs
- Statistics cards: accounts count, total messages, total storage, (admin) total users, total stores, storage capacity
- Attention items: error accounts (red), stale accounts (yellow, >7 days since sync)
- Recent Jobs table: 5 latest sync jobs across user's accounts

**User Actions:**
- Click account in attention list → /accounts/{id}
- Click recent job → /accounts/{id}/partials/history (implied, view full history)
- (Admin) View system health → trigger health checks, FTS reindex, force-resync

**Core Concepts Touched:**
- **Source** (accounts status)
- **Local backup** (sync state, messages backed up)

---

### Accounts List (`/accounts`)

**Purpose:** Browsable list of all accounts owned/accessible by user (admin sees all if show_all=1).

**Widgets/Sections:**
- Table: account name, email, provider logo, last sync time (with color-coded freshness), sync state (idle/syncing/error), message count, size
- Show All toggle (admin only)
- New Account button → /accounts/new

**User Actions:**
- Click account row → /accounts/{id}
- Click New Account → /accounts/new
- (Admin) Toggle "show all users' accounts"

**Core Concepts Touched:**
- **Source** (email accounts)
- **Local backup** (sync state, message counts)

---

### Account Detail (`/accounts/{id}`)

**Purpose:** Central hub for account management. Monitor sync, configure backup, manage ownership, migrate store.

**Widgets/Sections:**
- Sync Panel (hero): displays 8 states (idle/syncing/error/empty/first-sync/paused/sign-in-needed/migrating) with action buttons (sync now, pause, resume, sign in, retry)
- Live Log (if syncing) or last error message (if error)
- Folder Stats [HTMX]: breakdown by folder (messages, size)
- Sync History [HTMX]: 150 most recent sync jobs, expandable logs
- Account Settings section: edit IMAP host, port, password, schedule, TLS type, advanced mbsync directives
- Backup Configuration [HTMX]: select destination, set schedule (cron), retention preset, trigger manual backup
- Backup Snapshots [HTMX]: list restic snapshots, download/restore buttons
- User Ownership (admin): add/remove co-owners from account
- Store Migration (admin): move account to different store with progress bar

**User Actions:**
- Sync now
- Pause/resume sync
- Sign in with OAuth (Google/Microsoft)
- Edit sync settings, IMAP config
- Configure backup destination
- Trigger manual backup
- Restore from backup snapshot
- (Admin) Assign/remove owners
- (Admin) Migrate account to another store

**Core Concepts Touched:**
- **Source** (IMAP credentials, server config)
- **Local backup** (sync state, folder structure, message count)
- **Remote depot** (if backup configured: destination, schedule, retention, snapshots)
- **Snapshots** (restic snapshots of this account)

---

### Restore Page (`/restore`)

**Purpose:** Browse backed-up accounts and restore individual messages to destination account.

**Widgets/Sections:**
- Running Jobs section [HTMX polled]: progress bars for active restore jobs
- Completed/Failed Jobs table: list all non-running restore jobs
- Source Account selector: dropdown of accounts user owns [HTMX]
  - Triggers folder browser [HTMX] showing all mailbox folders
- Message Search [HTMX]: search within selected folder or across all folders
  - Search criteria: subject, from, to, body, date range, attachment
  - Shows up to 100 matching message headers
- Folder separator warning [HTMX]: alerts if destination uses "." separator
- Target Account selector: dropdown of destination accounts user owns
- Restore Mode selector: full (all messages) or selective (chosen folders/messages)
- Folder Mapping mode: original names or flat/prefixed
- Submit button: POST /api/restore to create job

**User Actions:**
- Select source account → browse folders [HTMX]
- Search messages by various criteria [HTMX]
- Select target account
- Choose restore mode and folder mapping
- Submit restore → job created, HTMX monitor progress

**Core Concepts Touched:**
- **Source** (messages being restored from)
- **Local backup** (destination account in local store)

---

### Admin → Users (`/admin/users`)

**Purpose:** Manage system users, roles, passwords, allowed stores, migration.

**Widgets/Sections:**
- User table: username, role (user/admin), enabled status, store assignment, actions
- New User form: username, password, role, initial store
- Per-user actions (dropdown/buttons):
  - Edit username or role
  - Reset password (requires admin password verification or cooldown bypass)
  - Enable/disable user
  - Delete user
  - Migrate user home to another store [with progress bar HTMX]
  - Set allowed stores (multi-select)
- Migration Progress [HTMX]

**User Actions:**
- Create new user
- Edit user properties
- Reset user password
- Enable/disable user
- Delete user
- Assign/restrict user's allowed stores
- Migrate user's home directory to another store

**Core Concepts Touched:**
- **Local backup** (user home directories, per-user store assignments)
- **Snapshots** (implied: user home migrations)

---

### Admin → Stores (`/admin/stores`)

**Purpose:** Manage mail storage locations (filesystem paths). Monitor disk usage. Migrate accounts/users between stores.

**Widgets/Sections:**
- Store table: name, enabled status, path, disk usage (used/total/free), default flag, actions
- Orphaned directories list per store (user homes for deleted users)
- New Store form: name, path
- Per-store actions:
  - Enable/disable
  - Rename
  - Set as default
  - Delete (must be empty)
  - Drain to target store [shows multi-account migration progress HTMX]
  - Cleanup orphaned directories

**User Actions:**
- Add new store
- Enable/disable store
- Rename store
- Set default store for new users
- Delete empty store
- Migrate all accounts from one store to another [HTMX progress]
- Clean up orphaned user home directories

**Core Concepts Touched:**
- **Local backup** (all messages stored in store filesystem)

---

### Admin → Backups (`/admin/backup`)

**Purpose:** Configure offsite backup destinations (restic repositories). Manage which accounts use which destinations.

**Widgets/Sections:**
- Backup Destination table: name, backend type (S3 or local), account count using it, actions
- New Destination form: name, backend type toggle, credentials (S3: endpoint/bucket/access key/secret key; local: path), restic password, insecure TLS flag
- Per-destination actions:
  - Edit credentials/password
  - Test connection
  - Delete (if no accounts reference it)

**User Actions:**
- Create new backup destination (S3 or local path)
- Edit destination configuration
- Test destination connectivity
- Delete unused destination

**Core Concepts Touched:**
- **Remote depot** (S3 buckets or local restic repositories)

---

### Admin → Groups (`/admin/groups`)

**Purpose:** Create logical groups of users and accounts. Optionally sync membership from OIDC groups.

**Widgets/Sections:**
- Group list: name, member count, account count, owner, SSO sync flag
- New Group form: name, owner selection, SSO sync toggle
- Per-group:
  - Edit members (multi-select users)
  - Edit accounts (multi-select accounts; non-admins can only add own)
  - Delete group

**User Actions:**
- Create group
- Add/remove members
- Assign accounts to group
- Delete group
- (Admin) Enable/disable SSO sync for group

**Core Concepts Touched:**
- None (groups are metadata/access control, not directly related to backup concepts)

---

### Admin → System (`/settings`)

**Purpose:** View system health and trigger maintenance tasks.

**Widgets/Sections:**
- System statistics: total users, total accounts, total stores
- Scheduler status: running yes/no, number of scheduled jobs
- Feature flags: OIDC enabled, Webmail enabled, Tika enabled, debug mode
- System actions:
  - Dovecot health check → shows health status, error if unhealthy
  - FTS (Full-Text Search) reindex → triggers background task, shows status
  - Force resync → triggers background task to re-sync all accounts

**User Actions:**
- Run Dovecot health check
- Trigger FTS reindex
- Trigger full-system resync

**Core Concepts Touched:**
- **Local backup** (all stored messages, FTS affects Dovecot search)

---

### Admin → Audit Log (`/admin/audit`)

**Purpose:** Review system actions for compliance and debugging.

**Widgets/Sections:**
- Filter form: by username, action type, date range
- Paginated audit log table (50 rows/page): timestamp, username, action (labeled), resource type, resource ID, resource name, IP address, details JSON
- Pagination controls

**User Actions:**
- Filter log by user, action, date
- Navigate pages
- View detailed action information

**Core Concepts Touched:**
- None (purely administrative/compliance)

---

### User Profile (`/profile`)

**Purpose:** User self-service settings.

**Widgets/Sections:**
- Store selection: if user has multiple allowed stores, dropdown to switch primary store
- Group membership: read-only display of groups user belongs to
- Change Password form: current password, new password, confirm new password

**User Actions:**
- Change primary store (if multiple allowed)
- Change password

**Core Concepts Touched:**
- **Local backup** (switching stores changes where future data is backed up)

---

### Login (`/login`)

**Purpose:** Authenticate user into system.

**Widgets/Sections:**
- Username/password form
- (Optional) OIDC login button (if enabled)
- Error message if login failed

**User Actions:**
- Submit credentials
- Or login via OIDC

**Core Concepts Touched:**
- None (authentication gate)

---

## 5. Workflow Inventory

### Workflow 1: First-Time Install → First Synced Account

**Path:**
1. Admin logs in with default admin user (created at startup)
2. Dashboard shows "0 accounts, 0 messages, 0 storage"
3. Admin navigates `/admin/stores` → confirms default store exists (path: `/data/mailboxes`)
4. Admin navigates `/admin/users` → creates user "john" with password
5. John logs in; Dashboard shows his "0 accounts"
6. John navigates `/accounts` → clicks "New Account"
7. John fills form: name="Gmail Account", email="john@gmail.com", auth_type="oauth2"
8. John clicks "Sign in with Google" → /auth/google/start → redirects to Google → /auth/google/callback stores token
9. John redirected to `/accounts/{account_id}` → sync panel shows "Empty" state (no syncs yet)
10. John clicks "Sync Now" → sync starts, live log streams, folder stats populate
11. After sync complete, sync panel shows "Idle", folder stats display totals
12. Dashboard now shows "1 account, XXX messages, XXX GB"

**Key Screens Traversed:**
- /login → / → /accounts → /accounts/new → Google OAuth → /accounts/{id}

---

### Workflow 2: Adding a Gmail Account

**Path:**
1. User at `/accounts` clicks "New Account"
2. User fills: name="Work Email", email="user@company.com", provider="google"
3. User clicks "Sign in with Google"
4. Browser redirects to Google consent screen
5. After consent, redirected to /auth/google/callback with code
6. Code exchanged for OAuth token; stored in account.credentials
7. User at /accounts/{id}; sync panel shows "Empty"
8. User clicks "Sync Now" or wait for scheduled sync
9. Account syncs; panel transitions to "Idle" when complete

**Key Screens Traversed:**
- /accounts/new → Google OAuth → /accounts/{id}

---

### Workflow 3: Configure Offsite Restic Backup for an Account

**Path:**
1. Admin at `/admin/backup` (no destinations exist yet)
2. Admin clicks "New Destination"
3. Admin fills form: name="AWS S3", backend_type="s3", endpoint="s3.amazonaws.com", bucket="my-backups", access_key="...", secret_key="...", restic_password="..."
4. Admin clicks "Create" → destination tested; if OK, saved; if fail, error shown
5. User navigates `/accounts/{id}` → scrolls to "Backup Configuration" widget
6. User selects destination "AWS S3", schedule "0 2 * * *" (daily 2 AM), retention="standard"
7. User clicks "Save" → /accounts/{id}/backup/configure
8. User clicks "Backup Now" to trigger first backup
9. After backup completes, "Backup Snapshots" widget shows snapshot entries
10. User can download snapshot or click "Restore" to restore as new account

**Key Screens Traversed:**
- /admin/backup → /accounts/{id} → backup configured

---

### Workflow 4: Doing a Manual Backup

**Path:**
1. User at `/accounts/{id}`, sees "Backup Configuration" widget showing destination "S3"
2. User clicks "Backup Now" button
3. POST /accounts/{id}/backup/now triggers background backup worker
4. Flash message: "Backup started"
5. User refreshes or navigates to /accounts/{id} → snapshots list updates [via HTMX]

**Key Screens Traversed:**
- /accounts/{id} (manual action)

---

### Workflow 5: Restoring from a Snapshot (Backup-Based Restore)

**Path:**
1. User at `/accounts/{id}`, "Backup Snapshots" widget shows snapshots
2. User clicks "Restore" button on a snapshot
3. POST /accounts/{id}/backup/restore/{snapshot_id} extracts snapshot to temp dir, creates suspended account
4. New account appears in /accounts list labeled "Backup {original} (2026-05-10)"
5. User can view this new account's folders via Dovecot read-only access

**Key Screens Traversed:**
- /accounts/{id} → snapshot restored → new account in /accounts

---

### Workflow 6: Restoring Messages (From Account → Account)

**Path:**
1. User navigates `/restore`
2. User selects source account dropdown → list of user's accounts
3. Source account triggers [HTMX] folder browser showing all folders
4. User clicks folder (or "All Folders") → [HTMX] message search form appears
5. User types search query "budget 2026" → [HTMX] searches folder(s), shows up to 100 matching messages
6. User reviews messages, decides which to restore
7. User selects target account dropdown
8. User selects restore mode (full or selective) and folder mapping
9. User clicks "Restore" → POST /api/restore creates job, starts background worker
10. Running Jobs section [HTMX polled] shows progress bar with message counts
11. When complete, job moves to Completed Jobs table

**Key Screens Traversed:**
- /restore (primary interaction, multiple HTMX refreshes)

---

### Workflow 7: Migrating a Store (Account → Store)

**Path:**
1. Admin at `/admin/stores` sees store "Store-A" is 80% full
2. Admin wants to move accounts to "Store-B"
3. Admin clicks "Drain to Store-B" on Store-A
4. Dialog prompts for target store; admin selects "Store-B"
5. Admin clicks "Drain" → POST /admin/stores/{store_id}/drain creates migrations
6. Drain Progress [HTMX polled] shows multi-account migration status
7. When all migrations complete, accounts now on Store-B

**Key Screens Traversed:**
- /admin/stores (primary interaction)

---

### Workflow 8: Recovering from a Sync Error

**Path:**
1. User at `/` (Dashboard) sees attention item: "Account: Sync failed: [error message]"
2. User clicks account name → /accounts/{id}
3. Sync panel shows "Error" state with error message
4. User clicks "Retry" button (or "Update Credentials" if auth failed)
5. If auth issue, user may click "Sign in with Google" to re-auth
6. Sync restarts; live log appears; on success, panel transitions to "Idle"

**Key Screens Traversed:**
- / → /accounts/{id} (troubleshooting)

---

### Workflow 9: Adding a New System User

**Path:**
1. Admin at `/admin/users` clicks "New User"
2. Admin fills: username="alice", password="...", role="user", initial_store="default"
3. Admin clicks "Create" → user created, assigned to default store
4. Flash message: "User alice created"
5. Alice can now log in; sees her own dashboard with "0 accounts"
6. Alice navigates /accounts/new to add first account

**Key Screens Traversed:**
- /admin/users (create action)

---

## 6. Concept-to-Screen Quick Reference

| Concept | Definition | Primary Screen | Secondary Screens |
|---------|-----------|---|---|
| **Source** | Email account (IMAP server) being backed up | `/accounts/{id}` (IMAP config, auth, sync state) | `/accounts` (list), `/auth/google/*`, `/auth/microsoft/*` (OAuth), `/restore` (source account selector) |
| **Local Backup** | Maildir stored on local filesystem in MailStore | `/accounts/{id}` (sync panel, folder stats), `/` (dashboard totals), `/admin/stores` (store capacity), `/profile` (store selection) | `/admin/users` (per-user store), `/accounts` (account list shows size) |
| **Remote Depot** | Restic repository (S3 or local path) for offsite backup | `/admin/backup` (list/config destinations) | `/accounts/{id}` (backup configuration, snapshot list), `/admin/backup/new`, `/admin/backup/{dest_id}/edit` |
| **Snapshots** | Point-in-time backup of account maildir | `/accounts/{id}` (snapshot list via HTMX), `/accounts/{id}/backup/snapshots` | `/accounts/{id}/backup/restore/{snapshot_id}` (restore action) |

---

## Summary Statistics

- **Total UI Routes:** 40+
- **Total API Routes:** 10+
- **Total Templates:** 35+ (3 main pages, 30+ partials)
- **Admin-Only Pages:** 6 (/admin/users, /admin/stores, /admin/backup, /admin/groups, /admin/audit, /settings)
- **HTMX-Driven Partials:** 16 (live log, stats, snapshots, progress bars, etc.)
- **Authentication Methods:** 3 (Username/password, Google OAuth2, Microsoft OAuth2, OIDC)
- **Background Jobs:** Sync, backup, restore, FTS reindex, force resync, migrations
- **Core Workflows:** 9 documented (install → first sync, account add, backup configure, manual backup, snapshot restore, message restore, store migration, error recovery, user creation)

---

**Document Generated:** 2026-05-10
**Word Count:** ~2,450
**Last Verified Routes:** All routers read (ui.py, ui_accounts.py, ui_admin.py, ui_audit.py, ui_backup.py, ui_restore.py, ui_profile.py, auth.py, restore.py)
