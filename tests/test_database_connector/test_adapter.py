"""Tests for DatabaseToolAdapter tool creation."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from fim_agent.core.tool.connector.database.adapter import DatabaseToolAdapter


@pytest.fixture()
def sample_schema_tables() -> list[dict[str, Any]]:
    """Sample schema tables data for testing."""
    return [
        {
            "table_name": "users",
            "display_name": "Users",
            "description": "Application users",
            "column_count": 3,
            "columns": [
                {
                    "column_name": "id",
                    "data_type": "integer",
                    "is_nullable": False,
                    "is_primary_key": True,
                    "display_name": "ID",
                    "description": "Primary key",
                },
                {
                    "column_name": "name",
                    "data_type": "varchar",
                    "is_nullable": False,
                    "is_primary_key": False,
                    "display_name": "Name",
                    "description": "User name",
                },
                {
                    "column_name": "email",
                    "data_type": "varchar",
                    "is_nullable": True,
                    "is_primary_key": False,
                    "display_name": "Email",
                    "description": None,
                },
            ],
        },
        {
            "table_name": "orders",
            "display_name": "Orders",
            "description": "Customer orders",
            "column_count": 2,
            "columns": [
                {
                    "column_name": "id",
                    "data_type": "integer",
                    "is_nullable": False,
                    "is_primary_key": True,
                    "display_name": None,
                    "description": None,
                },
                {
                    "column_name": "user_id",
                    "data_type": "integer",
                    "is_nullable": False,
                    "is_primary_key": False,
                    "display_name": None,
                    "description": "FK to users",
                },
            ],
        },
    ]


@pytest.fixture()
def sample_db_config() -> dict[str, Any]:
    """Sample decrypted DB config."""
    return {
        "host": "localhost",
        "port": 5432,
        "database": "testdb",
        "username": "testuser",
        "password": "testpass",
        "driver": "postgresql",
        "read_only": True,
        "max_rows": 500,
        "query_timeout": 15,
    }


class TestCreateTools:
    """Test that DatabaseToolAdapter.create_tools produces correct tool set."""

    def test_creates_three_tools(
        self,
        sample_db_config: dict[str, Any],
        sample_schema_tables: list[dict[str, Any]],
    ) -> None:
        tools = DatabaseToolAdapter.create_tools(
            connector_name="My Test DB",
            connector_id="test-123",
            db_config=sample_db_config,
            schema_tables=sample_schema_tables,
        )
        assert len(tools) == 3

    def test_tool_names(
        self,
        sample_db_config: dict[str, Any],
        sample_schema_tables: list[dict[str, Any]],
    ) -> None:
        tools = DatabaseToolAdapter.create_tools(
            connector_name="My Test DB",
            connector_id="test-123",
            db_config=sample_db_config,
            schema_tables=sample_schema_tables,
        )
        names = {t.name for t in tools}
        assert "my_test_db__list_tables" in names
        assert "my_test_db__describe_table" in names
        assert "my_test_db__query" in names

    def test_tool_categories(
        self,
        sample_db_config: dict[str, Any],
        sample_schema_tables: list[dict[str, Any]],
    ) -> None:
        tools = DatabaseToolAdapter.create_tools(
            connector_name="My Test DB",
            connector_id="test-123",
            db_config=sample_db_config,
            schema_tables=sample_schema_tables,
        )
        for tool in tools:
            assert tool.category == "database"

    def test_query_tool_has_sql_parameter(
        self,
        sample_db_config: dict[str, Any],
        sample_schema_tables: list[dict[str, Any]],
    ) -> None:
        tools = DatabaseToolAdapter.create_tools(
            connector_name="My Test DB",
            connector_id="test-123",
            db_config=sample_db_config,
            schema_tables=sample_schema_tables,
        )
        query_tool = [t for t in tools if "query" in t.name][0]
        schema = query_tool.parameters_schema
        assert "sql" in schema["properties"]
        assert "sql" in schema["required"]

    def test_describe_tool_has_table_name_parameter(
        self,
        sample_db_config: dict[str, Any],
        sample_schema_tables: list[dict[str, Any]],
    ) -> None:
        tools = DatabaseToolAdapter.create_tools(
            connector_name="My Test DB",
            connector_id="test-123",
            db_config=sample_db_config,
            schema_tables=sample_schema_tables,
        )
        describe_tool = [t for t in tools if "describe" in t.name][0]
        schema = describe_tool.parameters_schema
        assert "table_name" in schema["properties"]

    def test_list_tables_has_no_required_params(
        self,
        sample_db_config: dict[str, Any],
        sample_schema_tables: list[dict[str, Any]],
    ) -> None:
        tools = DatabaseToolAdapter.create_tools(
            connector_name="My Test DB",
            connector_id="test-123",
            db_config=sample_db_config,
            schema_tables=sample_schema_tables,
        )
        list_tool = [t for t in tools if "list_tables" in t.name][0]
        schema = list_tool.parameters_schema
        assert schema["required"] == []


class TestListTablesTool:
    """Test the list_tables tool execution."""

    @pytest.mark.asyncio
    async def test_returns_table_info(
        self,
        sample_db_config: dict[str, Any],
        sample_schema_tables: list[dict[str, Any]],
    ) -> None:
        tools = DatabaseToolAdapter.create_tools(
            connector_name="My Test DB",
            connector_id="test-123",
            db_config=sample_db_config,
            schema_tables=sample_schema_tables,
        )
        list_tool = [t for t in tools if "list_tables" in t.name][0]
        result = await list_tool.run()
        data = json.loads(result)
        assert len(data) == 2
        assert data[0]["table_name"] == "users"
        assert data[1]["table_name"] == "orders"


class TestDescribeTableTool:
    """Test the describe_table tool execution."""

    @pytest.mark.asyncio
    async def test_returns_cached_columns(
        self,
        sample_db_config: dict[str, Any],
        sample_schema_tables: list[dict[str, Any]],
    ) -> None:
        tools = DatabaseToolAdapter.create_tools(
            connector_name="My Test DB",
            connector_id="test-123",
            db_config=sample_db_config,
            schema_tables=sample_schema_tables,
        )
        describe_tool = [t for t in tools if "describe" in t.name][0]
        result = await describe_tool.run(table_name="users")
        data = json.loads(result)
        assert data["table_name"] == "users"
        assert len(data["columns"]) == 3
        assert data["columns"][0]["column_name"] == "id"
        assert data["columns"][0]["is_primary_key"] is True


class TestQueryToolDescription:
    """Test that the query tool includes schema context in its description."""

    def test_description_includes_table_names(
        self,
        sample_db_config: dict[str, Any],
        sample_schema_tables: list[dict[str, Any]],
    ) -> None:
        tools = DatabaseToolAdapter.create_tools(
            connector_name="My Test DB",
            connector_id="test-123",
            db_config=sample_db_config,
            schema_tables=sample_schema_tables,
        )
        query_tool = [t for t in tools if "query" in t.name][0]
        desc = query_tool.description
        assert "users" in desc
        assert "orders" in desc

    def test_description_includes_read_only_info(
        self,
        sample_db_config: dict[str, Any],
        sample_schema_tables: list[dict[str, Any]],
    ) -> None:
        tools = DatabaseToolAdapter.create_tools(
            connector_name="My Test DB",
            connector_id="test-123",
            db_config=sample_db_config,
            schema_tables=sample_schema_tables,
        )
        query_tool = [t for t in tools if "query" in t.name][0]
        assert "read-only" in query_tool.description

    def test_description_with_write_enabled(
        self,
        sample_schema_tables: list[dict[str, Any]],
    ) -> None:
        config = {
            "host": "localhost",
            "port": 5432,
            "database": "testdb",
            "username": "testuser",
            "password": "testpass",
            "driver": "postgresql",
            "read_only": False,
            "max_rows": 500,
            "query_timeout": 15,
        }
        tools = DatabaseToolAdapter.create_tools(
            connector_name="My Test DB",
            connector_id="test-123",
            db_config=config,
            schema_tables=sample_schema_tables,
        )
        query_tool = [t for t in tools if "query" in t.name][0]
        assert "read-write" in query_tool.description


class TestToolDisplayNames:
    """Test human-readable display names."""

    def test_display_names(
        self,
        sample_db_config: dict[str, Any],
        sample_schema_tables: list[dict[str, Any]],
    ) -> None:
        tools = DatabaseToolAdapter.create_tools(
            connector_name="My Production DB",
            connector_id="test-123",
            db_config=sample_db_config,
            schema_tables=sample_schema_tables,
        )
        display_names = {t.display_name for t in tools}
        assert "My Production DB: List Tables" in display_names
        assert "My Production DB: Describe Table" in display_names
        assert "My Production DB: Query" in display_names
