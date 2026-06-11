# Restore Plan 2/3 — Staging Area + Push Upstream (+ admin scope toggle)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Selected search results land in a per-user writable Dovecot namespace (`Staging/`), the user curates them in webmail, and MFB pushes the survivors to the upstream IMAP server — plus an explicit, audited admin toggle to search beyond owned mailboxes.

**Architecture:** One `StagingArea` per user (TTL + quota, permissive defaults) backed by a real Maildir at `{store}/.dovecot-home/{username}/staging/`, exposed by the Lua userdb as an extra namespace and made writable by dedicated global-ACL lines. `staging_service` owns copy-in (live file or `restic dump`), reconcile (webmail deletions win), quota accounting, cleanup, and push grouping; push reuses `RestoreJob` with a new `staging_push` mode whose worker branch reads staged files from disk and APPENDs upstream with the existing retry/skip-duplicates plumbing. Spec: `docs/superpowers/specs/2026-06-11-restore-staging-attachments-design.md`. UI contract: `.claude/mockup_restore_staging_reference.png` (now INCLUDING the staging bar + "Add to staging").

**Tech stack / conventions:** as Plan 1 (`docs/superpowers/plans/2026-06-11-restore-attachments-search.md`). Baseline on this branch: **702 tests green**. Model + migration SAME commit (drift hook). `uv run pytest tests/ -n auto -q`; ruff check + format. CSS in `style.css`, JS in `restore_workspace.js`, English copy.

**Branch:** create `feat/restore-staging` from the current worktree HEAD (`/Users/koma/src/mailfallback/.claude/worktrees/restore-plan1`, which already merged feat/repo-access; migration head there is 018).

---

## File structure

- Modify: `src/mailfallback/services/search_service.py` (+`include_all` admin scope), `src/mailfallback/routers/restore.py` (search/preview/resolve-uids gates + staging endpoints), `src/mailfallback/routers/ui_restore.py` (accessible-only accounts_json + admin flag)
- Create: `alembic/versions/019_staging_areas.py`; `src/mailfallback/services/staging_service.py`
- Modify: `src/mailfallback/models.py` (StagingArea, StagingMessage), `src/mailfallback/config.py` (staging_ttl_minutes, staging_max_bytes)
- Modify: `src/mailfallback/services/config_generator.py` (ACL lines), `src/mailfallback/routers/dovecot.py` (staging namespace)
- Modify: `src/mailfallback/services/scheduler.py` (staging-cleanup job), `src/mailfallback/services/restore_worker.py` (staging_push mode)
- Modify: `src/mailfallback/templates/restore_workspace.html`, `static/js/restore_workspace.js`, `static/css/style.css`
- Tests: `tests/test_staging_service.py`, `tests/test_staging_api.py`, extend `tests/test_search_service.py`, `tests/test_restore_worker.py`, `tests/test_config_generator.py`, `tests/test_dovecot_userdb.py` (find exact name: `grep -rln "userdb" tests/`), `tests/test_restore_ui.py`

---

### Task 0: Admin "include all mailboxes" toggle, audited

**Files:** `search_service.py`, `routers/restore.py`, `routers/ui_restore.py`, `templates/restore_workspace.html`, `static/js/restore_workspace.js`, tests.

Decision (user-approved): mirrors the Accounts page `show_all` switch (`routers/ui.py:350-383`). Default = accessible only (fixes the dropdown overcount); admins get an explicit switch; activity is audited.

- [ ] **Step 1: failing service test** (extend `tests/test_search_service.py`, reuse its fixtures): admin user owning NOTHING + one account owned by another user; `search_messages(db, user=admin, query=..., include_all=True)` returns the hit; same call with `include_all=False` (default) returns empty; NON-admin with `include_all=True` still gets empty (flag ignored).
- [ ] **Step 2: implement** in `search_service.search_messages`: new kwarg `include_all: bool = False`. After computing `visible`:

```python
    if include_all and user.role == UserRole.admin:
        visible = [a_id for (a_id,) in db.query(Account.id).all()]
```

(import `UserRole`; place before the `if not visible` check; scope filtering vs `account_ids` unchanged.)
- [ ] **Step 3: API gates** in `routers/restore.py`:
  - `RestoreSearchRequest` gains `include_all: bool = False`; pass through to `search_messages`. When the call ran with `include_all` honored (admin), `log_action(db, user=user, action="restore.search_all", resource_type="restore", details={"query": req.query, "accounts": len(req.account_ids) if req.account_ids else "all"}, ip_address=...)` — once per request, only when honored.
  - `api_restore_preview` and `api_resolve_uids`: accept `include_all` (query param for the GET, body field for the POST); when set AND user is admin, bypass `account_service.get_account` ownership by loading the account directly (`db.query(Account).filter_by(id=...)`); for preview on a non-owned account log `restore.preview` with `resource_id=account_id`, details `{"message_id_hash": hex}`. Non-admin: flag ignored (behavior unchanged).
