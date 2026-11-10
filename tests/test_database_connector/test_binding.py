"""``{{variable}}`` references in a database query step become bound parameters.

The values never reach the SQL text: a reference is rewritten to the
driver's placeholder, and one that is not where SQL takes a value is
refused.
"""

from __future__ import annotations

import pytest

from fim_one.core.tool.connector.database.base import DatabaseDriver
from fim_one.core.tool.connector.database.binding import (
    SqlBindingError,
    SqlDialect,
    SqlTemplate,
    bound_value,
    mysql_literal,
    parse_sql_template,
    render_sql,
)
from fim_one.core.tool.connector.database.drivers import DRIVER_REGISTRY


def _bound(sql: str, driver: str) -> tuple[str, tuple[str, ...]]:
    """The SQL *driver* is given, each variable bound to its own name."""
    cls = DRIVER_REGISTRY[driver]
    assert cls.placeholder_style is not None
    template = parse_sql_template(sql, dialect=cls.sql_dialect)
    bound, _ = render_sql(template, cls.placeholder_style, list(template.variables))
    return bound, template.variables


def _hex(text: str) -> str:
    return f"_utf8mb4 X'{text.encode().hex().upper()}'"


class TestEveryDriverHasItsPlaceholder:
    SQL = "SELECT a FROM t WHERE id = {{start_1.id}} AND name = {{ name }} LIMIT {{n}}"

    def test_every_registered_driver_can_bind(self) -> None:
        assert set(DRIVER_REGISTRY) == {"postgresql", "kingbasees", "highgo", "mysql", "dm8"}
        for cls in DRIVER_REGISTRY.values():
            assert cls.placeholder_style in ("numeric", "qmark", "literal")

    @pytest.mark.parametrize("driver", ["postgresql", "kingbasees", "highgo"])
    def test_postgresql_family_numbers_them(self, driver: str) -> None:
        sql, names = _bound(self.SQL, driver)
        assert sql == "SELECT a FROM t WHERE id = $1 AND name = $2 LIMIT $3"
        assert names == ("start_1.id", "name", "n")

    def test_mysql_writes_each_value_as_a_hex_literal_and_sends_no_parameters(self) -> None:
        template = parse_sql_template(
            "SELECT a FROM t WHERE name LIKE 'a%' AND b % 2 = {{x}} AND c = {{y}}", dialect="mysql"
        )
        sql, parameters = render_sql(template, "literal", ["x' OR 1=1 -- ", "縗' OR 1=1 -- "])
        assert parameters is None
        # Nothing of a value reaches the statement but hex digits; the
        # statement's own percent signs are left alone.
        assert sql == (
            "SELECT a FROM t WHERE name LIKE 'a%' AND b % 2 = "
            + _hex("x' OR 1=1 -- ")
            + " AND c = "
            + _hex("縗' OR 1=1 -- ")
        )
        assert "OR 1=1" not in sql

    def test_dm8_uses_question_marks(self) -> None:
        sql, names = _bound(self.SQL, "dm8")
        assert sql == "SELECT a FROM t WHERE id = ? AND name = ? LIMIT ?"
        assert names == ("start_1.id", "name", "n")

    def test_a_repeated_variable_is_bound_each_time(self) -> None:
        sql, names = _bound("SELECT a FROM t WHERE x = {{v}} OR y = {{v}}", "postgresql")
        assert sql == "SELECT a FROM t WHERE x = $1 OR y = $2"
        assert names == ("v", "v")

    def test_a_driver_with_no_placeholder_style_says_it_cannot_bind(self) -> None:
        assert DatabaseDriver.placeholder_style is None


