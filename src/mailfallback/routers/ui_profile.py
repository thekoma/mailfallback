# src/mailfallback/routers/ui_profile.py
from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, field_validator
from sqlalchemy.orm import Session

from mailfallback.dependencies import get_current_user, get_db
from mailfallback.models import User
from mailfallback.routers.ui import _get_session_user, templates
from mailfallback.security import verify_password
from mailfallback.services.group_service import get_user_groups
from mailfallback.services.store_service import get_selectable_stores, get_user_store
from mailfallback.services.user_service import MIN_PASSWORD_LENGTH, change_password, update_user

router = APIRouter(tags=["ui"])


@router.get("/profile", response_class=HTMLResponse)
def profile_page(request: Request, db: Session = Depends(get_db)):
    user = _get_session_user(request, db)
    if not user:
        return RedirectResponse("/login")
    store = get_user_store(db, user)
    selectable_stores = get_selectable_stores(db, user)
    user_groups = get_user_groups(db, user)
    force_password_change = request.query_params.get("force_password_change") == "1"
    return templates.TemplateResponse(
        request=request,
        name="profile.html",
        context={
            "user": user,
            "store": store,
            "selectable_stores": selectable_stores,
            "user_groups": user_groups,
            "error": None,
            "success": None,
            "force_password_change": force_password_change,
        },
    )


@router.post("/profile/store")
async def profile_change_store(request: Request, db: Session = Depends(get_db)):
    user = _get_session_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)
    if user.migrating:
        return RedirectResponse("/profile", status_code=303)
    form = await request.form()
    new_store_id = form["store_id"]
    allowed_ids = {s.id for s in user.allowed_stores if s.enabled}
    if new_store_id not in allowed_ids:
        return RedirectResponse("/profile", status_code=303)
    update_user(db, user.id, store_id=new_store_id)
    return RedirectResponse("/profile", status_code=303)


@router.post("/profile/password")
async def profile_change_password(request: Request, db: Session = Depends(get_db)):
    user = _get_session_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)

    form = await request.form()
    current = form["current_password"]
    new = form["new_password"]
    confirm = form["confirm_password"]

    store = get_user_store(db, user)
    selectable_stores = get_selectable_stores(db, user)
    user_groups = get_user_groups(db, user)
    base_context = {
        "user": user,
        "store": store,
        "selectable_stores": selectable_stores,
        "user_groups": user_groups,
    }

    if user.password_hash and not verify_password(current, user.password_hash):
        return templates.TemplateResponse(
            request=request,
            name="profile.html",
            context={**base_context, "error": "Current password is incorrect", "success": None},
        )
    if new != confirm:
        return templates.TemplateResponse(
            request=request,
            name="profile.html",
            context={**base_context, "error": "New passwords do not match", "success": None},
        )
    if len(new) < MIN_PASSWORD_LENGTH:
        return templates.TemplateResponse(
            request=request,
            name="profile.html",
            context={
                **base_context,
                "error": f"Password must be at least {MIN_PASSWORD_LENGTH} characters",
                "success": None,
            },
        )

    try:
        change_password(db, user.id, new)
    except ValueError as e:
        return templates.TemplateResponse(
            request=request,
            name="profile.html",
            context={**base_context, "error": str(e), "success": None},
        )
    return templates.TemplateResponse(
        request=request,
        name="profile.html",
        context={**base_context, "error": None, "success": "Password updated successfully"},
    )


class PreferencesUpdate(BaseModel):
    theme: str | None = None

    @field_validator("theme")
    @classmethod
    def validate_theme(cls, v):
        if v is not None and v not in ("light", "dark"):
            raise ValueError("theme must be 'light' or 'dark'")
        return v


@router.patch("/api/preferences")
def update_preferences(
    body: PreferencesUpdate,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    prefs = dict(user.preferences or {})
    update = body.model_dump(exclude_none=True)
    prefs.update(update)
    user.preferences = prefs
    db.commit()
    if "theme" in update:
        request.session["theme"] = update["theme"]
    return Response(status_code=204)
