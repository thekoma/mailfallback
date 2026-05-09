"""Generate Dovecot and Roundcube configuration files from MFB settings.

This module centralizes all component configuration so MFB is the single
source of truth.  Called at startup (app lifespan) to write config files
into ``settings.confs_path``, which is volume-mounted into sidecars.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Dovecot templates
# ---------------------------------------------------------------------------


def _dovecot_ssl_conf() -> str:
    return "ssl = no\nauth_allow_cleartext = yes\n"


def _dovecot_service_conf() -> str:
    return """\
service doveadm {
  inet_listener http {
    port = 8080
    ssl = no
  }
}
"""


def _dovecot_stats_conf() -> str:
    return """\
service stats {
  inet_listener http {
    port = 9900
    ssl = no
  }
}
"""


def _dovecot_mail_conf() -> str:
    return """\
# Override image defaults -- Lua userdb creates all namespaces dynamically.
mail_path =
mail_home =
mailbox_list_layout = fs
"""


def _dovecot_acl_conf() -> str:
    return """\
mail_plugins = acl

protocol imap {
  mail_plugins = acl imap_acl
}

acl_driver = vfile
acl_globals_only = yes
acl_global_path = /etc/dovecot/conf.d/dovecot-acl
"""


def _dovecot_acl_file() -> str:
    return "* owner lrs\n"


def _dovecot_fts_conf(settings: Any) -> str:
    tika_block = ""
    if settings.tika_enabled:
        tika_block = f"""
fts_decoder_driver = tika
fts_decoder_tika_url = {settings.tika_url}/tika/
"""

    return f"""\
mail_plugins {{
  fts = yes
  fts_flatcurve = yes
}}

fts flatcurve {{
  commit_limit = 500
  min_term_size = 2
  substring_search = no
  rotate_count = 5000
  rotate_time = 5000s
  optimize_limit = 10
}}

fts_search_add_missing = yes
{tika_block}"""


def _dovecot_auth_conf(settings: Any) -> str:
    oauth2_section = ""
    if settings.oidc_enabled and settings.oidc_userinfo_url:
        oauth2_section = f"""\

oauth2_introspection_url = {settings.oidc_userinfo_url}
oauth2_introspection_mode = auth
oauth2_username_attribute = preferred_username

passdb oauth2 {{
  mechanisms_filter = oauthbearer xoauth2
}}
"""

    auth_mechanisms = "plain login"
    if settings.oidc_enabled:
        auth_mechanisms = "plain login oauthbearer xoauth2"

    passdb_query = (
        "SELECT username, password_hash AS password \\\n"
        "    FROM users \\\n"
        "    WHERE username = '%{user}'"
        " AND enabled = true AND migrating = false"
    )

    return (
        f"auth_mechanisms = {auth_mechanisms}\n"
        f"\n"
        f"sql_driver = pgsql\n"
        f"\n"
        f"pgsql {settings.db_host} {{\n"
        f"  parameters {{\n"
        f"    port = {settings.db_port}\n"
        f"    dbname = {settings.db_name}\n"
        f"    user = {settings.db_user}\n"
        f"    password = {settings.db_password}\n"
        f"  }}\n"
        f"}}\n"
        f"{oauth2_section}"
        f"passdb sql {{\n"
        f"  query = {passdb_query}\n"
        f"}}\n"
        f"\n"
        f"userdb lua {{\n"
        f"  lua_file = /etc/dovecot/conf.d/mfb-lua-userdb.lua\n"
        f"}}\n"
        f"\n"
        f"passdb_default_password_scheme = BLF-CRYPT"
        f"  # pragma: allowlist secret\n"
        f"doveadm_password = {settings.dovecot_api_key}"
        f"  # pragma: allowlist secret\n"
    )


def _dovecot_lua_userdb(settings: Any) -> str:
    # API_KEY is hardcoded into the script because the Dovecot 2.4 official
    # image has no shell, so env-var expansion is unreliable.
    api_base = "http://mailfallback:8000"
    api_key = settings.dovecot_api_key

    return f"""\
-- mfb-lua-userdb.lua -- Dovecot 2.4 Lua userdb for MailFallBack
--
-- Fetches dynamic namespaces from the MFB internal API at login time.
-- Each user's accounts are returned as separate Dovecot namespaces,
-- enabling UUID-based maildir paths per account.
--
-- The FIRST account overrides the default inbox namespace's global
-- mail_path/mail_driver/mailbox_list_layout fields directly.
-- Additional accounts create new namespaces via "namespace +=".

local json = require "json"

local http_client = dovecot.http.client {{
    connect_timeout = "5s",
    request_timeout = "10s",
    request_max_attempts = 3,
}}

local API_BASE = "{api_base}"
local API_KEY = "{api_key}"


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

    -- Empty inbox namespace at the root -- accounts go under prefixed namespaces
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


# ---------------------------------------------------------------------------
# Webmail (Roundcube) template
# ---------------------------------------------------------------------------


