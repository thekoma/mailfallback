# Mail Restore — Design Spec

## Overview

Push archived emails from any MFB account back to any other MFB account's IMAP server. Covers three use cases: full account restore, folder-level restore, and single email restore. Destination is always an account already configured in MFB (any-to-any between existing accounts).

## Data Flow

```
User UI → selects source account, destination account, emails
    ↓
MFB Backend → creates RestoreJob (background thread)
    ↓
Dovecot IMAP (local, dovecot:31143) ← FETCH RFC822 (reads from source)
    ↓
imaplib APPEND → writes to destination's remote IMAP server
    ↓
Progress tracking → updates job state, audit log on completion
```

The restore worker maintains two simultaneous IMAP connections:

- **Read**: Dovecot local (`dovecot:31143`), authenticated as the source account's owner
- **Write**: remote IMAP server of the destination account (`account.imap_host:imap_port`), authenticated with decrypted credentials

Each email is fetched individually (FETCH RFC822 + FLAGS + INTERNALDATE), then APPENDed to the destination with original flags and internal date preserved.

## Data Model

New `RestoreJob` model (migration 007):

| Column | Type | Notes |
|--------|------|-------|
| id | String PK | UUID |
| source_account_id | FK → accounts.id | Source account |
| target_account_id | FK → accounts.id | Destination account |
| status | Enum | pending / running / completed / failed |
| restore_mode | Enum | full / folder / selection |
| folder_mapping | String | "original" or custom prefix |
| skip_duplicates | Boolean | Default True |
| selected_folders | JSON | List of folder names, null = all |
| selected_uids | JSON | Dict of folder → UID list, null = all in folder |
| total_messages | Integer | Total to restore |
| restored_messages | Integer | Progress counter |
| skipped_messages | Integer | Duplicates skipped |
| failed_messages | Integer | Non-fatal errors |
| error | Text | Fatal error message |
| requested_by | FK → users.id | Who triggered the restore |
| requested_at | DateTime(tz) | When requested |
| started_at | DateTime(tz) | When worker started |
| completed_at | DateTime(tz) | When finished or failed |

No changes to the Account model. Restore is an operation, not account state.

## Backend Services

### services/restore_service.py — Orchestration

- `create_restore_job(db, source_account_id, target_account_id, options)` — validates both accounts exist, have credentials, aren't suspended/migrating; creates job; launches worker in background thread
- `get_restore_job(db, job_id)` — returns job state and progress
- `list_restore_jobs(db, account_id)` — restore history for an account (as source or target)
- `cancel_restore_job(db, job_id)` — sets cancel flag; worker checks per-email and stops

### services/restore_worker.py — Execution

Background thread (same pattern as sync_worker):

1. Connect to Dovecot local IMAP as source account owner (plain auth)
2. Connect to destination remote IMAP with decrypted credentials (app_password or refreshed OAuth2 token)
3. For each selected folder:
   - SELECT on source
   - If `skip_duplicates`: SEARCH Message-ID on target to build existing-ID set
   - FETCH RFC822 + FLAGS + INTERNALDATE for each UID
   - If folder_mapping is a prefix: CREATE target folder if needed (e.g., `Restored/INBOX`)
   - APPEND to target with preserved flags and date
   - Update `restored_messages` in DB per email
   - Check cancel flag per email
4. On completion: audit log entry (counts, source, target, options)
5. On fatal error: save to `error`, status → failed, audit log

### services/imap_check.py — Extension

New `connect_imap(host, port, tls_type, username, password) → IMAP4` function. Handles IMAPS/STARTTLS/plain. Both imap_check and restore_worker use it, eliminating connection code duplication.

### OAuth2 Handling

For destination accounts with `auth_type=oauth2`: refresh token before connecting, using the same flow as `sync_worker.py`. For source (Dovecot local): always plain auth.

## API Endpoints

### Browse & Search (read source via Dovecot IMAP)

