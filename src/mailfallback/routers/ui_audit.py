from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from mailfallback.dependencies import get_db
from mailfallback.models import AuditLog
from mailfallback.routers.ui import _get_session_user, templates
from mailfallback.services.audit_service import get_action_label
from mailfallback.services.user_service import list_users

router = APIRouter(tags=["ui"])

PAGE_SIZE = 50


def _build_query(db: Session, params):
    from datetime import datetime

    q = db.query(AuditLog).order_by(AuditLog.timestamp.desc())
    if params.get("user"):
        q = q.filter(AuditLog.username == params["user"])
    if params.get("action"):
        q = q.filter(AuditLog.action == params["action"])
    if params.get("from"):
        try:
            dt = datetime.fromisoformat(params["from"])
            q = q.filter(AuditLog.timestamp >= dt)
        except (ValueError, TypeError):
            pass
    if params.get("to"):
        try:
            dt = datetime.fromisoformat(params["to"])
            q = q.filter(AuditLog.timestamp <= dt)
        except (ValueError, TypeError):
            pass
    return q


@router.get("/admin/audit", response_class=HTMLResponse)
def admin_audit_page(request: Request, db: Session = Depends(get_db)):
    user = _get_session_user(request, db)
    if not user or user.role.value != "admin":
        return RedirectResponse("/")

    params = dict(request.query_params)
    try:
        page = int(params.pop("page", 1))
        if page < 1:
            page = 1
    except (ValueError, TypeError):
        page = 1
    q = _build_query(db, params)
    total = q.count()
    entries = q.offset((page - 1) * PAGE_SIZE).limit(PAGE_SIZE).all()
    total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)

    all_users = list_users(db)
    distinct_actions = [
        r[0] for r in db.query(AuditLog.action).distinct().order_by(AuditLog.action).all()
    ]

    return templates.TemplateResponse(
        request=request,
        name="admin_audit.html",
        context={
            "user": user,
            "entries": entries,
            "page": page,
            "total_pages": total_pages,
            "total": total,
            "all_users": all_users,
            "distinct_actions": distinct_actions,
            "get_action_label": get_action_label,
            "filters": params,
        },
    )


@router.get("/admin/audit/table", response_class=HTMLResponse)
def admin_audit_table(request: Request, db: Session = Depends(get_db)):
    user = _get_session_user(request, db)
    if not user or user.role.value != "admin":
        return HTMLResponse("")

    params = dict(request.query_params)
    try:
        page = int(params.pop("page", 1))
        if page < 1:
            page = 1
    except (ValueError, TypeError):
        page = 1
    q = _build_query(db, params)
    total = q.count()
    entries = q.offset((page - 1) * PAGE_SIZE).limit(PAGE_SIZE).all()
    total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)

    return templates.TemplateResponse(
        request=request,
        name="partials/audit_table.html",
        context={
            "entries": entries,
            "page": page,
            "total_pages": total_pages,
            "total": total,
            "get_action_label": get_action_label,
            "filters": params,
        },
    )
