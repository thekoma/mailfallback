# src/mailfallback/app.py
from contextlib import asynccontextmanager

from fastapi import FastAPI
from starlette.middleware.sessions import SessionMiddleware

from mailfallback.config import settings
from mailfallback.db import SessionLocal
from mailfallback.routers import accounts, auth, health, sync
from mailfallback.services.scheduler import start_scheduler, stop_scheduler
from mailfallback.services.user_service import ensure_admin_exists


@asynccontextmanager
async def lifespan(app: FastAPI):
    db = SessionLocal()
    try:
        ensure_admin_exists(db)
        start_scheduler(db)
    finally:
        db.close()
    yield
    stop_scheduler()


def create_app() -> FastAPI:
    app = FastAPI(title="Mailfallback", version="0.1.0", lifespan=lifespan)
    app.add_middleware(SessionMiddleware, secret_key=settings.session_secret)
    app.include_router(auth.router)
    app.include_router(accounts.router)
    app.include_router(sync.router)
    app.include_router(health.router)

    @app.get("/healthz")
    async def healthz():
        return {"status": "ok"}

    return app


app = create_app()
