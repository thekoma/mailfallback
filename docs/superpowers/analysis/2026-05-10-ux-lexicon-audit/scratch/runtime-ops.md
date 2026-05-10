# Runtime / operations inventory

**Audit Date:** 2026-05-10
**Purpose:** Inventory all running background services, subprocess calls, and file I/O so we can later verify whether the UI accurately reflects what's happening behind the scenes.

## Method

Inspected:
- `app.py` lifespan hooks (startup/shutdown)
- `scheduler.py` — APScheduler job registration
- `sync_worker.py` — ThreadPool-based mbsync invocation
- `backup_worker.py` — ThreadPool-based restic backup orchestration
- `restore_worker.py` — IMAP restore pipeline with temp user creation
- `restic_service.py` — restic subprocess wrapper (init, backup, snapshots, restore)
- `dovecot_manager.py` — doveadm HTTP API calls (reload, fts_rescan, index, mailbox stats)
- `stats_service.py` — post-sync statistics collection
- `background_tasks.py` — FTS reindex and force resync tasks
- Models, templates, and routers to trace data flow to UI

---

## Process diagram

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           FastAPI Application (app.py)                      │
│                          ─ Lifespan: start_scheduler                        │
│                          ─ Shutdown: stop_scheduler, shutdown executors     │
└──────────────────────────────────────────────────────────┬────────────────────┘
                                                           │
                    ┌──────────────────────────────────────┼──────────────────────────┐
                    │                                      │                         │
                    ▼                                      ▼                         ▼
        ┌─────────────────────┐          ┌──────────────────────────┐    ┌──────────────────┐
        │   APScheduler       │          │   ThreadPoolExecutor     │    │   Thread Pool    │
        │  ─ sync jobs        │          │   (sync_worker)          │    │ (backup_worker)  │
        │  ─ backup jobs      │          │   max_workers=4          │    │  max_workers=2   │
        │  ─ bg tasks         │          │                          │    │                  │
        └──────────┬──────────┘          └────────────┬─────────────┘    └──────────┬───────┘
                   │ _run_scheduled_sync             │                        │
                   │ _run_scheduled_backup           │ submit_sync_job        │ submit_backup
                   │ submit_fts_reindex              │                        │
                   │ submit_force_resync             ▼ execute_sync_job       ▼ execute_backup
                   │                         ┌────────────────────┐   ┌──────────────────┐
                   │                         │   mbsync subprocess│   │  restic commands │
                   │                         │   (config via temp  │   │  ─ init          │
                   │                         │    .mbsyncrc file)  │   │  ─ backup        │
                   │                         │                    │   │  ─ snapshots     │
                   │                         │   ┌─ token file    │   │  ─ forget --prune│
                   │                         │   │  (OAuth2)       │   │                  │
                   │                         │   │                │   │  (env vars:      │
                   │                         │   └─ maildir_path  │   │   RESTIC_REPO    │
                   │                         │      (sync target) │   │   AWS_KEY_ID)    │
                   │                         └────────────────────┘   └──────────────────┘
                   │                                │                        │
                   │                                ├─► log to               │
                   │                                │   /data/logs/sync      ├─► phase tracking
                   │                                │   /{account_id}/       │   (_backup_progress)
                   │                                │   {job_id}.log         │
                   │                                │                        │
                   │                                └─► parse output         └─► store summary,
                   │                                    collect_account_stats    last_backup_at,
                   │                                    update Account:          last_status,
                   │                                    ─ total_messages         last_error
                   │                                    ─ unread_messages
                   │                                    ─ maildir_size_bytes
                   │                                    ─ folder_stats
                   │
                   └─► via doveadm HTTP API
                       (fts_rescan, index_user,
                        force_resync, mailboxStatus)
