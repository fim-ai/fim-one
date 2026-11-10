"""What a read-only connector lets through and what it refuses, per dialect.

Two tables, so that a change to the check cannot tip either way unnoticed:
reads that people and models really write (they must pass), and writes
dressed as reads (they must not). Each row is judged the way the
connector's database reads it: ``postgresql`` (PostgreSQL, KingbaseES,
HighGo), ``mysql`` (MySQL, MariaDB) and ``generic`` (DM8).

DM8 has no read-only transaction behind the check, so there the check also
refuses INSERT, UPDATE, DELETE and MERGE where they could be a name, and
reads a backslash before a quote both ways. The ``ONLY_...`` and
``NOT_ON_...`` sets below say exactly which rows differ.
"""

from __future__ import annotations

import pytest

from fim_one.core.tool.connector.database.reading import SqlDialect, is_odd, read_sql
from fim_one.core.tool.connector.database.safety import SqlSafetyError, validate_sql

DIALECTS: tuple[SqlDialect, ...] = ("postgresql", "mysql", "generic")

# Reads. Each passes on every dialect unless listed in NOT_ON below.
READS: tuple[str, ...] = (
    "SELECT id, update_time, deleted, insert_date FROM t",
    "SELECT * FROM t WHERE note = 'please delete me'",
    "SELECT * FROM merge_log",
    "WITH a AS (SELECT 1) SELECT * FROM a",
    "EXPLAIN SELECT * FROM t",
    "EXPLAIN ANALYZE SELECT * FROM t",
    "EXPLAIN (ANALYZE, BUFFERS) SELECT * FROM t",
    "EXPLAIN (FORMAT JSON) SELECT 1",
    "EXPLAIN FORMAT=JSON SELECT 1",
    "EXPLAIN ANALYZE FORMAT=TREE SELECT 1",
    "EXPLAIN (ANALYZE, FORMAT JSON, TIMING OFF) SELECT 1",
    "EXPLAIN ANALYZE VERBOSE WITH a AS (SELECT 1) SELECT * FROM a",
    "EXPLAIN ANALYZE\nSELECT 1",
    "explain (analyze true, costs off) select 1",
    "EXPLAIN (ANALYZE, SETTINGS, WAL) SELECT 1",
    "EXPLAIN ANALYZE (SELECT 1)",
    # Planned, never run.
    "EXPLAIN DELETE FROM users",
    "EXPLAIN (ANALYZE false) DELETE FROM users",
    "SHOW TABLES",
    "SHOW CREATE TABLE t",
    "SHOW search_path",
    "SHOW COLUMNS FROM t; ",
    "DESCRIBE t",
    "DESC t",
    "DESCRIBE SELECT 1",
    "-- c\nSELECT 1",
    "/* c */ SELECT 1",
    "# c\nSELECT 1",
    "SELECT 1 # trailing",
    "SELECT a # b\nFROM t",
    "SELECT 1 -- delete later",
    "SELECT 1 /* update */",
    "SELECT 1 -- a; b",
    "SELECT COUNT(*) FROM t -- count; all",
    "SELECT * FROM t WHERE id = 1 /* */ ; ",
    'SELECT "update", "delete" FROM t',
    "SELECT `update`, `insert` FROM t",
    "SELECT 'a' AS `update`",
    "SELECT t.update, t.delete FROM t",
    "SELECT update FROM t",
    "SELECT id AS update, id AS merge FROM t",
    "SELECT o.update FROM (SELECT 1 AS update) o",
    "SELECT (update) FROM t",
    "SELECT * FROM t WHERE (delete IS NULL)",
    "SELECT count(*) AS merge FROM t",
    "SELECT last_update FROM t",
    "SELECT updated_at, is_deleted FROM t",
    "SELECT REPLACE(name,'a','b') FROM t",
    "SELECT replace (name,'a','b') FROM t",
    "SELECT INSERT('abc',1,1,'x')",
    "SELECT * FROM t WHERE action IN ('insert','update','delete')",
    "SELECT * FROM t WHERE op = 'INSERT INTO x'",
    "SELECT o.id FROM t o WHERE 'x' = 'into'",
    "SELECT * FROM audit WHERE sql_text LIKE '%sleep(%'",
    "SELECT * FROM t WHERE fn = 'sleep (1)'",
    "SELECT * FROM job_log WHERE msg LIKE '%benchmark(%'",
    "SELECT * FROM t WHERE note = 'load data now'",
    "SELECT 'x' WHERE 'a' ~ 'load data'",
    "SELECT * FROM t WHERE note = 'call pg_read_file(1) or dblink(2)'",
    "SELECT pg_read_file, dblink, set_config FROM t",
    "SELECT * FROM t WHERE note = 'it''s; fine'",
    "SELECT * FROM t WHERE path = 'C:\\tmp\\'",
    "SELECT * FROM t WHERE path = 'C:\\tmp\\' AND action = 'delete'",
    "SELECT 'C:\\tmp\\' AS p, 'delete' AS op",
    "SELECT 'it\\'s' AS a, 'delete' AS b",
    "SELECT id FROM t WHERE note = 'it\\'s' OR customer = 'update'",
    "SELECT * FROM t WHERE s = 'a\\\\' AND op='delete'",
    "SELECT * FROM t WHERE a ~ '\\d+' ",
    "SELECT E'it\\'s' AS x",
    "SELECT $$it's delete$$ AS x",
    "SELECT $$a;b$$",
    "SELECT * FROM update_log",
    "SELECT * FROM deleted",
    "SELECT * FROM updates",
    "SELECT * FROM `delete`",
    "SELECT * FROM t ORDER BY id LIMIT 5;",
    "(SELECT 1) UNION (SELECT 2)",
    "TABLE t",
    "VALUES (1),(2)",
    "SELECT * FROM t WHERE data->>'k' = 'v' AND tags ?| array['a']",
    "SELECT * FROM t WHERE a::text = 'x'",
    "SELECT a INTO_x FROM t",
    "SELECT into_date, intox FROM t",
    'SELECT * FROM t WHERE "into" = 1',
    "SELECT * FROM t WHERE x = 'a' -- it's a note\n AND y = 1",
    "SELECT * FROM t WHERE x = 'a' /* don't */ AND y = 1",
    "SELECT * FROM t WHERE name = 'O''Brien' AND status = 'update'",
    "select date_format(create_time,'%Y-%m') m, count(*) from t group by m",
    "SELECT * FROM t WHERE k = '{{x}}'",
    "SELECT 1 FROM dual WHERE 'a#b' = 'x'",
    "SELECT EXECUTE_time, exec FROM t",
    "SELECT * FROM t WHERE c = 'x' GROUP BY a HAVING count(*) > 1 ORDER BY 1",
    "WITH RECURSIVE r AS (SELECT 1 UNION ALL SELECT n+1 FROM r WHERE n<3) SELECT * FROM r",
    "WITH a AS (SELECT 1) SELECT REPLACE('a','a','b') FROM a",
    "WITH a AS (SELECT (1) create_time) SELECT * FROM a",
    "WITH a AS (SELECT max(id) updated FROM t) SELECT * FROM a",
    "WITH a AS (SELECT 1) (SELECT * FROM a)",
    "WITH a AS (SELECT 1), b (x) AS MATERIALIZED (SELECT 2) SELECT * FROM a, b",
    "WITH a AS (SELECT count(1) grant_total FROM t) SELECT * FROM a",
    "WITH a AS (SELECT sum(x) replace FROM t) SELECT * FROM a",
    "WITH a AS (SELECT (x) create FROM t) SELECT * FROM a",
    "WITH a AS (SELECT sum(x) Drop FROM t) SELECT * FROM a",
    "WITH a AS (SELECT 1) SELECT count(x) comment, max(y) set FROM a",
    "WITH x AS (SELECT 1) TABLE secrets",
    "SELECT (a) update_count FROM t",
    "SELECT share, mode, lock_in FROM t WHERE for_share = 1",
    # PostgreSQL's named argument notation.
    "SELECT make_interval(days := 2)",
    "SELECT COUNT(*) create FROM t",
    "SELECT 1 FROM t WHERE id = (SELECT 1) UNION SELECT 2 /*! */",
    # Non-ASCII names, and odd characters inside quotes, are ordinary.
    "SELECT 客户, 金额 FROM (SELECT customer AS 客户, amount AS 金额 FROM t WHERE customer = '张三') 表",
    "SELECT 1 WHERE 1 = 1 -- 这是注释；带中文分号",
    "SELECT 'full\u3000width', 'nbsp\u00a0here', 'tab\there', 'bell\x07' FROM t",
    "SELECT 1 AS \"a\u3000b\"",
    "SELECT\n\t1\r\nFROM t\n;\n",
    "SELECT 5 - -3, 5--3, 5 -- 3",
)

