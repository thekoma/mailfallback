# tests/test_dovecot_home_cleanup.py
"""Dovecot home teardown and the orphaned-_restore_-home sweep.

Issue #240: deleting a user — temp restore user or real — removed the row and
left {store}/.dovecot-home/{username}/ on disk forever. 74 of 78 homes in
production were abandoned _restore_* directories.

The hazard the teardown introduces is collision: usernames are free-form
(create_user reserves only the _restore_ prefix, and SSO can mint them too)
while the directory name is the username with everything outside
[a-zA-Z0-9@._-] folded to "_". So "a b" and "a_b" are two distinct users
sharing one home, and removing either must not take the other's mail with it.
"""

import os
import tempfile

import pytest

from mailfallback.models import Account, User, UserRole, account_owners
from mailfallback.security import hash_password
from mailfallback.services import store_service
from mailfallback.services.dovecot_auth import (
    TEMP_USER_PREFIX,
    cleanup_temp_imap_users,
    create_temp_imap_user,
    delete_temp_imap_user,
    sweep_orphaned_restore_homes,
)
from mailfallback.services.user_service import delete_user


@pytest.fixture
def store(db_session):
    tmp = tempfile.mkdtemp(prefix="mfb_home_")
    return store_service.create_store(db_session, "homes", tmp)


def _home(store, username):
    return store_service.dovecot_home_dir(store.path, username)


def _make_home(store, username, marker="msg"):
    path = _home(store, username)
    os.makedirs(os.path.join(path, "root-inbox", "new"), exist_ok=True)
    with open(os.path.join(path, "root-inbox", "new", marker), "w") as fh:
        fh.write("x")
    return path


