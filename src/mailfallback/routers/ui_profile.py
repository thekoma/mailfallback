# src/mailfallback/routers/ui_profile.py
import json

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, field_validator
from sqlalchemy.orm import Session

from mailfallback.config import settings
from mailfallback.dependencies import get_current_user, get_db
from mailfallback.models import User
from mailfallback.routers.ui import _get_session_user, templates
from mailfallback.security import verify_password
from mailfallback.services import app_credential_service
from mailfallback.services import notification_service as _ns
from mailfallback.services.group_service import get_user_groups
from mailfallback.services.store_service import get_selectable_stores, get_user_store
from mailfallback.services.user_service import MIN_PASSWORD_LENGTH, change_password, update_user

router = APIRouter(tags=["ui"])


def _mask_apprise_url(encrypted_url: str) -> str:
    """Decrypt and mask an Apprise URL — return scheme://… only."""
    try:
        from mailfallback.security import decrypt_credentials

        plain = decrypt_credentials(encrypted_url, settings.secret_key)
        if "://" in plain:
            scheme = plain.split("://", 1)[0]
            return f"{scheme}://…"
        return "…"
    except Exception:
        return "…"


def _build_channels_context(db: Session, user) -> list[dict]:
    from mailfallback.models import NotificationChannel

    rows = db.query(NotificationChannel).filter_by(user_id=user.id).all()
    return [
        {
            "id": ch.id,
            "label": ch.label,
            "masked_url": _mask_apprise_url(ch.apprise_url),
            "enabled": ch.enabled,
            "events": ch.events or [],
            "payload_format": ch.payload_format,
        }
        for ch in rows
    ]


def _profile_context(request: Request, db: Session, user: User, **overrides) -> dict:
    """Context for profile.html.

    Shared by the page and by every handler that re-renders it instead of
    redirecting — a redirect would lose a one-time token or a flash message.
    Building it in one place is not cosmetic: the previous hand-built dict in
    profile_change_password omitted `channels`, so a wrong current password
    silently emptied the notification-channels section.
    """
    context = {
        "user": user,
        "store": get_user_store(db, user),
        "selectable_stores": get_selectable_stores(db, user),
        "user_groups": get_user_groups(db, user),
        "channels": _build_channels_context(db, user),
        "tokens": app_credential_service.list_credentials(db, user),
        "valid_scopes": sorted(app_credential_service.VALID_SCOPES),
        "error": None,
        "success": None,
        "new_token": None,
        "force_password_change": False,
    }
    context.update(overrides)
    return context


@router.get("/profile", response_class=HTMLResponse)
def profile_page(request: Request, db: Session = Depends(get_db)):
    user = _get_session_user(request, db)
    if not user:
        return RedirectResponse("/login")
    return templates.TemplateResponse(
        request=request,
        name="profile.html",
        context=_profile_context(
            request,
            db,
            user,
            force_password_change=request.query_params.get("force_password_change") == "1",
        ),
    )


@router.post("/profile/dismiss-chain-explainer")
async def dismiss_chain_explainer(request: Request, db: Session = Depends(get_db)):
    """Mark the chain-hero explainer as seen for this user.

    Called by the 'Got it' button on the dashboard's first-view explainer
    callout. Idempotent — repeated calls just keep the flag set.
    """
    user = _get_session_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)
    prefs = dict(user.preferences or {})
    prefs["chain_hero_seen"] = True
    user.preferences = prefs
    db.commit()
    return RedirectResponse("/", status_code=303)


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
    from mailfallback.services.audit_service import log_action

    log_action(
        db,
        user=user,
        action="user.store_change",
        resource_type="user",
        resource_id=user.id,
        resource_name=user.username,
        ip_address=request.client.host if request.client else None,
        details={"store_id": new_store_id},
    )
    return RedirectResponse("/profile", status_code=303)


