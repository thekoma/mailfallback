# Offsite Backup with Restic

## Problem

MFB backs up email to local Maildir but has no offsite backup. If the server dies, the backups die with it. Users need a way to push Maildir data to remote storage (S3, local NAS) with scheduling, retention, and granular restore.

## Solution

Restic-based offsite backup integrated into MFB. Per-account backup with isolated repos under shared credentials. Restore creates a temporary read-only account that plugs into the existing MFB restore flow.

## Architecture

### Repo Structure

One restic repo per account, same bucket/credentials, different path prefix:

```
Bucket: mfb-backups/
  {account-uuid-A}/    -> isolated restic repo
  {account-uuid-B}/    -> isolated restic repo
  {account-uuid-C}/    -> isolated restic repo
```

Each repo has its own snapshots, retention, and lifecycle. Deleting an account's backup data only touches its prefix - no risk to other accounts.

### Backend Types

- **S3**: any S3-compatible endpoint (AWS, Backblaze B2, MinIO, Wasabi)
- **Local**: path on disk (NAS mount, secondary volume)

### Backup Flow

1. APScheduler triggers backup per account schedule
2. MFB runs `restic init` (idempotent) then `restic backup {maildir_path} --json`
3. After backup, `restic forget --prune` applies retention policy
4. Progress tracked in-memory (like sync_worker), result saved to DB

### Restore Flow

1. Admin selects account + snapshot from UI
2. MFB runs `restic restore {snapshot_id} --target {temp_dir}`
3. Creates a temporary Account in DB:
   - name: "Backup {original_name} ({snapshot_date})"
   - maildir_path: temp directory
   - suspended: True (no sync)
   - no IMAP credentials
4. Account appears in list with "Backup Restore" badge
5. Admin uses existing MFB restore (source -> target) to move mail where needed
6. Admin dismisses the temporary account when done (deletes account + temp dir)

### Backup Data Scope

Only Maildir files. No DB metadata. The backup is a pure file-level copy of the email content. Account config, sync history, and stats are not backed up - they're reconstructable.

## Data Model

### BackupDestination

```
BackupDestination:
  id: UUID PK
  name: str                          "Backblaze B2", "NAS locale"
  backend_type: enum (s3, local)
  s3_endpoint: str nullable          "s3.eu-central-003.backblazeb2.com"
  s3_bucket: str nullable            "mfb-backups"
  s3_access_key: str nullable        encrypted via Fernet
  s3_secret_key: str nullable        encrypted via Fernet
  local_path: str nullable           "/mnt/nas/mfb-backups"
  restic_password: str               encrypted - shared by all repos in this destination
  created_at: datetime
```

### AccountBackup

```
AccountBackup:
  id: UUID PK
  account_id: FK Account
  destination_id: FK BackupDestination
  enabled: bool default True
  schedule: str                      cron expression, e.g. "0 2 * * *"
  retention_preset: enum (light, standard, full, custom)
  keep_daily: int nullable           only when custom
  keep_weekly: int nullable
  keep_monthly: int nullable
  last_backup_at: datetime nullable
  last_status: enum (idle, running, completed, failed)
  last_error: str nullable
  created_at: datetime
```

Retention presets:

| Preset | Daily | Weekly | Monthly |
|--------|-------|--------|---------|
| Light | 7 | 4 | 0 |
| Standard | 30 | 12 | 6 |
| Full | 90 | 52 | 24 |

Repo path computed at runtime:
- S3: `s3:{s3_endpoint}/{s3_bucket}/{account_id}`
- Local: `{local_path}/{account_id}`

Relationship: Account 1:N AccountBackup (one account can back up to multiple destinations).

## Restic Integration

### services/restic_service.py

Wrapper around restic binary via subprocess:

```python
def init_repo(destination, account_id) -> bool
def run_backup(destination, account_id, maildir_path) -> dict
def list_snapshots(destination, account_id) -> list[dict]
def restore_snapshot(destination, account_id, snapshot_id, target_path) -> bool
def apply_retention(destination, account_id, keep_daily, keep_weekly, keep_monthly) -> dict
def forget_all(destination, account_id) -> bool
```

