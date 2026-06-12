# tests/test_migration_backfill.py
"""Migration backfills: 017 (repository grants), 021 (initial-sync markers)."""

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


def test_migration_021_backfills_initial_sync(tmp_path, monkeypatch):
    """Accounts that have ever completed a sync skip the initial-sync regime:
    021 copies last_sync_at into initial_sync_completed_at; never-synced
    accounts stay NULL (the regime applies). The ledger column lands NOT NULL
    at 0 on existing rows (server_default)."""
    monkeypatch.delenv("MAILFALLBACK_DATABASE_URL", raising=False)
    db_url = f"sqlite:///{tmp_path}/mig21.db"
    cfg = _alembic_config(db_url)

    command.upgrade(cfg, "020")

    engine = sa.create_engine(db_url)
    with engine.begin() as conn:
        conn.execute(
            sa.text("INSERT INTO mail_stores (id, name, path) VALUES ('st1', 's', '/data/x')")
        )
        base_cols = (
            "INSERT INTO accounts (id, name, email_address, provider, imap_host, "
            "imap_port, auth_type, tls_type, maildir_path, sync_state, "
            "total_messages, unread_messages, maildir_size_bytes, store_id, "
            "enabled, suspended, migrating{extra_col}) "
            "VALUES (:id, :id, '', 'other', 'h', 993, 'app_password', 'IMAPS', "
            ":maildir, 'idle', 0, 0, 0, 'st1', 1, 0, 0{extra_val})"
        )
        conn.execute(
            sa.text(base_cols.format(extra_col=", last_sync_at", extra_val=", :ls")),
            {"id": "a-synced", "maildir": "/m/a1", "ls": "2026-06-01 10:00:00"},
        )
        conn.execute(
            sa.text(base_cols.format(extra_col="", extra_val="")),
            {"id": "a-fresh", "maildir": "/m/a2"},
        )
    engine.dispose()

    command.upgrade(cfg, "021")

    engine = sa.create_engine(db_url)
    with engine.connect() as conn:
        rows = conn.execute(
            sa.text(
                "SELECT id, initial_sync_completed_at, bytes_synced_today FROM accounts ORDER BY id"
            )
        ).fetchall()
        job_cols = {r[1] for r in conn.execute(sa.text("PRAGMA table_info(sync_jobs)")).fetchall()}
    engine.dispose()
    assert rows[0][0] == "a-fresh"
    assert rows[0][1] is None  # never synced -> initial-sync regime applies
    assert rows[1][0] == "a-synced"
    assert rows[1][1] is not None  # backfilled from last_sync_at
    assert [r[2] for r in rows] == [0, 0]  # NOT NULL ledger via server_default
    assert "failure_kind" in job_cols
