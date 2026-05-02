# Groups, Ownership & SSO Sync Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add MFB groups for account visibility sharing, allow owners/admins to manage account ownership, and auto-sync group membership from SSO claims on login.

**Architecture:** Three new tables (`groups`, `group_members`, `account_groups`) with SQLAlchemy relationships. Account visibility is UNION of ownership + group membership. Group membership can be managed manually or auto-synced from OIDC `groups` claim per a `sso_sync` flag on each group. New `group_service.py` handles all group logic. Existing `account_service.py` and `dovecot.py` updated for group-aware queries.

**Tech Stack:** SQLAlchemy (models + relationships), FastAPI routes, Jinja2 templates, existing test fixtures.

---

## File Structure

| File | Action | Responsibility |
|------|--------|---------------|
| `src/mailfallback/models.py` | Modify | Add `Group`, `group_members`, `account_groups` |
| `src/mailfallback/services/group_service.py` | Create | Group CRUD, membership, SSO sync |
| `src/mailfallback/services/account_service.py` | Modify | Group-aware visibility, `is_account_owner()` |
| `src/mailfallback/routers/dovecot.py` | Modify | Group-aware namespace query |
| `src/mailfallback/routers/auth.py` | Modify | Call `sync_sso_groups` on OIDC callback |
| `src/mailfallback/routers/ui_admin.py` | Modify | Groups admin page + routes |
| `src/mailfallback/routers/ui_accounts.py` | Modify | Ownership management in account detail |
| `src/mailfallback/templates/admin_groups.html` | Create | Groups management page |
| `src/mailfallback/templates/account_detail.html` | Modify | Ownership section |
| `src/mailfallback/templates/profile.html` | Modify | Show group memberships |
| `tests/test_group_service.py` | Create | Group service tests |
| `tests/test_groups_visibility.py` | Create | Visibility + Dovecot integration tests |

---

### Task 1: Data Model — Group, group_members, account_groups

**Files:**
- Modify: `src/mailfallback/models.py`
- Test: `tests/test_models.py`

- [ ] **Step 1: Write the failing test**

Add to end of `tests/test_models.py`:

```python
def test_group_relationships():
    session = make_session()
    store = _make_store(session)

    user1 = User(username="alice", role=UserRole.user, store_id=store.id)
    user2 = User(username="bob", role=UserRole.user, store_id=store.id)
    session.add_all([user1, user2])
    session.commit()

    account = Account(
        name="Shared",
        imap_host="imap.example.com",
        maildir_path="/data/mailboxes/shared-uuid",
        store_id=store.id,
    )
    session.add(account)
    session.commit()

    from mailfallback.models import Group

    group = Group(name="team", owner_id=user1.id)
    session.add(group)
    session.commit()

    group.members.append(user1)
    group.members.append(user2)
    group.accounts.append(account)
    session.commit()
    session.refresh(group)

    assert len(group.members) == 2
    assert len(group.accounts) == 1
    assert group in user1.groups
    assert account in group.accounts
    assert group.owner.username == "alice"
    assert group.sso_sync is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_models.py::test_group_relationships -v`
Expected: FAIL — `cannot import name 'Group'`

- [ ] **Step 3: Add tables and model to models.py**

In `src/mailfallback/models.py`, after `user_allowed_stores` Table, add:

```python
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
```

After the `MailStore` class, add the `Group` class:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_models.py::test_group_relationships -v`
Expected: PASS

- [ ] **Step 5: Run full test suite**

Run: `uv run pytest tests/ -q`
Expected: all pass

---

### Task 2: Group Service — CRUD and membership

**Files:**
- Create: `src/mailfallback/services/group_service.py`
- Create: `tests/test_group_service.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_group_service.py`:

```python
# tests/test_group_service.py
import pytest

from mailfallback.models import Account, MailStore, UserRole
from mailfallback.services.group_service import (
    add_member,
    can_manage_group,
    create_group,
    delete_group,
    get_user_groups,
    remove_member,
    set_group_accounts,
    update_group,
)
from mailfallback.services.user_service import create_user


@pytest.fixture
def store(db_session):
    s = MailStore(name="test", path="/tmp/grp-test")
    db_session.add(s)
    db_session.commit()
    db_session.refresh(s)
    return s


