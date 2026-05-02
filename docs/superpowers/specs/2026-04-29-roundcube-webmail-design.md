# Roundcube Webmail Integration

Read-only webmail access to backed-up email via Roundcube, connecting to the existing Dovecot IMAP server.

## Context

MailFallBack backs up IMAP mailboxes to local Maildir and provides read-only IMAP access via Dovecot. Users currently need a desktop IMAP client to read their backed-up mail. Adding a browser-based webmail client completes the "fallback" story — if a provider locks you out, you can read your mail from any browser.

## Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Webmail client | Roundcube (`roundcube/roundcubemail:latest`) | Well-maintained official image, PostgreSQL support, responsive UI |
| Database | Shared `mailfallback` PostgreSQL DB | Roundcube prefixes tables with `rc_`, no collision. One DB to backup. |
| Read-only enforcement | Dovecot ACL plugin only | Single point of enforcement at IMAP level. Blocks all write ops regardless of client. |
| SMTP | Not configured | No sending capability. Roundcube compose UI remains visible but send will fail. |
| Authentication | Manual login (same MFB username/password) | Simplest. SSO via Authentik can be added later. |
| Port | `8001` (host) → `80` (container) | MFB on 8000, Roundcube on 8001. In K8s, Ingress handles routing. |
| UI access | Global "Webmail" link in nav bar | One user sees all their accounts' mail under one Dovecot login. |
| Feature toggle | `MAILFALLBACK_WEBMAIL_URL` env var | Empty = link hidden. Set to Roundcube URL = link visible. |

## Architecture

```
Browser
  |
  +---> MFB (FastAPI :8000) --- manages accounts, triggers sync
  |
  +---> Roundcube (:8001) ---> Dovecot IMAP (:31143) ---> Maildir
                |                     |
                +--- PostgreSQL <-----+
                     (shared DB)
```

Roundcube connects to Dovecot via IMAP inside the Docker network. Dovecot authenticates against the `users` table in PostgreSQL. Roundcube stores its own state (sessions, cache, contacts) in the same database with `rc_` prefixed tables.

mbsync writes directly to the Maildir filesystem — unaffected by Dovecot ACLs.

## Components

### 1. Roundcube service (docker-compose.yml)

New service added to `docker-compose.yml`:

```yaml
roundcube:
  image: roundcube/roundcubemail:latest
  ports:
    - "8001:80"
  environment:
    ROUNDCUBEMAIL_DEFAULT_HOST: dovecot
    ROUNDCUBEMAIL_DEFAULT_PORT: "31143"
    ROUNDCUBEMAIL_DB_TYPE: pgsql
    ROUNDCUBEMAIL_DB_HOST: db
    ROUNDCUBEMAIL_DB_PORT: "5432"
    ROUNDCUBEMAIL_DB_USER: mailfallback
    ROUNDCUBEMAIL_DB_PASSWORD: mailfallback
    ROUNDCUBEMAIL_DB_NAME: mailfallback
    ROUNDCUBEMAIL_SKIN: elastic
    ROUNDCUBEMAIL_PLUGINS: archive,zipdownload
  volumes:
    - roundcube_data:/var/roundcube/config
  depends_on:
    db:
      condition: service_healthy
    dovecot:
      condition: service_started
```

Volume `roundcube_data` persists Roundcube's generated config (DES key, etc.) across restarts.

### 2. Dovecot ACL configuration (docker/dovecot/conf.d/mfb-acl.conf)

New config file enabling read-only IMAP access:

```dovecot
mail_plugins = acl

protocol imap {
  mail_plugins = $mail_plugins imap_acl
}

plugin {
  acl_driver = vfile
  acl_globals_only = yes
}
```

Global ACL granting only lookup, read, and write-seen rights. The `acl_globals_only = yes` prevents users from overriding via per-mailbox ACL files.

Rights granted:
- `l` — list/lookup mailboxes
- `r` — read message content
- `s` — set `\Seen` flag (mark as read)

Rights denied (by omission):
- `w` — write other flags
- `t` — write `\Deleted` flag
- `i` — insert/copy messages
- `e` — expunge messages
- `k` — create mailboxes
- `x` — delete mailboxes
- `a` — administer ACLs

The exact Dovecot 2.4 syntax for applying global ACL defaults needs verification against the running image, as the inline `mailbox * { acl owner { rights = lrs } }` syntax may differ from file-based global ACLs. Implementation should test both approaches.

### 3. MFB config and UI changes

**config.py** — new setting:

```python
webmail_url: str = ""
```

Env var: `MAILFALLBACK_WEBMAIL_URL`. Default empty (feature disabled).

**base.html** — conditional nav link:

When `webmail_url` is set, render a "Webmail" link in the nav bar between "Add Account" and the admin links. Opens in a new tab (`target="_blank"`). Uses Lucide `mail` icon with `icon-nav` class.

**routers/ui.py** — pass `webmail_url` to template context:

The `webmail_url` setting needs to be available in all page renders that use `base.html`. Add it to the template context in the relevant routes or via a middleware/dependency.

## What does NOT change

- No custom Dockerfile for Roundcube
- No changes to MFB models or database schema
- No Alembic migration needed
- No changes to mbsync, sync worker, or scheduler
- No changes to Dovecot auth queries or mail location
- No SMTP configuration

## Future enhancements (out of scope)

- SSO via Authentik (Roundcube `oauth2` plugin + Dovecot `oauthbearer`)
- Disable Roundcube compose UI via custom PHP config
- Magic link auto-login via Dovecot master user
