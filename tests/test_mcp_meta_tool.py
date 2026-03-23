"""Tests for MCPServerMetaTool -- progressive MCP tool disclosure."""

from __future__ import annotations

import json
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from fim_one.core.mcp.meta_tool import (
    MCPServerMetaTool,
    MCPServerStub,
    MCPToolStub,
    build_mcp_meta_tool,
    get_mcp_tool_mode,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


_DEFAULT_TOOL_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "path": {"type": "string", "description": "File path"},
    },
    "required": ["path"],
}


def _make_tool_stub(
    name: str = "read_file",
    description: str | None = "Read a file from disk",
    input_schema: dict | None = None,
) -> MCPToolStub:
    return MCPToolStub(
        name=name,
        description=description,
        input_schema=_DEFAULT_TOOL_SCHEMA if input_schema is None else input_schema,
    )


def _make_server_stub(
    name: str = "filesystem",
    description: str | None = "File system operations",
    tools: list[MCPToolStub] | None = None,
) -> MCPServerStub:
    tools = tools or [_make_tool_stub()]
    return MCPServerStub(
        name=name,
        description=description,
        tool_count=len(tools),
        tools=tools,
    )


def _make_adapter(
    original_name: str = "read_file",
    description: str = "Read a file from disk",
    schema: dict | None = None,
    run_return: str = "file contents here",
) -> MagicMock:
    """Create a mock MCPToolAdapter with the expected attributes."""
    adapter = MagicMock()
    adapter._original_name = original_name
    adapter._description = description
    adapter._schema = schema or {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "File path"},
        },
        "required": ["path"],
    }
    adapter.run = AsyncMock(return_value=run_return)
    return adapter


def _make_meta_tool(
    stubs: list[MCPServerStub] | None = None,
    adapters: dict[str, dict[str, MagicMock]] | None = None,
    on_call_complete: AsyncMock | None = None,
) -> MCPServerMetaTool:
    if stubs is None:
        stubs = [
            _make_server_stub(
                "filesystem",
                "File system operations",
                [
                    _make_tool_stub("read_file", "Read a file from disk"),
                    _make_tool_stub(
                        "write_file",
                        "Write content to a file",
                        {
                            "type": "object",
                            "properties": {
                                "path": {"type": "string", "description": "File path"},
                                "content": {
                                    "type": "string",
                                    "description": "File content",
                                },
                            },
                            "required": ["path", "content"],
                        },
                    ),
                ],
            ),
            _make_server_stub(
                "github",
                "GitHub API",
                [
                    _make_tool_stub(
                        "create_issue",
                        "Create a GitHub issue",
                        {
                            "type": "object",
                            "properties": {
                                "title": {
                                    "type": "string",
                                    "description": "Issue title",
                                },
                                "body": {
                                    "type": "string",
                                    "description": "Issue body",
                                },
                            },
                            "required": ["title"],
                        },
                    ),
                ],
            ),
        ]

    if adapters is None:
        adapters = {
            "filesystem": {
                "read_file": _make_adapter("read_file", run_return="file contents"),
                "write_file": _make_adapter(
                    "write_file", "Write content to a file", run_return="ok"
                ),
            },
            "github": {
                "create_issue": _make_adapter(
                    "create_issue",
                    "Create a GitHub issue",
                    run_return='{"number": 42}',
                ),
            },
        }

    return MCPServerMetaTool(
        stubs=stubs,
        adapters=adapters,
        on_call_complete=on_call_complete,
    )


# ---------------------------------------------------------------------------
# Test: Data structures (MCPToolStub, MCPServerStub)
# ---------------------------------------------------------------------------


class TestDataStructures:
    """Verify frozen dataclass behavior of MCPToolStub and MCPServerStub."""

    def test_tool_stub_creation(self) -> None:
        stub = _make_tool_stub()
        assert stub.name == "read_file"
        assert stub.description == "Read a file from disk"
        assert stub.input_schema["type"] == "object"

    def test_tool_stub_frozen(self) -> None:
        stub = _make_tool_stub()
        with pytest.raises(AttributeError):
            stub.name = "other"  # type: ignore[misc]

    def test_server_stub_creation(self) -> None:
        stub = _make_server_stub()
        assert stub.name == "filesystem"
        assert stub.description == "File system operations"
        assert stub.tool_count == 1
        assert len(stub.tools) == 1

    def test_server_stub_frozen(self) -> None:
        stub = _make_server_stub()
        with pytest.raises(AttributeError):
            stub.name = "other"  # type: ignore[misc]

    def test_server_stub_default_tools(self) -> None:
        stub = MCPServerStub(
            name="empty", description=None, tool_count=0
        )
        assert stub.tools == []

    def test_tool_stub_none_description(self) -> None:
        stub = MCPToolStub(name="bare", description=None, input_schema={})
        assert stub.description is None


