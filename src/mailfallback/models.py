# src/mailfallback/models.py
import enum
import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Table,
    Text,
)
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
    cancelled = "cancelled"


class MigrationStatus(enum.StrEnum):
    pending = "pending"
    copying = "copying"
    verifying = "verifying"
    cleaning = "cleaning"
    completed = "completed"
    failed = "failed"


class RestoreMode(enum.StrEnum):
    full = "full"
    folder = "folder"
    selection = "selection"


account_owners = Table(
    "account_owners",
    Base.metadata,
    Column("account_id", String, ForeignKey("accounts.id"), primary_key=True),
    Column("user_id", String, ForeignKey("users.id"), primary_key=True),
)

user_allowed_stores = Table(
    "user_allowed_stores",
    Base.metadata,
    Column("user_id", String, ForeignKey("users.id"), primary_key=True),
    Column("store_id", String, ForeignKey("mail_stores.id"), primary_key=True),
)

group_members = Table(
    "group_members",
    Base.metadata,
    Column("group_id", String, ForeignKey("groups.id"), primary_key=True),
    Column("user_id", String, ForeignKey("users.id"), primary_key=True),
)

account_groups = Table(
    "account_groups",
    Base.metadata,
    Column("account_id", String, ForeignKey("accounts.id"), primary_key=True),
    Column("group_id", String, ForeignKey("groups.id"), primary_key=True),
)


class User(Base):
    __tablename__ = "users"

    id = Column(String, primary_key=True, default=_new_uuid)
    username = Column(String, unique=True, nullable=False, index=True)
    password_hash = Column(String, nullable=True)
    role = Column(Enum(UserRole), nullable=False, default=UserRole.user)
    oidc_subject = Column(String, nullable=True, unique=True)
    enabled = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), default=_utcnow)

    store_id = Column(String, ForeignKey("mail_stores.id"), nullable=False)
    migrating = Column(Boolean, nullable=False, default=False)
    preferences = Column(JSON, nullable=False, default=dict, server_default="{}")

    accounts = relationship("Account", secondary=account_owners, back_populates="owners")
    store = relationship("MailStore", back_populates="users")
    allowed_stores = relationship("MailStore", secondary=user_allowed_stores)


class MailStore(Base):
    __tablename__ = "mail_stores"

    id = Column(String, primary_key=True, default=_new_uuid)
    name = Column(String, nullable=False)
    path = Column(String, unique=True, nullable=False)
    enabled = Column(Boolean, nullable=False, default=True)
    is_default = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime(timezone=True), default=_utcnow)

    users = relationship("User", back_populates="store")
    accounts = relationship("Account", back_populates="store")


class Group(Base):
    __tablename__ = "groups"

    id = Column(String, primary_key=True, default=_new_uuid)
    name = Column(String, unique=True, nullable=False)
    owner_id = Column(String, ForeignKey("users.id"), nullable=False)
    sso_sync = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime(timezone=True), default=_utcnow)

    owner = relationship("User", foreign_keys=[owner_id])
    members = relationship("User", secondary=group_members, backref="groups")
    accounts = relationship("Account", secondary=account_groups, backref="visible_to_groups")


