# OAuth Providers

MFB supports OAuth2 authentication for backing up Gmail and Microsoft (Outlook, Hotmail, Live) email accounts. This is separate from OIDC/SSO for MFB login - these OAuth2 credentials allow MFB to authenticate with email providers on behalf of users to pull their mail.

## Google OAuth2 (Gmail)

### 1. Create a Google Cloud Project

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a new project or select an existing one
3. Enable the **Gmail API** under **APIs & Services > Library**

### 2. Configure the OAuth Consent Screen

1. Go to **APIs & Services > OAuth consent screen**
2. Select **External** user type (or Internal if using Google Workspace)
3. Fill in the application name, support email, and developer contact
4. Add the scope: `https://mail.google.com/` (full IMAP access)
5. Add your email as a test user (required while the app is in testing mode)

### 3. Create OAuth2 Credentials

1. Go to **APIs & Services > Credentials**
2. Click **Create Credentials > OAuth client ID**
3. Select **Web application**
4. Set the **Authorized redirect URI** to: `http://your-mfb-url/auth/google/callback`
5. Note the **Client ID** and **Client Secret**

### 4. Configure MFB

Add to your `.env`:

```ini
MAILFALLBACK_GOOGLE_CLIENT_ID=your-client-id.apps.googleusercontent.com
MAILFALLBACK_GOOGLE_CLIENT_SECRET=your-client-secret
```

Restart MFB:

```bash
docker compose restart mailfallback
```

### 5. Usage

When adding a Gmail account:

1. Select "Google OAuth2" as the auth type
2. Click "Authorize with Google"
3. Sign in to Google and grant IMAP access
4. MFB stores the refresh token for automatic renewal

!!! note "App verification"
    Google requires app verification for production OAuth2 apps. While in testing mode, only users listed as test users in the consent screen can authorize. For personal/self-hosted use, you can stay in testing mode indefinitely.

## Microsoft OAuth2 (Outlook / Hotmail / Live / Microsoft 365)

### 1. Register an Application in Azure

1. Go to [Azure Portal > App registrations](https://portal.azure.com/#blade/Microsoft_AAD_RegisteredApps/ApplicationsListBlade)
2. Click **New registration**
3. Set the **Name** (e.g. "MailFallBack")
4. Set **Supported account types**:
    - For personal accounts (Outlook.com, Hotmail): **Personal Microsoft accounts only**
    - For work/school accounts: **Accounts in any organizational directory**
    - For both: **Accounts in any organizational directory and personal Microsoft accounts**
5. Set the **Redirect URI** (Web): `http://your-mfb-url/auth/microsoft/callback`
6. Click **Register**

### 2. Configure API Permissions

1. Go to **API permissions**
2. Click **Add a permission > Microsoft Graph**
3. Select **Delegated permissions**
4. Add these permissions:
    - `IMAP.AccessAsUser.All`
    - `offline_access`
    - `User.Read`
5. Click **Grant admin consent** if you are an admin

### 3. Create a Client Secret

1. Go to **Certificates & secrets**
2. Click **New client secret**
3. Set a description and expiry period
4. Note the **Value** (this is your client secret, shown only once)

### 4. Configure MFB

Add to your `.env`:

```ini
MAILFALLBACK_MICROSOFT_CLIENT_ID=your-application-id
MAILFALLBACK_MICROSOFT_CLIENT_SECRET=your-client-secret-value
MAILFALLBACK_MICROSOFT_TENANT=consumers
```

The `MAILFALLBACK_MICROSOFT_TENANT` value determines which account types can authenticate:

| Tenant | Account Types |
|--------|--------------|
| `consumers` | Personal accounts only (Outlook.com, Hotmail, Live) |
| `organizations` | Work/school accounts only |
| `common` | Both personal and work/school |
| `{tenant-id}` | Specific Azure AD tenant |

Restart MFB:

```bash
docker compose restart mailfallback
```

### 5. Usage

When adding an Outlook/Hotmail account:

1. Select "Microsoft OAuth2" as the auth type
2. Click "Authorize with Microsoft"
3. Sign in to your Microsoft account and grant access
4. MFB stores the refresh token for automatic renewal

!!! warning "Client secret expiry"
    Microsoft client secrets expire (1 year or 2 years maximum). Set a reminder to rotate the secret before it expires, or your OAuth2-authenticated accounts will fail to sync.

## Token Management

For both providers, MFB:

- Stores the refresh token encrypted in the database (using `MAILFALLBACK_SECRET_KEY`)
- Automatically requests new access tokens when the current one expires
- Access tokens are short-lived (typically 1 hour) and never stored
- If a refresh token is revoked or expires, the account shows an authentication error and the user must re-authorize

## Redirect URIs

Summary of redirect URIs to configure in each provider:

| Provider | Redirect URI |
|----------|-------------|
| Google | `http(s)://your-mfb-url/auth/google/callback` |
| Microsoft | `http(s)://your-mfb-url/auth/microsoft/callback` |

!!! tip "HTTPS in production"
    Both Google and Microsoft require HTTPS redirect URIs in production. For local development, `http://localhost:8000` is allowed. When deploying behind a reverse proxy with TLS, use the public HTTPS URL.
