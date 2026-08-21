# src/mailfallback/dependencies.py
from collections.abc import Generator
from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from mailfallback.db import SessionLocal
from mailfallback.models import AppCredential, User


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    user_id = request.session.get("user_id")
    if not user_id:
        raise HTTPException(status_code=401, detail="Not authenticated")
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    if not user.enabled:
        raise HTTPException(status_code=403, detail="Account disabled")
    return user


def require_admin(user: User = Depends(get_current_user)) -> User:
    if user.role.value != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


@dataclass(frozen=True)
class Principal:
    """Who is calling, and what they are allowed to ask for.

    A session principal (``credential is None``) carries every valid scope: an
    interactive user can already do all of this through the UI, so a scope check
    must never be what stops them. A token principal carries only the scopes its
    credential was created with.
    """

    user: User
    credential: AppCredential | None
    scopes: frozenset[str]

    @property
    def is_token(self) -> bool:
        return self.credential is not None


def get_current_principal(request: Request, db: Session = Depends(get_db)) -> Principal:
    """Resolve a bearer token first, then the session cookie.

    A caller who presented an ``Authorization`` header naming the bearer scheme
    meant to authenticate as that token. The scheme name is matched
    case-insensitively — RFC 7235 defines auth-scheme names as case-insensitive,
    so ``bearer``/``Bearer``/``BEARER`` must all take this path, and a naive
    ``startswith("Bearer ")`` would silently miss the lowercase form a real
    client can send. Once the scheme is recognized as bearer, the request IS a
    bearer attempt — including when the token part is empty or blank — and
    verification failure returns 401 rather than falling back to whatever
    session cookie happens to be attached. That fallback would be a confused
    deputy, acting with the browser user's identity on a request the token
    holder made. Only a genuinely different scheme (or no header at all) falls
    through to the session.
    """
    from mailfallback.services import app_credential_service

    header = request.headers.get("Authorization", "")
    scheme, _, rest = header.partition(" ")
    if scheme.lower() == "bearer":
        token = rest.strip()
        # required_scope is checked by require_scope(), not here: this
        # dependency answers "who is this", not "may they do that".
        result, cred = app_credential_service.verify_credential(
            db, username=None, token=token, required_scope=None, kind="api"
        )
        if result is not app_credential_service.VerifyResult.ok or cred is None:
            raise HTTPException(status_code=401, detail="Invalid access token")
        return Principal(user=cred.user, credential=cred, scopes=cred.scope_set)

    user = get_current_user(request, db)
    return Principal(user=user, credential=None, scopes=app_credential_service.VALID_SCOPES)


def require_scope(scope: str):
    """Dependency factory: a Principal that holds ``scope``, or 403."""

    def _dependency(principal: Principal = Depends(get_current_principal)) -> Principal:
        if scope not in principal.scopes:
            raise HTTPException(status_code=403, detail=f"Access token lacks the {scope} scope")
        return principal

    return _dependency
