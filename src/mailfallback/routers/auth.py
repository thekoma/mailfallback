# src/mailfallback/routers/auth.py
import json
import logging
import re
import secrets
import urllib.parse

from authlib.integrations.base_client.errors import OAuthError
from authlib.integrations.starlette_client import OAuth
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from mailfallback.config import settings
from mailfallback.dependencies import get_db
from mailfallback.models import Account, AuthType, User, UserRole
from mailfallback.security import encrypt_credentials
from mailfallback.services.account_service import is_account_owner
from mailfallback.services.group_service import sync_sso_groups
from mailfallback.services.oauth2 import (
    build_google_auth_url,
    build_microsoft_auth_url,
    exchange_google_code,
    exchange_microsoft_code,
)
from mailfallback.services.store_service import ensure_default_store
from mailfallback.services.sync_service import create_sync_job
from mailfallback.services.sync_worker import TOKEN_REFRESH_FAILED, submit_sync_job
from mailfallback.services.user_service import authenticate_user

logger = logging.getLogger(__name__)

router = APIRouter(tags=["auth"])


class LoginRequest(BaseModel):
    username: str
    password: str


@router.post("/api/auth/login")
def login(body: LoginRequest, request: Request, db: Session = Depends(get_db)):
    user = authenticate_user(db, body.username, body.password)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid credentials")
    request.session["user_id"] = user.id
    if user.preferences:
        request.session["theme"] = user.preferences.get("theme", "light")
    from mailfallback.services.audit_service import log_action

    log_action(
        db,
        user=user,
        action="user.login",
        resource_type="user",
        resource_id=user.id,
        resource_name=user.username,
        ip_address=request.client.host if request.client else None,
    )
    return {"ok": True, "role": user.role.value}


@router.post("/api/auth/logout")
def logout(request: Request):
    request.session.clear()
    if request.headers.get("hx-request"):
        response = HTMLResponse("")
        response.headers["HX-Redirect"] = "/login"
        return response
    return {"ok": True}


def _verify_oauth_access(request: Request, db: Session, account_id: str) -> Account:
    user_id = request.session.get("user_id")
    if not user_id:
        raise HTTPException(status_code=401, detail="Not authenticated")
    user = db.query(User).filter(User.id == user_id, User.enabled.is_(True)).first()
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    account = db.query(Account).filter(Account.id == account_id).first()
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    if user.role != UserRole.admin and not is_account_owner(user, account):
        raise HTTPException(status_code=403, detail="Not authorized for this account")
    return account


@router.get("/auth/google/start")
def google_oauth_start(request: Request, account_id: str, db: Session = Depends(get_db)):
    _verify_oauth_access(request, db, account_id)
    nonce = secrets.token_urlsafe(32)
    redirect_uri = str(request.url_for("google_oauth_callback"))
    request.session["oauth_account_id"] = account_id
    request.session["oauth_state"] = nonce
    url = build_google_auth_url(redirect_uri, state=nonce)
    return RedirectResponse(url)


def _oauth_failure_redirect(db, request, account_id, reason="failed"):
    if account_id:
        user_id = request.session.get("user_id") if request else None
        user = db.query(User).filter(User.id == user_id).first() if user_id else None
        account = db.query(Account).filter(Account.id == account_id).first()
        if (
            account
            and not account.credentials
            and user
            and (user.role == UserRole.admin or is_account_owner(user, account))
        ):
            email = account.email_address
            name = account.name
            db.delete(account)
            db.commit()
            q_reason = urllib.parse.quote(str(reason))
            q_email = urllib.parse.quote(str(email or ""))
            q_name = urllib.parse.quote(str(name or ""))
            return RedirectResponse(
                f"/accounts/new?oauth_failed=true&reason={q_reason}&email={q_email}&name={q_name}",
                status_code=303,
            )
        return RedirectResponse(f"/accounts/{account_id}", status_code=303)
    return RedirectResponse("/", status_code=303)