```

---

## Schedulers and recurring jobs

### APScheduler (BackgroundScheduler)

**Initialization:** `start_scheduler(db)` called in app lifespan.
**Shutdown:** `stop_scheduler()` in lifespan cleanup.

#### Sync Jobs

| Job ID | Function | Schedule | Trigger | Notes |
|--------|----------|----------|---------|-------|
| `sync_{account_id}` | `_run_scheduled_sync()` | Per `Account.sync_schedule` (5-part cron) | CronTrigger | Skips if account suspended, not authenticated, or migrating. Creates SyncJob, submits to thread pool. |

**Sync scheduler sync:** `sync_scheduler_jobs(db)` called at startup and after any account config change (via refresh_scheduler).

#### Backup Jobs

| Job ID | Function | Schedule | Trigger | Notes |
|--------|----------|----------|---------|-------|
| `backup-{account_backup_id}` | `_run_scheduled_backup()` | Per `AccountBackup.schedule` (crontab) | CronTrigger | Only for enabled backups. Submits to backup thread pool. |

**Backup scheduler sync:** `backup_scheduler_jobs(db)` called at startup and refreshed.

#### Background Tasks

No APScheduler registration. Submitted on-demand via API:
- `submit_fts_reindex(db, requested_by)` — spawns daemon thread → `run_fts_reindex()`
- `submit_force_resync(db, requested_by)` — spawns daemon thread → `run_force_resync()`

---

## Workers and queues

### Sync Worker (ThreadPoolExecutor)

**Pool size:** `settings.sync_max_workers` (default 4)
**Executor name:** `sync-worker`

| Who Submits | Queue | Who Runs | What It Consumes |
|-------------|-------|----------|-----------------|
| `_run_scheduled_sync()` (scheduler) | Thread pool queue | `execute_sync_job()` in pool thread | SyncJob ID, account credentials (OAuth2 refresh if needed), temp token file |
| Manual sync button (API `/api/sync/{account_id}`) | Thread pool queue | Same | Same |
| Error retry (dashboard) | Thread pool queue | Same | Same |

**Execution flow:**
1. Fetch Account and SyncJob from DB
2. Validate account (not suspended, not migrating, authenticated)
3. If OAuth2: refresh token via `_refresh_oauth_token()`, write to `/tmp/mfb_token_{account_id}`
4. Generate mbsync config via `generate_mbsyncrc()` → temp file
5. If debug mode: write to `/tmp/mbsync/{account_id}.rc` for inspection
6. Launch `mbsync -c {config} -a` subprocess
7. Stream stdout/stderr line-by-line to:
   - In-memory `_running_logs[job_id]` (cleared after completion)
   - On-disk log file: `/data/logs/sync/{account_id}/{job_id}.log`
8. Parse output via `parse_mbsync_lines()` for summary
9. On success: call `collect_account_stats()` → doveadm HTTP API
10. Clean up temp files (config, token file)

**Status tracking:** `Account.sync_state` (idle/syncing/error) + `SyncJob.status` (pending/running/completed/failed)

### Backup Worker (ThreadPoolExecutor)

**Pool size:** 2 (hardcoded)
**Executor name:** `backup-worker`

| Who Submits | Queue | Who Runs | What It Consumes |
|-------------|-------|----------|-----------------|
| `_run_scheduled_backup()` (scheduler) | Thread pool queue | `execute_backup()` in pool thread | AccountBackup ID, destination config |
| Manual backup trigger (API) | Thread pool queue | Same | Same |

**Execution flow:**
1. Fetch AccountBackup and Account from DB
2. Phase 1: `restic_service.init_repo()` — subprocess restic init
3. Phase 2: `restic_service.run_backup()` — subprocess restic backup --json {maildir_path}
   - Parse JSON output line-by-line, extract summary
4. Phase 3: `restic_service.apply_retention()` — subprocess restic forget --prune
5. Update AccountBackup: `last_status`, `last_backup_at`, `last_error`
6. Track progress in `_backup_progress[account_backup_id]` dict

**Status tracking:** `AccountBackup.last_status` (idle/running/completed/failed)

### Restore Worker (ThreadPoolExecutor)

**Pool size:** 2 (hardcoded)
**Executor name:** `restore-worker`

| Who Submits | Queue | Who Runs | What It Consumes |
|-------------|-------|----------|-----------------|
| Restore API endpoint | Thread pool queue | `execute_restore_job()` in pool thread | RestoreJob ID, mode (full/folder/selection) |

**Execution flow:**
1. Validate source/target accounts
2. Create ephemeral Dovecot user: `create_temp_imap_user(db, [source.id])`
   - User can only see source account's namespace
3. Connect to Dovecot IMAP (localhost, temp creds)
4. Connect to target IMAP (external provider, source credentials)
5. Enumerate folders, resolve mappings, select messages
6. Upload via IMAP append, track restored/skipped/failed counts
7. Delete ephemeral user on finish
8. Update RestoreJob: status, counts, error

---

## External binary calls

### mbsync (isync)

**Invoked by:** `execute_sync_job()` in sync_worker.py:236
**With arguments:** `mbsync -c {config_path} -a` (and `-Dm` if debug mode)
**Where invoked:** Subprocess.Popen with stdout/stderr capture
**Stdout/stderr handling:**
- Captured in `_running_logs[job_id]` (in-memory, for live UI)
- Written to `/data/logs/sync/{account_id}/{job_id}.log` (on-disk permanent)
- Parsed by `parse_mbsync_lines()` to extract version, message counts, errors

**Config source:** Generated in-memory from template, written to temp file
**Exit code stored in:** `SyncJob.exit_code` (0 = success, non-zero = failure)

### restic

**Invoked by:** `restic_service.py` (_run_restic)
**Commands:**
- `restic init` (init_repo)
- `restic backup --json {maildir_path}` (run_backup)
- `restic snapshots --json` (list_snapshots)
- `restic restore {snapshot_id} --target {path}` (restore_snapshot)
- `restic forget --prune {retention_args}` (apply_retention)

**Where invoked:** Subprocess.run with env vars and capture
**Environment vars:**
- `RESTIC_REPOSITORY` — S3 URL or local path (per destination)
- `RESTIC_PASSWORD` — decrypted from BackupDestination.restic_password
- `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` (for S3 backends)

**Output:** JSON parsed to dicts, stored in AccountBackup.last_error on failure

### doveadm (HTTP API only)

**Invoked by:** `dovecot_manager.py` functions via httpx
**Endpoints:**
- `POST /doveadm/v1` with `[["reload", {}, "tag1"]]` → reload_dovecot()
- `POST /doveadm/v1` with `[["processStatus", {}, "health"]]` → check_dovecot_health()
- `POST /doveadm/v1` with `[["ftsRescan", {"user": username}, "fts1"]]` → fts_rescan()
- `POST /doveadm/v1` with `[["forceResync", ...]]` → force_resync()
- `POST /doveadm/v1` with `[["index", ...]]` → index_user()
- `POST /doveadm/v1` with `[["mailboxStatus", ...]]` → get_mailbox_stats()

**Auth:** HTTP Basic Auth (doveadm / settings.dovecot_api_key)
**Base URL:** settings.dovecot_api_url (default `http://dovecot:8080`)
**Called from:**
- `collect_account_stats()` post-sync to get `mailboxStatus` → updates Account.folder_stats
- `run_fts_reindex()` to reindex all users
- `run_force_resync()` to force resync all users
- Health checks (30s TTL cached)

