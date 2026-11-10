"""SQL safety validation for database connector queries.

Validates SQL statements before execution. Only reads (SELECT, WITH ...
SELECT, TABLE, VALUES, EXPLAIN, SHOW, DESCRIBE) are allowed unless
``allow_write=True`` is explicitly passed.

Every rule is applied to the statement as its database reads it
(:mod:`.reading`): comments are gone, the inside of a string is not looked
at, and a keyword counts only where the database would see one. Nothing is
decided on the raw text.

This check is the first line, not the fence. A read-only connector also
runs every query in a read-only transaction where the driver has one (see
``DatabaseDriver.execute_query``), so a write this check does not recognise
is refused by the database itself. Where a driver has none (DM8) the check
is stricter. Neither stops a database account that is allowed to read
files or reach other servers from doing so through a function this module
has not heard of: the real fence is a least-privilege database account.
"""

from __future__ import annotations

from itertools import pairwise

from .reading import Lexeme, SqlDialect, ascii_lower, read_sql


class SqlSafetyError(Exception):
    """Raised when a SQL statement fails safety validation."""


_DIALECTS: tuple[SqlDialect, ...] = ("postgresql", "mysql", "generic")
_WHITE_SPACE = " \t\n\r"

# What a read may start with.
_READS = frozenset({"SELECT", "WITH", "TABLE", "VALUES"})
_READ_STARTS = _READS | {"EXPLAIN", "SHOW", "DESCRIBE", "DESC"}
_EXPLAINS = frozenset({"EXPLAIN", "DESCRIBE", "DESC"})
_EXPLAIN_RUNS = frozenset({"ANALYZE", "ANALYSE"})
_EXPLAIN_OPTIONS = frozenset({"VERBOSE", "EXTENDED", "PARTITIONS"})

# Statements that change rows. In PostgreSQL they can also stand inside a
# WITH clause; they are looked for where a statement can start.
_ROW_CHANGES = frozenset({"INSERT", "UPDATE", "DELETE", "MERGE", "REPLACE"})

# What a statement that is not a read starts with.
_STATEMENTS = _ROW_CHANGES | {
    "ALTER", "ANALYZE", "BEGIN", "CALL", "CLUSTER", "COMMENT", "COMMIT", "COPY", "CREATE",
    "DEALLOCATE", "DECLARE", "DISCARD", "DO", "DROP", "EXEC", "EXECUTE", "FLUSH", "GRANT",
    "HANDLER", "IMPORT", "INSTALL", "KILL", "LISTEN", "LOAD", "LOCK", "NOTIFY", "OPTIMIZE",
    "PREPARE", "PURGE", "REFRESH", "REINDEX", "RELEASE", "RENAME", "REPAIR", "RESET",
    "REVOKE", "ROLLBACK", "SAVEPOINT", "SECURITY", "SET", "SHUTDOWN", "START", "STOP",
    "TRUNCATE", "UNINSTALL", "UNLISTEN", "UNLOCK", "UPSERT", "USE", "VACUUM",
}  # fmt: skip

# Functions never allowed, whatever the connector's mode: they stall the
# connection, read server files or run commands.
_NEVER_FUNCTIONS = {
    "sleep": "SLEEP() is not allowed",
    "benchmark": "BENCHMARK() is not allowed",
    "load_file": "LOAD_FILE() is not allowed",
    "pg_sleep": "pg_sleep() is not allowed",
    "pg_sleep_for": "pg_sleep_for() is not allowed",
    "pg_sleep_until": "pg_sleep_until() is not allowed",
    "exec": "EXEC() is not allowed",
    "execute": "EXECUTE() is not allowed",
}

# Functions a read-only connector may not call: PostgreSQL runs them in a
# read-only transaction, and they reach outside it (another connection,
# the server's files, its processes, its configuration, its WAL).
# ``set_config`` can lift the statement timeout the query runs under.
_READ_ONLY_FUNCTIONS = frozenset(
    {
        "lo_import", "lo_export", "pg_terminate_backend", "pg_cancel_backend",
        "pg_reload_conf", "pg_switch_wal", "pg_rotate_logfile",
        "pg_create_restore_point", "pg_logical_emit_message", "set_config",
        "pg_stat_file", "pg_current_logfile", "ts_stat",
        "pg_drop_replication_slot", "pg_replication_slot_advance",
        # Session-level locks: they outlive the query.
        "get_lock", "release_lock", "release_all_locks", "is_free_lock", "is_used_lock",
    }
)  # fmt: skip
_READ_ONLY_FUNCTION_PREFIXES = (
    "dblink", "pg_read_", "pg_ls_", "pg_stat_reset",
    # They run the query text they are given, in a way of their own.
    "query_to_xml", "table_to_xml", "cursor_to_xml", "database_to_xml", "schema_to_xml",
    "pg_create_physical_replication_slot", "pg_create_logical_replication_slot",
    "pg_copy_physical_replication_slot", "pg_copy_logical_replication_slot",
    "pg_logical_slot_", "pg_replication_origin_",
    "pg_advisory_", "pg_try_advisory_",
)  # fmt: skip

