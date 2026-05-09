# Centralized Config Generation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** MFB generates all Dovecot and Roundcube config files at startup into named volumes, replacing static files in the repo. Tika support added as optional FTS attachment indexing.

**Architecture:** New `config_generator.py` service writes config files to `/confs/<component>/` at boot. Each component mounts its volume at the path it expects (`/etc/dovecot/conf.d`, `/var/roundcube/config`). Feature flags (`MAILFALLBACK_WEBMAIL_ENABLED`, `MAILFALLBACK_TIKA_ENABLED`) control which configs are generated. `dovecot_enabled` removed — Dovecot is always on.

**Tech Stack:** Python f-string templates, FastAPI lifespan hooks, Docker Compose named volumes + profiles

---

### Task 1: Update config.py Settings

**Files:**
- Modify: `src/mailfallback/config.py`
- Test: `tests/test_config.py`

- [ ] **Step 1: Write tests for new settings**

Add to `tests/test_config.py`:

```python
def test_tika_settings_defaults():
    from mailfallback.config import Settings
    s = Settings()
    assert s.tika_enabled is False
    assert s.tika_url == "http://tika:9998"


def test_webmail_settings_defaults():
    from mailfallback.config import Settings
    s = Settings()
    assert s.webmail_enabled is False
    assert s.confs_path == "/confs"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_config.py -v -k "tika or webmail_settings"`
Expected: FAIL — `tika_enabled`, `webmail_enabled`, `confs_path` don't exist yet

- [ ] **Step 3: Update config.py**

In `src/mailfallback/config.py`, replace:

```python
    dovecot_enabled: bool = False
    dovecot_api_url: str = "http://dovecot:8080"
    dovecot_api_key: str = ""
    dovecot_imap_host: str = "dovecot"
    dovecot_imap_port: int = 31143
    webmail_url: str = ""
```

With:

```python
    dovecot_api_url: str = "http://dovecot:8080"
    dovecot_api_key: str = ""
    dovecot_imap_host: str = "dovecot"
    dovecot_imap_port: int = 31143

    webmail_enabled: bool = False
    webmail_url: str = ""

    tika_enabled: bool = False
    tika_url: str = "http://tika:9998"

    confs_path: str = "/confs"

    db_host: str = "db"
    db_port: int = 5432
    db_name: str = "mailfallback"
    db_user: str = "mailfallback"
    db_password: str = "mailfallback"
```

Note: `db_host/port/name/user/password` are needed so the config generator can embed DB credentials in Dovecot config. They're read from `MAILFALLBACK_DB_HOST` etc. The existing `database_url` stays for SQLAlchemy.

- [ ] **Step 4: Fix all references to `dovecot_enabled`**

Search and replace across the codebase:

```bash
grep -rn "dovecot_enabled\|settings.dovecot_enabled" src/
```

In every file that checks `settings.dovecot_enabled`, replace with `True` (Dovecot is always on) or remove the guard. Files likely affected:
- `services/dovecot_manager.py` — remove `if not settings.dovecot_enabled` guards (6 occurrences). Replace with `if not settings.dovecot_api_url` for the case where doveadm URL is empty.
- `routers/ui_admin.py` — `dovecot_enabled` in settings page context. Change to always `True`.
- `templates/settings.html` — `{% if dovecot_enabled %}` → always show.

- [ ] **Step 5: Run all tests**

Run: `uv run pytest tests/ -n auto -v --tb=short`
Expected: all pass (may need to fix tests that mock `dovecot_enabled`)

- [ ] **Step 6: Commit**

```bash
git add src/mailfallback/config.py tests/test_config.py
# Also add any files changed in step 4
git commit -m "refactor: update settings for centralized config generation"
```

---

### Task 2: Create config_generator.py with Dovecot Templates

**Files:**
- Create: `src/mailfallback/services/config_generator.py`
- Create: `tests/test_config_generator.py`

- [ ] **Step 1: Write tests for Dovecot config generation**

