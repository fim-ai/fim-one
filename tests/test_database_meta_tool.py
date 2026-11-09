"""Tests for DatabaseMetaTool -- progressive database disclosure."""

from __future__ import annotations

import json
import os
from dataclasses import FrozenInstanceError
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from fim_one.core.tool.connector.database.meta_tool import (
    DatabaseMetaTool,
    DatabaseStub,
    TableStub,
    build_database_meta_tool,
    get_database_tool_mode,
)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _make_table_stub(
    name: str = "users",
    display_name: str | None = "Users",
    description: str | None = "User accounts table",
    column_count: int = 5,
) -> TableStub:
    return TableStub(
        name=name,
        display_name=display_name,
        description=description,
        column_count=column_count,
    )


def _make_schema_table(
    table_name: str = "users",
    display_name: str | None = "Users",
    description: str | None = "User accounts table",
    columns: list[dict] | None = None,
) -> dict:
    if columns is None:
        columns = [
            {
                "column_name": "id",
                "data_type": "INTEGER",
                "is_primary_key": True,
                "is_nullable": False,
            },
            {
                "column_name": "email",
                "data_type": "VARCHAR(255)",
                "is_primary_key": False,
                "is_nullable": False,
                "display_name": "Email Address",
                "description": "User email",
            },
            {
                "column_name": "name",
                "data_type": "VARCHAR(100)",
                "is_primary_key": False,
                "is_nullable": True,
            },
        ]
    return {
        "table_name": table_name,
        "display_name": display_name,
        "description": description,
        "column_count": len(columns),
        "columns": columns,
    }


def _make_database_stub(
    name: str = "my_postgres",
    display_name: str = "My Postgres",
    description: str | None = "Main PostgreSQL database",
    tables: list[TableStub] | None = None,
    schema_tables: list[dict] | None = None,
    db_config: dict | None = None,
    connector_id: str = "conn-db-1",
    read_only: bool = True,
    max_rows: int = 1000,
    query_timeout: int = 30,
) -> DatabaseStub:
    if tables is None:
        tables = [
            _make_table_stub("users", "Users", "User accounts table", 3),
            _make_table_stub("orders", "Orders", "Customer orders", 5),
        ]
    if schema_tables is None:
        schema_tables = [
            _make_schema_table("users"),
            _make_schema_table(
                "orders",
                "Orders",
                "Customer orders",
                columns=[
                    {
                        "column_name": "id",
                        "data_type": "INTEGER",
                        "is_primary_key": True,
                        "is_nullable": False,
                    },
                    {
                        "column_name": "user_id",
                        "data_type": "INTEGER",
                        "is_primary_key": False,
                        "is_nullable": False,
                        "description": "FK to users",
                    },
                    {
                        "column_name": "total",
                        "data_type": "DECIMAL(10,2)",
                        "is_primary_key": False,
                        "is_nullable": False,
                    },
                    {
                        "column_name": "status",
                        "data_type": "VARCHAR(20)",
                        "is_primary_key": False,
                        "is_nullable": True,
                    },
                    {
                        "column_name": "created_at",
                        "data_type": "TIMESTAMP",
                        "is_primary_key": False,
                        "is_nullable": True,
                    },
                ],
            ),
        ]
    if db_config is None:
        db_config = {
            "driver": "postgresql",
            "host": "localhost",
            "port": 5432,
            "database": "testdb",
            "username": "user",
            "password": "pass",
        }
    return DatabaseStub(
        name=name,
        display_name=display_name,
        description=description,
        table_count=len(tables),
        tables=tables,
        schema_tables=schema_tables,
        db_config=db_config,
        connector_id=connector_id,
        read_only=read_only,
        max_rows=max_rows,
        query_timeout=query_timeout,
    )


def _make_meta_tool(
    stubs: list[DatabaseStub] | None = None,
    on_call_complete: AsyncMock | None = None,
) -> DatabaseMetaTool:
    if stubs is None:
        stubs = [
            _make_database_stub(),
            _make_database_stub(
                name="analytics_db",
                display_name="Analytics DB",
                description="Analytics data warehouse",
                tables=[
                    _make_table_stub("events", "Events", "Analytics events", 8),
                ],
                schema_tables=[
                    _make_schema_table(
                        "events",
                        "Events",
                        "Analytics events",
                        columns=[
                            {
                                "column_name": "id",
                                "data_type": "BIGINT",
                                "is_primary_key": True,
                                "is_nullable": False,
                            },
                            {
                                "column_name": "event_type",
                                "data_type": "VARCHAR(50)",
                                "is_primary_key": False,
                                "is_nullable": False,
                            },
                        ],
                    ),
                ],
                connector_id="conn-db-2",
            ),
        ]
    return DatabaseMetaTool(stubs=stubs, on_call_complete=on_call_complete)


# ---------------------------------------------------------------------------
# Test: data structures
# ---------------------------------------------------------------------------