# ---------------------------------------------------------------------------
# Test: BaseTool protocol
# ---------------------------------------------------------------------------


class TestMCPServerMetaToolProtocol:
    """Verify the tool satisfies the BaseTool interface."""

    def test_name(self) -> None:
        tool = _make_meta_tool()
        assert tool.name == "mcp"

    def test_display_name(self) -> None:
        tool = _make_meta_tool()
        assert tool.display_name == "MCP"

    def test_category(self) -> None:
        tool = _make_meta_tool()
        assert tool.category == "mcp"

    def test_description_contains_server_stubs(self) -> None:
        tool = _make_meta_tool()
        desc = tool.description
        assert "filesystem" in desc
        assert "File system operations" in desc
        assert "github" in desc
        assert "GitHub API" in desc
        assert "discover" in desc
        assert "call" in desc

    def test_description_shows_tool_names(self) -> None:
        tool = _make_meta_tool()
        desc = tool.description
        # filesystem tools listed inline
        assert "read_file" in desc
        assert "write_file" in desc
        # github tool listed inline
        assert "create_issue" in desc

    def test_description_shows_tool_counts(self) -> None:
        tool = _make_meta_tool()
        desc = tool.description
        assert "2 tools" in desc  # filesystem has 2
        assert "1 tools" in desc  # github has 1

    def test_description_uses_name_when_no_description(self) -> None:
        """When server description is None, the name is used as fallback."""
        stub = MCPServerStub(
            name="myserver",
            description=None,
            tool_count=1,
            tools=[_make_tool_stub("do_thing")],
        )
        tool = MCPServerMetaTool(stubs=[stub], adapters={})
        desc = tool.description
        # Should use the name as fallback for description
        assert "myserver: myserver" in desc

    def test_parameters_schema_structure(self) -> None:
        tool = _make_meta_tool()
        schema = tool.parameters_schema
        assert schema["type"] == "object"
        props = schema["properties"]
        assert "subcommand" in props
        assert "server" in props
        assert "tool" in props
        assert "parameters" in props

        # subcommand should enumerate discover/call
        assert props["subcommand"]["enum"] == ["discover", "call"]
        # server should enumerate available names (sorted)
        assert props["server"]["enum"] == ["filesystem", "github"]

        # required fields
        assert "subcommand" in schema["required"]
        assert "server" in schema["required"]

    def test_parameters_schema_no_enum_when_empty_servers(self) -> None:
        """When there are no servers, server property has no enum."""
        tool = MCPServerMetaTool(stubs=[], adapters={})
        schema = tool.parameters_schema
        assert "enum" not in schema["properties"]["server"]

    def test_server_names_property(self) -> None:
        tool = _make_meta_tool()
        assert tool.server_names == ["filesystem", "github"]

    def test_stub_count_property(self) -> None:
        tool = _make_meta_tool()
        assert tool.stub_count == 2


# ---------------------------------------------------------------------------
# Test: discover subcommand
# ---------------------------------------------------------------------------


