"""A subscriber's MCP credential must fit the transport before it replaces
the owner's.

With ``allow_fallback`` off, a user who is not the server's owner connects
with their own credential or not at all.  The credential counts only when it
supplies what the transport reads (stdio: env; SSE / HTTP: headers).  Before
the fix an ``env``-only personal credential on an HTTP server passed the
gate in chat and in the workflow MCP node, and the connection went out with
the owner's headers.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from fim_one.core.assembly.credentials import (
    must_bring_own_credential,
    personal_credential_fits,
)
from fim_one.core.assembly.principal import CallContext, provisional_principal
from fim_one.core.workflow.nodes import MCPExecutor
from fim_one.core.workflow.types import (
    ExecutionContext,
    NodeStatus,
    NodeType,
    WorkflowNodeDef,
)
from fim_one.core.workflow.variable_store import VariableStore

OWNER = "owner-1"
SUBSCRIBER = "user-1"
OWNER_HEADERS = {"Authorization": "Bearer owner-token", "X-Tenant": "owner-tenant"}
OWNER_ENV = {"API_KEY": "owner-key", "REGION": "owner-region"}


def _server(
    *,
    transport: str = "streamable_http",
    allow_fallback: bool = False,
    owner: str = OWNER,
) -> SimpleNamespace:
    return SimpleNamespace(
        id="srv-1",
        name="shared-server",
        transport=transport,
        command="npx" if transport == "stdio" else None,
        args=[],
        env=dict(OWNER_ENV),
        url=None if transport == "stdio" else "https://mcp.example.com/mcp",
        working_dir=None,
        headers=dict(OWNER_HEADERS),
        is_active=True,
        allow_fallback=allow_fallback,
        user_id=owner,
    )


def _cred(
    env: dict[str, str] | None = None, headers: dict[str, str] | None = None
) -> SimpleNamespace:
    return SimpleNamespace(env_blob=env, headers_blob=headers)


def _client() -> AsyncMock:
    client = AsyncMock()
    client.connect_stdio = AsyncMock(return_value=[])
    client.connect_sse = AsyncMock(return_value=[])
    client.connect_streamable_http = AsyncMock(return_value=[])
    client.disconnect_all = AsyncMock()
    return client


# ---------------------------------------------------------------------------
# The rule itself
# ---------------------------------------------------------------------------


class TestPersonalCredentialFits:
    @pytest.mark.parametrize(
        ("transport", "env", "headers", "expected"),
        [
            ("stdio", {"K": "v"}, None, True),
            ("stdio", None, {"H": "v"}, False),
            ("stdio", {}, {}, False),
            ("sse", None, {"H": "v"}, True),
            ("sse", {"K": "v"}, None, False),
            ("streamable_http", {"K": "v"}, {"H": "v"}, True),
            ("streamable_http", {"K": "v"}, None, False),
            ("streamable_http", {"K": "v"}, {}, False),
            ("websocket", {"K": "v"}, {"H": "v"}, False),
            (None, {"K": "v"}, {"H": "v"}, False),
        ],
    )
    def test_table(
        self,
        transport: str | None,
        env: dict[str, str] | None,
        headers: dict[str, str] | None,
        expected: bool,
    ) -> None:
        assert personal_credential_fits(transport, env, headers) is expected


class TestMustBringOwnCredential:
    def test_fallback_off_and_not_owner(self) -> None:
        assert must_bring_own_credential(_server(), SUBSCRIBER) is True

    def test_owner_is_exempt(self) -> None:
        assert must_bring_own_credential(_server(), OWNER) is False

    def test_fallback_on(self) -> None:
        assert must_bring_own_credential(_server(allow_fallback=True), SUBSCRIBER) is False

    def test_no_identity_is_not_the_owner(self) -> None:
        assert must_bring_own_credential(_server(), None) is True
        assert must_bring_own_credential(_server(owner=""), "") is True


# ---------------------------------------------------------------------------
# Chat: _connect_pending_mcp_servers
# ---------------------------------------------------------------------------


def _cred_session(cred: Any = None, *, raises: bool = False) -> AsyncMock:
    session = AsyncMock()
    if raises:
        session.execute = AsyncMock(side_effect=RuntimeError("db down"))
    else:
        result = MagicMock()
        result.scalar_one_or_none.return_value = cred
        session.execute = AsyncMock(return_value=result)
    cm = AsyncMock()
    cm.__aenter__ = AsyncMock(return_value=session)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm


async def _chat_connect(
    server: Any,
    cred: Any,
    *,
    user_id: str | None = SUBSCRIBER,
    lookup_raises: bool = False,
) -> AsyncMock:
    """Run chat's MCP connect step for one pending server; return the client."""
    from fim_one.web.api.chat import _connect_pending_mcp_servers

    tools: Any = SimpleNamespace(
        _pending_mcp_servers=[server],
        _mcp_call_context=CallContext(
            principal=provisional_principal(user_id or ""), binding=None, entry="chat"
        ),
        register=MagicMock(),
        list_tools=lambda: [],
    )
    client = _client()
    with (
        patch(
            "fim_one.db.create_session",
            return_value=_cred_session(cred, raises=lookup_raises),
        ),
        patch("fim_one.core.mcp.MCPClient", return_value=client),
        patch("fim_one.core.security.is_stdio_allowed", return_value=True),
    ):
        await _connect_pending_mcp_servers(
            tools,
            {"model_config_json": {"mcp_tool_mode": "legacy"}},
        )
    return client


