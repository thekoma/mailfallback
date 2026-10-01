"""Role-split home (airmail redesign, phase A).

A non-admin always gets the owner view at ``/``; an admin gets the ledger of
every mailbox at ``/`` and the owner view of their own at ``/mine``. ``/mine``
is scoped exactly like a non-admin's mailbox list — owned or shared through a
group — whatever the viewer's role."""

import re
import uuid
from datetime import UTC, datetime, timedelta, timezone

import pytest

from mailfallback.models import (
    Account,
    BackupPolicy,
    BackupStatus,
    Repository,
    SyncState,
    UserRole,
)
from mailfallback.services import group_service
from mailfallback.services.account_service import assign_owner
from mailfallback.services.user_service import create_user


def _login(client, username):
    client.post("/api/auth/logout")
    client.cookies.clear()
    client.post("/api/auth/login", json={"username": username, "password": "pass"})


def _box(db_session, default_store, name, **kw):
    kw.setdefault("initial_sync_completed_at", datetime.now(UTC) - timedelta(days=30))
    kw.setdefault("last_sync_at", datetime.now(UTC) - timedelta(minutes=5))
    account = Account(
        name=name,
        provider=kw.pop("provider", "other"),
        imap_host="imap.example.com",
        maildir_path=f"/data/mailboxes/{uuid.uuid4()}",
        store_id=default_store.id,
        sync_schedule="0 * * * *",
        **kw,
    )
    db_session.add(account)
    db_session.commit()
    return account


def _envelope_ids(html: str) -> set[str]:
    return set(
        re.findall(
            r'<article class="envelope envelope-mailbox[^"]*" data-account-id="([^"]+)"', html
        )
    )


def _is_owner_view(html: str) -> bool:
    return 'class="address-field"' in html and "sorting-office" not in html


def _is_admin_view(html: str) -> bool:
    return "sorting-office" in html and 'class="address-field"' not in html


def _world(db_session, default_store):
    """admin owns A; giulia owns G; marco owns M and shares S with giulia's
    family group; nobody shares anything with the admin."""
    admin = create_user(db_session, "boss", "pass", UserRole.admin, store_id=default_store.id)
    giulia = create_user(db_session, "giulia", "pass", UserRole.user, store_id=default_store.id)
    marco = create_user(db_session, "marco", "pass", UserRole.user, store_id=default_store.id)
    a = _box(db_session, default_store, "AdminBox")
    g = _box(db_session, default_store, "GiuliaBox")
    m = _box(db_session, default_store, "MarcoBox")
    shared = _box(db_session, default_store, "FamilyBox")
    assign_owner(db_session, a.id, admin.id)
    assign_owner(db_session, g.id, giulia.id)
    assign_owner(db_session, m.id, marco.id)
    assign_owner(db_session, shared.id, marco.id)
    family = group_service.create_group(db_session, "family", marco.id)
    group_service.add_member(db_session, family.id, giulia.id)
    group_service.set_group_accounts(db_session, family.id, [shared.id])
    return {"admin": admin, "a": a, "g": g, "m": m, "shared": shared}


def test_non_admin_home_is_the_owner_view_of_own_and_group_mailboxes(
    client, db_session, default_store
):
    w = _world(db_session, default_store)
    _login(client, "giulia")
    html = client.get("/").text
    assert _is_owner_view(html)
    assert _envelope_ids(html) == {w["g"].id, w["shared"].id}
    assert "MarcoBox" not in html
    assert "AdminBox" not in html
    # The address field is the primary control and lands in the search workspace.
    assert '<form class="address-field" action="/restore" method="get" role="search"' in html
    assert 'name="q"' in html
    assert "Search messages and attachments" in html


def test_non_admin_mine_matches_their_home(client, db_session, default_store):
    w = _world(db_session, default_store)
    _login(client, "giulia")
    mine = client.get("/mine")
    assert mine.status_code == 200
    assert _is_owner_view(mine.text)
    assert _envelope_ids(mine.text) == {w["g"].id, w["shared"].id}
    # No admin-only link for a plain user.
    assert 'href="/mine"' not in mine.text


