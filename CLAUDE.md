# CLAUDE.md — MailFallBack (MFB)

## Project Overview

Self-hosted email backup service wrapping mbsync/isync with a web UI. Backs up IMAP mailboxes to local Maildir and provides read-only IMAP access via Dovecot as a fallback.

## Tech Stack

- **Backend**: Python 3.12+, FastAPI, SQLAlchemy, Alembic, APScheduler
- **Frontend**: Jinja2 templates, HTMX, Pico CSS, Lucide icons
- **Database**: PostgreSQL (only supported backend)
- **Sync**: mbsync/isync (subprocess)
- **IMAP access**: Dovecot 2.4 (`dovecot/dovecot:latest-2.4`, SQL auth against MFB database)
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
│   ├── sync_worker.py        # Execute mbsync subprocess
│   ├── mbsync_config.py      # Generate .mbsyncrc files
│   ├── scheduler.py          # APScheduler periodic sync
│   ├── store_service.py      # Mail store management + path derivation + orphan detection
│   ├── stats_service.py      # Per-account stats from Dovecot doveadm API
│   ├── dovecot_manager.py    # Dovecot HTTP API (reload, mailbox stats)
│   ├── migration_service.py  # Store migration orchestration (validate, copy, verify, cleanup)
│   ├── migration_worker.py   # Low-level file copy engine with progress tracking
│   ├── provider_discovery.py # Auto-discover IMAP settings
│   └── oauth2.py             # Google + Microsoft OAuth2 token management
├── templates/                # Jinja2 HTML templates
└── static/                   # CSS + JS
```

## Commands

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

## Key Patterns

- **TemplateResponse**: Use `request=request, name="template.html", context={...}` (Python 3.14 Starlette API)
- **Auth check in UI routes**: Use `_get_session_user(request, db)` helper — checks session, queries User, verifies `user.enabled`, returns `User | None`
- **Admin check**: `user.role.value != "admin"` -> redirect to `/`; in service layer compare against `UserRole.admin` enum directly
- **Field allowlists**: Update functions use `_UPDATABLE_*_FIELDS` sets to restrict which columns can be changed (see `account_service.py`, `user_service.py`, `store_service.py`)
- **Inline styles**: All CSS in `static/css/style.css`, use classes not inline styles
- **JavaScript**: All JS in `static/js/app.js`, no inline `<script>` blocks
- **Icons**: Lucide via CDN, use classes `icon-sm/md/lg/xl icon-inline`
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
- **StoreMigration**: Per-account or per-user-home migration. `account_id` set = maildir migration, `user_id` set = dovecot-home migration. Status phases (pending/copying/verifying/cleaning/completed/failed), crash recovery on startup
- **Store deletion**: Blocked when accounts or user homes still on the store — must drain first via `get_store_contents()`
- **Orphan detection**: `store_service.detect_orphans()` finds UUID directories on disk not matching any account in the database

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

- Uses **official `dovecot/dovecot:latest-2.4`** image — no custom Dockerfile
- Config files volume-mounted from `docker/dovecot/conf.d/mfb-*.conf` to `/etc/dovecot/conf.d/`
- SQL auth queries the MFB `users` and `mail_stores` tables directly via PostgreSQL
- `auth_mechanisms = plain login` (login required by Roundcube)
- `auth_allow_cleartext = yes` (no TLS on internal Docker network)
- Login blocked during migration (`migrating = false` in SQL WHERE clause)
- SSL disabled by default (`mfb-ssl.conf: ssl = no`); enable by mounting certs and overriding
- doveadm HTTP API on port 8080 for stats collection and reload commands
- **Lua userdb**: `mfb-lua-userdb.lua` calls MFB's internal API (`GET /api/internal/dovecot/userdb/{username}`) at login. Returns dynamic namespace fields per account. Uses `dovecot.http.client` + `require "json"`. Passdb stays SQL.
- **Dynamic namespaces**: Each account becomes a Dovecot namespace. First account = inbox namespace (no prefix). Others get `"Name (email)/"` prefix. MFB is the control plane for IMAP visibility.
- **ACL read-only**: `acl_driver = vfile`, `acl_globals_only = yes`, `acl_global_path = /etc/dovecot/dovecot-acl` with `* owner lrs` (lookup, read, write-seen). Blocks delete, expunge, insert, flag changes. mbsync writes directly to filesystem — unaffected by ACLs.

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
