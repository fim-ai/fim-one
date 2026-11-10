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
    "SELECT get_lock, pg_advisory_lock FROM t WHERE note = 'GET_LOCK(1)'",
    # PostgreSQL's named argument notation.
    "SELECT make_interval(days := 2)",
    # For the transaction only, which a read-only connector rolls back.
    "SELECT set_config('search_path', 'rv', true)",
    "SELECT set_config('search_path', 'rv', TRUE), current_setting('search_path')",
    "SELECT pg_catalog.set_config('SEARCH_PATH', 'rv', true /* c */ )",
    "SELECT set_config('DateStyle', 'ISO', true), set_config('timezone', 'UTC', true)",
    "SELECT set_config('intervalstyle', 'iso_8601', true), set_config('extra_float_digits', '3', true)",
    "SELECT set_config, dblink, pg_read_file FROM t",
    # An alias with a column list is not a call.
    "SELECT * FROM t AS dblink (a, b)",
    "SELECT * FROM generate_series(1, 3) AS pg_read_file (n)",
    'SELECT * FROM t AS "set_config" (a, b)',
    # PostgreSQL's SEARCH and CYCLE clauses of a recursive query.
    "WITH RECURSIVE t(n) AS (SELECT 1 UNION ALL SELECT n+1 FROM t WHERE n < 3) "
    "SEARCH DEPTH FIRST BY n SET ord SELECT * FROM t",
    "WITH RECURSIVE t(n, m) AS (SELECT 1, 1 UNION ALL SELECT n+1, m FROM t WHERE n < 3) "
    "SEARCH BREADTH FIRST BY n, m SET ord SELECT * FROM t ORDER BY ord",
    "WITH RECURSIVE t(n) AS (SELECT 1 UNION ALL SELECT n+1 FROM t WHERE n < 3) "
    "CYCLE n SET is_cycle USING path SELECT * FROM t",
    "WITH RECURSIVE t(n) AS (SELECT 1 UNION ALL SELECT n+1 FROM t WHERE n < 3) "
    "SEARCH DEPTH FIRST BY n SET ord CYCLE n SET is_cycle TO 'y' DEFAULT 'n' USING path, "
    "u AS (SELECT 2) SELECT * FROM t, u",
    "SELECT 'x' AS pg_read_file, (1) AS set_config",
    "SELECT coalesce(set_config.dblink, 0) FROM set_config",
    "SELECT COUNT(*) create FROM t",
    "SELECT 1 FROM t WHERE id = (SELECT 1) UNION SELECT 2 /*! */",
    # Non-ASCII names, and odd characters inside quotes, are ordinary.
    "SELECT 客户, 金额 FROM (SELECT customer AS 客户, amount AS 金额 FROM t WHERE customer = '张三') 表",
    "SELECT 1 WHERE 1 = 1 -- 这是注释；带中文分号",
    "SELECT 'full\u3000width', 'nbsp\u00a0here', 'tab\there', 'bell\x07' FROM t",
    "SELECT 1 AS \"a\u3000b\"",
    "SELECT\n\t1\r\nFROM t\n;\n",
    "SELECT 5 - -3, 5--3, 5 -- 3",
    # Inside a comment, whose end ASCII alone decides, they change nothing.
    "SELECT 1 -- 中文注释\u3000带全角空格",
    "SELECT 1 /* 说明\u3000全角 */",
    "SELECT 姓名, 金额 FROM 订单表 WHERE 状态 = '已完成' -- 查询已完成订单\u3000",
    "SELECT 1 -- zwj \U0001f468\u200d\U0001f469\u200d\U0001f467 in comment",
    "SELECT 1 -- zero\u200bwidth, rtl \u200f mark, soft\u00adhyphen, nbsp\u00a0",
    "SELECT 1 /* \u2028 line separator */ FROM t",
    # Comments that only look like MySQL's executable ones.
    "/*/!CREATE TABLE pwn AS*/ SELECT 1",
    "/*\n!CREATE TABLE pwn AS*/ SELECT 1",
    # Planned, not run.
    "EXPLAIN (ANALYZE off) DELETE FROM t",
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
            # DM8's lexer is not known here: odd characters stay refused
            # in its comments too.
            "SELECT 1 -- 中文注释\u3000带全角空格",
            "SELECT 1 /* 说明\u3000全角 */",
            "SELECT 姓名, 金额 FROM 订单表 WHERE 状态 = '已完成' -- 查询已完成订单\u3000",
            "SELECT 1 -- zwj \U0001f468\u200d\U0001f469\u200d\U0001f467 in comment",
            "SELECT 1 -- zero\u200bwidth, rtl \u200f mark, soft\u00adhyphen, nbsp\u00a0",
            "SELECT 1 /* \u2028 line separator */ FROM t",
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
    "WITH RECURSIVE t(n) AS (SELECT 1) SEARCH DEPTH FIRST BY n SET ord DELETE FROM u",
    "WITH RECURSIVE t(n) AS (SELECT 1) CYCLE n SET c USING p INSERT INTO u SELECT * FROM t",
    "WITH RECURSIVE t(n) AS (SELECT 1) SEARCH DEPTH FIRST BY n SET ord, "
    "d AS (DELETE FROM u RETURNING id) SELECT * FROM d",
    "SELECT 1 AS a, dblink_exec('x', 'y')",
    "SELECT * FROM t AS a, dblink('x', 'select 1') AS b (c int)",
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
    "\u3000SELECT 1",
    "SELECT\x001",
    "\x00/*!CREATE TABLE pwn AS*/ SELECT 1",
    "SELECT 1 \x00 /*!INTO OUTFILE '/tmp/rv_out5'*/",
    "SELECT 1 /* \x0b */",
    "SELECT 1 -- nul \x00 in a comment",
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
    "EXPLAIN ANALYZE SELECT pg_read_file('/etc/passwd')",
    "SELECT * FROM pg_read_file('/etc/hostname')",
    "SELECT query_to_xml('select pg_read_file(''/etc/hostname'')', true, false, '')",
    "SELECT (xpath('//text()', query_to_xml('select dblink_exec(''x'', ''y'')', true, false, '')))[1]",
    "SELECT query_to_xml_and_xmlschema('select 1', true, false, '')",
    "SELECT table_to_xml('t', true, false, '')",
    "SELECT cursor_to_xml('c', 1, true, false, '')",
    "SELECT database_to_xml(true, false, '')",
    "SELECT schema_to_xmlschema('public', true, false, '')",
    "SELECT * FROM ts_stat('select pg_read_file(''/etc/hostname'')::tsvector')",
    "SELECT pg_stat_file('/etc/hostname')",
    "SELECT pg_current_logfile()",
    "SELECT pg_create_physical_replication_slot('s')",
    "SELECT pg_create_logical_replication_slot('s', 'pgoutput')",
    "SELECT pg_drop_replication_slot('s')",
    "SELECT pg_replication_origin_create('o')",
    "SELECT set_config('search_path', 'rv', false)",
    # Locks that belong to the session, not to the query.
    "SELECT GET_LOCK('l', 0)",
    "SELECT COUNT(*) FROM t WHERE GET_LOCK('l', 30) = 5",
    "SELECT RELEASE_LOCK('l'), RELEASE_ALL_LOCKS()",
    "SELECT IS_FREE_LOCK('l'), IS_USED_LOCK('l')",
    "SELECT pg_advisory_lock(4242)",
    "SELECT pg_advisory_xact_lock(1), pg_try_advisory_lock(2)",
    "SELECT pg_advisory_unlock_all()",
    "SELECT set_config('statement_timeout', '0', NOT false)",
    # Local to the transaction, but not a setting a read needs.
    "SELECT set_config('statement_timeout', '0', true)",
    "SELECT set_config('lock_timeout', '0', true)",
    "SELECT set_config('role', 'rv_high', true)",
    "SELECT set_config('session_authorization', 'rv_high', true)",
    "SELECT set_config('row_security', 'off', true)",
    "SELECT set_config('transaction_read_only', 'off', true)",
    "SELECT set_config('default_transaction_read_only', 'off', true)",
    "SELECT set_config('work_mem', '2000000000', true)",
    "SELECT set_config('x.y', 'v', true)",
    # The setting's name must be written out as a plain string.
    "SELECT set_config(a, b, true) FROM (SELECT 'search_path' a, 'rv' b) x",
    "SELECT set_config('search_' || 'path', 'rv', true)",
    "SELECT set_config(E'search_path', 'rv', true)",
    "SELECT set_config($$search_path$$, 'rv', true)",
    "SELECT set_config('search_path', 'rv', (true))",
    "SELECT set_config('search_path', 'rv', is_local => true)",
    "SELECT set_config('search_path', 'rv', true), set_config('role', 'x', true)",
    "SELECT set_config('statement_timeout', '0', 'true')",
    "SELECT set_config('a', 'b')",
    "WITH f AS (SELECT pg_ls_dir('/') AS name) SELECT * FROM f",
)

