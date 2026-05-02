# tests/test_groups_visibility.py
from mailfallback.models import Account, MailStore, UserRole
from mailfallback.services.account_service import (
    get_account,
    get_accounts_for_user,
    is_account_owner,
)
from mailfallback.services.group_service import (
    add_member,
    create_group,
    set_group_accounts,
)
from mailfallback.services.user_service import create_user


def _setup(db):
    store = MailStore(name="vis", path="/tmp/vis")
    db.add(store)
    db.commit()
    db.refresh(store)

    owner = create_user(db, "owner", "pass", UserRole.user, store_id=store.id)
    viewer = create_user(db, "viewer", "pass", UserRole.user, store_id=store.id)
    outsider = create_user(db, "outsider", "pass", UserRole.user, store_id=store.id)

    account = Account(
        name="Shared Mail",
        imap_host="imap.ex.com",
        maildir_path="/tmp/vis/shared-uuid",
        store_id=store.id,
    )
    db.add(account)
    db.commit()
    account.owners.append(owner)
    db.commit()

    group = create_group(db, "team", owner.id)
    add_member(db, group.id, viewer.id)
    set_group_accounts(db, group.id, [account.id])

    return store, owner, viewer, outsider, account, group


def test_owner_sees_account(db_session):
    _, owner, _, _, account, _ = _setup(db_session)
    accounts = get_accounts_for_user(db_session, owner)
    assert account in accounts


def test_group_member_sees_account(db_session):
    _, _, viewer, _, account, _ = _setup(db_session)
    accounts = get_accounts_for_user(db_session, viewer)
    assert account in accounts


def test_outsider_cannot_see_account(db_session):
    _, _, _, outsider, account, _ = _setup(db_session)
    accounts = get_accounts_for_user(db_session, outsider)
    assert account not in accounts


def test_get_account_via_group(db_session):
    _, _, viewer, _, account, _ = _setup(db_session)
    result = get_account(db_session, account.id, viewer)
    assert result is not None
    assert result.id == account.id


def test_get_account_denied_for_outsider(db_session):
    _, _, _, outsider, account, _ = _setup(db_session)
    result = get_account(db_session, account.id, outsider)
    assert result is None


def test_is_account_owner(db_session):
    _, owner, viewer, _, account, _ = _setup(db_session)
    assert is_account_owner(owner, account) is True
    assert is_account_owner(viewer, account) is False


def test_no_duplicates_when_owner_and_group_member(db_session):
    _, owner, _, _, account, group = _setup(db_session)
    add_member(db_session, group.id, owner.id)
    accounts = get_accounts_for_user(db_session, owner)
    ids = [a.id for a in accounts]
    assert ids.count(account.id) == 1
