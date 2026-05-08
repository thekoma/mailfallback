# src/mailfallback/routers/auth.py
import json

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
from mailfallback.services.user_service import authenticate_user

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
    redirect_uri = str(request.url_for("google_oauth_callback"))
    request.session["oauth_account_id"] = account_id
    url = build_google_auth_url(redirect_uri, state=account_id)
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
            return RedirectResponse(
                f"/accounts/new?oauth_failed=true&reason={reason}&email={email}&name={name}",
                status_code=303,
            )
        return RedirectResponse(f"/accounts/{account_id}", status_code=303)
    return RedirectResponse("/", status_code=303)


@router.get("/auth/google/callback")
async def google_oauth_callback(
    request: Request,
    code: str | None = None,
    error: str | None = None,
    db: Session = Depends(get_db),
):
    account_id = request.session.pop("oauth_account_id", None)

    if error or not code:
        return _oauth_failure_redirect(db, request, account_id, reason=error or "denied")

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

    return RedirectResponse(f"/accounts/{account_id}")


@router.get("/auth/microsoft/start")
def microsoft_oauth_start(request: Request, account_id: str, db: Session = Depends(get_db)):
    _verify_oauth_access(request, db, account_id)
    redirect_uri = str(request.url_for("microsoft_oauth_callback"))
    request.session["oauth_account_id"] = account_id
    url = build_microsoft_auth_url(redirect_uri, state=account_id)
    return RedirectResponse(url)


@router.get("/auth/microsoft/callback")
async def microsoft_oauth_callback(
    request: Request,
    code: str | None = None,
    error: str | None = None,
    db: Session = Depends(get_db),
):
    account_id = request.session.pop("oauth_account_id", None)

    if error or not code:
        return _oauth_failure_redirect(db, request, account_id, reason=error or "denied")

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

    return RedirectResponse(f"/accounts/{account_id}")


oauth = OAuth()

if settings.oidc_enabled:
    oauth.register(
        name="oidc",
        client_id=settings.oidc_client_id,
        client_secret=settings.oidc_client_secret,
        server_metadata_url=settings.oidc_discovery_url,
        client_kwargs={"scope": "openid email profile groups"},
    )


@router.get("/auth/oidc/login")
async def oidc_login(request: Request):
    if not settings.oidc_enabled:
        raise HTTPException(status_code=404, detail="OIDC not enabled")
    redirect_uri = str(request.url_for("oidc_callback"))
    return await oauth.oidc.authorize_redirect(request, redirect_uri)


@router.get("/auth/oidc/callback")
async def oidc_callback(request: Request, db: Session = Depends(get_db)):
    if not settings.oidc_enabled:
        raise HTTPException(status_code=404, detail="OIDC not enabled")

    token = await oauth.oidc.authorize_access_token(request)
    userinfo = token.get("userinfo", {})

    sub = userinfo.get("sub")
    username = userinfo.get("preferred_username") or userinfo.get("email", sub)
    groups = userinfo.get("groups", [])

    role = UserRole.admin if settings.oidc_admin_group in groups else UserRole.user

    user = db.query(User).filter(User.oidc_subject == sub).first()
    if not user:
        default_store = ensure_default_store(db)
        user = User(username=username, oidc_subject=sub, role=role, store_id=default_store.id)
        db.add(user)
        db.commit()
        db.refresh(user)
    else:
        user.role = role
        db.commit()

    sync_sso_groups(db, user, groups)

    request.session["user_id"] = user.id
    return RedirectResponse("/")