def validate_sql(sql: str, *, allow_write: bool = False, dialect: str | None = None) -> str:
    """Validate and clean a SQL statement for safe execution.

    Parameters
    ----------
    sql:
        The raw SQL string to validate.
    allow_write:
        If ``False`` (default), only reads are allowed.
    dialect:
        Whose rules for comments and quoting the SQL is read by:
        ``"postgresql"``, ``"mysql"`` or ``"generic"`` (a driver's
        ``sql_dialect``). ``None`` when the database is not known: the SQL
        is then read by every one of them, and refused if any reading is.

    Returns
    -------
    str
        The cleaned SQL string (trailing semicolons stripped).

    Raises
    ------
    SqlSafetyError
        If the SQL fails any safety check.
    """
    if not sql or not sql.strip(_WHITE_SPACE):
        raise SqlSafetyError("Empty SQL statement")

    # ASCII white space only: str.strip() would also drop U+3000 and its
    # like, which no database takes for white space.
    cleaned = sql.strip(_WHITE_SPACE).rstrip(";").strip(_WHITE_SPACE)
    if not cleaned:
        raise SqlSafetyError("Empty SQL statement after cleaning")

    known = tuple(name for name in _DIALECTS if name == dialect)
    for name in known or _DIALECTS:
        _check(read_sql(cleaned, dialect=name), name, allow_write)
        # DM8 has no read-only transaction behind this check, so a second
        # reading may add refusals there: its strings with MySQL's
        # backslash rule, in case the server is set to it.
        if name == "generic" and not allow_write and "\\" in cleaned:
            _check(read_sql(cleaned, dialect=name, backslash_escapes=True), name, allow_write)
    return cleaned


def _check(lexemes: list[Lexeme], dialect: SqlDialect, allow_write: bool) -> None:
    odd = [lex.text for lex in lexemes if lex.kind == "odd"]
    if odd and not allow_write:
        # A control character or a non-ASCII "space" outside quotes. What
        # it does to the statement depends on the server's lexer down to
        # the byte (MySQL starts a comment after "--" plus U+007F, not
        # after "--" plus U+3000), and nobody writes one on purpose.
        raise SqlSafetyError(
            f"The SQL has an invisible or control character (U+{ord(odd[0]):04X}) outside "
            "quotes, which is not allowed on a read-only connection. Retype the "
            "statement with ordinary spaces and line breaks."
        )
    lexemes = _one_statement([lex for lex in lexemes if lex.kind != "odd"])
    _refuse_never_allowed(lexemes)
    if allow_write:
        return

    if any(lex.kind == "executable" for lex in lexemes):
        raise SqlSafetyError(
            "A /*! ... */ comment is run as SQL by MySQL and MariaDB, so it is not "
            "allowed on a read-only connection. Remove the comment."
        )

    first = _first_word(lexemes)
    if first not in _READ_STARTS:
        raise SqlSafetyError(
            f"Only SELECT queries are allowed (got {first}). "
            "Write operations require explicit permission."
        )
    if first == "SHOW":
        return
    if first in _EXPLAINS:
        explained = _explained_if_run(lexemes)
        if explained is None:
            # Planned, not run.
            return
        keyword = _first_word(explained)
        if keyword not in _READS:
            raise SqlSafetyError(
                "EXPLAIN ANALYZE runs the statement it explains, so on a read-only "
                f"connection it is allowed for SELECT queries only (got {keyword or 'nothing'})."
            )
        lexemes = explained
    _refuse_writes_inside_a_read(lexemes, dialect)


def _one_statement(lexemes: list[Lexeme]) -> list[Lexeme]:
    """*lexemes* without trailing semicolons; a second statement is refused."""
    end = len(lexemes)
    while end and lexemes[end - 1].is_punct(";"):
        end -= 1
    if any(lex.is_punct(";") for lex in lexemes[:end]):
        raise SqlSafetyError("Multi-statement queries are not allowed")
    if not end:
        raise SqlSafetyError("Empty SQL statement after cleaning")
    return lexemes[:end]