class TestChatFallbackOff:
    @pytest.mark.parametrize("transport", ["streamable_http", "sse"])
    async def test_env_only_credential_on_http_server_is_refused(self, transport: str) -> None:
        """N1: the owner's headers went out with this connection."""
        client = await _chat_connect(_server(transport=transport), _cred(env={"ANYTHING": "x"}))

        client.connect_streamable_http.assert_not_awaited()
        client.connect_sse.assert_not_awaited()

    async def test_headers_only_credential_on_stdio_server_is_refused(self) -> None:
        client = await _chat_connect(
            _server(transport="stdio"), _cred(headers={"Authorization": "Bearer mine"})
        )

        client.connect_stdio.assert_not_awaited()

    async def test_own_headers_connect_without_the_owners(self) -> None:
        own = {"Authorization": "Bearer mine"}
        client = await _chat_connect(_server(), _cred(headers=own))

        client.connect_streamable_http.assert_awaited_once()
        assert client.connect_streamable_http.await_args.kwargs["headers"] == own

    async def test_own_env_connects_stdio_without_the_owners(self) -> None:
        own = {"API_KEY": "mine"}
        client = await _chat_connect(_server(transport="stdio"), _cred(env=own))

        client.connect_stdio.assert_awaited_once()
        assert client.connect_stdio.await_args.kwargs["env"] == own

    async def test_no_credential_is_refused(self) -> None:
        client = await _chat_connect(_server(), None)

        client.connect_streamable_http.assert_not_awaited()

    async def test_failed_lookup_is_refused(self) -> None:
        """The lookup error used to fall through to the owner's credentials."""
        client = await _chat_connect(_server(), None, lookup_raises=True)

        client.connect_streamable_http.assert_not_awaited()

    async def test_no_caller_identity_is_refused(self) -> None:
        client = await _chat_connect(_server(), None, user_id=None)

        client.connect_streamable_http.assert_not_awaited()