def test_create_group(db_session, store):
    owner = create_user(db_session, "owner", "pass", UserRole.user, store_id=store.id)
    group = create_group(db_session, "devs", owner.id)
    assert group.name == "devs"
    assert group.owner_id == owner.id
    assert group.sso_sync is False


def test_create_group_with_sso_sync(db_session, store):
    owner = create_user(db_session, "owner", "pass", UserRole.user, store_id=store.id)
    group = create_group(db_session, "sso-team", owner.id, sso_sync=True)
    assert group.sso_sync is True


def test_update_group(db_session, store):
    owner = create_user(db_session, "owner", "pass", UserRole.user, store_id=store.id)
    group = create_group(db_session, "old-name", owner.id)
    updated = update_group(db_session, group.id, name="new-name")
    assert updated.name == "new-name"


def test_delete_group(db_session, store):
    owner = create_user(db_session, "owner", "pass", UserRole.user, store_id=store.id)
    group = create_group(db_session, "doomed", owner.id)
    gid = group.id
    delete_group(db_session, gid)
    from mailfallback.models import Group
    assert db_session.query(Group).filter(Group.id == gid).first() is None


def test_add_and_remove_member(db_session, store):
    owner = create_user(db_session, "owner", "pass", UserRole.user, store_id=store.id)
    member = create_user(db_session, "member", "pass", UserRole.user, store_id=store.id)
    group = create_group(db_session, "team", owner.id)

    add_member(db_session, group.id, member.id)
    db_session.refresh(group)
    assert member in group.members

    remove_member(db_session, group.id, member.id)
    db_session.refresh(group)
    assert member not in group.members


def test_set_group_accounts(db_session, store):
    owner = create_user(db_session, "owner", "pass", UserRole.user, store_id=store.id)
    group = create_group(db_session, "shared", owner.id)
    a1 = Account(name="A1", imap_host="imap.ex.com", maildir_path="/tmp/a1", store_id=store.id)
    a2 = Account(name="A2", imap_host="imap.ex.com", maildir_path="/tmp/a2", store_id=store.id)
    db_session.add_all([a1, a2])
    db_session.commit()

    set_group_accounts(db_session, group.id, [a1.id, a2.id])
    db_session.refresh(group)
    assert len(group.accounts) == 2

    set_group_accounts(db_session, group.id, [a1.id])
    db_session.refresh(group)
    assert len(group.accounts) == 1


def test_get_user_groups(db_session, store):
    owner = create_user(db_session, "owner", "pass", UserRole.user, store_id=store.id)
    user = create_user(db_session, "user1", "pass", UserRole.user, store_id=store.id)
    g1 = create_group(db_session, "g1", owner.id)
    g2 = create_group(db_session, "g2", owner.id)
    add_member(db_session, g1.id, user.id)
    result = get_user_groups(db_session, user)
    assert len(result) == 1
    assert result[0].id == g1.id


def test_can_manage_group(db_session, store):
    admin = create_user(db_session, "admin", "pass", UserRole.admin, store_id=store.id)
    owner = create_user(db_session, "owner", "pass", UserRole.user, store_id=store.id)
    other = create_user(db_session, "other", "pass", UserRole.user, store_id=store.id)
    group = create_group(db_session, "managed", owner.id)
    assert can_manage_group(admin, group) is True
    assert can_manage_group(owner, group) is True
    assert can_manage_group(other, group) is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_group_service.py -v`
Expected: FAIL — `No module named 'mailfallback.services.group_service'`

- [ ] **Step 3: Implement group_service.py**

Create `src/mailfallback/services/group_service.py`:

```python
# src/mailfallback/services/group_service.py
from sqlalchemy.orm import Session

from mailfallback.models import Account, Group, User, UserRole


def create_group(
    db: Session, name: str, owner_id: str, sso_sync: bool = False
) -> Group:
    group = Group(name=name, owner_id=owner_id, sso_sync=sso_sync)
    db.add(group)
    db.commit()
    db.refresh(group)
    return group


_UPDATABLE_GROUP_FIELDS = {"name", "sso_sync"}


def update_group(db: Session, group_id: str, **kwargs) -> Group | None:
    group = db.query(Group).filter(Group.id == group_id).first()
    if not group:
        return None
    for key, value in kwargs.items():
        if key in _UPDATABLE_GROUP_FIELDS:
            setattr(group, key, value)
    db.commit()
    db.refresh(group)
    return group


