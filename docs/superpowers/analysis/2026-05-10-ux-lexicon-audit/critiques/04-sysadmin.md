# Sysadmin critique

> Voice: the on-call SRE who has been paged at 3am by every product they ever shipped, and would like, this once, to ship one that doesn't lie to them.

## Bottom line

- The product can fail in at least three places (mbsync, restic, doveadm) and bubbles a single muddled "error" up to one stat card. From the dashboard I cannot tell which subsystem is sad without three drill-downs.
- "Backup" is not just a UX problem — it is an **operational** problem. When a flash message says "Backup failed" I have to read the URL bar to know whether to log into Postgres, kick mbsync, or check the S3 bill. That is MTTR I will not get back.
- The instrumentation that exists (sync logs on disk, `last_error` on `AccountBackup`, doveadm health TTL cache) is **good** — it's buried, scattered, and not surfaced to the dashboard. The fix is plumbing, not new features.

## The visibility gap

**Scenario:** 03:47. Twenty accounts. One account's `mbsync` failed because the source IMAP rotated its app password three days ago. The same morning, the restic destination for a *different* account is silently 401ing because the S3 IAM key was rolled.

What I see at 09:00 today:

- Stat cards: Accounts 20, Messages 2.3M, Storage 412 GB, Errors **1**.
- "Needs Attention" shows the one account with `sync_state == error`. Good.
- "Recent Activity" shows the latest sync jobs. Good for the sync side.
- **Nothing about the failed restic backup.** Per `runtime-ops.md`, `AccountBackup.last_error` only surfaces on the per-account detail page, behind a collapsed `<details>` block. I'd have to drill into all twenty accounts to find it. I won't.
- "Errors: 1" is a lie of omission: it counts sync errors only. The restic failure does not increment it. The system *looks* like it has one problem when it has two.

What I should see:

- **One "unhealthy chain links" counter** covering `account.sync_state == error` + `account_backup.last_status == failed` + `restore_job.status == failed` + any destination whose last `restic check` is older than N hours.
- A **per-subsystem health pill** on the dashboard: `Sync: 19/20 OK`, `Offsite: 18/19 OK (1 failed at 02:14)`, `Dovecot: alive (last check 30s ago)`, `Destinations: 2/2 reachable`.
- **A scheduler heartbeat.** If `_run_scheduled_backup` didn't tick once in the last 24h, that's a scheduler problem, not an account problem, and I want it loud.

Summary: **the dashboard is a sync dashboard wearing a system dashboard's costume.**

## Failure modes I want one click away

Rating each F1–F10 from the personas doc. "Detectability" = will I notice without prompting. "Recovery clarity" = once I notice, does the UI guide me.

| # | Failure | Detectability | Recovery clarity | Min UI fix |
|---|---|---|---|---|
| F1 | Sync fails silently overnight | **MED** | CLEAR | Already in Needs Attention, but distinguish "transient retry" from "credentials expired" — only the latter needs me. |
| F2 | Offsite backup fails silently | **LOW** | VAGUE | Surface `account_backup.last_status==failed` on dashboard. Add a "Backup health" stat card with drill-down. |
| F3 | Disk fills up | **MED** | MISSING | Storage card today is one number. Need: per-store free%, threshold-coloured (>80% amber, >95% red), and a "what's growing" row. |
| F4 | Offsite repo unreachable (S3 down, TLS expired) | **LOW** | VAGUE | The destination test exists (`test_destination`). Run it on a schedule (every 6h) and store `destination.last_check_at`/`last_check_error`. Show on /admin/backup. |
| F5 | User can't find the offsite feature | NONE (for the user) | N/A here | Not my problem in this critique; I find it fine. |
| F6 | User configures offsite but never local sync | LOW | MISSING | If `account.last_sync_at IS NULL` and `account_backup.enabled` exists, banner: "This account has an offsite policy but has never synced. There is nothing to back up." |
| F7 | Snapshot completeness uncertainty | LOW | MISSING | `restic check --read-data-subset=10%` on a slow schedule, store `destination.last_integrity_check_at`. Without this, "snapshots: 47" is theatre. |
| F8 | Delete account, fear losing snapshots | NONE | MISSING | Delete confirmation must say literally "this will not delete the 47 restic snapshots in destination X. To purge, run `restic forget --tag account=...` afterwards (or tick this box)." |
| F9 | Migrate offsite local→S3 | NONE | MISSING | Out of scope for the dashboard fix. But the *symptom* (no path) is at least an honest "not yet supported". |
| F10 | Two accounts, same email, different stores | MED | VAGUE | Show the store name in the accounts list, not just on the detail page. Two rows reading "alice@example.com" with no disambiguator is a future incident. |