def test_admin_home_is_the_ledger_of_every_mailbox(client, db_session, default_store):
    w = _world(db_session, default_store)
    _login(client, "boss")
    html = client.get("/").text
    assert _is_admin_view(html)
    for key in ("a", "g", "m", "shared"):
        assert f'data-account-id="{w[key].id}"' in html
    # The admin shell reaches the owner view in one click.
    assert '<a href="/mine"' in html


def test_admin_mine_shows_only_the_admins_own_mailboxes(client, db_session, default_store):
    w = _world(db_session, default_store)
    _login(client, "boss")
    html = client.get("/mine").text
    assert _is_owner_view(html)
    assert _envelope_ids(html) == {w["a"].id}
    assert "GiuliaBox" not in html
    assert "MarcoBox" not in html
    assert "FamilyBox" not in html


def test_admin_mine_includes_group_shared_mailboxes(client, db_session, default_store):
    w = _world(db_session, default_store)
    family = group_service.create_group(db_session, "household", w["admin"].id)
    group_service.add_member(db_session, family.id, w["admin"].id)
    group_service.set_group_accounts(db_session, family.id, [w["shared"].id])
    _login(client, "boss")
    assert _envelope_ids(client.get("/mine").text) == {w["a"].id, w["shared"].id}


def test_mine_requires_login(client):
    resp = client.get("/mine", follow_redirects=False)
    assert resp.status_code in (302, 303, 307)
    assert resp.headers["location"] == "/login"


def test_owner_home_first_run(client, db_session, default_store):
    create_user(db_session, "fresh", "pass", UserRole.user, store_id=default_store.id)
    _login(client, "fresh")
    html = client.get("/").text
    assert "No mailboxes yet." in html
    assert 'href="/accounts/new"' in html
    assert "Connect a mailbox" in html
    # Honest postmark: nothing was ever copied, so no date.
    assert "Not copied yet" in html
    assert "postmark-blank" in html


def test_owner_verdict_names_the_mailbox_that_needs_attention(client, db_session, default_store):
    user = create_user(db_session, "ann", "pass", UserRole.user, store_id=default_store.id)
    fine = _box(db_session, default_store, "Fine")
    broken = _box(
        db_session,
        default_store,
        "Broken",
        sync_state=SyncState.error,
        last_error="AUTHENTICATIONFAILED Invalid credentials",
    )
    assign_owner(db_session, fine.id, user.id)
    assign_owner(db_session, broken.id, user.id)
    _login(client, "ann")
    html = client.get("/").text
    lead = re.search(r'<article class="envelope envelope-lead".*?</article>', html, re.S).group(0)
    assert "Broken needs attention." in lead
    # The verdict stamp is the most urgent mailbox's — the error stamp.
    assert '<span class="stamp stamp-error">' in lead
    # The urgent envelope comes first.
    assert re.findall(r'data-account-id="([^"]+)"', html)[0] == broken.id


def test_owner_verdict_all_current_shows_latest_copy_time(client, db_session, default_store):
    from mailfallback.routers.ui import _postmark_date, _postmark_time

    user = create_user(db_session, "bea", "pass", UserRole.user, store_id=default_store.id)
    latest = (datetime.now(UTC) - timedelta(minutes=5)).replace(microsecond=0)
    for name, ts in (("One", latest), ("Two", latest - timedelta(hours=3))):
        acc = _box(db_session, default_store, name, last_sync_at=ts)
        assign_owner(db_session, acc.id, user.id)
    _login(client, "bea")
    html = client.get("/").text
    lead = re.search(r'<article class="envelope envelope-lead".*?</article>', html, re.S).group(0)
    assert "All 2 mailboxes are up to date." in lead
    assert '<span class="stamp stamp-ok">' in lead
    # The postmark carries the latest copy across mailboxes: "DD MON YYYY" and
    # "HH:MM", marked UTC until static/js/postmark.js localises it.
    assert re.fullmatch(r"\d\d [A-Z]{3} \d{4}", _postmark_date(latest))
    assert f'<span class="postmark-date">{_postmark_date(latest)}</span>' in lead
    assert (
        f'<span class="postmark-time">{_postmark_time(latest)}'
        '<span class="postmark-zone"> UTC</span></span>'
    ) in lead
    assert f'data-postmark="{latest.isoformat()}"' in lead


