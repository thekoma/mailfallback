# Mailfallback Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a self-hosted email backup service that wraps mbsync with a FastAPI web UI, read-only webmail via Dovecot, multi-account support, and Prometheus metrics.

**Architecture:** Monolith orchestrator pattern — a single Python/FastAPI application manages configuration, scheduling, and sync orchestration. mbsync, Dovecot, and webmail run as separate Docker containers sharing Maildir volumes. Job queue pattern decouples sync triggers from execution.

**Tech Stack:** Python 3.12+, FastAPI, SQLAlchemy (SQLite/PostgreSQL), Alembic, HTMX, Jinja2, Pico CSS, APScheduler, prometheus-client, authlib, bcrypt, cryptography

---

## File Structure

```
mailfallback/
├── pyproject.toml
├── alembic.ini
├── src/
│   └── mailfallback/
│       ├── __init__.py
│       ├── app.py                    # FastAPI application factory
│       ├── config.py                 # Settings from env vars (pydantic-settings)
│       ├── db.py                     # SQLAlchemy engine + session factory
│       ├── models.py                 # All SQLAlchemy models
│       ├── security.py              # Password hashing + credential encryption
│       ├── dependencies.py          # FastAPI dependencies (get_db, get_current_user)
│       ├── routers/
│       │   ├── __init__.py
│       │   ├── auth.py              # Login/logout + OIDC routes
│       │   ├── accounts.py          # Account CRUD API
│       │   ├── sync.py              # Sync trigger + job status API
│       │   ├── health.py            # /healthz, /readyz, /metrics
│       │   ├── config_io.py         # Config export/import API
│       │   └── ui.py                # HTML page routes (dashboard, accounts, admin)
│       ├── services/
│       │   ├── __init__.py
│       │   ├── user_service.py      # User CRUD + role management
│       │   ├── account_service.py   # Account CRUD + ownership
│       │   ├── sync_service.py      # Job creation + deduplication
│       │   ├── sync_worker.py       # Background worker that runs mbsync
│       │   ├── mbsync_config.py     # Generate .mbsyncrc files
│       │   ├── dovecot_config.py    # Generate Dovecot user/namespace config
│       │   ├── scheduler.py         # APScheduler setup + job management
│       │   └── oauth2.py            # OAuth2 token management (Gmail etc.)
│       └── templates/
│           ├── base.html
│           ├── login.html
│           ├── dashboard.html
│           ├── account_form.html
│           ├── account_detail.html
│           ├── settings.html
│           └── admin_users.html
├── alembic/
│   ├── env.py
│   └── versions/
├── tests/
│   ├── conftest.py
│   ├── test_config.py
│   ├── test_models.py
│   ├── test_security.py
│   ├── test_auth.py
│   ├── test_accounts.py
│   ├── test_sync_service.py
│   ├── test_sync_worker.py
│   ├── test_mbsync_config.py
│   ├── test_dovecot_config.py
│   ├── test_health.py
│   ├── test_config_io.py
│   └── test_ui.py
├── docker/
│   ├── Dockerfile
│   ├── Dockerfile.mbsync
│   ├── entrypoint.sh
│   └── token_helper.sh
└── docker-compose.yml
```

---

## Task 1: Project Scaffolding

**Files:**
- Create: `pyproject.toml`
- Create: `src/mailfallback/__init__.py`
- Create: `src/mailfallback/config.py`
- Create: `src/mailfallback/app.py`
- Create: `tests/conftest.py`
- Create: `tests/test_config.py`

- [ ] **Step 1: Create pyproject.toml**

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "mailfallback"
version = "0.1.0"
description = "Self-hosted email backup service wrapping mbsync"
requires-python = ">=3.12"
dependencies = [
    "fastapi>=0.115",
    "uvicorn[standard]>=0.34",
    "sqlalchemy>=2.0",
    "alembic>=1.14",
    "pydantic-settings>=2.7",
    "jinja2>=3.1",
    "python-multipart>=0.0.18",
    "bcrypt>=4.2",
    "cryptography>=44.0",
    "apscheduler>=3.10,<4",
    "prometheus-client>=0.21",
    "authlib>=1.4",
    "httpx>=0.28",
    "itsdangerous>=2.2",
]

[project.optional-dependencies]
postgres = ["psycopg2-binary>=2.9"]
dev = [
    "pytest>=8.3",
    "pytest-asyncio>=0.25",
    "httpx>=0.28",
    "ruff>=0.8",
]

[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"

[tool.ruff]
target-version = "py312"
line-length = 100
```

- [ ] **Step 2: Create src/mailfallback/__init__.py**

```python
```

(Empty file — package marker only.)

- [ ] **Step 3: Create src/mailfallback/config.py**

```python
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    model_config = {"env_prefix": "MAILFALLBACK_"}

    database_url: str = "sqlite:///data/config/mailfallback.db"
    secret_key: str = "change-me-in-production"
    session_secret: str = "change-me-session-secret"

    mbsync_binary: str = "mbsync"
    maildir_base_path: str = "/data/mailboxes"
    config_path: str = "/data/config"

    oidc_enabled: bool = False
    oidc_client_id: str = ""
    oidc_client_secret: str = ""
    oidc_discovery_url: str = ""
    oidc_admin_group: str = "mailfallback-admin"
    oidc_user_group: str = "mailfallback-user"

    google_client_id: str = ""
    google_client_secret: str = ""

    dovecot_config_path: str = "/data/config/dovecot"


settings = Settings()
```

- [ ] **Step 4: Write failing test for config**

```python
# tests/test_config.py
import os

from mailfallback.config import Settings


def test_default_settings():
    s = Settings()
    assert s.database_url == "sqlite:///data/config/mailfallback.db"
    assert s.mbsync_binary == "mbsync"
    assert s.oidc_enabled is False


def test_settings_from_env(monkeypatch):
    monkeypatch.setenv("MAILFALLBACK_DATABASE_URL", "postgresql://localhost/mf")
    monkeypatch.setenv("MAILFALLBACK_OIDC_ENABLED", "true")
    s = Settings()
    assert s.database_url == "postgresql://localhost/mf"
    assert s.oidc_enabled is True
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd /home/koma/src/mailfallback && pip install -e ".[dev]" && pytest tests/test_config.py -v`
Expected: 2 PASSED

- [ ] **Step 6: Create minimal FastAPI app**

```python
# src/mailfallback/app.py
from fastapi import FastAPI


def create_app() -> FastAPI:
    app = FastAPI(title="Mailfallback", version="0.1.0")

    @app.get("/healthz")
    async def healthz():
        return {"status": "ok"}

    return app


app = create_app()
```

- [ ] **Step 7: Write test for app creation**

```python
# tests/conftest.py
import pytest
from fastapi.testclient import TestClient

from mailfallback.app import create_app


@pytest.fixture
def app():
    return create_app()


@pytest.fixture
def client(app):
    return TestClient(app)
```

Add to `tests/test_config.py`:

```python
def test_healthz(client):
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
```

- [ ] **Step 8: Run all tests**

Run: `pytest tests/ -v`
Expected: 3 PASSED

- [ ] **Step 9: Commit**

```bash
git add pyproject.toml src/ tests/
git commit -m "feat: project scaffolding with FastAPI, config, and tests"
```

---

## Task 2: Database Models

**Files:**
- Create: `src/mailfallback/db.py`
- Create: `src/mailfallback/models.py`
- Create: `tests/test_models.py`

- [ ] **Step 1: Create db.py**

```python
# src/mailfallback/db.py
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from mailfallback.config import settings


class Base(DeclarativeBase):
    pass


engine = create_engine(
    settings.database_url,
    connect_args={"check_same_thread": False} if "sqlite" in settings.database_url else {},
)
SessionLocal = sessionmaker(bind=engine)
```

- [ ] **Step 2: Create models.py**

```python
# src/mailfallback/models.py
import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, Enum, ForeignKey, Integer, String, Table, Text
from sqlalchemy.orm import relationship

from mailfallback.db import Base


def _utcnow():
    return datetime.now(timezone.utc)


def _new_uuid():
    return str(uuid.uuid4())


class UserRole(str, enum.Enum):
    admin = "admin"
    user = "user"


class AuthType(str, enum.Enum):
    oauth2 = "oauth2"
    app_password = "app_password"


class SyncState(str, enum.Enum):
    idle = "idle"
    syncing = "syncing"
    error = "error"


class JobStatus(str, enum.Enum):
    pending = "pending"
    running = "running"
    completed = "completed"
    failed = "failed"


account_owners = Table(
    "account_owners",
    Base.metadata,
    Column("account_id", String, ForeignKey("accounts.id"), primary_key=True),
    Column("user_id", String, ForeignKey("users.id"), primary_key=True),
)


class User(Base):
    __tablename__ = "users"

    id = Column(String, primary_key=True, default=_new_uuid)
    username = Column(String, unique=True, nullable=False, index=True)
    password_hash = Column(String, nullable=True)
    role = Column(Enum(UserRole), nullable=False, default=UserRole.user)
    oidc_subject = Column(String, nullable=True, unique=True)
    created_at = Column(DateTime(timezone=True), default=_utcnow)

    accounts = relationship("Account", secondary=account_owners, back_populates="owners")


class Account(Base):
    __tablename__ = "accounts"

    id = Column(String, primary_key=True, default=_new_uuid)
    name = Column(String, nullable=False)
    imap_host = Column(String, nullable=False)
    imap_port = Column(Integer, nullable=False, default=993)
    auth_type = Column(Enum(AuthType), nullable=False, default=AuthType.app_password)
    credentials = Column(Text, nullable=True)
    maildir_path = Column(String, nullable=False)
    sync_schedule = Column(String, nullable=True, default="0 */6 * * *")
    sync_state = Column(Enum(SyncState), nullable=False, default=SyncState.idle)
    last_sync_at = Column(DateTime(timezone=True), nullable=True)
    last_error = Column(Text, nullable=True)
    enabled = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), default=_utcnow)

    owners = relationship("User", secondary=account_owners, back_populates="accounts")
    sync_jobs = relationship("SyncJob", back_populates="account", cascade="all, delete-orphan")


class SyncJob(Base):
    __tablename__ = "sync_jobs"

    id = Column(String, primary_key=True, default=_new_uuid)
    account_id = Column(String, ForeignKey("accounts.id"), nullable=False, index=True)
    status = Column(Enum(JobStatus), nullable=False, default=JobStatus.pending)
    source = Column(String, nullable=False, default="api")
    requested_at = Column(DateTime(timezone=True), default=_utcnow)
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    exit_code = Column(Integer, nullable=True)
    log = Column(Text, nullable=True)

    account = relationship("Account", back_populates="sync_jobs")
```

- [ ] **Step 3: Write failing tests for models**

```python
# tests/test_models.py
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from mailfallback.db import Base
from mailfallback.models import (
    Account,
    AuthType,
    JobStatus,
    SyncJob,
    SyncState,
    User,
    UserRole,
    account_owners,
)


def make_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def test_create_user():
    session = make_session()
    user = User(username="admin", role=UserRole.admin)
    session.add(user)
    session.commit()
    assert user.id is not None
    assert user.username == "admin"
    assert user.role == UserRole.admin


def test_create_account_with_owner():
    session = make_session()
    user = User(username="testuser")
    account = Account(
        name="Gmail",
        imap_host="imap.gmail.com",
        imap_port=993,
        maildir_path="/data/mailboxes/gmail",
    )
    account.owners.append(user)
    session.add(account)
    session.commit()
    assert account.id is not None
    assert len(account.owners) == 1
    assert account.owners[0].username == "testuser"
    assert len(user.accounts) == 1


