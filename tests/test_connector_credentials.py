"""Unit tests for connector credential encryption and auth_config split helpers."""

from __future__ import annotations

import json

import pytest


# ---------------------------------------------------------------------------
# Shared fixture: reset _cred_fernet_instance between tests
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_cred_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reset CREDENTIAL_ENCRYPTION_KEY to a stable test value between tests.

    The new implementation always encrypts (no plaintext fallback), so we set a
    fixed test key rather than deleting the env var.  Tests that need a *different*
    key can override with their own monkeypatch.setenv call.
    """
    import fim_one.core.security.encryption as enc

    monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", "test-credential-key-for-unit-tests-1234")
    enc._CREDENTIAL_KEY_RAW = "test-credential-key-for-unit-tests-1234"
    enc._cred_fernet_instance = None
    yield
    enc._cred_fernet_instance = None


# ---------------------------------------------------------------------------
# Group 1: encrypt_credential / decrypt_credential
# ---------------------------------------------------------------------------


class TestEncryptCredential:
    """Tests for encrypt_credential and decrypt_credential functions."""

    def test_always_encrypts(self) -> None:
        """encrypt_credential always produces Fernet ciphertext (never plaintext JSON)."""
        from fim_one.core.security.encryption import encrypt_credential

        result = encrypt_credential({"token": "abc"})
        # Must NOT be plain JSON — key is auto-generated at startup
        assert not result.startswith("{")
        with pytest.raises((json.JSONDecodeError, ValueError)):
            json.loads(result)

    def test_encrypted_when_key_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """With CREDENTIAL_ENCRYPTION_KEY set, output is opaque Fernet ciphertext."""
        import fim_one.core.security.encryption as enc

        monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", "test-credential-key-abc123")
        enc._cred_fernet_instance = None

        from fim_one.core.security.encryption import encrypt_credential

        result = encrypt_credential({"token": "secret_value"})
        # Fernet tokens do not start with '{'
        assert not result.startswith("{")
        # Should not be parseable as JSON
        with pytest.raises((json.JSONDecodeError, ValueError)):
            json.loads(result)

    def test_roundtrip_without_key(self) -> None:
        """encrypt then decrypt returns original dict when no encryption key is set."""
        from fim_one.core.security.encryption import decrypt_credential, encrypt_credential

        blob = {"default_token": "my-bearer-token", "extra": "value"}
        assert decrypt_credential(encrypt_credential(blob)) == blob

    def test_roundtrip_with_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """encrypt then decrypt returns original dict when encryption key is set."""
        import fim_one.core.security.encryption as enc

        monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", "roundtrip-test-key-xyz789")
        enc._cred_fernet_instance = None

        from fim_one.core.security.encryption import decrypt_credential, encrypt_credential

        blob = {"default_api_key": "supersecretkey", "scope": "read"}
        assert decrypt_credential(encrypt_credential(blob)) == blob

    def test_decrypt_plain_json(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """decrypt_credential handles plain JSON even when a key is now configured (backward compat)."""
        import fim_one.core.security.encryption as enc

        monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", "some-key-for-compat-test")
        enc._cred_fernet_instance = None

        from fim_one.core.security.encryption import decrypt_credential

        plaintext_row = '{"default_token": "xyz"}'
        result = decrypt_credential(plaintext_row)
        assert result == {"default_token": "xyz"}

    def test_empty_dict(self) -> None:
        """encrypt_credential({}) round-trips cleanly to {}."""
        from fim_one.core.security.encryption import decrypt_credential, encrypt_credential

        result = encrypt_credential({})
        assert decrypt_credential(result) == {}

    def test_encrypted_different_ciphertexts_each_call(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Fernet uses a random IV — two encryptions of the same blob differ."""
        import fim_one.core.security.encryption as enc

        monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", "iv-uniqueness-test-key")
        enc._cred_fernet_instance = None

        from fim_one.core.security.encryption import encrypt_credential

        blob = {"default_token": "same-value"}
        c1 = encrypt_credential(blob)
        c2 = encrypt_credential(blob)
        assert c1 != c2

    def test_decrypt_returns_empty_dict_on_corrupt_token(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """decrypt_credential returns {} gracefully when Fernet token is corrupt."""
        import fim_one.core.security.encryption as enc

        monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", "corrupt-token-test-key")
        enc._cred_fernet_instance = None

        from fim_one.core.security.encryption import decrypt_credential

        # A non-JSON string that does not start with '{' will trigger Fernet path
        result = decrypt_credential("not-a-valid-fernet-token")
        assert result == {}


# ---------------------------------------------------------------------------
# Group 2: _split_auth_config
# ---------------------------------------------------------------------------


class TestSplitAuthConfig:
    """Tests for the _split_auth_config helper in connectors API."""

    def test_bearer_splits_correctly(self) -> None:
        """Bearer auth: token_prefix stays in clean_config, default_token goes to cred_blob."""
        from fim_one.web.api.connectors import _split_auth_config

        config = {"token_prefix": "Bearer", "default_token": "secret"}
        clean, blob = _split_auth_config("bearer", config)

        assert "token_prefix" in clean
        assert "default_token" not in clean
        assert blob["default_token"] == "secret"
        assert "token_prefix" not in blob

    def test_api_key_splits_correctly(self) -> None:
        """API key auth: header_name stays in clean, default_api_key goes to blob."""
        from fim_one.web.api.connectors import _split_auth_config

        config = {"header_name": "X-Key", "default_api_key": "mykey"}
        clean, blob = _split_auth_config("api_key", config)

        assert clean["header_name"] == "X-Key"
        assert "default_api_key" not in clean
        assert blob["default_api_key"] == "mykey"

    def test_basic_splits_correctly(self) -> None:
        """Basic auth: both username and password are sensitive and go to blob; clean is empty."""
        from fim_one.web.api.connectors import _split_auth_config

        config = {"default_username": "user", "default_password": "pass"}
        clean, blob = _split_auth_config("basic", config)

        assert clean == {}
        assert blob["default_username"] == "user"
        assert blob["default_password"] == "pass"

    def test_none_auth_type(self) -> None:
        """'none' auth type returns two empty dicts regardless of config."""
        from fim_one.web.api.connectors import _split_auth_config

        clean, blob = _split_auth_config("none", None)
        assert clean == {}
        assert blob == {}

    def test_empty_token_not_in_blob(self) -> None:
        """Falsy sensitive values are excluded from the credential blob."""
        from fim_one.web.api.connectors import _split_auth_config

        config = {"token_prefix": "Bearer", "default_token": ""}
        clean, blob = _split_auth_config("bearer", config)

        assert "token_prefix" in clean
        assert blob == {}

    def test_none_auth_config(self) -> None:
        """None auth_config always returns ({}, {})."""
        from fim_one.web.api.connectors import _split_auth_config

        clean, blob = _split_auth_config("bearer", None)
        assert clean == {}
        assert blob == {}

    def test_unknown_auth_type_passes_all_to_clean(self) -> None:
        """Unknown auth type has no sensitive fields; everything stays in clean, blob is empty."""
        from fim_one.web.api.connectors import _split_auth_config

        config = {"custom_field": "val", "other": "data"}
        clean, blob = _split_auth_config("oauth2", config)

        assert clean == config
        assert blob == {}

    def test_bearer_with_only_token_prefix(self) -> None:
        """Bearer config with only non-sensitive fields: clean gets it all, blob is empty."""
        from fim_one.web.api.connectors import _split_auth_config

        config = {"token_prefix": "Token"}
        clean, blob = _split_auth_config("bearer", config)

        assert clean["token_prefix"] == "Token"
        assert blob == {}


# ---------------------------------------------------------------------------
# Group 3: _strip_sensitive_auth_config
# ---------------------------------------------------------------------------


class TestStripSensitiveAuthConfig:
    """Tests for the _strip_sensitive_auth_config helper in connectors API."""

    def test_bearer_strips_token(self) -> None:
        """Bearer auth: default_token is removed, token_prefix is preserved."""
        from fim_one.web.api.connectors import _strip_sensitive_auth_config

        config = {"token_prefix": "Bearer", "default_token": "secret"}
        result = _strip_sensitive_auth_config("bearer", config)

        assert result is not None
        assert "token_prefix" in result
        assert "default_token" not in result

    def test_none_config_returns_none(self) -> None:
        """None input is returned as-is (None)."""
        from fim_one.web.api.connectors import _strip_sensitive_auth_config

        result = _strip_sensitive_auth_config("bearer", None)
        assert result is None

    def test_none_auth_type_strips_nothing(self) -> None:
        """'none' auth type has no sensitive fields; all config fields are preserved."""
        from fim_one.web.api.connectors import _strip_sensitive_auth_config

        config = {"some_field": "val"}
        result = _strip_sensitive_auth_config("none", config)
        assert result == {"some_field": "val"}

    def test_basic_strips_both_username_and_password(self) -> None:
        """Basic auth: both default_username and default_password are stripped."""
        from fim_one.web.api.connectors import _strip_sensitive_auth_config

        config = {"default_username": "admin", "default_password": "hunter2"}
        result = _strip_sensitive_auth_config("basic", config)

        assert result is not None
        assert "default_username" not in result
        assert "default_password" not in result

    def test_api_key_strips_key_preserves_header_name(self) -> None:
        """API key auth: default_api_key is stripped, header_name is kept."""
        from fim_one.web.api.connectors import _strip_sensitive_auth_config

        config = {"header_name": "X-API-Key", "default_api_key": "topsecret"}
        result = _strip_sensitive_auth_config("api_key", config)

        assert result is not None
        assert result["header_name"] == "X-API-Key"
        assert "default_api_key" not in result

    def test_does_not_mutate_original_config(self) -> None:
        """The original auth_config dict is not modified in place."""
        from fim_one.web.api.connectors import _strip_sensitive_auth_config

        config = {"token_prefix": "Bearer", "default_token": "secret"}
        original_keys = set(config.keys())
        _strip_sensitive_auth_config("bearer", config)
        assert set(config.keys()) == original_keys

    def test_unknown_auth_type_returns_config_unchanged(self) -> None:
        """Unknown auth types have no defined sensitive fields; config passes through."""
        from fim_one.web.api.connectors import _strip_sensitive_auth_config

        config = {"custom_header": "X-Custom", "custom_value": "data"}
        result = _strip_sensitive_auth_config("oauth2", config)
        assert result == config
