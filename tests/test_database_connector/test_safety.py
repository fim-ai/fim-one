"""Tests for SQL safety validation."""

from __future__ import annotations

import pytest

from fim_one.core.tool.connector.database.safety import SqlSafetyError, validate_sql


class TestValidateSqlSelectAllowed:
    """SELECT queries should be allowed by default."""

    def test_simple_select(self) -> None:
        result = validate_sql("SELECT * FROM users")
        assert result == "SELECT * FROM users"

    def test_select_with_where(self) -> None:
        result = validate_sql("SELECT id, name FROM users WHERE active = true")
        assert "WHERE active = true" in result

    def test_select_with_join(self) -> None:
        result = validate_sql(
            "SELECT u.name, o.total FROM users u JOIN orders o ON u.id = o.user_id"
        )
        assert "JOIN orders" in result

    def test_select_strips_trailing_semicolon(self) -> None:
        result = validate_sql("SELECT 1;")
        assert result == "SELECT 1"

    def test_select_strips_multiple_trailing_semicolons(self) -> None:
        result = validate_sql("SELECT 1;;;")
        assert result == "SELECT 1"

    def test_cte_select(self) -> None:
        sql = "WITH cte AS (SELECT id FROM users) SELECT * FROM cte"
        result = validate_sql(sql)
        assert "WITH cte" in result

    def test_explain(self) -> None:
        result = validate_sql("EXPLAIN SELECT * FROM users")
        assert result.startswith("EXPLAIN")

    def test_show(self) -> None:
        result = validate_sql("SHOW TABLES")
        assert result == "SHOW TABLES"

    def test_describe(self) -> None:
        result = validate_sql("DESCRIBE users")
        assert result == "DESCRIBE users"


class TestValidateSqlWriteBlocked:
    """Write operations should be blocked when allow_write=False."""

    def test_insert_blocked(self) -> None:
        with pytest.raises(SqlSafetyError, match="Only SELECT"):
            validate_sql("INSERT INTO users (name) VALUES ('test')")

    def test_update_blocked(self) -> None:
        with pytest.raises(SqlSafetyError, match="Only SELECT"):
            validate_sql("UPDATE users SET name = 'test'")

    def test_delete_blocked(self) -> None:
        with pytest.raises(SqlSafetyError, match="Only SELECT"):
            validate_sql("DELETE FROM users WHERE id = 1")

    def test_drop_blocked(self) -> None:
        with pytest.raises(SqlSafetyError, match="Only SELECT"):
            validate_sql("DROP TABLE users")

    def test_alter_blocked(self) -> None:
        with pytest.raises(SqlSafetyError, match="Only SELECT"):
            validate_sql("ALTER TABLE users ADD COLUMN age INT")

    def test_create_blocked(self) -> None:
        with pytest.raises(SqlSafetyError, match="Only SELECT"):
            validate_sql("CREATE TABLE evil (id INT)")

    def test_truncate_blocked(self) -> None:
        with pytest.raises(SqlSafetyError, match="Only SELECT"):
            validate_sql("TRUNCATE TABLE users")

    def test_cte_with_insert_blocked(self) -> None:
        with pytest.raises(SqlSafetyError, match="CTE"):
            validate_sql(
                "WITH cte AS (SELECT id FROM users) INSERT INTO log SELECT * FROM cte"
            )


class TestValidateSqlWriteAllowed:
    """Write operations should pass when allow_write=True."""

    def test_insert_allowed(self) -> None:
        result = validate_sql(
            "INSERT INTO users (name) VALUES ('test')", allow_write=True
        )
        assert "INSERT" in result

    def test_update_allowed(self) -> None:
        result = validate_sql(
            "UPDATE users SET name = 'test' WHERE id = 1", allow_write=True
        )
        assert "UPDATE" in result

    def test_delete_allowed(self) -> None:
        result = validate_sql("DELETE FROM users WHERE id = 1", allow_write=True)
        assert "DELETE" in result


class TestValidateSqlMultiStatement:
    """Multi-statement queries should always be blocked."""

    def test_two_selects(self) -> None:
        with pytest.raises(SqlSafetyError, match="Multi-statement"):
            validate_sql("SELECT 1; SELECT 2")

    def test_select_then_drop(self) -> None:
        with pytest.raises(SqlSafetyError, match="Multi-statement"):
            validate_sql("SELECT 1; DROP TABLE users")

    def test_semicolon_in_string_literal_ok(self) -> None:
        # Semicolons inside string literals should not trigger multi-statement detection
        result = validate_sql("SELECT 'hello; world' FROM dual")
        assert "hello; world" in result


