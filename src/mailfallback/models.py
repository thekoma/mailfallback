# src/mailfallback/models.py
import enum
import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    ARRAY,
    JSON,
    BigInteger,
    Boolean,
    Column,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    LargeBinary,
    String,
    Table,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import TSVECTOR
from sqlalchemy.orm import backref, relationship

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
    needs_reauth = "needs_reauth"


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
    # Push from the requester's staging area to the target upstream;
    # selected_uids doubles as the {folder: [staged_filename]} manifest.
    staging_push = "staging_push"


class TaskStatus(enum.StrEnum):
    pending = "pending"
    running = "running"
    completed = "completed"
    failed = "failed"


class BackendType(enum.StrEnum):
    s3 = "s3"
    local = "local"


class RetentionPreset(enum.StrEnum):
    light = "light"
    standard = "standard"
    full = "full"
    custom = "custom"


class RecoveryStatus(enum.StrEnum):
    restoring = "restoring"
    ready = "ready"
    failed = "failed"
    deleting = "deleting"


class RecoveryKind(enum.StrEnum):
    persistent = "persistent"
    ephemeral = "ephemeral"


class BackupStatus(enum.StrEnum):
    idle = "idle"
    running = "running"
    completed = "completed"
    failed = "failed"


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

user_allowed_repositories = Table(
    "user_allowed_repositories",
    Base.metadata,
    Column(
        "user_id",
        String,
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "repository_id",
        String,
        ForeignKey("backup_destinations.id", ondelete="CASCADE"),
        primary_key=True,
    ),
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
    allowed_repositories = relationship(
        "Repository", secondary=user_allowed_repositories, passive_deletes=True
    )


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
    # BigInteger: byte totals exceed INTEGER (~2.1 GB) for large mailboxes
    # (a 15 GB Gmail account overflowed it, failing the stats UPDATE).
    maildir_size_bytes = Column(BigInteger, nullable=False, default=0)
    folder_stats = Column(Text, nullable=True)
    # Sync budget / initial-sync regime (migration 021). traffic_date +
    # bytes_synced_today form the per-account daily byte ledger (UTC day,
    # reset on rollover); daily_sync_budget_mb: NULL → provider default,
    # 0 → unlimited. sync_paused_until + pause_reason gate the scheduler
    # (self-recovering pauses — NOT errors). initial_sync_* mark the
    # first-full-sync regime and feed the progress %/ETA UI.
    traffic_date = Column(Date, nullable=True)
    bytes_synced_today = Column(BigInteger, nullable=False, default=0, server_default=text("0"))
    daily_sync_budget_mb = Column(Integer, nullable=True)
    sync_paused_until = Column(DateTime(timezone=True), nullable=True)
    pause_reason = Column(String, nullable=True)
    initial_sync_completed_at = Column(DateTime(timezone=True), nullable=True)
    initial_sync_total_messages = Column(Integer, nullable=True)
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
    # Failure classification: throttled | budget_paused | transient |
    # interrupted | error. A PLAIN string by design (spec) — new kinds must
    # not need a migration.
    failure_kind = Column(String, nullable=True)

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


class BackgroundTask(Base):
    __tablename__ = "background_tasks"

    id = Column(String, primary_key=True, default=_new_uuid)
    task_type = Column(String, nullable=False, index=True)
    status = Column(Enum(TaskStatus), nullable=False, default=TaskStatus.pending)
    progress_current = Column(Integer, nullable=False, default=0, server_default="0")
    progress_total = Column(Integer, nullable=False, default=0, server_default="0")
    details = Column(JSON, nullable=True)
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    requested_by = Column(String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime(timezone=True), default=_utcnow)


class Repository(Base):
    """Off-site repository (restic) where mailbox snapshots are stored.

    DB table name kept as 'backup_destinations' for backward compatibility
    with existing data and migrations 010/011. Audit log action strings
    (`backup_destination.*`) likewise preserved; user-facing display labels
    are mapped via services.audit_service.ACTION_LABELS.
    """

    __tablename__ = "backup_destinations"

    id = Column(String, primary_key=True, default=_new_uuid)
    name = Column(String, nullable=False)
    backend_type = Column(Enum(BackendType), nullable=False)
    s3_endpoint = Column(String, nullable=True)
    s3_bucket = Column(String, nullable=True)
    s3_access_key = Column(String, nullable=True)
    s3_secret_key = Column(String, nullable=True)
    local_path = Column(String, nullable=True)
    restic_password = Column(String, nullable=False)
    insecure_tls = Column(Boolean, nullable=False, default=False, server_default="false")
    config_backup_enabled = Column(Boolean, nullable=False, default=False, server_default="false")
    config_backup_passphrase = Column(String, nullable=True)  # Fernet-encrypted at rest
    last_config_backup_at = Column(DateTime(timezone=True), nullable=True)
    last_config_backup_status = Column(String, nullable=True)  # "ok" | "failed"
    last_config_backup_error = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=_utcnow)


