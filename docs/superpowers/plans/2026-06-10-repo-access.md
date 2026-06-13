# Repository Access Control Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Per-user repository access mirroring the mail-store `allowed_stores` pattern: non-admin users can only point backup policies at repositories an admin granted them, with grandfathering for existing policies.

**Architecture:** One new M2M table (`user_allowed_repositories`, migration 017 with an in-migration backfill granting all existing repos to all existing users), a `set_allowed_repositories` service + admin Users checkbox group cloned from allowed-stores, route-level enforcement in `account_backup_configure`, a filtered `backup_destinations` context on the account page, and the table added to the DR config export.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy/Alembic, Jinja2. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-06-10-repo-access-design.md`

**Branch:** `feat/repo-access` off `main` (main includes PR #175, merge commit c19e980; baseline 632 tests).

**Conventions (unchanged from previous cycles):**
- Tests `uv run pytest tests/<file> -n auto -v`; full suite `uv run pytest tests/ -n auto -q`; lint `uv run ruff check src/ tests/` + `ruff format --check`.
- Model + migration in ONE commit (drift hook). Route-level admin checks use `user.role.value != "admin"`. detect-secrets: `# pragma: allowlist secret` on fake secrets.
- Admin route tests: `tests/test_ui_backup_admin.py` helpers (`_login_admin`, `_mk_account`, `_mk_repo`, `S3_FORM`).

---

### Task 0: Branch

- [ ] **Step 1:** `git checkout main && git pull && git checkout -b feat/repo-access`
- [ ] **Step 2:** `uv run pytest tests/ -n auto -q` → expect 632 passed.

No commit.

---

### Task 1: Model + migration 017 with backfill

**Files:**
- Modify: `src/mailfallback/models.py` (association table near `user_allowed_stores` line ~120; relationship on User near `allowed_stores` line ~159)
- Create: `alembic/versions/017_user_allowed_repositories.py`
- Test: `tests/test_models.py` (relationship test) + create `tests/test_migration_backfill.py` (upgrade-path test)

**Model + migration in ONE commit.**

- [ ] **Step 1: Failing model test** (append to `tests/test_models.py`):

```python
def test_user_allowed_repositories_relationship(db_session):
    from mailfallback.models import MailStore, Repository, User, UserRole

    store = MailStore(name="s17", path="/data/m17")
    db_session.add(store)
    db_session.flush()
    user = User(username="u17", password_hash="x", role=UserRole.user, store_id=store.id)
    repo = Repository(name="r17", backend_type="s3", restic_password="enc")
    db_session.add_all([user, repo])
    db_session.flush()

    user.allowed_repositories.append(repo)
    db_session.commit()
    db_session.refresh(user)

    assert [r.id for r in user.allowed_repositories] == [repo.id]
    # deleting the repo cascades the grant row away
    db_session.delete(repo)
    db_session.commit()
    db_session.refresh(user)
    assert user.allowed_repositories == []
```

- [ ] **Step 2:** Run `-k user_allowed_repositories` → FAIL (no attribute).

- [ ] **Step 3: Model.** In `models.py`, after the `user_allowed_stores` Table:

```python
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
```

In `class User`, after `allowed_stores`:

```python
    allowed_repositories = relationship(
        "Repository", secondary=user_allowed_repositories, passive_deletes=True
    )
```

