# src/mailfallback/security.py
import base64
import hashlib
import logging

import bcrypt
from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger(__name__)

_KDF_ITERATIONS = 600_000
_KDF_SALT = b"mailfallback-fernet-v1"


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode(), hashed.encode())


def _derive_fernet_key(secret_key: str) -> bytes:
    dk = hashlib.pbkdf2_hmac("sha256", secret_key.encode(), _KDF_SALT, _KDF_ITERATIONS)
    return base64.urlsafe_b64encode(dk)


def _derive_fernet_key_legacy(secret_key: str) -> bytes:
    digest = hashlib.sha256(secret_key.encode()).digest()
    return base64.urlsafe_b64encode(digest)


def encrypt_credentials(plaintext: str, secret_key: str) -> str:
    f = Fernet(_derive_fernet_key(secret_key))
    return f.encrypt(plaintext.encode()).decode()


def decrypt_credentials(encrypted: str, secret_key: str) -> str:
    f = Fernet(_derive_fernet_key(secret_key))
    try:
        return f.decrypt(encrypted.encode()).decode()
    except InvalidToken:
        f_legacy = Fernet(_derive_fernet_key_legacy(secret_key))
        plaintext = f_legacy.decrypt(encrypted.encode()).decode()
        logger.warning("Legacy KDF used for decryption — credentials should be re-encrypted")
        return plaintext


def decrypt_credentials_with_upgrade(encrypted: str, secret_key: str) -> tuple[str, str | None]:
    """Decrypt, and if the ciphertext used the weak legacy KDF, also return a
    freshly PBKDF2-encrypted replacement for the caller to persist.

    Returns (plaintext, upgraded_ciphertext) where upgraded_ciphertext is None
    for modern ciphertext. The common (modern) path does a single key
    derivation; the legacy path reuses the modern key for re-encryption.
    """
    f = Fernet(_derive_fernet_key(secret_key))
    try:
        return f.decrypt(encrypted.encode()).decode(), None
    except InvalidToken:
        f_legacy = Fernet(_derive_fernet_key_legacy(secret_key))
        plaintext = f_legacy.decrypt(encrypted.encode()).decode()
        logger.warning("Legacy KDF used for decryption — re-encrypting with modern KDF")
        return plaintext, f.encrypt(plaintext.encode()).decode()


def is_legacy_encrypted(encrypted: str, secret_key: str) -> bool:
    """True if the ciphertext was produced by the weak unsalted-SHA256 KDF.

    Callers with the persisted row should re-encrypt it (see
    account_service.get_account_credentials) so the brute-forceable legacy path
    drains out of the database over time.
    """
    try:
        Fernet(_derive_fernet_key(secret_key)).decrypt(encrypted.encode())
        return False
    except InvalidToken:
        try:
            Fernet(_derive_fernet_key_legacy(secret_key)).decrypt(encrypted.encode())
            return True
        except InvalidToken:
            return False