class TestChatOwnerAndFallbackOn:
    async def test_owner_uses_server_credentials(self) -> None:
        client = await _chat_connect(_server(), None, user_id=OWNER)

        assert client.connect_streamable_http.await_args.kwargs["headers"] == OWNER_HEADERS

    async def test_owner_with_env_only_credential_keeps_own_headers(self) -> None:
        client = await _chat_connect(_server(), _cred(env={"ANYTHING": "x"}), user_id=OWNER)

        assert client.connect_streamable_http.await_args.kwargs["headers"] == OWNER_HEADERS

    async def test_a_failed_lookup_refuses_the_owner_too(self) -> None:
        """Their saved credential, if any, would have replaced the server's."""
        client = await _chat_connect(_server(), None, user_id=OWNER, lookup_raises=True)

        client.connect_streamable_http.assert_not_awaited()

    async def test_fallback_on_without_credential_uses_the_owners(self) -> None:
        client = await _chat_connect(_server(allow_fallback=True), None)

        assert client.connect_streamable_http.await_args.kwargs["headers"] == OWNER_HEADERS

    async def test_fallback_on_with_env_only_credential_uses_owner_headers(self) -> None:
        client = await _chat_connect(_server(allow_fallback=True), _cred(env={"ANYTHING": "x"}))

        assert client.connect_streamable_http.await_args.kwargs["headers"] == OWNER_HEADERS

    async def test_fallback_on_personal_headers_replace_owner_headers(self) -> None:
        own = {"Authorization": "Bearer mine"}
        client = await _chat_connect(_server(allow_fallback=True), _cred(headers=own))

        assert client.connect_streamable_http.await_args.kwargs["headers"] == own

    async def test_owner_personal_headers_replace_server_headers(self) -> None:
        own = {"Authorization": "Bearer mine"}
        client = await _chat_connect(_server(), _cred(headers=own), user_id=OWNER)

        assert client.connect_streamable_http.await_args.kwargs["headers"] == own

    async def test_fallback_on_stdio_personal_env_replaces_owner_env(self) -> None:
        own = {"API_KEY": "mine"}
        client = await _chat_connect(
            _server(transport="stdio", allow_fallback=True), _cred(env=own)
        )

        assert client.connect_stdio.await_args.kwargs["env"] == own

    async def test_fallback_on_does_not_cover_a_failed_lookup(self) -> None:
        """The lookup error fell through to the owner's credentials (B4)."""
        client = await _chat_connect(_server(allow_fallback=True), None, lookup_raises=True)

        client.connect_streamable_http.assert_not_awaited()


# ---------------------------------------------------------------------------
# Workflow MCP node
# ---------------------------------------------------------------------------


@pytest.fixture()
def _passthrough_visibility(monkeypatch: pytest.MonkeyPatch) -> None:
    """Skip the node's visibility queries; these tests are about credentials."""
    from sqlalchemy import true as _sa_true

    async def _fake_resolve(*args: Any, **kwargs: Any) -> tuple[Any, list[str], list[str]]:
        return _sa_true(), [], []

    monkeypatch.setattr("fim_one.core.assembly.visibility.resolve_visibility", _fake_resolve)


def _node_session(server: Any, cred: Any, *, cred_raises: bool = False) -> AsyncMock:
    calls = 0

    def _execute(*args: Any, **kwargs: Any) -> MagicMock:
        nonlocal calls
        calls += 1
        result = MagicMock()
        if calls == 1:
            result.scalar_one_or_none.return_value = server
            return result
        if cred_raises:
            raise RuntimeError("db down")
        result.scalar_one_or_none.return_value = cred
        return result

    session = AsyncMock()
    session.execute = AsyncMock(side_effect=_execute)
    cm = AsyncMock()
    cm.__aenter__ = AsyncMock(return_value=session)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm


async def _run_node(
    server: Any,
    cred: Any,
    *,
    user_id: str = SUBSCRIBER,
    cred_raises: bool = False,
) -> tuple[Any, AsyncMock]:
    tool = MagicMock()
    tool._original_name = "read_file"
    tool.run = AsyncMock(return_value="ok")
    client = _client()
    for connect in (client.connect_stdio, client.connect_sse, client.connect_streamable_http):
        connect.return_value = [tool]
    node = WorkflowNodeDef(
        id="mcp-1",
        type=NodeType.MCP,
        data={"server_id": "srv-1", "tool_name": "read_file", "parameters": {}},
    )
    context = ExecutionContext(run_id="run-1", user_id=user_id, workflow_id="wf-1")
    with (
        patch(
            "fim_one.db.create_session",
            return_value=_node_session(server, cred, cred_raises=cred_raises),
        ),
        patch("fim_one.core.mcp.MCPClient", return_value=client),
        patch("fim_one.core.security.is_stdio_allowed", return_value=True),
    ):
        result = await MCPExecutor().execute(node, VariableStore(), context)
    return result, client