---

## File-system layout

### Maildir Structure

```
{store.path}  (default /data/mailboxes)
├── {account_uuid}/
│   ├── cur/
│   │   └── {msg_id}
│   ├── new/
│   ├── tmp/
│   └── (IMAP folders as subdirectories)
│       └── .../{cur,new,tmp}
│
├── {another_account_uuid}/
└── ...
```

**Writer:** mbsync subprocess (sync_worker.py)
**Reader:**
- restic (for backup)
- Dovecot (IMAP access)
- Restore worker (IMAP copy source)

**Unique constraint:** `Account.maildir_path` must be unique in DB (ensures no collisions)

### Dovecot Home Directories (Dynamic Namespace)

```
{store.path}/.dovecot-home/
├── {username}/
│   ├── mail/  (symlink or mount point to user's namespace)
│   └── .dovecot/  (Dovecot metadata)
```

**Created by:** Dovecot Lua userdb script (generated in config_generator.py)
**Purpose:** Per-user namespace isolation; users see only their account folders

### Restore Staging

```
{store.path}/.offsite-restore/
├── {restore_job_id}/
│   └── (snapshot extracted here during restore browse/selection)
```

**Created by:** Restore API when previewing snapshots
**Cleaned up by:** ? (TBD — may need manual intervention or scheduled GC)

