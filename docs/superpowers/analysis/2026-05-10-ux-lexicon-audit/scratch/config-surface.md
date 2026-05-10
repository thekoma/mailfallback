# Configuration surface inventory

## Method

This inventory was compiled by reading the following sources:

- **`src/mailfallback/config.py`** — Settings class with pydantic-settings, all env vars with defaults
- **`docker-compose.yml`** — Service definitions, environment vars, volumes, dependencies
- **`docs/src/getting-started/configuration.md`** — Authoritative user-facing configuration guide
- **`docs/src/admin-guide/{stores,users,groups}.md`** — Admin-side UI documentation
- **`docs/src/user-guide/accounts.md`** — User-facing account management guide
- **`docs/src/architecture/overview.md`** — Component diagram and data flow
- **`CLAUDE.md`** — Commands section for dev tooling
- **`README.md`** — Quick start and configuration tables

## Environment variables

| Env name | Type | Default | What it controls | UI equivalent |
|----------|------|---------|------------------|---|
| `MAILFALLBACK_SECRET_KEY` | string | `change-me-in-production` | Root encryption key for stored credentials (Fernet AES-128-CBC) | None; set once at deploy |
| `MAILFALLBACK_SESSION_SECRET` | string | `change-me-session-secret` | Session cookie signing key | None; set once at deploy |
| `MAILFALLBACK_SESSION_HTTPS_ONLY` | bool | `false` | Enforce HTTPS for session cookies (requires TLS-terminating reverse proxy) | None; HTTP/HTTPS transport config |
| `MAILFALLBACK_DEBUG` | bool | `false` | Enable debug logging; persists mbsync configs to `/tmp/mbsync/` for inspection | None; dev/prod mode toggle |
| `MAILFALLBACK_BOOTSTRAP_STORE_PATH` | path | `/data/mailboxes` | Filesystem path for the default mail store created on first boot | Admin → Stores → name/path of auto-created default |
| `MAILFALLBACK_CONFS_PATH` | path | `/confs` | Base directory where generated Dovecot and Roundcube configs are written | None; internal mount point |
| `MAILFALLBACK_MBSYNC_BINARY` | path | `mbsync` | Path to the mbsync binary (for subprocess execution) | None; sync engine selection |
| `MAILFALLBACK_DATABASE_URL` | string | `postgresql://mailfallback:mailfallback@db:5432/mailfallback` <!-- pragma: allowlist secret --> | Full PostgreSQL connection string | None; set via docker-compose or external DB |
| `MAILFALLBACK_DB_HOST` | string | `db` | Database hostname (used internally for Dovecot SQL auth config generation) | None; Dovecot config generation |
| `MAILFALLBACK_DB_PORT` | int | `5432` | Database port (used internally for Dovecot SQL auth config generation) | None; Dovecot config generation |
| `MAILFALLBACK_DB_NAME` | string | `mailfallback` | PostgreSQL database name | None; part of connection setup |
| `MAILFALLBACK_DB_USER` | string | `mailfallback` | PostgreSQL username | None; part of connection setup |
| `MAILFALLBACK_DB_PASSWORD` | string | `mailfallback` | PostgreSQL password (shared across all services) | None; part of connection setup |
| `MAILFALLBACK_SYNC_MAX_WORKERS` | int | `4` | Maximum concurrent mbsync processes (job queue parallelism) | Admin → System → Sync max workers (if UI exposed) |
| `MAILFALLBACK_SYNC_LOG_DIR` | path | `/data/logs/sync` | Directory where sync job logs are stored | None; internal logs dir |
| `MAILFALLBACK_OIDC_ENABLED` | bool | `false` | Enable OIDC single sign-on | Admin → Settings → OIDC enabled toggle |
| `MAILFALLBACK_OIDC_CLIENT_ID` | string | _(empty)_ | OAuth2 client ID from identity provider | Admin → Settings → OIDC Client ID |
| `MAILFALLBACK_OIDC_CLIENT_SECRET` | string | _(empty)_ | OAuth2 client secret | Admin → Settings → OIDC Client Secret (not shown, encrypted) |
| `MAILFALLBACK_OIDC_DISCOVERY_URL` | string | _(empty)_ | OIDC discovery endpoint URL | Admin → Settings → OIDC Discovery URL |
| `MAILFALLBACK_OIDC_ADMIN_GROUP` | string | `mailfallback-admin` | OIDC group claim value that grants admin role | Admin → Settings → OIDC Admin Group |
| `MAILFALLBACK_OIDC_USER_GROUP` | string | `mailfallback-user` | OIDC group claim value for regular users | Admin → Settings → OIDC User Group |
| `MAILFALLBACK_OIDC_USERINFO_URL` | string | _(empty)_ | UserInfo endpoint URL (also used for Dovecot OAuth2 token validation) | Admin → Settings → OIDC UserInfo URL |
| `MAILFALLBACK_GOOGLE_CLIENT_ID` | string | _(empty)_ | Google OAuth2 client ID for Gmail backup | Admin → Settings → Google OAuth Client ID |
| `MAILFALLBACK_GOOGLE_CLIENT_SECRET` | string | _(empty)_ | Google OAuth2 client secret | Admin → Settings → Google OAuth Client Secret (encrypted) |
| `MAILFALLBACK_MICROSOFT_CLIENT_ID` | string | _(empty)_ | Microsoft OAuth2 application ID for Outlook backup | Admin → Settings → Microsoft OAuth Client ID |
| `MAILFALLBACK_MICROSOFT_CLIENT_SECRET` | string | _(empty)_ | Microsoft OAuth2 client secret | Admin → Settings → Microsoft OAuth Client Secret (encrypted) |
| `MAILFALLBACK_MICROSOFT_TENANT` | string | `consumers` | Azure AD tenant (`consumers`, `common`, `organizations`, or specific tenant ID) | Admin → Settings → Microsoft Tenant |
| `MAILFALLBACK_DOVECOT_API_URL` | string | `http://dovecot:8080` | URL for Dovecot doveadm HTTP API (mailbox stats, reload) | None; internal service discovery |
| `MAILFALLBACK_DOVECOT_API_KEY` | string | _(empty)_ | API key for authenticating with doveadm HTTP API (must match `DOVEADM_PASSWORD` on Dovecot container) | Admin → Settings → Dovecot API Key (encrypted) |
| `MAILFALLBACK_DOVECOT_IMAP_HOST` | string | `dovecot` | Hostname for connecting to Dovecot IMAP (used during restore operations) | None; internal service discovery |
| `MAILFALLBACK_DOVECOT_IMAP_PORT` | int | `31143` | IMAP port for Dovecot (plaintext IMAP; see IMAPS in docker-compose comments) | None; IMAP access port |
| `MAILFALLBACK_WEBMAIL_ENABLED` | bool | `false` | Enable webmail integration (Roundcube) | Admin → Settings → Webmail enabled toggle |
| `MAILFALLBACK_WEBMAIL_URL` | string | _(empty)_ | Public URL for Roundcube (e.g., `http://localhost:8001`); when set, "Webmail" link appears in MFB sidebar | Admin → Settings → Webmail URL |
| `MAILFALLBACK_WEBMAIL_OAUTH_CLIENT_ID` | string | _(empty)_ | OAuth2 client ID for Roundcube SSO login | Admin → Settings → Webmail OAuth Client ID |
| `MAILFALLBACK_WEBMAIL_OAUTH_CLIENT_SECRET` | string | _(empty)_ | OAuth2 client secret for Roundcube SSO | Admin → Settings → Webmail OAuth Client Secret (encrypted) |
| `MAILFALLBACK_WEBMAIL_OAUTH_AUTH_URI` | string | _(empty)_ | OAuth2 authorization endpoint for Roundcube SSO | Admin → Settings → Webmail OAuth Auth URI |
| `MAILFALLBACK_WEBMAIL_OAUTH_TOKEN_URI` | string | _(empty)_ | OAuth2 token endpoint for Roundcube SSO | Admin → Settings → Webmail OAuth Token URI |
| `MAILFALLBACK_WEBMAIL_OAUTH_IDENTITY_URI` | string | _(empty)_ | OAuth2 userinfo endpoint for Roundcube SSO | Admin → Settings → Webmail OAuth Identity URI |
| `MAILFALLBACK_TIKA_ENABLED` | bool | `false` | Enable Apache Tika for FTS attachment content indexing | Admin → Settings → Tika enabled toggle |
| `MAILFALLBACK_TIKA_URL` | string | `http://tika:9998` | URL for Tika server (used by Dovecot FTS decoder) | None; internal service discovery |
| `MAILFALLBACK_METRICS_API_KEY` | string | _(empty)_ | API key for the `/metrics` Prometheus endpoint (required in `Authorization: Bearer` header when set) | None; ops/monitoring |
| `DB_USER` | string | `mailfallback` | PostgreSQL username (used by docker-compose.yml; aliases `MAILFALLBACK_DB_USER` when explicitly set) | None; DB setup |
| `DB_PASSWORD` | string | `mailfallback` | PostgreSQL password (shared across all services in docker-compose) | None; DB setup |
| `DB_NAME` | string | `mailfallback` | PostgreSQL database name (used by docker-compose.yml) | None; DB setup |