class Account(Base):
    __tablename__ = "accounts"

    id = Column(String, primary_key=True, default=_new_uuid)
    name = Column(String, nullable=False)
    email_address = Column(String, nullable=False, default="")
    provider = Column(String, nullable=False, default="other")
    imap_host = Column(String, nullable=False)
    imap_port = Column(Integer, nullable=False, default=993)
    auth_type = Column(Enum(AuthType), nullable=False, default=AuthType.app_password)
    credentials = Column(Text, nullable=True)
    imap_user = Column(String, nullable=True)
    tls_type = Column(String, nullable=False, default="IMAPS")
    maildir_path = Column(String, nullable=False, unique=True)
    sync_schedule = Column(String, nullable=True, default="0 * * * *")
    extra_config = Column(Text, nullable=True)
    sync_state = Column(Enum(SyncState), nullable=False, default=SyncState.idle)
    last_sync_at = Column(DateTime(timezone=True), nullable=True)
    last_error = Column(Text, nullable=True)
    total_messages = Column(Integer, nullable=False, default=0)
    unread_messages = Column(Integer, nullable=False, default=0)
    maildir_size_bytes = Column(Integer, nullable=False, default=0)
    folder_stats = Column(Text, nullable=True)
    store_id = Column(String, ForeignKey("mail_stores.id"), nullable=False)
    enabled = Column(Boolean, nullable=False, default=True)
    suspended = Column(Boolean, nullable=False, default=False)
    migrating = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime(timezone=True), default=_utcnow)

    owners = relationship("User", secondary=account_owners, back_populates="accounts")
    sync_jobs = relationship("SyncJob", back_populates="account", cascade="all, delete-orphan")
    store = relationship("MailStore", back_populates="accounts")

    @property
    def is_authenticated(self) -> bool:
        return not (self.auth_type == AuthType.oauth2 and not self.credentials)


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
    log_path = Column(String, nullable=True)
    parsed_summary = Column(Text, nullable=True)
    mbsync_version = Column(String, nullable=True)
    signal = Column(String, nullable=True)

    account = relationship("Account", back_populates="sync_jobs")


class StoreMigration(Base):
    __tablename__ = "store_migrations"

    id = Column(String, primary_key=True, default=_new_uuid)
    user_id = Column(String, ForeignKey("users.id"), nullable=True)
    account_id = Column(String, ForeignKey("accounts.id"), nullable=True)
    source_store_id = Column(String, ForeignKey("mail_stores.id"), nullable=False)
    target_store_id = Column(String, ForeignKey("mail_stores.id"), nullable=False)
    status = Column(Enum(MigrationStatus), nullable=False, default=MigrationStatus.pending)
    total_files = Column(Integer, nullable=False, default=0)
    copied_files = Column(Integer, nullable=False, default=0)
    total_bytes = Column(Integer, nullable=False, default=0)
    copied_bytes = Column(Integer, nullable=False, default=0)
    error = Column(Text, nullable=True)
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    last_resumed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=_utcnow)


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id = Column(String, primary_key=True, default=_new_uuid)
    timestamp = Column(DateTime(timezone=True), nullable=False, default=_utcnow, index=True)
    user_id = Column(String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    username = Column(String, nullable=False)
    action = Column(String, nullable=False, index=True)
    resource_type = Column(String, nullable=False)
    resource_id = Column(String, nullable=True)
    resource_name = Column(String, nullable=True)
    ip_address = Column(String, nullable=True)
    details = Column(JSON, nullable=True)


class RestoreJob(Base):
    __tablename__ = "restore_jobs"

    id = Column(String, primary_key=True, default=_new_uuid)
    source_account_id = Column(String, ForeignKey("accounts.id"), nullable=False)
    target_account_id = Column(String, ForeignKey("accounts.id"), nullable=False)
    status = Column(Enum(JobStatus), nullable=False, default=JobStatus.pending)
    restore_mode = Column(Enum(RestoreMode), nullable=False)
    folder_mapping = Column(String, nullable=False, default="original")
    skip_duplicates = Column(Boolean, nullable=False, default=True)
    selected_folders = Column(JSON, nullable=True)
    selected_uids = Column(JSON, nullable=True)
    total_messages = Column(Integer, nullable=False, default=0)
    restored_messages = Column(Integer, nullable=False, default=0)
    skipped_messages = Column(Integer, nullable=False, default=0)
    failed_messages = Column(Integer, nullable=False, default=0)
    error = Column(Text, nullable=True)
    requested_by = Column(String, ForeignKey("users.id"), nullable=False)
    requested_at = Column(DateTime(timezone=True), default=_utcnow)
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)

    source_account = relationship("Account", foreign_keys=[source_account_id])
    target_account = relationship("Account", foreign_keys=[target_account_id])
    requester = relationship("User", foreign_keys=[requested_by])
