"""Repository content inventory: list restic sub-repo prefixes and classify
them against the database (account / config / attached / orphan)."""

import logging
import os

from sqlalchemy.orm import Session

from mailfallback.config import settings
from mailfallback.models import Account, BackendType, Repository, RepositoryAttachment
from mailfallback.security import decrypt_credentials
from mailfallback.services import restic_service, s3_probe

logger = logging.getLogger(__name__)

CONFIG_PREFIX = "__mfb_config__"


def list_prefixes(destination: Repository) -> list[str]:
    """Top-level prefixes in the repository backend (= restic sub-repos)."""
    if destination.backend_type == BackendType.s3:
        client = s3_probe.s3_client(destination)
        bucket = s3_probe.bucket_name(destination)
        prefixes: list[str] = []
        kwargs: dict = {"Bucket": bucket, "Delimiter": "/"}
        while True:
            resp = client.list_objects_v2(**kwargs)
            prefixes.extend(p["Prefix"].rstrip("/") for p in resp.get("CommonPrefixes", []))
            if not resp.get("IsTruncated"):
                break
            token = resp.get("NextContinuationToken")
            if not token:
                break
            kwargs["ContinuationToken"] = token
        return sorted(prefixes)
    path = decrypt_credentials(destination.local_path, settings.secret_key)
    if not os.path.isdir(path):
        return []
    return sorted(d for d in os.listdir(path) if os.path.isdir(os.path.join(path, d)))


def classify(db: Session, destination: Repository, prefixes: list[str]) -> list[dict]:
    """Classify each prefix: account | config | attached | orphan.

    Returns [{"prefix", "kind", "account": Account|None, "attachment": RepositoryAttachment|None}].
    """
    accounts = {a.id: a for a in db.query(Account).all()}
    attachments = {
        att.prefix: att
        for att in db.query(RepositoryAttachment)
        .filter(RepositoryAttachment.repository_id == destination.id)
        .all()
    }
    entries = []
    for prefix in prefixes:
        if prefix == CONFIG_PREFIX:
            kind, account, attachment = "config", None, None
        elif prefix in accounts:
            kind, account, attachment = "account", accounts[prefix], None
        elif prefix in attachments:
            att = attachments[prefix]
            kind, account, attachment = "attached", accounts.get(att.account_id), att
        else:
            kind, account, attachment = "orphan", None, None
        entries.append(
            {"prefix": prefix, "kind": kind, "account": account, "attachment": attachment}
        )
    return entries


def prefix_detail(
    destination: Repository, prefix: str, restic_password_enc: str | None = None
) -> dict:
    """Open the sub-repo and summarize. Validates the effective password.

    restic_password_enc: optional per-attachment Fernet password; falls back
    to the repository's own restic password when None.
    """
    try:
        snapshots = restic_service.list_snapshots(
            destination, prefix, restic_password_enc=restic_password_enc
        )
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}
    email = name = None
    if snapshots:
        for tag in snapshots[0].get("tags") or []:
            if tag.startswith("mfb:email="):
                email = tag.removeprefix("mfb:email=")
            elif tag.startswith("mfb:name="):
                name = tag.removeprefix("mfb:name=")
    return {
        "ok": True,
        "snapshot_count": len(snapshots),
        "latest": snapshots[0]["time"] if snapshots else None,
        "email": email,
        "name": name,
    }
