# User-Selectable Storage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let users choose which storage backend their email accounts use, with admin-managed allowlists per user.

**Architecture:** New many-to-many table `user_allowed_stores` links users to permitted stores. Users see a store dropdown in profile (default store) and account creation (per-account override). Admin manages allowlists via checkboxes in user management. Single-store users see no dropdown (implicit).

**Tech Stack:** SQLAlchemy (Table + relationship), FastAPI routes, Jinja2 templates, existing test patterns.

---

### Task 1: Data Model — `user_allowed_stores` table

**Files:**
- Modify: `src/mailfallback/models.py:52-76`

- [ ] **Step 1: Write the failing test**

File: `tests/test_models.py` — add at end:

```python
def test_user_allowed_stores_relationship(db_session):
    from mailfallback.models import MailStore, User, UserRole

    store1 = MailStore(name="s1", path="/tmp/s1")
    store2 = MailStore(name="s2", path="/tmp/s2")
    db_session.add_all([store1, store2])
    db_session.commit()

    user = User(
        username="storeuser",
        password_hash="x",
        role=UserRole.user,
        store_id=store1.id,
    )
    db_session.add(user)
    db_session.commit()

    user.allowed_stores.append(store1)
    user.allowed_stores.append(store2)
    db_session.commit()

    db_session.refresh(user)
    assert len(user.allowed_stores) == 2
    assert store1 in user.allowed_stores
    assert store2 in user.allowed_stores
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_models.py::test_user_allowed_stores_relationship -v`
Expected: FAIL — `User` has no attribute `allowed_stores`

- [ ] **Step 3: Add the table and relationships to models.py**

In `src/mailfallback/models.py`, after the `account_owners` Table (line 57), add:

```python
user_allowed_stores = Table(
    "user_allowed_stores",
    Base.metadata,
    Column("user_id", String, ForeignKey("users.id"), primary_key=True),
    Column("store_id", String, ForeignKey("mail_stores.id"), primary_key=True),
)
```

In the `User` class, after the `store` relationship (line 75), add:

```python
    allowed_stores = relationship("MailStore", secondary=user_allowed_stores)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_models.py::test_user_allowed_stores_relationship -v`
Expected: PASS

- [ ] **Step 5: Run full test suite**

Run: `uv run pytest tests/ -q`
Expected: all pass

---

### Task 2: Backend — store allowlist service functions

**Files:**
- Modify: `src/mailfallback/services/store_service.py`
- Test: `tests/test_store_drain.py` (add at end)

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_store_drain.py`:

```python
from mailfallback.services.store_service import (
    get_allowed_stores,
    get_selectable_stores,
    set_allowed_stores,
)


def test_get_allowed_stores_returns_user_stores(db_session):
    s1 = create_store(db_session, "s1", "/tmp/allowed1")
    s2 = create_store(db_session, "s2", "/tmp/allowed2")
    user = create_user(db_session, "alice", "pass", UserRole.user, store_id=s1.id)
    set_allowed_stores(db_session, user.id, [s1.id, s2.id])
    result = get_allowed_stores(db_session, user)
    assert len(result) == 2


def test_get_allowed_stores_excludes_disabled(db_session):
    s1 = create_store(db_session, "s1", "/tmp/allowdis1")
    s2 = create_store(db_session, "s2", "/tmp/allowdis2")
    s2.enabled = False
    db_session.commit()
    user = create_user(db_session, "bob", "pass", UserRole.user, store_id=s1.id)
    set_allowed_stores(db_session, user.id, [s1.id, s2.id])
    result = get_allowed_stores(db_session, user)
    assert len(result) == 1
    assert result[0].id == s1.id


def test_set_allowed_stores_replaces(db_session):
    s1 = create_store(db_session, "s1", "/tmp/allowrepl1")
    s2 = create_store(db_session, "s2", "/tmp/allowrepl2")
    user = create_user(db_session, "carol", "pass", UserRole.user, store_id=s1.id)
    set_allowed_stores(db_session, user.id, [s1.id, s2.id])
    set_allowed_stores(db_session, user.id, [s2.id])
    db_session.refresh(user)
    assert len(user.allowed_stores) == 1
    assert user.store_id == s2.id


def test_set_allowed_stores_updates_default_if_removed(db_session):
    s1 = create_store(db_session, "s1", "/tmp/allowdef1")
    s2 = create_store(db_session, "s2", "/tmp/allowdef2")
    user = create_user(db_session, "dave", "pass", UserRole.user, store_id=s1.id)
    set_allowed_stores(db_session, user.id, [s1.id, s2.id])
    set_allowed_stores(db_session, user.id, [s2.id])
    db_session.refresh(user)
    assert user.store_id == s2.id