def _first_word(lexemes: list[Lexeme]) -> str:
    """The statement's first keyword; ``(SELECT 1) UNION ...`` starts with SELECT."""
    for lex in lexemes:
        if lex.is_punct("("):
            continue
        return lex.upper
    return ""


def _function_calls_at(lexemes: list[Lexeme]) -> list[tuple[int, str]]:
    """The names called as functions, lower-cased, each with its position.

    A name right before ``(``. ``pg_catalog.pg_read_file(...)`` and
    ``"pg_read_file"(...)`` are calls of ``pg_read_file``; a column or a
    string with that text is not.
    """
    return [
        (index, ascii_lower(lex.text))
        for index, (lex, following) in enumerate(pairwise(lexemes))
        if lex.kind in ("word", "name") and following.is_punct("(")
    ]


def _function_calls(lexemes: list[Lexeme]) -> list[str]:
    return [name for _, name in _function_calls_at(lexemes)]


def _third_argument_is_true(lexemes: list[Lexeme], name_at: int) -> bool:
    """Whether the call at *name_at* is ``f(a, b, true)``: the keyword, written out."""
    opening = name_at + 1
    close = _closing(lexemes, opening)
    inner = lexemes[opening].depth + 1
    arguments: list[list[Lexeme]] = [[]]
    for lex in lexemes[opening + 1 : close]:
        if lex.is_punct(",") and lex.depth == inner:
            arguments.append([])
        else:
            arguments[-1].append(lex)
    return (
        close < len(lexemes)
        and len(arguments) == 3
        and len(arguments[2]) == 1
        and arguments[2][0].upper == "TRUE"
    )


def _has_words(lexemes: list[Lexeme], *words: str) -> bool:
    """Whether *words* stand next to each other somewhere in the statement."""
    size = len(words)
    return any(
        tuple(lex.upper for lex in lexemes[i : i + size]) == words
        for i in range(len(lexemes) - size + 1)
    )


def _refuse_never_allowed(lexemes: list[Lexeme]) -> None:
    for name in _function_calls(lexemes):
        if name in _NEVER_FUNCTIONS:
            raise SqlSafetyError(_NEVER_FUNCTIONS[name])
    for words, message in (
        (("INTO", "OUTFILE"), "INTO OUTFILE is not allowed"),
        (("INTO", "DUMPFILE"), "INTO DUMPFILE is not allowed"),
        (("LOAD", "DATA"), "LOAD DATA is not allowed"),
        (("LOAD", "XML"), "LOAD XML is not allowed"),
        (("XP_CMDSHELL",), "xp_cmdshell is not allowed"),
    ):
        if _has_words(lexemes, *words):
            raise SqlSafetyError(message)


def _explained_if_run(lexemes: list[Lexeme]) -> list[Lexeme] | None:
    """The statement an EXPLAIN / DESCRIBE runs, or ``None`` when it only plans it.

    ``EXPLAIN ANALYZE ...``, ``EXPLAIN (ANALYZE, ...) ...`` (PostgreSQL) and
    ``DESCRIBE ANALYZE ...`` (MySQL) run the statement.
    """
    i = 1
    runs = False
    if i < len(lexemes) and lexemes[i].is_punct("("):
        # PostgreSQL's option list, unless it is a parenthesised statement.
        close = _closing(lexemes, i)
        inside = lexemes[i + 1 : close]
        if not (inside and inside[0].upper in _READS | _ROW_CHANGES):
            for position, lex in enumerate(inside):
                if lex.upper in _EXPLAIN_RUNS:
                    setting = inside[position + 1 : position + 2]
                    off = bool(setting) and setting[0].text.upper() in ("FALSE", "OFF", "0")
                    runs = runs or not off
            i = close + 1
    while i < len(lexemes):
        word = lexemes[i].upper
        if word in _EXPLAIN_RUNS:
            runs = True
            i += 1
        elif word in _EXPLAIN_OPTIONS:
            i += 1
        elif word == "FORMAT":
            i += 3 if i + 1 < len(lexemes) and lexemes[i + 1].is_punct("=") else 2
        else:
            break
    return lexemes[i:] if runs else None