The cheapest, highest-MTTR-impact wins: **F2, F3, F4, F7.** All four are server-side state that already exists or is one subprocess call away — they are not surfaced.

## Status surfaces I want

For each background process: a **liveness signal** (last tick), a **last-result signal** (what happened), and an **unhealthy-because** explainer when red. Today in italics, want in bold.

**mbsync per account.** *Today: `Account.sync_state`, `last_sync_at`, `SyncJob` history.* **Want:** split `last_sync_attempt_at` from `last_sync_success_at`. Today's `last_sync_at` lies — it's the last completion timestamp; if today's attempt failed early, the field still shows yesterday's success and the row reads green-ish. Surface on the accounts list, not just detail.

**restic per account.** *Today: `AccountBackup.last_status / last_backup_at / last_error`, only on detail page, no "next run".* **Want:** A `Backup` column on the accounts list with state pill, `next_backup_at` derived from cron, and a dashboard counter "N configured / M backed up in last 24h".

**FTS / Tika reindex.** *Today: `BackgroundTask.progress_current/total/details`.* **Want:** A "last reindex" row on the System page with completion + duration + per-user error count. When Tika is enabled but unhealthy, surface that — today an unhealthy Tika silently produces empty FTS results.

**Store migrations.** *Today: per-account banner on account detail; crash recovery on boot.* **Want:** Persistent badge on Accounts and Stores lists while any migration runs. Plus `migration.last_progress_at` so a *stuck* migration (no progress for 10min) is distinguished from a *running* one.

**Restore jobs.** *Today: `RestoreJob` row; restore page lists them.* **Want:** Show duration while running. On completion, show the "restored as suspended placeholder account" warning *persistently* — currently this most-confusing piece of state lives in a flash message that disappears.

**APScheduler itself.** *Today: nothing.* **Want:** A `/healthz/scheduler` endpoint and a System-page row showing "alive, last tick at X, N jobs registered, next fire at Y". If the scheduler thread dies (APScheduler can swallow exceptions), every account's "last sync 4h ago" tells me nothing about *why*. A heartbeat does.

Where these live: **Dashboard** for unified counters, **Accounts list** for per-account chain status, **System** for backend services, **Audit Log** for the trail. Not collapsed. Not behind HTMX clicks I won't make at 3am.

## Honesty audit (extension)

`runtime-ops.md` already has its own honesty list. Adding more user-facing strings I want rewritten:

| Today's string | What it suggests | Reality | Suggested replacement |
|---|---|---|---|
| "Sync now" button | Will pull mail right now | Submits to a queue with at-most-4 in flight; may sit pending | "Queue sync" with a hover "will start when a sync slot is free (currently N/4 in use)" |
| "Last sync: 5 min ago" | Mail is current | mbsync may have run a 0-message no-op against a half-down IMAP | "Last attempt: 5m ago, last success: 2h ago, 0 new messages" |
| "Backup destination" generic test "OK" | The depot is healthy and can accept writes | Today's test (`test_destination`) checks reachability and credentials, not "I can write 1MB and read it back" | "Reachable" vs "Reachable + write/read OK" — two states. |
| "Snapshots: 12" badge | Twelve good restore points | Twelve restic snapshot manifest IDs; integrity not verified | "12 snapshots (last `restic check`: never / 4d ago)" |
| "Storage: 412 GB" stat card | I know my disk situation | Sum of `maildir_size_bytes`; doesn't include restic local depots, sync logs (unbounded — see below), Postgres | "Maildirs: 412 GB / Local depots: 88 GB / Logs: 2.1 GB / Free on /data: 1.2 TB (78% used)" |
| "Restored as 'Backup acct@x.com (2026-05-10)'" | Done | Created a *suspended* account with `imap_host="restored"`, `port=0`, that no scheduler will touch and no Dovecot user can log into without manual reactivation | "Restore complete. The restored mail is parked in a suspended placeholder account. Click here to activate / merge into the original." |
| "FTS reindex complete" | Search now works on everything | Tika failures are per-message and counted in `details["errors"]` but the global "complete" overrides | "Reindex complete: N successful, M failed (see details)" |
| "Account suspended" (no reason) | Some manual action | Could be admin action OR migration OR auth failure threshold | Always paired with a `suspended_reason` string. We have the data; we don't show it. |

