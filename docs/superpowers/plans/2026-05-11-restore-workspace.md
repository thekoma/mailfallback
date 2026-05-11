# Restore Workspace Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild `/restore` as a unified, story-driven workspace where the actual restore action is the primary citizen and snapshots are mounted on demand as ephemeral Dovecot namespaces with TTL-based auto-cleanup.

**Architecture:** Extend the existing `Recovery` model with a `kind` (ephemeral/persistent) + `last_accessed_at` + `ttl_minutes`. Introduce a small `mount_service` that wraps the existing `recovery_service.create_recovery` for ephemeral lifecycle. A new search endpoint mounts snapshots in the chosen time range and routes IMAP SEARCH across namespaces via Dovecot. A scheduler job sweeps idle ephemerals hourly. UI is a single workspace page that absorbs the move tool's role; legacy `/restore/move` stays available but unlinked.

**Tech Stack:** FastAPI + Jinja2 + HTMX, SQLAlchemy + Alembic + PostgreSQL, APScheduler, Dovecot 2.4 IMAP (`imaplib`), restic via subprocess (`restic_service`).

**Spec:** [`docs/superpowers/specs/2026-05-11-restore-workspace-design.md`](../specs/2026-05-11-restore-workspace-design.md)

---

## File Structure

**Created:**
- `src/mailfallback/services/mount_service.py` — ephemeral mount lifecycle (ensure/touch/cleanup/force_unmount)
- `alembic/versions/013_recovery_ephemeral_kind.py` — Recovery columns
- `src/mailfallback/templates/restore_workspace.html` — new unified workspace page
- `src/mailfallback/templates/partials/restore_search_results.html` — HTMX results fragment
- `src/mailfallback/templates/partials/restore_status_strip.html` — demoted health/calendar
- `src/mailfallback/static/js/restore_workspace.js` — client interactions
- `tests/test_mount_service.py` — mount lifecycle tests
- `tests/test_restore_workspace_router.py` — workspace + search endpoint tests

**Modified:**
- `src/mailfallback/config.py` — three new env vars
- `src/mailfallback/models.py` — Recovery `kind`, `last_accessed_at`, `ttl_minutes` + `RecoveryKind` enum
- `src/mailfallback/services/recovery_service.py` — `create_recovery` defaults to `kind=persistent`
- `src/mailfallback/services/scheduler.py` — register hourly cleanup job
- `src/mailfallback/routers/ui_restore.py` — replace `/restore` view with workspace, demote calendar
- `src/mailfallback/routers/restore.py` — extract reusable IMAP search helper, expose new workspace search endpoint
- `src/mailfallback/templates/restore.html` — keep file but rewrite as workspace shell
- `src/mailfallback/static/css/style.css` — workspace layout, preset chips, provenance badges

---

## Phase 0 — Foundation: settings, model, migration

### Task 1: Add the three recovery env vars

**Files:**
- Modify: `src/mailfallback/config.py`
- Test: `tests/test_config.py`

- [ ] **Step 1: Read the current Settings class to find the right insertion point**

```bash
grep -n "sync_max_workers\|tika_enabled" src/mailfallback/config.py
```

- [ ] **Step 2: Write the failing test**

Append to `tests/test_config.py`:

```python
def test_recovery_settings_defaults(monkeypatch):
    monkeypatch.delenv("MAILFALLBACK_RECOVERY_EPHEMERAL_TTL_MINUTES", raising=False)
    monkeypatch.delenv("MAILFALLBACK_RECOVERY_MAX_PARALLEL_MOUNTS", raising=False)
    monkeypatch.delenv("MAILFALLBACK_RECOVERY_BACKEND", raising=False)
    from mailfallback.config import Settings
    s = Settings()
    assert s.recovery_ephemeral_ttl_minutes == 30
    assert s.recovery_max_parallel_mounts == 5
    assert s.recovery_backend == "restore"


def test_recovery_settings_env_override(monkeypatch):
    monkeypatch.setenv("MAILFALLBACK_RECOVERY_EPHEMERAL_TTL_MINUTES", "60")
    monkeypatch.setenv("MAILFALLBACK_RECOVERY_MAX_PARALLEL_MOUNTS", "10")
    monkeypatch.setenv("MAILFALLBACK_RECOVERY_BACKEND", "fuse")
    from mailfallback.config import Settings
    s = Settings()
    assert s.recovery_ephemeral_ttl_minutes == 60
    assert s.recovery_max_parallel_mounts == 10
    assert s.recovery_backend == "fuse"
```

- [ ] **Step 3: Run tests to verify they fail**

```bash
uv run pytest tests/test_config.py::test_recovery_settings_defaults tests/test_config.py::test_recovery_settings_env_override -v
```

Expected: FAIL with `AttributeError: ... has no attribute 'recovery_ephemeral_ttl_minutes'`

- [ ] **Step 4: Add the fields to `Settings`**

In `src/mailfallback/config.py`, add after `sync_max_workers`:

```python
    recovery_ephemeral_ttl_minutes: int = 30
    recovery_max_parallel_mounts: int = 5
    recovery_backend: str = "restore"  # "restore" (default) | "fuse" (future)
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
uv run pytest tests/test_config.py -v
```

Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/mailfallback/config.py tests/test_config.py
git commit -m "feat(config): add recovery workspace tunables (TTL, max mounts, backend)"
```

---

### Task 2: Extend Recovery model with `kind`, `last_accessed_at`, `ttl_minutes`

**Files:**
- Modify: `src/mailfallback/models.py:89,385-440`
- Test: `tests/test_models.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_models.py`:

```python
def test_recovery_defaults_to_persistent(db_session, default_store):
    from mailfallback.models import Account, Recovery, RecoveryKind, RecoveryStatus, Repository

    repo = Repository(name="test", backend_type="local", path="/tmp/test")
    db_session.add(repo)
    acct = Account(name="a", store=default_store, maildir_path="/data/mailboxes/a")
    db_session.add(acct)
    db_session.commit()

    r = Recovery(
        account_id=acct.id,
        repository_id=repo.id,
        snapshot_id="abc123",
        restore_path="/tmp/r",
        status=RecoveryStatus.ready,
    )
    db_session.add(r)
    db_session.commit()
    db_session.refresh(r)

    assert r.kind == RecoveryKind.persistent
    assert r.ttl_minutes is None
    assert r.last_accessed_at is not None


def test_recovery_can_be_ephemeral_with_ttl(db_session, default_store):
    from mailfallback.models import Account, Recovery, RecoveryKind, RecoveryStatus, Repository

    repo = Repository(name="test", backend_type="local", path="/tmp/test")
    db_session.add(repo)
    acct = Account(name="a", store=default_store, maildir_path="/data/mailboxes/a")
    db_session.add(acct)
    db_session.commit()

    r = Recovery(
        account_id=acct.id,
        repository_id=repo.id,
        snapshot_id="abc",
        restore_path="/tmp/r",
        status=RecoveryStatus.ready,
        kind=RecoveryKind.ephemeral,
        ttl_minutes=30,
    )
    db_session.add(r)
    db_session.commit()
    db_session.refresh(r)

    assert r.kind == RecoveryKind.ephemeral
    assert r.ttl_minutes == 30
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_models.py::test_recovery_defaults_to_persistent tests/test_models.py::test_recovery_can_be_ephemeral_with_ttl -v
```

Expected: FAIL with `ImportError: cannot import name 'RecoveryKind'`

- [ ] **Step 3: Add `RecoveryKind` enum to `models.py`**

In `src/mailfallback/models.py`, add directly after `RecoveryStatus` (around line 96):

```python
class RecoveryKind(enum.StrEnum):
    persistent = "persistent"
    ephemeral = "ephemeral"
```

- [ ] **Step 4: Add the three columns to `Recovery`**

In `src/mailfallback/models.py`, inside the `Recovery` class (around line 414), add after `size_bytes`:

```python
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
        server_default=sa.text("now()"),
    )
    ttl_minutes = Column(Integer, nullable=True)  # NULL = no TTL
```

(If `sa` is not already imported, use `from sqlalchemy import text` at the top — check the existing imports first with `grep -n "^from sqlalchemy" src/mailfallback/models.py`.)

- [ ] **Step 5: Run tests to verify they pass**

```bash
uv run pytest tests/test_models.py -v
```

Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/mailfallback/models.py tests/test_models.py
git commit -m "feat(model): Recovery gains kind (persistent/ephemeral), last_accessed_at, ttl_minutes"
```

---

### Task 3: Alembic migration 013 for the new Recovery columns

**Files:**
- Create: `alembic/versions/013_recovery_ephemeral_kind.py`
- Test: `tests/test_alembic_sync.py` (existing test will exercise the migration)