def delete_group(db: Session, group_id: str) -> bool:
    group = db.query(Group).filter(Group.id == group_id).first()
    if not group:
        return False
    db.delete(group)
    db.commit()
    return True


def add_member(db: Session, group_id: str, user_id: str) -> None:
    group = db.query(Group).filter(Group.id == group_id).first()
    user = db.query(User).filter(User.id == user_id).first()
    if group and user and user not in group.members:
        group.members.append(user)
        db.commit()


def remove_member(db: Session, group_id: str, user_id: str) -> None:
    group = db.query(Group).filter(Group.id == group_id).first()
    user = db.query(User).filter(User.id == user_id).first()
    if group and user and user in group.members:
        group.members.remove(user)
        db.commit()


def set_group_accounts(db: Session, group_id: str, account_ids: list[str]) -> None:
    group = db.query(Group).filter(Group.id == group_id).first()
    if not group:
        return
    accounts = db.query(Account).filter(Account.id.in_(account_ids)).all()
    group.accounts = accounts
    db.commit()


def get_user_groups(db: Session, user: User) -> list[Group]:
    return user.groups


def can_manage_group(user: User, group: Group) -> bool:
    if user.role == UserRole.admin:
        return True
    return user.id == group.owner_id


def sync_sso_groups(db: Session, user: User, sso_group_names: list[str]) -> None:
    sso_groups = db.query(Group).filter(Group.sso_sync.is_(True)).all()
    for group in sso_groups:
        is_member = user in group.members
        should_be_member = group.name in sso_group_names
        if should_be_member and not is_member:
            group.members.append(user)
        elif not should_be_member and is_member:
            group.members.remove(user)
    db.commit()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_group_service.py -v`
Expected: all PASS

- [ ] **Step 5: Run full test suite**

Run: `uv run pytest tests/ -q`
Expected: all pass

---

### Task 3: SSO Sync — test and wire up

**Files:**
- Test: `tests/test_group_service.py` (add SSO tests)
- Modify: `src/mailfallback/routers/auth.py:179-205`

- [ ] **Step 1: Write SSO sync tests**

Add to end of `tests/test_group_service.py`:

```python
from mailfallback.services.group_service import sync_sso_groups


def test_sync_sso_groups_adds_member(db_session, store):
    owner = create_user(db_session, "owner", "pass", UserRole.user, store_id=store.id)
    user = create_user(db_session, "ssouser", "pass", UserRole.user, store_id=store.id)
    group = create_group(db_session, "sso-team", owner.id, sso_sync=True)

    sync_sso_groups(db_session, user, ["sso-team"])
    db_session.refresh(group)
    assert user in group.members


def test_sync_sso_groups_removes_member(db_session, store):
    owner = create_user(db_session, "owner", "pass", UserRole.user, store_id=store.id)
    user = create_user(db_session, "ssouser", "pass", UserRole.user, store_id=store.id)
    group = create_group(db_session, "sso-team", owner.id, sso_sync=True)
    add_member(db_session, group.id, user.id)

    sync_sso_groups(db_session, user, [])
    db_session.refresh(group)
    assert user not in group.members


def test_sync_sso_groups_ignores_non_sso(db_session, store):
    owner = create_user(db_session, "owner", "pass", UserRole.user, store_id=store.id)
    user = create_user(db_session, "ssouser", "pass", UserRole.user, store_id=store.id)
    manual_group = create_group(db_session, "manual", owner.id, sso_sync=False)
    add_member(db_session, manual_group.id, user.id)

    sync_sso_groups(db_session, user, [])
    db_session.refresh(manual_group)
    assert user in manual_group.members


def test_sync_sso_groups_no_op_for_unmatched_names(db_session, store):
    owner = create_user(db_session, "owner", "pass", UserRole.user, store_id=store.id)
    user = create_user(db_session, "ssouser", "pass", UserRole.user, store_id=store.id)
    group = create_group(db_session, "sso-team", owner.id, sso_sync=True)

    sync_sso_groups(db_session, user, ["other-group"])
    db_session.refresh(group)
    assert user not in group.members
```

- [ ] **Step 2: Run SSO tests**

Run: `uv run pytest tests/test_group_service.py -v -k "sso"`
Expected: all PASS (implementation already in Task 2)

- [ ] **Step 3: Wire sync_sso_groups into OIDC callback**

In `src/mailfallback/routers/auth.py`, add import at the top:

```python
from mailfallback.services.group_service import sync_sso_groups
```

In the `oidc_callback` function (after `db.commit()` on line 202), before `request.session["user_id"]`, add:

```python
    sync_sso_groups(db, user, groups)
