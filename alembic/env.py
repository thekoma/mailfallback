# alembic/env.py
import os
from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

from alembic import context
from mailfallback.db import Base
from mailfallback.models import (  # noqa: F401 — register models
    Account,
    AuditLog,
    MailIndexMessage,
    MailIndexRebuildStatus,
    MailStore,
    RestoreJob,
    SnapshotMessage,
    StoreMigration,
    SyncJob,
    User,
)

config = context.config

if config.config_file_name is not None:
    # disable_existing_loggers defaults to True, which silently disables every
    # logger created before this point. In-process alembic runs (tests,
    # migration resume in app lifespan) would otherwise kill application
    # loggers — e.g. caplog assertions in any test running after
    # test_alembic_sync.py in the same worker.
    fileConfig(config.config_file_name, disable_existing_loggers=False)

db_url = os.environ.get("MAILFALLBACK_DATABASE_URL")
if db_url:
    config.set_main_option("sqlalchemy.url", db_url)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        include_schemas=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = config.attributes.get("connection")
    if connectable is None:
        connectable = engine_from_config(
            config.get_section(config.config_ini_section, {}),
            prefix="sqlalchemy.",
            poolclass=pool.NullPool,
        )

    if hasattr(connectable, "connect"):
        with connectable.connect() as connection:
            context.configure(
                connection=connection,
                target_metadata=target_metadata,
                include_schemas=True,
            )
            with context.begin_transaction():
                context.run_migrations()
    else:
        context.configure(
            connection=connectable,
            target_metadata=target_metadata,
            include_schemas=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
