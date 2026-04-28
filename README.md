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

Self-hosted email backup service with a web UI. Wraps [mbsync/isync](https://isync.sourceforge.io/) to back up your email and provides read-only webmail access via Dovecot as a fallback in case you lose access to your provider.

## Why

Cloud email providers can lock you out without warning. If your entire digital life depends on a single Gmail account, one accidental lockout means losing 20+ years of correspondence. Mailfallback gives you an independent, encrypted backup with a web interface to access it.

## Features

### Email Backup
- Automated IMAP backup via **mbsync** with configurable cron schedules
- **Multi-account** support with independent storage volumes per mailbox
- **Job queue** with deduplication — triggers from scheduler, API, or future webhooks
- Sync history with logs, exit codes, and error tracking

### Authentication
- **Google OAuth2** for Gmail (no app passwords needed)
- **App password** fallback for any IMAP provider
- **OIDC/SSO** login (Authentik, Keycloak, or any OpenID Connect provider)
- Role-based access: **admin** (manages accounts/users) and **user** (manages own mailboxes)
- Credentials encrypted at rest with Fernet (AES-128-CBC + HMAC-SHA256)

### Web Interface
- Clean dashboard with live sync status (HTMX polling)
- Account management: create, edit, disable/enable, delete (with email confirmation)
- **Lucide icons** throughout, **Google logo** for OAuth2 accounts
- User management and settings pages (admin)
- Responsive design with Pico CSS

### Read-Only Webmail (Optional)
- **Dovecot** exposes backed-up Maildir via IMAP in read-only mode
- **Namespaces** — multiple mailboxes appear as top-level folders under one login
- Compatible with **Snappymail** or **Roundcube**

### Monitoring
- `/healthz` — liveness probe
- `/readyz` — readiness probe (database connectivity)
- `/metrics` — Prometheus format (sync counters, durations, maildir sizes, queue depth)

### Deployment
- **Docker Compose** with SQLite (zero-config default)
- **Kubernetes** with Helm (bjw-s-labs charts), PostgreSQL via `DATABASE_URL`
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

```yaml
services:
  mailfallback:
    image: ghcr.io/thekoma/mailfallback:latest
    ports:
      - "8000:8000"
    volumes:
      - config:/data/config
      - /mnt/nas/gmail:/data/mailboxes/gmail
      - /mnt/nas/work:/data/mailboxes/work
    environment:
      - MAILFALLBACK_SECRET_KEY=your-random-secret-here
      - MAILFALLBACK_SESSION_SECRET=your-session-secret-here
      # For PostgreSQL (recommended for Kubernetes):
      # - MAILFALLBACK_DATABASE_URL=postgresql://user:pass@db:5432/mailfallback

volumes:
  config:
```

```bash
docker compose up -d
```

Open `http://localhost:8000` — default login: `admin` / `changeme`.

### From Source

```bash
pip install -e ".[dev]"
alembic upgrade head
uvicorn mailfallback.app:app --host 0.0.0.0 --port 8000
```

## Configuration

All settings via environment variables with `MAILFALLBACK_` prefix:

| Variable | Default | Description |
|---|---|---|
| `MAILFALLBACK_DATABASE_URL` | `sqlite:///data/config/mailfallback.db` | Database connection string |
| `MAILFALLBACK_SECRET_KEY` | `change-me-in-production` | Key for encrypting stored credentials |
| `MAILFALLBACK_SESSION_SECRET` | `change-me-session-secret` | Session cookie signing key |
| `MAILFALLBACK_OIDC_ENABLED` | `false` | Enable OIDC/SSO login |
| `MAILFALLBACK_OIDC_CLIENT_ID` | | OIDC client ID |
| `MAILFALLBACK_OIDC_CLIENT_SECRET` | | OIDC client secret |
| `MAILFALLBACK_OIDC_DISCOVERY_URL` | | OIDC discovery endpoint |
| `MAILFALLBACK_OIDC_ADMIN_GROUP` | `mailfallback-admin` | OIDC group mapped to admin role |
| `MAILFALLBACK_OIDC_USER_GROUP` | `mailfallback-user` | OIDC group mapped to user role |
| `MAILFALLBACK_GOOGLE_CLIENT_ID` | | Google OAuth2 client ID (for Gmail) |
| `MAILFALLBACK_GOOGLE_CLIENT_SECRET` | | Google OAuth2 client secret (for Gmail) |

## Google OAuth2 Setup (Gmail)

Gmail requires OAuth2 for IMAP access. Here's how to set it up:

### 1. Create a Google Cloud Project

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a new project (or select an existing one)
3. Enable the **Gmail API**:
   - Navigate to **APIs & Services > Library**
   - Search for "Gmail API" and click **Enable**

### 2. Configure OAuth Consent Screen

1. Go to **APIs & Services > OAuth consent screen**
2. Choose **External** user type (or Internal if using Google Workspace)
3. Fill in the required fields:
   - **App name**: Mailfallback
   - **User support email**: your email
   - **Developer contact**: your email
4. Add scope: `https://mail.google.com/`
5. Add your Google account as a **test user** (required while in "Testing" status)

### 3. Create OAuth2 Credentials

1. Go to **APIs & Services > Credentials**
2. Click **Create Credentials > OAuth client ID**
3. Application type: **Web application**
4. **Authorized redirect URIs**: add your Mailfallback URL:
   ```
   http://localhost:8000/auth/google/callback
   ```
   For production, use your actual domain:
   ```
   https://mailfallback.yourdomain.com/auth/google/callback
   ```
5. Copy the **Client ID** and **Client Secret**

### 4. Configure Mailfallback

Set the environment variables:

```bash
MAILFALLBACK_GOOGLE_CLIENT_ID=your-client-id.apps.googleusercontent.com
MAILFALLBACK_GOOGLE_CLIENT_SECRET=your-client-secret
```

### 5. Add a Gmail Account

1. Login to Mailfallback as admin
2. Click **Add Account**
3. Fill in:
   - **Account Name**: Gmail Personal
   - **Email Address**: your.email@gmail.com
   - **IMAP Host**: imap.gmail.com
   - **Authentication**: OAuth2 (Gmail)
4. Click **Create Account**
5. You'll be redirected to Google — authorize access
6. Done! The account is now configured with OAuth2 tokens

The tokens are encrypted at rest using `MAILFALLBACK_SECRET_KEY` and automatically refreshed when they expire.

> **Note**: While your Google Cloud project is in "Testing" status, OAuth tokens expire after 7 days. Publish the app to remove this limitation (Google review required).

## Security

- All credentials (passwords, OAuth2 tokens) are **encrypted at rest** using Fernet symmetric encryption (AES-128-CBC + HMAC-SHA256)
- The encryption key is derived from `MAILFALLBACK_SECRET_KEY` via SHA-256
- **Never commit or expose** `MAILFALLBACK_SECRET_KEY` — use environment variables or Kubernetes Secrets
- Session cookies are signed with `MAILFALLBACK_SESSION_SECRET`
- Passwords are hashed with **bcrypt**
- Dovecot runs in **read-only mode** to preserve backup integrity

## Architecture

```
┌──────────────────────────────────────────────────────┐
│                 mailfallback core                     │
│  FastAPI + HTMX/Jinja2 + SQLAlchemy + APScheduler    │
│                                                      │
│  ┌───────────┐  ┌────────────┐  ┌──────────────────┐ │
│  │ Scheduler │─>│ Job Queue  │<─│ REST API         │ │
│  │ (cron)    │  │ (DB table) │  │ POST /api/sync/… │ │
│  └───────────┘  └─────┬──────┘  └──────────────────┘ │
│                       │                               │
│                       v                               │
│                ┌─────────────┐                        │
│                │ Sync Worker │                        │
│                │ subprocess  │                        │
│                └──────┬──────┘                        │
└───────────────────────┼──────────────────────────────┘
                        │
         ┌──────────────┼──────────────┐
         v              v              v
   ┌──────────┐  ┌───────────┐  ┌───────────┐
   │  mbsync  │  │  Dovecot  │  │  Webmail  │
   │          │  │ read-only │  │(optional) │
   └────┬─────┘  └─────^─────┘  └─────^─────┘
        │               │              │
        v               │         IMAP │
   ┌─────────┐          │              │
   │ Maildir │──────────┘──────────────┘
   │ volumes │
   └─────────┘
```

## Tech Stack

- **Backend**: Python 3.12+, FastAPI, SQLAlchemy, Alembic, APScheduler
- **Frontend**: Jinja2, HTMX, Pico CSS, Lucide icons
- **Database**: SQLite (default) or PostgreSQL
- **Sync**: mbsync/isync
- **Auth**: bcrypt, Fernet, authlib (OIDC + OAuth2)
- **Monitoring**: prometheus-client

## Development

```bash
git clone https://github.com/thekoma/mailfallback.git
cd mailfallback
pip install -e ".[dev]"
pytest tests/ -v
ruff check src/ tests/
```

## License

[MIT](LICENSE)