```

The full end of the function becomes:

```python
    else:
        user.role = role
        db.commit()

    sync_sso_groups(db, user, groups)

    request.session["user_id"] = user.id
    return RedirectResponse("/")
```

- [ ] **Step 4: Run full test suite**

Run: `uv run pytest tests/ -q`
Expected: all pass

---

### Task 4: Group-aware account visibility

**Files:**
- Modify: `src/mailfallback/services/account_service.py:67-79`
- Modify: `src/mailfallback/routers/dovecot.py:38-44`
- Create: `tests/test_groups_visibility.py`

- [ ] **Step 1: Write visibility tests**

Create `tests/test_groups_visibility.py`:

```python
# tests/test_groups_visibility.py
from mailfallback.models import Account, MailStore, UserRole
from mailfallback.services.account_service import (
    get_account,
    get_accounts_for_user,
    is_account_owner,
)
from mailfallback.services.group_service import (
    add_member,
    create_group,
    set_group_accounts,
)
from mailfallback.services.user_service import create_user


def _setup(db):
    store = MailStore(name="vis", path="/tmp/vis")
    db.add(store)
    db.commit()
    db.refresh(store)

    owner = create_user(db, "owner", "pass", UserRole.user, store_id=store.id)
    viewer = create_user(db, "viewer", "pass", UserRole.user, store_id=store.id)
    outsider = create_user(db, "outsider", "pass", UserRole.user, store_id=store.id)

    account = Account(
        name="Shared Mail",
        imap_host="imap.ex.com",
        maildir_path="/tmp/vis/shared-uuid",
        store_id=store.id,
    )
    db.add(account)
    db.commit()
    account.owners.append(owner)
    db.commit()

    group = create_group(db, "team", owner.id)
    add_member(db, group.id, viewer.id)
    set_group_accounts(db, group.id, [account.id])

    return store, owner, viewer, outsider, account, group


def test_owner_sees_account(db_session):
    _, owner, _, _, account, _ = _setup(db_session)
    accounts = get_accounts_for_user(db_session, owner)
    assert account in accounts


def test_group_member_sees_account(db_session):
    _, _, viewer, _, account, _ = _setup(db_session)
    accounts = get_accounts_for_user(db_session, viewer)
    assert account in accounts


def test_outsider_cannot_see_account(db_session):
    _, _, _, outsider, account, _ = _setup(db_session)
    accounts = get_accounts_for_user(db_session, outsider)
    assert account not in accounts


def test_get_account_via_group(db_session):
    _, _, viewer, _, account, _ = _setup(db_session)
    result = get_account(db_session, account.id, viewer)
    assert result is not None
    assert result.id == account.id


def test_get_account_denied_for_outsider(db_session):
    _, _, _, outsider, account, _ = _setup(db_session)
    result = get_account(db_session, account.id, outsider)
    assert result is None


def test_is_account_owner(db_session):
    _, owner, viewer, _, account, _ = _setup(db_session)
    assert is_account_owner(owner, account) is True
    assert is_account_owner(viewer, account) is False


def test_no_duplicates_when_owner_and_group_member(db_session):
    _, owner, _, _, account, group = _setup(db_session)
    add_member(db_session, group.id, owner.id)
    accounts = get_accounts_for_user(db_session, owner)
    ids = [a.id for a in accounts]
    assert ids.count(account.id) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_groups_visibility.py -v`
Expected: FAIL — `cannot import name 'is_account_owner'`

- [ ] **Step 3: Update account_service.py**

In `src/mailfallback/services/account_service.py`, add import:

```python
from mailfallback.models import Account, MailStore, User, UserRole, account_groups, group_members
```

Replace `get_accounts_for_user`:

```python
def get_accounts_for_user(db: Session, user: User) -> list[Account]:
    if user.role == UserRole.admin:
        return db.query(Account).all()
    owned = set(a.id for a in user.accounts)
    via_groups = (
        db.query(Account.id)
        .join(account_groups, Account.id == account_groups.c.account_id)
        .join(group_members, account_groups.c.group_id == group_members.c.group_id)
        .filter(group_members.c.user_id == user.id)
        .all()
    )
    group_ids = {row[0] for row in via_groups}
    all_ids = owned | group_ids
    if not all_ids:
        return []
    return db.query(Account).filter(Account.id.in_(all_ids)).all()
