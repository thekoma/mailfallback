# Design: unified per-mailbox status resolver

Status: draft v4 (elephant/goldfish design check, round 4). Branch: `design/impeccable-critique`.

## Why

The Impeccable critique of the web UI (`.impeccable/critique/2026-09-30T15-53-17Z__src-mailfallback-templates.md`, priority issue **P0 "status disagrees across screens"**) found that surfaces compute their own answer to "is this mailbox OK?" and contradict each other. PRODUCT.md's first success criterion is "Quiet confidence: an owner can tell at a glance whether every mailbox is healthy"; principle 1 is "calm by default, loud only when it matters".

Ten places derive mailbox or job health independently today:

| # | Surface | Where | Problem |
|---|---|---|---|
| 1 | Dashboard error stat + Needs Attention | `routers/ui.py:348-350`, `380-417` | Initial syncs listed as attention; stale check ignores `suspended` |
| 2 | Dashboard chain, Source stage | `templates/dashboard.html:86` | Dot hardcoded `stats-dot-ok` |
| 3 | Dashboard chain, Local backup stage | `routers/ui.py:475-476`, `dashboard.html:91-104` | `healthy = idle`, `failing = error`; `needs_reauth`/`syncing` fall in neither |
| 4 | Dashboard Recent Activity | `routers/ui.py:434-466`, `dashboard.html:193-217` | Uses `job.status` only: a `budget_paused` job renders red "failed" |
| 5 | Accounts table status column | `partials/accounts_table.html:53-82` | No `needs_reauth` branch: a reauth account reads "idle"/"Initial sync" |
| 6 | Account detail Health box | `account_detail.html:36-84` | Local sync ignores reauth/pause/suspended; Auth dot green for a revoked token |
| 7 | Account detail hero | `routers/ui_accounts.py:189-239` `_compute_hero_state` | Its own private precedence order |
| 8 | System status strip (admin) | `routers/ui.py:522-573`, `partials/system_status.html:14-18,82-95` | Counts jobs not accounts; all `sync_state=='error'` incl. paused and token-refresh; ignores reauth; "1 errors" |
| 9 | Agent API / MCP `list_mailboxes` | `routers/agent.py:52-59` `MailboxOut`, `mcp_server.py:337-382` | No status at all |
| 10 | Account detail Sync History | `partials/account_history.html:1-15` | Every failed job red by `job.status`, including budget/throttle pauses and stops |

## Scope

**In:**
1. New module `src/mailfallback/services/mailbox_status.py`:
   - `resolve_mailbox_status(account, *, last_job=None, now=None) -> MailboxStatus` — pure (no DB, no I/O).
   - `resolve_job_outcome(job) -> JobOutcome` — pure.
   - `latest_finished_jobs_by_account(db, account_ids) -> dict[str, SyncJob]` — the one DB helper: a single query returning, per account id, the most recent **finished** `SyncJob` (status in completed/failed/cancelled, ordered by `completed_at`, which is never NULL for finished jobs). Finished-only is deliberate: a pending retry or a running job must not hide how the previous run ended, and it avoids the NULLS-FIRST (PostgreSQL) vs NULLS-LAST (SQLite) ordering difference of today's hero query. **Every** surface, including the detail hero, gets its job from this helper.
2. Surfaces 1-10 consume it. None derives mailbox health on its own afterwards.
3. Additive `status` object on the agent API `MailboxOut` (and so on the MCP `list_mailboxes` tool) + skill doc update.
4. Minimal CSS: one dot token pair and two classes for the new "attention" tone, and two hero state names added to existing hero rules (below). No other CSS.
5. Two small new branches in `partials/sync_panel.html` (`stopped`, `out-of-date`) plus one conditional "Update password" link in its existing error branch — the only new markup.
6. Move `TOKEN_REFRESH_FAILED` from `services/sync_worker.py:58` to `src/mailfallback/constants.py`; `sync_worker.py` imports it from there (the name stays importable from `sync_worker`).

**Out (explicitly):**
- Visual redesign, layout changes, copy rewrites beyond what is spelled out here. Needs Attention `reason` strings stay exactly as today (copy is the later clarify pass).
- Off-site / Repository / Snapshot health (`ui_restore._compute_health`, `ui_backup` destination health, `_backup_attention_items`, config-backup items). Unchanged.
- REST `/api/accounts`, `/metrics`, notification emitter, scheduler stale-notify predicate, worker pause-clearing on Timeout/Exception paths. Follow-ups.
- Owner-level migration (`User.migrating`). Follow-up.
- Dead `/accounts/{id}/sync-status` route + `partials/sync_status.html`. Untouched.

## Interfaces

```python
# src/mailfallback/services/mailbox_status.py
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from sqlalchemy import func
from sqlalchemy.orm import Session

from mailfallback.constants import TOKEN_REFRESH_FAILED
from mailfallback.models import Account, AuthType, JobStatus, SyncJob, SyncState


class Tone(StrEnum):
    ok = "ok"                # current
    active = "active"        # working or self-recovering
    attention = "attention"  # owner must act; nothing is broken yet
    error = "error"          # real failure — the ONLY red tone
    muted = "muted"          # deliberately off, stopped, or not started


class MailboxState(StrEnum):
    migrating = "migrating"
    suspended = "suspended"
    sign_in_needed = "sign_in_needed"
    first_sync = "first_sync"      # sync running, initial sync never completed
    syncing = "syncing"            # sync running, initial sync completed
    paused = "paused"              # pause gate set (budget/throttle/transient/interrupted)
    stopped = "stopped"            # last run was stopped by a user (signal), not a failure
    error = "error"
    initial_sync = "initial_sync"  # initial sync started, idle between passes
    initial_stalled = "initial_stalled"  # initial sync started but nothing happened for STALE_AFTER
    waiting = "waiting"            # never started
    stale = "stale"                # last success older than STALE_AFTER
    current = "current"


class NextAction(StrEnum):
    reconnect = "reconnect"              # OAuth sign-in: /auth/{provider}/start
    update_password = "update_password"  # app password rejected: /accounts/{id}#admin-edit  # pragma: allowlist secret
    retry = "retry"                      # real error: POST /api/sync/{id}
    sync_now = "sync_now"                # stale / stalled: POST /api/sync/{id}


STALE_AFTER = timedelta(days=7)  # same threshold as today's dashboard stale item and scheduler stale-notify


@dataclass(frozen=True)
class MailboxStatus:
    state: MailboxState
    tone: Tone
    label: str               # UI chip text
    detail: str | None       # UI-only sentence; may contain last_error. NEVER sent to the agent API.
    action: NextAction | None
    needs_attention: bool    # True only for sign_in_needed, error, stale, initial_stalled
    signed_in: bool          # independent of precedence: False iff not is_authenticated, or needs_reauth,
                             # or (error AND oauth2 AND last_error == TOKEN_REFRESH_FAILED). Drives the Source stage and the Auth dot.
    badge: str               # CSS badge class (see table)
    icon: str                # Lucide icon name (see table)
    spin: bool               # icon gets the `spin` class
    last_success_at: datetime | None  # account.last_sync_at, tz-aware
    resumes_at: datetime | None       # account.sync_paused_until whenever pause_reason is set (any state), tz-aware, may be in the past


def _aware(dt: datetime | None) -> datetime | None:
    # Local copy of scheduler._aware_utc: importing services.scheduler would pull APScheduler into a pure module.
    return None if dt is None else (dt if dt.tzinfo else dt.replace(tzinfo=UTC))


def resolve_mailbox_status(account: Account, *, last_job: SyncJob | None = None, live_progress: bool = False,
                           now: datetime | None = None) -> MailboxStatus: ...
# live_progress: the caller passes True when the in-memory sampler has progress for this account
# (`account_live_status(account)["pct"] is not None`, routers/ui.py:182-212). The resolver stays pure: it never reads the sampler itself.

def latest_finished_jobs_by_account(db: Session, account_ids: list[str]) -> dict[str, SyncJob]:
    # SELECT sync_jobs.* JOIN (SELECT account_id, max(completed_at) m FROM sync_jobs
    #   WHERE account_id IN (...) AND status IN ('completed','failed','cancelled') AND completed_at IS NOT NULL
    #   GROUP BY account_id) ON account_id + completed_at = m
    # Empty input → {} without querying. Portable SQL (SQLite + PostgreSQL). Ties: any one of them (dedupe in Python).
    ...


TONE_DOT = {"ok": "stats-dot-ok", "active": "stats-dot-syncing", "attention": "stats-dot-warning",
            "error": "stats-dot-error", "muted": ""}   # the ONE tone→dot map; passed to templates as `dot_class`
```

