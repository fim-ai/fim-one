"""``{{variable}}`` references in SQL become bound parameters.

A workflow's database query step is written as SQL with ``{{variable}}``
references. The values come from whoever starts the run or from an earlier
step, so they are never pasted into the SQL text: each reference is replaced
by the driver's placeholder (``$1``, ``%s``, ``?``) and the value travels to
the database separately, where it can only ever be a value.

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
from dataclasses import dataclass
from typing import Any, Literal

__all__ = [
    "PlaceholderStyle",
    "SqlBindingError",
    "SqlDialect",
    "SqlTemplate",
    "bound_value",
    "has_variables",
    "parse_sql_template",
    "render_sql",
    "sql_tokens",
]

# ``numeric``: ``$1`` (asyncpg). ``format``: ``%s`` (aiomysql). ``qmark``:
# ``?`` (dmPython).
PlaceholderStyle = Literal["numeric", "format", "qmark"]
# What the scanner needs to know about the SQL it reads: MySQL has ``#``
# comments, PostgreSQL has dollar-quoted strings.
SqlDialect = Literal["postgresql", "mysql", "generic"]

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

    With *strict*, text whose reading differs between databases raises
    :class:`SqlBindingError`; without it the scanner takes the reading that
    keeps more text inside the string or comment.
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
            if strict and dialect == "mysql" and two == "--" and not sql[i + 2 : i + 3].isspace():
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
            if i > 0 and (sql[i - 1].isalnum() or sql[i - 1] == "_"):
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
    for token in tokens:
        if token.kind == "variable":
            variables.append(token.text)
            parts.append("")
        elif token.kind == "comment":
            # A comment says nothing to the database; a space keeps the
            # words around it apart.
            parts[-1] += " "
        else:
            parts[-1] += token.text
    return SqlTemplate(parts=tuple(parts), variables=tuple(variables))


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


def render_sql(template: SqlTemplate, style: PlaceholderStyle) -> str:
    """The SQL of *template* with the driver's placeholders in place of the references.

    Raises :class:`SqlBindingError` when the SQL already holds text the
    driver would read as a placeholder.
    """
    out: list[str] = []
    for index, part in enumerate(template.parts):
        if style == "format":
            # The driver fills placeholders with ``%``: a literal percent
            # sign is written doubled.
            part = part.replace("%", "%%")
        elif style == "numeric":
            if _numbered_placeholder_in_code(part):
                raise SqlBindingError(
                    "The SQL already has a numbered parameter ($1, $2, ...). "
                    "Use {{variable}} references only."
                )
        elif _question_mark_in_code(part):
            raise SqlBindingError(
                "The SQL has a '?' outside quotes, which this database reads as "
                "a parameter. Use {{variable}} references only."
            )
        out.append(part)
        if index < len(template.variables):
            out.append({"numeric": f"${index + 1}", "format": "%s", "qmark": "?"}[style])
    return "".join(out)


def _code_of(part: str) -> str:
    return " ".join(t.text for t in sql_tokens(part, dialect="postgresql") if t.kind == "code")


def _numbered_placeholder_in_code(part: str) -> bool:
    return re.search(r"(?<![A-Za-z0-9_$])\$\d", _code_of(part)) is not None


def _question_mark_in_code(part: str) -> bool:
    return "?" in " ".join(t.text for t in sql_tokens(part, dialect="generic") if t.kind == "code")
