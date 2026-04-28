from fastapi import FastAPI


def create_app() -> FastAPI:
    app = FastAPI(title="Mailfallback", version="0.1.0")

    @app.get("/healthz")
    async def healthz():
        return {"status": "ok"}

    return app


app = create_app()
