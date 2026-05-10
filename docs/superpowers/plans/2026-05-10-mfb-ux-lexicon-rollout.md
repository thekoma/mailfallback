# MFB UX & Lexicon Rollout Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Each Wave is a separate Phase below; pick up wherever the checkboxes stop.

**Goal:** Rename MFB's user-facing lexicon to a single coherent system, promote off-site backup to a first-class concept, and surface the data-flow chain (Source → Local backup → Repository → Snapshot) to the user. No DB rename. No i18n infrastructure. English-only labels for now.

**Source of truth:** `docs/superpowers/analysis/2026-05-10-ux-lexicon-audit/08-recommendation.md`. The "AMENDMENTS — v1.1" section at the bottom overrides any conflicting v1.0 statement. Supplementary: `lexicon/LEXICON-draft.md`, `09-iteration-synthesis.md`, `mockups/06-mockups.md`, `QUICK-WINS.md`.

**Tech stack:** Python 3.12+, FastAPI, SQLAlchemy, Alembic, APScheduler, Jinja2, HTMX, Pico CSS, Lucide icons. PostgreSQL (production), in-memory SQLite (tests).

**Estimated effort:** 4.5–5 weeks single developer, split across 7 waves + final verification.

**Coordination note:** Recent commit `4934ad3` (edit backup destination — inline expandable form) touches `partials/account_backup.html`, the same file Wave 1b modifies. Either let it land first or coordinate via PR.

---

## Phase 0 — Allowed APIs & verified facts (READ FIRST every wave)

These facts were verified by Phase-0 documentation discovery and must inform every wave.

### Models — current state

- **`AccountBackup`** (`src/mailfallback/models.py:324-347`) has: `id, account_id, destination_id, enabled, schedule, retention_preset, keep_daily, keep_weekly, keep_monthly, last_backup_at, last_status, last_error, created_at`. **MISSING (added in Wave 2.5):** `last_run_at, last_successful_run_at, last_snapshot_count, last_snapshot_at`.
- **`BackupDestination`** (`src/mailfallback/models.py:308-322`) has: `id, name, backend_type, s3_endpoint, s3_bucket, s3_access_key, s3_secret_key, local_path, restic_password, insecure_tls, created_at`. `BackendType` enum: `s3, local`.
- **`Account.suspended`** (`src/mailfallback/models.py:198`): `Boolean, nullable=False, default=False`.
- **`User.preferences`** (`src/mailfallback/models.py:138`): `JSON, nullable=False, default=dict, server_default="{}"`. Currently unused.
- **`AuditLog.action`** is plain text (e.g., `"backup_destination.create"`). Action labels mapper exists at `src/mailfallback/services/audit_service.py:5-40`.

### Routers / endpoints

- **Dovecot userdb endpoint** at `src/mailfallback/routers/dovecot.py:24-47`. Filters on `a.enabled and a.store.enabled`. **DOES NOT filter on `a.suspended`** — this is a Wave 1.5 bug.
- **Scheduler `_run_scheduled_sync`** correctly filters suspended (`scheduler.py:24, 51`). Behaviour is inconsistent across surfaces.
- **`/admin/audit` rendering** uses `get_action_label()` filter — so the audit-action display mapper is **already in place** as a Jinja filter. Wave 2 just needs to extend its dictionary.

### Templates — current state (post commit 4934ad3)

- **`partials/account_backup.html`** — 118 lines. `retention_options` macro at top. Status pill logic lines 47-68. **`insecure_tls` checkbox lives in `templates/admin_backup.html:206`, NOT in account_backup.html.**
- **`partials/system_status.html`** — 133 lines, served by `/partials/system-status` endpoint, admin-only, polled every 5s from base.html, currently shows 5 status badges (Dovecot, FTS, Sync, Restore, Backup).
- **`accounts.html`** — 7-column table (Account, Owner, Auth, Stats, Status, Last Sync, Actions), status pill uses `badge-error/badge-syncing/badge-idle/badge-disabled`.
- **`account_detail.html`** — 8 sections in order: Overview, Stats, History, Ownership, Settings, Manage (admin), Backup, Delete (danger zone).
- **`dashboard.html`** — Stat cards (3-col grid), "Needs Attention" panel, "Recent Activity" block. **NO backup signal currently surfaced.**
- **`base.html` sidebar** — 8 nav items, active state via `request.url.path`. Admin-only system-status bar mounted in base.

### CSS classes that already exist (use these, don't invent)

- Status dots: `stats-dot-ok`, `stats-dot-error`, `stats-dot-syncing`
- Badges: `badge-idle`, `badge-error`, `badge-syncing`, `badge-disabled`
- Status text colours: `sync-idle`, `sync-syncing`, `sync-error`, `status-ok`, `status-error`, `status-active`
- Alerts: `alert-error`
- Containers: `stats-pill`, `stats-sep`

### Tooling — current state

- **Pre-commit hooks** (`.pre-commit-config.yaml`): trailing-whitespace, end-of-file-fixer, check-yaml/json/toml, large-file check, debug-statements, ruff (with `--fix`), ruff-format, detect-secrets, gitleaks, alembic-drift (runs `pytest tests/test_alembic_sync.py -x -q` on changes to `models.py` or `alembic/versions/`).
- **CI workflows** (`.github/workflows/`): `ci.yml` (lint + secrets + test on Py 3.12/3.13 + docker build), `docs.yml` (mkdocs strict + Pages deploy), `release.yml` (versioned multi-arch Docker).
- **No `scripts/` directory exists.** Wave 1a creates it.
- **Tests use in-memory SQLite** via `tests/conftest.py:14-36`. **No tests assert on flash messages** today (zero matches for `flash_success|flash_error`).
- **Ruff config** (`pyproject.toml:44-72`): line length 100, target Python 3.12.

### Anti-patterns to avoid

- **DO NOT** rename `Account.sync_state.value == 'syncing'` enum literals in templates. The enum value is wire-format; only labels around it change.
- **DO NOT** rename DB tables (`BackupDestination`, `AccountBackup`). The deferral is in the recommendation; touching them is out of scope.
- **DO NOT** add Italian-language strings to templates. There is no i18n infrastructure. The IT column in `LEXICON.md` is i18n-target only.
- **DO NOT** invent CSS classes when the existing ones cover the case. Reuse `sync-error`, `stats-dot-ok`, etc.
- **DO NOT** poll restic on every chain-widget request. The widget reads cached `last_snapshot_count` / `last_snapshot_at` from `AccountBackup` (added in Wave 2.5).
- **DO NOT** introduce new routers. Extend existing `ui_*` routers.
- **DO NOT** skip the alembic-drift hook. Every model change in Wave 2.5 must keep `tests/test_alembic_sync.py` green.

