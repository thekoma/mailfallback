<p align="center">
  <img src="src/mailfallback/static/favicon.svg" alt="MFB Logo" width="128">
</p>

<h1 align="center">MailFallBack</h1>
<p align="center"><strong>MFB</strong> — Your email safety net</p>

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-yellow.svg" alt="License: MIT"></a>
  <a href="https://github.com/thekoma/mailfallback/actions/workflows/ci.yml"><img src="https://github.com/thekoma/mailfallback/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <a href="https://github.com/thekoma/mailfallback/actions/workflows/release.yml"><img src="https://github.com/thekoma/mailfallback/actions/workflows/release.yml/badge.svg" alt="Release"></a>
  <a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/python-3.14+-blue.svg" alt="Python 3.14+"></a>
  <a href="https://github.com/thekoma/mailfallback/pkgs/container/mailfallback"><img src="https://img.shields.io/badge/docker-ghcr.io%2Fthekoma%2Fmailfallback-blue?logo=docker" alt="Docker"></a>
</p>

<p align="center">
  <strong><a href="https://thekoma.github.io/mailfallback/">📖 Full documentation</a></strong> — installation guides, admin guide, architecture, security model
</p>

---

Self-hosted email safety net with a web UI. Wraps [mbsync/isync](https://isync.sourceforge.io/) to keep a local sync of your mailboxes, optionally pushes encrypted off-site backups to S3 or local storage via restic, and provides read-only IMAP access via Dovecot as a fallback in case you lose access to your provider.

<p align="center">
  <img src="docs/screenshots/02-dashboard.png" alt="MailFallBack dashboard" width="900">
</p>

## Why

Cloud email providers can lock you out without warning. If your entire digital life depends on a single Gmail account, one accidental lockout means losing 20+ years of correspondence. MailFallBack gives you an independent local sync — plus optional off-site, encrypted snapshots — with a web interface to reach your mail again.

## Features

### Local sync
- Automated IMAP sync via **mbsync** with configurable cron schedules
- **Multi-account** support with independent storage volumes (mail stores)
- **Daily sync budget** per account (provider-aware default, e.g. Gmail's ~2 GB/day) with automatic throttle backoff and ETA on first sync
- **Watchdog** reaper — detects stalled/crashed sync jobs and recovers them automatically
- **Groups** — share mailbox access across multiple users without duplicating accounts
- **Store migration** — move users between mail stores with progress tracking and crash recovery
- **Job queue** with deduplication — triggers from scheduler or API
- Sync history with logs, exit codes, and failure classification (throttled/transient states self-recover; only real errors are flagged)

### Off-site backup (optional)
- Push encrypted **snapshots** to a **repository** (local disk or S3) via [restic](https://restic.net/)
- Per-mailbox backup policy: schedule, retention preset (light/standard/full) or custom retention
- **Recover** a snapshot into a new, suspended mailbox for inspection before reactivating it
- Attach pre-existing restic prefixes in a repository to an account (orphan detection)

### Notifications
- **Apprise**-backed notification channels — 100+ services (email, Slack, Discord, ntfy, ...) via a single URL scheme
- Per-channel event subscriptions: re-auth needed, sync error, sync paused, stale mailbox, plus activity events (sync completed, restore completed, off-site backup completed, account added)
- **Text or structured JSON** payload per channel — the JSON envelope carries a plain account object plus per-event details, ready for webhook-style consumers

### Restore
- **Restore staging workspace** — search across mailboxes, stage selected messages, then push them back to any mailbox
- **Attachment content search** (optional) — full-text search inside attachment contents via Apache Tika, in addition to headers/body

### Authentication
- **Google OAuth2** for Gmail (no app passwords needed)
- **Microsoft OAuth2** for Outlook/Live/Hotmail
- **App password** fallback for any IMAP provider
- **OIDC/SSO** login (Authentik, Keycloak, or any OpenID Connect provider)
- Role-based access: **admin** (manages accounts/users) and **user** (manages own mailboxes)
- Credentials encrypted at rest with Fernet (AES-128-CBC + HMAC-SHA256)

### Web Interface
- Clean dashboard with live sync status (HTMX polling)
- **Dark mode** with one-click toggle (persisted per-user, no flash on reload)
- Account management: create, edit, disable/enable, delete
- Per-folder mailbox statistics (messages, unread, size) via Dovecot doveadm API
- User management and settings pages (admin)
- **Audit log** — tracks admin and account operations (who did what, when, from where)
- Responsive design with Pico CSS and Lucide icons

### Read-Only IMAP Access (Optional)
- **Dovecot 2.4** exposes the local sync's Maildir via IMAP in read-only mode
- SQL auth against the MFB database — no separate user management
- Login automatically blocked during store migrations
- Compatible with **Roundcube** (bundled, optional profile) or any IMAP client

### Monitoring
- `/healthz` — liveness probe
- `/readyz` — readiness probe (database connectivity)
- `/metrics` — Prometheus format (sync counters, durations, maildir sizes, queue depth)

### Deployment
- **Docker Compose** with PostgreSQL, the mailfallback app, and Dovecot; optional `webmail` (Roundcube) and `tika` (attachment content search) profiles
- **Kubernetes** with the official Helm chart (see [Deploy on Kubernetes](#deploy-on-kubernetes-helm))
- Config **export/import** API for portability between deployments
- Multi-architecture Docker images (amd64 + arm64)

### API
- `POST /api/sync/{account_id}` — trigger sync
- `GET /api/sync/jobs/{job_id}` — job status
- `GET /api/sync/jobs?account_id=X` — job history
- `GET /api/config/export` — export config (admin)
- `POST /api/config/import` — import config (admin)
- `PATCH /api/accounts/{id}` — update account
- `DELETE /api/accounts/{id}` — delete account (admin)
- `PATCH /api/preferences` — update user preferences (theme)

## Quick Start

### Docker Compose

The repo's [`docker-compose.yml`](docker-compose.yml) is the source of truth — clone it and go:

```bash
git clone https://github.com/thekoma/mailfallback.git
cd mailfallback
cp .env.example .env
# edit .env — at minimum set MAILFALLBACK_SECRET_KEY and MAILFALLBACK_SESSION_SECRET

docker compose up -d --build
```

This starts three services: **PostgreSQL 18**, the **mailfallback** app, and **Dovecot 2.4.2** (read-only IMAP fallback). Two optional Compose profiles add more:

```bash
# Roundcube webmail on http://localhost:8001 (also set MAILFALLBACK_WEBMAIL_ENABLED=true
# and MAILFALLBACK_WEBMAIL_URL=http://localhost:8001 in .env)
docker compose --profile webmail up -d --build

# Apache Tika for attachment content search in the restore workspace
# (also set MAILFALLBACK_TIKA_ENABLED=true in .env)
docker compose --profile tika up -d --build
```

Open `http://localhost:8000` — default login: `admin` / `changeme1234!` (you'll be forced to change it on first login).

Connect an IMAP client to `localhost:31143` (plaintext) to read the local sync's mail.

### From Source

**Requires Python 3.14+.** That is the version the published container is built
on (`docker/Dockerfile` uses `python:3.14-slim`) and the only version CI tests,
so it is the only one supported. `uv` installs it for you if it is missing.

```bash
git clone https://github.com/thekoma/mailfallback.git
cd mailfallback
uv sync --dev
uv run alembic upgrade head
uv run uvicorn mailfallback.app:app --host 0.0.0.0 --port 8000
```

### Deploy on Kubernetes (Helm)

The official chart is published as an OCI artifact on GHCR. Chart version equals
the app version (CalVer), so always pass `--version` explicitly:

```bash
helm install mailfallback oci://ghcr.io/thekoma/charts/mailfallback \
  --version 2026.07.4 -n mailfallback --create-namespace \
  -f values.yaml
```

> **Note:** version-less tag discovery does **not** work with the CalVer scheme
> (Helm can't semver-match `2026.07.x`) — always pin `--version`.

See the [chart README](charts/mailfallback/README.md) for the full step-by-step
guide (external PostgreSQL, secrets, storage, exposure, upgrades).

## Configuration

All settings via environment variables with `MAILFALLBACK_` prefix:

| Variable | Default | Description |
|---|---|---|
| `MAILFALLBACK_DATABASE_URL` | `postgresql://mailfallback:mailfallback@db:5432/mailfallback` | PostgreSQL connection string | <!-- pragma: allowlist secret -->
| `MAILFALLBACK_SECRET_KEY` | `change-me-in-production` | Key for encrypting stored credentials |
| `MAILFALLBACK_SESSION_SECRET` | `change-me-session-secret` | Session cookie signing key |
| `MAILFALLBACK_SESSION_HTTPS_ONLY` | `false` | Require HTTPS for session cookies |
| `MAILFALLBACK_DEBUG` | `false` | Enable debug mode (persists mbsync configs to `/tmp/mbsync/`) |
| `MAILFALLBACK_DOVECOT_API_URL` | `http://dovecot:8080` | Dovecot doveadm HTTP API URL |
| `MAILFALLBACK_DOVECOT_API_KEY` | | Dovecot doveadm API password |
| `MAILFALLBACK_WEBMAIL_ENABLED` | `false` | Show the Webmail link and generate the Roundcube config (use with the `webmail` Compose profile) |
| `MAILFALLBACK_WEBMAIL_URL` | | Webmail URL shown in the nav bar (e.g. `http://localhost:8001`) |
| `MAILFALLBACK_TIKA_ENABLED` | `false` | Enable attachment content search via Apache Tika (use with the `tika` Compose profile) |
| `MAILFALLBACK_OIDC_ENABLED` | `false` | Enable OIDC/SSO login |
| `MAILFALLBACK_OIDC_CLIENT_ID` | | OIDC client ID |
| `MAILFALLBACK_OIDC_CLIENT_SECRET` | | OIDC client secret |
| `MAILFALLBACK_OIDC_DISCOVERY_URL` | | OIDC discovery endpoint |
| `MAILFALLBACK_OIDC_ADMIN_GROUP` | `mailfallback-admin` | OIDC group mapped to admin role |
| `MAILFALLBACK_OIDC_USER_GROUP` | `mailfallback-user` | OIDC group mapped to user role |
| `MAILFALLBACK_GOOGLE_CLIENT_ID` | | Google OAuth2 client ID (for Gmail) |
| `MAILFALLBACK_GOOGLE_CLIENT_SECRET` | | Google OAuth2 client secret (for Gmail) |
| `MAILFALLBACK_MICROSOFT_CLIENT_ID` | | Microsoft OAuth2 client ID (for Outlook/Live/Hotmail) |
| `MAILFALLBACK_MICROSOFT_CLIENT_SECRET` | | Microsoft OAuth2 client secret |
| `MAILFALLBACK_MICROSOFT_TENANT` | `consumers` | Microsoft tenant (`consumers`, `common`, `organizations`) |

## OAuth2 Setup Guides

Most major email providers now require OAuth2 for IMAP access. MFB supports both Google and Microsoft OAuth2 natively — the form auto-detects the provider from your email address.

| Provider | Domains | Guide |
|----------|---------|-------|
| **Google** | gmail.com, googlemail.com | [Google OAuth2 Setup](docs/guides/google-oauth2-setup.md) |
| **Microsoft** | outlook.com, live.it, live.com, hotmail.com | [Microsoft OAuth2 Setup](docs/guides/microsoft-oauth2-setup.md) |
| **Others** | Any IMAP provider | App password or generic OAuth2 |

## Dovecot / TLS

Dovecot runs with **SSL disabled by default** (`ssl = no` in `mfb-ssl.conf`). This is safe when Dovecot is only accessed over a trusted internal network or via a reverse proxy that terminates TLS.

To enable IMAPS (port 31993):

1. Mount your certificate and key into the Dovecot container:
   ```yaml
   volumes:
     - ./certs/tls.crt:/etc/dovecot/ssl/tls.crt:ro
     - ./certs/tls.key:/etc/dovecot/ssl/tls.key:ro
   ```

2. Override `mfb-ssl.conf` to enable SSL:
   ```
   ssl = yes
   ssl_cert = </etc/dovecot/ssl/tls.crt
   ssl_key = </etc/dovecot/ssl/tls.key
   ```

3. Expose the IMAPS port:
   ```yaml
   ports:
     - "31993:31993"
   ```

**Kubernetes patterns**: Use cert-manager to provision certificates, or terminate TLS at the ingress/load-balancer level and keep Dovecot plaintext internally.

## Security

- All credentials (passwords, OAuth2 tokens) are **encrypted at rest** using Fernet symmetric encryption (AES-128-CBC + HMAC-SHA256)
- The encryption key is derived from `MAILFALLBACK_SECRET_KEY` via SHA-256
- **Never commit or expose** `MAILFALLBACK_SECRET_KEY` — use environment variables or Kubernetes Secrets
- Session cookies are signed with `MAILFALLBACK_SESSION_SECRET`
- Passwords are hashed with **bcrypt**
- Dovecot runs in **read-only mode** to preserve the local sync's integrity

## Architecture

```
┌──────────────────────────────────────────────────────────┐
│                  mailfallback container                    │
│  FastAPI + HTMX/Jinja2 + SQLAlchemy + APScheduler         │
│                                                            │
│  ┌───────────┐  ┌────────────┐  ┌──────────────────┐      │
│  │ Scheduler │─>│ Job Queue  │<─│ REST API         │      │
│  │ (cron)    │  │ (DB table) │  │ POST /api/sync/… │      │
│  └───────────┘  └─────┬──────┘  └──────────────────┘      │
│                       │                                    │
│                       v                                    │
│                ┌─────────────┐                             │
│                │ Sync Worker │                             │
│                │ subprocess  │                             │
│                └──────┬──────┘                             │
└───────────────────────┼────────────────────────────────────┘
                        │
         ┌──────────────┼──────────────┐
         v              v              v
   ┌──────────┐  ┌───────────────┐  ┌───────────┐
   │  mbsync  │  │   Dovecot     │  │  Webmail  │
   │          │  │ (separate     │  │(optional) │
   └────┬─────┘  │  container)   │  └─────^─────┘
        │        └──────^────────┘        │
        v               │           IMAP │
   ┌─────────┐          │                │
   │ Maildir │──────────┘────────────────┘
   │ volumes │
   └─────────┘
        │
   ┌─────────┐
   │PostgreSQL│
   └─────────┘
```

## Tech Stack

- **Backend**: Python 3.14+, FastAPI, SQLAlchemy, Alembic, APScheduler
- **Frontend**: Jinja2, HTMX, Pico CSS, Lucide icons
- **Database**: PostgreSQL 18
- **Sync**: mbsync/isync
- **Off-site backup**: restic (S3 or local-disk repositories)
- **Notifications**: Apprise
- **Content search**: Apache Tika (optional, attachment content)
- **IMAP access**: Dovecot 2.4 (official image, SQL auth)
- **Auth**: bcrypt, Fernet, authlib (OIDC + Google/Microsoft OAuth2)
- **Monitoring**: prometheus-client

## Development

```bash
git clone https://github.com/thekoma/mailfallback.git
cd mailfallback
uv sync --dev
uv run pytest tests/ -v
uv run ruff check src/ tests/
```

## License

[MIT](LICENSE)
