"""Task 10 UI: list folders removed from the Source on the account page.

No notification is involved here — see folder_reconcile module docstring for
why a quarantined folder is deliberately silent. This only covers rendering
what already happened on disk.
"""

from test_ui_sync_budget import _login, _mk_account

from mailfallback.routers.ui_accounts import _list_removed_folders
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


def test_removed_folders_render_newest_first(client, db_session, default_store, tmp_path):
    """Newest-first is an explicit contract item. Every test above creates
    exactly one quarantined folder, so none of them pin the ordering —
    deleting `reverse=True`, or the whole sort call, still leaves them green.

    This creates three folders with different dates, deliberately created out
    of chronological order on disk, plus a same-minute collision pair (a
    plain quarantine and its " (2)" twin) to make sure a duplicate sort key
    doesn't crash or break the surrounding order.
    """
    account = _mk_account(db_session, default_store, maildir_path=str(tmp_path))
    container = tmp_path / REMOVED_CONTAINER

    # Created out of chronological order on purpose: charlie, then alpha,
    # then the bravo pair — none of that matches the expected render order.
    (container / "charlie (2026-09-12 0800)" / "cur").mkdir(parents=True)
    (container / "alpha (2026-09-10 0900)" / "cur").mkdir(parents=True)
    (container / "bravo (2026-09-15 1200)" / "cur").mkdir(parents=True)
    (container / "bravo (2026-09-15 1200) (2)" / "cur").mkdir(parents=True)

    _login(client, db_session, default_store)
    resp = client.get(f"/accounts/{account.id}")
    assert resp.status_code == 200

    # bravo (2026-09-15, newest) must render before charlie (09-12) before
    # alpha (09-10). The collision pair both render as "bravo" with the same
    # date, so they can't be told apart on the page — that's expected, and
    # covered separately below.
    assert resp.text.count("bravo") == 2
    pos_bravo = resp.text.find("bravo")
    pos_charlie = resp.text.find("charlie")
    pos_alpha = resp.text.find("alpha")
    assert pos_bravo < pos_charlie < pos_alpha

    # The plain/collision pair share both a name and a date, so their
    # relative order isn't observable on the page — pin it at the source
    # instead, where `path` still distinguishes them. Which of the two sorts
    # first is arbitrary (they share a sort key); what matters is that it's
    # deterministic (from `path`, not from os.listdir's unspecified order)
    # so a later change to the tie-break is visible here.
    removed = _list_removed_folders(str(tmp_path))
    assert [r["path"] for r in removed] == [
        "bravo (2026-09-15 1200) (2)",
        "bravo (2026-09-15 1200)",
        "charlie (2026-09-12 0800)",
        "alpha (2026-09-10 0900)",
    ]