class TestDataStructures:
    """Verify TableStub and DatabaseStub frozen dataclass behaviour."""

    def test_table_stub_creation(self) -> None:
        stub = TableStub(
            name="users",
            display_name="Users",
            description="User accounts",
            column_count=5,
        )
        assert stub.name == "users"
        assert stub.display_name == "Users"
        assert stub.description == "User accounts"
        assert stub.column_count == 5

    def test_table_stub_frozen(self) -> None:
        stub = _make_table_stub()
        with pytest.raises(FrozenInstanceError):
            stub.name = "modified"  # type: ignore[misc]

    def test_table_stub_none_fields(self) -> None:
        stub = TableStub(
            name="raw_table",
            display_name=None,
            description=None,
            column_count=0,
        )
        assert stub.display_name is None
        assert stub.description is None
        assert stub.column_count == 0

    def test_database_stub_creation(self) -> None:
        stub = _make_database_stub()
        assert stub.name == "my_postgres"
        assert stub.display_name == "My Postgres"
        assert stub.description == "Main PostgreSQL database"
        assert stub.table_count == 2
        assert len(stub.tables) == 2
        assert len(stub.schema_tables) == 2
        assert stub.connector_id == "conn-db-1"
        assert stub.read_only is True
        assert stub.max_rows == 1000
        assert stub.query_timeout == 30

    def test_database_stub_frozen(self) -> None:
        stub = _make_database_stub()
        with pytest.raises(FrozenInstanceError):
            stub.name = "modified"  # type: ignore[misc]

    def test_database_stub_defaults(self) -> None:
        stub = DatabaseStub(
            name="minimal",
            display_name="Minimal",
            description=None,
            table_count=0,
        )
        assert stub.tables == []
        assert stub.schema_tables == []
        assert stub.db_config == {}
        assert stub.connector_id == ""
        assert stub.read_only is True
        assert stub.max_rows == 1000
        assert stub.query_timeout == 30


# ---------------------------------------------------------------------------
# Test: BaseTool protocol
# ---------------------------------------------------------------------------


class TestDatabaseMetaToolProtocol:
    """Verify the tool satisfies the BaseTool interface."""

    def test_name(self) -> None:
        tool = _make_meta_tool()
        assert tool.name == "database"

    def test_display_name(self) -> None:
        tool = _make_meta_tool()
        assert tool.display_name == "Database"

    def test_category(self) -> None:
        tool = _make_meta_tool()
        assert tool.category == "database"

    def test_description_contains_database_stubs(self) -> None:
        tool = _make_meta_tool()
        desc = tool.description
        assert "my_postgres" in desc
        assert "Main PostgreSQL database" in desc
        assert "analytics_db" in desc
        assert "Analytics data warehouse" in desc

    def test_description_shows_table_names(self) -> None:
        tool = _make_meta_tool()
        desc = tool.description
        assert "users" in desc
        assert "orders" in desc
        assert "events" in desc

    def test_description_shows_table_counts(self) -> None:
        tool = _make_meta_tool()
        desc = tool.description
        assert "2 tables" in desc
        assert "1 tables" in desc

    def test_description_shows_subcommands(self) -> None:
        tool = _make_meta_tool()
        desc = tool.description
        assert "list_tables" in desc
        assert "discover" in desc
        assert "query" in desc

    def test_description_truncates_many_tables(self) -> None:
        """When a database has >10 tables, the description truncates."""
        tables = [
            _make_table_stub(f"table_{i}", f"Table {i}", f"Desc {i}", 3)
            for i in range(15)
        ]
        stub = _make_database_stub(
            tables=tables,
            schema_tables=[
                _make_schema_table(f"table_{i}", f"Table {i}", f"Desc {i}")
                for i in range(15)
            ],
        )
        tool = _make_meta_tool(stubs=[stub])
        desc = tool.description
        assert "15 total" in desc

    def test_description_uses_display_name_when_no_description(self) -> None:
        """When description is None, fall back to display_name."""
        stub = _make_database_stub(description=None, display_name="Fallback Name")
        tool = _make_meta_tool(stubs=[stub])
        desc = tool.description
        assert "Fallback Name" in desc

    def test_parameters_schema_structure(self) -> None:
        tool = _make_meta_tool()
        schema = tool.parameters_schema
        assert schema["type"] == "object"
        props = schema["properties"]
        assert "subcommand" in props
        assert "database" in props
        assert "table" in props
        assert "sql" in props

        # subcommand enumerates the three commands
        assert props["subcommand"]["enum"] == ["list_tables", "discover", "query"]
        # database enumerates available names (sorted)
        assert sorted(props["database"]["enum"]) == ["analytics_db", "my_postgres"]

        # required fields
        assert "subcommand" in schema["required"]
        assert "database" in schema["required"]

    def test_parameters_schema_no_enum_when_empty_names(self) -> None:
        """When stubs are empty, database property has no enum."""
        tool = _make_meta_tool(stubs=[])
        schema = tool.parameters_schema
        assert "enum" not in schema["properties"]["database"]

    def test_database_names_property(self) -> None:
        tool = _make_meta_tool()
        assert tool.database_names == ["analytics_db", "my_postgres"]

    def test_stub_count_property(self) -> None:
        tool = _make_meta_tool()
        assert tool.stub_count == 2