@router.post("/profile/password")
async def profile_change_password(request: Request, db: Session = Depends(get_db)):
    user = _get_session_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)

    form = await request.form()
    # SSO-provisioned users have no password_hash and the form omits the field
    current = form.get("current_password", "")
    new = form["new_password"]
    confirm = form["confirm_password"]

    if user.password_hash and not verify_password(current, user.password_hash):
        return templates.TemplateResponse(
            request=request,
            name="profile.html",
            context=_profile_context(request, db, user, error="Current password is incorrect"),
        )
    if new != confirm:
        return templates.TemplateResponse(
            request=request,
            name="profile.html",
            context=_profile_context(request, db, user, error="New passwords do not match"),
        )
    if len(new) < MIN_PASSWORD_LENGTH:
        return templates.TemplateResponse(
            request=request,
            name="profile.html",
            context=_profile_context(
                request,
                db,
                user,
                error=f"Password must be at least {MIN_PASSWORD_LENGTH} characters",
            ),
        )

    try:
        change_password(db, user.id, new)
    except ValueError as e:
        return templates.TemplateResponse(
            request=request,
            name="profile.html",
            context=_profile_context(request, db, user, error=str(e)),
        )
    from mailfallback.services.audit_service import log_action

    log_action(
        db,
        user=user,
        action="user.password_change",
        resource_type="user",
        resource_id=user.id,
        resource_name=user.username,
        ip_address=request.client.host if request.client else None,
    )
    return templates.TemplateResponse(
        request=request,
        name="profile.html",
        context=_profile_context(request, db, user, success="Password updated successfully"),
    )


_VALID_EVENT_KEYS = frozenset(_ns.EVENT_KEYS)


@router.post("/profile/notifications")
async def add_notification_channel(request: Request, db: Session = Depends(get_db)):
    user = _get_session_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)
    form = await request.form()
    from mailfallback.models import NotificationChannel
    from mailfallback.security import encrypt_credentials
    from mailfallback.services.audit_service import log_action

    events = [e for e in form.getlist("events") if e in _VALID_EVENT_KEYS]
    fmt = form.get("payload_format")
    payload_format = fmt if fmt in {"text", "json"} else "text"
    ch = NotificationChannel(
        user_id=user.id,
        label=form["label"],
        apprise_url=encrypt_credentials(form["apprise_url"], settings.secret_key),
        events=events,
        payload_format=payload_format,
    )
    db.add(ch)
    db.commit()
    log_action(
        db,
        user=user,
        action="user.notification_channel_add",
        resource_type="notification_channel",
        resource_id=ch.id,
        resource_name=ch.label,
        ip_address=request.client.host if request.client else None,
    )
    return RedirectResponse("/profile", status_code=303)


@router.post("/profile/notifications/{channel_id}/delete")
async def delete_notification_channel(
    channel_id: str, request: Request, db: Session = Depends(get_db)
):
    user = _get_session_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)
    from mailfallback.models import NotificationChannel
    from mailfallback.services.audit_service import log_action

    ch = db.query(NotificationChannel).filter_by(id=channel_id, user_id=user.id).first()
    if ch:
        label = ch.label
        db.delete(ch)
        db.commit()
        log_action(
            db,
            user=user,
            action="user.notification_channel_delete",
            resource_type="notification_channel",
            resource_id=channel_id,
            resource_name=label,
            ip_address=request.client.host if request.client else None,
        )
    return RedirectResponse("/profile", status_code=303)


@router.post("/profile/notifications/{channel_id}/toggle")
async def toggle_notification_channel(
    channel_id: str, request: Request, db: Session = Depends(get_db)
):
    user = _get_session_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)
    from mailfallback.models import NotificationChannel

    ch = db.query(NotificationChannel).filter_by(id=channel_id, user_id=user.id).first()
    if ch:
        ch.enabled = not ch.enabled
        db.commit()
    return RedirectResponse("/profile", status_code=303)