```

Replace `get_account`:

```python
def get_account(db: Session, account_id: str, user: User) -> Account | None:
    account = db.query(Account).filter(Account.id == account_id).first()
    if not account:
        return None
    if user.role == UserRole.admin:
        return account
    if user in account.owners:
        return account
    via_group = (
        db.query(account_groups.c.account_id)
        .join(group_members, account_groups.c.group_id == group_members.c.group_id)
        .filter(
            account_groups.c.account_id == account_id,
            group_members.c.user_id == user.id,
        )
        .first()
    )
    if via_group:
        return account
    return None
```

Add new function:

```python
def is_account_owner(user: User, account: Account) -> bool:
    return user in account.owners
```

- [ ] **Step 4: Update dovecot.py**

In `src/mailfallback/routers/dovecot.py`, replace the accounts query (lines 38-44):

```python
from mailfallback.models import Account, User, account_groups, account_owners, group_members
```

```python
    owned = (
        db.query(Account)
        .join(account_owners, Account.id == account_owners.c.account_id)
        .filter(account_owners.c.user_id == user.id)
    )
    via_groups = (
        db.query(Account)
        .join(account_groups, Account.id == account_groups.c.account_id)
        .join(group_members, account_groups.c.group_id == group_members.c.group_id)
        .filter(group_members.c.user_id == user.id)
    )
    all_accounts = owned.union(via_groups).order_by(Account.created_at.asc()).all()
    accounts = [a for a in all_accounts if a.enabled and a.store.enabled]
```

- [ ] **Step 5: Run tests**

Run: `uv run pytest tests/test_groups_visibility.py tests/test_dovecot_api.py -v`
Expected: all PASS

- [ ] **Step 6: Run full test suite**

Run: `uv run pytest tests/ -q`
Expected: all pass

---

### Task 5: Delete permission guard

**Files:**
- Modify: `src/mailfallback/routers/accounts.py`
- Test: `tests/test_groups_visibility.py` (add test)

- [ ] **Step 1: Write failing test**

Add to `tests/test_groups_visibility.py`:

```python
def test_delete_blocked_for_group_member(client, db_session):
    store, owner, viewer, _, account, _ = _setup(db_session)
    from mailfallback.services.user_service import create_user as cu

    client.post("/api/auth/login", json={"username": "viewer", "password": "pass"})  # pragma: allowlist secret
    resp = client.delete(f"/api/accounts/{account.id}")
    assert resp.status_code == 403
```

- [ ] **Step 2: Add guard to delete endpoint**

In `src/mailfallback/routers/accounts.py`, find the delete endpoint. Add an ownership check before deletion:

```python
from mailfallback.services.account_service import is_account_owner
```

In the delete handler, after fetching the account and before calling `delete_account`, add:

```python
    if user.role != UserRole.admin and not is_account_owner(user, account):
        raise HTTPException(status_code=403, detail="Only owners can delete accounts")
```

- [ ] **Step 3: Run test**

Run: `uv run pytest tests/test_groups_visibility.py::test_delete_blocked_for_group_member -v`
Expected: PASS

- [ ] **Step 4: Run full test suite**

Run: `uv run pytest tests/ -q`
Expected: all pass

---

### Task 6: Admin Groups page — routes and template

**Files:**
- Modify: `src/mailfallback/routers/ui_admin.py`
- Create: `src/mailfallback/templates/admin_groups.html`
- Modify: `src/mailfallback/templates/base.html` (add nav link)

- [ ] **Step 1: Add routes to ui_admin.py**

Add imports:

```python
from mailfallback.models import Group
from mailfallback.services.group_service import (
    add_member,
    can_manage_group,
    create_group,
    delete_group,
    remove_member,
    set_group_accounts,
    update_group,
)
from mailfallback.services.account_service import get_accounts_for_user
```

Add routes:

```python
# --- Group management ---