# ---------------------------------------------------------------------------
# Test: list_tables subcommand
# ---------------------------------------------------------------------------


class TestListTables:
    """Test the list_tables subcommand."""

    async def test_list_tables_returns_table_info(self) -> None:
        tool = _make_meta_tool()
        result = await tool.run(subcommand="list_tables", database="my_postgres")
        data = json.loads(result)
        assert isinstance(data, list)
        assert len(data) == 2

        # First table: users
        users_entry = data[0]
        assert users_entry["table_name"] == "users"
        assert users_entry["display_name"] == "Users"
        assert users_entry["description"] == "User accounts table"
        assert users_entry["column_count"] == 3

    async def test_list_tables_unknown_database(self) -> None:
        tool = _make_meta_tool()
        result = await tool.run(subcommand="list_tables", database="nonexistent")
        assert "Unknown database" in result
        assert "nonexistent" in result
        assert "my_postgres" in result

    async def test_list_tables_omits_empty_fields(self) -> None:
        """Fields that are falsy should not appear in the output."""
        schema_table = {
            "table_name": "bare_table",
            "display_name": None,
            "description": None,
            "column_count": 0,
            "columns": [],
        }
        stub = _make_database_stub(
            tables=[_make_table_stub("bare_table", None, None, 0)],
            schema_tables=[schema_table],
        )
        tool = _make_meta_tool(stubs=[stub])
        result = await tool.run(subcommand="list_tables", database="my_postgres")
        data = json.loads(result)
        assert len(data) == 1
        entry = data[0]
        assert entry["table_name"] == "bare_table"
        assert "display_name" not in entry
        assert "description" not in entry
        assert "column_count" not in entry

    async def test_list_tables_calls_on_call_complete(self) -> None:
        callback = AsyncMock()
        tool = _make_meta_tool(on_call_complete=callback)
        await tool.run(subcommand="list_tables", database="my_postgres")

        callback.assert_awaited_once()
        call_kwargs = callback.call_args[1]
        assert call_kwargs["connector_id"] == "conn-db-1"
        assert call_kwargs["connector_name"] == "My Postgres"
        assert call_kwargs["action_name"] == "list_tables"
        assert call_kwargs["success"] is True
        assert call_kwargs["response_status"] == 200
        assert call_kwargs["request_method"] == "QUERY"
        assert "my_postgres" in call_kwargs["request_url"]


# ---------------------------------------------------------------------------
# Test: discover subcommand
# ---------------------------------------------------------------------------


class TestDiscover:
    """Test the discover subcommand."""

    async def test_discover_all_tables(self) -> None:
        tool = _make_meta_tool()
        result = await tool.run(subcommand="discover", database="my_postgres")
        assert "Database: my_postgres" in result
        assert "Tables (2):" in result
        assert "users" in result
        assert "orders" in result

    async def test_discover_shows_column_details(self) -> None:
        tool = _make_meta_tool()
        result = await tool.run(subcommand="discover", database="my_postgres")
        # Column info for users table
        assert "id" in result
        assert "INTEGER" in result
        assert "PK" in result
        assert "NOT NULL" in result
        # Column with display_name and description
        assert "email" in result
        assert "VARCHAR(255)" in result
        assert "Email Address" in result
        assert "User email" in result

    async def test_discover_single_table(self) -> None:
        tool = _make_meta_tool()
        result = await tool.run(
            subcommand="discover", database="my_postgres", table="users"
        )
        assert "Database: my_postgres" in result
        assert "Tables (1):" in result
        assert "users" in result
        # Should not contain the other table
        assert "orders" not in result.split("Tables (1):")[1]

    async def test_discover_nonexistent_table(self) -> None:
        tool = _make_meta_tool()
        result = await tool.run(
            subcommand="discover", database="my_postgres", table="nonexistent"
        )
        assert "Unknown table" in result
        assert "nonexistent" in result
        assert "users" in result  # lists available tables

    async def test_discover_unknown_database(self) -> None:
        tool = _make_meta_tool()
        result = await tool.run(subcommand="discover", database="nonexistent")
        assert "Unknown database" in result
        assert "nonexistent" in result

    async def test_discover_empty_schema(self) -> None:
        """Database with no visible tables."""
        stub = _make_database_stub(
            tables=[], schema_tables=[]
        )
        tool = _make_meta_tool(stubs=[stub])
        result = await tool.run(subcommand="discover", database="my_postgres")
        assert "no visible tables" in result

    async def test_discover_table_no_columns(self) -> None:
        """Table with no column info shows fallback message."""
        schema_table = {
            "table_name": "mystery_table",
            "display_name": None,
            "description": None,
            "columns": [],
        }
        stub = _make_database_stub(
            tables=[_make_table_stub("mystery_table", None, None, 0)],
            schema_tables=[schema_table],
        )
        tool = _make_meta_tool(stubs=[stub])
        result = await tool.run(subcommand="discover", database="my_postgres")
        assert "no column info" in result

    async def test_discover_table_with_description_and_display_name(self) -> None:
        """Verify display_name and description appear in formatted output."""
        tool = _make_meta_tool()
        result = await tool.run(
            subcommand="discover", database="my_postgres", table="orders"
        )
        assert "Orders" in result  # display_name
        assert "Customer orders" in result  # description
        assert "FK to users" in result  # column description


