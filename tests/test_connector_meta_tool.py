"""Tests for ConnectorMetaTool — progressive connector disclosure."""

from __future__ import annotations

import json
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from fim_one.core.tool.connector.meta_tool import (
    ActionStub,
    ConnectorMetaTool,
    ConnectorStub,
    build_connector_meta_tool,
    get_connector_tool_mode,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_action_stub(
    name: str = "get_contacts",
    description: str = "Retrieve contacts",
    method: str = "GET",
    path: str = "/api/contacts",
    parameters_schema: dict | None = None,
    requires_confirmation: bool = False,
    action_id: str | None = "act-1",
) -> ActionStub:
    return ActionStub(
        name=name,
        description=description,
        method=method,
        path=path,
        parameters_schema=parameters_schema
        or {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "Max results"},
            },
            "required": [],
        },
        request_body_template=None,
        response_extract=None,
        requires_confirmation=requires_confirmation,
        action_id=action_id,
    )


def _make_connector_stub(
    name: str = "salesforce",
    description: str = "CRM platform",
    actions: list[ActionStub] | None = None,
) -> ConnectorStub:
    actions = actions or [_make_action_stub()]
    return ConnectorStub(
        name=name,
        description=description,
        action_count=len(actions),
        actions=actions,
    )


def _make_meta_tool(
    stubs: list[ConnectorStub] | None = None,
    configs: dict | None = None,
) -> ConnectorMetaTool:
    stubs = stubs or [
        _make_connector_stub(
            "salesforce",
            "CRM platform",
            [
                _make_action_stub("get_contacts", "Retrieve contacts"),
                _make_action_stub(
                    "create_lead",
                    "Create a new lead",
                    method="POST",
                    path="/api/leads",
                    requires_confirmation=True,
                    action_id="act-2",
                ),
            ],
        ),
        _make_connector_stub(
            "slack",
            "Team messaging",
            [
                _make_action_stub(
                    "send_message",
                    "Send a message to a channel",
                    method="POST",
                    path="/api/chat.postMessage",
                    action_id="act-3",
                ),
            ],
        ),
    ]
    configs = configs or {
        "salesforce": {
            "base_url": "https://api.salesforce.com",
            "auth_type": "bearer",
            "auth_config": {},
            "auth_credentials": {"token": "test-token"},
            "connector_id": "conn-1",
        },
        "slack": {
            "base_url": "https://slack.com",
            "auth_type": "bearer",
            "auth_config": {},
            "auth_credentials": {"token": "xoxb-test"},
            "connector_id": "conn-2",
        },
    }
    return ConnectorMetaTool(stubs=stubs, connector_configs=configs)


# ---------------------------------------------------------------------------
# Test: BaseTool protocol
# ---------------------------------------------------------------------------


class TestConnectorMetaToolProtocol:
    """Verify the tool satisfies the BaseTool interface."""

    def test_name(self) -> None:
        tool = _make_meta_tool()
        assert tool.name == "connector"

    def test_display_name(self) -> None:
        tool = _make_meta_tool()
        assert tool.display_name == "Connector"

    def test_category(self) -> None:
        tool = _make_meta_tool()
        assert tool.category == "connector"

    def test_description_contains_connector_stubs(self) -> None:
        tool = _make_meta_tool()
        desc = tool.description
        assert "salesforce" in desc
        assert "CRM platform" in desc
        assert "slack" in desc
        assert "Team messaging" in desc
        assert "discover" in desc
        assert "execute" in desc

    def test_description_shows_action_names(self) -> None:
        tool = _make_meta_tool()
        desc = tool.description
        # salesforce actions listed inline
        assert "get_contacts" in desc
        assert "create_lead" in desc
        # slack action listed inline
        assert "send_message" in desc

    def test_parameters_schema_structure(self) -> None:
        tool = _make_meta_tool()
        schema = tool.parameters_schema
        assert schema["type"] == "object"
        props = schema["properties"]
        assert "subcommand" in props
        assert "connector" in props
        assert "action" in props
        assert "parameters" in props

        # subcommand should enumerate discover/execute
        assert props["subcommand"]["enum"] == ["discover", "execute"]
        # connector should enumerate available names
        assert sorted(props["connector"]["enum"]) == ["salesforce", "slack"]

        # required fields
        assert "subcommand" in schema["required"]
        assert "connector" in schema["required"]

    def test_connector_names_property(self) -> None:
        tool = _make_meta_tool()
        assert tool.connector_names == ["salesforce", "slack"]

    def test_stub_count_property(self) -> None:
        tool = _make_meta_tool()
        assert tool.stub_count == 2