**Notes on UI equivalence:**
- Most env vars are set once at deploy time (secrets, URLs).
- Some vars expose read-only or limited UI controls in **Admin → Settings** (OIDC, OAuth, webmail, Tika toggles).
- Database and internal service URLs have no UI equivalent — they are bootstrap/infrastructure.
- **Mismatch observed**: `MAILFALLBACK_SYNC_MAX_WORKERS` is not currently exposed in the UI's System settings, despite being operationally important.

---

## Docker compose services

| Service | Image | Role | External port | Depends on | Volumes mounted |
|---------|-------|------|---|---|---|
| `db` | `postgres:18-alpine` | PostgreSQL database backend for all services | None (internal) | None | `pgdata:/var/lib/postgresql` |
| `mailfallback` | Built from `docker/Dockerfile` in repo | FastAPI control plane; generates configs, orchestrates sync, manages auth | `8000:8000` | `db:healthy` | `dovecot_confd:/confs/dovecot`, `webmail_conf:/confs/webmail`, `maildirs:/data/mailboxes`, `maildirs2:/data/mailboxes2` |
| `dovecot` | `dovecot/dovecot:latest-2.4` | Read-only IMAP backend; provides fallback access to backed-up mail | `31143:31143` (IMAP plaintext), `9900:9900` (OpenMetrics) | `db:healthy`, `mailfallback:healthy` | `dovecot_confd:/etc/dovecot/conf.d:ro`, `maildirs:/data/mailboxes`, `maildirs2:/data/mailboxes2` |
| `webmail` | `roundcube/roundcubemail:latest` | Web-based mail client; read-only access to backed-up mail | `8001:80` | `db:healthy`, `dovecot:started` | `webmail_conf:/var/roundcube/config:ro` |
| `tika` | `apache/tika:latest` | Full-text search (FTS) decoder; analyzes attachments for indexing | `9998:9998` | None | None |

