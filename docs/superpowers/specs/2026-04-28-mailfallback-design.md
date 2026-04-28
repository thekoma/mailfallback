# Mailfallback — Design Spec

## Problem

Cloud email providers can lock you out without warning. 20+ years of email history should not depend on a single provider's goodwill. Existing backup tools like isync/mbsync work well but lack a user-friendly interface.

## Solution

A self-hosted email backup service that:

- Wraps mbsync with a web UI for configuration and monitoring
- Provides read-only webmail access to backed-up email via Dovecot + Snappymail/Roundcube
- Supports multiple email accounts per instance with independent storage paths
- Deploys via Docker Compose or Helm chart (bjw-s-labs)

## Architecture — Monolith Orchestrator

A single Python application (FastAPI) orchestrates all logic. Infrastructure components (mbsync, Dovecot, webmail) run as separate, optional containers.

```
┌──────────────────────────────────────────────────────┐
│                 mailfallback core                     │
│  FastAPI + HTMX/Jinja2 + SQLAlchemy + APScheduler    │
│                                                      │
│  ┌───────────┐  ┌────────────┐  ┌──────────────────┐ │
│  │ Scheduler │─▶│ Job Queue  │◀─│ REST API         │ │
│  │ (cron)    │  │ (DB table) │  │ POST /api/sync/… │ │
│  └───────────┘  └─────┬──────┘  └──────────────────┘ │
│                       │                               │
│                       ▼                               │
│                ┌─────────────┐                        │
│                │ Sync Worker │                        │
│                │ subprocess  │                        │
│                └──────┬──────┘                        │
└───────────────────────┼──────────────────────────────┘
                        │
         ┌──────────────┼──────────────┐
         ▼              ▼              ▼
   ┌──────────┐  ┌───────────┐  ┌───────────┐
   │  mbsync  │  │  Dovecot  │  │  Webmail  │
   │          │  │ read-only │  │(optional) │
   └────┬─────┘  └─────▲─────┘  └─────▲─────┘
        │               │              │
        ▼               │         IMAP │
   ┌─────────┐          │              │
   │ Maildir │──────────┘──────────────┘
   │ volumes │
   └─────────┘
```

## Containers

| Container | Image | Role | Required |
|---|---|---|---|
| **mailfallback** | Custom (Python 3.12+) | Core: UI, API, scheduler, config gen, OAuth2, auth | Yes |
| **mbsync** | Custom (Alpine + mbsync) | Executes sync on request from core | Yes |
| **dovecot** | Custom/official | Exposes Maildir via IMAP read-only, namespaces per account | No |
| **webmail** | Snappymail or Roundcube | Read-only email interface | No |

## Storage

### Maildir Volumes

One volume per email account, user-configurable paths. Enables independent backup strategies per mailbox at NAS level.

```yaml
# Example docker-compose volumes
volumes:
  - /mnt/nas1/gmail:/data/mailboxes/gmail
  - /mnt/nas2/work:/data/mailboxes/work
```

### Config Volume

Dedicated volume for application config (SQLite DB when used, generated configs, secrets).

## Database

**SQLAlchemy** as ORM, supporting two backends:

- **SQLite** (default) — zero-config, single file on config volume. Best for Docker Compose deployments.
- **PostgreSQL** — for Kubernetes deployments or shared DB setups. Connect via `DATABASE_URL` environment variable.

Schema migrations via **Alembic**.

### Data Model

**Table `users`:**

| Column | Type | Description |
|---|---|---|
| id | UUID | Primary key |
| username | string | Login username |
| password_hash | string | bcrypt hash (null if SSO-only) |
| role | enum | `admin` / `user` |
| oidc_subject | string | OIDC subject claim (for SSO users) |

**Table `accounts`:**

| Column | Type | Description |
|---|---|---|
| id | UUID | Primary key |
| name | string | Display name ("Gmail personale", "Work") |
| imap_host | string | e.g. `imap.gmail.com` |
| imap_port | int | e.g. 993 |
| auth_type | enum | `oauth2` / `app_password` |
| credentials | encrypted blob | OAuth2 tokens or app password, encrypted at-rest |
| maildir_path | string | Local Maildir path (e.g. `/data/mailboxes/gmail`) |
| sync_schedule | string | Cron expression (e.g. `*/30 * * * *`) |
| sync_state | enum | `idle` / `syncing` / `error` |
| last_sync_at | datetime | Last successful sync |
| last_error | text | Last error message |
| enabled | bool | Account active/inactive |

**Table `account_owners`:**

| Column | Type | Description |
|---|---|---|
| account_id | FK → accounts | |
| user_id | FK → users | |

An account can have multiple owners (shared mailbox scenario).

**Table `sync_jobs`:**

| Column | Type | Description |
|---|---|---|
| id | UUID | Job ID |
| account_id | FK → accounts | Account to sync |
| status | enum | `pending` / `running` / `completed` / `failed` |
| source | string | `scheduler`, `api`, `webhook` |
| requested_at | datetime | When requested |
| started_at | datetime | When started |
| completed_at | datetime | When finished |
| exit_code | int | mbsync exit code |
| log | text | Captured stdout+stderr |

## Sync Engine

### Job Queue Pattern

All sync triggers (scheduler, API, future webhooks) produce jobs by inserting rows into `sync_jobs` with status `pending`. A background async worker consumes them.

- **Concurrency**: one sync at a time per account (lock per account), parallel across different accounts
- **Deduplication**: if a `pending` or `running` job exists for the same account, new requests are discarded
- **Config generation**: each sync generates a temporary `.mbsyncrc` for the target account
- **Execution**: `subprocess.run(["mbsync", "-c", config_path, "-a"])`, capturing output and exit code