def _closing(lexemes: list[Lexeme], opening: int) -> int:
    """Index of the ``)`` that closes the ``(`` at *opening* (the end if none does)."""
    depth = lexemes[opening].depth
    for i in range(opening + 1, len(lexemes)):
        if lexemes[i].is_punct(")") and lexemes[i].depth == depth:
            return i
    return len(lexemes)


def _refuse_writes_inside_a_read(lexemes: list[Lexeme], dialect: SqlDialect) -> None:
    """Refuse a read that changes, locks or exports something on the way."""
    if _first_word(lexemes) == "WITH":
        main = _statement_after_with(lexemes)
        if main is None:
            # Not a WITH clause this module can follow: any statement
            # keyword right after a closing parenthesis is taken for the
            # statement the clause leads to.
            unread = any(
                lex.is_punct(")")
                and following.upper in _STATEMENTS
                and after.kind in ("word", "name")
                for lex, following, after in zip(lexemes, lexemes[1:], lexemes[2:], strict=False)
            )
        else:
            unread = main not in _READS
        if unread:
            raise SqlSafetyError("WITH (CTE) queries must end with SELECT, not a write operation")

    # A statement inside parentheses: the body of a CTE in PostgreSQL.
    for i, lex in enumerate(lexemes):
        if lex.is_punct("(") and (word := _row_change_at(lexemes, i + 1)):
            raise SqlSafetyError(
                f"Only SELECT queries are allowed: {word} inside the query changes "
                "rows. Write operations require explicit permission."
            )

    if any(lex.upper == "INTO" for lex in lexemes):
        raise SqlSafetyError(
            "Only SELECT queries are allowed: SELECT ... INTO writes the result to "
            "a table, a file or a variable. Write operations require explicit permission."
        )

    for words in (
        ("FOR", "UPDATE"),
        ("FOR", "SHARE"),
        ("FOR", "NO", "KEY", "UPDATE"),
        ("FOR", "KEY", "SHARE"),
        ("LOCK", "IN", "SHARE", "MODE"),
    ):
        if _has_words(lexemes, *words):
            raise SqlSafetyError(
                f"Only SELECT queries are allowed: {' '.join(words)} locks rows, which "
                "a read-only connection does not do."
            )

    if any(lex.kind == "unicode_escaped" for lex in lexemes):
        raise SqlSafetyError(
            "U&\"...\" and U&'...' spell their text with \\XXXX escapes, so a name "
            "written that way cannot be checked. They are not allowed on a read-only "
            "connection; write the name or the text out."
        )

    for index, name in _function_calls_at(lexemes):
        if name == "set_config" and _third_argument_is_true(lexemes, index):
            # For this transaction only, which is rolled back.
            continue
        if name in _READ_ONLY_FUNCTIONS or name.startswith(_READ_ONLY_FUNCTION_PREFIXES):
            raise SqlSafetyError(
                f"{name}() is not allowed on a read-only connection: it acts outside "
                "the query (files, other connections, locks, server settings or processes)."
            )

    if dialect == "mysql" and any(
        lex.is_punct(":") and following.is_punct("=") for lex, following in pairwise(lexemes)
    ):
        raise SqlSafetyError(
            "Only SELECT queries are allowed: ':=' sets a session variable, which a "
            "read-only connection does not do."
        )

    if dialect == "generic":
        # No read-only transaction stands behind this driver: a row-changing
        # keyword anywhere is refused, also where it could be a name.
        for lex, following in zip(lexemes, [*lexemes[1:], None], strict=False):
            if lex.upper in _ROW_CHANGES - {"REPLACE"} and not (
                following and following.is_punct("(")
            ):
                raise SqlSafetyError(
                    f"Only SELECT queries are allowed: {lex.upper} inside the query "
                    "changes rows. Write operations require explicit permission."
                )


def _row_change_at(lexemes: list[Lexeme], i: int) -> str | None:
    """The row-changing statement that starts at *i*, if one does.

    ``DELETE FROM``, ``INSERT INTO``, ``MERGE INTO``, ``REPLACE INTO`` and
    ``UPDATE ... SET``. A column or alias with one of these names
    (``(update)``, ``(delete IS NULL)``) is not followed that way.
    """
    if i + 1 >= len(lexemes):
        return None
    word = lexemes[i].upper
    following = lexemes[i + 1].upper
    if (word, following) in {
        ("DELETE", "FROM"),
        ("INSERT", "INTO"),
        ("MERGE", "INTO"),
        ("REPLACE", "INTO"),
    }:
        return word
    if word == "UPDATE" and lexemes[i + 1].kind in ("word", "name"):
        depth = lexemes[i].depth
        for lex in lexemes[i + 1 : i + 12]:
            if lex.depth < depth:
                break
            if lex.upper == "SET" and lex.depth == depth:
                return word
    return None


