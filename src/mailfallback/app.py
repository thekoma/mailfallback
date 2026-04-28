# src/mailfallback/app.py
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from mailfallback.config import settings
from mailfallback.db import SessionLocal
from mailfallback.routers import accounts, auth, config_io, health, sync, ui
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
    app = FastAPI(
        title="MailFallBack",
        description="Self-hosted email backup service",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.add_middleware(SessionMiddleware, secret_key=settings.session_secret)

    static_dir = Path(__file__).parent / "static"
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    app.include_router(ui.router)
    app.include_router(auth.router)
    app.include_router(accounts.router)
    app.include_router(sync.router)
    app.include_router(health.router)
    app.include_router(config_io.router)

    @app.get("/healthz")
    async def healthz():
        return {"status": "ok"}

    return app


app = create_app()