def test_create_sync_job():
    session = make_session()
    account = Account(
        name="Work",
        imap_host="imap.work.com",
        maildir_path="/data/mailboxes/work",
    )
    session.add(account)
    session.commit()
    job = SyncJob(account_id=account.id, source="api")
    session.add(job)
    session.commit()
    assert job.status == JobStatus.pending
    assert job.account.name == "Work"


def test_account_defaults():
    session = make_session()
    account = Account(
        name="Test",
        imap_host="imap.test.com",
        maildir_path="/data/mailboxes/test",
    )
    session.add(account)
    session.commit()
    assert account.auth_type == AuthType.app_password
    assert account.sync_state == SyncState.idle
    assert account.enabled is True
    assert account.imap_port == 993
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_models.py -v`
Expected: 4 PASSED

- [ ] **Step 5: Commit**

```bash
git add src/mailfallback/db.py src/mailfallback/models.py tests/test_models.py
git commit -m "feat: SQLAlchemy models for users, accounts, sync_jobs"
```

---

## Task 3: Alembic Migrations

**Files:**
- Create: `alembic.ini`
- Create: `alembic/env.py`

- [ ] **Step 1: Initialize Alembic**

Run: `cd /home/koma/src/mailfallback && alembic init alembic`

- [ ] **Step 2: Edit alembic.ini — set sqlalchemy.url**

In `alembic.ini`, change the `sqlalchemy.url` line to:

```ini
sqlalchemy.url = sqlite:///data/config/mailfallback.db
```

- [ ] **Step 3: Edit alembic/env.py to import models and support DATABASE_URL env var**

```python
# alembic/env.py
import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from mailfallback.db import Base
from mailfallback.models import Account, SyncJob, User  # noqa: F401 — register models

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

db_url = os.environ.get("MAILFALLBACK_DATABASE_URL")
if db_url:
    config.set_main_option("sqlalchemy.url", db_url)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
```

- [ ] **Step 4: Generate initial migration**

Run: `MAILFALLBACK_DATABASE_URL=sqlite:///tmp/mf_test.db alembic revision --autogenerate -m "initial schema"`

- [ ] **Step 5: Apply migration and verify**

Run: `MAILFALLBACK_DATABASE_URL=sqlite:///tmp/mf_test.db alembic upgrade head`
Expected: `INFO  [alembic.runtime.migration] Running upgrade  -> xxxx, initial schema`

- [ ] **Step 6: Commit**

```bash
git add alembic.ini alembic/
git commit -m "feat: Alembic migration setup with initial schema"
```

---

## Task 4: Security Utilities

**Files:**
- Create: `src/mailfallback/security.py`
- Create: `tests/test_security.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_security.py
from mailfallback.security import decrypt_credentials, encrypt_credentials, hash_password, verify_password


def test_password_hash_and_verify():
    hashed = hash_password("mysecretpass")
    assert hashed != "mysecretpass"
    assert verify_password("mysecretpass", hashed) is True
    assert verify_password("wrongpass", hashed) is False


def test_encrypt_decrypt_credentials():
    secret_key = "test-secret-key-for-encryption"
    plaintext = "oauth2-refresh-token-abc123"
    encrypted = encrypt_credentials(plaintext, secret_key)
    assert encrypted != plaintext
    decrypted = decrypt_credentials(encrypted, secret_key)
    assert decrypted == plaintext


def test_encrypt_decrypt_with_different_keys():
    encrypted = encrypt_credentials("data", "key1")
    try:
        decrypt_credentials(encrypted, "key2")
        assert False, "Should have raised an exception"
    except Exception:
        pass
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_security.py -v`
Expected: FAIL — `ImportError`

- [ ] **Step 3: Implement security.py**

```python
# src/mailfallback/security.py
import base64
import hashlib

import bcrypt
from cryptography.fernet import Fernet


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode(), hashed.encode())


def _derive_fernet_key(secret_key: str) -> bytes:
    digest = hashlib.sha256(secret_key.encode()).digest()
    return base64.urlsafe_b64encode(digest)


def encrypt_credentials(plaintext: str, secret_key: str) -> str:
    f = Fernet(_derive_fernet_key(secret_key))
    return f.encrypt(plaintext.encode()).decode()


def decrypt_credentials(encrypted: str, secret_key: str) -> str:
    f = Fernet(_derive_fernet_key(secret_key))
    return f.decrypt(encrypted.encode()).decode()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_security.py -v`
Expected: 3 PASSED

- [ ] **Step 5: Commit**

```bash
git add src/mailfallback/security.py tests/test_security.py
git commit -m "feat: password hashing and credential encryption utilities"
```

---

## Task 5: FastAPI Dependencies and Auth Router

**Files:**
- Create: `src/mailfallback/dependencies.py`
- Create: `src/mailfallback/services/user_service.py`
- Create: `src/mailfallback/services/__init__.py`
- Create: `src/mailfallback/routers/__init__.py`
- Create: `src/mailfallback/routers/auth.py`
- Create: `tests/test_auth.py`
- Modify: `src/mailfallback/app.py`
- Modify: `tests/conftest.py`

- [ ] **Step 1: Update conftest.py with in-memory DB fixtures**

```python
# tests/conftest.py
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from mailfallback.app import create_app
from mailfallback.db import Base
from mailfallback.dependencies import get_db


@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def app(db_session):
    application = create_app()
    application.dependency_overrides[get_db] = lambda: db_session
    return application


@pytest.fixture
def client(app):
    return TestClient(app)
```

- [ ] **Step 2: Create dependencies.py**

```python
# src/mailfallback/dependencies.py
from typing import Generator

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from mailfallback.db import SessionLocal
from mailfallback.models import User


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    user_id = request.session.get("user_id")
    if not user_id:
        raise HTTPException(status_code=401, detail="Not authenticated")
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    return user


def require_admin(user: User = Depends(get_current_user)) -> User:
    if user.role.value != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return user
```

- [ ] **Step 3: Create user_service.py**

```python
# src/mailfallback/services/__init__.py
```

```python
# src/mailfallback/services/user_service.py
from sqlalchemy.orm import Session

from mailfallback.models import User, UserRole
from mailfallback.security import hash_password, verify_password


def create_user(db: Session, username: str, password: str, role: UserRole = UserRole.user) -> User:
    user = User(username=username, password_hash=hash_password(password), role=role)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def authenticate_user(db: Session, username: str, password: str) -> User | None:
    user = db.query(User).filter(User.username == username).first()
    if not user or not user.password_hash:
        return None
    if not verify_password(password, user.password_hash):
        return None
    return user


def get_user_by_id(db: Session, user_id: str) -> User | None:
    return db.query(User).filter(User.id == user_id).first()


def list_users(db: Session) -> list[User]:
    return db.query(User).all()


def ensure_admin_exists(db: Session) -> None:
    admin = db.query(User).filter(User.role == UserRole.admin).first()
    if not admin:
        create_user(db, username="admin", password="changeme", role=UserRole.admin)
```

- [ ] **Step 4: Create auth router**

```python
# src/mailfallback/routers/__init__.py
```

```python
# src/mailfallback/routers/auth.py
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from mailfallback.dependencies import get_db
from mailfallback.services.user_service import authenticate_user

router = APIRouter(tags=["auth"])


class LoginRequest(BaseModel):
    username: str
    password: str


@router.post("/api/auth/login")
def login(body: LoginRequest, request: Request, db: Session = Depends(get_db)):
    user = authenticate_user(db, body.username, body.password)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid credentials")
    request.session["user_id"] = user.id
    return {"ok": True, "role": user.role.value}


@router.post("/api/auth/logout")
def logout(request: Request):
    request.session.clear()
    return {"ok": True}
```

- [ ] **Step 5: Update app.py to wire routers and session middleware**

```python
# src/mailfallback/app.py
from fastapi import FastAPI
from starlette.middleware.sessions import SessionMiddleware

from mailfallback.config import settings
from mailfallback.routers import auth


def create_app() -> FastAPI:
    app = FastAPI(title="Mailfallback", version="0.1.0")
    app.add_middleware(SessionMiddleware, secret_key=settings.session_secret)
    app.include_router(auth.router)

    @app.get("/healthz")
    async def healthz():
        return {"status": "ok"}

    return app


app = create_app()
```

- [ ] **Step 6: Write auth tests**

```python
# tests/test_auth.py
from mailfallback.models import UserRole
from mailfallback.services.user_service import create_user


def test_login_success(client, db_session):
    create_user(db_session, "admin", "secret123", UserRole.admin)
    resp = client.post("/api/auth/login", json={"username": "admin", "password": "secret123"})
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    assert resp.json()["role"] == "admin"


def test_login_wrong_password(client, db_session):
    create_user(db_session, "admin", "secret123", UserRole.admin)
    resp = client.post("/api/auth/login", json={"username": "admin", "password": "wrong"})
    assert resp.status_code == 401


def test_login_unknown_user(client):
    resp = client.post("/api/auth/login", json={"username": "nobody", "password": "x"})
    assert resp.status_code == 401


def test_logout(client, db_session):
    create_user(db_session, "user1", "pass", UserRole.user)
    client.post("/api/auth/login", json={"username": "user1", "password": "pass"})
    resp = client.post("/api/auth/logout")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
```

- [ ] **Step 7: Run tests**

Run: `pytest tests/test_auth.py -v`
Expected: 4 PASSED

- [ ] **Step 8: Commit**

```bash
git add src/mailfallback/dependencies.py src/mailfallback/services/ src/mailfallback/routers/ src/mailfallback/app.py tests/conftest.py tests/test_auth.py
git commit -m "feat: auth system with login/logout, user service, session middleware"
```

---

## Task 6: Account CRUD API

**Files:**
- Create: `src/mailfallback/services/account_service.py`
- Create: `src/mailfallback/routers/accounts.py`
- Create: `tests/test_accounts.py`
- Modify: `src/mailfallback/app.py`

- [ ] **Step 1: Create account_service.py**

```python
# src/mailfallback/services/account_service.py
from sqlalchemy.orm import Session

from mailfallback.config import settings
from mailfallback.models import Account, User, UserRole, account_owners
from mailfallback.security import decrypt_credentials, encrypt_credentials


def create_account(
    db: Session,
    name: str,
    imap_host: str,
    imap_port: int,
    auth_type: str,
    maildir_path: str,
    credentials: str | None = None,
    sync_schedule: str = "0 */6 * * *",
) -> Account:
    encrypted_creds = None
    if credentials:
        encrypted_creds = encrypt_credentials(credentials, settings.secret_key)
    account = Account(
        name=name,
        imap_host=imap_host,
        imap_port=imap_port,
        auth_type=auth_type,
        credentials=encrypted_creds,
        maildir_path=maildir_path,
        sync_schedule=sync_schedule,
    )
    db.add(account)
    db.commit()
    db.refresh(account)
    return account


def assign_owner(db: Session, account_id: str, user_id: str) -> None:
    account = db.query(Account).filter(Account.id == account_id).first()
    user = db.query(User).filter(User.id == user_id).first()
    if not account or not user:
        raise ValueError("Account or user not found")
    if user not in account.owners:
        account.owners.append(user)
        db.commit()


def remove_owner(db: Session, account_id: str, user_id: str) -> None:
    account = db.query(Account).filter(Account.id == account_id).first()
    user = db.query(User).filter(User.id == user_id).first()
    if account and user and user in account.owners:
        account.owners.remove(user)
        db.commit()


def get_accounts_for_user(db: Session, user: User) -> list[Account]:
    if user.role == UserRole.admin:
        return db.query(Account).all()
    return user.accounts


def get_account(db: Session, account_id: str, user: User) -> Account | None:
    account = db.query(Account).filter(Account.id == account_id).first()
    if not account:
        return None
    if user.role != UserRole.admin and user not in account.owners:
        return None
    return account


def update_account(db: Session, account_id: str, user: User, **kwargs) -> Account | None:
    account = get_account(db, account_id, user)
    if not account:
        return None
    if "credentials" in kwargs and kwargs["credentials"] is not None:
        kwargs["credentials"] = encrypt_credentials(kwargs["credentials"], settings.secret_key)
    for key, value in kwargs.items():
        setattr(account, key, value)
    db.commit()
    db.refresh(account)
    return account


def delete_account(db: Session, account_id: str) -> bool:
    account = db.query(Account).filter(Account.id == account_id).first()
    if not account:
        return False
    db.delete(account)
    db.commit()
    return True


def get_account_credentials(db: Session, account_id: str) -> str | None:
    account = db.query(Account).filter(Account.id == account_id).first()
    if not account or not account.credentials:
        return None
    return decrypt_credentials(account.credentials, settings.secret_key)
```

