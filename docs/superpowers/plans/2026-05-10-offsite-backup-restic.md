# Offsite Backup with Restic Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Per-account offsite backup to S3 or local storage via restic, with scheduled backups, retention policies, and restore to temporary read-only accounts.

**Architecture:** Two new models (BackupDestination, AccountBackup) + restic subprocess wrapper + backup worker (ThreadPoolExecutor pattern from sync_worker) + admin UI for destinations + account detail backup section. Restore creates temporary Account pointing to restored Maildir.

**Tech Stack:** restic binary (subprocess), SQLAlchemy models, APScheduler, HTMX UI

---

### Task 1: Models and Migration

**Files:**
- Modify: `src/mailfallback/models.py`
- Create: `alembic/versions/010_add_backup_tables.py`

- [ ] **Step 1: Add enums and models to models.py**

Add after the `TaskStatus` enum:

```python
class BackendType(enum.StrEnum):
    s3 = "s3"
    local = "local"


class RetentionPreset(enum.StrEnum):
    light = "light"
    standard = "standard"
    full = "full"
    custom = "custom"


class BackupStatus(enum.StrEnum):
    idle = "idle"
    running = "running"
    completed = "completed"
    failed = "failed"
```

Add after the `BackgroundTask` class:

```python
class BackupDestination(Base):
    __tablename__ = "backup_destinations"

    id = Column(String, primary_key=True, default=_new_uuid)
    name = Column(String, nullable=False)
    backend_type = Column(Enum(BackendType), nullable=False)
    s3_endpoint = Column(String, nullable=True)
    s3_bucket = Column(String, nullable=True)
    s3_access_key = Column(String, nullable=True)
    s3_secret_key = Column(String, nullable=True)
    local_path = Column(String, nullable=True)
    restic_password = Column(String, nullable=False)  # pragma: allowlist secret
    created_at = Column(DateTime(timezone=True), default=_utcnow)


class AccountBackup(Base):
    __tablename__ = "account_backups"

    id = Column(String, primary_key=True, default=_new_uuid)
    account_id = Column(String, ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False)
    destination_id = Column(
        String, ForeignKey("backup_destinations.id"), nullable=False
    )
    enabled = Column(Boolean, nullable=False, default=True, server_default="true")
    schedule = Column(String, nullable=False, default="0 2 * * *")
    retention_preset = Column(
        Enum(RetentionPreset), nullable=False, default=RetentionPreset.standard
    )
    keep_daily = Column(Integer, nullable=True)
    keep_weekly = Column(Integer, nullable=True)
    keep_monthly = Column(Integer, nullable=True)
    last_backup_at = Column(DateTime(timezone=True), nullable=True)
    last_status = Column(
        Enum(BackupStatus), nullable=False, default=BackupStatus.idle
    )
    last_error = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=_utcnow)

    account = relationship("Account", backref="backups")
    destination = relationship("BackupDestination")
```

- [ ] **Step 2: Create migration**

```bash
docker compose exec mailfallback uv run alembic revision --autogenerate -m "add backup tables"
```

Edit the generated file to keep only `backup_destinations` and `account_backups` table creation (remove any Roundcube `rc_*` table drops). Save as `alembic/versions/010_add_backup_tables.py` with `revision = "010"` and `down_revision = "009"`.

- [ ] **Step 3: Run tests**

Run: `uv run pytest tests/ -n auto --tb=short`
Expected: all pass

- [ ] **Step 4: Commit**

```bash
git add src/mailfallback/models.py alembic/versions/010_add_backup_tables.py
git commit -m "feat: add BackupDestination and AccountBackup models"
```

---

### Task 2: Restic Service

**Files:**
- Create: `src/mailfallback/services/restic_service.py`
- Create: `tests/test_restic_service.py`

- [ ] **Step 1: Write tests**

Create `tests/test_restic_service.py`:

```python
import json
from unittest.mock import MagicMock, patch

from mailfallback.services.restic_service import (
    build_env,
    build_repo_url,
    get_retention_args,
    init_repo,
    list_snapshots,
    run_backup,
)


class FakeDest:
    backend_type = "s3"
    s3_endpoint = "s3.example.com"
    s3_bucket = "mfb-backups"
    s3_access_key = "encrypted_key"
    s3_secret_key = "encrypted_secret"  # pragma: allowlist secret
    restic_password = "encrypted_pass"  # pragma: allowlist secret
    local_path = None


class FakeDestLocal:
    backend_type = "local"
    s3_endpoint = None
    s3_bucket = None
    s3_access_key = None
    s3_secret_key = None
    restic_password = "encrypted_pass"  # pragma: allowlist secret
    local_path = "/mnt/nas/backups"


def test_build_repo_url_s3():
    url = build_repo_url(FakeDest(), "abc-123")
    assert url == "s3:s3.example.com/mfb-backups/abc-123"


def test_build_repo_url_local():
    url = build_repo_url(FakeDestLocal(), "abc-123")
    assert url == "/mnt/nas/backups/abc-123"


@patch("mailfallback.services.restic_service.decrypt_credentials", side_effect=lambda x, _: f"dec_{x}")
def test_build_env_s3(mock_decrypt):
    env = build_env(FakeDest(), "abc-123")
    assert env["RESTIC_REPOSITORY"] == "s3:s3.example.com/mfb-backups/abc-123"
    assert env["RESTIC_PASSWORD"] == "dec_encrypted_pass" # pragma: allowlist secret
    assert env["AWS_ACCESS_KEY_ID"] == "dec_encrypted_key"
    assert env["AWS_SECRET_ACCESS_KEY"] == "dec_encrypted_secret" # pragma: allowlist secret


@patch("mailfallback.services.restic_service.decrypt_credentials", side_effect=lambda x, _: f"dec_{x}")
def test_build_env_local(mock_decrypt):
    env = build_env(FakeDestLocal(), "abc-123")
    assert env["RESTIC_REPOSITORY"] == "/mnt/nas/backups/abc-123"
    assert "AWS_ACCESS_KEY_ID" not in env


def test_get_retention_args_preset_light():
    args = get_retention_args("light", None, None, None)
    assert args == ["--keep-daily", "7", "--keep-weekly", "4"]


def test_get_retention_args_preset_standard():
    args = get_retention_args("standard", None, None, None)
    assert args == ["--keep-daily", "30", "--keep-weekly", "12", "--keep-monthly", "6"]


def test_get_retention_args_preset_full():
    args = get_retention_args("full", None, None, None)
    assert args == ["--keep-daily", "90", "--keep-weekly", "52", "--keep-monthly", "24"]


def test_get_retention_args_custom():
    args = get_retention_args("custom", 5, 2, 1)
    assert args == ["--keep-daily", "5", "--keep-weekly", "2", "--keep-monthly", "1"]


@patch("mailfallback.services.restic_service.subprocess.run")
@patch("mailfallback.services.restic_service.decrypt_credentials", side_effect=lambda x, _: x)
def test_init_repo_success(mock_decrypt, mock_run):
    mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
    result = init_repo(FakeDest(), "abc-123")
    assert result is True
    mock_run.assert_called_once()
    cmd = mock_run.call_args[0][0]
    assert cmd[0] == "restic"
    assert "init" in cmd


@patch("mailfallback.services.restic_service.subprocess.run")
@patch("mailfallback.services.restic_service.decrypt_credentials", side_effect=lambda x, _: x)
def test_run_backup_success(mock_decrypt, mock_run):
    mock_run.return_value = MagicMock(
        returncode=0,
        stdout=json.dumps({"files_new": 10, "data_added": 1024}),
        stderr="",
    )
    result = run_backup(FakeDest(), "abc-123", "/data/mailboxes/abc-123")
    assert result["ok"] is True
    cmd = mock_run.call_args[0][0]
    assert "backup" in cmd
    assert "/data/mailboxes/abc-123" in cmd


@patch("mailfallback.services.restic_service.subprocess.run")
@patch("mailfallback.services.restic_service.decrypt_credentials", side_effect=lambda x, _: x)
def test_list_snapshots(mock_decrypt, mock_run):
    snapshots = [
        {"short_id": "abc123", "time": "2026-05-09T02:00:00Z", "paths": ["/data"]},
    ]
    mock_run.return_value = MagicMock(
        returncode=0, stdout=json.dumps(snapshots), stderr=""
    )
    result = list_snapshots(FakeDest(), "abc-123")
    assert len(result) == 1
    assert result[0]["short_id"] == "abc123"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_restic_service.py -v`
