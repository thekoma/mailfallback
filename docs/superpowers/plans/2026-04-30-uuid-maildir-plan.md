# UUID-Based Maildir Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Decouple maildir storage from user identity using UUID-based paths, with Dovecot namespace visibility controlled by MFB's internal API.

**Architecture:** Each Account gets `store_id` FK and `maildir_path = store.path/<account-uuid>`. Dovecot Lua userdb calls MFB's `GET /api/internal/dovecot/userdb/{username}` to discover namespaces at login. Store deletion requires draining all contents first.

**Tech Stack:** Python 3.12+, FastAPI, SQLAlchemy, Alembic, Lua (Dovecot 2.4 built-in), `dovecot.http.client`

**Spec:** `docs/superpowers/specs/2026-04-30-uuid-maildir-design.md`

---

### Task 1: Dovecot Lua + HTTP Feasibility Test

Validate that Dovecot 2.4's Lua userdb can call an HTTP endpoint and return dynamic namespace extra fields before writing any production code.

**Files:**
- Create: `tests/dovecot_feasibility/docker-compose.yml`
- Create: `tests/dovecot_feasibility/lua-userdb.lua`
- Create: `tests/dovecot_feasibility/mock-api.py`
- Create: `tests/dovecot_feasibility/mfb-auth.conf`
- Create: `tests/dovecot_feasibility/run-test.sh`

- [ ] **Step 1: Create the mock MFB API server**

A minimal Python HTTP server that returns the userdb JSON response Dovecot's Lua script will consume.

```python
# tests/dovecot_feasibility/mock-api.py
from http.server import HTTPServer, BaseHTTPRequestHandler
import json

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/api/internal/dovecot/userdb/testuser":
            resp = {
                "uid": 1000,
                "gid": 1000,
                "home": "/data/mailboxes/.dovecot-home/testuser",
                "namespaces": [
                    {
                        "name": "acc_1",
                        "prefix": "",
                        "location": "maildir:/data/mailboxes/test-uuid-1:LAYOUT=fs",
                        "inbox": True,
                    },
                    {
                        "name": "acc_2",
                        "prefix": "Second Account/",
                        "location": "maildir:/data/mailboxes/test-uuid-2:LAYOUT=fs",
                        "inbox": False,
                    },
                ],
            }
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(resp).encode())
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, fmt, *args):
        print(f"mock-api: {fmt % args}")

HTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
```

- [ ] **Step 2: Write the Lua userdb script**

```lua
-- tests/dovecot_feasibility/lua-userdb.lua
local http = require("dovecot.http")
local json = require("cjson")

function auth_userdb_lookup(req)
  local url = "http://mock-api:8080/api/internal/dovecot/userdb/" .. req.user
  local http_client = http.client({ timeout = 5000 })
  local http_req = http.request({ url = url, method = "GET" })

  local status, response = http_client:request(http_req)
  if status ~= 200 then
    return dovecot.auth.USERDB_RESULT_USER_UNKNOWN, "API returned " .. tostring(status)
  end

  local data = json.decode(response)
  local fields = {
    uid = tostring(data.uid),
    gid = tostring(data.gid),
    home = data.home,
  }

  for _, ns in ipairs(data.namespaces) do
    fields["namespace/" .. ns.name .. "/location"] = ns.location
    fields["namespace/" .. ns.name .. "/prefix"] = ns.prefix
    fields["namespace/" .. ns.name .. "/separator"] = "/"
    if ns.inbox then
      fields["namespace/" .. ns.name .. "/inbox"] = "yes"
    end
  end

  return dovecot.auth.USERDB_RESULT_OK, fields
end
```

- [ ] **Step 3: Write the Dovecot auth config**

```
# tests/dovecot_feasibility/mfb-auth.conf
auth_mechanisms = plain

passdb static {
  password = testpass
}

userdb lua {
  file = /etc/dovecot/lua-userdb.lua
}
```

- [ ] **Step 4: Write the docker-compose test stack**

```yaml
# tests/dovecot_feasibility/docker-compose.yml
services:
  mock-api:
    image: python:3.12-slim
    command: python /app/mock-api.py
    volumes:
      - ./mock-api.py:/app/mock-api.py:ro

  dovecot:
    image: dovecot/dovecot:latest-2.4
    volumes:
      - ./mfb-auth.conf:/etc/dovecot/conf.d/mfb-auth.conf:ro
      - ./lua-userdb.lua:/etc/dovecot/lua-userdb.lua:ro
    depends_on:
      - mock-api
```

- [ ] **Step 5: Write the test runner script**

```bash
#!/bin/bash
# tests/dovecot_feasibility/run-test.sh
set -e
cd "$(dirname "$0")"

echo "=== Starting feasibility test stack ==="
docker compose up -d --build --wait

echo "=== Testing Lua userdb via doveadm ==="
docker compose exec dovecot doveadm auth test testuser testpass
docker compose exec dovecot doveadm auth lookup testuser

echo "=== Checking namespace fields in lookup output ==="
LOOKUP=$(docker compose exec dovecot doveadm auth lookup testuser 2>&1)
echo "$LOOKUP"

if echo "$LOOKUP" | grep -q "namespace/acc_1/location"; then
  echo "✓ Dynamic namespace fields returned successfully"
else
  echo "✗ Namespace fields NOT found — Lua userdb may not support dynamic namespaces"
  echo "  Falling back to Option A (SQL fixed slots)"
fi

echo "=== Cleanup ==="
docker compose down
```

- [ ] **Step 6: Run the feasibility test**

Run: `bash tests/dovecot_feasibility/run-test.sh`

Expected: `doveadm auth test` succeeds, `doveadm auth lookup` shows `namespace/acc_1/location` and `namespace/acc_2/location` fields.

If this fails, investigate the Dovecot logs (`docker compose logs dovecot`) and adjust the Lua script. Common issues:
- `dovecot.http` module may have different API in 2.4 — check `require` paths
- `cjson` may not be available — try `require("json")` or parse manually
- Namespace extra fields may need different key format in 2.4

If dynamic namespaces from Lua are confirmed impossible, pivot to Option A (SQL fixed slots) and update the plan.

- [ ] **Step 7: Commit feasibility test**

```bash
git add tests/dovecot_feasibility/
git commit -m "test: Dovecot Lua + HTTP feasibility validation for UUID namespaces"
```

---

### Task 2: Data Model — Add Account.store_id

Add `store_id` FK to `Account` and update `StoreMigration` to support per-account migrations.

**Files:**
- Modify: `src/mailfallback/models.py:90-117` (Account class)
- Modify: `src/mailfallback/models.py:136-153` (StoreMigration class)
- Create: `alembic/versions/005_account_store_id_and_migration_rework.py`
- Modify: `tests/conftest.py`
- Create: `tests/test_models_uuid.py`

- [ ] **Step 1: Write tests for the new model fields**

