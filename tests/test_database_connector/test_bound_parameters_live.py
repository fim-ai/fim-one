"""A value bound to a query step's SQL stays a value, on a real database.

The step's SQL is ``... WHERE customer = {{customer}}``; the value comes from
whoever triggers the workflow. The payloads below read another table, force
an error that quotes it, or widen the WHERE clause when the value is pasted
into the SQL text. Bound, each of them is a customer name nobody has.

Runs against PostgreSQL 16 with ``FIM_TEST_PG_URL`` and MySQL 8 with
``FIM_TEST_MYSQL_URL``; skipped without them.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from fim_one.core.tool.connector.database.adapter import DatabaseToolAdapter, _DatabaseQueryTool

from .conftest import SECRET, LiveDatabase

pytestmark = pytest.mark.timeout(60)


def _payloads(secrets: str) -> list[str]:
    return [
        f"x' UNION SELECT id, token FROM {secrets} --",
        f"x' UNION SELECT id, token FROM {secrets} -- ",
        f"x' AND 1 = CAST((SELECT token FROM {secrets} LIMIT 1) AS integer) --",
        f"x' AND extractvalue(1, concat(0x7e, (SELECT token FROM {secrets} LIMIT 1))) -- ",
        "x'/**/OR/**/1=1--",
        "x' OR '1'='1",
        "x' OR 1=1 #",
        "x'; DROP TABLE orders; --",
        "x\\' OR 1=1 -- ",
        "x\\\\' OR 1=1 -- ",
        'x" OR ""="',
        "x%' OR 1=1 -- ",
        "$1",
        "%s",
        "{{customer}}",
    ]


async def _query(live: LiveDatabase, sql: str, values: dict[str, Any]) -> str:
    tool = DatabaseToolAdapter.create_tools(
        connector_name="live",
        connector_id=live.connector_id,
        db_config=live.config,
        schema_tables=[],
        on_call_complete=None,
    )[2]
    assert isinstance(tool, _DatabaseQueryTool)

    async def resolve(name: str) -> Any:
        return values[name]

    return await tool.run_with_variables(sql, resolve)


class _BoundValues:
    """The same checks for each database; subclasses name the fixture."""

    async def _check_payloads(self, live: LiveDatabase) -> None:
        sql = f"SELECT id, customer FROM {live.orders} WHERE customer = {{{{customer}}}}"
        for payload in _payloads(live.secrets):
            text = await _query(live, sql, {"customer": payload})
            assert SECRET not in text, payload
            assert not text.startswith("["), (payload, text)
            assert json.loads(text)["rows"] == [], payload

    async def _check_plain_values(self, live: LiveDatabase) -> None:
        sql = f"SELECT id FROM {live.orders} WHERE customer = {{{{customer}}}}"
        # A value with a quote, a backslash and a comment marker is found
        # as the text it is.
        text = await _query(live, sql, {"customer": "O'Brien \\ -- x"})
        assert json.loads(text)["rows"] == [[2]]
        text = await _query(live, sql, {"customer": "alice"})
        assert json.loads(text)["rows"] == [[1]]

    async def _check_typed_values(self, live: LiveDatabase) -> None:
        by_id = f"SELECT customer FROM {live.orders} WHERE id = {{{{id}}}}"
        for value in (1, "1", 1.0):
            text = await _query(live, by_id, {"id": value})
            assert json.loads(text)["rows"] == [["alice"]], value
        text = await _query(live, by_id, {"id": None})
        assert json.loads(text)["rows"] == []
        limited = f"SELECT id FROM {live.orders} ORDER BY id LIMIT {{{{n}}}}"
        text = await _query(live, limited, {"n": 1})
        assert json.loads(text)["rows"] == [[1]]

    async def _check_number_payload(self, live: LiveDatabase) -> None:
        by_id = f"SELECT customer FROM {live.orders} WHERE id = {{{{id}}}}"
        text = await _query(live, by_id, {"id": f"0 OR 1=1 UNION SELECT token FROM {live.secrets}"})
        assert SECRET not in text
        assert "alice" not in text and "Brien" not in text


class TestPostgreSQL(_BoundValues):
    async def test_injection_payloads_match_nothing(self, live_pg: LiveDatabase) -> None:
        await self._check_payloads(live_pg)

    async def test_a_value_with_sql_punctuation_is_plain_text(self, live_pg: LiveDatabase) -> None:
        await self._check_plain_values(live_pg)

    async def test_numbers_text_numbers_and_null_are_bound_in_type(
        self, live_pg: LiveDatabase
    ) -> None:
        await self._check_typed_values(live_pg)

    async def test_sql_in_a_number_is_not_run(self, live_pg: LiveDatabase) -> None:
        await self._check_number_payload(live_pg)

    async def test_a_list_is_bound_as_json_text(self, live_pg: LiveDatabase) -> None:
        text = await _query(
            live_pg, "SELECT {{tags}}::jsonb ->> 1 AS second", {"tags": ["a", "b'--"]}
        )
        assert json.loads(text)["rows"] == [["b'--"]]

    async def test_a_quoted_reference_runs_nothing(self, live_pg: LiveDatabase) -> None:
        text = await _query(
            live_pg,
            f"SELECT id FROM {live_pg.orders} WHERE customer = '{{{{customer}}}}'",
            {"customer": "alice"},
        )
        assert text.startswith("[SQL Safety Error]")
        assert "WHERE id = {{variable}}" in text


class TestMySQL(_BoundValues):
    async def test_injection_payloads_match_nothing(self, live_mysql: LiveDatabase) -> None:
        await self._check_payloads(live_mysql)

    async def test_a_value_with_sql_punctuation_is_plain_text(
        self, live_mysql: LiveDatabase
    ) -> None:
        await self._check_plain_values(live_mysql)

    async def test_numbers_text_numbers_and_null_are_bound_in_type(
        self, live_mysql: LiveDatabase
    ) -> None:
        await self._check_typed_values(live_mysql)

    async def test_sql_in_a_number_is_not_run(self, live_mysql: LiveDatabase) -> None:
        await self._check_number_payload(live_mysql)

    async def test_a_literal_percent_sign_survives_binding(self, live_mysql: LiveDatabase) -> None:
        text = await _query(
            live_mysql,
            f"SELECT id FROM {live_mysql.orders} WHERE customer LIKE 'ali%' AND id = {{{{id}}}}",
            {"id": 1},
        )
        assert json.loads(text)["rows"] == [[1]]
