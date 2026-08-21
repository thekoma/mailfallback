# CLAUDE.md — MailFallBack (MFB)

## Project Overview

Self-hosted email backup service wrapping mbsync/isync with a web UI. Backs up IMAP mailboxes to local Maildir and provides read-only IMAP access via Dovecot as a fallback.

## Tech Stack

- **Backend**: Python 3.14+ (the version the container ships and the only one CI tests), FastAPI, SQLAlchemy, Alembic, APScheduler
- **Frontend**: Jinja2 templates, HTMX, Pico CSS, Lucide icons
- **Database**: PostgreSQL (only supported backend)
- **Sync**: mbsync/isync (subprocess)
- **IMAP access**: Dovecot 2.4 (`dovecot/dovecot:2.4.4` pinned, SQL auth against MFB database)
- **Webmail**: Roundcube (`roundcube/roundcubemail:latest`, read-only via Dovecot IMAP)
- **Package manager**: uv (with uv.lock)

## Project Structure

```
src/mailfallback/
├── app.py                    # FastAPI app factory + lifespan (includes migration resume)
├── config.py                 # Settings from env vars (pydantic-settings)
├── db.py                     # SQLAlchemy engine + session
├── models.py                 # All models (User, Account, SyncJob, MailStore, StoreMigration, Group)
├── security.py               # bcrypt + Fernet encryption
├── dependencies.py           # FastAPI deps (get_db, get_current_user, require_admin)
├── routers/                  # API + UI routes
│   ├── auth.py               # Login/logout + OIDC + Google/Microsoft OAuth2
│   ├── accounts.py           # Account CRUD API
│   ├── sync.py               # Sync trigger + test connection + discovery
│   ├── health.py             # /healthz, /readyz, /metrics
│   ├── config_io.py          # Config export/import
│   ├── dovecot.py            # Internal API for Dovecot Lua userdb
│   ├── ui.py                 # Dashboard + accounts list + login routes
│   ├── ui_accounts.py        # Account detail + edit + ownership routes
│   ├── ui_admin.py           # Admin: users, stores, groups, settings
│   └── ui_profile.py         # User profile + password change
├── services/
│   ├── account_service.py    # Account CRUD logic + ownership
│   ├── user_service.py       # User CRUD + auth
│   ├── group_service.py      # Group CRUD + SSO sync
│   ├── sync_service.py       # Job queue (create/get/list)
│   ├── sync_worker.py        # Execute mbsync subprocess + byte sampler/ledger + crash sweep
│   ├── sync_failures.py      # Classify failed syncs: throttled/transient vs real errors
│   ├── sync_budget.py        # Daily budget resolution + progress/ETA + backoff math
│   ├── mbsync_config.py      # Generate .mbsyncrc files
│   ├── scheduler.py          # APScheduler periodic sync + pause gate/expiry tick
│   ├── store_service.py      # Mail store management + path derivation + orphan detection
│   ├── stats_service.py      # Per-account stats from Dovecot doveadm API
│   ├── dovecot_manager.py    # Dovecot HTTP API (reload, mailbox stats)
│   ├── migration_service.py  # Store migration orchestration (validate, copy, verify, cleanup)
│   ├── migration_worker.py   # Low-level file copy engine with progress tracking
│   ├── provider_discovery.py # Auto-discover IMAP settings
│   ├── s3_probe.py           # boto3 connection probe + bucket helpers (no restic side effects)
│   ├── repo_inventory.py     # List/classify restic prefixes in a Repository (orphan detection)
│   ├── config_backup_service.py # Encrypted full-config export/import + scheduled backup runner
│   ├── app_credential_service.py # Per-user access token lifecycle: create/list/revoke + verify_credential()
│   └── oauth2.py             # Google + Microsoft OAuth2 token management
├── templates/                # Jinja2 HTML templates
└── static/                   # CSS + JS
```

## Commands

Local hooks catch what CI would, before the code leaves the machine — this
laptop is much faster than the CI runners, so a failure found here costs
seconds instead of minutes. Install both hook types once:

```bash
uv run pre-commit install --hook-type pre-commit --hook-type pre-push
```