class TestWhereAValueMayStand:
    @pytest.mark.parametrize(
        "sql",
        [
            "SELECT * FROM t WHERE id = {{v}}",
            "SELECT * FROM t WHERE id={{v}}",
            "SELECT * FROM t WHERE id IN ({{a}}, {{b}})",
            "SELECT * FROM t WHERE name LIKE {{v}}",
            "SELECT * FROM t WHERE name LIKE '%' || {{v}} || '%'",
            "SELECT * FROM t WHERE d BETWEEN {{a}} AND {{b}}",
            "SELECT * FROM t WHERE id = {{v}}::int",
            "SELECT * FROM t LIMIT {{n}} OFFSET {{m}}",
            "SELECT {{v}} AS x",
            "SELECT CASE WHEN a > {{v}} THEN {{x}} ELSE {{y}} END FROM t",
            "SELECT * FROM t WHERE tags @> {{v}}",
            "INSERT INTO t (a, b) VALUES ({{a}}, {{b}})",
            "UPDATE t SET a = {{a}} WHERE id = {{id}}",
            "SELECT * FROM t WHERE note = 'it''s {not} a variable' AND id = {{v}}",
            "SELECT * FROM t -- trailing remark\nWHERE id = {{v}}",
            "SELECT * FROM t /* remark */ WHERE id = /* here */ {{v}}",
        ],
    )
    def test_a_value_position_is_accepted(self, sql: str) -> None:
        template = parse_sql_template(sql, dialect="postgresql")
        assert len(template.parts) == len(template.variables) + 1

    @pytest.mark.parametrize(
        "sql",
        [
            "SELECT * FROM orders WHERE id = '{{v}}'",
            "SELECT * FROM orders WHERE name LIKE '%{{v}}%'",
            'SELECT * FROM orders WHERE "{{v}}" = 1',
            "SELECT * FROM orders WHERE `{{v}}` = 1",
            "SELECT * FROM orders WHERE note = $$ {{v}} $$",
            "SELECT * FROM orders WHERE note = $tag$ {{v}} $tag$",
            "SELECT * FROM orders WHERE note = E'a {{v}}'",
            "SELECT * FROM orders WHERE note = 'never closed {{v}}",
        ],
    )
    def test_inside_quotes_is_refused_and_says_to_drop_the_quotes(self, sql: str) -> None:
        with pytest.raises(SqlBindingError) as refused:
            parse_sql_template(sql, dialect="postgresql")
        assert "inside quotes" in str(refused.value)
        assert "WHERE id = {{variable}}" in str(refused.value)

    @pytest.mark.parametrize(
        ("sql", "dialect"),
        [
            ("SELECT * FROM t -- {{v}}\nWHERE id = 1", "postgresql"),
            ("SELECT * FROM t /* {{v}} */ WHERE id = 1", "postgresql"),
            ("SELECT * FROM t # {{v}}\nWHERE id = 1", "mysql"),
        ],
    )
    def test_inside_a_comment_is_refused(self, sql: str, dialect: SqlDialect) -> None:
        with pytest.raises(SqlBindingError, match="inside a comment"):
            parse_sql_template(sql, dialect=dialect)

    @pytest.mark.parametrize(
        "sql",
        [
            "{{whole_statement}}",
            "SELECT * FROM {{table}}",
            "SELECT * FROM t JOIN {{table}} ON 1 = 1",
            "SELECT * FROM t ORDER BY {{column}}",
            "SELECT * FROM t ORDER BY id {{direction}}",
            "SELECT * FROM t WHERE col_{{suffix}} = 1",
            "SELECT * FROM t WHERE id = {{a}}{{b}}",
            "SELECT * FROM t WHERE id = {{a}} {{b}}",
            "SELECT {{fn}}(a) FROM t",
            "SELECT * FROM t WHERE {{alias}}.id = 1",
            "SELECT a AS {{alias}} FROM t",
            "SELECT a::{{type}} FROM t",
            "SELECT * FROM t {{clause}}",
            "DELETE FROM t WHERE id = 1 {{more}}",
        ],
    )
    def test_a_name_keyword_or_statement_position_is_refused(self, sql: str) -> None:
        with pytest.raises(SqlBindingError) as refused:
            parse_sql_template(sql, dialect="postgresql")
        assert "does not take a value" in str(refused.value)
        assert "WHERE id = {{variable}}" in str(refused.value)

    def test_braces_that_are_not_a_reference_are_refused(self) -> None:
        with pytest.raises(SqlBindingError, match="not a variable reference"):
            parse_sql_template(
                "SELECT * FROM t WHERE a = {{ 1 + 1 }} AND b = {{v}}", dialect="mysql"
            )