def _statement_after_with(lexemes: list[Lexeme]) -> str | None:
    """The first keyword of the statement that follows a WITH clause.

    ``WITH [RECURSIVE] name [(columns)] AS [[NOT] MATERIALIZED] (body)
    [SEARCH ...] [CYCLE ...] [, ...]`` and then the statement. ``None`` when
    the clause is not written that way (a dialect's own extension): the
    statement is then judged by the other rules alone.
    """
    i = 0
    while i < len(lexemes) and lexemes[i].is_punct("("):
        i += 1
    base = lexemes[i].depth if i < len(lexemes) else 0
    i += 1
    if i < len(lexemes) and lexemes[i].upper == "RECURSIVE":
        i += 1
    while i < len(lexemes):
        if lexemes[i].kind not in ("word", "name"):
            return None
        i += 1
        if i < len(lexemes) and lexemes[i].is_punct("("):
            i = _closing(lexemes, i) + 1
        if i >= len(lexemes) or lexemes[i].upper != "AS":
            return None
        i += 1
        if i < len(lexemes) and lexemes[i].upper == "NOT":
            i += 1
        if i < len(lexemes) and lexemes[i].upper == "MATERIALIZED":
            i += 1
        if i >= len(lexemes) or not lexemes[i].is_punct("("):
            return None
        i = _closing(lexemes, i) + 1
        # PostgreSQL: [SEARCH ...] [CYCLE ...] after a recursive query's body.
        for clause in (_after_search_clause, _after_cycle_clause):
            after = clause(lexemes, i)
            if after is None:
                return None
            i = after
        if i < len(lexemes) and lexemes[i].is_punct(",") and lexemes[i].depth == base:
            i += 1
            continue
        break
    while i < len(lexemes) and lexemes[i].is_punct("("):
        i += 1
    if i >= len(lexemes):
        return ""
    return lexemes[i].upper if lexemes[i].kind == "word" else None


def _names_from(lexemes: list[Lexeme], i: int) -> int | None:
    """Index after ``name [, name ...]`` at *i*; ``None`` if no name stands there."""
    while True:
        if i >= len(lexemes) or lexemes[i].kind not in ("word", "name"):
            return None
        i += 1
        if i < len(lexemes) and lexemes[i].is_punct(","):
            i += 1
            continue
        return i


def _after_search_clause(lexemes: list[Lexeme], i: int) -> int | None:
    """Index after ``SEARCH {DEPTH | BREADTH} FIRST BY col [, ...] SET col`` at *i*.

    *i* itself when no SEARCH clause stands there; ``None`` when one starts
    and is not written this way.
    """
    words = [lex.upper for lex in lexemes[i : i + 4]]
    if not words or words[0] != "SEARCH":
        return i
    if len(words) < 4 or words[1] not in ("DEPTH", "BREADTH") or words[2:] != ["FIRST", "BY"]:
        return None
    # "a, b SET ord": SET ends the list of columns.
    at = i + 4
    while at < len(lexemes) and lexemes[at].upper != "SET":
        if lexemes[at].kind not in ("word", "name") and not lexemes[at].is_punct(","):
            return None
        at += 1
    if at >= len(lexemes) - 1 or lexemes[at + 1].kind not in ("word", "name"):
        return None
    return at + 2


def _after_cycle_clause(lexemes: list[Lexeme], i: int) -> int | None:
    """Index after ``CYCLE col [, ...] SET col [TO value DEFAULT value] USING col`` at *i*."""
    if i >= len(lexemes) or lexemes[i].upper != "CYCLE":
        return i
    at = i + 1
    while at < len(lexemes) and lexemes[at].upper != "SET":
        if lexemes[at].kind not in ("word", "name") and not lexemes[at].is_punct(","):
            return None
        at += 1
    at += 2  # SET col
    if at < len(lexemes) and lexemes[at].upper == "TO":
        # TO value DEFAULT value: each a constant (a word, a number or a string).
        if at + 3 >= len(lexemes) or lexemes[at + 2].upper != "DEFAULT":
            return None
        at += 4
    if at + 1 >= len(lexemes) or lexemes[at].upper != "USING":
        return None
    if lexemes[at + 1].kind not in ("word", "name"):
        return None
    return at + 2