**Special cases:**
- `webmail` and `tika` are **optional** (Docker Compose profiles: `webmail`, `tika`).
- Dovecot IMAPS (port 31993) is commented out in the default compose but can be enabled by mounting TLS certs.
- Health checks are configured on `db` and `mailfallback`; other services depend on explicit conditions.
- All services run as UID 1000 (user `vmail`) for Kubernetes pod-level volume sharing compatibility.

---

## Docs site map

### Index and navigation

**`docs/src/index.md`** — Landing page, feature overview, quick start reference.

### Getting Started (4 files)

| File | H1 | Summary |
|------|----|----|
| `installation.md` | Installation | Prerequisites (Docker, PostgreSQL) and source code cloning |
| `docker.md` | Running with Docker | docker-compose quickstart, env file setup, default credentials |
| `configuration.md` | Configuration | **Master reference** for all env vars, defaults, and descriptions; organized by category (core, database, sync, OIDC, OAuth providers, Dovecot, webmail, Tika, metrics) |
| `kubernetes.md` | Kubernetes Deployment | Helm chart setup, cert-manager integration, config mgmt |

### User Guide (5 files)

| File | H1 | Summary |
|------|----|----|
| `dashboard.md` | Dashboard | Stat cards (accounts, messages, usage, errors, users, stores), recent activity |
| `accounts.md` | Accounts | Account CRUD, provider auto-discovery, auth methods (app password, Google OAuth2, Microsoft OAuth2), sync schedules, manual sync |
| `profile.md` | Profile | User password change, theme preferences (dark mode toggle), preferences API |
| `webmail.md` | Webmail | Roundcube integration, shared mail access, folder navigation |
| `restore.md` | Restore | IMAP client setup, Dovecot connection, recovery scenarios |

### Admin Guide (6 files)