- [ ] **Step 2: Create accounts router**

```python
# src/mailfallback/routers/accounts.py
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from mailfallback.dependencies import get_current_user, get_db, require_admin
from mailfallback.models import User
from mailfallback.services import account_service

router = APIRouter(prefix="/api/accounts", tags=["accounts"])


class AccountCreate(BaseModel):
    name: str
    imap_host: str
    imap_port: int = 993
    auth_type: str = "app_password"
    credentials: str | None = None
    maildir_path: str
    sync_schedule: str = "0 */6 * * *"


class AccountUpdate(BaseModel):
    name: str | None = None
    imap_host: str | None = None
    imap_port: int | None = None
    sync_schedule: str | None = None
    credentials: str | None = None
    enabled: bool | None = None


class OwnerAssign(BaseModel):
    user_id: str


@router.post("")
def create(body: AccountCreate, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    account = account_service.create_account(db, **body.model_dump())
    return {"id": account.id, "name": account.name}


@router.get("")
def list_accounts(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    accounts = account_service.get_accounts_for_user(db, user)
    return [
        {
            "id": a.id,
            "name": a.name,
            "imap_host": a.imap_host,
            "sync_state": a.sync_state.value,
            "last_sync_at": a.last_sync_at.isoformat() if a.last_sync_at else None,
            "enabled": a.enabled,
        }
        for a in accounts
    ]


@router.get("/{account_id}")
def get(account_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    account = account_service.get_account(db, account_id, user)
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    return {
        "id": account.id,
        "name": account.name,
        "imap_host": account.imap_host,
        "imap_port": account.imap_port,
        "auth_type": account.auth_type.value,
        "maildir_path": account.maildir_path,
        "sync_schedule": account.sync_schedule,
        "sync_state": account.sync_state.value,
        "last_sync_at": account.last_sync_at.isoformat() if account.last_sync_at else None,
        "last_error": account.last_error,
        "enabled": account.enabled,
        "owners": [{"id": o.id, "username": o.username} for o in account.owners],
    }


@router.patch("/{account_id}")
def update(
    account_id: str,
    body: AccountUpdate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    updates = body.model_dump(exclude_unset=True)
    account = account_service.update_account(db, account_id, user, **updates)
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    return {"id": account.id, "name": account.name}


@router.delete("/{account_id}")
def delete(account_id: str, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    if not account_service.delete_account(db, account_id):
        raise HTTPException(status_code=404, detail="Account not found")
    return {"ok": True}


@router.post("/{account_id}/owners")
def assign_owner(
    account_id: str,
    body: OwnerAssign,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    try:
        account_service.assign_owner(db, account_id, body.user_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"ok": True}


@router.delete("/{account_id}/owners/{user_id}")
def remove_owner(
    account_id: str,
    user_id: str,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    account_service.remove_owner(db, account_id, user_id)
    return {"ok": True}
```

- [ ] **Step 3: Wire accounts router in app.py**

Add to `create_app()` in `src/mailfallback/app.py`:

```python
from mailfallback.routers import auth, accounts

# inside create_app():
app.include_router(accounts.router)
```

- [ ] **Step 4: Write tests**

```python
# tests/test_accounts.py
from mailfallback.models import UserRole
from mailfallback.services.user_service import create_user


def _login(client, username, password):
    client.post("/api/auth/login", json={"username": username, "password": password})


def test_admin_creates_account(client, db_session):
    admin = create_user(db_session, "admin", "pass", UserRole.admin)
    _login(client, "admin", "pass")
    resp = client.post("/api/accounts", json={
        "name": "Gmail",
        "imap_host": "imap.gmail.com",
        "imap_port": 993,
        "maildir_path": "/data/mailboxes/gmail",
    })
    assert resp.status_code == 200
    assert resp.json()["name"] == "Gmail"


def test_user_cannot_create_account(client, db_session):
    create_user(db_session, "user1", "pass", UserRole.user)
    _login(client, "user1", "pass")
    resp = client.post("/api/accounts", json={
        "name": "Gmail",
        "imap_host": "imap.gmail.com",
        "maildir_path": "/data/mailboxes/gmail",
    })
    assert resp.status_code == 403


def test_user_sees_only_own_accounts(client, db_session):
    from mailfallback.services.account_service import assign_owner, create_account
    admin = create_user(db_session, "admin", "pass", UserRole.admin)
    user = create_user(db_session, "user1", "pass", UserRole.user)
    a1 = create_account(db_session, "Gmail", "imap.gmail.com", 993, "app_password", "/data/gmail")
    a2 = create_account(db_session, "Work", "imap.work.com", 993, "app_password", "/data/work")
    assign_owner(db_session, a1.id, user.id)

    _login(client, "user1", "pass")
    resp = client.get("/api/accounts")
    assert resp.status_code == 200
    accounts = resp.json()
    assert len(accounts) == 1
    assert accounts[0]["name"] == "Gmail"


def test_admin_sees_all_accounts(client, db_session):
    from mailfallback.services.account_service import create_account
    create_user(db_session, "admin", "pass", UserRole.admin)
    create_account(db_session, "Gmail", "imap.gmail.com", 993, "app_password", "/data/gmail")
    create_account(db_session, "Work", "imap.work.com", 993, "app_password", "/data/work")

    _login(client, "admin", "pass")
    resp = client.get("/api/accounts")
    assert len(resp.json()) == 2


def test_assign_and_remove_owner(client, db_session):
    from mailfallback.services.account_service import create_account
    admin = create_user(db_session, "admin", "pass", UserRole.admin)
    user = create_user(db_session, "user1", "pass", UserRole.user)
    account = create_account(db_session, "Gmail", "imap.gmail.com", 993, "app_password", "/data/gmail")

    _login(client, "admin", "pass")
    resp = client.post(f"/api/accounts/{account.id}/owners", json={"user_id": user.id})
    assert resp.status_code == 200

    resp = client.get(f"/api/accounts/{account.id}")
    assert len(resp.json()["owners"]) == 1

    resp = client.delete(f"/api/accounts/{account.id}/owners/{user.id}")
    assert resp.status_code == 200


def test_user_updates_own_account(client, db_session):
    from mailfallback.services.account_service import assign_owner, create_account
    user = create_user(db_session, "user1", "pass", UserRole.user)
    account = create_account(db_session, "Gmail", "imap.gmail.com", 993, "app_password", "/data/gmail")
    assign_owner(db_session, account.id, user.id)

    _login(client, "user1", "pass")
    resp = client.patch(f"/api/accounts/{account.id}", json={"sync_schedule": "*/15 * * * *"})
    assert resp.status_code == 200
```

- [ ] **Step 5: Run tests**

Run: `pytest tests/test_accounts.py -v`
Expected: 6 PASSED

- [ ] **Step 6: Commit**

```bash
git add src/mailfallback/services/account_service.py src/mailfallback/routers/accounts.py src/mailfallback/app.py tests/test_accounts.py
git commit -m "feat: account CRUD API with ownership and role-based access"
```

---

## Task 7: mbsync Config Generation

**Files:**
- Create: `src/mailfallback/services/mbsync_config.py`
- Create: `tests/test_mbsync_config.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_mbsync_config.py
from mailfallback.services.mbsync_config import generate_mbsyncrc


def test_generate_app_password_config():
    config = generate_mbsyncrc(
        account_name="gmail",
        imap_host="imap.gmail.com",
        imap_port=993,
        username="user@gmail.com",
        auth_type="app_password",
        password="myapppassword",
        maildir_path="/data/mailboxes/gmail",
    )
    assert "IMAPStore gmail-remote" in config
    assert "Host imap.gmail.com" in config
    assert "Port 993" in config
    assert "User user@gmail.com" in config
    assert 'Pass "myapppassword"' in config
    assert "MaildirStore gmail-local" in config
    assert "Path /data/mailboxes/gmail/" in config
    assert "Channel gmail" in config
    assert "Create Near" in config
    assert "SyncState *" in config


def test_generate_oauth2_config():
    config = generate_mbsyncrc(
        account_name="gmail",
        imap_host="imap.gmail.com",
        imap_port=993,
        username="user@gmail.com",
        auth_type="oauth2",
        token_command="/usr/local/bin/token_helper gmail",
        maildir_path="/data/mailboxes/gmail",
    )
    assert "AuthMechs XOAUTH2" in config
    assert 'PassCmd "/usr/local/bin/token_helper gmail"' in config
    assert "Pass " not in config


def test_maildir_path_gets_trailing_slash():
    config = generate_mbsyncrc(
        account_name="test",
        imap_host="imap.test.com",
        imap_port=993,
        username="u@test.com",
        auth_type="app_password",
        password="p",
        maildir_path="/data/mailboxes/test",
    )
    assert "Path /data/mailboxes/test/" in config


def test_maildir_path_no_double_slash():
    config = generate_mbsyncrc(
        account_name="test",
        imap_host="imap.test.com",
        imap_port=993,
        username="u@test.com",
        auth_type="app_password",
        password="p",
        maildir_path="/data/mailboxes/test/",
    )
    assert "Path /data/mailboxes/test/" in config
    assert "/test//" not in config
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_mbsync_config.py -v`
Expected: FAIL — `ImportError`

- [ ] **Step 3: Implement mbsync_config.py**

```python
# src/mailfallback/services/mbsync_config.py


def generate_mbsyncrc(
    account_name: str,
    imap_host: str,
    imap_port: int,
    username: str,
    auth_type: str,
    maildir_path: str,
    password: str | None = None,
    token_command: str | None = None,
) -> str:
    maildir = maildir_path.rstrip("/") + "/"
    lines = []

    lines.append(f"IMAPStore {account_name}-remote")
    lines.append(f"Host {imap_host}")
    lines.append(f"Port {imap_port}")
    lines.append(f"User {username}")
    lines.append("SSLType IMAPS")
    lines.append("CertificateFile /etc/ssl/certs/ca-certificates.crt")

    if auth_type == "oauth2":
        lines.append("AuthMechs XOAUTH2")
        lines.append(f'PassCmd "{token_command}"')
    else:
        lines.append(f'Pass "{password}"')

    lines.append("")
    lines.append(f"MaildirStore {account_name}-local")
    lines.append(f"Path {maildir}")
    lines.append(f"Inbox {maildir}INBOX")
    lines.append("SubFolders Verbatim")

    lines.append("")
    lines.append(f"Channel {account_name}")
    lines.append(f"Far :{account_name}-remote:")
    lines.append(f"Near :{account_name}-local:")
    lines.append("Patterns *")
    lines.append("Create Near")
    lines.append("Expunge None")
    lines.append("SyncState *")

    return "\n".join(lines) + "\n"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_mbsync_config.py -v`
Expected: 4 PASSED

- [ ] **Step 5: Commit**

```bash
git add src/mailfallback/services/mbsync_config.py tests/test_mbsync_config.py
git commit -m "feat: mbsync config file generation"
```

---

## Task 8: Sync Service (Job Queue)

