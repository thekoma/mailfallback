# SSO / OIDC

MailFallBack supports OpenID Connect (OIDC) for single sign-on. This allows users to log in with their existing identity provider credentials instead of managing separate MFB passwords.

## Supported Providers

MFB works with any OIDC-compliant identity provider, including:

- **Authentik** (recommended, tested)
- **Keycloak**
- **Authelia**
- **Azure AD / Entra ID**
- **Okta**
- **Google Workspace**

## Setup with Authentik

This guide uses Authentik as the identity provider. Adapt the steps for your provider.

### 1. Create an OAuth2 Provider

In Authentik:

1. Go to **Applications > Providers > Create**
2. Select **OAuth2/OpenID Connect**
3. Set the **Name** (e.g. "MailFallBack")
4. Set the **Authorization flow** to your default authorization flow
5. Set the **Client type** to "Confidential"
6. Note the generated **Client ID** and **Client Secret**
7. Add the **Redirect URI**: `http://your-mfb-url/auth/oidc/callback`
8. Under **Advanced protocol settings**, ensure the `groups` scope is included

### 2. Create an Application

1. Go to **Applications > Applications > Create**
2. Set the **Name** (e.g. "MailFallBack")
3. Select the provider you created
4. Set the **Launch URL** to your MFB URL

### 3. Configure Groups

Create two groups in Authentik (or your existing groups):

- **mailfallback-admin** - users in this group get the admin role in MFB
- **mailfallback-user** - users in this group get the user role in MFB

Users not in either group will be denied access.

!!! tip "Custom group names"
    You can use any group names. Configure them with `MAILFALLBACK_OIDC_ADMIN_GROUP` and `MAILFALLBACK_OIDC_USER_GROUP`.

### 4. Configure MFB

Add these variables to your `.env` file:

```ini
MAILFALLBACK_OIDC_ENABLED=true
MAILFALLBACK_OIDC_CLIENT_ID=your-client-id
MAILFALLBACK_OIDC_CLIENT_SECRET=your-client-secret
MAILFALLBACK_OIDC_DISCOVERY_URL=https://sso.example.com/application/o/mailfallback/.well-known/openid-configuration
MAILFALLBACK_OIDC_ADMIN_GROUP=mailfallback-admin
MAILFALLBACK_OIDC_USER_GROUP=mailfallback-user
MAILFALLBACK_OIDC_USERINFO_URL=https://sso.example.com/application/o/userinfo/
```

Restart MFB:

```bash
docker compose restart mailfallback
```

### 5. Test Login

Go to the MFB login page. A "Login with SSO" button should appear. Click it to be redirected to your identity provider for authentication.

## User Auto-Provisioning

When a user logs in via OIDC for the first time:

1. A new MFB user is created with the OIDC subject as identifier
2. The username is taken from the `preferred_username` claim
3. The role is set based on group membership
4. The user is assigned to the default mail store
5. No password is set (the user authenticates via OIDC only)

On subsequent logins, the user's role is updated if their group membership has changed.

## Group Synchronization

MFB groups with `sso_sync` enabled automatically update their membership based on OIDC claims:

1. On each SSO login, MFB reads the `groups` claim from the ID token or userinfo endpoint
2. For each SSO-synced MFB group whose name matches a claim value, the user is added as a member
3. For groups whose name does not appear in the claims, the user is removed

This provides automatic, provider-driven access control to shared email accounts.

## Dovecot OAuth2 (Webmail SSO)

To enable SSO login for Roundcube webmail, you need a separate OAuth2 provider/application in your identity provider. Dovecot validates OAuth2 tokens against the userinfo endpoint.

Set the userinfo URL:

```ini
MAILFALLBACK_OIDC_USERINFO_URL=https://sso.example.com/application/o/userinfo/
```

This URL is used in the generated Dovecot configuration for token validation.

See also: [Webmail SSO setup](../user-guide/webmail.md)

## Troubleshooting

### "Access denied" after SSO login

The user is not a member of either the admin or user group. Check group membership in your identity provider.

### User created with wrong role

Group claims are evaluated on each login. Ensure the `groups` scope is included in the OAuth2 provider configuration and that the group names match exactly (case-sensitive).

### Discovery URL not working

The discovery URL must be the full `.well-known/openid-configuration` URL. MFB fetches the authorization, token, and userinfo endpoints from this document.

### OIDC callback error

Verify the redirect URI in your identity provider matches `http(s)://your-mfb-url/auth/oidc/callback` exactly, including the protocol and path.