- `GET /api/accounts/{id}/mailboxes` — list folders with message counts (IMAP LIST + STATUS)
- `GET /api/accounts/{id}/mailboxes/{folder}/messages?page=1&limit=50` — list emails in folder (subject, from, date, uid, flags) via IMAP FETCH ENVELOPE. Folder path is URL-encoded (e.g., `%5BGmail%5D%2FAll%20Mail` for `[Gmail]/All Mail`).
- `GET /api/accounts/{id}/mailboxes/{folder}/search?q=term` — FTS search via IMAP SEARCH (triggers Dovecot fts-flatcurve). Same folder encoding.

### Restore Operations

- `POST /api/restore` — create RestoreJob:
  ```json
  {
    "source_account_id": "uuid",
    "target_account_id": "uuid",
    "restore_mode": "full|folder|selection",
    "selected_folders": ["INBOX", "Sent"],
    "selected_uids": {"INBOX": [123, 456]},
    "folder_mapping": "original|prefix",
    "folder_prefix": "Restored",
    "skip_duplicates": true
  }
  ```
- `GET /api/restore/{job_id}` — job state and progress (HTMX polling)
- `POST /api/restore/{job_id}/cancel` — cancel running job

### Authorization

Browse endpoints require authentication + ownership of the account (or admin). Restore creation requires ownership of both source and target accounts (or admin).

## UI/UX

Dedicated page at `/restore`, accessible from sidebar (icon: `archive-restore`).

### User Flow

**Step 1 — Source & Destination**: two dropdowns listing the user's accounts. Can be the same account.

**Step 2 — Select what to restore** (three modes via radio buttons):

- **Full restore**: no selection needed, restores everything
- **Folders**: folder list with checkboxes and message counts
- **Search & pick**: search field + results table with checkboxes per email (subject, from, date)

Folder list and search results load via HTMX after source account is selected.

**Step 3 — Options**:

- Folder mapping: radio "Original folder" / "Custom prefix" with text input (default "Restored")
- Skip duplicates: checkbox (default on)

**Step 4 — Confirm & start**: summary (N emails from Account X to Account Y, mapping, options), "Start Restore" button.

**Progress**: after launch, page shows progress bar with counters (restored/skipped/failed/total), HTMX polling every 2s. Cancel button.

**Restore history**: table below the form on `/restore`, showing recent jobs (date, source→destination, status, counters).

All via HTMX, Pico CSS styling, minimal JS for tab switching only.

## Restic Integration (V2 Design Hook)

V1 implements only local Maildir via Dovecot IMAP as source. The design accommodates a future restic source:

- The restore worker receives source connection parameters derived from the account, not hardcoded
- In V2, a restic restore could: extract snapshot to temp directory → Dovecot sees it via dynamic namespace → restore_worker reads from it like a normal account
- `RestoreJob.source_account_id` identifies the source. In V2, this could become nullable with an alternative `source_snapshot_id` field

No abstraction code in V1 — just awareness that the source IMAP parameters are derived, not fixed.

## Error Handling

### Fatal Errors (stop the job)

- Auth failure on target → status `failed`, clear error message
- Connection lost on target, not recoverable after 3 retries → `failed`
- Source or target account deleted during restore → `failed`
- Target OAuth2 token not refreshable → `failed`

### Non-Fatal Errors (per email, job continues)

- APPEND rejected by server (quota full, message too large) → increment `failed_messages`, log UID and reason, continue
- Corrupt email in source (FETCH fails) → skip, increment `failed_messages`

### Retry

Temporary connection loss → 3 attempts with backoff (1s, 3s, 10s), then fatal.

### Concurrency

- One active restore job per source-destination pair at a time. Second attempt rejected.
- Sync and restore can run in parallel — sync writes to local Maildir, restore reads from Dovecot IMAP. No conflict.
- Accounts in `suspended` or `migrating` state → restore rejected at job creation.

### Quota

No pre-check of target quota (not all IMAP servers expose it). If server rejects an APPEND for quota, it's a non-fatal error.

## Audit Logging

Every restore job is logged via `audit_service.log_action()`:

- On job creation: action `restore_start`, details include source, target, mode, options
- On completion: action `restore_complete`, details include restored/skipped/failed counts
- On failure: action `restore_failed`, details include error message

New entries in `ACTION_LABELS` dict for the three restore actions.