**Files:**
- Create: `src/mailfallback/services/sync_service.py`
- Create: `tests/test_sync_service.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_sync_service.py
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from mailfallback.db import Base
from mailfallback.models import Account, JobStatus, SyncJob
from mailfallback.services.sync_service import create_sync_job, get_job, list_jobs_for_account


def make_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _make_account(session):
    account = Account(
        name="Test",
        imap_host="imap.test.com",
        maildir_path="/data/test",
    )
    session.add(account)
    session.commit()
    return account


def test_create_job():
    session = make_session()
    account = _make_account(session)
    job = create_sync_job(session, account.id, source="api")
    assert job.status == JobStatus.pending
    assert job.source == "api"
    assert job.account_id == account.id


def test_dedup_pending_job():
    session = make_session()
    account = _make_account(session)
    job1 = create_sync_job(session, account.id, source="api")
    job2 = create_sync_job(session, account.id, source="scheduler")
    assert job2 is None


def test_dedup_running_job():
    session = make_session()
    account = _make_account(session)
    job1 = create_sync_job(session, account.id, source="api")
    job1.status = JobStatus.running
    session.commit()
    job2 = create_sync_job(session, account.id, source="api")
    assert job2 is None


def test_allows_job_after_completed():
    session = make_session()
    account = _make_account(session)
    job1 = create_sync_job(session, account.id, source="api")
    job1.status = JobStatus.completed
    session.commit()
    job2 = create_sync_job(session, account.id, source="api")
    assert job2 is not None
    assert job2.id != job1.id


def test_get_job():
    session = make_session()
    account = _make_account(session)
    job = create_sync_job(session, account.id, source="api")
    fetched = get_job(session, job.id)
    assert fetched is not None
    assert fetched.id == job.id


def test_list_jobs_for_account():
    session = make_session()
    account = _make_account(session)
    create_sync_job(session, account.id, source="api")
    jobs = list_jobs_for_account(session, account.id)
    assert len(jobs) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_sync_service.py -v`
Expected: FAIL — `ImportError`

- [ ] **Step 3: Implement sync_service.py**

```python
# src/mailfallback/services/sync_service.py
from sqlalchemy.orm import Session

from mailfallback.models import JobStatus, SyncJob


def create_sync_job(db: Session, account_id: str, source: str = "api") -> SyncJob | None:
    existing = (
        db.query(SyncJob)
        .filter(
            SyncJob.account_id == account_id,
            SyncJob.status.in_([JobStatus.pending, JobStatus.running]),
        )
        .first()
    )
    if existing:
        return None

    job = SyncJob(account_id=account_id, source=source)
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def get_job(db: Session, job_id: str) -> SyncJob | None:
    return db.query(SyncJob).filter(SyncJob.id == job_id).first()


def list_jobs_for_account(
    db: Session, account_id: str, limit: int = 50
) -> list[SyncJob]:
    return (
        db.query(SyncJob)
        .filter(SyncJob.account_id == account_id)
        .order_by(SyncJob.requested_at.desc())
        .limit(limit)
        .all()
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_sync_service.py -v`
Expected: 6 PASSED

- [ ] **Step 5: Commit**

```bash
git add src/mailfallback/services/sync_service.py tests/test_sync_service.py
git commit -m "feat: sync job queue with deduplication"
```

---

## Task 9: Sync Worker (mbsync Subprocess Execution)

**Files:**
- Create: `src/mailfallback/services/sync_worker.py`
- Create: `tests/test_sync_worker.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_sync_worker.py
import os
import tempfile
from datetime import datetime, timezone
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from mailfallback.db import Base
from mailfallback.models import Account, JobStatus, SyncJob, SyncState
from mailfallback.services.sync_worker import execute_sync_job


def make_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _make_account_and_job(session):
    account = Account(
        name="Test",
        imap_host="imap.test.com",
        maildir_path="/tmp/test_maildir",
        credentials=None,
    )
    session.add(account)
    session.commit()
    job = SyncJob(account_id=account.id, source="test")
    session.add(job)
    session.commit()
    return account, job


def test_successful_sync():
    session = make_session()
    account, job = _make_account_and_job(session)

    mock_result = type("Result", (), {"returncode": 0, "stdout": "synced ok", "stderr": ""})()
    with patch("mailfallback.services.sync_worker.subprocess.run", return_value=mock_result):
        with patch("mailfallback.services.sync_worker.generate_mbsyncrc", return_value="config"):
            execute_sync_job(session, job.id)

    session.refresh(job)
    session.refresh(account)
    assert job.status == JobStatus.completed
    assert job.exit_code == 0
    assert job.completed_at is not None
    assert account.sync_state == SyncState.idle
    assert account.last_sync_at is not None


def test_failed_sync():
    session = make_session()
    account, job = _make_account_and_job(session)

    mock_result = type("Result", (), {"returncode": 1, "stdout": "", "stderr": "auth failed"})()
    with patch("mailfallback.services.sync_worker.subprocess.run", return_value=mock_result):
        with patch("mailfallback.services.sync_worker.generate_mbsyncrc", return_value="config"):
            execute_sync_job(session, job.id)

    session.refresh(job)
    session.refresh(account)
    assert job.status == JobStatus.failed
    assert job.exit_code == 1
    assert "auth failed" in job.log
    assert account.sync_state == SyncState.error
    assert account.last_error is not None


def test_sync_sets_running_state():
    session = make_session()
    account, job = _make_account_and_job(session)

    captured_state = {}

    original_run = None

    def capture_state(*args, **kwargs):
        session.refresh(account)
        session.refresh(job)
        captured_state["account_state"] = account.sync_state
        captured_state["job_status"] = job.status
        return type("Result", (), {"returncode": 0, "stdout": "ok", "stderr": ""})()

    with patch("mailfallback.services.sync_worker.subprocess.run", side_effect=capture_state):
        with patch("mailfallback.services.sync_worker.generate_mbsyncrc", return_value="config"):
            execute_sync_job(session, job.id)

    assert captured_state["account_state"] == SyncState.syncing
    assert captured_state["job_status"] == JobStatus.running
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_sync_worker.py -v`
Expected: FAIL — `ImportError`

- [ ] **Step 3: Implement sync_worker.py**

```python
# src/mailfallback/services/sync_worker.py
import os
import subprocess
import tempfile
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from mailfallback.config import settings
from mailfallback.models import Account, JobStatus, SyncJob, SyncState
from mailfallback.security import decrypt_credentials
from mailfallback.services.mbsync_config import generate_mbsyncrc


def execute_sync_job(db: Session, job_id: str) -> None:
    job = db.query(SyncJob).filter(SyncJob.id == job_id).first()
    if not job:
        return

    account = db.query(Account).filter(Account.id == job.account_id).first()
    if not account:
        job.status = JobStatus.failed
        job.log = "Account not found"
        job.completed_at = datetime.now(timezone.utc)
        db.commit()
        return

    job.status = JobStatus.running
    job.started_at = datetime.now(timezone.utc)
    account.sync_state = SyncState.syncing
    db.commit()

    password = None
    token_command = None
    if account.credentials:
        creds = decrypt_credentials(account.credentials, settings.secret_key)
        if account.auth_type.value == "oauth2":
            token_command = creds
        else:
            password = creds

    config_content = generate_mbsyncrc(
        account_name=account.name.lower().replace(" ", "_"),
        imap_host=account.imap_host,
        imap_port=account.imap_port,
        username=account.name,
        auth_type=account.auth_type.value,
        maildir_path=account.maildir_path,
        password=password,
        token_command=token_command,
    )

    try:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".mbsyncrc", delete=False) as f:
            f.write(config_content)
            config_path = f.name

        os.makedirs(account.maildir_path, exist_ok=True)

        result = subprocess.run(
            [settings.mbsync_binary, "-c", config_path, "-a"],
            capture_output=True,
            text=True,
            timeout=3600,
        )

        job.exit_code = result.returncode
        job.log = (result.stdout + "\n" + result.stderr).strip()
        job.completed_at = datetime.now(timezone.utc)

        if result.returncode == 0:
            job.status = JobStatus.completed
            account.sync_state = SyncState.idle
            account.last_sync_at = datetime.now(timezone.utc)
            account.last_error = None
        else:
            job.status = JobStatus.failed
            account.sync_state = SyncState.error
            account.last_error = job.log

    except subprocess.TimeoutExpired:
        job.status = JobStatus.failed
        job.log = "Sync timed out after 3600 seconds"
        job.completed_at = datetime.now(timezone.utc)
        account.sync_state = SyncState.error
        account.last_error = job.log

    except Exception as e:
        job.status = JobStatus.failed
        job.log = str(e)
        job.completed_at = datetime.now(timezone.utc)
        account.sync_state = SyncState.error
        account.last_error = str(e)

    finally:
        if "config_path" in locals():
            os.unlink(config_path)
        db.commit()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_sync_worker.py -v`
Expected: 3 PASSED

- [ ] **Step 5: Commit**

```bash
git add src/mailfallback/services/sync_worker.py tests/test_sync_worker.py
git commit -m "feat: sync worker — executes mbsync via subprocess"
```

---

## Task 10: Sync API Router

**Files:**
- Create: `src/mailfallback/routers/sync.py`
- Modify: `src/mailfallback/app.py`

- [ ] **Step 1: Create sync router**

```python
# src/mailfallback/routers/sync.py
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from mailfallback.dependencies import get_current_user, get_db
from mailfallback.models import User
from mailfallback.services import account_service, sync_service

router = APIRouter(prefix="/api/sync", tags=["sync"])


@router.post("/{account_id}")
def trigger_sync(
    account_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    account = account_service.get_account(db, account_id, user)
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    job = sync_service.create_sync_job(db, account_id, source="api")
    if not job:
        raise HTTPException(status_code=409, detail="Sync already pending or running")
    return {"job_id": job.id, "status": job.status.value}


@router.get("/jobs/{job_id}")
def get_job(
    job_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    job = sync_service.get_job(db, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    account = account_service.get_account(db, job.account_id, user)
    if not account:
        raise HTTPException(status_code=404, detail="Job not found")
    return {
        "id": job.id,
        "account_id": job.account_id,
        "status": job.status.value,
        "source": job.source,
        "requested_at": job.requested_at.isoformat() if job.requested_at else None,
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "completed_at": job.completed_at.isoformat() if job.completed_at else None,
        "exit_code": job.exit_code,
        "log": job.log,
    }


@router.get("/jobs")
def list_jobs(
    account_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    account = account_service.get_account(db, account_id, user)
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    jobs = sync_service.list_jobs_for_account(db, account_id)
    return [
        {
            "id": j.id,
            "status": j.status.value,
            "source": j.source,
            "requested_at": j.requested_at.isoformat() if j.requested_at else None,
            "completed_at": j.completed_at.isoformat() if j.completed_at else None,
            "exit_code": j.exit_code,
        }
        for j in jobs
    ]
```

- [ ] **Step 2: Wire in app.py**

Add to `create_app()`:

```python
from mailfallback.routers import auth, accounts, sync

app.include_router(sync.router)
```

- [ ] **Step 3: Write tests**

Add to `tests/test_sync_service.py` or create a new file — since these are API-level tests, add to the bottom of `tests/test_sync_service.py`:

```python
# Add to tests/test_sync_service.py — API-level tests

def test_trigger_sync_api(client, db_session):
    from mailfallback.models import UserRole
    from mailfallback.services.account_service import assign_owner, create_account
    from mailfallback.services.user_service import create_user

    user = create_user(db_session, "user1", "pass", UserRole.user)
    account = create_account(db_session, "Gmail", "imap.gmail.com", 993, "app_password", "/data/g")
    assign_owner(db_session, account.id, user.id)

    client.post("/api/auth/login", json={"username": "user1", "password": "pass"})
    resp = client.post(f"/api/sync/{account.id}")
    assert resp.status_code == 200
    assert resp.json()["status"] == "pending"

    resp2 = client.post(f"/api/sync/{account.id}")
    assert resp2.status_code == 409


def test_get_job_api(client, db_session):
    from mailfallback.models import UserRole
    from mailfallback.services.account_service import assign_owner, create_account
    from mailfallback.services.user_service import create_user

    user = create_user(db_session, "user1", "pass", UserRole.user)
    account = create_account(db_session, "Gmail", "imap.gmail.com", 993, "app_password", "/data/g")
    assign_owner(db_session, account.id, user.id)

    client.post("/api/auth/login", json={"username": "user1", "password": "pass"})
    resp = client.post(f"/api/sync/{account.id}")
    job_id = resp.json()["job_id"]

    resp = client.get(f"/api/sync/jobs/{job_id}")
    assert resp.status_code == 200
    assert resp.json()["status"] == "pending"
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_sync_service.py -v`
Expected: 8 PASSED

- [ ] **Step 5: Commit**

```bash
git add src/mailfallback/routers/sync.py src/mailfallback/app.py tests/test_sync_service.py
git commit -m "feat: sync API — trigger, status, history endpoints"
```

---

## Task 11: Scheduler (APScheduler)

**Files:**
- Create: `src/mailfallback/services/scheduler.py`
- Modify: `src/mailfallback/app.py`

- [ ] **Step 1: Create scheduler.py**

```python
# src/mailfallback/services/scheduler.py
import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy.orm import Session

from mailfallback.db import SessionLocal
from mailfallback.models import Account
from mailfallback.services.sync_service import create_sync_job
from mailfallback.services.sync_worker import execute_sync_job

logger = logging.getLogger(__name__)

scheduler = BackgroundScheduler()


def _run_scheduled_sync(account_id: str) -> None:
    db = SessionLocal()
    try:
        job = create_sync_job(db, account_id, source="scheduler")
        if job:
            execute_sync_job(db, job.id)
    finally:
        db.close()


def sync_scheduler_jobs(db: Session) -> None:
    existing_job_ids = {j.id for j in scheduler.get_jobs()}

    accounts = db.query(Account).filter(Account.enabled == True).all()
    active_job_ids = set()

    for account in accounts:
        job_id = f"sync_{account.id}"
        active_job_ids.add(job_id)

        if not account.sync_schedule:
            continue

        parts = account.sync_schedule.split()
        if len(parts) != 5:
            logger.warning("Invalid cron for account %s: %s", account.name, account.sync_schedule)
            continue

        trigger = CronTrigger(
            minute=parts[0],
            hour=parts[1],
            day=parts[2],
            month=parts[3],
            day_of_week=parts[4],
        )

        if job_id in existing_job_ids:
            scheduler.reschedule_job(job_id, trigger=trigger)
        else:
            scheduler.add_job(
                _run_scheduled_sync,
                trigger=trigger,
                id=job_id,
                args=[account.id],
                replace_existing=True,
            )

    for job_id in existing_job_ids - active_job_ids:
        if job_id.startswith("sync_"):
            scheduler.remove_job(job_id)


def start_scheduler(db: Session) -> None:
    sync_scheduler_jobs(db)
    if not scheduler.running:
        scheduler.start()


def stop_scheduler() -> None:
    if scheduler.running:
        scheduler.shutdown(wait=False)
```

- [ ] **Step 2: Wire scheduler lifecycle in app.py**

```python
# src/mailfallback/app.py
from contextlib import asynccontextmanager

from fastapi import FastAPI
from starlette.middleware.sessions import SessionMiddleware

from mailfallback.config import settings
from mailfallback.db import SessionLocal
from mailfallback.routers import accounts, auth, sync
from mailfallback.services.scheduler import start_scheduler, stop_scheduler
from mailfallback.services.user_service import ensure_admin_exists


@asynccontextmanager
async def lifespan(app: FastAPI):
    db = SessionLocal()
    try:
        ensure_admin_exists(db)
        start_scheduler(db)
    finally:
        db.close()
    yield
    stop_scheduler()


def create_app() -> FastAPI:
    app = FastAPI(title="Mailfallback", version="0.1.0", lifespan=lifespan)
    app.add_middleware(SessionMiddleware, secret_key=settings.session_secret)
    app.include_router(auth.router)
    app.include_router(accounts.router)
    app.include_router(sync.router)

    @app.get("/healthz")
    async def healthz():
        return {"status": "ok"}

    return app


app = create_app()
```

- [ ] **Step 3: Run all tests**