def _make_user(db, store, username, role=UserRole.user):
    user = User(
        username=username,
        password_hash=hash_password("testpassword1"),
        role=role,
        store_id=store.id,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


class TestHomePathDerivation:
    def test_derives_the_dovecot_home_under_the_store(self, store):
        assert _home(store, "alice") == f"{store.path}/.dovecot-home/alice"

    def test_folds_path_separators_so_a_username_cannot_escape_the_store(self, store):
        home = _home(store, "../../etc/passwd")
        root = f"{store.path}/.dovecot-home"

        # ".." survives as literal text (".._.._etc_passwd") — what matters is
        # that it is one inert path segment, not a traversal, so the resolved
        # path still lands directly inside the home root.
        assert os.path.dirname(home) == root
        assert os.path.normpath(home) == f"{root}/.._.._etc_passwd"


class TestRemoveDovecotHome:
    def test_removes_the_home_directory(self, db_session, store):
        user = _make_user(db_session, store, "alice")
        path = _make_home(store, "alice")

        assert store_service.remove_dovecot_home(db_session, user) is True
        assert not os.path.exists(path)

    def test_is_a_noop_when_the_home_was_never_created(self, db_session, store):
        user = _make_user(db_session, store, "ghost")

        assert store_service.remove_dovecot_home(db_session, user) is False

    def test_refuses_when_another_user_maps_onto_the_same_directory(self, db_session, store):
        # "a b" and "a_b" are distinct users — username is unique — but both
        # sanitize to "a_b", so they share one home on disk. Deleting either
        # must not destroy the survivor's mail.
        spaced = _make_user(db_session, store, "a b")
        _make_user(db_session, store, "a_b")
        path = _make_home(store, "a b")
        assert _home(store, "a b") == _home(store, "a_b")

        assert store_service.remove_dovecot_home(db_session, spaced) is False
        assert os.path.exists(path)

    def test_removes_once_the_colliding_user_is_gone(self, db_session, store):
        spaced = _make_user(db_session, store, "a b")
        other = _make_user(db_session, store, "a_b")
        path = _make_home(store, "a b")
        db_session.delete(other)
        db_session.commit()

        assert store_service.remove_dovecot_home(db_session, spaced) is True
        assert not os.path.exists(path)

    def test_ignores_a_same_named_user_on_a_different_store(self, db_session, store):
        other_store = store_service.create_store(
            db_session, "elsewhere", tempfile.mkdtemp(prefix="mfb_home2_")
        )
        user = _make_user(db_session, store, "a b")
        twin = User(
            username="a_b",
            password_hash=hash_password("testpassword1"),
            role=UserRole.user,
            store_id=other_store.id,
        )
        db_session.add(twin)
        db_session.commit()
        path = _make_home(store, "a b")

        # Same derived name, different store: no collision on disk.
        assert store_service.remove_dovecot_home(db_session, user) is True
        assert not os.path.exists(path)


class TestTempUserTeardown:
    def test_delete_temp_imap_user_removes_the_home(self, db_session, store):
        store_service.set_default_store(db_session, store.id)
        username, _ = create_temp_imap_user(db_session, [])
        path = _make_home(store, username)

        delete_temp_imap_user(db_session, username)

        assert not os.path.exists(path)
        assert db_session.query(User).filter(User.username == username).first() is None

    def test_cleanup_temp_imap_users_removes_the_homes(self, db_session, store):
        import datetime

        store_service.set_default_store(db_session, store.id)
        username, _ = create_temp_imap_user(db_session, [])
        path = _make_home(store, username)
        expired = db_session.query(User).filter(User.username == username).one()
        expired.created_at = datetime.datetime.now(datetime.UTC) - datetime.timedelta(hours=2)
        db_session.commit()

        assert cleanup_temp_imap_users(db_session) == 1
        assert not os.path.exists(path)


class TestRealUserDeletion:
    def test_delete_user_removes_the_home(self, db_session, store):
        user = _make_user(db_session, store, "alice")
        path = _make_home(store, "alice")

        assert delete_user(db_session, user.id) is True
        assert not os.path.exists(path)

    def test_delete_user_leaves_a_shared_account_and_its_maildir_intact(self, db_session, store):
        # The account maildir lives at {store}/{account-uuid}, never inside a
        # user home, and account_owners is a plain association table — losing
        # one owner must cost the co-owner nothing.
        alice = _make_user(db_session, store, "alice")
        bob = _make_user(db_session, store, "bob")
        account = Account(
            name="Shared",
            email_address="shared@example.com",
            imap_host="imap.example.com",
            maildir_path=store_service.derive_maildir_path(store.path, "shared-uuid"),
            store_id=store.id,
        )
        db_session.add(account)
        db_session.commit()
        account.owners.extend([alice, bob])
        db_session.commit()
        os.makedirs(os.path.join(account.maildir_path, "new"), exist_ok=True)
        _make_home(store, "alice")
        bob_home = _make_home(store, "bob")

        delete_user(db_session, alice.id)

        assert db_session.query(Account).filter(Account.id == account.id).first() is not None
        assert os.path.isdir(account.maildir_path)
        assert os.path.exists(bob_home)
        remaining = db_session.execute(
            account_owners.select().where(account_owners.c.account_id == account.id)
        ).fetchall()
        assert [row.user_id for row in remaining] == [bob.id]


class TestTempPrefixIsNotALikePattern:
    """TEMP_USER_PREFIX is "_restore_", and "_" is a single-character wildcard
    in SQL LIKE. Unescaped, the temp-user query also selects real accounts such
    as "arestore_x" — which create_user accepts, because its own guard is a
    literal Python startswith. The row deletion was already wrong; adding home
    teardown to the same sweep would turn it into mail loss.
    """

    def test_cleanup_spares_a_real_user_whose_name_matches_the_wildcard(self, db_session, store):
        import datetime

        victim = _make_user(db_session, store, "arestore_victim")
        victim.created_at = datetime.datetime.now(datetime.UTC) - datetime.timedelta(hours=2)
        db_session.commit()
        path = _make_home(store, "arestore_victim")

        assert cleanup_temp_imap_users(db_session) == 0
        assert db_session.query(User).filter(User.id == victim.id).first() is not None
        assert os.path.exists(path)

    def test_sweep_spares_a_home_that_only_matches_the_wildcard(self, db_session, store):
        path = _make_home(store, "arestore_victim")
        _make_user(db_session, store, "arestore_victim")

        assert sweep_orphaned_restore_homes(db_session) == 0
        assert os.path.exists(path)


class TestOrphanedRestoreHomeSweep:
    def test_removes_restore_homes_with_no_user_row(self, db_session, store):
        path = _make_home(store, f"{TEMP_USER_PREFIX}deadbeef")

        assert sweep_orphaned_restore_homes(db_session) == 1
        assert not os.path.exists(path)

    def test_keeps_a_restore_home_whose_user_still_exists(self, db_session, store):
        store_service.set_default_store(db_session, store.id)
        username, _ = create_temp_imap_user(db_session, [])
        path = _make_home(store, username)

        assert sweep_orphaned_restore_homes(db_session) == 0
        assert os.path.exists(path)

    def test_never_touches_a_home_outside_the_restore_prefix(self, db_session, store):
        # A real user's home with no row — a database restore mid-flight, a
        # half-finished migration — must survive. Only directories MFB itself
        # mints under the _restore_ prefix are ever swept.
        legacy = _make_home(store, "__tmp_claude_admin")
        real = _make_home(store, "alice")

        assert sweep_orphaned_restore_homes(db_session) == 0
        assert os.path.exists(legacy)
        assert os.path.exists(real)

    def test_sweeps_every_store_not_just_the_default(self, db_session, store):
        second = store_service.create_store(
            db_session, "second", tempfile.mkdtemp(prefix="mfb_home3_")
        )
        a = _make_home(store, f"{TEMP_USER_PREFIX}aaaaaaaa")
        b = _make_home(second, f"{TEMP_USER_PREFIX}bbbbbbbb")

        assert sweep_orphaned_restore_homes(db_session) == 2
        assert not os.path.exists(a)
        assert not os.path.exists(b)