### Debug Directories (if MAILFALLBACK_DEBUG=true)

```
/tmp/mbsync/
├── {account_id}.rc  (generated config for inspection)
└── ...

/tmp/mfb_token_{account_id}  (OAuth2 access token, deleted after sync)
```

### Sync Logs (Permanent)

```
/data/logs/sync/
├── {account_id}/
│   ├── {job_id}.log
│   └── ...
```

**Retention:** Cleanup in sync_worker.py (cleanup_old_jobs) — kept for UI history
**Size:** Unbounded (potential storage issue)

### Restic Repositories

**Local backend:**
```
{destination.local_path}/{account_id}/
├── config
├── data/
├── index/
├── keys/
└── locks/
```

**S3 backend:**
```
s3://{s3_endpoint}/{s3_bucket}/{account_id}/
```

**Created by:** restic_service.init_repo()

---

## Status surfaces

### Account Sync State Badge

**Where:** Account row in accounts table, account detail page
**Template:** `partials/sync_status.html`
**States:** idle (green checkmark), syncing (spinner), error (red alert)
**Data source:** `Account.sync_state` enum
**Refresh:** HTMX polling every 3s while syncing (hx-trigger="every 3s")
**Endpoint:** `GET /accounts/{account_id}/sync-status`

### Account Stats Strip (Detail Page)

**Where:** Account detail page
**Data:** `Account.sync_state`, `total_messages`, `maildir_size_bytes`, `folder_stats`, `last_sync_at`, `sync_schedule`
**Refreshes:** On sync completion (custom HTMX event "sync-finished from:body")
**Endpoint:** `GET /accounts/{account_id}/partials/stats`

### System Status Badge (Global Header)

**Where:** base.html toast bar
**Data:** Dovecot health (doveadm processStatus), disk usage
**Refresh:** Periodic (background polling)
**Cached:** 30s TTL via `get_cached_health()` in dovecot_manager.py

### Dashboard "Needs Attention"

**Where:** Dashboard page
**Data:** Accounts with `sync_state == error` or no recent sync
**Computed in:** `dashboard()` router in ui.py
**Endpoint:** Server-side render (no polling)

### Dashboard "Recent Activity"

**Where:** Dashboard page
**Data:** Last N SyncJobs ordered by completed_at desc
**Endpoint:** Server-side render

### Background Task Progress

**Where:** Admin panel (FTS reindex, force resync)
**Data:** `BackgroundTask.progress_current`, `progress_total`, `details`, `user_statuses`
**Live progress:** `_task_progress[task_id]` dict
**Endpoint:** `GET /admin/background-tasks/{task_type}`

### Backup Status

**Where:** Admin backup configuration panel
**Data:** `AccountBackup.last_status`, `last_backup_at`, `last_error`
**Endpoint:** Server-side render

---

## Failure modes the UI must explain