Expected: FAIL - module doesn't exist

- [ ] **Step 3: Implement restic_service.py**

Create `src/mailfallback/services/restic_service.py`:

```python
import json
import logging
import subprocess

from mailfallback.config import settings
from mailfallback.security import decrypt_credentials

logger = logging.getLogger(__name__)

RETENTION_PRESETS = {
    "light": {"keep_daily": 7, "keep_weekly": 4, "keep_monthly": 0},
    "standard": {"keep_daily": 30, "keep_weekly": 12, "keep_monthly": 6},
    "full": {"keep_daily": 90, "keep_weekly": 52, "keep_monthly": 24},
}


def build_repo_url(destination, account_id: str) -> str:
    if destination.backend_type == "s3":
        return f"s3:{destination.s3_endpoint}/{destination.s3_bucket}/{account_id}"
    return f"{destination.local_path}/{account_id}"


def build_env(destination, account_id: str) -> dict:
    env = {
        "RESTIC_REPOSITORY": build_repo_url(destination, account_id),
        "RESTIC_PASSWORD": decrypt_credentials(
            destination.restic_password, settings.secret_key
        ),
    }
    if destination.backend_type == "s3":
        env["AWS_ACCESS_KEY_ID"] = decrypt_credentials(
            destination.s3_access_key, settings.secret_key
        )
        env["AWS_SECRET_ACCESS_KEY"] = decrypt_credentials(
            destination.s3_secret_key, settings.secret_key
        )
    return env


def get_retention_args(
    preset: str,
    keep_daily: int | None,
    keep_weekly: int | None,
    keep_monthly: int | None,
) -> list[str]:
    if preset == "custom":
        values = {
            "keep_daily": keep_daily or 7,
            "keep_weekly": keep_weekly or 4,
            "keep_monthly": keep_monthly or 0,
        }
    else:
        values = RETENTION_PRESETS.get(preset, RETENTION_PRESETS["standard"])

    args = []
    for key, val in values.items():
        if val > 0:
            args.extend([f"--{key.replace('_', '-')}", str(val)])
    return args


def _run_restic(env: dict, args: list[str], timeout: int = 3600) -> subprocess.CompletedProcess:
    cmd = ["restic", *args]
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        env={**dict(__import__("os").environ), **env},
        timeout=timeout,
    )


def init_repo(destination, account_id: str) -> bool:
    env = build_env(destination, account_id)
    result = _run_restic(env, ["init", "--json"])
    if result.returncode == 0:
        logger.info("Initialized restic repo for %s", account_id)
        return True
    if "already initialized" in result.stderr.lower() or "config file already exists" in result.stderr.lower():
        return True
    logger.error("Failed to init repo for %s: %s", account_id, result.stderr)
    return False


def run_backup(destination, account_id: str, maildir_path: str) -> dict:
    env = build_env(destination, account_id)
    result = _run_restic(env, ["backup", maildir_path, "--json"])
    if result.returncode != 0:
        return {"ok": False, "error": result.stderr}
    try:
        summary = json.loads(result.stdout.strip().split("\n")[-1])
    except (json.JSONDecodeError, IndexError):
        summary = {}
    return {"ok": True, **summary}


def list_snapshots(destination, account_id: str) -> list[dict]:
    env = build_env(destination, account_id)
    result = _run_restic(env, ["snapshots", "--json"], timeout=30)
    if result.returncode != 0:
        logger.warning("Failed to list snapshots for %s: %s", account_id, result.stderr)
        return []
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return []


def restore_snapshot(
    destination, account_id: str, snapshot_id: str, target_path: str
) -> dict:
    env = build_env(destination, account_id)
    result = _run_restic(env, ["restore", snapshot_id, "--target", target_path])
    if result.returncode != 0:
        return {"ok": False, "error": result.stderr}
    return {"ok": True}


def apply_retention(
    destination,
    account_id: str,
    preset: str,
    keep_daily: int | None = None,
    keep_weekly: int | None = None,
    keep_monthly: int | None = None,
) -> dict:
    env = build_env(destination, account_id)
    retention_args = get_retention_args(preset, keep_daily, keep_weekly, keep_monthly)
    result = _run_restic(env, ["forget", "--prune", *retention_args, "--json"])
    if result.returncode != 0:
        return {"ok": False, "error": result.stderr}
    return {"ok": True}


def forget_all(destination, account_id: str) -> bool:
    env = build_env(destination, account_id)
    result = _run_restic(env, ["forget", "--prune", "--keep-last", "0", "--json"])
    return result.returncode == 0
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_restic_service.py -v`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add src/mailfallback/services/restic_service.py tests/test_restic_service.py
git commit -m "feat: restic service wrapper with subprocess integration"
```

---

### Task 3: Backup Worker

**Files:**
- Create: `src/mailfallback/services/backup_worker.py`
- Create: `tests/test_backup_worker.py`

- [ ] **Step 1: Write tests**

Create `tests/test_backup_worker.py`:

```python
from unittest.mock import MagicMock, patch

