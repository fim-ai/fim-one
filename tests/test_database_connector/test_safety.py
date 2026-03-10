"""Tests for SQL safety validation."""

from __future__ import annotations

import pytest

from fim_agent.core.tool.connector.database.safety import SqlSafetyError, validate_sql


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
        result = validate_sql(
            "DELETE FROM users WHERE id = 1", allow_write=True
        )
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
            validate_sql("LOAD DATA INFILE '/tmp/data' INTO TABLE users", allow_write=True)


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