def test_theme_follows_the_os_until_the_user_chooses(client, db_session, default_store):
    create_user(db_session, "theo", "pass", UserRole.user, store_id=default_store.id)
    _login(client, "theo")
    assert "data-theme=" not in client.get("/").text.split(">", 2)[1]
    client.patch("/api/preferences", json={"theme": "dark"})
    assert 'data-theme="dark"' in re.search(r"<html[^>]*>", client.get("/").text).group(0)
    # And a fresh login restores the saved choice.
    _login(client, "theo")
    assert 'data-theme="dark"' in re.search(r"<html[^>]*>", client.get("/").text).group(0)


# --- Finish-review round: verdict, scoped chain counts, filters ------------

_CEST = timezone(timedelta(hours=2))


def _policy(db_session, account, repo, **kw):
    p = BackupPolicy(account_id=account.id, destination_id=repo.id, **kw)
    db_session.add(p)
    db_session.commit()
    return p


def _repo(db_session, name):
    r = Repository(name=name, backend_type="local", local_path=f"/tmp/{name}", restic_password="x")
    db_session.add(r)
    db_session.commit()
    return r


def _lead(html):
    return re.search(r'<article class="envelope envelope-lead".*?</article>', html, re.S).group(0)


def test_owner_verdict_counts_a_failed_offsite_backup(client, db_session, default_store):
    """A mailbox that syncs but whose last back-up failed must not leave the
    headline saying nothing needs attention while its card shows a red
    Back up now (#248 class)."""
    user = create_user(db_session, "cleo", "pass", UserRole.user, store_id=default_store.id)
    box = _box(db_session, default_store, "Synced")
    assign_owner(db_session, box.id, user.id)
    _policy(
        db_session,
        box,
        _repo(db_session, "r-cleo"),
        last_status=BackupStatus.failed,
        last_error="repository unreachable",
    )
    _login(client, "cleo")
    lead = _lead(client.get("/").text)
    assert "Synced needs attention." in lead
    assert "Nothing needs your attention." not in lead
    assert "up to date" not in lead
    assert "repository unreachable" in lead
    assert '<span class="stamp stamp-error">' in lead


def test_running_backup_is_not_needs_attention(client, db_session, default_store):
    admin = create_user(db_session, "runa", "pass", UserRole.admin, store_id=default_store.id)
    box = _box(db_session, default_store, "Backing")
    assign_owner(db_session, box.id, admin.id)
    _policy(
        db_session,
        box,
        _repo(db_session, "r-run"),
        last_status=BackupStatus.running,
        last_run_at=datetime.now(UTC) - timedelta(minutes=3),
    )
    _login(client, "runa")
    html = client.get("/").text
    assert 'id="needs-attention"' not in html
    assert "Off-site backup running" in html  # a quiet note on the row
    owner = client.get("/mine").text
    assert "needs attention" not in _lead(owner)


def test_owner_chain_counts_are_scoped_to_the_viewer(client, db_session, default_store):
    """A plain user's route strip counts only their own policies, snapshots
    and the repositories those policies use — never another user's."""
    ann = create_user(db_session, "ann2", "pass", UserRole.user, store_id=default_store.id)
    bob = create_user(db_session, "bob2", "pass", UserRole.user, store_id=default_store.id)
    mine = _box(db_session, default_store, "AnnBox")
    theirs = _box(db_session, default_store, "BobBox")
    assign_owner(db_session, mine.id, ann.id)
    assign_owner(db_session, theirs.id, bob.id)
    r1, r2, r3 = (_repo(db_session, n) for n in ("ra", "rb", "rc"))
    _policy(db_session, mine, r1, last_snapshot_count=3)
    _policy(db_session, theirs, r2, last_snapshot_count=40, last_status=BackupStatus.failed)
    _policy(db_session, theirs, r3, last_snapshot_count=500)
    _login(client, "ann2")
    html = client.get("/").text
    strip = re.search(r'<ol class="route".*?</ol>', html, re.S).group(0)
    assert "1 configured" in strip
    assert "1 policy" in strip
    assert "failing" not in strip
    assert "3 stored" in strip
    assert "540" not in strip and "543" not in strip