---

## Phase 1 — Wave 1a: Foundations (Day 1–2)

**Goal:** Land `LEXICON.md`, an advisory CI lint check, and any required scripts plumbing. Nothing user-facing changes here. This wave protects every later wave from re-litigating vocabulary.

### Task 1.1 — Lift `LEXICON.md` to repo root

**Files:**
- Source: `docs/superpowers/analysis/2026-05-10-ux-lexicon-audit/lexicon/LEXICON-draft.md` (the v1.1-updated draft already in the audit folder).
- Create: `LEXICON.md` at repo root.

- [ ] **Step 1.1.1** — Copy the contents of `LEXICON-draft.md` to `LEXICON.md` at repo root. Delete the "DRAFT for repo root" preamble lines and the v1.1 update note (it lives in the audit doc). Keep everything else verbatim.
- [ ] **Step 1.1.2** — Add a top-of-file note: `> Source of truth for MFB's user-facing vocabulary. See \`docs/superpowers/analysis/2026-05-10-ux-lexicon-audit/08-recommendation.md\` for the design reasoning.`
- [ ] **Step 1.1.3** — Verify: `head -3 LEXICON.md` shows the heading and the source-of-truth pointer.

### Task 1.2 — Create `scripts/lexicon-check.sh`

**Files:**
- Create: `scripts/` directory (does not exist today).
- Create: `scripts/lexicon-check.sh`.
- Create: `scripts/.lexicon-allowlist` (one line per allowed exception, format: `path/to/file.html: <reason>`).

- [ ] **Step 1.2.1** — `mkdir -p scripts/`. Create `scripts/lexicon-check.sh` with the body below. Make it executable (`chmod +x scripts/lexicon-check.sh`).

```bash
#!/usr/bin/env bash
# scripts/lexicon-check.sh — advisory check for bare "Backup" in user-facing copy.
# See LEXICON.md. Exits 0 (warning only); CI logs the warnings but does not fail.
set -uo pipefail

# Scope: HTML templates and router flash strings only.
SCOPE_TEMPLATES='src/mailfallback/templates/'
SCOPE_ROUTERS='src/mailfallback/routers/'

# Allowed qualifiers (case-insensitive): backup must be followed or preceded by one of these.
ALLOWED='(local|off-site|offsite|locale|configuration|destination|policy|deposito|repository|snapshot|completed|failed|started|now|history|profile)'

# Find bare "Backup" in templates and in flash_* assignments in routers.
TPL_HITS=$(grep -REn '\bBackup\b' "$SCOPE_TEMPLATES" --include='*.html' 2>/dev/null \
    | grep -vEi "[Bb]ackup[[:space:]]+${ALLOWED}\b" \
    | grep -vEi "${ALLOWED}[[:space:]]+[Bb]ackup\b" \
    || true)

ROUTER_HITS=$(grep -REn 'flash_(success|error)' "$SCOPE_ROUTERS" --include='*.py' 2>/dev/null \
    | grep -E '\bBackup\b|\bbackup\b' \
    | grep -vEi "[Bb]ackup[[:space:]]+${ALLOWED}\b" \
    | grep -vEi "${ALLOWED}[[:space:]]+[Bb]ackup\b" \
    || true)

if [ -n "$TPL_HITS" ] || [ -n "$ROUTER_HITS" ]; then
    echo "::warning::lexicon-check found bare 'Backup' usage; see LEXICON.md."
    [ -n "$TPL_HITS" ]    && echo "$TPL_HITS"
    [ -n "$ROUTER_HITS" ] && echo "$ROUTER_HITS"
fi

exit 0
```

- [ ] **Step 1.2.2** — Create `scripts/.lexicon-allowlist` (empty for now; future file:line entries with reason).
- [ ] **Step 1.2.3** — Run `bash scripts/lexicon-check.sh` locally. Confirm it produces warnings on today's templates (it should — the rename hasn't happened yet). The warnings are informational; the script exits 0.

### Task 1.3 — Wire lint check into pre-commit + CI (advisory)

**Files:**
- Modify: `.pre-commit-config.yaml` (add a local hook).
- Modify: `.github/workflows/ci.yml` (add a step in the `lint` job).

- [ ] **Step 1.3.1** — Add to `.pre-commit-config.yaml` (under `repos:` → `local:` block, alongside the existing `alembic-drift` hook):

```yaml
  - id: lexicon-check
    name: lexicon-check
    entry: scripts/lexicon-check.sh
    language: script
    pass_filenames: false
    files: '^(src/mailfallback/templates/.*\.html|src/mailfallback/routers/.*\.py)$'
    verbose: true
```

- [ ] **Step 1.3.2** — Add to `.github/workflows/ci.yml` in the `lint` job, after the ruff-format step, a new step:

```yaml
      - name: Lexicon check (advisory)
        run: bash scripts/lexicon-check.sh
```

- [ ] **Step 1.3.3** — Verify locally: `pre-commit run lexicon-check --all-files` runs without erroring out (warnings are OK).

### Task 1.4 — Document the audit-display mapper Wave 2 will extend

The mapper already exists at `src/mailfallback/services/audit_service.py:5-40` (the `ACTION_LABELS` dict) and is invoked via the `get_action_label` filter (per Phase 0 frontend discovery). Wave 2 just needs to add 4–6 new mappings. Nothing to do in Wave 1a — note in the LEXICON.md "Enforcement" section that the mapper is the home for legacy action strings.

- [ ] **Step 1.4.1** — Edit `LEXICON.md` (now at repo root): under "Enforcement", add: `Legacy audit-action strings (e.g. \`backup_destination.create\`) render via \`get_action_label\` in \`audit_service.py\`. New display strings are added there in Wave 2; the underlying action strings stay stable for backward-compatibility of historical audit rows.`

### Verification — Wave 1a

