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
