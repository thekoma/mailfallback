# S3 Repository Management Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Clean repository connection testing, bucket inventory with orphan attach, encrypted full-config backup into the repository, and disaster-recovery config restore.

**Architecture:** restic stays the storage engine; boto3 is added for the two things restic cannot do (probe without side effects, list bucket prefixes). Config backups are scrypt+Fernet-encrypted JSON exports stored as restic snapshots under a reserved `__mfb_config__` prefix. A new `repository_attachments` table maps orphan prefixes to accounts as read-only restore sources.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy, Alembic, boto3, restic (subprocess, mocked in tests), HTMX, Jinja2.

**Spec:** `docs/superpowers/specs/2026-06-10-s3-repo-management-design.md`

**Branch:** all work happens on `feat/repo-s3` off `main`.

**Conventions used throughout:**
- Tests run with `uv run pytest tests/<file> -n auto -v` (parallel; per project rule).
- Admin route tests follow the `tests/test_audit_ui.py` pattern: `client`, `db_session`, `default_store` fixtures from `conftest.py`; log in via `client.post("/api/auth/login", json={"username": ..., "password": ...})`.
- Repository fixture fields hold already-encrypted placeholder strings (existing tests use e.g. `"enc-bucket"`); when a test needs real decryption, encrypt with `encrypt_credentials(value, settings.secret_key)` first.
- Model change + its Alembic migration MUST be in the same commit (pre-commit drift hook).

---

### Task 0: Branch + boto3 dependency

**Files:**
- Modify: `pyproject.toml` (dependencies list, line ~10)

- [ ] **Step 1: Create the branch**

```bash
git checkout -b feat/repo-s3
```

- [ ] **Step 2: Add boto3 to dependencies**

In `pyproject.toml`, in the `dependencies = [` list, after `"psycopg2-binary>=2.9",` add:

```toml
    "boto3>=1.35",
```

- [ ] **Step 3: Lock and sync**

Run: `uv lock && uv sync --all-extras`
Expected: lockfile updated, boto3 installed.

- [ ] **Step 4: Sanity check**

Run: `uv run python -c "import boto3; print(boto3.__version__)"`
Expected: a version string ≥ 1.35.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "build: add boto3 for S3 probing and inventory"
```

---

### Task 1: `s3_probe` service

**Files:**
- Create: `src/mailfallback/services/s3_probe.py`
- Test: `tests/test_s3_probe.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_s3_probe.py`:

```python
"""Tests for s3_probe — boto3 mocked, no network."""

import os
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError

from mailfallback.config import settings
from mailfallback.models import BackendType, Repository
from mailfallback.security import encrypt_credentials
from mailfallback.services import s3_probe


def _enc(value: str) -> str:
    return encrypt_credentials(value, settings.secret_key)


@pytest.fixture
def s3_destination():
    return Repository(
        name="probe-s3",
        backend_type=BackendType.s3,
        s3_endpoint=_enc("https://s3.example.com"),
        s3_bucket=_enc("mfb-bucket"),
        s3_access_key=_enc("AKIA123"),
        s3_secret_key=_enc("sekrit"),
        restic_password=_enc("resticpass"),
        insecure_tls=False,
    )


@pytest.fixture
def local_destination(tmp_path):
    return Repository(
        name="probe-local",
        backend_type=BackendType.local,
        local_path=_enc(str(tmp_path / "repo")),
        restic_password=_enc("resticpass"),
    )


class TestProbeS3:
    @patch("mailfallback.services.s3_probe.boto3")
    def test_success_puts_and_deletes_probe_object(self, mock_boto3, s3_destination):
        client = MagicMock()
        client.list_objects_v2.return_value = {"Contents": []}
        mock_boto3.client.return_value = client

        result = s3_probe.probe(s3_destination)

        assert result["ok"] is True
        assert client.put_object.call_count == 1
        put_kwargs = client.put_object.call_args.kwargs
        assert put_kwargs["Bucket"] == "mfb-bucket"
        assert put_kwargs["Key"].startswith(".mfb-probe-")
        delete_kwargs = client.delete_object.call_args.kwargs
        assert delete_kwargs["Key"] == put_kwargs["Key"]

    @patch("mailfallback.services.s3_probe.boto3")
    def test_failure_returns_error(self, mock_boto3, s3_destination):
        client = MagicMock()
        client.put_object.side_effect = ClientError(
            {"Error": {"Code": "AccessDenied", "Message": "denied"}}, "PutObject"
        )
        mock_boto3.client.return_value = client

        result = s3_probe.probe(s3_destination)

        assert result["ok"] is False
        assert "AccessDenied" in result["error"]

    @patch("mailfallback.services.s3_probe.boto3")
    def test_success_cleans_legacy_junk(self, mock_boto3, s3_destination):
        client = MagicMock()
        client.list_objects_v2.return_value = {
            "Contents": [{"Key": "__mfb_connection_test__/config"}]
        }
        mock_boto3.client.return_value = client

        result = s3_probe.probe(s3_destination)

        assert result["ok"] is True
        client.delete_objects.assert_called_once()
        deleted = client.delete_objects.call_args.kwargs["Delete"]["Objects"]
        assert deleted == [{"Key": "__mfb_connection_test__/config"}]

    @patch("mailfallback.services.s3_probe.boto3")
    def test_insecure_tls_disables_verify(self, mock_boto3, s3_destination):
        s3_destination.insecure_tls = True
        client = MagicMock()
        client.list_objects_v2.return_value = {"Contents": []}
        mock_boto3.client.return_value = client

        s3_probe.probe(s3_destination)

        assert mock_boto3.client.call_args.kwargs["verify"] is False


class TestProbeLocal:
    def test_success_creates_and_removes_probe_file(self, local_destination, tmp_path):
        result = s3_probe.probe(local_destination)

        assert result["ok"] is True
        repo_dir = tmp_path / "repo"
        assert repo_dir.is_dir()
        assert not any(f.name.startswith(".mfb-probe-") for f in repo_dir.iterdir())

    def test_unwritable_path_returns_error(self, tmp_path):
        target = tmp_path / "ro"
        target.mkdir()
        os.chmod(target, 0o500)
        dest = Repository(
            name="probe-ro",
            backend_type=BackendType.local,
            local_path=_enc(str(target)),
            restic_password=_enc("x"),
        )
        try:
            result = s3_probe.probe(dest)
        finally:
            os.chmod(target, 0o700)

        assert result["ok"] is False
        assert result["error"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_s3_probe.py -n auto -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'mailfallback.services.s3_probe'`

- [ ] **Step 3: Implement `s3_probe.py`**

Create `src/mailfallback/services/s3_probe.py`:

```python
"""Connection probing for backup repositories — validates reachability and
write permission without restic side effects (no junk repos in the bucket)."""

import logging
import os
import uuid

import boto3
from botocore.client import Config as BotoConfig
from botocore.exceptions import BotoCoreError, ClientError

from mailfallback.config import settings
from mailfallback.models import Repository
from mailfallback.security import decrypt_credentials

logger = logging.getLogger(__name__)

# Prefix used by pre-2026-06 versions' `restic init` connection test.
LEGACY_TEST_PREFIX = "__mfb_connection_test__/"


def s3_client(destination: Repository):
    """Build a boto3 S3 client from a Repository's (encrypted) settings."""
    endpoint = decrypt_credentials(destination.s3_endpoint, settings.secret_key)
    access_key = decrypt_credentials(destination.s3_access_key, settings.secret_key)
    secret_key = decrypt_credentials(destination.s3_secret_key, settings.secret_key)
    if not endpoint.startswith(("http://", "https://")):
        endpoint = f"https://{endpoint}"
    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        verify=not destination.insecure_tls,
        config=BotoConfig(connect_timeout=10, read_timeout=30, retries={"max_attempts": 1}),
    )


def bucket_name(destination: Repository) -> str:
    return decrypt_credentials(destination.s3_bucket, settings.secret_key)


def probe(destination: Repository) -> dict:
    """Test reachability + write permission. Returns {"ok": bool, "error": str|None}.

    Does NOT validate the restic password: on a new prefix the password
    *defines* the repository, there is nothing to check it against.
    """
    if destination.backend_type.value == "s3":
        return _probe_s3(destination)
    return _probe_local(destination)


def _probe_s3(destination: Repository) -> dict:
    key = f".mfb-probe-{uuid.uuid4()}"
    try:
        client = s3_client(destination)
        bucket = bucket_name(destination)
        client.put_object(Bucket=bucket, Key=key, Body=b"mfb-probe")
        client.delete_object(Bucket=bucket, Key=key)
    except (ClientError, BotoCoreError, ValueError) as e:
        return {"ok": False, "error": str(e)[:200]}
    _cleanup_legacy_junk(client, bucket)
    return {"ok": True, "error": None}


def _cleanup_legacy_junk(client, bucket: str) -> None:
    """Best-effort removal of leftover `__mfb_connection_test__` objects."""
    try:
        resp = client.list_objects_v2(Bucket=bucket, Prefix=LEGACY_TEST_PREFIX)
        objs = [{"Key": o["Key"]} for o in resp.get("Contents", [])]
        if objs:
            client.delete_objects(Bucket=bucket, Delete={"Objects": objs})
            logger.info("Removed %d legacy connection-test objects", len(objs))
    except Exception:
        logger.debug("Legacy junk cleanup skipped", exc_info=True)


def _probe_local(destination: Repository) -> dict:
    path = decrypt_credentials(destination.local_path, settings.secret_key)
    probe_file = os.path.join(path, f".mfb-probe-{uuid.uuid4()}")
    try:
        os.makedirs(path, exist_ok=True)
        with open(probe_file, "w") as f:
            f.write("mfb-probe")
        os.remove(probe_file)
    except OSError as e:
        return {"ok": False, "error": str(e)[:200]}
    return {"ok": True, "error": None}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_s3_probe.py -n auto -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/mailfallback/services/s3_probe.py tests/test_s3_probe.py
git commit -m "feat(backup): boto3 connection probe without restic side effects"
```

---

### Task 2: Rewire `test_destination` to the probe

**Files:**
- Modify: `src/mailfallback/services/restic_service.py:91-101` (`test_destination`)
- Test: `tests/test_connection_test.py` (existing — update expectations)

- [ ] **Step 1: Read the existing tests**

Read `tests/test_connection_test.py` in full. Identify tests that mock `_run_restic` (or `subprocess`) for `test_destination` — they will need updating to mock `s3_probe.probe` instead. Note: that file may also test the *IMAP* connection test (`imap_check`); leave those untouched. If `test_destination` tests live elsewhere (search: `grep -rn "test_destination" tests/`), update that file instead.

- [ ] **Step 2: Update/write the failing tests**

Replace the `test_destination` tests with:

```python
from unittest.mock import patch

from mailfallback.services.restic_service import test_destination


@patch("mailfallback.services.s3_probe.probe")
def test_test_destination_delegates_to_probe(mock_probe, ...existing destination fixture...):
    mock_probe.return_value = {"ok": True, "error": None}
    result = test_destination(destination)
    assert result == {"ok": True, "error": None}
    mock_probe.assert_called_once_with(destination)


@patch("mailfallback.services.s3_probe.probe")
def test_test_destination_propagates_failure(mock_probe, ...existing destination fixture...):
    mock_probe.return_value = {"ok": False, "error": "AccessDenied"}
    result = test_destination(destination)
    assert result["ok"] is False
    assert result["error"] == "AccessDenied"
```

(Use whatever Repository fixture the file already has; `...existing destination fixture...` means reuse it, not a placeholder in the code you write.)

- [ ] **Step 3: Run to verify the new tests fail**

Run: `uv run pytest tests/test_connection_test.py -n auto -v` (or the file found in Step 1)
Expected: new tests FAIL (probe not called yet).

- [ ] **Step 4: Rewrite `test_destination`**

In `src/mailfallback/services/restic_service.py` replace the function body (lines ~91-101):

```python
def test_destination(destination: Repository) -> dict:
    """Test connectivity to a backup destination. Returns {ok: bool, error: str}.

    Delegates to s3_probe: validates reachability and write permission with a
    probe object (S3) or a probe file (local), creating no restic repositories.
    """
    from mailfallback.services import s3_probe

    return s3_probe.probe(destination)
```

The old `test_id = "__mfb_connection_test__"` logic is deleted.

- [ ] **Step 5: Run the full suite for regressions**

Run: `uv run pytest tests/ -n auto -q`
Expected: all green (511+ tests).

- [ ] **Step 6: Commit**

```bash
git add src/mailfallback/services/restic_service.py tests/
git commit -m "fix(backup): connection test no longer leaves junk repos in the bucket"
```

---

### Task 3: Test-before-commit on create, re-test on edit

**Files:**
- Modify: `src/mailfallback/routers/ui_backup.py:80-153` (create), `:190-236` (edit)
- Test: `tests/test_ui_backup_admin.py` (create if missing; check `grep -rln "admin/backup" tests/` first — as of writing there is none)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_ui_backup_admin.py`:

```python
"""Admin repository create/edit flows — probe mocked."""

from unittest.mock import patch

from mailfallback.config import settings
from mailfallback.models import BackendType, Repository, UserRole
from mailfallback.security import decrypt_credentials
from mailfallback.services.user_service import create_user


def _login_admin(client, db_session, default_store):
    user = create_user(db_session, "admin", "pass", UserRole.admin, store_id=default_store.id)
    client.post("/api/auth/login", json={"username": "admin", "password": "pass"})  # pragma: allowlist secret
    return user


S3_FORM = {
    "name": "repo1",
    "backend_type": "s3",
    "restic_password": "resticpass",  # pragma: allowlist secret
    "s3_endpoint": "https://s3.example.com",
    "s3_bucket": "bucket",
    "s3_access_key": "ak",
    "s3_secret_key": "sk",  # pragma: allowlist secret
}


class TestCreate:
    @patch("mailfallback.services.s3_probe.probe")
    def test_failed_probe_saves_nothing(self, mock_probe, client, db_session, default_store):
        _login_admin(client, db_session, default_store)
        mock_probe.return_value = {"ok": False, "error": "AccessDenied"}

        resp = client.post("/admin/backup/new", data=S3_FORM, follow_redirects=False)

        assert resp.status_code == 303
        assert db_session.query(Repository).count() == 0

    @patch("mailfallback.services.s3_probe.probe")
    def test_successful_probe_saves(self, mock_probe, client, db_session, default_store):
        _login_admin(client, db_session, default_store)
        mock_probe.return_value = {"ok": True, "error": None}

        resp = client.post("/admin/backup/new", data=S3_FORM, follow_redirects=False)

        assert resp.status_code == 303
        repo = db_session.query(Repository).one()
        assert repo.name == "repo1"
        assert repo.backend_type == BackendType.s3
        # the probe ran against the not-yet-committed object
        mock_probe.assert_called_once()


class TestEdit:
    @patch("mailfallback.services.s3_probe.probe")
    def test_failed_probe_rolls_back_changes(self, mock_probe, client, db_session, default_store):
        _login_admin(client, db_session, default_store)
        mock_probe.return_value = {"ok": True, "error": None}
        client.post("/admin/backup/new", data=S3_FORM, follow_redirects=False)
        repo = db_session.query(Repository).one()
        old_bucket = repo.s3_bucket

        mock_probe.return_value = {"ok": False, "error": "NoSuchBucket"}
        resp = client.post(
            f"/admin/backup/{repo.id}/edit",
            data={"name": "renamed", "s3_bucket": "other-bucket"},
            follow_redirects=False,
        )

        assert resp.status_code == 303
        db_session.expire_all()
        repo = db_session.query(Repository).one()
        assert repo.name == "repo1"
        assert repo.s3_bucket == old_bucket

    @patch("mailfallback.services.s3_probe.probe")
    def test_successful_probe_commits_changes(self, mock_probe, client, db_session, default_store):
        _login_admin(client, db_session, default_store)
        mock_probe.return_value = {"ok": True, "error": None}
        client.post("/admin/backup/new", data=S3_FORM, follow_redirects=False)
        repo = db_session.query(Repository).one()

        resp = client.post(
            f"/admin/backup/{repo.id}/edit",
            data={"name": "renamed", "s3_bucket": "other-bucket"},
            follow_redirects=False,
        )

        assert resp.status_code == 303
        db_session.expire_all()
        repo = db_session.query(Repository).one()
        assert repo.name == "renamed"
        assert decrypt_credentials(repo.s3_bucket, settings.secret_key) == "other-bucket"
```

- [ ] **Step 2: Run to verify failures**

Run: `uv run pytest tests/test_ui_backup_admin.py -n auto -v`
Expected: `test_failed_probe_saves_nothing` FAILS today only if the legacy create flow misbehaves — actually the current code deletes after a failed test, so it may pass; the EDIT tests must FAIL (edit never re-tests). Confirm at least the edit tests fail.

- [ ] **Step 3: Rework the create flow**

In `admin_create_backup_destination` (ui_backup.py): construct `dest` exactly as today **but** pass the enum (`backend_type=BackendType(backend_type)`, import `BackendType` from `mailfallback.models`) so `dest.backend_type.value` works pre-flush. Then replace the current `db.add(dest)`/`db.commit()`/test/delete block (lines ~129-141) with test-first:

```python
    from mailfallback.services.restic_service import test_destination

    test_result = test_destination(dest)
    if not test_result["ok"]:
        error_msg = test_result.get("error", "Unknown error")
        request.session["flash_error"] = f"Connection test failed: {error_msg}"
        return RedirectResponse("/admin/backup", status_code=303)

    db.add(dest)
    db.commit()
    db.refresh(dest)
```

(The audit `log_action` and success flash stay as they are, after the commit.)

- [ ] **Step 4: Rework the edit flow**

In `admin_edit_backup_destination`, after all form fields are applied to `dest` (after the `insecure_tls` line, before `db.commit()`), insert:

```python
    from mailfallback.services.restic_service import test_destination

    test_result = test_destination(dest)
    if not test_result["ok"]:
        db.rollback()
        error_msg = test_result.get("error", "Unknown error")
        request.session["flash_error"] = f"Connection test failed — changes not saved: {error_msg}"
        return RedirectResponse("/admin/backup", status_code=303)

    db.commit()
```

- [ ] **Step 5: Run tests**

Run: `uv run pytest tests/test_ui_backup_admin.py tests/test_backup_worker.py -n auto -v`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add src/mailfallback/routers/ui_backup.py tests/test_ui_backup_admin.py
git commit -m "fix(backup): repository create tests before commit, edit re-tests with rollback"
```

---

### Task 4: Inline HTMX feedback for the Test button

**Files:**
- Modify: `src/mailfallback/routers/ui_backup.py:239-260` (`admin_test_backup_destination`)
- Modify: `src/mailfallback/templates/admin_backup.html:73-77` (test form)
- Create: `src/mailfallback/templates/partials/repo_test_result.html`
- Test: `tests/test_ui_backup_admin.py` (extend)

Scope note: create/edit keep the flash-after-redirect pattern (they navigate anyway); the per-row **Test** button is the one that becomes inline, because a full reload to show "OK" is the jarring case.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_ui_backup_admin.py`)

```python
class TestInlineTest:
    @patch("mailfallback.services.s3_probe.probe")
    def test_htmx_test_returns_partial_ok(self, mock_probe, client, db_session, default_store):
        _login_admin(client, db_session, default_store)
        mock_probe.return_value = {"ok": True, "error": None}
        client.post("/admin/backup/new", data=S3_FORM, follow_redirects=False)
        repo = db_session.query(Repository).one()

        resp = client.post(f"/admin/backup/{repo.id}/test", headers={"HX-Request": "true"})

        assert resp.status_code == 200
        assert "Connection OK" in resp.text

    @patch("mailfallback.services.s3_probe.probe")
    def test_htmx_test_returns_partial_error(self, mock_probe, client, db_session, default_store):
        _login_admin(client, db_session, default_store)
        mock_probe.return_value = {"ok": True, "error": None}
        client.post("/admin/backup/new", data=S3_FORM, follow_redirects=False)
        repo = db_session.query(Repository).one()
        mock_probe.return_value = {"ok": False, "error": "AccessDenied"}

        resp = client.post(f"/admin/backup/{repo.id}/test", headers={"HX-Request": "true"})

        assert resp.status_code == 200
        assert "AccessDenied" in resp.text

    @patch("mailfallback.services.s3_probe.probe")
    def test_non_htmx_test_still_redirects(self, mock_probe, client, db_session, default_store):
        _login_admin(client, db_session, default_store)
        mock_probe.return_value = {"ok": True, "error": None}
        client.post("/admin/backup/new", data=S3_FORM, follow_redirects=False)
        repo = db_session.query(Repository).one()

        resp = client.post(f"/admin/backup/{repo.id}/test", follow_redirects=False)

        assert resp.status_code == 303
```

- [ ] **Step 2: Run to verify failures**

Run: `uv run pytest tests/test_ui_backup_admin.py::TestInlineTest -n auto -v`
Expected: FAIL (route always redirects).

- [ ] **Step 3: Create the partial**

`src/mailfallback/templates/partials/repo_test_result.html`:

```html
{% if ok %}
<span class="stats-pill"><span class="stats-dot stats-dot-ok"></span> Connection OK</span>
{% else %}
<span class="stats-pill"><span class="stats-dot stats-dot-error"></span> {{ error }}</span>
{% endif %}
```

- [ ] **Step 4: Update the route**

Replace the body tail of `admin_test_backup_destination` (after `result = test_destination(dest)`):

```python
    if request.headers.get("HX-Request"):
        return templates.TemplateResponse(
            request=request,
            name="partials/repo_test_result.html",
            context={"ok": result["ok"], "error": result.get("error")},
        )
    if result["ok"]:
        request.session["flash_success"] = f"{dest.name}: connection OK"
    else:
        error_msg = result.get("error", "Unknown error")
        request.session["flash_error"] = f"{dest.name}: {error_msg}"
    return RedirectResponse("/admin/backup", status_code=303)
```