- [ ] `head -1 LEXICON.md` shows `# MailFallBack lexicon`.
- [ ] `bash scripts/lexicon-check.sh` runs and exits 0 (warnings expected pre-rename).
- [ ] `pre-commit run --all-files` passes (the new lexicon-check hook is non-blocking).
- [ ] CI lint job runs the lexicon-check step in a draft PR and the workflow is green.

### Anti-pattern guards — Wave 1a

- **DO NOT** make the lint check blocking. It must be advisory-only until the rename has stabilised (revisit after Wave 4).
- **DO NOT** scope the regex broader than `templates/` + router flash strings. Touching code comments, tests, audit log strings would explode false positives.

### Commit

`feat(lexicon): wave 1a — lift LEXICON.md to repo root + advisory CI lexicon-check`

---

## Phase 2 — Wave 1b: Four security-adjacent fixes (Day 3–5)

**Goal:** Fix four user-facing dishonesties before any rename or IA work. Each is independent.

### Task 2.1 — "Backup configured" badge → honest status

**Files:**
- Modify: `src/mailfallback/templates/partials/account_backup.html` (status pill logic at lines 47-68).
- Modify: `src/mailfallback/routers/ui_backup.py` (or wherever the snapshots/last-backup status helper lives — check `account_detail` route in `ui_accounts.py:436+`).

- [ ] **Step 2.1.1** — In `partials/account_backup.html`, locate the existing status-pill block (lines 47-68). Replace it with a three-state version:

```jinja
{% if backup_config.last_backup_at %}
    <span class="stats-pill"><span class="stats-dot stats-dot-ok"></span>
        Last back-up {{ backup_config.last_backup_at | time_ago }}
    </span>
{% elif backup_config.last_status.value == "running" %}
    <span class="stats-pill"><span class="stats-dot stats-dot-syncing"></span> Running</span>
{% elif backup_config.last_status.value == "failed" %}
    <span class="stats-pill"><span class="stats-dot stats-dot-error"></span>
        Last back-up failed
    </span>
{% else %}
    <span class="stats-pill"><span class="stats-dot"></span>
        Off-site policy set — no successful back-up yet
    </span>
{% endif %}
```

- [ ] **Step 2.1.2** — **Note:** this Wave uses `last_backup_at` as the proxy for "last successful". The proper `last_successful_run_at` column lands in Wave 2.5. Add a TODO comment in the template: `{# TODO Wave 2.5: switch to last_successful_run_at #}`.
- [ ] **Step 2.1.3** — Verify by spinning up `docker compose up -d` and visiting an account with no successful backup yet. Should show "Off-site policy set — no successful back-up yet".

### Task 2.2 — `insecure_tls` toggle: relabel + warning banner

**Files:**
- Modify: `src/mailfallback/templates/admin_backup.html` (line 206 area, where the checkbox is — both create and edit forms).
- Reuse: existing `sync-error` class from `static/css/style.css:194`.

- [ ] **Step 2.2.1** — Locate the `insecure_tls` checkbox in `admin_backup.html` (Phase 0 reports it at line 206). Both the create form AND the edit form expose it. For each instance, restructure the field:

```html
<label>
    <input type="checkbox" name="insecure_tls" value="1"
           {% if dest and dest.insecure_tls %}checked{% endif %}
           onchange="this.closest('form').querySelector('.tls-warning').hidden = !this.checked">
    <strong>Skip TLS certificate verification</strong>
    <small class="text-muted">Self-signed CA only. Production deployments must use a trusted certificate.</small>
</label>
<div class="alert alert-error tls-warning" role="alert"
     {% if not (dest and dest.insecure_tls) %}hidden{% endif %}>
    <i data-lucide="shield-alert" class="icon-md icon-inline"></i>
    <strong>Warning:</strong> certificate verification is disabled. The connection is encrypted but the server's identity is NOT verified.
</div>
```

- [ ] **Step 2.2.2** — Verify in browser: toggling the checkbox shows/hides the warning banner.

### Task 2.3 — Recovered-mailbox: rewrite flash + add "Promote to live" button

**Files:**
- Modify: `src/mailfallback/routers/ui_backup.py:411` (the success flash) and `:388` (the auto-name).
- Add: a new POST route `/accounts/{id}/promote-recovered` in `ui_accounts.py`.
- Modify: `src/mailfallback/templates/account_detail.html` to show a "Promote to live" button for accounts whose name starts with `Recovered ` and whose `suspended=True`.

- [ ] **Step 2.3.1** — In `ui_backup.py:388`, replace `name=f"Backup {account.name} ({datetime.now(UTC).strftime('%Y-%m-%d')})"` with `name=f"Recovered {account.name} ({datetime.now(UTC).strftime('%Y-%m-%d')})"`.

- [ ] **Step 2.3.2** — In `ui_backup.py:411`, replace the success flash:

```python
request.session["flash_success"] = (
    f"Recovered into '{restored_account.name}'. The mailbox is suspended — "
    f"review and use the 'Promote to live' button to enable it, or delete it to drop the recovered data."
)
```

- [ ] **Step 2.3.3** — Add a new POST route to `ui_accounts.py` (place near `account_toggle_suspend` at line ~574):

```python
@router.post("/accounts/{account_id}/promote-recovered")
async def account_promote_recovered(
    account_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    user = _get_session_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)
    account = get_account(db, account_id, user)
    if not account:
        return RedirectResponse("/", status_code=303)
    if not account.name.startswith("Recovered "):
        request.session["flash_error"] = "Only recovered mailboxes can be promoted."
        return RedirectResponse(f"/accounts/{account_id}", status_code=303)
    update_account(db, account_id, user, suspended=False)
    log_action(
        db, user=user, action="account.promote_recovered",
        resource_type="account", resource_id=account_id,
        resource_name=account.email_address or account.name,
        ip_address=request.client.host if request.client else None,
    )
    request.session["flash_success"] = f"Promoted '{account.name}' to live. Sync may now run."
    return RedirectResponse(f"/accounts/{account_id}", status_code=303)
```

- [ ] **Step 2.3.4** — In `account_detail.html`, add a button near the existing "Edit Account" / suspend toggle, conditionally visible:

