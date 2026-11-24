import base64
import hashlib
import json
from typing import Any, Optional

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings


def get_fernet_key(secret: Optional[str] = None) -> bytes:
    """Derive a URL-safe base64-encoded 32-byte key from a secret string."""
    key_material = (secret or settings.JWT_SECRET).encode("utf-8")
    digest = hashlib.sha256(key_material).digest()
    return base64.urlsafe_b64encode(digest)


def get_fernet_cipher(secret: Optional[str] = None) -> Fernet:
    """Return a Fernet cipher instance."""
    return Fernet(get_fernet_key(secret))


def encrypt_data(plaintext: str, secret: Optional[str] = None) -> str:
    """Encrypt a string and return URL-safe base64 ciphertext string."""
    cipher = get_fernet_cipher(secret)
    encrypted_bytes = cipher.encrypt(plaintext.encode("utf-8"))
    return encrypted_bytes.decode("utf-8")


def decrypt_data(ciphertext: str, secret: Optional[str] = None) -> str:
    """Decrypt a ciphertext string back to plaintext."""
    cipher = get_fernet_cipher(secret)
    try:
        decrypted_bytes = cipher.decrypt(ciphertext.encode("utf-8"))
        return decrypted_bytes.decode("utf-8")
    except (InvalidToken, Exception) as exc:
        raise ValueError("Invalid or corrupted ciphertext") from exc


def encrypt_json(data: Any, secret: Optional[str] = None) -> str:
    """Serialize data to JSON and encrypt."""
    raw_json = json.dumps(data, separators=(",", ":"))
    return encrypt_data(raw_json, secret=secret)


def decrypt_json(ciphertext: str, secret: Optional[str] = None) -> Any:
    """Decrypt and deserialize JSON."""
    decrypted_str = decrypt_data(ciphertext, secret=secret)
    try:
        return json.loads(decrypted_str)
    except json.JSONDecodeError as exc:
        raise ValueError("Decrypted content is not valid JSON") from exc
