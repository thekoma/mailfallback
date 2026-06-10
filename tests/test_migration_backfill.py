# tests/test_migration_backfill.py
"""Migration 017 backfill: existing users get all existing repositories."""

import sqlalchemy as sa
from alembic.config import Config

from alembic import command


def _alembic_config(db_url: str) -> Config:
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", db_url)
    return cfg


def test_migration_017_backfills_grants(tmp_path, monkeypatch):
    # env.py prefers MAILFALLBACK_DATABASE_URL over the config URL
    monkeypatch.delenv("MAILFALLBACK_DATABASE_URL", raising=False)
    db_url = f"sqlite:///{tmp_path}/mig.db"
    cfg = _alembic_config(db_url)

    command.upgrade(cfg, "016")

    engine = sa.create_engine(db_url)
    with engine.begin() as conn:
        conn.execute(
            sa.text("INSERT INTO mail_stores (id, name, path) VALUES ('st1', 's', '/data/x')")
        )
        for uid in ("u1", "u2"):
            conn.execute(
                sa.text(
                    "INSERT INTO users "
                    "(id, username, role, enabled, store_id, migrating, preferences) "
                    "VALUES (:id, :id, 'user', 1, 'st1', 0, '{}')"
                ),
                {"id": uid},
            )
        for rid in ("r1", "r2"):
            conn.execute(
                sa.text(
                    # created_at's server_default is now(), which SQLite
                    # cannot evaluate — supply it explicitly.
                    "INSERT INTO backup_destinations "
                    "(id, name, backend_type, restic_password, insecure_tls, "
                    "config_backup_enabled, created_at) "
                    "VALUES (:id, :id, 's3', 'enc', 0, 0, CURRENT_TIMESTAMP)"
                ),
                {"id": rid},
            )
    engine.dispose()

    command.upgrade(cfg, "017")

    engine = sa.create_engine(db_url)
    with engine.connect() as conn:
        rows = conn.execute(
            sa.text("SELECT user_id, repository_id FROM user_allowed_repositories")
        ).fetchall()
    engine.dispose()
    assert sorted(rows) == [("u1", "r1"), ("u1", "r2"), ("u2", "r1"), ("u2", "r2")]
