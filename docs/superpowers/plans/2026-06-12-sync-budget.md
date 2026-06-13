# Sync Budget Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Throttle-aware first full sync with provider bandwidth budget, progress %/ETA, and crash-safe resumability (spec: `docs/superpowers/specs/2026-06-12-sync-budget-design.md`).

**Architecture:** Failure classification + per-account daily byte ledger enforced by a sampler thread inside the sync worker; pause/resume columns gate the scheduler; an upstream STATUS pass provides the progress denominator; lifespan sweep recovers zombie jobs. UI surfaces a dedicated non-error "Initial sync" state with %/ETA.

**Tech Stack:** existing — FastAPI, SQLAlchemy/Alembic (PostgreSQL prod, SQLite tests), mbsync subprocess (isync 1.5.1), Jinja2+Alpine, pytest with mocked subprocess.

**Branch:** `feat/sync-budget` (stacked on feat/restore-attachments-view; migration 021 follows 020). Suite baseline: 890 green.

---

### Task 1: Migration 021 + model columns (atomic — alembic drift hook)

**Files:** `src/mailfallback/models.py`, `alembic/versions/021_sync_budget.py`, `tests/test_migrations.py` (or the existing migration-test pattern)

- Accounts: `traffic_date: Date | None`, `bytes_synced_today: BigInteger NOT NULL server_default '0'`, `daily_sync_budget_mb: Integer | None`, `sync_paused_until: DateTime(tz) | None`, `pause_reason: String | None`, `initial_sync_completed_at: DateTime(tz) | None`, `initial_sync_total_messages: Integer | None`.
- SyncJob: `failure_kind: String | None` (values: throttled|budget_paused|transient|interrupted|error — plain string, NOT enum).
- Migration backfill: `UPDATE accounts SET initial_sync_completed_at = last_sync_at WHERE last_sync_at IS NOT NULL` (existing healthy accounts skip the initial-sync regime).
- Model + migration in ONE commit (pre-commit hook enforces).

### Task 2: Failure classifier (pure module)

**Files:** `src/mailfallback/services/sync_failures.py`, `tests/test_sync_failures.py`

```python
TRANSIENT_SIGNATURES = ("Connection reset", "unexpected EOF", "Broken pipe", "timed out", "Connection timed out")
THROTTLE_SIGNATURES = {
    "google": ("[OVERQUOTA]", "Too many simultaneous connections"),
    "microsoft": ("Request is throttled", "[THROTTLED]", "server busy"),
    "other": (),
}
GENERIC_THROTTLE = ("[OVERQUOTA]", "[THROTTLED]")

def classify_failure(log_tail: str, provider: str) -> str | None:
    """throttled | transient | None (= real error). Case-sensitive on bracketed
    response codes, case-insensitive on prose."""
```
- First match wins: throttle (provider + generic) before transient.
- TDD with the REAL production line: `IMAP error: unexpected BYE response: [OVERQUOTA] Account exceeded command or bandwidth limits.` → `throttled` for provider google. "unexpected EOF" → transient. Unknown junk → None.

### Task 3: Budget resolution + ETA math (pure module)

**Files:** `src/mailfallback/services/sync_budget.py`, `tests/test_sync_budget.py`

```python
PROVIDER_DAILY_BUDGET_MB = {"google": 2000}  # prudent under Gmail's ~2.5GB/day

def daily_budget_bytes(account) -> int | None:
    # account.daily_sync_budget_mb: None → provider default; 0 → unlimited (None)

def compute_progress(done_msgs, total_msgs) -> float | None  # 0..100, None if no total

def estimate_eta(done_msgs, total_msgs, done_bytes, bytes_today, budget_bytes, run_rate_msgs_per_s) -> dict
    # {"seconds": int|None, "days": float|None, "label": "≈ 3g"|"≈ 2h"|None}
    # remaining_bytes = (total-done) * avg_bytes_per_msg(done_bytes/done_msgs)
    # if budget: days = remaining_bytes / budget (counting today's remaining headroom first)
    # else: rate-based from run_rate; degrade gracefully on missing inputs (None)
```
- Resume helpers: `next_budget_resume(now) -> datetime` (next UTC midnight + jitter 0–30min), `next_throttle_resume(now, attempt) -> datetime` (4h × 2^(attempt-1), cap 24h, jitter), `next_transient_resume(now, attempt)` (2min × 2^(attempt-1), cap 30min).
- TDD all formulas including degenerate inputs (zero done, no total, no budget).

### Task 4: Byte meter / sampler thread + ledger

**Files:** `src/mailfallback/services/sync_worker.py`, `tests/test_sync_worker.py`

- `_sample_maildir(path, since_ts) -> (total_msgs, total_bytes, run_msgs, run_bytes)`: os.walk over cur/new dirs; cumulative totals + delta (mtime ≥ since_ts). Comment pinning the `CopyArrivalDate no` dependency.
- Sampler thread started by `execute_sync_job` alongside the subprocess: every ~30s sample, flush to DB (OWN session — thread-local), update: account ledger (`traffic_date`/`bytes_synced_today` with UTC rollover reset), live progress dict (module-level, like `_running_procs`: `_live_progress[job_id] = {...msgs, bytes, pct, eta}`), and **budget check**: `bytes_synced_today ≥ budget` → call existing `stop_sync_job(job_id)` and record `_budget_stops[job_id] = True`.
- Ledger arithmetic: bytes added per tick = max(0, run_bytes − last_flushed_run_bytes) (monotonic within run, restart-proof across runs because it re-derives from disk each tick within the run window only).
- Thread joins/cleans up in the worker's finally. Tests: fake maildir with growing files + fake clock; assert ledger rows, budget stop invoked, rollover reset.