- **pre-commit**: ruff, secrets, alembic drift, lexicon.
- **pre-push**: the full suite twice — `-n auto` (fast) and `-n 4`. The second
  is not redundant: xdist groups tests differently depending on worker count,
  so a laptop with ~10 workers and a CI runner with 2-4 can disagree. A test
  that purges a module from `sys.modules` once passed locally and failed in CI
  for exactly this reason.

```bash
# Run tests (parallel, ~7s)
uv run pytest tests/ -n auto -v

# Run tests (sequential, ~47s)
uv run pytest tests/ -v

# Lint
uv run ruff check src/ tests/

# Format
uv run ruff format src/ tests/

# Run dev server
uv run uvicorn mailfallback.app:app --reload

# Run with Docker
docker compose up -d --build

# Generate migration
uv run alembic revision --autogenerate -m "description"

# Apply migrations
uv run alembic upgrade head

# Apply migrations on running container
docker compose exec mailfallback uv run alembic upgrade head
```

## Release (CalVer)

- Versioning: `YYYY.MM.INC` (es. `2026.07.0`), fonte unica `src/mailfallback/version.py` (NON pyproject: PEP 440 normalizzerebbe `07`→`7`). Bumpata SOLO dalla release PR.
- Flusso: ogni push su main aggiorna la PR `release/next` (version bump + CHANGELOG.md via git-cliff + callout migration). Merge della PR → `release.yml`: build multi-arch → push ghcr (`:VER`, `:stable`, `:latest`) → tag git + GitHub Release PER ULTIMI (mai tag orfani).
- Prerelease: "Run workflow" su Release → rc/beta (`2026.07.1-rc1`, GitHub prerelease, nessun changelog; l'alias `:rc` viene aggiornato solo per le rc, non per le beta).
- La release PR (creata con GITHUB_TOKEN) non triggera la CI: per forzarla, close/reopen.

## Key Patterns

- **TemplateResponse**: Use `request=request, name="template.html", context={...}` (Python 3.14 Starlette API)
- **Auth check in UI routes**: Use `_get_session_user(request, db)` helper — checks session, queries User, verifies `user.enabled`, returns `User | None`
- **Admin check**: `user.role.value != "admin"` -> redirect to `/`; in service layer compare against `UserRole.admin` enum directly
- **Field allowlists**: Update functions use `_UPDATABLE_*_FIELDS` sets to restrict which columns can be changed (see `account_service.py`, `user_service.py`, `store_service.py`)
- **Inline styles**: All CSS in `static/css/style.css`, use classes not inline styles
- **JavaScript**: All JS in `static/js/` (`core.js` + per-page files like `account-detail.js`, `restore_workspace.js`), no inline `<script>` blocks
- **Icons**: Lucide (vendored), use classes `icon-sm/md/lg/xl icon-inline`
- **Vendored frontend assets**: htmx/Alpine/Lucide/flatpickr/Pico live in `static/vendor/` with versions pinned in `vendor.json`. Renovate bumps the manifest (custom manager); after a bump run `python3 scripts/sync_vendor.py` to re-download the files — the `vendor` CI job fails on drift
- **Sync failure classification**: throttled/transient/budget_paused are self-recovering pauses, never error states (`sync_failures.py` + `sync_budget.py`); only `error` is red in the UI
- **New columns with NOT NULL**: Always add `server_default` in migrations
- **Sidebar active state**: `request.url.path` checked in `base.html` to add `class="active"` on current nav link
- **Login page isolation**: `body.login-page` class hides sidebar and centers content when `user` is not set
- **Jinja2 filters**: `filesizeformat` for byte sizes, `cron_human` for human-readable cron schedules

## Maildir Layout

Uses **UUID-based paths** with **LAYOUT=fs** and **SubFolders Verbatim**. Folder structure on disk:

```
{store_path}/{account-uuid}/INBOX/
{store_path}/{account-uuid}/Sent/
{store_path}/{account-uuid}/Drafts/
{store_path}/{account-uuid}/[Gmail]/All Mail/
```

- `store_path` comes from the MailStore record (e.g., `/data/mailboxes`)
- `account-uuid` is the Account's primary key (UUID4), derived at creation via `derive_maildir_path(store.path, account.id)`
- Paths are determined at account creation and never change (except store migration)
- Reassigning an account to a different user requires zero file operations — only DB change in `account_owners`
- Dovecot home directories: `{store_path}/.dovecot-home/{username}/`

## Data Model

- **User -> MailStore**: 1:1 via `store_id` FK — determines Dovecot home location + default store for new accounts
- **User -> allowed_stores**: Many-to-many — restricts which stores a non-admin user can select
- **Account -> MailStore**: 1:1 via `store_id` FK — tracks where the account's mail physically lives
- **User <-> Account**: Many-to-many via `account_owners` join table — supports multiple owners per account and multiple accounts per user
- **Group**: Owner (User FK) + members (many-to-many `group_members`) + accounts (many-to-many `group_accounts`). `sso_sync` flag auto-syncs membership from OIDC group claims
- **Account -> SyncJob**: One-to-many with cascade delete
- **Sync budget (migration 021)**: Account carries the daily traffic ledger (`traffic_date` + `bytes_synced_today`, UTC day), `daily_sync_budget_mb` (NULL = provider default, 0 = unlimited), the scheduler pause gate (`sync_paused_until` + `pause_reason` ∈ budget|throttle|transient), and initial-sync markers (`initial_sync_completed_at` + `initial_sync_total_messages`). `SyncJob.failure_kind` is a plain string: throttled|budget_paused|transient|interrupted|error — only `error` is a red UI state, the rest self-recover
- **StoreMigration**: Per-account or per-user-home migration. `account_id` set = maildir migration, `user_id` set = dovecot-home migration. Status phases (pending/copying/verifying/cleaning/completed/failed), crash recovery on startup
- **Store deletion**: Blocked when accounts or user homes still on the store — must drain first via `get_store_contents()`
- **Orphan detection**: `store_service.detect_orphans()` finds UUID directories on disk not matching any account in the database
- **AppCredential (migration 028)**: Per-user access tokens, wire format `mfb_<prefix>_<secret>` — `token_prefix` is the indexed lookup key, `secret_hash` is a keyed-HMAC of the secret (keyed to `MAILFALLBACK_SECRET_KEY`, so rotating that key invalidates every existing token). Comma-separated `scopes` ∈ `imap` | `mail:read` | `sync:trigger` (`mail:read` and `sync:trigger` have no consumer until phase 2). Carries expiry, revocation, and `last_used_at`/`last_used_kind`. Excluded from the encrypted config export (`config_backup_service._EXPORT_TABLES`), consistent with notification channels

## UI Architecture

- **Layout**: Sidebar navigation (left, 220px fixed) + main content area (max-width 900px)
- **Sidebar sections**: User links (Dashboard, Accounts, Webmail) → ADMIN section (Users, Stores, Groups, System) → Profile + Logout
- **Dashboard**: Stat cards (3x2 grid: Accounts, Messages, Storage, Errors, Users, Stores) + Needs Attention panel + Recent Activity feed with sync status
- **Accounts page**: Table with Account/Owner/Auth/Stats/Status/Last Sync/Actions columns, kebab dropdown for row actions
- **Account detail**: Info table + collapsible sections (Mailbox Stats, Ownership & Visibility, Edit Account, Migrate Store, Delete Account with danger-zone separator) + Sync History
- **Admin pages**: Tables with inline icon buttons (all have `title` tooltips) + inline forms below for quick-add (Add User, Add Store, Create Group)
- **Login page**: Full-width centered card, sidebar hidden via `body.login-page` CSS class
- **Responsive**: Mobile breakpoint at 768px — sidebar becomes drawer with hamburger menu, stat cards go 2-column

## Groups & Account Visibility

- Groups provide shared account access: members of a group can see all accounts assigned to that group via Dovecot namespaces
- Account visibility in Dovecot is determined by: direct ownership (via `account_owners`) OR group membership (via `group_accounts` + `group_members`)
- `sso_sync` groups auto-update membership from OIDC `groups` claim on login
- The Lua userdb endpoint (`/api/internal/dovecot/userdb/{username}`) computes the full account list from both ownership and group membership

## Dovecot Integration

- Uses **official `dovecot/dovecot:2.4.4`** image (latest stable) — no custom Dockerfile
- Config files volume-mounted from `docker/dovecot/conf.d/mfb-*.conf` to `/etc/dovecot/conf.d/`
- SQL auth queries the MFB `users` and `mail_stores` tables directly via PostgreSQL
- `auth_mechanisms = plain login` (login required by Roundcube)
- `auth_allow_cleartext = yes` (no TLS on internal Docker network)
- Login blocked during migration (`migrating = false` in SQL WHERE clause)
- SSL disabled by default (`mfb-ssl.conf: ssl = no`); enable by mounting certs and overriding
- doveadm HTTP API on port 8080 for stats collection and reload commands
- **Lua userdb**: `mfb-lua-userdb.lua` calls MFB's internal API (`GET /api/internal/dovecot/userdb/{username}`) at login. Returns dynamic namespace fields per account. Uses `dovecot.http.client` + `require "json"`.
- **Lua passdb**: `mfb-lua-passdb.lua` is generated and sits BEFORE `passdb sql`. Both lua files are written before `mfb-auth.conf` because that file references them via `lua_file`; writing it first would let a Dovecot start or reload landing in between hit a parse error on a missing file. The passdb returns `PASSDB_RESULT_NEXT` without any HTTP call unless the password starts with `mfb_`, so ordinary password logins are unaffected; otherwise it POSTs to `/api/internal/dovecot/passdb`, whose status code IS the contract: 200 OK, 404 NEXT (falls through to SQL passdb), 401 MISMATCH, anything else INTERNAL_FAILURE.
- **Dynamic namespaces**: Each account becomes a Dovecot namespace. First account = inbox namespace (no prefix). Others get `"Name (email)/"` prefix. MFB is the control plane for IMAP visibility.
- **ACL read-only**: `acl_driver = vfile`, `acl_globals_only = yes`. ACLs are settings blocks (dovecot 2.4.3+ removed the global acl file): a global `acl readonly { acl_id = owner; acl_rights = lrs }` makes every mailbox owner-read-only (incl. dynamic per-account namespaces), and `mailbox Staging` / `mailbox Staging/*` filters grant `lrwstie` on the plain `Staging` mailbox that lives inside the root namespace (`{home}/root-inbox/Staging` — there is no separate per-user Staging namespace). `acl_defaults_from_inbox = yes` makes defaults for mailboxes without an ACL entry come from INBOX (`lrs`) rather than the private-namespace default of full owner rights, so a client can't CREATE an undeletable top-level mailbox. Non-obvious: Dovecot's `mailbox` ACL filters match the namespace-INTERNAL mailbox name with the namespace prefix stripped — this is why `Staging` had to move inside the root namespace rather than living in its own. Blocks delete, expunge, insert, flag changes everywhere except Staging. mbsync writes directly to filesystem — unaffected by ACLs.

## Roundcube Webmail

- Uses **official `roundcube/roundcubemail:latest`** image — no custom Dockerfile
- Connects to Dovecot IMAP (`dovecot:31143`) inside Docker network
- Shares PostgreSQL database with table prefix `rc_` (via `docker/roundcube/custom.php`)
- `use_subscriptions = false` — lists all folders via IMAP LIST, no SUBSCRIBE needed
- `subscriptions_option` plugin enabled
- No SMTP configured — compose UI visible but send will fail
- Port `8001` (host) → `80` (container)
- Feature-toggled via `MAILFALLBACK_WEBMAIL_URL` env var — when set, shows "Webmail" link in MFB nav bar

## Store Migration

When an admin moves a user to a different store:
1. `user.migrating` is set to `True` — blocks sync jobs and Dovecot login
2. Files are copied with skip-existing semantics and per-file progress tracking
3. File counts are verified between source and destination
4. Account `maildir_path` values are updated to the new store
5. Old directory is removed
6. On crash/restart, incomplete migrations are automatically resumed via `app.py` lifespan

## Testing

- Tests use in-memory SQLite via `conftest.py` fixtures (SQLAlchemy abstracts the layer)
- `db_session` fixture with dependency override for `get_db`
- mbsync subprocess is mocked in tests
- Run specific test: `uv run pytest tests/test_sync_worker.py -v`

## Important Notes

- **Never** use `docker compose down -v` to fix migration issues — it destroys data
- Default admin credentials: `admin` / `changeme`
- `MAILFALLBACK_DEBUG=true` persists mbsync configs to `/tmp/mbsync/` for inspection
- Single UID (1000/vmail) across all containers for K8s cgroup compatibility