- [ ] **Step 1: Generate the migration scaffold**

```bash
uv run alembic revision -m "recovery ephemeral kind"
```

Find the newly created file and rename it to match the project convention (numbered prefix `013_`):

```bash
ls -t alembic/versions/*.py | head -2
mv alembic/versions/<generated-id>_recovery_ephemeral_kind.py alembic/versions/013_recovery_ephemeral_kind.py
```

- [ ] **Step 2: Write the upgrade/downgrade**

Replace the entire content of `alembic/versions/013_recovery_ephemeral_kind.py` with:

```python
"""recovery ephemeral kind

Adds three columns to the recoveries table:
- kind: persistent (today's behaviour) | ephemeral (TTL-driven, auto-cleanup)
- last_accessed_at: bumped on each access; ephemerals past TTL get swept
- ttl_minutes: NULL means no TTL (persistent never expires)

Existing rows backfill to kind=persistent, ttl_minutes=NULL.

Revision ID: 013
Revises: 012
Create Date: 2026-05-11
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "013"
down_revision: str | Sequence[str] | None = "012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    recovery_kind = sa.Enum("persistent", "ephemeral", name="recoverykind")
    recovery_kind.create(op.get_bind(), checkfirst=True)
    op.add_column(
        "recoveries",
        sa.Column("kind", recovery_kind, nullable=False, server_default="persistent"),
    )
    op.add_column(
        "recoveries",
        sa.Column(
            "last_accessed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.add_column("recoveries", sa.Column("ttl_minutes", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("recoveries", "ttl_minutes")
    op.drop_column("recoveries", "last_accessed_at")
    op.drop_column("recoveries", "kind")
    sa.Enum(name="recoverykind").drop(op.get_bind(), checkfirst=False)
```

- [ ] **Step 3: Run alembic-sync test to verify the model and migration agree**

```bash
uv run pytest tests/test_alembic_sync.py -v
```

Expected: PASS (the test compares model metadata against migrations)

- [ ] **Step 4: Verify upgrade against a real PostgreSQL via Docker**

```bash
docker compose up -d db
uv run alembic upgrade head
docker compose exec db psql -U mailfallback -d mailfallback -c "\d recoveries"
```

Expected: the three new columns visible in the table description.

- [ ] **Step 5: Commit**

```bash
git add alembic/versions/013_recovery_ephemeral_kind.py
git commit -m "feat(migration): 013 Recovery ephemeral kind + TTL columns"
```

---

### Task 4: Make `recovery_service.create_recovery` accept `kind`

**Files:**
- Modify: `src/mailfallback/services/recovery_service.py`
- Test: `tests/test_recovery_service.py` (create if missing)

- [ ] **Step 1: Check whether the test file exists**

```bash
ls tests/test_recovery_service.py 2>/dev/null || echo "missing"
```

If missing, create with the standard imports:

```python
"""Tests for recovery_service."""
from unittest.mock import patch

import pytest

from mailfallback.models import (
    Account,
    BackupPolicy,
    Recovery,
    RecoveryKind,
    Repository,
)
from mailfallback.services import recovery_service
```

- [ ] **Step 2: Write the failing test**

Append to `tests/test_recovery_service.py`:

```python
@patch("mailfallback.services.recovery_service.restic_service")
def test_create_recovery_defaults_to_persistent(mock_restic, db_session, default_store):
    repo = Repository(name="r", backend_type="local", path="/tmp/r")
    db_session.add(repo)
    acct = Account(name="a", store=default_store, maildir_path="/data/mailboxes/a")
    db_session.add(acct)
    db_session.flush()
    db_session.add(BackupPolicy(account_id=acct.id, destination_id=repo.id))
    db_session.commit()

    mock_restic.restore_snapshot.return_value = None
    mock_restic.list_snapshots.return_value = []

    rec = recovery_service.create_recovery(db_session, acct.id, "snap-1")
    assert rec.kind == RecoveryKind.persistent
    assert rec.ttl_minutes is None


@patch("mailfallback.services.recovery_service.restic_service")
def test_create_recovery_can_be_ephemeral(mock_restic, db_session, default_store):
    repo = Repository(name="r", backend_type="local", path="/tmp/r")
    db_session.add(repo)
    acct = Account(name="a", store=default_store, maildir_path="/data/mailboxes/a")
    db_session.add(acct)
    db_session.flush()
    db_session.add(BackupPolicy(account_id=acct.id, destination_id=repo.id))
    db_session.commit()

    mock_restic.restore_snapshot.return_value = None
    mock_restic.list_snapshots.return_value = []

    rec = recovery_service.create_recovery(
        db_session, acct.id, "snap-1", kind=RecoveryKind.ephemeral, ttl_minutes=15
    )
    assert rec.kind == RecoveryKind.ephemeral
    assert rec.ttl_minutes == 15
```

- [ ] **Step 3: Run tests to verify they fail**

```bash
uv run pytest tests/test_recovery_service.py -v
```

Expected: FAIL — `create_recovery` doesn't accept `kind`/`ttl_minutes` kwargs.

- [ ] **Step 4: Update the signature in `recovery_service.py`**

In `src/mailfallback/services/recovery_service.py`, change the function signature from:

```python
def create_recovery(db: Session, account_id: str, snapshot_id: str) -> Recovery:
```

to:

```python
def create_recovery(
    db: Session,
    account_id: str,
    snapshot_id: str,
    *,
    kind: RecoveryKind = RecoveryKind.persistent,
    ttl_minutes: int | None = None,
) -> Recovery:
```

Add the import at the top: `from mailfallback.models import ..., RecoveryKind`.

Then in the `Recovery(...)` instantiation inside the function body, add the two fields:

```python
    recovery = Recovery(
        account_id=account.id,
        repository_id=backup.destination_id,
        snapshot_id=snapshot_id,
        restore_path=restore_root,
        status=RecoveryStatus.restoring,
        kind=kind,
        ttl_minutes=ttl_minutes,
    )
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
uv run pytest tests/test_recovery_service.py -v
```

Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/mailfallback/services/recovery_service.py tests/test_recovery_service.py
git commit -m "feat(recovery): create_recovery accepts kind + ttl_minutes (default persistent)"
```

---

## Phase 1 — Mount Manager

### Task 5: `mount_service.ensure_mounted` (idempotent)

**Files:**
- Create: `src/mailfallback/services/mount_service.py`
- Create: `tests/test_mount_service.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_mount_service.py`:

```python
"""Tests for mount_service — ephemeral Recovery lifecycle."""
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest

from mailfallback.config import settings
from mailfallback.models import (
    Account,
    BackupPolicy,
    Recovery,
    RecoveryKind,
    RecoveryStatus,
    Repository,
)
from mailfallback.services import mount_service


@pytest.fixture
def account_with_backup(db_session, default_store):
    repo = Repository(name="r", backend_type="local", path="/tmp/r")
    db_session.add(repo)
    acct = Account(name="a", store=default_store, maildir_path="/data/mailboxes/a")
    db_session.add(acct)
    db_session.flush()
    db_session.add(BackupPolicy(account_id=acct.id, destination_id=repo.id))
    db_session.commit()
    return acct


@patch("mailfallback.services.mount_service.recovery_service")
def test_ensure_mounted_creates_ephemeral(mock_recovery, db_session, account_with_backup):
    fake = Recovery(
        account_id=account_with_backup.id,
        repository_id="repo-id",
        snapshot_id="snap-1",
        restore_path="/tmp/recovered",
        status=RecoveryStatus.ready,
        kind=RecoveryKind.ephemeral,
        ttl_minutes=settings.recovery_ephemeral_ttl_minutes,
    )
    mock_recovery.create_recovery.return_value = fake

    rec = mount_service.ensure_mounted(db_session, account_with_backup.id, "snap-1")

    assert rec.kind == RecoveryKind.ephemeral
    mock_recovery.create_recovery.assert_called_once_with(
        db_session,
        account_with_backup.id,
        "snap-1",
        kind=RecoveryKind.ephemeral,
        ttl_minutes=settings.recovery_ephemeral_ttl_minutes,
    )


