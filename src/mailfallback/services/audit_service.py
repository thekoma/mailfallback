from sqlalchemy.orm import Session

from mailfallback.models import AuditLog, User

ACTION_LABELS = {
    "user.create": "Created user",
    "user.edit": "Edited user",
    "user.delete": "Deleted user",
    "user.toggle": "Toggled user status",
    "user.password_reset": "Reset password",  # pragma: allowlist secret
    "user.migrate": "Started user migration",
    "user.set_allowed_stores": "Updated allowed stores",
    "user.set_allowed_repositories": "Updated allowed repositories",
    "user.login": "Signed in",
    "user.password_change": "Changed own password",  # pragma: allowlist secret
    "user.role_changed": "Changed user role",
    "user.store_change": "Changed user mail store",
    "user.cancel_migration": "Cancelled user migration",
    "user.notification_channel_add": "Added notification channel",
    "user.notification_channel_update": "Updated notification channel",
    "user.notification_channel_delete": "Deleted notification channel",
    "store.create": "Created store",
    "store.edit": "Edited store",
    "store.delete": "Deleted store",
    "store.set_default": "Set default mail store",
    "store.drain": "Started mail store drain",
    "store.cleanup_orphans": "Cleaned up orphaned directories",
    "group.create": "Created group",
    "group.edit": "Edited group",
    "group.delete": "Deleted group",
    "group.add_member": "Added group member",
    "group.remove_member": "Removed group member",
    "group.add_account": "Added account to group",
    "group.remove_account": "Removed account from group",
    "account.create": "Created account",
    "account.edit": "Edited account",
    "account.delete": "Deleted account",
    "account.sync": "Triggered sync",
    "account.sync_all": "Triggered sync of all mailboxes",
    "account.sync_stop": "Stopped sync",
    "account.migrate": "Started account migration",
    "account.add_owner": "Added account owner",
    "account.remove_owner": "Removed account owner",
    "account.suspend": "Suspended account",
    "account.unsuspend": "Unsuspended account",
    "settings.update": "Updated system settings",
    "token.create": "Created access token",
    "token.revoke": "Revoked access token",
    "config.export": "Exported configuration",
    "config.import": "Imported configuration",
    "config.restore": "Restored configuration",
    "restore.start": "Started mail restore",
    "restore.complete": "Completed mail restore",
    "restore.failed": "Mail restore failed",
    "restore.cancel": "Cancelled mail restore",
    "restore.cancelled": "Mail restore cancelled",
    "restore.search_all": "Searched all users' mailboxes",
    "restore.preview": "Previewed message in another user's mailbox",
    "staging.add": "Staged messages for restore",
    "staging.empty": "Emptied staging area",
    "staging.push": "Pushed staged messages upstream",
    "attachment.download": "Downloaded attachment",
    "dovecot.health_check": "Dovecot health check",
    "dovecot.fts_reindex": "FTS reindex all users",
    "dovecot.force_resync": "Force resync all users",
    # Wave 2: legacy DB action strings ("backup_destination.*", "account.backup_*")
    # render here as user-facing labels so the rename can defer touching the DB.
    "backup_destination.create": "Repository created",
    "backup_destination.delete": "Repository deleted",
    "backup_destination.edit": "Repository edited",
    "backup_destination.attach": "Repository prefix attached",
    "backup_destination.attach_password": (  # pragma: allowlist secret
        "Repository prefix password updated"
    ),
    "backup_destination.detach": "Repository prefix detached",
    "backup_destination.config_backup": "Configuration snapshot stored",
    "backup_destination.backfill_tags": "Snapshot tags backfilled",
    "account.backup_configure": "Backup policy configured",
    "account.backup_now": "Manual back-up triggered",
    "account.backup_restore": "Recovered from snapshot",
    "account.recovery_delete": "Deleted recovery",
    "account.promote_recovered": "Recovered mailbox promoted to live",
}


def log_action(
    db: Session,
    *,
    user: User,
    action: str,
    resource_type: str,
    resource_id: str | None = None,
    resource_name: str | None = None,
    ip_address: str | None = None,
    details: dict | None = None,
) -> None:
    entry = AuditLog(
        user_id=user.id,
        username=user.username,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        resource_name=resource_name,
        ip_address=ip_address,
        details=details,
    )
    db.add(entry)
    db.commit()


def get_action_label(action: str) -> str:
    return ACTION_LABELS.get(action, action)