```jinja
{% if account.name.startswith("Recovered ") and account.suspended %}
<form method="post" action="/accounts/{{ account.id }}/promote-recovered" style="display:inline">
    <button type="submit" class="icon-btn primary"
            data-confirm="Promote {{ account.name }} to a live mailbox? Sync will start running. Make sure this is the recovered data you want to keep."
            onsubmit="return confirm(this.dataset.confirm)">
        <i data-lucide="check-circle" class="icon-md"></i> Promote to live
    </button>
</form>
{% endif %}
```

- [ ] **Step 2.3.5** — Add a test in `tests/test_ui_accounts.py` (create the file if needed) covering: (a) non-recovered account can't be promoted, (b) recovered account can be promoted, (c) promotion clears `suspended`.

**Note:** The "Promote to live" button is COSMETIC until Wave 1.5 fixes the dovecot suspended-flag bug. Until that's fixed, suspended accounts are still served by Dovecot and the user sees no behavioural difference. This is acceptable — the button works at the data level, the user-visible effect arrives with Wave 1.5.

### Task 2.4 — Dashboard "off-site health" row

**Files:**
- Modify: `src/mailfallback/templates/dashboard.html` (add row after stat cards).
- Modify: `src/mailfallback/routers/ui.py` (the dashboard route, to query backup-health summary).

- [ ] **Step 2.4.1** — In the dashboard route handler (find via `grep -n "dashboard.html" src/mailfallback/routers/ui.py`), add a query for backup-health summary:

```python
from mailfallback.models import AccountBackup, BackupStatus
backup_summary = {
    "total_policies": db.query(AccountBackup).count(),
    "with_recent_success": db.query(AccountBackup).filter(
        AccountBackup.last_backup_at.isnot(None),
        AccountBackup.last_status == BackupStatus.completed,
    ).count(),
    "with_failures": db.query(AccountBackup).filter(
        AccountBackup.last_status == BackupStatus.failed,
    ).count(),
    "never_succeeded": db.query(AccountBackup).filter(
        AccountBackup.last_backup_at.is_(None),
    ).count(),
}
```

Pass `backup_summary` to the template.

- [ ] **Step 2.4.2** — In `dashboard.html`, after the stat cards block, add an off-site health row:

```jinja
{% if backup_summary.total_policies > 0 %}
<section class="card mt-1">
    <h3><i data-lucide="cloud-upload" class="icon-md icon-inline"></i> Off-site backup health</h3>
    <div class="flex gap-1 flex-wrap">
        <span class="stats-pill">
            <span class="stats-dot stats-dot-ok"></span>
            {{ backup_summary.with_recent_success }} healthy
        </span>
        {% if backup_summary.with_failures > 0 %}
        <span class="stats-pill">
            <span class="stats-dot stats-dot-error"></span>
            {{ backup_summary.with_failures }} failed
        </span>
        {% endif %}
        {% if backup_summary.never_succeeded > 0 %}
        <span class="stats-pill">
            <span class="stats-dot"></span>
            {{ backup_summary.never_succeeded }} no successful back-up yet
        </span>
        {% endif %}
        <a href="/admin/backup" class="text-small">Manage repositories →</a>
    </div>
</section>
{% endif %}
```

- [ ] **Step 2.4.3** — Verify on the running stack: dashboard shows the row when at least one `AccountBackup` exists, hidden otherwise.

### Verification — Wave 1b

- [ ] `uv run ruff check src/ tests/` is clean.
- [ ] `uv run pytest tests/ -n auto -q` passes (390+ tests).
- [ ] Manual browser walkthrough in the running stack:
  - Account with no successful backup yet shows "Off-site policy set — no successful back-up yet" pill.
  - `insecure_tls` toggle on /admin/backup shows the warning banner when checked.
  - A recovered account shows "Promote to live" button; non-recovered does not.
  - Dashboard shows the off-site health row when at least one AccountBackup exists.

### Anti-pattern guards — Wave 1b

- **DO NOT** rename `BackupStatus` enum values. The string values are stored in DB rows and changing them would break historical data.
- **DO NOT** add Italian strings to any template touched in this wave. English-only.
- **DO NOT** lift the suspended account into Dovecot active rotation yet — that's Wave 1.5.

### Commit

`feat(ux): wave 1b — four security-adjacent honesty fixes`

---

## Phase 3 — Wave 1.5: Dovecot suspended verification + DR doc (~1 day)

**Goal:** Fix the bug where the Dovecot userdb endpoint serves suspended accounts. Add the disaster-recovery doc page.

### Task 3.1 — Patch dovecot.py to filter suspended

**Files:**
- Modify: `src/mailfallback/routers/dovecot.py:24-47` (the userdb endpoint).
- Add tests: `tests/test_dovecot_userdb.py` (or extend the existing dovecot test file).

- [ ] **Step 3.1.1** — In `dovecot.py:47`, find the line `accounts = [a for a in all_accounts if a.enabled and a.store.enabled]`. Change to:

```python
accounts = [a for a in all_accounts if a.enabled and not a.suspended and a.store.enabled]
```

- [ ] **Step 3.1.2** — Search for any other places in `dovecot.py` that enumerate accounts; verify each is suspended-aware. Likely there is also a passdb-related filter; cross-check with `models.py` `Account.is_authenticated` property.

- [ ] **Step 3.1.3** — Add a test that calls the userdb endpoint with a suspended account and asserts the suspended account is NOT returned. Use the in-memory SQLite + TestClient pattern from `conftest.py`.

- [ ] **Step 3.1.4** — Verify against running Dovecot: in `docker compose exec dovecot doveadm user --field= "test@example.com"`, suspended account should yield "user not found" (or equivalent), not a successful lookup.

### Task 3.2 — Add disaster-recovery doc page

**Files:**
- Create: `docs/src/admin/disaster-recovery.md`.
- Modify: `mkdocs.yml` to include it in nav.

- [ ] **Step 3.2.1** — Create `docs/src/admin/disaster-recovery.md` with content covering:
  - **Why this matters:** restic snapshots are useless without `MAILFALLBACK_SECRET_KEY` (Fernet-encrypts the restic password) AND a recent Postgres dump (holds the `BackupDestination` rows including the encrypted password).
  - **What to back up off-MFB:** the secret key (`MAILFALLBACK_SECRET_KEY` env var) + a periodic Postgres dump.
  - **How to back up Postgres:** `docker compose exec db pg_dump -U mailfallback mailfallback > mfb-$(date +%F).sql`.
  - **Recovery procedure:** restore Postgres → set `MAILFALLBACK_SECRET_KEY` → `docker compose up -d` → re-discover restic snapshots.
  - **What MFB does NOT back up:** itself. The product backs up your mail; you must back up the product.