# ---------------------------------------------------------------------------
# Test: query subcommand
# ---------------------------------------------------------------------------


class TestQuery:
    """Test the query subcommand."""

    async def test_query_successful_execution(self) -> None:
        tool = _make_meta_tool()

        mock_result = MagicMock()
        mock_result.columns = ["id", "email"]
        mock_result.rows = [[1, "alice@example.com"], [2, "bob@example.com"]]
        mock_result.row_count = 2
        mock_result.execution_time_ms = 15
        mock_result.truncated = False

        mock_driver = AsyncMock()
        mock_driver.execute_query = AsyncMock(return_value=mock_result)

        mock_pool = MagicMock()
        mock_pool.get_driver = AsyncMock(return_value=mock_driver)

        with patch(
            "fim_one.core.tool.connector.database.meta_tool.ConnectionPoolManager"
        ) as MockPool:
            MockPool.get_instance.return_value = mock_pool

            result = await tool.run(
                subcommand="query",
                database="my_postgres",
                sql="SELECT id, email FROM users",
            )

        data = json.loads(result)
        assert data["columns"] == ["id", "email"]
        assert data["row_count"] == 2
        assert len(data["rows"]) == 2
        assert data["execution_time_ms"] == 15
        assert "truncated" not in data

    async def test_query_truncated_results(self) -> None:
        tool = _make_meta_tool()

        mock_result = MagicMock()
        mock_result.columns = ["id"]
        mock_result.rows = [[i] for i in range(1000)]
        mock_result.row_count = 1000
        mock_result.execution_time_ms = 50
        mock_result.truncated = True

        mock_driver = AsyncMock()
        mock_driver.execute_query = AsyncMock(return_value=mock_result)

        mock_pool = MagicMock()
        mock_pool.get_driver = AsyncMock(return_value=mock_driver)

        with patch(
            "fim_one.core.tool.connector.database.meta_tool.ConnectionPoolManager"
        ) as MockPool:
            MockPool.get_instance.return_value = mock_pool

            result = await tool.run(
                subcommand="query",
                database="my_postgres",
                sql="SELECT id FROM users",
            )

        data = json.loads(result)
        assert data["truncated"] is True
        assert "limited to" in data["note"]

    async def test_query_sql_safety_error(self) -> None:
        """Dangerous SQL like DROP TABLE should be rejected."""
        tool = _make_meta_tool()

        result = await tool.run(
            subcommand="query",
            database="my_postgres",
            sql="DROP TABLE users",
        )
        assert "SQL Safety Error" in result

    async def test_query_empty_sql(self) -> None:
        tool = _make_meta_tool()
        result = await tool.run(
            subcommand="query",
            database="my_postgres",
            sql="",
        )
        assert "Error" in result
        assert "sql" in result.lower()

    async def test_query_whitespace_only_sql(self) -> None:
        tool = _make_meta_tool()
        result = await tool.run(
            subcommand="query",
            database="my_postgres",
            sql="   ",
        )
        assert "Error" in result

    async def test_query_unknown_database(self) -> None:
        tool = _make_meta_tool()
        result = await tool.run(
            subcommand="query",
            database="nonexistent",
            sql="SELECT 1",
        )
        assert "Unknown database" in result
        assert "nonexistent" in result

    async def test_query_timeout_error(self) -> None:
        tool = _make_meta_tool()

        mock_driver = AsyncMock()
        mock_driver.execute_query = AsyncMock(
            side_effect=TimeoutError("Query exceeded 30s timeout")
        )

        mock_pool = MagicMock()
        mock_pool.get_driver = AsyncMock(return_value=mock_driver)

        with patch(
            "fim_one.core.tool.connector.database.meta_tool.ConnectionPoolManager"
        ) as MockPool:
            MockPool.get_instance.return_value = mock_pool

            result = await tool.run(
                subcommand="query",
                database="my_postgres",
                sql="SELECT * FROM huge_table",
            )

        assert "Timeout" in result

    async def test_query_generic_error(self) -> None:
        tool = _make_meta_tool()

        mock_driver = AsyncMock()
        mock_driver.execute_query = AsyncMock(
            side_effect=RuntimeError("Connection lost")
        )

        mock_pool = MagicMock()
        mock_pool.get_driver = AsyncMock(return_value=mock_driver)

        with patch(
            "fim_one.core.tool.connector.database.meta_tool.ConnectionPoolManager"
        ) as MockPool:
            MockPool.get_instance.return_value = mock_pool

            result = await tool.run(
                subcommand="query",
                database="my_postgres",
                sql="SELECT 1",
            )

        assert "Error" in result
        assert "Connection lost" in result

    async def test_query_calls_on_call_complete_on_success(self) -> None:
        callback = AsyncMock()
        tool = _make_meta_tool(on_call_complete=callback)

        mock_result = MagicMock()
        mock_result.columns = ["id"]
        mock_result.rows = [[1]]
        mock_result.row_count = 1
        mock_result.execution_time_ms = 5
        mock_result.truncated = False

        mock_driver = AsyncMock()
        mock_driver.execute_query = AsyncMock(return_value=mock_result)

        mock_pool = MagicMock()
        mock_pool.get_driver = AsyncMock(return_value=mock_driver)

        with patch(
            "fim_one.core.tool.connector.database.meta_tool.ConnectionPoolManager"
        ) as MockPool:
            MockPool.get_instance.return_value = mock_pool

            await tool.run(
                subcommand="query",
                database="my_postgres",
                sql="SELECT 1",
            )

        callback.assert_awaited_once()
        call_kwargs = callback.call_args[1]
        assert call_kwargs["success"] is True
        assert call_kwargs["action_name"] == "query"
        assert call_kwargs["response_status"] == 200

    async def test_query_calls_on_call_complete_on_failure(self) -> None:
        callback = AsyncMock()
        tool = _make_meta_tool(on_call_complete=callback)

        # Use SQL safety error (no need to mock pool)
        await tool.run(
            subcommand="query",
            database="my_postgres",
            sql="DROP TABLE users",
        )

        callback.assert_awaited_once()
        call_kwargs = callback.call_args[1]
        assert call_kwargs["success"] is False
        assert call_kwargs["response_status"] == 500
        assert call_kwargs["error_message"] is not None

    async def test_query_respects_read_only(self) -> None:
        """read_only=True should block write queries via validate_sql."""
        stub = _make_database_stub(read_only=True)
        tool = _make_meta_tool(stubs=[stub])
        result = await tool.run(
            subcommand="query",
            database="my_postgres",
            sql="INSERT INTO users (email) VALUES ('test@test.com')",
        )
        assert "SQL Safety Error" in result

    async def test_query_allows_write_when_not_read_only(self) -> None:
        """read_only=False should allow write queries."""
        stub = _make_database_stub(read_only=False)
        tool = _make_meta_tool(stubs=[stub])

        mock_result = MagicMock()
        mock_result.columns = []
        mock_result.rows = []
        mock_result.row_count = 0
        mock_result.execution_time_ms = 10
        mock_result.truncated = False

        mock_driver = AsyncMock()
        mock_driver.execute_query = AsyncMock(return_value=mock_result)

        mock_pool = MagicMock()
        mock_pool.get_driver = AsyncMock(return_value=mock_driver)

        with patch(
            "fim_one.core.tool.connector.database.meta_tool.ConnectionPoolManager"
        ) as MockPool:
            MockPool.get_instance.return_value = mock_pool

            result = await tool.run(
                subcommand="query",
                database="my_postgres",
                sql="INSERT INTO users (email) VALUES ('test@test.com')",
            )

        # Should succeed (no safety error)
        assert "SQL Safety Error" not in result
        data = json.loads(result)
        assert data["row_count"] == 0