# ---------------------------------------------------------------------------
# Test: discover subcommand
# ---------------------------------------------------------------------------


class TestDiscover:
    """Test the discover subcommand."""

    @pytest.mark.asyncio
    async def test_discover_returns_action_schemas(self) -> None:
        tool = _make_meta_tool()
        result = await tool.run(subcommand="discover", connector="salesforce")

        assert "Connector: salesforce" in result
        assert "get_contacts" in result
        assert "create_lead" in result
        assert "Retrieve contacts" in result
        assert "Create a new lead" in result
        assert "GET" in result
        assert "POST" in result
        assert "/api/contacts" in result
        assert "/api/leads" in result

    @pytest.mark.asyncio
    async def test_discover_shows_parameters(self) -> None:
        tool = _make_meta_tool()
        result = await tool.run(subcommand="discover", connector="salesforce")
        assert "limit" in result
        assert "integer" in result

    @pytest.mark.asyncio
    async def test_discover_shows_requires_confirmation(self) -> None:
        tool = _make_meta_tool()
        result = await tool.run(subcommand="discover", connector="salesforce")
        assert "requires_confirmation: true" in result

    @pytest.mark.asyncio
    async def test_discover_unknown_connector(self) -> None:
        tool = _make_meta_tool()
        result = await tool.run(subcommand="discover", connector="jira")

        assert "Unknown connector" in result
        assert "jira" in result
        assert "salesforce" in result  # lists available connectors
        assert "slack" in result

    @pytest.mark.asyncio
    async def test_discover_empty_actions(self) -> None:
        stub = ConnectorStub(
            name="empty_conn", description="Empty", action_count=0, actions=[]
        )
        tool = ConnectorMetaTool(stubs=[stub])
        result = await tool.run(subcommand="discover", connector="empty_conn")
        assert "no actions configured" in result


# ---------------------------------------------------------------------------
# Test: execute subcommand
# ---------------------------------------------------------------------------