- [ ] **Step 3.2.2** — Add to `mkdocs.yml` nav (in the Admin Guide section): `- Disaster recovery: admin/disaster-recovery.md`.
- [ ] **Step 3.2.3** — Build docs locally: `uv run mkdocs build --strict`. Ensure no broken links.
- [ ] **Step 3.2.4** — Add a link from `templates/admin_backup.html` (top of page) to the published doc URL: `<small>See <a href="/docs/admin/disaster-recovery/">disaster recovery</a> for what to back up alongside your repositories.</small>`. Use the relative URL or the GitHub Pages URL depending on deploy mode.

### Verification — Wave 1.5

- [ ] `uv run pytest tests/test_dovecot_userdb.py -v` passes the new suspended-filter test.
- [ ] Manual: suspend a test account, observe Dovecot login fails (`doveadm auth test`).
- [ ] `uv run mkdocs build --strict` is clean.
- [ ] `/admin/backup` shows the disaster-recovery link.

### Anti-pattern guards — Wave 1.5

- **DO NOT** silently change the dovecot endpoint without test coverage — the audit log dependency makes this a security-impact change.
- **DO NOT** put the disaster-recovery doc in the User Guide; it's admin-only.

### Commit

`fix(dovecot): wave 1.5 — userdb endpoint filters suspended accounts + DR doc`

---

## Phase 4 — Wave 2: English-only lexicon rename + audit display map (Week 2)

**Goal:** Rename ~120 user-facing strings in templates and ~25 in routers per the binding `LEXICON.md` table. Add 4–6 audit-action display labels for renamed concepts. **English only.**

### Task 4.1 — Inventory the rename surface

**Files:**
- Read: `docs/superpowers/analysis/2026-05-10-ux-lexicon-audit/scratch/vocab-inventory.md` (the 25 worst offenders are listed there).

- [ ] **Step 4.1.1** — Generate a fresh rename inventory:

```bash
grep -rEn '"Backup |"Backed up|"Sync |Sync now|"backup destination|"Backup Now|"Snapshot restored|No backup configured|"Backup destination|"Mail Store|"Stores' \
    src/mailfallback/templates/ src/mailfallback/routers/ \
    > /tmp/mfb-rename-surface.txt
wc -l /tmp/mfb-rename-surface.txt
```

- [ ] **Step 4.1.2** — Cross-reference with `scratch/vocab-inventory.md`'s "Sites of confusion" list. The 25 numbered entries are the prioritised rename targets.

### Task 4.2 — Apply the rename per the binding table

For each row in `LEXICON.md`'s "Banned words" table, find every occurrence in `src/mailfallback/templates/` and `src/mailfallback/routers/` (per the lexicon-check scope) and replace per the EN column.

**Important rules:**
- **DO NOT** touch enum string values (e.g. `account.sync_state.value == 'syncing'`). The enum value is wire-format.
- **DO NOT** touch internal log lines (`logger.info("Sync started for ...")`). Only user-facing strings.
- **DO** touch flash messages (`request.session["flash_*"] = "..."`).
- **DO** touch all `<h1>`, `<h2>`, `<h3>`, `<button>`, `<label>`, `<th>`, `<small>`, etc. text content in templates.

Concrete replacement table (apply EVERYWHERE, case-preserving):

| Find | Replace with |
|---|---|
| `Backup configured` | `Off-site policy set` |
| `Backup destination` (admin nav, page titles) | `Repository` |
| `Backup Destinations` | `Repositories` |
| `Backup Now` (button) | `Back up now` (off-site context) / `Sync now` (local context) |
| `Backup started` | `Snapshot started` |
| `Backup configuration saved` | `Backup policy saved` |
| `No backup configured for this account` | `No off-site backup configured for this account` |
| `No backup destinations configured` | `No repositories configured` |
| `Snapshot restored as` | `Recovered into` |
| `Backed up X ago` | `Last back-up X ago` |
| `Start first backup` | `Start first sync` |
| `First backup in progress` | `First sync in progress` |
| `Mail store` (singular, body text) | leave unchanged |
| `Mail Store` (heading) | `Mail store` (sentence case per LEXICON) |
| `Stores` (alone, nav) | `Mail stores` |
| `Migrate Store` | `Migrate mail store` |

- [ ] **Step 4.2.1** — Apply each row of the table above with a sed/Edit pass. Commit after each major group (templates → routers → admin_*) to keep commits reviewable.

### Task 4.3 — Add audit-action display labels

**File:** `src/mailfallback/services/audit_service.py:5-40` (the `ACTION_LABELS` dict).

- [ ] **Step 4.3.1** — Add new entries to the `ACTION_LABELS` dict:

```python
ACTION_LABELS = {
    # ... existing entries ...
    "backup_destination.create": "Repository created",
    "backup_destination.delete": "Repository deleted",
    "backup_destination.edit": "Repository edited",
    "account.backup_configure": "Backup policy configured",
    "account.backup_now": "Manual back-up triggered",
    "account.backup_restore": "Recovered from snapshot",
    "account.promote_recovered": "Recovered mailbox promoted to live",
}
```

The underlying audit `action` strings stay stable for backward compatibility; only the display label changes.

### Task 4.4 — Update tests

- [ ] **Step 4.4.1** — Run the test suite. Test failures will be in any test that asserts on a renamed string. Update each.
- [ ] **Step 4.4.2** — Add at least 2-3 tests asserting the new flash messages (Phase 0 found ZERO existing flash-message tests — start the pattern now). Place in `tests/test_ui_backup.py` (create if needed).

### Verification — Wave 2

- [ ] `bash scripts/lexicon-check.sh` shows ZERO warnings (rename is complete).
- [ ] `uv run ruff check src/ tests/` clean.
- [ ] `uv run pytest tests/ -n auto -q` passes.
- [ ] Manual browser walkthrough confirms the binding LEXICON terms appear consistently. No bare "Backup" anywhere.
- [ ] `/admin/audit` shows the new display labels for the 7 mapped actions.

### Anti-pattern guards — Wave 2

- **DO NOT** add Italian strings (no i18n yet).
- **DO NOT** rename DB action strings (only display labels).
- **DO NOT** rename enum literal values.
- **DO NOT** touch comments / log lines / docstrings.