class TestValidateSqlDangerousPatterns:
    """Dangerous SQL patterns should always be blocked."""

    def test_into_outfile(self) -> None:
        with pytest.raises(SqlSafetyError, match="INTO OUTFILE"):
            validate_sql("SELECT * FROM users INTO OUTFILE '/tmp/evil'")

    def test_load_file(self) -> None:
        with pytest.raises(SqlSafetyError, match="LOAD_FILE"):
            validate_sql("SELECT LOAD_FILE('/etc/passwd')")

    def test_benchmark(self) -> None:
        with pytest.raises(SqlSafetyError, match="BENCHMARK"):
            validate_sql("SELECT BENCHMARK(1000000, SHA1('test'))")

    def test_sleep(self) -> None:
        with pytest.raises(SqlSafetyError, match="SLEEP"):
            validate_sql("SELECT SLEEP(10)")

    def test_pg_sleep(self) -> None:
        with pytest.raises(SqlSafetyError, match="pg_sleep"):
            validate_sql("SELECT pg_sleep(10)")

    def test_xp_cmdshell(self) -> None:
        with pytest.raises(SqlSafetyError, match="xp_cmdshell"):
            validate_sql("EXEC xp_cmdshell 'dir'", allow_write=True)

    def test_exec_function(self) -> None:
        with pytest.raises(SqlSafetyError, match="EXEC"):
            validate_sql("SELECT EXEC('evil')")

    def test_execute_function(self) -> None:
        with pytest.raises(SqlSafetyError, match="EXECUTE"):
            validate_sql("SELECT EXECUTE('evil')")

    def test_into_dumpfile(self) -> None:
        with pytest.raises(SqlSafetyError, match="INTO DUMPFILE"):
            validate_sql("SELECT * FROM users INTO DUMPFILE '/tmp/evil'")

    def test_load_data(self) -> None:
        with pytest.raises(SqlSafetyError, match="LOAD DATA"):
            validate_sql(
                "LOAD DATA INFILE '/tmp/data' INTO TABLE users", allow_write=True
            )


class TestValidateSqlEdgeCases:
    """Edge cases and error handling."""

    def test_empty_string(self) -> None:
        with pytest.raises(SqlSafetyError, match="Empty"):
            validate_sql("")

    def test_whitespace_only(self) -> None:
        with pytest.raises(SqlSafetyError, match="Empty"):
            validate_sql("   ")

    def test_semicolons_only(self) -> None:
        with pytest.raises(SqlSafetyError, match="Empty"):
            validate_sql(";;;")

    def test_case_insensitive_blocking(self) -> None:
        with pytest.raises(SqlSafetyError):
            validate_sql("select sleep(10)")

    def test_case_insensitive_select_allowed(self) -> None:
        result = validate_sql("select * from users")
        assert result == "select * from users"


# ---------------------------------------------------------------------------
# Writes dressed as reads
# ---------------------------------------------------------------------------


class TestWritesInsideAReadAreBlocked:
    """Shapes that start like a read and change data on a read-only connection."""

    @pytest.mark.parametrize(
        "sql",
        [
            "WITH d AS (DELETE FROM users WHERE id = 1 RETURNING id) SELECT * FROM d",
            "WITH d AS (UPDATE users SET name = 'x' RETURNING id) SELECT * FROM d",
            "WITH d AS (INSERT INTO users (name) VALUES ('x') RETURNING id) SELECT * FROM d",
            "with d as (delete from users returning id) select * from d",
            "WITH d AS (/* hidden */ DELETE /* x */ FROM users RETURNING id) SELECT * FROM d",
            "WITH o AS (SELECT 1) /* gap */ DELETE FROM users",
            "WITH o AS (SELECT 1)\n-- gap\nDELETE FROM users",
            "SELECT id FROM users FOR UPDATE",
        ],
    )
    def test_a_statement_that_changes_or_locks_rows(self, sql: str) -> None:
        with pytest.raises(SqlSafetyError):
            validate_sql(sql)

    @pytest.mark.parametrize(
        "sql",
        [
            "SELECT * INTO copied FROM users",
            "SELECT id, name INTO TEMP copied FROM users",
            "SELECT id INTO @last FROM users LIMIT 1",
            "WITH o AS (SELECT * FROM users) SELECT * INTO copied FROM o",
        ],
    )
    def test_select_into(self, sql: str) -> None:
        with pytest.raises(SqlSafetyError, match="INTO"):
            validate_sql(sql)

    @pytest.mark.parametrize(
        "sql",
        [
            "EXPLAIN ANALYZE DELETE FROM users",
            "EXPLAIN ANALYSE DELETE FROM users",
            "explain analyze update users set name = 'x'",
            "EXPLAIN (ANALYZE) DELETE FROM users",
            "EXPLAIN (ANALYZE, BUFFERS) INSERT INTO users (name) VALUES ('x')",
            "EXPLAIN (VERBOSE, ANALYZE TRUE) DELETE FROM users",
            "EXPLAIN ANALYZE VERBOSE DELETE FROM users",
            "EXPLAIN /* c */ ANALYZE /* c */ DELETE FROM users",
            "EXPLAIN ANALYZE CREATE TABLE copied AS SELECT * FROM users",
            "EXPLAIN ANALYZE REFRESH MATERIALIZED VIEW totals",
            "EXPLAIN ANALYZE EXECUTE prepared_delete",
            "EXPLAIN ANALYZE WITH d AS (DELETE FROM users RETURNING id) SELECT * FROM d",
            "EXPLAIN ANALYZE SELECT * INTO copied FROM users",
            "EXPLAIN ANALYZE FORMAT=TREE DELETE a FROM users a JOIN users b ON a.id = b.id",
        ],
    )
    def test_explain_analyze_of_anything_but_a_read(self, sql: str) -> None:
        with pytest.raises(SqlSafetyError):
            validate_sql(sql)

    @pytest.mark.parametrize(
        "sql",
        [
            "WITH o AS (SELECT id FROM users) SELECT * FROM o",
            "WITH a AS (SELECT 1), b AS (SELECT 2) SELECT * FROM a, b",
            "SELECT 'DELETE FROM users' AS example, 'copy INTO x' AS other",
            "SELECT * FROM users -- UPDATE later\nWHERE id = 1",
            "SELECT /* INSERT INTO nothing */ id FROM users",
            'SELECT "update", `delete` FROM audit',
            "SELECT updated_at, deleted, inserted_by, into_stock FROM users",
            "SELECT REPLACE(name, 'a', 'b'), INSERT(name, 1, 2, 'x') FROM users",
            "EXPLAIN SELECT * FROM users",
            "EXPLAIN ANALYZE SELECT * FROM users",
            "EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) SELECT * FROM users",
            "EXPLAIN ANALYZE VERBOSE SELECT * FROM users",
            "EXPLAIN FORMAT=JSON SELECT * FROM users",
            "EXPLAIN ANALYZE WITH o AS (SELECT 1) SELECT * FROM o",
            "EXPLAIN ANALYZE (SELECT 1)",
            # Planned, never run.
            "EXPLAIN DELETE FROM users",
        ],
    )
    def test_reads_are_still_allowed(self, sql: str) -> None:
        assert validate_sql(sql) == sql

    @pytest.mark.parametrize(
        "sql",
        [
            "WITH d AS (DELETE FROM users WHERE id = 1 RETURNING id) SELECT * FROM d",
            "EXPLAIN ANALYZE DELETE FROM users",
            "SELECT * INTO copied FROM users",
        ],
    )
    def test_a_connection_that_allows_writes_is_not_limited(self, sql: str) -> None:
        assert validate_sql(sql, allow_write=True) == sql