@router.get("/admin/groups", response_class=HTMLResponse)
def admin_groups_page(request: Request, db: Session = Depends(get_db)):
    user = _get_session_user(request, db)
    if not user:
        return RedirectResponse("/login")
    if user.role.value == "admin":
        groups = db.query(Group).all()
    else:
        groups = db.query(Group).filter(Group.owner_id == user.id).all()
    if not groups and user.role.value != "admin":
        return RedirectResponse("/")
    all_users = list_users(db) if user.role.value == "admin" else []
    all_accounts = db.query(Account).all() if user.role.value == "admin" else get_accounts_for_user(db, user)
    return templates.TemplateResponse(
        request=request,
        name="admin_groups.html",
        context={
            "user": user,
            "groups": groups,
            "all_users": all_users,
            "all_accounts": all_accounts,
        },
    )


@router.post("/admin/groups/new")
async def admin_create_group(request: Request, db: Session = Depends(get_db)):
    user = _get_session_user(request, db)
    if not user or user.role.value != "admin":
        return RedirectResponse("/", status_code=303)
    form = await request.form()
    owner_id = form.get("owner_id") or user.id
    sso_sync = bool(form.get("sso_sync"))
    create_group(db, form["name"], owner_id, sso_sync=sso_sync)
    return RedirectResponse("/admin/groups", status_code=303)


@router.post("/admin/groups/{group_id}/edit")
async def admin_edit_group(group_id: str, request: Request, db: Session = Depends(get_db)):
    user = _get_session_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)
    group = db.query(Group).filter(Group.id == group_id).first()
    if not group or not can_manage_group(user, group):
        return RedirectResponse("/admin/groups", status_code=303)
    form = await request.form()
    member_ids = form.getlist("member_ids")
    account_ids = form.getlist("account_ids")
    sso_sync = bool(form.get("sso_sync"))
    update_group(db, group_id, sso_sync=sso_sync)
    # Replace members
    group.members = db.query(User).filter(User.id.in_(member_ids)).all() if member_ids else []
    # Replace accounts
    set_group_accounts(db, group_id, account_ids)
    db.commit()
    return RedirectResponse("/admin/groups", status_code=303)


@router.post("/admin/groups/{group_id}/delete")
async def admin_delete_group_route(group_id: str, request: Request, db: Session = Depends(get_db)):
    user = _get_session_user(request, db)
    if not user or user.role.value != "admin":
        return RedirectResponse("/", status_code=303)
    delete_group(db, group_id)
    return RedirectResponse("/admin/groups", status_code=303)
```

- [ ] **Step 2: Create admin_groups.html template**

Create `src/mailfallback/templates/admin_groups.html`:

```html
{% extends "base.html" %}
{% block title %}Groups — MFB{% endblock %}
{% block content %}
<h2><i data-lucide="users" class="icon-xl icon-inline"></i>Groups</h2>

<div class="table-wrap">
<table>
    <thead>
        <tr>
            <th>Name</th>
            <th>Owner</th>
            <th>Members</th>
            <th>Accounts</th>
            <th>SSO Sync</th>
            <th class="text-right">Actions</th>
        </tr>
    </thead>
    <tbody>
        {% for g in groups %}
        <tr>
            <td>{{ g.name }}</td>
            <td><span class="badge badge-user">{{ g.owner.username }}</span></td>
            <td>{{ g.members | length }}</td>
            <td>{{ g.accounts | length }}</td>
            <td>
                {% if g.sso_sync %}
                <span class="badge badge-admin"><i data-lucide="refresh-cw" class="icon-sm"></i> SSO</span>
                {% else %}
                <span class="text-muted">—</span>
                {% endif %}
            </td>
            <td>
                <div class="actions flex-end">
                    <button class="icon-btn" title="Edit" onclick="toggleRow('edit-{{ g.id }}')">
                        <i data-lucide="pencil" class="icon-md"></i>
                    </button>
                    {% if user.role.value == "admin" %}
                    <form method="post" action="/admin/groups/{{ g.id }}/delete" class="inline-form"
                        onsubmit="return confirm('Delete group {{ g.name }}?')">
                        <button type="submit" class="icon-btn danger" title="Delete">
                            <i data-lucide="trash-2" class="icon-md"></i>
                        </button>
                    </form>
                    {% endif %}
                </div>
            </td>
        </tr>
        <tr id="edit-{{ g.id }}" class="hidden">
            <td colspan="6">
                <form method="post" action="/admin/groups/{{ g.id }}/edit">
                    <div class="grid-2 mt-025">
                        <div>
                            <label><strong>Members:</strong></label>
                            <div class="flex gap-05 flex-wrap mt-025">
                                {% for u in all_users %}
                                <label class="checkbox-pill">
                                    <input type="checkbox" name="member_ids" value="{{ u.id }}"
                                        {% if u in g.members %}checked{% endif %}>
                                    {{ u.username }}
                                </label>
                                {% endfor %}
                            </div>
                        </div>
                        <div>
                            <label><strong>Visible accounts:</strong></label>
                            <div class="flex gap-05 flex-wrap mt-025">
                                {% for a in all_accounts %}
                                <label class="checkbox-pill">
                                    <input type="checkbox" name="account_ids" value="{{ a.id }}"
                                        {% if a in g.accounts %}checked{% endif %}>
                                    {{ a.name }}
                                </label>
                                {% endfor %}
                            </div>
                        </div>
                    </div>
                    <div class="mt-05">
                        <label class="checkbox-pill">
                            <input type="checkbox" name="sso_sync" value="1"
                                {% if g.sso_sync %}checked{% endif %}>
                            SSO auto-sync
                        </label>
                    </div>
                    <button type="submit" class="icon-btn primary mt-05">
                        <i data-lucide="save" class="icon-md"></i> Save
                    </button>
                </form>
            </td>
        </tr>
        {% endfor %}
        {% if not groups %}
        <tr><td colspan="6" class="text-muted">No groups yet.</td></tr>
        {% endif %}
    </tbody>