def test_get_selectable_stores_none_if_single(db_session):
    s1 = create_store(db_session, "s1", "/tmp/sel1")
    user = create_user(db_session, "eve", "pass", UserRole.user, store_id=s1.id)
    set_allowed_stores(db_session, user.id, [s1.id])
    result = get_selectable_stores(db_session, user)
    assert result is None


def test_get_selectable_stores_list_if_multiple(db_session):
    s1 = create_store(db_session, "s1", "/tmp/selm1")
    s2 = create_store(db_session, "s2", "/tmp/selm2")
    user = create_user(db_session, "frank", "pass", UserRole.user, store_id=s1.id)
    set_allowed_stores(db_session, user.id, [s1.id, s2.id])
    result = get_selectable_stores(db_session, user)
    assert result is not None
    assert len(result) == 2
```

Also add the new imports at the top of the file (update the existing import block).

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_store_drain.py::test_get_allowed_stores_returns_user_stores -v`
Expected: FAIL — `cannot import name 'get_allowed_stores'`

- [ ] **Step 3: Implement the three functions in store_service.py**

Add to `src/mailfallback/services/store_service.py`, after `get_user_store`:

```python
def get_allowed_stores(db: Session, user: User) -> list[MailStore]:
    """Return enabled stores in the user's allowlist."""
    return [s for s in user.allowed_stores if s.enabled]


def set_allowed_stores(db: Session, user_id: str, store_ids: list[str]) -> None:
    """Replace a user's allowed stores. Updates default store if no longer in list."""
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        return
    stores = db.query(MailStore).filter(MailStore.id.in_(store_ids)).all()
    user.allowed_stores = stores
    if user.store_id not in {s.id for s in stores} and stores:
        user.store_id = stores[0].id
    db.commit()


def get_selectable_stores(db: Session, user: User) -> list[MailStore] | None:
    """Return allowed stores if user has >1, else None (implicit single store)."""
    stores = get_allowed_stores(db, user)
    return stores if len(stores) > 1 else None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_store_drain.py -v -k "allowed or selectable"`
Expected: all 6 new tests PASS

- [ ] **Step 5: Run full test suite**

Run: `uv run pytest tests/ -q`
Expected: all pass

---

### Task 3: Admin UI — allowlist management

**Files:**
- Modify: `src/mailfallback/routers/ui_admin.py`
- Modify: `src/mailfallback/templates/admin_users.html`

- [ ] **Step 1: Add the allowed-stores route**

In `src/mailfallback/routers/ui_admin.py`, add import for `set_allowed_stores` (add to the existing store_service import block):

```python
from mailfallback.services.store_service import (
    create_store,
    delete_orphaned_dirs,
    delete_store,
    ensure_default_store,
    get_orphaned_dirs,
    list_stores,
    set_allowed_stores,
    set_default_store,
    update_store,
)
```

Add the route after `admin_create_user`:

```python
@router.post("/admin/users/{target_user_id}/allowed-stores")
async def admin_set_allowed_stores(
    target_user_id: str, request: Request, db: Session = Depends(get_db)
):
    user = _get_session_user(request, db)
    if not user or user.role.value != "admin":
        return RedirectResponse("/", status_code=303)
    form = await request.form()
    store_ids = form.getlist("store_ids")
    set_allowed_stores(db, target_user_id, store_ids)
    return RedirectResponse("/admin/users", status_code=303)
```

- [ ] **Step 2: Update admin_create_user to auto-add allowlist**

In the existing `admin_create_user` function, after `create_user(...)`, add:

```python
    new_user = create_user(db, form["username"], form["password"], form["role"], store_id=store_id)
    set_allowed_stores(db, new_user.id, [store_id])
    return RedirectResponse("/admin/users", status_code=303)
```

(Replace the existing `create_user` + `return` lines.)

- [ ] **Step 3: Add allowed-stores expander to admin_users.html**

In `src/mailfallback/templates/admin_users.html`, add a new action button in the actions div (after the truck button):

```html
<button class="icon-btn" title="Allowed Stores" onclick="toggleRow('stores-{{ u.id }}')">
    <i data-lucide="hard-drive" class="icon-md"></i>
</button>
```

Add a new expander row after the `migrate-{{ u.id }}` row (before `{% endfor %}`):