class TestDiscover:
    """Test the discover subcommand."""

    @pytest.mark.asyncio
    async def test_discover_returns_tool_schemas(self) -> None:
        tool = _make_meta_tool()
        result = await tool.run(subcommand="discover", server="filesystem")

        assert "Server: filesystem" in result
        assert "File system operations" in result
        assert "read_file" in result
        assert "write_file" in result
        assert "Read a file from disk" in result
        assert "Write content to a file" in result

    @pytest.mark.asyncio
    async def test_discover_shows_parameters(self) -> None:
        tool = _make_meta_tool()
        result = await tool.run(subcommand="discover", server="filesystem")
        assert "path" in result
        assert "string" in result

    @pytest.mark.asyncio
    async def test_discover_shows_full_json_schema(self) -> None:
        """Discover should include the full JSON schema for tools with properties."""
        tool = _make_meta_tool()
        result = await tool.run(subcommand="discover", server="filesystem")
        # The schema should be JSON-formatted
        assert '"type": "object"' in result
        assert '"properties"' in result

    @pytest.mark.asyncio
    async def test_discover_unknown_server(self) -> None:
        tool = _make_meta_tool()
        result = await tool.run(subcommand="discover", server="jira")

        assert "Unknown server" in result
        assert "jira" in result
        assert "filesystem" in result  # lists available servers
        assert "github" in result

    @pytest.mark.asyncio
    async def test_discover_empty_tools(self) -> None:
        stub = MCPServerStub(
            name="empty_server", description="Empty", tool_count=0, tools=[]
        )
        tool = MCPServerMetaTool(stubs=[stub], adapters={})
        result = await tool.run(subcommand="discover", server="empty_server")
        assert "no tools" in result

    @pytest.mark.asyncio
    async def test_discover_tool_with_no_description(self) -> None:
        """Tool stubs with None description should still be listed."""
        stub = _make_server_stub(
            "test_server",
            "Test",
            [_make_tool_stub("bare_tool", description=None)],
        )
        tool = MCPServerMetaTool(stubs=[stub], adapters={})
        result = await tool.run(subcommand="discover", server="test_server")
        assert "bare_tool" in result

    @pytest.mark.asyncio
    async def test_discover_tool_with_no_properties(self) -> None:
        """Tool with empty input_schema should show 'parameters: (none)'."""
        stub = _make_server_stub(
            "test_server",
            "Test",
            [_make_tool_stub("no_params_tool", input_schema={})],
        )
        tool = MCPServerMetaTool(stubs=[stub], adapters={})
        result = await tool.run(subcommand="discover", server="test_server")
        assert "(none)" in result


# ---------------------------------------------------------------------------
# Test: call subcommand
# ---------------------------------------------------------------------------


class TestCall:
    """Test the call subcommand."""

    @pytest.mark.asyncio
    async def test_call_routes_to_correct_adapter(self) -> None:
        """Verify call delegates to the stored MCPToolAdapter.run()."""
        tool = _make_meta_tool()
        result = await tool.run(
            subcommand="call",
            server="filesystem",
            tool="read_file",
            parameters={"path": "/tmp/test.txt"},
        )

        # Verify the adapter's run was called with correct params
        adapter = tool._adapters["filesystem"]["read_file"]
        adapter.run.assert_awaited_once_with(path="/tmp/test.txt")
        assert result == "file contents"

    @pytest.mark.asyncio
    async def test_call_returns_adapter_result(self) -> None:
        tool = _make_meta_tool()
        result = await tool.run(
            subcommand="call",
            server="github",
            tool="create_issue",
            parameters={"title": "Bug report"},
        )
        assert result == '{"number": 42}'

    @pytest.mark.asyncio
    async def test_call_unknown_server(self) -> None:
        tool = _make_meta_tool()
        result = await tool.run(
            subcommand="call",
            server="jira",
            tool="get_issues",
        )
        assert "Unknown server" in result
        assert "jira" in result
        assert "filesystem" in result
        assert "github" in result

    @pytest.mark.asyncio
    async def test_call_unknown_tool(self) -> None:
        tool = _make_meta_tool()
        result = await tool.run(
            subcommand="call",
            server="filesystem",
            tool="nonexistent_tool",
        )
        assert "Unknown tool" in result
        assert "nonexistent_tool" in result
        assert "read_file" in result  # lists available tools

    @pytest.mark.asyncio
    async def test_call_missing_tool_name(self) -> None:
        tool = _make_meta_tool()
        result = await tool.run(
            subcommand="call",
            server="filesystem",
        )
        assert "'tool' is required" in result
        assert "read_file" in result

    @pytest.mark.asyncio
    async def test_call_with_empty_parameters(self) -> None:
        """Parameters default to {} when not provided."""
        tool = _make_meta_tool()
        await tool.run(
            subcommand="call",
            server="filesystem",
            tool="read_file",
        )
        adapter = tool._adapters["filesystem"]["read_file"]
        adapter.run.assert_awaited_once_with()

    @pytest.mark.asyncio
    async def test_call_adapter_exception_returns_error(self) -> None:
        """When the adapter raises, the error is caught and returned."""
        adapter = _make_adapter("read_file")
        adapter.run = AsyncMock(side_effect=RuntimeError("connection lost"))

        stub = _make_server_stub(
            "filesystem",
            "FS",
            [_make_tool_stub("read_file")],
        )
        tool = MCPServerMetaTool(
            stubs=[stub],
            adapters={"filesystem": {"read_file": adapter}},
        )
        result = await tool.run(
            subcommand="call",
            server="filesystem",
            tool="read_file",
            parameters={"path": "/tmp"},
        )
        assert "[Error]" in result
        assert "RuntimeError" in result
        assert "connection lost" in result

    @pytest.mark.asyncio
    async def test_call_delegates_to_adapter_run(self) -> None:
        """Explicitly verify that call invokes adapter.run() with **parameters."""
        mock_run = AsyncMock(return_value="result data")
        adapter = _make_adapter("query")
        adapter.run = mock_run

        stub = _make_server_stub(
            "db",
            "Database",
            [_make_tool_stub("query")],
        )
        tool = MCPServerMetaTool(
            stubs=[stub],
            adapters={"db": {"query": adapter}},
        )
        await tool.run(
            subcommand="call",
            server="db",
            tool="query",
            parameters={"sql": "SELECT 1", "limit": 10},
        )
        mock_run.assert_awaited_once_with(sql="SELECT 1", limit=10)


