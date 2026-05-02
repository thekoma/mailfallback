# tests/test_store_drain.py
import os
import tempfile

from mailfallback.models import Account, UserRole
from mailfallback.services.store_service import (
    create_store,
    delete_orphaned_dirs,
    delete_store,
    get_allowed_stores,
    get_orphaned_dirs,
    get_selectable_stores,
    get_store_contents,
    set_allowed_stores,
)
from mailfallback.services.user_service import create_user


def test_get_store_contents_empty(db_session):
    store = create_store(db_session, "empty", "/data/empty")
    contents = get_store_contents(db_session, store.id)
    assert contents["accounts"] == []
    assert contents["users"] == []


def test_get_store_contents_with_accounts(db_session):
    store = create_store(db_session, "full", "/data/full")
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
    assert contents["accounts"][0]["id"] == account.id


def test_get_store_contents_with_users(db_session):
    store = create_store(db_session, "full", "/data/full")
    create_user(db_session, "alice", "pass", UserRole.user, store_id=store.id)
    contents = get_store_contents(db_session, store.id)
    assert len(contents["users"]) == 1
    assert contents["users"][0]["username"] == "alice"


def test_delete_empty_store(db_session):
    store = create_store(db_session, "deletable", "/data/deletable")
    ok, error = delete_store(db_session, store.id)
    assert ok is True
    assert error is None


def test_delete_store_blocked_by_accounts(db_session):
    store = create_store(db_session, "full", "/data/full")
    account = Account(
        name="Test",
        imap_host="imap.test.com",
        maildir_path="/data/full/test-uuid",
        store_id=store.id,
    )
    db_session.add(account)
    db_session.commit()
    ok, error = delete_store(db_session, store.id)
    assert ok is False
    assert "not empty" in error.lower()


def test_delete_store_blocked_by_users(db_session):
    store = create_store(db_session, "full", "/data/full")
    create_user(db_session, "alice", "pass", UserRole.user, store_id=store.id)
    ok, error = delete_store(db_session, store.id)
    assert ok is False
    assert "not empty" in error.lower()


# --- Orphan detection tests ---


def test_get_orphaned_dirs_finds_orphans(db_session):
    tmp = tempfile.mkdtemp(prefix="mfb_orphan_")
    store = create_store(db_session, "orphan-test", tmp)

    account = Account(
        name="Real",
        imap_host="imap.test.com",
        maildir_path=f"{tmp}/real-uuid",
        store_id=store.id,
    )
    db_session.add(account)
    db_session.commit()

    os.makedirs(f"{tmp}/{account.id}", exist_ok=True)
    os.makedirs(f"{tmp}/orphan-fake-uuid", exist_ok=True)
    os.makedirs(f"{tmp}/.dovecot-home", exist_ok=True)

    orphans = get_orphaned_dirs(db_session, store.id)
    orphan_names = [o["name"] for o in orphans]
    assert "orphan-fake-uuid" in orphan_names
    assert account.id not in orphan_names
    assert ".dovecot-home" not in orphan_names


def test_get_orphaned_dirs_empty_store(db_session):
    tmp = tempfile.mkdtemp(prefix="mfb_orphan_empty_")
    store = create_store(db_session, "clean", tmp)
    orphans = get_orphaned_dirs(db_session, store.id)
    assert orphans == []


def test_delete_orphaned_dirs(db_session):
    tmp = tempfile.mkdtemp(prefix="mfb_orphan_del_")
    store = create_store(db_session, "cleanup", tmp)

    os.makedirs(f"{tmp}/orphan-1", exist_ok=True)
    os.makedirs(f"{tmp}/orphan-2", exist_ok=True)

    count = delete_orphaned_dirs(db_session, store.id)
    assert count == 2
    assert not os.path.exists(f"{tmp}/orphan-1")
    assert not os.path.exists(f"{tmp}/orphan-2")


# --- Allowed stores tests ---


def test_get_allowed_stores(db_session):
    s1 = create_store(db_session, "s1", "/tmp/allowed1")
    s2 = create_store(db_session, "s2", "/tmp/allowed2")
    user = create_user(db_session, "alice", "pass", UserRole.user, store_id=s1.id)
    set_allowed_stores(db_session, user.id, [s1.id, s2.id])
    result = get_allowed_stores(db_session, user)
    assert len(result) == 2


def test_get_allowed_stores_excludes_disabled(db_session):
    s1 = create_store(db_session, "s1", "/tmp/allowdis1")
    s2 = create_store(db_session, "s2", "/tmp/allowdis2")
    s2.enabled = False
    db_session.commit()
    user = create_user(db_session, "bob", "pass", UserRole.user, store_id=s1.id)
    set_allowed_stores(db_session, user.id, [s1.id, s2.id])
    result = get_allowed_stores(db_session, user)
    assert len(result) == 1
    assert result[0].id == s1.id


def test_set_allowed_stores_replaces(db_session):
    s1 = create_store(db_session, "s1", "/tmp/allowrepl1")
    s2 = create_store(db_session, "s2", "/tmp/allowrepl2")
    user = create_user(db_session, "carol", "pass", UserRole.user, store_id=s1.id)
    set_allowed_stores(db_session, user.id, [s1.id, s2.id])
    set_allowed_stores(db_session, user.id, [s2.id])
    db_session.refresh(user)
    assert len(user.allowed_stores) == 1
    assert user.store_id == s2.id


def test_get_selectable_stores_none_if_single(db_session):
    s1 = create_store(db_session, "s1", "/tmp/sel1")
    user = create_user(db_session, "eve", "pass", UserRole.user, store_id=s1.id)
    set_allowed_stores(db_session, user.id, [s1.id])
    result = get_selectable_stores(db_session, user)
    assert result is None


def test_get_selectable_stores_list_if_multiple(db_session):
    s1 = create_store(db_session, "s1", "/tmp/selm1")
    s2 = create_store(db_session, "s2", "/tmp/selm2")
    user = create_user(db_session, "frank", "pass", UserRole.user, store_id=s1.id)
    set_allowed_stores(db_session, user.id, [s1.id, s2.id])
    result = get_selectable_stores(db_session, user)
    assert result is not None
    assert len(result) == 2