```html
<tr id="stores-{{ u.id }}" class="hidden">
    <td colspan="6">
        <form method="post" action="/admin/users/{{ u.id }}/allowed-stores">
            <label><strong>Allowed stores for {{ u.username }}:</strong></label>
            <div class="flex gap-05 flex-wrap mt-025">
                {% for s in stores %}
                <label class="checkbox-pill">
                    <input type="checkbox" name="store_ids" value="{{ s.id }}"
                        {% if s in u.allowed_stores %}checked{% endif %}>
                    {{ s.name }}
                </label>
                {% endfor %}
            </div>
            <button type="submit" class="icon-btn primary mt-05">
                <i data-lucide="save" class="icon-md"></i> Save
            </button>
        </form>
    </td>
</tr>
```

- [ ] **Step 4: Rebuild and verify in browser**

Run: `docker compose up -d --build`
Navigate to `/admin/users`, click the hard-drive icon on a user, verify checkboxes appear and save works.

- [ ] **Step 5: Run full test suite**

Run: `uv run pytest tests/ -q`
Expected: all pass

---

### Task 4: Profile — default store selection

**Files:**
- Modify: `src/mailfallback/routers/ui_profile.py`
- Modify: `src/mailfallback/templates/profile.html`

- [ ] **Step 1: Add store imports and route to ui_profile.py**

Add imports:

```python
from mailfallback.services.store_service import get_selectable_stores, get_user_store
from mailfallback.services.user_service import update_user
```

Update `profile_page` to pass store context:

```python
@router.get("/profile", response_class=HTMLResponse)
def profile_page(request: Request, db: Session = Depends(get_db)):
    user = _get_session_user(request, db)
    if not user:
        return RedirectResponse("/login")
    store = get_user_store(db, user)
    selectable_stores = get_selectable_stores(db, user)
    return templates.TemplateResponse(
        request=request,
        name="profile.html",
        context={
            "user": user,
            "store": store,
            "selectable_stores": selectable_stores,
            "error": None,
            "success": None,
        },
    )
```

Add the store change route:

```python
@router.post("/profile/store")
async def profile_change_store(request: Request, db: Session = Depends(get_db)):
    user = _get_session_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)

    form = await request.form()
    new_store_id = form["store_id"]

    allowed_ids = {s.id for s in user.allowed_stores}
    if new_store_id not in allowed_ids:
        return RedirectResponse("/profile", status_code=303)

    update_user(db, user.id, store_id=new_store_id)
    return RedirectResponse("/profile", status_code=303)
```

- [ ] **Step 2: Update profile.html to show store section**

In `src/mailfallback/templates/profile.html`, after the accounts table row (line 9), add:

```html
    <tr>
        <td><i data-lucide="hard-drive" class="icon-md icon-inline"></i>Store</td>
        <td>{{ store.name if store else '—' }}</td>
    </tr>
```

After the `</table>` (line 10), before `<hr>`, add:

```html
{% if selectable_stores %}
<hr>
<h3><i data-lucide="hard-drive" class="icon-lg icon-inline"></i>Default Store</h3>
<p class="text-muted text-small">New accounts will be created on this store. Existing accounts are not affected.</p>
<form method="post" action="/profile/store" class="container-narrow">
    <label for="store_id">Store for new accounts</label>
    <select id="store_id" name="store_id">
        {% for s in selectable_stores %}
        <option value="{{ s.id }}" {% if s.id == user.store_id %}selected{% endif %}>{{ s.name }} ({{ s.path }})</option>
        {% endfor %}
    </select>
    <button type="submit">
        <i data-lucide="save" class="icon-md icon-inline"></i>Save
    </button>
</form>
{% endif %}
```

- [ ] **Step 3: Verify `update_user` supports `store_id`**

Check that `store_id` is in `_UPDATABLE_USER_FIELDS` in `src/mailfallback/services/user_service.py`. If not, add it.

- [ ] **Step 4: Run full test suite**

Run: `uv run pytest tests/ -q`
Expected: all pass

---

### Task 5: Account creation — per-account store override

**Files:**
- Modify: `src/mailfallback/routers/ui_accounts.py:89-183`
- Modify: `src/mailfallback/templates/account_form.html`

- [ ] **Step 1: Pass selectable stores to account form**

In `src/mailfallback/routers/ui_accounts.py`, add import:

```python
from mailfallback.services.store_service import (
    get_selectable_stores,
    get_store,
    get_user_store,
    list_stores,
)
```

Update `account_form` to pass selectable stores:

```python
@router.get("/accounts/new", response_class=HTMLResponse)
def account_form(request: Request, db: Session = Depends(get_db)):
    user = _get_session_user(request, db)
    if not user:
        return RedirectResponse("/login")
    store = get_user_store(db, user)
    selectable_stores = get_selectable_stores(db, user)
    return templates.TemplateResponse(
        request=request,
        name="account_form.html",
        context={"user": user, "store": store, "selectable_stores": selectable_stores, "error": None},
    )
```

