# src/mailfallback/app.py
import logging
import threading
import warnings
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from mailfallback.config import settings
from mailfallback.db import SessionLocal
from mailfallback.models import MigrationStatus, StoreMigration
from mailfallback.routers import (
    accounts,
    agent,
    auth,
    config_io,
    dovecot,
    health,
    sync,
    ui,
    ui_accounts,
    ui_admin,
    ui_audit,
    ui_backup,
    ui_profile,
    ui_restore,
)
from mailfallback.routers.restore import browse_router as restore_browse_router
from mailfallback.routers.restore import router as restore_router
from mailfallback.services.config_generator import (
    clear_fts_reindex_flag,
    generate_all_configs,
    needs_fts_reindex,
)
from mailfallback.services.migration_service import (
    execute_account_migration,
    execute_home_migration,
)
from mailfallback.services.scheduler import start_scheduler, stop_scheduler
from mailfallback.services.store_service import ensure_default_store, set_allowed_stores
from mailfallback.services.sync_worker import shutdown_sync_executor
from mailfallback.services.user_service import ensure_admin_exists
from mailfallback.version import __version__

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    generate_all_configs(settings)
    _reload_dovecot_after_config()
    db = SessionLocal()
    try:
        default_store = ensure_default_store(db)
        ensure_admin_exists(db, default_store.id)
        _backfill_allowed_stores(db)
        _recover_zombie_jobs(db)
        _cleanup_temp_restore_users(db)
        start_scheduler(db)
        _resume_migrations(db)
        if needs_fts_reindex(settings):
            from mailfallback.services.background_tasks import submit_fts_reindex

            logger.info("FTS config changed — triggering automatic reindex")
            submit_fts_reindex(db, None)
            clear_fts_reindex_flag(settings)
    finally:
        db.close()

    # A mounted ASGI app's own lifespan never runs — Starlette only enters the
    # lifespan of the app it was handed directly. The MCP session manager's
    # `run()` context has to be entered here instead, or every request against
    # it fails with an anyio task-group error rather than a readable response.
    # AsyncExitStack keeps this function to a single `yield`: entering the
    # session manager conditionally around a second `yield` would give the
    # generator two suspension points, which `@asynccontextmanager` rejects.
    from mailfallback.mcp_server import get_server

    async with AsyncExitStack() as stack:
        mcp_server = get_server(settings)
        if mcp_server is not None:
            await stack.enter_async_context(mcp_server.session_manager.run())
        yield

    stop_scheduler()
    shutdown_sync_executor()
    from mailfallback.services.backup_worker import shutdown_backup_executor

    shutdown_backup_executor()


def _reload_dovecot_after_config() -> None:
    """Best-effort Dovecot reload so freshly written config is actually read.

    ``generate_all_configs`` above only writes files to the shared confs
    volume; nothing makes Dovecot re-read them. On docker compose this is
    masked by ``depends_on`` recreating the dovecot container, but on
    Kubernetes an app-only upgrade can leave the dovecot pod running against
    stale config (new access tokens silently fail to authenticate, ACL
    changes don't take effect) with nothing in the UI to explain why.

    Never allowed to fail startup: ``reload_dovecot()`` already catches its
    own exceptions and returns a bool, but this wrapper catches again
    defensively and only logs.
    """
    from mailfallback.services.dovecot_manager import reload_dovecot

    try:
        reloaded = reload_dovecot()
    except Exception:
        logger.info("Dovecot reload raised unexpectedly -- continuing boot", exc_info=True)
        return

    if reloaded:
        logger.info("Dovecot reloaded to pick up regenerated config")
    else:
        logger.info(
            "Dovecot reload did not succeed -- normal at first start on docker "
            "compose, where dovecot's depends_on makes it start after the app "
            "so it may not be up yet; if it persists once dovecot is up, "
            "tokens and ACL changes may be serving stale config"
        )


def _backfill_allowed_stores(db):
    from mailfallback.models import User

    for u in db.query(User).all():
        if not u.allowed_stores:
            set_allowed_stores(db, u.id, [u.store_id])


