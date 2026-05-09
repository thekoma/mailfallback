# Configuration

All configuration is done through environment variables. MFB uses the `MAILFALLBACK_` prefix for its own settings. Database credentials shared by multiple services use `DB_` prefix without the `MAILFALLBACK_` prefix.

## Core Settings

| Variable | Default | Description |
|----------|---------|-------------|
| `MAILFALLBACK_SECRET_KEY` | `change-me-in-production` | Encryption key for storing credentials. Must be a strong random string. |
| `MAILFALLBACK_SESSION_SECRET` | `change-me-session-secret` | Secret for signing session cookies. Must be a strong random string. |
| `MAILFALLBACK_SESSION_HTTPS_ONLY` | `false` | Set to `true` to require HTTPS for session cookies. Enable this behind a TLS-terminating reverse proxy. |
| `MAILFALLBACK_DEBUG` | `false` | Enable debug logging. When `true`, persists mbsync configs to `/tmp/mbsync/` for inspection. |
| `MAILFALLBACK_BOOTSTRAP_STORE_PATH` | `/data/mailboxes` | Path for the default mail store created on first boot. |
| `MAILFALLBACK_CONFS_PATH` | `/confs` | Base directory where generated Dovecot and Roundcube configs are written. |
| `MAILFALLBACK_MBSYNC_BINARY` | `mbsync` | Path to the mbsync binary. |

## Database

| Variable | Default | Description |
|----------|---------|-------------|
| `MAILFALLBACK_DATABASE_URL` | `postgresql://user:pass@db:5432/mailfallback` | Full PostgreSQL connection URL. Set by docker-compose.yml automatically. Override when using an external database. |  <!-- pragma: allowlist secret -->
| `DB_USER` | `mailfallback` | PostgreSQL username. Used by docker-compose.yml to configure the database container and build the connection URL. |
| `DB_PASSWORD` | `mailfallback` | PostgreSQL password. Shared across all services. |
| `DB_NAME` | `mailfallback` | PostgreSQL database name. |
| `MAILFALLBACK_DB_HOST` | `db` | Database hostname. Used internally for Dovecot SQL auth config generation. |
| `MAILFALLBACK_DB_PORT` | `5432` | Database port. Used internally for Dovecot SQL auth config generation. |

!!! note "PostgreSQL is the only supported database"
    MFB requires PostgreSQL. SQLite is used only in the test suite. Do not attempt to run production with any other database backend.

## Sync Settings

| Variable | Default | Description |
|----------|---------|-------------|
| `MAILFALLBACK_SYNC_MAX_WORKERS` | `4` | Maximum number of concurrent mbsync processes. |
| `MAILFALLBACK_SYNC_LOG_DIR` | `/data/logs/sync` | Directory where sync job logs are stored. |

## OIDC / SSO

| Variable | Default | Description |
|----------|---------|-------------|
| `MAILFALLBACK_OIDC_ENABLED` | `false` | Enable OIDC single sign-on. |
| `MAILFALLBACK_OIDC_CLIENT_ID` | _(empty)_ | OAuth2 client ID from your identity provider. |
| `MAILFALLBACK_OIDC_CLIENT_SECRET` | _(empty)_ | OAuth2 client secret. |
| `MAILFALLBACK_OIDC_DISCOVERY_URL` | _(empty)_ | OIDC discovery URL (e.g. `https://sso.example.com/application/o/mailfallback/.well-known/openid-configuration`). |
| `MAILFALLBACK_OIDC_ADMIN_GROUP` | `mailfallback-admin` | OIDC group claim value that grants admin role. |
| `MAILFALLBACK_OIDC_USER_GROUP` | `mailfallback-user` | OIDC group claim value for regular users. |
| `MAILFALLBACK_OIDC_USERINFO_URL` | _(empty)_ | UserInfo endpoint URL. Also used for Dovecot OAuth2 token validation. |

See the [SSO/OIDC guide](../admin-guide/sso.md) for complete setup instructions.

## OAuth Providers (Email Backup)

These credentials allow MFB to authenticate with email providers via OAuth2 for backing up Gmail and Outlook accounts.

### Google

| Variable | Default | Description |
|----------|---------|-------------|
| `MAILFALLBACK_GOOGLE_CLIENT_ID` | _(empty)_ | Google OAuth2 client ID. |
| `MAILFALLBACK_GOOGLE_CLIENT_SECRET` | _(empty)_ | Google OAuth2 client secret. |

### Microsoft