(`templates` is already imported/configured in this router — check the top of the file; it follows the same pattern as the other UI routers. The route's `response_class` must allow HTML — add `response_class=HTMLResponse` is NOT needed since TemplateResponse sets it.)

- [ ] **Step 5: Update the template**

In `admin_backup.html`, replace the test form (lines ~73-77) with:

```html
                    <span id="repo-test-result-{{ dest.id }}" class="text-small"></span>
                    <button class="icon-btn" title="Test connection"
                            hx-post="/admin/backup/{{ dest.id }}/test"
                            hx-target="#repo-test-result-{{ dest.id }}"
                            hx-swap="innerHTML">
                        <i data-lucide="plug-zap" class="icon-md"></i>
                    </button>
```

- [ ] **Step 6: Run tests + lint**

Run: `uv run pytest tests/test_ui_backup_admin.py -n auto -v && uv run ruff check src/ tests/`
Expected: PASS, no lint errors.

- [ ] **Step 7: Commit**

```bash
git add src/mailfallback/routers/ui_backup.py src/mailfallback/templates/
git add tests/test_ui_backup_admin.py
git commit -m "feat(backup): inline HTMX feedback for repository connection test"
```

---

### Task 5: Models + migration 015 (attachments table, config-backup columns)

**Files:**
- Modify: `src/mailfallback/models.py` (after the `Repository` class, ~line 346; and inside `Repository`)
- Create: `alembic/versions/015_repo_attachments_and_config_backup.py`
- Test: `tests/test_models.py` (extend)

**IMPORTANT:** model + migration in ONE commit (drift hook).

- [ ] **Step 1: Write the failing test** (append to `tests/test_models.py`)

```python
def test_repository_attachment_unique_per_prefix(db_session):
    from sqlalchemy.exc import IntegrityError

    from mailfallback.models import Account, MailStore, Repository, RepositoryAttachment

    store = MailStore(name="s", path="/data/m")
    db_session.add(store)
    db_session.flush()
    acc = Account(name="a", imap_host="h", maildir_path="/data/m/x", store_id=store.id)
    repo = Repository(name="r", backend_type="s3", restic_password="enc")
    db_session.add_all([acc, repo])
    db_session.flush()

    db_session.add(RepositoryAttachment(repository_id=repo.id, account_id=acc.id, prefix="old-uuid"))
    db_session.commit()

    db_session.add(RepositoryAttachment(repository_id=repo.id, account_id=acc.id, prefix="old-uuid"))
    try:
        db_session.commit()
        raised = False
    except IntegrityError:
        db_session.rollback()
        raised = True
    assert raised


def test_repository_config_backup_defaults(db_session):
    from mailfallback.models import Repository

    repo = Repository(name="r2", backend_type="local", local_path="enc", restic_password="enc")
    db_session.add(repo)
    db_session.commit()
    db_session.refresh(repo)
    assert repo.config_backup_enabled is False
    assert repo.config_backup_passphrase is None
    assert repo.last_config_backup_at is None
    assert repo.last_config_backup_status is None
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_models.py -n auto -v -k "repository_attachment or config_backup_defaults"`
Expected: FAIL (`RepositoryAttachment` not importable / columns missing).

- [ ] **Step 3: Add the model changes**

Inside `class Repository` (models.py, after `insecure_tls`, before `created_at`):

```python
    config_backup_enabled = Column(Boolean, nullable=False, default=False, server_default="false")
    config_backup_passphrase = Column(String, nullable=True)  # Fernet-encrypted at rest
    last_config_backup_at = Column(DateTime(timezone=True), nullable=True)
    last_config_backup_status = Column(String, nullable=True)  # "ok" | "failed: <msg>"
```

After the `BackupDestination = Repository` alias block add:

```python
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
    created_at = Column(DateTime(timezone=True), default=_utcnow)

    __table_args__ = (
        UniqueConstraint("repository_id", "prefix", name="uq_repo_attachment_prefix"),
    )

    repository = relationship("Repository", backref="attachments")
    account = relationship("Account", backref="repo_attachments")
```

Add `UniqueConstraint` to the existing `from sqlalchemy import ...` block at the top of models.py if not already imported.

- [ ] **Step 4: Write migration 015**

Create `alembic/versions/015_repo_attachments_and_config_backup.py`:

```python
"""repository attachments + config backup columns

- repository_attachments: orphan restic prefixes attached to accounts as
  read-only restore sources (unique per repository+prefix).
- backup_destinations: config_backup_enabled/passphrase + last run status,
  for the encrypted MFB configuration backup feature.

Revision ID: 015
Revises: 014
Create Date: 2026-06-10
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "015"
down_revision: str | Sequence[str] | None = "014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "repository_attachments",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column(
            "repository_id",
            sa.String(),
            sa.ForeignKey("backup_destinations.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "account_id",
            sa.String(),
            sa.ForeignKey("accounts.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("prefix", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("repository_id", "prefix", name="uq_repo_attachment_prefix"),
    )
    op.add_column(
        "backup_destinations",
        sa.Column(
            "config_backup_enabled", sa.Boolean(), nullable=False, server_default="false"
        ),
    )
    op.add_column(
        "backup_destinations", sa.Column("config_backup_passphrase", sa.String(), nullable=True)
    )
    op.add_column(
        "backup_destinations",
        sa.Column("last_config_backup_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "backup_destinations",
        sa.Column("last_config_backup_status", sa.String(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("backup_destinations", "last_config_backup_status")
    op.drop_column("backup_destinations", "last_config_backup_at")
    op.drop_column("backup_destinations", "config_backup_passphrase")
    op.drop_column("backup_destinations", "config_backup_enabled")
    op.drop_table("repository_attachments")
```

- [ ] **Step 5: Run model tests + alembic sync test**

Run: `uv run pytest tests/test_models.py tests/test_alembic_sync.py -n auto -v`
Expected: PASS (the alembic sync test verifies model/migration agreement).

- [ ] **Step 6: Commit (model + migration together)**

```bash
git add src/mailfallback/models.py alembic/versions/015_repo_attachments_and_config_backup.py tests/test_models.py
git commit -m "feat(backup): RepositoryAttachment model and config-backup columns"
```

---

### Task 6: `repo_inventory` service

**Files:**
- Create: `src/mailfallback/services/repo_inventory.py`
- Test: `tests/test_repo_inventory.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_repo_inventory.py`:

```python
"""Tests for repo_inventory — boto3 and restic mocked."""

from unittest.mock import MagicMock, patch

import pytest

from mailfallback.config import settings
from mailfallback.models import Account, BackendType, MailStore, Repository, RepositoryAttachment
from mailfallback.security import encrypt_credentials
from mailfallback.services import repo_inventory


def _enc(value: str) -> str:
    return encrypt_credentials(value, settings.secret_key)


@pytest.fixture
def store(db_session):
    s = MailStore(name="default", path="/data/mailboxes")
    db_session.add(s)
    db_session.commit()
    return s


@pytest.fixture
def account(db_session, store):
    a = Account(
        name="acc", imap_host="h", maildir_path="/data/mailboxes/u1", store_id=store.id
    )
    db_session.add(a)
    db_session.commit()
    return a


@pytest.fixture
def s3_repo(db_session):
    r = Repository(
        name="s3repo",
        backend_type=BackendType.s3,
        s3_endpoint=_enc("https://s3.example.com"),
        s3_bucket=_enc("bucket"),
        s3_access_key=_enc("ak"),
        s3_secret_key=_enc("sk"),
        restic_password=_enc("rp"),
    )
    db_session.add(r)
    db_session.commit()
    return r


class TestListPrefixes:
    @patch("mailfallback.services.s3_probe.s3_client")
    def test_s3_lists_common_prefixes_paginated(self, mock_client_fn, s3_repo):
        client = MagicMock()
        client.list_objects_v2.side_effect = [
            {
                "CommonPrefixes": [{"Prefix": "aaa/"}, {"Prefix": "bbb/"}],
                "IsTruncated": True,
                "NextContinuationToken": "tok",
            },
            {"CommonPrefixes": [{"Prefix": "ccc/"}], "IsTruncated": False},
        ]
        mock_client_fn.return_value = client

        prefixes = repo_inventory.list_prefixes(s3_repo)

        assert prefixes == ["aaa", "bbb", "ccc"]

    def test_local_lists_subdirectories(self, db_session, tmp_path):
        (tmp_path / "one").mkdir()
        (tmp_path / "two").mkdir()
        (tmp_path / "stray-file").write_text("x")
        repo = Repository(
            name="loc",
            backend_type=BackendType.local,
            local_path=_enc(str(tmp_path)),
            restic_password=_enc("rp"),
        )
        db_session.add(repo)
        db_session.commit()

        assert repo_inventory.list_prefixes(repo) == ["one", "two"]


class TestClassify:
    def test_classification_kinds(self, db_session, s3_repo, account):
        db_session.add(
            RepositoryAttachment(
                repository_id=s3_repo.id, account_id=account.id, prefix="old-prefix"
            )
        )
        db_session.commit()

        prefixes = [account.id, "__mfb_config__", "old-prefix", "stranger"]
        entries = repo_inventory.classify(db_session, s3_repo, prefixes)

        kinds = {e["prefix"]: e["kind"] for e in entries}
        assert kinds[account.id] == "account"
        assert kinds["__mfb_config__"] == "config"
        assert kinds["old-prefix"] == "attached"
        assert kinds["stranger"] == "orphan"
        acc_entry = next(e for e in entries if e["prefix"] == account.id)
        assert acc_entry["account"].id == account.id


class TestPrefixDetail:
    @patch("mailfallback.services.repo_inventory.restic_service")
    def test_detail_returns_snapshot_count_and_latest(self, mock_restic, s3_repo):
        mock_restic.list_snapshots.return_value = [
            {"short_id": "ab12", "time": "2026-06-09T03:00:00Z"},
            {"short_id": "cd34", "time": "2026-06-08T03:00:00Z"},
        ]

        detail = repo_inventory.prefix_detail(s3_repo, "stranger")

        assert detail["ok"] is True
        assert detail["snapshot_count"] == 2
        assert detail["latest"] == "2026-06-09T03:00:00Z"
        mock_restic.list_snapshots.assert_called_once_with(s3_repo, "stranger")

    @patch("mailfallback.services.repo_inventory.restic_service")
    def test_detail_reports_error(self, mock_restic, s3_repo):
        mock_restic.list_snapshots.side_effect = RuntimeError("wrong password")

        detail = repo_inventory.prefix_detail(s3_repo, "stranger")

        assert detail["ok"] is False
        assert "wrong password" in detail["error"]
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_repo_inventory.py -n auto -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement `repo_inventory.py`**

```python
"""Repository content inventory: list restic sub-repo prefixes and classify
them against the database (account / config / attached / orphan)."""

import logging
import os

from sqlalchemy.orm import Session

from mailfallback.config import settings
from mailfallback.models import Account, Repository, RepositoryAttachment
from mailfallback.security import decrypt_credentials
from mailfallback.services import restic_service, s3_probe

logger = logging.getLogger(__name__)

CONFIG_PREFIX = "__mfb_config__"


def list_prefixes(destination: Repository) -> list[str]:
    """Top-level prefixes in the repository backend (= restic sub-repos)."""
    if destination.backend_type.value == "s3":
        client = s3_probe.s3_client(destination)
        bucket = s3_probe.bucket_name(destination)
        prefixes: list[str] = []
        kwargs: dict = {"Bucket": bucket, "Delimiter": "/"}
        while True:
            resp = client.list_objects_v2(**kwargs)
            prefixes.extend(
                p["Prefix"].rstrip("/") for p in resp.get("CommonPrefixes", [])
            )
            if not resp.get("IsTruncated"):
                break
            kwargs["ContinuationToken"] = resp["NextContinuationToken"]
        return sorted(prefixes)
    path = decrypt_credentials(destination.local_path, settings.secret_key)
    if not os.path.isdir(path):
        return []
    return sorted(d for d in os.listdir(path) if os.path.isdir(os.path.join(path, d)))


def classify(db: Session, destination: Repository, prefixes: list[str]) -> list[dict]:
    """Classify each prefix: account | config | attached | orphan.

    Returns [{"prefix", "kind", "account": Account|None, "attachment": RepositoryAttachment|None}].
    """
    accounts = {a.id: a for a in db.query(Account).all()}
    attachments = {
        att.prefix: att
        for att in db.query(RepositoryAttachment)
        .filter(RepositoryAttachment.repository_id == destination.id)
        .all()
    }
    entries = []
    for prefix in prefixes:
        if prefix == CONFIG_PREFIX:
            kind, account, attachment = "config", None, None
        elif prefix in accounts:
            kind, account, attachment = "account", accounts[prefix], None
        elif prefix in attachments:
            att = attachments[prefix]
            kind, account, attachment = "attached", accounts.get(att.account_id), att
        else:
            kind, account, attachment = "orphan", None, None
        entries.append(
            {"prefix": prefix, "kind": kind, "account": account, "attachment": attachment}
        )
    return entries


def prefix_detail(destination: Repository, prefix: str) -> dict:
    """Open the sub-repo with the repository's restic password and summarize.

    This is where the restic password gets genuinely validated.
    """
    try:
        snapshots = restic_service.list_snapshots(destination, prefix)
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}
    return {
        "ok": True,
        "snapshot_count": len(snapshots),
        "latest": snapshots[0]["time"] if snapshots else None,
    }
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_repo_inventory.py -n auto -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/mailfallback/services/repo_inventory.py tests/test_repo_inventory.py
git commit -m "feat(backup): repository inventory service (prefix listing + classification)"
```

---

### Task 7: Contents panel UI + attach/detach routes

**Files:**
- Modify: `src/mailfallback/routers/ui_backup.py` (new routes)
- Modify: `src/mailfallback/templates/admin_backup.html` (Contents toggle per row)
- Create: `src/mailfallback/templates/partials/repo_contents.html`
- Create: `src/mailfallback/templates/partials/repo_prefix_detail.html`
- Test: `tests/test_ui_backup_admin.py` (extend)

- [ ] **Step 1: Write the failing tests** (append to `tests/test_ui_backup_admin.py`)

```python
from mailfallback.models import Account, MailStore, RepositoryAttachment


def _mk_account(db_session, default_store, name="acc1", path="/data/m/acc1"):
    acc = Account(name=name, imap_host="h", maildir_path=path, store_id=default_store.id)
    db_session.add(acc)
    db_session.commit()
    return acc


def _mk_repo(client, db_session, default_store):
    with patch("mailfallback.services.s3_probe.probe") as mock_probe:
        mock_probe.return_value = {"ok": True, "error": None}
        client.post("/admin/backup/new", data=S3_FORM, follow_redirects=False)
    return db_session.query(Repository).one()


class TestContents:
    @patch("mailfallback.services.repo_inventory.list_prefixes")
    def test_contents_panel_classifies(self, mock_list, client, db_session, default_store):
        _login_admin(client, db_session, default_store)
        acc = _mk_account(db_session, default_store)
        repo = _mk_repo(client, db_session, default_store)
        mock_list.return_value = [acc.id, "__mfb_config__", "ghost-uuid"]

        resp = client.get(f"/admin/backup/{repo.id}/contents")

        assert resp.status_code == 200
        assert "ghost-uuid" in resp.text
        assert "Orphan" in resp.text
        assert "Attach" in resp.text

    @patch("mailfallback.services.repo_inventory.list_prefixes")
    def test_contents_error_is_rendered(self, mock_list, client, db_session, default_store):
        _login_admin(client, db_session, default_store)
        repo = _mk_repo(client, db_session, default_store)
        mock_list.side_effect = RuntimeError("boom")

        resp = client.get(f"/admin/backup/{repo.id}/contents")

        assert resp.status_code == 200
        assert "boom" in resp.text

    @patch("mailfallback.services.repo_inventory.restic_service")
    def test_prefix_detail_partial(self, mock_restic, client, db_session, default_store):
        _login_admin(client, db_session, default_store)
        repo = _mk_repo(client, db_session, default_store)
        mock_restic.list_snapshots.return_value = [
            {"short_id": "ab12", "time": "2026-06-09T03:00:00Z"}
        ]

        resp = client.get(f"/admin/backup/{repo.id}/contents/ghost-uuid/detail")

        assert resp.status_code == 200
        assert "1" in resp.text


