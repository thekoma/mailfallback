"""Task 10 UI: list folders removed from the Source on the account page.

No notification is involved here — see folder_reconcile module docstring for
why a quarantined folder is deliberately silent. This only covers rendering
what already happened on disk.
"""

from test_ui_sync_budget import _login, _mk_account

from mailfallback.services.folder_reconcile import REMOVED_CONTAINER


def test_the_account_page_lists_folders_removed_from_the_source(
    client, db_session, default_store, tmp_path
):
    account = _mk_account(db_session, default_store, maildir_path=str(tmp_path))
    q = tmp_path / REMOVED_CONTAINER / "push-dixie (2026-09-15 1430)" / "cur"
    q.mkdir(parents=True)
    _login(client, db_session, default_store)

    resp = client.get(f"/accounts/{account.id}")

    assert resp.status_code == 200
    assert "push-dixie" in resp.text
    assert "2026-09-15 1430" in resp.text


def test_an_account_with_no_removed_folders_shows_no_section(
    client, db_session, default_store, tmp_path
):
    account = _mk_account(db_session, default_store, maildir_path=str(tmp_path))
    _login(client, db_session, default_store)

    resp = client.get(f"/accounts/{account.id}")

    assert resp.status_code == 200
    assert "Removed from Source" not in resp.text


def test_the_containers_own_cur_new_tmp_are_not_listed_as_folders(
    client, db_session, default_store, tmp_path
):
    account = _mk_account(db_session, default_store, maildir_path=str(tmp_path))
    container = tmp_path / REMOVED_CONTAINER
    for sub in ("cur", "new", "tmp"):
        (container / sub).mkdir(parents=True)
    _login(client, db_session, default_store)

    resp = client.get(f"/accounts/{account.id}")

    assert resp.status_code == 200
    assert "Removed from Source" not in resp.text


def test_a_missing_maildir_path_does_not_break_the_account_page(
    client, db_session, default_store, tmp_path
):
    missing = tmp_path / "does-not-exist"
    account = _mk_account(db_session, default_store, maildir_path=str(missing))
    _login(client, db_session, default_store)

    resp = client.get(f"/accounts/{account.id}")

    assert resp.status_code == 200
    assert "Removed from Source" not in resp.text
