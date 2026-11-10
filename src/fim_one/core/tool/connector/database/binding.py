"""``{{variable}}`` references in SQL become bound parameters.

A workflow's database query step is written as SQL with ``{{variable}}``
references. The values come from whoever starts the run or from an earlier
step, so they are never pasted into the SQL text: each reference is replaced
by the driver's placeholder (``$1``, ``?``) and the value travels to the
database separately, where it can only ever be a value. MySQL's driver has
no such channel: there each value is written as a literal made of hex
digits (:func:`mysql_literal`).

A reference can therefore stand only where SQL takes a value. One inside a
quoted string, a quoted identifier or a comment, or where a table name, a
column name or a keyword is expected, is refused before anything runs.

The scanner here decides what is a string and what is code. Where databases
disagree about that (a backslash before a quote, a block comment inside a
block comment) the template is refused rather than guessed at.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal

from .reading import SqlDialect, is_name_char, is_odd

__all__ = [
    "PlaceholderStyle",
    "SqlBindingError",
    "SqlDialect",
    "SqlTemplate",
    "bound_value",
    "has_variables",
    "mysql_literal",
    "parse_sql_template",
    "render_sql",
    "sql_tokens",
]

# ``numeric``: ``$1`` (asyncpg). ``qmark``: ``?`` (dmPython). ``literal``:
# no placeholder at all; each value is written into the statement as a
# literal that cannot be read as anything else (MySQL, whose driver has no
# server-side parameters).
PlaceholderStyle = Literal["numeric", "qmark", "literal"]
# What the scanner needs to know about the SQL it reads: MySQL has ``#``
# comments, PostgreSQL has dollar-quoted strings.

# The same reference syntax the workflow variable store fills in.
_VARIABLE = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_.]*)\s*\}\}")
_DOLLAR_TAG = re.compile(r"\$([A-Za-z_][A-Za-z0-9_]*)?\$")

_HOW_TO = (
    "A variable can only stand for a value. Write it without quotes, for example "
    "WHERE id = {{variable}}: the value is passed to the database separately and "
    "needs no quoting."
)

# Words a value may follow. Anything else before a reference (FROM, JOIN,
# ORDER BY, AS, a name) means it stands for a name or a keyword.
_VALUE_KEYWORDS = frozenset(
    {
        "ALL", "AND", "ANY", "BETWEEN", "CASE", "DISTINCT", "ELSE", "ESCAPE",
        "FIRST", "HAVING", "ILIKE", "IN", "LIKE", "LIMIT", "NEXT", "NOT",
        "OFFSET", "ON", "OR", "REGEXP", "RETURNING", "RLIKE", "SELECT", "SOME",
        "THEN", "TO", "TOP", "VALUES", "WHEN", "WHERE",
    }
)  # fmt: skip
# Operators and punctuation a value may follow.
_VALUE_PUNCTUATION = frozenset("=<>!(,+-*/%|&^~[")


class SqlBindingError(ValueError):
    """The SQL uses a ``{{variable}}`` where it cannot be bound as a value."""


@dataclass(frozen=True)
class SqlTemplate:
    """SQL split around its references: ``parts`` has one more item than ``variables``."""

    parts: tuple[str, ...]
    variables: tuple[str, ...]
    # Where each reference stands: ``"count"`` right after LIMIT or OFFSET
    # (the one place where SQL wants a bare whole number), ``"list"`` when
    # it is all there is between the parentheses of ``IN (...)`` (a list
    # value is bound there as one value per item), else ``"value"``.
    positions: tuple[str, ...] = ()


@dataclass(frozen=True)
class _Token:
    # ``code``, ``quoted`` (string, quoted identifier, dollar-quoted string),
    # ``comment`` or ``variable`` (text is the variable's name).
    kind: Literal["code", "quoted", "comment", "variable"]
    text: str


def has_variables(sql: str) -> bool:
    """Whether *sql* holds a ``{{variable}}`` reference anywhere."""
    return _VARIABLE.search(sql) is not None


def bound_value(value: Any) -> Any:
    """*value* as it is bound: scalars keep their type, lists and objects become JSON text."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    try:
        return json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(value)