class TestAttach:
    def test_attach_creates_row(self, client, db_session, default_store):
        _login_admin(client, db_session, default_store)
        acc = _mk_account(db_session, default_store)
        repo = _mk_repo(client, db_session, default_store)

        resp = client.post(
            f"/admin/backup/{repo.id}/attach",
            data={"prefix": "ghost-uuid", "account_id": acc.id},
            follow_redirects=False,
        )

        assert resp.status_code == 303
        att = db_session.query(RepositoryAttachment).one()
        assert att.prefix == "ghost-uuid"
        assert att.account_id == acc.id

    def test_attach_duplicate_prefix_rejected(self, client, db_session, default_store):
        _login_admin(client, db_session, default_store)
        acc = _mk_account(db_session, default_store)
        repo = _mk_repo(client, db_session, default_store)
        client.post(
            f"/admin/backup/{repo.id}/attach",
            data={"prefix": "ghost-uuid", "account_id": acc.id},
            follow_redirects=False,
        )

        resp = client.post(
            f"/admin/backup/{repo.id}/attach",
            data={"prefix": "ghost-uuid", "account_id": acc.id},
            follow_redirects=False,
        )

        assert resp.status_code == 303
        assert db_session.query(RepositoryAttachment).count() == 1

    def test_detach_deletes_row(self, client, db_session, default_store):
        _login_admin(client, db_session, default_store)
        acc = _mk_account(db_session, default_store)
        repo = _mk_repo(client, db_session, default_store)
        client.post(
            f"/admin/backup/{repo.id}/attach",
            data={"prefix": "ghost-uuid", "account_id": acc.id},
            follow_redirects=False,
        )
        att = db_session.query(RepositoryAttachment).one()

        resp = client.post(
            f"/admin/backup/attachments/{att.id}/delete", follow_redirects=False
        )

        assert resp.status_code == 303
        assert db_session.query(RepositoryAttachment).count() == 0
```

- [ ] **Step 2: Run to verify failures**

Run: `uv run pytest tests/test_ui_backup_admin.py::TestContents tests/test_ui_backup_admin.py::TestAttach -n auto -v`
Expected: FAIL (404s).

- [ ] **Step 3: Add the routes** (in `ui_backup.py`, after the test route)

```python
@router.get("/admin/backup/{dest_id}/contents", response_class=HTMLResponse)
def admin_repo_contents(dest_id: str, request: Request, db: Session = Depends(get_db)):
    user = _get_session_user(request, db)
    if not user or user.role.value != "admin":
        return RedirectResponse("/", status_code=303)
    dest = db.query(Repository).filter(Repository.id == dest_id).first()
    if not dest:
        return HTMLResponse("Repository not found", status_code=404)

    from mailfallback.services import repo_inventory

    error = None
    entries: list[dict] = []
    try:
        prefixes = repo_inventory.list_prefixes(dest)
        entries = repo_inventory.classify(db, dest, prefixes)
    except Exception as e:
        error = str(e)[:200]

    accounts = db.query(Account).order_by(Account.name).all()
    return templates.TemplateResponse(
        request=request,
        name="partials/repo_contents.html",
        context={"dest": dest, "entries": entries, "error": error, "accounts": accounts},
    )


@router.get("/admin/backup/{dest_id}/contents/{prefix}/detail", response_class=HTMLResponse)
def admin_repo_prefix_detail(
    dest_id: str, prefix: str, request: Request, db: Session = Depends(get_db)
):
    user = _get_session_user(request, db)
    if not user or user.role.value != "admin":
        return RedirectResponse("/", status_code=303)
    dest = db.query(Repository).filter(Repository.id == dest_id).first()
    if not dest:
        return HTMLResponse("Repository not found", status_code=404)

    from mailfallback.services import repo_inventory

    detail = repo_inventory.prefix_detail(dest, prefix)
    return templates.TemplateResponse(
        request=request,
        name="partials/repo_prefix_detail.html",
        context={"detail": detail},
    )


@router.post("/admin/backup/{dest_id}/attach")
async def admin_repo_attach(dest_id: str, request: Request, db: Session = Depends(get_db)):
    user = _get_session_user(request, db)
    if not user or user.role.value != "admin":
        return RedirectResponse("/", status_code=303)
    dest = db.query(Repository).filter(Repository.id == dest_id).first()
    if not dest:
        request.session["flash_error"] = "Repository not found"
        return RedirectResponse("/admin/backup", status_code=303)

    form = await request.form()
    prefix = form.get("prefix", "").strip()
    account_id = form.get("account_id", "").strip()
    account = db.query(Account).filter(Account.id == account_id).first()
    if not prefix or not account:
        request.session["flash_error"] = "Prefix and account are required"
        return RedirectResponse("/admin/backup", status_code=303)

    existing = (
        db.query(RepositoryAttachment)
        .filter(
            RepositoryAttachment.repository_id == dest_id,
            RepositoryAttachment.prefix == prefix,
        )
        .first()
    )
    if existing:
        request.session["flash_error"] = f"Prefix {prefix} is already attached"
        return RedirectResponse("/admin/backup", status_code=303)

    att = RepositoryAttachment(repository_id=dest_id, account_id=account.id, prefix=prefix)
    db.add(att)
    db.commit()
    log_action(
        db,
        user=user,
        action="backup_destination.attach",
        resource_type="backup_destination",
        resource_id=dest_id,
        resource_name=dest.name,
        details={"prefix": prefix, "account_id": account.id},
        ip_address=request.client.host if request.client else None,
    )
    request.session["flash_success"] = f"Attached {prefix} to {account.name}"
    return RedirectResponse("/admin/backup", status_code=303)


@router.post("/admin/backup/attachments/{attachment_id}/delete")
async def admin_repo_detach(attachment_id: str, request: Request, db: Session = Depends(get_db)):
    user = _get_session_user(request, db)
    if not user or user.role.value != "admin":
        return RedirectResponse("/", status_code=303)
    att = db.query(RepositoryAttachment).filter(RepositoryAttachment.id == attachment_id).first()
    if att:
        db.delete(att)
        db.commit()
        log_action(
            db,
            user=user,
            action="backup_destination.detach",
            resource_type="backup_destination",
            resource_id=att.repository_id,
            details={"prefix": att.prefix},
            ip_address=request.client.host if request.client else None,
        )
    request.session["flash_success"] = "Prefix detached"
    return RedirectResponse("/admin/backup", status_code=303)
```

Add `Account`, `RepositoryAttachment` to the models import at the top of `ui_backup.py`, and `HTMLResponse` to the fastapi.responses import if missing. Check how `log_action` handles a `details` kwarg in `audit_service.py` — if the signature differs, match it.

Register the audit labels: in `services/audit_service.py` find `ACTION_LABELS` and add entries:

```python
    "backup_destination.attach": "Repository prefix attached",
    "backup_destination.detach": "Repository prefix detached",
```

(Match the exact phrasing style of neighboring labels when editing.)

- [ ] **Step 4: Create the partials**

`src/mailfallback/templates/partials/repo_contents.html`:

```html
{% if error %}
<div class="alert-error" role="alert">
    <i data-lucide="alert-triangle" class="icon-md icon-inline"></i>
    Could not list repository contents: {{ error }}
</div>
{% elif not entries %}
<p class="text-muted text-small">This repository is empty.</p>
{% else %}
<table class="text-small">
    <thead>
        <tr><th>Prefix</th><th>Kind</th><th>Snapshots</th><th class="text-right">Actions</th></tr>
    </thead>
    <tbody>
        {% for e in entries %}
        <tr>
            <td><code>{{ e.prefix }}</code></td>
            <td>
                {% if e.kind == "account" %}
                <span class="badge">{{ e.account.name }}</span>
                {% elif e.kind == "config" %}
                <span class="badge badge-admin">Config backup</span>
                {% elif e.kind == "attached" %}
                <span class="badge">Attached → {{ e.account.name if e.account else "?" }}</span>
                {% else %}
                <span class="badge badge-warn">Orphan</span>
                {% endif %}
            </td>
            <td>
                <span hx-get="/admin/backup/{{ dest.id }}/contents/{{ e.prefix }}/detail"
                      hx-trigger="load" hx-swap="innerHTML">
                    <span class="text-muted">loading…</span>
                </span>
            </td>
            <td class="text-right">
                {% if e.kind == "orphan" %}
                <form method="post" action="/admin/backup/{{ dest.id }}/attach" class="inline-form">
                    <input type="hidden" name="prefix" value="{{ e.prefix }}">
                    <select name="account_id" required>
                        <option value="" disabled selected>Attach to mailbox…</option>
                        {% for acc in accounts %}
                        <option value="{{ acc.id }}">{{ acc.name }} ({{ acc.email_address }})</option>
                        {% endfor %}
                    </select>
                    <button type="submit" class="icon-btn" title="Attach as restore source">
                        <i data-lucide="link" class="icon-md"></i> Attach
                    </button>
                </form>
                {% elif e.kind == "attached" %}
                <form method="post" action="/admin/backup/attachments/{{ e.attachment.id }}/delete"
                      class="inline-form"
                      onsubmit="return confirm('Detach {{ e.prefix }}? Bucket data is not touched.')">
                    <button type="submit" class="icon-btn" title="Detach">
                        <i data-lucide="unlink" class="icon-md"></i> Detach
                    </button>
                </form>
                {% endif %}
            </td>
        </tr>
        {% endfor %}
    </tbody>
</table>
{% endif %}
```

(If `badge-warn` does not exist in `static/css/style.css`, use the plain `badge` class — check first; do not invent CSS classes.)

`src/mailfallback/templates/partials/repo_prefix_detail.html`:

```html
{% if detail.ok %}
{{ detail.snapshot_count }}{% if detail.latest %} <span class="text-muted">· latest {{ detail.latest[:10] }}</span>{% endif %}
{% else %}
<span class="text-muted" title="{{ detail.error }}">unreadable</span>
{% endif %}
```

- [ ] **Step 5: Wire the panel into `admin_backup.html`**

In the actions cell of each repository row (next to the Test button) add:

```html
                    <button class="icon-btn" title="Contents"
                            hx-get="/admin/backup/{{ dest.id }}/contents"
                            hx-target="#repo-contents-{{ dest.id }}"
                            hx-swap="innerHTML"
                            onclick="document.getElementById('contents-{{ dest.id }}').classList.toggle('hidden')">
                        <i data-lucide="folder-search" class="icon-md"></i>
                    </button>
```

And after the edit row (`<tr id="edit-{{ dest.id }}" ...>...</tr>`) add a contents row:

```html
        <tr id="contents-{{ dest.id }}" class="hidden">
            <td colspan="8"><div id="repo-contents-{{ dest.id }}"></div></td>
        </tr>
```

- [ ] **Step 6: Run tests + lint**

Run: `uv run pytest tests/test_ui_backup_admin.py -n auto -v && uv run ruff check src/ tests/`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/mailfallback/routers/ui_backup.py src/mailfallback/services/audit_service.py src/mailfallback/templates/ tests/test_ui_backup_admin.py
git commit -m "feat(backup): repository contents panel with orphan attach/detach"
```

---

### Task 8: Restore from attached prefixes

**Files:**
- Modify: `src/mailfallback/services/recovery_service.py:88-...` (`create_recovery`)
- Modify: `src/mailfallback/routers/ui_backup.py:352-434` (snapshots panel + restore route)
- Modify: the snapshots partial template used by `account_backup_snapshots` (find with `grep -n "TemplateResponse" src/mailfallback/routers/ui_backup.py` around line 353 — read it before editing)
- Test: `tests/test_recovery_service.py` (extend)

- [ ] **Step 1: Write the failing test** (append to `tests/test_recovery_service.py`, reusing its existing fixtures — read the file first; it already has account/destination/policy fixtures used by `create_recovery` tests)