class TestExecute:
    """Test the execute subcommand."""

    @pytest.mark.asyncio
    async def test_execute_routes_to_correct_action(self) -> None:
        """Verify execute creates a ConnectorToolAdapter and calls run()."""
        tool = _make_meta_tool()

        with patch(
            "fim_one.core.tool.connector.meta_tool.ConnectorToolAdapter"
        ) as MockAdapter:
            mock_instance = AsyncMock()
            mock_instance.run = AsyncMock(return_value='{"contacts": []}')
            MockAdapter.return_value = mock_instance

            result = await tool.run(
                subcommand="execute",
                connector="salesforce",
                action="get_contacts",
                parameters={"limit": 10},
            )

            # Verify adapter was created with correct params
            MockAdapter.assert_called_once()
            call_kwargs = MockAdapter.call_args[1]
            assert call_kwargs["connector_name"] == "salesforce"
            assert call_kwargs["action_name"] == "get_contacts"
            assert call_kwargs["connector_base_url"] == "https://api.salesforce.com"
            assert call_kwargs["connector_auth_type"] == "bearer"
            assert call_kwargs["auth_credentials"] == {"token": "test-token"}
            assert call_kwargs["connector_id"] == "conn-1"
            assert call_kwargs["action_id"] == "act-1"

            # Verify run was called with params
            mock_instance.run.assert_awaited_once_with(limit=10)
            assert result == '{"contacts": []}'

    @pytest.mark.asyncio
    async def test_execute_unknown_connector(self) -> None:
        tool = _make_meta_tool()
        result = await tool.run(
            subcommand="execute",
            connector="jira",
            action="get_issues",
        )
        assert "Unknown connector" in result
        assert "jira" in result

    @pytest.mark.asyncio
    async def test_execute_unknown_action(self) -> None:
        tool = _make_meta_tool()
        result = await tool.run(
            subcommand="execute",
            connector="salesforce",
            action="nonexistent_action",
        )
        assert "Unknown action" in result
        assert "nonexistent_action" in result
        assert "get_contacts" in result  # lists available actions

    @pytest.mark.asyncio
    async def test_execute_missing_action_name(self) -> None:
        tool = _make_meta_tool()
        result = await tool.run(
            subcommand="execute",
            connector="salesforce",
        )
        assert "'action' is required" in result
        assert "get_contacts" in result

    @pytest.mark.asyncio
    async def test_execute_no_config_returns_error(self) -> None:
        """If connector_configs is missing the connector, return error."""
        stub = _make_connector_stub("no_config", "No config connector")
        tool = ConnectorMetaTool(stubs=[stub], connector_configs={})
        result = await tool.run(
            subcommand="execute",
            connector="no_config",
            action="get_contacts",
        )
        assert "No configuration found" in result

    @pytest.mark.asyncio
    async def test_execute_passes_on_call_complete(self) -> None:
        """Verify the on_call_complete callback is forwarded to adapter."""
        callback = AsyncMock()
        stub = _make_connector_stub()
        configs = {
            "salesforce": {
                "base_url": "https://api.salesforce.com",
                "auth_type": "none",
                "auth_config": {},
                "auth_credentials": None,
                "connector_id": "conn-1",
            },
        }
        tool = ConnectorMetaTool(
            stubs=[stub],
            connector_configs=configs,
            on_call_complete=callback,
        )

        with patch(
            "fim_one.core.tool.connector.meta_tool.ConnectorToolAdapter"
        ) as MockAdapter:
            mock_instance = AsyncMock()
            mock_instance.run = AsyncMock(return_value="ok")
            MockAdapter.return_value = mock_instance

            await tool.run(
                subcommand="execute",
                connector="salesforce",
                action="get_contacts",
                parameters={},
            )

            call_kwargs = MockAdapter.call_args[1]
            assert call_kwargs["on_call_complete"] is callback


# ---------------------------------------------------------------------------
# Test: edge cases
# ---------------------------------------------------------------------------


class TestRequiresConfirmationFor:
    """Per-call confirmation flag lookup, used by FeishuGateHook."""

    def test_execute_flagged_action_returns_true(self) -> None:
        flagged = _make_action_stub(name="send_email", requires_confirmation=True)
        stub = _make_connector_stub(name="gmail", actions=[flagged])
        tool = ConnectorMetaTool(stubs=[stub])
        assert tool.requires_confirmation_for(
            {"subcommand": "execute", "connector": "gmail", "action": "send_email"}
        ) is True

    def test_execute_unflagged_action_returns_false(self) -> None:
        unflagged = _make_action_stub(name="list_inbox", requires_confirmation=False)
        stub = _make_connector_stub(name="gmail", actions=[unflagged])
        tool = ConnectorMetaTool(stubs=[stub])
        assert tool.requires_confirmation_for(
            {"subcommand": "execute", "connector": "gmail", "action": "list_inbox"}
        ) is False

    def test_discover_subcommand_never_requires_confirmation(self) -> None:
        flagged = _make_action_stub(name="delete_repo", requires_confirmation=True)
        stub = _make_connector_stub(name="github", actions=[flagged])
        tool = ConnectorMetaTool(stubs=[stub])
        assert tool.requires_confirmation_for(
            {"subcommand": "discover", "connector": "github"}
        ) is False

    def test_unknown_connector_returns_false(self) -> None:
        tool = ConnectorMetaTool(stubs=[_make_connector_stub()])
        assert tool.requires_confirmation_for(
            {"subcommand": "execute", "connector": "ghost", "action": "x"}
        ) is False

    def test_unknown_action_returns_false(self) -> None:
        stub = _make_connector_stub(actions=[_make_action_stub(name="known_op")])
        tool = ConnectorMetaTool(stubs=[stub])
        assert tool.requires_confirmation_for(
            {
                "subcommand": "execute",
                "connector": stub.name,
                "action": "nonexistent",
            }
        ) is False