class TestWhatDatabasesReadDifferentlyIsRefused:
    def test_a_backslash_before_a_quote(self) -> None:
        # MySQL: the string runs on to the last quote and holds the
        # reference. Standard SQL: it ends after the backslash.
        sql = r"SELECT * FROM t WHERE a = 'x\' AND b = {{v}} AND c = '"
        dialects: tuple[SqlDialect, ...] = ("postgresql", "mysql", "generic")
        for dialect in dialects:
            with pytest.raises(SqlBindingError, match="backslash"):
                parse_sql_template(sql, dialect=dialect)

    def test_an_escaped_backslash_before_a_quote_is_read_the_same_everywhere(self) -> None:
        template = parse_sql_template(
            r"SELECT * FROM t WHERE a = 'x\\' AND b = {{v}}", dialect="mysql"
        )
        assert template.variables == ("v",)

    def test_a_block_comment_inside_a_block_comment(self) -> None:
        # PostgreSQL: still inside the outer comment. MySQL: code again.
        with pytest.raises(SqlBindingError, match="block comment"):
            parse_sql_template("SELECT 1 /* a /* b */ + {{v}} */", dialect="mysql")

    def test_two_dashes_with_no_space_in_mysql(self) -> None:
        with pytest.raises(SqlBindingError, match="'--'"):
            parse_sql_template("SELECT 5--'a\n' + {{v}} + '", dialect="mysql")

    def test_a_mysql_executable_comment(self) -> None:
        with pytest.raises(SqlBindingError, match="runs as SQL"):
            parse_sql_template("SELECT /*! 'a */ + {{v}} + '", dialect="mysql")

    def test_a_hash_is_a_comment_in_mysql_and_an_operator_in_postgresql(self) -> None:
        sql = "SELECT * FROM t WHERE doc #> {{path}} IS NOT NULL"
        assert parse_sql_template(sql, dialect="postgresql").variables == ("path",)
        with pytest.raises(SqlBindingError, match="inside a comment"):
            parse_sql_template(sql, dialect="mysql")


class TestOddCharactersInATemplate:
    @pytest.mark.parametrize(
        "sql",
        [
            "SELECT 1 --\u3000'\n + {{v}} + '",
            "SELECT 1 --\x7f'\n WHERE a = {{v}} --\x7f'",
            "SELECT\u00a0a FROM t WHERE b = {{v}}",
            "SELECT a FROM t WHERE b = {{v}} /* \x00 */",
        ],
    )
    def test_a_control_or_invisible_character_outside_quotes_is_refused(self, sql: str) -> None:
        dialects: tuple[SqlDialect, ...] = ("postgresql", "mysql", "generic")
        for dialect in dialects:
            with pytest.raises(SqlBindingError):
                parse_sql_template(sql, dialect=dialect)

    def test_inside_quotes_it_is_text(self) -> None:
        template = parse_sql_template(
            "SELECT a FROM t WHERE b = 'x\u3000y' AND c = {{v}}", dialect="mysql"
        )
        assert template.variables == ("v",)


class TestPlaceholdersAlreadyInTheSql:
    def test_a_numbered_parameter_is_refused_for_postgresql(self) -> None:
        template = parse_sql_template(
            "SELECT * FROM t WHERE a = $1 AND b = {{v}}", dialect="postgresql"
        )
        with pytest.raises(SqlBindingError, match="numbered parameter"):
            render_sql(template, "numeric", list(template.variables))

    def test_a_dollar_sign_in_a_string_is_text(self) -> None:
        template = parse_sql_template(
            "SELECT * FROM t WHERE a = 'costs $1' AND b = {{v}}", dialect="postgresql"
        )
        assert render_sql(template, "numeric", ["x"]) == (
            "SELECT * FROM t WHERE a = 'costs $1' AND b = $1",
            ["x"],
        )

    def test_a_question_mark_is_refused_for_dm8(self) -> None:
        template = parse_sql_template(
            "SELECT * FROM t WHERE a = ? AND b = {{v}}", dialect="generic"
        )
        with pytest.raises(SqlBindingError, match=r"'\?'"):
            render_sql(template, "qmark", list(template.variables))

    def test_comments_are_left_out_of_what_is_sent(self) -> None:
        template = parse_sql_template(
            "SELECT * FROM t -- who?\nWHERE a = {{v}} /* $1 */", dialect="generic"
        )
        assert render_sql(template, "qmark", ["x"]) == ("SELECT * FROM t  \nWHERE a = ?  ", ["x"])


