# Mail Restore Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enable users to push archived emails from any MFB account back to any other MFB account's IMAP server, with folder browse, FTS search, and per-email selection.

**Architecture:** Dovecot IMAP local (read) → imaplib APPEND (write). Background RestoreJob with progress tracking. Any-to-any between configured accounts. UI at `/restore` with HTMX-driven wizard flow.

**Tech Stack:** Python imaplib (stdlib), SQLAlchemy, Alembic, FastAPI, Jinja2/HTMX/Pico CSS, Dovecot IMAP

**Spec:** `docs/superpowers/specs/2026-05-06-mail-restore-design.md`

---

## File Structure

| Action | File | Responsibility |
|--------|------|----------------|
| Create | `src/mailfallback/services/restore_service.py` | Job CRUD, validation, orchestration |
| Create | `src/mailfallback/services/restore_worker.py` | Background IMAP read→write execution |
| Create | `src/mailfallback/routers/restore.py` | REST API for browse, search, restore ops |
| Create | `src/mailfallback/routers/ui_restore.py` | UI routes + HTMX partials |
| Create | `src/mailfallback/templates/restore.html` | Main restore page |
| Create | `src/mailfallback/templates/partials/restore_folders.html` | Folder list with checkboxes |
| Create | `src/mailfallback/templates/partials/restore_messages.html` | Message list with checkboxes |
| Create | `src/mailfallback/templates/partials/restore_progress.html` | Progress bar + counters |
| Create | `src/mailfallback/templates/partials/restore_history.html` | Job history table |
| Create | `alembic/versions/007_add_restore_jobs.py` | RestoreJob table migration |
| Create | `tests/test_restore_service.py` | Service layer tests |
| Create | `tests/test_restore_worker.py` | Worker tests with mocked IMAP |
| Create | `tests/test_restore_api.py` | API endpoint tests |
| Create | `tests/test_restore_ui.py` | UI route tests |
| Modify | `src/mailfallback/models.py` | Add RestoreMode enum + RestoreJob model |
| Modify | `src/mailfallback/services/imap_check.py` | Extract `connect_imap()` function |
| Modify | `src/mailfallback/services/audit_service.py` | Add restore action labels |
| Modify | `src/mailfallback/templates/base.html` | Add Restore sidebar link |
| Modify | `src/mailfallback/app.py` | Register restore routers |
| Modify | `src/mailfallback/static/css/style.css` | Restore page styles |

---

## Task 1: RestoreJob Model + Migration

**Files:**
- Modify: `src/mailfallback/models.py`
- Create: `alembic/versions/007_add_restore_jobs.py`
- Create: `tests/test_restore_model.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_restore_model.py`:

```python
from mailfallback.models import RestoreJob, RestoreMode, JobStatus


def test_restore_job_defaults(db_session, default_store):
    from mailfallback.models import Account, User

    user = User(username="restoreuser", password_hash="x", store_id=default_store.id)
    db_session.add(user)
    db_session.flush()

    src = Account(
        name="src",
        imap_host="imap.src.com",
        imap_port=993,
        maildir_path="/data/mailboxes/src-uuid",
        store_id=default_store.id,
    )
    tgt = Account(
        name="tgt",
        imap_host="imap.tgt.com",
        imap_port=993,
        maildir_path="/data/mailboxes/tgt-uuid",
        store_id=default_store.id,
    )
    db_session.add_all([src, tgt])
    db_session.flush()

    job = RestoreJob(
        source_account_id=src.id,
        target_account_id=tgt.id,
        restore_mode=RestoreMode.full,
        requested_by=user.id,
    )
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)

    assert job.id is not None
    assert job.status == JobStatus.pending
    assert job.restore_mode == RestoreMode.full
    assert job.folder_mapping == "original"
    assert job.skip_duplicates is True
    assert job.total_messages == 0
    assert job.restored_messages == 0
    assert job.skipped_messages == 0
    assert job.failed_messages == 0
    assert job.error is None
    assert job.selected_folders is None
    assert job.selected_uids is None
    assert job.requested_at is not None


def test_restore_mode_enum():
    assert RestoreMode.full == "full"
    assert RestoreMode.folder == "folder"
    assert RestoreMode.selection == "selection"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_restore_model.py -v`
Expected: ImportError — `RestoreJob` and `RestoreMode` don't exist yet.

- [ ] **Step 3: Add RestoreMode enum and RestoreJob model to models.py**

Add after the `MigrationStatus` enum (after line 61 in `models.py`):

```python
class RestoreMode(enum.StrEnum):
    full = "full"
    folder = "folder"
    selection = "selection"
```

Add after the `AuditLog` class (after line 231 in `models.py`):

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_restore_model.py -v`
Expected: PASS

- [ ] **Step 5: Write the Alembic migration**

Create `alembic/versions/007_add_restore_jobs.py`:

```python
"""Add restore_jobs table.

Revision ID: 007
Revises: 006
Create Date: 2026-05-06
"""

import sqlalchemy as sa

from alembic import op