Create `tests/test_config_generator.py`:

```python
import os
import tempfile

from mailfallback.services.config_generator import (
    generate_all_configs,
    generate_dovecot_config,
    generate_webmail_config,
)


class FakeSettings:
    confs_path = ""
    db_host = "db"
    db_port = 5432
    db_name = "mailfallback"
    db_user = "mailfallback"
    db_password = "mailfallback"  # pragma: allowlist secret
    dovecot_api_key = "test-api-key"  # pragma: allowlist secret
    dovecot_api_url = "http://dovecot:8080"
    dovecot_imap_host = "dovecot"
    dovecot_imap_port = 31143
    tika_enabled = False
    tika_url = "http://tika:9998"
    webmail_enabled = False
    webmail_url = ""
    oidc_enabled = False
    oidc_client_id = ""
    oidc_client_secret = ""
    oidc_discovery_url = ""


def test_generate_dovecot_creates_all_files():
    with tempfile.TemporaryDirectory() as tmp:
        s = FakeSettings()
        s.confs_path = tmp
        generate_dovecot_config(s)
        dovecot_dir = os.path.join(tmp, "dovecot")
        expected_files = [
            "mfb-auth.conf",
            "mfb-mail.conf",
            "mfb-acl.conf",
            "mfb-fts.conf",
            "mfb-service.conf",
            "mfb-ssl.conf",
            "mfb-stats.conf",
            "mfb-lua-userdb.lua",
            "dovecot-acl",
        ]
        for f in expected_files:
            assert os.path.exists(os.path.join(dovecot_dir, f)), f"Missing: {f}"


def test_dovecot_auth_contains_db_credentials():
    with tempfile.TemporaryDirectory() as tmp:
        s = FakeSettings()
        s.confs_path = tmp
        s.db_host = "mydbhost"
        s.db_user = "myuser"
        s.db_password = "mypass"  # pragma: allowlist secret
        s.db_name = "mydb"
        generate_dovecot_config(s)
        auth = open(os.path.join(tmp, "dovecot", "mfb-auth.conf")).read()
        assert "mydbhost" in auth
        assert "myuser" in auth
        assert "mypass" in auth
        assert "mydb" in auth


def test_dovecot_auth_contains_api_key():
    with tempfile.TemporaryDirectory() as tmp:
        s = FakeSettings()
        s.confs_path = tmp
        s.dovecot_api_key = "secret-key-123"  # pragma: allowlist secret
        generate_dovecot_config(s)
        auth = open(os.path.join(tmp, "dovecot", "mfb-auth.conf")).read()
        assert "secret-key-123" in auth


def test_dovecot_lua_contains_mfb_url():
    with tempfile.TemporaryDirectory() as tmp:
        s = FakeSettings()
        s.confs_path = tmp
        generate_dovecot_config(s)
        lua = open(os.path.join(tmp, "dovecot", "mfb-lua-userdb.lua")).read()
        assert "http://mailfallback:8000" in lua
        assert "test-api-key" in lua


def test_dovecot_fts_without_tika():
    with tempfile.TemporaryDirectory() as tmp:
        s = FakeSettings()
        s.confs_path = tmp
        s.tika_enabled = False
        generate_dovecot_config(s)
        fts = open(os.path.join(tmp, "dovecot", "mfb-fts.conf")).read()
        assert "fts_flatcurve" in fts
        assert "fts_tika" not in fts


def test_dovecot_fts_with_tika():
    with tempfile.TemporaryDirectory() as tmp:
        s = FakeSettings()
        s.confs_path = tmp
        s.tika_enabled = True
        s.tika_url = "http://tika:9998"
        generate_dovecot_config(s)
        fts = open(os.path.join(tmp, "dovecot", "mfb-fts.conf")).read()
        assert "fts_tika" in fts
        assert "http://tika:9998" in fts


def test_dovecot_acl_content():
    with tempfile.TemporaryDirectory() as tmp:
        s = FakeSettings()
        s.confs_path = tmp
        generate_dovecot_config(s)
        acl = open(os.path.join(tmp, "dovecot", "dovecot-acl")).read()
        assert "* owner lrs" in acl


def test_dovecot_acl_path_in_config():
    with tempfile.TemporaryDirectory() as tmp:
        s = FakeSettings()
        s.confs_path = tmp
        generate_dovecot_config(s)
        acl_conf = open(os.path.join(tmp, "dovecot", "mfb-acl.conf")).read()
        assert "/etc/dovecot/conf.d/dovecot-acl" in acl_conf
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_config_generator.py -v`
Expected: FAIL — module doesn't exist

