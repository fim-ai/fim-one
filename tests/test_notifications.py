"""Tests for :mod:`fim_one.web.notifications` — per-agent completion card.

These tests exercise the no-op branches (missing / malformed config),
the defensive branches (channel not found / inactive / wrong org), and
the happy path where a Feishu interactive card is built and POSTed.

Card rendering is now a Feishu concern, so the card-structure tests
drive ``FeishuChannel.send_completion`` directly and inspect the JSON
body that would have been POSTed.  ``notify_agent_completion`` itself
stays channel-agnostic.

Outbound HTTP is mocked with ``patch("httpx.AsyncClient")`` — same
pattern as :mod:`tests.test_feishu_channel`.  respx is NOT a project
dependency.
"""

from __future__ import annotations

import json
import logging
import uuid
from types import SimpleNamespace
from typing import Any, AsyncIterator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import fim_one.db.models  # noqa: F401  (register models)
from fim_one.core.channels import CompletionSummary
from fim_one.core.channels.feishu import FeishuChannel, _format_duration
from fim_one.db.base import Base
from fim_one.db.models.channel import Channel
from fim_one.db.models.organization import Organization
from fim_one.db.models.user import User
from fim_one.web.notifications import notify_agent_completion


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _stable_cred_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin the encryption key so EncryptedJSON decrypts across tests."""
    import fim_one.core.security.encryption as enc

    monkeypatch.setenv(
        "CREDENTIAL_ENCRYPTION_KEY",
        "test-notifications-key-abcdefghijklmn",
    )
    enc._CREDENTIAL_KEY_RAW = "test-notifications-key-abcdefghijklmn"
    enc._cred_fernet_instance = None


@pytest.fixture()
async def engine() -> AsyncIterator[Any]:
    eng = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest.fixture()
async def session_factory(
    engine: Any,
) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


@pytest.fixture()
async def seed(
    session_factory: async_sessionmaker[AsyncSession],
) -> dict[str, Any]:
    """Seed a user, org, and an active Feishu channel."""
    async with session_factory() as db:
        user = User(
            id=str(uuid.uuid4()),
            username="ops",
            email="ops@test.com",
            is_admin=False,
        )
        db.add(user)
        org = Organization(
            id=str(uuid.uuid4()),
            name="DemoCo",
            slug=f"democo-{uuid.uuid4().hex[:6]}",
            owner_id=user.id,
        )
        db.add(org)
        channel = Channel(
            id=str(uuid.uuid4()),
            name="Feishu",
            type="feishu",
            org_id=org.id,
            created_by=user.id,
            is_active=True,
            config={
                "app_id": "cli_x",
                "app_secret": "s",
                "chat_id": "oc_group",
            },
        )
        db.add(channel)
        await db.commit()
    return {
        "user_id": user.id,
        "org_id": org.id,
        "channel_id": channel.id,
    }


def _make_agent(
    *,
    org_id: str | None = "org-1",
    enabled: bool = True,
    channel_id: str | None = "chan-1",
    name: str = "Summariser",
) -> SimpleNamespace:
    """Build an agent-shaped SimpleNamespace with optional notifications."""
    notifications: dict[str, Any] = {}
    if enabled is not None or channel_id is not None:
        on_complete: dict[str, Any] = {}
        if enabled is not None:
            on_complete["enabled"] = enabled
        if channel_id is not None:
            on_complete["channel_id"] = channel_id
        notifications["on_complete"] = on_complete
    return SimpleNamespace(
        id="agent-42",
        name=name,
        org_id=org_id,
        model_config_json={"notifications": notifications},
    )


def _install_feishu_http_mock() -> tuple[MagicMock, AsyncMock]:
    """Patch httpx.AsyncClient to capture outbound POSTs.

    Returns ``(mock_async_client, mock_client_instance)`` so the test
    can assert on ``mock_client_instance.post.await_args_list``.
    """
    mock_client_instance = AsyncMock()
    # Token endpoint returns the structure expected by FeishuChannel.
    token_response = MagicMock()
    token_response.json = MagicMock(
        return_value={"code": 0, "tenant_access_token": "t-abc"}
    )
    message_response = MagicMock()
    message_response.json = MagicMock(
        return_value={"code": 0, "msg": "ok", "data": {}}
    )
    # post() is called for BOTH the token fetch and the message send.
    mock_client_instance.post = AsyncMock(
        side_effect=[token_response, message_response]
    )
    mock_client_instance.aclose = AsyncMock()

    mock_async_client = MagicMock()
    mock_async_client.return_value.__aenter__ = AsyncMock(
        return_value=mock_client_instance
    )
    mock_async_client.return_value.__aexit__ = AsyncMock(return_value=None)
    return mock_async_client, mock_client_instance


async def _send_feishu_completion(
    summary: CompletionSummary,
) -> dict[str, Any]:
    """Send a completion via a real FeishuChannel with HTTP mocked.

    Returns the JSON body that would have been POSTed to the Feishu
    message endpoint — specifically the parsed ``content`` field, which
    is the JSON-encoded card.
    """
    channel = FeishuChannel(
        {"app_id": "cli_x", "app_secret": "s", "chat_id": "oc_group"}
    )
    mock_async_client, mock_client_instance = _install_feishu_http_mock()
    with patch("httpx.AsyncClient", mock_async_client):
        result = await channel.send_completion(summary)
    assert result.ok, result.error
    send_call = mock_client_instance.post.await_args_list[1]
    body = send_call.kwargs["json"]
    card = json.loads(body["content"])
    assert isinstance(card, dict)
    return card


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------


class TestFormatDuration:
    def test_sub_minute(self) -> None:
        assert _format_duration(0.4) == "0.4s"
        assert _format_duration(2.345) == "2.3s"
        assert _format_duration(59.9) == "59.9s"

    def test_minutes(self) -> None:
        assert _format_duration(60) == "1m 0s"
        assert _format_duration(107) == "1m 47s"

    def test_hours(self) -> None:
        assert _format_duration(3725) == "1h 2m 5s"

    def test_negative_clamped_to_zero(self) -> None:
        assert _format_duration(-1) == "0.0s"


class TestBuildCompletionCard:
    """Card structure is driven through ``FeishuChannel.send_completion``.

    Each test builds a ``CompletionSummary``, sends through a real
    ``FeishuChannel`` with HTTP mocked, and inspects the card JSON.
    """

    @pytest.mark.asyncio
    async def test_card_structure(self) -> None:
        summary = CompletionSummary(
            agent_name="Summariser",
            duration_seconds=2.345,
            tools_used=["web_search", "code_sandbox"],
            user_message="Hi there",
            final_answer="Here is the result.",
            conversation_id="conv-1",
            conversation_url=None,
        )
        card = await _send_feishu_completion(summary)

        assert card["schema"] == "2.0"
        assert card["header"]["template"] == "green"
        assert "Summariser" in card["header"]["title"]["content"]
        md_blocks = [
            el["content"]
            for el in card["body"]["elements"]
            if el.get("tag") == "markdown"
        ]
        column_sets = [
            el
            for el in card["body"]["elements"]
            if el.get("tag") == "column_set"
        ]
        for cs in column_sets:
            for col in cs["columns"]:
                for el in col["elements"]:
                    if el.get("tag") == "markdown":
                        md_blocks.append(el["content"])
        joined = "\n".join(md_blocks)
        assert "2.3s" in joined
        assert "web_search" in joined
        assert "code_sandbox" in joined
        assert "Hi there" in joined
        assert "Here is the result." in joined

    @pytest.mark.asyncio
    async def test_tools_truncated(self) -> None:
        tools = [f"tool_{i}" for i in range(10)]
        summary = CompletionSummary(
            agent_name="X",
            duration_seconds=1.0,
            tools_used=tools,
            user_message="",
            final_answer="",
            conversation_id=None,
            conversation_url=None,
        )
        card = await _send_feishu_completion(summary)
        md = "\n".join(
            el["content"]
            for cs in card["body"]["elements"]
            if cs.get("tag") == "column_set"
            for col in cs["columns"]
            for el in col["elements"]
            if el.get("tag") == "markdown"
        )
        assert "tool_0" in md
        assert "tool_5" in md
        # Tool index 6 and beyond must NOT appear inline.
        assert "tool_6" not in md
        assert "…" in md

    @pytest.mark.asyncio
    async def test_long_answer_truncated(self) -> None:
        long = "x" * 2000
        summary = CompletionSummary(
            agent_name="X",
            duration_seconds=1.0,
            tools_used=[],
            user_message="",
            final_answer=long,
            conversation_id=None,
            conversation_url=None,
        )
        card = await _send_feishu_completion(summary)
        md = [
            el["content"]
            for el in card["body"]["elements"]
            if el.get("tag") == "markdown"
        ]
        joined = "\n".join(md)
        # 600-char truncation plus ellipsis — total body content must be
        # much smaller than the raw input.
        assert len(joined) < len(long)
        assert "…" in joined


class TestFeishuSendCompletionGuards:
    """Channel-level guard: missing chat_id should surface as a non-OK result."""

    @pytest.mark.asyncio
    async def test_missing_chat_id_returns_error(self) -> None:
        channel = FeishuChannel({"app_id": "cli_x", "app_secret": "s"})
        summary = CompletionSummary(
            agent_name="A",
            duration_seconds=1.0,
            tools_used=[],
            user_message="u",
            final_answer="a",
            conversation_id=None,
            conversation_url=None,
        )
        with patch("httpx.AsyncClient") as mock_client:
            result = await channel.send_completion(summary)
        mock_client.assert_not_called()
        assert result.ok is False
        assert result.error is not None
        assert "chat_id" in result.error


# ---------------------------------------------------------------------------
# Skip branches
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_skip_when_notifications_missing(
    session_factory: async_sessionmaker[AsyncSession],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """``model_config_json`` has no ``notifications`` → no-op, no logs."""
    agent = SimpleNamespace(
        id="a", name="n", org_id="o", model_config_json={}
    )
    with patch("httpx.AsyncClient") as mock_client:
        with caplog.at_level(logging.WARNING):
            await notify_agent_completion(
                agent=agent,
                conversation_id="c",
                user_message="q",
                final_answer="a",
                tools_used=[],
                duration_seconds=1.0,
                session_factory=session_factory,
            )
    # No HTTP call, no warning logs.
    mock_client.assert_not_called()
    assert not any(
        "completion notification" in r.message.lower() for r in caplog.records
    )


@pytest.mark.asyncio
async def test_skip_when_disabled(
    session_factory: async_sessionmaker[AsyncSession],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """``enabled=false`` → silent no-op."""
    agent = _make_agent(enabled=False, channel_id="whatever")
    with patch("httpx.AsyncClient") as mock_client:
        with caplog.at_level(logging.WARNING):
            await notify_agent_completion(
                agent=agent,
                conversation_id="c",
                user_message="q",
                final_answer="a",
                tools_used=[],
                duration_seconds=1.0,
                session_factory=session_factory,
            )
    mock_client.assert_not_called()
    assert not any(
        "completion notification" in r.message.lower() for r in caplog.records
    )


@pytest.mark.asyncio
async def test_skip_when_channel_id_missing(
    session_factory: async_sessionmaker[AsyncSession],
    caplog: pytest.LogCaptureFixture,
) -> None:
    agent = SimpleNamespace(
        id="a",
        name="n",
        org_id="o",
        model_config_json={
            "notifications": {"on_complete": {"enabled": True}}
        },
    )
    with patch("httpx.AsyncClient") as mock_client:
        with caplog.at_level(logging.WARNING):
            await notify_agent_completion(
                agent=agent,
                conversation_id="c",
                user_message="q",
                final_answer="a",
                tools_used=[],
                duration_seconds=1.0,
                session_factory=session_factory,
            )
    mock_client.assert_not_called()
    assert any(
        "no channel_id" in r.message for r in caplog.records
    )


@pytest.mark.asyncio
async def test_skip_when_channel_not_found(
    session_factory: async_sessionmaker[AsyncSession],
    caplog: pytest.LogCaptureFixture,
) -> None:
    agent = _make_agent(channel_id=str(uuid.uuid4()))
    with patch("httpx.AsyncClient") as mock_client:
        with caplog.at_level(logging.WARNING):
            await notify_agent_completion(
                agent=agent,
                conversation_id="c",
                user_message="q",
                final_answer="a",
                tools_used=[],
                duration_seconds=1.0,
                session_factory=session_factory,
            )
    mock_client.assert_not_called()
    assert any("not found" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_skip_when_channel_inactive(
    session_factory: async_sessionmaker[AsyncSession],
    seed: dict[str, Any],
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Flip the seeded channel inactive.
    async with session_factory() as db:
        ch = await db.get(Channel, seed["channel_id"])
        assert ch is not None
        ch.is_active = False
        await db.commit()

    agent = _make_agent(org_id=seed["org_id"], channel_id=seed["channel_id"])
    with patch("httpx.AsyncClient") as mock_client:
        with caplog.at_level(logging.WARNING):
            await notify_agent_completion(
                agent=agent,
                conversation_id="c",
                user_message="q",
                final_answer="a",
                tools_used=[],
                duration_seconds=1.0,
                session_factory=session_factory,
            )
    mock_client.assert_not_called()
    assert any("inactive" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_skip_when_org_mismatch(
    session_factory: async_sessionmaker[AsyncSession],
    seed: dict[str, Any],
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Agent org differs from channel org.
    agent = _make_agent(org_id="other-org", channel_id=seed["channel_id"])
    with patch("httpx.AsyncClient") as mock_client:
        with caplog.at_level(logging.WARNING):
            await notify_agent_completion(
                agent=agent,
                conversation_id="c",
                user_message="q",
                final_answer="a",
                tools_used=[],
                duration_seconds=1.0,
                session_factory=session_factory,
            )
    mock_client.assert_not_called()
    assert any("does not match agent org_id" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# Happy path and failure-tolerance
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_successful_notification(
    session_factory: async_sessionmaker[AsyncSession],
    seed: dict[str, Any],
) -> None:
    agent = _make_agent(org_id=seed["org_id"], channel_id=seed["channel_id"])
    mock_async_client, mock_client_instance = _install_feishu_http_mock()
    with patch("httpx.AsyncClient", mock_async_client):
        await notify_agent_completion(
            agent=agent,
            conversation_id="conv-xyz",
            user_message="Run the pipeline",
            final_answer="Done — 42 items processed.",
            tools_used=["web_search", "code_sandbox"],
            duration_seconds=5.5,
            session_factory=session_factory,
        )

    # Two POSTs expected: token fetch + message send.
    assert mock_client_instance.post.await_count == 2
    send_call = mock_client_instance.post.await_args_list[1]
    # URL should be the Feishu message endpoint.
    url = send_call.args[0]
    assert url.endswith("/open-apis/im/v1/messages")
    # Body is passed as json=.
    body = send_call.kwargs["json"]
    assert body["receive_id"] == "oc_group"
    assert body["msg_type"] == "interactive"
    # Card content is JSON-encoded into the ``content`` field.
    assert "Summariser" in body["content"]
    assert "web_search" in body["content"]
    assert "Done — 42 items processed." in body["content"]


@pytest.mark.asyncio
async def test_http_failure_does_not_raise(
    session_factory: async_sessionmaker[AsyncSession],
    seed: dict[str, Any],
    caplog: pytest.LogCaptureFixture,
) -> None:
    agent = _make_agent(org_id=seed["org_id"], channel_id=seed["channel_id"])

    # Token fetch succeeds; message send returns a Feishu error code.
    mock_client_instance = AsyncMock()
    token_response = MagicMock()
    token_response.json = MagicMock(
        return_value={"code": 0, "tenant_access_token": "t-abc"}
    )
    message_response = MagicMock()
    message_response.json = MagicMock(
        return_value={"code": 500, "msg": "internal error"}
    )
    mock_client_instance.post = AsyncMock(
        side_effect=[token_response, message_response]
    )
    mock_client_instance.aclose = AsyncMock()
    mock_async_client = MagicMock()
    mock_async_client.return_value.__aenter__ = AsyncMock(
        return_value=mock_client_instance
    )
    mock_async_client.return_value.__aexit__ = AsyncMock(return_value=None)

    with patch("httpx.AsyncClient", mock_async_client):
        with caplog.at_level(logging.WARNING):
            # Must not raise even though the API reported an error.
            await notify_agent_completion(
                agent=agent,
                conversation_id="c",
                user_message="q",
                final_answer="a",
                tools_used=[],
                duration_seconds=1.0,
                session_factory=session_factory,
            )
    assert any(
        "send failed" in r.message for r in caplog.records
    )


@pytest.mark.asyncio
async def test_unknown_channel_type_is_skipped(
    session_factory: async_sessionmaker[AsyncSession],
    seed: dict[str, Any],
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Mutate the seeded channel to a type without an adapter.
    async with session_factory() as db:
        ch = await db.get(Channel, seed["channel_id"])
        assert ch is not None
        ch.type = "made_up_provider"
        await db.commit()

    agent = _make_agent(org_id=seed["org_id"], channel_id=seed["channel_id"])
    with patch("httpx.AsyncClient") as mock_client:
        with caplog.at_level(logging.WARNING):
            await notify_agent_completion(
                agent=agent,
                conversation_id="c",
                user_message="q",
                final_answer="a",
                tools_used=[],
                duration_seconds=1.0,
                session_factory=session_factory,
            )
    mock_client.assert_not_called()
    assert any(
        "No channel adapter" in r.message for r in caplog.records
    )