Theme: **never show a green checkmark you cannot back with a recent successful round-trip.** If you don't know, say "unknown — last verified Xh ago".

## Operational verbs vs marketing verbs

The audit is renaming "backup" to something cleaner. Fine. From this seat, here is what is **non-negotiable** because it maps 1:1 to the underlying tooling I will be debugging at 3am:

- **`sync`** — keep. This is what mbsync does. Subprocess is called a sync, isync's name is *isync*, the cron job is "sync schedule". Renaming this would force me to translate every error to grep the right log. **Hard veto.**
- **`snapshot`** — keep. This is restic's noun. `restic snapshots`, `restic forget`, `restic restore <snapshot-id>`. Rename it and the gap between the UI and `restic ls latest` widens. **Hard veto.**
- **`retention`** — keep. This is what `restic forget --keep-...` policies are called everywhere in the storage world. Borg, Restic, ZFS, AWS lifecycle. **Hard veto.**
- **`restore`** — keep, but distinguish the two restore flows under different sub-words: `restore from snapshot` (restic) vs `restore to mailbox` (IMAP push). One verb, two destinations.
- **`maildir`** — keep where it appears (mostly admin). It is the on-disk artefact name. Hiding it from advanced views would make support harder.
- **`store`** — keep. Slightly internal but clean and consistent. It maps to a filesystem mount point, which is a real ops concern.

Acceptable to rename:
- "backup" the noun — the local-mirror meaning has to die. Local mirror == "the sync output" == the maildir. The remote restic depot can be called "depot", "archive", "off-site copy", whatever marketing wants — but in **error messages and logs** I want to see `restic` or `mbsync` named explicitly so I know which binary to debug.
- "destination" — happily renamed to "depot" or "repository" for the restic side. The IMAP-target side can be "target mailbox". Two unrelated concepts, two words.

The principle: **the words on the screen at the moment something fails must match the word in the log file I will be tailing 30 seconds later.** Anything else is a tax on MTTR.

## Disaster-recovery walkthrough

How many of these steps does the GUI guide today vs. how many require shell + docs?

**Scenario A: ransomware encrypted local store.**
1. I notice (alerting outside MFB; nothing in MFB tells me "files I expected to read are now `.locked`").
2. Identify last-clean snapshot — GUI shows snapshots per account; OK.
3. Restore each account from snapshot — GUI exists per account, but creates a *suspended placeholder* with `imap_host="restored"`. Each restored account then needs manual reactivation.
4. Re-point Dovecot at restored maildirs — **shell**: today the restored data lives in `{store}/.offsite-restore/{account-id}-{ts}/`, not at the original maildir path. The Lua userdb namespace maps to `account.maildir_path`. To make the restored copy "live" I have to either rename the directory on disk OR update DB rows. There is no GUI button "promote restored copy to live".
5. Verify mail reads via Roundcube.

GUI steps: ~3. Shell steps: at least 2 per account, plus understanding the namespace logic. **For 20 accounts this is hours.** A "Promote restored maildir" button is the single biggest DR UX win available.

**Scenario B: S3 bucket purged.**
1. Notice — current `test_destination` on next backup will fail with auth/notfound; surfaces as `last_error` on per-account page only. Likely silent for hours.
2. Re-create bucket / new bucket. Update destination credentials in admin/backup.
3. Re-init repo at new location — restic init is implicit in current backup_worker. OK.
4. Decide retention — old snapshots are gone forever; that fact is *not* communicated. The UI will happily start fresh and tell you "1 snapshot" the next morning. **Honesty bug:** snapshot count resetting to 1 should trigger a "you appear to have lost N prior snapshots" banner.

