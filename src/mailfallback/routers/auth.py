# src/mailfallback/routers/auth.py
import json

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from mailfallback.config import settings
from mailfallback.dependencies import get_db
from mailfallback.security import encrypt_credentials
from mailfallback.services.oauth2 import build_google_auth_url, exchange_google_code
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
    return {"ok": True}


@router.get("/auth/google/start")
def google_oauth_start(request: Request, account_id: str):
    redirect_uri = str(request.url_for("google_oauth_callback"))
    request.session["oauth_account_id"] = account_id
    url = build_google_auth_url(redirect_uri, state=account_id)
    return RedirectResponse(url)


@router.get("/auth/google/callback")
async def google_oauth_callback(
    request: Request,
    code: str,
    db: Session = Depends(get_db),
):
    redirect_uri = str(request.url_for("google_oauth_callback"))
    token = await exchange_google_code(code, redirect_uri)
    account_id = request.session.pop("oauth_account_id", None)
    if not account_id:
        raise HTTPException(status_code=400, detail="No account in session")

    from mailfallback.models import Account, AuthType
    account = db.query(Account).filter(Account.id == account_id).first()
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")

    token_data = json.dumps({
        "access_token": token["access_token"],
        "refresh_token": token.get("refresh_token", ""),
        "token_type": token.get("token_type", "Bearer"),
    })
    account.credentials = encrypt_credentials(token_data, settings.secret_key)
    account.auth_type = AuthType.oauth2
    db.commit()

    return RedirectResponse(f"/accounts/{account_id}")