# Legacy alias — kept so external imports (notably alembic env.py) don't break
# during the transition. New code MUST use Repository.
BackupDestination = Repository


class RepositoryAttachment(Base):
    """Maps an orphan restic prefix in a Repository to an Account as a
    read-only restore source. Future backups keep using the account's own
    UUID prefix; detaching deletes only this row, never bucket data."""

    __tablename__ = "repository_attachments"

    id = Column(String, primary_key=True, default=_new_uuid)
    repository_id = Column(
        String,
        ForeignKey("backup_destinations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    account_id = Column(
        String, ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    prefix = Column(String, nullable=False)
    restic_password = Column(String, nullable=True)  # Fernet; NULL = repository password
    created_at = Column(DateTime(timezone=True), default=_utcnow)

    __table_args__ = (
        UniqueConstraint("repository_id", "prefix", name="uq_repo_attachment_prefix"),
    )

    repository = relationship(
        "Repository",
        backref=backref("attachments", passive_deletes=True, cascade="all, delete-orphan"),
    )
    account = relationship(
        "Account",
        backref=backref("repo_attachments", passive_deletes=True, cascade="all, delete-orphan"),
    )


class BackupPolicy(Base):
    """Per-mailbox off-site backup policy (Repository + schedule + retention).

    DB table name kept as 'account_backups' for backward compatibility with
    existing data and migrations 010/011. Backref on Account is named
    `backup_policies` to match the canonical noun.
    """

    __tablename__ = "account_backups"

    id = Column(String, primary_key=True, default=_new_uuid)
    account_id = Column(
        String, ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    destination_id = Column(String, ForeignKey("backup_destinations.id"), nullable=False)
    enabled = Column(Boolean, nullable=False, default=True, server_default="true")
    schedule = Column(String, nullable=False, default="0 2 * * *")
    retention_preset = Column(
        Enum(RetentionPreset), nullable=False, default=RetentionPreset.standard
    )
    keep_daily = Column(Integer, nullable=True)
    keep_weekly = Column(Integer, nullable=True)
    keep_monthly = Column(Integer, nullable=True)
    last_backup_at = Column(DateTime(timezone=True), nullable=True)
    last_run_at = Column(DateTime(timezone=True), nullable=True)
    last_successful_run_at = Column(DateTime(timezone=True), nullable=True)
    last_snapshot_count = Column(Integer, nullable=False, default=0, server_default="0")
    last_snapshot_at = Column(DateTime(timezone=True), nullable=True)
    last_status = Column(Enum(BackupStatus), nullable=False, default=BackupStatus.idle)
    last_error = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=_utcnow)

    account = relationship("Account", backref="backup_policies")
    destination = relationship("Repository")


# Legacy alias — kept so external imports don't break during the transition.
# New code MUST use BackupPolicy.
AccountBackup = BackupPolicy


class Recovery(Base):
    """A snapshot recovered from a Repository, attached to its source Account.

    Distinct from an Account: a Recovery is a read-only artefact (not a live
    mailbox). It is exposed through Dovecot as an additional namespace under
    the source Account's owner(s), and never synced. Removing the Recovery
    deletes both the DB row and the on-disk extracted Maildir.
    """

    __tablename__ = "recoveries"

    id = Column(String, primary_key=True, default=_new_uuid)
    account_id = Column(
        String, ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    repository_id = Column(
        String, ForeignKey("backup_destinations.id", ondelete="SET NULL"), nullable=True
    )
    snapshot_id = Column(String, nullable=False)  # restic short_id
    snapshot_time = Column(DateTime(timezone=True), nullable=True)  # when the snapshot was taken
    restored_at = Column(DateTime(timezone=True), default=_utcnow)
    restore_path = Column(String, nullable=False)  # absolute path to the recovered Maildir root
    status = Column(Enum(RecoveryStatus), nullable=False, default=RecoveryStatus.restoring)
    error = Column(Text, nullable=True)
    size_bytes = Column(Integer, nullable=True)  # disk size of the recovered tree
    kind = Column(
        Enum(RecoveryKind),
        nullable=False,
        default=RecoveryKind.persistent,
        server_default=RecoveryKind.persistent.value,
    )
    last_accessed_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=_utcnow,
        server_default=text("now()"),
    )
    ttl_minutes = Column(Integer, nullable=True)  # NULL = no TTL

    account = relationship("Account", backref="recoveries")
    repository = relationship("Repository")


class StagingArea(Base):
    """Per-user writable staging mailbox for curated restores.

    At most one per user. The on-disk Maildir under the user's dovecot home
    is the source of truth for contents (webmail deletions just remove
    files); rows track quota, origin and expiry. TTL clock starts at
    creation; the scheduler purges expired areas.
    """

    __tablename__ = "staging_areas"

    id = Column(String, primary_key=True, default=_new_uuid)
    user_id = Column(
        String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    max_bytes = Column(BigInteger, nullable=False, server_default=text("0"))
    bytes_used = Column(BigInteger, nullable=False, server_default=text("0"))

    messages = relationship(
        "StagingMessage",
        backref=backref("staging", passive_deletes=True),
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class StagingMessage(Base):
    """Origin bookkeeping for one staged message (file lives in the Maildir)."""

    __tablename__ = "staging_messages"

    id = Column(String, primary_key=True, default=_new_uuid)
    staging_id = Column(
        String,
        ForeignKey("staging_areas.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    source_account_id = Column(
        String, ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False
    )
    message_id_hash = Column(LargeBinary(20), nullable=False)
    original_folder = Column(Text, nullable=False)
    staged_filename = Column(Text, nullable=False)
    size_bytes = Column(Integer, nullable=False)
    staged_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)


class MailIndexMessage(Base):
    """Per-account, per-message metadata index used by the search API.

    One row per (account_id, message_id_hash). Headers only — no body content.
    Deep search adds a full-folder Dovecot body search and unions the matches
    into this index query by message_id_hash (see services/search_service.py).
    """

    __tablename__ = "messages"
    __table_args__ = {"schema": "mail_index"}  # noqa: RUF012

    account_id = Column(String, ForeignKey("accounts.id", ondelete="CASCADE"), primary_key=True)
    message_id_hash = Column(LargeBinary(20), primary_key=True)
    message_id = Column(Text, nullable=False)
    date_sent = Column(DateTime(timezone=True))
    from_addr = Column(Text)
    from_name = Column(Text)
    subject = Column(Text)
    to_addrs = Column(ARRAY(Text).with_variant(JSON(), "sqlite"))
    folder_path = Column(Text, nullable=False)
    maildir_filename = Column(Text, nullable=False)
    size_bytes = Column(Integer)
    first_seen_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=_utcnow,
        server_default=text("now()"),
    )
    last_seen_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=_utcnow,
        server_default=text("now()"),
    )
    deleted_at = Column(DateTime(timezone=True))
    tsv = Column(TSVECTOR().with_variant(Text(), "sqlite"))
    has_attachments = Column(Boolean, nullable=False, server_default=text("false"))
    attachments_indexed_at = Column(DateTime(timezone=True))


class MailIndexAttachment(Base):
    """One row per attachment MIME part (a leaf part with a filename).

    `part_index` counts non-multipart leaves in msg.walk() order — the
    download endpoint (Plan 3) re-walks with the same algorithm, so the
    numbering is part of the contract with `index_service._parse_attachments`.
    `content_text` stays NULL until the Tika extraction phase (Plan 3).
    `ext` is normalized: lowercase, no leading dot, "" when the filename has
    no extension.
    """

    __tablename__ = "attachments"
    __table_args__ = (
        ForeignKeyConstraint(
            ["account_id", "message_id_hash"],
            ["mail_index.messages.account_id", "mail_index.messages.message_id_hash"],
            ondelete="CASCADE",
        ),
        {"schema": "mail_index"},
    )

    account_id = Column(String, primary_key=True)
    message_id_hash = Column(LargeBinary(20), primary_key=True)
    part_index = Column(Integer, primary_key=True)
    filename = Column(Text, nullable=False)
    ext = Column(Text, nullable=False, server_default="")
    size_bytes = Column(Integer)
    content_type = Column(Text)
    content_text = Column(Text)

    message = relationship(
        "MailIndexMessage",
        backref=backref("attachments", passive_deletes=True, cascade="all, delete-orphan"),
    )


# Must match idx_attachments_fts in migration 018 verbatim — PG matches
# expression indexes structurally, so any deviation means a seq scan.
# The column names are deliberately unqualified (the index is defined that
# way): mail_index.messages must NEVER gain filename or content_text columns,
# or this expression becomes ambiguous under the attachment search JOIN
# (search_service._build_attachment_query) — a loud PG error, but better never.
ATTACHMENTS_FTS_EXPR = (
    "to_tsvector('simple', coalesce(filename, '') || ' ' || coalesce(content_text, ''))"
)


class SnapshotMessage(Base):
    """Join table: which messages exist in which restic snapshots.

    Forward-only at install: bits are set by backup_worker after each restic
    backup succeeds. CLI command `mfb index backfill-snapshots` populates the
    history retroactively.
    """

    __tablename__ = "snapshot_messages"
    __table_args__ = {"schema": "mail_index"}  # noqa: RUF012

    snapshot_id = Column(Text, primary_key=True)
    account_id = Column(String, primary_key=True)
    message_id_hash = Column(LargeBinary(20), primary_key=True)


class MailIndexRebuildStatus(Base):
    """Per-account watermark + state for the index lifecycle.

    states: idle | live_indexing | snap_backfilling | failed
    """

    __tablename__ = "rebuild_status"
    __table_args__ = {"schema": "mail_index"}  # noqa: RUF012

    account_id = Column(String, ForeignKey("accounts.id", ondelete="CASCADE"), primary_key=True)
    state = Column(Text, nullable=False, default="idle", server_default="idle")
    last_indexed_at = Column(DateTime(timezone=True))
    backfill_progress = Column(Integer)
    backfill_total = Column(Integer)
    last_error = Column(Text)