</table>
</div>

{% if user.role.value == "admin" %}
<hr>
<h3><i data-lucide="plus-circle" class="icon-lg icon-inline"></i>Create Group</h3>
<form method="post" action="/admin/groups/new">
    <div class="grid-3-auto">
        <div>
            <label for="group_name">Name</label>
            <input type="text" id="group_name" name="name" required>
        </div>
        <div>
            <label for="group_owner">Owner</label>
            <select id="group_owner" name="owner_id">
                {% for u in all_users %}
                <option value="{{ u.id }}">{{ u.username }}</option>
                {% endfor %}
            </select>
        </div>
        <div>
            <label>&nbsp;</label>
            <label class="checkbox-pill">
                <input type="checkbox" name="sso_sync" value="1"> SSO sync
            </label>
        </div>
        <button type="submit" class="icon-btn primary">
            <i data-lucide="plus-circle" class="icon-md"></i> Create
        </button>
    </div>
</form>
{% endif %}
{% endblock %}
```

- [ ] **Step 3: Add nav link in base.html**

Find the sidebar nav section in `src/mailfallback/templates/base.html`. Add a "Groups" link near the admin section:

```html
<a href="/admin/groups"><i data-lucide="users" class="icon-md"></i> Groups</a>
```

This should be visible to all users (group owners need access too), placed after the existing admin nav links.

- [ ] **Step 4: Run full test suite**

Run: `uv run pytest tests/ -q`
Expected: all pass

---

### Task 7: Account detail — ownership management

**Files:**
- Modify: `src/mailfallback/routers/ui_accounts.py`
- Modify: `src/mailfallback/templates/account_detail.html`

- [ ] **Step 1: Add ownership routes**

In `src/mailfallback/routers/ui_accounts.py`, add imports:

```python
from mailfallback.services.account_service import assign_owner, is_account_owner, remove_owner
from mailfallback.services.user_service import list_users
```

Add routes:

```python
@router.post("/accounts/{account_id}/add-owner")
async def account_add_owner(account_id: str, request: Request, db: Session = Depends(get_db)):
    user = _get_session_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)
    account = get_account(db, account_id, user)
    if not account:
        return RedirectResponse("/", status_code=303)
    if not is_account_owner(user, account) and user.role.value != "admin":
        return RedirectResponse(f"/accounts/{account_id}", status_code=303)
    form = await request.form()
    new_owner_id = form["user_id"]
    assign_owner(db, account_id, new_owner_id)
    return RedirectResponse(f"/accounts/{account_id}", status_code=303)


@router.post("/accounts/{account_id}/remove-owner")
async def account_remove_owner(account_id: str, request: Request, db: Session = Depends(get_db)):
    user = _get_session_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)
    account = get_account(db, account_id, user)
    if not account:
        return RedirectResponse("/", status_code=303)
    if not is_account_owner(user, account) and user.role.value != "admin":
        return RedirectResponse(f"/accounts/{account_id}", status_code=303)
    form = await request.form()
    remove_owner(db, account_id, form["user_id"])
    return RedirectResponse(f"/accounts/{account_id}", status_code=303)
