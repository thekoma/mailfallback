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
        "dovecot-acl",
        "mfb-fts.conf",
        "mfb-auth.conf",
        "mfb-lua-userdb.lua",
    }
    actual_names = {p.name for p in written}
    assert actual_names == expected_names
    assert len(written) == 9

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


def test_dovecot_fts_with_tika(tmp_path):
    settings = _make_settings(tmp_path, tika_enabled=True, tika_url="http://tika:9998")
    generate_dovecot_config(settings)

    fts = (tmp_path / "dovecot" / "mfb-fts.conf").read_text()
    assert "fts_flatcurve = yes" in fts
    assert "fts_decoder_driver = tika" in fts
    assert "fts_decoder_tika_url = http://tika:9998/tika/" in fts


def test_dovecot_acl_content(tmp_path):
    settings = _make_settings(tmp_path)
    generate_dovecot_config(settings)

    acl = (tmp_path / "dovecot" / "dovecot-acl").read_text()
    # Exact content, in order: default read-only first, then the writable
    # per-user Staging/ namespace (restore curation surface). Dovecot global
    # ACL lines are not merged -- the most specific mailbox pattern wins.
    assert acl == "* owner lrs\nStaging owner lrwstie\nStaging/* owner lrwstie\n"


def test_dovecot_acl_path_in_config(tmp_path):
    settings = _make_settings(tmp_path)
    generate_dovecot_config(settings)

    acl_conf = (tmp_path / "dovecot" / "mfb-acl.conf").read_text()
    assert "acl_global_path = /etc/dovecot/conf.d/dovecot-acl" in acl_conf


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
