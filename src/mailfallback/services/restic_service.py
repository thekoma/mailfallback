"""Restic subprocess wrapper for offsite backup operations."""

import json
import logging
import os
import subprocess

from mailfallback.config import settings
from mailfallback.models import BackupDestination
from mailfallback.security import decrypt_credentials

logger = logging.getLogger(__name__)

# Retention presets: (keep_daily, keep_weekly, keep_monthly)
_RETENTION_PRESETS: dict[str, tuple[int, int, int]] = {
    "light": (7, 4, 0),
    "standard": (30, 12, 6),
    "full": (90, 52, 24),
}


def build_repo_url(destination: BackupDestination, account_id: str) -> str:
    """Build the restic repository URL for a given destination and account."""
    if destination.backend_type.value == "s3":
        endpoint = decrypt_credentials(destination.s3_endpoint, settings.secret_key)
        bucket = decrypt_credentials(destination.s3_bucket, settings.secret_key)
        return f"s3:{endpoint}/{bucket}/{account_id}"
    return os.path.join(
        decrypt_credentials(destination.local_path, settings.secret_key),
        account_id,
    )


def build_env(destination: BackupDestination, account_id: str) -> dict[str, str]:
    """Build environment variables dict for restic subprocess calls."""
    env: dict[str, str] = {
        "RESTIC_REPOSITORY": build_repo_url(destination, account_id),
        "RESTIC_PASSWORD": decrypt_credentials(destination.restic_password, settings.secret_key),
    }
    if destination.backend_type.value == "s3":
        env["AWS_ACCESS_KEY_ID"] = decrypt_credentials(
            destination.s3_access_key, settings.secret_key
        )
        env["AWS_SECRET_ACCESS_KEY"] = decrypt_credentials(
            destination.s3_secret_key, settings.secret_key
        )
    return env


def get_retention_args(
    preset: str,
    keep_daily: int | None = None,
    keep_weekly: int | None = None,
    keep_monthly: int | None = None,
) -> list[str]:
    """Return restic forget arguments based on retention preset or custom values."""
    if preset == "custom":
        d = keep_daily or 0
        w = keep_weekly or 0
        m = keep_monthly or 0
    else:
        d, w, m = _RETENTION_PRESETS.get(preset, _RETENTION_PRESETS["standard"])

    args: list[str] = []
    if d > 0:
        args.extend(["--keep-daily", str(d)])
    if w > 0:
        args.extend(["--keep-weekly", str(w)])
    if m > 0:
        args.extend(["--keep-monthly", str(m)])
    return args


def _run_restic(args: list[str], env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    """Run a restic command with the given args and env."""
    cmd = ["restic", *args]
    full_env = {**os.environ, **env}
    logger.debug("Running: %s", " ".join(cmd))
    return subprocess.run(cmd, capture_output=True, text=True, env=full_env)


def init_repo(destination: BackupDestination, account_id: str) -> bool:
    """Initialize a restic repository. Returns True if init succeeded or repo exists."""
    env = build_env(destination, account_id)
    result = _run_restic(["init"], env)
    if result.returncode == 0:
        logger.info("Restic repo initialized for account %s", account_id)
        return True
    # restic returns 1 if repo already exists
    if "already initialized" in result.stderr or "already exists" in result.stderr:
        logger.debug("Restic repo already exists for account %s", account_id)
        return True
    logger.error("Failed to init restic repo for %s: %s", account_id, result.stderr)
    return False


def run_backup(destination: BackupDestination, account_id: str, maildir_path: str) -> dict:
    """Run a restic backup of the maildir path. Returns parsed JSON output."""
    env = build_env(destination, account_id)
    result = _run_restic(["backup", "--json", maildir_path], env)
    if result.returncode != 0:
        raise RuntimeError(f"Restic backup failed: {result.stderr}")

    # restic --json outputs one JSON object per line; the last one is the summary
    summary = {}
    for line in result.stdout.strip().splitlines():
        try:
            parsed = json.loads(line)
            if parsed.get("message_type") == "summary":
                summary = parsed
        except json.JSONDecodeError:
            continue
    return summary


def list_snapshots(destination: BackupDestination, account_id: str) -> list[dict]:
    """List all snapshots in the restic repository."""
    env = build_env(destination, account_id)
    result = _run_restic(["snapshots", "--json"], env)
    if result.returncode != 0:
        raise RuntimeError(f"Restic snapshots failed: {result.stderr}")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return []


def restore_snapshot(
    destination: BackupDestination,
    account_id: str,
    snapshot_id: str,
    target_path: str,
) -> dict:
    """Restore a specific snapshot to the target path."""
    env = build_env(destination, account_id)
    result = _run_restic(["restore", snapshot_id, "--target", target_path], env)
    if result.returncode != 0:
        raise RuntimeError(f"Restic restore failed: {result.stderr}")
    return {"snapshot_id": snapshot_id, "target_path": target_path, "output": result.stdout}


def apply_retention(
    destination: BackupDestination,
    account_id: str,
    preset: str,
    keep_daily: int | None = None,
    keep_weekly: int | None = None,
    keep_monthly: int | None = None,
) -> dict:
    """Apply retention policy using restic forget --prune."""
    env = build_env(destination, account_id)
    retention_args = get_retention_args(preset, keep_daily, keep_weekly, keep_monthly)
    if not retention_args:
        return {"pruned": False, "reason": "no retention args"}
    result = _run_restic(["forget", "--prune", *retention_args], env)
    if result.returncode != 0:
        raise RuntimeError(f"Restic forget failed: {result.stderr}")
    return {"pruned": True, "output": result.stdout}


def forget_all(destination: BackupDestination, account_id: str) -> bool:
    """Delete all snapshots from the repository."""
    env = build_env(destination, account_id)
    # First list snapshots to get their IDs
    snapshots = list_snapshots(destination, account_id)
    if not snapshots:
        return True

    snapshot_ids = [s["short_id"] for s in snapshots]
    result = _run_restic(["forget", "--prune", *snapshot_ids], env)
    if result.returncode != 0:
        logger.error("Failed to forget all snapshots: %s", result.stderr)
        return False
    return True