# Rows of READS that do not pass on a dialect, and why.
NOT_ON: dict[SqlDialect, frozenset[str]] = {
    "postgresql": frozenset(
        {
            # '#' is an operator there: the statement starts with it.
            "# c\nSELECT 1",
        }
    ),
    "mysql": frozenset(
        {
            # /*! ... */ is run as SQL by MySQL, whatever is in it.
            "SELECT 1 FROM t WHERE id = (SELECT 1) UNION SELECT 2 /*! */",
            # "$$" is not a quote there: the ";" is a second statement.
            "SELECT $$a;b$$",
            # ":=" assigns a session variable there.
            "SELECT make_interval(days := 2)",
            # A double-quoted string there, with the odd character inside.
        }
    ),
    "generic": frozenset(
        {
            "# c\nSELECT 1",
            # DM8, the check alone: a row-changing keyword is refused where
            # it could be a name (all four are reserved words in DM8)...
            "SELECT t.update, t.delete FROM t",
            "SELECT update FROM t",
            "SELECT id AS update, id AS merge FROM t",
            "SELECT o.update FROM (SELECT 1 AS update) o",
            "SELECT (update) FROM t",
            "SELECT * FROM t WHERE (delete IS NULL)",
            "SELECT count(*) AS merge FROM t",
            # ...and a backslash before a quote is read both ways.
            "SELECT * FROM t WHERE path = 'C:\\tmp\\' AND action = 'delete'",
            "SELECT 'C:\\tmp\\' AS p, 'delete' AS op",
            "SELECT 'it\\'s' AS a, 'delete' AS b",
            "SELECT id FROM t WHERE note = 'it\\'s' OR customer = 'update'",
            "SELECT $$a;b$$",
        }
    ),
}

