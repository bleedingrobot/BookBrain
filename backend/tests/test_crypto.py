from cryptography.fernet import Fernet

from app.core import crypto
from app.core.config import get_settings


def test_encrypt_decrypt_round_trip(monkeypatch) -> None:
    monkeypatch.setattr(
        get_settings(), "token_encryption_key", Fernet.generate_key().decode()
    )
    ciphertext = crypto.encrypt("hello world")
    assert ciphertext != "hello world"
    assert crypto.decrypt(ciphertext) == "hello world"