@patch("mailfallback.services.mount_service.recovery_service")
def test_ensure_mounted_returns_existing_and_bumps_last_accessed(
    mock_recovery, db_session, account_with_backup
):
    old = datetime.now(UTC) - timedelta(minutes=20)
    existing = Recovery(
        account_id=account_with_backup.id,
        snapshot_id="snap-1",
        restore_path="/tmp/r",
        status=RecoveryStatus.ready,
        kind=RecoveryKind.ephemeral,
        ttl_minutes=30,
        last_accessed_at=old,
    )
    db_session.add(existing)
    db_session.commit()

    rec = mount_service.ensure_mounted(db_session, account_with_backup.id, "snap-1")

    assert rec.id == existing.id
    assert rec.last_accessed_at > old
    mock_recovery.create_recovery.assert_not_called()
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_mount_service.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'mailfallback.services.mount_service'`

- [ ] **Step 3: Create the service**

Create `src/mailfallback/services/mount_service.py`:

```python
"""Mount service — ephemeral Recovery lifecycle.

Wraps recovery_service.create_recovery for the workspace flow:
- ensure_mounted is idempotent: returns the existing Recovery if there's
  already one for (account_id, snapshot_id) with status=ready, otherwise
  creates an ephemeral one.
- touch_mount bumps last_accessed_at to defer cleanup.
- cleanup_idle_mounts removes ephemerals whose last_accessed_at is older
  than ttl_minutes.
- force_unmount removes a Recovery row immediately.

The persistent path is unchanged — call recovery_service.create_recovery
directly (or ensure_mounted with kind=persistent).
"""

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from mailfallback.config import settings
from mailfallback.models import Recovery, RecoveryKind, RecoveryStatus
from mailfallback.services import recovery_service

logger = logging.getLogger(__name__)


def ensure_mounted(
    db: Session,
    account_id: str,
    snapshot_id: str,
    *,
    kind: RecoveryKind = RecoveryKind.ephemeral,
    ttl_minutes: int | None = None,
) -> Recovery:
    """Idempotent mount. Returns existing Recovery (bumping last_accessed_at)
    or creates a new one via recovery_service.create_recovery.
    """
    existing = (
        db.query(Recovery)
        .filter(
            Recovery.account_id == account_id,
            Recovery.snapshot_id == snapshot_id,
            Recovery.status == RecoveryStatus.ready,
        )
        .first()
    )
    if existing:
        existing.last_accessed_at = datetime.now(UTC)
        db.commit()
        db.refresh(existing)
        return existing

    if ttl_minutes is None and kind == RecoveryKind.ephemeral:
        ttl_minutes = settings.recovery_ephemeral_ttl_minutes

    return recovery_service.create_recovery(
        db, account_id, snapshot_id, kind=kind, ttl_minutes=ttl_minutes
    )
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/test_mount_service.py -v
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/mailfallback/services/mount_service.py tests/test_mount_service.py
git commit -m "feat(mount): ensure_mounted — idempotent ephemeral Recovery creation"
```

---

### Task 6: `mount_service.touch_mount` and `force_unmount`

**Files:**
- Modify: `src/mailfallback/services/mount_service.py`
- Modify: `tests/test_mount_service.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_mount_service.py`:

```python
def test_touch_mount_updates_last_accessed_at(db_session, account_with_backup):
    old = datetime.now(UTC) - timedelta(minutes=20)
    rec = Recovery(
        account_id=account_with_backup.id,
        snapshot_id="snap-1",
        restore_path="/tmp/r",
        status=RecoveryStatus.ready,
        kind=RecoveryKind.ephemeral,
        ttl_minutes=30,
        last_accessed_at=old,
    )
    db_session.add(rec)
    db_session.commit()

    mount_service.touch_mount(db_session, rec.id)

    db_session.refresh(rec)
    assert rec.last_accessed_at > old


@patch("mailfallback.services.mount_service.recovery_service")
def test_force_unmount_delegates_to_delete_recovery(
    mock_recovery, db_session, account_with_backup
):
    rec = Recovery(
        account_id=account_with_backup.id,
        snapshot_id="snap-1",
        restore_path="/tmp/r",
        status=RecoveryStatus.ready,
        kind=RecoveryKind.ephemeral,
    )
    db_session.add(rec)
    db_session.commit()

    mount_service.force_unmount(db_session, rec.id)

    mock_recovery.delete_recovery.assert_called_once_with(db_session, rec.id)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_mount_service.py::test_touch_mount_updates_last_accessed_at tests/test_mount_service.py::test_force_unmount_delegates_to_delete_recovery -v
```

Expected: FAIL — `touch_mount` and `force_unmount` not defined.

- [ ] **Step 3: Add the functions to `mount_service.py`**

Append to `src/mailfallback/services/mount_service.py`:

```python
def touch_mount(db: Session, recovery_id: str) -> None:
    """Bump last_accessed_at — defers cleanup."""
    rec = db.query(Recovery).filter(Recovery.id == recovery_id).first()
    if rec is None:
        return
    rec.last_accessed_at = datetime.now(UTC)
    db.commit()


def force_unmount(db: Session, recovery_id: str) -> None:
    """Remove the Recovery (DB row + on-disk tree). Delegates to recovery_service.

    Idempotent: succeeds silently if the Recovery is already gone.
    """
    recovery_service.delete_recovery(db, recovery_id)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/test_mount_service.py -v
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/mailfallback/services/mount_service.py tests/test_mount_service.py
git commit -m "feat(mount): touch_mount + force_unmount"
```

---

### Task 7: `mount_service.cleanup_idle_mounts`

**Files:**
- Modify: `src/mailfallback/services/mount_service.py`
- Modify: `tests/test_mount_service.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_mount_service.py`:

```python
@patch("mailfallback.services.mount_service.recovery_service")
def test_cleanup_idle_mounts_removes_expired_ephemeral(
    mock_recovery, db_session, account_with_backup
):
    expired = Recovery(
        account_id=account_with_backup.id,
        snapshot_id="old",
        restore_path="/tmp/r",
        status=RecoveryStatus.ready,
        kind=RecoveryKind.ephemeral,
        ttl_minutes=30,
        last_accessed_at=datetime.now(UTC) - timedelta(minutes=45),
    )
    db_session.add(expired)
    db_session.commit()

    removed = mount_service.cleanup_idle_mounts(db_session)

    assert removed == 1
    mock_recovery.delete_recovery.assert_called_once_with(db_session, expired.id)


@patch("mailfallback.services.mount_service.recovery_service")
def test_cleanup_idle_mounts_keeps_recent_ephemeral(
    mock_recovery, db_session, account_with_backup
):
    fresh = Recovery(
        account_id=account_with_backup.id,
        snapshot_id="fresh",
        restore_path="/tmp/r",
        status=RecoveryStatus.ready,
        kind=RecoveryKind.ephemeral,
        ttl_minutes=30,
        last_accessed_at=datetime.now(UTC) - timedelta(minutes=5),
    )
    db_session.add(fresh)
    db_session.commit()

    removed = mount_service.cleanup_idle_mounts(db_session)

    assert removed == 0
    mock_recovery.delete_recovery.assert_not_called()


@patch("mailfallback.services.mount_service.recovery_service")
def test_cleanup_idle_mounts_keeps_persistent_even_if_old(
    mock_recovery, db_session, account_with_backup
):
    persistent = Recovery(
        account_id=account_with_backup.id,
        snapshot_id="forever",
        restore_path="/tmp/r",
        status=RecoveryStatus.ready,
        kind=RecoveryKind.persistent,
        ttl_minutes=None,
        last_accessed_at=datetime.now(UTC) - timedelta(days=30),
    )
    db_session.add(persistent)
    db_session.commit()

    removed = mount_service.cleanup_idle_mounts(db_session)

    assert removed == 0
    mock_recovery.delete_recovery.assert_not_called()
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_mount_service.py -v
```

Expected: FAIL — `cleanup_idle_mounts` not defined.

- [ ] **Step 3: Add the function**

Append to `src/mailfallback/services/mount_service.py`:

```python
def cleanup_idle_mounts(db: Session) -> int:
    """Remove ephemeral Recoveries whose last_accessed_at is older than ttl.

    Returns the number of recoveries removed.
    """
    now = datetime.now(UTC)
    candidates = (
        db.query(Recovery)
        .filter(
            Recovery.kind == RecoveryKind.ephemeral,
            Recovery.ttl_minutes.is_not(None),
        )
        .all()
    )
    removed = 0
    for rec in candidates:
        cutoff = rec.last_accessed_at + timedelta(minutes=rec.ttl_minutes)
        if cutoff < now:
            recovery_service.delete_recovery(db, rec.id)
            removed += 1
    if removed:
        logger.info("cleanup_idle_mounts: removed %d ephemeral recoveries", removed)
    return removed
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/test_mount_service.py -v
```

Expected: PASS — all six mount_service tests green.

- [ ] **Step 5: Commit**

```bash
git add src/mailfallback/services/mount_service.py tests/test_mount_service.py
git commit -m "feat(mount): cleanup_idle_mounts — sweeps expired ephemerals"
```

---

### Task 8: Wire scheduler to run `cleanup_idle_mounts` hourly

**Files:**
- Modify: `src/mailfallback/services/scheduler.py`

- [ ] **Step 1: Add the cleanup runner and registration**

In `src/mailfallback/services/scheduler.py`, add after `_run_scheduled_sync` (around line 39):

```python
def _run_mount_cleanup() -> None:
    db = SessionLocal()
    try:
        from mailfallback.services import mount_service
        mount_service.cleanup_idle_mounts(db)
    except Exception:
        logger.exception("mount cleanup failed")
    finally:
        db.close()