# Writes dressed as reads, refused on every dialect.
WRITES: tuple[str, ...] = (
    "INSERT INTO t VALUES (1)",
    "UPDATE t SET a = 1",
    "DELETE FROM t",
    "DROP TABLE t",
    "ALTER TABLE t ADD c int",
    "CREATE TABLE x (id int)",
    "TRUNCATE TABLE t",
    "RENAME TABLE a TO b",
    "GRANT ALL ON t TO u",
    "SET @x = 1",
    "SET search_path = rv",
    "SET TRANSACTION READ WRITE",
    "CALL p()",
    "DO rv_fn()",
    "HANDLER t OPEN",
    "LOCK TABLE t",
    "VACUUM t",
    "PREPARE p AS DELETE FROM t",
    "EXECUTE p",
    "COPY t TO PROGRAM 'id'",
    "COPY (SELECT 1) TO '/tmp/x'",
    "LOAD DATA INFILE '/etc/passwd' INTO TABLE t",
    "SELECT 1; DROP TABLE t",
    "SELECT 1; DELETE FROM t",
    "COMMIT; DELETE FROM t",
    "WITH d AS (SELECT 1) DELETE FROM t",
    "WITH d AS (SELECT 1) /* gap */ DELETE FROM t",
    "WITH d AS (SELECT 1)\n-- gap\nDELETE FROM t",
    "WITH x AS (SELECT 1) INSERT INTO t SELECT 5",
    "WITH d AS (DELETE FROM t RETURNING id) SELECT * FROM d",
    "WITH d AS (UPDATE t SET a = 1 RETURNING id) SELECT * FROM d",
    "WITH d AS (UPDATE ONLY s.t AS x SET a = 1 RETURNING id) SELECT * FROM d",
    "WITH d AS (INSERT INTO t (a) VALUES (1) RETURNING id) SELECT * FROM d",
    "WITH d AS (/* hidden */ DELETE /* x */ FROM t RETURNING id) SELECT * FROM d",
    "WITH a AS (SELECT 1), d AS NOT MATERIALIZED (DELETE FROM t RETURNING id) SELECT * FROM d",
    "(SELECT 1) UNION (DELETE FROM t RETURNING 1)",
    "EXPLAIN ANALYZE DELETE FROM t",
    "explain analyse verbose delete from t",
    "EXPLAIN (ANALYZE) DELETE FROM t",
    "EXPLAIN (ANALYZE, FORMAT JSON) INSERT INTO t VALUES (5)",
    "EXPLAIN (VERBOSE, ANALYZE TRUE) DELETE FROM t",
    "EXPLAIN /* c */ ANALYZE /* c */ DELETE FROM t",
    "EXPLAIN ANALYZE CREATE TABLE c AS SELECT * FROM t",
    "EXPLAIN (ANALYZE) CREATE TABLE c AS SELECT 1",
    "EXPLAIN ANALYZE REFRESH MATERIALIZED VIEW totals",
    "EXPLAIN ANALYZE EXECUTE p",
    "EXPLAIN ANALYZE WITH d AS (DELETE FROM t RETURNING id) SELECT * FROM d",
    "EXPLAIN ANALYZE SELECT * INTO c FROM t",
    "EXPLAIN ANALYZE FORMAT=TREE DELETE a FROM t a JOIN t b ON a.id = b.id",
    "DESCRIBE ANALYZE DELETE a FROM t a JOIN t b ON a.id = b.id",
    "SELECT * INTO c FROM t",
    "SELECT id, name INTO TEMP c FROM t",
    "SELECT 1 INTO @a",
    "SELECT * FROM t INTO OUTFILE '/tmp/x'",
    "SELECT * FROM t INTO DUMPFILE '/tmp/x'",
    "SELECT * FROM t FOR UPDATE",
    "SELECT * FROM t FOR NO KEY UPDATE",
    # Shared locks are locks too (PostgreSQL refuses them in a read-only
    # transaction; MySQL takes them).
    "SELECT * FROM t FOR SHARE",
    "SELECT * FROM t FOR KEY SHARE",
    "SELECT * FROM t LOCK IN SHARE MODE",
    "SELECT * FROM t WHERE id = 1 FOR SHARE NOWAIT",
    "SELECT SLEEP(1)",
    "SELECT sleep (1)",
    "SELECT/**/SLEEP/**/(2)",
    "SELECT BENCHMARK(1000000, SHA1('x'))",
    "SELECT LOAD_FILE('/etc/passwd')",
    "SELECT pg_sleep(10)",
    "SELECT pg_catalog.pg_sleep(10)",
    "SELECT pg_sleep_for('5 seconds')",
    "SELECT EXEC('x')",
    # A control character or a non-ASCII "space" outside quotes: what it
    # does depends on the server's lexer to the byte. MySQL starts a "--"
    # comment before U+007F and not before U+3000 or U+0085; all of these
    # wrote a file or a variable on a read-only MySQL connector.
    "SELECT 1 --\x7f'\nINTO OUTFILE '/tmp/rv_out1' --\x7f'",
    "SELECT 1 --\x7f'\n/*!INTO OUTFILE '/tmp/rv_out2'*/ --\x7f'",
    "SELECT 1 --\x7f'\nINTO @leak --\x7f'",
    "SELECT 7 --\x7f'\nFOR SHARE --\x7f'",
    "SELECT 1 FROM t `\u3000a` WHERE 1 --\u3000a.id INTO OUTFILE '/tmp/rv_out3'",
    "SELECT 1 FROM t `\u00a0a` WHERE 1 --\u00a0a.id INTO @leak2",
    "SELECT 1 FROM t `\u0085a` WHERE 1 --\u0085a.id INTO OUTFILE '/tmp/rv_out4'",
    "\u3000SELECT 1",
    "SELECT\x001",
    "\x00/*!CREATE TABLE pwn AS*/ SELECT 1",
    "SELECT 1 \x00 /*!INTO OUTFILE '/tmp/rv_out5'*/",
    "SELECT 1 /* \x0b */",
    "SELECT 1 -- zero\u200bwidth",
    "SELECT\u00a01",
    "SELECT 1\x0c",
    # Functions that act outside the query; PostgreSQL runs them in a
    # read-only transaction for a role that is allowed to.
    "SELECT dblink_exec('dbname=x', 'INSERT INTO t VALUES (1)')",
    "SELECT * FROM dblink('dbname=x', 'INSERT INTO t VALUES (1) RETURNING id') AS t(id int)",
    "SELECT dblink_connect('c', 'dbname=x')",
    "SELECT pg_read_file('/etc/passwd')",
    "SELECT pg_read_binary_file('/etc/hostname')",
    "SELECT pg_catalog.pg_read_file('/etc/passwd')",
    "SELECT PG_READ_FILE /* c */ ('/etc/passwd')",
    "SELECT pg_ls_dir('/')",
    "SELECT pg_ls_logdir()",
    "SELECT lo_import('/etc/passwd')",
    "SELECT lo_export(1, '/tmp/x')",
    "SELECT pg_terminate_backend(pg_backend_pid())",
    "SELECT pg_cancel_backend(1)",
    "SELECT pg_reload_conf()",
    "SELECT pg_switch_wal()",
    "SELECT pg_stat_reset()",
    "SELECT pg_stat_reset_shared('bgwriter')",
    "SELECT pg_create_restore_point('x')",
    "SELECT set_config('statement_timeout', '0', true), g FROM generate_series(1, 9) g",
    "EXPLAIN ANALYZE SELECT pg_read_file('/etc/passwd')",
    "WITH f AS (SELECT pg_ls_dir('/') AS name) SELECT * FROM f",
)

