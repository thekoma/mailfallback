# Sysadmin critique

## TL;DR

- The UI confuses *configured* with *working* across both sync and restic — a green badge today proves a row exists, not that bytes moved last night.
- There is no fleet health surface: silent per-account failures hide inside individual account-detail pages while the dashboard counts only `sync_state == error`.
- Operator vocabulary (sync, snapshot, retention, restore, prune) is the contract with the underlying tooling — rename the marketing layer if you must, but do not touch these verbs or the strings in error messages and logs.

## The visibility gap

When 1 of 20 accounts silently fails overnight mbsync, today I get a red pill on that row in `/accounts`, a tile in "Needs Attention", and `Account.last_error` on click-through — adequate, *if* I open the dashboard. No email, webhook, or `/metrics` counter. When restic fails the same morning, I get nothing on the dashboard: `AccountBackup.last_status=failed` lives only inside the collapsed `<details>` on each account-detail page (current-state.md:106); admin → Backups shows depot config, not per-account run history. The dashboard "Errors: N" counts sync only — restic does not increment it. Both systems failing the same night surface in two different places, only one of which I would think to check.

## Failure-mode coverage

| F# | Failure | Detectability today | Recovery clarity | Min UI fix |
|---|---|---|---|---|
| F1 | Sync fails silently overnight | MED — dashboard pill if I look | MED — log + retry visible | Email/webhook on transition to `error`; `/metrics` counter |
| F2 | Offsite fails silently | LOW — buried in account `<details>` | LOW — error string only | Promote `last_status` to dashboard; per-account "offsite health" column |
| F3 | Disk fills up | LOW — single stat card, informational | NONE — no remediation hint | Per-store free%, threshold colours, "what's growing" row |
| F4 | Offsite repo unreachable | LOW — only at "Test" click | LOW — generic error | Periodic depot reachability probe; depot row goes red on stale `last_check_at` |
| F5 | User can't find offsite | NONE — buried | N/A | Promote out of collapsed `<details>` |
| F6 | Configures offsite, never local sync | NONE | NONE | Banner: "policy exists but account has never synced" |
| F7 | Snapshot completeness uncertainty | NONE | N/A | Periodic `restic check`; show `last_integrity_check_at` |
| F8 | Delete account, fate of snapshots | NONE | NONE | Confirm dialog must say "snapshots in depot X will/won't be removed" with explicit toggle |
| F9 | Migrate depot local→S3 | NONE | NONE | Depot-migration flow (mirror StoreMigration) |
| F10 | Two accounts, same email, diff store | LOW — both shown, no disambiguation | N/A | Show store name in account list; (email, store) badge |

Cheapest, highest-MTTR-impact wins: **F2, F3, F4, F7.** All four are server-side state that already exists or is one subprocess call away — they are simply not surfaced.

## Status surfaces I want

- **mbsync per account** — alive / `last_success_at` (separate from `last_attempt_at`) / unhealthy because `<last_error>`. Today's `last_sync_at` lies — it is the last completion timestamp regardless of exit code semantics.
- **restic per account** — alive / last successful snapshot / N snapshots / depot reachable / unhealthy because. Today: only inside `<details>`; no fleet view; no `next_backup_at`.
- **FTS / Tika** — coverage % / last reindex / Tika reachable y/n. Today only visible during a manually-triggered task; an unhealthy Tika silently produces empty FTS results.
- **Store migrations** — N in flight / longest running / failed-needs-resume / `last_progress_at` to distinguish stuck from running. Today only on per-account detail.
- **Restore jobs** — N running / N failed last 7d / oldest still-suspended restored placeholder. Today only on the originating restore page; the "restored as suspended" warning lives in a flash that disappears.
- **APScheduler heartbeat** — `last_tick_at`, jobs registered, next fire. Today: nothing. If the scheduler thread dies, every account reads "last sync 4h ago" with no explanation.

All belong on a `/admin/health` page plus Prometheus metrics for external alerting.

## More overpromise strings

- `templates/account_detail.html` — "Backup configured" → "Configured (last run: failed 3h ago)" or colour red. Existence-of-row is not health.
- Sync flash "Backup failed" → must say `mbsync failed` or `offsite snapshot failed`; today ambiguous (current-state.md:120).
- `partials/backup_snapshots.html` — "Snapshots: 12" → "12 restic snapshots (last `restic check`: never / 4d ago)". A count alone is theatre.
- "Restored as 'Backup X (date)'" → must include "**This account is suspended; activate when ready**" persistently, not in a transient toast.
- `account_detail.html` "No backup configured" → "No **offsite** backup configured (local sync is independent)".
- Dashboard "Errors: N" → split into `sync errors / offsite errors / restore errors`. One number conflates three subsystems.
- "Storage: 412 GB" → split Maildirs / local depots / sync logs / Postgres / free on `/data`.

## Operational verbs that must NOT change

`sync` (mbsync verb), `snapshot` (restic noun), `retention` / `forget` / `prune` (restic verbs), `restore` (restic + IMAP), `init`, `reindex` (Dovecot FTS), `force-resync`, `migrate` (store migration). These map 1:1 to underlying CLIs and to log lines I grep at 03:00. Rename the marketing word "backup" all you want; the strings in **error messages and worker logs** must still name the binary so I know which to debug.

## DR walkthrough

- **Ransomware encrypted local store** → restore from depot. Today: **SOME** — per-account restore exists, but creates a *suspended placeholder* at `{store}/.offsite-restore/...` with `imap_host="restored"`. No "promote restored maildir to live" button — for 20 accounts this is hours of shell work. Single biggest DR UX win available.
- **S3 bucket purged** → today: **NONE** for rebuild. Worse: snapshot count silently resets to 1 next morning with no "you appear to have lost N prior snapshots" banner.
- **Postgres lost** → today: **NONE**. Maildirs survive but are orphaned UUIDs. `detect_orphans()` exists in code but has no UI. Land-mine: `BackupDestination.restic_password` is encrypted with `MAILFALLBACK_SECRET_KEY` — lose both Postgres and the secret and your snapshots are unrecoverable. This dependency is surfaced *nowhere*.
- **Wrong `rm -rf /data/mailboxes`** → today: **NONE** for bulk; per-account restore only, with the same "suspended placeholder" last-mile gap as scenario A.

## What I want logged that isn't

- **Worker outcomes in audit log** — workers (`sync_worker.py`, `backup_worker.py`) write to `SyncJob` / `AccountBackup` but not to `AuditLog`. I want one timeline mixing sync + backup + migration + restore for incident reconstruction.
- **Scheduler lifecycle** — `scheduler_started`, `job_missed(id, scheduled_for, fired_at)`. APScheduler misfire grace is silent today.
- **Destination-level events** — `destination_unreachable`, `restic_check_passed/failed`, credential rotations. Today destination state lives only as N copies of per-account `last_error` with no aggregation.
- **Sync-log directory size** — `/data/logs/sync` has per-job cleanup but no total-bytes cap; will silently fill a disk over a year.

## Things current-state doc gets wrong

- "The system status sticky bar gives a global 'what's the backend doing'" (line 167) — it tells me Dovecot health and disk usage. Nothing about scheduler liveness, sync/backup queue depth, depot reachability, or Tika. One stoplight, one bulb.
- "Audit log + flash-message toast system is solid and reusable" (line 168) — solid mechanically, but workers and scheduler don't write to it. A solid empty bucket is still empty.
- `runtime-ops.md` flags `.offsite-restore/` cleanup as "TBD" — confirmed: `restore_worker.py` has no scheduled GC. This belongs in the bug list, not the unknowns list.