from mailfallback.models import AccountBackup, BackupStatus


@patch("mailfallback.services.backup_worker.init_repo", return_value=True)
@patch("mailfallback.services.backup_worker.run_backup", return_value={"ok": True, "files_new": 5})
@patch("mailfallback.services.backup_worker.apply_retention", return_value={"ok": True})
def test_execute_backup_success(mock_retention, mock_backup, mock_init, db_session, default_store):
    from mailfallback.models import Account, BackupDestination
    from mailfallback.services.backup_worker import execute_backup

    dest = BackupDestination(
        name="test", backend_type="local",
        local_path="/tmp/backups",
        restic_password="test",  # pragma: allowlist secret
    )
    db_session.add(dest)
    db_session.flush()

    acct = Account(
        name="test", imap_host="imap.test.com", imap_port=993,
        maildir_path="/data/mailboxes/test-uuid", store_id=default_store.id,
    )
    db_session.add(acct)
    db_session.flush()

    ab = AccountBackup(
        account_id=acct.id, destination_id=dest.id,
        schedule="0 2 * * *", retention_preset="standard",
    )
    db_session.add(ab)
    db_session.commit()

    execute_backup(db_session, ab.id)

    db_session.refresh(ab)
    assert ab.last_status == BackupStatus.completed
    assert ab.last_backup_at is not None
    assert ab.last_error is None
    mock_init.assert_called_once()
    mock_backup.assert_called_once()
    mock_retention.assert_called_once()


@patch("mailfallback.services.backup_worker.init_repo", return_value=True)
@patch("mailfallback.services.backup_worker.run_backup", return_value={"ok": False, "error": "connection refused"})
def test_execute_backup_failure(mock_backup, mock_init, db_session, default_store):
    from mailfallback.models import Account, BackupDestination
    from mailfallback.services.backup_worker import execute_backup

    dest = BackupDestination(
        name="test", backend_type="local",
        local_path="/tmp/backups",
        restic_password="test",  # pragma: allowlist secret
    )
    db_session.add(dest)
    db_session.flush()

    acct = Account(
        name="test", imap_host="imap.test.com", imap_port=993,
        maildir_path="/data/mailboxes/test-uuid2", store_id=default_store.id,
    )
    db_session.add(acct)
    db_session.flush()

    ab = AccountBackup(
        account_id=acct.id, destination_id=dest.id,
        schedule="0 2 * * *", retention_preset="light",
    )
    db_session.add(ab)
    db_session.commit()

    execute_backup(db_session, ab.id)

    db_session.refresh(ab)
    assert ab.last_status == BackupStatus.failed
    assert "connection refused" in ab.last_error