```python
@patch("mailfallback.services.recovery_service.restic_service")
def test_create_recovery_from_attached_source(mock_restic, db_session, account, destination, tmp_path, monkeypatch):
    """An attachment source restores from its own prefix, ignoring BackupPolicy."""
    mock_restic.restore_snapshot.return_value = {"snapshot_id": "ab12"}
    mock_restic.list_snapshots.return_value = [
        {"short_id": "ab12", "time": "2026-06-01T00:00:00Z"}
    ]

    rec = create_recovery(
        db_session,
        account.id,
        "ab12",
        source_repository=destination,
        source_prefix="old-ghost-prefix",
    )

    assert rec.status.value == "ready"
    assert rec.repository_id == destination.id
    # restic was driven with the attachment's prefix, not account.id
    args = mock_restic.restore_snapshot.call_args.args
    assert args[1] == "old-ghost-prefix"
```

Adapt fixture names/setup to what the file actually uses (e.g. it may patch `_resolve_maildir_inside_restore` or pre-create directories — mirror the existing `create_recovery` success test and change only the source kwargs + prefix assertion).

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_recovery_service.py -n auto -v -k attached`
Expected: FAIL — unexpected keyword `source_repository`.

- [ ] **Step 3: Extend `create_recovery`**

Change the signature:

```python
def create_recovery(
    db: Session,
    account_id: str,
    snapshot_id: str,
    *,
    kind: RecoveryKind = RecoveryKind.persistent,
    ttl_minutes: int | None = None,
    source_repository: Repository | None = None,
    source_prefix: str | None = None,
) -> Recovery:
```

(`Repository` import added to the model imports of recovery_service.) Replace the policy lookup block:

```python
    if source_repository is not None and source_prefix:
        destination = source_repository
        repo_prefix = source_prefix
    else:
        backup = db.query(BackupPolicy).filter(BackupPolicy.account_id == account_id).first()
        if not backup:
            raise ValueError("Account has no backup policy; nothing to restore from")
        destination = backup.destination
        repo_prefix = account.id
```

Then replace every later use of `backup.destination` with `destination`, `backup.destination_id` with `destination.id`, and every restic call's `account.id` argument with `repo_prefix` (`restore_snapshot(destination, repo_prefix, snapshot_id, restore_root)` and `list_snapshots(destination, repo_prefix)`).

- [ ] **Step 4: Add the restore route and surface attached snapshots**

Read the `account_backup_snapshots` route (ui_backup.py:352) and its template partial. Extend the route context with attachments:

```python
    attachments = (
        db.query(RepositoryAttachment)
        .filter(RepositoryAttachment.account_id == account_id)
        .all()
    )
    attached_sources = []
    for att in attachments:
        try:
            snaps = restic_service.list_snapshots(att.repository, att.prefix)
        except Exception as e:
            snaps = []
            logger.warning("Cannot list attached prefix %s: %s", att.prefix, e)
        attached_sources.append({"attachment": att, "snapshots": snaps})
