# src/mailfallback/routers/ui.py
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from mailfallback.config import settings
from mailfallback.dependencies import get_db
from mailfallback.models import User
from mailfallback.services.account_service import get_accounts_for_user
from mailfallback.services.user_service import authenticate_user

router = APIRouter(tags=["ui"])

templates = Jinja2Templates(directory="src/mailfallback/templates")


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="login.html",
        context={"oidc_enabled": settings.oidc_enabled, "error": None},
    )


@router.post("/login")
async def login_submit(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    user = authenticate_user(db, form["username"], form["password"])
    if not user:
        return templates.TemplateResponse(
            request=request,
            name="login.html",
            context={"oidc_enabled": settings.oidc_enabled, "error": "Invalid credentials"},
        )
    request.session["user_id"] = user.id
    return RedirectResponse("/", status_code=303)


@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request, db: Session = Depends(get_db)):
    user_id = request.session.get("user_id")
    if not user_id:
        return RedirectResponse("/login")
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        return RedirectResponse("/login")
    accounts = get_accounts_for_user(db, user)
    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={"user": user, "accounts": accounts},
    )


@router.get("/partials/accounts-table", response_class=HTMLResponse)
def accounts_table_partial(request: Request, db: Session = Depends(get_db)):
    user_id = request.session.get("user_id")
    if not user_id:
        return HTMLResponse("")
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        return HTMLResponse("")
    accounts = get_accounts_for_user(db, user)
    return templates.TemplateResponse(
        request=request,
        name="partials/accounts_table.html",
        context={"accounts": accounts},
    )