NOTE: `passive_deletes=True` lets the DB-level CASCADE remove grant rows when a Repository or User is deleted, instead of the ORM null-ing composite-PK columns (the cascade lesson from migration 015 — write the model this way from the start). Check whether `user_allowed_stores` columns carry `ondelete` (they DON'T, models.py:120-125) — for the NEW table we use CASCADE deliberately; do not "fix" the old table in this commit. Verify the relationship-delete test passes with this setup; if SQLAlchemy still tries to delete association rows itself for secondary relationships (it does, via the secondary table — which is fine and correct for M2M), the test simply confirms behavior.

- [ ] **Step 4: Migration** `alembic/versions/017_user_allowed_repositories.py` (confirm 016's revision string):

```python
"""user allowed repositories

Per-user repository access, mirroring user_allowed_stores. The backfill
grants every existing repository to every existing user so behavior at
upgrade time is unchanged; admins prune afterwards. New users start empty.

Revision ID: 017
Revises: 016
Create Date: 2026-06-10
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "017"
down_revision: str | Sequence[str] | None = "016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "user_allowed_repositories",
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("repository_id", sa.String(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["repository_id"], ["backup_destinations.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("user_id", "repository_id"),
    )
    op.execute(
        "INSERT INTO user_allowed_repositories (user_id, repository_id) "
        "SELECT u.id, r.id FROM users u CROSS JOIN backup_destinations r"
    )


def downgrade() -> None:
    op.drop_table("user_allowed_repositories")
```

(Compare constraint style against migration 015/016 and `tests/test_alembic_sync.py` expectations — match what keeps the drift check green; the association table has a composite PK, mirror how migration 001/006 created `user_allowed_stores`-style tables: read one of them first.)

- [ ] **Step 5: Backfill test.** Create `tests/test_migration_backfill.py`. READ `tests/test_alembic_sync.py` first to copy its alembic-config mechanics (how it points alembic at a temp SQLite URL):

```python
"""Migration 017 backfill: existing users get all existing repositories."""

import sqlalchemy as sa
from alembic import command
from alembic.config import Config


def _alembic_config(db_url: str) -> Config:
    # Mirror tests/test_alembic_sync.py's config setup (script_location, url override).
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", db_url)
    return cfg


def test_migration_017_backfills_grants(tmp_path):
    db_url = f"sqlite:///{tmp_path}/mig.db"
    cfg = _alembic_config(db_url)

    command.upgrade(cfg, "016")

    engine = sa.create_engine(db_url)
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO mail_stores (id, name, path) VALUES ('st1', 's', '/data/x')"
            )
        )
        for uid in ("u1", "u2"):
            conn.execute(
                sa.text(
                    "INSERT INTO users (id, username, role, enabled, store_id, migrating, preferences) "
                    "VALUES (:id, :id, 'user', 1, 'st1', 0, '{}')"
                ),
                {"id": uid},
            )
        for rid in ("r1", "r2"):
            conn.execute(
                sa.text(
                    "INSERT INTO backup_destinations "
                    "(id, name, backend_type, restic_password, insecure_tls, config_backup_enabled) "
                    "VALUES (:id, :id, 's3', 'enc', 0, 0)"
                ),
                {"id": rid},
            )
    engine.dispose()

    command.upgrade(cfg, "017")

    engine = sa.create_engine(db_url)
    with engine.connect() as conn:
        rows = conn.execute(
            sa.text("SELECT user_id, repository_id FROM user_allowed_repositories")
        ).fetchall()
    engine.dispose()
    assert sorted(rows) == [("u1", "r1"), ("u1", "r2"), ("u2", "r1"), ("u2", "r2")]
```

ADAPT the INSERT column lists to the real NOT NULL columns at revision 016 (run `command.upgrade(cfg, "016")` once and inspect, or read migrations 001-016 for the users/backup_destinations columns; enum columns store values like 'user'/'s3' — verify how the existing schema stores enum values in SQLite, native strings). If a NOT NULL column is missing from the INSERT the test will tell you immediately — fix the INSERT, not the schema.

- [ ] **Step 6:** Run `uv run pytest tests/test_models.py tests/test_alembic_sync.py tests/test_migration_backfill.py -n auto -q` → PASS. Full suite + ruff.

- [ ] **Step 7: Commit (atomic):**

```bash
git add src/mailfallback/models.py alembic/versions/017_user_allowed_repositories.py tests/
git commit -m "feat(backup): user_allowed_repositories table with upgrade backfill"
```

---

### Task 2: Service + admin UI

**Files:**
- Modify: `src/mailfallback/services/store_service.py` — NO. The repository grant service belongs beside repositories, not stores: add to `src/mailfallback/services/account_service.py`? Also no. Decision: create the function in `src/mailfallback/services/user_service.py` (it manages users; the grant is a user attribute, and `set_allowed_stores` lives in store_service only for historical reasons). Place `set_allowed_repositories` in `user_service.py`.
- Modify: `src/mailfallback/routers/ui_admin.py` (new route after `admin_set_allowed_stores` line ~381; pass `repositories` into the users page context — find the `admin_users_page` route and add `db.query(Repository).all()`)
- Modify: `src/mailfallback/templates/admin_users.html` (checkbox block + toggle button)
- Modify: `src/mailfallback/services/audit_service.py` (ACTION_LABELS)
- Test: `tests/test_user_service.py` (service; check the file exists — `ls tests/test_user_service.py`; if missing, put service tests in tests/test_models.py's style in a new `tests/test_repo_access.py` and ALSO put the route tests there)

Decision for tests: create ONE new file `tests/test_repo_access.py` holding service + route + enforcement tests for this whole cycle (Tasks 2-4) — keeps the feature's tests together.

- [ ] **Step 1: Failing tests** — create `tests/test_repo_access.py`:

```python
"""Repository access control: grants service, admin UI, enforcement."""

from mailfallback.models import BackupPolicy, Repository, User, UserRole
from mailfallback.services.user_service import create_user, set_allowed_repositories


def _login_admin(client, db_session, default_store):
    user = create_user(db_session, "admin", "pass", UserRole.admin, store_id=default_store.id)
    client.post("/api/auth/login", json={"username": "admin", "password": "pass"})  # pragma: allowlist secret
    return user


def _mk_repo(db_session, name="repo-a"):
    r = Repository(name=name, backend_type="s3", restic_password="enc")
    db_session.add(r)
    db_session.commit()
    return r


class TestSetAllowedRepositories:
    def test_sets_and_replaces(self, db_session, default_store):
        user = create_user(db_session, "u1", "pass", UserRole.user, store_id=default_store.id)
        r1, r2 = _mk_repo(db_session, "r1"), _mk_repo(db_session, "r2")

        set_allowed_repositories(db_session, user.id, [r1.id])
        assert [r.id for r in user.allowed_repositories] == [r1.id]

        set_allowed_repositories(db_session, user.id, [r2.id])
        db_session.refresh(user)
        assert [r.id for r in user.allowed_repositories] == [r2.id]

    def test_unknown_ids_ignored(self, db_session, default_store):
        user = create_user(db_session, "u2", "pass", UserRole.user, store_id=default_store.id)
        r1 = _mk_repo(db_session, "r3")

        set_allowed_repositories(db_session, user.id, [r1.id, "nonexistent"])

        assert [r.id for r in user.allowed_repositories] == [r1.id]

    def test_unknown_user_returns_error(self, db_session):
        assert set_allowed_repositories(db_session, "ghost", []) is not None


class TestAllowedRepositoriesRoute:
    def test_admin_sets_grants(self, client, db_session, default_store):
        _login_admin(client, db_session, default_store)
        target = create_user(db_session, "u4", "pass", UserRole.user, store_id=default_store.id)
        r1 = _mk_repo(db_session, "r4")

        resp = client.post(
            f"/admin/users/{target.id}/allowed-repositories",
            data={"repository_ids": [r1.id]},
            follow_redirects=False,
        )

        assert resp.status_code == 303
        db_session.refresh(target)
        assert [r.id for r in target.allowed_repositories] == [r1.id]

    def test_non_admin_rejected(self, client, db_session, default_store):
        create_user(db_session, "u5", "pass", UserRole.user, store_id=default_store.id)
        client.post("/api/auth/login", json={"username": "u5", "password": "pass"})  # pragma: allowlist secret
        target = create_user(db_session, "u6", "pass", UserRole.user, store_id=default_store.id)

        resp = client.post(
            f"/admin/users/{target.id}/allowed-repositories",
            data={"repository_ids": []},
            follow_redirects=False,
        )

        assert resp.status_code == 303
        assert target.allowed_repositories == []
```

- [ ] **Step 2:** Run → FAIL (import error / 404).

- [ ] **Step 3: Service** in `user_service.py` (match its existing function style — read neighbors):

```python
def set_allowed_repositories(db: Session, user_id: str, repository_ids: list[str]) -> str | None:
    """Replace the user's allowed-repositories set. Returns an error string or None."""
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        return "User not found"
    repositories = db.query(Repository).filter(Repository.id.in_(repository_ids)).all()
    user.allowed_repositories = repositories
    db.commit()
    return None
```

(Import `Repository` in user_service's models import. No home-store-style guard — repositories have no "home" equivalent.)

- [ ] **Step 4: Route** in `ui_admin.py` after `admin_set_allowed_stores`:

```python
@router.post("/admin/users/{target_user_id}/allowed-repositories")
async def admin_set_allowed_repositories(
    target_user_id: str, request: Request, db: Session = Depends(get_db)
):
    user = _get_session_user(request, db)
    if not user or user.role.value != "admin":
        return RedirectResponse("/", status_code=303)
    form = await request.form()
    repository_ids = form.getlist("repository_ids")
    error = set_allowed_repositories(db, target_user_id, repository_ids)
    if error:
        logger.warning("set_allowed_repositories refused for %s: %s", target_user_id, error)
    log_action(
        db,
        user=user,
        action="user.set_allowed_repositories",
        resource_type="user",
        resource_id=target_user_id,
        ip_address=request.client.host if request.client else None,
    )
    return RedirectResponse("/admin/users", status_code=303)
```

Import `set_allowed_repositories` next to the existing `set_allowed_stores` import (it comes from user_service, not store_service — adjust the import line accordingly). Add `Repository` to ui_admin's models import if missing. In the users page route (`admin_users_page` — find it), add `"repositories": db.query(Repository).all()` to the context.

Add `"user.set_allowed_repositories": <label>` to ACTION_LABELS — read the existing `user.set_allowed_stores` label and mirror its phrasing exactly.

- [ ] **Step 5: Template** `admin_users.html`. Read the existing stores row (`<tr id="stores-{{ u.id }}" class="hidden">`, ~line 162) and its toggle button in the user row (find the button that reveals `stores-{{ u.id }}` — note whether it uses `toggleRow` or `data-show-target`). Clone both for repositories:

Row (after the stores row):

```html
        <tr id="repos-{{ u.id }}" class="hidden">
            <td colspan="6">
                <form method="post" action="/admin/users/{{ u.id }}/allowed-repositories">
                    <label><strong>Allowed repositories for {{ u.username }}:</strong></label>
                    <div class="flex gap-05 flex-wrap mt-025">
                        {% for r in repositories %}
                        <label class="checkbox-pill">
                            <input type="checkbox" name="repository_ids" value="{{ r.id }}"
                                {% if r in u.allowed_repositories %}checked{% endif %}>
                            {{ r.name }}
                        </label>
                        {% endfor %}
                        {% if not repositories %}
                        <span class="text-muted text-small">No repositories defined yet.</span>
                        {% endif %}
                    </div>
                    <button type="submit" class="icon-btn primary mt-05">
                        <i data-lucide="save" class="icon-md"></i> Save
                    </button>
                </form>
            </td>
        </tr>
```

Toggle button in the actions cell next to the stores toggle (mirror its exact mechanism; pick a distinct lucide icon, e.g. `cloud-upload` which the Repositories page already uses — verify it's in the vendored bundle; add `title="Allowed repositories"`). The `colspan` must match the table's actual column count — COUNT the `<th>` elements, don't trust the stores row (it may be stale; if it differs, use the correct count for the NEW row and leave the old one alone).

- [ ] **Step 6:** Tests + full suite + ruff. **Step 7: Commit:**

```bash
git add src/mailfallback/services/ src/mailfallback/routers/ui_admin.py src/mailfallback/templates/admin_users.html tests/test_repo_access.py
git commit -m "feat(backup): admin-managed per-user repository grants"
```

---

### Task 3: Enforcement in account_backup_configure

**Files:**
- Modify: `src/mailfallback/routers/ui_backup.py` (`account_backup_configure`, ~line 676)
- Test: `tests/test_repo_access.py` (extend)

- [ ] **Step 1: Failing tests** (append; reuse `_login_admin`, `_mk_repo` from the file; `_mk_account`-style helper inline):

```python
from mailfallback.models import Account


def _mk_account_owned(db_session, default_store, owner, name="acc-e", path="/data/m/acc-e"):
    acc = Account(name=name, imap_host="h", maildir_path=path, store_id=default_store.id)
    db_session.add(acc)
    db_session.flush()
    acc.owners.append(owner)
    db_session.commit()
    return acc


class TestConfigureEnforcement:
    def test_non_admin_rejected_on_non_allowed_repo(self, client, db_session, default_store):
        owner = create_user(db_session, "own1", "pass", UserRole.user, store_id=default_store.id)
        client.post("/api/auth/login", json={"username": "own1", "password": "pass"})  # pragma: allowlist secret
        acc = _mk_account_owned(db_session, default_store, owner, name="a1", path="/data/m/a1")
        repo = _mk_repo(db_session, "r-deny")

        resp = client.post(
            f"/accounts/{acc.id}/backup/configure",
            data={"destination_id": repo.id, "schedule": "0 2 * * *"},
            follow_redirects=False,
        )

        assert resp.status_code == 303
        assert db_session.query(BackupPolicy).count() == 0

    def test_non_admin_allowed_repo_accepted(self, client, db_session, default_store):
        owner = create_user(db_session, "own2", "pass", UserRole.user, store_id=default_store.id)
        client.post("/api/auth/login", json={"username": "own2", "password": "pass"})  # pragma: allowlist secret
        acc = _mk_account_owned(db_session, default_store, owner, name="a2", path="/data/m/a2")
        repo = _mk_repo(db_session, "r-allow")
        set_allowed_repositories(db_session, owner.id, [repo.id])

        resp = client.post(
            f"/accounts/{acc.id}/backup/configure",
            data={"destination_id": repo.id, "schedule": "0 2 * * *"},
            follow_redirects=False,
        )

        assert resp.status_code == 303
        assert db_session.query(BackupPolicy).count() == 1

    def test_admin_bypasses(self, client, db_session, default_store):
        admin = _login_admin(client, db_session, default_store)
        acc = _mk_account_owned(db_session, default_store, admin, name="a3", path="/data/m/a3")
        repo = _mk_repo(db_session, "r-admin")

        resp = client.post(
            f"/accounts/{acc.id}/backup/configure",
            data={"destination_id": repo.id, "schedule": "0 2 * * *"},
            follow_redirects=False,
        )

        assert resp.status_code == 303
        assert db_session.query(BackupPolicy).count() == 1

    def test_grandfathered_current_repo_resubmit_passes(self, client, db_session, default_store):
        owner = create_user(db_session, "own3", "pass", UserRole.user, store_id=default_store.id)
        client.post("/api/auth/login", json={"username": "own3", "password": "pass"})  # pragma: allowlist secret
        acc = _mk_account_owned(db_session, default_store, owner, name="a4", path="/data/m/a4")
        legacy = _mk_repo(db_session, "r-legacy")
        db_session.add(
            BackupPolicy(account_id=acc.id, destination_id=legacy.id, schedule="0 2 * * *")
        )
        db_session.commit()

        # same destination, new schedule: allowed even though repo is not granted
        resp = client.post(
            f"/accounts/{acc.id}/backup/configure",
            data={"destination_id": legacy.id, "schedule": "0 3 * * *"},
            follow_redirects=False,
        )

        assert resp.status_code == 303
        db_session.expire_all()
        assert db_session.query(BackupPolicy).one().schedule == "0 3 * * *"

    def test_grandfathered_switch_to_other_non_allowed_rejected(
        self, client, db_session, default_store
    ):
        owner = create_user(db_session, "own4", "pass", UserRole.user, store_id=default_store.id)
        client.post("/api/auth/login", json={"username": "own4", "password": "pass"})  # pragma: allowlist secret
        acc = _mk_account_owned(db_session, default_store, owner, name="a5", path="/data/m/a5")
        legacy = _mk_repo(db_session, "r-legacy2")
        other = _mk_repo(db_session, "r-other")
        db_session.add(
            BackupPolicy(account_id=acc.id, destination_id=legacy.id, schedule="0 2 * * *")
        )
        db_session.commit()

        resp = client.post(
            f"/accounts/{acc.id}/backup/configure",
            data={"destination_id": other.id, "schedule": "0 2 * * *"},
            follow_redirects=False,
        )

        assert resp.status_code == 303
        db_session.expire_all()
        assert db_session.query(BackupPolicy).one().destination_id == legacy.id
```

- [ ] **Step 2:** Run → the rejection/grandfathering tests FAIL (everything is currently accepted).

- [ ] **Step 3: Implement.** In `account_backup_configure`, move the existing-policy query UP (before the permission check) and insert the check after the `dest` lookup:

```python
    backup = db.query(BackupPolicy).filter(BackupPolicy.account_id == account_id).first()

    if user.role.value != "admin":
        allowed_ids = {r.id for r in user.allowed_repositories}
        current_id = backup.destination_id if backup else None
        if destination_id not in allowed_ids and destination_id != current_id:
            request.session["flash_error"] = (
                "You are not allowed to use this repository — ask an administrator"
            )
            return RedirectResponse(f"/accounts/{account_id}", status_code=303)
```

The rest of the function (update-or-create) stays as is, minus the now-duplicate `backup = ...` query lower down (remove it).

- [ ] **Step 4:** Tests + full suite + ruff. **Step 5: Commit:**

```bash
git add src/mailfallback/routers/ui_backup.py tests/test_repo_access.py
git commit -m "feat(backup): enforce repository grants on policy configure"
```

---

### Task 4: Filtered select + grandfathered marking

**Files:**
- Modify: `src/mailfallback/routers/ui_accounts.py` (account_detail context, `backup_destinations` ~line 448)
- Modify: `src/mailfallback/templates/partials/account_backup.html` (both `<select name="destination_id">` sites, ~lines 20 and 89)
- Test: `tests/test_repo_access.py` (extend)

- [ ] **Step 1: Failing tests** (append):

```python
class TestAccountPageFilter:
    def test_non_admin_sees_only_allowed(self, client, db_session, default_store):
        owner = create_user(db_session, "own5", "pass", UserRole.user, store_id=default_store.id)
        client.post("/api/auth/login", json={"username": "own5", "password": "pass"})  # pragma: allowlist secret
        acc = _mk_account_owned(db_session, default_store, owner, name="a6", path="/data/m/a6")
        allowed = _mk_repo(db_session, "r-visible")
        hidden = _mk_repo(db_session, "r-hidden")
        set_allowed_repositories(db_session, owner.id, [allowed.id])

        resp = client.get(f"/accounts/{acc.id}")

        assert resp.status_code == 200
        assert "r-visible" in resp.text
        assert "r-hidden" not in resp.text

    def test_admin_sees_all(self, client, db_session, default_store):
        admin = _login_admin(client, db_session, default_store)
        acc = _mk_account_owned(db_session, default_store, admin, name="a7", path="/data/m/a7")
        _mk_repo(db_session, "r-one")
        _mk_repo(db_session, "r-two")

        resp = client.get(f"/accounts/{acc.id}")

        assert "r-one" in resp.text and "r-two" in resp.text

    def test_grandfathered_current_marked(self, client, db_session, default_store):
        owner = create_user(db_session, "own6", "pass", UserRole.user, store_id=default_store.id)
        client.post("/api/auth/login", json={"username": "own6", "password": "pass"})  # pragma: allowlist secret
        acc = _mk_account_owned(db_session, default_store, owner, name="a8", path="/data/m/a8")
        legacy = _mk_repo(db_session, "r-legacy3")
        db_session.add(
            BackupPolicy(account_id=acc.id, destination_id=legacy.id, schedule="0 2 * * *")
        )
        db_session.commit()

        resp = client.get(f"/accounts/{acc.id}")

        assert "r-legacy3" in resp.text
        assert "not in your allowed set" in resp.text
```

- [ ] **Step 2:** Run → FAIL.

- [ ] **Step 3: Context.** In `account_detail` (ui_accounts.py), replace `backup_destinations = db.query(Repository).all()`:

```python
    if user.role.value == "admin":
        backup_destinations = db.query(Repository).all()
    else:
        backup_destinations = list(user.allowed_repositories)
        if (
            backup_config
            and backup_config.destination
            and backup_config.destination not in backup_destinations
        ):
            backup_destinations.append(backup_config.destination)
    allowed_repo_ids = {r.id for r in user.allowed_repositories} if user.role.value != "admin" else {
        r.id for r in backup_destinations
    }
```

(Reformat to taste/ruff. `backup_config` is queried above in the same function — verify ordering; if `backup_config` is computed after this point, move this block below it.) Pass `allowed_repo_ids` in the context.

- [ ] **Step 4: Template.** In BOTH selects of `account_backup.html`, mark non-allowed options:

```html
<option value="{{ dest.id }}" {% if backup_config and dest.id == backup_config.destination_id %}selected{% endif %}>{{ dest.name }} ({{ dest.backend_type.value }}){% if dest.id not in allowed_repo_ids %} (current — not in your allowed set){% endif %}</option>
```

(Adapt each site to its existing option markup — the first select at ~line 20 is the not-yet-configured form and may not reference backup_config; keep each site's existing selected-logic and only append the marker span. The marker can only ever appear for the grandfathered current repo because non-allowed repos aren't in the list otherwise.)

- [ ] **Step 5:** Tests + full suite + ruff. **Step 6: Commit:**

```bash
git add src/mailfallback/routers/ui_accounts.py src/mailfallback/templates/partials/account_backup.html tests/test_repo_access.py
git commit -m "feat(ui): filter policy repositories to the user's allowed set"
```

---

### Task 5: Config export includes grants

**Files:**
- Modify: `src/mailfallback/services/config_backup_service.py` (`_EXPORT_TABLES`)
- Test: `tests/test_config_backup_service.py` (extend)

- [ ] **Step 1: Failing test** (append to tests/test_config_backup_service.py; the `populated` fixture exists — extend the assertion inside a new test):

```python
class TestExportAllowedRepositories:
    def test_grants_round_trip(self, db_session, populated):
        from mailfallback.models import User, user_allowed_repositories

        user = populated["user"]
        repo = populated["repo"]
        user.allowed_repositories.append(repo)
        db_session.commit()

        data = cbs.build_export(db_session)
        assert data["tables"]["user_allowed_repositories"] == [
            {"user_id": user.id, "repository_id": repo.id}
        ]

        # wipe and import
        db_session.execute(user_allowed_repositories.delete())
        db_session.commit()
        report = cbs.import_export(db_session, data)
        assert report["errors"] == []
        db_session.expire_all()
        refreshed = db_session.query(User).filter(User.id == user.id).one()
        assert [r.id for r in refreshed.allowed_repositories] == [repo.id]
```

(Import style: match the file. NOTE the import wipes only the grants table — users/repos still exist so they're skipped and the grant row's FKs resolve via identity remap.)

- [ ] **Step 2:** Run → FAIL (table not in export).

- [ ] **Step 3:** In `_EXPORT_TABLES`, insert `"user_allowed_repositories"` immediately after `"backup_destinations"` (FK order: it references users AND backup_destinations, both earlier in the list — verify users comes before backup_destinations in the list; it does).

- [ ] **Step 4:** Tests + full suite + ruff. **Step 5: Commit:**

```bash
git add src/mailfallback/services/config_backup_service.py tests/test_config_backup_service.py
git commit -m "feat(backup): include repository grants in config snapshots"
```

---

### Task 6: Live verification + PR

- [ ] **Step 1:** `docker compose up -d --build mailfallback`; `docker compose exec mailfallback uv run alembic upgrade head` (017 — verify the backfill granted the existing user the existing repositories: the admin Users page checkboxes should come up checked).
- [ ] **Step 2:** Browser verification:
  - Admin Users page: "Allowed repositories" checkbox group renders; toggle works; save persists.
  - Backfill: existing user has all existing repos checked after upgrade.
  - As admin: account policy select shows all repos.
  - (Optional if a non-admin test user is practical: create one, grant nothing, verify the select is empty and configure is rejected.)
- [ ] **Step 3:** Full suite + ruff + format. Push and open the PR:

```bash
git push -u origin feat/repo-access
gh pr create --title "feat: per-user repository access (allowed repositories)" --body "..."
```

(PR body: the four building blocks — table+backfill, admin grants UI, configure enforcement with grandfathering, filtered select — plus DR export coverage; link the spec; standard footer.)

---

## Self-review notes (already applied)

- Spec coverage: data model → Task 1; enforcement 1 → Task 3; enforcement 2 + UI marking → Task 4; admin UI + service + audit → Task 2; DR export → Task 5; tests per spec section embedded.
- Type consistency: `set_allowed_repositories(db, user_id, repository_ids) -> str | None`; form field `repository_ids`; route `/admin/users/{id}/allowed-repositories`; context key `allowed_repo_ids`; audit `user.set_allowed_repositories`.
- The Task 1 model uses CASCADE on both FKs (unlike legacy `user_allowed_stores`) per the migration-015 cascade lesson; the plan explicitly says not to retrofit the old table.
- Task 2's service placement decision (user_service, not store_service) is recorded inline to stop the implementer from "mirroring" the historical misplacement.
