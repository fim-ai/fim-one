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


# ---------------------------------------------------------------------------
# Credential encryption key — auto-generated on first startup, same pattern
# as JWT_SECRET_KEY in auth.py.  Persisted to .env so the key survives
# restarts and encrypted credential rows remain readable.
# ---------------------------------------------------------------------------

_cred_fernet_instance = None


def _resolve_credential_key() -> str:
    """Return CREDENTIAL_ENCRYPTION_KEY, auto-generating and persisting it if absent.

    Mirrors the ``_resolve_secret_key()`` pattern in ``auth.py``:
    - If the env var is already set, use it as-is.
    - Otherwise generate a secure 64-hex-char secret, write it to the project
      ``.env`` file (creating it if necessary), inject it into the current
      process, and log a warning so operators know to back it up.

    Changing this key after credentials have been stored will make those rows
    unreadable — treat it like a database encryption master key.
    """
    import re
    import secrets
    from pathlib import Path

    val = os.environ.get("CREDENTIAL_ENCRYPTION_KEY", "")
    if val:
        return val

    generated = secrets.token_hex(32)

    # encryption.py lives at src/fim_one/core/security/encryption.py
    # parents[0]=security/, [1]=core/, [2]=fim_one/, [3]=src/, [4]=project root
    project_root = Path(__file__).resolve().parents[4]
    env_file = project_root / ".env"

    try:
        content = env_file.read_text(encoding="utf-8") if env_file.exists() else ""
        new_line = f"CREDENTIAL_ENCRYPTION_KEY={generated}"
        new_content, n = re.subn(
            r'^#?\s*CREDENTIAL_ENCRYPTION_KEY=.*$',
            new_line,
            content,
            flags=re.MULTILINE,
        )
        updated = new_content if n > 0 else (content.rstrip("\n") + ("\n" if content else "") + new_line + "\n")
        env_file.write_text(updated, encoding="utf-8")
        logger.warning(
            "CREDENTIAL_ENCRYPTION_KEY was not set — auto-generated a secure key and "
            "persisted it to %s. Back this key up: losing it makes all stored connector "
            "credentials unreadable.",
            env_file,
        )
    except OSError as exc:
        logger.warning(
            "CREDENTIAL_ENCRYPTION_KEY was not set and could not write to %s (%s). "
            "A temporary in-memory key will be used — credentials will be unreadable after restart.",
            env_file,
            exc,
        )

    os.environ["CREDENTIAL_ENCRYPTION_KEY"] = generated
    return generated


# Module-level resolution — runs at import time so all callers get the same key.
_CREDENTIAL_KEY_RAW: str = _resolve_credential_key()


def get_credential_key() -> bytes:
    """Return the Fernet-compatible credential encryption key (always set)."""
    return base64.urlsafe_b64encode(hashlib.sha256(_CREDENTIAL_KEY_RAW.encode()).digest())


def encrypt_credential(blob: dict) -> str:
    """Encrypt a credential dict to a Fernet-encrypted string."""
    import json
    from cryptography.fernet import Fernet
    f = Fernet(get_credential_key())
    return f.encrypt(json.dumps(blob).encode("utf-8")).decode("utf-8")


def decrypt_credential(ciphertext: str) -> dict:
    """Decrypt a credential string back to a dict.

    Handles legacy plaintext-JSON rows transparently: if the string starts
    with ``{`` it is parsed directly (backward-compat for rows written before
    the encryption key was configured).
    """
    import json
    if ciphertext.startswith("{"):
        try:
            return json.loads(ciphertext)
        except Exception:
            return {}
    try:
        from cryptography.fernet import Fernet
        f = Fernet(get_credential_key())
        return json.loads(f.decrypt(ciphertext.encode("utf-8")).decode("utf-8"))
    except Exception:
        logger.warning("Failed to decrypt credential blob")
        return {}
