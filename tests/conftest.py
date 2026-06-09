# tests/conftest.py
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from mailfallback.app import create_app
from mailfallback.db import Base
from mailfallback.dependencies import get_db
from mailfallback.models import MailStore


@pytest.fixture
def db_session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    # Enable foreign key support in SQLite (required for ondelete="SET NULL")
    from sqlalchemy import event

    @event.listens_for(engine, "connect")
    def set_sqlite_pragma(dbapi_conn, connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        # mail_index schema needs an attached DB on SQLite (no native schemas)
        cursor.execute("ATTACH DATABASE ':memory:' AS mail_index")
        cursor.close()

    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def default_store(db_session):
    """Create and return a default MailStore for tests that need one."""
    store = MailStore(name="default", path="/data/mailboxes")
    db_session.add(store)
    db_session.commit()
    db_session.refresh(store)
    return store


@pytest.fixture
def app(db_session):
    import tempfile

    import mailfallback.config as cfg

    original_key = cfg.settings.dovecot_api_key
    original_metrics_key = cfg.settings.metrics_api_key
    original_confs_path = cfg.settings.confs_path
    cfg.settings.dovecot_api_key = "test-key"
    cfg.settings.metrics_api_key = "test-key"
    cfg.settings.confs_path = tempfile.mkdtemp()
    application = create_app()
    application.dependency_overrides[get_db] = lambda: db_session
    yield application
    cfg.settings.dovecot_api_key = original_key
    cfg.settings.metrics_api_key = original_metrics_key
    cfg.settings.confs_path = original_confs_path


@pytest.fixture
def client(app):
    from mailfallback.middleware.rate_limit import reset_rate_limits

    reset_rate_limits()
    return TestClient(app)


@pytest.fixture
def login_user(db_session, default_store):
    from mailfallback.models import User, UserRole
    from mailfallback.security import hash_password

    u = User(
        username="koma",
        password_hash=hash_password("x"),
        role=UserRole.admin,
        enabled=True,
        store_id=default_store.id,
    )
    db_session.add(u)
    db_session.commit()
    return u
