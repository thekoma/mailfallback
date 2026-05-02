# src/mailfallback/security.py
import base64
import hashlib

import bcrypt
from cryptography.fernet import Fernet


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode(), hashed.encode())


def _derive_fernet_key(secret_key: str) -> bytes:
    digest = hashlib.sha256(secret_key.encode()).digest()
    return base64.urlsafe_b64encode(digest)


def encrypt_credentials(plaintext: str, secret_key: str) -> str:
    f = Fernet(_derive_fernet_key(secret_key))
    return f.encrypt(plaintext.encode()).decode()


def decrypt_credentials(encrypted: str, secret_key: str) -> str:
    f = Fernet(_derive_fernet_key(secret_key))
    return f.decrypt(encrypted.encode()).decode()
