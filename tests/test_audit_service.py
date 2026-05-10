from mailfallback.models import AuditLog, UserRole
from mailfallback.services.audit_service import log_action
from mailfallback.services.user_service import create_user


def test_log_action_creates_entry(db_session, default_store):
    user = create_user(db_session, "admin", "pass", UserRole.admin, store_id=default_store.id)
    log_action(
        db_session,
        user=user,
        action="user.create",
        resource_type="user",
        resource_id="new-user-id",
        resource_name="newuser",
        ip_address="10.0.0.1",
    )
    entry = db_session.query(AuditLog).first()
    assert entry is not None
    assert entry.action == "user.create"
    assert entry.username == "admin"
    assert entry.resource_type == "user"
    assert entry.resource_id == "new-user-id"
    assert entry.resource_name == "newuser"
    assert entry.ip_address == "10.0.0.1"
    assert entry.user_id == user.id
    assert entry.timestamp is not None


def test_log_action_with_details(db_session, default_store):
    user = create_user(db_session, "admin", "pass", UserRole.admin, store_id=default_store.id)
    log_action(
        db_session,
        user=user,
        action="account.edit",
        resource_type="account",
        details={"changed": "schedule"},
    )
    entry = db_session.query(AuditLog).first()
    assert entry.details == {"changed": "schedule"}


def test_log_action_minimal(db_session, default_store):
    user = create_user(db_session, "admin", "pass", UserRole.admin, store_id=default_store.id)
    log_action(
        db_session,
        user=user,
        action="config.export",
        resource_type="config",
    )
    entry = db_session.query(AuditLog).first()
    assert entry.resource_id is None
    assert entry.resource_name is None
    assert entry.ip_address is None
    assert entry.details is None


def test_get_action_label_for_repository_actions():
    """Wave 2: legacy backup_* action strings render with the new Repository display labels."""
    from mailfallback.services.audit_service import get_action_label

    assert get_action_label("backup_destination.create") == "Repository created"
    assert get_action_label("backup_destination.delete") == "Repository deleted"
    assert get_action_label("backup_destination.edit") == "Repository edited"
    assert get_action_label("account.backup_configure") == "Backup policy configured"
    assert get_action_label("account.backup_now") == "Manual back-up triggered"
    assert get_action_label("account.backup_restore") == "Recovered from snapshot"
    assert get_action_label("account.promote_recovered") == "Recovered mailbox promoted to live"


def test_get_action_label_unknown_falls_back_to_action_string():
    from mailfallback.services.audit_service import get_action_label

    assert get_action_label("totally.unknown") == "totally.unknown"
