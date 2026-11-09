"""Regression tests for ConnectorToolAdapter._inject_auth credential resolution.

Guards against field-name drift between the encrypted credential blob
(``default_token``/``default_api_key``/``default_username``/``default_password``)
and the adapter's lookup logic.  Historical bug: adapter read ``creds["token"]``
while the blob stored the value under ``default_token``, producing an empty
``Authorization`` header and surprise 401s from the target service.
"""

from __future__ import annotations

from typing import Any

import pytest

from fim_one.core.tool.connector.adapter import ConnectorToolAdapter


def _make_adapter(
    auth_type: str,
    auth_config: dict[str, Any] | None,
    auth_credentials: dict[str, str] | None,
) -> ConnectorToolAdapter:
    return ConnectorToolAdapter(
        connector_name="github",
        connector_base_url="https://api.example.com",
        connector_auth_type=auth_type,
        connector_auth_config=auth_config,
        action_name="list_repos",
        action_description="list",
        action_method="GET",
        action_path="/user/repos",
        action_parameters_schema=None,
        action_request_body_template=None,
        action_response_extract=None,
        action_requires_confirmation=False,
        auth_credentials=auth_credentials,
        on_call_complete=None,
    )


class TestBearerAuthInjection:
    def test_reads_default_token_from_decrypted_creds(self) -> None:
        adapter = _make_adapter(
            auth_type="bearer",
            auth_config={"token_prefix": "Bearer"},
            auth_credentials={"default_token": "ghp_secret_abc"},
        )
        headers: dict[str, str] = {}
        adapter._inject_auth(headers)
        assert headers["Authorization"] == "Bearer ghp_secret_abc"

    def test_legacy_token_key_still_honoured(self) -> None:
        adapter = _make_adapter(
            auth_type="bearer",
            auth_config={"token_prefix": "Bearer"},
            auth_credentials={"token": "legacy_xyz"},
        )
        headers: dict[str, str] = {}
        adapter._inject_auth(headers)
        assert headers["Authorization"] == "Bearer legacy_xyz"

    @pytest.mark.parametrize(
        ("auth_type", "auth_config"),
        [
            ("bearer", {"token_prefix": "Bearer", "default_token": "config_tok"}),
            ("api_key", {"header_name": "X-Key", "default_api_key": "config_key"}),
            ("basic", {"default_username": "u", "default_password": "p"}),
        ],
    )
    def test_a_secret_in_auth_config_is_not_read(
        self, auth_type: str, auth_config: dict[str, Any]
    ) -> None:
        """The adapter sends what was resolved for the caller and nothing else.

        It used to fall back to a secret left in ``auth_config``, whoever the
        caller was and whatever the owner's fallback switch said.
        """
        adapter = _make_adapter(auth_type=auth_type, auth_config=auth_config, auth_credentials={})
        headers: dict[str, str] = {}
        adapter._inject_auth(headers)
        assert headers == {}

    def test_no_header_when_all_sources_empty(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        adapter = _make_adapter(
            auth_type="bearer",
            auth_config={"token_prefix": "Bearer"},
            auth_credentials={},
        )
        headers: dict[str, str] = {}
        with caplog.at_level("WARNING"):
            adapter._inject_auth(headers)
        assert "Authorization" not in headers
        assert any(
            "no resolvable token" in rec.message for rec in caplog.records
        ), "missing token should emit a WARNING so this failure mode is not silent"

    def test_custom_prefix_respected(self) -> None:
        adapter = _make_adapter(
            auth_type="bearer",
            auth_config={"token_prefix": "token"},
            auth_credentials={"default_token": "ghp_x"},
        )
        headers: dict[str, str] = {}
        adapter._inject_auth(headers)
        assert headers["Authorization"] == "token ghp_x"


class TestApiKeyAuthInjection:
    def test_reads_default_api_key_from_creds(self) -> None:
        adapter = _make_adapter(
            auth_type="api_key",
            auth_config={"header_name": "X-API-Key"},
            auth_credentials={"default_api_key": "sk-live-123"},
        )
        headers: dict[str, str] = {}
        adapter._inject_auth(headers)
        assert headers["X-API-Key"] == "sk-live-123"

    def test_legacy_api_key_name_still_honoured(self) -> None:
        adapter = _make_adapter(
            auth_type="api_key",
            auth_config={"header_name": "X-API-Key"},
            auth_credentials={"api_key": "old-key"},
        )
        headers: dict[str, str] = {}
        adapter._inject_auth(headers)
        assert headers["X-API-Key"] == "old-key"


class TestBasicAuthInjection:
    def test_reads_default_username_password_from_creds(self) -> None:
        adapter = _make_adapter(
            auth_type="basic",
            auth_config={},
            auth_credentials={
                "default_username": "alice",
                "default_password": "s3cret",
            },
        )
        headers: dict[str, str] = {}
        adapter._inject_auth(headers)
        # Basic YWxpY2U6czNjcmV0 == base64("alice:s3cret")
        assert headers["Authorization"] == "Basic YWxpY2U6czNjcmV0"

    def test_legacy_username_password_names_honoured(self) -> None:
        adapter = _make_adapter(
            auth_type="basic",
            auth_config={},
            auth_credentials={"username": "alice", "password": "s3cret"},
        )
        headers: dict[str, str] = {}
        adapter._inject_auth(headers)
        assert headers["Authorization"] == "Basic YWxpY2U6czNjcmV0"
