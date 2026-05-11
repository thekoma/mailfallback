# tests/test_alembic_sync.py
"""Verify Alembic migrations produce a schema matching SQLAlchemy models."""

from pathlib import Path

from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, event
from sqlalchemy.pool import StaticPool

from alembic import command
from mailfallback.db import Base

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def test_no_migration_drift():
    """Fail if models.py defines tables/columns not covered by migrations."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def set_sqlite_pragma(dbapi_conn, connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    alembic_cfg = Config(str(PROJECT_ROOT / "alembic.ini"))
    alembic_cfg.set_main_option("sqlalchemy.url", "sqlite://")
    alembic_cfg.attributes["connection"] = engine.connect()

    with engine.connect() as conn:
        alembic_cfg.attributes["connection"] = conn
        command.upgrade(alembic_cfg, "head")

        mc = MigrationContext.configure(
            conn,
            opts={"include_schemas": True},
        )
        diff = compare_metadata(mc, Base.metadata)

    meaningful = [op for op in diff if _is_meaningful(op)]

    if meaningful:
        lines = []
        for op in meaningful:
            lines.append(f"  {op}")
        msg = "Migration drift detected — models.py has changes not in migrations:\n"
        raise AssertionError(msg + "\n".join(lines))


def _is_meaningful(op):
    """Filter out SQLite-specific false positives."""
    if isinstance(op, tuple):
        op_type = op[0]
        # SQLite doesn't support server_default comparison well
        if op_type == "modify_default":
            return False
        # SQLite Enum rendering differs from PostgreSQL
        if op_type == "modify_type":
            return False
        # SQLite ATTACHed databases don't reflect FKs / indexes back to the
        # comparator; tolerate "added FK" / "removed index" noise from the
        # mail_index schema (real Postgres deployments are checked separately).
        if op_type in ("add_fk", "remove_fk", "add_index", "remove_index"):
            obj = op[1] if len(op) > 1 else None
            schema = getattr(obj, "schema", None) or getattr(
                getattr(obj, "table", None), "schema", None
            )
            if schema == "mail_index":
                return False
    return True
