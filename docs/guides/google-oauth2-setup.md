# Google OAuth2 Setup for Gmail

MFB uses Google OAuth2 to access Gmail via IMAP. This avoids app passwords and provides secure, token-based authentication.

## Prerequisites

- A Google account
- Access to [Google Cloud Console](https://console.cloud.google.com/)

## Step 1: Create a Google Cloud Project

1. Go to [console.cloud.google.com](https://console.cloud.google.com/)
2. Click the project dropdown (top bar) → **New Project**
3. Name it (e.g., "MailFallBack") → **Create**
4. Select the new project from the dropdown

## Step 2: Enable the Gmail API

1. Go to **APIs & Services** → **Library**
2. Search for **"Gmail API"**
3. Click on it → **Enable**

## Step 3: Configure OAuth Consent Screen

1. Go to **APIs & Services** → **OAuth consent screen**
2. Select **External** → **Create**
3. Fill in:
   - **App name**: MailFallBack
   - **User support email**: your email
   - **Developer contact**: your email
4. Click **Save and Continue**
5. **Scopes** → **Add or Remove Scopes** → search `https://mail.google.com/` → check it → **Update** → **Save and Continue**
6. **Test users** → **Add Users** → add your Gmail address → **Save and Continue**
7. **Summary** → **Back to Dashboard**

> **Note**: While in "Testing" status, OAuth tokens expire after 7 days. To remove this limit, click **Publish App** (requires Google review for apps with sensitive scopes).

## Step 4: Create OAuth2 Credentials

1. Go to **APIs & Services** → **Credentials**
2. Click **Create Credentials** → **OAuth client ID**
3. **Application type**: Web application
4. **Name**: MailFallBack
5. **Authorized redirect URIs** → **Add URI**:
   ```
   http://localhost:8000/auth/google/callback
   ```
   For production:
   ```
   https://mfb.yourdomain.com/auth/google/callback
   ```
6. Click **Create**
7. Copy the **Client ID** and **Client Secret**

## Step 5: Configure MFB

Add to your `.env` file:

```env
MAILFALLBACK_GOOGLE_CLIENT_ID=123456789-xxxxxx.apps.googleusercontent.com
MAILFALLBACK_GOOGLE_CLIENT_SECRET=GOCSPX-xxxxxxxxxxxxxxxx
```

Restart MFB:

```bash
docker compose up -d --build
```

## Step 6: Add a Gmail Account

1. Log in to MFB as admin or user
2. Click **Add Account**
3. Enter your Gmail address — the form auto-detects Gmail and switches to OAuth2
4. IMAP settings are auto-filled and locked (imap.gmail.com:993/IMAPS)
5. Click **Create Account**
6. You'll be redirected to Google — sign in and click **Allow**
7. You're redirected back to MFB — the account is configured

## How It Works

1. MFB redirects you to Google's consent screen
2. You authorize MFB to read your email
3. Google returns an authorization code
4. MFB exchanges the code for access + refresh tokens
5. Tokens are encrypted with `MAILFALLBACK_SECRET_KEY` and stored in the database
6. On each sync, MFB uses the refresh token to get a fresh access token
7. mbsync uses the token via `PassCmd` with the XOAUTH2 mechanism

## Token Lifecycle

| Token | Lifetime | Renewal |
|-------|----------|---------|
| Access token | 1 hour | Automatic via refresh token |
| Refresh token | 6 months (or until revoked) | Re-authorize if expired |

> **Testing mode limit**: Refresh tokens expire after 7 days unless the app is published.

## Troubleshooting

### "Access blocked: This app's request is invalid"
- Check that the redirect URI in Google Console matches exactly (including trailing slash)

### "Error 403: access_denied"
- Make sure your Gmail address is added as a **Test user** in the OAuth consent screen

### Token expired after 7 days
- Publish your Google Cloud app or re-authorize the account

### Sync fails with "AUTHENTICATE failed"
- The refresh token may have expired — go to the account detail page and click the OAuth2 re-authorize link