def sql_tokens(sql: str, *, dialect: SqlDialect, strict: bool = False) -> list[_Token]:
    """*sql* as code, quoted text and comments (and, in code, variable references).

    This is the scanner for templates: it is deliberately cautious, not
    exact. With *strict*, text whose reading differs between databases
    raises :class:`SqlBindingError`; without it the scanner takes the
    reading that keeps more text inside the string or comment. The safety
    check reads SQL with :mod:`.reading`, which follows each database.
    """
    tokens: list[_Token] = []
    code_start = 0
    i = 0
    n = len(sql)

    def flush(upto: int) -> None:
        if upto > code_start:
            tokens.append(_Token("code", sql[code_start:upto]))

    while i < n:
        ch = sql[i]
        two = sql[i : i + 2]
        if ch in "'\"`":
            flush(i)
            end = _end_of_quoted(sql, i, strict=strict)
            tokens.append(_Token("quoted", sql[i:end]))
            i = code_start = end
        elif two == "--" or (ch == "#" and dialect == "mysql"):
            if (
                strict
                and dialect == "mysql"
                and two == "--"
                and sql[i + 2 : i + 3] not in (" ", "\t", "\n", "\r")
            ):
                # MySQL starts a comment at ``--`` only before whitespace;
                # elsewhere it is two minus signs.
                if i + 2 < n:
                    raise SqlBindingError(
                        "The SQL has '--' with no space after it, which MySQL does not "
                        "read as a comment, so variables cannot be bound safely in it. "
                        "Put a space after '--', or between the two minus signs."
                    )
            flush(i)
            newline = sql.find("\n", i)
            end = n if newline == -1 else newline
            tokens.append(_Token("comment", sql[i:end]))
            i = code_start = end
        elif two == "/*":
            if strict and dialect == "mysql" and sql[i + 2 : i + 3] == "!":
                raise SqlBindingError(
                    "The SQL has a /*! ... */ comment, which MySQL runs as SQL, so "
                    "variables cannot be bound safely in it. Remove the comment."
                )
            flush(i)
            end = _end_of_block_comment(sql, i, strict=strict)
            tokens.append(_Token("comment", sql[i:end]))
            i = code_start = end
        elif ch == "$" and dialect == "postgresql" and (tag := _DOLLAR_TAG.match(sql, i)):
            # ``$tag$ ... $tag$``. A ``$`` inside a name (``a$b$c``) is not a tag.
            if i > 0 and is_name_char(sql[i - 1]):
                i += 1
                continue
            flush(i)
            close = sql.find(tag.group(0), tag.end())
            end = n if close == -1 else close + len(tag.group(0))
            tokens.append(_Token("quoted", sql[i:end]))
            i = code_start = end
        elif two == "{{" and (ref := _VARIABLE.match(sql, i)):
            flush(i)
            tokens.append(_Token("variable", ref.group(1)))
            i = code_start = ref.end()
        else:
            i += 1
    flush(n)
    return tokens


def _end_of_quoted(sql: str, start: int, *, strict: bool) -> int:
    """Index just past the quoted text that opens at *start*."""
    quote = sql[start]
    i = start + 1
    n = len(sql)
    while i < n:
        ch = sql[i]
        if ch == "\\":
            run = 1
            while i + run < n and sql[i + run] == "\\":
                run += 1
            after = sql[i + run] if i + run < n else ""
            if run % 2 == 1 and after == quote:
                # MySQL reads an escaped quote here, standard SQL the end of
                # the string.
                if strict:
                    raise SqlBindingError(
                        "The SQL has a backslash right before a quote inside a quoted "
                        "string, which databases read differently, so variables "
                        "cannot be bound safely in it. Write the quote doubled ('') "
                        "instead of escaping it with a backslash."
                    )
                i += run + 1
                continue
            i += run
            continue
        if ch == quote:
            if i + 1 < n and sql[i + 1] == quote:
                i += 2
                continue
            return i + 1
        i += 1
    return n


def _end_of_block_comment(sql: str, start: int, *, strict: bool) -> int:
    """Index just past the ``/* ... */`` comment that opens at *start*."""
    close = sql.find("*/", start + 2)
    end = len(sql) if close == -1 else close + 2
    if strict and "/*" in sql[start + 2 : end]:
        # PostgreSQL nests block comments, MySQL does not.
        raise SqlBindingError(
            "The SQL has a block comment inside a block comment, which databases "
            "read differently, so variables cannot be bound safely in it. Remove "
            "the nested comment."
        )
    return end