# ---------------------------------------------------------------------------
# Test: error handling / edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    """Miscellaneous edge case tests."""

    async def test_unknown_subcommand(self) -> None:
        tool = _make_meta_tool()
        result = await tool.run(subcommand="delete", database="my_postgres")
        assert "Unknown subcommand" in result
        assert "delete" in result

    async def test_missing_subcommand(self) -> None:
        tool = _make_meta_tool()
        result = await tool.run(database="my_postgres")
        assert "'subcommand' is required" in result

    async def test_missing_database(self) -> None:
        tool = _make_meta_tool()
        result = await tool.run(subcommand="discover")
        assert "'database' is required" in result

    async def test_empty_string_subcommand(self) -> None:
        tool = _make_meta_tool()
        result = await tool.run(subcommand="", database="my_postgres")
        assert "'subcommand' is required" in result

    async def test_empty_string_database(self) -> None:
        tool = _make_meta_tool()
        result = await tool.run(subcommand="discover", database="")
        assert "'database' is required" in result

    async def test_on_call_complete_exception_does_not_propagate(self) -> None:
        """If the callback raises, the tool should not fail."""
        callback = AsyncMock(side_effect=RuntimeError("callback boom"))
        tool = _make_meta_tool(on_call_complete=callback)
        # list_tables calls _log_call -- should not raise
        result = await tool.run(subcommand="list_tables", database="my_postgres")
        # Should still return valid JSON despite callback failure
        data = json.loads(result)
        assert isinstance(data, list)


