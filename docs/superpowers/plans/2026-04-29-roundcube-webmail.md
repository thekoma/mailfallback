# Roundcube Webmail Integration — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add read-only webmail access to backed-up email via Roundcube, connecting to the existing Dovecot IMAP server.

**Architecture:** Roundcube runs as a new Docker service on port 8001, connecting to Dovecot IMAP (port 31143) inside the Docker network. Dovecot ACLs enforce read-only access (lrs rights). MFB shows a conditional "Webmail" link in the nav when `MAILFALLBACK_WEBMAIL_URL` is set.

**Tech Stack:** Roundcube (official Docker image), Dovecot ACL plugin, FastAPI/Jinja2 (existing)

**Spec:** `docs/superpowers/specs/2026-04-29-roundcube-webmail-design.md`

---

## File Map

| Action | File | Responsibility |
|--------|------|----------------|
| Create | `docker/dovecot/conf.d/mfb-acl.conf` | Dovecot ACL plugin config — enforces read-only IMAP |
| Modify | `docker-compose.yml` | Add `roundcube` service + `roundcube_data` volume |
| Modify | `src/mailfallback/config.py` | Add `webmail_url` setting |
| Modify | `src/mailfallback/routers/ui.py` | Register `webmail_url` as Jinja2 global |
| Modify | `src/mailfallback/templates/base.html` | Conditional "Webmail" nav link |
| Modify | `.env.example` | Document `MAILFALLBACK_WEBMAIL_URL` |
| Modify | `tests/test_config.py` | Test `webmail_url` default |
| Modify | `tests/test_ui.py` | Test nav link presence/absence |

---

### Task 1: Dovecot ACL config

**Files:**
- Create: `docker/dovecot/conf.d/mfb-acl.conf`

- [ ] **Step 1: Create the ACL config file**

```dovecot
mail_plugins = acl

protocol imap {
  mail_plugins = $mail_plugins imap_acl
}

plugin {
  acl_driver = vfile
  acl_globals_only = yes
}
```

This enables the ACL plugin with `vfile` driver. `acl_globals_only = yes` prevents users from creating per-mailbox ACL override files.

Note: the global default ACL rights (`lrs`) need to be applied. Dovecot 2.4 supports inline ACL definitions in namespace blocks, but the exact syntax must be verified against the running image. Two approaches to try:

**Approach A — inline in namespace (preferred, Dovecot 2.4 style):**
Add to the config:
```dovecot
namespace inbox {
  mailbox * {
    acl owner { rights = lrs }
  }
}
```

