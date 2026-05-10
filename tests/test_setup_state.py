# tests/test_setup_state.py
"""First-run setup checklist state."""

from mailfallback.models import (
    Account,
    BackendType,
    BackupPolicy,
    Repository,
    UserRole,
)
from mailfallback.services.setup_state import get_setup_state
from mailfallback.services.user_service import create_user


def _make_repository(db_session, name="rustfs"):
    repo = Repository(
        name=name,
        backend_type=BackendType.s3,
        restic_password="enc",  # pragma: allowlist secret
    )
    db_session.add(repo)
    db_session.commit()
    db_session.refresh(repo)
    return repo


def _make_account(db_session, default_store, name="Gmail"):
    acc = Account(
        name=name,
        email_address=f"{name.lower()}@example.com",
        imap_host="imap.example.com",
        imap_port=993,
        maildir_path=f"/data/mailboxes/{name.lower()}",
        store_id=default_store.id,
    )
    db_session.add(acc)
    db_session.commit()
    db_session.refresh(acc)
    return acc


def test_fresh_admin_has_all_pending(db_session, default_store):
    """Right after bootstrap: default password set, zero accounts, zero repos."""
    admin = create_user(
        db_session, "admin", "changeme1234!", UserRole.admin, store_id=default_store.id
    )
    state = get_setup_state(db_session, admin)
    assert state["is_admin"] is True
    assert state["default_password"] is True
    assert state["has_mailbox"] is False
    assert state["has_repository"] is False
    assert state["has_backup_policy"] is False
    assert state["complete"] is False


def test_admin_with_changed_password_marks_password_done(db_session, default_store):
    admin = create_user(
        db_session, "admin", "a-different-strong-pass", UserRole.admin, store_id=default_store.id
    )
    state = get_setup_state(db_session, admin)
    assert state["default_password"] is False
    # Still pending mailbox.
    assert state["complete"] is False


def test_admin_complete_with_password_changed_and_mailbox(db_session, default_store):
    admin = create_user(
        db_session, "admin", "another-strong-pass", UserRole.admin, store_id=default_store.id
    )
    _make_account(db_session, default_store)
    state = get_setup_state(db_session, admin)
    assert state["default_password"] is False
    assert state["has_mailbox"] is True
    assert state["complete"] is True
    # Recommended items still pending — that's OK, doesn't gate `complete`.
    assert state["has_repository"] is False
    assert state["has_backup_policy"] is False


def test_admin_with_full_setup_all_green(db_session, default_store):
    admin = create_user(
        db_session, "admin", "another-strong-pass", UserRole.admin, store_id=default_store.id
    )
    account = _make_account(db_session, default_store)
    repo = _make_repository(db_session)
    policy = BackupPolicy(account_id=account.id, destination_id=repo.id)
    db_session.add(policy)
    db_session.commit()
    state = get_setup_state(db_session, admin)
    assert state["default_password"] is False
    assert state["has_mailbox"] is True
    assert state["has_repository"] is True
    assert state["has_backup_policy"] is True
    assert state["complete"] is True


def test_non_admin_user_skips_checklist(db_session, default_store):
    user = create_user(
        db_session, "alice", "her-strong-pass", UserRole.user, store_id=default_store.id
    )
    state = get_setup_state(db_session, user)
    assert state["is_admin"] is False
    # Non-admin: complete=True so the dashboard hides the card.
    assert state["complete"] is True