# Refused only where the dialect makes them a write.
ONLY_ON: dict[SqlDialect, tuple[str, ...]] = {
    "postgresql": (
        'SELECT "pg_sleep"(10)',
        'SELECT "pg_read_file"(\'/etc/passwd\')',
        # A name spelled with escapes is not the name the check sees.
        "SELECT U&\"pg\\005Fread\\005Ffile\"('/etc/hostname')",
        "SELECT U&\"d\\0062link_exec\"('dbname=x', 'INSERT INTO t(id) VALUES (79)')",
        "SELECT U&'d!0061ta' UESCAPE '!'",
        'SELECT u&"id" FROM t',
        # A "--" comment ends at a carriage return there.
        "SELECT 1 --x\r, pg_read_file('/etc/hostname')",
        "SELECT 1 --x\r FOR SHARE OF o FROM t o",
        "SELECT * FROM t --x\r FOR SHARE",
        # A non-ASCII character is part of a name, "$" after it too: no
        # dollar-quoted string starts here ...
        "SELECT 1 AS \U0001f600$$ , pg_read_file('/etc/hostname') AS x\U0001f600$$",
        "SELECT 1 AS \U0001f600$$ INTO newt FROM t -- \U0001f600$$",
        "WITH d AS \U0001f600$$ (SELECT 1) SELECT 1 --$$\n (DELETE FROM t RETURNING id) SELECT * FROM d",
        # ... and a tag may be non-ASCII: this one is a string.
        "SELECT $\u00e9$ ' $\u00e9$, pg_read_file('/etc/hostname') -- '",
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
        # "--" before a non-ASCII "space" is two minus signs to MySQL, and
        # what follows is SQL. (To PostgreSQL it is a comment, and stays one.)
        "SELECT 1 FROM t `\u3000a` WHERE 1 --\u3000a.id INTO OUTFILE '/tmp/rv_out3'",
        "SELECT 1 FROM t `\u00a0a` WHERE 1 --\u00a0a.id INTO @leak2",
        "SELECT 1 FROM t `\u0085a` WHERE 1 --\u0085a.id INTO OUTFILE '/tmp/rv_out4'",
        "/**/ /*!CREATE TABLE pwn AS*/ SELECT 1",
        "/*!SET SESSION character_set_client = (*/ SELECT 'gbk' /*!)*/",
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
        "SELECT 1 FROM t `\u3000a` WHERE 1 --\u3000a.id INTO OUTFILE '/tmp/rv_out3'",
        "SELECT 1 FROM t `\u00a0a` WHERE 1 --\u00a0a.id INTO @leak2",
        "SELECT 1 FROM t `\u0085a` WHERE 1 --\u0085a.id INTO OUTFILE '/tmp/rv_out4'",
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


def test_the_row_number_idiom_is_refused_with_what_to_write_instead() -> None:
    with pytest.raises(SqlSafetyError, match=r"ROW_NUMBER\(\) OVER"):
        validate_sql(
            "SELECT @rownum := @rownum + 1 AS r FROM t, (SELECT @rownum := 0) x", dialect="mysql"
        )


# Ordinary reads in each database's own syntax (window functions, JSON
# operators, hints, catalog queries, comments in Chinese, ...), from the
# third review of this check. All of them pass on their dialect.
DIALECT_READS: dict[SqlDialect, tuple[str, ...]] = {
    "postgresql": (
        # A U&'...' string is a string; only U&"..." spells a name.
        "SELECT E'tab\\there', 'it''s', $$dollar 'quoted'$$, $q$with ; semi$q$, U&'d\\0061ta', B'101', X'1F'",
        "SELECT U&'d\\0061ta', U&'\\0441\\043B\\043E\\043D'",
        'SELECT customer, SUM(amount) OVER (PARTITION BY customer ORDER BY created ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS running FROM rv.orders',
        "WITH monthly AS (SELECT date_trunc('month', created) AS m, SUM(amount) AS total FROM rv.orders GROUP BY 1), ranked AS (SELECT *, RANK() OVER (ORDER BY total DESC) r FROM monthly) SELECT * FROM ranked WHERE r <= 3;",
        'SELECT * FROM rv.orders o WHERE EXISTS (SELECT 1 FROM rv.orders x WHERE x.id = o.id AND x.amount > (SELECT AVG(amount) FROM rv.orders))',
        "SELECT CASE WHEN amount > 15 THEN 'high' WHEN amount IS NULL THEN 'n/a' ELSE 'low' END AS bucket, COUNT(*) FROM rv.orders GROUP BY 1 ORDER BY 2 DESC",
        "SELECT * FROM rv.orders WHERE created >= CURRENT_DATE - INTERVAL '30 days' AND created < now()",
        'SELECT tags->>\'k\' AS k, tags #>> \'{a,b}\' AS ab, tags @> \'{"k":"v"}\' AS has, tags ? \'k\' AS q FROM rv.orders WHERE tags ?| array[\'k\',\'z\']',
        "SELECT * FROM rv.orders WHERE customer ILIKE '%ali%' OR note ~* '^fi' OR note !~ 'x$' OR customer SIMILAR TO '(a|b)%'",
        'SELECT customer, created, SUM(amount) FROM rv.orders GROUP BY ROLLUP (customer, created) HAVING SUM(amount) > 0',
        'SELECT customer, COUNT(*) FROM rv.orders GROUP BY GROUPING SETS ((customer), ()) ORDER BY 1 NULLS LAST',
        'SELECT o.id, l.n FROM rv.orders o CROSS JOIN LATERAL (SELECT COUNT(*) n FROM rv.orders x WHERE x.id <= o.id) l',
        'SELECT id FROM rv.orders WHERE id = 1 UNION ALL SELECT id FROM rv.orders WHERE id = 2 EXCEPT SELECT 99 INTERSECT SELECT 98',
        'SELECT "update", "delete", "select" FROM (SELECT 1 AS "update", 2 AS "delete", 3 AS "select") t',
        "SELECT 客户, 金额 FROM (SELECT customer AS 客户, amount AS 金额 FROM rv.orders WHERE customer = '张三' OR note LIKE '%中文%') 表",
        '/* leading */ SELECT id -- trailing\n FROM rv.orders /* mid; with semicolon */ WHERE id = 1 -- end; DROP\n;',
        'SELECT id FROM rv.orders ORDER BY id DESC LIMIT 10 OFFSET 0;',
        'SELECT id FROM rv.orders FETCH FIRST 2 ROWS ONLY',
        'SELECT DISTINCT ON (customer) customer, created FROM rv.orders ORDER BY customer, created DESC',
        "SELECT COUNT(*) FILTER (WHERE amount > 10), string_agg(customer, ', ' ORDER BY customer), array_agg(id), percentile_cont(0.5) WITHIN GROUP (ORDER BY amount) FROM rv.orders",
        "SELECT id::text || ' - ' || COALESCE(note, ''), CAST(amount AS integer), EXTRACT(year FROM created), to_char(created, 'YYYY-MM') FROM rv.orders",
        'SELECT * FROM generate_series(1, 3) g(n) JOIN rv.orders o ON o.id = g.n',
        'SELECT * FROM unnest(ARRAY[1,2,3]) WITH ORDINALITY AS t(v, i)',
        'SELECT jsonb_agg(to_jsonb(o)) FROM rv.orders o',
        "SELECT table_name, column_name, data_type FROM information_schema.columns WHERE table_schema = 'rv' ORDER BY 1, ordinal_position",
        'SELECT relname, n_live_tup FROM pg_stat_user_tables',
        "SELECT current_database(), current_user, version(), current_setting('server_version'), now()",
        "SELECT pg_size_pretty(pg_total_relation_size('rv.orders'))",
        "SELECT pg_get_viewdef('pg_stat_activity'::regclass, true)",
        'SHOW server_version',
        'SHOW ALL',
        'EXPLAIN SELECT * FROM rv.orders',
        'EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) SELECT * FROM rv.orders WHERE id = 1',
        'EXPLAIN ANALYZE VERBOSE SELECT 1',
        'WITH RECURSIVE t(n) AS (VALUES (1) UNION ALL SELECT n + 1 FROM t WHERE n < 5) SELECT sum(n) FROM t',
        'WITH RECURSIVE t(n) AS (SELECT 1 UNION ALL SELECT n+1 FROM t WHERE n < 3) SEARCH DEPTH FIRST BY n SET ord SELECT * FROM t',
        'WITH a AS MATERIALIZED (SELECT 1 AS x), b AS NOT MATERIALIZED (SELECT 2 AS y) SELECT * FROM a, b',
        "SELECT * FROM rv.orders WHERE note = 'please delete; update; insert into x' AND customer <> 'drop table'",
        'SELECT last_update, updated_at, is_deleted, insert_date, set_at, call_count, lock_version, grant_id FROM (SELECT 1 last_update, 2 updated_at, 3 is_deleted, 4 insert_date, 5 set_at, 6 call_count, 7 lock_version, 8 grant_id) s',
        'SELECT id AS update, id AS delete, id AS insert, id AS merge, id AS replace, id AS set, id AS into_x FROM rv.orders',
        'SELECT o.update FROM (SELECT 1 AS update) o WHERE o.update IN (1) AND (o.update) = 1 AND (update) IS NOT NULL',
        "SELECT replace(customer, 'a', 'b'), overlay(note placing 'x' from 1), position('a' in customer), substring(note from 1 for 2), trim(both ' ' from note) FROM rv.orders",
        "SELECT * FROM rv.orders WHERE (amount, id) IN ((10.5, 1)) AND id = ANY('{1,2}'::int[]) AND id BETWEEN SYMMETRIC 5 AND 1",
        "SELECT 'C:\\tmp\\' AS p, 'delete' AS op",
        "SELECT * FROM rv.orders WHERE note LIKE '%sleep(%' OR note LIKE '%load data%' OR note = 'into outfile'",
        'SELECT 1 AS "for", 2 AS share',
        'SELECT lock, mode FROM (SELECT 1 AS lock, 2 AS mode) x',
        'TABLE rv.orders',
        "VALUES (1, 'a'), (2, 'b')",
        '(SELECT 1) UNION (SELECT 2) ORDER BY 1',
        'SELECT 1 WHERE 1 = 1 -- 这是注释；带中文分号',
        'SELECT count(*) AS "into", max(id) AS exec FROM rv.orders',
        'SELECT execute, exec FROM (SELECT 1 AS execute, 2 AS exec) t',
        'SELECT coalesce(set_config.dblink, 0) FROM rv.set_config',
        "SELECT * FROM rv.orders WHERE customer = 'x' AND created BETWEEN '2026-01-01' AND '2026-12-31' ORDER BY created DESC NULLS LAST LIMIT 50",
        "SELECT d::date FROM generate_series('2026-01-01'::date, '2026-01-03', '1 day') d",
        'SELECT NULLIF(amount, 0), GREATEST(1,2), id % 2, amount ^ 2, |/ amount, id # 3, ~id FROM rv.orders',
        'SELECT row_number() OVER w, lag(amount) OVER w FROM rv.orders WINDOW w AS (ORDER BY created)',
        'SELECT * FROM rv.orders TABLESAMPLE SYSTEM (50)',
        'SELECT xmlelement(name a, 1)',
        "SELECT '2026-01-01'::timestamptz AT TIME ZONE 'Asia/Shanghai'",
        'SELECT 1;',
        'select\n\t1\n;\n',
        'SELECT pg_typeof(1), txid_current_if_assigned()',
        'SELECT id FROM rv.orders WHERE id IN (SELECT id FROM rv.orders WHERE amount > 0) AND NOT (customer IS NULL)',
        "SELECT set_config('search_path', 'rv', true)",
        "SELECT current_setting('search_path')",
        'DESCRIBE rv.orders',
    ),
    "mysql": (
        'SELECT customer, SUM(id) OVER (PARTITION BY customer ORDER BY id ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS running FROM rv_orders',
        "WITH m AS (SELECT DATE_FORMAT(d, '%Y-%m') AS ym, SUM(dec1) AS total FROM cs GROUP BY 1), r AS (SELECT *, RANK() OVER (ORDER BY total DESC) rk FROM m) SELECT * FROM r WHERE rk <= 3;",
        "SELECT CASE WHEN n > 3 THEN 'high' ELSE 'low' END AS bucket, COUNT(*) FROM cs GROUP BY bucket ORDER BY 2 DESC",
        'SELECT * FROM cs WHERE d >= CURDATE() - INTERVAL 30 DAY OR dt < NOW() - INTERVAL 1 HOUR OR d = DATE_SUB(CURDATE(), INTERVAL 1 MONTH)',
        "SELECT j->>'$.a' AS a, j->'$.a', JSON_EXTRACT(j, '$.a'), JSON_CONTAINS(j, '1', '$.a') FROM cs",
        "SELECT * FROM cs WHERE l LIKE '%ali%' OR l REGEXP '^a' OR l RLIKE 'e$' OR l NOT LIKE 'x%'",
        'SELECT l, COUNT(*) FROM cs GROUP BY l WITH ROLLUP HAVING COUNT(*) > 0',
        'SELECT o.id, t.n FROM rv_orders o, LATERAL (SELECT COUNT(*) n FROM rv_orders x WHERE x.id <= o.id) t',
        'SELECT id FROM rv_orders WHERE id = 1 UNION ALL SELECT id FROM rv_orders WHERE id = 2',
        'SELECT `update`, `delete`, `select` FROM (SELECT 1 AS `update`, 2 AS `delete`, 3 AS `select`) t',
        'SELECT "double quoted string", \'it\\\'s\', \'it\'\'s\', "say \\"hi\\"", \'delete\' AS op, \'a\\\\\' AS bs',
        "SELECT 客户 FROM (SELECT g AS 客户 FROM cs WHERE g = '张三' OR g LIKE '%零%') 表",
        '/* leading */ SELECT id -- trailing\n FROM rv_orders # hash; comment\n WHERE id = 1 /* mid; semi */ -- end; DROP\n;',
        'SELECT id FROM rv_orders ORDER BY id DESC LIMIT 10 OFFSET 0;',
        'SELECT id FROM rv_orders LIMIT 0, 5',
        "SELECT GROUP_CONCAT(customer ORDER BY customer SEPARATOR ', '), COUNT(DISTINCT customer), IFNULL(MAX(id), 0), IF(COUNT(*) > 1, 'many', 'one') FROM rv_orders",
        "SELECT CONCAT(id, ' - ', COALESCE(note, '')), CAST(id AS CHAR), YEAR(NOW()), DATE_FORMAT(NOW(), '%Y-%m-%d %H:%i'), 5 % 2, 5 MOD 2, 5 DIV 2 FROM rv_orders",
        'SELECT table_name, column_name, data_type FROM information_schema.columns WHERE table_schema = DATABASE() ORDER BY 1, ordinal_position',
        'SHOW TABLES',
        'SHOW FULL COLUMNS FROM rv_orders',
        'SHOW CREATE TABLE rv_orders',
        'SHOW INDEX FROM cs',
        "SHOW VARIABLES LIKE 'version%'",
        'SHOW TABLE STATUS',
        'SHOW DATABASES',
        'DESCRIBE rv_orders',
        'DESC cs',
        'SHOW PROCESSLIST',
        'EXPLAIN SELECT * FROM rv_orders',
        'EXPLAIN FORMAT=JSON SELECT * FROM rv_orders WHERE id = 1',
        'EXPLAIN ANALYZE SELECT * FROM rv_orders',
        'WITH RECURSIVE t(n) AS (SELECT 1 UNION ALL SELECT n + 1 FROM t WHERE n < 5) SELECT SUM(n) FROM t',
        "SELECT * FROM rv_orders WHERE note = 'please delete; update; insert into x' AND customer <> 'drop table'",
        'SELECT last_update, updated_at, is_deleted, insert_date, set_at, call_count FROM (SELECT 1 last_update, 2 updated_at, 3 is_deleted, 4 insert_date, 5 set_at, 6 call_count) s',
        'SELECT o.update, o.delete FROM (SELECT 1 AS `update`, 2 AS `delete`) o',
        'SELECT id AS merge, id AS upsert FROM rv_orders',
        "SELECT REPLACE(customer, 'a', 'b'), INSERT(customer, 1, 1, 'X'), LEFT(note, 2), SUBSTRING(note, 1, 2), TRIM(BOTH ' ' FROM note), REPEAT('a', 2) FROM rv_orders",
        "SELECT * FROM rv_orders WHERE (id, customer) IN ((1, 'alice')) AND id BETWEEN 1 AND 5 AND customer IS NOT NULL",
        "SELECT * FROM rv_orders WHERE note LIKE '%sleep(%' OR note LIKE '%load data%' OR note = 'into outfile' OR note = '/*! x */' OR note = 'a := b'",
        'SELECT @@version, @@sql_mode',
        'TABLE rv_orders',
        '(SELECT 1) UNION (SELECT 2) ORDER BY 1',
        'SELECT 1 FROM DUAL WHERE 1 = 1 -- 这是注释；带中文分号',
        'SELECT 1 --no space comment',
        'SELECT 5 - -3, 5--3, 5 -- 3',
        'SELECT id FROM rv_orders WHERE id > 1-- trailing\nORDER BY id',
        'SELECT /*+ MAX_EXECUTION_TIME(1000) */ * FROM rv_orders',
        'SELECT SQL_NO_CACHE * FROM rv_orders',
        'SELECT * FROM rv_orders USE INDEX (PRIMARY) WHERE id = 1',
        'SELECT * FROM rv_orders FORCE INDEX (PRIMARY) IGNORE INDEX (PRIMARY)',
        'SELECT * FROM rv_orders PARTITION (p0)',
        'SELECT STRAIGHT_JOIN a.id FROM rv_orders a JOIN rv_orders b USING (id)',
        'SELECT ROW_NUMBER() OVER w, LAG(id) OVER w FROM rv_orders WINDOW w AS (ORDER BY id)',
        "SELECT 0x41, X'41', b'01', _utf8mb4'abc', N'abc', _latin1 X'E9', TRUE, NULL",
        'SELECT 1;',
        'select\n\t1\n;\n',
        'SELECT DATABASE(), USER(), VERSION(), CONNECTION_ID(), LAST_INSERT_ID(), FOUND_ROWS()',
        'SELECT id FROM rv_orders WHERE id IN (SELECT id FROM rv_orders WHERE id > 0) AND NOT (customer IS NULL)',
        'SELECT COUNT(*) AS `into`, MAX(id) AS `exec` FROM rv_orders',
        'SELECT exec_time, execute_count FROM (SELECT 1 exec_time, 2 execute_count) t',
        "SELECT JSON_OBJECT('k', id), JSON_ARRAYAGG(id) FROM rv_orders",
        'SELECT id, customer FROM rv_orders ORDER BY FIELD(id, 2, 1), customer COLLATE utf8mb4_bin',
        "SELECT TIMESTAMPDIFF(DAY, d, NOW()), DATEDIFF(NOW(), d), UNIX_TIMESTAMP(), STR_TO_DATE('2026-01-01', '%Y-%m-%d') FROM cs",
        "SELECT 'a' 'b', 'x' AS `set`, 1 AS call_id",
        'SELECT * FROM rv_orders WHERE customer = "alice" # 注释\n',
    ),
    "generic": (),
}
# From the same lists: refused by design, on the dialect they are written for.
DIALECT_REFUSED: dict[SqlDialect, tuple[str, ...]] = {
    "postgresql": (
        'SELECT * FROM rv.orders FOR UPDATE',
        'SELECT * FROM rv.orders FOR UPDATE SKIP LOCKED',
        'USE rv',
    ),
    "mysql": (
        'SELECT * FROM rv_orders FOR UPDATE',
        'SELECT * FROM rv_orders LOCK IN SHARE MODE',
        'SELECT @rownum := @rownum + 1 AS r FROM rv_orders, (SELECT @rownum := 0) x',
        'USE app',
    ),
    "generic": (),
}


@pytest.mark.parametrize("dialect", DIALECTS)
def test_reads_in_the_dialects_own_syntax_pass(dialect: SqlDialect) -> None:
    assert [sql for sql in DIALECT_READS[dialect] if _refused(sql, dialect)] == []
    assert [sql for sql in DIALECT_REFUSED[dialect] if not _refused(sql, dialect)] == []


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

        sql = "SELECT\x00 1 \u3000 -- \x7f\n /* \u200b */"
        # In a comment only ASCII control characters are odd ...
        assert odd(sql) == ["\x00", "\u3000", "\x7f"]
        assert odd(sql, "postgresql") == ["\x00", "\u3000", "\x7f"]
        # ... except for DM8, where everything odd is.
        assert odd(sql, "generic") == ["\x00", "\u3000", "\x7f", "\u200b"]
        assert odd("SELECT '\x00\u3000', `\u00a0`, \"\x7f\"") == []
        assert odd("SELECT 1\r\nFROM t\n") == []
        # DM8: nobody here knows where its "--" comment ends.
        assert odd("SELECT 1 -- a\r, b", "generic") == ["\r"]
        assert odd("SELECT 1 -- a\r\n, b", "generic") == []

    def test_postgresql_ends_a_dash_comment_at_a_carriage_return(self) -> None:
        assert self._words("SELECT 1 --x\r y", "postgresql") == ["SELECT", "Y"]
        assert self._words("SELECT 1 -- x\r y", "mysql") == ["SELECT"]
        assert self._words("SELECT 1 --x\r\n y", "postgresql") == ["SELECT", "Y"]

    def test_postgresql_dollar_tags_and_names_take_non_ascii(self) -> None:
        kinds = [lex.kind for lex in read_sql("SELECT $\u00e9$ ' $\u00e9$, b", dialect="postgresql")]
        assert kinds == ["word", "string", "punct", "word"]
        # "$$" right after a name character belongs to the name.
        assert self._words("SELECT 1 AS \U0001f600$$ , b AS x\U0001f600$$", "postgresql") == [
            "SELECT",
            "AS",
            "\U0001f600$$",
            "B",
            "AS",
            "X\U0001f600$$",
        ]

    def test_an_executable_comment_is_seen_only_where_it_is_one(self) -> None:
        sql = "SELECT 1 /*!50000 , 2 */"
        assert [lex.kind for lex in read_sql(sql, dialect="mysql")][-1] == "executable"
        assert all(lex.kind != "executable" for lex in read_sql(sql, dialect="postgresql"))
        # Inside a string it is text.
        assert all(
            lex.kind != "executable" for lex in read_sql("SELECT '/*! x */'", dialect="mysql")
        )