def _resume_after_reauth(db: Session, account: Account) -> None:
    """Clear the token error/needs_reauth and kick a sync after a successful
    re-authentication — the UI promises sync resumes on reconnect."""
    from mailfallback.models import SyncState

    refresh_failed = (
        account.sync_state == SyncState.error and account.last_error == TOKEN_REFRESH_FAILED
    )
    if account.sync_state == SyncState.needs_reauth or refresh_failed:
        account.sync_state = SyncState.idle
        account.last_error = None
        db.commit()
        job = create_sync_job(db, account.id, source="reauth")
        if job:
            submit_sync_job(job.id)


@router.get("/auth/google/callback")
async def google_oauth_callback(
    request: Request,
    code: str | None = None,
    error: str | None = None,
    state: str | None = None,
    db: Session = Depends(get_db),
):
    account_id = request.session.pop("oauth_account_id", None)
    expected_state = request.session.pop("oauth_state", None)

    if error or not code:
        return _oauth_failure_redirect(db, request, account_id, reason=error or "denied")

    if not state or not expected_state or state != expected_state:
        return _oauth_failure_redirect(db, request, account_id, reason="invalid_state")

    redirect_uri = str(request.url_for("google_oauth_callback"))
    try:
        token = await exchange_google_code(code, redirect_uri)
    except Exception:
        return _oauth_failure_redirect(db, request, account_id, reason="token_exchange")

    if not account_id:
        raise HTTPException(status_code=400, detail="No account in session")

    account = _verify_oauth_access(request, db, account_id)

    token_data = json.dumps(
        {
            "access_token": token["access_token"],
            "refresh_token": token.get("refresh_token", ""),
            "token_type": token.get("token_type", "Bearer"),
        }
    )
    account.credentials = encrypt_credentials(token_data, settings.secret_key)
    account.auth_type = AuthType.oauth2
    db.commit()
    _resume_after_reauth(db, account)

    return RedirectResponse(f"/accounts/{account_id}")


@router.get("/auth/microsoft/start")
def microsoft_oauth_start(request: Request, account_id: str, db: Session = Depends(get_db)):
    _verify_oauth_access(request, db, account_id)
    nonce = secrets.token_urlsafe(32)
    redirect_uri = str(request.url_for("microsoft_oauth_callback"))
    request.session["oauth_account_id"] = account_id
    request.session["oauth_state"] = nonce
    url = build_microsoft_auth_url(redirect_uri, state=nonce)
    return RedirectResponse(url)


@router.get("/auth/microsoft/callback")
async def microsoft_oauth_callback(
    request: Request,
    code: str | None = None,
    error: str | None = None,
    state: str | None = None,
    db: Session = Depends(get_db),
):
    account_id = request.session.pop("oauth_account_id", None)
    expected_state = request.session.pop("oauth_state", None)

    if error or not code:
        return _oauth_failure_redirect(db, request, account_id, reason=error or "denied")

    if not state or not expected_state or state != expected_state:
        return _oauth_failure_redirect(db, request, account_id, reason="invalid_state")

    redirect_uri = str(request.url_for("microsoft_oauth_callback"))
    try:
        token = await exchange_microsoft_code(code, redirect_uri)
    except Exception:
        return _oauth_failure_redirect(db, request, account_id, reason="token_exchange")

    if not account_id:
        raise HTTPException(status_code=400, detail="No account in session")

    account = _verify_oauth_access(request, db, account_id)

    token_data = json.dumps(
        {
            "access_token": token["access_token"],
            "refresh_token": token.get("refresh_token", ""),
            "token_type": token.get("token_type", "Bearer"),
            "provider": "microsoft",
        }
    )
    account.credentials = encrypt_credentials(token_data, settings.secret_key)
    account.auth_type = AuthType.oauth2
    db.commit()
    _resume_after_reauth(db, account)

    return RedirectResponse(f"/accounts/{account_id}")