class TestEdgeCases:
    """Miscellaneous edge case tests."""

    @pytest.mark.asyncio
    async def test_unknown_subcommand(self) -> None:
        tool = _make_meta_tool()
        result = await tool.run(subcommand="delete", connector="salesforce")
        assert "Unknown subcommand" in result
        assert "delete" in result

    @pytest.mark.asyncio
    async def test_missing_subcommand(self) -> None:
        tool = _make_meta_tool()
        result = await tool.run(connector="salesforce")
        assert "'subcommand' is required" in result

    @pytest.mark.asyncio
    async def test_missing_connector(self) -> None:
        tool = _make_meta_tool()
        result = await tool.run(subcommand="discover")
        assert "'connector' is required" in result

    @pytest.mark.asyncio
    async def test_execute_with_empty_parameters(self) -> None:
        """Parameters default to {} when not provided."""
        tool = _make_meta_tool()

        with patch(
            "fim_one.core.tool.connector.meta_tool.ConnectorToolAdapter"
        ) as MockAdapter:
            mock_instance = AsyncMock()
            mock_instance.run = AsyncMock(return_value="ok")
            MockAdapter.return_value = mock_instance

            await tool.run(
                subcommand="execute",
                connector="salesforce",
                action="get_contacts",
            )

            mock_instance.run.assert_awaited_once_with()


# ---------------------------------------------------------------------------
# Test: build_connector_meta_tool factory
# ---------------------------------------------------------------------------


