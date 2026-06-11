from unittest.mock import AsyncMock, MagicMock, patch

from mailfallback.models import Account, AuthType, User, UserRole
from mailfallback.services.user_service import create_user


def test_restore_page_redirects_unauthenticated(client):
    resp = client.get("/restore", follow_redirects=False)
    assert resp.status_code == 307


def test_restore_page_renders(client, db_session, default_store):
    create_user(db_session, "uitest", "pass", UserRole.admin, store_id=default_store.id)
    client.post("/api/auth/login", json={"username": "uitest", "password": "pass"})
    resp = client.get("/restore")
    assert resp.status_code == 200
    assert "Restore" in resp.text


def test_restore_mailbox_select_lists_accounts_without_backup_policy(
    client, db_session, default_store
):
    """Every account select must list every owned account, not just the ones
    with a BackupPolicy — otherwise searches/restores silently target
    account_id="" and always come up empty."""
    acct = _setup_separator_test(db_session, default_store, client)
    resp = client.get("/restore")
    assert resp.status_code == 200
    # The account id must appear in the Mailbox + Destination sidebar selects
    # (folder/full presets). The search scope select renders its options from
    # the data island via Alpine x-for, so it adds no Jinja-rendered value.
    assert resp.text.count(f'value="{acct.id}"') == 2
    # ...and in the data island that feeds the scope select and maps account
    # ids to display names.
    assert f'"id": "{acct.id}"' in resp.text


def _extract_island(text, island_id):
    """Parse one JSON data island out of the rendered page."""
    import json
    import re

    m = re.search(
        rf'<script type="application/json" id="{island_id}">(.*?)</script>',
        text,
        re.S,
    )
    return json.loads(m.group(1)) if m else None


def _mk_owned_and_foreign_accounts(db_session, default_store, me):
    """One account owned by `me` + one owned by somebody else."""
    other = User(
        username="someoneelse",
        password_hash="x",
        store_id=default_store.id,
        role=UserRole.user,
    )
    db_session.add(other)
    mine = Account(
        name="mine",
        email_address="mine@example.com",
        imap_host="imap.example.com",
        maildir_path="/data/mailboxes/mine",
        store_id=default_store.id,
    )
    foreign = Account(
        name="foreign",
        email_address="foreign@example.com",
        imap_host="imap.example.com",
        maildir_path="/data/mailboxes/foreign",
        store_id=default_store.id,
    )
    db_session.add_all([mine, foreign])
    db_session.flush()
    mine.owners.append(me)
    foreign.owners.append(other)
    db_session.commit()
    return mine, foreign


def test_restore_page_admin_has_audited_toggle_and_both_islands(client, db_session, default_store):
    """Admins get the audited 'All users' mailboxes' switch plus a second data
    island with every account; the default island stays accessible-only."""
    admin = create_user(db_session, "wsadmin", "pass", UserRole.admin, store_id=default_store.id)
    mine, foreign = _mk_owned_and_foreign_accounts(db_session, default_store, admin)
    client.post("/api/auth/login", json={"username": "wsadmin", "password": "pass"})

    resp = client.get("/restore")

    assert resp.status_code == 200
    assert "ws-admin-toggle" in resp.text
    accessible = _extract_island(resp.text, "ws-accounts-data")
    everything = _extract_island(resp.text, "ws-accounts-all-data")
    assert accessible is not None and everything is not None
    accessible_ids = {a["id"] for a in accessible}
    all_ids = {a["id"] for a in everything}
    assert mine.id in accessible_ids
    assert foreign.id not in accessible_ids
    assert {mine.id, foreign.id} <= all_ids


def test_restore_page_non_admin_has_no_toggle_and_no_all_island(client, db_session, default_store):
    user = create_user(db_session, "wsuser", "pass", UserRole.user, store_id=default_store.id)
    mine, foreign = _mk_owned_and_foreign_accounts(db_session, default_store, user)
    client.post("/api/auth/login", json={"username": "wsuser", "password": "pass"})

    resp = client.get("/restore")

    assert resp.status_code == 200
    assert "ws-admin-toggle" not in resp.text
    assert "ws-accounts-all-data" not in resp.text
    accessible = _extract_island(resp.text, "ws-accounts-data")
    accessible_ids = {a["id"] for a in accessible}
    assert accessible_ids == {mine.id}
    # The foreign account must not leak anywhere in the page.
    assert foreign.id not in resp.text


