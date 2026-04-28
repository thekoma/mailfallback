# src/mailfallback/routers/ui.py
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from mailfallback.config import settings
from mailfallback.dependencies import get_db
from mailfallback.models import Account, User
from mailfallback.services.account_service import (
    create_account,
    get_account,
    get_accounts_for_user,
)
from mailfallback.services.sync_service import list_jobs_for_account
from mailfallback.services.user_service import authenticate_user, create_user, list_users

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


@router.get("/accounts/new", response_class=HTMLResponse)
def account_form(request: Request, db: Session = Depends(get_db)):
    user_id = request.session.get("user_id")
    if not user_id:
        return RedirectResponse("/login")
    user = db.query(User).filter(User.id == user_id).first()
    if not user or user.role.value != "admin":
        return RedirectResponse("/")
    return templates.TemplateResponse(
        request=request,
        name="account_form.html",
        context={"user": user},
    )


@router.post("/accounts/new")
async def account_form_submit(request: Request, db: Session = Depends(get_db)):
    user_id = request.session.get("user_id")
    if not user_id:
        return RedirectResponse("/login", status_code=303)
    user = db.query(User).filter(User.id == user_id).first()
    if not user or user.role.value != "admin":
        return RedirectResponse("/", status_code=303)

    form = await request.form()
    create_account(
        db,
        name=form["name"],
        imap_host=form["imap_host"],
        imap_port=int(form["imap_port"]),
        auth_type=form["auth_type"],
        maildir_path=form["maildir_path"],
        credentials=form.get("credentials") or None,
        sync_schedule=form.get("sync_schedule", "0 */6 * * *"),
    )
    return RedirectResponse("/", status_code=303)


@router.get("/accounts/{account_id}", response_class=HTMLResponse)
def account_detail(account_id: str, request: Request, db: Session = Depends(get_db)):
    user_id = request.session.get("user_id")
    if not user_id:
        return RedirectResponse("/login")
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        return RedirectResponse("/login")

    account = get_account(db, account_id, user)
    if not account:
        return RedirectResponse("/")
    jobs = list_jobs_for_account(db, account_id, limit=20)
    return templates.TemplateResponse(
        request=request,
        name="account_detail.html",
        context={"user": user, "account": account, "jobs": jobs},
    )


@router.get("/admin/users", response_class=HTMLResponse)
def admin_users_page(request: Request, db: Session = Depends(get_db)):
    user_id = request.session.get("user_id")
    if not user_id:
        return RedirectResponse("/login")
    user = db.query(User).filter(User.id == user_id).first()
    if not user or user.role.value != "admin":
        return RedirectResponse("/")
    users = list_users(db)
    return templates.TemplateResponse(
        request=request,
        name="admin_users.html",
        context={"request": request, "user": user, "users": users},
    )


@router.post("/admin/users/new")
async def admin_create_user(request: Request, db: Session = Depends(get_db)):
    user_id = request.session.get("user_id")
    if not user_id:
        return RedirectResponse("/login", status_code=303)
    user = db.query(User).filter(User.id == user_id).first()
    if not user or user.role.value != "admin":
        return RedirectResponse("/", status_code=303)

    form = await request.form()
    create_user(db, form["username"], form["password"], form["role"])
    return RedirectResponse("/admin/users", status_code=303)


@router.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request, db: Session = Depends(get_db)):
    user_id = request.session.get("user_id")
    if not user_id:
        return RedirectResponse("/login")
    user = db.query(User).filter(User.id == user_id).first()
    if not user or user.role.value != "admin":
        return RedirectResponse("/")
    return templates.TemplateResponse(
        request=request,
        name="settings.html",
        context={
            "request": request,
            "user": user,
            "total_accounts": db.query(Account).count(),
            "total_users": db.query(User).count(),
            "oidc_enabled": settings.oidc_enabled,
        },
    )
