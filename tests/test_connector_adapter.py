"""ConnectorToolAdapter applies an action's ``response_extract``.

The adapter evaluates ``response_extract`` with JMESPath, but ``jmespath``
was not a declared dependency: on a clean install the import failed, the
``ImportError`` was swallowed, and every action returned the raw response
regardless of what the user configured in the UI.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from fim_one.core.tool.connector import adapter as adapter_mod
from fim_one.core.tool.connector.adapter import ConnectorToolAdapter

_PAYLOAD = {"data": {"items": [{"name": "alpha"}, {"name": "beta"}]}, "meta": 1}


@pytest.fixture(autouse=True)
def _mock_http(monkeypatch: pytest.MonkeyPatch) -> None:
    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_PAYLOAD)

    def _client(**kwargs: Any) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(_handler))

    monkeypatch.setattr(adapter_mod, "validate_url", lambda *a, **k: None)
    monkeypatch.setattr(adapter_mod, "get_safe_async_client", _client)


def _adapter(response_extract: str | None) -> ConnectorToolAdapter:
    return ConnectorToolAdapter(
        connector_name="extract-test",
        connector_base_url="https://api.example.com",
        connector_auth_type="none",
        connector_auth_config=None,
        action_name="list_items",
        action_description="list",
        action_method="GET",
        action_path="/items",
        action_parameters_schema=None,
        action_request_body_template=None,
        action_response_extract=response_extract,
        action_requires_confirmation=False,
    )


class TestResponseExtract:
    @pytest.mark.asyncio
    async def test_jmespath_projection_applied(self) -> None:
        result = await _adapter("data.items[].name").run()

        assert json.loads(result) == ["alpha", "beta"]

    @pytest.mark.asyncio
    async def test_scalar_extract_returned_bare(self) -> None:
        result = await _adapter("data.items[0].name").run()

        assert result == "alpha"

    @pytest.mark.asyncio
    async def test_no_extract_returns_raw_body(self) -> None:
        result = await _adapter(None).run()

        assert '"meta"' in result
