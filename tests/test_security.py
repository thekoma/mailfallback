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


def test_hash_token_is_deterministic_and_keyed():
    from mailfallback.security import hash_token

    a = hash_token("s3cret", "key-one")
    b = hash_token("s3cret", "key-one")
    c = hash_token("s3cret", "key-two")

    assert a == b
    assert a != c
    assert len(a) == 64  # sha256 hex
    assert "s3cret" not in a


def test_verify_token_accepts_the_secret_and_rejects_others():
    from mailfallback.security import hash_token, verify_token

    hashed = hash_token("s3cret", "key-one")

    assert verify_token("s3cret", hashed, "key-one") is True
    assert verify_token("wrong", hashed, "key-one") is False
    # A right secret under the wrong server key must not authenticate.
    assert verify_token("s3cret", hashed, "key-two") is False
