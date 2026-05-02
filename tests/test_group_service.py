# tests/test_group_service.py
import pytest

from mailfallback.models import Account, MailStore, UserRole
from mailfallback.services.group_service import (
    add_member,
    can_manage_group,
    create_group,
    delete_group,
    get_user_groups,
    remove_member,
    set_group_accounts,
    sync_sso_groups,
    update_group,
)
from mailfallback.services.user_service import create_user


@pytest.fixture
def store(db_session):
    s = MailStore(name="test", path="/tmp/grp-test")
    db_session.add(s)
    db_session.commit()
    db_session.refresh(s)
    return s


def test_create_group(db_session, store):
    owner = create_user(db_session, "owner", "pass", UserRole.user, store_id=store.id)
    group = create_group(db_session, "devs", owner.id)
    assert group.name == "devs"
    assert group.owner_id == owner.id
    assert group.sso_sync is False


def test_create_group_with_sso_sync(db_session, store):
    owner = create_user(db_session, "owner", "pass", UserRole.user, store_id=store.id)
    group = create_group(db_session, "sso-team", owner.id, sso_sync=True)
    assert group.sso_sync is True


def test_update_group(db_session, store):
    owner = create_user(db_session, "owner", "pass", UserRole.user, store_id=store.id)
    group = create_group(db_session, "old-name", owner.id)
    updated = update_group(db_session, group.id, name="new-name")
    assert updated.name == "new-name"


def test_delete_group(db_session, store):
    owner = create_user(db_session, "owner", "pass", UserRole.user, store_id=store.id)
    group = create_group(db_session, "doomed", owner.id)
    gid = group.id
    delete_group(db_session, gid)
    from mailfallback.models import Group

    assert db_session.query(Group).filter(Group.id == gid).first() is None


def test_add_and_remove_member(db_session, store):
    owner = create_user(db_session, "owner", "pass", UserRole.user, store_id=store.id)
    member = create_user(db_session, "member", "pass", UserRole.user, store_id=store.id)
    group = create_group(db_session, "team", owner.id)
    add_member(db_session, group.id, member.id)
    db_session.refresh(group)
    assert member in group.members
    remove_member(db_session, group.id, member.id)
    db_session.refresh(group)
    assert member not in group.members


def test_set_group_accounts(db_session, store):
    owner = create_user(db_session, "owner", "pass", UserRole.user, store_id=store.id)
    group = create_group(db_session, "shared", owner.id)
    a1 = Account(name="A1", imap_host="imap.ex.com", maildir_path="/tmp/a1", store_id=store.id)
    a2 = Account(name="A2", imap_host="imap.ex.com", maildir_path="/tmp/a2", store_id=store.id)
    db_session.add_all([a1, a2])
    db_session.commit()
    set_group_accounts(db_session, group.id, [a1.id, a2.id])
    db_session.refresh(group)
    assert len(group.accounts) == 2
    set_group_accounts(db_session, group.id, [a1.id])
    db_session.refresh(group)
    assert len(group.accounts) == 1


def test_get_user_groups(db_session, store):
    owner = create_user(db_session, "owner", "pass", UserRole.user, store_id=store.id)
    user = create_user(db_session, "user1", "pass", UserRole.user, store_id=store.id)
    g1 = create_group(db_session, "g1", owner.id)
    create_group(db_session, "g2", owner.id)
    add_member(db_session, g1.id, user.id)
    result = get_user_groups(db_session, user)
    assert len(result) == 1
    assert result[0].id == g1.id


def test_can_manage_group(db_session, store):
    admin = create_user(db_session, "admin", "pass", UserRole.admin, store_id=store.id)
    owner = create_user(db_session, "owner", "pass", UserRole.user, store_id=store.id)
    other = create_user(db_session, "other", "pass", UserRole.user, store_id=store.id)
    group = create_group(db_session, "managed", owner.id)
    assert can_manage_group(admin, group) is True
    assert can_manage_group(owner, group) is True
    assert can_manage_group(other, group) is False


def test_sync_sso_groups_adds_member(db_session, store):
    owner = create_user(db_session, "owner", "pass", UserRole.user, store_id=store.id)
    user = create_user(db_session, "ssouser", "pass", UserRole.user, store_id=store.id)
    group = create_group(db_session, "sso-team", owner.id, sso_sync=True)
    sync_sso_groups(db_session, user, ["sso-team"])
    db_session.refresh(group)
    assert user in group.members


def test_sync_sso_groups_removes_member(db_session, store):
    owner = create_user(db_session, "owner", "pass", UserRole.user, store_id=store.id)
    user = create_user(db_session, "ssouser", "pass", UserRole.user, store_id=store.id)
    group = create_group(db_session, "sso-team", owner.id, sso_sync=True)
    add_member(db_session, group.id, user.id)
    sync_sso_groups(db_session, user, [])
    db_session.refresh(group)
    assert user not in group.members


def test_sync_sso_groups_ignores_non_sso(db_session, store):
    owner = create_user(db_session, "owner", "pass", UserRole.user, store_id=store.id)
    user = create_user(db_session, "ssouser", "pass", UserRole.user, store_id=store.id)
    manual_group = create_group(db_session, "manual", owner.id, sso_sync=False)
    add_member(db_session, manual_group.id, user.id)
    sync_sso_groups(db_session, user, [])
    db_session.refresh(manual_group)
    assert user in manual_group.members


def test_sync_sso_groups_no_op_for_unmatched(db_session, store):
    owner = create_user(db_session, "owner", "pass", UserRole.user, store_id=store.id)
    user = create_user(db_session, "ssouser", "pass", UserRole.user, store_id=store.id)
    group = create_group(db_session, "sso-team", owner.id, sso_sync=True)
    sync_sso_groups(db_session, user, ["other-group"])
    db_session.refresh(group)
    assert user not in group.members
