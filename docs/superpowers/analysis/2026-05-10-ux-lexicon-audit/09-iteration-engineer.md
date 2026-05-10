# Engineer red team on the implementability

## TL;DR

- **4 weeks is optimistic but defensible** for one dev — security fixes 1-2 days, lexicon rename 4-5 days, chain widget the iceberg at 5-7 days, IA reorder 3-4 days. Realistic: 4.5-5 weeks; 6 with review cycles.
- **Riskiest wave: Wave 4.** The chain widget needs to coexist with the existing `system_status.html` strip without "wall of status bars" — it's a new endpoint, partial, three queries, poll cadence, and a per-user dismissal preference (no structured home today; `user.preferences` JSONB is the place).
- **Unblocker: ship the binding `LEXICON.md` table Day 1** with a CI check stub. Every later wave becomes find/replace. Without it, Wave 2 rebikesheds the mapping at every PR.

## Wave-by-wave reality check

### Wave 1 — Honesty + foundations (estimate: 4 days, audit said 1 week — realistic)

| Task | Files | Diff | Tests |
|---|---|---|---|
| "Backup configured" badge → "Off-site policy set" + last-success state | `partials/account_backup.html`, `routers/ui_backup.py` (status query) | ~30 lines | 1 new test, 2 updates |
| `insecure_tls` toggle relabel + warning banner | `partials/account_backup.html` form section, `static/css/style.css` (warning class likely exists) | ~15 lines | 0 (cosmetic) |
| "Snapshot restored as 'Backup X'" flash rewrite | `routers/ui_backup.py:411`, `routers/ui_backup.py:388` (account name template) | ~10 lines | `test_restore_*` flash assertions: 2-3 updates |
| SECRET_KEY + Postgres DR doc | `docs/disaster-recovery.md` (new) + link from `admin_backup.html` | ~150 lines new doc | 0 |
| `LEXICON.md` at repo root | new file, ~80 lines | — | 0 |
| CI lint hook | `.github/workflows/*.yml` or pre-commit + script in `scripts/` | new file ~40 lines | 1 unit test for the script itself |

Gotcha: "last successful back-up: X ago" needs a column that may not exist. `models.py:331` has `last_status` and `last_run_at` but no separate `last_successful_run_at`. If missing, +Alembic migration → 5 days.

### Wave 2 — Lexicon rename (5-6 days, audit said 2 weeks — slack is fine)

Vocab inventory said 50-80 strings; grep shows in `templates/`: 41 `backup`, 61 `sync`, 60 `store`, 10 `destination`. After deduping layout strings and excluding code-comparison sites (e.g. `account.sync_state.value == 'syncing'` — enum value, **must not change**), user-facing rename surface is ~120 strings in 14 templates plus ~25 in 3 routers (flashes, audit action strings).

Constraint not in the recommendation: **enum string values are hardcoded in templates**. `sync_state.value == 'syncing'` appears in 4 templates. Reviewers will repeatedly try to "fix" these. Document a "DO NOT TOUCH" section in `LEXICON.md`.

Gotcha #1: `audit_logs.action` strings (`"backup_destination.create"`) render in the admin audit UI. Renaming breaks historical readability. Keep action strings stable; add a display mapper.

Gotcha #2: no i18n infrastructure exists. The Italian column is aspirational. Wave 2 ships **English only**; make this explicit.

### Wave 3 — IA promotion (4 days)

`account_detail.html` reorder is straightforward — `<details>` blocks at line 309+ are already discrete sections. The two-pill `accounts.html` status requires preloading `AccountBackup.last_status` per row; today's query likely doesn't. Add `selectinload(Account.backup_configs)` in `account_service.py` to avoid N+1.

The `/restore` entry chooser is ~80 LOC. "Sposta posta tra caselle" reuses `restore.html`; "Recupera da snapshot" needs a cross-account snapshot picker that doesn't exist today (snapshots are per-account, see `partials/backup_snapshots.html`). Likely a new `/recover` route — more code than implied.

### Wave 4 — Chain widget + empty states (6-7 days, audit said 1 week — TIGHT)

The underestimated wave. See "chain-widget HTMX architecture" below. Empty-dashboard redesign and admin status columns are 1 day each; the sticky widget is 4-5 days.

## The qualifier-discipline lint check

**Where:** pre-commit (fast feedback) + GitHub Actions (enforcement; source of truth).

**Sketch** — bash wrapping `git diff`:

```bash
# scripts/check_backup_qualifier.sh
#!/usr/bin/env bash
set -euo pipefail
RANGE="${1:-HEAD~1..HEAD}"
ALLOWED='(local|off-site|offsite|locale|della casella|configuration|destination|policy|deposito|repository|snapshot|completed|failed|started|now|history|profile)'
# Find added lines in templates/ and routers/ containing bare "Backup"
git diff "$RANGE" -- 'src/mailfallback/templates/**/*.html' 'src/mailfallback/routers/**/*.py' \
  | grep -E '^\+' \
  | grep -v '^+++' \
  | grep -iE '\bbackup\b' \
  | grep -ivE "\bbackup[[:space:]]+${ALLOWED}\b" \
  | grep -ivE "${ALLOWED}[[:space:]]+backup\b" \
  && { echo "FAIL: bare 'backup' introduced — see LEXICON.md"; exit 1; } \
  || exit 0
```

**Whitelist:** skip `docs/superpowers/`, `tests/`, `*.md` (analyses use bare "backup"). Allow `# lexicon-allow-bare-backup` escape comment. Audit action strings are code identifiers — restrict scope to `.html` templates and `flash_*` assignments in routers. Skip everything else.