- [ ] **Step 3: Implement config_generator.py**

Create `src/mailfallback/services/config_generator.py`:

```python
import logging
import os

logger = logging.getLogger(__name__)


def _write(path: str, content: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(content)
    logger.info("Generated %s", path)


def _dovecot_path(settings, filename: str) -> str:
    return os.path.join(settings.confs_path, "dovecot", filename)


def _webmail_path(settings, filename: str) -> str:
    return os.path.join(settings.confs_path, "webmail", filename)


# ---------------------------------------------------------------------------
# Dovecot configs
# ---------------------------------------------------------------------------


def _auth_conf(settings) -> str:
    return f"""\
auth_mechanisms = plain login oauthbearer xoauth2

sql_driver = pgsql

pgsql {settings.db_host} {{
  parameters {{
    port = {settings.db_port}
    dbname = {settings.db_name}
    user = {settings.db_user}
    password = {settings.db_password} # pragma: allowlist secret
  }}
}}

passdb sql {{
  query = SELECT username, password_hash AS password \\
    FROM users \\
    WHERE username = '%{{user}}' AND enabled = true AND migrating = false
}}

userdb lua {{
  lua_file = /etc/dovecot/conf.d/mfb-lua-userdb.lua
}}

passdb_default_password_scheme = BLF-CRYPT
doveadm_password = {settings.dovecot_api_key} # pragma: allowlist secret
"""


def _mail_conf() -> str:
    return """\
mail_path =
mail_home =
mailbox_list_layout = fs
"""


def _acl_conf() -> str:
    return """\
mail_plugins = acl

protocol imap {
  mail_plugins = acl imap_acl
}

acl_driver = vfile
acl_globals_only = yes
acl_global_path = /etc/dovecot/conf.d/dovecot-acl
"""


def _fts_conf(settings) -> str:
    lines = """\
mail_plugins {
  fts = yes
  fts_flatcurve = yes
}

fts flatcurve {
  commit_limit = 500
  min_term_size = 2
  substring_search = no
  rotate_count = 5000
  rotate_time = 5000s
  optimize_limit = 10
}

fts_search_add_missing = yes
"""
    if settings.tika_enabled:
        lines += f"\nfts_tika = {settings.tika_url}/tika/\n"
    return lines


def _service_conf() -> str:
    return """\
service doveadm {
  inet_listener http {
    port = 8080
    ssl = no
  }
}
"""


def _ssl_conf() -> str:
    return """\
ssl = no
auth_allow_cleartext = yes
"""


def _stats_conf() -> str:
    return """\
service stats {
  inet_listener http {
    port = 9900
    ssl = no
  }
}
"""


def _lua_userdb(settings) -> str:
    return f"""\
local json = require "json"

local http_client = dovecot.http.client {{
    connect_timeout = "5s",
    request_timeout = "10s",
    request_max_attempts = 3,
}}

local API_BASE = "http://mailfallback:8000"
local API_KEY = "{settings.dovecot_api_key}"

function script_init()
    dovecot.i_info("mfb-lua-userdb: initialized, API base = " .. API_BASE)
    return 0
end

function script_deinit()
end

function auth_userdb_lookup(req)
    local user = req.user
    dovecot.i_info("mfb-lua-userdb: lookup for user=" .. user)

    local url = API_BASE .. "/api/internal/dovecot/userdb/" .. user
    local http_req = http_client:request {{
        url = url,
        method = "GET",
    }}
    http_req:add_header("X-API-Key", API_KEY)

    local resp = http_req:submit()
    local status = resp:status()

    if status == 404 then
        dovecot.i_info("mfb-lua-userdb: user not found: " .. user)
        return dovecot.auth.USERDB_RESULT_USER_UNKNOWN, "user not found"
    end

    if status ~= 200 then
        dovecot.i_error("mfb-lua-userdb: API returned status " .. tostring(status)
            .. " for user " .. user)
        return dovecot.auth.USERDB_RESULT_INTERNAL_FAILURE, "API error"
    end

    local body = resp:payload()
    local ok, data = pcall(json.decode, body)
    if not ok then
        dovecot.i_error("mfb-lua-userdb: JSON parse error: " .. tostring(data))
        return dovecot.auth.USERDB_RESULT_INTERNAL_FAILURE, "JSON parse error"
    end

    local fields = {{
        uid = tostring(data.uid),
        gid = tostring(data.gid),
        home = data.home,
    }}

    local ns_names = {{"mfb_root"}}
    fields["namespace/mfb_root/inbox"] = "yes"
    fields["namespace/mfb_root/prefix"] = ""
    fields["namespace/mfb_root/separator"] = "/"
    fields["namespace/mfb_root/mail_driver"] = "maildir"
    fields["namespace/mfb_root/mail_path"] = data.home .. "/root-inbox"

    if data.namespaces then
        for _, ns in ipairs(data.namespaces) do
            local name = ns.name
            table.insert(ns_names, name)
            fields["namespace/" .. name .. "/mail_driver"] = ns.mail_driver or "maildir"
            fields["namespace/" .. name .. "/mail_path"] = ns.mail_path
            fields["namespace/" .. name .. "/mail_inbox_path"] = ns.mail_path .. "/INBOX"
            fields["namespace/" .. name .. "/separator"] = "/"
            fields["namespace/" .. name .. "/prefix"] = ns.prefix or ""
            fields["namespace/" .. name .. "/inbox"] = "no"
        end
    end

    fields["namespace"] = table.concat(ns_names, " ")

    local ns_count = data.namespaces and #data.namespaces or 0
    dovecot.i_info("mfb-lua-userdb: returning " .. tostring(ns_count)
        .. " account(s) for user " .. user)
    for k, v in pairs(fields) do
        dovecot.i_debug("mfb-lua-userdb:   " .. k .. " = " .. v)
    end

    return dovecot.auth.USERDB_RESULT_OK, fields
end

function auth_userdb_iterate()
    return {{}}
end
"""


def _dovecot_acl() -> str:
    return "* owner lrs\n"


# ---------------------------------------------------------------------------
# Webmail config
# ---------------------------------------------------------------------------


def _webmail_custom_php(settings) -> str:
    lines = """\
<?php
$config['db_prefix'] = 'rc_';
$config['use_subscriptions'] = false;
$config['check_all_folders'] = true;
$config['disabled_actions'] = ['mail.compose'];
$config['search_mods'] = [
    '*' => ['subject' => 1, 'from' => 1, 'body' => 1],
];
$config['search_scope'] = 'base';
"""
    if settings.oidc_enabled and settings.oidc_client_id:
        lines += f"""
$config['oauth_provider'] = 'generic';
$config['oauth_provider_name'] = 'SSO';
$config['oauth_client_id'] = '{settings.oidc_client_id}';
$config['oauth_client_secret'] = '{settings.oidc_client_secret}';
$config['oauth_auth_uri'] = '{settings.oidc_discovery_url}';
$config['oauth_token_uri'] = '';
$config['oauth_identity_uri'] = '';
$config['oauth_scope'] = 'openid email profile offline_access';
$config['oauth_identity_fields'] = ['preferred_username'];
$config['oauth_login_redirect'] = false;
"""
    return lines


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def generate_dovecot_config(settings) -> None:
    _write(_dovecot_path(settings, "mfb-auth.conf"), _auth_conf(settings))
    _write(_dovecot_path(settings, "mfb-mail.conf"), _mail_conf())
    _write(_dovecot_path(settings, "mfb-acl.conf"), _acl_conf())
    _write(_dovecot_path(settings, "mfb-fts.conf"), _fts_conf(settings))
    _write(_dovecot_path(settings, "mfb-service.conf"), _service_conf())
    _write(_dovecot_path(settings, "mfb-ssl.conf"), _ssl_conf())
    _write(_dovecot_path(settings, "mfb-stats.conf"), _stats_conf())
    _write(_dovecot_path(settings, "mfb-lua-userdb.lua"), _lua_userdb(settings))
    _write(_dovecot_path(settings, "dovecot-acl"), _dovecot_acl())
    logger.info("Dovecot config generated (%s/dovecot/)", settings.confs_path)


def generate_webmail_config(settings) -> None:
    _write(_webmail_path(settings, "custom.php"), _webmail_custom_php(settings))
    logger.info("Webmail config generated (%s/webmail/)", settings.confs_path)


def generate_all_configs(settings) -> None:
    generate_dovecot_config(settings)
    if settings.webmail_enabled:
        generate_webmail_config(settings)
    logger.info("Config generation complete")
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_config_generator.py -v`
Expected: all 9 tests pass

