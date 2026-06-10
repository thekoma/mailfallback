"""Restic subprocess wrapper for offsite backup operations."""

import json
import logging
import os
import subprocess

from mailfallback.config import settings
from mailfallback.models import Repository
from mailfallback.security import decrypt_credentials

logger = logging.getLogger(__name__)

# Retention presets: (keep_daily, keep_weekly, keep_monthly)
_RETENTION_PRESETS: dict[str, tuple[int, int, int]] = {
    "light": (7, 4, 0),
    "standard": (30, 12, 6),
    "full": (90, 52, 24),
}


def build_repo_url(destination: Repository, account_id: str) -> str:
    """Build the restic repository URL for a given destination and account."""
    if destination.backend_type.value == "s3":
        endpoint = decrypt_credentials(destination.s3_endpoint, settings.secret_key)
        bucket = decrypt_credentials(destination.s3_bucket, settings.secret_key)
        return f"s3:{endpoint}/{bucket}/{account_id}"
    return os.path.join(
        decrypt_credentials(destination.local_path, settings.secret_key),
        account_id,
    )


def build_env(destination: Repository, account_id: str) -> dict[str, str]:
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


def _run_restic(
    args: list[str], env: dict[str, str], insecure_tls: bool = False
) -> subprocess.CompletedProcess[str]:
    """Run a restic command with the given args and env."""
    cmd = ["restic"]
    if insecure_tls:
        cmd.append("--insecure-tls")
    cmd.extend(args)
    full_env = {**os.environ, **env}
    logger.debug("Running: %s", " ".join(cmd))
    return subprocess.run(cmd, capture_output=True, text=True, env=full_env)


def _is_insecure(destination: Repository) -> bool:
    return getattr(destination, "insecure_tls", False)


def test_destination(destination: Repository) -> dict:
    """Test connectivity to a backup destination. Returns {ok: bool, error: str}.

    Delegates to s3_probe: validates reachability and write permission with a
    probe object (S3) or a probe file (local), creating no restic repositories.
    """
    from mailfallback.services import s3_probe

    return s3_probe.probe(destination)


def init_repo(destination: Repository, account_id: str) -> bool:
    """Initialize a restic repository. Returns True if init succeeded or repo exists."""
    env = build_env(destination, account_id)
    result = _run_restic(["init"], env, _is_insecure(destination))
    if result.returncode == 0:
        logger.info("Restic repo initialized for account %s", account_id)
        return True
    # restic returns 1 if repo already exists
    if "already initialized" in result.stderr or "already exists" in result.stderr:
        logger.debug("Restic repo already exists for account %s", account_id)
        return True
    logger.error("Failed to init restic repo for %s: %s", account_id, result.stderr)
    return False


def run_backup(destination: Repository, account_id: str, maildir_path: str) -> dict:
    """Run a restic backup of the maildir path. Returns parsed JSON output."""
    env = build_env(destination, account_id)
    result = _run_restic(["backup", "--json", maildir_path], env, _is_insecure(destination))
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


def list_snapshots(destination: Repository, account_id: str) -> list[dict]:
    """List snapshots in the restic repository, newest first."""
    env = build_env(destination, account_id)
    result = _run_restic(["snapshots", "--json"], env, _is_insecure(destination))
    if result.returncode != 0:
        raise RuntimeError(f"Restic snapshots failed: {result.stderr}")
    try:
        snapshots = json.loads(result.stdout)
    except json.JSONDecodeError:
        return []
    snapshots.sort(key=lambda s: s.get("time", ""), reverse=True)
    return snapshots


def restore_snapshot(
    destination: Repository,
    account_id: str,
    snapshot_id: str,
    target_path: str,
) -> dict:
    """Restore a specific snapshot to the target path."""
    env = build_env(destination, account_id)
    result = _run_restic(
        ["restore", snapshot_id, "--target", target_path],
        env,
        _is_insecure(destination),
    )
    if result.returncode != 0:
        raise RuntimeError(f"Restic restore failed: {result.stderr}")
    return {"snapshot_id": snapshot_id, "target_path": target_path, "output": result.stdout}


def apply_retention(
    destination: Repository,
    account_id: str,
    preset: str,
    keep_daily: int | None = None,
    keep_weekly: int | None = None,
    keep_monthly: int | None = None,
) -> dict:
    """Apply retention policy using restic forget --prune.

    Returns dict with at least:
        pruned (bool): True when forget --prune ran successfully.
        removed_snapshot_ids (list[str]): short_ids of snapshots removed by this run.
        output (str): raw stdout from restic.
    """
    env = build_env(destination, account_id)
    retention_args = get_retention_args(preset, keep_daily, keep_weekly, keep_monthly)
    if not retention_args:
        return {"pruned": False, "removed_snapshot_ids": [], "reason": "no retention args"}
    result = _run_restic(
        ["forget", "--prune", "--json", *retention_args], env, _is_insecure(destination)
    )
    if result.returncode != 0:
        raise RuntimeError(f"Restic forget failed: {result.stderr}")

    removed_ids: list[str] = []
    if result.stdout:
        for raw_line in result.stdout.splitlines():
            line = raw_line.strip()
            if not (line.startswith("[") or line.startswith("{")):
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            entries = payload if isinstance(payload, list) else [payload]
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                for snap in entry.get("remove", []) or []:
                    if not isinstance(snap, dict):
                        continue
                    sid = snap.get("short_id") or (snap.get("id") or "")[:8]
                    if sid:
                        removed_ids.append(sid)
    return {"pruned": True, "removed_snapshot_ids": removed_ids, "output": result.stdout}


def list_files(destination, account_id: str, snapshot_id: str):
    """Yield file paths inside a snapshot. Uses restic ls --json --recursive.

    Returns a generator of strings (paths inside the snapshot). Directory
    entries are filtered out — only file paths are yielded.
    """
    env = build_env(destination, account_id)
    result = _run_restic(
        ["ls", "--json", "--recursive", snapshot_id],
        env,
        _is_insecure(destination),
    )
    if result.returncode != 0:
        return
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if entry.get("type") != "node":
            continue
        if entry.get("node_type") != "file":
            continue
        path = entry.get("path")
        if path:
            yield path


def forget_all(destination: Repository, account_id: str) -> bool:
    """Delete all snapshots from the repository."""
    env = build_env(destination, account_id)
    # First list snapshots to get their IDs
    snapshots = list_snapshots(destination, account_id)
    if not snapshots:
        return True

    snapshot_ids = [s["short_id"] for s in snapshots]
    result = _run_restic(["forget", "--prune", *snapshot_ids], env, _is_insecure(destination))
    if result.returncode != 0:
        logger.error("Failed to forget all snapshots: %s", result.stderr)
        return False
    return True
