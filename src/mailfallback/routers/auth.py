# src/mailfallback/routers/auth.py
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from mailfallback.dependencies import get_db
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