- [ ] **Step 5: Commit**

```bash
git add src/mailfallback/services/config_generator.py tests/test_config_generator.py
git commit -m "feat: add config_generator service with Dovecot and Webmail templates"
```

---

### Task 3: Add Webmail Config Tests

**Files:**
- Modify: `tests/test_config_generator.py`

- [ ] **Step 1: Add webmail tests**

Append to `tests/test_config_generator.py`:

```python
def test_webmail_config_not_generated_when_disabled():
    with tempfile.TemporaryDirectory() as tmp:
        s = FakeSettings()
        s.confs_path = tmp
        s.webmail_enabled = False
        generate_all_configs(s)
        assert not os.path.exists(os.path.join(tmp, "webmail", "custom.php"))


def test_webmail_config_generated_when_enabled():
    with tempfile.TemporaryDirectory() as tmp:
        s = FakeSettings()
        s.confs_path = tmp
        s.webmail_enabled = True
        generate_all_configs(s)
        php = open(os.path.join(tmp, "webmail", "custom.php")).read()
        assert "db_prefix" in php
        assert "rc_" in php


def test_webmail_config_with_oauth():
    with tempfile.TemporaryDirectory() as tmp:
        s = FakeSettings()
        s.confs_path = tmp
        s.webmail_enabled = True
        s.oidc_enabled = True
        s.oidc_client_id = "my-client-id"
        s.oidc_client_secret = "my-secret"  # pragma: allowlist secret
        s.oidc_discovery_url = "https://auth.example.com"
        generate_webmail_config(s)
        php = open(os.path.join(tmp, "webmail", "custom.php")).read()
        assert "my-client-id" in php
        assert "oauth_provider" in php


def test_webmail_config_without_oauth():
    with tempfile.TemporaryDirectory() as tmp:
        s = FakeSettings()
        s.confs_path = tmp
        s.webmail_enabled = True
        s.oidc_enabled = False
        generate_webmail_config(s)
        php = open(os.path.join(tmp, "webmail", "custom.php")).read()
        assert "oauth" not in php
```

