"""Tests for the centralized config generator."""

from pathlib import Path

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
    db_password = "mailfallback"
    dovecot_api_key = "test-api-key"
    dovecot_api_url = "http://dovecot:8080"
    dovecot_imap_host = "dovecot"
    dovecot_imap_port = 31143
    dovecot_nfs = False
    dovecot_tls = False
    tika_enabled = False
    tika_url = "http://tika:9998"
    webmail_enabled = False
    webmail_url = ""
    oidc_enabled = False
    oidc_client_id = ""
    oidc_client_secret = ""
    oidc_discovery_url = ""
    webmail_oauth_client_id = ""
    webmail_oauth_client_secret = ""
    webmail_oauth_auth_uri = ""
    webmail_oauth_token_uri = ""
    webmail_oauth_identity_uri = ""
    bootstrap_store_path = "/data/mailboxes"


def _make_settings(tmp_path: Path, **overrides) -> FakeSettings:
    s = FakeSettings()
    s.confs_path = str(tmp_path)
    for k, v in overrides.items():
        setattr(s, k, v)
    return s


# ---------------------------------------------------------------------------
# Dovecot tests
# ---------------------------------------------------------------------------


def test_generate_dovecot_creates_all_files(tmp_path):
    settings = _make_settings(tmp_path)
    written = generate_dovecot_config(settings)

    expected_names = {
        "mfb-ssl.conf",
        "mfb-service.conf",
        "mfb-stats.conf",
        "mfb-mail.conf",
        "mfb-acl.conf",
        "mfb-fts.conf",
        "mfb-auth.conf",
        "mfb-lua-userdb.lua",
    }
    actual_names = {p.name for p in written}
    assert actual_names == expected_names
    assert len(written) == 8

    # All files should exist on disk
    for p in written:
        assert p.exists()


def test_dovecot_auth_contains_db_credentials(tmp_path):
    settings = _make_settings(tmp_path, db_host="pghost", db_port=5433, db_name="mydb")
    generate_dovecot_config(settings)

    auth = (tmp_path / "dovecot" / "mfb-auth.conf").read_text()
    assert "pgsql pghost" in auth
    assert "port = 5433" in auth
    assert "dbname = mydb" in auth


def test_dovecot_auth_contains_api_key(tmp_path):
    settings = _make_settings(tmp_path, dovecot_api_key="super-secret-key")
    generate_dovecot_config(settings)

    auth = (tmp_path / "dovecot" / "mfb-auth.conf").read_text()
    assert "doveadm_password = super-secret-key" in auth


def test_dovecot_lua_contains_mfb_url(tmp_path):
    settings = _make_settings(tmp_path, dovecot_api_key="my-key")
    generate_dovecot_config(settings)

    lua = (tmp_path / "dovecot" / "mfb-lua-userdb.lua").read_text()
    assert 'API_BASE = "http://mailfallback:8000"' in lua
    assert 'API_KEY = "my-key"' in lua


def test_dovecot_fts_without_tika(tmp_path):
    settings = _make_settings(tmp_path, tika_enabled=False)
    generate_dovecot_config(settings)

    fts = (tmp_path / "dovecot" / "mfb-fts.conf").read_text()
    assert "fts_flatcurve = yes" in fts
    assert "fts_decoder_driver" not in fts
    # dovecot 2.4 requires an explicit language definition for FTS init.
    assert "language en {" in fts
    assert "language_default = yes" in fts
    assert "language_tokenizers = generic email-address" in fts


def test_dovecot_fts_with_tika(tmp_path):
    settings = _make_settings(tmp_path, tika_enabled=True, tika_url="http://tika:9998")
    generate_dovecot_config(settings)

    fts = (tmp_path / "dovecot" / "mfb-fts.conf").read_text()
    assert "fts_flatcurve = yes" in fts
    assert "fts_decoder_driver = tika" in fts
    assert "fts_decoder_tika_url = http://tika:9998/tika/" in fts


def test_dovecot_acl_uses_settings_blocks(tmp_path):
    import re

    settings = _make_settings(tmp_path)
    generate_dovecot_config(settings)

    acl_conf = (tmp_path / "dovecot" / "mfb-acl.conf").read_text()
    # dovecot 2.4.3+ removed the global acl file; ACLs are settings blocks.
    assert "acl_driver = vfile" in acl_conf
    assert "acl_globals_only = yes" in acl_conf
    assert "acl_global_path" not in acl_conf

    # Structural, not substring: the RIGHTS must be bound to the correct block,
    # so a rights inversion (global writable / Staging read-only) fails the test.
    # Global default block grants read-only (lrs), NOT lrwstie.
    global_rights = re.search(r"acl readonly \{.*?acl_rights = (\w+)", acl_conf, re.DOTALL)
    assert global_rights and global_rights.group(1) == "lrs", "global ACL must be lrs"
    # Staging mailbox blocks grant the writable set (lrwstie).
    staging_rights = re.search(r"mailbox Staging \{.*?acl_rights = (\w+)", acl_conf, re.DOTALL)
    assert staging_rights and staging_rights.group(1) == "lrwstie", "Staging must be lrwstie"
    staging_sub = re.search(r"mailbox Staging/\* \{.*?acl_rights = (\w+)", acl_conf, re.DOTALL)
    assert staging_sub and staging_sub.group(1) == "lrwstie", "Staging/* must be lrwstie"


