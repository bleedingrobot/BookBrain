from cryptography.fernet import Fernet

from app.core.config import get_settings


def _fernet() -> Fernet:
    key = get_settings().token_encryption_key
    if not key:
        raise RuntimeError(
            "TOKEN_ENCRYPTION_KEY is not set. Generate one with: "
            "python -c \"from cryptography.fernet import Fernet; "
            'print(Fernet.generate_key().decode())"'
        )
    return Fernet(key.encode())


def encrypt(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt(ciphertext: str, *, ttl_seconds: int | None = None) -> str:
    """`ttl_seconds` rejects a token older than that (Fernet embeds its own
    creation time) — used for the viewer passcode session cookie, which has
    no DB row to check for revocation, only its own age. Omitted (the OAuth
    token blob's case) means no expiry check here at all."""
    return _fernet().decrypt(ciphertext.encode(), ttl=ttl_seconds).decode()
