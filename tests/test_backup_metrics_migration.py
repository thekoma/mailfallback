"""Exercise upgrade/backfill/downgrade on existing repository rows."""

import importlib.util
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, text


def test_config_success_backfill_is_conservative_and_reversible():
    path = Path(__file__).resolve().parents[1] / "alembic/versions/030_config_backup_success.py"
    assert path.exists(), "config success requires a migration"
    spec = importlib.util.spec_from_file_location("migration_030_test", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE backup_destinations (id TEXT PRIMARY KEY, "
                "last_config_backup_at TIMESTAMP, last_config_backup_status TEXT)"
            )
        )
        for key, status in [("success", "ok"), ("failure", "failed"), ("unknown", None)]:
            conn.execute(
                text(
                    "INSERT INTO backup_destinations VALUES (:id, '2026-01-02 00:00:00', :status)"
                ),
                {"id": key, "status": status},
            )
        with Operations.context(MigrationContext.configure(conn)):
            migration.upgrade()
            rows = dict(
                conn.execute(
                    text("SELECT id, last_config_backup_success_at FROM backup_destinations")
                ).all()
            )
            assert rows == {"success": "2026-01-02 00:00:00", "failure": None, "unknown": None}
            migration.downgrade()
        assert "last_config_backup_success_at" not in {
            c["name"] for c in inspect(conn).get_columns("backup_destinations")
        }
    engine.dispose()