class TestTheCheckReadsSqlTheWayTheDatabaseDoes:
    """Where a string or a comment ends depends on the database."""

    # DM8 and MySQL read ``$$`` as a name; PostgreSQL as the start of a string.
    DOLLARS = "WITH $$ AS (SELECT 1) DELETE FROM users WHERE 1 = 1 /* $$ */"
    # MySQL reads ``#`` as a comment; PostgreSQL as an operator.
    HASH = "WITH d AS (SELECT 1 # ) DELETE FROM users\n) SELECT * FROM d"
    # MySQL reads the backslash as escaping the quote; standard SQL does not.
    BACKSLASH = (
        "WITH d AS (SELECT 'a\\' AS x), e AS (DELETE FROM users RETURNING id) "
        "SELECT * FROM e WHERE 'b' = ' '"
    )

    @pytest.mark.parametrize("dialect", ["generic", "mysql", None])
    def test_dollar_signs_hide_nothing_where_they_are_not_quotes(
        self, dialect: str | None
    ) -> None:
        with pytest.raises(SqlSafetyError):
            validate_sql(self.DOLLARS, dialect=dialect)

    def test_a_dollar_quoted_string_is_a_string_in_postgresql(self) -> None:
        sql = "SELECT $$ DELETE FROM users $$ AS body"
        assert validate_sql(sql, dialect="postgresql") == sql
        # Not known to be PostgreSQL: read as SQL too, and refused.
        with pytest.raises(SqlSafetyError):
            validate_sql(sql)

    @pytest.mark.parametrize("dialect", ["postgresql", "generic", None])
    def test_a_hash_hides_nothing_where_it_is_not_a_comment(self, dialect: str | None) -> None:
        with pytest.raises(SqlSafetyError):
            validate_sql(self.HASH, dialect=dialect)

    @pytest.mark.parametrize("dialect", ["postgresql", "mysql", "generic", None])
    def test_a_backslash_before_a_quote_is_read_both_ways(self, dialect: str | None) -> None:
        with pytest.raises(SqlSafetyError):
            validate_sql(self.BACKSLASH, dialect=dialect)

    def test_the_inside_of_a_mysql_executable_comment_is_sql(self) -> None:
        with pytest.raises(SqlSafetyError):
            validate_sql("WITH o AS (SELECT 1) /*!50000 DELETE FROM users */", dialect="mysql")
        with pytest.raises(SqlSafetyError):
            validate_sql("SELECT id /*! INTO @x */ FROM users", dialect="mysql")

    def test_backslashes_in_ordinary_reads_are_fine(self) -> None:
        for sql in (
            r"SELECT * FROM files WHERE path LIKE 'C:\\temp\\%'",
            r"SELECT 'it\'s' AS quoted, name FROM users",
            r"SELECT regexp_replace(name, '\\d+', '') FROM users",
        ):
            assert validate_sql(sql) == sql
