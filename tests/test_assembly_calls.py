"""The call record writer (``core/assembly/calls.py``)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import fim_one.db.models  # noqa: F401
from fim_one.core.assembly.calls import call_recorder
from fim_one.core.assembly.credentials import CredentialSource
from fim_one.core.assembly.principal import Binding, CallContext, provisional_principal
from fim_one.db.base import Base
from fim_one.db.models.connector_call_log import ConnectorCallLog


@pytest.fixture()
async def factory(tmp_path: Path) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'calls.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def _create_session() -> AsyncIterator[AsyncSession]:
        async with maker() as s:
            yield s

    with patch("fim_one.db.create_session", _create_session):
        yield maker
    await engine.dispose()


async def _rows(factory: async_sessionmaker[AsyncSession]) -> list[ConnectorCallLog]:
    async with factory() as s:
        return list((await s.execute(select(ConnectorCallLog))).scalars().all())


def _ctx() -> CallContext:
    return CallContext(
        principal=provisional_principal("u-1"),
        binding=Binding(kind="agent", id="agent-1", owner_id="u-owner"),
        entry="chat",
        conversation_id="conv-1",
        agent_id="agent-1",
    )


async def test_a_connector_call_is_attributed_to_who_made_it_and_where(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    record = call_recorder(
        _ctx(),
        kind="connector",
        credential_source={
            "conn-1": CredentialSource.PERSONAL,
            "conn-2": CredentialSource.OWNER_FALLBACK,
        },
    )

    await record(
        connector_id="conn-1",
        connector_name="crm",
        action_id="act-1",
        action_name="list contacts",
        request_method="GET",
        request_url="https://crm.example.test/contacts",
        response_status=200,
        response_time_ms=12,
        success=True,
        error_message=None,
    )

    (row,) = await _rows(factory)
    assert (row.connector_id, row.connector_name, row.action_id) == ("conn-1", "crm", "act-1")
    assert (row.user_id, row.conversation_id, row.agent_id) == ("u-1", "conv-1", "agent-1")
    assert (row.request_method, row.response_status, row.success) == ("GET", 200, True)
    assert row.scope_rules_applied is None
    # What was called, where the call entered, and whose credential it used.
    assert (row.resource_kind, row.mcp_server_id) == ("connector", None)
    assert (row.entry, row.workflow_run_id) == ("chat", None)
    assert row.credential_source == "personal"


async def test_a_call_through_an_organization_resource_names_the_organization(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    """The agent or workflow can be deleted later; the record still says
    whose organization the call was made through."""
    ctx = CallContext(
        principal=provisional_principal("u-1"),
        binding=Binding(kind="workflow", id="wf-1", owner_id="u-owner", org_id="org-9"),
        entry="workflow_node",
        workflow_run_id="run-1",
    )

    await call_recorder(ctx, kind="mcp")(mcp_server_id="srv-1", action_name="search", success=True)

    (row,) = await _rows(factory)
    assert (row.org_id, row.workflow_run_id, row.user_id) == ("org-9", "run-1", "u-1")


async def test_a_call_with_no_binding_or_a_personal_one_names_no_organization(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    direct = CallContext(principal=provisional_principal("u-1"), binding=None, entry="chat_noagent")

    await call_recorder(direct, kind="connector")(connector_id="c-1", success=True)
    await call_recorder(_ctx(), kind="connector")(connector_id="c-1", success=True)

    assert [row.org_id for row in await _rows(factory)] == [None, None]


async def test_a_database_call_keeps_the_fences_it_ran_under(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    record = call_recorder(_ctx(), kind="database", credential_source=CredentialSource.OWNER)

    await record(
        connector_id="db-1",
        connector_name="warehouse",
        action_name="query",
        request_method="SQL",
        request_url="SELECT 1",
        success=True,
        scope_rules_applied='{"read_only": true}',
    )

    (row,) = await _rows(factory)
    assert row.scope_rules_applied == '{"read_only": true}'
    assert (row.resource_kind, row.credential_source) == ("database", "owner")


async def test_a_record_that_cannot_be_written_does_not_fail_the_call_and_is_reported(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """It was logged at DEBUG: an audit write failing every time was invisible."""

    def _broken() -> AsyncSession:
        raise RuntimeError("db down: INSERT ... 'https://crm.test/contacts?token=SECRET'")

    with (
        patch("fim_one.db.create_session", _broken),
        caplog.at_level("WARNING", logger="fim_one.core.assembly.calls"),
    ):
        await call_recorder(_ctx(), kind="connector")(
            connector_id="c", request_url="https://crm.test/contacts?token=SECRET", success=True
        )

    (warning,) = [r for r in caplog.records if r.levelname == "WARNING"]
    assert warning.getMessage() == "Failed to record a connector call: RuntimeError"
    # The payload is not in the log, by text or by traceback.
    assert "SECRET" not in caplog.text


@pytest.mark.parametrize(
    ("message", "stored"),
    [
        (
            "ConnectError: all attempts failed for https://mcp.example.com/sse?api_key=SECRET&x=1",
            "ConnectError: all attempts failed for https://mcp.example.com/sse",
        ),
        (
            "[MCP Error] GET https://user:SECRET@api.example.com:8443/v1/items#frag returned 500",
            "[MCP Error] GET https://api.example.com:8443/v1/items returned 500",
        ),
        (
            "redirected from 'http://a.test/x?token=SECRET' to (http://b.test/y?token=SECRET)",
            "redirected from 'http://a.test/x' to (http://b.test/y)",
        ),
        ("Request exceeded 30 seconds", "Request exceeded 30 seconds"),
    ],
)
async def test_urls_in_an_error_message_are_stored_without_their_secrets(
    factory: async_sessionmaker[AsyncSession], message: str, stored: str
) -> None:
    """The message of a failed call quotes the URL, query string and all."""
    await call_recorder(_ctx(), kind="mcp")(
        mcp_server_id="srv-1",
        connector_name="files",
        action_name="read_file",
        request_method="MCP",
        request_url="mcp://files/read_file",
        success=False,
        error_message=message,
    )

    (row,) = await _rows(factory)
    assert row.error_message == stored


async def test_the_address_is_stored_without_the_credential_in_its_path(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    """The recorder cleaned only user-info, query string and fragment: a bot
    token in the path was stored as it was."""
    await call_recorder(_ctx(), kind="connector")(
        connector_id="c",
        request_method="POST",
        request_url="https://api.telegram.org/bot123456789:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw"
        "/sendMessage?chat_id=1",
        success=True,
    )

    (row,) = await _rows(factory)
    assert row.request_url == "https://api.telegram.org/***/sendMessage"