```

and pass `attached_sources` into the template. In the snapshots partial, after the policy snapshots table, render each attached source with the same row markup as the existing snapshots (copy the existing snapshot-row block) plus a heading:

```html
{% for src in attached_sources %}
<h4 class="mt-1">Attached: <code>{{ src.attachment.prefix }}</code></h4>
{# same table structure as above, but the restore form posts to:
   /accounts/{{ account.id }}/backup/attachments/{{ src.attachment.id }}/restore/{{ snap.short_id }} #}
{% endfor %}
```

New route (mirror `account_backup_restore` at ui_backup.py:384 — same permission checks and same call style into the recovery flow; read it first and copy its structure):

```python
@router.post("/accounts/{account_id}/backup/attachments/{attachment_id}/restore/{snapshot_id}")
async def account_attachment_restore(
    account_id: str,
    attachment_id: str,
    snapshot_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    # identical auth/ownership checks as account_backup_restore
    ...
    att = (
        db.query(RepositoryAttachment)
        .filter(
            RepositoryAttachment.id == attachment_id,
            RepositoryAttachment.account_id == account_id,
        )
        .first()
    )
    if not att:
        request.session["flash_error"] = "Attachment not found"
        return RedirectResponse(f"/accounts/{account_id}", status_code=303)
    # then invoke the same recovery submission path as account_backup_restore,
    # passing source_repository=att.repository, source_prefix=att.prefix
```

**Important:** `account_backup_restore` may run `create_recovery` via an executor/background task — read it and propagate the two new kwargs through whatever wrapper it uses, keeping the exact same submission mechanism.

- [ ] **Step 5: Run targeted tests + full suite**

Run: `uv run pytest tests/test_recovery_service.py tests/test_ui_backup_admin.py tests/test_recover_routes.py -n auto -v`
Then: `uv run pytest tests/ -n auto -q`
Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add src/mailfallback/services/recovery_service.py src/mailfallback/routers/ui_backup.py src/mailfallback/templates/ tests/
git commit -m "feat(backup): restore recoveries from attached repository prefixes"
```

---

### Task 9: Config export + passphrase encryption

**Files:**
- Create: `src/mailfallback/services/config_backup_service.py`
- Test: `tests/test_config_backup_service.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_config_backup_service.py`:

```python
"""Config backup: export, scrypt+Fernet envelope, import round-trip."""

import pytest

from mailfallback.config import settings
from mailfallback.models import (
    Account,
    BackendType,
    BackupPolicy,
    MailStore,
    Repository,
    RepositoryAttachment,
    User,
    UserRole,
)
from mailfallback.security import decrypt_credentials, encrypt_credentials
from mailfallback.services import config_backup_service as cbs


def _enc(value: str) -> str:
    return encrypt_credentials(value, settings.secret_key)


@pytest.fixture
def populated(db_session):
    store = MailStore(name="default", path="/data/mailboxes")
    db_session.add(store)
    db_session.flush()
    user = User(username="alice", password_hash="bcrypt$x", role=UserRole.admin, store_id=store.id)
    db_session.add(user)
    db_session.flush()
    acc = Account(
        name="work",
        email_address="a@b.c",
        imap_host="imap.b.c",
        maildir_path=f"/data/mailboxes/fixed-uuid",
        credentials=_enc("imap-secret"),
        store_id=store.id,
    )
    db_session.add(acc)
    db_session.flush()
    acc.owners.append(user)
    repo = Repository(
        name="offsite",
        backend_type=BackendType.s3,
        s3_endpoint=_enc("https://s3.example.com"),
        s3_bucket=_enc("bucket"),
        s3_access_key=_enc("ak"),
        s3_secret_key=_enc("sk"),
        restic_password=_enc("rp"),
    )
    db_session.add(repo)
    db_session.flush()
    db_session.add(BackupPolicy(account_id=acc.id, destination_id=repo.id, schedule="0 2 * * *"))
    db_session.add(
        RepositoryAttachment(repository_id=repo.id, account_id=acc.id, prefix="old-prefix")
    )
    db_session.commit()
    return {"store": store, "user": user, "account": acc, "repo": repo}


class TestExport:
    def test_export_contains_all_tables_and_plaintext_secrets(self, db_session, populated):
        data = cbs.build_export(db_session)

        assert data["schema_version"] == 1
        tables = data["tables"]
        assert {r["username"] for r in tables["users"]} == {"alice"}
        acc_row = tables["accounts"][0]
        assert acc_row["id"] == populated["account"].id  # IDs preserved
        assert acc_row["credentials"] == "imap-secret"  # decrypted for re-keying
        repo_row = tables["backup_destinations"][0]
        assert repo_row["restic_password"] == "rp"  # pragma: allowlist secret
        assert tables["account_owners"] == [
            {"account_id": populated["account"].id, "user_id": populated["user"].id}
        ]
        assert tables["repository_attachments"][0]["prefix"] == "old-prefix"


class TestEnvelope:
    def test_round_trip(self):
        blob = cbs.encrypt_export({"schema_version": 1, "tables": {}}, "correct horse battery")
        data = cbs.decrypt_export(blob, "correct horse battery")
        assert data["schema_version"] == 1

    def test_wrong_passphrase_raises_clean_error(self):
        blob = cbs.encrypt_export({"schema_version": 1, "tables": {}}, "right")
        with pytest.raises(cbs.ConfigDecryptError):
            cbs.decrypt_export(blob, "wrong")

    def test_garbage_raises_clean_error(self):
        with pytest.raises(cbs.ConfigDecryptError):
            cbs.decrypt_export(b"not an envelope", "whatever")


class TestImport:
    def test_import_into_empty_db_preserves_ids_and_rekeys(self, db_session, populated):
        data = cbs.build_export(db_session)
        # wipe everything (delete in FK order)
        for model in (RepositoryAttachment, BackupPolicy, Account, Repository, User, MailStore):
            for row in db_session.query(model).all():
                db_session.delete(row)
        db_session.commit()

        report = cbs.import_export(db_session, data)

        assert report["errors"] == []
        acc = db_session.query(Account).one()
        assert acc.id == populated["account"].id
        assert decrypt_credentials(acc.credentials, settings.secret_key) == "imap-secret"
        repo = db_session.query(Repository).one()
        assert decrypt_credentials(repo.restic_password, settings.secret_key) == "rp"
        assert db_session.query(RepositoryAttachment).count() == 1
        user = db_session.query(User).one()
        assert user.password_hash == "bcrypt$x"  # pragma: allowlist secret

    def test_import_skips_collisions(self, db_session, populated):
        data = cbs.build_export(db_session)

        report = cbs.import_export(db_session, data)  # everything already exists

        assert report["imported"]["accounts"] == 0
        assert report["skipped"]["accounts"] == 1
        assert report["skipped"]["users"] == 1
        assert db_session.query(Account).count() == 1
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_config_backup_service.py -n auto -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement the service (export + envelope + import)**

Create `src/mailfallback/services/config_backup_service.py`:

```python
"""Full-configuration backup: export every config table with secrets decrypted,
encrypt with a passphrase-derived key (scrypt + Fernet), and import the result
back, re-encrypting secrets with the local MAILFALLBACK_SECRET_KEY.

Original primary keys are preserved on import — they are what makes restic
prefixes and maildir paths line up again after a disaster recovery."""

import base64
import datetime
import enum
import hashlib
import json
import logging
import os

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import Table
from sqlalchemy.orm import Session

from mailfallback.config import settings
from mailfallback.models import (
    Base,
    account_groups,
    account_owners,
    group_members,
    user_allowed_stores,
)
from mailfallback.security import decrypt_credentials, encrypt_credentials

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1
CONFIG_FILENAME = "mfb-config.json.enc"

# Insert order respects FKs; reversed for collision checks it doesn't matter.
_EXPORT_TABLES: list[str] = [
    "mail_stores",
    "users",
    "user_allowed_stores",
    "backup_destinations",
    "accounts",
    "account_owners",
    "groups",
    "group_members",
    "account_groups",
    "account_backups",
    "repository_attachments",
]

# Fernet-encrypted columns that must be decrypted on export / re-encrypted on import.
_SECRET_COLUMNS: dict[str, list[str]] = {
    "accounts": ["credentials"],
    "backup_destinations": [
        "s3_endpoint",
        "s3_bucket",
        "s3_access_key",
        "s3_secret_key",
        "local_path",
        "restic_password",
        "config_backup_passphrase",
    ],
}

# Tables whose primary key is composite (association tables).
_ASSOCIATION_TABLES = {"user_allowed_stores", "account_owners", "group_members", "account_groups"}


class ConfigDecryptError(Exception):
    """Wrong passphrase or corrupt envelope."""


def _table(name: str) -> Table:
    return Base.metadata.tables[name]


def _jsonable(value):
    if isinstance(value, enum.Enum):
        return value.value
    if isinstance(value, datetime.datetime):
        return value.isoformat()
    return value


def build_export(db: Session) -> dict:
    tables: dict[str, list[dict]] = {}
    for name in _EXPORT_TABLES:
        table = _table(name)
        rows = []
        for row in db.execute(table.select()).mappings():
            record = {k: _jsonable(v) for k, v in dict(row).items()}
            for col in _SECRET_COLUMNS.get(name, []):
                if record.get(col):
                    record[col] = decrypt_credentials(record[col], settings.secret_key)
            rows.append(record)
        tables[name] = rows
    return {"schema_version": SCHEMA_VERSION, "tables": tables}


def _derive_key(passphrase: str, salt: bytes) -> bytes:
    dk = hashlib.scrypt(
        passphrase.encode(), salt=salt, n=2**15, r=8, p=1, maxmem=64 * 1024 * 1024, dklen=32
    )
    return base64.urlsafe_b64encode(dk)


def encrypt_export(data: dict, passphrase: str) -> bytes:
    salt = os.urandom(16)
    token = Fernet(_derive_key(passphrase, salt)).encrypt(
        json.dumps(data, separators=(",", ":")).encode()
    )
    envelope = {
        "mfb_config_backup": SCHEMA_VERSION,
        "kdf": "scrypt",
        "salt": base64.b64encode(salt).decode(),
        "ciphertext": token.decode(),
    }
    return json.dumps(envelope).encode()


def decrypt_export(blob: bytes, passphrase: str) -> dict:
    try:
        envelope = json.loads(blob)
        salt = base64.b64decode(envelope["salt"])
        token = envelope["ciphertext"].encode()
    except (ValueError, KeyError, TypeError) as e:
        raise ConfigDecryptError(f"Not a valid MFB config backup: {e}") from e
    try:
        plaintext = Fernet(_derive_key(passphrase, salt)).decrypt(token)
    except InvalidToken as e:
        raise ConfigDecryptError("Wrong passphrase (or corrupt backup)") from e
    return json.loads(plaintext)


def _parse_datetimes(table: Table, record: dict) -> dict:
    out = dict(record)
    for col in table.columns:
        v = out.get(col.name)
        if v is not None and isinstance(v, str) and str(col.type).startswith("DATETIME"):
            try:
                out[col.name] = datetime.datetime.fromisoformat(v)
            except ValueError:
                pass
    return out


def import_export(db: Session, data: dict) -> dict:
    """Insert exported rows, preserving IDs, re-encrypting secrets locally.

    Collisions (existing PK, or username for users) are skipped and counted.
    Returns {"imported": {table: n}, "skipped": {table: n}, "errors": [str]}.
    """
    if data.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"Unsupported schema_version: {data.get('schema_version')}")

    imported: dict[str, int] = dict.fromkeys(_EXPORT_TABLES, 0)
    skipped: dict[str, int] = dict.fromkeys(_EXPORT_TABLES, 0)
    errors: list[str] = []

    for name in _EXPORT_TABLES:
        table = _table(name)
        pk_cols = [c.name for c in table.primary_key.columns]
        for record in data["tables"].get(name, []):
            try:
                pk_filter = [table.c[c] == record[c] for c in pk_cols]
                exists = db.execute(table.select().where(*pk_filter)).first()
                if not exists and name == "users":
                    exists = db.execute(
                        table.select().where(table.c.username == record["username"])
                    ).first()
                if exists:
                    skipped[name] += 1
                    continue
                values = _parse_datetimes(table, record)
                for col in _SECRET_COLUMNS.get(name, []):
                    if values.get(col):
                        values[col] = encrypt_credentials(values[col], settings.secret_key)
                db.execute(table.insert().values(**values))
                imported[name] += 1
            except Exception as e:
                db.rollback()
                errors.append(f"{name}: {str(e)[:200]}")
    db.commit()
    return {"imported": imported, "skipped": skipped, "errors": errors}
```

**Note for the implementer:** the `str(col.type).startswith("DATETIME")` check must be verified against SQLAlchemy's actual type names in this project (run a quick `python -c` with one of the models). If it doesn't match, use `isinstance(col.type, sa.DateTime)` instead (import `sqlalchemy as sa`). Same for `Base.metadata.tables` table names — confirm `groups` is the groups table's real name (`grep -n "__tablename__" src/mailfallback/models.py`).

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_config_backup_service.py -n auto -v`
Expected: PASS. If `_parse_datetimes` or table names fail, fix per the note above.

- [ ] **Step 5: Commit**

```bash
git add src/mailfallback/services/config_backup_service.py tests/test_config_backup_service.py
git commit -m "feat(backup): encrypted full-config export/import with preserved IDs"
```

---

### Task 10: `run_config_backup` + `fetch_latest_config`

**Files:**
- Modify: `src/mailfallback/services/config_backup_service.py`
- Test: `tests/test_config_backup_service.py` (extend)

- [ ] **Step 1: Write the failing tests** (append)

```python
from unittest.mock import patch


class TestRunConfigBackup:
    @patch("mailfallback.services.config_backup_service.restic_service")
    def test_success_updates_status(self, mock_restic, db_session, populated):
        repo = populated["repo"]
        repo.config_backup_enabled = True
        repo.config_backup_passphrase = _enc("a-strong-passphrase")
        db_session.commit()
        mock_restic.init_repo.return_value = True
        mock_restic.run_backup.return_value = {"message_type": "summary"}
        mock_restic.apply_retention.return_value = {"pruned": True}

        result = cbs.run_config_backup(db_session, repo)

        assert result["ok"] is True
        db_session.refresh(repo)
        assert repo.last_config_backup_status == "ok"
        assert repo.last_config_backup_at is not None
        # backed up a file named mfb-config.json.enc under the __mfb_config__ prefix
        run_args = mock_restic.run_backup.call_args.args
        assert run_args[1] == "__mfb_config__"
        assert run_args[2].endswith("mfb-config.json.enc")
        ret_args = mock_restic.apply_retention.call_args
        assert ret_args.args[1] == "__mfb_config__"
        assert ret_args.kwargs.get("keep_daily") == 30

    @patch("mailfallback.services.config_backup_service.restic_service")
    def test_failure_records_error(self, mock_restic, db_session, populated):
        repo = populated["repo"]
        repo.config_backup_enabled = True
        repo.config_backup_passphrase = _enc("a-strong-passphrase")
        db_session.commit()
        mock_restic.init_repo.return_value = True
        mock_restic.run_backup.side_effect = RuntimeError("S3 down")

        result = cbs.run_config_backup(db_session, repo)

        assert result["ok"] is False
        db_session.refresh(repo)
        assert repo.last_config_backup_status.startswith("failed")
        assert "S3 down" in repo.last_config_backup_status


class TestFetchLatestConfig:
    @patch("mailfallback.services.config_backup_service.restic_service")
    def test_fetches_and_finds_file(self, mock_restic, populated, tmp_path):
        repo = populated["repo"]
        mock_restic.list_snapshots.return_value = [
            {"short_id": "ab12", "time": "2026-06-09T03:00:00Z"}
        ]

        def fake_restore(dest, prefix, snap_id, target):
            nested = os.path.join(target, "tmp", "cfg")
            os.makedirs(nested, exist_ok=True)
            with open(os.path.join(nested, "mfb-config.json.enc"), "wb") as f:
                f.write(b"blob")
            return {}

        mock_restic.restore_snapshot.side_effect = fake_restore

        path = cbs.fetch_latest_config(repo, str(tmp_path))

        assert path.endswith("mfb-config.json.enc")
        with open(path, "rb") as f:
            assert f.read() == b"blob"

    @patch("mailfallback.services.config_backup_service.restic_service")
    def test_no_snapshots_raises(self, mock_restic, populated, tmp_path):
        mock_restic.list_snapshots.return_value = []
        with pytest.raises(ValueError):
            cbs.fetch_latest_config(populated["repo"], str(tmp_path))
```

Add `import os` to the test file imports.

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_config_backup_service.py -n auto -v -k "RunConfigBackup or FetchLatest"`
Expected: FAIL — functions missing.

- [ ] **Step 3: Implement**

Append to `config_backup_service.py` (add `import tempfile`, `from pathlib import Path`, and `from mailfallback.services import restic_service` at the top; also `from mailfallback.models import Repository` and a timezone-aware now helper — models.py has `_utcnow`, but define locally to avoid importing privates):

```python
def _utcnow() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


def run_config_backup(db: Session, repository: Repository) -> dict:
    """Export, encrypt, and snapshot the configuration into __mfb_config__."""
    from mailfallback.services.repo_inventory import CONFIG_PREFIX

    try:
        passphrase = decrypt_credentials(
            repository.config_backup_passphrase, settings.secret_key
        )
        blob = encrypt_export(build_export(db), passphrase)
        with tempfile.TemporaryDirectory(prefix="mfb-config-backup-") as tmpdir:
            file_path = os.path.join(tmpdir, CONFIG_FILENAME)
            with open(file_path, "wb") as f:
                f.write(blob)
            restic_service.init_repo(repository, CONFIG_PREFIX)
            restic_service.run_backup(repository, CONFIG_PREFIX, file_path)
            restic_service.apply_retention(repository, CONFIG_PREFIX, "custom", keep_daily=30)
        repository.last_config_backup_at = _utcnow()
        repository.last_config_backup_status = "ok"
        db.commit()
        logger.info("Config backup completed for repository %s", repository.name)
        return {"ok": True, "error": None}
    except Exception as e:
        db.rollback()
        repository.last_config_backup_at = _utcnow()
        repository.last_config_backup_status = f"failed: {str(e)[:180]}"
        db.commit()
        logger.error("Config backup failed for %s: %s", repository.name, e)
        return {"ok": False, "error": str(e)[:200]}


def fetch_latest_config(repository: Repository, target_dir: str) -> str:
    """Restore the newest __mfb_config__ snapshot and return the file path."""
    from mailfallback.services.repo_inventory import CONFIG_PREFIX

    snapshots = restic_service.list_snapshots(repository, CONFIG_PREFIX)
    if not snapshots:
        raise ValueError("No configuration snapshots found in this repository")
    restic_service.restore_snapshot(
        repository, CONFIG_PREFIX, snapshots[0]["short_id"], target_dir
    )
    matches = list(Path(target_dir).rglob(CONFIG_FILENAME))
    if not matches:
        raise ValueError("Snapshot did not contain a configuration file")
    return str(matches[0])
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_config_backup_service.py -n auto -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/mailfallback/services/config_backup_service.py tests/test_config_backup_service.py
git commit -m "feat(backup): config backup runner and latest-config fetch via restic"
```

---

### Task 11: Scheduler job + repository form fields + Backup-now button

**Files:**
- Modify: `src/mailfallback/services/scheduler.py` (new `config_backup_scheduler_jobs`, called where `backup_scheduler_jobs` is called: lines 143 and 161)
- Modify: `src/mailfallback/routers/ui_backup.py` (create/edit parse new fields; new `config-backup` trigger route)
- Modify: `src/mailfallback/templates/admin_backup.html` (wizard step 3 + edit form: checkbox + passphrase; row shows last status; Backup-now button)
- Test: `tests/test_scheduler_config_backup.py` (new), `tests/test_ui_backup_admin.py` (extend)

- [ ] **Step 1: Write the failing scheduler test**

Create `tests/test_scheduler_config_backup.py`:

```python
"""Config-backup scheduler job reconciliation."""

from unittest.mock import MagicMock, patch

from mailfallback.config import settings
from mailfallback.models import BackendType, Repository
from mailfallback.security import encrypt_credentials
from mailfallback.services.scheduler import config_backup_scheduler_jobs


def _repo(db_session, enabled=True):
    r = Repository(
        name="cfg-repo",
        backend_type=BackendType.local,
        local_path=encrypt_credentials("/backups", settings.secret_key),
        restic_password=encrypt_credentials("rp", settings.secret_key),
        config_backup_enabled=enabled,
        config_backup_passphrase=encrypt_credentials("longpassphrase", settings.secret_key),
    )
    db_session.add(r)
    db_session.commit()
    return r


@patch("mailfallback.services.scheduler.scheduler")
def test_adds_job_for_enabled_repo(mock_sched, db_session):
    mock_sched.get_jobs.return_value = []
    repo = _repo(db_session, enabled=True)

    config_backup_scheduler_jobs(db_session)

    add_call = mock_sched.add_job.call_args
    assert add_call.kwargs["id"] == f"config-backup-{repo.id}"


@patch("mailfallback.services.scheduler.scheduler")
def test_removes_job_for_disabled_repo(mock_sched, db_session):
    repo = _repo(db_session, enabled=False)
    job = MagicMock()
    job.id = f"config-backup-{repo.id}"
    mock_sched.get_jobs.return_value = [job]

    config_backup_scheduler_jobs(db_session)

    mock_sched.remove_job.assert_called_once_with(f"config-backup-{repo.id}")
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_scheduler_config_backup.py -n auto -v`
Expected: FAIL — function missing.

- [ ] **Step 3: Implement the scheduler function**

In `scheduler.py`, after `backup_scheduler_jobs`:

```python
def _run_scheduled_config_backup(repository_id: str) -> None:
    from mailfallback.db import SessionLocal
    from mailfallback.models import Repository
    from mailfallback.services.config_backup_service import run_config_backup

    db = SessionLocal()
    try:
        repo = db.query(Repository).filter(Repository.id == repository_id).first()
        if repo and repo.config_backup_enabled:
            run_config_backup(db, repo)
    finally:
        db.close()


def config_backup_scheduler_jobs(db: Session) -> None:
    from mailfallback.models import Repository

    existing_job_ids = {j.id for j in scheduler.get_jobs()}
    enabled = (
        db.query(Repository).filter(Repository.config_backup_enabled.is_(True)).all()
    )
    trigger = CronTrigger(hour=3, minute=0)

    for repo in enabled:
        job_id = f"config-backup-{repo.id}"
        if job_id in existing_job_ids:
            scheduler.reschedule_job(job_id, trigger=trigger)
        else:
            scheduler.add_job(
                _run_scheduled_config_backup,
                trigger,
                args=[repo.id],
                id=job_id,
                replace_existing=True,
            )

    active_ids = {f"config-backup-{r.id}" for r in enabled}
    for job_id in existing_job_ids:
        if job_id.startswith("config-backup-") and job_id not in active_ids:
            scheduler.remove_job(job_id)
```

Check the actual session factory name (`grep -n "SessionLocal\|sessionmaker" src/mailfallback/db.py`) and how `_run_scheduled_sync` (scheduler.py:18) opens its session — copy that exact pattern in `_run_scheduled_config_backup`.

Call `config_backup_scheduler_jobs(db)` right after both existing `backup_scheduler_jobs(db)` calls (lines ~143 and ~161).

- [ ] **Step 4: Wire the form fields and the trigger route**

In `admin_create_backup_destination` and `admin_edit_backup_destination`, after the `insecure_tls` handling, add (identical block in both; in edit, only overwrite the passphrase when a new one is supplied):

```python
    config_backup_enabled = bool(form.get("config_backup_enabled"))
    config_passphrase = form.get("config_backup_passphrase", "").strip()
    if config_backup_enabled:
        if config_passphrase:
            if len(config_passphrase) < 12:
                request.session["flash_error"] = (
                    "Config backup passphrase must be at least 12 characters"
                )
                return RedirectResponse("/admin/backup", status_code=303)
            dest.config_backup_passphrase = encrypt_credentials(
                config_passphrase, settings.secret_key
            )
        elif not dest.config_backup_passphrase:
            request.session["flash_error"] = (
                "A passphrase is required to enable config backup"
            )
            return RedirectResponse("/admin/backup", status_code=303)
    dest.config_backup_enabled = config_backup_enabled
```

(In the create flow `dest.config_backup_passphrase` starts as None, so the `elif` correctly rejects enabling without a passphrase. Place this block BEFORE the probe/test call so validation failures don't run a probe. In the edit flow a failed probe later still rolls everything back.)

After saving in both flows, refresh the scheduler:

```python
    from mailfallback.services.scheduler import config_backup_scheduler_jobs

    config_backup_scheduler_jobs(db)
```

New trigger route:

```python
@router.post("/admin/backup/{dest_id}/config-backup")
async def admin_run_config_backup(dest_id: str, request: Request, db: Session = Depends(get_db)):
    user = _get_session_user(request, db)
    if not user or user.role.value != "admin":
        return RedirectResponse("/", status_code=303)
    dest = db.query(Repository).filter(Repository.id == dest_id).first()
    if not dest or not dest.config_backup_enabled:
        request.session["flash_error"] = "Config backup is not enabled for this repository"
        return RedirectResponse("/admin/backup", status_code=303)

    from mailfallback.services.config_backup_service import run_config_backup

    result = run_config_backup(db, dest)
    if result["ok"]:
        request.session["flash_success"] = f"Configuration backed up to {dest.name}"
    else:
        request.session["flash_error"] = f"Config backup failed: {result['error']}"
    log_action(
        db,
        user=user,
        action="backup_destination.config_backup",
        resource_type="backup_destination",
        resource_id=dest.id,
        resource_name=dest.name,
        ip_address=request.client.host if request.client else None,
    )
    return RedirectResponse("/admin/backup", status_code=303)
```

Add `"backup_destination.config_backup": "Configuration backup run"` to `ACTION_LABELS`.

- [ ] **Step 5: Template changes** (`admin_backup.html`)

Wizard step 3, after the TLS checkbox block:

```html
            <div class="mt-1">
                <label>
                    <input type="checkbox" name="config_backup_enabled" value="1"
                           onchange="this.closest('form').querySelector('.config-passphrase-block').hidden = !this.checked">
                    <strong>Back up MFB configuration to this repository</strong>
                    <small class="text-muted">— nightly encrypted export of users, mailboxes, and settings for disaster recovery.</small>
                </label>
                <div class="config-passphrase-block mt-05" hidden>
                    <label for="config_backup_passphrase">Config backup passphrase</label>
                    <input type="password" id="config_backup_passphrase" name="config_backup_passphrase"
                           minlength="12" placeholder="at least 12 characters" autocomplete="new-password">
                    <small class="text-muted">Separate from the Restic password. Needed to restore the configuration on a new installation — store it safely.</small>
                </div>
            </div>
```

Edit form: same block, but `{% if dest.config_backup_enabled %}checked{% endif %}` on the checkbox, the block not hidden when enabled, and the passphrase input uses `placeholder="Leave blank to keep current"` with no `minlength` requirement client-side (server validates only non-empty values).

Repository row: in the actions cell, add a Backup-now button (visible only when enabled):

```html
                    {% if dest.config_backup_enabled %}
                    <form method="post" action="/admin/backup/{{ dest.id }}/config-backup" class="inline-form">
                        <button type="submit" class="icon-btn" title="Back up configuration now{% if dest.last_config_backup_at %} — last: {{ dest.last_config_backup_at | time_ago }} ({{ dest.last_config_backup_status }}){% endif %}">
                            <i data-lucide="file-lock" class="icon-md"></i>
                        </button>
                    </form>
                    {% endif %}
```

- [ ] **Step 6: Write route tests** (append to `tests/test_ui_backup_admin.py`)

```python
class TestConfigBackupRoutes:
    @patch("mailfallback.services.s3_probe.probe")
    def test_create_with_config_backup_requires_passphrase(
        self, mock_probe, client, db_session, default_store
    ):
        _login_admin(client, db_session, default_store)
        mock_probe.return_value = {"ok": True, "error": None}
        form = dict(S3_FORM, config_backup_enabled="1")

        client.post("/admin/backup/new", data=form, follow_redirects=False)

        assert db_session.query(Repository).count() == 0  # rejected: no passphrase

    @patch("mailfallback.services.s3_probe.probe")
    def test_create_with_passphrase_enables(self, mock_probe, client, db_session, default_store):
        _login_admin(client, db_session, default_store)
        mock_probe.return_value = {"ok": True, "error": None}
        form = dict(
            S3_FORM, config_backup_enabled="1", config_backup_passphrase="averylongpassphrase"
        )

        client.post("/admin/backup/new", data=form, follow_redirects=False)

        repo = db_session.query(Repository).one()
        assert repo.config_backup_enabled is True
        assert repo.config_backup_passphrase is not None

    @patch("mailfallback.services.config_backup_service.restic_service")
    @patch("mailfallback.services.s3_probe.probe")
    def test_backup_now_runs(self, mock_probe, mock_restic, client, db_session, default_store):
        _login_admin(client, db_session, default_store)
        mock_probe.return_value = {"ok": True, "error": None}
        form = dict(
            S3_FORM, config_backup_enabled="1", config_backup_passphrase="averylongpassphrase"
        )
        client.post("/admin/backup/new", data=form, follow_redirects=False)
        repo = db_session.query(Repository).one()
        mock_restic.init_repo.return_value = True
        mock_restic.run_backup.return_value = {}
        mock_restic.apply_retention.return_value = {"pruned": True}

        resp = client.post(f"/admin/backup/{repo.id}/config-backup", follow_redirects=False)

        assert resp.status_code == 303
        db_session.expire_all()
        repo = db_session.query(Repository).one()
        assert repo.last_config_backup_status == "ok"
```

- [ ] **Step 7: Run everything**

Run: `uv run pytest tests/test_scheduler_config_backup.py tests/test_ui_backup_admin.py tests/test_config_backup_service.py -n auto -v && uv run ruff check src/ tests/`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add src/mailfallback/services/scheduler.py src/mailfallback/routers/ui_backup.py src/mailfallback/services/audit_service.py src/mailfallback/templates/admin_backup.html tests/
git commit -m "feat(backup): scheduled encrypted config backups per repository"
```

---

### Task 12: DR restore form on the System page

**Files:**
- Modify: `src/mailfallback/routers/ui_admin.py` (two new routes: preview + confirm)
- Modify: `src/mailfallback/templates/settings.html` (DR restore section)
- Create: `src/mailfallback/templates/partials/config_restore_preview.html`
- Test: `tests/test_config_restore_ui.py`

The flow is stateless: the preview step fetches+decrypts and shows counts plus a hidden re-submission form; confirm re-fetches and imports. Two restic fetches, zero server-side state.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_config_restore_ui.py`:

```python
"""DR config-restore flow on the System page — restic mocked end to end."""

import os
from unittest.mock import patch

from mailfallback.models import Account, Repository, UserRole
from mailfallback.services import config_backup_service as cbs
from mailfallback.services.user_service import create_user


def _login_admin(client, db_session, default_store):
    user = create_user(db_session, "admin", "pass", UserRole.admin, store_id=default_store.id)
    client.post("/api/auth/login", json={"username": "admin", "password": "pass"})  # pragma: allowlist secret
    return user


RESTORE_FORM = {
    "backend_type": "s3",
    "s3_endpoint": "https://s3.example.com",
    "s3_bucket": "bucket",
    "s3_access_key": "ak",
    "s3_secret_key": "sk",  # pragma: allowlist secret
    "restic_password": "rp",  # pragma: allowlist secret
    "passphrase": "averylongpassphrase",
}


def _fake_fetch(blob):
    """Return a fetch_latest_config replacement that writes `blob` and returns its path."""

    def fetch(repository, target_dir):
        path = os.path.join(target_dir, "mfb-config.json.enc")
        with open(path, "wb") as f:
            f.write(blob)
        return path

    return fetch


def _export_blob(db_session, passphrase="averylongpassphrase"):
    return cbs.encrypt_export(cbs.build_export(db_session), passphrase)


class TestPreview:
    @patch("mailfallback.routers.ui_admin.config_backup_service")
    def test_preview_shows_counts(self, mock_cbs, client, db_session, default_store):
        _login_admin(client, db_session, default_store)
        blob = _export_blob(db_session)
        mock_cbs.fetch_latest_config.side_effect = _fake_fetch(blob)
        mock_cbs.decrypt_export = cbs.decrypt_export
        mock_cbs.ConfigDecryptError = cbs.ConfigDecryptError

        resp = client.post("/admin/system/config-restore/preview", data=RESTORE_FORM)

        assert resp.status_code == 200
        assert "users" in resp.text.lower()
        assert "Confirm restore" in resp.text

    @patch("mailfallback.routers.ui_admin.config_backup_service")
    def test_wrong_passphrase_shows_error(self, mock_cbs, client, db_session, default_store):
        _login_admin(client, db_session, default_store)
        blob = _export_blob(db_session, passphrase="different")
        mock_cbs.fetch_latest_config.side_effect = _fake_fetch(blob)
        mock_cbs.decrypt_export = cbs.decrypt_export
        mock_cbs.ConfigDecryptError = cbs.ConfigDecryptError

        resp = client.post("/admin/system/config-restore/preview", data=RESTORE_FORM)

        assert resp.status_code == 200
        assert "passphrase" in resp.text.lower()


class TestConfirm:
    @patch("mailfallback.routers.ui_admin.config_backup_service")
    def test_confirm_imports(self, mock_cbs, client, db_session, default_store):
        admin = _login_admin(client, db_session, default_store)
        # build an export that contains one extra account not present locally
        from mailfallback.models import MailStore

        acc = Account(
            name="ghost",
            imap_host="h",
            maildir_path="/data/m/ghost",
            store_id=default_store.id,
        )
        db_session.add(acc)
        db_session.commit()
        blob = _export_blob(db_session)
        db_session.delete(acc)
        db_session.commit()

        mock_cbs.fetch_latest_config.side_effect = _fake_fetch(blob)
        mock_cbs.decrypt_export = cbs.decrypt_export
        mock_cbs.import_export = cbs.import_export
        mock_cbs.ConfigDecryptError = cbs.ConfigDecryptError

        resp = client.post(
            "/admin/system/config-restore/confirm", data=RESTORE_FORM, follow_redirects=False
        )

        assert resp.status_code == 303
        restored = db_session.query(Account).filter(Account.name == "ghost").one()
        assert restored.id == acc.id  # ID preserved
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_config_restore_ui.py -n auto -v`
Expected: FAIL (404).

- [ ] **Step 3: Implement the routes**

In `ui_admin.py` (check its imports/`_get_session_user`/`templates` usage first and follow them; add `from mailfallback.services import config_backup_service` plus a module-level helper):

```python
def _transient_repository_from_form(form) -> "Repository":
    """Build a NOT-persisted Repository from DR-restore form fields, with
    values encrypted so the normal decrypt paths work."""
    from mailfallback.models import BackendType, Repository

    backend = form.get("backend_type", "s3")
    repo = Repository(
        name="__dr_restore__",
        backend_type=BackendType(backend),
        restic_password=encrypt_credentials(
            form.get("restic_password", "").strip(), settings.secret_key
        ),
        insecure_tls=bool(form.get("insecure_tls")),
    )
    if backend == "s3":
        repo.s3_endpoint = encrypt_credentials(
            form.get("s3_endpoint", "").strip(), settings.secret_key
        )
        repo.s3_bucket = encrypt_credentials(
            form.get("s3_bucket", "").strip(), settings.secret_key
        )
        repo.s3_access_key = encrypt_credentials(
            form.get("s3_access_key", "").strip(), settings.secret_key
        )
        repo.s3_secret_key = encrypt_credentials(
            form.get("s3_secret_key", "").strip(), settings.secret_key
        )
    else:
        repo.local_path = encrypt_credentials(
            form.get("local_path", "").strip(), settings.secret_key
        )
    return repo


def _fetch_and_decrypt(form) -> dict:
    import tempfile

    repo = _transient_repository_from_form(form)
    passphrase = form.get("passphrase", "")
    with tempfile.TemporaryDirectory(prefix="mfb-config-restore-") as tmpdir:
        path = config_backup_service.fetch_latest_config(repo, tmpdir)
        with open(path, "rb") as f:
            blob = f.read()
    return config_backup_service.decrypt_export(blob, passphrase)


@router.post("/admin/system/config-restore/preview", response_class=HTMLResponse)
async def config_restore_preview(request: Request, db: Session = Depends(get_db)):
    user = _get_session_user(request, db)
    if not user or user.role.value != "admin":
        return RedirectResponse("/", status_code=303)
    form = await request.form()
    error = None
    counts: dict[str, int] = {}
    try:
        data = _fetch_and_decrypt(form)
        counts = {name: len(rows) for name, rows in data["tables"].items()}
    except config_backup_service.ConfigDecryptError as e:
        error = str(e)
    except Exception as e:
        error = str(e)[:200]
    return templates.TemplateResponse(
        request=request,
        name="partials/config_restore_preview.html",
        context={"error": error, "counts": counts, "form": dict(form)},
    )


@router.post("/admin/system/config-restore/confirm")
async def config_restore_confirm(request: Request, db: Session = Depends(get_db)):
    user = _get_session_user(request, db)
    if not user or user.role.value != "admin":
        return RedirectResponse("/", status_code=303)
    form = await request.form()
    try:
        data = _fetch_and_decrypt(form)
        report = config_backup_service.import_export(db, data)
    except Exception as e:
        request.session["flash_error"] = f"Restore failed: {str(e)[:200]}"
        return RedirectResponse("/admin/settings", status_code=303)

    imported = sum(report["imported"].values())
    skipped = sum(report["skipped"].values())
    log_action(
        db,
        user=user,
        action="config.restore",
        resource_type="config",
        details={"imported": report["imported"], "skipped": report["skipped"], "errors": report["errors"]},
        ip_address=request.client.host if request.client else None,
    )
    msg = f"Configuration restored: {imported} rows imported, {skipped} skipped"
    if report["errors"]:
        request.session["flash_error"] = f"{msg}, {len(report['errors'])} errors (see audit log)"
    else:
        request.session["flash_success"] = msg
    from mailfallback.services.scheduler import refresh_scheduler

    refresh_scheduler()
    return RedirectResponse("/admin/settings", status_code=303)
```

**Check before coding:** the System page's actual route path (`grep -n "admin/settings\|settings.html" src/mailfallback/routers/ui_admin.py`) — use the real redirect target. Add `"config.restore": "Configuration restored"` to `ACTION_LABELS`. Verify `log_action` accepts `details` and that `refresh_scheduler()` takes no args (scheduler.py:155).

- [ ] **Step 4: Create the preview partial**

`src/mailfallback/templates/partials/config_restore_preview.html`:

```html
{% if error %}
<div class="alert-error" role="alert">
    <i data-lucide="alert-triangle" class="icon-md icon-inline"></i> {{ error }}
</div>
{% else %}
<div class="confirm-summary">
    <strong>Backup found.</strong> It contains:
    <ul>
        {% for name, n in counts.items() %}{% if n %}<li>{{ n }} {{ name | replace("_", " ") }}</li>{% endif %}{% endfor %}
    </ul>
    <p class="text-small text-muted">Existing rows (matching ID or username) are skipped, never overwritten.</p>
</div>
<form method="post" action="/admin/system/config-restore/confirm" class="mt-05">
    {% for key, value in form.items() %}
    <input type="hidden" name="{{ key }}" value="{{ value }}">
    {% endfor %}
    <button type="submit" class="icon-btn primary">
        <i data-lucide="archive-restore" class="icon-md"></i> Confirm restore
    </button>
</form>
{% endif %}
```

- [ ] **Step 5: Add the form to `settings.html`**

Read `settings.html` first; add a new `<details>` section styled like its neighbors, at the end before any export section:

```html
<details class="mt-1">
    <summary><i data-lucide="archive-restore" class="icon-lg icon-inline"></i><strong>Restore configuration from repository</strong></summary>
    <p class="text-muted text-small mt-05">
        Disaster recovery: fetch the latest encrypted configuration backup from a repository
        and re-import users, mailboxes, repositories, and policies. Existing rows are skipped.
    </p>
    <form hx-post="/admin/system/config-restore/preview" hx-target="#config-restore-result" hx-swap="innerHTML">
        <div class="grid-2 mt-1">
            <div>
                <label for="dr_backend">Backend</label>
                <select id="dr_backend" name="backend_type"
                        onchange="document.getElementById('dr-s3').hidden = this.value !== 's3'; document.getElementById('dr-local').hidden = this.value !== 'local'">
                    <option value="s3">S3-compatible</option>
                    <option value="local">Local path</option>
                </select>
            </div>
        </div>
        <div id="dr-s3" class="grid-2 mt-05">
            <div><label>S3 Endpoint</label><input type="text" name="s3_endpoint" placeholder="https://s3.example.com"></div>
            <div><label>S3 Bucket</label><input type="text" name="s3_bucket"></div>
            <div><label>Access Key</label><input type="password" name="s3_access_key"></div>
            <div><label>Secret Key</label><input type="password" name="s3_secret_key"></div>
        </div>
        <div id="dr-local" class="mt-05" hidden>
            <label>Local path</label><input type="text" name="local_path" placeholder="/backups/restic">
        </div>
        <div class="grid-2 mt-05">
            <div><label>Restic password</label><input type="password" name="restic_password" required></div>
            <div><label>Config backup passphrase</label><input type="password" name="passphrase" required></div>
        </div>
        <button type="submit" class="icon-btn mt-05">
            <i data-lucide="search" class="icon-md"></i> Fetch and preview
        </button>
    </form>
    <div id="config-restore-result" class="mt-05"></div>
</details>
```

- [ ] **Step 6: Run tests + lint + full suite**

Run: `uv run pytest tests/test_config_restore_ui.py -n auto -v && uv run pytest tests/ -n auto -q && uv run ruff check src/ tests/`
Expected: all green.

- [ ] **Step 7: Commit**

```bash
git add src/mailfallback/routers/ui_admin.py src/mailfallback/services/audit_service.py src/mailfallback/templates/ tests/test_config_restore_ui.py
git commit -m "feat(backup): disaster-recovery config restore from repository"
```

---

### Task 13: Docker image check + docs touch-ups

**Files:**
- Check: `Dockerfile` / `docker/` (boto3 arrives via uv.lock — verify the image build installs from the lockfile, no change expected)
- Modify: `src/mailfallback/templates/admin_backup.html:8-13` (the "Snapshots are useless without…" hint — now partially outdated: config backup covers the Postgres-dump half)
- Modify: `CLAUDE.md` (Project Structure: add `s3_probe.py`, `repo_inventory.py`, `config_backup_service.py` lines)

- [ ] **Step 1: Verify the Docker build picks up boto3**

Run: `grep -n "uv sync\|uv pip\|requirements" Dockerfile docker/*/Dockerfile 2>/dev/null`
Expected: the app image installs via `uv sync` from `uv.lock` — nothing to change. If pinned requirements exist elsewhere, add boto3 there.

- [ ] **Step 2: Update the repositories-page hint**

Replace the paragraph at `admin_backup.html:8-13` with:

```html
<p class="text-small text-muted">
    <i data-lucide="info" class="icon-sm icon-inline"></i>
    Enable <strong>configuration backup</strong> on a repository to store an encrypted export of users, mailboxes, and settings alongside your snapshots — together they make a repository fully restorable on a fresh installation. See
    <a href="https://thekoma.github.io/mailfallback/admin-guide/disaster-recovery/" target="_blank" rel="noopener">disaster recovery</a>.
</p>
```

Also update the wizard step-3 footnote (line ~277-279): `restic init` is no longer run by the test. Replace with:

```html
            <p class="text-muted text-small mt-05">
                We'll verify connectivity and write permission before saving. If the check fails (bad credentials, unreachable host, etc.), nothing is saved.
            </p>
```

- [ ] **Step 3: Update CLAUDE.md services list**

Add to the services tree in CLAUDE.md:

```
│   ├── s3_probe.py           # boto3 connection probe + bucket helpers (no restic side effects)
│   ├── repo_inventory.py     # List/classify restic prefixes in a Repository (orphan detection)
│   ├── config_backup_service.py # Encrypted full-config export/import + scheduled backup runner
```

- [ ] **Step 4: Final full verification**

Run: `uv run pytest tests/ -n auto -q && uv run ruff check src/ tests/ && uv run ruff format --check src/ tests/`
Expected: everything green.

- [ ] **Step 5: Commit**

```bash
git add src/mailfallback/templates/admin_backup.html CLAUDE.md Dockerfile 2>/dev/null || git add src/mailfallback/templates/admin_backup.html CLAUDE.md
git commit -m "docs: update repository hints and project docs for config backup"
```

---

### Task 14: Live verification + PR

- [ ] **Step 1: Build and run the local stack**

Run: `docker compose up -d --build mailfallback`
(Static/templates are baked into the image — every UI change needs a rebuild. Login: `admin` / `MfbDevLocal2026!` at http://localhost:8000.)

- [ ] **Step 2: Verify in the browser (Chrome MCP)**

- Repositories page: Test button shows inline result without reload; Contents panel lists prefixes with classification badges; wizard step 3 shows the config-backup checkbox with passphrase reveal.
- Create a repo against a junk endpoint → inline/flash error, no row saved.
- If a real S3/MinIO target is available locally: enable config backup, hit "Backup config now", confirm `__mfb_config__` appears in Contents; then System page → Restore configuration → preview shows counts.
- Screenshot light + dark, compare against neighboring panels.

- [ ] **Step 3: Run the gemini critic** (per project feedback memory — skip if the CLI is still broken on this Mac, and note it)

- [ ] **Step 4: Push and open the PR**

```bash
git push -u origin feat/repo-s3   # use the SSH remote if .github/workflows was touched (it wasn't)
gh pr create --title "feat: S3 repository management — clean probing, inventory/attach, encrypted config backup + DR restore" --body "$(cat <<'EOF'
## Summary
- Connection test now probes via boto3 (no more __mfb_connection_test__ junk; edit re-tests with rollback; inline HTMX feedback)
- Repository Contents panel: lists restic prefixes, classifies them, attach/detach orphans as read-only restore sources
- Encrypted configuration backup (scrypt+Fernet, full DR export with preserved UUIDs) into a __mfb_config__ sub-repo, nightly + on-demand
- Disaster-recovery restore from the System page (fetch latest config snapshot, preview, import with collision skip)

Spec: docs/superpowers/specs/2026-06-10-s3-repo-management-design.md

## Test plan
- [x] Full suite green (`uv run pytest tests/ -n auto`)
- [x] Live verification on local stack (light + dark)

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

---

## Self-review notes (already applied)

- Spec coverage: §1 → Tasks 1-4; §2 → Tasks 5-8; §3 → Tasks 5, 9-11; §4 → Tasks 10, 12; migration → Task 5; testing → embedded per task; doc drift → Task 13.
- The `attach` restore path reuses `create_recovery`'s existing submission mechanism — Task 8 Step 4 explicitly tells the implementer to read `account_backup_restore` first and propagate kwargs through whatever wrapper exists rather than inventing a new path.
- Type consistency: `probe()` returns `{"ok", "error"}` everywhere; `prefix_detail` returns `{"ok", "snapshot_count", "latest"}`/`{"ok", "error"}`; `import_export` returns `{"imported", "skipped", "errors"}` and Task 12 consumes exactly those keys.