- [ ] **Step 2: Run tests**

Run: `uv run pytest tests/test_config_generator.py -v`
Expected: all 13 tests pass

- [ ] **Step 3: Commit**

```bash
git add tests/test_config_generator.py
git commit -m "test: add webmail config generation tests"
```

---

### Task 4: Integrate Config Generation into App Startup

**Files:**
- Modify: `src/mailfallback/app.py`

- [ ] **Step 1: Add config generation to lifespan**

In `src/mailfallback/app.py`, add import at top:

```python
from mailfallback.services.config_generator import generate_all_configs
```

In the `lifespan` function, add as the **first** action inside the try block (before `ensure_default_store`):

```python
    try:
        generate_all_configs(settings)
        default_store = ensure_default_store(db)
```

- [ ] **Step 2: Run all tests**

Run: `uv run pytest tests/ -n auto -v --tb=short`
Expected: all pass. The config generator writes to `/confs` which won't exist in test env, but `generate_all_configs` uses `settings.confs_path` which defaults to `/confs`. Tests use their own `FakeSettings` so no conflict.

Note: if any tests instantiate the app (like `client` fixture), they may fail because `/confs` doesn't exist. If so, set `confs_path` to a temp dir in the test app fixture or mock the call. Check and fix.

- [ ] **Step 3: Commit**

