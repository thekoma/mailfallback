# src/mailfallback/routers/ui_profile.py
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from mailfallback.dependencies import get_db
from mailfallback.routers.ui import _get_session_user, templates
from mailfallback.security import verify_password
from mailfallback.services.group_service import get_user_groups
from mailfallback.services.store_service import get_selectable_stores, get_user_store
from mailfallback.services.user_service import change_password, update_user

router = APIRouter(tags=["ui"])


@router.get("/profile", response_class=HTMLResponse)
def profile_page(request: Request, db: Session = Depends(get_db)):
    user = _get_session_user(request, db)
    if not user:
        return RedirectResponse("/login")
    store = get_user_store(db, user)
    selectable_stores = get_selectable_stores(db, user)
    user_groups = get_user_groups(db, user)
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
        },
    )


@router.post("/profile/store")
async def profile_change_store(request: Request, db: Session = Depends(get_db)):
    user = _get_session_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)
    form = await request.form()
    new_store_id = form["store_id"]
    allowed_ids = {s.id for s in user.allowed_stores}
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
