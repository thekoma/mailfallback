"""Connection probing for backup repositories — validates reachability and
write permission without restic side effects (no junk repos in the bucket)."""

import contextlib
import logging
import os
import uuid

import boto3
from botocore.client import Config as BotoConfig
from botocore.exceptions import BotoCoreError, ClientError
from cryptography.fernet import InvalidToken

from mailfallback.config import settings
from mailfallback.models import BackendType, Repository
from mailfallback.security import decrypt_credentials

logger = logging.getLogger(__name__)

# Prefix used by pre-2026-06 versions' `restic init` connection test.
LEGACY_TEST_PREFIX = "__mfb_connection_test__/"


def s3_client(destination: Repository):
    """Build a boto3 S3 client from a Repository's (encrypted) settings."""
    endpoint = decrypt_credentials(destination.s3_endpoint, settings.secret_key)
    access_key = decrypt_credentials(destination.s3_access_key, settings.secret_key)
    secret_key = decrypt_credentials(destination.s3_secret_key, settings.secret_key)
    if not endpoint.startswith(("http://", "https://")):
        endpoint = f"https://{endpoint}"
    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        verify=not destination.insecure_tls,
        config=BotoConfig(connect_timeout=10, read_timeout=30, retries={"max_attempts": 1}),
    )


def bucket_name(destination: Repository) -> str:
    return decrypt_credentials(destination.s3_bucket, settings.secret_key)


def probe(destination: Repository) -> dict:
    """Test reachability + write permission. Returns {"ok": bool, "error": str|None}.

    Does NOT validate the restic password: on a new prefix the password
    *defines* the repository, there is nothing to check it against.
    """
    if destination.backend_type == BackendType.s3:
        return _probe_s3(destination)
    return _probe_local(destination)


def _probe_s3(destination: Repository) -> dict:
    required = (
        destination.s3_endpoint,
        destination.s3_bucket,
        destination.s3_access_key,
        destination.s3_secret_key,
    )
    if not all(required):
        return {"ok": False, "error": "repository is missing S3 settings"}
    try:
        client = s3_client(destination)
        bucket = bucket_name(destination)
    except InvalidToken:
        return {"ok": False, "error": "cannot decrypt repository settings (secret key changed?)"}
    except ValueError as e:
        return {"ok": False, "error": f"invalid S3 endpoint: {str(e)[:200]}"}
    key = f".mfb-probe-{uuid.uuid4()}"
    put_succeeded = False
    try:
        client.put_object(Bucket=bucket, Key=key, Body=b"mfb-probe")
        put_succeeded = True
        client.delete_object(Bucket=bucket, Key=key)
    except (ClientError, BotoCoreError, ValueError) as e:
        if put_succeeded:
            # Don't leave the probe object orphaned in the bucket — retry best-effort.
            with contextlib.suppress(Exception):
                client.delete_object(Bucket=bucket, Key=key)
            return {
                "ok": False,
                "error": (
                    "write succeeded but delete was denied "
                    f"(restic needs delete permission for lock files): {str(e)[:200]}"
                ),
            }
        return {"ok": False, "error": str(e)[:200]}
    _cleanup_legacy_junk(client, bucket)
    return {"ok": True, "error": None}


def _cleanup_legacy_junk(client, bucket: str) -> None:
    """Best-effort removal of leftover `__mfb_connection_test__` objects.

    Single-page listing (<=1000 keys) is enough: a legacy junk repo from
    `restic init` holds only ~10 objects (config + empty key/lock dirs).
    """
    try:
        resp = client.list_objects_v2(Bucket=bucket, Prefix=LEGACY_TEST_PREFIX)
        objs = [{"Key": o["Key"]} for o in resp.get("Contents", [])]
        if objs:
            client.delete_objects(Bucket=bucket, Delete={"Objects": objs})
            logger.info("Removed %d legacy connection-test objects", len(objs))
    except Exception:
        logger.debug("Legacy junk cleanup skipped", exc_info=True)


def _probe_local(destination: Repository) -> dict:
    if not destination.local_path:
        return {"ok": False, "error": "repository is missing a local path"}
    try:
        path = decrypt_credentials(destination.local_path, settings.secret_key)
    except InvalidToken:
        return {"ok": False, "error": "cannot decrypt repository settings (secret key changed?)"}
    probe_file = os.path.join(path, f".mfb-probe-{uuid.uuid4()}")
    try:
        os.makedirs(path, exist_ok=True)
        with open(probe_file, "w") as f:
            f.write("mfb-probe")
        os.remove(probe_file)
    except OSError as e:
        return {"ok": False, "error": str(e)[:200]}
    return {"ok": True, "error": None}
