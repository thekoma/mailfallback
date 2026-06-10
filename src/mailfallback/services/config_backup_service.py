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
import tempfile
from pathlib import Path

import sqlalchemy as sa
from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import Table
from sqlalchemy.orm import Session

from mailfallback.config import settings
from mailfallback.models import Base, Repository
from mailfallback.security import decrypt_credentials, encrypt_credentials
from mailfallback.services import restic_service

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1
CONFIG_FILENAME = "mfb-config.json.enc"

# Insert order respects FKs.
_EXPORT_TABLES: list[str] = [
    "mail_stores",
    "users",
    "user_allowed_stores",
    "backup_destinations",
    "user_allowed_repositories",
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


# Unique columns used to detect "same thing, different UUID" rows on import
# (e.g. the default store and admin user seeded by a fresh install). Matching
# rows are skipped and their old PK is remapped onto the existing row's PK.
_NATURAL_KEYS: dict[str, str] = {
    "users": "username",
    "mail_stores": "path",
    "accounts": "maildir_path",
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
    if not passphrase:
        raise ValueError("Passphrase must not be empty")
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
    if envelope.get("kdf") != "scrypt":
        raise ConfigDecryptError(f"Unsupported KDF: {envelope.get('kdf')!r}")
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


def _safe_error(e: Exception) -> str:
    """Exception description WITHOUT row values: SQLAlchemy's str(e) appends a
    '[parameters: (...)]' dump containing full row contents (password hashes,
    ciphertexts). Only the type name plus, for DBAPI errors, the first line of
    e.orig (constraint info, no parameters) is safe to surface."""
    detail = type(e).__name__
    orig = getattr(e, "orig", None)
    if orig is not None:
        detail += f" ({str(orig).splitlines()[0][:120]})"
    return detail


def import_export(db: Session, data: dict) -> dict:
    """Insert exported rows, preserving IDs, re-encrypting secrets locally.

    Collision handling:
    - Same primary key already present -> row skipped (identity, no remap).
    - Same natural key (_NATURAL_KEYS: username / store path / maildir_path)
      under a DIFFERENT id -> row skipped and old_id remapped onto the
      existing row's id. This makes restore work on a seeded fresh install,
      where lifespan already created a default MailStore and admin user with
      new UUIDs: every FK column (derived from table metadata, including the
      association tables) is rewritten through the remap before insert.

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
    remap: dict[str, str] = {}  # old exported id -> id of the existing local row

    for name in _EXPORT_TABLES:
        table = _table(name)
        pk_cols = [c.name for c in table.primary_key.columns]
        fk_cols = [c.name for c in table.columns if c.foreign_keys]
        natural_key = _NATURAL_KEYS.get(name)
        for record in data["tables"].get(name, []):
            try:
                record = dict(record)
                for col in fk_cols:
                    if record.get(col) in remap:
                        record[col] = remap[record[col]]
                pk_filter = [table.c[c] == record[c] for c in pk_cols]
                if db.execute(table.select().where(*pk_filter)).first():
                    skipped[name] += 1
                    continue
                if natural_key and record.get(natural_key) is not None:
                    existing = (
                        db.execute(
                            table.select().where(table.c[natural_key] == record[natural_key])
                        )
                        .mappings()
                        .first()
                    )
                    if existing:
                        remap[record[pk_cols[0]]] = existing[pk_cols[0]]
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
                logger.warning("Config import: %s row failed: %s", name, type(e).__name__)
                errors.append(f"{name}: {_safe_error(e)}")
    db.commit()
    return {"imported": imported, "skipped": skipped, "errors": errors}


def _utcnow() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


def run_config_backup(db: Session, repository: Repository) -> dict:
    """Export, encrypt, and snapshot the configuration into __mfb_config__.

    Sets last_config_backup_at/status ("ok"|"failed") and last_config_backup_error.
    """
    from mailfallback.services.repo_inventory import CONFIG_PREFIX

    try:
        if not repository.config_backup_passphrase:
            raise ValueError("Repository has no config backup passphrase")
        passphrase = decrypt_credentials(repository.config_backup_passphrase, settings.secret_key)
        blob = encrypt_export(build_export(db), passphrase)
        with tempfile.TemporaryDirectory(prefix="mfb-config-backup-") as tmpdir:
            file_path = os.path.join(tmpdir, CONFIG_FILENAME)
            with open(file_path, "wb") as f:
                f.write(blob)
            if not restic_service.init_repo(repository, CONFIG_PREFIX):
                raise RuntimeError("Could not initialize config repository")
            restic_service.run_backup(repository, CONFIG_PREFIX, file_path, tags=["mfb:config"])
            restic_service.apply_retention(repository, CONFIG_PREFIX, "custom", keep_daily=30)
        repository.last_config_backup_at = _utcnow()
        repository.last_config_backup_status = "ok"
        repository.last_config_backup_error = None
        db.commit()
        logger.info("Config backup completed for repository %s", repository.name)
        return {"ok": True, "error": None}
    except Exception as e:
        db.rollback()
        repository.last_config_backup_at = _utcnow()
        repository.last_config_backup_status = "failed"
        repository.last_config_backup_error = str(e)[:500]
        db.commit()
        logger.error("Config backup failed for %s: %s", repository.name, e)
        return {"ok": False, "error": str(e)[:200]}


def fetch_latest_config(repository: Repository, target_dir: str) -> str:
    """Restore the newest __mfb_config__ snapshot and return the file path."""
    from mailfallback.services.repo_inventory import CONFIG_PREFIX

    snapshots = restic_service.list_snapshots(repository, CONFIG_PREFIX)
    if not snapshots:
        raise ValueError("No configuration snapshots found in this repository")
    restic_service.restore_snapshot(repository, CONFIG_PREFIX, snapshots[0]["short_id"], target_dir)
    matches = list(Path(target_dir).rglob(CONFIG_FILENAME))
    if not matches:
        raise ValueError("Snapshot did not contain a configuration file")
    return str(matches[0])