```python
# tests/test_models_uuid.py
from mailfallback.models import Account, MailStore, StoreMigration, User, UserRole


def test_account_has_store_id(db_session, default_store):
    account = Account(
        name="Test",
        imap_host="imap.test.com",
        maildir_path="/data/mailboxes/test-uuid",
        store_id=default_store.id,
    )
    db_session.add(account)
    db_session.commit()
    db_session.refresh(account)
    assert account.store_id == default_store.id
    assert account.store.name == "default"


def test_account_store_relationship(db_session, default_store):
    a1 = Account(
        name="A1",
        imap_host="imap.test.com",
        maildir_path="/data/mailboxes/uuid-1",
        store_id=default_store.id,
    )
    a2 = Account(
        name="A2",
        imap_host="imap.test.com",
        maildir_path="/data/mailboxes/uuid-2",
        store_id=default_store.id,
    )
    db_session.add_all([a1, a2])
    db_session.commit()
    db_session.refresh(default_store)
    assert len(default_store.accounts) == 2


def test_store_migration_per_account(db_session, default_store):
    from mailfallback.services.store_service import create_store

    target = create_store(db_session, "target", "/data/target")
    account = Account(
        name="Test",
        imap_host="imap.test.com",
        maildir_path="/data/mailboxes/test-uuid",
        store_id=default_store.id,
    )
    db_session.add(account)
    db_session.commit()

    migration = StoreMigration(
        account_id=account.id,
        source_store_id=default_store.id,
        target_store_id=target.id,
    )
    db_session.add(migration)
    db_session.commit()
    db_session.refresh(migration)
    assert migration.account_id == account.id
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_models_uuid.py -v`
Expected: FAIL — `Account` has no `store_id` column, `StoreMigration` has no `account_id` column.

- [ ] **Step 3: Update the Account model**

In `src/mailfallback/models.py`, add `store_id` and `store` relationship to `Account`:

```python
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
    store_id = Column(String, ForeignKey("mail_stores.id"), nullable=False)
    sync_schedule = Column(String, nullable=True, default="*/10 * * * *")
    extra_config = Column(Text, nullable=True)
    sync_state = Column(Enum(SyncState), nullable=False, default=SyncState.idle)
    last_sync_at = Column(DateTime(timezone=True), nullable=True)
    last_error = Column(Text, nullable=True)
    total_messages = Column(Integer, nullable=False, default=0)
    unread_messages = Column(Integer, nullable=False, default=0)
    maildir_size_bytes = Column(Integer, nullable=False, default=0)
    folder_stats = Column(Text, nullable=True)
    enabled = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), default=_utcnow)

    store = relationship("MailStore", back_populates="accounts")
    owners = relationship("User", secondary=account_owners, back_populates="accounts")
    sync_jobs = relationship("SyncJob", back_populates="account", cascade="all, delete-orphan")
```

Add `accounts` backref to `MailStore`:

```python
class MailStore(Base):
    __tablename__ = "mail_stores"

    id = Column(String, primary_key=True, default=_new_uuid)
    name = Column(String, nullable=False)
    path = Column(String, unique=True, nullable=False)
    enabled = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), default=_utcnow)

    users = relationship("User", back_populates="store")
    accounts = relationship("Account", back_populates="store")
```

- [ ] **Step 4: Update StoreMigration to per-account**

Replace `user_id` with `account_id` in `StoreMigration`:

```python
class StoreMigration(Base):
    __tablename__ = "store_migrations"

    id = Column(String, primary_key=True, default=_new_uuid)
    account_id = Column(String, ForeignKey("accounts.id"), nullable=True)
    user_id = Column(String, ForeignKey("users.id"), nullable=True)
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
```

Both `account_id` and `user_id` are nullable — a migration targets either an account (maildir move) or a user (dovecot-home move), never both.

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_models_uuid.py -v`
Expected: PASS

- [ ] **Step 6: Generate Alembic migration**

Run: `uv run alembic revision --autogenerate -m "add Account.store_id, rework StoreMigration to per-account"`

Review the generated file. It should:
- Add `store_id` column to `accounts` (NOT NULL with FK)
- Add `account_id` column to `store_migrations` (nullable with FK)
- Make `store_migrations.user_id` nullable

- [ ] **Step 7: Commit**

```bash
git add src/mailfallback/models.py tests/test_models_uuid.py alembic/versions/005_*
git commit -m "feat: add Account.store_id FK and per-account StoreMigration"
```

---

### Task 3: Store Service — UUID Path Derivation

Simplify `derive_maildir_path` to use account UUID instead of username + email.

**Files:**
- Modify: `src/mailfallback/services/store_service.py:25-27`
- Modify: `tests/test_accounts.py` (path assertions)

- [ ] **Step 1: Write test for new derive_maildir_path**

Add to `tests/test_models_uuid.py`:

```python
from mailfallback.services.store_service import derive_maildir_path


def test_derive_maildir_path_uuid():
    path = derive_maildir_path("/data/mailboxes", "550e8400-e29b-41d4-a716-446655440000")
    assert path == "/data/mailboxes/550e8400-e29b-41d4-a716-446655440000"


def test_derive_maildir_path_strips_trailing_slash():
    path = derive_maildir_path("/data/mailboxes/", "some-uuid")
    assert path == "/data/mailboxes/some-uuid"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_models_uuid.py::test_derive_maildir_path_uuid -v`
Expected: FAIL — `derive_maildir_path` still takes 3 args (store_path, username, email).

- [ ] **Step 3: Update derive_maildir_path**

In `src/mailfallback/services/store_service.py`, replace the old function. Keep the old `username`/`email` parameters as optional for backward compat during the transition — Task 4 will remove them:

```python
def derive_maildir_path(store_path: str, account_id: str, _deprecated_email: str | None = None) -> str:
    return f"{store_path.rstrip('/')}/{account_id}"
```

- [ ] **Step 4: Fix migration_service caller**

The accounts router still calls `derive_maildir_path(store.path, username, email)` — this continues to work because the third arg is accepted and ignored. Task 4 will fully rework the router.

In `src/mailfallback/services/migration_service.py:129-131`, change:

```python
account.maildir_path = derive_maildir_path(
    target_store.path, user.username, account.email_address
)
```

to:

```python
account.maildir_path = derive_maildir_path(target_store.path, account.id)
```

- [ ] **Step 5: Run all tests**

Run: `uv run pytest tests/ -v`
Expected: Some existing tests fail due to changed path format — the path no longer contains username/email. Fix path assertions in `tests/test_accounts.py:27`:

```python
assert "/data/mailboxes/" in resp.json()["maildir_path"]
```

And fix any other test that hardcodes old-style paths.

- [ ] **Step 6: Commit**

```bash
git add src/mailfallback/services/store_service.py src/mailfallback/routers/accounts.py \
  src/mailfallback/services/migration_service.py tests/
