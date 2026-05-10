# Data model — naming inventory

## Method

Analyzed `/home/koma/src/mailfallback/src/mailfallback/models.py` (347 lines) to extract all SQLAlchemy models and enum definitions. Cross-referenced 10 alembic migration files (001–010) to understand schema evolution. Scanned 20+ template files (Jinja2) in `/src/mailfallback/templates/` to identify how enum `.value` properties are directly compared in conditionals (e.g., `if account.sync_state.value == 'syncing'`). Examined backup/store/account templates to map model names to user-facing terminology.

## Model summary

| Model Name | Table Name | Purpose | Key Columns |
|---|---|---|---|
| User | users | System user with OIDC + app password auth | username, role (admin/user), store_id, migrating, oidc_subject |
| MailStore | mail_stores | Base directory for mailbox storage (Maildir/Dovecot homes) | name, path, is_default, enabled |
| Account | accounts | IMAP mail source + config (email backup target) | name, email_address, imap_host, sync_state, store_id, migrating, suspended |
| SyncJob | sync_jobs | Audit trail of sync attempts (mbsync runs) | status (pending/running/completed/failed/cancelled), account_id, log_path |
| Group | groups | User collection for permission/sync delegation | name, owner_id, sso_sync |
| StoreMigration | store_migrations | Batch copy operation (one User/Account → new MailStore) | source_store_id, target_store_id, status (pending/copying/verifying/cleaning/completed/failed) |
| RestoreJob | restore_jobs | IMAP restore: copy messages from source to target account | status (JobStatus), restore_mode (full/folder/selection), source_account_id, target_account_id |
| AuditLog | audit_logs | Immutable record of user actions | user_id, username, action, resource_type, resource_id |
| BackgroundTask | background_tasks | Generic async task wrapper | task_type (string), status (TaskStatus) |
| BackupDestination | backup_destinations | Offsite backup target (S3 or local) | name, backend_type (s3/local), s3_bucket, local_path |
| AccountBackup | account_backups | Per-account backup schedule config (NOT a snapshot) | account_id, destination_id, schedule, retention_preset, last_status (BackupStatus) |

## Enums in detail

### UserRole
- **Values:** admin, user
- **Used in:** User.role column; templates check `user.role.value == "admin"` throughout (admin_groups.html, accounts.html, etc.)
- **Definition:** Role-based access control for web interface and API

### AuthType
- **Values:** oauth2, app_password
- **Used in:** Account.auth_type column
- **Definition:** IMAP authentication method for mail source
- **Template usage:** `account.auth_type.value == "oauth2"` triggers reauth flows or custom UI (account_detail.html, sync_panel.html)

### SyncState
- **Values:** idle, syncing, error
- **Used in:** Account.sync_state column (tracks ongoing or failed mbsync process)
- **Definition:** Real-time state of mail synchronization
- **Template usage:** `account.sync_state.value == 'syncing'` triggers polling UI; `== 'error'` shows error badge (account_detail.html, accounts_table.html)

### JobStatus
- **Values:** pending, running, completed, failed, cancelled
- **Used in:** SyncJob.status, RestoreJob.status
- **Definition:** Lifecycle state of a discrete job execution
- **Template usage:** `job.status.value == 'completed'` mapped to CSS class `badge-idle`; complex ternary in restore_history.html checks `.value == 'completed' and failed_messages > 0` for "partial" badge

### MigrationStatus
- **Values:** pending, copying, verifying, cleaning, completed, failed
- **Used in:** StoreMigration.status
- **Definition:** Multi-stage progress tracking for Maildir-to-MailStore migration
- **Template usage:** `migration.status.value == "failed"` and `== "completed"` in migration_progress.html

### TaskStatus
- **Values:** pending, running, completed, failed
- **Used in:** BackgroundTask.status
- **Definition:** Generic async task state (for future extensibility)

### RestoreMode
- **Values:** full, folder, selection
- **Used in:** RestoreJob.restore_mode
- **Definition:** Scope of IMAP restore operation
- **No direct template enum comparison** (form dropdown uses string values)

### BackendType
- **Values:** s3, local
- **Used in:** BackupDestination.backend_type
- **Definition:** Storage backend for offsite backup
- **Template usage:** `dest.backend_type.value == "s3"` in account_backup.html (lines 22, 90) triggers S3-specific UI for endpoint/bucket

### RetentionPreset
- **Values:** light, standard, full, custom
- **Used in:** AccountBackup.retention_preset
- **Definition:** Restic backup retention policy (days/weeks/months of snapshots to keep)
- **Template usage:** `backup_config.retention_preset.value` piped to `| capitalize` filter in account_backup.html (line 63); form dropdown uses presets (lines 2–9)