- [ ] **Step 2: Validate store_id in account_form_submit**

In `account_form_submit`, replace the existing `store = get_user_store(db, user)` block with:

```python
    store_id_override = form.get("store_id")
    if store_id_override:
        allowed_ids = {s.id for s in user.allowed_stores}
        if store_id_override in allowed_ids:
            store = get_store(db, store_id_override)
        else:
            store = get_user_store(db, user)
    else:
        store = get_user_store(db, user)
    if not store:
        return templates.TemplateResponse(
            request=request,
            name="account_form.html",
            context={
                "user": user,
                "store": None,
                "selectable_stores": None,
                "error": "No store assigned. Contact your administrator.",
            },
        )
```

- [ ] **Step 3: Add store dropdown to account_form.html**

In `src/mailfallback/templates/account_form.html`, replace the store info block (lines 29-33) with:

```html
        {% if selectable_stores %}
        <div>
            <label for="store_id">Store</label>
            <select id="store_id" name="store_id">
                {% for s in selectable_stores %}
                <option value="{{ s.id }}" {% if s.id == store.id %}selected{% endif %}>{{ s.name }} ({{ s.path }})</option>
                {% endfor %}
            </select>
        </div>
        {% elif store %}
        <small class="text-muted"><i data-lucide="hard-drive" class="icon-sm icon-inline"></i> Store: <strong>{{ store.name }}</strong> (<code>{{ store.path }}</code>)</small>
        {% else %}
        <small class="sync-syncing"><i data-lucide="alert-triangle" class="icon-sm icon-inline"></i> No store assigned. Contact your administrator.</small>
        {% endif %}
```

- [ ] **Step 4: Run full test suite**

Run: `uv run pytest tests/ -q`
Expected: all pass

---

### Task 6: Account detail — show store for all users

**Files:**
- Modify: `src/mailfallback/templates/account_detail.html`

- [ ] **Step 1: Make store row visible for all users**

In `src/mailfallback/templates/account_detail.html`, find the store display row (lines 136-145). Change from admin-only to visible for all users:

Replace:

```html
    {% if user.role.value == "admin" %}
    <tr>
        <td><i data-lucide="folder" class="icon-md icon-inline"></i>Maildir</td>
        <td><code>{{ account.maildir_path }}</code></td>
    </tr>
    <tr>
        <td><i data-lucide="hard-drive" class="icon-md icon-inline"></i>Store</td>
        <td>{{ account.store.name }} (<code>{{ account.store.path }}</code>)</td>
    </tr>
    {% endif %}
```

With:

```html
    <tr>
        <td><i data-lucide="hard-drive" class="icon-md icon-inline"></i>Store</td>
        <td>{{ account.store.name }}</td>
    </tr>
    {% if user.role.value == "admin" %}
    <tr>
        <td><i data-lucide="folder" class="icon-md icon-inline"></i>Maildir</td>
        <td><code>{{ account.maildir_path }}</code></td>
    </tr>
    {% endif %}
```

- [ ] **Step 2: Run full test suite**

Run: `uv run pytest tests/ -q`
Expected: all pass

---

### Task 7: Seed existing users — backfill allowlists

**Files:**
- Modify: `src/mailfallback/app.py` (lifespan function)

- [ ] **Step 1: Add backfill to app startup**

In `src/mailfallback/app.py`, in the lifespan function (after `_recover_zombie_jobs` or `ensure_default_store`), add a backfill that ensures every user has at least their current store in their allowlist:

```python
    # Backfill user_allowed_stores for users with empty allowlists
    from mailfallback.services.store_service import set_allowed_stores

    users_without_allowlist = db.query(User).all()
    for u in users_without_allowlist:
        if not u.allowed_stores:
            set_allowed_stores(db, u.id, [u.store_id])
```

This is idempotent — safe to run on every startup.

- [ ] **Step 2: Run full test suite**

Run: `uv run pytest tests/ -q`
Expected: all pass

- [ ] **Step 3: Rebuild and verify end-to-end**

Run: `docker compose up -d --build`

Verify:
1. `/admin/users` — hard-drive icon shows allowlist checkboxes, saves correctly
2. `/profile` — if user has >1 store, dropdown appears; if 1 store, just shows name
3. `/accounts/new` — if user has >1 store, store dropdown in Basic Info; if 1 store, info text
4. `/accounts/{id}` — store name visible for non-admin users