# Refused only where the dialect makes them a write.
ONLY_ON: dict[SqlDialect, tuple[str, ...]] = {
    "postgresql": (
        'SELECT "pg_sleep"(10)',
        'SELECT "pg_read_file"(\'/etc/passwd\')',
        # "$$" opens a string only in PostgreSQL; elsewhere this is a
        # harmless query, here the statement is unreadable past it.
    ),
    "mysql": (
        "/*!CREATE TABLE pwn AS*/ SELECT * FROM secrets",
        "/*!50000 CREATE TABLE pwn2 */ SELECT 1 AS a",
        "/*!CREATE OR REPLACE VIEW v AS*/ SELECT token FROM secrets",
        "/*!CALL rv_ddl() */ # SELECT",
        "/*!SET SESSION sql_mode = (*/ SELECT 'NO_BACKSLASH_ESCAPES' /*!)*/",
        "/*!SET @b = (*/ SELECT 7 /*!)*/",
        "/*!DROP TABLE pwn, */ SELECT",
        "/*!INSERT INTO t */ SELECT 55,'c','c'",
        "/*!DO*/ SELECT rv_fn()",
        "/*!DROP TABLE IF EXISTS victim, `*/ SELECT /*!`*/",
        "/*!ALTER VIEW v AS*/ SELECT token FROM secrets",
        "/*!ALTER TABLE victim2 COMMENT = '*/ SELECT /*!'*/",
        "/*!TRUNCATE TABLE victim2 #*/ SELECT\n*/",
        "/*!RENAME TABLE secrets TO `*/ SELECT /*!`*/",
        "/*!CREATE USER 'u'@'%' IDENTIFIED BY '*/ SELECT /*!'*/",
        "/*!GRANT ALL ON *.* TO 'u'@'%' WITH GRANT OPTION #*/ SELECT\n*/",
        "/*!SET GLOBAL max_connections = (*/ SELECT 123 /*!)*/",
        "SELECT 1 /*!UNION SELECT token FROM secrets*/",
        "/*M!CREATE TABLE pwn AS*/ SELECT * FROM secrets",
        "/*M!100100 DROP TABLE t, `*/ SELECT /*M!`*/",
        # "--" starts a comment only before whitespace: this is 5 - -1.
        "SELECT 5 --1 INTO @c",
        "SELECT id FROM t WHERE id=1 --1 FOR UPDATE",
        "SELECT `SLEEP`(2)",
        # Sets a user variable on the session.
        "SELECT @a := 5",
        # "$$" and "#" hide nothing where they are not a quote / an operator.
        "WITH $$ AS (SELECT 1) DELETE FROM t WHERE 1 = 1 /* $$ */",
    ),
    "generic": (
        "WITH $$ AS (SELECT 1) DELETE FROM t WHERE 1 = 1 /* $$ */",
        "WITH d AS (SELECT 1 # ) DELETE FROM t\n) SELECT * FROM d",
        'SELECT "SLEEP"(2)',
        # Standard strings end at the backslash-quote; MySQL's rule is
        # tried too, and the DELETE it uncovers is refused.
        "SELECT 'a\\' AS x, 'b' FROM t WHERE c = ' DELETE FROM t WHERE d = '",
    ),
}