### BackupStatus
- **Values:** idle, running, completed, failed
- **Used in:** AccountBackup.last_status (last restic run state)
- **Definition:** Outcome of most recent backup operation (distinct from `SyncState` which is live sync)
- **Template usage:** `backup_config.last_status.value == "completed"` / `"running"` / `"failed"` in account_backup.html (lines 48–56) controls status badge text and icon

---

## Relationships involving the four core concepts

```
┌────────────────────────────────────────────────────────┐
│                      User                              │
│  (system login; has Dovecot home on one MailStore)     │
└──────────────┬────────────────────────────────┬────────┘
               │ 1:1                    N:M     │
               │ store_id              allows    │
               │ (User.store)                    │
               │                                 │ user_allowed_stores
               ▼                                 ▼
        ┌─────────────────┐             ┌──────────────────┐
        │  MailStore      │ ◄───────────┤  MailStore       │
        │ (base dir)      │  1:N         │ (allowed for)    │
        └────┬────────────┘ accounts     └──────────────────┘
             │
             │ 1:N (store_id FK)
             │
             ▼
        ┌──────────────────────┐
        │     Account          │
        │ (IMAP mail source)   │
        └────┬──────┬──────────┘
             │      │
             │ 1:N  │ N:M
             │      └──────────────────────┐
             │                             │ account_owners
             ▼                             ▼
        ┌──────────────┐          ┌─────────────────┐
        │  SyncJob     │          │ User (owners)   │
        │  (log)       │          │                 │
        └──────────────┘          └─────────────────┘

        Account also has:
        • 1:M → RestoreJob (source or target)
        • 1:M → AccountBackup (per-destination config)
        • 1:M → StoreMigration (user or account migration)

┌─────────────────────────────────────────────┐
│         BackupDestination                   │
│      (S3 bucket / local path)               │
└─────────┬──────────────────────────────────┘
          │ 1:M (destination_id FK)
          │
          ▼
┌────────────────────────────────┐
│    AccountBackup               │
│  (per-Account schedule config) │
│  NOT a snapshot               │
└────────────────────────────────┘

RestoreJob: Account → Account
StoreMigration: MailStore → MailStore (with optional User/Account scope)
Group: owns Groups, has Users (N:M via group_members), owns Accounts (N:M via account_groups)
```

---

## Naming friction

### 1. MailStore vs "Stores" sidebar term

| Dimension | Details |
|-----------|---------|
| **Model name** | MailStore (PascalCase class) |
| **Column reference** | Account.store_id, User.store_id |
| **User-facing sidebar** | "Mail Stores" (page heading: admin_stores.html line 2) |
| **UI terminology** | "Stores" (admin_stores.html: "Stores are base directories…") |
| **What it is** | Base directory path(s) on disk where mailboxes + Dovecot homes live |
| **Severity** | **MED** — Code reads "store_id" but UI says "Mail Stores". User cannot guess that "Store" = base filesystem path. |
| **Renamability cost** | **MED** — Would require: renaming class MailStore → Store (convention shift), updating Account/User FK column names, template adjustments. No Alembic migration needed if enum values don't leak to API. |

### 2. BackupDestination vs "backup" (loose term)

| Dimension | Details |
|-----------|---------|
| **Model name** | BackupDestination |
| **DB table** | backup_destinations |
| **What it is** | Reusable config: S3 bucket or local path where restic repos live |
| **Competing term** | "backup" (AccountBackup, BackupStatus, last_backup_at, "Backup Now" button) |
| **Confusion risk** | User sees "Enable Backup" button but the form requests a "Destination" dropdown. "Destination" is not a word in the UX narrative. "Backup" is overloaded: destination vs. snapshot vs. config vs. operation. |
| **Severity** | **HIGH** — Leaks implementation (restic abstraction: destination → snapshot → retention) into UI without explanation. Forms do not label "where backups go" vs. "what snapshots keep". |
| **Renamability cost** | **HIGH** — Rename would ripple: BackupDestination → BackupRepository or BackupTarget, update 10+ template references, Alembic migration to rename table, test suite. Alternative: keep class name, rebrand UI labels from "Destination" to "Repository" or "Backup Target". |

### 3. AccountBackup (config) vs. actual snapshots