GUI steps: 2. Shell steps: 1. Information loss: high.

**Scenario C: Postgres lost.**
1. Restore Postgres from external dump (out of MFB scope — but worth documenting that **MFB has no internal Postgres backup**; the sentence "MFB backs up your email" is funny when MFB itself has no DR story for its own state).
2. App boots, runs migrations, runs migration-resume on lifespan. OK.
3. Mail on disk is intact (UUID-pathed, not affected). OK.
4. Restic destinations are intact (encryption keys in DB — wait, **are they?**). They are: `BackupDestination.restic_password` is stored encrypted with `MAILFALLBACK_SECRET_KEY`. **If you lost Postgres AND lost `MAILFALLBACK_SECRET_KEY`, your restic snapshots are unrecoverable.** This single fact deserves a banner on the admin/backup page: "Your restic password is encrypted with the application secret. Back up `MAILFALLBACK_SECRET_KEY` separately." It is not surfaced anywhere I can find.

GUI steps: 0 (everything is implicit). Shell steps: many. Cognitive land-mines: at least one (the secret-key dependency).

**Scenario D: wrong `rm -rf`.**
1. Same as A but no encryption — just gone. Same restore flow. Same suspended-placeholder problem. Same "promote to live" missing button.

Across all four scenarios, the recurring problem is: **MFB knows how to *make* backups; it does not know how to *finish* a restore.** The last mile is shell.

## What I want logged that isn't

Audit log exists. It is action-flavoured. Spot-checking `backup_worker.py` and `sync_worker.py`: **no audit entries are written by the workers themselves.** Things I want recorded that aren't:

1. **Every scheduled-job execution outcome** — `sync_completed(account=X, duration=Ys, exit=Z)` and `backup_completed(account=X, snapshot=ABC123, dest=Y, bytes=N)`. Today I have `SyncJob` rows but no append-only event stream that mixes sync + backup + migration + restore in one timeline. When I'm reconstructing what happened between 02:00 and 04:00, I want one query.
2. **Scheduler lifecycle events** — `scheduler_started`, `scheduler_stopped`, `scheduler_job_registered(id)`, `scheduler_job_missed(id, scheduled_for, fired_at)`. APScheduler's misfire grace is silent today. If a backup *would have* run at 02:00 but didn't (process restart), I want a row.
3. **Destination-level events** — `destination_unreachable(dest=X, reason=Y)`, `destination_credential_rotated(dest=X, by=user)`, `restic_check_passed/failed`. Today, destination state lives only on per-account `last_error`. If a destination dies, I see N copies of "this account's backup failed" with no aggregation.

Bonus (related): **sync log retention is per-account `cleanup_old_jobs` only; I cannot find any cap on `/data/logs/sync` total bytes.** This is the kind of thing that fills a disk over a year, takes down sync (no space for new logs), and presents as a sync error with no obvious cause. Either cap by total bytes, by age, or surface the directory size on the System page.

## Things the current-state doc gets wrong from operations angle

1. **"The system status sticky bar gives a global 'what's the backend doing' without per-page noise."** It really doesn't. It tells you Dovecot is alive and disk usage is some-number. It doesn't tell you the scheduler is alive, that restic destinations are reachable, that the sync queue is draining, that Tika (when enabled) is responding, or that the database connection pool is healthy. It is one stoplight wired to one bulb.
2. **"Audit log + flash-message toast system is solid and reusable."** The system is solid mechanically. The *coverage* is wrong: workers don't write to it, scheduler doesn't write to it, destination tests don't write to it. A solid empty bucket is still empty.
3. **"`.offsite-restore/` cleaned up by ?"** — `runtime-ops.md` itself flags this as TBD. Confirmed by reading `restore_worker.py`: there is no scheduled GC for restore staging. This is a future "/data full because of forgotten restore previews" incident waiting to happen. Should be in the bug list as concrete work, not a TBD.

That's the on-call report. The good news: most of the gaps above are *display* problems on top of state the system already tracks. The fix is plumbing, not architecture. Build the dashboard for me, not for the demo screenshot.