# ---------------------------------------------------------------------------
# Test: edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    """Miscellaneous edge case tests."""

    @pytest.mark.asyncio
    async def test_unknown_subcommand(self) -> None:
        tool = _make_meta_tool()
        result = await tool.run(subcommand="delete", server="filesystem")
        assert "Unknown subcommand" in result
        assert "delete" in result

    @pytest.mark.asyncio
    async def test_missing_subcommand(self) -> None:
        tool = _make_meta_tool()
        result = await tool.run(server="filesystem")
        assert "'subcommand' is required" in result

    @pytest.mark.asyncio
    async def test_missing_server(self) -> None:
        tool = _make_meta_tool()
        result = await tool.run(subcommand="discover")
        assert "'server' is required" in result

    @pytest.mark.asyncio
    async def test_empty_string_subcommand(self) -> None:
        tool = _make_meta_tool()
        result = await tool.run(subcommand="", server="filesystem")
        assert "'subcommand' is required" in result

    @pytest.mark.asyncio
    async def test_empty_string_server(self) -> None:
        tool = _make_meta_tool()
        result = await tool.run(subcommand="discover", server="")
        assert "'server' is required" in result

    @pytest.mark.asyncio
    async def test_no_kwargs_at_all(self) -> None:
        tool = _make_meta_tool()
        result = await tool.run()
        assert "'subcommand' is required" in result


# ---------------------------------------------------------------------------
# Test: on_call_complete callback
# ---------------------------------------------------------------------------


class TestOnCallComplete:
    """Verify the on_call_complete callback is stored and accessible."""

    def test_on_call_complete_stored(self) -> None:
        callback = AsyncMock()
        tool = _make_meta_tool(on_call_complete=callback)
        assert tool._on_call_complete is callback

    def test_on_call_complete_none_by_default(self) -> None:
        tool = _make_meta_tool()
        assert tool._on_call_complete is None


# ---------------------------------------------------------------------------
# Test: build_mcp_meta_tool factory
# ---------------------------------------------------------------------------


