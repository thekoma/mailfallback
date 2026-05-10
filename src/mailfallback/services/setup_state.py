# src/mailfallback/services/setup_state.py
"""First-run setup checklist state.

Computes which setup steps are still pending for the current admin user so
the dashboard can render a 'Get started' card. The card auto-hides once
all required items are complete; no explicit dismiss is needed because the
state is derived from real data.
"""

from sqlalchemy.orm import Session

from mailfallback.models import Account, BackupPolicy, Repository, User, UserRole
from mailfallback.security import verify_password

DEFAULT_ADMIN_PASSWORD = "changeme1234!"  # pragma: allowlist secret


def get_setup_state(db: Session, user: User) -> dict:
    """Return a dict describing first-run setup progress for the admin dashboard.

    Keys:
        default_password   bool — admin still using the bootstrap default password
        has_mailbox        bool — at least one Account exists
        has_repository     bool — at least one Repository (off-site target) exists
        has_backup_policy  bool — at least one BackupPolicy attaches a mailbox to a repo
        is_admin           bool — current user is an admin (only admins see the checklist)
        complete           bool — all required items done; card should hide

    "Required" items are: not using default password, at least one mailbox.
    "Recommended" items (Repository + backup policy) lower the urgency but
    don't gate `complete`.
    """
    is_admin = user.role == UserRole.admin
    if not is_admin:
        return {
            "is_admin": False,
            "default_password": False,
            "has_mailbox": False,
            "has_repository": False,
            "has_backup_policy": False,
            "complete": True,
        }

    # Default password: try to verify the bootstrap default against the user's hash.
    # `verify_password` is bcrypt; comparing against the documented bootstrap is honest.
    default_password = False
    try:
        default_password = verify_password(DEFAULT_ADMIN_PASSWORD, user.password_hash)
    except (ValueError, TypeError):
        # Malformed hash or any bcrypt error — assume not default.
        default_password = False

    has_mailbox = db.query(Account).count() > 0
    has_repository = db.query(Repository).count() > 0
    has_backup_policy = db.query(BackupPolicy).count() > 0

    # "Complete" means: required items done. Repository + policy are recommended,
    # not required, so we don't gate on them. The card includes them as soft items
    # that turn green when satisfied but don't keep the card visible by themselves.
    complete = not default_password and has_mailbox

    return {
        "is_admin": True,
        "default_password": default_password,
        "has_mailbox": has_mailbox,
        "has_repository": has_repository,
        "has_backup_policy": has_backup_policy,
        "complete": complete,
    }
