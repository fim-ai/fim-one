"""Tests for field-level encryption utilities."""

from __future__ import annotations

import os

import pytest


@pytest.fixture(autouse=True)
def _set_jwt_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure JWT_SECRET_KEY is set for encryption tests."""
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret-key-for-encryption-tests-1234")
    # Reset the cached Fernet instance so it picks up the new key
    import fim_agent.core.security.encryption as enc_mod

    enc_mod._fernet_instance = None


class TestEncryptDecryptField:
    """Test encrypt_field / decrypt_field roundtrip."""

    def test_roundtrip(self) -> None:
        from fim_agent.core.security.encryption import decrypt_field, encrypt_field

        original = "my-secret-password-123"
        encrypted = encrypt_field(original)
        assert encrypted != original
        assert decrypt_field(encrypted) == original

    def test_different_ciphertexts(self) -> None:
        """Each encryption should produce different ciphertext (Fernet uses random IV)."""
        from fim_agent.core.security.encryption import encrypt_field

        enc1 = encrypt_field("same-value")
        enc2 = encrypt_field("same-value")
        # Fernet uses random IV, so ciphertexts should differ
        assert enc1 != enc2

    def test_empty_string(self) -> None:
        from fim_agent.core.security.encryption import decrypt_field, encrypt_field

        encrypted = encrypt_field("")
        assert decrypt_field(encrypted) == ""

    def test_unicode_roundtrip(self) -> None:
        from fim_agent.core.security.encryption import decrypt_field, encrypt_field

        original = "password with unicode characters"
        assert decrypt_field(encrypt_field(original)) == original


class TestEncryptDecryptDbConfig:
    """Test encrypt_db_config / decrypt_db_config."""

    def test_roundtrip(self) -> None:
        from fim_agent.core.security.encryption import decrypt_db_config, encrypt_db_config

        config = {
            "host": "localhost",
            "port": 5432,
            "database": "mydb",
            "username": "admin",
            "password": "super-secret",
        }
        encrypted = encrypt_db_config(config)
        # Password should be removed, encrypted_password should be present
        assert "password" not in encrypted
        assert "encrypted_password" in encrypted
        assert encrypted["encrypted_password"] != "super-secret"

        # Other fields should be preserved
        assert encrypted["host"] == "localhost"
        assert encrypted["port"] == 5432

        # Decrypt should restore password
        decrypted = decrypt_db_config(encrypted)
        assert decrypted["password"] == "super-secret"
        assert "encrypted_password" not in decrypted
        assert decrypted["host"] == "localhost"

    def test_no_password(self) -> None:
        from fim_agent.core.security.encryption import encrypt_db_config

        config = {"host": "localhost", "port": 5432}
        encrypted = encrypt_db_config(config)
        assert "encrypted_password" not in encrypted
        assert encrypted["host"] == "localhost"

    def test_empty_password(self) -> None:
        from fim_agent.core.security.encryption import encrypt_db_config

        config = {"host": "localhost", "password": ""}
        encrypted = encrypt_db_config(config)
        # Empty password should not be encrypted
        assert "encrypted_password" not in encrypted

    def test_decrypt_without_encrypted_password(self) -> None:
        from fim_agent.core.security.encryption import decrypt_db_config

        config = {"host": "localhost", "port": 5432}
        decrypted = decrypt_db_config(config)
        assert decrypted == config


class TestMissingSecretKey:
    """Test behaviour when JWT_SECRET_KEY is not set."""

    def test_encryption_fails_without_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import fim_agent.core.security.encryption as enc_mod

        monkeypatch.delenv("JWT_SECRET_KEY", raising=False)
        enc_mod._fernet_instance = None

        from fim_agent.core.security.encryption import encrypt_field

        with pytest.raises(RuntimeError, match="JWT_SECRET_KEY"):
            encrypt_field("test")