class TestAListInAnInList:
    """``IN ({{ids}})`` with a list: one bound value per item."""

    SQL = "SELECT a FROM t WHERE id IN ({{ids}}) AND b = {{b}}"

    def test_each_driver_gets_a_placeholder_per_item(self) -> None:
        template = parse_sql_template(self.SQL, dialect="postgresql")
        assert template.positions == ("list", "value")
        assert render_sql(template, "numeric", [[1, 2, 3], "x"]) == (
            "SELECT a FROM t WHERE id IN ($1, $2, $3) AND b = $4",
            [1, 2, 3, "x"],
        )
        assert render_sql(template, "qmark", [["a", "b"], "x"]) == (
            "SELECT a FROM t WHERE id IN (?, ?) AND b = ?",
            ["a", "b", "x"],
        )
        assert render_sql(template, "literal", [[1, "a'b"], "x"])[0] == (
            f"SELECT a FROM t WHERE id IN ({_hex('1')}, {_hex(chr(97) + chr(39) + chr(98))}) "
            f"AND b = {_hex('x')}"
        )

    def test_an_empty_list_is_an_empty_set_not_null(self) -> None:
        """``IN (NULL)`` is unknown, so ``NOT IN (NULL)`` matched nothing either."""
        template = parse_sql_template(self.SQL, dialect="postgresql")
        assert render_sql(template, "numeric", [[], "x"]) == (
            "SELECT a FROM t WHERE id = ANY($1) AND b = $2",
            [[], "x"],
        )
        negated = parse_sql_template(
            "SELECT a FROM t WHERE id not  in ( {{ids}} ) AND b = {{b}}", dialect="postgresql"
        )
        assert render_sql(negated, "numeric", [[], "x"]) == (
            "SELECT a FROM t WHERE id <> ALL($1 ) AND b = $2",
            [[], "x"],
        )
        for style in ("qmark", "literal"):
            sql, _ = render_sql(negated, style, [[], "x"])  # type: ignore[arg-type]
            assert "id not  in ( SELECT NULL FROM DUAL WHERE 1 = 0 )" in sql

    def test_more_values_than_postgresql_takes_says_so(self) -> None:
        template = parse_sql_template(self.SQL, dialect="postgresql")
        with pytest.raises(SqlBindingError) as refused:
            render_sql(template, "numeric", [list(range(40000)), "x"])
        assert "40001 values" in str(refused.value)
        assert "32767" in str(refused.value) and "= ANY({{list}})" in str(refused.value)
        # Just under the limit is fine.
        assert len(render_sql(template, "numeric", [list(range(32766)), "x"])[1] or []) == 32767

    def test_one_value_in_an_in_list_is_one_value(self) -> None:
        template = parse_sql_template(self.SQL, dialect="postgresql")
        assert render_sql(template, "numeric", [7, "x"])[1] == [7, "x"]

    def test_items_that_are_lists_or_objects_are_json_text(self) -> None:
        template = parse_sql_template(self.SQL, dialect="postgresql")
        assert render_sql(template, "numeric", [[[1], {"k": 2}], "x"])[1] == [
            "[1]",
            '{"k": 2}',
            "x",
        ]

    @pytest.mark.parametrize(
        "sql",
        [
            "SELECT a FROM t WHERE id IN ({{a}}, {{b}})",
            "SELECT a FROM t WHERE id = ANY({{a}})",
            "SELECT a FROM t WHERE tags @> {{a}}",
            "SELECT a FROM t WHERE x.in ({{a}})",
            "SELECT f({{a}})",
        ],
    )
    def test_anywhere_else_a_list_is_one_json_text(self, sql: str) -> None:
        template = parse_sql_template(sql, dialect="postgresql")
        assert "list" not in template.positions
        values = [[1, 2]] * len(template.variables)
        assert render_sql(template, "numeric", values)[1] == ["[1, 2]"] * len(values)


