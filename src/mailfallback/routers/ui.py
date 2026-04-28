# src/mailfallback/routers/ui.py
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from mailfallback.config import settings
from mailfallback.dependencies import get_db
from mailfallback.models import Account, User
from mailfallback.security import verify_password
from mailfallback.services.account_service import (
    create_account,
    get_account,
    get_accounts_for_user,
    update_account,
)
from mailfallback.services.sync_service import list_jobs_for_account
from mailfallback.services.user_service import (
    authenticate_user,
    change_password,
    create_user,
    delete_user,
    list_users,
    update_user,
)

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
    auth_type = form["auth_type"]
    account = create_account(
        db,
        name=form["name"],
        email_address=form.get("email_address", ""),
        imap_host=form["imap_host"],
        imap_port=int(form["imap_port"]),
        auth_type=auth_type,
        maildir_path=form["maildir_path"],
        credentials=form.get("credentials") or None,
        sync_schedule=form.get("sync_schedule", "0 */6 * * *"),
    )
    if auth_type == "oauth2":
        return RedirectResponse(f"/auth/google/start?account_id={account.id}", status_code=303)
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


@router.post("/accounts/{account_id}/edit")
async def account_edit_submit(account_id: str, request: Request, db: Session = Depends(get_db)):
    user_id = request.session.get("user_id")
    if not user_id:
        return RedirectResponse("/login", status_code=303)
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        return RedirectResponse("/login", status_code=303)

    form = await request.form()
    updates = {}
    for key in ("name", "email_address", "imap_host", "imap_port", "sync_schedule", "credentials"):
        val = form.get(key, "")
        if val:
            updates[key] = int(val) if key == "imap_port" else val
    update_account(db, account_id, user, **updates)
    return RedirectResponse(f"/accounts/{account_id}", status_code=303)


@router.get("/profile", response_class=HTMLResponse)
def profile_page(request: Request, db: Session = Depends(get_db)):
    user_id = request.session.get("user_id")
    if not user_id:
        return RedirectResponse("/login")
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        return RedirectResponse("/login")
    return templates.TemplateResponse(
        request=request,
        name="profile.html",
        context={"user": user, "error": None, "success": None},
    )


@router.post("/profile/password")
async def profile_change_password(request: Request, db: Session = Depends(get_db)):
    user_id = request.session.get("user_id")
    if not user_id:
        return RedirectResponse("/login", status_code=303)
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        return RedirectResponse("/login", status_code=303)

    form = await request.form()
    current = form["current_password"]
    new = form["new_password"]
    confirm = form["confirm_password"]

    if not user.password_hash or not verify_password(current, user.password_hash):
        return templates.TemplateResponse(
            request=request,
            name="profile.html",
            context={"user": user, "error": "Current password is incorrect", "success": None},
        )
    if new != confirm:
        return templates.TemplateResponse(
            request=request,
            name="profile.html",
            context={"user": user, "error": "New passwords do not match", "success": None},
        )
    if len(new) < 6:
        return templates.TemplateResponse(
            request=request,
            name="profile.html",
            context={
                "user": user,
                "error": "Password must be at least 6 characters",
                "success": None,
            },
        )

    change_password(db, user.id, new)
    return templates.TemplateResponse(
        request=request,
        name="profile.html",
        context={"user": user, "error": None, "success": "Password updated successfully"},
    )


ADMIN_PW_COOLDOWN = 15 * 60


def _admin_pw_verified(request: Request) -> bool:
    verified_at = request.session.get("admin_pw_verified_at")
    if not verified_at:
        return False
    import time

    return (time.time() - verified_at) < ADMIN_PW_COOLDOWN


@router.post("/admin/users/{target_user_id}/password")
async def admin_change_user_password(
    target_user_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    user_id = request.session.get("user_id")
    if not user_id:
        return RedirectResponse("/login", status_code=303)
    user = db.query(User).filter(User.id == user_id).first()
    if not user or user.role.value != "admin":
        return RedirectResponse("/", status_code=303)

    form = await request.form()
    new_password = form["new_password"]
    if len(new_password) < 6:
        return RedirectResponse("/admin/users", status_code=303)

    if not _admin_pw_verified(request):
        admin_password = form.get("admin_password", "")
        if not admin_password or not verify_password(admin_password, user.password_hash):
            return RedirectResponse("/admin/users?error=invalid_admin_password", status_code=303)
        import time

        request.session["admin_pw_verified_at"] = time.time()

    change_password(db, target_user_id, new_password)
    return RedirectResponse("/admin/users", status_code=303)


@router.post("/admin/users/{target_user_id}/edit")
async def admin_edit_user(
    target_user_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    user_id = request.session.get("user_id")
    if not user_id:
        return RedirectResponse("/login", status_code=303)
    user = db.query(User).filter(User.id == user_id).first()
    if not user or user.role.value != "admin":
        return RedirectResponse("/", status_code=303)

    form = await request.form()
    updates = {}
    username = form.get("username", "").strip()
    if username:
        updates["username"] = username
    role = form.get("role")
    if role in ("admin", "user"):
        updates["role"] = role
    if updates:
        update_user(db, target_user_id, **updates)
    return RedirectResponse("/admin/users", status_code=303)


@router.post("/admin/users/{target_user_id}/toggle")
async def admin_toggle_user(
    target_user_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    user_id = request.session.get("user_id")
    if not user_id:
        return RedirectResponse("/login", status_code=303)
    user = db.query(User).filter(User.id == user_id).first()
    if not user or user.role.value != "admin":
        return RedirectResponse("/", status_code=303)
    if target_user_id == user.id:
        return RedirectResponse("/admin/users", status_code=303)

    target = db.query(User).filter(User.id == target_user_id).first()
    if target:
        update_user(db, target_user_id, enabled=not target.enabled)
    return RedirectResponse("/admin/users", status_code=303)


@router.post("/admin/users/{target_user_id}/delete")
async def admin_delete_user(
    target_user_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    user_id = request.session.get("user_id")
    if not user_id:
        return RedirectResponse("/login", status_code=303)
    user = db.query(User).filter(User.id == user_id).first()
    if not user or user.role.value != "admin":
        return RedirectResponse("/", status_code=303)
    if target_user_id == user.id:
        return RedirectResponse("/admin/users", status_code=303)

    delete_user(db, target_user_id)
    return RedirectResponse("/admin/users", status_code=303)


@router.get("/admin/users", response_class=HTMLResponse)
def admin_users_page(request: Request, db: Session = Depends(get_db)):
    user_id = request.session.get("user_id")
    if not user_id:
        return RedirectResponse("/login")
    user = db.query(User).filter(User.id == user_id).first()
    if not user or user.role.value != "admin":
        return RedirectResponse("/")
    users = list_users(db)
    error = request.query_params.get("error")
    return templates.TemplateResponse(
        request=request,
        name="admin_users.html",
        context={
            "user": user,
            "users": users,
            "admin_verified": _admin_pw_verified(request),
            "error": error,
        },
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