revision = "007"
down_revision = "006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "restore_jobs",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("source_account_id", sa.String(), nullable=False),
        sa.Column("target_account_id", sa.String(), nullable=False),
        sa.Column(
            "status",
            sa.Enum("pending", "running", "completed", "failed", name="jobstatus", create_constraint=False),
            nullable=False,
        ),
        sa.Column(
            "restore_mode",
            sa.Enum("full", "folder", "selection", name="restoremode", create_constraint=True),
            nullable=False,
        ),
        sa.Column("folder_mapping", sa.String(), nullable=False, server_default="original"),
        sa.Column("skip_duplicates", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("selected_folders", sa.JSON(), nullable=True),
        sa.Column("selected_uids", sa.JSON(), nullable=True),
        sa.Column("total_messages", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("restored_messages", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("skipped_messages", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failed_messages", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("requested_by", sa.String(), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["source_account_id"], ["accounts.id"]),
        sa.ForeignKeyConstraint(["target_account_id"], ["accounts.id"]),
        sa.ForeignKeyConstraint(["requested_by"], ["users.id"]),
    )


def downgrade() -> None:
    op.drop_table("restore_jobs")
```

- [ ] **Step 6: Run migration drift test**

Run: `uv run pytest tests/test_alembic_sync.py -v`
Expected: PASS — migration and model are in sync.

- [ ] **Step 7: Run full test suite**

Run: `uv run pytest tests/ -q`
Expected: All tests pass (258+).

- [ ] **Step 8: Commit**

```bash
git add src/mailfallback/models.py alembic/versions/007_add_restore_jobs.py tests/test_restore_model.py
git commit -m "feat: add RestoreJob model and migration 007"
```

---

## Task 2: Extract `connect_imap()` from imap_check.py

**Files:**
- Modify: `src/mailfallback/services/imap_check.py`
- Create: `tests/test_imap_connect.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_imap_connect.py`:

```python
from unittest.mock import MagicMock, patch

from mailfallback.services.imap_check import connect_imap


@patch("mailfallback.services.imap_check.imaplib.IMAP4_SSL")
def test_connect_imap_ssl(mock_ssl):
    mock_conn = MagicMock()
    mock_ssl.return_value = mock_conn

    conn = connect_imap("imap.example.com", 993, "IMAPS", "user@example.com", "pass123")

    mock_ssl.assert_called_once_with("imap.example.com", 993, timeout=30)
    mock_conn.login.assert_called_once_with("user@example.com", "pass123")
    assert conn is mock_conn


@patch("mailfallback.services.imap_check.imaplib.IMAP4")
def test_connect_imap_starttls(mock_imap):
    mock_conn = MagicMock()
    mock_imap.return_value = mock_conn

    conn = connect_imap("imap.example.com", 143, "STARTTLS", "user", "pass")

    mock_imap.assert_called_once_with("imap.example.com", 143, timeout=30)
    mock_conn.starttls.assert_called_once()
    mock_conn.login.assert_called_once_with("user", "pass")
    assert conn is mock_conn


@patch("mailfallback.services.imap_check.imaplib.IMAP4")
def test_connect_imap_plain(mock_imap):
    mock_conn = MagicMock()
    mock_imap.return_value = mock_conn

    conn = connect_imap("imap.example.com", 143, "NONE", "user", "pass")

    mock_imap.assert_called_once_with("imap.example.com", 143, timeout=30)
    mock_conn.login.assert_called_once_with("user", "pass")
    assert conn is mock_conn
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_imap_connect.py -v`
Expected: ImportError — `connect_imap` doesn't exist.

- [ ] **Step 3: Add `connect_imap()` to imap_check.py**

Add at the top of `imap_check.py`, after the imports:

```python
def connect_imap(
    host: str,
    port: int = 993,
    tls_type: str = "IMAPS",
    username: str | None = None,
    password: str | None = None,
    timeout: int = 30,
) -> imaplib.IMAP4:
    if tls_type == "IMAPS":
        conn = imaplib.IMAP4_SSL(host, port, timeout=timeout)
    elif tls_type == "STARTTLS":
        conn = imaplib.IMAP4(host, port, timeout=timeout)
        conn.starttls()
    else:
        conn = imaplib.IMAP4(host, port, timeout=timeout)
    if username and password:
        conn.login(username, password)
    return conn
```

Then refactor `check_imap_credentials()` to use it internally — replace lines 12–19 with:

```python
    try:
        conn = connect_imap(host, port, tls_type, timeout=10)
        greeting = conn.welcome.decode() if conn.welcome else "OK"
        auth_mechs = _extract_auth_capabilities(conn)

        login_ok = None
        login_message = None
        if username and password:
            try:
                conn.login(username, password)
```

- [ ] **Step 4: Run tests to verify**

Run: `uv run pytest tests/test_imap_connect.py tests/test_connection_test.py -v`
Expected: All pass. The existing `test_connection_test.py` still passes (refactor didn't change behavior).

- [ ] **Step 5: Commit**

```bash
git add src/mailfallback/services/imap_check.py tests/test_imap_connect.py
git commit -m "refactor: extract connect_imap() from imap_check for reuse"
```

---

## Task 3: Add Restore Audit Labels

**Files:**
- Modify: `src/mailfallback/services/audit_service.py`

- [ ] **Step 1: Add restore labels to ACTION_LABELS dict**

In `audit_service.py`, add three entries before the closing brace of `ACTION_LABELS` (after line 33):

```python
    "restore.start": "Started mail restore",
    "restore.complete": "Completed mail restore",
    "restore.failed": "Mail restore failed",
```

- [ ] **Step 2: Run existing audit tests**

Run: `uv run pytest tests/test_audit_service.py -v`
Expected: All pass.

- [ ] **Step 3: Commit**

```bash
git add src/mailfallback/services/audit_service.py
git commit -m "feat: add restore audit action labels"
```

---

## Task 4: Restore Service (Job CRUD + Validation)

**Files:**
- Create: `src/mailfallback/services/restore_service.py`
- Create: `tests/test_restore_service.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_restore_service.py`:

```python
import pytest
from mailfallback.models import Account, JobStatus, RestoreJob, RestoreMode, User
from mailfallback.services.restore_service import (
    cancel_restore_job,
    create_restore_job,
    get_restore_job,
    list_restore_jobs,
)


@pytest.fixture
def restore_fixtures(db_session, default_store):
    user = User(username="restorer", password_hash="x", store_id=default_store.id)
    db_session.add(user)
    db_session.flush()
    src = Account(
        name="source",
        imap_host="imap.src.com",
        imap_port=993,
        maildir_path="/data/mailboxes/src",
        store_id=default_store.id,
        credentials="encrypted-creds",
    )
    tgt = Account(
        name="target",
        imap_host="imap.tgt.com",
        imap_port=993,
        maildir_path="/data/mailboxes/tgt",
        store_id=default_store.id,
        credentials="encrypted-creds",
    )
    db_session.add_all([src, tgt])
    db_session.commit()
    db_session.refresh(user)
    db_session.refresh(src)
    db_session.refresh(tgt)
    return {"user": user, "source": src, "target": tgt}


def test_create_restore_job(db_session, restore_fixtures):
    f = restore_fixtures
    job = create_restore_job(
        db_session,
        source_account_id=f["source"].id,
        target_account_id=f["target"].id,
        restore_mode="full",
        requested_by=f["user"].id,
    )
    assert job is not None
    assert job.status == JobStatus.pending
    assert job.restore_mode == RestoreMode.full
    assert job.source_account_id == f["source"].id
    assert job.target_account_id == f["target"].id


def test_create_restore_job_rejects_suspended_source(db_session, restore_fixtures):
    f = restore_fixtures
    f["source"].suspended = True
    db_session.commit()
    job = create_restore_job(
        db_session,
        source_account_id=f["source"].id,
        target_account_id=f["target"].id,
        restore_mode="full",
        requested_by=f["user"].id,
    )
    assert job is None


def test_create_restore_job_rejects_suspended_target(db_session, restore_fixtures):
    f = restore_fixtures
    f["target"].suspended = True
    db_session.commit()
    job = create_restore_job(
        db_session,
        source_account_id=f["source"].id,
        target_account_id=f["target"].id,
        restore_mode="full",
        requested_by=f["user"].id,
    )
    assert job is None


def test_create_restore_job_rejects_migrating(db_session, restore_fixtures):
    f = restore_fixtures
    f["target"].migrating = True
    db_session.commit()
    job = create_restore_job(
        db_session,
        source_account_id=f["source"].id,
        target_account_id=f["target"].id,
        restore_mode="full",
        requested_by=f["user"].id,
    )
    assert job is None


def test_create_restore_job_rejects_no_credentials(db_session, restore_fixtures):
    f = restore_fixtures
    f["target"].credentials = None
    db_session.commit()
    job = create_restore_job(
        db_session,
        source_account_id=f["source"].id,
        target_account_id=f["target"].id,
        restore_mode="full",
        requested_by=f["user"].id,
    )
    assert job is None


def test_create_restore_job_rejects_duplicate(db_session, restore_fixtures):
    f = restore_fixtures
    create_restore_job(
        db_session,
        source_account_id=f["source"].id,
        target_account_id=f["target"].id,
        restore_mode="full",
        requested_by=f["user"].id,
    )
    second = create_restore_job(
        db_session,
        source_account_id=f["source"].id,
        target_account_id=f["target"].id,
        restore_mode="full",
        requested_by=f["user"].id,
    )
    assert second is None


def test_get_restore_job(db_session, restore_fixtures):
    f = restore_fixtures
    job = create_restore_job(
        db_session,
        source_account_id=f["source"].id,
        target_account_id=f["target"].id,
        restore_mode="folder",
        requested_by=f["user"].id,
        selected_folders=["INBOX", "Sent"],
    )
    fetched = get_restore_job(db_session, job.id)
    assert fetched.id == job.id
    assert fetched.selected_folders == ["INBOX", "Sent"]


def test_list_restore_jobs(db_session, restore_fixtures):
    f = restore_fixtures
    create_restore_job(
        db_session,
        source_account_id=f["source"].id,
        target_account_id=f["target"].id,
        restore_mode="full",
        requested_by=f["user"].id,
    )
    jobs = list_restore_jobs(db_session, f["source"].id)
    assert len(jobs) == 1


def test_cancel_restore_job(db_session, restore_fixtures):
    f = restore_fixtures
    job = create_restore_job(
        db_session,
        source_account_id=f["source"].id,
        target_account_id=f["target"].id,
        restore_mode="full",
        requested_by=f["user"].id,
    )
    job.status = JobStatus.running
    db_session.commit()
    result = cancel_restore_job(db_session, job.id)
    assert result is True
    db_session.refresh(job)
    assert job.status == JobStatus.failed
    assert job.error == "Cancelled by user"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_restore_service.py -v`
Expected: ImportError — `restore_service` doesn't exist.

- [ ] **Step 3: Implement restore_service.py**

Create `src/mailfallback/services/restore_service.py`:

```python
from sqlalchemy.orm import Session

from mailfallback.models import Account, JobStatus, RestoreJob, RestoreMode


def create_restore_job(
    db: Session,
    *,
    source_account_id: str,
    target_account_id: str,
    restore_mode: str,
    requested_by: str,
    folder_mapping: str = "original",
    skip_duplicates: bool = True,
    selected_folders: list[str] | None = None,
    selected_uids: dict | None = None,
) -> RestoreJob | None:
    source = db.query(Account).filter(Account.id == source_account_id).first()
    target = db.query(Account).filter(Account.id == target_account_id).first()
    if not source or not target:
        return None

    if source.suspended or source.migrating:
        return None
    if target.suspended or target.migrating:
        return None
    if not target.credentials:
        return None

    existing = (
        db.query(RestoreJob)
        .filter(
            RestoreJob.source_account_id == source_account_id,
            RestoreJob.target_account_id == target_account_id,
            RestoreJob.status.in_([JobStatus.pending, JobStatus.running]),
        )
        .first()
    )
    if existing:
        return None

    job = RestoreJob(
        source_account_id=source_account_id,
        target_account_id=target_account_id,
        restore_mode=RestoreMode(restore_mode),
        folder_mapping=folder_mapping,
        skip_duplicates=skip_duplicates,
        selected_folders=selected_folders,
        selected_uids=selected_uids,
        requested_by=requested_by,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def get_restore_job(db: Session, job_id: str) -> RestoreJob | None:
    return db.query(RestoreJob).filter(RestoreJob.id == job_id).first()


def list_restore_jobs(db: Session, account_id: str, limit: int = 50) -> list[RestoreJob]:
    return (
        db.query(RestoreJob)
        .filter(
            (RestoreJob.source_account_id == account_id)
            | (RestoreJob.target_account_id == account_id)
        )
        .order_by(RestoreJob.requested_at.desc())
        .limit(limit)
        .all()
    )


def list_restore_jobs_for_user(db: Session, user_id: str, limit: int = 50) -> list[RestoreJob]:
    return (
        db.query(RestoreJob)
        .filter(RestoreJob.requested_by == user_id)
        .order_by(RestoreJob.requested_at.desc())
        .limit(limit)
        .all()
    )


def cancel_restore_job(db: Session, job_id: str) -> bool:
    job = db.query(RestoreJob).filter(RestoreJob.id == job_id).first()
    if not job or job.status not in (JobStatus.pending, JobStatus.running):
        return False
    job.status = JobStatus.failed
    job.error = "Cancelled by user"
    db.commit()
    return True
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_restore_service.py -v`
Expected: All pass.

- [ ] **Step 5: Commit**

```bash
git add src/mailfallback/services/restore_service.py tests/test_restore_service.py
git commit -m "feat: add restore_service with job CRUD and validation"
```

---

## Task 5: Restore Worker (IMAP Read → Write)

**Files:**
- Create: `src/mailfallback/services/restore_worker.py`
- Create: `tests/test_restore_worker.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_restore_worker.py`:

```python
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest

from mailfallback.models import Account, JobStatus, RestoreJob, RestoreMode, User
from mailfallback.services.restore_worker import execute_restore_job


@pytest.fixture
def restore_job_fixtures(db_session, default_store):
    user = User(username="worker_test", password_hash="x", store_id=default_store.id)
    db_session.add(user)
    db_session.flush()

    src = Account(
        name="source",
        email_address="src@example.com",
        imap_host="imap.src.com",
        imap_port=993,
        maildir_path="/data/mailboxes/src",
        store_id=default_store.id,
        credentials="encrypted",
    )
    tgt = Account(
        name="target",
        email_address="tgt@example.com",
        imap_host="imap.tgt.com",
        imap_port=993,
        maildir_path="/data/mailboxes/tgt",
        store_id=default_store.id,
        credentials="encrypted",
    )
    db_session.add_all([src, tgt])
    db_session.flush()
    src.owners.append(user)
    db_session.commit()

    job = RestoreJob(
        source_account_id=src.id,
        target_account_id=tgt.id,
        restore_mode=RestoreMode.folder,
        selected_folders=["INBOX"],
        skip_duplicates=False,
        requested_by=user.id,
    )
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    return {"job": job, "user": user, "source": src, "target": tgt}


@patch("mailfallback.services.restore_worker.connect_imap")
@patch("mailfallback.services.restore_worker.decrypt_credentials")
def test_execute_restore_folder(mock_decrypt, mock_connect, db_session, restore_job_fixtures):
    f = restore_job_fixtures
    mock_decrypt.return_value = "plaintext-pass"

    src_conn = MagicMock()
    tgt_conn = MagicMock()
    mock_connect.side_effect = [src_conn, tgt_conn]

    src_conn.list.return_value = ("OK", [b'(\\HasNoChildren) "/" "INBOX"'])
    src_conn.select.return_value = ("OK", [b"2"])
    src_conn.search.return_value = ("OK", [b"1 2"])
    src_conn.fetch.side_effect = [
        ("OK", [(b"1 (RFC822 {100}", b"From: a@b.com\r\nSubject: Test1\r\n\r\nBody1"), b")"]),
        ("OK", [(b"2 (RFC822 {100}", b"From: c@d.com\r\nSubject: Test2\r\n\r\nBody2"), b")"]),
    ]

    tgt_conn.append.return_value = ("OK", [b"APPEND completed"])

    execute_restore_job(db_session, f["job"].id)

    db_session.refresh(f["job"])
    assert f["job"].status == JobStatus.completed
    assert f["job"].restored_messages == 2
    assert f["job"].total_messages == 2
    assert tgt_conn.append.call_count == 2


@patch("mailfallback.services.restore_worker.connect_imap")
@patch("mailfallback.services.restore_worker.decrypt_credentials")
def test_execute_restore_job_not_found(mock_decrypt, mock_connect, db_session):
    execute_restore_job(db_session, "nonexistent-id")
    mock_connect.assert_not_called()


@patch("mailfallback.services.restore_worker.connect_imap")
@patch("mailfallback.services.restore_worker.decrypt_credentials")
def test_execute_restore_handles_append_failure(mock_decrypt, mock_connect, db_session, restore_job_fixtures):
    f = restore_job_fixtures
    mock_decrypt.return_value = "plaintext-pass"

    src_conn = MagicMock()
    tgt_conn = MagicMock()
    mock_connect.side_effect = [src_conn, tgt_conn]

    src_conn.list.return_value = ("OK", [b'(\\HasNoChildren) "/" "INBOX"'])
    src_conn.select.return_value = ("OK", [b"1"])
    src_conn.search.return_value = ("OK", [b"1"])
    src_conn.fetch.return_value = (
        "OK",
        [(b"1 (RFC822 {50}", b"From: a@b.com\r\nSubject: Fail\r\n\r\nBody"), b")"],
    )

    tgt_conn.append.return_value = ("NO", [b"Quota exceeded"])

    execute_restore_job(db_session, f["job"].id)

    db_session.refresh(f["job"])
    assert f["job"].status == JobStatus.completed
    assert f["job"].restored_messages == 0
    assert f["job"].failed_messages == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_restore_worker.py -v`
Expected: ImportError — `restore_worker` doesn't exist.

- [ ] **Step 3: Implement restore_worker.py**

Create `src/mailfallback/services/restore_worker.py`:

```python
import imaplib
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from mailfallback.config import settings
from mailfallback.db import SessionLocal
from mailfallback.models import Account, JobStatus, RestoreJob, User
from mailfallback.security import decrypt_credentials
from mailfallback.services.imap_check import connect_imap

logger = logging.getLogger(__name__)

_restore_executor: ThreadPoolExecutor | None = None
_cancel_flags: set[str] = set()

RETRY_DELAYS = [1, 3, 10]


def _retry_imap(fn, *args):
    for attempt, delay in enumerate(RETRY_DELAYS):
        try:
            return fn(*args)
        except (imaplib.IMAP4.error, OSError) as e:
            if attempt == len(RETRY_DELAYS) - 1:
                raise
            logger.warning("IMAP error (attempt %d/%d), retrying in %ds: %s", attempt + 1, len(RETRY_DELAYS), delay, e)
            import time
            time.sleep(delay)


def get_restore_executor() -> ThreadPoolExecutor:
    global _restore_executor
    if _restore_executor is None:
        _restore_executor = ThreadPoolExecutor(
            max_workers=2,
            thread_name_prefix="restore-worker",
        )
    return _restore_executor


def submit_restore_job(job_id: str) -> None:
    def _run():
        db = SessionLocal()
        try:
            execute_restore_job(db, job_id)
        finally:
            db.close()

    get_restore_executor().submit(_run)


def request_cancel(job_id: str) -> None:
    _cancel_flags.add(job_id)


def execute_restore_job(db: Session, job_id: str) -> None:
    job = db.query(RestoreJob).filter(RestoreJob.id == job_id).first()
    if not job:
        return

    source = db.query(Account).filter(Account.id == job.source_account_id).first()
    target = db.query(Account).filter(Account.id == job.target_account_id).first()
    if not source or not target:
        _fail_job(db, job, "Source or target account not found")
        return

    if source.suspended or target.suspended:
        _fail_job(db, job, "Account is suspended")
        return

    owner = db.query(User).join(User.accounts).filter(Account.id == source.id).first()
    if not owner:
        _fail_job(db, job, "Source account has no owner")
        return

    target_creds = decrypt_credentials(target.credentials, settings.secret_key) if target.credentials else None
    if not target_creds:
        _fail_job(db, job, "Target account has no credentials")
        return

    job.status = JobStatus.running
    job.started_at = datetime.now(UTC)
    db.commit()

    src_conn = None
    tgt_conn = None
    try:
        src_conn = connect_imap(
            settings.dovecot_imap_host,
            settings.dovecot_imap_port,
            "NONE",
            owner.username,
            "internal-auth",
        )

        if target.auth_type.value == "oauth2":
            tgt_password = _refresh_target_token(target_creds, db, target)
            if not tgt_password:
                _fail_job(db, job, "Failed to refresh OAuth2 token for target")
                return
        else:
            tgt_password = target_creds

        tgt_conn = connect_imap(
            target.imap_host,
            target.imap_port,
            target.tls_type or "IMAPS",
            target.imap_user or target.email_address,
            tgt_password,
        )

        folders = _resolve_folders(src_conn, source, job)
        if not folders:
            _fail_job(db, job, "No folders found to restore")
            return

        _execute_restore(db, job, src_conn, tgt_conn, folders)

    except (imaplib.IMAP4.error, OSError) as e:
        _fail_job(db, job, f"IMAP error: {e}")
    except Exception as e:
        _fail_job(db, job, str(e))
    finally:
        _cancel_flags.discard(job_id)
        for conn in (src_conn, tgt_conn):
            if conn:
                try:
                    conn.logout()
                except Exception:
                    pass
        if job.status == JobStatus.running:
            job.status = JobStatus.completed
            job.completed_at = datetime.now(UTC)
        db.commit()

        try:
            from mailfallback.services.audit_service import log_action

            requester = db.query(User).filter(User.id == job.requested_by).first()
            if requester:
                action = "restore.complete" if job.status == JobStatus.completed else "restore.failed"
                log_action(
                    db,
                    user=requester,
                    action=action,
                    resource_type="restore",
                    resource_id=job.id,
                    resource_name=f"{source.name} → {target.name}",
                    details={
                        "restored": job.restored_messages,
                        "skipped": job.skipped_messages,
                        "failed": job.failed_messages,
                        "total": job.total_messages,
                    },
                )
        except Exception:
            logger.warning("Failed to write audit log for restore %s", job_id)


def _resolve_folders(src_conn, source, job):
    namespace_prefix = _get_namespace_prefix(source)
    status, folder_data = src_conn.list(f'"{namespace_prefix}"', "*")
    if status != "OK" or not folder_data:
        return []

    all_folders = []
    for item in folder_data:
        if not item or item == b"":
            continue
        decoded = item.decode() if isinstance(item, bytes) else item
        parts = decoded.rsplit('" "', 1)
        if len(parts) == 2:
            folder_name = parts[1].rstrip('"')
            if namespace_prefix:
                folder_name_short = folder_name.removeprefix(namespace_prefix)
            else:
                folder_name_short = folder_name
            all_folders.append((folder_name, folder_name_short))

    if job.selected_folders:
        return [(full, short) for full, short in all_folders if short in job.selected_folders]
    return all_folders


def _get_namespace_prefix(account):
    short_id = account.id[:8]
    return f"{account.name} ({account.email_address}) [{short_id}]/"


def _execute_restore(db, job, src_conn, tgt_conn, folders):
    total = 0
    for full_folder, _ in folders:
        status, data = src_conn.select(f'"{full_folder}"', readonly=True)
        if status != "OK":
            continue
        count = int(data[0].decode())
        total += count
    job.total_messages = total
    db.commit()

    for full_folder, short_folder in folders:
        if job.id in _cancel_flags:
            _fail_job(db, job, "Cancelled by user")
            return

        status, _ = src_conn.select(f'"{full_folder}"', readonly=True)
        if status != "OK":
            continue

        uid_filter = None
        if job.selected_uids and short_folder in job.selected_uids:
            uid_filter = set(job.selected_uids[short_folder])

        status, data = src_conn.search(None, "ALL")
        if status != "OK" or not data[0]:
            continue
        uids = data[0].split()

        existing_ids = set()
        if job.skip_duplicates:
            target_folder = _map_folder(short_folder, job.folder_mapping)
            existing_ids = _get_existing_message_ids(tgt_conn, target_folder)

        for uid_bytes in uids:
            if job.id in _cancel_flags:
                _fail_job(db, job, "Cancelled by user")
                return

            uid = uid_bytes.decode()
            if uid_filter and int(uid) not in uid_filter:
                continue

            try:
                _restore_single_message(
                    src_conn, tgt_conn, uid, full_folder, short_folder,
                    job, existing_ids, db,
                )
            except Exception:
                job.failed_messages += 1
                logger.warning("Failed to restore UID %s from %s", uid, full_folder, exc_info=True)

            db.commit()


def _restore_single_message(src_conn, tgt_conn, uid, full_folder, short_folder, job, existing_ids, db):
    status, data = src_conn.fetch(uid, "(RFC822 FLAGS INTERNALDATE)")
    if status != "OK" or not data or not data[0]:
        job.failed_messages += 1
        return

    msg_data = data[0]
    if isinstance(msg_data, tuple) and len(msg_data) >= 2:
        raw_message = msg_data[1]
    else:
        job.failed_messages += 1
        return

    if job.skip_duplicates and existing_ids:
        import email

        try:
            parsed = email.message_from_bytes(raw_message)
            msg_id = parsed.get("Message-ID", "")
            if msg_id and msg_id in existing_ids:
                job.skipped_messages += 1
                return
        except Exception:
            pass

    flags_str = ""
    date_str = None
    meta = msg_data[0].decode() if isinstance(msg_data[0], bytes) else str(msg_data[0])
    if "FLAGS" in meta:
        import re

        flags_match = re.search(r"FLAGS \(([^)]*)\)", meta)
        if flags_match:
            flags_str = flags_match.group(1)
    if "INTERNALDATE" in meta:
        import re

        date_match = re.search(r'INTERNALDATE "([^"]+)"', meta)
        if date_match:
            import imaplib

            date_str = imaplib.Time2Internaldate(
                imaplib.Internaldate2tuple(
                    f'INTERNALDATE "{date_match.group(1)}"'.encode()
                )
            )

    target_folder = _map_folder(short_folder, job.folder_mapping)
    _ensure_folder(tgt_conn, target_folder)

    try:
        result, _ = _retry_imap(
            tgt_conn.append,
            f'"{target_folder}"',
            flags_str if flags_str else None,
            date_str,
            raw_message,
        )
        if result == "OK":
            job.restored_messages += 1
        else:
            job.failed_messages += 1
    except (imaplib.IMAP4.error, OSError):
        job.failed_messages += 1


def _map_folder(folder_name, folder_mapping):
    if folder_mapping == "original":
        return folder_name
    return f"{folder_mapping}/{folder_name}"


def _ensure_folder(conn, folder_name):
    try:
        status, _ = conn.select(f'"{folder_name}"')
        if status == "OK":
            conn.close()
            return
    except imaplib.IMAP4.error:
        pass
    try:
        conn.create(f'"{folder_name}"')
    except imaplib.IMAP4.error:
        pass


def _get_existing_message_ids(conn, folder_name):
    ids = set()
    try:
        status, _ = conn.select(f'"{folder_name}"', readonly=True)
        if status != "OK":
            return ids
        status, data = conn.search(None, "ALL")
        if status != "OK" or not data[0]:
            conn.close()
            return ids
        for uid in data[0].split():
            status, msg_data = conn.fetch(uid, "(BODY.PEEK[HEADER.FIELDS (MESSAGE-ID)])")
            if status == "OK" and msg_data and msg_data[0]:
                header = msg_data[0][1] if isinstance(msg_data[0], tuple) else msg_data[0]
                if isinstance(header, bytes):
                    header = header.decode(errors="replace")
                for line in header.splitlines():
                    if line.lower().startswith("message-id:"):
                        ids.add(line.split(":", 1)[1].strip())
        conn.close()
    except imaplib.IMAP4.error:
        pass
    return ids


def _refresh_target_token(creds_json, db, account):
    import asyncio
    import json

    try:
        token_data = json.loads(creds_json)
    except json.JSONDecodeError:
        return None
    refresh_token = token_data.get("refresh_token", "")
    if not refresh_token:
        return None
    provider = token_data.get("provider", "google")
    try:
        from mailfallback.services.oauth2 import refresh_google_token, refresh_microsoft_token

        refresh_fn = {"microsoft": refresh_microsoft_token}.get(provider, refresh_google_token)
        access_token = asyncio.run(refresh_fn(refresh_token))
        token_data["access_token"] = access_token
        from mailfallback.security import encrypt_credentials

        account.credentials = encrypt_credentials(json.dumps(token_data), settings.secret_key)
        db.commit()
        return access_token
    except Exception:
        logger.exception("Failed to refresh OAuth2 token for %s", account.name)
        return None


def _fail_job(db, job, error_msg):
    job.status = JobStatus.failed
    job.error = error_msg
    job.completed_at = datetime.now(UTC)
    db.commit()
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_restore_worker.py -v`
Expected: All pass.

Note: The worker references `settings.dovecot_imap_host` and `settings.dovecot_imap_port` which need to be added to `config.py`. Add these defaults:

```python
dovecot_imap_host: str = "dovecot"
dovecot_imap_port: int = 31143
```

- [ ] **Step 5: Run full test suite**

Run: `uv run pytest tests/ -q`
Expected: All pass.

- [ ] **Step 6: Commit**

```bash
git add src/mailfallback/services/restore_worker.py src/mailfallback/config.py tests/test_restore_worker.py
git commit -m "feat: add restore_worker with IMAP read/write execution"
```

---

## Task 6: REST API Endpoints (Browse + Restore Ops)

**Files:**
- Create: `src/mailfallback/routers/restore.py`
- Create: `tests/test_restore_api.py`
- Modify: `src/mailfallback/app.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_restore_api.py`:

```python
from unittest.mock import MagicMock, patch

import pytest

from mailfallback.models import Account, JobStatus, RestoreJob, RestoreMode, User, UserRole


@pytest.fixture
def restore_api_fixtures(db_session, default_store):
    user = User(
        username="apiuser",
        password_hash="x",
        store_id=default_store.id,
        role=UserRole.admin,
    )
    db_session.add(user)
    db_session.flush()

    src = Account(
        name="source",
        email_address="src@example.com",
        imap_host="imap.src.com",
        imap_port=993,
        maildir_path="/data/mailboxes/api-src",
        store_id=default_store.id,
        credentials="encrypted",
    )
    tgt = Account(
        name="target",
        email_address="tgt@example.com",
        imap_host="imap.tgt.com",
        imap_port=993,
        maildir_path="/data/mailboxes/api-tgt",
        store_id=default_store.id,
        credentials="encrypted",
    )
    db_session.add_all([src, tgt])
    db_session.commit()
    db_session.refresh(user)
    db_session.refresh(src)
    db_session.refresh(tgt)
    return {"user": user, "source": src, "target": tgt}


def _login(client, username="apiuser"):
    client.post("/api/auth/login", data={"username": username, "password": "x"})


@patch("mailfallback.services.restore_worker.submit_restore_job")
def test_create_restore_job_api(mock_submit, client, restore_api_fixtures):
    f = restore_api_fixtures
    _login(client, "apiuser")
    resp = client.post("/api/restore", json={
        "source_account_id": f["source"].id,
        "target_account_id": f["target"].id,
        "restore_mode": "full",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "pending"
    assert "job_id" in data
    mock_submit.assert_called_once()


def test_get_restore_job_api(client, db_session, restore_api_fixtures):
    f = restore_api_fixtures
    _login(client, "apiuser")
    job = RestoreJob(
        source_account_id=f["source"].id,
        target_account_id=f["target"].id,
        restore_mode=RestoreMode.full,
        requested_by=f["user"].id,
    )
    db_session.add(job)
    db_session.commit()

    resp = client.get(f"/api/restore/{job.id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "pending"
    assert data["total_messages"] == 0


def test_cancel_restore_job_api(client, db_session, restore_api_fixtures):
    f = restore_api_fixtures
    _login(client, "apiuser")
    job = RestoreJob(
        source_account_id=f["source"].id,
        target_account_id=f["target"].id,
        restore_mode=RestoreMode.full,
        requested_by=f["user"].id,
        status=JobStatus.running,
    )
    db_session.add(job)
    db_session.commit()

    resp = client.post(f"/api/restore/{job.id}/cancel")
    assert resp.status_code == 200
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_restore_api.py -v`
Expected: Fails — router not registered.

- [ ] **Step 3: Implement restore router**

Create `src/mailfallback/routers/restore.py`:

```python
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from mailfallback.dependencies import get_current_user, get_db
from mailfallback.models import User
from mailfallback.services.account_service import get_account
from mailfallback.services.audit_service import log_action
from mailfallback.services.restore_service import (
    cancel_restore_job,
    create_restore_job,
    get_restore_job,
    list_restore_jobs,
)
from mailfallback.services.restore_worker import request_cancel, submit_restore_job

router = APIRouter(prefix="/api/restore", tags=["restore"])


@router.post("")
def create_restore(
    body: dict,
    request=None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    source = get_account(db, body["source_account_id"], user)
    if not source:
        raise HTTPException(status_code=404, detail="Source account not found")
    target = get_account(db, body["target_account_id"], user)
    if not target:
        raise HTTPException(status_code=404, detail="Target account not found")

    job = create_restore_job(
        db,
        source_account_id=source.id,
        target_account_id=target.id,
        restore_mode=body.get("restore_mode", "full"),
        requested_by=user.id,
        folder_mapping=body.get("folder_mapping", "original"),
        skip_duplicates=body.get("skip_duplicates", True),
        selected_folders=body.get("selected_folders"),
        selected_uids=body.get("selected_uids"),
    )
    if not job:
        raise HTTPException(status_code=409, detail="Restore blocked or already running")

    submit_restore_job(job.id)

    log_action(
        db,
        user=user,
        action="restore.start",
        resource_type="restore",
        resource_id=job.id,
        resource_name=f"{source.name} → {target.name}",
        ip_address=request.client.host if request and request.client else None,
    )

    return {"job_id": job.id, "status": job.status.value}


@router.get("/{job_id}")
def get_restore(
    job_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    job = get_restore_job(db, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Restore job not found")

    return {
        "id": job.id,
        "source_account_id": job.source_account_id,
        "target_account_id": job.target_account_id,
        "status": job.status.value,
        "restore_mode": job.restore_mode.value,
        "total_messages": job.total_messages,
        "restored_messages": job.restored_messages,
        "skipped_messages": job.skipped_messages,
        "failed_messages": job.failed_messages,
        "error": job.error,
        "requested_at": job.requested_at.isoformat() if job.requested_at else None,
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "completed_at": job.completed_at.isoformat() if job.completed_at else None,
    }


@router.post("/{job_id}/cancel")
def cancel_restore(
    job_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    job = get_restore_job(db, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Restore job not found")
    request_cancel(job_id)
    result = cancel_restore_job(db, job_id)
    if not result:
        raise HTTPException(status_code=409, detail="Cannot cancel this job")
    return {"ok": True}
```

- [ ] **Step 4: Register the router in app.py**

In `src/mailfallback/app.py`, add the import and include:

```python
from mailfallback.routers import restore
app.include_router(restore.router)
```

Add it alongside the other router registrations.

- [ ] **Step 5: Run tests**

Run: `uv run pytest tests/test_restore_api.py -v`
Expected: All pass.

- [ ] **Step 6: Commit**

```bash
git add src/mailfallback/routers/restore.py src/mailfallback/app.py tests/test_restore_api.py
git commit -m "feat: add restore REST API endpoints"
```

---

## Task 7: Mailbox Browse & Search API

**Files:**
- Modify: `src/mailfallback/routers/restore.py`
- Create: `tests/test_restore_browse.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_restore_browse.py`:

```python
from unittest.mock import MagicMock, patch

import pytest

from mailfallback.models import Account, User, UserRole


@pytest.fixture
def browse_fixtures(db_session, default_store):
    user = User(username="browser", password_hash="x", store_id=default_store.id, role=UserRole.admin)
    db_session.add(user)
    db_session.flush()
    acct = Account(
        name="browseacct",
        email_address="browse@example.com",
        imap_host="imap.example.com",
        imap_port=993,
        maildir_path="/data/mailboxes/browse",
        store_id=default_store.id,
        credentials="enc",
    )
    db_session.add(acct)
    db_session.commit()
    db_session.refresh(user)
    db_session.refresh(acct)
    acct.owners.append(user)
    db_session.commit()
    return {"user": user, "account": acct}


def _login(client, username="browser"):
    client.post("/api/auth/login", data={"username": username, "password": "x"})


@patch("mailfallback.routers.restore.connect_imap")
def test_list_mailboxes(mock_connect, client, browse_fixtures):
    f = browse_fixtures
    _login(client)
    mock_conn = MagicMock()
    mock_connect.return_value = mock_conn
    prefix = f'browseacct (browse@example.com) [{f["account"].id[:8]}]'
    mock_conn.list.return_value = (
        "OK",
        [
            f'(\\HasNoChildren) "/" "{prefix}/INBOX"'.encode(),
            f'(\\HasNoChildren) "/" "{prefix}/Sent"'.encode(),
        ],
    )
    mock_conn.status.side_effect = [
        ("OK", [f'"{prefix}/INBOX" (MESSAGES 42)'.encode()]),
        ("OK", [f'"{prefix}/Sent" (MESSAGES 10)'.encode()]),
    ]

    resp = client.get(f"/api/accounts/{f['account'].id}/mailboxes")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2
    assert data[0]["name"] == "INBOX"
    assert data[0]["messages"] == 42
    mock_conn.logout.assert_called_once()


@patch("mailfallback.routers.restore.connect_imap")
def test_list_messages(mock_connect, client, browse_fixtures):
    f = browse_fixtures
    _login(client)
    mock_conn = MagicMock()
    mock_connect.return_value = mock_conn

    prefix = f'browseacct (browse@example.com) [{f["account"].id[:8]}]'
    mock_conn.select.return_value = ("OK", [b"2"])
    mock_conn.search.return_value = ("OK", [b"1 2"])
    mock_conn.fetch.return_value = (
        "OK",
        [
            (b"1 (ENVELOPE (\"Mon, 01 Jan 2024 00:00:00 +0000\" \"Subject1\" ((\"From\" NIL \"user\" \"example.com\")) NIL NIL NIL NIL NIL NIL \"<msg1@example.com>\") FLAGS (\\Seen))", b""),
            (b"2 (ENVELOPE (\"Tue, 02 Jan 2024 00:00:00 +0000\" \"Subject2\" ((\"From2\" NIL \"user2\" \"example.com\")) NIL NIL NIL NIL NIL NIL \"<msg2@example.com>\") FLAGS ())", b""),
        ],
    )

    resp = client.get(f"/api/accounts/{f['account'].id}/mailboxes/INBOX/messages")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) >= 1


@patch("mailfallback.routers.restore.connect_imap")
def test_search_messages(mock_connect, client, browse_fixtures):
    f = browse_fixtures
    _login(client)
    mock_conn = MagicMock()
    mock_connect.return_value = mock_conn

    prefix = f'browseacct (browse@example.com) [{f["account"].id[:8]}]'
    mock_conn.select.return_value = ("OK", [b"1"])
    mock_conn.search.return_value = ("OK", [b"1"])
    mock_conn.fetch.return_value = (
        "OK",
        [
            (b"1 (ENVELOPE (\"Mon, 01 Jan 2024 00:00:00 +0000\" \"Found It\" ((\"Sender\" NIL \"s\" \"example.com\")) NIL NIL NIL NIL NIL NIL \"<found@example.com>\") FLAGS (\\Seen))", b""),
        ],
    )

    resp = client.get(f"/api/accounts/{f['account'].id}/mailboxes/INBOX/search?q=test")
    assert resp.status_code == 200
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_restore_browse.py -v`
Expected: 404 — endpoints don't exist.

- [ ] **Step 3: Add browse endpoints to restore router**

Add these endpoints to `src/mailfallback/routers/restore.py`:

```python
import imaplib
import re
from urllib.parse import unquote

from mailfallback.services.imap_check import connect_imap


@router.get("/accounts/{account_id}/mailboxes", prefix="")
def list_mailboxes(
    account_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    account = get_account(db, account_id, user)
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")

    owner = account.owners[0] if account.owners else None
    if not owner:
        raise HTTPException(status_code=400, detail="Account has no owner")

    from mailfallback.config import settings
    conn = connect_imap(
        settings.dovecot_imap_host,
        settings.dovecot_imap_port,
        "NONE",
        owner.username,
        "internal-auth",
    )

    try:
        prefix = f'{account.name} ({account.email_address}) [{account.id[:8]}]/'
        status, folder_data = conn.list(f'"{prefix}"', "*")
        if status != "OK" or not folder_data:
            return []

        folders = []
        for item in folder_data:
            if not item:
                continue
            decoded = item.decode() if isinstance(item, bytes) else item
            parts = decoded.rsplit('" "', 1)
            if len(parts) < 2:
                continue
            full_name = parts[1].rstrip('"')
            short_name = full_name.removeprefix(prefix)

            messages = 0
            try:
                st, st_data = conn.status(f'"{full_name}"', "(MESSAGES)")
                if st == "OK" and st_data:
                    match = re.search(r"MESSAGES (\d+)", st_data[0].decode())
                    if match:
                        messages = int(match.group(1))
            except imaplib.IMAP4.error:
                pass

            folders.append({"name": short_name, "full_name": full_name, "messages": messages})
        return folders
    finally:
        conn.logout()
```

Note: The browse endpoints need to be registered on a **separate router with no prefix** since they use `/api/accounts/{id}/mailboxes` path (not `/api/restore`). Add a second router at module level:

```python
browse_router = APIRouter(prefix="/api", tags=["restore-browse"])
```

And use `@browse_router.get(...)` for the browse endpoints. Register both routers in `app.py`.

For messages and search endpoints, add:

```python
@browse_router.get("/accounts/{account_id}/mailboxes/{folder:path}/messages")
def list_messages(
    account_id: str,
    folder: str,
    page: int = 1,
    limit: int = 50,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    account = get_account(db, account_id, user)
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    owner = account.owners[0] if account.owners else None
    if not owner:
        raise HTTPException(status_code=400, detail="Account has no owner")

    from mailfallback.config import settings
    conn = connect_imap(
        settings.dovecot_imap_host, settings.dovecot_imap_port,
        "NONE", owner.username, "internal-auth",
    )

    try:
        folder = unquote(folder)
        prefix = f'{account.name} ({account.email_address}) [{account.id[:8]}]/'
        full_folder = f"{prefix}{folder}"
        status, _ = conn.select(f'"{full_folder}"', readonly=True)
        if status != "OK":
            return []

        status, data = conn.search(None, "ALL")
        if status != "OK" or not data[0]:
            return []

        uids = data[0].split()
        uids.reverse()
        start = (page - 1) * limit
        page_uids = uids[start:start + limit]
        if not page_uids:
            return []

        uid_str = b",".join(page_uids).decode()
        status, msg_data = conn.fetch(uid_str, "(ENVELOPE FLAGS)")
        if status != "OK":
            return []

        return _parse_envelope_list(msg_data)
    finally:
        conn.logout()


@browse_router.get("/accounts/{account_id}/mailboxes/{folder:path}/search")
def search_messages(
    account_id: str,
    folder: str,
    q: str = "",
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not q:
        raise HTTPException(status_code=400, detail="Query parameter 'q' is required")

    account = get_account(db, account_id, user)
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    owner = account.owners[0] if account.owners else None
    if not owner:
        raise HTTPException(status_code=400, detail="Account has no owner")

    from mailfallback.config import settings
    conn = connect_imap(
        settings.dovecot_imap_host, settings.dovecot_imap_port,
        "NONE", owner.username, "internal-auth",
    )

    try:
        folder = unquote(folder)
        prefix = f'{account.name} ({account.email_address}) [{account.id[:8]}]/'
        full_folder = f"{prefix}{folder}"
        status, _ = conn.select(f'"{full_folder}"', readonly=True)
        if status != "OK":
            return []

        status, data = conn.search(None, "TEXT", f'"{q}"')
        if status != "OK" or not data[0]:
            return []

        uids = data[0].split()[:100]
        uid_str = b",".join(uids).decode()
        status, msg_data = conn.fetch(uid_str, "(ENVELOPE FLAGS)")
        if status != "OK":
            return []

        return _parse_envelope_list(msg_data)
    finally:
        conn.logout()


def _parse_envelope_list(msg_data):
    results = []
    for item in msg_data:
        if not isinstance(item, tuple) or len(item) < 2:
            continue
        meta = item[0].decode() if isinstance(item[0], bytes) else str(item[0])
        uid_match = re.match(r"(\d+)", meta)
        uid = int(uid_match.group(1)) if uid_match else 0

        subject = ""
        subject_match = re.search(r'ENVELOPE \("[^"]*" "([^"]*)"', meta)
        if subject_match:
            subject = subject_match.group(1)

        seen = "\\Seen" in meta
        flagged = "\\Flagged" in meta

        results.append({
            "uid": uid,
            "subject": subject,
            "seen": seen,
            "flagged": flagged,
        })
    return results
```

- [ ] **Step 4: Register both routers in app.py**

```python
from mailfallback.routers.restore import browse_router, router as restore_router
app.include_router(restore_router)
app.include_router(browse_router)
```

- [ ] **Step 5: Run tests**

Run: `uv run pytest tests/test_restore_browse.py tests/test_restore_api.py -v`
Expected: All pass.

- [ ] **Step 6: Commit**

```bash
git add src/mailfallback/routers/restore.py src/mailfallback/app.py tests/test_restore_browse.py
git commit -m "feat: add mailbox browse and search API endpoints"
```

---

## Task 8: UI — Restore Page + Sidebar Link

**Files:**
- Create: `src/mailfallback/routers/ui_restore.py`
- Create: `src/mailfallback/templates/restore.html`
- Create: `src/mailfallback/templates/partials/restore_folders.html`
- Create: `src/mailfallback/templates/partials/restore_messages.html`
- Create: `src/mailfallback/templates/partials/restore_progress.html`
- Create: `src/mailfallback/templates/partials/restore_history.html`
- Modify: `src/mailfallback/templates/base.html`
- Modify: `src/mailfallback/app.py`
- Modify: `src/mailfallback/static/css/style.css`
- Create: `tests/test_restore_ui.py`

This is the largest task. Follow the pattern from existing UI routes (ui.py, ui_accounts.py).

- [ ] **Step 1: Add sidebar link to base.html**

After the Accounts link (line 34 in `base.html`), add:

```html
<a href="/restore" {% if request.url.path.startswith("/restore") %}class="active"{% endif %}><i data-lucide="archive-restore" class="icon-nav"></i>Restore</a>
```

- [ ] **Step 2: Write the UI route**

Create `src/mailfallback/routers/ui_restore.py`:

```python
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from mailfallback.dependencies import get_db
from mailfallback.routers.ui import _get_session_user
from mailfallback.services.account_service import get_accounts_for_user
from mailfallback.services.restore_service import list_restore_jobs_for_user

router = APIRouter(tags=["ui-restore"])
templates = Jinja2Templates(directory="src/mailfallback/templates")


@router.get("/restore", response_class=HTMLResponse)
def restore_page(request: Request, db: Session = Depends(get_db)):
    user = _get_session_user(request, db)
    if not user:
        return RedirectResponse("/login")
    accounts = get_accounts_for_user(db, user)
    jobs = list_restore_jobs_for_user(db, user.id)
    return templates.TemplateResponse(
        request=request,
        name="restore.html",
        context={"user": user, "accounts": accounts, "jobs": jobs},
    )
```

- [ ] **Step 3: Create the restore.html template**

Create `src/mailfallback/templates/restore.html`:

```html
{% extends "base.html" %}
{% block title %}Restore — MailFallBack{% endblock %}
{% block content %}
<h2><i data-lucide="archive-restore" class="icon-inline"></i> Mail Restore</h2>

<form id="restore-form">
    <div class="grid">
        <label>
            Source account
            <select name="source_account_id" id="source-account"
                    hx-get="/restore/partials/folders"
                    hx-target="#folder-panel"
                    hx-include="[name='source_account_id']"
                    hx-trigger="change">
                <option value="">Select source…</option>
                {% for acct in accounts %}
                <option value="{{ acct.id }}">{{ acct.name }} ({{ acct.email_address }})</option>
                {% endfor %}
            </select>
        </label>
        <label>
            Destination account
            <select name="target_account_id" id="target-account">
                <option value="">Select destination…</option>
                {% for acct in accounts %}
                <option value="{{ acct.id }}">{{ acct.name }} ({{ acct.email_address }})</option>
                {% endfor %}
            </select>
        </label>
    </div>

    <fieldset>
        <legend>Restore mode</legend>
        <label><input type="radio" name="restore_mode" value="full" checked> Full restore</label>
        <label><input type="radio" name="restore_mode" value="folder"> Select folders</label>
        <label><input type="radio" name="restore_mode" value="selection"> Search & pick</label>
    </fieldset>

    <div id="folder-panel"></div>

    <fieldset>
        <legend>Options</legend>
        <div class="grid">
            <label>
                Folder mapping
                <select name="folder_mapping">
                    <option value="original">Original folder</option>
                    <option value="Restored">Restored/ prefix</option>
                </select>
            </label>
            <label>
                <input type="checkbox" name="skip_duplicates" checked>
                Skip duplicate messages
            </label>
        </div>
    </fieldset>

    <button type="button" id="start-restore-btn"
            hx-post="/api/restore"
            hx-target="#restore-progress"
            hx-swap="innerHTML"
            hx-vals="js:{
                source_account_id: document.getElementById('source-account').value,
                target_account_id: document.getElementById('target-account').value,
                restore_mode: document.querySelector('[name=restore_mode]:checked').value,
                folder_mapping: document.querySelector('[name=folder_mapping]').value,
                skip_duplicates: document.querySelector('[name=skip_duplicates]').checked
            }">
        <i data-lucide="play" class="icon-sm icon-inline"></i> Start Restore
    </button>
</form>

<div id="restore-progress"></div>

<hr>
<h3>Restore History</h3>
{% include "partials/restore_history.html" %}
{% endblock %}
```

- [ ] **Step 4: Create partial templates**

Create `src/mailfallback/templates/partials/restore_folders.html`:

```html
<div id="folder-panel">
    {% if folders %}
    <table class="compact">
        <thead><tr><th></th><th>Folder</th><th>Messages</th></tr></thead>
        <tbody>
        {% for f in folders %}
        <tr>
            <td><input type="checkbox" name="selected_folders" value="{{ f.name }}" checked></td>
            <td>{{ f.name }}</td>
            <td>{{ f.messages }}</td>
        </tr>
        {% endfor %}
        </tbody>
    </table>
    {% else %}
    <p class="text-muted">No folders found.</p>
    {% endif %}
</div>
```

Create `src/mailfallback/templates/partials/restore_messages.html`:

```html
<div id="message-panel">
    {% if messages %}
    <table class="compact">
        <thead><tr><th></th><th>Subject</th><th>From</th><th>Date</th></tr></thead>
        <tbody>
        {% for msg in messages %}
        <tr>
            <td><input type="checkbox" name="selected_uids" value="{{ msg.uid }}"></td>
            <td>{{ msg.subject }}</td>
            <td>{{ msg.from }}</td>
            <td>{{ msg.date }}</td>
        </tr>
        {% endfor %}
        </tbody>
    </table>
    {% else %}
    <p class="text-muted">No messages found.</p>
    {% endif %}
</div>
```

Create `src/mailfallback/templates/partials/restore_progress.html`:

```html
<div id="restore-progress"
    {% if not finished %}
    hx-get="/restore/partials/progress?job_id={{ job_id }}"
    hx-trigger="every 2s"
    hx-swap="outerHTML"
    {% endif %}>
    {% if job %}
    <article>
        <header>
            <strong>
                {% if finished %}
                    {% if job.status.value == "completed" %}
                    <i data-lucide="check-circle" class="icon-sm icon-inline"></i> Restore completed
                    {% else %}
                    <i data-lucide="x-circle" class="icon-sm icon-inline"></i> Restore failed
                    {% endif %}
                {% else %}
                <i data-lucide="loader" class="icon-sm icon-inline spin"></i> Restoring…
                {% endif %}
            </strong>
        </header>
        <progress value="{{ job.restored_messages + job.skipped_messages + job.failed_messages }}" max="{{ job.total_messages }}"></progress>
        <small>
            {{ job.restored_messages }} restored
            {% if job.skipped_messages %} · {{ job.skipped_messages }} skipped{% endif %}
            {% if job.failed_messages %} · {{ job.failed_messages }} failed{% endif %}
            / {{ job.total_messages }} total
        </small>
        {% if job.error %}
        <p class="error-text">{{ job.error }}</p>
        {% endif %}
        {% if not finished %}
        <footer>
            <button type="button" class="outline secondary"
                    hx-post="/api/restore/{{ job.id }}/cancel"
                    hx-swap="none">Cancel</button>
        </footer>
        {% endif %}
    </article>
    {% endif %}
</div>
```

Create `src/mailfallback/templates/partials/restore_history.html`:

```html
<div id="restore-history">
    {% if jobs %}
    <table class="compact">
        <thead><tr><th>Date</th><th>Source → Destination</th><th>Status</th><th>Restored</th><th>Skipped</th><th>Failed</th></tr></thead>
        <tbody>
        {% for job in jobs %}
        <tr>
            <td>{{ job.requested_at|time_ago }}</td>
            <td>{{ job.source_account.name }} → {{ job.target_account.name }}</td>
            <td><span class="badge badge-{{ 'idle' if job.status.value == 'completed' else 'error' if job.status.value == 'failed' else 'syncing' }}">{{ job.status.value }}</span></td>
            <td>{{ job.restored_messages }}</td>
            <td>{{ job.skipped_messages }}</td>
            <td>{{ job.failed_messages }}</td>
        </tr>
        {% endfor %}
        </tbody>
    </table>
    {% else %}
    <p class="text-muted">No restore jobs yet.</p>
    {% endif %}
</div>
```

- [ ] **Step 5: Add HTMX partial routes to ui_restore.py**

Add to `ui_restore.py`:

```python
@router.get("/restore/partials/folders", response_class=HTMLResponse)
def restore_folders_partial(
    request: Request,
    source_account_id: str = "",
    db: Session = Depends(get_db),
):
    user = _get_session_user(request, db)
    if not user or not source_account_id:
        return HTMLResponse("")

    import httpx
    resp = httpx.get(
        f"http://localhost:8000/api/accounts/{source_account_id}/mailboxes",
        cookies={"session": request.cookies.get("session", "")},
    )
    folders = resp.json() if resp.status_code == 200 else []

    return templates.TemplateResponse(
        request=request,
        name="partials/restore_folders.html",
        context={"folders": folders},
    )


@router.get("/restore/partials/progress", response_class=HTMLResponse)
def restore_progress_partial(
    request: Request,
    job_id: str = "",
    db: Session = Depends(get_db),
):
    user = _get_session_user(request, db)
    if not user or not job_id:
        return HTMLResponse("")

    from mailfallback.services.restore_service import get_restore_job
    job = get_restore_job(db, job_id)
    finished = job and job.status.value in ("completed", "failed")

    return templates.TemplateResponse(
        request=request,
        name="partials/restore_progress.html",
        context={"job": job, "job_id": job_id, "finished": finished},
    )
```

- [ ] **Step 6: Register ui_restore router in app.py**

```python
from mailfallback.routers import ui_restore
app.include_router(ui_restore.router)
```

- [ ] **Step 7: Write basic UI tests**

Create `tests/test_restore_ui.py`:

```python
def test_restore_page_redirects_unauthenticated(client):
    resp = client.get("/restore", follow_redirects=False)
    assert resp.status_code == 307


def test_restore_page_renders(client, db_session, default_store):
    from mailfallback.models import User, UserRole

    user = User(username="uitest", password_hash="x", store_id=default_store.id, role=UserRole.admin)
    db_session.add(user)
    db_session.commit()

    client.post("/api/auth/login", data={"username": "uitest", "password": "x"})
    resp = client.get("/restore")
    assert resp.status_code == 200
    assert "Restore" in resp.text
```

- [ ] **Step 8: Run tests**

Run: `uv run pytest tests/test_restore_ui.py -v`
Expected: All pass.

- [ ] **Step 9: Commit**

```bash
git add src/mailfallback/routers/ui_restore.py src/mailfallback/templates/ src/mailfallback/app.py tests/test_restore_ui.py
git commit -m "feat: add restore UI page with sidebar link and HTMX partials"
```

---

## Task 9: CSS + Visual Polish

**Files:**
- Modify: `src/mailfallback/static/css/style.css`

- [ ] **Step 1: Add restore-specific styles**

Append to `style.css`:

```css
/* === Restore page === */
#restore-form fieldset {
    margin-bottom: 1rem;
}
#restore-progress article {
    margin-top: 1rem;
}
#restore-progress progress {
    width: 100%;
    margin: 0.5rem 0;
}
.error-text {
    color: var(--mfb-danger-color);
    margin-top: 0.5rem;
}
```

- [ ] **Step 2: Test in browser**

Run: `docker compose up -d --build`
Navigate to `http://localhost:8000/restore` and verify:
- Sidebar link appears with `archive-restore` icon
- Source/destination dropdowns populate
- Restore mode radio buttons work
- HTMX folder loading works on source change

- [ ] **Step 3: Commit**

```bash
git add src/mailfallback/static/css/style.css
git commit -m "style: add restore page CSS"
```

---

## Task 10: Config + Integration + Final Tests

**Files:**
- Modify: `src/mailfallback/config.py`
- Run: full test suite

- [ ] **Step 1: Add Dovecot IMAP settings to config.py**

Add to the Settings class in `config.py`:

```python
dovecot_imap_host: str = "dovecot"
dovecot_imap_port: int = 31143
```

- [ ] **Step 2: Run full test suite**

Run: `uv run pytest tests/ -v`
Expected: All pass (268+ tests).

- [ ] **Step 3: Run migration drift check**

Run: `uv run pytest tests/test_alembic_sync.py -v`
Expected: PASS — no drift between models and migrations.

- [ ] **Step 4: Run linter**

Run: `uv run ruff check src/ tests/`
Expected: Clean.

- [ ] **Step 5: Commit any remaining changes**

```bash
git add src/mailfallback/config.py
git commit -m "feat: add dovecot IMAP connection settings for restore"
```

- [ ] **Step 6: Build and test with Docker**

```bash
docker compose up -d --build
docker compose exec mailfallback uv run alembic upgrade head
```

Navigate to `http://localhost:8000/restore` and perform a test restore.

- [ ] **Step 7: Final commit — push to remote**

```bash
git push
```