### Commit

`feat(ux): wave 2 — English-only lexicon rename + audit display labels`

---

## Phase 5 — Wave 2.5: Batched Alembic migration (~1 day)

**Goal:** Add three columns to `AccountBackup` so the chain widget (Wave 4) and the honesty badge (Wave 1b) can read cached data instead of shelling restic on every render.

### Task 5.1 — Generate the migration

**Files:**
- Modify: `src/mailfallback/models.py:324+` (AccountBackup model).
- Create: `alembic/versions/011_add_backup_progress_columns.py`.

- [ ] **Step 5.1.1** — In `models.py`, add to the `AccountBackup` class (alongside the existing fields):

```python
last_run_at = Column(DateTime(timezone=True), nullable=True)
last_successful_run_at = Column(DateTime(timezone=True), nullable=True)
last_snapshot_count = Column(Integer, nullable=False, default=0, server_default="0")
last_snapshot_at = Column(DateTime(timezone=True), nullable=True)
```

- [ ] **Step 5.1.2** — Generate the migration: `uv run alembic revision --autogenerate -m "add backup progress columns"`. Verify the generated file in `alembic/versions/011_*.py` adds exactly these four columns. If autogenerate misses anything (timezone aware?), edit by hand to match the migration 010 pattern.

- [ ] **Step 5.1.3** — Apply locally: `uv run alembic upgrade head`. Verify schema with `docker compose exec db psql -U mailfallback -c '\d account_backups'`.

### Task 5.2 — Update backup_worker to write the new columns

**File:** `src/mailfallback/services/backup_worker.py:88-90` (the success update block).

- [ ] **Step 5.2.1** — In `backup_worker.execute_backup`, find the success block (~line 88-90) and the failure block (~line 94-95). Update both:

```python
# success path
backup.last_run_at = datetime.now(UTC)
backup.last_successful_run_at = datetime.now(UTC)
backup.last_backup_at = datetime.now(UTC)  # legacy alias; keep for now
backup.last_status = BackupStatus.completed
backup.last_error = None

# failure path
backup.last_run_at = datetime.now(UTC)
backup.last_status = BackupStatus.failed
backup.last_error = str(exc)
# DO NOT touch last_successful_run_at on failure
```

- [ ] **Step 5.2.2** — Add snapshot-count caching after a successful backup. After `run_backup()` returns, also call `list_snapshots()` and cache the count + the most recent snapshot's `time` field:

```python
snapshots = list_snapshots(backup.destination, backup.account_id)
backup.last_snapshot_count = len(snapshots)
if snapshots:
    backup.last_snapshot_at = datetime.fromisoformat(snapshots[0]["time"].replace("Z", "+00:00"))
```

- [ ] **Step 5.2.3** — Switch the Wave 1b honesty badge in `partials/account_backup.html` from `last_backup_at` to `last_successful_run_at`. Remove the TODO comment.

### Task 5.3 — Update tests

- [ ] **Step 5.3.1** — Add a test for `backup_worker` that verifies all four new columns are populated after a successful backup (mock restic). Place in `tests/test_backup_worker.py`.
- [ ] **Step 5.3.2** — Run `uv run pytest tests/test_alembic_sync.py -x -q` — the alembic-drift hook will fail loudly otherwise on next commit.

### Verification — Wave 2.5

- [ ] `uv run alembic upgrade head` clean on a fresh DB.
- [ ] `uv run pytest tests/test_alembic_sync.py tests/test_backup_worker.py -v` passes.
- [ ] Trigger a manual backup on the running stack; observe `account_backups.last_successful_run_at` populates.

### Anti-pattern guards — Wave 2.5

- **DO NOT** drop or rename `last_backup_at` in this wave (legacy alias for backward compat with admin views; can be deprecated in a later wave).
- **DO NOT** touch retention columns (`keep_daily`/`keep_weekly`/`keep_monthly`) — out of scope.

### Commit

`feat(backup): wave 2.5 — batched Alembic migration for last_run_at, last_successful_run_at, last_snapshot_count, last_snapshot_at`

---

## Phase 6 — Wave 3: IA reorder + two-pill Accounts list + /recover route (Week 3)

**Goal:** Promote off-site to a first-class concept on account-detail. Add Repository pill on the Accounts list. Split /restore into a two-flow chooser.

### Task 6.1 — Reorder account_detail sections

**File:** `src/mailfallback/templates/account_detail.html`.

- [ ] **Step 6.1.1** — Per `06-mockups.md` §4, reorder the section blocks. New order: Overview → Local backup (current "hero") → Off-site backup (was "Backup") → Source connection → Storage volume / Mail store → Ownership → Sharing → Danger zone.
- [ ] **Step 6.1.2** — Add `open` attribute to the Off-site backup `<details>` when `backup_config` exists: `<details id="section-backup"{% if backup_config %} open{% endif %}>`.
- [ ] **Step 6.1.3** — Verify in browser: account with offsite configured shows the section expanded by default; non-configured stays collapsed.

### Task 6.2 — Two-pill status on Accounts list

**Files:**
- Modify: `src/mailfallback/templates/accounts.html` (or `partials/accounts_table.html`).
- Modify: the accounts list route — likely needs `selectinload(Account.backup_configs)` to avoid N+1.

- [ ] **Step 6.2.1** — Add a "Repository" column between "Status" (mirror state) and "Last Sync". Per `06-mockups.md` §8:

```jinja
<td>
    {% if account.backup_configs %}
        {% set bc = account.backup_configs[0] %}
        {% if bc.last_successful_run_at %}
            <span class="stats-pill"><span class="stats-dot stats-dot-ok"></span>
                {{ bc.last_successful_run_at | time_ago }}
            </span>
        {% else %}
            <span class="stats-pill"><span class="stats-dot"></span> No back-up yet</span>
        {% endif %}
    {% else %}
        <span class="text-muted text-small">—</span>
    {% endif %}
</td>
```

- [ ] **Step 6.2.2** — In the accounts list service / route, add `.options(joinedload(Account.backup_configs).joinedload(AccountBackup.destination))` to avoid N+1.
- [ ] **Step 6.2.3** — Update the table column count in any colspan elsewhere (footers / empty states).

### Task 6.3 — /restore entry chooser

