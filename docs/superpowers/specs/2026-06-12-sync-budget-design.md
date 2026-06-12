# Throttle-aware first sync with bandwidth budget — Design

**Date**: 2026-06-12
**Cycle**: sync-budget (stacked on feat/restore-attachments-view → PR chain #176→#179)
**Status**: approved approach B (budget di banda), native-tooling verification completed

## Problem

First full sync of large mailboxes hits provider rate limits. Evidence (account
"Main gMail", 2026-06-12):

```
IMAP error: unexpected BYE response: [OVERQUOTA] Account exceeded command or bandwidth limits.
```

- Run 1: 36 minutes of download, then OVERQUOTA death.
- Run 2 (10 min later via blind `*/10` cron): died in 3 minutes, +84 messages.
- The account lands in `sync_state = error` — red noise in dashboard/accounts,
  indistinguishable from real failures.
- Gmail's IMAP quota (~2.5 GB/day download + command-rate limits) is **per
  account, not per client**: when MFB exhausts it, the user's phone/desktop
  IMAP for that mailbox breaks too. So self-restraint is a correctness
  feature, not cosmetics.
- mbsync syncs boxes in listing order (observed alphabetical: "Alessandro
  Latino", "Archives", "Banca IWBank"…), so INBOX may not land for days on a
  multi-day initial sync.

## Verified native toolbox (isync 1.5.1)

Verified against the official man page, the NEWS changelog, and the live wire
(2026-06-12, real Gmail account, `-Dn --dry-run` on INBOX only):

| Tool | Finding | Use |
|---|---|---|
| `COMPRESS=DEFLATE` | **Already negotiated automatically** with Gmail (wire-verified: `>>> 3 COMPRESS DEFLATE` after auth; `+HAVE_LIBZ` build). | Free win, nothing to do. Our byte meter measures *uncompressed* filesystem bytes, so if Google meters wire bytes our budget is conservative — safe direction. |
| `PipelineDepth` | Man page blesses our exact case: *"may also be used to limit average bandwidth consumption (**GMail may require this** if you have a very fast connection)"*. Default: unlimited. | Set `PipelineDepth 1` while initial sync incomplete — addresses the "command limits" half of OVERQUOTA. |
| Per-folder sync state, per-message commit | Resume across failed runs proven in production (+84 messages kept). | Retries never re-download; the architecture is chunk-friendly by design. |
| `CHANNEL:BOX1,BOX2` invocation syntax | mbsync accepts box-scoped runs of a channel. | Priority pass: invocation 1 = `channel:INBOX`, invocation 2 = full channel (already-synced boxes are cheap no-ops). Deterministic ordering without relying on undocumented internals. |
| `Old` sync operation (new in 1.5) | "Retry previously skipped messages". | Candidate mop-up pass at initial-sync completion; noted, not required for v1. |
| `Timeout` (default 20s) | OVERQUOTA BYE is detected fast; no hang risk. | Keep default. |
| `CopyArrivalDate` (default `no`) | File mtime = write time. | The byte sampler depends on this default. If ever enabled, switch sampler to ctime. Guard with a comment in the generator. |
| Rate/bandwidth limiting | **Absent** from man page and changelog. | Confirmed: the budget enforcer must live in MFB. |

**Evaluated and rejected:**

- `MaxSize` + placeholder + `Upgrade` (1.5 semantics): placeholders in a
  *backup* product are lying backups — a restore would push placeholder
  bodies. Rejected as default; possible future opt-in with a guaranteed
  Upgrade pass.
- `MaxMessages` staging (option C, newest-first): 1.5.1 changelog literally
  fixes a crash "when resuming message propagation with MaxMessages" —
  fragile path. Deferred as decided; needs empirical testing first.

## Design

### 1. Throttle classification (sync_worker)

Signature table per provider matched against the mbsync log tail:

- Gmail: `[OVERQUOTA]`, `Too many simultaneous connections`
- Microsoft: M365 throttling responses (`Request is throttled`, server-busy
  BYE variants)
- Generic: extensible list, first match wins.

New nullable column `sync_jobs.failure_kind` (`throttled` | `budget_paused` |
`error`) — no JobStatus enum surgery. A throttled/budget-paused job is **not**
an error in any UI surface.

### 2. Byte meter

Sampler thread during the run (joins the existing live-log machinery): walks
the account maildir every ~30 s summing `st_size` of files with mtime ≥ run
start. Feeds both the live progress (msgs + bytes) and the budget enforcer.
Depends on `CopyArrivalDate no` (see toolbox).

### 3. Traffic ledger

Two columns on `accounts`: `traffic_date` (date) + `bytes_synced_today`
(bigint). Reset on date rollover (UTC). Updated by the sampler at each tick
and finalized at job end.

### 4. Budgets

- Provider defaults in code: `google` → 2000 MB/day (prudent under the 2.5 GB
  observed ceiling); `microsoft` → none initially (no hard daily byte quota
  documented; throttle classification covers it); `other` → none.
- Per-account override column `accounts.daily_sync_budget_mb`
  (NULL = provider default, 0 = unlimited). No new env vars (Settings-in-DB
  direction).

### 5. Enforcement

When the sampler crosses the budget: graceful stop via the existing
`stop_sync_job` mechanism (SIGTERM → 5 s → SIGKILL; mbsync commits per
message, loses at most the in-flight one). Job ends `failure_kind =
budget_paused`. **The provider quota is never touched** — the user's other
IMAP clients stay alive.

### 6. Resume scheduling

New columns `accounts.sync_paused_until` (timestamptz) + `accounts.pause_reason`
(`budget` | `throttle`):

- Budget exhausted → resume at next UTC day rollover + jitter (0–30 min).
- Throttled (wall already hit) → backoff: 4 h, ×2 per repeat within the same
  day, cap 24 h, jitter.
- Scheduler skips accounts with `sync_paused_until > now()`.
- Manual "Sync now" overrides the pause with warning copy (it may burn quota).

### 7. Initial-sync state

`accounts.initial_sync_completed_at` (timestamptz, NULL = never completed a
clean full pass). Set when a sync run completes with exit 0 and no
budget/throttle interruption. Drives:

- UI chip: `Initial sync · 12.4k msg · 1.9/2.0 GB oggi · riprende 02:00`
- `PipelineDepth 1` in the generated mbsync config while NULL.
- Priority pass while NULL: invocation 1 = `channel:INBOX`, invocation 2 =
  full channel (single job, two sequential subprocesses; the budget/throttle
  machinery spans both).

### 8. UI

- Accounts page + account detail: dedicated state chip (not error-red) with
  progress, today's bytes vs budget, and resume time.
- Dashboard "Needs Attention" and Errors panel exclude budget/throttle pauses;
  only true errors stay red.
- Account detail: budget override field (admin/owner edit form), with
  provider-default shown as placeholder.

### 9. Migration

`021` (stacked on the restore chain's 020 — keeps alembic linear; this branch
forks from feat/restore-attachments-view for that reason). All new columns
nullable or defaulted: `accounts.traffic_date`, `accounts.bytes_synced_today`
(server_default '0'), `accounts.daily_sync_budget_mb`,
`accounts.sync_paused_until`, `accounts.pause_reason`,
`accounts.initial_sync_completed_at`, `sync_jobs.failure_kind`.

Backfill: existing accounts with at least one successful sync get
`initial_sync_completed_at = last_sync_at` (so only genuinely-new accounts
enter the initial-sync regime).

### 10. Testing

- Classifier: signature table units (Gmail OVERQUOTA from the real log,
  M365 strings, no-match → error).
- Budget enforcement: fake maildir growth → sampler crosses budget → graceful
  stop invoked → job `budget_paused`, ledger updated, `sync_paused_until` set.
- Rollover: ledger resets across `traffic_date` change.
- Backoff: throttle repeats double the delay, capped.
- Scheduler: paused accounts skipped; manual sync overrides.
- Config generator: `PipelineDepth 1` iff initial sync incomplete;
  `CopyArrivalDate` guard comment pinned.
- Worker: two-invocation priority pass while initial sync incomplete.
- Migration: columns + backfill.
- UI: chip states, dashboard exclusions.

## Out of scope (declared)

- Option C (newest-first via MaxMessages) — deferred, fragile in 1.5.1.
- MaxSize placeholders — rejected for backup integrity.
- Microsoft Graph API sync — different architecture entirely.
- Per-provider hourly (vs daily) budget windows — start daily, observe.
