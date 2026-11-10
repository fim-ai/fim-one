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

    async def _check_lists(self, live: LiveDatabase) -> None:
        in_list = f"SELECT id FROM {live.orders} WHERE id IN ({{{{ids}}}}) ORDER BY id"
        assert json.loads(await _query(live, in_list, {"ids": [1, 2, 9]}))["rows"] == [[1], [2]]
        assert json.loads(await _query(live, in_list, {"ids": ["2"]}))["rows"] == [[2]]
        assert json.loads(await _query(live, in_list, {"ids": []}))["rows"] == []
        # Everything is "not in" an empty list.
        not_in = f"SELECT id FROM {live.orders} WHERE id NOT IN ({{{{ids}}}}) ORDER BY id"
        assert json.loads(await _query(live, not_in, {"ids": []}))["rows"] == [[1], [2]]
        assert json.loads(await _query(live, not_in, {"ids": [1]}))["rows"] == [[2]]
        text_col = f"SELECT id FROM {live.orders} WHERE customer NOT IN ({{{{ids}}}}) ORDER BY id"
        assert json.loads(await _query(live, text_col, {"ids": []}))["rows"] == [[1], [2]]
        assert json.loads(await _query(live, in_list, {"ids": 1}))["rows"] == [[1]]
        names = f"SELECT id FROM {live.orders} WHERE customer IN ({{{{names}}}})"
        payload = ["alice') OR ('1'='1", f"x' UNION SELECT token FROM {live.secrets} --"]
        text = await _query(live, names, {"names": payload})
        assert SECRET not in text
        assert json.loads(text)["rows"] == []

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

    async def test_a_list_in_an_in_list_is_bound_item_by_item(self, live_pg: LiveDatabase) -> None:
        await self._check_lists(live_pg)

    async def test_a_list_longer_than_the_parameter_limit_says_what_to_write(
        self, live_pg: LiveDatabase
    ) -> None:
        ids = list(range(40000))
        in_list = f"SELECT count(*) FROM {live_pg.orders} WHERE id IN ({{{{ids}}}})"
        text = await _query(live_pg, in_list, {"ids": ids})
        assert text.startswith("[SQL Safety Error]")
        assert "32767" in text and "= ANY({{list}})" in text
        any_of = f"SELECT count(*) FROM {live_pg.orders} WHERE id = ANY({{{{ids}}}})"
        assert json.loads(await _query(live_pg, any_of, {"ids": ids}))["rows"] == [[2]]

    async def test_a_list_fits_an_array_parameter(self, live_pg: LiveDatabase) -> None:
        any_of = f"SELECT id FROM {live_pg.orders} WHERE id = ANY({{{{ids}}}}) ORDER BY id"
        assert json.loads(await _query(live_pg, any_of, {"ids": [2, "1"]}))["rows"] == [[1], [2]]

    async def test_a_number_with_a_fraction_does_not_match_an_integer(
        self, live_pg: LiveDatabase
    ) -> None:
        by_id = f"SELECT customer FROM {live_pg.orders} WHERE id = {{{{id}}}}"
        text = await _query(live_pg, by_id, {"id": 1.5})
        assert text.startswith("[Error]") and "not a whole number" in text
        assert "alice" not in text

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

    async def test_a_list_in_an_in_list_is_bound_item_by_item(
        self, live_mysql: LiveDatabase
    ) -> None:
        await self._check_lists(live_mysql)

    @pytest.mark.parametrize("value", [0, False, -0.0, 0.0, 1, True, 7, "0"])
    async def test_a_number_or_boolean_does_not_match_text_that_is_not_that_number(
        self, live_mysql: LiveDatabase, value: object
    ) -> None:
        """Bound as a number, 0 equals every text that does not start with a digit."""
        for column in ("customer", "note"):
            text = await _query(
                live_mysql,
                f"SELECT id FROM {live_mysql.orders} WHERE {column} = {{{{v}}}}",
                {"v": value},
            )
            assert json.loads(text)["rows"] == [], (column, value)
        text = await _query(
            live_mysql, f"SELECT token FROM {live_mysql.secrets} WHERE token = {{{{v}}}}", {"v": value}
        )
        assert SECRET not in text

    async def test_a_numeric_column_still_matches_by_value(self, live_mysql: LiveDatabase) -> None:
        by_id = f"SELECT customer FROM {live_mysql.orders} WHERE id = {{{{v}}}}"
        for value in (1, 1.0, "1", " 1", True):
            assert json.loads(await _query(live_mysql, by_id, {"v": value}))["rows"] == [
                ["alice"]
            ], value
        above = f"SELECT id FROM {live_mysql.orders} WHERE id > {{{{v}}}} ORDER BY id"
        assert json.loads(await _query(live_mysql, above, {"v": 1}))["rows"] == [[2]]
        # Numeric, not text, comparison: '10' > '9' only as numbers.
        assert json.loads(await _query(live_mysql, above, {"v": 10}))["rows"] == []
        total = f"SELECT id + {{{{v}}}} FROM {live_mysql.orders} WHERE id = 1"
        assert json.loads(await _query(live_mysql, total, {"v": 2}))["rows"] == [[3.0]]

    async def test_limit_takes_a_whole_number_and_nothing_else(
        self, live_mysql: LiveDatabase
    ) -> None:
        limited = f"SELECT id FROM {live_mysql.orders} ORDER BY id LIMIT {{{{n}}}} OFFSET {{{{m}}}}"
        assert json.loads(await _query(live_mysql, limited, {"n": 1, "m": "1"}))["rows"] == [[2]]
        text = await _query(live_mysql, limited, {"n": "1; DROP TABLE x", "m": 0})
        assert text.startswith("[SQL Safety Error]") and "LIMIT or OFFSET" in text

    async def test_text_a_columns_character_set_cannot_hold_fails_with_a_readable_message(
        self, live_mysql: LiveDatabase
    ) -> None:
        from fim_one.core.tool.connector.database.pool import ConnectionPoolManager

        table = f"{live_mysql.orders}_cs"
        driver = await ConnectionPoolManager.get_instance().get_driver(
            live_mysql.connector_id, live_mysql.config
        )
        await driver.execute_query(
            f"CREATE TABLE {table} (l varchar(32) CHARACTER SET latin1, "
            "u3 varchar(32) CHARACTER SET utf8mb3)",
            read_only=False,
        )
        try:
            await driver.execute_query(
                f"INSERT INTO {table} VALUES ('alice', 'alice')", read_only=False
            )
            for column, value in (("l", "张三"), ("u3", "😀")):
                sql = f"SELECT l FROM {table} WHERE {column} = {{{{v}}}}"
                text = await _query(live_mysql, sql, {"v": value})
                assert text.startswith("[Error]"), text
                assert "character set cannot hold" in text and "Illegal mix" in text
            # Text the column can hold is compared as usual.
            found = await _query(live_mysql, f"SELECT l FROM {table} WHERE l = {{{{v}}}}", {"v": "alice"})
            assert json.loads(found)["rows"] == [["alice"]]
        finally:
            await driver.execute_query(f"DROP TABLE {table}", read_only=False)

    async def test_a_literal_percent_sign_survives_binding(self, live_mysql: LiveDatabase) -> None:
        text = await _query(
            live_mysql,
            f"SELECT id FROM {live_mysql.orders} WHERE customer LIKE 'ali%' AND id = {{{{id}}}}",
            {"id": 1},
        )
        assert json.loads(text)["rows"] == [[1]]