def test_dovecot_acl_file_not_emitted(tmp_path):
    settings = _make_settings(tmp_path)
    generate_dovecot_config(settings)
    assert not (tmp_path / "dovecot" / "dovecot-acl").exists()


def test_dovecot_acl_removes_orphan_legacy_file(tmp_path):
    # An upgraded deployment may carry a stale dovecot-acl from an older MFB.
    dovecot_dir = tmp_path / "dovecot"
    dovecot_dir.mkdir(parents=True, exist_ok=True)
    (dovecot_dir / "dovecot-acl").write_text("* owner lrs\n")
    settings = _make_settings(tmp_path)
    generate_dovecot_config(settings)
    assert not (dovecot_dir / "dovecot-acl").exists()


# ---------------------------------------------------------------------------
# Webmail tests
# ---------------------------------------------------------------------------


def test_webmail_config_not_generated_when_disabled(tmp_path):
    settings = _make_settings(tmp_path, webmail_enabled=False)
    written = generate_all_configs(settings)

    webmail_dir = tmp_path / "webmail"
    assert not webmail_dir.exists() or not list(webmail_dir.iterdir())
    assert not any(p.name == "custom.php" for p in written)


def test_webmail_config_generated_when_enabled(tmp_path):
    settings = _make_settings(tmp_path, webmail_enabled=True)
    written = generate_all_configs(settings)

    custom_php = tmp_path / "webmail" / "custom.php"
    assert custom_php.exists()
    assert any(p.name == "custom.php" for p in written)

    content = custom_php.read_text()
    assert "$config['db_prefix'] = 'rc_'" in content
    assert "$config['use_subscriptions'] = false" in content
    assert "$config['disabled_actions'] = ['mail.compose']" in content
    # Curation contract: webmail Delete must work inside Staging/ (no Trash
    # is reachable from read-only account namespaces; blank = delete directly).
    assert "$config['trash_mbox'] = ''" in content


def test_webmail_config_with_oauth(tmp_path):
    settings = _make_settings(
        tmp_path,
        webmail_enabled=True,
        webmail_oauth_client_id="rc-client-id",
        webmail_oauth_client_secret="rc-secret",
        webmail_oauth_auth_uri="https://sso.example.com/authorize/",
        webmail_oauth_token_uri="https://sso.example.com/token/",
        webmail_oauth_identity_uri="https://sso.example.com/userinfo/",
    )
    written = generate_all_configs(settings)

    custom_php = tmp_path / "webmail" / "custom.php"
    assert custom_php.exists()
    assert any(p.name == "custom.php" for p in written)

    content = custom_php.read_text()
    assert "$config['oauth_provider'] = 'generic'" in content
    assert "$config['oauth_client_id'] = 'rc-client-id'" in content
    assert "$config['oauth_client_secret'] = 'rc-secret'" in content
    assert "https://sso.example.com/authorize/" in content
    assert "token" in content


def test_webmail_config_without_oauth(tmp_path):
    settings = _make_settings(
        tmp_path,
        webmail_enabled=True,
        oidc_enabled=False,
    )
    generate_webmail_config(settings)

    content = (tmp_path / "webmail" / "custom.php").read_text()
    assert "oauth" not in content.lower()
    assert "$config['db_prefix'] = 'rc_'" in content


def test_custom_php_restores_https_behind_proxy(tmp_path):
    settings = _make_settings(tmp_path, webmail_enabled=True)
    generate_webmail_config(settings)
    content = (tmp_path / "webmail" / "custom.php").read_text()
    assert content.startswith("<?php\n")
    proxy_block = (
        "if (($_SERVER['HTTP_X_FORWARDED_PROTO'] ?? '') === 'https') {\n"
        "    $_SERVER['HTTPS'] = 'on';\n"
        "}"
    )
    assert proxy_block in content
    assert content.index(proxy_block) < content.index("db_prefix")  # runs before any config


def test_mail_conf_default_has_no_nfs_settings(tmp_path):
    settings = _make_settings(tmp_path)
    generate_dovecot_config(settings)
    content = (tmp_path / "dovecot" / "mfb-mail.conf").read_text()
    assert "mmap_disable" not in content
    assert "mail_fsync" not in content


def test_mail_conf_nfs_mode_adds_safety_settings(tmp_path):
    settings = _make_settings(tmp_path, dovecot_nfs=True)
    generate_dovecot_config(settings)
    content = (tmp_path / "dovecot" / "mfb-mail.conf").read_text()
    assert "mmap_disable = yes" in content
    assert "mail_fsync = always" in content


def test_ssl_conf_default_is_plaintext(tmp_path):
    settings = _make_settings(tmp_path)
    generate_dovecot_config(settings)
    content = (tmp_path / "dovecot" / "mfb-ssl.conf").read_text()
    assert content == "ssl = no\nauth_allow_cleartext = yes\n"


def test_ssl_conf_tls_mode_enables_imaps(tmp_path):
    settings = _make_settings(tmp_path, dovecot_tls=True)
    generate_dovecot_config(settings)
    content = (tmp_path / "dovecot" / "mfb-ssl.conf").read_text()
    assert "ssl = yes" in content
    assert "ssl_server_cert_file = /etc/dovecot/ssl/tls.crt" in content
    assert "ssl_server_key_file = /etc/dovecot/ssl/tls.key" in content
    assert "auth_allow_cleartext = yes" in content  # in-cluster Roundcube uses plain 31143
    assert "ssl = no" not in content