# ---------------------------------------------------------------------------
# Test: build_database_meta_tool factory
# ---------------------------------------------------------------------------


class TestBuildDatabaseMetaTool:
    """Test the factory function that builds from ORM connector data."""

    def _make_mock_connector(
        self,
        conn_id: str = "conn-1",
        name: str = "My Postgres DB",
        description: str = "Main database",
    ) -> MagicMock:
        conn = MagicMock()
        conn.id = conn_id
        conn.name = name
        conn.description = description
        return conn

    def _make_db_config(
        self,
        read_only: bool = True,
        max_rows: int = 500,
        query_timeout: int = 60,
    ) -> dict:
        return {
            "driver": "postgresql",
            "host": "localhost",
            "port": 5432,
            "database": "testdb",
            "read_only": read_only,
            "max_rows": max_rows,
            "query_timeout": query_timeout,
        }

    def _make_schema_tables(self) -> list[dict]:
        return [
            {
                "table_name": "users",
                "display_name": "Users",
                "description": "User table",
                "column_count": 3,
                "columns": [
                    {
                        "column_name": "id",
                        "data_type": "INTEGER",
                        "is_primary_key": True,
                        "is_nullable": False,
                    },
                    {
                        "column_name": "email",
                        "data_type": "VARCHAR",
                        "is_primary_key": False,
                        "is_nullable": False,
                    },
                    {
                        "column_name": "name",
                        "data_type": "VARCHAR",
                        "is_primary_key": False,
                        "is_nullable": True,
                    },
                ],
            },
            {
                "table_name": "orders",
                "display_name": None,
                "description": None,
                "columns": [
                    {
                        "column_name": "id",
                        "data_type": "INTEGER",
                        "is_primary_key": True,
                        "is_nullable": False,
                    },
                ],
            },
        ]

    def test_builds_meta_tool_from_orm(self) -> None:
        conn = self._make_mock_connector()
        db_config = self._make_db_config()
        schema_tables = self._make_schema_tables()

        meta_tool = build_database_meta_tool(
            [(conn, db_config, schema_tables)],
            on_call_complete=None,
        )

        assert isinstance(meta_tool, DatabaseMetaTool)
        assert meta_tool.stub_count == 1
        assert "my_postgres_db" in meta_tool.database_names

    def test_sanitizes_connector_name(self) -> None:
        conn = self._make_mock_connector(name="My Awesome DB!")
        db_config = self._make_db_config()
        schema_tables = self._make_schema_tables()

        meta_tool = build_database_meta_tool(
            [(conn, db_config, schema_tables)],
            on_call_complete=None,
        )
        assert "my_awesome_db" in meta_tool.database_names

    def test_sanitizes_special_characters(self) -> None:
        conn = self._make_mock_connector(name="  --DB@#$%  ")
        db_config = self._make_db_config()
        schema_tables = self._make_schema_tables()

        meta_tool = build_database_meta_tool(
            [(conn, db_config, schema_tables)],
            on_call_complete=None,
        )
        names = meta_tool.database_names
        assert len(names) == 1
        # Name should only contain alphanumeric + underscores
        name = names[0]
        assert all(c.isalnum() or c == "_" for c in name)

    def test_multiple_connectors(self) -> None:
        conn1 = self._make_mock_connector(conn_id="c1", name="Postgres DB")
        conn2 = self._make_mock_connector(
            conn_id="c2", name="MySQL DB", description="Analytics"
        )
        db_config = self._make_db_config()
        schema_tables = self._make_schema_tables()

        meta_tool = build_database_meta_tool([
            (conn1, db_config, schema_tables),
            (conn2, db_config, schema_tables),
        ],
        on_call_complete=None,)
        assert meta_tool.stub_count == 2
        assert sorted(meta_tool.database_names) == ["mysql_db", "postgres_db"]

    def test_empty_connectors_list(self) -> None:
        meta_tool = build_database_meta_tool([], on_call_complete=None)
        assert meta_tool.stub_count == 0
        assert meta_tool.database_names == []

    def test_table_stubs_built_correctly(self) -> None:
        conn = self._make_mock_connector()
        db_config = self._make_db_config()
        schema_tables = self._make_schema_tables()

        meta_tool = build_database_meta_tool(
            [(conn, db_config, schema_tables)],
            on_call_complete=None,
        )

        # Access internal stub to verify table stubs
        stub = meta_tool._stubs["my_postgres_db"]
        assert len(stub.tables) == 2
        assert stub.tables[0].name == "users"
        assert stub.tables[0].display_name == "Users"
        assert stub.tables[0].description == "User table"
        assert stub.tables[0].column_count == 3
        # orders has no column_count key -- should fallback to len(columns)
        assert stub.tables[1].name == "orders"
        assert stub.tables[1].column_count == 1

    def test_db_config_propagated(self) -> None:
        conn = self._make_mock_connector()
        db_config = self._make_db_config(
            read_only=False, max_rows=200, query_timeout=15
        )
        schema_tables = self._make_schema_tables()

        meta_tool = build_database_meta_tool(
            [(conn, db_config, schema_tables)],
            on_call_complete=None,
        )

        stub = meta_tool._stubs["my_postgres_db"]
        assert stub.read_only is False
        assert stub.max_rows == 200
        assert stub.query_timeout == 15
        assert stub.db_config is db_config

    def test_db_config_defaults(self) -> None:
        """Config without read_only/max_rows/query_timeout uses defaults."""
        conn = self._make_mock_connector()
        db_config = {"driver": "postgresql", "host": "localhost"}
        schema_tables = self._make_schema_tables()

        meta_tool = build_database_meta_tool(
            [(conn, db_config, schema_tables)],
            on_call_complete=None,
        )

        stub = meta_tool._stubs["my_postgres_db"]
        assert stub.read_only is True
        assert stub.max_rows == 1000
        assert stub.query_timeout == 30

    def test_connector_id_propagated(self) -> None:
        conn = self._make_mock_connector(conn_id="my-uuid-123")
        db_config = self._make_db_config()
        schema_tables = self._make_schema_tables()

        meta_tool = build_database_meta_tool(
            [(conn, db_config, schema_tables)],
            on_call_complete=None,
        )

        stub = meta_tool._stubs["my_postgres_db"]
        assert stub.connector_id == "my-uuid-123"

    def test_description_fallback_to_name(self) -> None:
        """When connector.description is None, fall back to connector.name."""
        conn = self._make_mock_connector(description=None)
        conn.description = None
        db_config = self._make_db_config()
        schema_tables = self._make_schema_tables()

        meta_tool = build_database_meta_tool(
            [(conn, db_config, schema_tables)],
            on_call_complete=None,
        )

        stub = meta_tool._stubs["my_postgres_db"]
        assert stub.description == "My Postgres DB"

    def test_on_call_complete_forwarded(self) -> None:
        callback = AsyncMock()
        conn = self._make_mock_connector()
        db_config = self._make_db_config()
        schema_tables = self._make_schema_tables()

        meta_tool = build_database_meta_tool(
            [(conn, db_config, schema_tables)],
            on_call_complete=callback,
        )
        assert meta_tool._on_call_complete is callback

    def test_fallback_name_when_empty(self) -> None:
        """Connector name that sanitizes to empty gets a fallback."""
        conn = self._make_mock_connector(name="@#$%^&")
        db_config = self._make_db_config()
        schema_tables = self._make_schema_tables()

        meta_tool = build_database_meta_tool(
            [(conn, db_config, schema_tables)],
            on_call_complete=None,
        )
        names = meta_tool.database_names
        assert len(names) == 1
        assert names[0].startswith("db_")