**Approach B — global ACL file (fallback if A doesn't work):**
Add to plugin block:
```dovecot
plugin {
  acl_driver = vfile
  acl_globals_only = yes
  acl_global_path = /etc/dovecot/global-acl
}
```
Then create `/etc/dovecot/global-acl` containing:
```
owner lrs
```
And volume-mount it in docker-compose.yml.

- [ ] **Step 2: Verify the config loads correctly**

Start the stack and check Dovecot logs:
```bash
docker compose up -d dovecot
docker compose logs dovecot 2>&1 | tail -20
```
Expected: no config errors related to ACL. If Approach A fails with a parse error, switch to Approach B.

- [ ] **Step 3: Test read-only enforcement**

Connect to Dovecot with a test user and attempt to delete a message:
```bash
docker compose exec dovecot doveadm acl get -u <username> INBOX
```
Expected: only `lookup`, `read`, `write-seen` rights shown.

If `doveadm acl` is not available, test via IMAP directly:
```bash
curl -v --url "imap://localhost:31143/INBOX" --user "admin:changeme" -X "STORE 1 +FLAGS (\Deleted)"
```
Expected: permission denied / NO response.

- [ ] **Step 4: Commit**

```bash
git add docker/dovecot/conf.d/mfb-acl.conf
git commit -m "feat: add Dovecot ACL config for read-only IMAP access"
```

---

### Task 2: Add `webmail_url` setting to config

**Files:**
- Modify: `src/mailfallback/config.py:30` (after `dovecot_api_key`)
- Modify: `tests/test_config.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_config.py`:

```python
def test_webmail_url_defaults():
    s = Settings(secret_key="test", session_secret="test", _env_file=None)
    assert s.webmail_url == ""


def test_webmail_url_from_env(monkeypatch):
    monkeypatch.setenv("MAILFALLBACK_WEBMAIL_URL", "http://localhost:8001")
    s = Settings()
    assert s.webmail_url == "http://localhost:8001"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_config.py::test_webmail_url_defaults -v
```
Expected: FAIL — `Settings` has no attribute `webmail_url`

- [ ] **Step 3: Add the setting**

In `src/mailfallback/config.py`, add after line 32 (`dovecot_api_key: str = ""`):

```python
    webmail_url: str = ""
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/test_config.py -v
```
Expected: all tests PASS including the two new ones.

- [ ] **Step 5: Commit**

```bash
git add src/mailfallback/config.py tests/test_config.py
git commit -m "feat: add webmail_url setting"
```

---

### Task 3: Add Webmail link to nav

**Files:**
- Modify: `src/mailfallback/routers/ui.py:72` (after the `filesizeformat` filter registration)
- Modify: `src/mailfallback/templates/base.html:22` (after "Add Account" link)
- Modify: `tests/test_ui.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_ui.py`:

```python
def test_webmail_link_hidden_by_default(client, db_session):
    create_user(db_session, "admin", "pass", UserRole.admin)
    client.post("/api/auth/login", json={"username": "admin", "password": "pass"})  # pragma: allowlist secret
    resp = client.get("/")
    assert resp.status_code == 200
    assert "Webmail" not in resp.text


def test_webmail_link_shown_when_configured(client, db_session, monkeypatch):
    monkeypatch.setattr("mailfallback.routers.ui.templates.env.globals", {
        **client.app.state.templates_env_globals_backup,
        "webmail_url": "http://localhost:8001",
    })
    create_user(db_session, "admin", "pass", UserRole.admin)
    client.post("/api/auth/login", json={"username": "admin", "password": "pass"})  # pragma: allowlist secret
    resp = client.get("/")
    assert resp.status_code == 200
    assert "Webmail" in resp.text
    assert "http://localhost:8001" in resp.text
```

Note: the monkeypatch approach for Jinja2 globals may need adjustment depending on how the global is registered. A simpler alternative is to monkeypatch `settings.webmail_url` before the app is created. If the global is set at module import time, use the `app` fixture with the monkeypatch applied first:

```python
def test_webmail_link_shown_when_configured(db_session, monkeypatch):
    monkeypatch.setattr("mailfallback.config.settings.webmail_url", "http://localhost:8001")
    from mailfallback.app import create_app
    from mailfallback.dependencies import get_db
    application = create_app()
    application.dependency_overrides[get_db] = lambda: db_session
    test_client = TestClient(application)
    create_user(db_session, "admin", "pass", UserRole.admin)
    test_client.post("/api/auth/login", json={"username": "admin", "password": "pass"})  # pragma: allowlist secret
    resp = test_client.get("/")
    assert resp.status_code == 200
    assert "Webmail" in resp.text
    assert "http://localhost:8001" in resp.text
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_ui.py::test_webmail_link_hidden_by_default tests/test_ui.py::test_webmail_link_shown_when_configured -v
```
Expected: FAIL — "Webmail" check may pass vacuously for the hidden test, the shown test fails because the global isn't set.

- [ ] **Step 3: Register `webmail_url` as Jinja2 global**

In `src/mailfallback/routers/ui.py`, after line 72 (`templates.env.filters["filesizeformat"] = _filesizeformat`), add:

```python
templates.env.globals["webmail_url"] = settings.webmail_url
```

This makes `webmail_url` available in all templates without modifying every route's context dict.

- [ ] **Step 4: Add conditional link to base.html**

In `src/mailfallback/templates/base.html`, after line 22 (the "Add Account" `<li>`), add:

```html
            {% if webmail_url %}
            <li><a href="{{ webmail_url }}" target="_blank" rel="noopener"><i data-lucide="mail" class="icon-nav"></i>Webmail</a></li>
            {% endif %}
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
uv run pytest tests/test_ui.py -v
```
Expected: all tests PASS.

- [ ] **Step 6: Commit**

```bash
git add src/mailfallback/routers/ui.py src/mailfallback/templates/base.html tests/test_ui.py
git commit -m "feat: add conditional Webmail link in nav"
```

---

### Task 4: Add Roundcube service to docker-compose

**Files:**
- Modify: `docker-compose.yml`

- [ ] **Step 1: Add the roundcube service and volume**

Add the `roundcube` service after the `dovecot` service block (before the `volumes:` section):

```yaml
  roundcube:
    image: roundcube/roundcubemail:latest
    ports:
      - "8001:80"
    environment:
      ROUNDCUBEMAIL_DEFAULT_HOST: dovecot
      ROUNDCUBEMAIL_DEFAULT_PORT: "31143"
      ROUNDCUBEMAIL_DB_TYPE: pgsql
      ROUNDCUBEMAIL_DB_HOST: db
      ROUNDCUBEMAIL_DB_PORT: "5432"
      ROUNDCUBEMAIL_DB_USER: mailfallback
      ROUNDCUBEMAIL_DB_PASSWORD: mailfallback
      ROUNDCUBEMAIL_DB_NAME: mailfallback
      ROUNDCUBEMAIL_SKIN: elastic
      ROUNDCUBEMAIL_PLUGINS: archive,zipdownload
    volumes:
      - roundcube_data:/var/roundcube/config
    depends_on:
      db:
        condition: service_healthy
      dovecot:
        condition: service_started
```

Add `roundcube_data:` to the `volumes:` section at the bottom:

```yaml
volumes:
  pgdata:
  maildirs:
  roundcube_data:
```

- [ ] **Step 2: Validate compose config**

```bash
docker compose config --quiet
```
Expected: no errors (exit code 0).

- [ ] **Step 3: Commit**

```bash
git add docker-compose.yml
git commit -m "feat: add Roundcube webmail service to docker-compose"
```

---

### Task 5: Update .env.example and documentation

**Files:**
- Modify: `.env.example`

- [ ] **Step 1: Add webmail_url to .env.example**

Add at the end of `.env.example`:

```bash

# Webmail (optional — show "Webmail" link in nav bar)
# MAILFALLBACK_WEBMAIL_URL=http://localhost:8001
```

- [ ] **Step 2: Commit**

```bash
git add .env.example
git commit -m "docs: add MAILFALLBACK_WEBMAIL_URL to .env.example"
```

---

### Task 6: Integration smoke test

This task verifies the full stack works end-to-end. Requires Docker.

- [ ] **Step 1: Start the full stack**

```bash
docker compose up -d --build
```
Wait for all services to be healthy.

- [ ] **Step 2: Check Roundcube is accessible**

```bash
curl -s -o /dev/null -w "%{http_code}" http://localhost:8001
```
Expected: `200` (Roundcube login page).

- [ ] **Step 3: Check Dovecot ACL is active**

```bash
docker compose logs dovecot 2>&1 | grep -i acl
```
Expected: no errors related to ACL plugin loading.

- [ ] **Step 4: Log into Roundcube**

Open `http://localhost:8001` in a browser. Log in with the MFB admin credentials (`admin` / `changeme`). Verify:
- Mailbox folders are visible
- Messages can be read
- Deleting a message fails or is rejected
- The `\Seen` flag works (messages mark as read)

- [ ] **Step 5: Verify MFB webmail link**

Set `MAILFALLBACK_WEBMAIL_URL=http://localhost:8001` in `.env`, restart MFB:
```bash
docker compose restart mailfallback
```
Open `http://localhost:8000`, log in, verify the "Webmail" link appears in the nav bar and opens Roundcube in a new tab.

- [ ] **Step 6: Run the full test suite**

```bash
uv run pytest tests/ -v
```
Expected: all tests PASS (no regressions).