def _refused(sql: str, dialect: SqlDialect | None) -> bool:
    try:
        validate_sql(sql, dialect=dialect)
    except SqlSafetyError:
        return True
    return False


@pytest.mark.parametrize("dialect", DIALECTS)
def test_reads_pass(dialect: SqlDialect) -> None:
    expected = [sql for sql in READS if sql not in NOT_ON[dialect]]
    refused = [sql for sql in expected if _refused(sql, dialect)]
    assert refused == []


@pytest.mark.parametrize("dialect", DIALECTS)
def test_the_exceptions_are_exactly_the_listed_ones(dialect: SqlDialect) -> None:
    assert NOT_ON[dialect] <= set(READS)
    passed = [sql for sql in NOT_ON[dialect] if not _refused(sql, dialect)]
    assert passed == []


@pytest.mark.parametrize("dialect", DIALECTS)
def test_writes_are_refused(dialect: SqlDialect) -> None:
    passed = [sql for sql in (*WRITES, *ONLY_ON[dialect]) if not _refused(sql, dialect)]
    assert passed == []


def test_an_unknown_database_is_read_every_way() -> None:
    """With no dialect, a write under any reading is refused."""
    every = (*WRITES, *(sql for rows in ONLY_ON.values() for sql in rows))
    assert [sql for sql in every if not _refused(sql, None)] == []