| Failure | Mechanism | Current UI Surface? | Evidence |
|---------|-----------|---------------------|----------|
| Sync timeout | mbsync -c runs 3600s, then TimeoutExpired | `SyncJob.log = "Sync timed out..."`, `Account.sync_state = error` | ✅ Yes — error badge, log visible |
| Sync command failed | mbsync exit code != 0 | `SyncJob.exit_code`, `Account.sync_state = error`, `last_error = job.log` | ✅ Yes — error badge + last_error tooltip |
| OAuth2 token refresh failed | `_refresh_oauth_token()` returns None | `Account.sync_state = error`, `last_error = "Failed to refresh..."` | ✅ Yes |
| Account suspended by admin | Scheduler checks `account.suspended`, skips job | No SyncJob created; no error shown | ❌ No — user might not know why sync stopped |
| Store disk full | mbsync writes to maildir, ENOSPC | Captured in mbsync output + log | ⚠️ Partial — shown in log only, not highlighted in UI |
| Dovecot down | doveadm HTTP request fails, stats collection fails silently | No Account.folder_stats update; stats incomplete | ❌ No — user sees stale stats |
| Restic repo init failed | `init_repo()` raises RuntimeError | `AccountBackup.last_status = failed`, `last_error = str(e)` | ✅ Yes — backup error badge |
| Restic backup corrupted | restic backup exits non-zero | `last_error` set, backup marked failed | ✅ Yes |
| S3 credentials invalid | restic subprocess fails with auth error | Captured in `last_error` | ✅ Yes |
| Restore IMAP connection timeout | `connect_imap()` OSError after retries | `RestoreJob.error`, `status = failed` | ✅ Yes — restore status page |
| Migration in progress | `execute_sync_job()` checks `account.migrating` | Job marked failed with "account migration in progress" | ⚠️ Partial — error shown but migration progress not surfaced alongside it |
| FTS reindex Tika unavailable | `index_user()` doveadm call fails | `BackgroundTask.details["errors"]` list | ✅ Yes — task status shows errors |

---

## Honesty audit (quick)

**List of user-facing claims that may be misleading:**

| Claim | Reality | Issue |
|-------|---------|-------|
| "Backup configured" | Existence of AccountBackup row with enabled=true | ❌ Does NOT mean backup ever ran successfully. Last backup could have failed, but "configured" suggests operational. Need `last_status=completed` check. |
| "Last sync: X hours ago" | `Account.last_sync_at` = when job status changed to completed | ⚠️ Does not distinguish "last attempted" vs "last successful". If last 3 attempts failed, user might think sync works. Should show `last_sync_at` only on success. |
| "Syncing..." spinner | `Account.sync_state = syncing` | ✅ Honest — cleared immediately on job completion (success or failure). |
| "N messages" | Sum of `folder_stats` from doveadm mailboxStatus post-sync | ⚠️ Stale if Dovecot unavailable or down. Will show old count if stats collection fails silently. |
| "Store: [name]" | `Account.store.name` | ✅ Honest — shows current store. |
| "Snapshots" badge with count | List from `restic snapshots --json` | ❌ Misleading. These are restic snapshots only, not a guarantee of Maildir integrity. User might think "5 snapshots = backed up 5 times" when in reality restic could have partial state. Should say "Restic snapshots" explicitly. |
| "FTS reindex running" | `BackgroundTask.status = running` | ✅ Honest. |
| "Backup enabled" checkbox | `AccountBackup.enabled` | ✅ Honest — toggle controls whether scheduler registers the job. |
| "Restore full mailbox" | Restore mode `full` will restore all folders from snapshot | ⚠️ Does not explain deduplication logic (skip_duplicates). User might expect to "restore" to get a copy but could lose mail if target already has duplicates. |
| "Error: account migration in progress" | Sync skipped because `Account.migrating = true` | ✅ Honest, but UI should surface ongoing migration status on account row. Currently hidden in error log. |

**Top 5 honesty gaps:**

1. **"Last sync" timestamp** — should be "last successful sync" or show a separate "last attempted" field.
2. **Backup configured vs. backed up** — red flag: enabled backup with failed last_status still shows "configured" cheerfully.
3. **Snapshots count** — conflates restic snapshots with backup completeness; Dovecot state is the actual backup.
4. **Stale stats** — if Dovecot is down, Account.total_messages/folder_stats stay frozen but UI has no "⚠️ stats stale" indicator.
5. **Migration blocking sync** — migration status is first-class but not surfaced prominently; user might assume account is broken.

---

**Word count:** 1,847 words