### Precedence (first match wins)

`now` defaults to `datetime.now(UTC)`. Every datetime read from the account or job goes through `_aware`. `last_job` is the latest **finished** job (see `latest_finished_jobs_by_account`).
- "Started initial sync" = `live_progress` OR `account.initial_sync_total_messages is not None` OR (`last_job is not None` AND `last_job.started_at is not None`). A job blocked by the pre-flight guards (`sync_worker.py:799-827`: "Sync blocked: …") never gets `started_at` (set at `sync_worker.py:848`), so it does not count. (The worker *tries* to write `initial_sync_total_messages` at the start of an initial-regime job, `sync_worker.py:900-913`, but that count is non-fatal and can stay None.)
- "Last activity" = `_aware(last_job.completed_at)` when `last_job` exists and `last_job.started_at is not None`, else `_aware(account.created_at)`; None when both are None (legacy) → never "old". A blocked job therefore never resets the stall clock (it falls back to `created_at`, which is older — conservative).
- "Stale threshold" = `STALE_AFTER` (7 days), or `2 × schedule interval` if that is larger, where the interval is the gap between the next two fire times of `CronTrigger.from_crontab(account.sync_schedule, timezone=UTC)` after `now` (APScheduler 3.11, `apscheduler.triggers.cron`, no scheduler import). An invalid cron string → 7 days.
- "Manual only" = `account.sync_schedule` is None or empty. A manual-only mailbox is **never** long unsynced (the owner syncs it by hand; nobody promised freshness).
- "Old" (for a datetime `t`) = `t is not None and t < now - stale threshold`.
- "Old" (for a datetime `t`) = `t is not None and t < now - STALE_AFTER`.
- "Long unsynced" = not manual only AND (initial sync completed and `last_sync_at` is old, or initial sync not completed and last activity is old).

| # | Condition | state | tone | label | badge | icon (spin) | action | needs_attention |
|---|---|---|---|---|---|---|---|---|
| 1 | `migrating` | migrating | active | "Migrating" | badge-syncing | loader | None | False |
| 2 | `suspended` | suspended | muted | "Suspended" | badge-disabled | pause-circle | None | False |
| 3 | `not is_authenticated` OR `sync_state == needs_reauth` OR (`sync_state == error` AND `auth_type == oauth2` AND `last_error == TOKEN_REFRESH_FAILED`) | sign_in_needed | attention | "Sign-in needed" | badge-warning | key-round | reconnect | True |
| 4 | `sync_state == syncing` AND `initial_sync_completed_at is None` | first_sync | active | "Initial sync" | badge-info | loader (spin) | None | False |
| 5 | `sync_state == syncing` | syncing | active | "syncing" | badge-syncing | loader | None | False |
| 6 | `sync_state == error` AND `last_job` is not None AND `last_job.status == failed` AND `last_job.signal` is set AND `last_job.failure_kind is None` AND NOT long unsynced | stopped | muted | "Stopped" | badge-disabled | circle-slash | None | False |
| 7 | `sync_state == error` AND `pause_reason` is not set | error | error | "error" | badge-error | alert-circle | update_password if `auth_type == app_password` and `sync_progress._classify_error(last_error)[0] == "auth"`, else retry | True |
| 8 | initial sync not completed AND long unsynced | initial_stalled | attention | "Initial sync stalled" if started, else "Never synced" | badge-warning | alert-triangle | sync_now, or None when `pause_reason` is set | True |
| 9 | initial sync completed AND long unsynced | stale | attention | "Out of date" | badge-warning | clock | sync_now, or None when `pause_reason` is set (the agent/UI trigger refuses a paused mailbox; `resumes_at` tells when it lifts) | True |
| 10 | `pause_reason` is set | paused | active | "Paused" | badge-info | pause-circle | None | False |
| 11 | initial sync not completed AND started | initial_sync | active | "Initial sync" | badge-info | download | None | False |
| 12 | initial sync not completed (not started) | waiting | muted | "Waiting for first sync" | badge-disabled | clock | None | False |
| 13 | otherwise | current | ok | "idle" | badge-idle | check-circle | None | False |

Row ordering, in words: identity/permission problems first (1-3), then a sync in progress (4-5), then how the last run ended (6-7), then "has anything been copied lately?" (8-9) — which **beats a pause**, so a mailbox that is throttled or budget-paused every day and never finishes still surfaces after 7 days — then the self-recovering pause (10) — which also absorbs `error` + pause, since row 7 requires no pause — then the quiet states (11-13). A legacy row with `initial_sync_completed_at` set and `last_sync_at` None is not "long unsynced" (the definition reads `last_sync_at`) and falls to row 13.

**Detail** (UI only):
- sign_in_needed via `error` + `TOKEN_REFRESH_FAILED` (non-terminal refresh failure): "Couldn't refresh the sign-in. It retries on the next sync; reconnect if this keeps happening." Otherwise: "Reconnect your Google account to resume syncing." (`provider == "google"`), "...Microsoft account..." (`provider == "microsoft"`), else "Reconnect this mailbox to resume syncing." The codebase's provider values are `google`, `microsoft`, `yahoo`, `icloud`, `protonmail`, `other`.
- paused: `PAUSE_TOOLTIPS[pause_reason]`, falling back to "Paused for now. It resumes on its own." for an unknown reason.
- stopped: "The last sync was stopped. The next scheduled sync runs normally." — or, when manual only, "The last sync was stopped. Start a sync when you're ready."
- error: `last_error[:200]`, or "The last sync failed." when empty.
- initial_stalled: "The initial sync has not progressed for over 7 days." if started, else "This mailbox has never synced (added {N} days ago)." (N = whole days since `created_at`, ≥ 7).
- stale: "Last sync was {N} days ago." (N = whole days since `last_sync_at`, always ≥ 7, so always plural).
- first_sync, initial_sync: "First full sync incomplete" (the existing tooltip).
- everything else: None.

