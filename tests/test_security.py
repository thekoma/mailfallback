# tests/test_security.py
from mailfallback.security import (
    decrypt_credentials,
    encrypt_credentials,
    hash_password,
    verify_password,
)


def test_password_hash_and_verify():
    hashed = hash_password("mysecretpass")
    assert hashed != "mysecretpass"
    assert verify_password("mysecretpass", hashed) is True
    assert verify_password("wrongpass", hashed) is False


def test_encrypt_decrypt_credentials():
    secret_key = "test-secret-key-for-encryption"
    plaintext = "oauth2-refresh-token-abc123"
    encrypted = encrypt_credentials(plaintext, secret_key)
    assert encrypted != plaintext
    decrypted = decrypt_credentials(encrypted, secret_key)
    assert decrypted == plaintext


def test_encrypt_decrypt_with_different_keys():
    encrypted = encrypt_credentials("data", "key1")
    try:
        decrypt_credentials(encrypted, "key2")
        raise AssertionError("Should have raised an exception")
    except Exception:
        pass