Run: `pytest tests/ -v`
Expected: all PASSED (the lifespan won't run during tests because TestClient manages its own lifecycle; the test conftest creates the app with dependency overrides)

- [ ] **Step 4: Commit**

```bash
git add src/mailfallback/services/scheduler.py src/mailfallback/app.py
git commit -m "feat: APScheduler integration for periodic sync"
```

---

## Task 12: Health and Metrics Endpoints

**Files:**
- Create: `src/mailfallback/routers/health.py`
- Create: `tests/test_health.py`
- Modify: `src/mailfallback/app.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_health.py
def test_healthz(client):
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_readyz(client, db_session):
    resp = client.get("/readyz")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert "db" in data["checks"]


def test_metrics_endpoint(client, db_session):
    resp = client.get("/metrics")
    assert resp.status_code == 200
    assert "mailfallback_accounts_total" in resp.text
    assert "mailfallback_jobs_pending" in resp.text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_health.py -v`
Expected: test_readyz and test_metrics FAIL

- [ ] **Step 3: Implement health.py**

```python
# src/mailfallback/routers/health.py
from fastapi import APIRouter, Depends
from fastapi.responses import PlainTextResponse
from prometheus_client import CollectorRegistry, Counter, Gauge, generate_latest
from sqlalchemy import text
from sqlalchemy.orm import Session

from mailfallback.dependencies import get_db
from mailfallback.models import Account, JobStatus, SyncJob

router = APIRouter(tags=["health"])

registry = CollectorRegistry()
SYNC_TOTAL = Counter(
    "mailfallback_sync_total",
    "Total syncs by account and status",
    ["account", "status"],
    registry=registry,
)
SYNC_DURATION = Gauge(
    "mailfallback_sync_duration_seconds",
    "Duration of last sync",
    ["account"],
    registry=registry,
)
SYNC_LAST_SUCCESS = Gauge(
    "mailfallback_sync_last_success_timestamp",
    "Timestamp of last successful sync",
    ["account"],
    registry=registry,
)
MAILDIR_SIZE = Gauge(
    "mailfallback_maildir_size_bytes",
    "Maildir size per account",
    ["account"],
    registry=registry,
)
ACCOUNTS_TOTAL = Gauge(
    "mailfallback_accounts_total",
    "Total configured accounts",
    registry=registry,
)
JOBS_PENDING = Gauge(
    "mailfallback_jobs_pending",
    "Pending jobs in queue",
    registry=registry,
)


@router.get("/readyz")
def readyz(db: Session = Depends(get_db)):
    checks = {}
    try:
        db.execute(text("SELECT 1"))
        checks["db"] = "ok"
    except Exception as e:
        checks["db"] = str(e)
        return {"status": "error", "checks": checks}

    return {"status": "ok", "checks": checks}


@router.get("/metrics", response_class=PlainTextResponse)
def metrics(db: Session = Depends(get_db)):
    accounts = db.query(Account).all()
    ACCOUNTS_TOTAL.set(len(accounts))

    pending = (
        db.query(SyncJob).filter(SyncJob.status == JobStatus.pending).count()
    )
    JOBS_PENDING.set(pending)

    for account in accounts:
        if account.last_sync_at:
            SYNC_LAST_SUCCESS.labels(account=account.name).set(
                account.last_sync_at.timestamp()
            )

    return generate_latest(registry).decode()
```

- [ ] **Step 4: Wire in app.py**

Add to `create_app()`:

```python
from mailfallback.routers import accounts, auth, health, sync

app.include_router(health.router)
```

- [ ] **Step 5: Run tests**

Run: `pytest tests/test_health.py -v`
Expected: 3 PASSED

- [ ] **Step 6: Commit**

```bash
git add src/mailfallback/routers/health.py tests/test_health.py src/mailfallback/app.py
git commit -m "feat: health probes and Prometheus metrics endpoint"
```

---

## Task 13: Config Export/Import API

**Files:**
- Create: `src/mailfallback/routers/config_io.py`
- Create: `tests/test_config_io.py`
- Modify: `src/mailfallback/app.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_config_io.py
from mailfallback.models import UserRole
from mailfallback.services.account_service import create_account
from mailfallback.services.user_service import create_user


def _login_admin(client, db_session):
    create_user(db_session, "admin", "pass", UserRole.admin)
    client.post("/api/auth/login", json={"username": "admin", "password": "pass"})


def test_export_config(client, db_session):
    _login_admin(client, db_session)
    create_account(db_session, "Gmail", "imap.gmail.com", 993, "app_password", "/data/gmail")

    resp = client.get("/api/config/export")
    assert resp.status_code == 200
    data = resp.json()
    assert "accounts" in data
    assert len(data["accounts"]) == 1
    assert data["accounts"][0]["name"] == "Gmail"
    assert "credentials" not in data["accounts"][0]


def test_import_config(client, db_session):
    _login_admin(client, db_session)
    payload = {
        "accounts": [
            {
                "name": "Imported",
                "imap_host": "imap.imported.com",
                "imap_port": 993,
                "auth_type": "app_password",
                "maildir_path": "/data/imported",
                "sync_schedule": "0 */6 * * *",
            }
        ]
    }
    resp = client.post("/api/config/import", json=payload)
    assert resp.status_code == 200
    assert resp.json()["imported"] == 1

    resp = client.get("/api/config/export")
    assert len(resp.json()["accounts"]) == 1


def test_export_requires_admin(client, db_session):
    create_user(db_session, "user1", "pass", UserRole.user)
    client.post("/api/auth/login", json={"username": "user1", "password": "pass"})
    resp = client.get("/api/config/export")
    assert resp.status_code == 403
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_config_io.py -v`
Expected: FAIL

- [ ] **Step 3: Implement config_io.py**

```python
# src/mailfallback/routers/config_io.py
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from mailfallback.dependencies import get_db, require_admin
from mailfallback.models import Account, User

router = APIRouter(prefix="/api/config", tags=["config"])


class AccountExport(BaseModel):
    name: str
    imap_host: str
    imap_port: int
    auth_type: str
    maildir_path: str
    sync_schedule: str | None


class ConfigExport(BaseModel):
    accounts: list[AccountExport]


class ConfigImport(BaseModel):
    accounts: list[AccountExport]


@router.get("/export")
def export_config(admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    accounts = db.query(Account).all()
    return {
        "accounts": [
            {
                "name": a.name,
                "imap_host": a.imap_host,
                "imap_port": a.imap_port,
                "auth_type": a.auth_type.value,
                "maildir_path": a.maildir_path,
                "sync_schedule": a.sync_schedule,
            }
            for a in accounts
        ]
    }


@router.post("/import")
def import_config(
    body: ConfigImport,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    count = 0
    for acc_data in body.accounts:
        account = Account(
            name=acc_data.name,
            imap_host=acc_data.imap_host,
            imap_port=acc_data.imap_port,
            auth_type=acc_data.auth_type,
            maildir_path=acc_data.maildir_path,
            sync_schedule=acc_data.sync_schedule,
        )
        db.add(account)
        count += 1
    db.commit()
    return {"imported": count}
```

- [ ] **Step 4: Wire in app.py**

Add to `create_app()`:

```python
from mailfallback.routers import accounts, auth, config_io, health, sync

app.include_router(config_io.router)
```

- [ ] **Step 5: Run tests**

Run: `pytest tests/test_config_io.py -v`
Expected: 3 PASSED

- [ ] **Step 6: Commit**

```bash
git add src/mailfallback/routers/config_io.py tests/test_config_io.py src/mailfallback/app.py
git commit -m "feat: config export/import API"
```

---

## Task 14: Dovecot Config Generation

**Files:**
- Create: `src/mailfallback/services/dovecot_config.py`
- Create: `tests/test_dovecot_config.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_dovecot_config.py
from mailfallback.services.dovecot_config import generate_dovecot_userdb, generate_dovecot_passdb


def test_generate_userdb_single_account():
    accounts_by_user = {
        "user1": [
            {"name": "Gmail", "maildir_path": "/data/mailboxes/gmail"},
        ]
    }
    userdb = generate_dovecot_userdb(accounts_by_user)
    assert "user1:" in userdb
    assert "/data/mailboxes/gmail" in userdb


def test_generate_userdb_multiple_accounts():
    accounts_by_user = {
        "user1": [
            {"name": "Gmail", "maildir_path": "/data/mailboxes/gmail"},
            {"name": "Work", "maildir_path": "/data/mailboxes/work"},
        ]
    }
    userdb = generate_dovecot_userdb(accounts_by_user)
    assert "user1:" in userdb
    assert "Gmail" in userdb
    assert "Work" in userdb


def test_generate_passdb():
    users = [
        {"username": "user1", "password_hash": "{BLF-CRYPT}$2b$12$abc"},
        {"username": "user2", "password_hash": "{BLF-CRYPT}$2b$12$def"},
    ]
    passdb = generate_dovecot_passdb(users)
    assert "user1:{BLF-CRYPT}$2b$12$abc" in passdb
    assert "user2:{BLF-CRYPT}$2b$12$def" in passdb


def test_generate_userdb_empty():
    userdb = generate_dovecot_userdb({})
    assert userdb == ""
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_dovecot_config.py -v`
Expected: FAIL — `ImportError`

- [ ] **Step 3: Implement dovecot_config.py**

```python
# src/mailfallback/services/dovecot_config.py


def generate_dovecot_userdb(accounts_by_user: dict[str, list[dict]]) -> str:
    lines = []
    for username, accounts in accounts_by_user.items():
        if not accounts:
            continue

        if len(accounts) == 1:
            maildir = accounts[0]["maildir_path"].rstrip("/")
            lines.append(f"user1={username}::::::userdb_mail=maildir:{maildir}")
        else:
            namespace_parts = []
            for acc in accounts:
                maildir = acc["maildir_path"].rstrip("/")
                namespace_parts.append(f"{acc['name']}={maildir}")

            first_maildir = accounts[0]["maildir_path"].rstrip("/")
            namespaces = " ".join(
                f"namespace/{acc['name']}/prefix={acc['name']}/:namespace/{acc['name']}/location=maildir:{acc['maildir_path'].rstrip('/')}"
                for acc in accounts
            )
            lines.append(
                f"{username}::::::userdb_mail=maildir:{first_maildir} {namespaces}"
            )

    return "\n".join(lines)


def generate_dovecot_passdb(users: list[dict[str, str]]) -> str:
    lines = []
    for user in users:
        lines.append(f"{user['username']}:{user['password_hash']}")
    return "\n".join(lines)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_dovecot_config.py -v`
Expected: 4 PASSED

- [ ] **Step 5: Commit**

```bash
git add src/mailfallback/services/dovecot_config.py tests/test_dovecot_config.py
git commit -m "feat: Dovecot userdb/passdb config generation with namespaces"
```

---

## Task 15: UI — Base Template and Dashboard

**Files:**
- Create: `src/mailfallback/templates/base.html`
- Create: `src/mailfallback/templates/login.html`
- Create: `src/mailfallback/templates/dashboard.html`
- Create: `src/mailfallback/routers/ui.py`
- Modify: `src/mailfallback/app.py`
- Create: `tests/test_ui.py`

- [ ] **Step 1: Create base.html**

```html
<!-- src/mailfallback/templates/base.html -->
<!DOCTYPE html>
<html lang="en" data-theme="light">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{% block title %}Mailfallback{% endblock %}</title>
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/@picocss/pico@2/css/pico.min.css">
    <script src="https://unpkg.com/htmx.org@2.0.4"></script>
    <style>
        .sync-idle { color: var(--pico-color-green-500); }
        .sync-syncing { color: var(--pico-color-amber-500); }
        .sync-error { color: var(--pico-color-red-500); }
        nav { margin-bottom: 2rem; }
    </style>
</head>
<body>
    <nav class="container">
        <ul>
            <li><strong>Mailfallback</strong></li>
        </ul>
        <ul>
            {% if user %}
            <li><a href="/">Dashboard</a></li>
            <li><a href="/accounts/new">Add Account</a></li>
            {% if user.role.value == "admin" %}
            <li><a href="/admin/users">Users</a></li>
            <li><a href="/settings">Settings</a></li>
            {% endif %}
            <li><a href="#" hx-post="/api/auth/logout" hx-target="body">Logout ({{ user.username }})</a></li>
            {% endif %}
        </ul>
    </nav>
    <main class="container">
        {% block content %}{% endblock %}
    </main>
</body>
</html>
```

- [ ] **Step 2: Create login.html**

```html
<!-- src/mailfallback/templates/login.html -->
{% extends "base.html" %}
{% block title %}Login — Mailfallback{% endblock %}
{% block content %}
<article style="max-width: 400px; margin: 0 auto;">
    <header>
        <h2>Login</h2>
    </header>
    {% if error %}
    <p style="color: var(--pico-color-red-500);">{{ error }}</p>
    {% endif %}
    <form method="post" action="/login">
        <label for="username">Username</label>
        <input type="text" id="username" name="username" required>
        <label for="password">Password</label>
        <input type="password" id="password" name="password" required>
        <button type="submit">Login</button>
    </form>
    {% if oidc_enabled %}
    <hr>
    <a href="/auth/oidc/login" role="button" class="secondary outline" style="width:100%; text-align:center;">Login with SSO</a>
    {% endif %}
</article>
{% endblock %}
```

- [ ] **Step 3: Create dashboard.html**

```html
<!-- src/mailfallback/templates/dashboard.html -->
{% extends "base.html" %}
{% block title %}Dashboard — Mailfallback{% endblock %}
{% block content %}
<h2>Email Accounts</h2>

{% if not accounts %}
<p>No accounts configured yet. <a href="/accounts/new">Add one</a>.</p>
{% else %}
<div hx-get="/partials/accounts-table" hx-trigger="every 5s" hx-swap="innerHTML">
    {% include "partials/accounts_table.html" %}
</div>
{% endif %}
{% endblock %}
```

- [ ] **Step 4: Create accounts table partial**

```html
<!-- src/mailfallback/templates/partials/accounts_table.html -->
<table>
    <thead>
        <tr>
            <th>Name</th>
            <th>Host</th>
            <th>Status</th>
            <th>Last Sync</th>
            <th>Actions</th>
        </tr>
    </thead>
    <tbody>
        {% for account in accounts %}
        <tr>
            <td><a href="/accounts/{{ account.id }}">{{ account.name }}</a></td>
            <td>{{ account.imap_host }}</td>
            <td><span class="sync-{{ account.sync_state.value }}">{{ account.sync_state.value }}</span></td>
            <td>{{ account.last_sync_at.strftime('%Y-%m-%d %H:%M') if account.last_sync_at else 'Never' }}</td>
            <td>
                <button
                    hx-post="/api/sync/{{ account.id }}"
                    hx-swap="none"
                    {% if account.sync_state.value == 'syncing' %}disabled{% endif %}
                >Sync Now</button>
            </td>
        </tr>
        {% endfor %}
    </tbody>
</table>
```

- [ ] **Step 5: Create UI router**

```python
# src/mailfallback/routers/ui.py
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from mailfallback.config import settings
from mailfallback.dependencies import get_current_user, get_db
from mailfallback.models import User
from mailfallback.services.account_service import get_accounts_for_user

router = APIRouter(tags=["ui"])

templates = Jinja2Templates(directory="src/mailfallback/templates")


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    return templates.TemplateResponse(
        "login.html",
        {"request": request, "oidc_enabled": settings.oidc_enabled, "error": None},
    )


@router.post("/login", response_class=HTMLResponse)
def login_submit(request: Request, db: Session = Depends(get_db)):
    from mailfallback.services.user_service import authenticate_user

    form = None

    async def _get_form():
        nonlocal form
        form = await request.form()

    import asyncio
    asyncio.get_event_loop().run_until_complete(_get_form())

    user = authenticate_user(db, form["username"], form["password"])
    if not user:
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "oidc_enabled": settings.oidc_enabled, "error": "Invalid credentials"},
        )
    request.session["user_id"] = user.id
    return RedirectResponse("/", status_code=303)


@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request, db: Session = Depends(get_db)):
    user_id = request.session.get("user_id")
    if not user_id:
        return RedirectResponse("/login")
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        return RedirectResponse("/login")
    accounts = get_accounts_for_user(db, user)
    return templates.TemplateResponse(
        "dashboard.html",
        {"request": request, "user": user, "accounts": accounts},
    )


@router.get("/partials/accounts-table", response_class=HTMLResponse)
def accounts_table_partial(request: Request, db: Session = Depends(get_db)):
    user_id = request.session.get("user_id")
    if not user_id:
        return HTMLResponse("")
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        return HTMLResponse("")
    accounts = get_accounts_for_user(db, user)
    return templates.TemplateResponse(
        "partials/accounts_table.html",
        {"request": request, "accounts": accounts},
    )
```

- [ ] **Step 6: Wire in app.py and create templates directory structure**

Add to `create_app()`:

```python
from mailfallback.routers import accounts, auth, config_io, health, sync, ui

app.include_router(ui.router)
```

Also create the partials directory:

```bash
mkdir -p src/mailfallback/templates/partials
```

- [ ] **Step 7: Write tests**

```python
# tests/test_ui.py
from mailfallback.models import UserRole
from mailfallback.services.user_service import create_user


def test_login_page(client):
    resp = client.get("/login")
    assert resp.status_code == 200
    assert "Login" in resp.text


def test_dashboard_redirects_when_not_logged_in(client):
    resp = client.get("/", follow_redirects=False)
    assert resp.status_code == 307 or resp.status_code == 302


def test_dashboard_shows_when_logged_in(client, db_session):
    create_user(db_session, "admin", "pass", UserRole.admin)
    client.post("/api/auth/login", json={"username": "admin", "password": "pass"})
    resp = client.get("/")
    assert resp.status_code == 200
    assert "Email Accounts" in resp.text
```

- [ ] **Step 8: Run tests**

Run: `pytest tests/test_ui.py -v`
Expected: 3 PASSED

- [ ] **Step 9: Commit**

```bash
git add src/mailfallback/templates/ src/mailfallback/routers/ui.py src/mailfallback/app.py tests/test_ui.py
git commit -m "feat: UI — login page, dashboard with HTMX live sync status"
```

---

## Task 16: UI — Account Detail and Form Pages

**Files:**
- Create: `src/mailfallback/templates/account_form.html`
- Create: `src/mailfallback/templates/account_detail.html`
- Modify: `src/mailfallback/routers/ui.py`

- [ ] **Step 1: Create account_form.html**

```html
<!-- src/mailfallback/templates/account_form.html -->
{% extends "base.html" %}
{% block title %}Add Account — Mailfallback{% endblock %}
{% block content %}
<h2>Add Email Account</h2>
<form method="post" action="/accounts/new">
    <label for="name">Account Name</label>
    <input type="text" id="name" name="name" placeholder="Gmail Personal" required>

    <label for="imap_host">IMAP Host</label>
    <input type="text" id="imap_host" name="imap_host" placeholder="imap.gmail.com" required>

    <label for="imap_port">IMAP Port</label>
    <input type="number" id="imap_port" name="imap_port" value="993">

    <label for="auth_type">Authentication</label>
    <select id="auth_type" name="auth_type">
        <option value="app_password">App Password</option>
        <option value="oauth2">OAuth2 (Gmail)</option>
    </select>

    <label for="credentials">Password / App Password</label>
    <input type="password" id="credentials" name="credentials">

    <label for="maildir_path">Maildir Path</label>
    <input type="text" id="maildir_path" name="maildir_path" placeholder="/data/mailboxes/gmail" required>

    <label for="sync_schedule">Sync Schedule (cron)</label>
    <input type="text" id="sync_schedule" name="sync_schedule" value="0 */6 * * *">

    <button type="submit">Create Account</button>
</form>
{% endblock %}
```

- [ ] **Step 2: Create account_detail.html**

```html
<!-- src/mailfallback/templates/account_detail.html -->
{% extends "base.html" %}
{% block title %}{{ account.name }} — Mailfallback{% endblock %}
{% block content %}
<h2>{{ account.name }}</h2>

<div hx-get="/partials/account-status/{{ account.id }}" hx-trigger="every 5s" hx-swap="innerHTML">
    <table>
        <tr><td>Host</td><td>{{ account.imap_host }}:{{ account.imap_port }}</td></tr>
        <tr><td>Status</td><td><span class="sync-{{ account.sync_state.value }}">{{ account.sync_state.value }}</span></td></tr>
        <tr><td>Last Sync</td><td>{{ account.last_sync_at.strftime('%Y-%m-%d %H:%M:%S') if account.last_sync_at else 'Never' }}</td></tr>
        <tr><td>Schedule</td><td>{{ account.sync_schedule }}</td></tr>
        <tr><td>Maildir</td><td>{{ account.maildir_path }}</td></tr>
        <tr><td>Enabled</td><td>{{ 'Yes' if account.enabled else 'No' }}</td></tr>
    </table>
</div>

{% if account.last_error %}
<details>
    <summary>Last Error</summary>
    <pre>{{ account.last_error }}</pre>
</details>
{% endif %}

<div style="display: flex; gap: 1rem; margin: 1rem 0;">
    <button hx-post="/api/sync/{{ account.id }}" hx-swap="none"
        {% if account.sync_state.value == 'syncing' %}disabled{% endif %}>
        Sync Now
    </button>
</div>

<h3>Sync History</h3>
<table>
    <thead>
        <tr><th>Time</th><th>Source</th><th>Status</th><th>Exit Code</th><th>Log</th></tr>
    </thead>
    <tbody>
        {% for job in jobs %}
        <tr>
            <td>{{ job.requested_at.strftime('%Y-%m-%d %H:%M') if job.requested_at else '' }}</td>
            <td>{{ job.source }}</td>
            <td>{{ job.status.value }}</td>
            <td>{{ job.exit_code if job.exit_code is not none else '-' }}</td>
            <td>{% if job.log %}<details><summary>View</summary><pre>{{ job.log }}</pre></details>{% endif %}</td>
        </tr>
        {% endfor %}
    </tbody>
</table>
{% endblock %}
```

- [ ] **Step 3: Add routes to ui.py**

Add to `src/mailfallback/routers/ui.py`:

```python
@router.get("/accounts/new", response_class=HTMLResponse)
def account_form(request: Request, db: Session = Depends(get_db)):
    user_id = request.session.get("user_id")
    if not user_id:
        return RedirectResponse("/login")
    user = db.query(User).filter(User.id == user_id).first()
    if not user or user.role.value != "admin":
        return RedirectResponse("/")
    return templates.TemplateResponse(
        "account_form.html", {"request": request, "user": user}
    )


@router.post("/accounts/new")
async def account_form_submit(request: Request, db: Session = Depends(get_db)):
    user_id = request.session.get("user_id")
    if not user_id:
        return RedirectResponse("/login", status_code=303)
    user = db.query(User).filter(User.id == user_id).first()
    if not user or user.role.value != "admin":
        return RedirectResponse("/", status_code=303)

    form = await request.form()
    from mailfallback.services.account_service import create_account

    create_account(
        db,
        name=form["name"],
        imap_host=form["imap_host"],
        imap_port=int(form["imap_port"]),
        auth_type=form["auth_type"],
        maildir_path=form["maildir_path"],
        credentials=form.get("credentials") or None,
        sync_schedule=form.get("sync_schedule", "0 */6 * * *"),
    )
    return RedirectResponse("/", status_code=303)


@router.get("/accounts/{account_id}", response_class=HTMLResponse)
def account_detail(account_id: str, request: Request, db: Session = Depends(get_db)):
    user_id = request.session.get("user_id")
    if not user_id:
        return RedirectResponse("/login")
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        return RedirectResponse("/login")

    from mailfallback.services.account_service import get_account
    from mailfallback.services.sync_service import list_jobs_for_account

    account = get_account(db, account_id, user)
    if not account:
        return RedirectResponse("/")
    jobs = list_jobs_for_account(db, account_id, limit=20)
    return templates.TemplateResponse(
        "account_detail.html",
        {"request": request, "user": user, "account": account, "jobs": jobs},
    )
```

- [ ] **Step 4: Run all tests**

Run: `pytest tests/ -v`
Expected: all PASSED

- [ ] **Step 5: Commit**

```bash
git add src/mailfallback/templates/account_form.html src/mailfallback/templates/account_detail.html src/mailfallback/routers/ui.py
git commit -m "feat: UI — account creation form and detail page with sync history"
```

---

## Task 17: UI — Admin Users and Settings Pages

**Files:**
- Create: `src/mailfallback/templates/admin_users.html`
- Create: `src/mailfallback/templates/settings.html`
- Modify: `src/mailfallback/routers/ui.py`

- [ ] **Step 1: Create admin_users.html**

```html
<!-- src/mailfallback/templates/admin_users.html -->
{% extends "base.html" %}
{% block title %}Users — Mailfallback{% endblock %}
{% block content %}
<h2>User Management</h2>

<table>
    <thead>
        <tr><th>Username</th><th>Role</th><th>Accounts</th><th>Actions</th></tr>
    </thead>
    <tbody>
        {% for u in users %}
        <tr>
            <td>{{ u.username }}</td>
            <td>{{ u.role.value }}</td>
            <td>{{ u.accounts | length }}</td>
            <td>
                {% if u.username != 'admin' %}
                <button hx-delete="/api/admin/users/{{ u.id }}" hx-confirm="Delete user {{ u.username }}?" hx-swap="none" hx-on::after-request="location.reload()">Delete</button>
                {% endif %}
            </td>
        </tr>
        {% endfor %}
    </tbody>
</table>

<h3>Add User</h3>
<form method="post" action="/admin/users/new">
    <div style="display: grid; grid-template-columns: 1fr 1fr 1fr auto; gap: 1rem; align-items: end;">
        <div>
            <label for="username">Username</label>
            <input type="text" id="username" name="username" required>
        </div>
        <div>
            <label for="password">Password</label>
            <input type="password" id="password" name="password" required>
        </div>
        <div>
            <label for="role">Role</label>
            <select id="role" name="role">
                <option value="user">User</option>
                <option value="admin">Admin</option>
            </select>
        </div>
        <button type="submit">Add</button>
    </div>
</form>
{% endblock %}
```

- [ ] **Step 2: Create settings.html**

```html
<!-- src/mailfallback/templates/settings.html -->
{% extends "base.html" %}
{% block title %}Settings — Mailfallback{% endblock %}
{% block content %}
<h2>Settings</h2>

<h3>Config Export/Import</h3>
<div style="display: flex; gap: 1rem;">
    <a href="/api/config/export" role="button" class="outline">Export Config (JSON)</a>
    <form method="post" action="/settings/import" enctype="multipart/form-data" style="display: inline;">
        <input type="file" name="config_file" accept=".json" required>
        <button type="submit" class="secondary">Import Config</button>
    </form>
</div>

<h3>System Info</h3>
<table>
    <tr><td>Total Accounts</td><td>{{ total_accounts }}</td></tr>
    <tr><td>Total Users</td><td>{{ total_users }}</td></tr>
    <tr><td>OIDC Enabled</td><td>{{ 'Yes' if oidc_enabled else 'No' }}</td></tr>
</table>
{% endblock %}
```

- [ ] **Step 3: Add admin routes to ui.py**

Add to `src/mailfallback/routers/ui.py`:

```python
from mailfallback.services.user_service import create_user, list_users


@router.get("/admin/users", response_class=HTMLResponse)
def admin_users_page(request: Request, db: Session = Depends(get_db)):
    user_id = request.session.get("user_id")
    if not user_id:
        return RedirectResponse("/login")
    user = db.query(User).filter(User.id == user_id).first()
    if not user or user.role.value != "admin":
        return RedirectResponse("/")
    users = list_users(db)
    return templates.TemplateResponse(
        "admin_users.html",
        {"request": request, "user": user, "users": users},
    )


@router.post("/admin/users/new")
async def admin_create_user(request: Request, db: Session = Depends(get_db)):
    user_id = request.session.get("user_id")
    if not user_id:
        return RedirectResponse("/login", status_code=303)
    user = db.query(User).filter(User.id == user_id).first()
    if not user or user.role.value != "admin":
        return RedirectResponse("/", status_code=303)

    form = await request.form()
    create_user(db, form["username"], form["password"], form["role"])
    return RedirectResponse("/admin/users", status_code=303)


@router.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request, db: Session = Depends(get_db)):
    user_id = request.session.get("user_id")
    if not user_id:
        return RedirectResponse("/login")
    user = db.query(User).filter(User.id == user_id).first()
    if not user or user.role.value != "admin":
        return RedirectResponse("/")
    from mailfallback.models import Account
    return templates.TemplateResponse(
        "settings.html",
        {
            "request": request,
            "user": user,
            "total_accounts": db.query(Account).count(),
            "total_users": db.query(User).count(),
            "oidc_enabled": settings.oidc_enabled,
        },
    )
```

Also add missing import at the top of `ui.py`:

```python
from mailfallback.config import settings as app_settings
```

(Rename to `app_settings` to avoid shadowing.)

- [ ] **Step 4: Run all tests**

Run: `pytest tests/ -v`
Expected: all PASSED

- [ ] **Step 5: Commit**

```bash
git add src/mailfallback/templates/admin_users.html src/mailfallback/templates/settings.html src/mailfallback/routers/ui.py
git commit -m "feat: UI — admin user management and settings pages"
```

---

## Task 18: OAuth2 Flow for Gmail

**Files:**
- Create: `src/mailfallback/services/oauth2.py`
- Modify: `src/mailfallback/routers/auth.py`
- Create: `docker/token_helper.sh`

- [ ] **Step 1: Implement oauth2 service**

```python
# src/mailfallback/services/oauth2.py
from authlib.integrations.httpx_client import AsyncOAuth2Client

from mailfallback.config import settings

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_SCOPES = ["https://mail.google.com/"]


def get_google_oauth_client(redirect_uri: str) -> AsyncOAuth2Client:
    return AsyncOAuth2Client(
        client_id=settings.google_client_id,
        client_secret=settings.google_client_secret,
        redirect_uri=redirect_uri,
        scope=" ".join(GOOGLE_SCOPES),
    )


def build_google_auth_url(redirect_uri: str, state: str) -> str:
    client = get_google_oauth_client(redirect_uri)
    url, _ = client.create_authorization_url(
        GOOGLE_AUTH_URL,
        state=state,
        access_type="offline",
        prompt="consent",
    )
    return url


async def exchange_google_code(code: str, redirect_uri: str) -> dict:
    client = get_google_oauth_client(redirect_uri)
    token = await client.fetch_token(
        GOOGLE_TOKEN_URL,
        code=code,
    )
    await client.aclose()
    return token


async def refresh_google_token(refresh_token: str) -> str:
    client = get_google_oauth_client("")
    token = await client.fetch_token(
        GOOGLE_TOKEN_URL,
        grant_type="refresh_token",
        refresh_token=refresh_token,
    )
    await client.aclose()
    return token["access_token"]
```

- [ ] **Step 2: Add OAuth2 callback route to auth.py**

Add to `src/mailfallback/routers/auth.py`:

```python
import json

from mailfallback.config import settings
from mailfallback.security import encrypt_credentials
from mailfallback.services.oauth2 import build_google_auth_url, exchange_google_code


@router.get("/auth/google/start")
def google_oauth_start(request: Request, account_id: str):
    redirect_uri = str(request.url_for("google_oauth_callback"))
    request.session["oauth_account_id"] = account_id
    url = build_google_auth_url(redirect_uri, state=account_id)
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url)


@router.get("/auth/google/callback")
async def google_oauth_callback(
    request: Request,
    code: str,
    db: Session = Depends(get_db),
):
    redirect_uri = str(request.url_for("google_oauth_callback"))
    token = await exchange_google_code(code, redirect_uri)
    account_id = request.session.pop("oauth_account_id", None)
    if not account_id:
        raise HTTPException(status_code=400, detail="No account in session")

    from mailfallback.models import Account, AuthType
    account = db.query(Account).filter(Account.id == account_id).first()
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")

    token_data = json.dumps({
        "access_token": token["access_token"],
        "refresh_token": token.get("refresh_token", ""),
        "token_type": token.get("token_type", "Bearer"),
    })
    account.credentials = encrypt_credentials(token_data, settings.secret_key)
    account.auth_type = AuthType.oauth2
    db.commit()

    from fastapi.responses import RedirectResponse
    return RedirectResponse(f"/accounts/{account_id}")
```

- [ ] **Step 3: Create token_helper.sh**

```bash
#!/bin/sh
# docker/token_helper.sh
# Called by mbsync PassCmd to get a fresh OAuth2 access token.
# Usage: token_helper.sh <account_id>
# Calls the mailfallback API to refresh and return the access token.
ACCOUNT_ID="$1"
curl -sf "http://localhost:8000/api/internal/token/${ACCOUNT_ID}"
```

- [ ] **Step 4: Commit**

```bash
chmod +x docker/token_helper.sh
git add src/mailfallback/services/oauth2.py src/mailfallback/routers/auth.py docker/token_helper.sh
git commit -m "feat: Google OAuth2 flow for Gmail IMAP access"
```

---

## Task 19: OIDC/SSO Integration

**Files:**
- Modify: `src/mailfallback/routers/auth.py`

- [ ] **Step 1: Add OIDC routes to auth.py**

Add to `src/mailfallback/routers/auth.py`:

```python
from authlib.integrations.starlette_client import OAuth

from mailfallback.config import settings
from mailfallback.models import User, UserRole

oauth = OAuth()

if settings.oidc_enabled:
    oauth.register(
        name="oidc",
        client_id=settings.oidc_client_id,
        client_secret=settings.oidc_client_secret,
        server_metadata_url=settings.oidc_discovery_url,
        client_kwargs={"scope": "openid email profile groups"},
    )


@router.get("/auth/oidc/login")
async def oidc_login(request: Request):
    if not settings.oidc_enabled:
        raise HTTPException(status_code=404, detail="OIDC not enabled")
    redirect_uri = str(request.url_for("oidc_callback"))
    return await oauth.oidc.authorize_redirect(request, redirect_uri)


@router.get("/auth/oidc/callback")
async def oidc_callback(request: Request, db: Session = Depends(get_db)):
    if not settings.oidc_enabled:
        raise HTTPException(status_code=404, detail="OIDC not enabled")

    token = await oauth.oidc.authorize_access_token(request)
    userinfo = token.get("userinfo", {})

    sub = userinfo.get("sub")
    username = userinfo.get("preferred_username") or userinfo.get("email", sub)
    groups = userinfo.get("groups", [])

    if settings.oidc_admin_group in groups:
        role = UserRole.admin
    else:
        role = UserRole.user

    user = db.query(User).filter(User.oidc_subject == sub).first()
    if not user:
        user = User(username=username, oidc_subject=sub, role=role)
        db.add(user)
        db.commit()
        db.refresh(user)
    else:
        user.role = role
        db.commit()

    request.session["user_id"] = user.id
    from fastapi.responses import RedirectResponse
    return RedirectResponse("/")
```

- [ ] **Step 2: Run all tests**

Run: `pytest tests/ -v`
Expected: all PASSED (OIDC routes won't fire because `oidc_enabled` defaults to `False`)

- [ ] **Step 3: Commit**

```bash
git add src/mailfallback/routers/auth.py
git commit -m "feat: OIDC/SSO login with Authentik-compatible group role mapping"
```

---

## Task 20: Docker Setup

**Files:**
- Create: `docker/Dockerfile`
- Create: `docker/Dockerfile.mbsync`
- Create: `docker/entrypoint.sh`
- Create: `docker-compose.yml`

- [ ] **Step 1: Create Dockerfile for the core app**

```dockerfile
# docker/Dockerfile
FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml .
RUN pip install --no-cache-dir ".[postgres]"

COPY src/ src/
COPY alembic/ alembic/
COPY alembic.ini .
COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

RUN pip install --no-cache-dir -e .

EXPOSE 8000

ENTRYPOINT ["/entrypoint.sh"]
```

- [ ] **Step 2: Create entrypoint.sh**

```bash
#!/bin/sh
# docker/entrypoint.sh
set -e

echo "Running database migrations..."
alembic upgrade head

echo "Starting mailfallback..."
exec uvicorn mailfallback.app:app --host 0.0.0.0 --port 8000
```

- [ ] **Step 3: Create Dockerfile.mbsync**

```dockerfile
# docker/Dockerfile.mbsync
FROM alpine:3.20

RUN apk add --no-cache isync ca-certificates curl

COPY docker/token_helper.sh /usr/local/bin/token_helper
RUN chmod +x /usr/local/bin/token_helper

RUN adduser -D mbsync
USER mbsync

ENTRYPOINT ["sleep", "infinity"]
```

- [ ] **Step 4: Create docker-compose.yml**

```yaml
# docker-compose.yml
services:
  mailfallback:
    build:
      context: .
      dockerfile: docker/Dockerfile
    ports:
      - "8000:8000"
    volumes:
      - config:/data/config
      # Map mailbox volumes per account:
      # - /mnt/nas1/gmail:/data/mailboxes/gmail
      # - /mnt/nas2/work:/data/mailboxes/work
    environment:
      - MAILFALLBACK_SECRET_KEY=change-me-in-production
      - MAILFALLBACK_SESSION_SECRET=change-me-session-secret
      # For PostgreSQL:
      # - MAILFALLBACK_DATABASE_URL=postgresql://user:pass@db:5432/mailfallback
    depends_on:
      - mbsync

  mbsync:
    build:
      context: .
      dockerfile: docker/Dockerfile.mbsync
    volumes:
      - config:/data/config:ro
      # Same mailbox volumes as mailfallback:
      # - /mnt/nas1/gmail:/data/mailboxes/gmail
      # - /mnt/nas2/work:/data/mailboxes/work

  # Optional: Dovecot for read-only IMAP access
  # dovecot:
  #   image: dovecot/dovecot:2.3
  #   volumes:
  #     - config:/data/config:ro
  #     # Same mailbox volumes:
  #     # - /mnt/nas1/gmail:/data/mailboxes/gmail
  #   ports:
  #     - "143:143"

  # Optional: Webmail
  # webmail:
  #   image: djmaze/snappymail:latest
  #   ports:
  #     - "8888:8888"
  #   depends_on:
  #     - dovecot

volumes:
  config:
```

- [ ] **Step 5: Commit**

```bash
git add docker/ docker-compose.yml
git commit -m "feat: Docker setup — Dockerfile, mbsync container, docker-compose"
```

---

## Task 21: Integration Test — Full Sync Flow

**Files:**
- Create: `tests/test_integration.py`

- [ ] **Step 1: Write integration test**

```python
# tests/test_integration.py
from unittest.mock import patch

from mailfallback.models import JobStatus, SyncState, UserRole
from mailfallback.services.account_service import assign_owner, create_account
from mailfallback.services.user_service import create_user


def test_full_sync_flow(client, db_session):
    admin = create_user(db_session, "admin", "pass", UserRole.admin)
    account = create_account(
        db_session,
        name="Test Gmail",
        imap_host="imap.gmail.com",
        imap_port=993,
        auth_type="app_password",
        maildir_path="/tmp/test_maildir",
        credentials="test-app-password",
    )
    assign_owner(db_session, account.id, admin.id)

    client.post("/api/auth/login", json={"username": "admin", "password": "pass"})

    resp = client.get("/api/accounts")
    assert resp.status_code == 200
    assert len(resp.json()) == 1

    resp = client.post(f"/api/sync/{account.id}")
    assert resp.status_code == 200
    job_id = resp.json()["job_id"]

    resp = client.get(f"/api/sync/jobs/{job_id}")
    assert resp.status_code == 200
    assert resp.json()["status"] == "pending"

    mock_result = type("Result", (), {"returncode": 0, "stdout": "synced", "stderr": ""})()
    with patch("mailfallback.services.sync_worker.subprocess.run", return_value=mock_result):
        from mailfallback.services.sync_worker import execute_sync_job
        execute_sync_job(db_session, job_id)

    resp = client.get(f"/api/sync/jobs/{job_id}")
    assert resp.json()["status"] == "completed"
    assert resp.json()["exit_code"] == 0

    resp = client.get(f"/api/accounts/{account.id}")
    assert resp.json()["sync_state"] == "idle"
    assert resp.json()["last_sync_at"] is not None


def test_full_sync_flow_failure(client, db_session):
    admin = create_user(db_session, "admin", "pass", UserRole.admin)
    account = create_account(
        db_session,
        name="Failing",
        imap_host="imap.fail.com",
        imap_port=993,
        auth_type="app_password",
        maildir_path="/tmp/test_fail",
    )
    assign_owner(db_session, account.id, admin.id)

    client.post("/api/auth/login", json={"username": "admin", "password": "pass"})
    resp = client.post(f"/api/sync/{account.id}")
    job_id = resp.json()["job_id"]

    mock_result = type("Result", (), {"returncode": 1, "stdout": "", "stderr": "auth error"})()
    with patch("mailfallback.services.sync_worker.subprocess.run", return_value=mock_result):
        from mailfallback.services.sync_worker import execute_sync_job
        execute_sync_job(db_session, job_id)

    resp = client.get(f"/api/sync/jobs/{job_id}")
    assert resp.json()["status"] == "failed"

    resp = client.get(f"/api/accounts/{account.id}")
    assert resp.json()["sync_state"] == "error"
    assert "auth error" in resp.json()["last_error"]
```

- [ ] **Step 2: Run integration test**

Run: `pytest tests/test_integration.py -v`
Expected: 2 PASSED

- [ ] **Step 3: Run full test suite**

Run: `pytest tests/ -v`
Expected: all PASSED

- [ ] **Step 4: Commit**

```bash
git add tests/test_integration.py
git commit -m "test: integration tests for full sync flow (success + failure)"
```

---

## Task 22: Final Verification

- [ ] **Step 1: Run full test suite with coverage**

Run: `pip install pytest-cov && pytest tests/ -v --cov=mailfallback --cov-report=term-missing`
Expected: all tests PASS, coverage report shows key modules covered

- [ ] **Step 2: Run ruff lint**

Run: `ruff check src/ tests/`
Expected: no errors (or fix any that appear)

- [ ] **Step 3: Verify Docker build**

Run: `docker compose build mailfallback`
Expected: builds successfully

- [ ] **Step 4: Commit any lint fixes**

```bash
git add -A
git commit -m "chore: lint fixes and final cleanup"
```