**Decisions the precedence encodes (and behaviour changes vs today):**
- **Pause beats error (row 7 requires no pause, so error + pause reaches row 10).** Keeps the sync-budget spec §8 exclusion (`routers/ui.py:346-347`) and the "error ⇒ no pause" note (`routers/ui_accounts.py:212-215`); `tests/test_ui_sync_budget.py::test_dashboard_excludes_paused_and_shows_initial_info` keeps asserting an error+pause account is not an error.
- **Paused = `pause_reason` is set, whatever `sync_paused_until` says** — same as today's accounts table. **Small behaviour change:** today's hero additionally requires `sync_paused_until is not None` (`ui_accounts.py:216`); a row with a reason and no date now reads paused on the hero too. An expired pause is cleared by the expiry tick within a minute; `resumes_at` in the past renders as today's `resume_rel` does. This keeps the three existing paused tests (June-2026 fixtures) green and makes the `interrupted` reason reachable (the worker writes `now - 1s`).
- **Long unsynced beats pause (rows 8-9 before 10): behaviour change.** Today a paused account can also be a stale attention item on the dashboard (the stale check at `ui.py:414-417` runs after the error branch). The resolver keeps that: a pause older than 7 days of no progress is attention, not "active".
- **A never-synced mailbox is not quiet forever (row 8, "Never synced"): behaviour change.** Today it is an info attention item from day one; now it is muted "Waiting for first sync" for 7 days after `created_at`, then amber "Never synced".
- **`PAUSE_TOOLTIPS`** moves from `routers/ui.py:175-179` into `mailbox_status.py`, gaining `"interrupted": "Interrupted by a restart. It resumes on its own shortly."`. `routers/ui.py` re-imports it so `account_live_status` and its tests are unchanged.
- **Suspended beats error (row 2): behaviour change.** Today a suspended account in `error` is an attention item. Suspended means syncing is off on purpose; it becomes muted "Suspended".
- **Stop is not a failure (row 6): behaviour change.** A user stop leaves `sync_state=error`, `failure_kind=None` and `job.signal` set (`sync_worker.py:1095-1097`, `1205-1217`). Today it is a red error with Retry; now muted "Stopped", everywhere including the hero (new small `stopped` hero branch, see UX flow 3). The scheduler still runs error-state accounts, so the next scheduled sync proceeds. Because `last_job` is the latest *finished* job, a queued retry does not flip a stopped account back to red.
- **`Unauthenticated` becomes `Sign-in needed`: behaviour change.** Today's accounts table shows red "Unauthenticated" (OAuth without credentials) before checking migrating; now migrating is checked first and the chip is amber "Sign-in needed".
- **`enabled=False` ("Hidden" in webmail) is orthogonal.** The resolver ignores it: hidden mailboxes are still synced by the scheduler (`scheduler.py:26-68` does not check `enabled`), so their health is real. **Behaviour change:** today a hidden idle account shows only "Hidden"; it will show "Hidden" plus its status badge, and a hidden account can surface under Needs Attention. The scheduler's stale-notify excludes hidden accounts (`scheduler.py:350`) — aligning it is a follow-up.
- **`first_sync` vs today's hero rule.** The hero keys first-sync on `syncing AND last_sync_at is None` (`ui_accounts.py:208-210`); the resolver keys on `initial_sync_completed_at is None`. The worker sets both in the same block (`sync_worker.py:1108-1117`) and migration 021 backfilled them equal, so they are equivalent.
- **Credential errors reuse the existing catalog**: `sync_progress._classify_error(text)` (`sync_progress.py:101-163`) already maps AUTHENTICATIONFAILED / LOGIN failed / Invalid credentials to category `"auth"`; the resolver imports it (pure regex, no I/O) instead of adding a second classifier. `None`/empty `last_error` → not auth.
- **Non-terminal token refresh stays `sign_in_needed` (row 3): explicit decision.** The worker sets `error` (not `needs_reauth`) when a refresh fails for a non-terminal reason (`sync_worker.py:450-453, 862-870`). That is today's hero behaviour (`ui_accounts.py:223`), and `routers/auth.py:130-144` `_resume_after_reauth` treats both cases as fixed by reconnecting. Reconnecting always helps and waiting sometimes does; the detail says so. The resolver extends that verdict to every surface.

```python
@dataclass(frozen=True)
class JobOutcome:
    tone: Tone
    label: str
    badge: str
    icon: str
    spin: bool

def resolve_job_outcome(job: SyncJob) -> JobOutcome: ...
```

| job | tone | label | badge | icon (spin) |
|---|---|---|---|---|
| status completed | ok | "synced" | badge-idle | check-circle |
| status running | active | "syncing" | badge-syncing | loader (spin) |
| status pending | muted | "queued" | badge-disabled | clock |
| status cancelled (no sync code path sets it today; kept for totality) | muted | "queued" | badge-disabled | clock |
| failed, `signal` set and `failure_kind is None` | muted | "stopped" | badge-disabled | circle-slash |
| failed, `failure_kind is None`, `log` starts with "Sync blocked:" | muted | "skipped" | badge-disabled | circle-slash |
| failed, `failure_kind is None`, `log == TOKEN_REFRESH_FAILED` | attention | "sign-in failed" | badge-warning | key-round |
| failed, `failure_kind == "budget_paused"` | active | "paused (daily limit)" | badge-info | pause-circle |
| failed, `failure_kind == "throttled"` | active | "paused (provider throttling)" | badge-info | pause-circle |
| failed, `failure_kind == "transient"` | active | "paused (temporary error)" | badge-info | pause-circle |
| failed, `failure_kind == "interrupted"` | active | "interrupted" | badge-info | pause-circle |
| failed, `failure_kind == "error"`, None, or unknown | error | "failed" | badge-error | x-circle |

Pending and cancelled keep today's `badge-disabled` "queued" rendering (`dashboard.html:205-206`). Recent Activity copy today reads "synced / failed / syncing …" after the account name; `label` keeps those words for the unchanged cases.

### CSS (the only additions)

In `static/css/style.css`, add `--mfb-stats-dot-warning` next to the other dot tokens in the light block (after line 99: `#d97706`) and the dark block (after line 131: `#fbbf24`), then:

```css
.stats-dot-warning { background: var(--mfb-stats-dot-warning); }   /* next to .stats-dot-error, line 1338 */
.status-warning { background: var(--mfb-badge-neutral-bg); color: var(--mfb-badge-warning-color); border-color: var(--mfb-badge-neutral-border); }  /* next to .status-active, line 1492 — neutral surface, tint only on text, per the "only errors keep a tinted background" rule at 1487-1488 */
```

`badge-warning` already exists (`style.css:291`).

