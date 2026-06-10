"""Connection probing for backup repositories — validates reachability and
write permission without restic side effects (no junk repos in the bucket)."""

import logging
import os
import uuid

import boto3
from botocore.client import Config as BotoConfig
from botocore.exceptions import BotoCoreError, ClientError

from mailfallback.config import settings
from mailfallback.models import Repository
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
    if destination.backend_type.value == "s3":
        return _probe_s3(destination)
    return _probe_local(destination)


def _probe_s3(destination: Repository) -> dict:
    key = f".mfb-probe-{uuid.uuid4()}"
    try:
        client = s3_client(destination)
        bucket = bucket_name(destination)
        client.put_object(Bucket=bucket, Key=key, Body=b"mfb-probe")
        client.delete_object(Bucket=bucket, Key=key)
    except (ClientError, BotoCoreError, ValueError) as e:
        return {"ok": False, "error": str(e)[:200]}
    _cleanup_legacy_junk(client, bucket)
    return {"ok": True, "error": None}


def _cleanup_legacy_junk(client, bucket: str) -> None:
    """Best-effort removal of leftover `__mfb_connection_test__` objects."""
    try:
        resp = client.list_objects_v2(Bucket=bucket, Prefix=LEGACY_TEST_PREFIX)
        objs = [{"Key": o["Key"]} for o in resp.get("Contents", [])]
        if objs:
            client.delete_objects(Bucket=bucket, Delete={"Objects": objs})
            logger.info("Removed %d legacy connection-test objects", len(objs))
    except Exception:
        logger.debug("Legacy junk cleanup skipped", exc_info=True)


def _probe_local(destination: Repository) -> dict:
    path = decrypt_credentials(destination.local_path, settings.secret_key)
    probe_file = os.path.join(path, f".mfb-probe-{uuid.uuid4()}")
    try:
        os.makedirs(path, exist_ok=True)
        with open(probe_file, "w") as f:
            f.write("mfb-probe")
        os.remove(probe_file)
    except OSError as e:
        return {"ok": False, "error": str(e)[:200]}
    return {"ok": True, "error": None}