class TestBuildConnectorMetaTool:
    """Test the factory function that builds from ORM objects."""

    def _make_mock_connector(
        self,
        conn_id: str = "conn-1",
        name: str = "Salesforce CRM",
        description: str = "CRM platform",
        base_url: str = "https://api.salesforce.com",
        auth_type: str = "bearer",
        auth_config: dict | None = None,
        actions: list | None = None,
    ) -> MagicMock:
        conn = MagicMock()
        conn.id = conn_id
        conn.name = name
        conn.description = description
        conn.base_url = base_url
        conn.auth_type = auth_type
        conn.auth_config = auth_config or {}

        if actions is None:
            action1 = MagicMock()
            action1.id = "act-1"
            action1.name = "Get Contacts"
            action1.description = "Retrieve contacts list"
            action1.method = "GET"
            action1.path = "/api/contacts"
            action1.parameters_schema = {
                "type": "object",
                "properties": {"limit": {"type": "integer"}},
                "required": [],
            }
            action1.request_body_template = None
            action1.response_extract = None
            action1.requires_confirmation = False

            action2 = MagicMock()
            action2.id = "act-2"
            action2.name = "Create Lead"
            action2.description = "Create a new lead"
            action2.method = "POST"
            action2.path = "/api/leads"
            action2.parameters_schema = {
                "type": "object",
                "properties": {"email": {"type": "string"}},
                "required": ["email"],
            }
            action2.request_body_template = None
            action2.response_extract = None
            action2.requires_confirmation = True

            actions = [action1, action2]

        conn.actions = actions
        return conn

    def test_builds_meta_tool_from_orm(self) -> None:
        conn = self._make_mock_connector()
        meta_tool = build_connector_meta_tool(
            [conn],
            resolved_credentials={"conn-1": {"token": "abc"}},
            on_call_complete=None,
        )

        assert isinstance(meta_tool, ConnectorMetaTool)
        assert meta_tool.stub_count == 1
        assert "salesforce_crm" in meta_tool.connector_names

    def test_sanitizes_connector_name(self) -> None:
        conn = self._make_mock_connector(name="My Awesome API!")
        meta_tool = build_connector_meta_tool([conn], on_call_complete=None)
        assert "my_awesome_api" in meta_tool.connector_names

    def test_sanitizes_action_names(self) -> None:
        conn = self._make_mock_connector()
        meta_tool = build_connector_meta_tool([conn], on_call_complete=None)
        result = meta_tool._discover("salesforce_crm")
        assert "get_contacts" in result
        assert "create_lead" in result

    def test_multiple_connectors(self) -> None:
        conn1 = self._make_mock_connector(conn_id="c1", name="Salesforce")
        conn2 = self._make_mock_connector(
            conn_id="c2",
            name="Slack",
            description="Team messaging",
            base_url="https://slack.com/api",
        )
        meta_tool = build_connector_meta_tool(
            [conn1, conn2],
            resolved_credentials={
                "c1": {"token": "sf-token"},
                "c2": {"token": "slack-token"},
            },
            on_call_complete=None,
        )
        assert meta_tool.stub_count == 2
        assert sorted(meta_tool.connector_names) == ["salesforce", "slack"]

    def test_empty_connectors_list(self) -> None:
        meta_tool = build_connector_meta_tool([], on_call_complete=None)
        assert meta_tool.stub_count == 0
        assert meta_tool.connector_names == []

    def test_credentials_mapping(self) -> None:
        conn = self._make_mock_connector()
        creds = {"token": "my-token"}
        meta_tool = build_connector_meta_tool(
            [conn],
            resolved_credentials={"conn-1": creds},
            on_call_complete=None,
        )
        config = meta_tool._connector_configs.get("salesforce_crm", {})
        assert config.get("auth_credentials") == creds

    def test_on_call_complete_forwarded(self) -> None:
        callback = AsyncMock()
        conn = self._make_mock_connector()
        meta_tool = build_connector_meta_tool(
            [conn], on_call_complete=callback
        )
        assert meta_tool._on_call_complete is callback


# ---------------------------------------------------------------------------
# Test: name deduplication
# ---------------------------------------------------------------------------


class TestConnectorNameDeduplication:
    """Verify that connectors with colliding safe_names get deduplicated."""

    def _make_mock_connector(
        self,
        conn_id: str = "conn-1",
        name: str = "My API",
        description: str = "An API",
        base_url: str = "https://api.example.com",
        auth_type: str = "bearer",
        actions: list | None = None,
    ) -> MagicMock:
        conn = MagicMock()
        conn.id = conn_id
        conn.name = name
        conn.description = description
        conn.base_url = base_url
        conn.auth_type = auth_type
        conn.auth_config = {}
        conn.actions = actions or []
        return conn

    def test_chinese_names_that_collapse_to_same_safe_name(self) -> None:
        """Two connectors whose names differ only in CJK chars must not collide."""
        conn_a = self._make_mock_connector("aaaa-1111", "智合API")
        conn_b = self._make_mock_connector("bbbb-2222", "测试API")

        tool = build_connector_meta_tool([conn_a, conn_b], on_call_complete=None)

        names = list(tool._stubs.keys())
        assert len(names) == 2, f"Expected 2 stubs, got {len(names)}: {names}"
        assert "api" in names
        assert any("bbbb" in n for n in names), (
            f"Colliding name should contain connector ID prefix: {names}"
        )

    def test_no_collision_no_suffix(self) -> None:
        """Distinct names should not get a suffix."""
        conn_a = self._make_mock_connector("aaaa-1111", "Salesforce")
        conn_b = self._make_mock_connector("bbbb-2222", "Stripe")

        tool = build_connector_meta_tool([conn_a, conn_b], on_call_complete=None)

        names = list(tool._stubs.keys())
        assert names == ["salesforce", "stripe"]