```

- [ ] **Step 2: Implement backup_worker.py**

Create `src/mailfallback/services/backup_worker.py`:

```python
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from mailfallback.models import AccountBackup, BackupStatus
from mailfallback.services.restic_service import (
    apply_retention,
    init_repo,
    run_backup,
)

logger = logging.getLogger(__name__)

_backup_executor: ThreadPoolExecutor | None = None
_backup_progress: dict[str, dict] = {}


def get_backup_executor() -> ThreadPoolExecutor:
    global _backup_executor
    if _backup_executor is None:
        _backup_executor = ThreadPoolExecutor(max_workers=2)
    return _backup_executor


def shutdown_backup_executor() -> None:
    global _backup_executor
    if _backup_executor is not None:
        _backup_executor.shutdown(wait=True)
        _backup_executor = None


def get_backup_progress(backup_id: str) -> dict | None:
    return _backup_progress.get(backup_id)


def execute_backup(db: Session, account_backup_id: str) -> None:
    ab = db.query(AccountBackup).filter(AccountBackup.id == account_backup_id).first()
    if not ab:
        return

    ab.last_status = BackupStatus.running
    ab.last_error = None
    db.commit()

    _backup_progress[account_backup_id] = {"status": "initializing"}

    try:
        if not init_repo(ab.destination, ab.account_id):
            ab.last_status = BackupStatus.failed
            ab.last_error = "Failed to initialize restic repository"
            db.commit()
            return

        _backup_progress[account_backup_id] = {"status": "backing up"}

        result = run_backup(ab.destination, ab.account_id, ab.account.maildir_path)
        if not result["ok"]:
            ab.last_status = BackupStatus.failed
            ab.last_error = result.get("error", "Backup failed")
            db.commit()
            return

        _backup_progress[account_backup_id] = {"status": "applying retention"}

        apply_retention(
            ab.destination,
            ab.account_id,
            ab.retention_preset.value,
            ab.keep_daily,
            ab.keep_weekly,
            ab.keep_monthly,
        )

        ab.last_status = BackupStatus.completed
        ab.last_backup_at = datetime.now(UTC)
        ab.last_error = None
        db.commit()
        logger.info("Backup completed for account %s", ab.account_id)

    except Exception as exc:
        logger.exception("Backup failed for %s", account_backup_id)
        ab.last_status = BackupStatus.failed
        ab.last_error = str(exc)
        db.commit()
    finally:
        _backup_progress.pop(account_backup_id, None)


def submit_backup(account_backup_id: str) -> None:
    def _run():
        from mailfallback.db import SessionLocal

        db = SessionLocal()
        try:
            execute_backup(db, account_backup_id)
        finally:
            db.close()

    get_backup_executor().submit(_run)
