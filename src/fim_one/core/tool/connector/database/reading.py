"""SQL read the way one database reads it: its words, without comments and strings.

The safety check judges a statement by its keywords. A keyword counts only
where the database would see one, so the text is first split the way that
database's own lexer splits it:

==============  =================  ==================  ===============
                PostgreSQL family  MySQL / MariaDB     generic (DM8)
==============  =================  ==================  ===============
``'...'``       no backslash       backslash escapes   no backslash
                escapes, except
                ``E'...'``
``"..."``       a name             a string            a name
```...```       (not SQL)          a name              (not SQL)
``$tag$...``    a string           (not a quote)       (not a quote)
``-- x``        a comment          a comment only      a comment
                                   before whitespace
``# x``         an operator        a comment           (not SQL)
``/* /* */``    nests              does not nest       does not nest
``/*! x */``    a comment          **runs x as SQL**   a comment
==============  =================  ==================  ===============

These are the servers' defaults. A PostgreSQL server with
``standard_conforming_strings`` off, or a MySQL server in
``NO_BACKSLASH_ESCAPES`` or ``ANSI_QUOTES`` mode, reads strings differently;
for those the read-only transaction is the fence, not this reading.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

__all__ = ["Lexeme", "SqlDialect", "read_sql"]

SqlDialect = Literal["postgresql", "mysql", "generic"]

_DOLLAR_TAG = re.compile(r"\$([A-Za-z_][A-Za-z0-9_]*)?\$")
# A word (keyword or name), a number, or one character of punctuation.
_PIECE = re.compile(r"[^\W\d][\w$]*|\$[\w$]+|\d[\w.]*|\S", re.UNICODE)


@dataclass(frozen=True)
class Lexeme:
    """One piece of a statement, as the database sees it.

    ``word``: a keyword or an unquoted name (``upper`` is its upper-case
    form). ``name``: a quoted name (``text`` is the name itself).
    ``string``: a string literal. ``number`` and ``punct``: as written.
    ``executable``: a MySQL ``/*! ... */`` or MariaDB ``/*M! ... */``
    comment, whose inside the server runs as SQL.
    """

    kind: Literal["word", "name", "string", "number", "punct", "executable"]
    text: str
    # How many parentheses are open around it.
    depth: int = 0

    @property
    def upper(self) -> str:
        return self.text.upper() if self.kind == "word" else ""

    def is_punct(self, text: str) -> bool:
        return self.kind == "punct" and self.text == text


def read_sql(
    sql: str, *, dialect: SqlDialect, backslash_escapes: bool | None = None
) -> list[Lexeme]:
    """The lexemes of *sql* under *dialect*; comments are left out.

    *backslash_escapes* overrides the dialect's rule for a backslash inside
    a string (``None``: the dialect's default).
    """
    out: list[Lexeme] = []
    depth = 0
    code_start = 0
    i = 0
    n = len(sql)

    def flush(upto: int) -> None:
        nonlocal depth
        for piece in _PIECE.finditer(sql, code_start, upto):
            text = piece.group(0)
            if text[0].isdigit():
                out.append(Lexeme("number", text, depth))
            elif text[0].isalpha() or text[0] == "_" or (text[0] == "$" and len(text) > 1):
                out.append(Lexeme("word", text, depth))
            else:
                if text == ")":
                    depth = max(0, depth - 1)
                out.append(Lexeme("punct", text, depth))
                if text == "(":
                    depth += 1

    while i < n:
        ch = sql[i]
        two = sql[i : i + 2]
        if ch in "'\"`":
            flush(i)
            is_name = ch == "`" or (ch == '"' and dialect != "mysql")
            if backslash_escapes is not None:
                escapes = backslash_escapes and ch != "`"
            elif dialect == "mysql":
                escapes = ch != "`"
            elif dialect == "postgresql":
                # E'...' (but not the last letter of a longer word).
                escapes = (
                    ch == "'"
                    and i > 0
                    and sql[i - 1] in "eE"
                    and not (i > 1 and (sql[i - 2].isalnum() or sql[i - 2] in "_$"))
                )
            else:
                escapes = False
            end = _end_of_quoted(sql, i, escapes)
            if is_name:
                closed = end - 1 if end > i + 1 and sql[end - 1] == ch else end
                out.append(Lexeme("name", sql[i + 1 : closed].replace(ch * 2, ch), depth))
            else:
                out.append(Lexeme("string", sql[i:end], depth))
            i = code_start = end
        elif (ch == "#" and dialect == "mysql") or (
            two == "--"
            # MySQL: a comment only before whitespace; else two minus signs.
            and (dialect != "mysql" or i + 2 >= n or sql[i + 2].isspace() or ord(sql[i + 2]) < 32)
        ):
            flush(i)
            newline = sql.find("\n", i)
            i = code_start = n if newline == -1 else newline
        elif two == "/*":
            flush(i)
            end = _end_of_block_comment(sql, i, nested=dialect == "postgresql")
            if dialect == "mysql" and (sql.startswith("/*!", i) or sql.startswith("/*M!", i)):
                out.append(Lexeme("executable", sql[i:end], depth))
            i = code_start = end
        elif ch == "$" and dialect == "postgresql" and (tag := _DOLLAR_TAG.match(sql, i)):
            if i > 0 and (sql[i - 1].isalnum() or sql[i - 1] == "_"):
                # ``a$b$c`` is one name.
                i += 1
                continue
            flush(i)
            close = sql.find(tag.group(0), tag.end())
            end = n if close == -1 else close + len(tag.group(0))
            out.append(Lexeme("string", sql[i:end], depth))
            i = code_start = end
        else:
            i += 1
    flush(n)
    return out


def _end_of_quoted(sql: str, start: int, escapes: bool) -> int:
    quote = sql[start]
    i = start + 1
    n = len(sql)
    while i < n:
        ch = sql[i]
        if ch == "\\" and escapes:
            i += 2
            continue
        if ch == quote:
            if i + 1 < n and sql[i + 1] == quote:
                i += 2
                continue
            return i + 1
        i += 1
    return n


def _end_of_block_comment(sql: str, start: int, *, nested: bool) -> int:
    n = len(sql)
    if not nested:
        close = sql.find("*/", start + 2)
        return n if close == -1 else close + 2
    open_count = 1
    i = start + 2
    while i < n and open_count:
        two = sql[i : i + 2]
        if two == "/*":
            open_count += 1
            i += 2
        elif two == "*/":
            open_count -= 1
            i += 2
        else:
            i += 1
    return i