| File | H1 | Summary |
|------|----|----|
| `users.md` | User Management | User CRUD, role assignment (admin/user), password reset, enabled/disabled, SSO provisioning |
| `stores.md` | Mail Stores | Store creation, path management, default store, store migration, orphan detection, deletion constraints |
| `groups.md` | Groups | Group creation, member management, account assignment, SSO group sync, account visibility rules |
| `settings.md` | Settings | System-wide settings (OIDC, OAuth providers, webmail, Tika, metrics) |
| `sso.md` | Single Sign-On (SSO) | OIDC setup, group claim mapping, auto-user-creation, role assignment |
| `oauth-providers.md` | OAuth Providers | Google and Microsoft OAuth2 setup guides (external links to `docs/guides/`) |
| `audit.md` | Audit Logging | Audit log viewing, who did what when, filtering |

### Architecture (4 files)

| File | H1 | Summary |
|------|----|----|
| `overview.md` | Architecture Overview | Component diagram (browser, MFB, Roundcube, Dovecot, Tika, PostgreSQL, Maildir), data flows (backup, IMAP access, config generation), service dependency graph |
| `data-model.md` | Data Model | Entity relationships (User, MailStore, Account, SyncJob, Group), ownership, account visibility, store migration, orphan detection |
| `dovecot.md` | Dovecot Integration | SQL auth, Lua userdb, dynamic namespaces, ACL read-only model, FTS Flatcurve |
| `config-generation.md` | Config Generation | How MFB auto-generates Dovecot and Roundcube configs from env vars at startup |
| `fts-tika.md` | Full-Text Search | Tika integration, attachment indexing, FTS queries |

### Key concepts discussed in docs vs gaps

| Concept | Docs coverage | GUI coverage | Gap? |
|---------|---|---|---|
| **Account** | Detailed (user guide + admin guide) | Comprehensive (CRUD, detail page, sync history, ownership, groups) | No gap |
| **Mail Store** | Detailed with path layout (admin guide) | Good (store list, create, migrate, delete) | Minor: `SYNC_MAX_WORKERS` config not in UI |
| **Sync job** | Mentioned in overview, detailed history on account detail | Good (queue depth, logs, error tracking, manual trigger) | No gap |
| **Dovecot** | Architecture documented, SQL auth + Lua userdb explained | Minimal (stats API integration, reload on config change, no manual config UI) | Gap: No UI for Dovecot config tweaks or health status |
| **Maildir path** | Detailed (UUID-based, LAYOUT=fs, folder naming) | Implicit (derives from account UUID at creation) | Gap: Users don't see the path; `derive_maildir_path()` is internal |
| **Group** | Detailed (membership, account assignment, SSO sync) | Good (create, manage members, assign accounts) | No gap |
| **User role** | Documented (admin vs user capabilities) | Good (role field on user edit) | No gap |
| **Store migration** | Detailed (copying, verification, crash recovery) | Good (detail page, status in job history, blocking during migration) | No gap |
| **OIDC / SSO** | Documented (setup, group claim mapping, auto-user-creation) | Configured in Admin → Settings, role/group mapping automatic | No gap |
| **OAuth2 (Gmail/Outlook)** | Documented with setup guides | Good (UI auto-detects provider, "Authorize with Google/Microsoft" buttons) | No gap |
| **Webmail** | Documented (integration, shared access) | Feature-toggled (visible when `WEBMAIL_URL` set) | No gap |
| **Tika/FTS** | Documented (attachment indexing) | Feature-toggled (visible when `TIKA_ENABLED` set) | No gap |
| **Roundcube** | Documented (official image, config generation) | Links in UI when enabled | No gap |
| **Encryption** | Documented (Fernet, bcrypt, at-rest) | Implicit (credentials not shown decrypted) | No gap |
| **ACL / read-only** | Detailed (ACL rules, mbsync bypass) | Implicit (users can only read, not delete) | Gap: No UI for understanding why they can't delete |
| **Orphan detection** | Mentioned in stores guide | Appears in Admin → System? | Minor: Not clearly surfaced in UI; "System Settings" location unclear |

---

## Vocabulary used in docs vs GUI

### Master vocabulary table (15 key concepts)

