<p align="center">
  <img src="src/mailfallback/static/logo.svg" alt="MFB Logo" width="128">
</p>

<h1 align="center">MailFallBack</h1>
<p align="center"><strong>MFB</strong> — Your email safety net</p>

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-yellow.svg" alt="License: MIT"></a>
  <a href="https://github.com/thekoma/mailfallback/actions/workflows/ci.yml"><img src="https://github.com/thekoma/mailfallback/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <a href="https://github.com/thekoma/mailfallback/actions/workflows/release.yml"><img src="https://github.com/thekoma/mailfallback/actions/workflows/release.yml/badge.svg" alt="Release"></a>
  <a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/python-3.12+-blue.svg" alt="Python 3.12+"></a>
  <a href="https://github.com/thekoma/mailfallback/pkgs/container/mailfallback"><img src="https://img.shields.io/badge/docker-ghcr.io%2Fthekoma%2Fmailfallback-blue?logo=docker" alt="Docker"></a>
</p>

---

Self-hosted email backup service with a web UI. Wraps [mbsync/isync](https://isync.sourceforge.io/) to back up your email and provides read-only IMAP access via Dovecot as a fallback in case you lose access to your provider.

## Why

Cloud email providers can lock you out without warning. If your entire digital life depends on a single Gmail account, one accidental lockout means losing 20+ years of correspondence. MailFallBack gives you an independent, encrypted backup with a web interface to access it.

## Features

### Email Backup
- Automated IMAP backup via **mbsync** with configurable cron schedules
- **Multi-account** support with independent storage volumes (mail stores)
- **Store migration** — move users between stores with progress tracking and crash recovery
- **Job queue** with deduplication — triggers from scheduler, API, or future webhooks
- Sync history with logs, exit codes, and error tracking

### Authentication
- **Google OAuth2** for Gmail (no app passwords needed)
- **Microsoft OAuth2** for Outlook/Live/Hotmail
- **App password** fallback for any IMAP provider
- **OIDC/SSO** login (Authentik, Keycloak, or any OpenID Connect provider)
- Role-based access: **admin** (manages accounts/users) and **user** (manages own mailboxes)
- Credentials encrypted at rest with Fernet (AES-128-CBC + HMAC-SHA256)

### Web Interface
- Clean dashboard with live sync status (HTMX polling)
- Account management: create, edit, disable/enable, delete
- Per-folder mailbox statistics (messages, unread, size) via Dovecot doveadm API
- User management and settings pages (admin)
- Responsive design with Pico CSS and Lucide icons

### Read-Only IMAP Access (Optional)
- **Dovecot 2.4** exposes backed-up Maildir via IMAP in read-only mode
- SQL auth against the MFB database — no separate user management
- Login automatically blocked during store migrations
- Compatible with **Snappymail**, **Roundcube**, or any IMAP client

### Monitoring
- `/healthz` — liveness probe
- `/readyz` — readiness probe (database connectivity)
- `/metrics` — Prometheus format (sync counters, durations, maildir sizes, queue depth)

### Deployment
- **Docker Compose** with PostgreSQL (3 services: db, mailfallback, dovecot)
- **Kubernetes** with Helm (bjw-s-labs charts)
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

## Quick Start

### Docker Compose

Create a `.env` file:

```bash
MAILFALLBACK_SECRET_KEY=your-random-secret-here
MAILFALLBACK_SESSION_SECRET=your-session-secret-here
MAILFALLBACK_DOVECOT_API_KEY=your-dovecot-api-key
MAILFALLBACK_DOVECOT_ENABLED=true
```

```yaml
services:
  db:
    image: postgres:16-alpine
    environment:
      POSTGRES_USER: mailfallback
      POSTGRES_PASSWORD: mailfallback
      POSTGRES_DB: mailfallback
    volumes:
      - pgdata:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U mailfallback"]
      interval: 5s
      timeout: 3s
      retries: 5

  mailfallback:
    image: ghcr.io/thekoma/mailfallback:latest
    ports:
      - "8000:8000"
    volumes:
      - maildirs:/data/mailboxes
    env_file: .env
    environment:
      MAILFALLBACK_DATABASE_URL: postgresql://mailfallback:mailfallback@db:5432/mailfallback  # pragma: allowlist secret
    depends_on:
      db:
        condition: service_healthy

  dovecot:
    image: dovecot/dovecot:latest
    volumes:
      - ./docker/dovecot/conf.d:/etc/dovecot/conf.d:ro
      - maildirs:/data/mailboxes
    ports:
      - "31143:31143"
    environment:
      DOVECOT_DB_HOST: db
      DOVECOT_DB_PORT: "5432"
      DOVECOT_DB_NAME: mailfallback
      DOVECOT_DB_USER: mailfallback
      DOVECOT_DB_PASSWORD: mailfallback
      DOVECOT_API_KEY: ${MAILFALLBACK_DOVECOT_API_KEY:-changeme}
    depends_on:
      db:
        condition: service_healthy

volumes:
  pgdata:
  maildirs:
```

```bash
docker compose up -d
```

Open `http://localhost:8000` — default login: `admin` / `changeme`.

Connect an IMAP client to `localhost:31143` (plaintext) to read backed-up mail.

### From Source

```bash
git clone https://github.com/thekoma/mailfallback.git
cd mailfallback
uv sync --dev
uv run alembic upgrade head
uv run uvicorn mailfallback.app:app --host 0.0.0.0 --port 8000
```

## Configuration

All settings via environment variables with `MAILFALLBACK_` prefix:

| Variable | Default | Description |
|---|---|---|
| `MAILFALLBACK_DATABASE_URL` | `postgresql://mailfallback:mailfallback@db:5432/mailfallback` | PostgreSQL connection string | <!-- pragma: allowlist secret -->
| `MAILFALLBACK_SECRET_KEY` | `change-me-in-production` | Key for encrypting stored credentials |
| `MAILFALLBACK_SESSION_SECRET` | `change-me-session-secret` | Session cookie signing key |
| `MAILFALLBACK_SESSION_HTTPS_ONLY` | `false` | Require HTTPS for session cookies |
| `MAILFALLBACK_DEBUG` | `false` | Enable debug mode (persists mbsync configs to `/tmp/mbsync/`) |
| `MAILFALLBACK_DOVECOT_ENABLED` | `false` | Enable Dovecot integration (stats, reload) |
| `MAILFALLBACK_DOVECOT_API_URL` | `http://dovecot:8080` | Dovecot doveadm HTTP API URL |
| `MAILFALLBACK_DOVECOT_API_KEY` | | Dovecot doveadm API password |
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
- Dovecot runs in **read-only mode** to preserve backup integrity

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

- **Backend**: Python 3.12+, FastAPI, SQLAlchemy, Alembic, APScheduler
- **Frontend**: Jinja2, HTMX, Pico CSS, Lucide icons
- **Database**: PostgreSQL
- **Sync**: mbsync/isync
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