def _recover_zombie_jobs(db):
    # Delegated to each worker's sweep — they own their in-memory process
    # registries and their resume policy (sync-budget spec §9). Error-isolated
    # SEPARATELY: a failure in one sweep must not skip the other, must never
    # block boot, and must leave the shared lifespan session clean for the
    # next boot step.
    from mailfallback.services.backup_worker import recover_zombie_backup_jobs
    from mailfallback.services.sync_worker import recover_zombie_sync_jobs

    for sweep, label in (
        (recover_zombie_sync_jobs, "Zombie sync job sweep"),
        (recover_zombie_backup_jobs, "Zombie backup job sweep"),
    ):
        try:
            sweep(db)
        except Exception:
            logger.exception("%s failed — continuing boot", label)
            db.rollback()


def _cleanup_temp_restore_users(db):
    from mailfallback.services.dovecot_auth import cleanup_temp_imap_users

    count = cleanup_temp_imap_users(db)
    if count:
        logger.info("Cleaned up %d orphaned restore users", count)


def _resume_migrations(db):
    from datetime import UTC, datetime

    incomplete = (
        db.query(StoreMigration)
        .filter(
            StoreMigration.status.in_(
                [
                    MigrationStatus.pending,
                    MigrationStatus.copying,
                    MigrationStatus.verifying,
                    MigrationStatus.cleaning,
                ]
            )
        )
        .all()
    )
    for migration in incomplete:
        migration.last_resumed_at = datetime.now(UTC)
        db.commit()

        if migration.account_id:
            execute_fn = execute_account_migration
            label = f"account {migration.account_id}"
        else:
            execute_fn = execute_home_migration
            label = f"user {migration.user_id}"

        logger.info("Resuming migration %s for %s", migration.id, label)
        thread = threading.Thread(
            target=_run_migration,
            args=(migration.id, execute_fn),
            daemon=True,
        )
        thread.start()


def _run_migration(migration_id: str, execute_fn):
    db = SessionLocal()
    try:
        execute_fn(db, migration_id)
    except Exception:
        logger.exception("Migration %s failed with unhandled exception", migration_id)
    finally:
        db.close()


def create_app() -> FastAPI:
    warnings.filterwarnings("ignore", message="authlib.jose module is deprecated")

    log_level = logging.DEBUG if settings.debug else logging.INFO
    logging.basicConfig(level=log_level, format="%(levelname)s:     %(name)s - %(message)s")
    logging.getLogger("mailfallback").setLevel(log_level)

    app = FastAPI(
        title="MailFallBack",
        description="Self-hosted email backup service",
        version=__version__,
        lifespan=lifespan,
    )
    from mailfallback.middleware.force_password_change import ForcePasswordChangeMiddleware
    from mailfallback.middleware.rate_limit import RateLimitMiddleware

    # Order: SessionMiddleware first (innermost) so request.session is populated
    # by the time the others run. Starlette executes outermost-added FIRST on
    # the request path, so we add SessionMiddleware LAST.
    app.add_middleware(RateLimitMiddleware)
    app.add_middleware(ForcePasswordChangeMiddleware)
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.session_secret,
        same_site="lax",
        https_only=settings.session_https_only,
    )

    static_dir = Path(__file__).parent / "static"
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    app.include_router(ui.router)
    app.include_router(ui_accounts.router)
    app.include_router(ui_admin.router)
    app.include_router(ui_audit.router)
    app.include_router(ui_backup.router)
    app.include_router(ui_profile.router)
    app.include_router(auth.router)
    app.include_router(accounts.router)
    app.include_router(agent.router)
    app.include_router(sync.router)
    app.include_router(health.router)
    app.include_router(config_io.router)
    app.include_router(dovecot.router)
    app.include_router(ui_restore.router)
    app.include_router(restore_router)
    app.include_router(restore_browse_router)

    from mailfallback.mcp_server import MCP_PATH, get_server, mcp_asgi_app

    mcp_server = get_server(settings)
    if mcp_server is not None:
        app.mount(MCP_PATH, mcp_asgi_app(mcp_server, settings))
        logger.info("MCP server mounted at %s", MCP_PATH)

    @app.get("/healthz")
    async def healthz():
        return {"status": "ok", "version": __version__}

    return app


app = create_app()