- [ ] **Step 4: UI** — `ui_restore.restore_page`: `accounts_json` switches from `all_accounts` to the user-accessible list when the user is not admin… check how `all_accounts` is built (`get_accounts_for_user`); for admins build BOTH lists: `accounts_json` (accessible) and `all_accounts_json` (every account) + `is_admin` flag in context. Template: admin-only switch after the scope select:

```html
            {% if user.role.value == "admin" %}
            <label class="ws-inline ws-admin-toggle">
              <input type="checkbox" role="switch" x-model="includeAll" @change="onIncludeAllChange()">
              All users' mailboxes <span class="text-muted text-xsmall">— audited</span>
            </label>
            {% endif %}
```

  JS: `includeAll: false`, second data island `ws-all-accounts-data` (admin only — guard the parse), `onIncludeAllChange()` swaps the `accounts` array used by `accountName()`/the scope `<option>` list (move scope options rendering to Alpine `x-for` over `accounts` so the swap re-renders; keep the "All mailboxes (N)" first option showing `accounts.length`), clears results/selection/preview like `onScopeChange`, and `runSearch`/`openPreview`/`restoreSelected` pass `include_all: this.includeAll`.
- [ ] **Step 5: router tests** (`tests/test_staging_api.py` or extend the resolve-uids file): admin + include_all=true previews a non-owned account → 200 + audit row exists (`query(AuditLog)` — check the model name in models.py); non-admin + include_all → 404. Search with include_all as admin → results + `restore.search_all` audit row.
- [ ] **Step 6:** suite + ruff; commit `feat(restore): audited admin scope toggle for cross-user search`.

---

### Task 1: StagingArea/StagingMessage models + migration 019 + settings

- [ ] **Step 1: settings** in `config.py` (after the deep_search timeout): `staging_ttl_minutes: int = 10080`, `staging_max_bytes: int = 0`.
- [ ] **Step 2: models** (after `Recovery` in models.py, reusing `_new_uuid`/`_utcnow`):

```python
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
        String, ForeignKey("staging_areas.id", ondelete="CASCADE"), nullable=False, index=True
    )
    source_account_id = Column(String, ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False)
    message_id_hash = Column(LargeBinary(20), nullable=False)
    original_folder = Column(Text, nullable=False)
    staged_filename = Column(Text, nullable=False)
    size_bytes = Column(Integer, nullable=False)
    staged_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)
```

(`BigInteger` import; relationship/backref per the RepoAttachment/MailIndexAttachment precedent.)
- [ ] **Step 3: migration 019** (`019_staging_areas.py`, revision "019", down "018"): plain `op.create_table` for both (no cross-schema tricks — these live in `public`), `sa.BigInteger` with `server_default=sa.text("0")`, unique constraint on `user_id`, index on `staging_messages.staging_id`. FKs with the ondeletes above (no SQLite branching needed — single schema). Downgrade drops both tables.
- [ ] **Step 4:** suite green (drift hook validates parity), ruff; commit `feat(staging): staging area models + migration` (model+migration together).

---

### Task 2: Dovecot surface — writable Staging namespace

**Files:** `config_generator.py`, `routers/dovecot.py`, tests (`tests/test_config_generator.py` + the dovecot userdb test file).

- [ ] **Step 1: failing tests** — (a) config_generator: `_dovecot_acl_file()` output contains the three lines below, in this order (default-deny first); (b) userdb: a user WITH an active StagingArea gets an extra namespace `{"name": "stg_<user.id>", "prefix": "Staging/", "mail_driver": "maildir", "mail_path": <home>/staging, "inbox": False}`; a user WITHOUT gets none; an EXPIRED area gets none (filter `expires_at > now`).
- [ ] **Step 2: ACL** in `config_generator.py`:

```python
def _dovecot_acl_file() -> str:
    # Everything read-only except the per-user staging namespace, which is
    # the curation surface for restores (delete-before-push in webmail).
    # lrwstie = lookup/read/write-flags/write-seen/write-deleted/insert/expunge
    # — no create/delete-mailbox/admin.
    return "* owner lrs\nStaging owner lrwstie\nStaging/* owner lrwstie\n"
```

- [ ] **Step 3: userdb** in `routers/dovecot.py` (after the recoveries block, before the return):

```python
    staging = (
        db.query(StagingArea)
        .filter(StagingArea.user_id == user.id)
        .filter(StagingArea.expires_at > datetime.now(UTC))
        .first()
    )
    if staging:
        namespaces.append(
            {
                "name": f"stg_{user.id}",
                "prefix": "Staging/",
                "mail_driver": "maildir",
                "mail_path": f"{home}/staging",
                "inbox": False,
            }
        )
```

(imports: `StagingArea`, `datetime`/`UTC` — check what the module already imports.)
- [ ] **Step 4:** suite + ruff; commit `feat(staging): writable Staging/ namespace via userdb + global ACL`.

---

### Task 3: staging_service — create/add/reconcile/empty/status