| Dimension | Details |
|-----------|---------|
| **Model name** | AccountBackup |
| **What it stores** | Cron schedule, retention policy, enabled flag, last run status/timestamp |
| **What it does NOT store** | Actual snapshot metadata, deduplication indices, or restore trees |
| **User confusion** | User configures backup for an account (create AccountBackup row). But "snapshots" are fetched asynchronously via `/accounts/{id}/backup/snapshots` (account_backup.html line 113–115). No model in ORM tracks snapshots themselves. |
| **Severity** | **MED** — Form says "Enable Backup" (implies snapshot is created), but backend must call restic to list snapshots. User expects one model, not schedule config + external state. |
| **Renamability cost** | **MED** — Rename AccountBackup → BackupSchedule or BackupConfig clarifies intent. Would require: model rename, Alembic table rename, template updates (forms, display logic). Snapshots stay external to ORM. |

### 4. SyncJob vs. broader "sync" concept

| Dimension | Details |
|-----------|---------|
| **Model name** | SyncJob |
| **What it is** | Single mbsync invocation record: has status, log, timestamps, exit code |
| **Related concepts** | Account.sync_state (live state: idle/syncing/error), Account.last_sync_at (timestamp), Account.last_error (text) |
| **Confusion** | User clicks "Sync All" button (accounts.html line 17). This spawns N SyncJobs (one per account). But the job status is not the same as Account.sync_state. And "last_error" on Account may not match the SyncJob log. |
| **Severity** | **MED** — Internal: clear separation. But user sees "sync state = error" and must dig into SyncJob history to understand. No unified "what happened in the last sync attempt" model. |
| **Renamability cost** | **LOW** — Keep class name, but clarify labeling in UI: "Sync Attempts" instead of "Sync Jobs"; show SyncJob exit_code + signal to user so they understand "syncing" ≠ "job running". |

### 5. StoreMigration vs. BackgroundTask

| Dimension | Details |
|-----------|---------|
| **StoreMigration** | Dedicated model for moving Maildir/Dovecot trees between MailStores. Has explicit status enum (MigrationStatus: pending/copying/verifying/cleaning/completed/failed). |
| **BackgroundTask** | Generic async task container. Has task_type (string, not enum) and TaskStatus (pending/running/completed/failed). |
| **Overlap** | StoreMigration is a StoreMigration; BackgroundTask is unused in current codebase. Both model long-running operations. |
| **Risk** | If both are exposed in templates, user may not know which table to query for a "migration" status. API routes may be ambiguous. |
| **Severity** | **LOW** — Currently no collision in UI (BackgroundTask not rendered anywhere). But future feature creep could cause naming collision. |
| **Renamability cost** | **LOW** — Remove BackgroundTask if not used, or rename to GenericAsyncTask. No users depend on current name yet. |

### 6. JobStatus enum value "completed" remapped to badge text

| Dimension | Details |
|-----------|---------|
| **Enum value** | "completed" (string literal in JobStatus) |
| **Template remapping** | restore_history.html line 40: `job.status.value == 'completed' and job.failed_messages == 0` → display "idle" badge. If failed_messages > 0 → display "partial" badge. If status == "completed" and failed_messages > 0 → display "warning" badge. |
| **User sees** | Badge text does NOT match enum value: status="completed" might show as "idle", "partial", or "warning" depending on context. |
| **Severity** | **MED** — User cannot learn the state machine from UI alone. Enum value "completed" does not mean the badge will say "Completed"; it depends on sibling columns. |
| **Renamability cost** | **MED** — Would require: renaming JobStatus → RestoreJobOutcome (or similar), updating all template comparisons, Alembic enum rename. Or: introduce a computed property on RestoreJob that derives display-friendly status. |

### 7. BackupStatus enum "running" vs. SyncState "syncing"

| Dimension | Details |
|-----------|---------|
| **BackupStatus** | idle, running, completed, failed (from account_backups.last_status) |
| **SyncState** | idle, syncing, error (from accounts.sync_state) |
| **Pattern inconsistency** | "running" vs. "syncing" — same concept, different enum. "error" vs. "failed" — same concept, different enum. |
| **Template impact** | account_backup.html line 50: `if backup_config.last_status.value == "running"` (but other status fields use "syncing" elsewhere). |
| **Severity** | **MED** — Inconsistent terminology across the codebase. If user reads model code, they see two different enums for the same state. Complicates testing and debugging. |
| **Renamability cost** | **MED** — Unify enums: either rename BackupStatus → SyncState (reuse), or create shared StatusEnum. Would require: Alembic enum migration (may be lossy), template updates, API docs update. |

---

## Renamability cost matrix