@pytest.mark.usefixtures("_passthrough_visibility")
class TestWorkflowNodeFallbackOff:
    @pytest.mark.parametrize("transport", ["streamable_http", "sse"])
    async def test_env_only_credential_on_http_server_fails_the_node(self, transport: str) -> None:
        """N1 in the workflow node: any non-empty field counted as a credential."""
        result, client = await _run_node(_server(transport=transport), _cred(env={"ANYTHING": "x"}))

        assert result.status == NodeStatus.FAILED
        assert "HTTP headers" in (result.error or "")
        client.connect_streamable_http.assert_not_awaited()
        client.connect_sse.assert_not_awaited()

    async def test_headers_only_credential_on_stdio_server_fails_the_node(self) -> None:
        result, client = await _run_node(
            _server(transport="stdio"), _cred(headers={"Authorization": "Bearer mine"})
        )

        assert result.status == NodeStatus.FAILED
        assert "environment variables" in (result.error or "")
        client.connect_stdio.assert_not_awaited()

    async def test_own_headers_are_not_merged_with_the_owners(self) -> None:
        """The owner's other headers used to ride along with a personal one."""
        own = {"Authorization": "Bearer mine"}
        result, client = await _run_node(_server(), _cred(headers=own))

        assert result.status == NodeStatus.COMPLETED
        assert client.connect_streamable_http.await_args.kwargs["headers"] == own

    async def test_own_env_is_not_merged_with_the_owners(self) -> None:
        own = {"API_KEY": "mine"}
        result, client = await _run_node(_server(transport="stdio"), _cred(env=own))

        assert result.status == NodeStatus.COMPLETED
        assert client.connect_stdio.await_args.kwargs["env"] == own

    async def test_failed_lookup_fails_the_node(self) -> None:
        result, client = await _run_node(_server(), None, cred_raises=True)

        assert result.status == NodeStatus.FAILED
        client.connect_streamable_http.assert_not_awaited()


@pytest.mark.usefixtures("_passthrough_visibility")
class TestWorkflowNodeOwnerAndFallbackOn:
    async def test_owner_uses_server_credentials(self) -> None:
        result, client = await _run_node(_server(), None, user_id=OWNER)

        assert result.status == NodeStatus.COMPLETED
        assert client.connect_streamable_http.await_args.kwargs["headers"] == OWNER_HEADERS

    async def test_owner_credential_replaces_the_server_values(self) -> None:
        """B3: the server's other headers used to ride along (key-by-key merge)."""
        own = {"Authorization": "Bearer other"}
        result, client = await _run_node(_server(), _cred(headers=own), user_id=OWNER)

        assert result.status == NodeStatus.COMPLETED
        assert client.connect_streamable_http.await_args.kwargs["headers"] == own

    async def test_fallback_on_does_not_cover_a_failed_lookup(self) -> None:
        """The node connected with the owner's credentials after the error (B4)."""
        result, client = await _run_node(_server(allow_fallback=True), None, cred_raises=True)

        assert result.status == NodeStatus.FAILED
        assert "could not be loaded" in (result.error or "")
        client.connect_streamable_http.assert_not_awaited()

    async def test_a_failed_lookup_fails_the_node_for_the_owner_too(self) -> None:
        result, client = await _run_node(_server(), None, user_id=OWNER, cred_raises=True)

        assert result.status == NodeStatus.FAILED
        client.connect_streamable_http.assert_not_awaited()

    async def test_fallback_on_without_credential_uses_the_owners(self) -> None:
        result, client = await _run_node(_server(allow_fallback=True), None)

        assert result.status == NodeStatus.COMPLETED
        assert client.connect_streamable_http.await_args.kwargs["headers"] == OWNER_HEADERS

    async def test_fallback_on_credential_replaces_the_server_values(self) -> None:
        """B3: with fallback on, the owner's other variables used to be sent too."""
        own = {"API_KEY": "mine"}
        result, client = await _run_node(
            _server(transport="stdio", allow_fallback=True), _cred(env=own)
        )

        assert result.status == NodeStatus.COMPLETED
        assert client.connect_stdio.await_args.kwargs["env"] == own