```bash
git add src/mailfallback/app.py
git commit -m "feat: generate component configs at app startup"
```

---

### Task 5: Update Docker Compose

**Files:**
- Modify: `docker-compose.yml`

- [ ] **Step 1: Update compose file**

Replace the entire `docker-compose.yml` with the new version from the spec. Key changes:
- Add `dovecot_confd` and `webmail_conf` named volumes
- MFB mounts `dovecot_confd:/confs/dovecot` and `webmail_conf:/confs/webmail`
- Dovecot mounts `dovecot_confd:/etc/dovecot/conf.d:ro`, remove bind mounts, remove most env vars, keep only `DOVEADM_PASSWORD`
- Roundcube gets `profiles: [webmail]`, remove `env_file`, remove OAuth env vars, mount `webmail_conf:/var/roundcube/config:ro`
- Add `tika` service with `profiles: [tika]`
- DB: remove `env_file`

The full compose is in the spec under "Resulting Compose". Add back healthchecks, ports, and depends_on from the current file.

- [ ] **Step 2: Commit**

```bash
git add docker-compose.yml
git commit -m "refactor: docker-compose uses generated config volumes"
```

---

### Task 6: Remove Static Config Files

**Files:**
- Delete: `docker/dovecot/conf.d/mfb-auth.conf`
- Delete: `docker/dovecot/conf.d/mfb-mail.conf`
- Delete: `docker/dovecot/conf.d/mfb-acl.conf`
- Delete: `docker/dovecot/conf.d/mfb-fts.conf`
- Delete: `docker/dovecot/conf.d/mfb-service.conf`
- Delete: `docker/dovecot/conf.d/mfb-ssl.conf`
- Delete: `docker/dovecot/conf.d/mfb-stats.conf`
- Delete: `docker/dovecot/conf.d/mfb-lua-userdb.lua`
- Delete: `docker/dovecot/dovecot-acl`
- Delete: `docker/roundcube/custom.php`

- [ ] **Step 1: Delete all static config files**

```bash
rm -rf docker/dovecot/conf.d/
rm -f docker/dovecot/dovecot-acl
rm -f docker/roundcube/custom.php
rmdir docker/dovecot/ 2>/dev/null || true
rmdir docker/roundcube/ 2>/dev/null || true
```

- [ ] **Step 2: Commit**

```bash
git add -A docker/
git commit -m "chore: remove static Dovecot and Roundcube config files

Config is now generated by MFB at startup into named volumes."
```

---

### Task 7: Fix dovecot_enabled References

**Files:**
- Modify: `src/mailfallback/services/dovecot_manager.py`
- Modify: `src/mailfallback/routers/ui_admin.py`
- Modify: `src/mailfallback/templates/settings.html`

- [ ] **Step 1: Update dovecot_manager.py**

In `src/mailfallback/services/dovecot_manager.py`, find all `if not settings.dovecot_enabled` guards and replace with `if not settings.dovecot_api_url`:

```python
# In reload_dovecot():
    if not settings.dovecot_api_url:
        return False

# In check_dovecot_health():
    if not settings.dovecot_api_url:
        return {"ok": False, "error": "Dovecot API not configured"}

# In fts_rescan():
    if not settings.dovecot_api_url:
        return {"ok": False, "error": "Dovecot API not configured"}

# In force_resync():
    if not settings.dovecot_api_url:
        return {"ok": False, "error": "Dovecot API not configured"}

# In get_mailbox_stats():
    if not settings.dovecot_api_url:
        return None
```

- [ ] **Step 2: Update ui_admin.py settings page**

In the `settings_page` route, change the context from `"dovecot_enabled": settings.dovecot_enabled` to `"dovecot_enabled": True`.

Or better: remove the variable entirely and update `settings.html` to always show the Dovecot section (remove the `{% if dovecot_enabled %}` guard).

- [ ] **Step 3: Update settings.html**

Remove `{% if dovecot_enabled %}` and its closing `{% endif %}` around the Dovecot Management section. It's always visible now.

- [ ] **Step 4: Run all tests**

Run: `uv run pytest tests/ -n auto -v --tb=short`
Expected: all pass. Fix any tests that reference `dovecot_enabled`.

- [ ] **Step 5: Commit**

```bash
git add src/mailfallback/services/dovecot_manager.py src/mailfallback/routers/ui_admin.py src/mailfallback/templates/settings.html
git commit -m "refactor: remove dovecot_enabled flag — Dovecot is always on"
```

---

### Task 8: Update Webmail Nav Link

**Files:**
- Modify: `src/mailfallback/routers/ui.py`
- Modify: `src/mailfallback/templates/base.html`

- [ ] **Step 1: Add webmail_enabled to template globals**

In `src/mailfallback/routers/ui.py`, after the existing `templates.env.globals["webmail_url"]` line, add:

```python
templates.env.globals["webmail_enabled"] = settings.webmail_enabled
```

- [ ] **Step 2: Update base.html nav link**

In `src/mailfallback/templates/base.html`, change the webmail link conditional:

From:
```html
{% if webmail_url %}
<a href="{{ webmail_url }}" ...>Webmail</a>
{% endif %}
```

To:
```html
{% if webmail_enabled and webmail_url %}
<a href="{{ webmail_url }}" ...>Webmail</a>
{% endif %}
```

- [ ] **Step 3: Run tests**

Run: `uv run pytest tests/ -n auto --tb=short`
Expected: all pass

- [ ] **Step 4: Commit**

```bash
git add src/mailfallback/routers/ui.py src/mailfallback/templates/base.html
git commit -m "feat: webmail nav link respects webmail_enabled flag"
```

---

### Task 9: Integration Test — Docker Compose Up

- [ ] **Step 1: Lint and test**

```bash
uv run ruff check src/ tests/ --fix && uv run ruff format src/ tests/
uv run pytest tests/ -n auto -v --tb=short
```

- [ ] **Step 2: Build and deploy**

```bash
docker compose down
docker compose up -d --build
```

- [ ] **Step 3: Verify Dovecot reads generated config**

```bash
# Wait for healthy
docker compose exec mailfallback curl -s http://localhost:8000/healthz

# Check Dovecot sees the generated config
docker compose exec dovecot doveconf -n | head -20

# Verify auth config has DB credentials (not env var references)
docker compose exec dovecot doveconf -n | grep -i "pgsql\|passdb"

# Verify FTS config
docker compose exec dovecot doveconf -n | grep -i "fts"

# Test IMAP login still works
docker compose logs dovecot --tail 5
```

- [ ] **Step 4: Verify webmail (if enabled)**

```bash
docker compose --profile webmail up -d roundcube
curl -s http://localhost:8001 | head -5
```

- [ ] **Step 5: Final commit with all fixes**

```bash
git add -A
git commit -m "feat: centralized config generation — MFB generates all component configs

Closes the centralized config generation spec. MFB generates Dovecot
and Roundcube configs at startup into named volumes. Static config
files removed from repo. Tika FTS support added as optional module."
```