```

- [ ] **Step 3: Run tests**

Run: `uv run pytest tests/test_backup_worker.py -v`
Expected: all pass

- [ ] **Step 4: Commit**

```bash
git add src/mailfallback/services/backup_worker.py tests/test_backup_worker.py
git commit -m "feat: backup worker with async execution"
```

---

### Task 4: Backup Destinations Admin UI

**Files:**
- Create: `src/mailfallback/routers/ui_backup.py`
- Create: `src/mailfallback/templates/admin_backup.html`
- Modify: `src/mailfallback/templates/base.html`
- Modify: `src/mailfallback/app.py`

- [ ] **Step 1: Create router**

Create `src/mailfallback/routers/ui_backup.py` with routes:
- `GET /admin/backup` - list destinations, admin only
- `POST /admin/backup/new` - create destination
- `POST /admin/backup/{dest_id}/delete` - delete destination (blocked if accounts use it)

Follow the exact pattern from `ui_admin.py` store management: `_get_session_user`, admin check, `log_action`, flash messages, `RedirectResponse`.

Encrypt `s3_access_key`, `s3_secret_key`, and `restic_password` with `encrypt_credentials()` before saving.

- [ ] **Step 2: Create template**

Create `src/mailfallback/templates/admin_backup.html` following `admin_stores.html` pattern: table of destinations + inline add form at bottom. Columns: Name, Type, Bucket/Path, Accounts, Actions.

The add form has fields: name, backend_type (radio s3/local), s3_endpoint, s3_bucket, s3_access_key, s3_secret_key, local_path, restic_password. Show/hide S3 vs local fields with JS based on radio selection.

- [ ] **Step 3: Add sidebar link**

In `src/mailfallback/templates/base.html`, add after the Stores link:
```html
<a href="/admin/backup" {% if request.url.path.startswith("/admin/backup") %}class="active"{% endif %}><i data-lucide="cloud-upload" class="icon-nav"></i>Backups</a>
```

- [ ] **Step 4: Register router in app.py**

In `src/mailfallback/app.py`, import and include the router:
```python
from mailfallback.routers import ui_backup
app.include_router(ui_backup.router)
```

- [ ] **Step 5: Run tests, lint**

```bash
uv run ruff check src/ tests/ --fix && uv run ruff format src/ tests/
uv run pytest tests/ -n auto --tb=short
```

- [ ] **Step 6: Commit**

```bash
git add src/mailfallback/routers/ui_backup.py src/mailfallback/templates/admin_backup.html src/mailfallback/templates/base.html src/mailfallback/app.py
git commit -m "feat: backup destinations admin page"
```

---

### Task 5: Account Backup Section in Detail Page

**Files:**
- Create: `src/mailfallback/templates/partials/account_backup.html`
- Modify: `src/mailfallback/routers/ui_accounts.py`
- Modify: `src/mailfallback/routers/ui_backup.py`
- Modify: `src/mailfallback/templates/account_detail.html`

- [ ] **Step 1: Add routes to ui_backup.py**

Add to `src/mailfallback/routers/ui_backup.py`:
- `POST /accounts/{account_id}/backup/configure` - create/update AccountBackup
- `POST /accounts/{account_id}/backup/now` - trigger immediate backup
- `GET /accounts/{account_id}/backup/snapshots` - list snapshots (returns HTML partial)
- `POST /accounts/{account_id}/backup/restore/{snapshot_id}` - restore snapshot to temp account

- [ ] **Step 2: Create partial template**

Create `src/mailfallback/templates/partials/account_backup.html`:
- If no backup configured: dropdown to select destination + schedule + retention + "Enable Backup" button
- If configured: status badge, last backup time, "Backup Now" button, snapshot list, restore buttons
- Snapshot list fetched via HTMX from the snapshots endpoint

- [ ] **Step 3: Add section to account detail**

In `src/mailfallback/templates/account_detail.html`, add a collapsible section "Offsite Backup" after the Sync History section. Include the partial.

- [ ] **Step 4: Pass backup data in account_detail route**

In `src/mailfallback/routers/ui_accounts.py` `account_detail()`, query AccountBackup and BackupDestination, pass to template context.

- [ ] **Step 5: Run tests, lint, commit**

```bash
uv run ruff check src/ tests/ --fix && uv run ruff format src/ tests/
uv run pytest tests/ -n auto --tb=short
git add -A && git commit -m "feat: account backup section with configure, trigger, snapshots, restore"
```

---

### Task 6: Backup Scheduler Integration

**Files:**
- Modify: `src/mailfallback/services/scheduler.py`
- Modify: `src/mailfallback/app.py`

- [ ] **Step 1: Add backup scheduling to scheduler.py**

Add a `_run_scheduled_backup` function (same pattern as `_run_scheduled_sync`) and a `backup_scheduler_jobs` function that registers APScheduler jobs for each enabled AccountBackup.

```python
def _run_scheduled_backup(account_backup_id: str) -> None:
    from mailfallback.services.backup_worker import submit_backup
    submit_backup(account_backup_id)