```

Then in `start_scheduler`, after `backup_scheduler_jobs(db)` and before `if not scheduler.running`, register the job:

```python
    if not any(j.id == "mount-cleanup" for j in scheduler.get_jobs()):
        scheduler.add_job(
            _run_mount_cleanup,
            CronTrigger(minute=0),  # every hour at :00
            id="mount-cleanup",
            replace_existing=True,
        )
```

- [ ] **Step 2: Verify the scheduler still passes its existing tests**

```bash
uv run pytest tests/test_scheduler.py -v 2>/dev/null || echo "no scheduler tests file — skip"
```

If a `test_scheduler.py` file doesn't exist, that's fine — the integration tests that boot the app will still cover registration.

- [ ] **Step 3: Smoke-test scheduler boot**

```bash
uv run python -c "
from mailfallback.db import SessionLocal, Base, engine
Base.metadata.create_all(engine)
from mailfallback.services.scheduler import start_scheduler, scheduler
db = SessionLocal()
start_scheduler(db)
assert any(j.id == 'mount-cleanup' for j in scheduler.get_jobs()), 'mount-cleanup job missing'
print('OK')
scheduler.shutdown(wait=False)
"
```

Expected: `OK` printed, no exception.

- [ ] **Step 4: Commit**

```bash
git add src/mailfallback/services/scheduler.py
git commit -m "feat(scheduler): hourly mount-cleanup job"
```

---

## Phase 2 — Search Backend

### Task 9: Extract reusable IMAP search helper

**Context:** `restore.py` already has `_build_search_criteria` (line 520) and `search_messages` (line 351). They're tightly coupled to a single account. We need a thin wrapper that takes an already-connected `imaplib` connection + a Dovecot namespace prefix and returns hits — so the workspace endpoint can call it once per mounted snapshot namespace.

**Files:**
- Modify: `src/mailfallback/routers/restore.py`
- Test: `tests/test_restore_workspace_router.py`

- [ ] **Step 1: Create the test file with a unit test for the helper**

Create `tests/test_restore_workspace_router.py`:

```python
"""Tests for the restore workspace router (search across namespaces)."""
from unittest.mock import MagicMock

from mailfallback.routers.restore import _search_namespace_for_query


def test_search_namespace_returns_envelopes():
    conn = MagicMock()
    # SELECT
    conn.select.return_value = ("OK", [b"3"])
    # SEARCH returns three UIDs
    conn.uid.side_effect = [
        ("OK", [b"1 2 3"]),
        # FETCH for each UID — returns minimal envelope tuple
        ("OK", [(b"1 (UID 1 ENVELOPE (...))", b'Subject: hi\r\nFrom: a@b\r\n\r\n')]),
        ("OK", [(b"2 (UID 2 ENVELOPE (...))", b'Subject: hello\r\nFrom: c@d\r\n\r\n')]),
        ("OK", [(b"3 (UID 3 ENVELOPE (...))", b'Subject: bye\r\nFrom: e@f\r\n\r\n')]),
    ]

    hits = _search_namespace_for_query(conn, namespace="snap-abc/", query="hi")

    assert len(hits) == 3
    assert all(h["namespace"] == "snap-abc/" for h in hits)
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_restore_workspace_router.py::test_search_namespace_returns_envelopes -v
```

Expected: FAIL — `ImportError: cannot import name '_search_namespace_for_query'`

- [ ] **Step 3: Add the helper to `restore.py`**

In `src/mailfallback/routers/restore.py`, append at module level (after `_fetch_message_header`):

```python
def _search_namespace_for_query(conn, namespace: str, query: str, folder: str = "INBOX") -> list[dict]:
    """Run a Dovecot SEARCH on `namespace + folder` for `query` (subject only).

    Returns a list of dicts with: uid, subject, from, namespace, folder, message_id.
    Caller is responsible for the connection lifecycle.
    """
    target = f"{namespace}{folder}" if namespace else folder
    typ, _ = conn.select(f'"{target}"', readonly=True)
    if typ != "OK":
        return []
    quoted = _sanitize_imap_string(query)
    typ, data = conn.uid("SEARCH", "SUBJECT", quoted)
    if typ != "OK" or not data or not data[0]:
        return []
    uids = data[0].decode().split()
    hits: list[dict] = []
    for uid in uids:
        env = _fetch_message_header(conn, uid, folder_name=target)
        if env:
            env["namespace"] = namespace
            env["folder"] = folder
            hits.append(env)
    return hits
```

- [ ] **Step 4: Run test to verify it passes**

```bash
uv run pytest tests/test_restore_workspace_router.py -v
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/mailfallback/routers/restore.py tests/test_restore_workspace_router.py
git commit -m "feat(restore): extract _search_namespace_for_query helper"
```

---

### Task 10: Workspace search endpoint with mount + dedup

**Files:**
- Modify: `src/mailfallback/routers/restore.py`
- Modify: `tests/test_restore_workspace_router.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_restore_workspace_router.py`:

```python
from unittest.mock import patch
from datetime import UTC, datetime, timedelta

from mailfallback.models import Account, BackupPolicy, Recovery, RecoveryKind, RecoveryStatus, Repository


@patch("mailfallback.routers.restore.mount_service")
@patch("mailfallback.routers.restore._connect_dovecot_for_account")
@patch("mailfallback.routers.restore.restic_service")
def test_workspace_search_dedup_by_message_id(
    mock_restic, mock_connect, mock_mount, client, db_session, default_store, login_user
):
    repo = Repository(name="r", backend_type="local", path="/tmp/r")
    db_session.add(repo)
    acct = Account(name="a", store=default_store, maildir_path="/data/mailboxes/a")
    db_session.add(acct)
    db_session.flush()
    acct.owners.append(login_user)
    db_session.add(BackupPolicy(account_id=acct.id, destination_id=repo.id))
    db_session.commit()

    mock_restic.list_snapshots.return_value = [
        {"short_id": "snap1", "time": (datetime.now(UTC) - timedelta(days=2)).isoformat()},
    ]

    fake_recovery = Recovery(
        account_id=acct.id,
        snapshot_id="snap1",
        restore_path="/tmp/r",
        status=RecoveryStatus.ready,
        kind=RecoveryKind.ephemeral,
    )
    db_session.add(fake_recovery)
    db_session.commit()
    mock_mount.ensure_mounted.return_value = fake_recovery

    fake_conn = MagicMock()
    fake_conn.select.return_value = ("OK", [b"1"])
    fake_conn.uid.side_effect = [
        # live SEARCH
        ("OK", [b"10"]),
        ("OK", [(b"...", b"Subject: x\r\nMessage-Id: <abc@host>\r\n\r\n")]),
        # snap SEARCH — same Message-Id
        ("OK", [b"99"]),
        ("OK", [(b"...", b"Subject: x\r\nMessage-Id: <abc@host>\r\n\r\n")]),
    ]
    mock_connect.return_value = fake_conn

    resp = client.post(
        f"/api/restore/workspace/search",
        json={
            "account_id": acct.id,
            "query": "x",
            "range_start": (datetime.now(UTC) - timedelta(days=7)).isoformat(),
            "range_end": datetime.now(UTC).isoformat(),
            "include_live": True,
            "include_snapshots": True,
        },
    )

    assert resp.status_code == 200
    body = resp.json()
    assert len(body["results"]) == 1
    result = body["results"][0]
    assert "live" in result["sources"]
    assert "snap1" in result["sources"]
```

You will also need a `client` fixture and a `login_user` fixture if they don't exist — check `tests/conftest.py` first:

```bash
grep -n "def client\|def login_user" tests/conftest.py
```

If `client` exists but `login_user` doesn't, add a minimal fixture in `tests/conftest.py`:

```python
@pytest.fixture
def login_user(db_session):
    from mailfallback.models import User, UserRole
    from mailfallback.security import hash_password
    u = User(
        username="koma",
        email="koma@test",
        password_hash=hash_password("x"),
        role=UserRole.admin,
        enabled=True,
    )
    db_session.add(u)
    db_session.commit()
    return u
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_restore_workspace_router.py::test_workspace_search_dedup_by_message_id -v
```

Expected: FAIL — `404 /api/restore/workspace/search` (route not defined yet).

- [ ] **Step 3: Add the workspace search endpoint to `restore.py`**

In `src/mailfallback/routers/restore.py`, add the import at the top:

```python
from mailfallback.services import mount_service, restic_service
```

Then add the endpoint near the bottom of the file:

```python
from datetime import datetime
from pydantic import BaseModel


