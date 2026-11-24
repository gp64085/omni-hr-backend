import pytest

from app.core.security_crypto import (
    decrypt_data,
    decrypt_json,
    encrypt_data,
    encrypt_json,
    get_fernet_key,
)


def test_fernet_key_derivation():
    key1 = get_fernet_key("my-secret-1")
    key2 = get_fernet_key("my-secret-1")
    key3 = get_fernet_key("my-secret-2")

    assert isinstance(key1, bytes)
    assert key1 == key2
    assert key1 != key3
    assert len(key1) == 44  # base64 encoded 32 bytes


def test_encrypt_decrypt_string():
    plaintext = "mock-sample-plaintext-secret-token"
    ciphertext = encrypt_data(plaintext)

    assert isinstance(ciphertext, str)
    assert ciphertext != plaintext
    assert "mock-sample" not in ciphertext

    decrypted = decrypt_data(ciphertext)
    assert decrypted == plaintext


def test_encrypt_decrypt_json():
    payload = {
        "bot_token": "mock-bot-token-sample",
        "signing_secret": "abcdef1234567890",
        "groq_api_key": "mock-api-key-sample",
        "model": "llama-3.3-70b-versatile",
    }
    encrypted = encrypt_json(payload)
    assert isinstance(encrypted, str)
    assert "mock-api-key" not in encrypted

    decrypted = decrypt_json(encrypted)
    assert decrypted == payload


def test_decrypt_invalid_ciphertext():
    with pytest.raises(ValueError, match="Invalid or corrupted ciphertext"):
        decrypt_data("not-a-valid-ciphertext")

    with pytest.raises(ValueError, match="Invalid or corrupted ciphertext"):
        decrypt_json("not-a-valid-ciphertext")