def _setup_separator_test(db_session, default_store, client):
    """Create a user and target account, login, and return the account."""
    user = User(
        username="sepuser",
        password_hash="x",
        store_id=default_store.id,
        role=UserRole.admin,
    )
    db_session.add(user)
    db_session.flush()
    acct = Account(
        name="target",
        email_address="tgt@example.com",
        imap_host="imap.example.com",
        imap_port=993,
        maildir_path="/data/mailboxes/tgt",
        store_id=default_store.id,
        credentials="encrypted-creds",
    )
    db_session.add(acct)
    db_session.commit()
    db_session.refresh(user)
    db_session.refresh(acct)
    acct.owners.append(user)
    db_session.commit()
    with patch("mailfallback.routers.auth.authenticate_user", return_value=user):
        client.post("/api/auth/login", json={"username": "sepuser", "password": "x"})
    return acct


def test_separator_warning_no_target(client, db_session, default_store):
    create_user(db_session, "sepuser2", "pass", UserRole.admin, store_id=default_store.id)
    client.post("/api/auth/login", json={"username": "sepuser2", "password": "pass"})
    resp = client.get("/restore/partials/separator-warning?target_account_id=")
    assert resp.status_code == 200
    assert "hidden" in resp.text


def test_separator_warning_dot_separator(client, db_session, default_store):
    acct = _setup_separator_test(db_session, default_store, client)
    mock_conn = MagicMock()
    mock_conn.list.return_value = ("OK", [b'(\\NoSelect) "." ""'])
    mock_conn.logout.return_value = None

    with (
        patch(
            "mailfallback.routers.ui_restore.decrypt_credentials",
            return_value="plainpass",
        ),
        patch(
            "mailfallback.services.imap_check.connect_imap",
            return_value=mock_conn,
        ),
    ):
        resp = client.get(f"/restore/partials/separator-warning?target_account_id={acct.id}")
    assert resp.status_code == 200
    assert "Dot separator detected" in resp.text
    assert "warning-box" in resp.text
    assert "My.Archive" in resp.text
    assert "My_Archive" in resp.text


def test_separator_warning_slash_separator(client, db_session, default_store):
    acct = _setup_separator_test(db_session, default_store, client)
    mock_conn = MagicMock()
    mock_conn.list.return_value = ("OK", [b'(\\NoSelect) "/" ""'])
    mock_conn.logout.return_value = None

    with (
        patch(
            "mailfallback.routers.ui_restore.decrypt_credentials",
            return_value="plainpass",
        ),
        patch(
            "mailfallback.services.imap_check.connect_imap",
            return_value=mock_conn,
        ),
    ):
        resp = client.get(f"/restore/partials/separator-warning?target_account_id={acct.id}")
    assert resp.status_code == 200
    assert "hidden" in resp.text
    assert "warning-box" not in resp.text


def test_separator_warning_oauth2_destination_uses_xoauth2(client, db_session, default_store):
    """The destination probe must connect with XOAUTH2 for oauth2 accounts —
    Gmail/Microsoft reject the refreshed access token via plain LOGIN, which
    silently degraded this warning to the 'Could not connect' info box."""
    acct = _setup_separator_test(db_session, default_store, client)
    acct.auth_type = AuthType.oauth2
    db_session.commit()

    mock_conn = MagicMock()
    mock_conn.list.return_value = ("OK", [b'(\\NoSelect) "/" ""'])
    mock_conn.logout.return_value = None

    with (
        patch(
            "mailfallback.routers.ui_restore.decrypt_credentials",
            return_value='{"provider": "google", "refresh_token": "rt"}',
        ),
        patch(
            "mailfallback.services.oauth2.refresh_google_token",
            new=AsyncMock(return_value="ya29.sep-token"),
        ),
        patch(
            "mailfallback.services.imap_check.connect_imap",
            return_value=mock_conn,
        ) as mock_connect,
    ):
        resp = client.get(f"/restore/partials/separator-warning?target_account_id={acct.id}")

    assert resp.status_code == 200
    assert "Could not connect" not in resp.text
    mock_connect.assert_called_once()
    call = mock_connect.call_args
    assert call.kwargs.get("auth_method") == "xoauth2", call
    password = call.args[4] if len(call.args) > 4 else call.kwargs.get("password")
    assert password == "ya29.sep-token", call


def test_separator_warning_connection_error(client, db_session, default_store):
    acct = _setup_separator_test(db_session, default_store, client)

    with (
        patch(
            "mailfallback.routers.ui_restore.decrypt_credentials",
            return_value="plainpass",
        ),
        patch(
            "mailfallback.services.imap_check.connect_imap",
            side_effect=OSError("Connection refused"),
        ),
    ):
        resp = client.get(f"/restore/partials/separator-warning?target_account_id={acct.id}")
    assert resp.status_code == 200
    assert "Could not connect" in resp.text
    assert "info-box" in resp.text