```

Also update `account_detail` to pass `all_users` and `is_owner`:

In the `account_detail` function, add to context:

```python
    all_users_list = list_users(db) if user.role.value == "admin" or is_account_owner(user, account) else []

    return templates.TemplateResponse(
        ...
        context={
            ...
            "all_users": all_users_list,
            "is_owner": is_account_owner(user, account),
        },
    )
```

- [ ] **Step 2: Add ownership section to account_detail.html**

In `src/mailfallback/templates/account_detail.html`, before the Edit Account Settings `<details>`, add:

```html
<details>
    <summary><i data-lucide="users" class="icon-md icon-inline"></i><strong>Ownership & Visibility</strong></summary>
    <div class="mt-1">
        <strong>Owners:</strong>
        <div class="flex gap-05 flex-wrap mt-025">
            {% for o in account.owners %}
            <span class="badge badge-user">
                {{ o.username }}
                {% if (is_owner or user.role.value == "admin") and account.owners | length > 1 %}
                <form method="post" action="/accounts/{{ account.id }}/remove-owner" class="inline-form" style="display:inline"
                    onsubmit="return confirm('Remove {{ o.username }} as owner?')">
                    <input type="hidden" name="user_id" value="{{ o.id }}">
                    <button type="submit" class="icon-btn-inline" title="Remove owner">
                        <i data-lucide="x" class="icon-sm"></i>
                    </button>
                </form>
                {% endif %}
            </span>
            {% endfor %}
        </div>

        {% if is_owner or user.role.value == "admin" %}
        <form method="post" action="/accounts/{{ account.id }}/add-owner" class="mt-05">
            <div class="flex gap-05 items-end">
                <div>
                    <label class="text-small">Add owner:</label>
                    <select name="user_id" style="margin-bottom:0">
                        {% for u in all_users %}
                        {% if u not in account.owners %}
                        <option value="{{ u.id }}">{{ u.username }}</option>
                        {% endif %}
                        {% endfor %}
                    </select>
                </div>
                <button type="submit" class="icon-btn primary">
                    <i data-lucide="user-plus" class="icon-md"></i> Add
                </button>
            </div>
        </form>
        {% endif %}

        {% if account.visible_to_groups %}
        <strong class="mt-05" style="display:block">Visible to groups:</strong>
        <div class="flex gap-05 flex-wrap mt-025">
            {% for g in account.visible_to_groups %}
            <span class="badge badge-admin">{{ g.name }}</span>
            {% endfor %}
        </div>
        {% endif %}
    </div>
</details>
```

Add a tiny CSS rule for inline icon buttons in `src/mailfallback/static/css/style.css`:

```css
.icon-btn-inline {
    background: none;
    border: none;
    cursor: pointer;
    padding: 0;
    margin: 0 0 0 0.2rem;
    color: inherit;
    opacity: 0.6;
    display: inline;
}
.icon-btn-inline:hover { opacity: 1; }
```

- [ ] **Step 3: Run full test suite**

Run: `uv run pytest tests/ -q`
Expected: all pass

---

### Task 8: Profile — show group memberships

**Files:**
- Modify: `src/mailfallback/routers/ui_profile.py`
- Modify: `src/mailfallback/templates/profile.html`

- [ ] **Step 1: Pass groups to profile context**

In `src/mailfallback/routers/ui_profile.py`, add import:

```python
from mailfallback.services.group_service import get_user_groups
```

In `profile_page`, add to context:

```python
    user_groups = get_user_groups(db, user)
    return templates.TemplateResponse(
        ...
        context={
            ...
            "user_groups": user_groups,
        },
    )
```

- [ ] **Step 2: Add groups row to profile.html**

In `src/mailfallback/templates/profile.html`, after the Store row in the table, add:

```html
    <tr>
        <td><i data-lucide="users" class="icon-md icon-inline"></i>Groups</td>
        <td>
            {% if user_groups %}
            {% for g in user_groups %}<span class="badge badge-admin">{{ g.name }}</span> {% endfor %}
            {% else %}
            <span class="text-muted">None</span>
            {% endif %}
        </td>
    </tr>
```

- [ ] **Step 3: Run full test suite and rebuild**

Run: `uv run pytest tests/ -q`
Expected: all pass

Run: `docker compose up -d --build`
Verify end-to-end.