def parse_sql_template(sql: str, *, dialect: SqlDialect) -> SqlTemplate:
    """Split *sql* around its ``{{variable}}`` references.

    Raises :class:`SqlBindingError` when a reference is not where SQL takes
    a value.
    """
    tokens = sql_tokens(sql, dialect=dialect, strict=True)

    for token in tokens:
        if token.kind != "quoted" and (odd := next(filter(is_odd, token.text), None)):
            # Lexers disagree about these down to the byte; see reading.is_odd.
            raise SqlBindingError(
                f"The SQL has an invisible or control character (U+{ord(odd):04X}) outside "
                "quotes, so variables cannot be bound safely in it. Retype the statement "
                "with ordinary spaces and line breaks."
            )
        if token.kind in ("quoted", "comment") and (ref := _VARIABLE.search(token.text)):
            where = (
                "inside a comment"
                if token.kind == "comment"
                else "inside quotes (a quoted string or a quoted name)"
            )
            raise SqlBindingError(f"{ref.group(0)} is {where}. {_HOW_TO}")
        if token.kind == "code" and "{{" in token.text:
            raise SqlBindingError(
                "The SQL has '{{' that is not a variable reference. A reference "
                "looks like {{step_1.output}}."
            )

    # Comments are not part of the statement for what follows.
    statement: list[_Token] = []
    for token in tokens:
        if token.kind == "comment":
            token = _Token("code", " ")
        if token.kind == "code" and statement and statement[-1].kind == "code":
            statement[-1] = _Token("code", statement[-1].text + token.text)
        else:
            statement.append(token)
    for index, token in enumerate(statement):
        if token.kind != "variable":
            continue
        before = statement[index - 1] if index > 0 else None
        after = statement[index + 1] if index + 1 < len(statement) else None
        if not (_value_may_follow(before) and _value_may_precede(after)):
            raise SqlBindingError(
                f"{{{{{token.text}}}}} is used where SQL does not take a value: table "
                f"and column names, keywords and whole statements cannot come from "
                f"a variable. {_HOW_TO}"
            )

    parts: list[str] = [""]
    variables: list[str] = []
    positions: list[str] = []
    for token in tokens:
        if token.kind == "variable":
            variables.append(token.text)
            positions.append(_position(parts[-1], positions, len(parts)))
            parts.append("")
        elif token.kind == "comment":
            # A comment says nothing to the database; a space keeps the
            # words around it apart.
            parts[-1] += " "
        else:
            parts[-1] += token.text
    for index, position in enumerate(positions):
        if (
            position == "value"
            and _OPENS_IN_LIST.search(parts[index])
            and parts[index + 1].lstrip().startswith(")")
        ):
            positions[index] = "list"
    return SqlTemplate(
        parts=tuple(parts), variables=tuple(variables), positions=tuple(positions)
    )


_OPENS_IN_LIST = re.compile(r"(?<![\w$.])IN\s*\(\s*$", re.IGNORECASE)
_OPENS_IN_LIST_WITH_NOT = re.compile(
    r"(?:(?<![\w$.])(?P<not>NOT)\s+)?(?<![\w$.])IN\s*\(\s*$", re.IGNORECASE
)
# The wire protocol counts a statement's parameters in 16 bits.
_POSTGRESQL_MAX_PARAMETERS = 32767


_AFTER_COUNT_KEYWORD = re.compile(r"(?<![\w$.])(?:LIMIT|OFFSET)\s*$", re.IGNORECASE)


def _position(before: str, earlier: list[str], part_count: int) -> str:
    """Where the reference that follows *before* stands (see ``SqlTemplate.positions``)."""
    if _AFTER_COUNT_KEYWORD.search(before):
        return "count"
    # MySQL's ``LIMIT {{offset}}, {{count}}``.
    if part_count > 1 and earlier and earlier[-1] == "count" and before.strip() == ",":
        return "count"
    return "value"


def _value_may_follow(before: _Token | None) -> bool:
    """Whether a value can come right after *before*."""
    if before is None or before.kind != "code":
        # The start of the statement, a string, or another reference.
        return False
    text = before.text.rstrip()
    if not text:
        return False
    last = text[-1]
    if last.isalnum() or last in "_$":
        if len(text) == len(before.text):
            # Glued to a word: part of a name.
            return False
        word = re.search(r"[A-Za-z_][A-Za-z0-9_$]*$", text)
        if word is None or word.group(0).upper() not in _VALUE_KEYWORDS:
            return False
        # ``t.select`` is a column, not the keyword.
        return not text[: word.start()].endswith(".")
    if text.endswith("::"):
        # A cast names a type.
        return False
    return last in _VALUE_PUNCTUATION


def _value_may_precede(after: _Token | None) -> bool:
    """Whether a value can come right before *after*."""
    if after is None:
        return True
    if after.kind != "code":
        return False
    first = after.text[0]
    if first.isalnum() or first in "_$":
        return False
    text = after.text.lstrip()
    # ``{{f}}(...)`` names a function, ``{{t}}.col`` a table.
    return not text.startswith(("(", "."))


