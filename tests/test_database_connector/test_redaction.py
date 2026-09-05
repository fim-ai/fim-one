"""Tests for PII column redaction in database connector results."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from fim_one.core.tool.connector.database.base import QueryResult
from fim_one.core.tool.connector.database.redaction import (
    MASK,
    pii_column_names,
    redact_rows,
)


@pytest.fixture()
def schema_tables() -> list[dict[str, Any]]:
    """One table whose ``salary`` column is marked as PII."""
    return [
        {
            "table_name": "employees",
            "display_name": "Employees",
            "description": None,
            "column_count": 3,
            "columns": [
                {
                    "column_name": "id",
                    "data_type": "integer",
                    "is_nullable": False,
                    "is_primary_key": True,
                    "is_pii": False,
                },
                {
                    "column_name": "name",
                    "data_type": "varchar",
                    "is_nullable": False,
                    "is_primary_key": False,
                    "is_pii": False,
                },
                {
                    "column_name": "salary",
                    "data_type": "numeric",
                    "is_nullable": True,
                    "is_primary_key": False,
                    "is_pii": True,
                },
            ],
        }
    ]


class TestPiiColumnNames:
    def test_collects_marked_columns_lowercased(self) -> None:
        tables = [
            {"columns": [{"column_name": "Salary", "is_pii": True}]},
            {"columns": [{"column_name": "ID_Card", "is_pii": True}]},
        ]
        assert pii_column_names(tables) == frozenset({"salary", "id_card"})

    def test_ignores_unmarked_and_blank(self, schema_tables: list[dict[str, Any]]) -> None:
        assert pii_column_names(schema_tables) == frozenset({"salary"})

    def test_empty_when_nothing_marked(self) -> None:
        tables = [{"columns": [{"column_name": "id", "is_pii": False}]}]
        assert pii_column_names(tables) == frozenset()

    def test_table_without_columns_key(self) -> None:
        assert pii_column_names([{"table_name": "t"}]) == frozenset()


class TestRedactRows:
    def test_masks_matching_column_only(self) -> None:
        rows, redacted = redact_rows(
            ["id", "name", "salary"],
            [[1, "Ann", 9000], [2, "Bo", 7000]],
            frozenset({"salary"}),
        )
        assert rows == [[1, "Ann", MASK], [2, "Bo", MASK]]
        assert redacted == ["salary"]

    def test_match_is_case_insensitive(self) -> None:
        rows, redacted = redact_rows([" Salary "], [[100]], frozenset({"salary"}))
        assert rows == [[MASK]]
        assert redacted == [" Salary "]

    def test_table_qualifier_is_stripped(self) -> None:
        rows, redacted = redact_rows(
            ["e.salary", "d.name"], [[100, "Ops"]], frozenset({"salary"})
        )
        assert rows == [[MASK, "Ops"]]
        assert redacted == ["e.salary"]

    def test_null_stays_null(self) -> None:
        # Masking a NULL would tell the model a value exists.
        rows, _ = redact_rows(["salary"], [[None]], frozenset({"salary"}))
        assert rows == [[None]]

    def test_no_pii_returns_rows_untouched(self) -> None:
        original = [[1, "Ann"]]
        rows, redacted = redact_rows(["id", "name"], original, frozenset())
        assert rows is original
        assert redacted == []

    def test_no_matching_column_returns_rows_untouched(self) -> None:
        original = [[1, "Ann"]]
        rows, redacted = redact_rows(
            ["id", "name"], original, frozenset({"salary"})
        )
        assert rows is original
        assert redacted == []

    def test_does_not_mutate_input_rows(self) -> None:
        original = [[1, 9000]]
        redact_rows(["id", "salary"], original, frozenset({"salary"}))
        assert original == [[1, 9000]]

    def test_row_shorter_than_columns(self) -> None:
        rows, _ = redact_rows(["id", "salary"], [[1]], frozenset({"salary"}))
        assert rows == [[1]]

    def test_multiple_pii_columns_reported_in_result_order(self) -> None:
        _, redacted = redact_rows(
            ["salary", "id", "phone"],
            [[1, 2, 3]],
            frozenset({"phone", "salary"}),
        )
        assert redacted == ["salary", "phone"]


def _driver_returning(result: QueryResult) -> MagicMock:
    driver = MagicMock()
    driver.execute_query = AsyncMock(return_value=result)
    return driver


class TestQueryToolRedaction:
    """The adapter-mode query tool masks PII before the model sees rows."""

    @pytest.mark.asyncio
    async def test_query_output_is_masked_and_annotated(
        self, schema_tables: list[dict[str, Any]]
    ) -> None:
        from fim_one.core.tool.connector.database.adapter import DatabaseToolAdapter

        tools = DatabaseToolAdapter.create_tools(
            connector_name="hr",
            connector_id="c1",
            db_config={"type": "postgresql", "read_only": True},
            schema_tables=schema_tables,
        )
        query_tool = next(t for t in tools if t.name.endswith("__query"))

        result = QueryResult(
            columns=["name", "salary"],
            rows=[["Ann", 9000], ["Bo", 7000]],
            row_count=2,
        )
        pool = MagicMock()
        pool.get_driver = AsyncMock(return_value=_driver_returning(result))

        with patch(
            "fim_one.core.tool.connector.database.adapter."
            "ConnectionPoolManager.get_instance",
            return_value=pool,
        ):
            output = await query_tool.run(sql="SELECT name, salary FROM employees")

        payload = json.loads(output)
        assert payload["rows"] == [["Ann", MASK], ["Bo", MASK]]
        assert payload["redacted_columns"] == ["salary"]
        assert "redaction_note" in payload

    @pytest.mark.asyncio
    async def test_select_star_is_masked_too(
        self, schema_tables: list[dict[str, Any]]
    ) -> None:
        # The model never asked for the column; the driver returned it anyway.
        from fim_one.core.tool.connector.database.adapter import DatabaseToolAdapter

        tools = DatabaseToolAdapter.create_tools(
            connector_name="hr",
            connector_id="c1",
            db_config={"type": "postgresql", "read_only": True},
            schema_tables=schema_tables,
        )
        query_tool = next(t for t in tools if t.name.endswith("__query"))

        result = QueryResult(
            columns=["id", "name", "salary"],
            rows=[[1, "Ann", 9000]],
            row_count=1,
        )
        pool = MagicMock()
        pool.get_driver = AsyncMock(return_value=_driver_returning(result))

        with patch(
            "fim_one.core.tool.connector.database.adapter."
            "ConnectionPoolManager.get_instance",
            return_value=pool,
        ):
            output = await query_tool.run(sql="SELECT * FROM employees")

        assert json.loads(output)["rows"] == [[1, "Ann", MASK]]

    @pytest.mark.asyncio
    async def test_no_pii_column_leaves_output_clean(self) -> None:
        from fim_one.core.tool.connector.database.adapter import DatabaseToolAdapter

        tools = DatabaseToolAdapter.create_tools(
            connector_name="hr",
            connector_id="c1",
            db_config={"type": "postgresql", "read_only": True},
            schema_tables=[
                {
                    "table_name": "depts",
                    "column_count": 1,
                    "columns": [{"column_name": "name", "data_type": "varchar"}],
                }
            ],
        )
        query_tool = next(t for t in tools if t.name.endswith("__query"))

        pool = MagicMock()
        pool.get_driver = AsyncMock(
            return_value=_driver_returning(
                QueryResult(columns=["name"], rows=[["Ops"]], row_count=1)
            )
        )

        with patch(
            "fim_one.core.tool.connector.database.adapter."
            "ConnectionPoolManager.get_instance",
            return_value=pool,
        ):
            output = await query_tool.run(sql="SELECT name FROM depts")

        payload = json.loads(output)
        assert payload["rows"] == [["Ops"]]
        assert "redacted_columns" not in payload

    @pytest.mark.asyncio
    async def test_call_log_records_the_fences(
        self, schema_tables: list[dict[str, Any]]
    ) -> None:
        from fim_one.core.tool.connector.database.adapter import DatabaseToolAdapter

        logged: list[dict[str, Any]] = []

        async def on_call_complete(**kwargs: Any) -> None:
            logged.append(kwargs)

        tools = DatabaseToolAdapter.create_tools(
            connector_name="hr",
            connector_id="c1",
            db_config={"type": "postgresql", "read_only": True},
            schema_tables=schema_tables,
            on_call_complete=on_call_complete,
        )
        query_tool = next(t for t in tools if t.name.endswith("__query"))

        pool = MagicMock()
        pool.get_driver = AsyncMock(
            return_value=_driver_returning(
                QueryResult(columns=["salary"], rows=[[9000]], row_count=1)
            )
        )

        with patch(
            "fim_one.core.tool.connector.database.adapter."
            "ConnectionPoolManager.get_instance",
            return_value=pool,
        ):
            await query_tool.run(sql="SELECT salary FROM employees")

        assert logged, "the query should have been logged"
        fences = json.loads(logged[-1]["scope_rules_applied"])
        assert fences == {"read_only": True, "redacted_columns": ["salary"]}


class TestMetaToolRedaction:
    """Progressive (meta-tool) mode masks the same way."""

    @pytest.mark.asyncio
    async def test_query_subcommand_masks(
        self, schema_tables: list[dict[str, Any]]
    ) -> None:
        from fim_one.core.tool.connector.database.meta_tool import (
            DatabaseMetaTool,
            DatabaseStub,
        )

        stub = DatabaseStub(
            name="hr",
            display_name="HR",
            description=None,
            table_count=1,
            schema_tables=schema_tables,
            db_config={"type": "postgresql"},
            connector_id="c1",
            read_only=True,
        )
        tool = DatabaseMetaTool([stub])

        pool = MagicMock()
        pool.get_driver = AsyncMock(
            return_value=_driver_returning(
                QueryResult(
                    columns=["name", "salary"], rows=[["Ann", 9000]], row_count=1
                )
            )
        )

        with patch(
            "fim_one.core.tool.connector.database.meta_tool."
            "ConnectionPoolManager.get_instance",
            return_value=pool,
        ):
            output = await tool.run(
                subcommand="query", database="hr", sql="SELECT name, salary FROM employees"
            )

        payload = json.loads(output)
        assert payload["rows"] == [["Ann", MASK]]
        assert payload["redacted_columns"] == ["salary"]

    @pytest.mark.asyncio
    async def test_discover_marks_the_column(
        self, schema_tables: list[dict[str, Any]]
    ) -> None:
        from fim_one.core.tool.connector.database.meta_tool import (
            DatabaseMetaTool,
            DatabaseStub,
        )

        stub = DatabaseStub(
            name="hr",
            display_name="HR",
            description=None,
            table_count=1,
            schema_tables=schema_tables,
            db_config={"type": "postgresql"},
            connector_id="c1",
        )
        tool = DatabaseMetaTool([stub])

        output = await tool.run(subcommand="discover", database="hr")

        # The model must know the column exists, and that its values are masked.
        assert "salary" in output
        assert "PII" in output