### Agent API (additive to `/api/v1/agent`)

In `routers/agent.py`:

```python
class MailboxStatusOut(BaseModel):
    state: str = Field(description="One of: migrating, suspended, sign_in_needed, first_sync, syncing, paused, stopped, error, initial_sync, initial_stalled, waiting, stale, current. New values may be added.")
    tone: str = Field(description="One of: ok, active, attention, error, muted. New values may be added.")
    label: str                   # informational English text, NOT a stable contract
    action: str | None = Field(description="One of: reconnect, update_password, retry, sync_now, or null. New values may be added.")
    needs_attention: bool
    last_success_at: datetime | None
    resumes_at: datetime | None

class MailboxOut(BaseModel):
    ...existing fields unchanged...
    status: MailboxStatusOut
```

- `detail` is deliberately **not** exposed: it can contain raw `last_error`, which is untrusted IMAP-server / mbsync output (prompt-injection surface, may include paths/usernames). Agents get `state` + `action` instead.
- **Contract rules** (documented in `skills/mailfallback-mcp/SKILL.md`): `state`, `tone` and `action` are **plain strings** (not JSON-Schema enums, so a new value never breaks a generated client or `structuredContent` validation); the known values are listed in each field's `description` and may grow without a `/v2`. Clients treat an unknown `state` by its `tone`. `label` is display text and may change. The values are filled from the StrEnums (`.value`).
- **What an agent does with `action`**: `retry` and `sync_now` → the `sync_now` MCP tool / `POST /api/v1/agent/sync/{account_id}` (requires the `sync:trigger` scope; may still answer 409 when the mailbox is paused, suspended or its owner is migrating — the agent reports that instead of retrying). `reconnect` and `update_password` are human-only: the agent tells the user to reconnect / update the password in the MFB web UI — and, for a group-shared mailbox the user does not own, to ask its owner or an admin (the API cannot tell the agent who can edit). `null` → nothing to do (including a paused, out-of-date mailbox: wait for `resumes_at`).
- `list_mailboxes` (agent.py:196-237) calls `latest_finished_jobs_by_account(db, ids)` once, then `resolve_mailbox_status(account, last_job=jobs.get(account.id))`, and fills `status` with `MailboxStatusOut(...)` built from the dataclass fields listed above.
- MCP `list_mailboxes` (mcp_server.py:337-382) builds the same dict via the same two calls and serialises it with `MailboxStatusOut(...).model_dump(mode="json")`, so datetimes become ISO strings like `last_sync_at` already is. `MailboxListOut` wraps `MailboxOut` and needs no change. The tool stays a sync `def`.

## UX flow (per surface)

Every router below computes statuses once per request: `jobs = latest_finished_jobs_by_account(db, [a.id for a in accounts])`, `statuses = {a.id: resolve_mailbox_status(a, last_job=jobs.get(a.id), now=now) for a in accounts}`, over the account list it already loads with its existing scoping. Templates receive `statuses` (a dict keyed by account id) or a single `status`; **no Jinja globals are added**.

### 1. Dashboard (`routers/ui.py` `dashboard()`)