@pytest.mark.parametrize("dialect", DIALECTS)
def test_a_connector_that_allows_writes_is_limited_only_by_the_never_allowed(
    dialect: SqlDialect,
) -> None:
    never = ("SLEEP", "BENCHMARK", "LOAD_FILE", "pg_sleep", "EXEC(", "OUTFILE", "DUMPFILE")
    # A connector that allows writes may call the functions a read-only one may not.
    never += ("LOAD DATA", "; ")
    for sql in WRITES:
        if any(mark.lower() in sql.lower() for mark in never):
            continue
        if any(is_odd(ch) for ch in sql):
            continue
        assert validate_sql(sql, allow_write=True, dialect=dialect) == sql.strip().rstrip(";")


class TestReading:
    """The reading the rules are applied to."""

    def _words(self, sql: str, dialect: SqlDialect) -> list[str]:
        return [lex.upper for lex in read_sql(sql, dialect=dialect) if lex.kind == "word"]

    def test_a_backslash_ends_a_string_or_not_by_dialect(self) -> None:
        sql = "SELECT 'a\\' , b , '"
        assert self._words(sql, "postgresql") == ["SELECT", "B"]
        assert self._words(sql, "generic") == ["SELECT", "B"]
        assert self._words(sql, "mysql") == ["SELECT"]
        # E'...' takes backslash escapes in PostgreSQL.
        assert self._words("SELECT E'a\\' , b , '", "postgresql") == ["SELECT", "E"]

    def test_double_quotes_are_a_name_or_a_string_by_dialect(self) -> None:
        kinds = {
            dialect: [lex.kind for lex in read_sql('SELECT "x"', dialect=dialect)]
            for dialect in DIALECTS
        }
        assert kinds == {
            "postgresql": ["word", "name"],
            "mysql": ["word", "string"],
            "generic": ["word", "name"],
        }

    def test_comments_by_dialect(self) -> None:
        assert self._words("SELECT 1 # x\n y", "mysql") == ["SELECT", "Y"]
        assert self._words("SELECT 1 # x\n y", "postgresql") == ["SELECT", "X", "Y"]
        assert self._words("SELECT 1 --x\n y", "mysql") == ["SELECT", "X", "Y"]
        assert self._words("SELECT 1 -- x\n y", "mysql") == ["SELECT", "Y"]
        assert self._words("SELECT 1 --x\n y", "postgresql") == ["SELECT", "Y"]
        # PostgreSQL nests block comments.
        assert self._words("SELECT /* a /* b */ c */ d", "postgresql") == ["SELECT", "D"]
        assert self._words("SELECT /* a /* b */ c */ d", "mysql") == ["SELECT", "C", "D"]

    def test_mysql_starts_a_dash_comment_only_before_an_ascii_space_or_control(self) -> None:
        for after in (" ", "\t", "\r", "\x0b", "\x0c", "\x00", "\x01", "\x1f", "\x7f"):
            assert self._words(f"SELECT 1 --{after}x\n y", "mysql") == ["SELECT", "Y"], repr(after)
        assert self._words("SELECT 1 --", "mysql") == ["SELECT"]
        # Not white space to MySQL, whatever Python's str.isspace says:
        # the "comment" is code.
        for after in ("\u3000", "\u00a0", "\u0085", "\u2028", "x", "1"):
            words = self._words(f"SELECT 1 --{after}x INTO y", "mysql")
            assert "INTO" in words, repr(after)

    def test_names_and_keywords_are_ascii_folded_and_non_ascii_is_part_of_a_name(self) -> None:
        # U+017F (long s) upper-cases to "S" in Python; no server folds it.
        assert self._words("\u017felect 1", "postgresql") == ["\u017fELECT"]
        # An emoji is not a "word character" to Python; to the servers its
        # bytes are part of the name, "$" and all.
        for dialect in DIALECTS:
            assert [
                (lex.kind, lex.text) for lex in read_sql("SELECT a\U0001f600$$b", dialect=dialect)
            ] == [("word", "SELECT"), ("word", "a\U0001f600$$b")]
        assert self._words("SELECT １２ AS n", "mysql") == ["SELECT", "１２", "AS", "N"]

    def test_odd_characters_are_reported_outside_quotes_only(self) -> None:
        def odd(sql: str, dialect: SqlDialect = "mysql") -> list[str]:
            return [lex.text for lex in read_sql(sql, dialect=dialect) if lex.kind == "odd"]

        assert odd("SELECT\x00 1 \u3000 -- \x7f\n /* \u200b */") == [
            "\x00",
            "\u3000",
            "\x7f",
            "\u200b",
        ]
        assert odd("SELECT '\x00\u3000', `\u00a0`, \"\x7f\"") == []
        assert odd("SELECT 1\r\nFROM t\n") == []
        # DM8: nobody here knows where its "--" comment ends.
        assert odd("SELECT 1 -- a\r, b", "generic") == ["\r"]
        assert odd("SELECT 1 -- a\r\n, b", "generic") == []

    def test_an_executable_comment_is_seen_only_where_it_is_one(self) -> None:
        sql = "SELECT 1 /*!50000 , 2 */"
        assert [lex.kind for lex in read_sql(sql, dialect="mysql")][-1] == "executable"
        assert all(lex.kind != "executable" for lex in read_sql(sql, dialect="postgresql"))
        # Inside a string it is text.
        assert all(
            lex.kind != "executable" for lex in read_sql("SELECT '/*! x */'", dialect="mysql")
        )