class TestBuildMcpMetaTool:
    """Test the factory function that builds from MCPToolAdapter instances."""

    def _make_mock_adapter(
        self,
        original_name: str = "read_file",
        description: str = "Read a file from disk",
        schema: dict | None = None,
    ) -> MagicMock:
        """Create a mock MCPToolAdapter with the internal attributes the factory accesses."""
        adapter = MagicMock()
        adapter._original_name = original_name
        adapter._description = description
        adapter._schema = schema or {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path"},
            },
            "required": ["path"],
        }
        adapter.run = AsyncMock(return_value="ok")
        return adapter

    def test_builds_meta_tool_from_adapters(self) -> None:
        adapter = self._make_mock_adapter()
        meta_tool = build_mcp_meta_tool({"filesystem": [adapter]})

        assert isinstance(meta_tool, MCPServerMetaTool)
        assert meta_tool.stub_count == 1
        assert "filesystem" in meta_tool.server_names

    def test_stub_has_correct_tool_info(self) -> None:
        adapter = self._make_mock_adapter(
            "read_file", "Read a file from disk"
        )
        meta_tool = build_mcp_meta_tool({"filesystem": [adapter]})

        stub = meta_tool._stubs["filesystem"]
        assert stub.tool_count == 1
        assert stub.tools[0].name == "read_file"
        assert stub.tools[0].description == "Read a file from disk"

    def test_server_description_is_none(self) -> None:
        """MCP protocol doesn't expose server descriptions, so it's always None."""
        adapter = self._make_mock_adapter()
        meta_tool = build_mcp_meta_tool({"my_server": [adapter]})
        stub = meta_tool._stubs["my_server"]
        assert stub.description is None

    def test_multiple_servers(self) -> None:
        adapter_fs = self._make_mock_adapter("read_file", "Read file")
        adapter_gh = self._make_mock_adapter("create_issue", "Create issue")
        meta_tool = build_mcp_meta_tool({
            "filesystem": [adapter_fs],
            "github": [adapter_gh],
        })

        assert meta_tool.stub_count == 2
        assert sorted(meta_tool.server_names) == ["filesystem", "github"]

    def test_multiple_tools_per_server(self) -> None:
        adapter1 = self._make_mock_adapter("read_file", "Read a file")
        adapter2 = self._make_mock_adapter("write_file", "Write a file")
        meta_tool = build_mcp_meta_tool({"filesystem": [adapter1, adapter2]})

        stub = meta_tool._stubs["filesystem"]
        assert stub.tool_count == 2
        tool_names = [t.name for t in stub.tools]
        assert "read_file" in tool_names
        assert "write_file" in tool_names

    def test_empty_servers_dict(self) -> None:
        meta_tool = build_mcp_meta_tool({})
        assert meta_tool.stub_count == 0
        assert meta_tool.server_names == []

    def test_server_with_empty_tool_list(self) -> None:
        meta_tool = build_mcp_meta_tool({"empty": []})
        assert meta_tool.stub_count == 1
        stub = meta_tool._stubs["empty"]
        assert stub.tool_count == 0
        assert stub.tools == []

    def test_adapter_stored_for_call(self) -> None:
        adapter = self._make_mock_adapter("read_file")
        meta_tool = build_mcp_meta_tool({"filesystem": [adapter]})

        # The adapter should be stored keyed by original name
        assert meta_tool._adapters["filesystem"]["read_file"] is adapter

    def test_on_call_complete_forwarded(self) -> None:
        callback = AsyncMock()
        adapter = self._make_mock_adapter()
        meta_tool = build_mcp_meta_tool(
            {"filesystem": [adapter]}, on_call_complete=callback
        )
        assert meta_tool._on_call_complete is callback

    def test_adapter_empty_description_becomes_none(self) -> None:
        """When adapter._description is empty string, the stub description should be None."""
        adapter = self._make_mock_adapter("bare_tool", description="")
        meta_tool = build_mcp_meta_tool({"server": [adapter]})
        stub = meta_tool._stubs["server"]
        # Empty string is falsy, so `or None` in build_mcp_meta_tool converts it
        assert stub.tools[0].description is None


# ---------------------------------------------------------------------------
# Test: get_mcp_tool_mode
# ---------------------------------------------------------------------------