@pytest.mark.parametrize(
    "value, expected",
    [
        ("<b>a@b.c", "&lt;b&gt;a@<wbr>b.<wbr>c"),
        ("giulia.demo@gmail.com", "giulia.<wbr>demo@<wbr>gmail.<wbr>com"),
        ("", ""),
        (None, ""),
    ],
)
def test_email_wrap_escapes_then_marks_breaks(value, expected):
    from mailfallback.routers.ui import _email_wrap

    assert str(_email_wrap(value)) == expected


@pytest.mark.parametrize(
    "value, date, time, iso",
    [
        (None, "", "", ""),
        (datetime(2026, 9, 30, 21, 4), "30 SEP 2026", "21:04", "2026-09-30T21:04:00+00:00"),
        (
            datetime(2026, 10, 1, 1, 30, tzinfo=_CEST),
            "30 SEP 2026",
            "23:30",
            "2026-09-30T23:30:00+00:00",
        ),
        (
            datetime(2026, 1, 2, 3, 4, tzinfo=UTC),
            "02 JAN 2026",
            "03:04",
            "2026-01-02T03:04:00+00:00",
        ),
    ],
)
def test_postmark_filters(value, date, time, iso):
    """None renders nothing; a naive value is UTC (the column's zone); an
    aware non-UTC value is converted to UTC before printing."""
    from mailfallback.routers.ui import _iso, _postmark_date, _postmark_time

    assert _postmark_date(value) == date
    assert _postmark_time(value) == time
    assert _iso(value) == iso


def test_backup_only_failure_is_listed_once_on_the_ledger(client, db_session, default_store):
    """A mailbox that syncs fine but whose off-site backup failed belongs to
    Needs attention only — not again under the other mailboxes."""
    admin = create_user(db_session, "lena", "pass", UserRole.admin, store_id=default_store.id)
    box = _box(db_session, default_store, "Once only")
    assign_owner(db_session, box.id, admin.id)
    _policy(
        db_session,
        box,
        _repo(db_session, "r-once"),
        last_status=BackupStatus.failed,
        last_error="repository unreachable",
    )
    _login(client, "lena")
    html = client.get("/").text
    assert html.count(f'href="/accounts/{box.id}"') >= 1
    rows = re.findall(r'<tr[^>]*class="[^"]*ledger-row[^"]*"[^>]*>.*?</tr>', html, re.S)
    assert sum(f"/accounts/{box.id}" in r for r in rows) == 1


@pytest.mark.parametrize(
    ("page", "next_value", "expected"),
    [("/mine", "/mine", "/mine"), ("/", "/", "/"), ("/", "https://evil.example/", "/")],
)
def test_dismiss_explainer_returns_to_its_page(
    client, db_session, default_store, page, next_value, expected
):
    dina = create_user(db_session, "dina", "pass", UserRole.admin, store_id=default_store.id)
    assign_owner(db_session, _box(db_session, default_store, "Dina's").id, dina.id)
    _login(client, "dina")
    assert 'name="next"' in client.get(page).text
    resp = client.post(
        "/profile/dismiss-chain-explainer", data={"next": next_value}, follow_redirects=False
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == expected


def test_logged_in_pages_mark_html_so_theme_ignores_local_storage(
    client, db_session, default_store
):
    """theme-init.js reads localStorage only without data-auth: another
    user's key on a shared browser must not leak into this profile."""
    create_user(db_session, "theo", "pass", UserRole.user, store_id=default_store.id)
    assert "data-auth" not in client.get("/login").text.split(">", 2)[1]
    _login(client, "theo")
    html_tag = re.search(r"<html[^>]*>", client.get("/").text).group(0)
    assert "data-auth" in html_tag
