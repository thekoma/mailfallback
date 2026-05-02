# Microsoft OAuth2 Setup for Outlook/Live/Hotmail

Microsoft **deprecated basic password authentication** for IMAP in September 2024. All Outlook.com, Live.it, Live.com, and Hotmail accounts now **require OAuth2** for IMAP access. App passwords are unreliable and being phased out entirely by April 2026.

MFB supports Microsoft OAuth2 natively.

## Prerequisites

- A Microsoft account (outlook.com, live.it, live.com, hotmail.com, etc.)
- Access to [Microsoft Entra Admin Center](https://entra.microsoft.com/) (free with any Microsoft account)

## Step 1: Enable IMAP on Your Account

> Do this first — OAuth2 won't help if IMAP is disabled.

1. Go to [outlook.live.com](https://outlook.live.com/) and sign in
2. Click **Settings** (gear icon) → **View all Outlook settings**
3. Go to **Mail** → **Sync email**
4. Under **POP and IMAP**, toggle **"Let devices and apps use IMAP"** to **ON**
5. Click **Save**

## Step 2: Register an Application in Entra

1. Go to [entra.microsoft.com](https://entra.microsoft.com/) and sign in
2. Navigate to **Identity** → **Applications** → **App registrations**
3. Click **New registration**
4. Fill in:
   - **Name**: MailFallBack
   - **Supported account types**: select **"Accounts in any organizational directory (Any Microsoft Entra ID tenant - Multitenant) and personal Microsoft accounts (e.g. Skype, Xbox)"**
   - **Redirect URI**: select **Web** and enter:
     ```
     http://localhost:8000/auth/microsoft/callback
     ```
     For production:
     ```
     https://mfb.yourdomain.com/auth/microsoft/callback
     ```
5. Click **Register**
6. On the overview page, copy the **Application (client) ID**

## Step 3: Create a Client Secret

1. In your app registration, go to **Certificates & secrets**
2. Click **New client secret**
3. **Description**: MFB
4. **Expires**: 24 months (recommended)
5. Click **Add**
6. **Copy the Value immediately** — you won't be able to see it again

## Step 4: About API Permissions

### For personal accounts (outlook.com, live.it, hotmail.com)

**You do NOT need to add any API permissions manually in the portal.** The IMAP scope (`https://outlook.office.com/IMAP.AccessAsUser.All`) is requested directly during the OAuth authorization flow, and the user grants consent interactively when they sign in.

The default permission **User.Read** (added automatically) is sufficient in the portal. The IMAP permission will appear in the consent screen when the user authorizes MFB.

### For work/school accounts (optional)

If you also want to support organizational accounts:

1. Go to **API permissions** → **Add a permission**
2. Select the tab **APIs my organization uses**
3. Search for **"Office 365 Exchange Online"**
4. Select **Delegated permissions**
5. Check **IMAP.AccessAsUser.All**
6. Click **Add permissions**
7. Click **Grant admin consent for [your org]** (requires admin)

> **Note**: "Office 365 Exchange Online" only appears for accounts that belong to an organization (Microsoft 365 tenant). If you only use personal accounts, skip this step entirely.

## Step 5: Configure MFB

Add to your `.env` file:

```env
MAILFALLBACK_MICROSOFT_CLIENT_ID=12345678-abcd-1234-efgh-1234567890ab
MAILFALLBACK_MICROSOFT_CLIENT_SECRET=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
MAILFALLBACK_MICROSOFT_TENANT=consumers
```

The `MICROSOFT_TENANT` setting controls which accounts can authenticate:

| Value | Who can sign in |
|-------|-----------------|
| `consumers` | Personal Microsoft accounts only (outlook.com, live.it, hotmail.com) — **use this for personal accounts** |
| `common` | Both personal and work/school accounts |
| `organizations` | Work/school accounts only |
| `{tenant-id}` | Specific organization only |

Restart MFB:

```bash
docker compose up -d --build
```

## Step 6: Add an Outlook/Live/Hotmail Account in MFB

1. Log in to MFB
2. Click **Add Account**
3. Enter your email address (e.g., `user@live.it`) — MFB auto-detects Microsoft and switches to OAuth2
4. IMAP settings are auto-filled and locked (outlook.office365.com:993/IMAPS)
5. Click **Create Account**
6. You'll be redirected to Microsoft — sign in and click **Accept**
7. You're redirected back to MFB — the account is configured with OAuth2 tokens

## How It Works

```
User clicks "Create Account"
    │
    ▼
MFB redirects to Microsoft:
    https://login.microsoftonline.com/consumers/oauth2/v2.0/authorize
    ?scope=https://outlook.office.com/IMAP.AccessAsUser.All offline_access
    │
    ▼
User signs in at Microsoft and clicks "Accept"
    │
    ▼
Microsoft redirects back with authorization code:
    http://localhost:8000/auth/microsoft/callback?code=M.R3_BAY...
    │
    ▼
MFB exchanges code for tokens (access + refresh)
    │
    ▼
Tokens encrypted and stored in database
    │
    ▼
On each sync: refresh token → fresh access token → mbsync XOAUTH2
```

## Token Lifecycle

| Token | Lifetime | Renewal |
|-------|----------|---------|
| Access token | 1 hour | Automatic via refresh token before each sync |
| Refresh token | 90 days (extended on each use) | Re-authorize if expired |

As long as MFB syncs at least once within 90 days, the refresh token stays valid indefinitely.

## IMAP Connection Details

| Setting | Value |
|---------|-------|
| Server | outlook.office365.com |
| Port | 993 |
| Encryption | SSL/TLS (IMAPS) |
| Auth mechanism | XOAUTH2 |
| Username | Your full email address |

## Troubleshooting

### "AADSTS50011: The redirect URI does not match"
The redirect URI in Entra must match **exactly**:
- `http://localhost:8000/auth/microsoft/callback` (no trailing slash)
- Check HTTP vs HTTPS

### "AADSTS7000218: The request body must contain client_assertion or client_secret"
`MAILFALLBACK_MICROSOFT_CLIENT_SECRET` is not set or is incorrect in your `.env`.

### "AADSTS700016: Application not found in the directory"
The `MAILFALLBACK_MICROSOFT_TENANT` doesn't match your account type:
- Personal accounts → use `consumers`
- Work/school accounts → use `common` or your tenant ID

### "AADSTS65001: The user or administrator has not consented"
For consumer accounts, try:
1. Go to [account.microsoft.com/consent](https://account.microsoft.com/consent)
2. Remove the MFB app
3. Re-authorize in MFB

### "Office 365 Exchange Online" not found in API permissions
**This is normal for personal accounts.** You don't need to add it manually — the IMAP scope is requested in the OAuth URL and the user grants it during sign-in. See [Step 4](#step-4-about-api-permissions).

### "AUTHENTICATE failed" during sync
- Refresh token may have expired (90 days without use)
- Re-authorize the account from its detail page in MFB

### IMAP is disabled
Go to Outlook.com settings → Mail → Sync email → Enable IMAP. Some accounts may have this disabled by default.

## Microsoft vs Google OAuth2

| Aspect | Google | Microsoft |
|--------|--------|-----------|
| Console | console.cloud.google.com | entra.microsoft.com |
| API permissions | Add Gmail API scope manually | Not needed for personal accounts |
| Scope | `https://mail.google.com/` | `https://outlook.office.com/IMAP.AccessAsUser.All` |
| Endpoint | Single | `/consumers`, `/common`, `/organizations` |
| Token refresh lifetime | 6 months | 90 days (extended on use) |
| Testing mode limits | 7-day token expiry | None |

## Key Dates

| Date | What happened |
|------|---------------|
| September 2024 | Basic auth disabled for Outlook.com IMAP |
| March 2026 | SMTP basic auth begins retirement |
| April 2026 | SMTP basic auth completely disabled |

## References

- [Authenticate IMAP/POP/SMTP using OAuth - Microsoft Docs](https://learn.microsoft.com/en-us/exchange/client-developer/legacy-protocols/how-to-authenticate-an-imap-pop-smtp-application-by-using-oauth)
- [Register an application - Microsoft Entra](https://learn.microsoft.com/en-us/azure/active-directory/develop/quickstart-register-app)
- [OAuth 2.0 authorization code flow](https://learn.microsoft.com/en-us/entra/identity-platform/v2-oauth2-auth-code-flow)
- [POP/IMAP settings for Outlook.com](https://support.microsoft.com/en-us/office/pop-imap-and-smtp-settings-for-outlook-com-d088b986-291d-42b8-9564-9c414e2aa040)
