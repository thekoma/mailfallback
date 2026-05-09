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
