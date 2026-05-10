# src/mailfallback/middleware/force_password_change.py
"""Block dashboard/admin pages when the admin still has the default password.

On a fresh `docker compose up`, MFB bootstraps an admin user with the
documented default password (see services/user_service.ensure_admin_exists).
Until that password is changed, this middleware redirects HTML page
requests to /profile so the admin can't accidentally use the system in
its insecure state.

Exempt paths (always pass through):
- /profile + /profile/* (the page where the password change happens)
- /login, /logout (need to log in/out to reach the prompt)
- /api/* (programmatic clients; exempt from the redirect heuristic)
- /static/* (CSS/JS/icons)
- /healthz, /readyz, /metrics (operational endpoints)
- /partials/* (HTMX fragments — they belong to the page that referred them)

Non-admin users and admins with a changed password are unaffected.
"""

import contextlib

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import RedirectResponse

from mailfallback.db import SessionLocal
from mailfallback.dependencies import get_db
from mailfallback.models import User, UserRole
from mailfallback.security import verify_password

DEFAULT_ADMIN_PASSWORD = "changeme1234!"  # pragma: allowlist secret

EXEMPT_PREFIXES = (
    "/profile",
    "/login",
    "/logout",
    "/api/",
    "/static/",
    "/healthz",
    "/readyz",
    "/metrics",
    "/partials/",
)


def _is_exempt(path: str) -> bool:
    return any(path == prefix.rstrip("/") or path.startswith(prefix) for prefix in EXEMPT_PREFIXES)


class ForcePasswordChangeMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.method != "GET" or _is_exempt(request.url.path):
            return await call_next(request)

        user_id = request.session.get("user_id") if "session" in request.scope else None
        if not user_id:
            return await call_next(request)

        # Respect FastAPI dependency_overrides so tests using an in-memory
        # SQLite session work; fall back to SessionLocal in production.
        override = request.app.dependency_overrides.get(get_db) if request.app else None
        db = None
        db_gen = None
        if override is not None:
            result = override()
            # Override may be a generator (yield-style) or a plain return.
            if hasattr(result, "__next__"):
                db_gen = result
                try:
                    db = next(db_gen)
                except StopIteration:
                    return await call_next(request)
            else:
                db = result
        else:
            db = SessionLocal()

        try:
            user = db.query(User).filter(User.id == user_id).first()
            if not user or user.role != UserRole.admin:
                return await call_next(request)
            try:
                still_default = verify_password(DEFAULT_ADMIN_PASSWORD, user.password_hash)
            except (ValueError, TypeError):
                still_default = False
            if still_default:
                return RedirectResponse("/profile?force_password_change=1", status_code=303)
        finally:
            if db_gen is not None:
                with contextlib.suppress(StopIteration):
                    next(db_gen)
            elif override is None:
                # Only close sessions WE opened; leave override-provided alone.
                db.close()

        return await call_next(request)
