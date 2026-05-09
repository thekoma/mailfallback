# Webmail

MailFallBack integrates Roundcube as an optional webmail interface for browsing backed-up mail in the browser.

## Accessing Webmail

When webmail is enabled, a "Webmail" link appears in the MFB sidebar navigation. Click it to open Roundcube in a new tab.

### Login

Log in to Roundcube with the same credentials as MFB:

- **Username**: your MFB username
- **Password**: your MFB password

If SSO is configured, a "Login with [provider]" button appears on the Roundcube login page for OAuth2 authentication.

## Read-Only Mode

Roundcube is configured as a read-only mail viewer. Dovecot ACLs enforce the following:

| Action | Allowed |
|--------|---------|
| Read messages | Yes |
| Mark as read/unread | Yes |
| Search messages | Yes |
| Download attachments | Yes |
| Delete messages | No |
| Move messages | No |
| Send mail | No |
| Create/rename folders | No |

!!! note "Why read-only?"
    MFB is a backup service. Allowing modifications through webmail would conflict with the next mbsync run, potentially causing sync conflicts or data loss. The backup should be an immutable reference copy.

## Folder Visibility

Roundcube lists all folders from all accounts you have access to:

- **Accounts you own** appear directly, with the first account as your inbox namespace
- **Group-shared accounts** appear with a prefix showing the account name and email

Roundcube uses `LIST` instead of `LSUB` (subscriptions disabled), so all folders appear automatically without needing to subscribe.

## Full-Text Search

Roundcube's search bar uses Dovecot's FTS engine. When FTS Flatcurve is active (it is by default), searching looks through indexed message headers and bodies.

When Apache Tika is also enabled, search extends to attachment content:

- PDF documents
- Microsoft Office files (Word, Excel, PowerPoint)
- Plain text files
- HTML content
- And many other formats Tika supports

!!! tip "Search syntax"
    Roundcube sends search queries to Dovecot via IMAP SEARCH. To search all message content including attachments, use the "Entire message" search scope in Roundcube's search options.

## SSO Login

To enable single sign-on for Roundcube through your OIDC provider (e.g. Authentik):

1. Create a separate OAuth2 provider/application in your identity provider for Roundcube
2. Set the redirect URI to `http://your-roundcube-url/index.php/login/oauth`
3. Configure these environment variables:

```ini
MAILFALLBACK_WEBMAIL_OAUTH_CLIENT_ID=your-roundcube-oauth-client-id
MAILFALLBACK_WEBMAIL_OAUTH_CLIENT_SECRET=your-roundcube-oauth-secret
MAILFALLBACK_WEBMAIL_OAUTH_AUTH_URI=https://sso.example.com/application/o/authorize/
MAILFALLBACK_WEBMAIL_OAUTH_TOKEN_URI=https://sso.example.com/application/o/token/
MAILFALLBACK_WEBMAIL_OAUTH_IDENTITY_URI=https://sso.example.com/application/o/userinfo/
```

MFB generates the Roundcube OAuth2 configuration from these variables at boot.

## Enabling Webmail

See the [Installation guide](../getting-started/installation.md) for how to start the webmail profile. In summary:

```bash
# Add to .env
MAILFALLBACK_WEBMAIL_ENABLED=true
MAILFALLBACK_WEBMAIL_URL=http://localhost:8001

# Start with webmail profile
docker compose --profile webmail up -d
```

## Plugins

Roundcube is configured with these plugins:

- **archive** - archive button (read-only, so limited use)
- **zipdownload** - download multiple messages or attachments as a ZIP file
- **subscriptions_option** - allows toggling folder subscriptions in the UI