def backup_scheduler_jobs(db: Session) -> None:
    from mailfallback.models import AccountBackup
    existing_job_ids = {j.id for j in scheduler.get_jobs()}
    active_backups = db.query(AccountBackup).filter(AccountBackup.enabled.is_(True)).all()

    for ab in active_backups:
        job_id = f"backup-{ab.id}"
        trigger = CronTrigger.from_crontab(ab.schedule)
        if job_id in existing_job_ids:
            scheduler.reschedule_job(job_id, trigger=trigger)
        else:
            scheduler.add_job(
                _run_scheduled_backup, trigger,
                args=[ab.id], id=job_id, replace_existing=True,
            )

    active_ids = {f"backup-{ab.id}" for ab in active_backups}
    for job_id in existing_job_ids:
        if job_id.startswith("backup-") and job_id not in active_ids:
            scheduler.remove_job(job_id)
```

- [ ] **Step 2: Call from app startup**

In `src/mailfallback/app.py` lifespan, after `start_scheduler(db)`:
```python
from mailfallback.services.scheduler import backup_scheduler_jobs
backup_scheduler_jobs(db)
```

- [ ] **Step 3: Add shutdown**

In `src/mailfallback/app.py` lifespan shutdown, after `shutdown_sync_executor()`:
```python
from mailfallback.services.backup_worker import shutdown_backup_executor
shutdown_backup_executor()
```

- [ ] **Step 4: Run tests, lint, commit**

```bash
uv run ruff check src/ tests/ --fix && uv run ruff format src/ tests/
uv run pytest tests/ -n auto --tb=short
git add -A && git commit -m "feat: backup scheduler integration"
```

---

### Task 7: System Status Bar + Backup Restore Badge

**Files:**
- Modify: `src/mailfallback/routers/ui.py`
- Modify: `src/mailfallback/templates/partials/system_status.html`
- Modify: `src/mailfallback/templates/partials/accounts_table.html`

- [ ] **Step 1: Add backup status to system status endpoint**

In `src/mailfallback/routers/ui.py` `system_status_partial()`, query active backups:
```python
from mailfallback.models import AccountBackup, BackupStatus
active_backups = db.query(AccountBackup).filter(AccountBackup.last_status == BackupStatus.running).count()
```

Add to `has_activity` check and pass to template.

- [ ] **Step 2: Add backup badge to system_status.html**

Add a fifth badge after restore:
```html
<span class="status-badge {% if active_backups > 0 %}status-active{% else %}status-neutral{% endif %}"
      onclick="toggleStatusDetail('backup')">
    <i data-lucide="cloud-upload" class="icon-sm"></i>
    {{ active_backups }} backup{{ 's' if active_backups != 1 else '' }}
</span>
```

- [ ] **Step 3: Add restore badge to accounts table**

In `src/mailfallback/templates/partials/accounts_table.html`, after the existing status badges, add:
```html
{% if account.suspended and account.name.startswith("Backup ") %}
<span class="badge badge-syncing"><i data-lucide="cloud-download" class="icon-sm"></i> Backup Restore</span>
{% endif %}
```

- [ ] **Step 4: Run tests, lint, commit**

```bash
uv run ruff check src/ tests/ --fix && uv run ruff format src/ tests/
uv run pytest tests/ -n auto --tb=short
git add -A && git commit -m "feat: backup status in system bar and restore badge"
```

---

### Task 8: Dockerfile + Integration Test

**Files:**
- Modify: `docker/Dockerfile`

- [ ] **Step 1: Add restic to Dockerfile**

In `docker/Dockerfile`, add `restic` to the apt-get install line:
```dockerfile
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       isync ca-certificates curl restic libsasl2-modules-kdexoauth2 \
```

- [ ] **Step 2: Apply migration**

```bash
docker compose up -d --build
docker compose exec mailfallback uv run alembic upgrade head
```

- [ ] **Step 3: Verify restic is available**

```bash
docker compose exec mailfallback restic version
```

- [ ] **Step 4: Run full test suite**

```bash
uv run ruff check src/ tests/ --fix && uv run ruff format src/ tests/
uv run pytest tests/ -n auto -v --tb=short
```

- [ ] **Step 5: Final commit**

```bash
git add -A
git commit -m "feat: offsite backup with restic - full implementation

Per-account backup to S3 or local storage via restic.
Scheduled backups with retention presets (light/standard/full/custom).
Restore creates temporary read-only account for cherry-pick restore.
Admin UI for backup destinations and per-account configuration."
```