# ---------------------------------------------------------------------------
# Test: get_database_tool_mode
# ---------------------------------------------------------------------------


class TestGetDatabaseToolMode:
    """Test the feature flag resolution logic."""

    def test_default_is_progressive(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("DATABASE_TOOL_MODE", None)
            assert get_database_tool_mode() == "progressive"

    def test_env_var_legacy(self) -> None:
        with patch.dict(os.environ, {"DATABASE_TOOL_MODE": "legacy"}):
            assert get_database_tool_mode() == "legacy"

    def test_env_var_progressive(self) -> None:
        with patch.dict(os.environ, {"DATABASE_TOOL_MODE": "progressive"}):
            assert get_database_tool_mode() == "progressive"

    def test_env_var_invalid_falls_back(self) -> None:
        with patch.dict(os.environ, {"DATABASE_TOOL_MODE": "invalid"}):
            assert get_database_tool_mode() == "progressive"

    def test_agent_config_overrides_env(self) -> None:
        agent_cfg = {
            "model_config_json": {"database_tool_mode": "legacy"},
        }
        with patch.dict(os.environ, {"DATABASE_TOOL_MODE": "progressive"}):
            assert get_database_tool_mode(agent_cfg) == "legacy"

    def test_agent_config_progressive(self) -> None:
        agent_cfg = {
            "model_config_json": {"database_tool_mode": "progressive"},
        }
        assert get_database_tool_mode(agent_cfg) == "progressive"

    def test_agent_config_invalid_falls_to_env(self) -> None:
        agent_cfg = {
            "model_config_json": {"database_tool_mode": "invalid"},
        }
        with patch.dict(os.environ, {"DATABASE_TOOL_MODE": "legacy"}):
            assert get_database_tool_mode(agent_cfg) == "legacy"

    def test_agent_config_none_model_config(self) -> None:
        agent_cfg = {"model_config_json": None}
        with patch.dict(os.environ, {"DATABASE_TOOL_MODE": "legacy"}):
            assert get_database_tool_mode(agent_cfg) == "legacy"

    def test_agent_config_no_model_config_key(self) -> None:
        agent_cfg = {}
        with patch.dict(os.environ, {"DATABASE_TOOL_MODE": "legacy"}):
            assert get_database_tool_mode(agent_cfg) == "legacy"

    def test_agent_config_none(self) -> None:
        with patch.dict(os.environ, {"DATABASE_TOOL_MODE": "legacy"}):
            assert get_database_tool_mode(None) == "legacy"

    def test_env_var_case_insensitive(self) -> None:
        with patch.dict(os.environ, {"DATABASE_TOOL_MODE": "PROGRESSIVE"}):
            assert get_database_tool_mode() == "progressive"

        with patch.dict(os.environ, {"DATABASE_TOOL_MODE": "Legacy"}):
            assert get_database_tool_mode() == "legacy"


# ---------------------------------------------------------------------------
# Test: name deduplication
# ---------------------------------------------------------------------------


class TestNameDeduplication:
    """Verify that connectors with colliding safe_names get deduplicated."""

    def _make_mock_connector(
        self,
        conn_id: str = "conn-1",
        name: str = "My DB",
        description: str = "A database",
    ) -> MagicMock:
        conn = MagicMock()
        conn.id = conn_id
        conn.name = name
        conn.description = description
        return conn

    def test_chinese_names_that_collapse_to_same_safe_name(self) -> None:
        """Two connectors whose names differ only in CJK chars must not collide."""
        conn_a = self._make_mock_connector("aaaa-1111", "智合staging", "DB A")
        conn_b = self._make_mock_connector("bbbb-2222", "测试staging", "DB B")
        schema: list[dict[str, Any]] = []
        db_config: dict[str, Any] = {"read_only": True}

        tool = build_database_meta_tool([
            (conn_a, db_config, schema),
            (conn_b, db_config, schema),
        ],
        on_call_complete=None,)

        names = list(tool._stubs.keys())
        assert len(names) == 2, f"Expected 2 stubs, got {len(names)}: {names}"
        assert "staging" in names
        assert any("bbbb" in n for n in names), (
            f"Colliding name should contain connector ID prefix: {names}"
        )

    def test_pure_ascii_duplicates(self) -> None:
        """Two connectors with identical ASCII names get deduplicated."""
        conn_a = self._make_mock_connector("aaaa-1111", "staging", "DB A")
        conn_b = self._make_mock_connector("bbbb-2222", "staging", "DB B")
        schema: list[dict[str, Any]] = []
        db_config: dict[str, Any] = {"read_only": True}

        tool = build_database_meta_tool([
            (conn_a, db_config, schema),
            (conn_b, db_config, schema),
        ],
        on_call_complete=None,)

        names = list(tool._stubs.keys())
        assert len(names) == 2, f"Expected 2 stubs, got {len(names)}: {names}"

    def test_no_collision_no_suffix(self) -> None:
        """Distinct names should not get a suffix."""
        conn_a = self._make_mock_connector("aaaa-1111", "staging", "DB A")
        conn_b = self._make_mock_connector("bbbb-2222", "production", "DB B")
        schema: list[dict[str, Any]] = []
        db_config: dict[str, Any] = {"read_only": True}

        tool = build_database_meta_tool([
            (conn_a, db_config, schema),
            (conn_b, db_config, schema),
        ],
        on_call_complete=None,)

        names = list(tool._stubs.keys())
        assert names == ["staging", "production"]


# ---------------------------------------------------------------------------
# Test: token efficiency
# ---------------------------------------------------------------------------


class TestTokenEfficiency:
    """Verify progressive mode produces compact descriptions."""

    def test_description_shorter_than_individual_tools(self) -> None:
        """With 10 databases x 5 tables each, description should be compact."""
        stubs = []
        for i in range(10):
            tables = [
                _make_table_stub(
                    f"table_{j}",
                    f"Table {j}",
                    f"Description for table {j} with some detail",
                    10,
                )
                for j in range(5)
            ]
            stubs.append(
                _make_database_stub(
                    name=f"database_{i}",
                    display_name=f"Database {i}",
                    description=f"Service {i} database with various tables",
                    tables=tables,
                    schema_tables=[
                        _make_schema_table(
                            f"table_{j}", f"Table {j}", f"Desc {j}"
                        )
                        for j in range(5)
                    ],
                    connector_id=f"conn-{i}",
                )
            )

        tool = _make_meta_tool(stubs=stubs)
        desc = tool.description

        # 10 databases with 5 tables each -- description should be compact
        desc_words = len(desc.split())
        assert desc_words < 300, (
            f"Meta tool description has {desc_words} words -- should be compact"
        )
