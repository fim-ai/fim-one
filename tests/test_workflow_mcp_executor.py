"""Tests for the MCP workflow node executor."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from fim_one.core.workflow.nodes import MCPExecutor
from fim_one.core.workflow.types import (
    ExecutionContext,
    NodeResult,
    NodeStatus,
    NodeType,
    WorkflowNodeDef,
)
from fim_one.core.workflow.variable_store import VariableStore


# ---------------------------------------------------------------------------
# Fake MCP types (mirrors test_mcp.py)
# ---------------------------------------------------------------------------


@dataclass
class FakeTextContent:
    type: str = "text"
    text: str = "tool output"


@dataclass
class FakeCallToolResult:
    content: list[Any] = field(default_factory=lambda: [FakeTextContent()])
    isError: bool = False


# ---------------------------------------------------------------------------
# Fake ORM models
# ---------------------------------------------------------------------------


class FakeMCPServer:
    """Mimics the MCPServer ORM model for testing."""

    def __init__(
        self,
        *,
        id: str = "srv-1",
        name: str = "test-server",
        transport: str = "sse",
        command: str | None = None,
        args: list[str] | None = None,
        env: dict[str, str] | None = None,
        url: str | None = "http://mcp.example.com/sse",
        working_dir: str | None = None,
        headers: dict[str, str] | None = None,
        is_active: bool = True,
    ) -> None:
        self.id = id
        self.name = name
        self.transport = transport
        self.command = command
        self.args = args
        self.env = env
        self.url = url
        self.working_dir = working_dir
        self.headers = headers
        self.is_active = is_active


class FakeMCPToolAdapter:
    """Minimal adapter that mimics MCPToolAdapter for tool lookup."""

    def __init__(self, original_name: str, output: str = "tool output") -> None:
        self._original_name = original_name
        self._output = output
        self.name = f"server__{original_name}"

    async def run(self, **kwargs: Any) -> str:
        return self._output


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def context() -> ExecutionContext:
    return ExecutionContext(
        run_id="run-1",
        user_id="user-1",
        workflow_id="wf-1",
    )


@pytest.fixture()
def store() -> VariableStore:
    return VariableStore()


@pytest.fixture()
def executor() -> MCPExecutor:
    return MCPExecutor()


def _make_node(
    data: dict[str, Any],
    node_id: str = "mcp-node-1",
) -> WorkflowNodeDef:
    return WorkflowNodeDef(
        id=node_id,
        type=NodeType.MCP,
        data=data,
    )


def _mock_db_session(
    server: Any = None,
    credential: Any = None,
) -> AsyncMock:
    """Create a mock DB session context manager.

    The first execute() call returns *server*, subsequent calls return
    *credential* (or None).
    """
    call_count = 0

    def make_result(*args: Any, **kwargs: Any) -> MagicMock:
        nonlocal call_count
        call_count += 1
        r = MagicMock()
        if call_count == 1:
            r.scalar_one_or_none.return_value = server
        else:
            r.scalar_one_or_none.return_value = credential
        return r

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(side_effect=make_result)

    mock_cm = AsyncMock()
    mock_cm.__aenter__ = AsyncMock(return_value=mock_session)
    mock_cm.__aexit__ = AsyncMock(return_value=False)
    return mock_cm


def _mock_mcp_client(
    tools: list[Any] | None = None,
    transport: str = "sse",
) -> AsyncMock:
    """Create a mock MCPClient that returns *tools* on connect."""
    client = AsyncMock()
    tools = tools or []
    if transport == "sse":
        client.connect_sse = AsyncMock(return_value=tools)
    elif transport == "stdio":
        client.connect_stdio = AsyncMock(return_value=tools)
    elif transport == "streamable_http":
        client.connect_streamable_http = AsyncMock(return_value=tools)
    client.disconnect_all = AsyncMock()
    return client


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestMCPExecutor:
    """Tests for the MCP workflow node executor."""

    async def test_missing_server_id(
        self, executor: MCPExecutor, store: VariableStore, context: ExecutionContext
    ) -> None:
        node = _make_node({"tool_name": "read_file", "parameters": {}})
        result = await executor.execute(node, store, context)

        assert result.status == NodeStatus.FAILED
        assert "server_id" in (result.error or "")

    async def test_missing_tool_name(
        self, executor: MCPExecutor, store: VariableStore, context: ExecutionContext
    ) -> None:
        node = _make_node({"server_id": "srv-1", "parameters": {}})
        result = await executor.execute(node, store, context)

        assert result.status == NodeStatus.FAILED
        assert "tool_name" in (result.error or "")

    async def test_server_not_found(
        self, executor: MCPExecutor, store: VariableStore, context: ExecutionContext
    ) -> None:
        """When the MCP server ID doesn't exist in the DB, return FAILED."""
        node = _make_node({
            "server_id": "nonexistent",
            "tool_name": "read_file",
            "parameters": {},
        })

        mock_cm = _mock_db_session(server=None)

        with patch("fim_one.db.create_session", return_value=mock_cm):
            result = await executor.execute(node, store, context)

        assert result.status == NodeStatus.FAILED
        assert "not found" in (result.error or "")

    async def test_server_disabled(
        self, executor: MCPExecutor, store: VariableStore, context: ExecutionContext
    ) -> None:
        """When the MCP server is inactive, return FAILED."""
        node = _make_node({
            "server_id": "srv-1",
            "tool_name": "read_file",
            "parameters": {},
        })

        disabled_server = FakeMCPServer(is_active=False)
        mock_cm = _mock_db_session(server=disabled_server)

        with patch("fim_one.db.create_session", return_value=mock_cm):
            result = await executor.execute(node, store, context)

        assert result.status == NodeStatus.FAILED
        assert "disabled" in (result.error or "")

    async def test_tool_not_found_on_server(
        self, executor: MCPExecutor, store: VariableStore, context: ExecutionContext
    ) -> None:
        """When the requested tool doesn't exist on the server, return FAILED."""
        node = _make_node({
            "server_id": "srv-1",
            "tool_name": "nonexistent_tool",
            "parameters": {},
        })

        server = FakeMCPServer(transport="sse", url="http://mcp.example.com/sse")
        mock_cm = _mock_db_session(server=server)

        available_tool = FakeMCPToolAdapter("other_tool")
        mock_client = _mock_mcp_client(tools=[available_tool], transport="sse")

        with (
            patch("fim_one.db.create_session", return_value=mock_cm),
            patch("fim_one.core.mcp.MCPClient", return_value=mock_client),
        ):
            result = await executor.execute(node, store, context)

        assert result.status == NodeStatus.FAILED
        assert "nonexistent_tool" in (result.error or "")
        assert "other_tool" in (result.error or "")
        mock_client.disconnect_all.assert_awaited_once()

    async def test_parameter_interpolation(
        self, executor: MCPExecutor, store: VariableStore, context: ExecutionContext
    ) -> None:
        """Variables in parameters should be interpolated from the store."""
        await store.set("input.filename", "/tmp/data.csv")
        await store.set("prev_node.output", "some context")

        node = _make_node({
            "server_id": "srv-1",
            "tool_name": "read_file",
            "parameters": {
                "path": "{{input.filename}}",
                "context": "{{prev_node.output}}",
                "literal": "unchanged",
            },
            "output_variable": "file_content",
        })

        server = FakeMCPServer(transport="sse", url="http://mcp.example.com/sse")
        mock_cm = _mock_db_session(server=server)

        # Track the params passed to the tool
        captured_params: dict[str, Any] = {}
        target_tool = FakeMCPToolAdapter("read_file", output="file contents here")

        original_run = target_tool.run

        async def capturing_run(**kwargs: Any) -> str:
            captured_params.update(kwargs)
            return await original_run(**kwargs)

        target_tool.run = capturing_run  # type: ignore[assignment]

        mock_client = _mock_mcp_client(tools=[target_tool], transport="sse")

        with (
            patch("fim_one.db.create_session", return_value=mock_cm),
            patch("fim_one.core.mcp.MCPClient", return_value=mock_client),
        ):
            result = await executor.execute(node, store, context)

        assert result.status == NodeStatus.COMPLETED
        assert captured_params["path"] == "/tmp/data.csv"
        assert captured_params["context"] == "some context"
        assert captured_params["literal"] == "unchanged"

    async def test_successful_execution_stores_output(
        self, executor: MCPExecutor, store: VariableStore, context: ExecutionContext
    ) -> None:
        """Successful tool call stores output in both node.output and output_variable."""
        node = _make_node({
            "server_id": "srv-1",
            "tool_name": "read_file",
            "parameters": {"path": "/tmp/test.txt"},
            "output_variable": "result_var",
        })

        server = FakeMCPServer(transport="sse", url="http://mcp.example.com/sse")
        mock_cm = _mock_db_session(server=server)

        target_tool = FakeMCPToolAdapter("read_file", output="file contents here")
        mock_client = _mock_mcp_client(tools=[target_tool], transport="sse")

        with (
            patch("fim_one.db.create_session", return_value=mock_cm),
            patch("fim_one.core.mcp.MCPClient", return_value=mock_client),
        ):
            result = await executor.execute(node, store, context)

        assert result.status == NodeStatus.COMPLETED
        assert result.duration_ms >= 0

        # Output should be stored under both node namespace and output_variable
        node_output = await store.get("mcp-node-1.output")
        assert node_output == "file contents here"

        var_output = await store.get("result_var")
        assert var_output == "file contents here"

    async def test_successful_execution_without_output_variable(
        self, executor: MCPExecutor, store: VariableStore, context: ExecutionContext
    ) -> None:
        """When output_variable is empty, output is stored only under node namespace."""
        node = _make_node({
            "server_id": "srv-1",
            "tool_name": "read_file",
            "parameters": {},
        })

        server = FakeMCPServer(transport="sse", url="http://mcp.example.com/sse")
        mock_cm = _mock_db_session(server=server)

        target_tool = FakeMCPToolAdapter("read_file", output="data")
        mock_client = _mock_mcp_client(tools=[target_tool], transport="sse")

        with (
            patch("fim_one.db.create_session", return_value=mock_cm),
            patch("fim_one.core.mcp.MCPClient", return_value=mock_client),
        ):
            result = await executor.execute(node, store, context)

        assert result.status == NodeStatus.COMPLETED
        node_output = await store.get("mcp-node-1.output")
        assert node_output == "data"

    async def test_stdio_transport_blocked_when_disabled(
        self, executor: MCPExecutor, store: VariableStore, context: ExecutionContext
    ) -> None:
        """STDIO transport should fail when ALLOW_STDIO_MCP is false."""
        node = _make_node({
            "server_id": "srv-1",
            "tool_name": "read_file",
            "parameters": {},
        })

        server = FakeMCPServer(
            transport="stdio",
            command="npx",
            args=["-y", "@modelcontextprotocol/server-filesystem"],
            url=None,
        )
        mock_cm = _mock_db_session(server=server)

        with (
            patch("fim_one.db.create_session", return_value=mock_cm),
            patch("fim_one.core.security.is_stdio_allowed", return_value=False),
        ):
            result = await executor.execute(node, store, context)

        assert result.status == NodeStatus.FAILED
        assert "STDIO" in (result.error or "")

    async def test_unsupported_transport(
        self, executor: MCPExecutor, store: VariableStore, context: ExecutionContext
    ) -> None:
        """Unsupported or misconfigured transport should fail."""
        node = _make_node({
            "server_id": "srv-1",
            "tool_name": "read_file",
            "parameters": {},
        })

        server = FakeMCPServer(transport="grpc", url=None, command=None)
        mock_cm = _mock_db_session(server=server)

        with patch("fim_one.db.create_session", return_value=mock_cm):
            result = await executor.execute(node, store, context)

        assert result.status == NodeStatus.FAILED
        assert "transport" in (result.error or "").lower()

    async def test_mcp_client_disconnected_on_success(
        self, executor: MCPExecutor, store: VariableStore, context: ExecutionContext
    ) -> None:
        """MCPClient.disconnect_all() should be called even on success."""
        node = _make_node({
            "server_id": "srv-1",
            "tool_name": "read_file",
            "parameters": {},
        })

        server = FakeMCPServer(transport="sse", url="http://mcp.example.com/sse")
        mock_cm = _mock_db_session(server=server)

        target_tool = FakeMCPToolAdapter("read_file")
        mock_client = _mock_mcp_client(tools=[target_tool], transport="sse")

        with (
            patch("fim_one.db.create_session", return_value=mock_cm),
            patch("fim_one.core.mcp.MCPClient", return_value=mock_client),
        ):
            result = await executor.execute(node, store, context)

        assert result.status == NodeStatus.COMPLETED
        mock_client.disconnect_all.assert_awaited_once()

    async def test_mcp_client_disconnected_on_failure(
        self, executor: MCPExecutor, store: VariableStore, context: ExecutionContext
    ) -> None:
        """MCPClient.disconnect_all() should be called even when tool execution fails."""
        node = _make_node({
            "server_id": "srv-1",
            "tool_name": "read_file",
            "parameters": {},
        })

        server = FakeMCPServer(transport="sse", url="http://mcp.example.com/sse")
        mock_cm = _mock_db_session(server=server)

        # Tool that raises an error
        failing_tool = FakeMCPToolAdapter("read_file")

        async def failing_run(**kwargs: Any) -> str:
            raise RuntimeError("connection lost")

        failing_tool.run = failing_run  # type: ignore[assignment]

        mock_client = _mock_mcp_client(tools=[failing_tool], transport="sse")

        with (
            patch("fim_one.db.create_session", return_value=mock_cm),
            patch("fim_one.core.mcp.MCPClient", return_value=mock_client),
        ):
            result = await executor.execute(node, store, context)

        assert result.status == NodeStatus.FAILED
        assert "connection lost" in (result.error or "")
        mock_client.disconnect_all.assert_awaited_once()

    async def test_streamable_http_transport(
        self, executor: MCPExecutor, store: VariableStore, context: ExecutionContext
    ) -> None:
        """Streamable HTTP transport should use connect_streamable_http."""
        node = _make_node({
            "server_id": "srv-1",
            "tool_name": "read_file",
            "parameters": {},
        })

        server = FakeMCPServer(
            transport="streamable_http",
            url="http://mcp.example.com/mcp",
        )
        mock_cm = _mock_db_session(server=server)

        target_tool = FakeMCPToolAdapter("read_file")
        mock_client = _mock_mcp_client(tools=[target_tool], transport="streamable_http")

        with (
            patch("fim_one.db.create_session", return_value=mock_cm),
            patch("fim_one.core.mcp.MCPClient", return_value=mock_client),
        ):
            result = await executor.execute(node, store, context)

        assert result.status == NodeStatus.COMPLETED
        mock_client.connect_streamable_http.assert_awaited_once()


class TestMCPExecutorRegistered:
    """Verify MCPExecutor is properly registered."""

    def test_mcp_in_node_type(self) -> None:
        assert NodeType.MCP.value == "MCP"

    def test_executor_registry_has_mcp(self) -> None:
        from fim_one.core.workflow.nodes import EXECUTOR_REGISTRY

        assert NodeType.MCP in EXECUTOR_REGISTRY
        assert EXECUTOR_REGISTRY[NodeType.MCP] is MCPExecutor

    def test_get_executor_returns_mcp(self) -> None:
        from fim_one.core.workflow.nodes import get_executor

        executor = get_executor(NodeType.MCP)
        assert isinstance(executor, MCPExecutor)