- `stats.errors` (ui.py:348-350) = count of `statuses` with `tone == error`.
- **Needs Attention** (ui.py:380-417): replace the per-account if/elif with a loop over accounts whose status `needs_attention`. Item dict keys stay exactly what `dashboard.html:151-178` reads — `id`, `name`, `type`, `reason`, `provider`, `href` (href unset as today) — plus one new key `action` (the `NextAction` value or None):
  - sign_in_needed → `type="reauth"`, `reason="Sign-in expired — reconnect needed"` (today's string), `provider=account.provider` (feeds `/auth/{{ item.provider }}/start`), `action="reconnect"` if `can_modify` else None.
  - error → `type="error"`, `reason=(account.last_error or "Sync failed")[:80]` (today's `[:80]` truncation at `ui.py:398-399` is kept — `last_error` can hold a whole mbsync log), `action` = status.action, except `update_password` becomes None when not `can_modify`.
  - stale → `type="stale"`, `reason` = today's stale string (ui.py:414-417, unchanged).
  - initial_stalled → `type="stale"`, `reason` = the status `detail` (see Detail).
  - Initial syncs (first_sync / initial_sync) are **no longer** attention items (the `info` branch at ui.py:401-413 is removed for mailboxes; the template's `info` branch stays for any other producer).
  - Backup and config-backup items (ui.py:419-432) unchanged.
- Template changes in Needs Attention (`dashboard.html:144-190`), and only these:
  - the `reauth` badge becomes `badge badge-warning` (was `badge-error`) — same condition, same amber tone as the chain and table.
  - `reauth` items: the existing Reconnect link renders only when `item.action == 'reconnect'` (a group member who can't reconnect sees the item without the link).
  - for `item.type == 'error'` with no `backup`/`config_backup` flag: when `item.action == 'update_password'`, render `<a class="icon-btn primary" href="/accounts/{{ item.id }}#admin-edit"><i data-lucide="key-round" class="icon-sm"></i> Update password</a>` instead of the Retry button. `static/js/account-bento.js:123` already opens the `#admin-edit` panel from the hash. The link is only built for `can_modify` users; others keep today's Retry button.
- **Chain** (`chain_summary`, ui.py:475-497): replace `mirrors_healthy` / `mirrors_error` with:
  - `mirrors_total` = len(accounts) (unchanged key)
  - `mirrors_failing` = count `tone == error`
  - `mirrors_attention` = count `tone == attention`
  - `mirrors_ok` = count `tone in (ok, active)`
  - `mirrors_muted` = count `tone == muted` (suspended, stopped, waiting)
  - `connected` = count `status.signed_in` (independent of precedence, so a suspended OAuth mailbox without credentials is *not* connected)
  - `source_tone` = `"muted"` if no accounts, elif `"attention"` if `connected < mirrors_total`, else `"ok"`
  - `local_tone` = `"error"` if `mirrors_failing`, elif `"attention"` if `mirrors_attention`, else `"ok"`
  - Template (`dashboard.html:81-104`): `dashboard()` passes `dot_class=TONE_DOT` (from `mailbox_status.py`); the stage dots render `class="stats-dot {{ dot_class[chain_summary.source_tone] }}"` and `... [chain_summary.local_tone]`. Source text: "{N} mailbox(es) connected" when `connected == mirrors_total` (today's wording), else "{connected} of {N} connected". Local text: "{mirrors_failing} of {N} failing" if failing, elif "{mirrors_attention} of {N} need attention", else "{mirrors_ok} of {N} healthy" followed by " · {mirrors_muted} off" when `mirrors_muted > 0`.
- **Recent Activity** (ui.py:434-466): each dict gains `outcome_badge`, `outcome_icon`, `outcome_spin`, `outcome_label` from `resolve_job_outcome(job)` computed in the router where the `SyncJob` is in hand. The template (`dashboard.html:193-217`) uses those four keys instead of branching on `status`. Existing keys (`account_id`, `account_name`, `status`, `time_ago`) stay.

### 2. Accounts list (`partials/accounts_table.html:53-82`)

`accounts_page()` (ui.py:576-603) and `accounts_table_partial()` (606-638) add `statuses` to the context (computed as above). The status cell becomes:

```jinja
{% set ls = live_status.get(account.id) if live_status else none %}
{% set st = statuses[account.id] %}
{% if not account.enabled %}<span class="badge badge-disabled"><i data-lucide="eye-off" class="icon-sm"></i> Hidden</span>{% endif %}
<span class="badge {{ st.badge }}"{% if st.detail and st.state.value in ('paused', 'initial_sync') %} title="{{ st.detail }}"{% endif %}><i data-lucide="{{ st.icon }}" class="icon-sm{% if st.spin %} spin{% endif %}"></i> {{ st.label }}{% if st.state.value in ('first_sync', 'initial_sync') and ls and ls.pct is not none %} {{ ls.pct | int }}%{% endif %}{% if st.state.value == 'paused' and ls and ls.resume_rel %} · resumes {{ ls.resume_rel }}{% endif %}</span>
```

This reproduces today's exact strings for the unchanged cases ("Initial sync 38%", `<span class="badge badge-error"><i data-lucide="alert-circle"` … "error", "Paused · resumes …" with the pause tooltip, "idle", "Suspended", "Migrating"). `title` is only emitted where it exists today (paused, idle initial sync — not the running first-sync chip). When `ls` is None the suffixes are skipped. The Repository column and action gating (lines 88-131) are unchanged. The `HX-Trigger: sync-idle` logic (ui.py:626, 636-637) is unchanged.

### 3. Account detail

- `_compute_hero_state(account, db)` keeps its signature and `(hero_state, snap, last_job)` return. Its own `order_by(SyncJob.completed_at.desc())` query (`ui_accounts.py:191-195`) is **replaced** by `latest_finished_jobs_by_account(db, [account.id]).get(account.id)`; it then calls `resolve_mailbox_status(account, last_job=last_job)` and maps state → hero_state:

  | status.state | hero_state | sync_panel branch |
  |---|---|---|
  | migrating | "migrating" | existing |
  | suspended | "paused" | existing (suspended) |
  | sign_in_needed | "sign-in-needed" | existing |
  | first_sync | "first-sync" | existing |
  | syncing | "syncing" | existing |
  | paused | "sync-paused" | existing |
  | stopped | "stopped" | **new** |
  | error | "error" | existing (`snap` parsed from `last_job.parsed_summary` exactly as today, `ui_accounts.py:225-233`) |
  | initial_stalled, stale | "out-of-date" | **new** |
  | initial_sync, waiting | "empty" if `last_sync_at is None` else "idle" | existing (today's result) |
  | current | "idle" | existing |

- **`can_modify`** = `user.role == UserRole.admin or user in account.owners` — the same rule as `account_service.get_account_for_modify` (`account_service.py:113-121`), computed in the router (a group member sees the account but cannot save edits or reconnect it). Passed to the detail page, the sync panel, and used by the dashboard when building attention items.
- `account_detail` (ui_accounts.py:490-597) gets `status` from the same resolve (call the resolver once more with the returned `last_job`, or return it — implementer's choice, but the same `last_job`) and passes `status` and `dot_class=TONE_DOT` in the context. `account_sync_panel` (ui_accounts.py:129-186) also passes `status` (it already calls `_compute_hero_state`).
- **Two new `sync_panel.html` branches**, inserted before the `{% elif hero_state == "paused" %}` branch (line 293), each a copy of that branch's structure (`hero-header` / `hero-title` / `hero-actions` / `hero-body`):
  - `stopped`: icon `circle-slash`, title "Sync stopped", one action = the "Sync now" button copied verbatim from the sync-paused branch (`sync_panel.html:259-263`, the one whose `hx-on::after-request` shows `d.warning` via `showToast` — so a pause-override warning is never lost), label "Sync now", body "{{ status.detail }}" then, when `account.last_sync_at`, the same "Last sync … · N messages" line as the paused branch.
  - `out-of-date`: icon `alert-triangle`, title "{{ status.label }}", the same "Sync now" button only when `status.action` is `sync_now` (a paused-and-stale mailbox gets no button; the body then adds "Paused — resumes {{ status.resumes_at | time_ago }}" when `status.resumes_at`), body "{{ status.detail }}" then the same last-sync line.
  - Neither new branch polls (the panel polls only for syncing/first-sync today, `ui_accounts.py:129-186`); no `HX-Trigger` change.
  - **Existing error branch** (`sync_panel.html:141-224`): when `status.action == "update_password"` and `can_modify` (below), add `<a class="icon-btn" href="#admin-edit"><i data-lucide="key-round" class="icon-md"></i> Update password</a>` after "Try again" in `hero-actions`. Nothing else in that branch changes.
- **CSS**: add `.hero-stopped` to the `.hero-paused` rule, `.hero-out-of-date` to the `.hero-warning, .hero-sign-in-needed` rule, and the missing `.hero-sync-paused` to the `.hero-syncing, …` rule (a self-recovering pause is blue) (`style.css:1357-1363`). No new colours.
- **Health box** (`account_detail.html:36-84`), only the dots:
  - Local sync: `<span class="stats-dot {{ dot_class[status.tone.value] }}"></span>` (muted → `""` → today's neutral dot). Value text unchanged.
  - Auth: `stats-dot-warning` when `not status.signed_in` or `status.action == "update_password"`, else `stats-dot-ok`. Value text (XOAUTH2/Password) unchanged.
  - Off-site row unchanged.

### 3b. Account detail Sync History (`partials/account_history.html`)

`account_history_partial` (ui_accounts.py:243-256) passes `outcomes = {job.id: resolve_job_outcome(job) for job in jobs}`. The status cell of `job_row` (`account_history.html:5-11`) becomes `{% set o = outcomes[job.id] %}<span class="badge {{ o.badge }}">{{ o.label }}</span>`. To avoid relying on macro context scoping, the macro takes it as an explicit second argument: `job_row(job, outcomes)` at every call site in the file. Other columns unchanged.

### 4. System status strip (`system_status_partial`, admin only, global)

- Load all accounts (admin-only, self-hosted scale: tens of rows; the query and resolve are O(accounts) every 5 s per admin tab — acceptable, noted), plus `latest_finished_jobs_by_account`.
- `syncing_count` = accounts in first_sync/syncing (accounts, not jobs).
- `error_accounts` = list of dicts `{id, name, label, detail}` for `tone == error`.
- `attention_accounts` = same dicts for `state == sign_in_needed` only. Stale / initial_stalled are **not** in the strip (the dashboard covers them; a deliberately rare schedule must not keep the strip on forever).
- The "no activity → empty response" check (ui.py:549-559) treats a non-empty `attention_accounts` as activity (a sign-in needs a human).
- Sync pill (`system_status.html:14-18`): class `status-error` if error_accounts, elif `status-warning` if attention_accounts, elif `status-active` if syncing_count, else `status-neutral`. Text: "{syncing_count} syncing", then " · 1 error" / " · {n} errors", then " · 1 needs sign-in" / " · {n} need sign-in", each only when non-zero.
- Detail list (`system_status.html:82-95`):
  - Header line: when `syncing_count > 0`, "1 mailbox syncing" / "{n} mailboxes syncing" (replaces "N sync job(s) running"); when `syncing_count == 0` and there are no error/attention rows, "No mailbox syncing" (replaces "All accounts idle"); when `syncing_count == 0` and rows exist, no header line (as today).
  - Error rows: keep today's markup exactly (`&#10007;` character, `text-error-state`, link `/accounts/{{ acct.id }}`), with text `{{ acct.name }} — {{ acct.label }}` followed by `: {{ acct.detail[:60] }}` when `detail` is set.
  - Attention rows: same markup with `text-warn-state`, the `&#9888;` character, same link and text format.

## Access control

- The resolver and job outcome are pure; `latest_finished_jobs_by_account` only takes ids the caller already scoped.
- Dashboard, accounts list, detail keep their existing ownership + group scoping; the resolver runs only over accounts those routes loaded.
- System strip: already admin-only; stays global.
- Agent API / MCP `list_mailboxes`: unchanged scoping via `search_service._accessible_account_ids` (owned **or** group-shared, `search_service.py:35-48`), `require_scope("mail:read")` / the MCP `mail:read` check. An admin's token still sees only the admin's own + group-shared mailboxes. No new scope. No raw `last_error` leaves through the API (see Agent API).

## Data & migration

None: no columns, tables, migration, config-export change, env vars or chart change. `TOKEN_REFRESH_FAILED` moves modules (import compatibility kept).

## Background work

None. Worker, scheduler, pause gate, budget ledger and failure classification are only read.

## Failure modes

- Unknown `pause_reason` → generic paused detail, still `active`.
- Unknown `failure_kind` on a failed job → `error` (fail loud).
- Naive datetimes (SQLite) → `_aware`; never raises.
- Long/multi-line `last_error` → detail truncated to 200 (strip to 60).
- No jobs for an account → `last_job=None`; "started" falls back to `initial_sync_total_messages`; "last activity" to `created_at`.
- `created_at` None (legacy) → treat as not stalled.
- Resolver totality: a parametrized unit test runs every combination listed under Verification and asserts it never raises.

## Verification criteria

New `tests/test_mailbox_status.py` (builds rows with `db_session`; asserts `state`, `tone`, `needs_attention`, `action`, `badge`, `icon`):
- `test_migrating_beats_everything`
- `test_suspended_beats_error_and_is_not_attention`
- `test_needs_reauth_is_sign_in_needed_with_reconnect`
- `test_oauth_without_credentials_is_sign_in_needed`
- `test_token_refresh_failure_error_is_sign_in_needed`
- `test_plain_error_is_red_with_retry`
- `test_app_password_credential_error_suggests_update_password`
- `test_pause_beats_error` (error + pause_reason set → paused)
- `test_paused_with_past_until_is_still_paused` (resumes_at in the past)
- `test_pause_reasons_have_tooltips` (parametrize budget/throttle/transient/interrupted: detail == PAUSE_TOOLTIPS value; unknown reason → generic)
- `test_user_stop_is_muted_stopped_not_error`
- `test_running_before_initial_complete_is_first_sync_with_spin`
- `test_running_after_initial_complete_is_syncing`
- `test_idle_initial_started_is_initial_sync_not_attention` (both via `initial_sync_total_messages` and via `last_job`)
- `test_initial_sync_without_progress_for_7_days_is_stalled_attention`
- `test_never_started_is_waiting`
- `test_stale_after_seven_days_needs_attention`
- `test_current_is_ok_idle`
- `test_naive_datetimes_are_treated_as_utc`
- `test_resolver_is_total` (every SyncState × {no pause, pause} × {initial done, not done} × {last_sync None, recent, 10 days old} × {no job, stopped job, failed job}: never raises, state ∈ MailboxState)
- `test_latest_finished_jobs_by_account_ignores_pending_and_running_and_handles_empty_input`
- `test_job_outcome_table` (parametrized over every row of the job table)

Existing tests that must stay green **unchanged**: `tests/test_ui_sync_budget.py` accounts-table tests (`test_accounts_table_initial_sync_chip_with_pct`, `..._paused_chip_with_resume_and_tooltip`, `..._throttle_tooltip`, `..._error_chip_unchanged`), `test_detail_paused_panel_shows_resume_and_override`, `test_dashboard_true_error_still_counts`, `test_first_sync_panel_*`; `tests/test_dashboard_attention.py::test_dashboard_flags_needs_reauth_account` ("Sign-in expired" kept); `tests/test_account_hero_state.py` (all three); `tests/test_chain_hero.py` (asserts "1 mailbox connected", unchanged wording); `tests/test_chain_explainer.py`; `tests/test_agent_api.py` and `tests/test_mcp_tools.py` existing tests incl. `test_every_tool_publishes_an_output_schema`.

Existing test that **changes**:
- `tests/test_ui_sync_budget.py::test_dashboard_excludes_paused_and_shows_initial_info` → renamed `test_dashboard_excludes_paused_and_initial_sync_from_attention`: the paused-exclusion assertions stay; the "initial sync 38%" / "ETA" attention-line assertions are inverted (the initial-sync account must NOT appear under Needs Attention).

New router/template tests:
- `tests/test_dashboard_attention.py`: `test_initial_sync_is_not_an_attention_item`, `test_reauth_badge_is_warning_not_error`, `test_reauth_turns_source_stage_to_attention` ("4 of 5 connected"-style text + `stats-dot-warning`), `test_credential_error_offers_update_password_link` (href `/accounts/{id}#admin-edit`), `test_recent_activity_budget_pause_is_not_failed` ("paused (daily limit)", no `badge-error` for that row), `test_suspended_error_is_not_attention`.
- `tests/test_ui_sync_budget.py::test_accounts_table_reauth_shows_sign_in_needed` (badge-warning + "Sign-in needed"), `::test_accounts_table_oauth_without_credentials_shows_sign_in_needed` (was red "Unauthenticated"), `::test_accounts_table_hidden_account_shows_status_too`, `::test_accounts_table_first_sync_chip_has_no_title`.
- `tests/test_mailbox_status.py` additions: `test_stopped_requires_finished_failed_job_with_signal`, `test_long_paused_account_becomes_stale_attention` (pause_reason set, `last_sync_at` 10 days ago → stale), `test_never_synced_after_7_days_is_initial_stalled_never_synced`, `test_waiting_within_7_days_is_muted`, `test_live_progress_counts_as_started`, `test_blocked_job_does_not_count_as_started_or_reset_stall_clock`, `test_manual_only_mailbox_is_never_out_of_date`, `test_monthly_schedule_uses_twice_the_interval`, `test_invalid_cron_falls_back_to_seven_days`, `test_paused_and_stale_has_no_action_and_resumes_at`, `test_non_terminal_token_refresh_failure_detail`, `test_signed_in_is_false_for_suspended_oauth_without_credentials`, `test_job_outcome_blocked_is_skipped_and_token_failure_is_amber`.
- **Cross-surface consistency — the point of this design.** New `tests/test_status_consistency.py::test_every_surface_agrees`, parametrized over one account per state: current, sign_in_needed (needs_reauth), sign_in_needed (oauth without credentials), error (app password AUTHENTICATIONFAILED), paused (budget), stopped, stale (10 days), initial_stalled, first_sync, syncing, initial_sync, suspended. Plus: hidden (`enabled=False`) current, paused + stale (budget pause, last sync 10 days ago), and a group-shared error account seen by a non-owner member. For each, with the admin session and the account owned by admin (for the group case: the member's session and a `mail:read` token of the member), assert: the accounts table cell contains `status.badge` and `status.label`; the account is in the dashboard Needs Attention list iff `status.needs_attention`; the detail page Local sync dot has `TONE_DOT[status.tone]`; the detail hero container has the class `hero-{mapped hero_state}` from the table in UX flow 3; the system strip lists the account under errors iff `tone == error` and under sign-in iff `state == sign_in_needed`; `GET /api/v1/agent/mailboxes` (bearer `mail:read`) returns the same `state` and `tone`; the dashboard Recent Activity row and the detail Sync History row for the account's last job both carry `resolve_job_outcome(job).badge` (never `badge-error` unless the account tone is `error` or the job failed with `failure_kind == "error"`); the chain Local backup stage text matches the counts rule. For the group-shared member: no Update password link on the dashboard or hero, and no Reconnect link.
- New `tests/test_system_status_strip.py`: `test_strip_counts_accounts_not_jobs`, `test_strip_excludes_paused_from_errors`, `test_strip_shows_reauth_as_needs_sign_in_warning`, `test_strip_ignores_stale`, `test_strip_pluralises_counts`, `test_strip_is_admin_only` (non-admin gets no strip content / redirect as today).
- `tests/test_account_hero_state.py`: `test_health_auth_dot_warns_on_needs_reauth`, `test_health_auth_dot_warns_on_rejected_app_password`, `test_hero_error_offers_update_password_to_owner_only`, `test_user_stop_hero_is_stopped_with_sync_now`, `test_stale_account_hero_is_out_of_date`, `test_hero_ignores_pending_retry_after_error` (a pending job newer than the failed one does not change the hero or `snap`).
- `tests/test_agent_api.py::test_mailboxes_include_status` (bearer `mail:read`: every mailbox has `status` with `state/tone/label/action/needs_attention/last_success_at/resumes_at`; a needs_reauth account → `sign_in_needed` + `reconnect`; no `detail` key; an error account's `last_error` text does not appear anywhere in the response body), `::test_mailbox_status_fields_are_plain_strings_in_openapi` (OpenAPI schema for `MailboxStatusOut.state`/`tone`/`action` has no `enum` and a description listing the values), and every `state` value returned ∈ `MailboxState`.
- `tests/test_mcp_tools.py::test_list_mailboxes_includes_status` (same assertions through the tool).
- Full suite at `-n auto` and `-n 4`; ruff; pre-commit.

Manual (running stack at http://localhost:8000 with seeded demo data — an ad-hoc, uncommitted seed script the elephant already has, run with `docker compose exec -T mailfallback uv run --no-sync python - < seed.py`; it builds the rows directly with the services, `sync_schedule=None` so nothing contacts a real IMAP server. The rows (all admin-owned unless noted, created with `account_service.create_account(..., sync_schedule=None)`; for the stale checks give the stale account `sync_schedule="0 * * * *"` so it isn't manual-only):

  | name | state to reach | fields |
  |---|---|---|
  | Personal Gmail | current | provider google, oauth2, `last_sync_at`=now-7min, `initial_sync_completed_at`=now-40d, 6 completed jobs; `folder_stats` JSON items use keys `name`, `messages`, `unread`, `size_bytes` |
  | Work Outlook | sign_in_needed | provider microsoft, oauth2, `sync_state=needs_reauth`, `last_sync_at`=now-3h, initial completed |
  | Family Fastmail | paused | provider other, app_password, `pause_reason="budget"`, `sync_paused_until`=now+5h, one failed job `failure_kind="budget_paused"` |
  | Old ISP mailbox | error → Update password | provider other, app_password, `sync_state=error`, `last_error="AUTHENTICATIONFAILED Invalid credentials"`, initial completed, one failed job `failure_kind="error"` (started_at set) |
  | New Gmail (initial sync) | first_sync | provider google, oauth2, `sync_state=syncing`, `initial_sync_total_messages`=96000, one running job; also owned by giulia |
  | Stale ISP | stale | provider other, app_password, `sync_schedule="0 * * * *"`, `last_sync_at`=now-10d, initial completed |

  User giulia (role user) owns only "New Gmail (initial sync)". The admin therefore sees six mailboxes: Source "5 of 6 connected", Local backup "1 of 6 failing", strip "1 syncing · 1 error · 1 needs sign-in".

  Checks: healthy Gmail, Outlook needs_reauth, Fastmail budget-paused, ISP mailbox error "AUTHENTICATIONFAILED", Gmail in initial sync, non-admin "giulia"; light + dark + 390 px):
- Dashboard: Work Outlook is an amber reauth item; Old ISP offers "Update password" that opens the edit panel; Fastmail and the initial Gmail are not attention items; Source reads "5 of 6 connected" with an amber dot; Local backup "1 of 6 failing"; Stale ISP is an "Out of date" attention item; Recent Activity shows the Fastmail budget job as info "paused (daily limit)".
- Accounts list: Outlook "Sign-in needed" amber; Fastmail "Paused · resumes …" info; ISP "error" red; initial Gmail "Initial sync N%".
- Outlook detail: Auth dot amber; hero "sign-in-needed" as today.
- System strip (admin): "1 syncing · 1 error · 1 needs sign-in", red.
- As giulia: only her mailbox; no attention item for her initial sync.
- `curl -H "Authorization: Bearer mfb_…" http://localhost:8000/api/v1/agent/mailboxes`: `status` present, no `detail`.

## Docs

- `skills/mailfallback-mcp/SKILL.md`: add a new `## Mailbox status` section right after the paragraph that starts "Start with `list_mailboxes`" (line 91), documenting the `status` object: the field list, the full value lists of `state`, `tone`, `action`, the contract rules (plain strings whose known values may grow; unknown state → use `tone`; `label` is display text; `needs_attention` is the thing to surface to the user), and the action → tool mapping from the Agent API section (`retry`/`sync_now` → `sync_now` tool, needs `sync:trigger`, may 409; `reconnect`/`update_password` → tell the user).
- `CLAUDE.md`, Key Patterns, new bullet: "**Mailbox status**: `services/mailbox_status.py` `resolve_mailbox_status()` is the single source of a mailbox's health (state, tone, label, action). Health/status *display* (chips, dots, attention items, hero state, strip counts, agent `status`) never derives from `sync_state`/pause fields directly — action gating (Stop/Sync now buttons, `HX-Trigger: sync-idle`) still reads `sync_state`; only `error` tone is red, `attention` is amber, self-recovering states are `active`."

## Out-of-scope follow-ups

- Align scheduler stale-notify (excludes hidden and paused) and the notification emitter with the resolver.
- Worker: clear pause columns on Timeout/Exception error paths (`sync_worker.py:1285-1314`).
- REST `/api/accounts` + `/metrics` per-account state gauge from the resolver.
- Owner-level migration in the resolver.
- Off-site health unification (Repository / Snapshot stages, `_compute_health`).
- LEXICON violations "Backup failed" in `sync_panel.html:146,198`; Title-case / lowercase chip labels ("idle", "error", "syncing") — clarify pass.
- Remove the dead `/accounts/{id}/sync-status` route + `partials/sync_status.html`.

## v5 amendments (post-cap override — these SUPERSEDE any conflicting text above)

The design check hit the 3-revision cap; the user chose "override and proceed" with the remaining 18 gaps folded in here. Where this section and earlier text disagree, this section wins.

**A1 — Stopped vs error vs long unsynced.** Define `stopped_job` = `last_job` is not None AND `last_job.status == failed` AND `last_job.signal` is set AND `last_job.failure_kind is None`. Row 6 = `sync_state == error` AND `stopped_job` AND NOT long unsynced → stopped. Row 7 = `sync_state == error` AND no pause AND NOT `stopped_job` → error. A stopped mailbox that is long unsynced therefore falls to rows 8/9 (amber), never red.

**A2 — One staleness threshold.** "Old" uses the schedule-aware stale threshold only (`max(STALE_AFTER, 2 × cron interval)`); the line "Old = `t < now - STALE_AFTER`" is void. `STALE_AFTER` is the *minimum* threshold (its comment says so; the scheduler's stale-notify still uses a fixed 7 days — follow-up). Copy that named "7 days" becomes dynamic: initial_stalled detail "The initial sync has not progressed for over {T} days." (T = threshold in whole days); stale detail "Last sync was {N} days ago." (N ≥ T). The dashboard stale attention item's `reason` becomes `status.detail` (replacing "No sync in 7+ days"; update any test that asserts the old string).

**A3 — Behaviour change (declared): manual-only mailboxes** (`sync_schedule` None/empty) are never flagged out of date or never-synced. Today the dashboard flags any mailbox unsynced for 7+ days.

**A4 — One entry point for every surface.** Add to `mailbox_status.py`:
```python
def has_live_progress(account: Account) -> bool:
    # lazy import inside the function to keep the module light:
    from mailfallback.routers.ui import account_live_status  # existing helper, routers/ui.py:182-212
    return account_live_status(account)["pct"] is not None

def resolve_many(db: Session, accounts: list[Account], *, now: datetime | None = None) -> dict[str, MailboxStatus]:
    jobs = latest_finished_jobs_by_account(db, [a.id for a in accounts])
    now = now or datetime.now(UTC)
    return {a.id: resolve_mailbox_status(a, last_job=jobs.get(a.id), live_progress=has_live_progress(a), now=now) for a in accounts}

def resolve_one(db: Session, account: Account, *, now: datetime | None = None) -> MailboxStatus:
    return resolve_many(db, [account], now=now)[account.id]
```
**Every** surface (dashboard, accounts page + partial, detail, sync panel / `_compute_hero_state`, system strip, agent `list_mailboxes`, MCP `list_mailboxes`) calls `resolve_many` / `resolve_one` — never `resolve_mailbox_status` directly — so `live_progress` and `last_job` are identical everywhere. (If importing `routers.ui` from the service creates an import cycle at module load, move `account_live_status` + `PAUSE_TOOLTIPS` consumers as needed, but keep one implementation.)

**A5 — Hero "Update password" must actually open the edit panel.** In `static/js/account-bento.js` (hash handling at lines ~120-134), refactor the load-time hash logic into a function and also call it on `window.addEventListener("hashchange", …)`. The hero link is `<a class="icon-btn" href="#admin-edit">`. Clicking it on the detail page changes the hash, which now opens the panel. This is the only JS change.

**A6 — Hero Reconnect gated.** In the existing sign-in-needed branch of `sync_panel.html` (~line 236), wrap the Reconnect link in `{% if can_modify %}`; non-modifiers see the copy without the link.

**A7 — Paused + out of date.** The UI trigger overrides pauses with a warning (`routers/sync.py:127-138`); only the agent refuses. So: the out-of-date hero branch **always** shows the Sync now button (the sync-paused branch's variant with the `d.warning` toast), plus "Paused — resumes {{ status.resumes_at | time_until }}" when `status.resumes_at`. `status.action` for rows 8/9 is None when `pause_reason` is set **or** `sync_paused_until` is in the future (both cases the agent trigger refuses, `routers/agent.py:464-471`); otherwise `sync_now`. `action` is agent-facing; UI buttons follow the rules in this doc, not `action`, except where the doc says so.

**A8 — Dashboard stale items.** The `stale` attention badge (`dashboard.html:156-158`) becomes `badge badge-warning` with the clock icon (was `badge-syncing`). Stale items get a "Sync now" button identical to the Retry button's markup (hx-post `/api/sync/{{ item.id }}`) labelled "Sync now", rendered when `item.action == 'sync_now'` (the router sets `action` from status, None when paused).

**A9 — "Sync blocked" jobs.** `resolve_job_outcome` maps to muted "skipped" only when `job.log` is exactly one of the four guard messages: "Sync blocked: account is suspended", "Sync blocked: account not authenticated", "Sync blocked: account migration in progress", "Sync blocked: user migration in progress" (`sync_worker.py:799-827`). Any other failed job with `failure_kind is None` and no signal (e.g. the host re-validation failure at `sync_worker.py:835-844`) → red "failed".

**A10/A11 — Consistency test details.** For the group-shared member case, skip the system-strip assertions (strip is admin-only) and assert the strip in an admin session instead. "The account's job" in the Recent Activity / Sync History assertions is `latest_finished_jobs_by_account(...)[account.id]`; assert the badge on *that* job's row (Recent Activity renders it only if it is among the 5 most recent — fixtures create at most that job plus nothing newer). For fixtures with no job, skip the job-badge assertions.

**A12 — Seed credentials.** Every seeded OAuth account gets a non-empty `credentials` value (any JSON string, e.g. `'{"refresh_token":"demo"}'`) so `is_authenticated` is True; Work Outlook is sign-in-needed only via `sync_state=needs_reauth`. Seed expectation stays "5 of 6 connected".

**A13 — No N+1 for `can_modify`.** Routers compute `owned_ids = {row.account_id for row in db.query(account_owners.c.account_id).filter(account_owners.c.user_id == user.id)}` once per request; `can_modify(a) = user.role == UserRole.admin or a.id in owned_ids`.

**A14 — Local backup muted suffixes.** Replace " · {mirrors_muted} off" with separate suffixes, each only when non-zero: " · {n} suspended", " · {n} stopped", " · {n} not started" (waiting). `mirrors_muted` is dropped.

**A15 — `time_until` filter.** Register the existing `_time_until` (`routers/ui.py:116`) as Jinja filter `time_until` next to the other filters (`ui.py:158-163`).