| Variable | Default | Description |
|----------|---------|-------------|
| `MAILFALLBACK_MICROSOFT_CLIENT_ID` | _(empty)_ | Microsoft/Azure OAuth2 application ID. |
| `MAILFALLBACK_MICROSOFT_CLIENT_SECRET` | _(empty)_ | Microsoft OAuth2 client secret. |
| `MAILFALLBACK_MICROSOFT_TENANT` | `consumers` | Azure AD tenant. Use `consumers` for personal accounts (Outlook.com, Hotmail), `organizations` for work/school accounts, or a specific tenant ID. |

See the [OAuth Providers guide](../admin-guide/oauth-providers.md) for setup instructions.

## Dovecot Integration

| Variable | Default | Description |
|----------|---------|-------------|
| `MAILFALLBACK_DOVECOT_API_URL` | `http://dovecot:8080` | URL for the Dovecot doveadm HTTP API. Used for mailbox stats and reload commands. |
| `MAILFALLBACK_DOVECOT_API_KEY` | _(empty)_ | API key for authenticating with the doveadm HTTP API. Must match the `DOVEADM_PASSWORD` set on the Dovecot container. |
| `MAILFALLBACK_DOVECOT_IMAP_HOST` | `dovecot` | Hostname for connecting to Dovecot IMAP. Used during restore operations. |
| `MAILFALLBACK_DOVECOT_IMAP_PORT` | `31143` | IMAP port for Dovecot. |

## Webmail (Roundcube)

| Variable | Default | Description |
|----------|---------|-------------|
| `MAILFALLBACK_WEBMAIL_ENABLED` | `false` | Enable webmail integration. |
| `MAILFALLBACK_WEBMAIL_URL` | _(empty)_ | Public URL for Roundcube (e.g. `http://localhost:8001`). When set, a "Webmail" link appears in the MFB sidebar. |
| `MAILFALLBACK_WEBMAIL_OAUTH_CLIENT_ID` | _(empty)_ | OAuth2 client ID for Roundcube SSO login. |
| `MAILFALLBACK_WEBMAIL_OAUTH_CLIENT_SECRET` | _(empty)_ | OAuth2 client secret for Roundcube SSO. |
| `MAILFALLBACK_WEBMAIL_OAUTH_AUTH_URI` | _(empty)_ | OAuth2 authorization endpoint for Roundcube SSO. |
| `MAILFALLBACK_WEBMAIL_OAUTH_TOKEN_URI` | _(empty)_ | OAuth2 token endpoint for Roundcube SSO. |
| `MAILFALLBACK_WEBMAIL_OAUTH_IDENTITY_URI` | _(empty)_ | OAuth2 userinfo endpoint for Roundcube SSO. |

## Full-Text Search (Tika)

| Variable | Default | Description |
|----------|---------|-------------|
| `MAILFALLBACK_TIKA_ENABLED` | `false` | Enable Apache Tika for attachment content indexing. |
| `MAILFALLBACK_TIKA_URL` | `http://tika:9998` | URL for the Apache Tika server. |

## Metrics

| Variable | Default | Description |
|----------|---------|-------------|
| `MAILFALLBACK_METRICS_API_KEY` | _(empty)_ | API key for the `/metrics` endpoint. When set, Prometheus must send this key in the `Authorization: Bearer <key>` header. When empty, the metrics endpoint is unprotected. |

## Example .env File

```ini
# Security - generate with: openssl rand -hex 32
MAILFALLBACK_SECRET_KEY=your-random-secret-key
MAILFALLBACK_SESSION_SECRET=your-random-session-secret

# Database
DB_USER=mailfallback
DB_PASSWORD=a-strong-database-password
DB_NAME=mailfallback

# OIDC (optional)
# MAILFALLBACK_OIDC_ENABLED=true
# MAILFALLBACK_OIDC_CLIENT_ID=your-client-id
# MAILFALLBACK_OIDC_CLIENT_SECRET=your-client-secret
# MAILFALLBACK_OIDC_DISCOVERY_URL=https://sso.example.com/.well-known/openid-configuration

# Dovecot
MAILFALLBACK_DOVECOT_API_KEY=your-dovecot-api-key

# Webmail (optional)
# MAILFALLBACK_WEBMAIL_ENABLED=true
# MAILFALLBACK_WEBMAIL_URL=http://localhost:8001

# Tika (optional)
# MAILFALLBACK_TIKA_ENABLED=true

# Metrics (optional)
# MAILFALLBACK_METRICS_API_KEY=your-metrics-key
```