@router.post("/profile/notifications/{channel_id}/update")
async def update_notification_channel(
    channel_id: str, request: Request, db: Session = Depends(get_db)
):
    user = _get_session_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)
    from mailfallback.models import NotificationChannel
    from mailfallback.security import encrypt_credentials
    from mailfallback.services.audit_service import log_action

    ch = db.query(NotificationChannel).filter_by(id=channel_id, user_id=user.id).first()
    if ch:
        form = await request.form()
        label = (form.get("label") or "").strip()
        if label:
            ch.label = label
        ch.events = [e for e in form.getlist("events") if e in _VALID_EVENT_KEYS]
        fmt = form.get("payload_format")
        ch.payload_format = fmt if fmt in {"text", "json"} else "text"
        new_url = (form.get("apprise_url") or "").strip()
        if new_url:
            ch.apprise_url = encrypt_credentials(new_url, settings.secret_key)
        db.commit()
        log_action(
            db,
            user=user,
            action="user.notification_channel_update",
            resource_type="notification_channel",
            resource_id=ch.id,
            resource_name=ch.label,
            ip_address=request.client.host if request.client else None,
        )
    return RedirectResponse("/profile", status_code=303)


@router.post("/profile/notifications/{channel_id}/test")
async def test_notification_channel(
    channel_id: str, request: Request, db: Session = Depends(get_db)
):
    user = _get_session_user(request, db)
    if not user:
        return Response(status_code=403)
    from mailfallback.models import NotificationChannel
    from mailfallback.services import notification_service

    ch = db.query(NotificationChannel).filter_by(id=channel_id, user_id=user.id).first()
    if not ch:
        return Response(status_code=404)
    ok = notification_service.send_to_channel(ch, "MailFallBack test", "Notifications are working.")
    message = f"Test sent to {ch.label}" if ok else f"Test failed for {ch.label} — check the URL"
    trigger = json.dumps(
        {"notifyToast": {"message": message, "type": "success" if ok else "error"}}
    )
    return HTMLResponse("", status_code=200, headers={"HX-Trigger": trigger})


@router.post("/profile/tokens")
async def create_access_token(request: Request, db: Session = Depends(get_db)):
    user = _get_session_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)
    from mailfallback.services.audit_service import log_action

    form = await request.form()
    # Scope names arrive from the request: keep only known ones, so a crafted
    # form cannot write an unrecognised scope into the row.
    scopes = [s for s in form.getlist("scopes") if s in app_credential_service.VALID_SCOPES]
    raw_ttl = (form.get("ttl_days") or "").strip()
    ttl_days = int(raw_ttl) if raw_ttl.isdigit() and int(raw_ttl) > 0 else None

    try:
        cred, token = app_credential_service.create_credential(
            db,
            user,
            name=(form.get("name") or "").strip() or "Unnamed token",
            scopes=scopes,
            ttl_days=ttl_days,
        )
    except ValueError as e:
        return templates.TemplateResponse(
            request=request,
            name="profile.html",
            context=_profile_context(request, db, user, error=str(e)),
        )

    log_action(
        db,
        user=user,
        action="token.create",
        resource_type="app_credential",
        resource_id=cred.id,
        resource_name=cred.name,
        ip_address=request.client.host if request.client else None,
    )
    # Rendered, not redirected: the secret exists only in this response.
    return templates.TemplateResponse(
        request=request,
        name="profile.html",
        context=_profile_context(request, db, user, new_token=token),
    )


@router.post("/profile/tokens/{credential_id}/revoke")
async def revoke_access_token(credential_id: str, request: Request, db: Session = Depends(get_db)):
    user = _get_session_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)
    from mailfallback.services.audit_service import log_action

    if app_credential_service.revoke_credential(db, user, credential_id):
        log_action(
            db,
            user=user,
            action="token.revoke",
            resource_type="app_credential",
            resource_id=credential_id,
            ip_address=request.client.host if request.client else None,
        )
    return RedirectResponse("/profile", status_code=303)


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
