# src/mailfallback/models.py
import enum
import uuid
from datetime import UTC, datetime

from sqlalchemy import Boolean, Column, DateTime, Enum, ForeignKey, Integer, String, Table, Text
from sqlalchemy.orm import relationship

from mailfallback.db import Base


def _utcnow():
    return datetime.now(UTC)


def _new_uuid():
    return str(uuid.uuid4())


class UserRole(enum.StrEnum):
    admin = "admin"
    user = "user"


class AuthType(enum.StrEnum):
    oauth2 = "oauth2"
    app_password = "app_password"


class SyncState(enum.StrEnum):
    idle = "idle"
    syncing = "syncing"
    error = "error"


class JobStatus(enum.StrEnum):
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
    email_address = Column(String, nullable=False, default="")
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