**Files:**
- Modify: `src/mailfallback/templates/restore.html` (or restructure).
- Add: a new template `recover.html` for the depot-side flow.
- Add: a new route `GET /recover` in `ui_restore.py` (or new `ui_recover.py`).

- [ ] **Step 6.3.1** — Per `06-mockups.md` §6, change `/restore` to render a chooser page with two cards:
  - "Recover from a snapshot" → `/recover`
  - "Move mail between mailboxes" → `/restore/move` (the old form, relocated)
- [ ] **Step 6.3.2** — Move the existing form contents from `restore.html` to a new `restore_move.html`. Keep all functionality; only the URL changes.
- [ ] **Step 6.3.3** — Build the `/recover` page (`recover.html` + `GET /recover` handler):
  - Lists all `AccountBackup` rows with at least one snapshot.
  - For each, lets the user pick a snapshot via `/accounts/{id}/backup/snapshots` (already exists).
  - The actual recover POST already exists at `/accounts/{id}/backup/restore/{snapshot_id}` — the new page is just a discoverable entry point.

### Task 6.4 — Update sidebar nav labels

**File:** `src/mailfallback/templates/base.html`.

- [ ] **Step 6.4.1** — Per `LEXICON.md`: rename `Backups` admin nav → `Repositories`, `Stores` → `Mail stores`. The href stays the same; only the label and the active-state pattern matching may need to update.

### Task 6.5 — Tests

- [ ] **Step 6.5.1** — Add a test for the new accounts-list two-pill column.
- [ ] **Step 6.5.2** — Add a test that GET /restore renders the chooser, GET /restore/move renders the old form, GET /recover renders the new page.

### Verification — Wave 3

- [ ] `uv run pytest tests/ -n auto -q` passes.
- [ ] Manual: account-detail shows reordered sections with off-site expanded when configured.
- [ ] Manual: accounts list shows two pills (Mirror + Repository).
- [ ] Manual: /restore is now a chooser, /restore/move has the old form, /recover lists snapshots across mailboxes.

### Anti-pattern guards — Wave 3

- **DO NOT** delete the old `/restore` POST endpoint — it's invoked by /restore/move now.
- **DO NOT** introduce a separate `BackupConfig` relationship on `Account` if it doesn't exist; check `models.py` first.

### Commit

`feat(ux): wave 3 — IA reorder, two-pill accounts list, /recover route split`

---

## Phase 7 — Wave 4: Chain widget + empty states + repository status columns (Week 4)

**Goal:** Add the dashboard chain hero, the per-account chain status header, the empty-state redesigns, and the status columns on /admin/backup. **Mobile floor 360px. A11y mandatory: every status surface needs icon + text + `aria-live`.**

### Task 7.1 — Chain widget data: extend the system-status endpoint

**File:** `src/mailfallback/templates/partials/system_status.html` + the route serving it (`/partials/system-status` per Phase 0 frontend).

- [ ] **Step 7.1.1** — Find the route handler. Add to its context the chain summary:

```python
chain_summary = {
    "mailboxes": db.query(Account).count(),
    "mirrors_healthy": db.query(Account).filter(Account.sync_state == SyncState.idle).count(),
    "mirrors_total": db.query(Account).count(),
    "repositories": db.query(BackupDestination).count(),
    "repositories_with_backup_recent": db.query(AccountBackup).filter(
        AccountBackup.last_successful_run_at >= datetime.now(UTC) - timedelta(days=2)
    ).count(),
    "snapshots_total": db.query(func.coalesce(func.sum(AccountBackup.last_snapshot_count), 0)).scalar(),
}
```

- [ ] **Step 7.1.2** — DO NOT poll restic from this endpoint. The values come from `AccountBackup.last_snapshot_count` (cached in Wave 2.5). Same poll cadence as today (5s).

### Task 7.2 — Dashboard chain hero

**File:** `src/mailfallback/templates/dashboard.html`.

- [ ] **Step 7.2.1** — On empty dashboard (no accounts), render the teaching hero per `06-mockups.md` §2:

```jinja
{% if not accounts %}
<section class="card card-large">
    <h2>Welcome to MailFallBack</h2>
    <p>MFB keeps your mail safe in two places:</p>
    <ol>
        <li>A <strong>local backup</strong> on this server (always available)</li>
        <li>Off-site <strong>snapshots</strong> in a Repository you control (disaster recovery)</li>
    </ol>
    <a href="/accounts/new" class="icon-btn primary">
        <i data-lucide="plus-circle" class="icon-md"></i> Connect a mailbox
    </a>
</section>
{% endif %}
```

- [ ] **Step 7.2.2** — On populated dashboard, render the chain hero card per `06-mockups.md` §3 (use the values from `chain_summary` in step 7.1.1). Include the four `●` dots with text labels (a11y: not colour-only) and `aria-live="polite"`.

### Task 7.3 — Per-account chain header line

**File:** `src/mailfallback/templates/account_detail.html`.

- [ ] **Step 7.3.1** — Add ONE line at the top of the account detail page (above the existing hero panel):

```jinja
<div class="chain-status flex gap-05 items-center mb-05" role="status" aria-live="polite">
    <span class="stats-pill" title="Mirror">
        {% if account.last_sync_at %}
            <i data-lucide="check-circle" class="icon-sm"></i> Mirror — {{ account.last_sync_at | time_ago }}
        {% else %}
            <i data-lucide="circle" class="icon-sm"></i> Mirror — never
        {% endif %}
    </span>
    <span class="stats-sep">·</span>
    <span class="stats-pill" title="Repository">
        {% if backup_config and backup_config.last_successful_run_at %}
            <i data-lucide="cloud-upload" class="icon-sm"></i> Repository — {{ backup_config.last_successful_run_at | time_ago }}
        {% elif backup_config %}
            <i data-lucide="cloud" class="icon-sm"></i> Repository — no back-up yet
        {% else %}
            <i data-lucide="cloud-off" class="icon-sm"></i> Repository — not configured
        {% endif %}
    </span>
</div>
```

### Task 7.4 — Empty-state redesigns

**Files:**
- Modify: `src/mailfallback/templates/accounts.html` (empty state).
- Modify: `src/mailfallback/templates/admin_backup.html` (empty state for /admin/backup → Repositories).

