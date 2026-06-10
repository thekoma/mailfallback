"""Full-configuration backup: export every config table with secrets decrypted,
encrypt with a passphrase-derived key (scrypt + Fernet), and import the result
back, re-encrypting secrets with the local MAILFALLBACK_SECRET_KEY.

Original primary keys are preserved on import — they are what makes restic
prefixes (per-account sub-repos named by account UUID) and maildir paths line
up again after a disaster recovery on a fresh install.
"""

import base64
import contextlib
import datetime
import enum
import hashlib
import json
import logging
import os

import sqlalchemy as sa
from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import Table
from sqlalchemy.orm import Session

from mailfallback.config import settings
from mailfallback.models import Base
from mailfallback.security import decrypt_credentials, encrypt_credentials

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1
CONFIG_FILENAME = "mfb-config.json.enc"

# Insert order respects FKs.
_EXPORT_TABLES: list[str] = [
    "mail_stores",
    "users",
    "user_allowed_stores",
    "backup_destinations",
    "accounts",
    "account_owners",
    "groups",
    "group_members",
    "account_groups",
    "account_backups",
    "repository_attachments",
]

# Fernet-encrypted columns: decrypted on export, re-encrypted on import.
_SECRET_COLUMNS: dict[str, list[str]] = {
    "accounts": ["credentials"],
    "backup_destinations": [
        "s3_endpoint",
        "s3_bucket",
        "s3_access_key",
        "s3_secret_key",
        "local_path",
        "restic_password",
        "config_backup_passphrase",
    ],
}


class ConfigDecryptError(Exception):
    """Wrong passphrase or corrupt envelope."""


def _table(name: str) -> Table:
    return Base.metadata.tables[name]


def _jsonable(value):
    if isinstance(value, enum.Enum):
        return value.value
    if isinstance(value, datetime.datetime):
        return value.isoformat()
    return value


def build_export(db: Session) -> dict:
    """Serialize all config tables with Fernet secrets decrypted to plaintext.

    The result must immediately be passed to encrypt_export() — it contains
    every credential in clear.
    """
    tables: dict[str, list[dict]] = {}
    for name in _EXPORT_TABLES:
        table = _table(name)
        rows = []
        for row in db.execute(table.select()).mappings():
            record = {k: _jsonable(v) for k, v in dict(row).items()}
            for col in _SECRET_COLUMNS.get(name, []):
                if record.get(col):
                    record[col] = decrypt_credentials(record[col], settings.secret_key)
            rows.append(record)
        tables[name] = rows
    return {"schema_version": SCHEMA_VERSION, "tables": tables}


def _derive_key(passphrase: str, salt: bytes) -> bytes:
    dk = hashlib.scrypt(
        passphrase.encode(), salt=salt, n=2**15, r=8, p=1, maxmem=64 * 1024 * 1024, dklen=32
    )
    return base64.urlsafe_b64encode(dk)


def encrypt_export(data: dict, passphrase: str) -> bytes:
    salt = os.urandom(16)
    token = Fernet(_derive_key(passphrase, salt)).encrypt(
        json.dumps(data, separators=(",", ":")).encode()
    )
    envelope = {
        "mfb_config_backup": SCHEMA_VERSION,
        "kdf": "scrypt",
        "salt": base64.b64encode(salt).decode(),
        "ciphertext": token.decode(),
    }
    return json.dumps(envelope).encode()


def decrypt_export(blob: bytes, passphrase: str) -> dict:
    try:
        envelope = json.loads(blob)
        salt = base64.b64decode(envelope["salt"])
        token = envelope["ciphertext"].encode()
    except (ValueError, KeyError, TypeError, AttributeError) as e:
        raise ConfigDecryptError(f"Not a valid MFB config backup: {e}") from e
    try:
        plaintext = Fernet(_derive_key(passphrase, salt)).decrypt(token)
    except InvalidToken as e:
        raise ConfigDecryptError("Wrong passphrase (or corrupt backup)") from e
    return json.loads(plaintext)


def _coerce_types(table: Table, record: dict) -> dict:
    out = dict(record)
    for col in table.columns:
        v = out.get(col.name)
        if v is not None and isinstance(v, str) and isinstance(col.type, sa.DateTime):
            with contextlib.suppress(ValueError):
                out[col.name] = datetime.datetime.fromisoformat(v)
    return out


def import_export(db: Session, data: dict) -> dict:
    """Insert exported rows, preserving IDs, re-encrypting secrets locally.

    Collisions (existing PK, or username for users) are skipped and counted.
    Each record is inserted inside its own SAVEPOINT (begin_nested), so a
    single broken row (e.g. dangling FK) is rolled back alone and reported
    without discarding rows already inserted in the same run.
    Returns {"imported": {table: n}, "skipped": {table: n}, "errors": [str]}.
    """
    if data.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"Unsupported schema_version: {data.get('schema_version')}")

    imported: dict[str, int] = dict.fromkeys(_EXPORT_TABLES, 0)
    skipped: dict[str, int] = dict.fromkeys(_EXPORT_TABLES, 0)
    errors: list[str] = []

    for name in _EXPORT_TABLES:
        table = _table(name)
        pk_cols = [c.name for c in table.primary_key.columns]
        for record in data["tables"].get(name, []):
            try:
                pk_filter = [table.c[c] == record[c] for c in pk_cols]
                exists = db.execute(table.select().where(*pk_filter)).first()
                if not exists and name == "users":
                    exists = db.execute(
                        table.select().where(table.c.username == record["username"])
                    ).first()
                if exists:
                    skipped[name] += 1
                    continue
                values = _coerce_types(table, record)
                for col in _SECRET_COLUMNS.get(name, []):
                    if values.get(col):
                        values[col] = encrypt_credentials(values[col], settings.secret_key)
                with db.begin_nested():
                    db.execute(table.insert().values(**values))
                imported[name] += 1
            except Exception as e:
                logger.warning("Config import: failed to restore %s row: %s", name, e)
                errors.append(f"{name}: {str(e)[:200]}")
    db.commit()
    return {"imported": imported, "skipped": skipped, "errors": errors}
