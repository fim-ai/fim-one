"""Field-level encryption utilities for sensitive configuration data.

Uses Fernet symmetric encryption derived from the JWT_SECRET_KEY so that
no additional secret management infrastructure is required.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

# Lazy-initialised Fernet instance (avoid import-time dependency on cryptography)
_fernet_instance = None


def get_encryption_key() -> bytes:
    """Derive a 32-byte Fernet key from JWT_SECRET_KEY via SHA-256 + base64.

    Returns
    -------
    bytes
        A URL-safe base64-encoded 32-byte key suitable for Fernet.
    """
    secret = os.environ.get("JWT_SECRET_KEY", "")
    if not secret:
        raise RuntimeError(
            "JWT_SECRET_KEY must be set for encryption. "
            "Database connector passwords cannot be encrypted without it."
        )
    digest = hashlib.sha256(secret.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


def _get_fernet():
    """Return a cached Fernet instance."""
    global _fernet_instance
    if _fernet_instance is None:
        from cryptography.fernet import Fernet

        _fernet_instance = Fernet(get_encryption_key())
    return _fernet_instance


def encrypt_field(plaintext: str) -> str:
    """Encrypt a string field using Fernet.

    Parameters
    ----------
    plaintext:
        The value to encrypt.

    Returns
    -------
    str
        The encrypted ciphertext as a URL-safe base64 string.
    """
    fernet = _get_fernet()
    return fernet.encrypt(plaintext.encode("utf-8")).decode("utf-8")


def decrypt_field(ciphertext: str) -> str:
    """Decrypt a Fernet-encrypted string field.

    Parameters
    ----------
    ciphertext:
        The encrypted value to decrypt.

    Returns
    -------
    str
        The original plaintext.
    """
    fernet = _get_fernet()
    return fernet.decrypt(ciphertext.encode("utf-8")).decode("utf-8")


def encrypt_db_config(config: dict[str, Any]) -> dict[str, Any]:
    """Encrypt the ``password`` field within a database config dict.

    If ``password`` is present and non-empty, it is removed and replaced
    with an ``encrypted_password`` field containing the Fernet ciphertext.

    Parameters
    ----------
    config:
        Database connection config dict (host, port, password, etc.).

    Returns
    -------
    dict
        A copy of the config with password encrypted.
    """
    result = dict(config)
    password = result.pop("password", None)
    if password:
        result["encrypted_password"] = encrypt_field(password)
    return result


def decrypt_db_config(config: dict[str, Any]) -> dict[str, Any]:
    """Decrypt the ``encrypted_password`` field back to ``password``.

    Parameters
    ----------
    config:
        Database connection config dict with encrypted_password.

    Returns
    -------
    dict
        A copy of the config with password decrypted.
    """
    result = dict(config)
    encrypted = result.pop("encrypted_password", None)
    if encrypted:
        try:
            result["password"] = decrypt_field(encrypted)
        except Exception:
            logger.warning("Failed to decrypt db_config password")
            result["password"] = ""
    return result
