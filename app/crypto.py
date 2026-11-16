"""Fernet encryption for customer MT5 passwords.

The key is ENCRYPTION_KEY (generated once, stored as an env var). Without
it the stored values are useless — which is the point. Passwords are
never logged, never returned by any API, and never shown in the admin
panel (only a "saved" badge).
"""
from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken

from . import config


def _fernet() -> Fernet:
    key = config.ENCRYPTION_KEY.encode()
    # _get_or_generate may have produced a urlsafe token, not a Fernet key.
    # Fernet keys are 44-char urlsafe base64; derive one if needed.
    if len(key) != 44:
        import base64
        import hashlib

        raw = hashlib.sha256(key).digest()
        key = base64.urlsafe_b64encode(raw)
    return Fernet(key)


def encrypt_password(plain: str) -> str:
    return _fernet().encrypt(plain.encode()).decode()


def decrypt_password(enc: str) -> str:
    try:
        return _fernet().decrypt(enc.encode()).decode()
    except InvalidToken:
        raise ValueError("stored password cannot be decrypted (wrong key?)")