def _webmail_custom_php(settings: Any) -> str:
    oauth_block = ""
    if settings.webmail_oauth_client_id:
        oauth_block = f"""
$config['oauth_provider'] = 'generic';
$config['oauth_provider_name'] = 'SSO';
$config['oauth_client_id'] = '{settings.webmail_oauth_client_id}';
$config['oauth_client_secret'] = '{settings.webmail_oauth_client_secret}';
$config['oauth_auth_uri'] = '{settings.webmail_oauth_auth_uri}';
$config['oauth_token_uri'] = '{settings.webmail_oauth_token_uri}';
$config['oauth_identity_uri'] = '{settings.webmail_oauth_identity_uri}';
$config['oauth_scope'] = 'openid email profile offline_access';
$config['oauth_identity_fields'] = ['preferred_username'];
$config['oauth_login_redirect'] = false;
"""

    return f"""\
<?php
$config['db_prefix'] = 'rc_';
$config['use_subscriptions'] = false;
$config['check_all_folders'] = true;
$config['disabled_actions'] = ['mail.compose'];
$config['search_mods'] = [
    '*' => ['subject' => 1, 'from' => 1, 'body' => 1],
];
$config['search_scope'] = 'base';
{oauth_block}"""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

_DOVECOT_FILES: list[tuple[str, Any]] = [
    ("mfb-ssl.conf", _dovecot_ssl_conf),
    ("mfb-service.conf", _dovecot_service_conf),
    ("mfb-stats.conf", _dovecot_stats_conf),
    ("mfb-mail.conf", _dovecot_mail_conf),
    ("mfb-acl.conf", _dovecot_acl_conf),
    ("dovecot-acl", _dovecot_acl_file),
    ("mfb-fts.conf", None),
    ("mfb-auth.conf", None),
    ("mfb-lua-userdb.lua", None),
]


def _check_fts_config_changed(settings: Any) -> bool:
    """Return True if FTS config changed (Tika toggled) since last boot."""
    marker = Path(settings.confs_path) / "dovecot" / ".fts-state"
    current = "tika" if settings.tika_enabled else "flatcurve"
    if marker.exists():
        previous = marker.read_text().strip()
        if previous == current:
            return False
        logger.info("FTS config changed: %s -> %s", previous, current)
    marker.write_text(current)
    return True


def _purge_fts_indexes(settings: Any) -> None:
    """Delete all FTS flatcurve indexes to force rebuild."""
    import shutil

    mailboxes_dirs = [settings.bootstrap_store_path]
    count = 0
    for base_dir in mailboxes_dirs:
        base = Path(base_dir)
        if not base.exists():
            continue
        for idx_dir in base.rglob("fts-flatcurve"):
            if idx_dir.is_dir():
                shutil.rmtree(idx_dir)
                count += 1
    if count:
        logger.info("Purged %d FTS indexes for rebuild with new config", count)


def generate_dovecot_config(settings: Any) -> list[Path]:
    """Write all Dovecot config files to ``{confs_path}/dovecot/``."""
    base = Path(settings.confs_path) / "dovecot"
    base.mkdir(parents=True, exist_ok=True)

    fts_changed = _check_fts_config_changed(settings)

    written: list[Path] = []

    for rel_path, factory in _DOVECOT_FILES:
        dest = base / rel_path
        if factory is not None:
            content = factory()
        elif rel_path == "mfb-fts.conf":
            content = _dovecot_fts_conf(settings)
        elif rel_path == "mfb-auth.conf":
            content = _dovecot_auth_conf(settings)
        elif rel_path == "mfb-lua-userdb.lua":
            content = _dovecot_lua_userdb(settings)
        else:
            continue  # pragma: no cover

        dest.write_text(content)
        written.append(dest)
        logger.info("Wrote %s", dest)

    if fts_changed:
        _purge_fts_indexes(settings)
        reindex_marker = base / ".fts-reindex-needed"
        reindex_marker.write_text("purged")

    return written


def generate_webmail_config(settings: Any) -> list[Path]:
    """Write Roundcube config to ``{confs_path}/webmail/``."""
    base = Path(settings.confs_path) / "webmail"
    base.mkdir(parents=True, exist_ok=True)

    dest = base / "custom.php"
    dest.write_text(_webmail_custom_php(settings))
    logger.info("Wrote %s", dest)
    return [dest]


def needs_fts_reindex(settings: Any) -> bool:
    """Check if FTS indexes were purged and need rebuilding."""
    marker = Path(settings.confs_path) / "dovecot" / ".fts-reindex-needed"
    return marker.exists()


def clear_fts_reindex_flag(settings: Any) -> None:
    marker = Path(settings.confs_path) / "dovecot" / ".fts-reindex-needed"
    marker.unlink(missing_ok=True)


def generate_all_configs(settings: Any) -> list[Path]:
    """Generate all sidecar configs.  Called from app lifespan."""
    written = generate_dovecot_config(settings)
    if settings.webmail_enabled:
        written += generate_webmail_config(settings)
    return written