### Task 5: Worker integration (classification, pause, priority pass, totals)

**Files:** `src/mailfallback/services/sync_worker.py`, `src/mailfallback/services/mbsync_config.py` (only if needed), `tests/test_sync_worker.py`

- On job start, if `initial_sync_completed_at IS NULL`:
  - Upstream STATUS pass via `imap_check.connect_imap` (+ XOAUTH2 token already refreshed by the worker): LIST folders, apply the account's pattern exclusions (export a small matcher from mbsync_config patterns string — at minimum honor the `!"..."` exclusions), `STATUS (MESSAGES)` each, sum → `accounts.initial_sync_total_messages`. Failure of this pass is NON-FATAL (log, proceed without total).
  - Inject `pipeline_depth=1` into the generated config (generator already supports the knob via extra).
  - Priority pass: run `mbsync <channel>:INBOX` first, then `mbsync <channel>` — same job row, sequential subprocesses, sampler spans both; budget/stop applies to both.
- On non-zero exit: `classify_failure(log_tail, provider)` →
  - `throttled` → `failure_kind=throttled`, `pause_reason='throttle'`, `sync_paused_until=next_throttle_resume(...)`, account `sync_state=idle` (NOT error), last_error cleared.
  - `transient` → analogous with short backoff.
  - budget stop recorded by sampler → `failure_kind=budget_paused`, `pause_reason='budget'`, `sync_paused_until=next_budget_resume(...)`, state idle.
  - None → today's behavior (error state).
- On clean exit 0 (full pass, not the INBOX-only invocation, no budget stop): set `initial_sync_completed_at=now()` if NULL.
- Attempt counter for backoff: count today's jobs with same failure_kind (query) — no new column.
- Tests: mocked subprocess (existing pattern) for each branch; assert two invocations while initial incomplete and ONE after completion; STATUS pass mocked via imap_check monkeypatch.

### Task 6: Crash recovery sweep (lifespan)

**Files:** `src/mailfallback/app.py`, `src/mailfallback/services/sync_worker.py` (sweep helper), `tests/test_app_lifespan.py` (follow StoreMigration-recovery test pattern)

- `recover_zombie_sync_jobs(db)`: jobs `status='running'` whose job_id not in `_running_procs` (fresh boot → always true) → `status=failed`, `failure_kind=interrupted`, `completed_at=now()`; account: if `sync_state==syncing` → idle; if initial sync incomplete AND budget headroom (ledger vs budget) → leave schedulable now (no pause), else respect existing pause.
- Called in lifespan next to the migration-resume hook.
- Tests: seed a running job + syncing account, run sweep, assert closure + account schedulable.

### Task 7: Scheduler gating + manual override

**Files:** `src/mailfallback/services/scheduler.py`, `src/mailfallback/routers/sync.py`, `tests/test_scheduler.py`

- Periodic tick skips accounts with `sync_paused_until > now()`; when the pause expires, normal scheduling resumes it naturally (cron) — ALSO: accounts whose pause expired but whose cron won't fire soon and initial sync incomplete → enqueue at pause expiry (the scheduler tick checks `sync_paused_until <= now() AND initial_sync_completed_at IS NULL` → enqueue immediately, once: clear sync_paused_until/pause_reason on enqueue).
- Manual `POST /api/sync/...` trigger: allowed on paused accounts; clears the pause; response includes a warning string when overriding a budget pause ("may exhaust the provider's daily quota").
- Tests: paused skipped, expiry enqueues once, manual override clears + warns.

### Task 8: API + UI (state chip, progress panel, dashboard exclusions, budget field)

**Files:** `src/mailfallback/routers/sync.py` (live progress payload), `src/mailfallback/routers/ui.py` / `ui_accounts.py`, `templates/accounts.html`, `templates/account_detail.html`, `templates/dashboard.html` (or partials), `static/css/style.css`, `tests/test_ui_*.py`

- Live sync status payload (existing polling endpoint) gains: `pct`, `done_msgs`, `total_msgs`, `bytes_today`, `budget_bytes`, `eta_label`, `paused_until`, `pause_reason`, `initial_sync` (bool).
- Account detail: "Initial sync" panel with progress bar (CSS class, no inline styles), `38% (46.2k/121k msg) · ETA ≈ 3g · 1.9/2.0 GB oggi · riprende 02:00`; budget override field in the edit form (`daily_sync_budget_mb`, placeholder shows provider default, 0 = unlimited; add to the `_UPDATABLE_*_FIELDS` allowlist).
- Accounts table + dashboard: chip `Initial sync 38%` (info-tone, NOT error); paused states show `Paused · riprende 02:00`.
- Dashboard "Needs Attention" + Errors stat: exclude accounts whose state is a self-recovering pause (pause_reason set / failure_kind != error on the last job).
- English UI copy (the app is English: "resumes 02:00", "Initial sync", "Paused (daily budget)").
- Tests: template/route assertions per existing UI-test patterns.

### Task 9: Docs + CLAUDE.md touch-up

**Files:** `CLAUDE.md` (Key Patterns or Data Model: one line on sync budget/initial-sync columns), `docs/` if a user-facing doc exists for sync.

- Brief; no new doc site pages in this cycle.

---

**Per-task discipline:** TDD red-first where practical; `uv run pytest tests/ -n auto` green + `ruff check` + `ruff format --check` before each commit; one commit per task (model+migration atomic in Task 1); no `--no-verify`.

**Verification at the end (controller):** rebuild container, live-check on "Main gMail": classification of the real OVERQUOTA log → paused-not-error; chip + %/ETA on /accounts and detail; ledger rows advancing during a real run; manual-sync override warning. Then PR #180 (base: feat/restore-attachments-view).