class TestMySQLLiterals:
    def test_text_is_hex_whatever_it_holds(self) -> None:
        assert mysql_literal("alice") == "_utf8mb4 X'616C696365'"
        assert mysql_literal("") == "_utf8mb4 X''"
        for text in ("'", "\\", "\\' OR 1=1 -- ", "縗' OR 1=1 -- ", "a\x00b", "%s", "{{x}}", "\n"):
            literal = mysql_literal(text)
            assert set(literal[len("_utf8mb4 X'") : -1]) <= set("0123456789ABCDEF")
            assert bytes.fromhex(literal[len("_utf8mb4 X'") : -1]).decode() == text

    def test_null_is_null(self) -> None:
        assert mysql_literal(None) == "NULL"

    def test_numbers_and_booleans_are_written_as_text(self) -> None:
        """A number against a text column would match every non-numeric row."""
        assert mysql_literal(0) == _hex("0")
        assert mysql_literal(-7) == _hex("-7")
        assert mysql_literal(1.5) == _hex("1.5")
        assert mysql_literal(-0.0) == _hex("-0.0")
        assert mysql_literal(True) == _hex("1")
        assert mysql_literal(False) == _hex("0")
        assert mysql_literal(2**70) == _hex(str(2**70))

    def _limit(self, sql: str, values: list[object]) -> str:
        template = parse_sql_template(sql, dialect="mysql")
        return render_sql(template, "literal", values)[0]

    def test_limit_and_offset_take_a_checked_whole_number(self) -> None:
        sql = "SELECT a FROM t WHERE b = {{b}} LIMIT {{n}} OFFSET {{m}}"
        assert self._limit(sql, [5, 10, "20"]) == (
            f"SELECT a FROM t WHERE b = {_hex('5')} LIMIT 10 OFFSET 20"
        )
        assert self._limit("SELECT a FROM t LIMIT {{m}}, {{n}}", [0, 5.0]) == (
            "SELECT a FROM t LIMIT 0, 5"
        )
        assert self._limit("SELECT a FROM t limit {{n}}", [" 3 "]) == "SELECT a FROM t limit 3"

    @pytest.mark.parametrize(
        "value", ["1; DROP TABLE t", "1 OR 1=1", "-1", -1, 1.5, True, None, "", "٣", "1e3", [1]]
    )
    def test_limit_refuses_what_is_not_a_whole_number(self, value: object) -> None:
        with pytest.raises(SqlBindingError, match="after LIMIT or OFFSET"):
            self._limit("SELECT a FROM t LIMIT {{n}}", [value])

    def test_only_limit_and_offset_are_count_positions(self) -> None:
        template = parse_sql_template(
            "SELECT a FROM t WHERE x.limit = {{a}} AND c IN ({{b}}, {{c}}) LIMIT {{n}}",
            dialect="mysql",
        )
        assert template.positions == ("value", "value", "value", "count")

    def test_text_that_is_not_unicode_is_refused(self) -> None:
        with pytest.raises(SqlBindingError, match="not valid Unicode"):
            mysql_literal("\udcbf' OR 1=1", name="v")


class TestValuesKeepTheirType:
    @pytest.mark.parametrize("value", [None, True, False, 0, 42, 1.5, "", "x' OR 1=1 --"])
    def test_scalars_are_bound_as_they_are(self, value: object) -> None:
        assert bound_value(value) is value

    def test_lists_and_objects_are_bound_as_json_text(self) -> None:
        assert bound_value([1, "a"]) == '[1, "a"]'
        assert bound_value({"k": "值"}) == '{"k": "值"}'

    def test_a_template_with_no_variables_is_one_part(self) -> None:
        assert parse_sql_template("SELECT 1", dialect="generic") == SqlTemplate(("SELECT 1",), ())