### Scheduling

APScheduler with jobs persisted in the database. Each account has its own cron expression. Survives restarts.

## Authentication & Authorization

### Authentication Methods

- **Username/password**: bcrypt hashed, built-in
- **SSO via OpenID Connect**: compatible with Authentik or any OIDC provider. Role mapping from OIDC claims/groups (configurable group names, e.g. `mailfallback-admin`, `mailfallback-user`)

Library: `authlib` for OIDC integration.

### Roles & Permissions

| Action | admin | user |
|---|---|---|
| View all accounts | Yes | No, own only |
| Create/delete accounts | Yes | No |
| Assign accounts to users | Yes | No |
| Modify own account config | Yes | Yes |
| Trigger manual sync (own) | Yes | Yes |
| Access webmail (own) | Yes | Yes |
| View global logs/metrics | Yes | No |
| Manage users | Yes | No |

Admin can self-assign accounts for debug purposes.

## Webmail Integration

### Dovecot Configuration

- **Read-only mode**: no write operations, backup integrity preserved
- **Namespaces**: one Dovecot namespace per assigned mailbox per user. All mailboxes appear as top-level folders under a single IMAP login:

```
Gmail-Personale/
  ├── INBOX
  ├── Sent
  └── ...
Lavoro/
  ├── INBOX
  ├── Sent
  └── ...
```

- **User mapping**: the core generates Dovecot virtual user config based on `account_owners` associations
- **Credential generation**: the core generates/manages Dovecot credentials; users authenticate to webmail with their mailfallback credentials

### Webmail Choice

Snappymail or Roundcube — to be decided during implementation. Both support IMAP and work well with Dovecot namespaces. Snappymail is lighter; Roundcube has a richer plugin ecosystem.

## UI — HTMX + Jinja2

### Pages

| Page | Path | Function |
|---|---|---|
| Dashboard | `/` | Overview: all accounts (filtered by role), sync status, next sync, recent errors |
| Account setup | `/accounts/new` | Wizard: IMAP host, credentials, maildir path, schedule |
| Account detail | `/accounts/{id}` | Detail: sync history, logs, config edit |
| Manual sync | (button in UI) | Triggers `POST /api/sync/{id}`, live status feedback |
| Settings | `/settings` | Global config, notifications, config export/import |
| User management | `/admin/users` | Admin only: create users, assign accounts |

### Interactivity

- Sync status updates via HTMX polling (`hx-trigger="every 5s"`) or SSE
- "Sync now" button: POST + panel refresh without page reload
- Live log streaming during active sync

### Styling

Classless CSS framework (Pico CSS or Simple.css) — clean look, zero build step.

## REST API

### Sync Endpoints

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/sync/{account_id}` | Trigger sync (creates job) |
| `GET` | `/api/sync/jobs/{job_id}` | Job status and log |
| `GET` | `/api/sync/jobs?account_id=X` | Job history for account |

### Config Endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/config/export` | Export full config as YAML/JSON |
| `POST` | `/api/config/import` | Import config from YAML/JSON |

### Health & Metrics

| Method | Path | Description |
|---|---|---|
| `GET` | `/healthz` | Liveness probe |
| `GET` | `/readyz` | Readiness probe (DB connected, mbsync available) |
| `GET` | `/metrics` | Prometheus format metrics |

### Prometheus Metrics

- `mailfallback_sync_total{account, status}` — sync counter per account
- `mailfallback_sync_duration_seconds{account}` — last sync duration
- `mailfallback_sync_last_success_timestamp{account}` — last successful sync timestamp
- `mailfallback_maildir_size_bytes{account}` — Maildir size per account
- `mailfallback_accounts_total` — total configured accounts
- `mailfallback_jobs_pending` — pending jobs in queue

Library: `prometheus-client`.

## OAuth2 — Gmail and Other Providers

### Flow

1. User navigates to `/accounts/new`, selects Gmail
2. Core redirects to Google OAuth consent screen
3. User authorizes, Google redirects back with authorization code
4. Core exchanges code for access + refresh tokens
5. Tokens encrypted and stored in DB
6. On each sync, core refreshes access token and generates `.mbsyncrc` with `PassCmd` pointing to a helper that outputs the token

### Prerequisites

User must create a Google Cloud project with OAuth credentials (client ID + secret). The setup wizard in the UI guides this process step by step.

### Other Providers

- Outlook, Yahoo: OAuth2 where supported
- Generic IMAP: app password fallback, manual host/port/credentials config

### Security

- All tokens/passwords encrypted at-rest with `MAILFALLBACK_SECRET_KEY` (env var or K8s Secret)
- No tokens in logs or generated config files
- Refresh tokens rotated when provider supports it

## Deployment

### Docker Compose (Default)

Single `docker-compose.yml`. SQLite default DB. Users map volumes for each mailbox.

### Kubernetes / Helm

Helm chart based on bjw-s-labs common chart. PostgreSQL via `DATABASE_URL`. Maildir paths as PVC mounts. Probes configured to `/healthz` and `/readyz`.

## Config Portability

- `GET /api/config/export` and `POST /api/config/import` for YAML/JSON config backup
- Bidirectional sync with K8s Secrets/ConfigMaps deferred to v2

## Future Considerations (Out of Scope for v1)

- K8s Secret/ConfigMap bidirectional sync
- Email notifications on sync failure
- Multi-provider OAuth2 token management UI
- Webmail choice finalization (Snappymail vs Roundcube)
- Full-text search across backed-up email