Each function builds the env dict:
```python
env = {
    "RESTIC_REPOSITORY": f"s3:{dest.s3_endpoint}/{dest.s3_bucket}/{account_id}",
    "RESTIC_PASSWORD": decrypt_credentials(dest.restic_password, settings.secret_key),
    "AWS_ACCESS_KEY_ID": decrypt_credentials(dest.s3_access_key, settings.secret_key),
    "AWS_SECRET_ACCESS_KEY": decrypt_credentials(dest.s3_secret_key, settings.secret_key),
}
```

All restic commands use `--json` for parseable output. Progress via `--json` on stderr.

### services/backup_worker.py

Same pattern as sync_worker.py:
- ThreadPoolExecutor for async execution
- In-memory progress dict for live polling
- Result persisted to AccountBackup model

Per-account backup job:
1. `restic init` (skip if already initialized)
2. `restic backup {maildir_path} --json`
3. `restic forget --prune --keep-daily N --keep-weekly N --keep-monthly N --json`
4. Update AccountBackup: last_backup_at, last_status, last_error

### Scheduler

APScheduler registers a job for each enabled AccountBackup, same pattern as sync scheduling. Job ID: `backup-{account_backup_id}`.

### Dockerfile

```dockerfile
RUN apt-get install -y --no-install-recommends restic
```

## UI

### Admin: Backup Destinations page

New sidebar link under Stores. Table of configured destinations with inline add form. Each row shows: name, type, bucket/path, account count, actions (edit, delete).

### Account Detail: Offsite Backup section

Collapsible section in account detail page:
- Destination dropdown + schedule + retention preset
- Last backup status badge (idle/running/completed/failed)
- "Backup Now" button
- Snapshot list (date, size) from `restic snapshots --json`
- "Restore" button per snapshot -> creates temp account
- Danger zone: "Delete remote backup data" checkbox on account delete

### Account list: Backup Restore badge

Temporary restore accounts show orange "Backup Restore" badge. No sync controls, no edit IMAP. "Dismiss" button removes account + temp directory.

### System Status bar

Fifth badge "Backup" showing active backup jobs with progress.

## Deletion Safety

When deleting an account with backup enabled:
- Default: only removes DB records and local Maildir. Remote backup data stays.
- With "Delete remote backup data" checked: runs `restic forget --prune` to clear all snapshots, then removes the repo prefix.
- Deleting a BackupDestination: blocked if any AccountBackup references it. Admin must remove backup configs first.

## Files to Create/Modify

**New:**
- `src/mailfallback/services/restic_service.py`
- `src/mailfallback/services/backup_worker.py`
- `src/mailfallback/routers/ui_backup.py`
- `src/mailfallback/templates/admin_backup.html`
- `src/mailfallback/templates/partials/account_backup.html`
- `tests/test_restic_service.py`
- `tests/test_backup_worker.py`
- Alembic migration for BackupDestination + AccountBackup tables

**Modified:**
- `src/mailfallback/models.py` - add BackupDestination, AccountBackup, RetentionPreset
- `src/mailfallback/app.py` - register backup scheduler jobs at startup
- `src/mailfallback/routers/ui_accounts.py` - add backup section to account detail
- `src/mailfallback/templates/account_detail.html` - backup collapsible section
- `src/mailfallback/templates/base.html` - sidebar link for Backup Destinations
- `src/mailfallback/templates/partials/system_status.html` - backup badge
- `src/mailfallback/services/scheduler.py` - register backup jobs
- `docker/Dockerfile` - add restic package

## Testing

- Unit tests for restic_service: mock subprocess, verify command construction and env vars
- Unit tests for backup_worker: mock restic_service, verify job flow and DB updates
- Unit tests for retention presets: verify keep values
- Route tests: auth guards, destination CRUD, backup trigger
- No integration tests with real restic (requires S3 or disk setup)
