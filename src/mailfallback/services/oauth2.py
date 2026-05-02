# src/mailfallback/services/oauth2.py
from authlib.integrations.httpx_client import AsyncOAuth2Client

from mailfallback.config import settings

# --- Google ---

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_SCOPES = ["https://mail.google.com/"]


def get_google_oauth_client(redirect_uri: str) -> AsyncOAuth2Client:
    return AsyncOAuth2Client(
        client_id=settings.google_client_id,
        client_secret=settings.google_client_secret,
        redirect_uri=redirect_uri,
        scope=" ".join(GOOGLE_SCOPES),
    )


def build_google_auth_url(redirect_uri: str, state: str) -> str:
    client = get_google_oauth_client(redirect_uri)
    url, _ = client.create_authorization_url(
        GOOGLE_AUTH_URL,
        state=state,
        access_type="offline",
        prompt="select_account consent",
    )
    return url


async def exchange_google_code(code: str, redirect_uri: str) -> dict:
    client = get_google_oauth_client(redirect_uri)
    token = await client.fetch_token(GOOGLE_TOKEN_URL, code=code)
    await client.aclose()
    return token


async def refresh_google_token(refresh_token: str) -> str:
    client = get_google_oauth_client("")
    token = await client.fetch_token(
        GOOGLE_TOKEN_URL,
        grant_type="refresh_token",
        refresh_token=refresh_token,
    )
    await client.aclose()
    return token["access_token"]


# --- Microsoft ---

MICROSOFT_SCOPES = [
    "https://outlook.office.com/IMAP.AccessAsUser.All",
    "offline_access",
]


def _ms_auth_url() -> str:
    return f"https://login.microsoftonline.com/{settings.microsoft_tenant}/oauth2/v2.0/authorize"


def _ms_token_url() -> str:
    return f"https://login.microsoftonline.com/{settings.microsoft_tenant}/oauth2/v2.0/token"


def get_microsoft_oauth_client(redirect_uri: str) -> AsyncOAuth2Client:
    return AsyncOAuth2Client(
        client_id=settings.microsoft_client_id,
        client_secret=settings.microsoft_client_secret,
        redirect_uri=redirect_uri,
        scope=" ".join(MICROSOFT_SCOPES),
    )


def build_microsoft_auth_url(redirect_uri: str, state: str) -> str:
    client = get_microsoft_oauth_client(redirect_uri)
    url, _ = client.create_authorization_url(
        _ms_auth_url(),
        state=state,
        response_mode="query",
    )
    return url


async def exchange_microsoft_code(code: str, redirect_uri: str) -> dict:
    client = get_microsoft_oauth_client(redirect_uri)
    token = await client.fetch_token(_ms_token_url(), code=code)
    await client.aclose()
    return token


async def refresh_microsoft_token(refresh_token: str) -> str:
    client = get_microsoft_oauth_client("")
    token = await client.fetch_token(
        _ms_token_url(),
        grant_type="refresh_token",
        refresh_token=refresh_token,
    )
    await client.aclose()
    return token["access_token"]