- [ ] **Step 7.4.1** — Per `06-mockups.md` §2, replace the empty Accounts copy with: "No mailboxes yet. **Connect a mailbox** to start your local backup."
- [ ] **Step 7.4.2** — Design the empty Repositories state: "No Repositories configured. A Repository is an off-site, encrypted store for snapshots. **Add a Repository** to enable off-site backup for any of your mailboxes." Add a CTA button.

### Task 7.5 — Repository admin status columns

**File:** `src/mailfallback/templates/admin_backup.html` (the destinations table).

- [ ] **Step 7.5.1** — Per `06-mockups.md` §5, add columns to the table: `Mailboxes`, `Snapshots` (sum of `last_snapshot_count` across all `AccountBackup` rows pointing to this destination), `Last back-up` (max `last_successful_run_at`), `Health` (computed: `OK` if any successful within 2d; `STALE` if older; `EMPTY` if none).
- [ ] **Step 7.5.2** — Add a `Snapshots` and `Health` query in the route handler. Aggregate per destination.

### Task 7.6 — Mobile + a11y pass

- [ ] **Step 7.6.1** — Use Chrome DevTools MCP (or browser DevTools) to test every new component at 360px width. Adjust CSS as needed (the codebase already has a 768px breakpoint in `style.css`).
- [ ] **Step 7.6.2** — For every status pill (Wave 1b badge, accounts list two-pill, chain header, dashboard hero), confirm: icon + text label, `role="status"` or `aria-live="polite"` where appropriate, sufficient colour contrast.
- [ ] **Step 7.6.3** — Run a Lighthouse a11y audit on `/dashboard` and `/accounts/{id}` (use the chrome-devtools-mcp:a11y-debugging skill if available).

### Task 7.7 — 30-day "what changed" tooltip + first-login page

**Files:**
- Modify: `src/mailfallback/templates/base.html` (sidebar nav items for renamed labels).
- Add: `src/mailfallback/templates/whats_changed.html` (one-screen page).
- Add: a redirect on first login post-rename.

- [ ] **Step 7.7.1** — Add tooltip-on-hover to renamed nav labels: e.g. `<a href="/admin/backup" title="Renamed from Backup Destinations">Repositories</a>`. Disable after 30 days post-deploy via a `settings.lexicon_renamed_at` config + a Jinja conditional.
- [ ] **Step 7.7.2** — Build a one-screen `/whats-changed` page summarising the rename (chain diagram + 5-row before/after table). Show on first login post-deploy via a `User.preferences["seen_whats_changed"]` flag.

### Verification — Wave 4

- [ ] `uv run pytest tests/ -n auto -q` passes.
- [ ] Lighthouse a11y score ≥ 90 on `/dashboard` and `/accounts/{id}`.
- [ ] Manual test at 360px: every page renders without horizontal scroll.
- [ ] Manual: dashboard shows the chain hero (empty + populated variants).
- [ ] Manual: /admin/backup shows Mailboxes / Snapshots / Last back-up / Health columns.
- [ ] First-login user sees `/whats-changed`; second login does not.

### Anti-pattern guards — Wave 4

- **DO NOT** add a sticky chain widget — it's been demoted (per recommendation v1.1).
- **DO NOT** poll restic on every render — read the cached AccountBackup columns.
- **DO NOT** colour-only status (must have icon + text).
- **DO NOT** introduce >1 new stat-card row on dashboard.

### Commit

`feat(ux): wave 4 — chain widget, empty states, repository status columns, a11y, mobile`

---

## Phase 8 — Final verification

- [ ] `uv run ruff check src/ tests/` clean.
- [ ] `uv run pytest tests/ -n auto -q` passes (target: 405–420 tests).
- [ ] `uv run mkdocs build --strict` clean.
- [ ] `bash scripts/lexicon-check.sh` returns ZERO warnings.
- [ ] `pre-commit run --all-files` passes.
- [ ] Manual walkthrough on `docker compose up -d`:
  - Dashboard shows chain hero (or teaching variant if empty).
  - Off-site health row visible when at least one AccountBackup exists.
  - Accounts list shows two pills per row (Mirror + Repository).
  - Account detail: sections in new order; off-site open when configured; chain header line visible.
  - /restore is a chooser; /recover lists snapshots across mailboxes; /restore/move is the old form.
  - /admin/backup is "Repositories" with the new columns.
  - "Promote to live" button works on a recovered mailbox; suspended mailbox cannot log in to Dovecot.
  - `insecure_tls` toggle shows the warning banner.
  - Audit log shows new display labels for the 7 mapped actions.
- [ ] Mobile (360px) walkthrough — no horizontal scroll on any page.
- [ ] Lighthouse a11y ≥ 90 on /dashboard and /accounts/{id}.
- [ ] First-login user sees `/whats-changed` page; subsequent logins do not.
- [ ] Open a PR to `main`. CI passes (lint, tests, secrets, docker build). The lexicon-check warning lines (advisory) are gone.

---

## Out of scope (deferred per recommendation v1.1)

- DB rename (`BackupDestination` → `Repository` table). Tracked as "Wave 5 follow-on" — not gated on P3.
- Italian-language UI (no i18n infrastructure). Tracked as separate epic.
- Per-org compliance report (P3 audience). Tracked as Option C, gated on real demand.
- Repository wizard (Kopia-style step-by-step add). Tracked as P5 stretch.
- Automated user testing infrastructure. Manual walkthrough only.

---

## Rollback strategy per wave

Each wave is an independent commit (or small group). To roll back:

- **Waves 1a, 1b, 1.5**: pure additive — revert the commit.
- **Wave 2 (lexicon rename)**: revert is mechanical but loses the binding lexicon change. Better to forward-fix.
- **Wave 2.5 (Alembic)**: requires `alembic downgrade -1` AND revert. Order matters.
- **Wave 3 (IA + /recover)**: revert + remove `/recover` route. /restore returns to original.
- **Wave 4 (chain widget)**: revert removes the dashboard hero and per-page chain header; the underlying data (Wave 2.5 columns) stays.

---

**Plan version:** 1.0 (matches `08-recommendation.md` v1.1 amendments).
**Last updated:** 2026-05-10.
**Total estimated effort:** 4.5–5 weeks single developer (Wave 1a + 1b + 1.5 + 2 + 2.5 = ~2 weeks; Waves 3 + 4 = ~2.5–3 weeks).
