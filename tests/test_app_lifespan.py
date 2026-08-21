# tests/test_app_lifespan.py
"""Lifespan crash-recovery wiring: the zombie sweep runs at boot through
the worker's recover_zombie_sync_jobs, and a sweep failure never blocks
startup (error-isolated, session rolled back)."""

from unittest.mock import MagicMock

from mailfallback import app as app_module


def test_recover_zombie_jobs_delegates_to_worker_sweep(monkeypatch):
    called = []
    monkeypatch.setattr(
        "mailfallback.services.sync_worker.recover_zombie_sync_jobs",
        lambda db: called.append(db) or 1,
    )
    fake_db = MagicMock()

    app_module._recover_zombie_jobs(fake_db)

    assert called == [fake_db]


def test_recover_zombie_jobs_failure_does_not_block_boot(monkeypatch):
    monkeypatch.setattr(
        "mailfallback.services.sync_worker.recover_zombie_sync_jobs",
        MagicMock(side_effect=RuntimeError("sweep exploded")),
    )
    fake_db = MagicMock()

    app_module._recover_zombie_jobs(fake_db)  # must not raise

    # The shared lifespan session is left clean for the next boot step.
    fake_db.rollback.assert_called_once()


def test_reload_dovecot_after_config_calls_reload_dovecot(monkeypatch):
    """Config is regenerated at every boot; nothing else tells Dovecot to
    re-read it, so the lifespan must call reload_dovecot() itself."""
    called = []
    monkeypatch.setattr(
        "mailfallback.services.dovecot_manager.reload_dovecot",
        lambda: called.append(True) or True,
    )

    app_module._reload_dovecot_after_config()

    assert called == [True]


def test_reload_dovecot_after_config_survives_a_raising_reload(monkeypatch):
    monkeypatch.setattr(
        "mailfallback.services.dovecot_manager.reload_dovecot",
        MagicMock(side_effect=RuntimeError("dovecot unreachable")),
    )

    app_module._reload_dovecot_after_config()  # must not raise


def test_lifespan_actually_calls_reload_dovecot_after_config(monkeypatch, tmp_path):
    """Guards the CALL SITE in lifespan(), not the helper.

    The two tests above prove _reload_dovecot_after_config()'s own behaviour,
    but neither would notice if someone deleted the call to it from
    lifespan() -- that's exactly the invisible failure I-1 is about (nothing
    in the UI shows that Dovecot never re-read its config). Only actually
    running startup catches that, so this test enters TestClient as a
    context manager to make the real ASGI lifespan execute, and asserts
    reload_dovecot was called during it.
    """
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, event
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from mailfallback.config import settings
    from mailfallback.db import Base

    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_conn, connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        # mail_index schema needs an attached DB on SQLite (no native schemas)
        cursor.execute("ATTACH DATABASE ':memory:' AS mail_index")
        cursor.close()

    Base.metadata.create_all(engine)
    monkeypatch.setattr(app_module, "SessionLocal", sessionmaker(bind=engine))
    # Skip the FTS-reindex path entirely -- it's boot machinery unrelated to
    # this test and would otherwise submit a background task on every run.
    monkeypatch.setattr(app_module, "needs_fts_reindex", lambda settings: False)
    monkeypatch.setattr(settings, "confs_path", str(tmp_path))
    monkeypatch.setattr(settings, "dovecot_api_key", "test-key")

    called = []
    monkeypatch.setattr(
        "mailfallback.services.dovecot_manager.reload_dovecot",
        lambda: called.append(True) or True,
    )

    application = app_module.create_app()
    with TestClient(application):
        pass

    assert called == [True]
