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
    parse_sql_template,
    render_sql,
)
from fim_one.core.tool.connector.database.drivers import DRIVER_REGISTRY


def _bound(sql: str, driver: str) -> tuple[str, tuple[str, ...]]:
    cls = DRIVER_REGISTRY[driver]
    assert cls.placeholder_style is not None
    template = parse_sql_template(sql, dialect=cls.sql_dialect)
    return render_sql(template, cls.placeholder_style), template.variables


class TestEveryDriverHasItsPlaceholder:
    SQL = "SELECT a FROM t WHERE id = {{start_1.id}} AND name = {{ name }} LIMIT {{n}}"

    def test_every_registered_driver_can_bind(self) -> None:
        assert set(DRIVER_REGISTRY) == {"postgresql", "kingbasees", "highgo", "mysql", "dm8"}
        for cls in DRIVER_REGISTRY.values():
            assert cls.placeholder_style in ("numeric", "format", "qmark")

    @pytest.mark.parametrize("driver", ["postgresql", "kingbasees", "highgo"])
    def test_postgresql_family_numbers_them(self, driver: str) -> None:
        sql, names = _bound(self.SQL, driver)
        assert sql == "SELECT a FROM t WHERE id = $1 AND name = $2 LIMIT $3"
        assert names == ("start_1.id", "name", "n")

    def test_mysql_uses_percent_s_and_doubles_literal_percent_signs(self) -> None:
        sql, names = _bound(
            "SELECT a FROM t WHERE name LIKE 'a%' AND b % 2 = {{x}} AND c = {{y}}", "mysql"
        )
        assert sql == "SELECT a FROM t WHERE name LIKE 'a%%' AND b %% 2 = %s AND c = %s"
        assert names == ("x", "y")

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


class TestPlaceholdersAlreadyInTheSql:
    def test_a_numbered_parameter_is_refused_for_postgresql(self) -> None:
        template = parse_sql_template(
            "SELECT * FROM t WHERE a = $1 AND b = {{v}}", dialect="postgresql"
        )
        with pytest.raises(SqlBindingError, match="numbered parameter"):
            render_sql(template, "numeric")

    def test_a_dollar_sign_in_a_string_is_text(self) -> None:
        template = parse_sql_template(
            "SELECT * FROM t WHERE a = 'costs $1' AND b = {{v}}", dialect="postgresql"
        )
        assert render_sql(template, "numeric") == "SELECT * FROM t WHERE a = 'costs $1' AND b = $1"

    def test_a_question_mark_is_refused_for_dm8(self) -> None:
        template = parse_sql_template(
            "SELECT * FROM t WHERE a = ? AND b = {{v}}", dialect="generic"
        )
        with pytest.raises(SqlBindingError, match=r"'\?'"):
            render_sql(template, "qmark")

    def test_comments_are_left_out_of_what_is_sent(self) -> None:
        template = parse_sql_template(
            "SELECT * FROM t -- who?\nWHERE a = {{v}} /* $1 */", dialect="generic"
        )
        assert render_sql(template, "qmark") == "SELECT * FROM t  \nWHERE a = ?  "


class TestValuesKeepTheirType:
    @pytest.mark.parametrize("value", [None, True, False, 0, 42, 1.5, "", "x' OR 1=1 --"])
    def test_scalars_are_bound_as_they_are(self, value: object) -> None:
        assert bound_value(value) is value

    def test_lists_and_objects_are_bound_as_json_text(self) -> None:
        assert bound_value([1, "a"]) == '[1, "a"]'
        assert bound_value({"k": "值"}) == '{"k": "值"}'

    def test_a_template_with_no_variables_is_one_part(self) -> None:
        assert parse_sql_template("SELECT 1", dialect="generic") == SqlTemplate(("SELECT 1",), ())