oauth = OAuth()

if settings.oidc_enabled:
    oauth.register(
        name="oidc",
        client_id=settings.oidc_client_id,
        client_secret=settings.oidc_client_secret,
        server_metadata_url=settings.oidc_discovery_url,
        # timeout covers authlib's lazy server-metadata fetch on the first
        # login after startup — the httpx default (5s) is too tight for a
        # cold identity provider and used to surface as a raw 500.
        client_kwargs={"scope": "openid email profile groups", "timeout": 15},
    )


@router.get("/auth/oidc/login")
async def oidc_login(request: Request):
    if not settings.oidc_enabled:
        raise HTTPException(status_code=404, detail="OIDC not enabled")
    redirect_uri = str(request.url_for("oidc_callback"))
    try:
        return await oauth.oidc.authorize_redirect(request, redirect_uri)
    except Exception:
        logger.exception("OIDC login failed: could not reach the identity provider")
        return RedirectResponse("/login?error=sso_unreachable", status_code=303)


@router.get("/auth/oidc/callback")
async def oidc_callback(request: Request, db: Session = Depends(get_db)):
    if not settings.oidc_enabled:
        raise HTTPException(status_code=404, detail="OIDC not enabled")

    try:
        token = await oauth.oidc.authorize_access_token(request)
    except OAuthError:
        logger.exception("OIDC callback rejected: state mismatch or provider error")
        return RedirectResponse("/login?error=sso_failed", status_code=303)
    except Exception:
        logger.exception("OIDC callback failed: could not reach the identity provider")
        return RedirectResponse("/login?error=sso_unreachable", status_code=303)
    userinfo = token.get("userinfo", {})

    sub = userinfo.get("sub")
    if not sub or not isinstance(sub, str) or not sub.strip():
        raise HTTPException(status_code=400, detail="Invalid OIDC token: missing sub claim")

    from mailfallback.services.dovecot_auth import TEMP_USER_PREFIX

    raw_username = userinfo.get("preferred_username") or userinfo.get("email", sub)
    username = re.sub(r"[^a-zA-Z0-9@._-]", "_", str(raw_username))[:255]
    # Never let an IdP-supplied name collide with the reserved restore-user
    # prefix — cleanup_temp_imap_users would later delete it as an orphan.
    if username.startswith(TEMP_USER_PREFIX):
        username = f"u{username}"
    groups = userinfo.get("groups", [])

    if (
        settings.oidc_user_group
        and settings.oidc_user_group not in groups
        and not (settings.oidc_admin_group and settings.oidc_admin_group in groups)
    ):
        raise HTTPException(status_code=403, detail="Not a member of the required group")

    role = UserRole.user
    if settings.oidc_admin_group and settings.oidc_admin_group in groups:
        role = UserRole.admin

    user = db.query(User).filter(User.oidc_subject == sub).first()
    if not user:
        default_store = ensure_default_store(db)
        user = User(username=username, oidc_subject=sub, role=role, store_id=default_store.id)
        db.add(user)
        db.commit()
        db.refresh(user)
    else:
        if settings.oidc_admin_group and user.role != role:
            from mailfallback.services.audit_service import log_action

            log_action(
                db,
                user=user,
                action="user.role_changed",
                resource_type="user",
                resource_id=user.id,
                resource_name=user.username,
                details={"old_role": user.role.value, "new_role": role.value, "source": "oidc"},
            )
            user.role = role
            db.commit()

    sync_sso_groups(db, user, groups)

    from mailfallback.services.audit_service import log_action

    log_action(
        db,
        user=user,
        action="user.login",
        resource_type="user",
        resource_id=user.id,
        resource_name=user.username,
        details={"method": "oidc"},
        ip_address=request.client.host if request.client else None,
    )

    request.session["user_id"] = user.id
    if user.preferences:
        request.session["theme"] = user.preferences.get("theme", "light")
    return RedirectResponse("/")