def render_sql(
    template: SqlTemplate, style: PlaceholderStyle, values: Sequence[Any]
) -> tuple[str, list[Any] | None]:
    """The SQL of *template* for a driver, and the parameters to send with it.

    ``numeric`` and ``qmark``: each reference becomes the driver's
    placeholder and *values* are returned as the parameters. ``literal``:
    each value is written into the SQL as a MySQL literal and there are no
    parameters (``None``).

    Raises :class:`SqlBindingError` when the SQL already holds text the
    driver would read as a placeholder, or a value cannot be bound.
    """
    if len(values) != len(template.variables):
        raise SqlBindingError("The SQL's references and their values do not match")
    out: list[str] = []
    parameters: list[Any] = []
    for index, part in enumerate(template.parts):
        if style == "numeric":
            if _numbered_placeholder_in_code(part):
                raise SqlBindingError(
                    "The SQL already has a numbered parameter ($1, $2, ...). "
                    "Use {{variable}} references only."
                )
        elif style == "qmark" and _question_mark_in_code(part):
            raise SqlBindingError(
                "The SQL has a '?' outside quotes, which this database reads as "
                "a parameter. Use {{variable}} references only."
            )
        out.append(part)
        if index == len(template.variables):
            break
        name = template.variables[index]
        position = template.positions[index] if template.positions else "value"
        raw = values[index]
        if position == "list" and isinstance(raw, (list, tuple)):
            # ``IN ({{ids}})``: one bound value per item.
            items = [bound_value(item) for item in raw]
            if not items:
                # Nothing is in an empty list and everything is not in it.
                # ``IN (NULL)`` would say "unknown" to both.
                if style == "numeric":
                    # PostgreSQL: an empty array, which takes the column's
                    # type (a subquery with no rows would need one).
                    opening = _OPENS_IN_LIST_WITH_NOT.search(out[-1])
                    assert opening is not None
                    out[-1] = out[-1][: opening.start()] + (
                        "<> ALL(" if opening.group("not") else "= ANY("
                    )
                    parameters.append([])
                    out.append(f"${len(parameters)}")
                else:
                    out.append("SELECT NULL FROM DUAL WHERE 1 = 0")
                continue
        else:
            items = [bound_value(raw)]
        written: list[str] = []
        for item in items:
            if style == "literal":
                written.append(
                    _mysql_count(item, name) if position == "count" else mysql_literal(item, name=name)
                )
            else:
                parameters.append(item)
                written.append(f"${len(parameters)}" if style == "numeric" else "?")
        out.append(", ".join(written))
    if style == "numeric" and len(parameters) > _POSTGRESQL_MAX_PARAMETERS:
        raise SqlBindingError(
            f"The query would bind {len(parameters)} values, and PostgreSQL takes at most "
            f"{_POSTGRESQL_MAX_PARAMETERS} in one statement. For a long list write "
            "= ANY({{list}}) instead of IN ({{list}}): the whole list is then one value."
        )
    return "".join(out), (None if style == "literal" else parameters)


def mysql_literal(value: Any, *, name: str = "value") -> str:
    """*value* as a MySQL literal that reads the same on any session.

    Text is written as ``_utf8mb4 X'<hex of its UTF-8 bytes>'``: only hex
    digits reach the statement, so no quote or backslash in the value, no
    ``sql_mode`` and no client character set can make it anything but that
    text. (Escaping a quote is only correct for the character set the
    session is in: with ``SET NAMES gbk`` a two-byte character that ends
    in 0x5C swallows the escaping backslash.)

    Numbers and booleans are written as text too (``5`` as the text
    ``'5'``, ``true`` as ``'1'``). MySQL compares a number with a text
    column by turning the column's text into a number, and text that is
    not a number becomes 0: bound as the number 0, a value would match
    every row whose text column does not start with a digit. Bound as
    text, it is compared as text with a text column, and MySQL still turns
    it into a number where the other side is one (``id = '5'``).
    """
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        value = "1" if value else "0"
    try:
        encoded = str(value).encode("utf-8")
    except UnicodeEncodeError:
        raise SqlBindingError(f"{{{{{name}}}}} holds text that is not valid Unicode") from None
    return f"_utf8mb4 X'{encoded.hex().upper()}'"


_COUNT_TEXT = re.compile(r"[0-9]+", re.ASCII)


def _mysql_count(value: Any, name: str) -> str:
    """*value* as the bare whole number MySQL's LIMIT and OFFSET take.

    They accept no expression and no text, so this is the one place a value
    is written as a number: digits only, checked here.
    """
    count: int | None = None
    if isinstance(value, bool):
        count = None
    elif isinstance(value, int):
        count = value
    elif isinstance(value, float) and value.is_integer():
        count = int(value)
    elif isinstance(value, str) and _COUNT_TEXT.fullmatch(value.strip()):
        count = int(value.strip())
    if count is None or count < 0 or count > 2**63 - 1:
        raise SqlBindingError(
            f"{{{{{name}}}}} stands after LIMIT or OFFSET and must be a whole number "
            f"that is not negative (got {value!r:.40})"
        )
    return str(count)


def _code_of(part: str) -> str:
    return " ".join(t.text for t in sql_tokens(part, dialect="postgresql") if t.kind == "code")


def _numbered_placeholder_in_code(part: str) -> bool:
    return re.search(r"(?<![A-Za-z0-9_$])\$\d", _code_of(part)) is not None


def _question_mark_in_code(part: str) -> bool:
    return "?" in " ".join(t.text for t in sql_tokens(part, dialect="generic") if t.kind == "code")