**Files:** create `services/staging_service.py`; tests `tests/test_staging_service.py` (reuse Maildir helpers from `tests/test_index_attachments.py`; snapshot path mocks restic exactly like `tests/test_preview_service.py`).

- [ ] **Step 1: failing tests** (real Maildirs, real indexer, restic mocked):
  - `test_add_live_message_copies_file_and_accounts_bytes` — index a live message, `add_messages(db, user, [(acc.id, hash)])` → file exists in `<home>/staging/cur/`, one StagingMessage row (origin folder + source account + size), `bytes_used` == file size, area created with `expires_at ≈ now + settings.staging_ttl_minutes`.
  - `test_add_snapshot_only_message_uses_dump_file` — deleted row + SnapshotMessage bit + BackupPolicy; mock `staging_service.restic_service.dump_file` to return the captured raw bytes → staged.
  - `test_quota_exceeded_rejects_before_copy` — `monkeypatch settings.staging_max_bytes` to a tiny value → `add_messages` raises `StagingQuotaExceededError` (custom exception in the module) and NO file/row/bytes written.
  - `test_add_is_idempotent_per_message` — adding the same (account, hash) twice → one file, one row.
  - `test_reconcile_drops_rows_for_deleted_files` — stage 2 messages, `os.remove` one staged file (webmail deletion), `reconcile(db, staging)` → 1 row left, `bytes_used` shrunk.
  - `test_empty_removes_files_rows_area` — `empty(db, user)` → staging dir gone (or empty), rows gone, area gone.
  - `test_visibility_enforced` — `add_messages` for an account the user cannot access raises `ValueError` (reuse `search_service._accessible_account_ids` for the check; admin path covered in Task 0's API tests).
- [ ] **Step 2: implement** `services/staging_service.py`:

```python
"""Per-user staging area — copy-in, reconcile, quota, lifecycle.

The staging Maildir ({dovecot_home}/staging) is the source of truth for
contents: webmail deletions remove files and reconcile() drops their rows.
Rows carry origin (account + folder) for push-to-origin and the byte
accounting that backs the quota. One area per user; TTL from creation.
"""

import logging
import os
import re
import shutil
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from mailfallback.config import settings
from mailfallback.models import (
    Account,
    BackupPolicy,
    MailIndexMessage,
    SnapshotMessage,
    StagingArea,
    StagingMessage,
    User,
)
from mailfallback.services import restic_service
from mailfallback.services.index_service import maildir_folder_bases, maildir_filename_prefix
from mailfallback.services.search_service import _accessible_account_ids

logger = logging.getLogger(__name__)


class StagingQuotaExceededError(Exception):
    pass


def _safe_username(username: str) -> str:
    return re.sub(r"[^a-zA-Z0-9@._-]", "_", username)


def staging_dir(user: User) -> str:
    """{store}/.dovecot-home/{username}/staging — same home the userdb serves."""
    return os.path.join(user.store.path, ".dovecot-home", _safe_username(user.username), "staging")


def _ensure_maildir(path: str) -> None:
    for sub in ("cur", "new", "tmp"):
        os.makedirs(os.path.join(path, sub), exist_ok=True)


def get_status(db: Session, user: User) -> dict:
    area = db.query(StagingArea).filter(StagingArea.user_id == user.id).first()
    if not area:
        return {"exists": False, "count": 0, "bytes_used": 0, "expires_at": None,
                "max_bytes": settings.staging_max_bytes}
    reconcile(db, user, area)
    count = db.query(StagingMessage).filter(StagingMessage.staging_id == area.id).count()
    return {"exists": True, "count": count, "bytes_used": area.bytes_used,
            "expires_at": area.expires_at.isoformat(), "max_bytes": area.max_bytes}


def _get_or_create_area(db: Session, user: User) -> StagingArea:
    area = db.query(StagingArea).filter(StagingArea.user_id == user.id).first()
    if area:
        return area
    area = StagingArea(
        user_id=user.id,
        expires_at=datetime.now(UTC) + timedelta(minutes=settings.staging_ttl_minutes),
        max_bytes=settings.staging_max_bytes,
    )
    db.add(area)
    db.flush()
    return area


def _message_bytes(db: Session, account: Account, row: MailIndexMessage) -> bytes | None:
    """Live file first (index locator, both INBOX bases, prefix fallback),
    else newest snapshot via restic dump — same strategy as preview_service."""
    from mailfallback.services.preview_service import _locate_live_file, _snapshot_bytes

    if row.deleted_at is None:
        path = _locate_live_file(account, row)
        if path:
            try:
                with open(path, "rb") as f:
                    return f.read()
            except OSError:
                pass
    found = _snapshot_bytes(db, account, row)
    return found[0] if found else None


def add_messages(db: Session, user: User, items: list[tuple[str, bytes]],
                 include_all: bool = False) -> dict:
    """Copy messages into the user's staging Maildir. items = [(account_id, hash)].

    Quota is checked BEFORE any copy. Returns {staged, skipped, failed}.
    Idempotent per (account, hash): already-staged messages are skipped.
    """
    from mailfallback.models import UserRole

    visible = set(_accessible_account_ids(db, user))
    area = _get_or_create_area(db, user)
    sdir = staging_dir(user)
    _ensure_maildir(sdir)
    reconcile(db, user, area)

    existing = {
        (m.source_account_id, m.message_id_hash)
        for m in db.query(StagingMessage).filter(StagingMessage.staging_id == area.id)
    }

    to_stage: list[tuple[Account, MailIndexMessage, bytes]] = []
    failed = 0
    for account_id, h in items:
        if account_id not in visible and not (include_all and user.role == UserRole.admin):
            raise ValueError(f"Account {account_id} not accessible")
        if (account_id, h) in existing:
            continue
        account = db.query(Account).filter(Account.id == account_id).first()
        row = (
            db.query(MailIndexMessage)
            .filter(MailIndexMessage.account_id == account_id,
                    MailIndexMessage.message_id_hash == h)
            .first()
        )
        if not account or not row:
            failed += 1
            continue
        raw = _message_bytes(db, account, row)
        if raw is None:
            failed += 1
            continue
        to_stage.append((account, row, raw))

    incoming = sum(len(raw) for _, _, raw in to_stage)
    if area.max_bytes and area.bytes_used + incoming > area.max_bytes:
        raise StagingQuotaExceededError(
            f"Staging quota exceeded: {area.bytes_used + incoming} > {area.max_bytes} bytes"
        )

    staged = 0
    now = datetime.now(UTC)
    for account, row, raw in to_stage:
        fname = f"{int(now.timestamp())}.s{staged}.{row.message_id_hash.hex()[:12]}:2,"
        try:
            with open(os.path.join(sdir, "cur", fname), "wb") as f:
                f.write(raw)
        except OSError:
            logger.warning("Staging copy failed for %s", row.message_id_hash.hex(),
                           exc_info=True)
            failed += 1
            continue
        db.add(StagingMessage(
            staging_id=area.id,
            source_account_id=account.id,
            message_id_hash=row.message_id_hash,
            original_folder=row.folder_path,
            staged_filename=fname,
            size_bytes=len(raw),
        ))
        area.bytes_used += len(raw)
        staged += 1
    db.commit()
    return {"staged": staged, "skipped": len(items) - staged - failed, "failed": failed}


def reconcile(db: Session, user: User, area: StagingArea) -> int:
    """Drop rows whose file vanished (webmail deletion); recompute bytes_used.
    Filenames are matched by stable prefix — Dovecot renames on flag changes."""
    sdir = staging_dir(user)
    on_disk: dict[str, str] = {}
    for sub in ("cur", "new"):
        d = os.path.join(sdir, sub)
        if not os.path.isdir(d):
            continue
        for fn in os.listdir(d):
            on_disk[maildir_filename_prefix(fn)] = fn
    dropped = 0
    total = 0
    for m in db.query(StagingMessage).filter(StagingMessage.staging_id == area.id).all():
        actual = on_disk.get(maildir_filename_prefix(m.staged_filename))
        if actual is None:
            db.delete(m)
            dropped += 1
        else:
            if actual != m.staged_filename:
                m.staged_filename = actual
            total += m.size_bytes
    area.bytes_used = total
    db.commit()
    return dropped


def empty(db: Session, user: User) -> None:
    area = db.query(StagingArea).filter(StagingArea.user_id == user.id).first()
    sdir = staging_dir(user)
    if os.path.isdir(sdir):
        shutil.rmtree(sdir, ignore_errors=True)
    if area:
        db.delete(area)  # cascade removes rows
        db.commit()


def cleanup_expired(db: Session) -> int:
    """Scheduler entrypoint — purge expired areas (files + rows). Always on."""
    expired = (
        db.query(StagingArea)
        .filter(StagingArea.expires_at <= datetime.now(UTC))
        .all()
    )
    for area in expired:
        user = db.query(User).filter(User.id == area.user_id).first()
        if user:
            sdir = staging_dir(user)
            if os.path.isdir(sdir):
                shutil.rmtree(sdir, ignore_errors=True)
        db.delete(area)
    db.commit()
    return len(expired)
```

  Check `User.store` relationship exists (users have store_id FK — `grep -n "store = relationship" src/mailfallback/models.py`); if the attribute is named differently, adapt. `MailStore.path` — verify attribute name (`path` vs `store_path`).
- [ ] **Step 3:** suite + ruff; commit `feat(staging): staging service — copy-in, reconcile, quota, lifecycle`.

---

### Task 4: Scheduler cleanup job (always on)

- [ ] **Step 1: failing test** (follow the mount-cleanup test pattern — `grep -rn "mount-cleanup\|mount_cleanup" tests/ src/mailfallback/services/scheduler.py`): `start_scheduler` registers job id `staging-cleanup`; the job function calls `staging_service.cleanup_expired` (mock SessionLocal like `_run_mount_cleanup`'s test does, or assert via expired-area integration if that's the existing style).
- [ ] **Step 2: implement** in `scheduler.py` (mirror `_run_mount_cleanup`):

```python
def _run_staging_cleanup() -> None:
    from mailfallback.services import staging_service

    db = SessionLocal()
    try:
        n = staging_service.cleanup_expired(db)
        if n:
            logger.info("Staging cleanup: purged %d expired area(s)", n)
    except Exception:
        logger.exception("Staging cleanup failed")
    finally:
        db.close()
```

  In `start_scheduler`, after the mount-cleanup registration:

```python
    if not any(j.id == "staging-cleanup" for j in scheduler.get_jobs()):
        scheduler.add_job(
            _run_staging_cleanup,
            CronTrigger(minute="*/15"),
            id="staging-cleanup",
            replace_existing=True,
        )
```

- [ ] **Step 3:** suite + ruff; commit `feat(staging): scheduled cleanup of expired staging areas`.

---

### Task 5: Staging API endpoints

**Files:** `routers/restore.py`; tests `tests/test_staging_api.py` (login/ownership patterns as in `tests/test_restore_preview_api.py`).

- [ ] **Step 1: failing tests**: GET `/api/restore/staging` (no area → exists False); POST `/api/restore/staging/items` with one live indexed message → 200 {staged:1}, audit row `staging.add`; quota exceeded → 413 with detail; DELETE → empties + audit `staging.empty`; non-owner item → 403/400; unauthenticated → 401 (follow get_current_user behavior).
- [ ] **Step 2: implement** (after the resolve-uids endpoint):

```python
class StagingItemsRequest(BaseModel):
    items: list[dict]  # [{"account_id": ..., "message_id_hash": hex}]
    include_all: bool = False


@router.get("/staging")
def api_staging_status(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    from mailfallback.services import staging_service

    return staging_service.get_status(db, user)


@router.post("/staging/items")
def api_staging_add(
    req: StagingItemsRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    from mailfallback.services import staging_service

    try:
        items = [(i["account_id"], bytes.fromhex(i["message_id_hash"])) for i in req.items[:200]]
    except (KeyError, ValueError):
        raise HTTPException(status_code=400, detail="Invalid items") from None
    try:
        result = staging_service.add_messages(db, user, items, include_all=req.include_all)
    except staging_service.StagingQuotaExceededError as e:
        raise HTTPException(status_code=413, detail=str(e)) from None
    except ValueError as e:
        raise HTTPException(status_code=403, detail=str(e)) from None
    log_action(db, user=user, action="staging.add", resource_type="staging",
               details=result, ip_address=request.client.host if request.client else None)
    return result


@router.delete("/staging")
def api_staging_empty(
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    from mailfallback.services import staging_service

    staging_service.empty(db, user)
    log_action(db, user=user, action="staging.empty", resource_type="staging",
               ip_address=request.client.host if request.client else None)
    return {"ok": True}
```

- [ ] **Step 3:** suite + ruff; commit `feat(staging): status/add/empty endpoints with audit`.

---

### Task 6: Push upstream — worker mode + endpoint

**Files:** `services/restore_worker.py`, `services/staging_service.py` (push orchestration), `routers/restore.py`; tests `tests/test_restore_worker.py`, `tests/test_staging_api.py`.

Design (locked): push groups surviving staged messages by target (origin per row, or one override account), creates ONE `RestoreJob` per target with `restore_mode="staging_push"`, `source_account_id = target_account_id = <target>`, and `selected_uids = {<upstream folder>: [staged_filename, ...]}` (the JSON column reused as the file manifest — folder keys are the DESTINATION folders, already mapped per `folder_mode`: `original_folder` or `Restored/<YYYY-MM-DD>`). The worker branch reads each file from the requester's staging dir and APPENDs to the target upstream (XOAUTH2-aware, existing retry + `_ensure_folder` plumbing); **no source IMAP connection**. On job success the worker deletes the pushed rows' files + rows via a completion hook; the area dies when empty (next reconcile/status).

- [ ] **Step 1: failing worker test** (in `tests/test_restore_worker.py`, mirroring the selection-mode test style): build a user + staging dir with 2 staged files, a `RestoreJob(restore_mode="staging_push", selected_uids={"INBOX": [f1], "Restored/2026-06-11": [f2]}, requested_by=user.id, ...)`; mock target `connect_imap`; run `execute_restore_job`; assert APPEND called once per file with the right folder, no source Dovecot connection attempted, job completed, staged rows for pushed files deleted, files removed.
- [ ] **Step 2: worker implementation** — in `execute_restore_job`, branch BEFORE the Dovecot source connect:

```python
        if job.restore_mode == RestoreMode.staging_push:
            _execute_staging_push(db, job, target, tgt_password, tgt_auth_method)
            return
```

  (`RestoreMode` — check whether restore_mode is an Enum or plain string in the model: `grep -n "restore_mode" src/mailfallback/models.py`; follow what `selection` does. If plain string, compare to "staging_push".) New function in the worker:

```python
def _execute_staging_push(db, job, target, tgt_password, tgt_auth_method):
    """Push staged files to the target upstream. selected_uids is the manifest:
    {destination_folder: [staged_filename, ...]}. Files live in the
    requester's staging Maildir; no source IMAP connection is needed."""
    from mailfallback.models import StagingArea, StagingMessage, User
    from mailfallback.services.staging_service import staging_dir

    requester = db.query(User).filter(User.id == job.requested_by).first()
    if not requester:
        _fail_job(db, job, "Requesting user no longer exists")
        return
    sdir = staging_dir(requester)

    tgt_conn = connect_imap(
        target.imap_host, target.imap_port, target.tls_type or "IMAPS",
        target.imap_user or target.email_address, tgt_password,
        auth_method=tgt_auth_method,
    )
    try:
        tgt_separator = _get_hierarchy_separator(tgt_conn)
        manifest: dict[str, list[str]] = job.selected_uids or {}
        total = sum(len(v) for v in manifest.values())
        job.total_messages = total
        db.commit()
        pushed_files: list[str] = []
        for folder, filenames in manifest.items():
            tgt_folder = folder.replace("/", tgt_separator) if tgt_separator != "/" else folder
            _ensure_folder(tgt_conn, tgt_folder, tgt_separator)
            for fn in filenames:
                path = None
                for sub in ("cur", "new"):
                    cand = os.path.join(sdir, sub, fn)
                    if os.path.exists(cand):
                        path = cand
                        break
                if path is None:
                    # match flag-renamed files by stable prefix
                    from mailfallback.services.index_service import maildir_filename_prefix
                    want = maildir_filename_prefix(fn)
                    for sub in ("cur", "new"):
                        d = os.path.join(sdir, sub)
                        if not os.path.isdir(d):
                            continue
                        for actual in os.listdir(d):
                            if maildir_filename_prefix(actual) == want:
                                path = os.path.join(d, actual)
                                break
                        if path:
                            break
                if path is None:
                    job.skipped_messages += 1
                    continue
                with open(path, "rb") as f:
                    raw = f.read()
                if _is_duplicate(tgt_conn, tgt_folder, raw) if job.skip_duplicates else False:
                    job.skipped_messages += 1
                else:
                    _retry_imap(tgt_conn.append, f'"{tgt_folder}"', None, None, raw)
                    job.restored_messages += 1
                pushed_files.append(fn)
                db.commit()
        # success: drop pushed rows + files; area dies when empty
        area = db.query(StagingArea).filter(StagingArea.user_id == requester.id).first()
        if area and pushed_files:
            for m in (
                db.query(StagingMessage)
                .filter(StagingMessage.staging_id == area.id,
                        StagingMessage.staged_filename.in_(pushed_files))
                .all()
            ):
                for sub in ("cur", "new"):
                    p = os.path.join(sdir, sub, m.staged_filename)
                    if os.path.exists(p):
                        os.remove(p)
                area.bytes_used = max(0, area.bytes_used - m.size_bytes)
                db.delete(m)
        job.status = JobStatus.completed
        job.completed_at = datetime.now(UTC)
        db.commit()
    except Exception as e:
        _fail_job(db, job, f"Staging push failed: {e}")
    finally:
        with contextlib.suppress(Exception):
            tgt_conn.logout()
```

  IMPORTANT adaptations while implementing (read the worker first — do NOT trust this snippet blindly):
  - `_ensure_folder`, `_get_hierarchy_separator`, `_retry_imap` — use their REAL signatures.
  - Duplicate detection: find how skip_duplicates works today (`grep -n "skip_dup\|Message-ID\|duplicate" src/mailfallback/services/restore_worker.py`) and REUSE that helper (extract if inline). If it keys on Message-Id via UID SEARCH HEADER, parse the Message-Id from `raw` headers (BytesHeaderParser) and reuse.
  - APPEND args: mirror `_restore_single_message`'s exact append call (flags/date handling) — keep `\Seen` default off, internaldate None.
  - The reconcile-before-push happens in the SERVICE (step 3), so the manifest only contains surviving files; the prefix fallback covers webmail flag renames between push click and job run.
- [ ] **Step 3: push orchestration** in `staging_service.py`:

```python
def push(db: Session, user: User, destination: str, folder_mode: str) -> list[str]:
    """Create one staging_push job per target. destination: "origin" | account_id.
    folder_mode: "original" | "restored". Returns job ids."""
    from mailfallback.services.restore_service import create_restore_job
    from mailfallback.services.restore_worker import submit_restore_job

    area = db.query(StagingArea).filter(StagingArea.user_id == user.id).first()
    if not area:
        return []
    reconcile(db, user, area)
    rows = db.query(StagingMessage).filter(StagingMessage.staging_id == area.id).all()
    if not rows:
        return []
    stamp = datetime.now(UTC).strftime("%Y-%m-%d")
    by_target: dict[str, dict[str, list[str]]] = {}
    for m in rows:
        target_id = m.source_account_id if destination == "origin" else destination
        folder = m.original_folder if folder_mode == "original" else f"Restored/{stamp}"
        by_target.setdefault(target_id, {}).setdefault(folder, []).append(m.staged_filename)
    job_ids = []
    for target_id, manifest in by_target.items():
        job = create_restore_job(
            db,
            source_account_id=target_id,
            target_account_id=target_id,
            restore_mode="staging_push",
            requested_by=user.id,
            selected_uids=manifest,
        )
        if job:
            submit_restore_job(job.id)
            job_ids.append(job.id)
    return job_ids
```

  Check `create_restore_job`'s signature/mode validation (`services/restore_service.py`) — if restore_mode is an Enum, add `staging_push` to it (model + enum change → migration ONLY if it's a DB enum; if it's a String column with a Python Enum, no migration; verify and follow the drift hook).
- [ ] **Step 4: endpoint** + tests (push with origin/override and both folder modes asserting the manifests; audit `staging.push` with job ids):

```python
class StagingPushRequest(BaseModel):
    destination: str = "origin"  # "origin" | account id
    folder_mode: str = "original"  # "original" | "restored"


@router.post("/staging/push")
def api_staging_push(
    req: StagingPushRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    from mailfallback.services import staging_service

    if req.destination != "origin":
        if not account_service.get_account(db, req.destination, user):
            raise HTTPException(status_code=404, detail="Destination account not found")
    if req.folder_mode not in ("original", "restored"):
        raise HTTPException(status_code=400, detail="Invalid folder_mode")
    job_ids = staging_service.push(db, user, req.destination, req.folder_mode)
    log_action(db, user=user, action="staging.push", resource_type="staging",
               details={"jobs": job_ids, "destination": req.destination,
                        "folder_mode": req.folder_mode},
               ip_address=request.client.host if request.client else None)
    return {"job_ids": job_ids}
```

- [ ] **Step 5:** suite + ruff; commit `feat(staging): push-upstream jobs from the staging area`.

---

### Task 7: UI — staging bar, Add to staging, push panel

**Files:** `restore_workspace.html`, `restore_workspace.js`, `style.css`; tests `tests/test_restore_ui.py`.

Match the frozen mock's bottom bar: `📥 Staging — N messaggi · X MB · scade tra Nd · quota ∞ [Apri in webmail] [Svuota] [Push upstream →]`.

- [ ] **Step 1: template** — inside the component root, after the results grid:

```html
  <div class="ws-staging-bar" x-show="staging.exists" x-transition>
    <i data-lucide="inbox" class="icon-md"></i>
    <span class="ws-staging-count" x-text="'Staging — ' + staging.count + ' message' + (staging.count === 1 ? '' : 's')"></span>
    <span class="ws-staging-meta text-muted text-xsmall"
          x-text="fmtSize(staging.bytes_used) + ' · expires ' + fmtExpiry(staging.expires_at) + ' · quota ' + (staging.max_bytes ? fmtSize(staging.max_bytes) : '∞')"></span>
    <span class="ws-staging-grow"></span>
    {% if webmail_enabled and webmail_url %}
    <a class="ws-btn-ghost" href="{{ webmail_url }}?_task=mail&_mbox=Staging" target="_blank" rel="noopener">Open in webmail</a>
    {% endif %}
    <button class="ws-btn-ghost ws-btn-danger" @click="emptyStaging()">Empty</button>
    <button class="ws-btn-primary" @click="pushPanelOpen = !pushPanelOpen">Push upstream →</button>
  </div>

  <div class="ws-push-panel" x-show="pushPanelOpen" x-transition>
    <div class="ws-push-group">
      <span class="ws-field-label">Destination</span>
      <label class="ws-inline"><input type="radio" value="origin" x-model="pushDestination"> Each message back to its origin mailbox</label>
      <label class="ws-inline"><input type="radio" value="override" x-model="pushDestination"> Everything into:
        <select x-model="pushOverrideId" :disabled="pushDestination !== 'override'">
          <template x-for="a in accounts" :key="a.id"><option :value="a.id" x-text="a.name + ' (' + a.email + ')'"></option></template>
        </select>
      </label>
    </div>
    <div class="ws-push-group">
      <span class="ws-field-label">Folder</span>
      <label class="ws-inline"><input type="radio" value="original" x-model="pushFolderMode"> Original folder (created if missing)</label>
      <label class="ws-inline"><input type="radio" value="restored" x-model="pushFolderMode"> Everything into Restored/&lt;today&gt;</label>
    </div>
    <button class="ws-btn-primary" :disabled="pushing" @click="pushStaging()">
      <span x-text="pushing ? 'Pushing…' : 'Confirm push'"></span>
    </button>
  </div>
```

  Add to the preview pane actions: `<button class="ws-btn-primary" @click="addToStaging([previewRef])" x-show="previewRef">📥 Add to staging</button>` (keep a `previewRef` of the result whose preview is open). Add to the selection action bar: `<button class="ws-btn-ghost" :disabled="selected.length === 0" @click="addSelectedToStaging()">📥 Add to staging</button>`.
- [ ] **Step 2: JS** — state: `staging: {exists:false,count:0,bytes_used:0,expires_at:null,max_bytes:0}`, `pushPanelOpen:false`, `pushDestination:'origin'`, `pushOverrideId:''`, `pushFolderMode:'original'`, `pushing:false`, `previewRef:null` (set in `openPreview`). Methods (all with try/catch + statusText, refreshIcons in finally — house style):
  - `refreshStaging()` → GET `/api/restore/staging` → `this.staging`; call in `init()` and after every mutation.
  - `addToStaging(results)` / `addSelectedToStaging()` → POST `/staging/items` with `{items: results.map(r => ({account_id: r.account_id, message_id_hash: r.message_id_hash})), include_all: this.includeAll}`; statusText from `{staged, skipped, failed}`; 413 → show quota error detail.
  - `emptyStaging()` → `confirm('Empty the staging area? Files are removed.')` then DELETE.
  - `pushStaging()` → POST `/staging/push` `{destination: pushDestination === 'origin' ? 'origin' : pushOverrideId, folder_mode: pushFolderMode}`; status `Started N push job(s)`; close panel; `refreshStaging()`.
  - `fmtExpiry(iso)` → days/hours remaining ("in 6d" / "in 3h").
- [ ] **Step 3: CSS** (workspace section):

```css
.ws-staging-bar { position: fixed; left: 220px; right: 0; bottom: 0; z-index: 50;
  background: var(--ws-card); border-top: 1px solid var(--ws-border);
  padding: 0.7rem 2rem; display: flex; align-items: center; gap: 1rem; }
.ws-staging-count { font-weight: 650; }
.ws-staging-grow { flex: 1; }
.ws-btn-danger { color: var(--ws-danger); border-color: var(--ws-danger); }
.ws-push-panel { position: fixed; right: 2rem; bottom: 64px; z-index: 51; width: 360px;
  background: var(--ws-card); border: 1px solid var(--ws-border); border-radius: 10px;
  padding: 1rem; display: flex; flex-direction: column; gap: 0.8rem;
  box-shadow: 0 8px 24px rgba(0,0,0,0.25); }
.ws-push-group { display: flex; flex-direction: column; gap: 0.3rem; }
.page-restore-workspace .main, body.page-restore-workspace .content { padding-bottom: 5rem; }
@media (max-width: 768px) { .ws-staging-bar { left: 0; flex-wrap: wrap; } }
```

  (Verify the actual content container selector for the bottom padding — `.content` per base layout; the `left: 220px` must match the sidebar width var/breakpoint used by `.content { margin-left: 220px }`.)
- [ ] **Step 4: template tests** — extend `tests/test_restore_ui.py`: page renders with staging bar markup (`ws-staging-bar` present), push panel radios present.
- [ ] **Step 5:** suite + ruff + `node --check`; commit `feat(ui): staging bar, add-to-staging, push panel`.

---

### Task 8: Live verification vs mockup + e2e + critic

- [ ] Rebuild from worktree (`docker compose -p mailfallback --project-directory <worktree> up -d --build mailfallback`), `alembic upgrade head` (019), healthz 200.
- [ ] Temp admin + temporary ownership (as Plan 1's verification; clean up after — including FK children before user delete).
- [ ] Browser e2e: search → select 2 messages (one live, one snapshot-only if available) → Add to staging → bar appears with count/bytes → **webmail check**: login Roundcube, the `Staging` folder lists the messages, DELETE one there → back in MFB, staging count drops after refresh (reconcile) → Push upstream (origin + original folder) → job completes; live message ends `skipped` (duplicate upstream), previously-deleted-upstream message ends `restored: 1` if one was staged, else accept skipped — record actuals.
- [ ] Verify ACL really blocks writes elsewhere (try moving a message into a NON-staging folder in Roundcube → must fail) and allows expunge in Staging.
- [ ] Screenshots dark+light 1440 + 420 → `.claude/plan2_*.png`; side-by-side vs `.claude/mockup_restore_staging_reference.png` — the staging bar is the contract centerpiece this time.
- [ ] Gemini critic pass; act only on findings consistent with the design system.
- [ ] Full suite + ruff one last time; cleanup temp data; report with screenshots.

---

## Self-review notes

- Spec coverage: staging model/lifecycle/quota (T1,3,4), Dovecot RW namespace + ACL (T2), endpoints + audit (T5), push modes + worker (T6), UI bar/panel/webmail link (T7), live verify (T8), admin toggle decision (T0). Attachment view/download/Tika = Plan 3.
- Cross-task types: `selected_uids` manifest is `dict[str, list[str]]` in both producer (T6 service) and consumer (T6 worker); staging status dict shape shared between T3 service, T5 endpoint, T7 JS.
- Open seams flagged inline: restore_mode enum vs string (T6), duplicate-detection helper reuse (T6), `User.store`/`MailStore.path` attribute names (T3), content-container selector (T7).