class TestGetMcpToolMode:
    """Test the feature flag resolution logic."""

    def test_default_is_progressive(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("MCP_TOOL_MODE", None)
            assert get_mcp_tool_mode() == "progressive"

    def test_env_var_legacy(self) -> None:
        with patch.dict(os.environ, {"MCP_TOOL_MODE": "legacy"}):
            assert get_mcp_tool_mode() == "legacy"

    def test_env_var_progressive(self) -> None:
        with patch.dict(os.environ, {"MCP_TOOL_MODE": "progressive"}):
            assert get_mcp_tool_mode() == "progressive"

    def test_env_var_invalid_falls_back(self) -> None:
        with patch.dict(os.environ, {"MCP_TOOL_MODE": "invalid"}):
            assert get_mcp_tool_mode() == "progressive"

    def test_agent_config_overrides_env(self) -> None:
        agent_cfg = {
            "model_config_json": {"mcp_tool_mode": "legacy"},
        }
        with patch.dict(os.environ, {"MCP_TOOL_MODE": "progressive"}):
            assert get_mcp_tool_mode(agent_cfg) == "legacy"

    def test_agent_config_progressive(self) -> None:
        agent_cfg = {
            "model_config_json": {"mcp_tool_mode": "progressive"},
        }
        assert get_mcp_tool_mode(agent_cfg) == "progressive"

    def test_agent_config_invalid_falls_to_env(self) -> None:
        agent_cfg = {
            "model_config_json": {"mcp_tool_mode": "invalid"},
        }
        with patch.dict(os.environ, {"MCP_TOOL_MODE": "legacy"}):
            assert get_mcp_tool_mode(agent_cfg) == "legacy"

    def test_agent_config_none_model_config(self) -> None:
        agent_cfg = {"model_config_json": None}
        with patch.dict(os.environ, {"MCP_TOOL_MODE": "legacy"}):
            assert get_mcp_tool_mode(agent_cfg) == "legacy"

    def test_agent_config_no_model_config_key(self) -> None:
        agent_cfg = {}
        with patch.dict(os.environ, {"MCP_TOOL_MODE": "legacy"}):
            assert get_mcp_tool_mode(agent_cfg) == "legacy"

    def test_agent_config_non_dict_model_config(self) -> None:
        """If model_config_json is a non-dict type, fall through to env."""
        agent_cfg = {"model_config_json": "not-a-dict"}
        with patch.dict(os.environ, {"MCP_TOOL_MODE": "legacy"}):
            assert get_mcp_tool_mode(agent_cfg) == "legacy"


# ---------------------------------------------------------------------------
# Test: token efficiency (the core value proposition)
# ---------------------------------------------------------------------------


class TestTokenEfficiency:
    """Verify progressive mode produces much shorter descriptions."""

    def test_description_shorter_than_individual_tools(self) -> None:
        """With 10 servers x 5 tools, progressive should be dramatically
        smaller than 50 individual tool descriptions."""
        stubs = []
        for i in range(10):
            tools = [
                _make_tool_stub(
                    name=f"tool_{j}",
                    description=f"Description for tool {j} with some detail about what it does",
                    input_schema={
                        "type": "object",
                        "properties": {
                            f"param_{k}": {"type": "string"}
                            for k in range(3)
                        },
                        "required": [],
                    },
                )
                for j in range(5)
            ]
            stubs.append(
                MCPServerStub(
                    name=f"server_{i}",
                    description=f"Service {i} platform with various capabilities",
                    tool_count=5,
                    tools=tools,
                )
            )

        tool = MCPServerMetaTool(stubs=stubs, adapters={})
        desc = tool.description

        # The meta tool description for 10 servers should be compact
        # ~30 tokens per server = ~300 tokens
        # vs 10 * 5 * ~50 tokens per tool = ~2500 tokens
        desc_words = len(desc.split())
        assert desc_words < 500, (
            f"Meta tool description has {desc_words} words -- should be compact"
        )


# ---------------------------------------------------------------------------
# Test: integration -- build then call
# ---------------------------------------------------------------------------


class TestBuildAndCall:
    """Integration: build from adapters and exercise discover/call."""

    @pytest.mark.asyncio
    async def test_build_and_discover(self) -> None:
        adapter = MagicMock()
        adapter._original_name = "list_repos"
        adapter._description = "List repositories"
        adapter._schema = {
            "type": "object",
            "properties": {
                "org": {"type": "string"},
            },
            "required": ["org"],
        }
        adapter.run = AsyncMock(return_value="repos list")

        meta_tool = build_mcp_meta_tool({"github": [adapter]})
        result = await meta_tool.run(subcommand="discover", server="github")

        assert "list_repos" in result
        assert "List repositories" in result
        assert "org" in result

    @pytest.mark.asyncio
    async def test_build_and_call(self) -> None:
        adapter = MagicMock()
        adapter._original_name = "list_repos"
        adapter._description = "List repositories"
        adapter._schema = {
            "type": "object",
            "properties": {"org": {"type": "string"}},
        }
        adapter.run = AsyncMock(return_value='["repo1", "repo2"]')

        meta_tool = build_mcp_meta_tool({"github": [adapter]})
        result = await meta_tool.run(
            subcommand="call",
            server="github",
            tool="list_repos",
            parameters={"org": "anthropic"},
        )

        adapter.run.assert_awaited_once_with(org="anthropic")
        assert result == '["repo1", "repo2"]'