class WorkspaceSearchRequest(BaseModel):
    account_id: str
    query: str
    range_start: datetime
    range_end: datetime
    include_live: bool = True
    include_snapshots: bool = True


@router.post("/workspace/search")
def workspace_search(
    req: WorkspaceSearchRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    user = _get_session_user(request, db) if "_get_session_user" in globals() else None
    account = db.query(Account).filter(Account.id == req.account_id).first()
    if not account:
        raise HTTPException(404, "account not found")

    results_by_msgid: dict[str, dict] = {}

    # Find in-range snapshots (by snapshot time).
    snapshot_ids: list[str] = []
    if req.include_snapshots:
        backup = (
            db.query(BackupPolicy)
            .filter(BackupPolicy.account_id == req.account_id)
            .first()
        )
        if backup:
            try:
                snaps = restic_service.list_snapshots(backup.destination, account.id)
            except Exception:
                snaps = []
            for s in snaps:
                ts_raw = s.get("time", "").replace("Z", "+00:00")
                if not ts_raw:
                    continue
                try:
                    ts = datetime.fromisoformat(ts_raw)
                except ValueError:
                    continue
                if req.range_start <= ts <= req.range_end:
                    snapshot_ids.append(s.get("short_id") or s.get("id", "")[:8])

    # Mount each snapshot ephemeral (idempotent, capped).
    mounted: list[tuple[str, str]] = []  # (snapshot_id, namespace_prefix)
    for snap_id in snapshot_ids[: settings.recovery_max_parallel_mounts]:
        rec = mount_service.ensure_mounted(db, req.account_id, snap_id)
        if rec.status != RecoveryStatus.ready:
            continue
        # Namespace label mirrors dovecot.py's existing convention.
        ns_label = f"Recovery — {account.name} ({snap_id})/"
        mounted.append((snap_id, ns_label))

    # Search live first, then each mounted snapshot.
    conn = _connect_dovecot_for_account(db, account)
    try:
        if req.include_live:
            for hit in _search_namespace_for_query(conn, namespace="", query=req.query):
                _merge_hit(results_by_msgid, hit, source_label="live")
        for snap_id, ns in mounted:
            for hit in _search_namespace_for_query(conn, namespace=ns, query=req.query):
                _merge_hit(results_by_msgid, hit, source_label=snap_id)
    finally:
        try:
            conn.logout()
        except Exception:
            pass

    return {"results": list(results_by_msgid.values()), "mounted_snapshots": [s for s, _ in mounted]}


def _merge_hit(dedup: dict[str, dict], hit: dict, source_label: str) -> None:
    """Merge a search hit into the dedup map keyed by Message-Id."""
    msgid = hit.get("message_id") or f"_no_msgid_{source_label}_{hit.get('uid')}"
    if msgid in dedup:
        if source_label not in dedup[msgid]["sources"]:
            dedup[msgid]["sources"].append(source_label)
    else:
        entry = dict(hit)
        entry["sources"] = [source_label]
        dedup[msgid] = entry
```

Make sure `_fetch_message_header` extracts `Message-Id` — check the current implementation:

```bash
sed -n '625,665p' src/mailfallback/routers/restore.py
```

If it doesn't include `message_id` in its return dict, add it:

```python
        env["message_id"] = msg.get("Message-Id", "").strip("<>")
```

- [ ] **Step 4: Run test to verify it passes**

```bash
uv run pytest tests/test_restore_workspace_router.py::test_workspace_search_dedup_by_message_id -v
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/mailfallback/routers/restore.py tests/test_restore_workspace_router.py tests/conftest.py
git commit -m "feat(restore): workspace search endpoint — mount + dedup by Message-Id"
```

---

## Phase 3 — Workspace UI

### Task 11: New `/restore` view + skeleton template

**Files:**
- Modify: `src/mailfallback/routers/ui_restore.py`
- Create: `src/mailfallback/templates/restore_workspace.html`
- Modify: `src/mailfallback/templates/restore.html` (rewrite as workspace shell)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_restore_workspace_router.py`:

```python
def test_restore_workspace_renders(client, db_session, default_store, login_user):
    from mailfallback.models import Account, BackupPolicy, Repository

    repo = Repository(name="r", backend_type="local", path="/tmp/r")
    db_session.add(repo)
    acct = Account(name="koma", store=default_store, maildir_path="/data/mailboxes/k")
    db_session.add(acct)
    db_session.flush()
    acct.owners.append(login_user)
    db_session.add(BackupPolicy(account_id=acct.id, destination_id=repo.id))
    db_session.commit()

    resp = client.get("/restore")
    assert resp.status_code == 200
    assert b"workspace" in resp.content.lower()
    assert b"Cosa stai cercando di recuperare" in resp.content or b"What are you looking" in resp.content
    # Preset chips visible
    assert b"data-preset=\"single-mail\"" in resp.content
    assert b"data-preset=\"folder\"" in resp.content
    assert b"data-preset=\"full\"" in resp.content
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_restore_workspace_router.py::test_restore_workspace_renders -v
```

Expected: FAIL — current `/restore` does not contain the preset chips.

- [ ] **Step 3: Rewrite `templates/restore.html`**

Replace the entire content of `src/mailfallback/templates/restore.html` with:

```html
{% extends "base.html" %}
{% block title %}Restore — MailFallBack{% endblock %}
{% block body_class %}page-wide page-restore-workspace{% endblock %}
{% block content %}

{% include "restore_workspace.html" %}

{% endblock %}
{% block scripts %}<script src="/static/js/restore_workspace.js"></script>{% endblock %}
```

- [ ] **Step 4: Create the workspace template**

Create `src/mailfallback/templates/restore_workspace.html`:

```html
<div class="flex-between">
  <div>
    <h2><i data-lucide="archive-restore" class="icon-xl icon-inline"></i>Restore</h2>
    <p class="text-muted text-small mt-0">What are you looking for?</p>
  </div>
</div>

{# Preset chips #}
<div class="restore-presets" role="tablist">
  <button type="button" class="preset-chip is-active" data-preset="single-mail">
    <i data-lucide="mail" class="icon-sm icon-inline"></i> A single mail
  </button>
  <button type="button" class="preset-chip" data-preset="folder">
    <i data-lucide="folder" class="icon-sm icon-inline"></i> A folder / subset
  </button>
  <button type="button" class="preset-chip" data-preset="full">
    <i data-lucide="alert-triangle" class="icon-sm icon-inline"></i> The whole mailbox
  </button>
</div>

<div class="workspace-grid">
  <aside class="workspace-sidebar">
    <label>Mailbox
      <select name="account_id" id="ws-account">
        {% for acct, _ in protected %}
        <option value="{{ acct.id }}">{{ acct.name }} ({{ acct.email_address }})</option>
        {% endfor %}
      </select>
    </label>

    <label>Time range
      <input type="date" id="ws-range-start" name="range_start">
      <input type="date" id="ws-range-end" name="range_end">
    </label>
    <p class="text-muted text-xsmall" id="ws-range-cost">— snapshots in range</p>

    <fieldset>
      <legend>Sources</legend>
      <label><input type="checkbox" id="ws-include-live" checked> Live</label>
      <label><input type="checkbox" id="ws-include-snapshots" checked> Snapshots</label>
    </fieldset>

    <details class="advanced">
      <summary>Advanced</summary>
      <label><input type="checkbox" id="ws-search-body"> Search in body</label>
      <label>TTL ephemeral mounts (min)
        <input type="number" id="ws-ttl-override" min="5" max="240"
               placeholder="default {{ settings.recovery_ephemeral_ttl_minutes }}">
      </label>
    </details>

    <label>Destination
      <select name="destination_id" id="ws-destination">
        {% for acct, _ in protected %}
        <option value="{{ acct.id }}">{{ acct.name }} ({{ acct.email_address }})</option>
        {% endfor %}
      </select>
    </label>
  </aside>

  <main class="workspace-panel">
    <form id="ws-search-form" onsubmit="event.preventDefault(); window.RestoreWorkspace.runSearch();">
      <div class="search-row">
        <input type="text" id="ws-query" placeholder="Search subject, sender…" autocomplete="off">
        <button type="submit" class="primary">
          <i data-lucide="search" class="icon-sm icon-inline"></i> Search
        </button>
      </div>
      <p class="text-muted text-xsmall" id="ws-mount-progress"></p>
    </form>

    <div id="ws-results"></div>

    <div id="ws-action-bar" class="ws-action-bar hidden">
      <label><input type="checkbox" id="ws-select-all"> Select all</label>
      <button type="button" id="ws-restore-selected" class="primary">
        <i data-lucide="play" class="icon-sm icon-inline"></i> Restore selected →
      </button>
    </div>
  </main>
</div>

{% include "partials/restore_status_strip.html" ignore missing %}
```

- [ ] **Step 5: Update the `/restore` route to pass `protected` and `settings`**

Inspect the existing route:

```bash
sed -n '52,112p' src/mailfallback/routers/ui_restore.py
```

Modify the existing `restore_page` function in `src/mailfallback/routers/ui_restore.py`. Add `from mailfallback.config import settings` at the top of the file if not present, then in the context dict passed to `templates.TemplateResponse` add:

```python
            "settings": settings,
```

Keep the existing `protected` and `unprotected` keys — the new template still uses `protected` for the dropdown.

- [ ] **Step 6: Create a stub status strip partial**

Create `src/mailfallback/templates/partials/restore_status_strip.html`:

```html
{# Demoted health/calendar — moved out of the primary surface. #}
<details class="restore-status-strip">
  <summary class="text-muted text-small">
    <i data-lucide="activity" class="icon-sm icon-inline"></i> Backup status & calendar
  </summary>
  <div class="restore-status-body">
    {# Health line — short, colored. #}
    <p class="status-line {{ health|default('unknown') }}">
      {% if health == "all_clear" %}
      ● All clear · {{ protected|length }} mailbox(es) safe · last snapshot {{ most_recent_snapshot|time_ago if most_recent_snapshot else "—" }}
      {% elif health == "attention" %}
      ● Check your backups — at least one missed run.
      {% elif health == "critical" %}
      ● No successful off-site copy in 7+ days for at least one mailbox.
      {% else %}
      ○ No safety net configured yet — <a href="/admin/backup">add a Repository →</a>
      {% endif %}
    </p>
    {# Compact per-account snapshot strip (reuses calendar-row partials lazily). #}
    {% for account, _policy in protected %}
    <div class="calendar-row compact"
         id="status-row-{{ account.id }}"
         hx-get="/restore/partials/calendar/{{ account.id }}"
         hx-trigger="revealed"
         hx-swap="outerHTML">
      <div class="cal-mailbox"><strong>{{ account.name }}</strong></div>
      <div class="cal-strip is-loading">
        {% for _ in range(20) %}<span class="snap-dot-skeleton"></span>{% endfor %}
      </div>
    </div>
    {% endfor %}
  </div>
</details>
```

- [ ] **Step 7: Run the test**

```bash
uv run pytest tests/test_restore_workspace_router.py::test_restore_workspace_renders -v
```

Expected: PASS

- [ ] **Step 8: Manual smoke**

```bash
docker compose up -d --build
```

Visit `http://localhost:8000/restore`. Expected: workspace layout with three preset chips, sidebar form, empty search panel, status strip collapsed at bottom.

- [ ] **Step 9: Commit**

```bash
git add src/mailfallback/templates/restore.html \
        src/mailfallback/templates/restore_workspace.html \
        src/mailfallback/templates/partials/restore_status_strip.html \
        src/mailfallback/routers/ui_restore.py \
        tests/test_restore_workspace_router.py
git commit -m "feat(ui): /restore workspace shell — presets, sidebar, status strip"
```

---

### Task 12: Workspace JS — search wiring + result rendering

**Files:**
- Create: `src/mailfallback/static/js/restore_workspace.js`
- Create: `src/mailfallback/templates/partials/restore_search_results.html`

- [ ] **Step 1: Create the partial that the JS will inject**

Create `src/mailfallback/templates/partials/restore_search_results.html`:

```html
{# Server-side rendered fragment of search hits — used both as initial empty
   state and as the template the client mirrors. #}
{% if not results %}
<p class="text-muted text-small">No results yet — type a query and press Search.</p>
{% else %}
{% for r in results %}
<div class="ws-result" data-msgid="{{ r.message_id|default('') }}">
  <label class="ws-result-row">
    <input type="checkbox" class="ws-result-cb" value="{{ r.message_id|default('') }}">
    <div class="ws-result-meta">
      <strong>{{ r.subject|default('(no subject)') }}</strong>
      <div class="text-muted text-xsmall">
        {{ r.folder|default('') }} · from {{ r['from']|default('?') }}
      </div>
      <div class="ws-badges">
        {% for s in r.sources %}
          <span class="ws-badge ws-badge-{{ 'live' if s == 'live' else 'snap' }}">{{ s }}</span>
        {% endfor %}
      </div>
    </div>
  </label>
</div>
{% endfor %}
{% endif %}
```

- [ ] **Step 2: Create the JS module**

Create `src/mailfallback/static/js/restore_workspace.js`:

```javascript
(function () {
  const RW = window.RestoreWorkspace = {};

  // Default range: last 7 days.
  function setDefaultRange() {
    const end = new Date();
    const start = new Date();
    start.setDate(start.getDate() - 7);
    document.getElementById('ws-range-start').valueAsDate = start;
    document.getElementById('ws-range-end').valueAsDate = end;
  }

  RW.applyPreset = function (preset) {
    document.querySelectorAll('.preset-chip').forEach(c => c.classList.toggle('is-active', c.dataset.preset === preset));
    if (preset === 'full') {
      document.getElementById('ws-include-live').checked = false;
      document.getElementById('ws-include-snapshots').checked = true;
    } else if (preset === 'folder') {
      document.getElementById('ws-include-live').checked = true;
      document.getElementById('ws-include-snapshots').checked = true;
    } else {
      document.getElementById('ws-include-live').checked = true;
      document.getElementById('ws-include-snapshots').checked = true;
    }
  };

  RW.runSearch = async function () {
    const accountId = document.getElementById('ws-account').value;
    const query = document.getElementById('ws-query').value.trim();
    const rangeStart = document.getElementById('ws-range-start').value;
    const rangeEnd = document.getElementById('ws-range-end').value;
    const includeLive = document.getElementById('ws-include-live').checked;
    const includeSnapshots = document.getElementById('ws-include-snapshots').checked;
    if (!query) return;

    const resultsEl = document.getElementById('ws-results');
    const progressEl = document.getElementById('ws-mount-progress');
    progressEl.textContent = 'Mounting snapshots & searching…';
    resultsEl.innerHTML = '';

    try {
      const resp = await fetch('/api/restore/workspace/search', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
          account_id: accountId,
          query: query,
          range_start: new Date(rangeStart).toISOString(),
          range_end: new Date(rangeEnd + 'T23:59:59').toISOString(),
          include_live: includeLive,
          include_snapshots: includeSnapshots,
        }),
      });
      if (!resp.ok) {
        progressEl.textContent = `Search failed: ${resp.status}`;
        return;
      }
      const body = await resp.json();
      progressEl.textContent = `${body.results.length} result(s) · ${body.mounted_snapshots.length} snapshot(s) mounted`;
      RW.renderResults(body.results);
    } catch (e) {
      progressEl.textContent = `Search error: ${e.message}`;
    }
  };

  RW.renderResults = function (results) {
    const el = document.getElementById('ws-results');
    const bar = document.getElementById('ws-action-bar');
    if (!results.length) {
      el.innerHTML = '<p class="text-muted text-small">Nothing matched. Try expanding the time range.</p>';
      bar.classList.add('hidden');
      return;
    }
    el.innerHTML = results.map(r => `
      <div class="ws-result" data-msgid="${r.message_id || ''}">
        <label class="ws-result-row">
          <input type="checkbox" class="ws-result-cb" value="${r.message_id || ''}">
          <div class="ws-result-meta">
            <strong>${escapeHtml(r.subject || '(no subject)')}</strong>
            <div class="text-muted text-xsmall">${escapeHtml(r.folder || '')} · from ${escapeHtml(r.from || '?')}</div>
            <div class="ws-badges">
              ${r.sources.map(s => `<span class="ws-badge ws-badge-${s === 'live' ? 'live' : 'snap'}">${escapeHtml(s)}</span>`).join('')}
            </div>
          </div>
        </label>
      </div>
    `).join('');
    bar.classList.remove('hidden');
  };

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  }

  document.addEventListener('DOMContentLoaded', () => {
    setDefaultRange();
    document.querySelectorAll('.preset-chip').forEach(chip => {
      chip.addEventListener('click', () => RW.applyPreset(chip.dataset.preset));
    });
    document.getElementById('ws-select-all').addEventListener('change', e => {
      document.querySelectorAll('.ws-result-cb').forEach(cb => { cb.checked = e.target.checked; });
    });
  });
})();
```

- [ ] **Step 3: Smoke-test in browser**

```bash
docker compose up -d --build
```

Visit `/restore`, type a query in the search box, click Search. Expected: a JSON request to `/api/restore/workspace/search` is fired, results render below (empty if no snapshots are present).

- [ ] **Step 4: Commit**

```bash
git add src/mailfallback/static/js/restore_workspace.js \
        src/mailfallback/templates/partials/restore_search_results.html
git commit -m "feat(ui): workspace JS — search wiring, preset toggles, result rendering"
```

---

### Task 13a: Workspace search returns per-source `locations`

**Context:** The existing restore engine (`POST /api/restore`, schema `RestoreCreate`) takes `source_account_id`, `target_account_id`, `restore_mode`, `selected_folders`, `selected_uids` (a `dict` of `{folder: [uid, ...]}`). It assumes a single source. The workspace dedup'd result has multiple sources per Message-Id; we need to keep the per-source `(folder, uid)` so the front-end can group correctly when the user clicks Restore.

**Files:**
- Modify: `src/mailfallback/routers/restore.py` (the `_merge_hit` from Task 10)
- Modify: `tests/test_restore_workspace_router.py`

- [ ] **Step 1: Tighten the dedup test to assert `locations` shape**

Replace the assertion block in `test_workspace_search_dedup_by_message_id` (Task 10) — change the last block from:

```python
    body = resp.json()
    assert len(body["results"]) == 1
    result = body["results"][0]
    assert "live" in result["sources"]
    assert "snap1" in result["sources"]
```

to:

```python
    body = resp.json()
    assert len(body["results"]) == 1
    result = body["results"][0]
    locs = {l["source"]: l for l in result["locations"]}
    assert "live" in locs and "snap1" in locs
    assert locs["live"]["uid"] == "10"
    assert locs["snap1"]["uid"] == "99"
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
uv run pytest tests/test_restore_workspace_router.py::test_workspace_search_dedup_by_message_id -v
```

Expected: FAIL — current `_merge_hit` does not produce `locations`.

- [ ] **Step 3: Update `_merge_hit` in `restore.py`**

Replace the `_merge_hit` function added in Task 10 with:

```python
def _merge_hit(dedup: dict[str, dict], hit: dict, source_label: str) -> None:
    """Merge a hit into the dedup map keyed by Message-Id, preserving per-source location.

    Each entry has:
      message_id, subject, from, sources: [labels...],
      locations: [ {source, namespace, folder, uid}, ... ]
    """
    msgid = hit.get("message_id") or f"_no_msgid_{source_label}_{hit.get('uid')}"
    location = {
        "source": source_label,
        "namespace": hit.get("namespace", ""),
        "folder": hit.get("folder", ""),
        "uid": hit.get("uid"),
    }
    if msgid in dedup:
        if source_label not in dedup[msgid]["sources"]:
            dedup[msgid]["sources"].append(source_label)
            dedup[msgid]["locations"].append(location)
    else:
        # Top-level subject/from/folder are kept for display; locations holds
        # the per-source (namespace, folder, uid) used by Restore Selected.
        entry = {
            "message_id": msgid,
            "subject": hit.get("subject"),
            "from": hit.get("from"),
            "folder": hit.get("folder", ""),
            "sources": [source_label],
            "locations": [location],
        }
        dedup[msgid] = entry
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
uv run pytest tests/test_restore_workspace_router.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/mailfallback/routers/restore.py tests/test_restore_workspace_router.py
git commit -m "feat(restore): workspace search returns per-source locations (folder, uid)"
```

---

### Task 13b: Restore Selected → group locations, submit one job per source namespace

**Context:** Each selected result may live in multiple namespaces (live + several snapshots). For v1 we pick the **best** source per Message-Id (priority: `live` > newest snapshot > older snapshots) so we submit at most one restore job per distinct source namespace. The existing engine handles each as `selected_uids = {folder: [uid]}`.

**Files:**
- Modify: `src/mailfallback/static/js/restore_workspace.js`
- Modify: `tests/test_restore_workspace_router.py`

- [ ] **Step 1: Write the integration test for the JS contract via the API**

This step verifies the back-end accepts what the JS will send. Append to `tests/test_restore_workspace_router.py`:

```python
@patch("mailfallback.routers.restore.submit_restore_job")
@patch("mailfallback.routers.restore.create_restore_job")
def test_workspace_restore_post_to_existing_engine(
    mock_create_job, mock_submit, client, db_session, default_store, login_user
):
    from mailfallback.models import Account, Repository, BackupPolicy

    repo = Repository(name="r", backend_type="local", path="/tmp/r")
    db_session.add(repo)
    src = Account(name="src", store=default_store, maildir_path="/data/mailboxes/s")
    dst = Account(name="dst", store=default_store, maildir_path="/data/mailboxes/d")
    db_session.add_all([src, dst])
    db_session.flush()
    src.owners.append(login_user)
    dst.owners.append(login_user)
    db_session.add(BackupPolicy(account_id=src.id, destination_id=repo.id))
    db_session.commit()

    fake_job = MagicMock()
    fake_job.id = "j-1"
    fake_job.status.value = "pending"
    fake_job.source_account_id = src.id
    fake_job.target_account_id = dst.id
    fake_job.restore_mode.value = "selection"
    mock_create_job.return_value = fake_job

    resp = client.post(
        "/api/restore",
        json={
            "source_account_id": src.id,
            "target_account_id": dst.id,
            "restore_mode": "selection",
            "selected_uids": {"INBOX": ["10"]},
        },
    )
    assert resp.status_code == 200
    mock_create_job.assert_called_once()
    mock_submit.assert_called_once_with("j-1")
```

- [ ] **Step 2: Run the test**

```bash
uv run pytest tests/test_restore_workspace_router.py::test_workspace_restore_post_to_existing_engine -v
```

Expected: PASS (the existing engine already handles `selected_uids` per `restore.py:36-90`).

- [ ] **Step 3: Wire the JS button**

Append to `src/mailfallback/static/js/restore_workspace.js`, inside the `DOMContentLoaded` handler:

```javascript
    document.getElementById('ws-restore-selected').addEventListener('click', async () => {
      const selectedRows = Array.from(document.querySelectorAll('.ws-result-cb:checked'))
        .map(cb => cb.closest('.ws-result'));
      if (!selectedRows.length) {
        alert('Select at least one result');
        return;
      }

      // RW.lastResults is set by renderResults — used to look up locations.
      const byMsgid = Object.fromEntries((RW.lastResults || []).map(r => [r.message_id, r]));

      // Group locations by source label; pick "best" location per result
      // (priority: live > snapshot listed earliest in sources).
      const grouped = {};  // sourceLabel -> {folder: [uid, ...]}
      for (const row of selectedRows) {
        const msgid = row.dataset.msgid;
        const result = byMsgid[msgid];
        if (!result) continue;
        const best = result.locations.find(l => l.source === 'live') || result.locations[0];
        if (!best) continue;
        if (!grouped[best.source]) grouped[best.source] = {};
        const folderKey = (best.namespace || '') + best.folder;
        if (!grouped[best.source][folderKey]) grouped[best.source][folderKey] = [];
        grouped[best.source][folderKey].push(String(best.uid));
      }

      const sourceAcct = document.getElementById('ws-account').value;
      const destAcct = document.getElementById('ws-destination').value;
      const jobs = [];
      for (const [sourceLabel, selected_uids] of Object.entries(grouped)) {
        const resp = await fetch('/api/restore', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({
            source_account_id: sourceAcct,
            target_account_id: destAcct,
            restore_mode: 'selection',
            selected_uids: selected_uids,
          }),
        });
        if (resp.ok) {
          jobs.push((await resp.json()).job_id);
        } else {
          alert(`Failed for source ${sourceLabel}: ${resp.status}`);
          return;
        }
      }
      alert(`Started ${jobs.length} restore job(s): ${jobs.join(', ')}`);
    });
```

Also update `RW.renderResults` to stash the last results so the click handler can look them up — at the very top of `renderResults`, add:

```javascript
    RW.lastResults = results;
```

- [ ] **Step 4: Manual smoke**

```bash
docker compose up -d --build
```

Browser: search → select 1 result that exists in `live` → click Restore selected. Expected: one job submitted, success alert with job ID. Check `/restore/move` History (or `/api/restore/{job_id}`) — job appears with `restore_mode=selection` and the right UID.

- [ ] **Step 5: Verify snapshot-source restore end-to-end (CRITICAL)**

This is the integration risk. The workspace passes `selected_uids = {"Recovery — name (snap-X)/INBOX": ["99"]}` to the engine. The engine (`services/restore_worker.py`) opens an IMAP source connection as the source account's owner and iterates over `selected_uids` keys. Because the Recovery is exposed as a Dovecot namespace under the same owner (`routers/dovecot.py:73-94`), `src_conn.select("Recovery — name (snap-X)/INBOX")` should succeed and the COPY proceeds normally.

End-to-end test:

```bash
docker compose up -d --build
# create a backup, run it, wait for a snapshot, then via the workspace:
# 1. mount the snapshot (search anything, even no hits will mount it)
# 2. confirm Recovery row exists with status=ready
# 3. restore one message from a result whose only source is the snap
# 4. observe the restore job completes successfully and the message lands
#    in the destination
```

If the COPY fails because `_resolve_folders` (`restore_worker.py:226`) restricts folder discovery to the source's default namespace prefix, fix it inline:

```python
def _resolve_folders(src_conn, source, job):
    namespace_prefix = _get_namespace_prefix(source)
    # If selected_uids was passed, trust the keys verbatim — they may include
    # alternative namespaces (e.g. mounted Recovery snapshots).
    if job.selected_uids:
        return [(folder, folder) for folder in job.selected_uids.keys()]
    # ... existing behaviour for mode=full / mode=folder follows
```

- [ ] **Step 6: Commit**

```bash
git add src/mailfallback/static/js/restore_workspace.js \
        tests/test_restore_workspace_router.py \
        src/mailfallback/services/restore_worker.py
git commit -m "feat(restore): Restore selected groups by source and submits one job per source"
```

---

### Task 14: Workspace CSS — layout, chips, badges, sidebar

**Files:**
- Modify: `src/mailfallback/static/css/style.css`

- [ ] **Step 1: Append the workspace styles**

Open `src/mailfallback/static/css/style.css` and append at the end:

```css
/* === Restore workspace === */

.page-restore-workspace .restore-presets {
  display: flex;
  gap: .5rem;
  margin: .75rem 0 1rem;
}

.preset-chip {
  background: var(--card-bg, #1e293b);
  color: var(--text, #cbd5e1);
  border: 1px solid var(--border, #334155);
  padding: .4rem .75rem;
  border-radius: 999px;
  cursor: pointer;
  font-size: .85rem;
}
.preset-chip.is-active {
  border-color: #38bdf8;
  color: #38bdf8;
  background: rgba(56, 189, 248, 0.08);
}

.workspace-grid {
  display: grid;
  grid-template-columns: 240px 1fr;
  gap: 1rem;
  align-items: start;
}
@media (max-width: 768px) {
  .workspace-grid { grid-template-columns: 1fr; }
}

.workspace-sidebar {
  background: var(--card-bg, #1e293b);
  border-radius: .5rem;
  padding: .75rem;
  display: flex;
  flex-direction: column;
  gap: .5rem;
}
.workspace-sidebar label { display: block; font-size: .8rem; }
.workspace-sidebar select,
.workspace-sidebar input[type="date"],
.workspace-sidebar input[type="number"] { width: 100%; }

.workspace-panel .search-row {
  display: flex;
  gap: .5rem;
  align-items: center;
}
.workspace-panel .search-row input { flex: 1; }

.ws-result {
  background: var(--card-bg, #1e293b);
  border-radius: .35rem;
  padding: .5rem .75rem;
  margin-top: .35rem;
  border-left: 3px solid #38bdf8;
}
.ws-result-row { display: flex; gap: .5rem; align-items: flex-start; cursor: pointer; }
.ws-result-meta { flex: 1; }

.ws-badges { margin-top: .25rem; display: flex; gap: .25rem; flex-wrap: wrap; }
.ws-badge {
  font-size: .7rem;
  padding: .15rem .4rem;
  border-radius: .2rem;
  background: #334155;
  color: #cbd5e1;
}
.ws-badge-live { background: #0ea5e9; color: white; }
.ws-badge-snap { background: #475569; color: #e2e8f0; }

.ws-action-bar {
  margin-top: .75rem;
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: .5rem .75rem;
  background: var(--card-bg, #1e293b);
  border-radius: .35rem;
}
.ws-action-bar.hidden { display: none; }

.restore-status-strip {
  margin-top: 2rem;
  border-top: 1px solid var(--border, #334155);
  padding-top: .75rem;
}
.restore-status-strip summary { cursor: pointer; }
.restore-status-strip .calendar-row.compact { padding: .25rem 0; }
```

- [ ] **Step 2: Visual check**

Reload `/restore` in the browser. Expected: presets pill-shaped, sidebar boxed, search input full-width, results render with provenance badges.

- [ ] **Step 3: Commit**

```bash
git add src/mailfallback/static/css/style.css
git commit -m "feat(ui): /restore workspace styles — presets, sidebar, badges, status strip"
```

---

## Phase 4 — Cleanup

### Task 15: Remove the legacy footer card and rewire `/restore/jump`

**Files:**
- (Already removed in Task 11 — the old `restore.html` content is gone since we rewrote it.)
- Modify: `src/mailfallback/routers/ui_restore.py:223` (`/restore/jump`)

- [ ] **Step 1: Inspect the current `/restore/jump`**

```bash
sed -n '223,237p' src/mailfallback/routers/ui_restore.py
```

It currently routes a single account to a snapshot picker. We make it explicit that the jump path creates **persistent** Recoveries — that's the "lost everything" path that needs the long-lived mount.

- [ ] **Step 2: Pass `kind=persistent` explicitly**

In the `/restore/jump` handler, find where it calls `recovery_service.create_recovery` (or the picker that does it). If the call is downstream, no change needed — the default is already `persistent` after Task 4. Add a comment at the top of the handler:

```python
    # /restore/jump creates a persistent Recovery — for the "lost everything"
    # case where the user needs the snapshot mounted for hours/days. The new
    # /restore workspace creates ephemeral Recoveries instead.
```

- [ ] **Step 3: Run the full test suite**

```bash
uv run pytest tests/ -n auto -v
```

Expected: all tests green (~423+ passing per the existing baseline).

- [ ] **Step 4: Commit**

```bash
git add src/mailfallback/routers/ui_restore.py
git commit -m "docs(restore): clarify /restore/jump creates persistent Recoveries (vs ephemeral workspace)"
```

---

### Task 16: Lint, format, lexicon, full smoke

**Files:**
- (none — verification only)

- [ ] **Step 1: Lint**

```bash
uv run ruff check src/ tests/
```

Expected: no errors. Fix any reported.

- [ ] **Step 2: Format**

```bash
uv run ruff format src/ tests/
```

- [ ] **Step 3: Lexicon check (advisory)**

```bash
uv run pre-commit run lexicon-check --all-files 2>/dev/null || true
```

Address any flagged copy.

- [ ] **Step 4: Full test suite**

```bash
uv run pytest tests/ -n auto -v
```

Expected: all green.

- [ ] **Step 5: Visual end-to-end smoke**

```bash
docker compose up -d --build
```

In the browser:
1. `/restore` shows the workspace, three preset chips, sidebar, empty search panel, status strip at the bottom.
2. Click a preset → toggles update.
3. Type a search query, click Search → results appear with provenance badges.
4. Select results, click "Restore selected" → restore job kicks off.
5. Wait 30+ minutes (or tweak `MAILFALLBACK_RECOVERY_EPHEMERAL_TTL_MINUTES=1` and wait 2 min) → ephemeral Recovery rows in DB are gone, on-disk dirs cleaned up.

- [ ] **Step 6: Commit (only if any format/lint changes)**

```bash
git add -A
git diff --cached --quiet || git commit -m "chore: format + lint pass after restore workspace"
```

- [ ] **Step 7: Push branch & open PR**

```bash
git push origin feat/recovery-model
gh pr view 164 --web
```

The branch is already `feat/recovery-model` (PR #164 open). The PR description should be updated to reflect the workspace addition.

---

## Notes for the executor

- The spec is the source of truth for design intent. If a task seems under-specified, re-read the spec section it implements.
- All new env vars default to sensible values; you don't need to set anything to run tests.
- Tests use SQLite in-memory (per `conftest.py`). The Alembic migration tests (Task 3 step 3) will exercise the migration logic against SQLite metadata; the PostgreSQL smoke (Task 3 step 4) is the authoritative check.
- `restic_service` is mocked everywhere it appears in tests — never call real restic from a unit test.
- The Dovecot Lua userdb already includes Recoveries with `status=ready` as namespaces (`routers/dovecot.py:73-94`). No changes needed there for ephemeral support — the namespace prefix convention `"Recovery — {label} ({ts}) [{short}]/"` already works.
- For Phase 3 UI work, the design called for "search streams as snapshots get mounted." This plan ships a simpler synchronous version (mount all in-range snapshots, then search). Adding SSE/streaming is a follow-up worth doing once the basic flow is validated.