| Concept | Term in docs | Term in GUI | Alignment | Notes |
|---------|---|---|---|---|
| **Email backup unit** | "account" or "email account" | "Account" (table header, sidebar) | ✅ Perfect match | Consistent across all materials |
| **Storage volume** | "mail store" or "store" | "Stores" (sidebar, admin section) | ✅ Perfect match | Consistent; "path" always used for filesystem location |
| **Sync operation** | "sync" or "sync job" | "Sync Now" button, "Sync History" | ✅ Perfect match | Consistent; "job" used in internal code |
| **User account** | "user" | "User" (admin section) | ✅ Perfect match | Not to be confused with email "account" (potential UX risk) |
| **User group** | "group" | "Groups" (admin section) | ✅ Perfect match | Consistent; group-based account visibility clear |
| **Account owner** | "owner" or "direct ownership" | "Owner" column, "Ownership & Visibility" section | ✅ Perfect match | Clear in both docs and UI |
| **Group member** | "group member" or "member" | "Members" (group detail) | ✅ Perfect match | Context makes it clear |
| **Group-shared account** | "group-shared account" or "visibility" | "Groups can see it" or namespace prefix in IMAP | ⚠️ Partial | Docs use "account visibility"; UI shows namespace prefix (e.g., "Account Name (email@domain.com)") but doesn't explicitly label as "group-shared" |
| **Read-only IMAP** | "read-only" (via ACL) | Implicit (users can't delete) | ⚠️ Implicit | Docs explain the mechanism; UI doesn't warn users "you can't delete messages here" |
| **Mail folder** | "mailbox" (in stats context) | "Mailbox Stats" section on account detail | ✅ Perfect match | Consistent; "folder" also used colloquially |
| **Auto-discovery** | "provider auto-discovery" | "Auto-discovery" doesn't appear; just "adds IMAP settings automatically" | ⚠️ Semantic gap | Docs call it auto-discovery; UI just silently fills fields — term not visible to user |
| **Sync schedule** | "cron expression" or "cron schedule" | "Sync Schedule" with examples (e.g., `0 * * * *`) | ✅ Good | Docs explain cron format; UI shows examples; no mismatch |
| **Credential** | "password" or "app password" or "refresh token" | "Password" field, OAuth flow hidden | ✅ Good | Clear distinction between password and OAuth token; "app password" term used in docs |
| **Store migration** | "store migration" or "migrate" | "Migrate Store" button, "Migration History" | ✅ Perfect match | Consistent; status and progress tracked |
| **Session** | "session cookie" (technical) | "Preferences" (theme toggle) | ✅ Technical vs user | Docs mention session mechanics; UI doesn't expose session concept |
| **OIDC / SSO** | "OIDC" and "SSO" used interchangeably | "Settings" → "OIDC" + "SSO Sync" (on groups) | ✅ Good | Docs explain both terms; UI uses "OIDC" for protocol config, "SSO Sync" for group feature |

### Highlighted mismatches and semantic gaps

1. **"Account" overloading** (RISK): Both email accounts and user accounts are called "account" in everyday language. Docs and UI use context to disambiguate ("email account" vs. "user account"), but there's potential for confusion, especially in error messages or when searching help. **Mitigation**: Always qualify — "email account," "user account," or distinguish by UI section (Users vs. Accounts).

2. **"Read-only" not surfaced** (UX GAP): Docs explain Dovecot ACLs enforce read-only mode, but the UI doesn't explicitly warn users or explain why they can't delete messages. Roundcube users may be surprised. **Mitigation**: Add a banner or tooltip: "Backed-up mail is read-only. Delete from the original account, then re-sync."

3. **"Group-shared" term missing** (SEMANTIC GAP): Docs use "account visibility" and "group-shared"; UI uses "Groups can see it" or shows a namespace prefix in IMAP. The term "group-shared" isn't explicit in the UI. **Mitigation**: In the "Ownership & Visibility" section, label it "Visible to groups:" with a clear explanation.

4. **Auto-discovery silent** (UX GAP): Docs call it "provider auto-discovery"; the UI just silently fills IMAP fields when you enter an email. Users don't know it happened. **Mitigation**: Show a brief message: "Auto-discovered IMAP settings for <provider>" or a badge "Auto-discovered" next to the host field.

5. **"Maildir" never used in UI** (ACCEPTABLE): Docs mention Maildir layout and paths; UI never exposes the term. Users don't need to know. **Verdict**: No gap; internal detail.

6. **"Store" vs. "mail store"** (CONSISTENCY): Docs use "mail store" formally; UI/sidebar use "Stores" (plural, short). **Verdict**: Acceptable for brevity; context is clear.

---

## Tooling commands

### Development commands (from CLAUDE.md)

```bash
# Tests (parallel, ~7s)
uv run pytest tests/ -n auto -v

# Tests (sequential, ~47s)
uv run pytest tests/ -v

# Linting
uv run ruff check src/ tests/

# Code formatting
uv run ruff format src/ tests/

# Dev server (with live reload)
uv run uvicorn mailfallback.app:app --reload

# Run with Docker
docker compose up -d --build

# Database migration (autogenerate)
uv run alembic revision --autogenerate -m "description"

# Database migration (apply)
uv run alembic upgrade head

# Database migration (on running container)
docker compose exec mailfallback uv run alembic upgrade head
```

### What's documented

- **CLAUDE.md**: Lists above commands under "Commands" section.
- **README.md**: Development section shows test, lint, and Docker commands; also mentions Kubernetes and certificate setup.
- **docs/src/getting-started/docker.md**: Shows `docker compose up -d` but doesn't mention manual migration or Alembic.
- **docs/src/getting-started/installation.md**: Mentions `uv sync --dev` for source install.

### Missing / unclear CLI operations

1. **No documented CLI for admin operations**: Everything is GUI-driven (user creation, store management, group management). An admin might want:
   - `mfb user create --username admin --password X --role admin`
   - `mfb store create --name "Archive" --path /archive`
   - `mfb account list --user alice`

2. **No documented CLI for force operations**:
   - Force re-run a sync job (if job is stuck)
   - Resume a failed store migration (only happens auto on startup)
   - Clear sync lock (if a job process dies without cleaning up)

3. **No documented health check command**: Users must `curl http://localhost:8000/healthz`, but this isn't mentioned in getting-started docs.

4. **Alembic not documented for end users**: Only in README ("From Source") section and CLAUDE.md dev commands. Operators might need to know how to check migration status or rollback.

5. **No reset/nuke script**: If things go wrong (orphaned entries, corruption), no documented way to reset without touching Docker volumes directly.

---

## CLI gaps (admin operations without a CLI but available in GUI)

| Operation | Current GUI method | Why CLI would help | Severity |
|-----------|---|---|---|
| Create user | Admin → Users → Add User form | Bulk user provisioning (e.g., from LDAP export) | High |
| Reset user password | Admin → Users → password reset button | Programmatic account recovery | Medium |
| Create store | Admin → Stores → Add Store form | Infrastructure-as-code; automated setup | Medium |
| Migrate account to store | Account detail → Migrate Store dropdown | Bulk migration (e.g., old store decommission) | Medium |
| Create group | Admin → Groups → Create Group form | SSO group sync setup automation | Low (if using SSO, managed externally) |
| Delete user | Admin → Users → delete button | Cleanup in scripts | Low |
| Enable/disable user | Admin → Users → toggle button | Bulk enable/disable (e.g., after security event) | Medium |
| Trigger sync now | Accounts → Sync Now button or kebab | Programmatic trigger (already has `/api/sync/{id}` REST) | Low (REST API exists) |
| View audit log | Admin → Audit section | Parse logs in scripts | Low |
| Export/import config | Admin → Settings → Export/Import buttons | Backup/restore (already has `/api/config/` endpoints) | Low (REST API exists) |

### Reverse: CLI operations without GUI equivalent

**None identified.** All operational needs are exposed via the GUI. Alembic migrations are a deployment-time concern (not an operator-facing UI).

---

## Summary: Lexicon alignment

**Vocabulary is remarkably consistent.** The docs and GUI use nearly identical terms for all major concepts: account, store, sync, user, group, owner, migrate, etc. No terminology rewrites are needed.

**However, semantic gaps exist:**

1. **"Read-only" not surfaced to end users** → Add UI hints explaining backup immutability.
2. **"Group-shared" term missing from UI** → Make account visibility to groups explicit.
3. **"Auto-discovery" silent in UI** → Show a success message when IMAP settings are auto-discovered.
4. **"Maildir" and UUID paths internal** → Acceptable; not needed for operators.
5. **Account creation CLI missing** → Implement if bulk provisioning is a use case.

**Config surface vocabulary:**
- Env var names all use `MAILFALLBACK_` prefix consistently.
- Naming is long but clear: `MAILFALLBACK_DOVECOT_API_KEY`, `MAILFALLBACK_WEBMAIL_OAUTH_TOKEN_URI`.
- Docs organize vars by category; no orphan vars.
- Most vars map cleanly to UI settings or are infrastructure-only (database URLs, paths).

**No critical misalignment detected. Operators reading docs, checking `.env`, and using the GUI will find consistent terminology and concepts.**