**Cost:** 4h regex + tuning, 2h wiring pre-commit + CI, 2h fixing surfaced violations = 1 day. Audit estimate correct.

## The chain-widget HTMX architecture

Today's `status-strip` (`system_status.html`) already shows Dovecot, FTS, sync, restore, backup counts — 5 dots, 5s poll, admin-only. The chain widget is conceptually a sibling (data-flow narrative vs. system health).

**Don't build a new endpoint — extend the existing one.** Add `chain_summary` to `/partials/system-status`. One poll, one query, no double-bar. Render the chain below the existing strip with a divider, or fold it in.

**Feeds:**
- Mirror: `Account.sync_state` aggregation — already in the query.
- Repository: `count(BackupDestination)` + `count(AccountBackup)` — one query.
- Snapshots: **the cost.** Snapshots live in restic, not the DB. Restic-shelling on every 5s poll is unacceptable. Solution: **cache `last_snapshot_count` + `last_snapshot_at` on `AccountBackup`**, updated by `backup_worker` after each run. Small Alembic migration — but **critical**; without it the widget lies or melts disk.

**Cadence:** 5s, matching the existing strip.

**Per-user dismissal:** `user.preferences` JSONB (column exists per migration 005): `{"chain_widget_dismissed": true}`. Default-on (`created_at < 30 days`) is a Jinja conditional in `base.html`.

## The four security-adjacent fixes

| # | Fix | Files | Diff | Test impact |
|---|---|---|---|---|
| 1 | Badge rewrite | `partials/account_backup.html`, `ui_backup.py` (status helper) | ~30 LOC + possible new `last_successful_run_at` column = +Alembic | 1 new test, 2 updates |
| 2 | TLS toggle relabel + banner | `partials/account_backup.html` (form section), `static/css/style.css` | ~20 LOC | 0 |
| 3 | Restore flash + suspended messaging | `ui_backup.py:411`, `ui_backup.py:388` (auto-name), `partials/restore_history.html` | ~15 LOC | `test_restore_ui.py`: 2-3 assertion updates |
| 4 | DR doc | new `docs/disaster-recovery.md` + link from `admin_backup.html` | ~150 LOC doc + 1 link | 0 |

**Hidden hardness:** Fix #1 likely needs Alembic (no `last_successful_run_at`). 30-minute migration but a DB change in the "honesty" wave. Fix #3's auto-name in `ui_backup.py:388` is one line, but Italian users get English until i18n exists — flag in release notes.

## DB rename deferral — is it really safe to defer?

Mostly yes; three leak points:

1. **Audit action strings** (`"backup_destination.create"` etc., `ui_backup.py:115-405`) render in `admin_audit.html` and will read as legacy. Add a 5-line display map in the audit template.
2. **Error messages:** `restic_service.py` uses the type name only in code paths; user-facing errors say "destination". Safe.
3. **Form field `name="destination_id"`:** form key, not a label. Safe.

Deferral is safe **if** the audit-display map ships in Wave 2.

## Test impact

Today: 390 tests.

- **Lexicon rename:** ~25-30 assertion updates. `grep -lE '"Backup |"Sync |Backed up|Sync now|Mail Store|backup destination' tests/` finds 4 files; `test_ui.py` and `test_audit_ui.py` add flash-message assertions.
- **Chain widget:** ~5 new tests — endpoint extension, dismissal persistence, snapshot-cache freshness, default-on logic, login-page suppression.
- **Security fixes:** ~5-7 new tests — last-successful helper, TLS warning render, restore flash text, DR doc link.

Net: **+15 tests, ~30 updates.** Parallel runtime stays under 10s. No fixture surgery (in-memory SQLite handles new columns).

## Risks the recommendation underestimates

1. **Snapshot caching is mandatory.** The chain widget cannot live-query restic every 5s; without `last_snapshot_count` it lies or kills disk.
2. **`system_status.html` overlap.** Stacking another bar creates visual "double status bar" syndrome. Either replace the strip or fold the chain into it. Recommendation assumes pure-add.
3. **Audit action strings are user-visible**, not DB-only.
4. **`account.suspended` semantics not verified.** Fix #3 ("recovered mailbox is suspended") assumes the Lua userdb honors the suspended flag. Verify before copy-only changes.
5. **No i18n exists.** Bilingual table can't ship — English only. Italian is a separate epic.
6. **Mid-flight conflict.** Commit `4934ad3` (edit backup destination, inline form) is open work touching the same partial Wave 1 will edit. Coordinate.

## Risks the recommendation overestimates

1. **"Italian/English divergence makes docs harder"** — no Italian docs exist. Non-issue.
2. **"What changed" toast** is overkill for 1-3 admins per install. A changelog line is enough.
3. **"Team picks Option C halfway"** — there is no team. Andrea decides Monday.

## My counter-proposal on sequencing

Keep the order, split Wave 1 and Wave 2:

- **Wave 1a (Day 1-2):** `LEXICON.md` + CI lint + audit display map. Foundation first.
- **Wave 1b (Day 3-5):** the four security fixes, parallelizable.
- **Wave 2 (Week 2):** English-only rename. IT column stays in `LEXICON.md` as i18n target.
- **Wave 2.5 (1 day):** one Alembic migration for `last_snapshot_count`, `last_snapshot_at`, `last_successful_run_at` — batched.
- **Wave 3 (Week 3):** IA reorder + two-pill accounts list + new `/recover` route.
- **Wave 4 (Week 4):** chain widget — extend `system_status.html`, don't parallel it. Empty-state redesigns fill the gaps.

The 1a/1b split protects the foundation from security-fix slippage. Batching the Alembic at Wave 2.5 gives Wave 4 real data without mid-wave schema scramble.