git commit -m "refactor: derive_maildir_path uses account UUID instead of username+email"
```

---

### Task 4: Account Creation — Store Selection

Update account creation to accept `store_id`, derive `maildir_path` from the selected store, and set `Account.store_id`.

**Files:**
- Modify: `src/mailfallback/routers/accounts.py:17-26, 43-71`
- Modify: `src/mailfallback/services/account_service.py:11-40`
- Modify: `tests/test_accounts.py`

- [ ] **Step 1: Write test for store-aware account creation**

Add to `tests/test_accounts.py`:

```python
def test_create_account_with_store_selection(client, db_session):
    from mailfallback.services.store_service import create_store

    store1 = create_store(db_session, "store-1", "/data/store1")
    store2 = create_store(db_session, "store-2", "/data/store2")
    create_user(db_session, "admin", "pass", UserRole.admin, store_id=store1.id)
    _login(client, "admin", "pass")

    resp = client.post(
        "/api/accounts",
        json={
            "name": "Gmail",
            "email_address": "test@gmail.com",
            "imap_host": "imap.gmail.com",
            "store_id": store2.id,
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["maildir_path"].startswith("/data/store2/")


def test_create_account_defaults_to_user_store(client, db_session):
    from mailfallback.services.store_service import create_store

    store = create_store(db_session, "default", "/data/mailboxes")
    create_user(db_session, "admin", "pass", UserRole.admin, store_id=store.id)
    _login(client, "admin", "pass")

    resp = client.post(
        "/api/accounts",
        json={
            "name": "Gmail",
            "email_address": "test@gmail.com",
            "imap_host": "imap.gmail.com",
        },
    )
    assert resp.status_code == 200
    assert resp.json()["maildir_path"].startswith("/data/mailboxes/")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_accounts.py::test_create_account_with_store_selection -v`
Expected: FAIL — `AccountCreate` has no `store_id` field.

- [ ] **Step 3: Update AccountCreate schema and create endpoint**

In `src/mailfallback/routers/accounts.py`:

```python
class AccountCreate(BaseModel):
    name: str
    email_address: str = ""
    provider: str = "other"
    imap_host: str
    imap_port: int = 993
    auth_type: str = "app_password"
    credentials: str | None = None
    sync_schedule: str = "*/10 * * * *"
    store_id: str | None = None


@router.post("")
def create(
    body: AccountCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    from mailfallback.services.store_service import get_store

    if body.store_id:
        store = get_store(db, body.store_id)
        if not store:
            raise HTTPException(status_code=404, detail="Store not found")
    else:
        store = user.store
        if not store:
            raise HTTPException(status_code=400, detail="No store assigned")

    account = account_service.create_account(
        db,
        name=body.name,
        email_address=body.email_address,
        provider=body.provider,
        imap_host=body.imap_host,
        imap_port=body.imap_port,
        auth_type=body.auth_type,
        credentials=body.credentials,
        sync_schedule=body.sync_schedule,
        store=store,
    )
    account_service.assign_owner(db, account.id, user.id)
    return {"id": account.id, "name": account.name, "maildir_path": account.maildir_path}
```

- [ ] **Step 4: Update account_service.create_account**

In `src/mailfallback/services/account_service.py`:

```python
from mailfallback.models import Account, MailStore, User, UserRole
from mailfallback.services.store_service import derive_maildir_path


def create_account(
    db: Session,
    name: str,
    imap_host: str,
    imap_port: int,
    auth_type: str,
    store: MailStore,
    credentials: str | None = None,
    sync_schedule: str = "*/10 * * * *",
    email_address: str = "",
    provider: str = "other",
) -> Account:
    encrypted_creds = None
    if credentials:
        encrypted_creds = encrypt_credentials(credentials, settings.secret_key)
    account = Account(
        name=name,
        email_address=email_address,
        provider=provider,
        imap_host=imap_host,
        imap_port=imap_port,
        auth_type=auth_type,
        credentials=encrypted_creds,
        store_id=store.id,
        sync_schedule=sync_schedule,
    )
    # Derive maildir_path from store path + account UUID after flush gives us the ID
    db.add(account)
    db.flush()
    account.maildir_path = derive_maildir_path(store.path, account.id)
    db.commit()
    db.refresh(account)
    refresh_scheduler()
    return account
```

Note the `db.flush()` before setting `maildir_path` — we need `account.id` (generated by `_new_uuid` default) to derive the path. The `flush()` materializes the ID without committing.

Remove the `maildir_path` parameter — it's now derived internally.

- [ ] **Step 5: Update all callers and fix remaining tests**

Remove `is_path_available` check from the create endpoint — UUID paths are guaranteed unique by construction.

Remove the import of `is_path_available` from `src/mailfallback/routers/accounts.py`.

Fix all tests in `tests/test_accounts.py` that pass `maildir_path` or check old-style paths.

- [ ] **Step 6: Run all tests**

Run: `uv run pytest tests/ -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add src/mailfallback/routers/accounts.py src/mailfallback/services/account_service.py tests/
git commit -m "feat: store-aware account creation with UUID-derived maildir paths"
```

---

### Task 5: Internal Dovecot API Endpoint

Create the internal API endpoint that Dovecot's Lua userdb calls at login to discover which namespaces a user should see.

**Files:**
- Create: `src/mailfallback/routers/dovecot.py`
- Create: `tests/test_dovecot_api.py`
- Modify: `src/mailfallback/app.py:15-25, 110-119` (register router)

- [ ] **Step 1: Write tests for the dovecot userdb endpoint**

```python
# tests/test_dovecot_api.py
from mailfallback.models import Account, UserRole
from mailfallback.services.user_service import create_user


def test_userdb_returns_namespaces(client, db_session, default_store):
    user = create_user(db_session, "alice", "pass", UserRole.user, store_id=default_store.id)

    a1 = Account(
        name="Work",
        email_address="alice@work.com",
        imap_host="imap.work.com",
        maildir_path="/data/mailboxes/uuid-1",
        store_id=default_store.id,
    )
    a2 = Account(
        name="Personal",
        email_address="alice@gmail.com",
        imap_host="imap.gmail.com",
        maildir_path="/data/mailboxes/uuid-2",
        store_id=default_store.id,
    )
    db_session.add_all([a1, a2])
    db_session.commit()
    user.accounts.extend([a1, a2])
    db_session.commit()

    resp = client.get(
        "/api/internal/dovecot/userdb/alice",
        headers={"X-API-Key": "test-key"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["uid"] == 1000
    assert data["gid"] == 1000
    assert data["home"] == "/data/mailboxes/.dovecot-home/alice"
    assert len(data["namespaces"]) == 2
    assert data["namespaces"][0]["inbox"] is True
    assert data["namespaces"][1]["inbox"] is False
    assert "maildir:" in data["namespaces"][0]["location"]
    assert "LAYOUT=fs" in data["namespaces"][0]["location"]


def test_userdb_no_accounts(client, db_session, default_store):
    create_user(db_session, "bob", "pass", UserRole.user, store_id=default_store.id)

    resp = client.get(
        "/api/internal/dovecot/userdb/bob",
        headers={"X-API-Key": "test-key"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["namespaces"] == []
    assert data["home"] == "/data/mailboxes/.dovecot-home/bob"


def test_userdb_unknown_user(client, db_session):
    resp = client.get(
        "/api/internal/dovecot/userdb/nobody",
        headers={"X-API-Key": "test-key"},
    )
    assert resp.status_code == 404


def test_userdb_disabled_user(client, db_session, default_store):
    user = create_user(db_session, "disabled", "pass", UserRole.user, store_id=default_store.id)
    user.enabled = False
    db_session.commit()

    resp = client.get(
        "/api/internal/dovecot/userdb/disabled",
        headers={"X-API-Key": "test-key"},
    )
    assert resp.status_code == 404


def test_userdb_migrating_user(client, db_session, default_store):
    user = create_user(db_session, "migrating", "pass", UserRole.user, store_id=default_store.id)
    user.migrating = True
    db_session.commit()

    resp = client.get(
        "/api/internal/dovecot/userdb/migrating",
        headers={"X-API-Key": "test-key"},
    )
    assert resp.status_code == 403


def test_userdb_shared_account(client, db_session, default_store):
    u1 = create_user(db_session, "user1", "pass", UserRole.user, store_id=default_store.id)
    u2 = create_user(db_session, "user2", "pass2", UserRole.user, store_id=default_store.id)

    shared = Account(
        name="Shared",
        email_address="shared@company.com",
        imap_host="imap.company.com",
        maildir_path="/data/mailboxes/shared-uuid",
        store_id=default_store.id,
    )
    db_session.add(shared)
    db_session.commit()
    u1.accounts.append(shared)
    u2.accounts.append(shared)
    db_session.commit()

    r1 = client.get("/api/internal/dovecot/userdb/user1", headers={"X-API-Key": "test-key"})
    r2 = client.get("/api/internal/dovecot/userdb/user2", headers={"X-API-Key": "test-key"})

    assert r1.json()["namespaces"][0]["location"] == r2.json()["namespaces"][0]["location"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_dovecot_api.py -v`
Expected: FAIL — 404 on `/api/internal/dovecot/userdb/alice` (router doesn't exist).

- [ ] **Step 3: Implement the dovecot router**

```python
# src/mailfallback/routers/dovecot.py
from fastapi import APIRouter, Depends, HTTPException, Header
from sqlalchemy.orm import Session

from mailfallback.config import settings
from mailfallback.dependencies import get_db
from mailfallback.models import User

router = APIRouter(prefix="/api/internal/dovecot", tags=["dovecot-internal"])


def _check_api_key(x_api_key: str = Header()):
    if x_api_key != settings.dovecot_api_key:
        raise HTTPException(status_code=401, detail="Invalid API key")


@router.get("/userdb/{username}")
def userdb_lookup(
    username: str,
    db: Session = Depends(get_db),
    _: None = Depends(_check_api_key),
):
    user = (
        db.query(User)
        .filter(User.username == username, User.enabled.is_(True))
        .first()
    )
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    if user.migrating:
        raise HTTPException(status_code=403, detail="User is migrating")

    home = f"{user.store.path}/.dovecot-home/{user.username}"

    namespaces = []
    for i, account in enumerate(
        sorted(user.accounts, key=lambda a: a.created_at)
    ):
        namespaces.append(
            {
                "name": f"acc_{i + 1}",
                "prefix": "" if i == 0 else f"{account.name} ({account.email_address})/",
                "location": f"maildir:{account.maildir_path}:LAYOUT=fs",
                "inbox": i == 0,
            }
        )

    return {
        "uid": 1000,
        "gid": 1000,
        "home": home,
        "namespaces": namespaces,
    }
```

- [ ] **Step 4: Register the router in app.py**

In `src/mailfallback/app.py`, add the import and include:

```python
from mailfallback.routers import (
    accounts,
    auth,
    config_io,
    dovecot,
    health,
    sync,
    ui,
    ui_accounts,
    ui_admin,
    ui_profile,
)
```

And in `create_app()`:

```python
app.include_router(dovecot.router)
```

- [ ] **Step 5: Update config.py for test API key**

In tests, `settings.dovecot_api_key` is empty by default. The test uses `"test-key"`. Set it via environment in `conftest.py` or monkeypatch. Simplest: add to `conftest.py` app fixture:

```python
@pytest.fixture
def app(db_session):
    import mailfallback.config as cfg
    original = cfg.settings.dovecot_api_key
    cfg.settings.dovecot_api_key = "test-key"  # pragma: allowlist secret
    application = create_app()
    application.dependency_overrides[get_db] = lambda: db_session
    yield application
    cfg.settings.dovecot_api_key = original
```

- [ ] **Step 6: Run tests**

Run: `uv run pytest tests/test_dovecot_api.py -v`
Expected: PASS

- [ ] **Step 7: Run full test suite**

Run: `uv run pytest tests/ -v`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add src/mailfallback/routers/dovecot.py src/mailfallback/app.py tests/test_dovecot_api.py tests/conftest.py
git commit -m "feat: internal Dovecot userdb API endpoint with namespace discovery"
```

---

### Task 6: Lua Userdb Script + Dovecot Config

Write the production Lua userdb script and update Dovecot config files.

**Files:**
- Create: `docker/dovecot/conf.d/mfb-lua-userdb.lua`
- Modify: `docker/dovecot/conf.d/mfb-auth.conf`
- Modify: `docker/dovecot/conf.d/mfb-mail.conf`
- Modify: `docker-compose.yml:37-60`

- [ ] **Step 1: Write the production Lua userdb script**

Adapt from the feasibility test, using the real MFB API URL:

```lua
-- docker/dovecot/conf.d/mfb-lua-userdb.lua
local json = require("cjson")

function script_init()
  return 0
end

function auth_userdb_lookup(req)
  local api_url = os.getenv("MFB_USERDB_URL") or "http://mailfallback:8000"
  local api_key = os.getenv("DOVECOT_API_KEY") or ""
  local url = api_url .. "/api/internal/dovecot/userdb/" .. req.user

  local http_client = dovecot.http.client({ timeout = 5000, debug = false })
  local http_req = dovecot.http.request({
    url = url,
    method = "GET",
  })
  http_req:add_header("X-API-Key", api_key)

  local status = http_client:request(http_req)
  if status ~= 200 then
    return dovecot.auth.USERDB_RESULT_USER_UNKNOWN, "API returned " .. tostring(status)
  end

  local body = http_req:response_payload()
  local data = json.decode(body)

  local fields = {
    uid = tostring(data.uid),
    gid = tostring(data.gid),
    home = data.home,
  }

  for _, ns in ipairs(data.namespaces) do
    fields["namespace/" .. ns.name .. "/location"] = ns.location
    fields["namespace/" .. ns.name .. "/prefix"] = ns.prefix
    fields["namespace/" .. ns.name .. "/separator"] = "/"
    if ns.inbox then
      fields["namespace/" .. ns.name .. "/inbox"] = "yes"
    end
  end

  return dovecot.auth.USERDB_RESULT_OK, fields
end
```

Note: The exact Lua API may differ based on findings from Task 1. Update this script to match the working API discovered during feasibility testing.

- [ ] **Step 2: Update mfb-auth.conf — replace SQL userdb with Lua**

```
# docker/dovecot/conf.d/mfb-auth.conf
import_environment {
  DOVECOT_DB_HOST = %{env:DOVECOT_DB_HOST | default(localhost)}
  DOVECOT_DB_PORT = %{env:DOVECOT_DB_PORT | default(5432)}
  DOVECOT_DB_NAME = %{env:DOVECOT_DB_NAME | default(mailfallback)}
  DOVECOT_DB_USER = %{env:DOVECOT_DB_USER | default(mailfallback)}
  DOVECOT_DB_PASSWORD = %{env:DOVECOT_DB_PASSWORD | default(mailfallback)}
  DOVECOT_API_KEY = %{env:DOVECOT_API_KEY | default(changeme)}
  DOVECOT_OAUTH2_USERINFO_URL = %{env:DOVECOT_OAUTH2_USERINFO_URL | default(none)}
  MFB_USERDB_URL = %{env:MFB_USERDB_URL | default(http://mailfallback:8000)}
}

auth_mechanisms = plain login oauthbearer xoauth2

sql_driver = pgsql

pgsql %{env:DOVECOT_DB_HOST} {
  parameters {
    port = %{env:DOVECOT_DB_PORT}
    dbname = %{env:DOVECOT_DB_NAME}
    user = %{env:DOVECOT_DB_USER}
    password = %{env:DOVECOT_DB_PASSWORD}
  }
}

oauth2_introspection_url = %{env:DOVECOT_OAUTH2_USERINFO_URL}
oauth2_introspection_mode = auth
oauth2_username_attribute = preferred_username

passdb oauth2 {
  mechanisms_filter = oauthbearer xoauth2
}

passdb sql {
  query = SELECT username, password_hash AS password \
    FROM users \
    WHERE username = '%{user}' AND enabled = true AND migrating = false
}

userdb lua {
  file = /etc/dovecot/conf.d/mfb-lua-userdb.lua
}

passdb_default_password_scheme = BLF-CRYPT  # pragma: allowlist secret
doveadm_password = %{env:DOVECOT_API_KEY}  # pragma: allowlist secret
```

Key change: `userdb sql { ... }` replaced with `userdb lua { ... }`.

- [ ] **Step 3: Update mfb-mail.conf — remove static namespace**

```
# docker/dovecot/conf.d/mfb-mail.conf
mail_path = ~
mailbox_list_layout = fs
```

Remove the static `namespace inbox` block — namespaces are now dynamic from Lua.

- [ ] **Step 4: Update docker-compose.yml**

Add the Lua script volume mount and MFB dependency to the dovecot service:

```yaml
  dovecot:
    image: dovecot/dovecot:latest-2.4
    volumes:
      - ./docker/dovecot/conf.d:/etc/dovecot/conf.d:ro
      - ./docker/dovecot/dovecot-acl:/etc/dovecot/dovecot-acl:ro
      - maildirs:/data/mailboxes
    ports:
      - "31143:31143"
    environment:
      DOVECOT_DB_HOST: db
      DOVECOT_DB_PORT: "5432"
      DOVECOT_DB_NAME: ${DB_NAME:-mailfallback}
      DOVECOT_DB_USER: ${DB_USER:-mailfallback}
      DOVECOT_DB_PASSWORD: ${DB_PASSWORD:-mailfallback}
      DOVECOT_API_KEY: ${MAILFALLBACK_DOVECOT_API_KEY:-mfb-dovecot-api-key-change-me}
      DOVECOT_OAUTH2_USERINFO_URL: ${DOVECOT_OAUTH2_USERINFO_URL:-}
      MFB_USERDB_URL: http://mailfallback:8000
    depends_on:
      db:
        condition: service_healthy
      mailfallback:
        condition: service_healthy
```

Note: Dovecot now depends on `mailfallback` (not just `db`) because the Lua userdb calls MFB's API.

- [ ] **Step 5: Commit**

```bash
git add docker/dovecot/conf.d/ docker-compose.yml
git commit -m "feat: Lua userdb script + Dovecot config for API-driven namespaces"
```

---

### Task 7: Migration Service — Per-Account

Rewrite migration to work at account level (maildir migration) and user level (dovecot-home migration).

**Files:**
- Modify: `src/mailfallback/services/migration_service.py`
- Modify: `src/mailfallback/app.py:50-76` (resume logic)
- Rewrite: `tests/test_migration_service.py`

- [ ] **Step 1: Write tests for per-account migration**

```python
# tests/test_migration_service.py
import os
import tempfile

import pytest

from mailfallback.models import Account, MailStore, MigrationStatus, User, UserRole
from mailfallback.services.migration_service import (
    execute_account_migration,
    execute_home_migration,
    initiate_account_migration,
    initiate_home_migration,
)


def _make_store(db, name, path):
    store = MailStore(name=name, path=path)
    db.add(store)
    db.commit()
    db.refresh(store)
    return store


def _make_user(db, username, store):
    user = User(
        username=username,
        password_hash="x",
        role=UserRole.user,
        enabled=True,
        store_id=store.id,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def test_initiate_account_migration(db_session):
    src = _make_store(db_session, "src", "/tmp/src")
    dst = _make_store(db_session, "dst", "/tmp/dst")
    account = Account(
        name="Test",
        imap_host="imap.test.com",
        maildir_path=f"/tmp/src/test-uuid",
        store_id=src.id,
    )
    db_session.add(account)
    db_session.commit()

    migration = initiate_account_migration(db_session, account.id, dst.id)
    assert migration.account_id == account.id
    assert migration.source_store_id == src.id
    assert migration.target_store_id == dst.id


def test_initiate_account_migration_rejects_same_store(db_session):
    store = _make_store(db_session, "only", "/tmp/only")
    account = Account(
        name="Test",
        imap_host="imap.test.com",
        maildir_path="/tmp/only/test-uuid",
        store_id=store.id,
    )
    db_session.add(account)
    db_session.commit()

    with pytest.raises(ValueError, match="same store"):
        initiate_account_migration(db_session, account.id, store.id)


def test_execute_account_migration_full_flow(db_session):
    with tempfile.TemporaryDirectory() as src_dir, tempfile.TemporaryDirectory() as dst_dir:
        src_store = _make_store(db_session, "source", src_dir)
        dst_store = _make_store(db_session, "target", dst_dir)

        account = Account(
            name="Test Gmail",
            email_address="test@gmail.com",
            imap_host="imap.gmail.com",
            maildir_path=f"{src_dir}/test-uuid",
            store_id=src_store.id,
        )
        db_session.add(account)
        db_session.commit()

        cur_dir = os.path.join(src_dir, "test-uuid", "cur")
        os.makedirs(cur_dir)
        for i in range(3):
            with open(os.path.join(cur_dir, f"msg{i}"), "w") as f:
                f.write(f"email content {i}")

        migration = initiate_account_migration(db_session, account.id, dst_store.id)
        execute_account_migration(db_session, migration.id)

        db_session.refresh(migration)
        db_session.refresh(account)

        assert migration.status == MigrationStatus.completed
        assert migration.total_files == 3
        assert account.store_id == dst_store.id
        assert account.maildir_path == f"{dst_dir}/{account.id}"
        assert os.path.exists(os.path.join(dst_dir, account.id, "cur", "msg0"))
        assert not os.path.exists(os.path.join(src_dir, "test-uuid"))


def test_initiate_home_migration(db_session):
    src = _make_store(db_session, "src", "/tmp/src")
    dst = _make_store(db_session, "dst", "/tmp/dst")
    user = _make_user(db_session, "alice", src)

    migration = initiate_home_migration(db_session, user.id, dst.id)
    assert migration.user_id == user.id
    assert migration.source_store_id == src.id
    assert migration.target_store_id == dst.id

    db_session.refresh(user)
    assert user.migrating is True


def test_execute_home_migration_full_flow(db_session):
    with tempfile.TemporaryDirectory() as src_dir, tempfile.TemporaryDirectory() as dst_dir:
        src_store = _make_store(db_session, "source", src_dir)
        dst_store = _make_store(db_session, "target", dst_dir)
        user = _make_user(db_session, "alice", src_store)

        home_dir = os.path.join(src_dir, ".dovecot-home", "alice")
        os.makedirs(home_dir)
        with open(os.path.join(home_dir, "dovecot.index"), "w") as f:
            f.write("index data")

        migration = initiate_home_migration(db_session, user.id, dst_store.id)
        execute_home_migration(db_session, migration.id)

        db_session.refresh(migration)
        db_session.refresh(user)

        assert migration.status == MigrationStatus.completed
        assert user.store_id == dst_store.id
        assert user.migrating is False
        assert os.path.exists(os.path.join(dst_dir, ".dovecot-home", "alice", "dovecot.index"))
        assert not os.path.exists(home_dir)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_migration_service.py -v`
Expected: FAIL — functions `initiate_account_migration`, `execute_account_migration`, `initiate_home_migration`, `execute_home_migration` don't exist.

- [ ] **Step 3: Rewrite migration_service.py**

```python
# src/mailfallback/services/migration_service.py
import logging
import os
import shutil
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from mailfallback.models import Account, MailStore, MigrationStatus, StoreMigration, User
from mailfallback.services.migration_worker import copy_tree, prescan, verify_copy
from mailfallback.services.store_service import derive_maildir_path

logger = logging.getLogger(__name__)


def initiate_account_migration(db: Session, account_id: str, target_store_id: str) -> StoreMigration:
    account = db.query(Account).filter(Account.id == account_id).first()
    if not account:
        raise ValueError("Account not found")

    target_store = db.query(MailStore).filter(MailStore.id == target_store_id).first()
    if not target_store:
        raise ValueError("Target store not found")

    if account.store_id == target_store_id:
        raise ValueError("Account is already on the same store")

    migration = StoreMigration(
        account_id=account.id,
        source_store_id=account.store_id,
        target_store_id=target_store_id,
    )
    db.add(migration)
    db.commit()
    db.refresh(migration)
    return migration


def execute_account_migration(db: Session, migration_id: str) -> None:
    migration = db.query(StoreMigration).filter(StoreMigration.id == migration_id).first()
    if not migration:
        logger.error("Migration %s not found", migration_id)
        return

    account = db.query(Account).filter(Account.id == migration.account_id).first()
    target_store = db.query(MailStore).filter(MailStore.id == migration.target_store_id).first()

    if not account or not target_store:
        migration.status = MigrationStatus.failed
        migration.error = "Missing account or store record"
        db.commit()
        return

    try:
        now = datetime.now(UTC)
        migration.status = MigrationStatus.copying
        migration.started_at = now
        if migration.last_resumed_at is None:
            migration.last_resumed_at = now
        db.commit()

        source_path = account.maildir_path
        target_path = derive_maildir_path(target_store.path, account.id)

        if os.path.exists(source_path):
            total_files, total_bytes = prescan(source_path)
            migration.total_files = total_files
            migration.total_bytes = total_bytes
            db.commit()

            _file_counter = [0]

            def on_progress(copied_files: int, copied_bytes: int) -> None:
                migration.copied_files = copied_files
                migration.copied_bytes = copied_bytes
                _file_counter[0] += 1
                if _file_counter[0] % 100 == 0:
                    db.commit()

            copy_tree(source_path, target_path, on_progress=on_progress)

            migration.status = MigrationStatus.verifying
            db.commit()

            ok, detail = verify_copy(source_path, target_path)
            if not ok:
                migration.status = MigrationStatus.failed
                migration.error = detail
                db.commit()
                return
        else:
            migration.total_files = 0
            migration.total_bytes = 0

        migration.status = MigrationStatus.cleaning
        db.commit()

        account.maildir_path = target_path
        account.store_id = target_store.id
        db.commit()

        if os.path.exists(source_path):
            shutil.rmtree(source_path, ignore_errors=True)

        migration.status = MigrationStatus.completed
        migration.completed_at = datetime.now(UTC)
        db.commit()

    except Exception as exc:
        logger.exception("Account migration %s failed", migration_id)
        migration.status = MigrationStatus.failed
        migration.error = str(exc)
        db.commit()


def initiate_home_migration(db: Session, user_id: str, target_store_id: str) -> StoreMigration:
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise ValueError("User not found")

    if user.migrating:
        raise ValueError("User is already migrating")

    target_store = db.query(MailStore).filter(MailStore.id == target_store_id).first()
    if not target_store:
        raise ValueError("Target store not found")

    if user.store_id == target_store_id:
        raise ValueError("User home is already on the same store")

    user.migrating = True

    migration = StoreMigration(
        user_id=user.id,
        source_store_id=user.store_id,
        target_store_id=target_store_id,
    )
    db.add(migration)
    db.commit()
    db.refresh(migration)
    return migration


def execute_home_migration(db: Session, migration_id: str) -> None:
    migration = db.query(StoreMigration).filter(StoreMigration.id == migration_id).first()
    if not migration:
        logger.error("Migration %s not found", migration_id)
        return

    user = db.query(User).filter(User.id == migration.user_id).first()
    source_store = db.query(MailStore).filter(MailStore.id == migration.source_store_id).first()
    target_store = db.query(MailStore).filter(MailStore.id == migration.target_store_id).first()

    if not user or not source_store or not target_store:
        migration.status = MigrationStatus.failed
        migration.error = "Missing user or store record"
        db.commit()
        return

    try:
        now = datetime.now(UTC)
        migration.status = MigrationStatus.copying
        migration.started_at = now
        if migration.last_resumed_at is None:
            migration.last_resumed_at = now
        db.commit()

        source_home = f"{source_store.path}/.dovecot-home/{user.username}"
        target_home = f"{target_store.path}/.dovecot-home/{user.username}"

        if os.path.exists(source_home):
            total_files, total_bytes = prescan(source_home)
            migration.total_files = total_files
            migration.total_bytes = total_bytes
            db.commit()

            _file_counter = [0]

            def on_progress(copied_files: int, copied_bytes: int) -> None:
                migration.copied_files = copied_files
                migration.copied_bytes = copied_bytes
                _file_counter[0] += 1
                if _file_counter[0] % 100 == 0:
                    db.commit()

            copy_tree(source_home, target_home, on_progress=on_progress)

            migration.status = MigrationStatus.verifying
            db.commit()

            ok, detail = verify_copy(source_home, target_home)
            if not ok:
                migration.status = MigrationStatus.failed
                migration.error = detail
                db.commit()
                return
        else:
            migration.total_files = 0
            migration.total_bytes = 0

        migration.status = MigrationStatus.cleaning
        db.commit()

        user.store_id = target_store.id
        db.commit()

        if os.path.exists(source_home):
            shutil.rmtree(source_home, ignore_errors=True)

        migration.status = MigrationStatus.completed
        migration.completed_at = datetime.now(UTC)
        user.migrating = False
        db.commit()

    except Exception as exc:
        logger.exception("Home migration %s failed", migration_id)
        migration.status = MigrationStatus.failed
        migration.error = str(exc)
        db.commit()
```

- [ ] **Step 4: Update app.py resume logic**

In `src/mailfallback/app.py`, update `_resume_migrations` to handle both migration types:

```python
def _resume_migrations(db):
    from datetime import UTC, datetime

    incomplete = (
        db.query(StoreMigration)
        .filter(
            StoreMigration.status.in_(
                [
                    MigrationStatus.pending,
                    MigrationStatus.copying,
                    MigrationStatus.verifying,
                    MigrationStatus.cleaning,
                ]
            )
        )
        .all()
    )
    for migration in incomplete:
        migration.last_resumed_at = datetime.now(UTC)
        db.commit()
        if migration.account_id:
            target = execute_account_migration
            label = f"account {migration.account_id}"
        else:
            target = execute_home_migration
            label = f"user home {migration.user_id}"
        logger.info("Resuming migration %s for %s", migration.id, label)
        thread = threading.Thread(
            target=_run_migration,
            args=(migration.id, target),
            daemon=True,
        )
        thread.start()


def _run_migration(migration_id: str, execute_fn):
    db = SessionLocal()
    try:
        execute_fn(db, migration_id)
    finally:
        db.close()
```

Update the import at the top of `app.py`:

```python
from mailfallback.services.migration_service import execute_account_migration, execute_home_migration
```

- [ ] **Step 5: Run tests**

Run: `uv run pytest tests/test_migration_service.py -v`
Expected: PASS

Run: `uv run pytest tests/ -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/mailfallback/services/migration_service.py src/mailfallback/app.py tests/test_migration_service.py
git commit -m "refactor: per-account and per-user-home store migration"
```

---

### Task 8: Store Deletion Drain

Implement the "drain before delete" flow — stores can only be deleted when empty.

**Files:**
- Modify: `src/mailfallback/services/store_service.py:61-70`
- Create: `tests/test_store_drain.py`

- [ ] **Step 1: Write tests for store drain**

```python
# tests/test_store_drain.py
from mailfallback.models import Account, MailStore, UserRole
from mailfallback.services.store_service import create_store, delete_store, get_store_contents
from mailfallback.services.user_service import create_user


def test_get_store_contents_empty(db_session):
    store = create_store(db_session, "empty", "/data/empty")
    contents = get_store_contents(db_session, store.id)
    assert contents["accounts"] == []
    assert contents["users"] == []


def test_get_store_contents_with_data(db_session):
    store = create_store(db_session, "full", "/data/full")
    user = create_user(db_session, "alice", "pass", UserRole.user, store_id=store.id)
    account = Account(
        name="Test",
        imap_host="imap.test.com",
        maildir_path="/data/full/test-uuid",
        store_id=store.id,
    )
    db_session.add(account)
    db_session.commit()

    contents = get_store_contents(db_session, store.id)
    assert len(contents["accounts"]) == 1
    assert len(contents["users"]) == 1
    assert contents["accounts"][0]["id"] == account.id
    assert contents["users"][0]["id"] == user.id


def test_delete_empty_store(db_session):
    store = create_store(db_session, "deletable", "/data/deletable")
    ok, error = delete_store(db_session, store.id)
    assert ok is True
    assert error is None


def test_delete_non_empty_store_blocked(db_session):
    store = create_store(db_session, "full", "/data/full")
    create_user(db_session, "alice", "pass", UserRole.user, store_id=store.id)

    ok, error = delete_store(db_session, store.id)
    assert ok is False
    assert "not empty" in error.lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_store_drain.py -v`
Expected: FAIL — `get_store_contents` doesn't exist, `delete_store` doesn't check for accounts.

- [ ] **Step 3: Add get_store_contents and update delete_store**

In `src/mailfallback/services/store_service.py`:

```python
def get_store_contents(db: Session, store_id: str) -> dict:
    store = db.query(MailStore).filter(MailStore.id == store_id).first()
    if not store:
        return {"accounts": [], "users": []}

    accounts_on_store = db.query(Account).filter(Account.store_id == store_id).all()
    users_on_store = db.query(User).filter(User.store_id == store_id).all()

    return {
        "accounts": [{"id": a.id, "name": a.name, "email_address": a.email_address} for a in accounts_on_store],
        "users": [{"id": u.id, "username": u.username} for u in users_on_store],
    }


def delete_store(db: Session, store_id: str) -> tuple[bool, str | None]:
    store = db.query(MailStore).filter(MailStore.id == store_id).first()
    if not store:
        return False, "Store not found"

    contents = get_store_contents(db, store_id)
    if contents["accounts"] or contents["users"]:
        return False, "Store is not empty — migrate all accounts and user homes before deleting"

    db.delete(store)
    db.commit()
    return True, None
```

Add `User` import at the top of `store_service.py`:

```python
from mailfallback.models import Account, MailStore, User
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_store_drain.py -v`
Expected: PASS

Run: `uv run pytest tests/ -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/mailfallback/services/store_service.py tests/test_store_drain.py
git commit -m "feat: store deletion requires draining all accounts and user homes first"
```

---

### Task 9: Stats Service — Namespace-Aware

Update stats collection to work with namespace-based mailbox paths instead of email-prefix filtering.

**Files:**
- Modify: `src/mailfallback/services/stats_service.py`
- Modify: `tests/test_stats_service.py`

- [ ] **Step 1: Write test for namespace-aware stats**

Check existing tests in `tests/test_stats_service.py` and update them. The key change: mailbox names from Dovecot will no longer have a `sanitized_email/` prefix — they'll be direct folder names from the namespace (e.g., `INBOX`, `Sent`, not `alice_gmail_com/INBOX`).

```python
# Add to tests/test_stats_service.py or replace existing test

def test_collect_account_stats_namespace_aware(db_session, default_store):
    """Stats work with namespace-based mailbox names (no email prefix)."""
    from unittest.mock import patch

    from mailfallback.models import Account, UserRole
    from mailfallback.services.stats_service import collect_account_stats
    from mailfallback.services.user_service import create_user

    user = create_user(db_session, "alice", "pass", UserRole.user, store_id=default_store.id)
    account = Account(
        name="Gmail",
        email_address="alice@gmail.com",
        imap_host="imap.gmail.com",
        maildir_path="/data/mailboxes/uuid-1",
        store_id=default_store.id,
    )
    db_session.add(account)
    db_session.commit()
    user.accounts.append(account)
    db_session.commit()

    mock_stats = [
        {"mailbox": "INBOX", "messages": 100, "unseen": 5, "vsize": 50000},
        {"mailbox": "Sent", "messages": 50, "unseen": 0, "vsize": 25000},
    ]

    with patch("mailfallback.services.stats_service.get_mailbox_stats", return_value=mock_stats):
        collect_account_stats(db_session, account)

    db_session.refresh(account)
    assert account.total_messages == 150
    assert account.unread_messages == 5
    assert account.maildir_size_bytes == 75000
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_stats_service.py::test_collect_account_stats_namespace_aware -v`
Expected: FAIL — stats_service still filters by `sanitize_email(account.email_address) + "/"` prefix, which drops all entries since `INBOX` doesn't start with `alice_gmail_com/`.

- [ ] **Step 3: Update stats_service.py**

With namespaces, each account gets its own Dovecot namespace. The doveadm `mailboxStatus` command with the right user will return ALL mailboxes across all namespaces. We need to query per-namespace.

Simplify: since each namespace has a unique prefix, we can filter by prefix. But the first account has no prefix (it's the inbox namespace). Instead, call doveadm once per account using the namespace name.

For now, the simplest approach: since doveadm returns all mailboxes and the first account has no prefix while others have `AccountName (email)/` prefix, we can filter accordingly.

```python
# src/mailfallback/services/stats_service.py
import json
import logging

from sqlalchemy.orm import Session

from mailfallback.models import Account

logger = logging.getLogger(__name__)


def collect_account_stats(db: Session, account: Account) -> None:
    try:
        if not account.owners:
            return

        from mailfallback.services.dovecot_manager import get_mailbox_stats

        owner = account.owners[0]
        stats = get_mailbox_stats(owner.username)
        if not stats:
            return

        accounts_by_creation = sorted(owner.accounts, key=lambda a: a.created_at)
        account_index = next(
            (i for i, a in enumerate(accounts_by_creation) if a.id == account.id),
            None,
        )
        if account_index is None:
            return

        if account_index == 0:
            prefix = ""
        else:
            prefix = f"{account.name} ({account.email_address})/"

        folder_stats = []
        for entry in stats:
            mailbox = entry["mailbox"]
            if prefix:
                if not mailbox.startswith(prefix):
                    continue
                folder_name = mailbox[len(prefix):]
            else:
                if any(
                    mailbox.startswith(f"{a.name} ({a.email_address})/")
                    for a in accounts_by_creation[1:]
                ):
                    continue
                folder_name = mailbox

            folder_stats.append(
                {
                    "name": folder_name,
                    "messages": entry["messages"],
                    "unread": entry["unseen"],
                    "size_bytes": entry["vsize"],
                }
            )

        account.total_messages = sum(f["messages"] for f in folder_stats)
        account.unread_messages = sum(f["unread"] for f in folder_stats)
        account.maildir_size_bytes = sum(f["size_bytes"] for f in folder_stats)
        account.folder_stats = json.dumps(folder_stats) if folder_stats else None

        db.commit()
        logger.info(
            "Stats for %s: %d messages (%d unread), %d folders",
            account.name,
            account.total_messages,
            account.unread_messages,
            len(folder_stats),
        )

    except Exception:
        logger.warning("Failed to collect stats for %s", account.name, exc_info=True)
        db.rollback()
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_stats_service.py -v`
Expected: PASS

Run: `uv run pytest tests/ -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/mailfallback/services/stats_service.py tests/test_stats_service.py
git commit -m "refactor: namespace-aware stats collection for UUID maildir"
```

---

### Task 10: Fix Remaining Tests + Integration Smoke Test

Fix all remaining test failures from the refactor and add an integration smoke test.

**Files:**
- Modify: `tests/test_accounts.py`
- Modify: `tests/test_integration.py`
- Modify: any other failing tests

- [ ] **Step 1: Run full test suite and inventory failures**

Run: `uv run pytest tests/ -v 2>&1 | tail -40`

Identify all failing tests. Common fixes needed:
- Tests creating `Account` without `store_id` → add `store_id=default_store.id`
- Tests checking `maildir_path` format → update to UUID format
- Tests calling `derive_maildir_path` with old 3-arg signature → update to 2-arg
- Tests for old `initiate_migration` / `execute_migration` → already rewritten in Task 7

- [ ] **Step 2: Fix each failing test file**

For each failing test, add `store_id` when constructing `Account` objects. Update path assertions. Do NOT change test logic — only update the parts affected by the schema change.

Key files likely affected:
- `tests/test_accounts.py` — Account creation tests
- `tests/test_integration.py` — End-to-end flow tests
- `tests/test_sync_worker.py` — May construct Account objects
- `tests/test_sync_api.py` — May construct Account objects

- [ ] **Step 3: Run full test suite**

Run: `uv run pytest tests/ -v`
Expected: ALL PASS

- [ ] **Step 4: Commit**

```bash
git add tests/
git commit -m "test: fix all tests for UUID maildir + Account.store_id schema"
```

---

### Post-Plan Notes

**Execution order is strict** — each task depends on prior tasks completing. Tasks cannot be parallelized except Task 1 (feasibility) which is independent.

**Task 1 is a gate.** If the Dovecot Lua feasibility test fails, the Lua-specific code in Tasks 5 and 6 must be adapted to use SQL fixed slots (Option A) instead. The rest of the plan (Tasks 2-4, 7-10) is unaffected by the Dovecot approach choice.

**UI changes are out of scope.** This plan covers the backend + Dovecot integration. UI changes for store selection in account creation, store drain flow, and account assign/unassign are a separate plan.

**After this plan completes:**
1. `docker compose up -d --build` with fresh volumes
2. Create admin user (auto via lifespan)
3. Create account → gets UUID path
4. Sync the account → mail at `/store/<uuid>/`
5. Login via IMAP → Dovecot Lua calls MFB API → namespace appears
6. Verify in Roundcube