| Model/Column | Current Name | Proposed Alternative | Cost | Reasoning |
|---|---|---|---|---|
| MailStore | MailStore | Store | MED | Class rename; FK columns (Account.store_id, User.store_id) are hardcoded in 50+ places. Alembic migration not required if enum/string values don't escape. |
| MailStore.is_default | is_default | — | LOW | Column name is clear; no UI friction. |
| Account.store_id | store_id | — | LOW | FK name is clear in code; templates don't expose it. |
| BackupDestination | BackupDestination | BackupRepository / BackupTarget | HIGH | Table rename + class rename + template updates (forms, dropdowns). API routes may change. 10+ references in templates. |
| AccountBackup | AccountBackup | BackupSchedule / BackupConfig | MED | Class + table rename; Alembic migration; form templates need updates. Snapshot ladder stays external. |
| SyncJob | SyncJob | SyncAttempt / SyncRun | LOW | Class name is internal (templates don't reference it directly; they access via `account.sync_jobs`). UI label change is safer than model rename. |
| StoreMigration | StoreMigration | MaildirMigration | LOW | Class is internal; rename doesn't affect templates (only admin UI references them generically). |
| SyncState.syncing | syncing | — | HIGH | Enum string value hardcoded in templates (account_detail.html, accounts_table.html). Changing to "running" would require template edits + Alembic enum migration. |
| SyncState.error | error | — | HIGH | Enum string value hardcoded in templates. If renamed to "failed", must update 5+ template comparisons. |
| BackupStatus | BackupStatus | (unify with SyncState) | HIGH | Merge into SyncState or create shared StatusEnum. Would require: Alembic enum rename/conversion, template updates for all enum comparisons. |
| JobStatus | JobStatus | (split into SyncJobStatus / RestoreJobStatus) | HIGH | Currently reused for SyncJob and RestoreJob. If split, Alembic must handle type change. Templates already check `.value == 'completed'` hardcoded; would need updates. |

---

## Migration history pointer

| Revision | Filename | Summary |
|---|---|---|
| 001 | 001_initial_schema.py | Initial schema — UUID maildir with Account.store_id. Creates users, mail_stores, accounts, sync_jobs. |
| 002 | 002_add_account_migrating.py | Add Account.migrating flag. |
| 003 | 003_add_account_suspended.py | Add Account.suspended flag. |
| 004 | 004_add_syncjob_log_columns.py | Add log_path, parsed_summary, mbsync_version, signal to SyncJob. |
| 005 | 005_add_preferences_and_audit_logs.py | Add User.preferences JSONB column and audit_logs table. |
| 006 | 006_add_groups_and_association_tables.py | Add groups table and missing association tables (user_allowed_stores, group_members, account_groups). |
| 007 | 007_add_restore_jobs.py | Add restore_jobs table. |
| 008 | 008_add_cancelled_job_status.py | Add cancelled value to jobstatus enum. |
| 009 | 009_add_background_tasks.py | Add background_tasks table. |
| 010 | 010_add_backup_tables.py | Add backup_destinations and account_backups tables. |

---

## Key observations

1. **Enum values are hardcoded in templates.** Over 20 template lines directly compare `.value` properties (e.g., `if account.sync_state.value == 'syncing'`). This means renaming an enum value requires code review across the entire codebase.

2. **Multiple enums represent the same concept with different names:**
   - SyncState (idle, syncing, error) vs. BackupStatus (idle, running, completed, failed) vs. TaskStatus (pending, running, completed, failed).
   - SyncState.error vs. JobStatus.failed, etc.
   - This is confusing for developers and complicates API consistency.

3. **AccountBackup is a configuration record, not a snapshot.** Templates fetch snapshots asynchronously, creating a gap between the ORM and UI reality. Users see "Enable Backup," but the model doesn't store what actually happened—only when and if it last ran.

4. **MailStore naming is weak.** "Store" = base directory is an implementation detail. Users need help understanding that stores are not backups, not accounts, but the physical storage layer. Sidebar says "Mail Stores," model says `store_id`, templates say "stores"—three slight variants.

5. **BackupDestination is a reustic abstraction leak.** Users don't know what a "destination" is. The form asks for it, but the label is not user-facing language. Compare: "Backup Target," "Backup Repository," or "Backup Location" are clearer.

6. **RestoreJob status depends on sibling columns.** A job with status="completed" might display as "partial" or "idle" depending on failed_messages count. This creates state spread across columns, complicating queries and UI logic.

---

## Recommendation for next phase

Prioritize HIGH-severity naming issues for UX audit phase 2:
1. **BackupDestination → BackupRepository** (clarify infrastructure concept)
2. **Unify BackupStatus + SyncState** enums (single source of truth for operation states)
3. **Clarify AccountBackup as a schedule, not a snapshot** (rebrand UI labels)

LOW-cost wins:
- Rename SyncJob → SyncAttempt in UI labels (internal, low migration cost)
- Add computed property on RestoreJob for display-friendly status (avoid hardcoding badge logic in templates)