# ---------------------------------------------------------------------------
# Test: get_connector_tool_mode
# ---------------------------------------------------------------------------


class TestGetConnectorToolMode:
    """Test the feature flag resolution logic."""

    def test_default_is_progressive(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("CONNECTOR_TOOL_MODE", None)
            assert get_connector_tool_mode() == "progressive"

    def test_env_var_legacy(self) -> None:
        with patch.dict(os.environ, {"CONNECTOR_TOOL_MODE": "legacy"}):
            assert get_connector_tool_mode() == "legacy"

    def test_env_var_progressive(self) -> None:
        with patch.dict(os.environ, {"CONNECTOR_TOOL_MODE": "progressive"}):
            assert get_connector_tool_mode() == "progressive"

    def test_env_var_invalid_falls_back(self) -> None:
        with patch.dict(os.environ, {"CONNECTOR_TOOL_MODE": "invalid"}):
            assert get_connector_tool_mode() == "progressive"

    def test_agent_config_overrides_env(self) -> None:
        agent_cfg = {
            "model_config_json": {"connector_tool_mode": "legacy"},
        }
        with patch.dict(os.environ, {"CONNECTOR_TOOL_MODE": "progressive"}):
            assert get_connector_tool_mode(agent_cfg) == "legacy"

    def test_agent_config_progressive(self) -> None:
        agent_cfg = {
            "model_config_json": {"connector_tool_mode": "progressive"},
        }
        assert get_connector_tool_mode(agent_cfg) == "progressive"

    def test_agent_config_invalid_falls_to_env(self) -> None:
        agent_cfg = {
            "model_config_json": {"connector_tool_mode": "invalid"},
        }
        with patch.dict(os.environ, {"CONNECTOR_TOOL_MODE": "legacy"}):
            assert get_connector_tool_mode(agent_cfg) == "legacy"

    def test_agent_config_none_model_config(self) -> None:
        agent_cfg = {"model_config_json": None}
        with patch.dict(os.environ, {"CONNECTOR_TOOL_MODE": "legacy"}):
            assert get_connector_tool_mode(agent_cfg) == "legacy"

    def test_agent_config_no_model_config_key(self) -> None:
        agent_cfg = {}
        with patch.dict(os.environ, {"CONNECTOR_TOOL_MODE": "legacy"}):
            assert get_connector_tool_mode(agent_cfg) == "legacy"


# ---------------------------------------------------------------------------
# Test: token efficiency (the core value proposition)
# ---------------------------------------------------------------------------


class TestTokenEfficiency:
    """Verify progressive mode produces much shorter descriptions."""

    def test_description_shorter_than_individual_tools(self) -> None:
        """With 10 connectors x 5 actions, progressive should be dramatically
        smaller than 50 individual tool descriptions."""
        stubs = []
        for i in range(10):
            actions = [
                _make_action_stub(
                    name=f"action_{j}",
                    description=f"Description for action {j} with some detail about what it does",
                    method=["GET", "POST", "PUT", "DELETE", "PATCH"][j % 5],
                    path=f"/api/v1/resource_{j}",
                )
                for j in range(5)
            ]
            stubs.append(
                ConnectorStub(
                    name=f"connector_{i}",
                    description=f"Service {i} platform with various capabilities",
                    action_count=5,
                    actions=actions,
                )
            )

        tool = ConnectorMetaTool(stubs=stubs)
        desc = tool.description

        # The meta tool description for 10 connectors should be compact
        # Rough estimate: ~30 tokens per connector = ~300 tokens
        # vs 10 * 5 * ~50 tokens per action = ~2500 tokens
        desc_words = len(desc.split())
        assert desc_words < 200, (
            f"Meta tool description has {desc_words} words — should be compact"
        )
