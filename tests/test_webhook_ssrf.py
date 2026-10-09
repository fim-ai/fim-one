"""Workflow webhook delivery must resolve and check the target at send time.

The webhook URL is validated when it is saved, but DNS can change between
then and delivery. Delivery therefore has to go through the SSRF-safe
transport, which re-resolves and refuses private addresses on every request.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Iterator
from typing import Any
from unittest.mock import patch

import httpx
import pytest

from fim_one.core.workflow.scheduler import WorkflowScheduler
from fim_one.web.api.workflows import _deliver_webhook

_REBOUND = [(2, 1, 6, "", ("169.254.169.254", 0))]
_PUBLIC = [(2, 1, 6, "", ("93.184.216.34", 0))]


@pytest.fixture
def sent() -> list[httpx.Request]:
    return []


@pytest.fixture
def fake_network(sent: list[httpx.Request]) -> Iterator[None]:
    async def fake_send(
        self: httpx.AsyncHTTPTransport, request: httpx.Request
    ) -> httpx.Response:
        sent.append(request)
        return httpx.Response(200)

    with patch.object(httpx.AsyncHTTPTransport, "handle_async_request", fake_send):
        yield


_DELIVERERS = [
    pytest.param(_deliver_webhook, id="api"),
    pytest.param(WorkflowScheduler._deliver_webhook, id="scheduler"),
]


class TestWebhookDeliverySSRF:
    @pytest.mark.parametrize("deliver", _DELIVERERS)
    async def test_rebound_dns_is_not_contacted(
        self,
        deliver: Callable[[str, dict[str, Any]], Awaitable[None]],
        sent: list[httpx.Request],
        fake_network: None,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        with patch("fim_one.core.security.ssrf.socket.getaddrinfo", return_value=_REBOUND):
            with caplog.at_level(logging.ERROR):
                await deliver("http://rebind.example/callback", {"event": "x"})
        assert sent == []
        assert "SSRF blocked" in caplog.text

    @pytest.mark.parametrize("deliver", _DELIVERERS)
    async def test_public_target_is_delivered_to_pinned_ip(
        self,
        deliver: Callable[[str, dict[str, Any]], Awaitable[None]],
        sent: list[httpx.Request],
        fake_network: None,
    ) -> None:
        with patch("fim_one.core.security.ssrf.socket.getaddrinfo", return_value=_PUBLIC):
            await deliver("http://hooks.example/callback", {"event": "x"})
        assert len(sent) == 1
        assert sent[0].url.host == "93.184.216.34"
        assert sent[0].headers["host"] == "hooks.example"


class TestWebhookUrlValidation:
    def test_alibaba_metadata_rejected_at_save_time(self) -> None:
        from fim_one.web.api.workflows import _validate_webhook_url
        from fim_one.web.exceptions import AppError

        with patch(
            "fim_one.web.api.workflows.socket.getaddrinfo",
            return_value=[(2, 1, 6, "", ("100.100.100.200", 0))],
        ):
            with pytest.raises(AppError):
                _validate_webhook_url("http://meta.example/")
