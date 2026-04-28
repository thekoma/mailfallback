# src/mailfallback/app.py
from fastapi import FastAPI
from starlette.middleware.sessions import SessionMiddleware

from mailfallback.config import settings
from mailfallback.routers import accounts, auth


def create_app() -> FastAPI:
    app = FastAPI(title="Mailfallback", version="0.1.0")
    app.add_middleware(SessionMiddleware, secret_key=settings.session_secret)
    app.include_router(auth.router)
    app.include_router(accounts.router)

    @app.get("/healthz")
    async def healthz():
        return {"status": "ok"}

    return app


app = create_app()
