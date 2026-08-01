# src/mailfallback/app.py
import logging
import threading
import warnings
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from mailfallback.config import settings
from mailfallback.db import SessionLocal
from mailfallback.models import MigrationStatus, StoreMigration
from mailfallback.routers import (
    accounts,
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
    yield
    stop_scheduler()
    shutdown_sync_executor()
    from mailfallback.services.backup_worker import shutdown_backup_executor

    shutdown_backup_executor()


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
    app.include_router(sync.router)
    app.include_router(health.router)
    app.include_router(config_io.router)
    app.include_router(dovecot.router)
    app.include_router(ui_restore.router)
    app.include_router(restore_router)
    app.include_router(restore_browse_router)

    @app.get("/healthz")
    async def healthz():
        return {"status": "ok", "version": __version__}

    return app


app = create_app()
