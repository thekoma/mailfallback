# Sync Budget & Watchdog

MFB paces backups of large mailboxes against provider rate limits, and it self-heals when a sync job or an OAuth2 connection gets stuck. This page covers the daily sync budget, the self-recovering pause states, the in-flight watchdog, and re-authentication.

## Why a daily budget exists

Providers cap how much IMAP traffic they'll serve in a day. Gmail enforces a bandwidth ceiling and drops the connection with `[OVERQUOTA]` once an account crosses it — this is exactly what happens on a large mailbox's first full sync, which can otherwise try to pull everything in one run.

MFB tracks a **daily byte ledger** per account (`traffic_date` + `bytes_synced_today`, reset every UTC day) and stops a sync before it blows through the budget, rather than letting the provider cut the connection.

Built-in provider defaults (`PROVIDER_DAILY_BUDGET_MB`):

| Provider | Default daily budget |
|----------|----------------------|
| Google (Gmail) | 2000 MB/day |
| Microsoft, other | Unlimited (no built-in default — Microsoft rate-throttles rather than enforcing a byte quota) |

!!! note "Where this number comes from"
    2000 MB/day is a deliberately conservative margin under Gmail's observed ~2.5 GB/day download ceiling, leaving headroom for webmail and other IMAP clients hitting the same account.

## Setting a per-account budget

On an account's detail page, under **Edit Account**, the **Daily sync budget (MB)** field controls this account's override:

| Value | Meaning |
|-------|---------|
| *(blank)* | Use the provider default (2000 MB/day for Google; unlimited otherwise) |
| `0` | Explicitly unlimited — no budget cap, regardless of provider |
| `N` (any positive integer) | Cap this account at `N` MB/day |

The budget applies to every sync for that account, not just the initial one — though it matters most during the first full sync, when the volume of data pulled is largest.

## Pause reasons: self-recovering, not errors

When a sync stops early or fails in a way MFB recognizes as temporary, it sets `sync_paused_until` (a resume timestamp) and `pause_reason` on the account, and leaves the account state `idle` — **not** `error`. Only a genuine, unrecognized failure sets the account to the `error` state and turns it red in the UI.

| Pause reason | Trigger | Resume timing |
|---------------|---------|----------------|
| `budget` | The byte meter hit the account's daily budget mid-sync | Next UTC midnight + 0–30 min jitter |
| `throttle` | The provider's log tail matched a throttle signature (e.g. Gmail `[OVERQUOTA]`, "Too many simultaneous connections"; Microsoft `[THROTTLED]`, "Request is throttled", "server busy") | Exponential backoff: 4h × 2^(attempt−1), capped at 24h, + 0–10 min jitter |
| `transient` | The log tail matched a network-blip signature (connection reset, broken pipe, unexpected EOF, a timeout) | Exponential backoff: 2min × 2^(attempt−1), capped at 30min, + 0–30s jitter |

The attempt count for backoff is derived from how many sync jobs already failed with that same reason today for the account — there's no separate counter column, the job history is the counter.

!!! note "Self-recovering by design"
    Budget, throttle, and transient pauses are all expected, temporary states. The dashboard and account list only flag `error` as a problem needing attention; a paused account is working as intended and will resume on its own.

Jitter exists so that a fleet of accounts paused at the same moment (e.g. all hitting a Gmail quota reset at midnight) doesn't retry in the same instant and re-trigger the same throttle.

### How a pause lifts

Every minute, a scheduler tick (`pause-expiry`) scans accounts with a non-null `sync_paused_until` and clears any that have passed:

- If the account's **initial sync isn't complete yet**, the tick enqueues a sync immediately — the budget/backoff window just opened, and waiting for the account's regular cron schedule would waste it.
- If the initial sync is **already complete**, the tick just clears the pause; the account's normal sync schedule picks it up on its own next run.

The scheduler's periodic path also checks `sync_paused_until` directly and skips a scheduled run while a pause is still in the future — manual "Sync now" requests bypass this gate.

## The watchdog: recovering stuck syncs

Two mechanisms close out sync jobs that a crashed or wedged worker left behind — neither of them re-enqueues a replacement sync itself; they only close the stale job and, where appropriate, arrange for a resume.

**Boot-time crash sweep** (`recover_zombie_sync_jobs`) runs once at startup, before the executor accepts new jobs. Any `SyncJob` still `running` or `pending` after a restart is a zombie — a fresh process has no record of it — and gets closed as `failure_kind = interrupted`.

**In-flight watchdog** (`recover_stalled_sync_jobs`) runs on a periodic tick (every `MAILFALLBACK_SYNC_WATCHDOG_INTERVAL_S` seconds, default 60) for long-lived containers where the worker thread itself wedges without the process dying. A running job is reaped when **both**:

- it started longer than `MAILFALLBACK_SYNC_STALL_GRACE_S` ago (default 900s / 15 min — a grace period so a normal, ongoing sync isn't reaped), **and**
- it has had no fresh progress sample in `MAILFALLBACK_SYNC_STALL_THRESHOLD_S` (default 600s / 10 min) *and* no live subprocess is still running.

A reaped job is closed as `failure_kind = interrupted`, and MFB force-kills any lingering subprocess to free the blocked worker slot.

There is also a hard per-invocation ceiling, `MAILFALLBACK_SYNC_JOB_MAX_RUNTIME_S` (default 21600s / 6h): mbsync is killed if a single run exceeds it, regardless of watchdog ticks.

After either sweep closes a job, the account goes back to `idle` and:

- if the **initial sync is still incomplete** and there's budget headroom left today, MFB sets an already-expired pause so the next minute's `pause-expiry` tick re-enqueues it right away;
- otherwise, any existing budget/throttle pause is left untouched — the account keeps its computed resume time rather than jumping the queue.

!!! warning "Worker pool starvation"
    If a single watchdog tick reaps most of the worker pool at once, MFB logs a warning suggesting a restart — a burst of stalls usually means the pool itself is degraded (e.g. a hung network mount), not that the reaping logic is wrong.

## Re-authentication (`needs_reauth`)

OAuth2 accounts (Gmail, Microsoft) refresh their access token automatically before each sync using the stored refresh token. Two outcomes distinguish a real sign-in problem from a transient one:

- **Terminal failure** — the refresh token itself is invalid (missing/malformed credentials, or the provider returns `invalid_grant`, meaning the token was revoked or expired). MFB sets the account's sync state to `needs_reauth` and sends a `needs_reauth` notification ("Reconnect the account in MailFallBack to resume backups.").
- **Non-terminal failure** — a network error or a provider 5xx during the refresh call. MFB treats this like any other sync error (`error` state) rather than demanding re-authentication, since retrying may simply work next time.

A `needs_reauth` account is skipped by the scheduler's periodic path entirely (it won't retry a token that's known to be dead) and won't inherit a self-recovering pause.

### Resuming after reconnect

From the account detail page, the **Re-authenticate** button walks the account back through the OAuth2 consent flow. On a successful callback, MFB:

1. Clears `needs_reauth` (and any lingering `error` state left by the same failed token) back to `idle`.
2. Immediately enqueues and submits a new sync job for the account, so the account starts backing up again without waiting for its regular schedule.

See [Notifications](../user-guide/notifications.md) for how `needs_reauth`, `sync_paused`, and `sync_error` events surface to users and admins.
