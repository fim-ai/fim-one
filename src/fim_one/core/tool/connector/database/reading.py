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
``-- x``        a comment, to      a comment only      a comment
                ``\n`` or ``\r``     before whitespace,
                                   to ``\n``
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
import unicodedata
from dataclasses import dataclass
from typing import Literal

__all__ = ["Lexeme", "SqlDialect", "ascii_lower", "is_name_char", "is_odd", "read_sql"]

SqlDialect = Literal["postgresql", "mysql", "generic"]

# PostgreSQL's dollar-quote tag: like a name without "$", non-ASCII included.
_DOLLAR_TAG = re.compile(
    r"\$((?:[A-Za-z_]|[^\x00-\x7f])(?:[A-Za-z0-9_]|[^\x00-\x7f])*)?\$"
)
# The servers' lexers work on bytes and know ASCII only: a letter is A-Z or
# a-z, a digit 0-9, white space is space, tab, newline, carriage return,
# form feed and vertical tab, and every byte of a non-ASCII character is
# part of a name. Python's ``str.isspace``, ``isalnum``, ``upper`` and the
# ``\s`` / ``\w`` of ``re`` follow Unicode instead (U+3000 is a space to
# them, an emoji is not a letter), so none of them is used here.
_NAME_START = r"(?:[A-Za-z_]|[^\x00-\x7f])"
_NAME_PART = r"(?:[A-Za-z0-9_$]|[^\x00-\x7f])"
# A word (keyword or name), a number, or one character of punctuation.
_PIECE = re.compile(
    rf"{_NAME_START}{_NAME_PART}*|\${_NAME_PART}+|[0-9][0-9A-Za-z_.]*|[^ \t\n\r\x0b\x0c]"
)
_WHITE_SPACE = " \t\n\r\x0b\x0c"
_ASCII_UPPER = str.maketrans("abcdefghijklmnopqrstuvwxyz", "ABCDEFGHIJKLMNOPQRSTUVWXYZ")
_ASCII_LOWER = str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz")


def is_name_char(ch: str) -> bool:
    """Whether the servers take *ch* for part of a name (a ``$`` included)."""
    return ch > "\x7f" or ch in "_$" or (ch.isascii() and ch.isalnum())


def ascii_lower(text: str) -> str:
    """*text* with A-Z lower-cased and nothing else touched, as the servers fold names."""
    return text.translate(_ASCII_LOWER)


def is_odd(ch: str) -> bool:
    """A character nobody writes in SQL on purpose, and that lexers disagree about.

    Control characters other than tab, newline and carriage return, and
    every non-ASCII space, line separator, control or invisible format
    character (U+0085, U+00A0, U+3000, zero-width characters, ...). A
    database does not treat the non-ASCII ones as white space, whatever
    they look like.
    """
    if ch < "\x80":
        return (ch < " " and ch not in "\t\n\r") or ch == "\x7f"
    return unicodedata.category(ch) in ("Zs", "Zl", "Zp", "Cc", "Cf")


@dataclass(frozen=True)
class Lexeme:
    """One piece of a statement, as the database sees it.

    ``word``: a keyword or an unquoted name (``upper`` is its upper-case
    form). ``name``: a quoted name (``text`` is the name itself).
    ``string``: a string literal. ``number`` and ``punct``: as written.
    ``executable``: a MySQL ``/*! ... */`` or MariaDB ``/*M! ... */``
    comment, whose inside the server runs as SQL. ``odd``: a character
    outside quotes that :func:`is_odd` names (also inside a comment, which
    is otherwise left out; see ``odd_in``). ``unicode_escaped``:
    PostgreSQL's ``U&"..."``, a name spelled with ``\XXXX`` escapes, which
    is not the name this reading sees. (``U&'...'`` is a string like any
    other here: where it ends does not depend on its escapes.)
    """

    kind: Literal[
        "word", "name", "string", "number", "punct", "executable", "odd", "unicode_escaped"
    ]
    text: str
    # How many parentheses are open around it.
    depth: int = 0

    @property
    def upper(self) -> str:
        return self.text.translate(_ASCII_UPPER) if self.kind == "word" else ""

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

    def lone_cr(start: int, end: int) -> bool:
        """A carriage return in ``sql[start:end]`` that no newline follows."""
        at = sql.find("\r", start, end)
        while at != -1:
            if sql[at + 1 : at + 2] != "\n":
                return True
            at = sql.find("\r", at + 1, end)
        return False

    def flush(upto: int) -> None:
        nonlocal depth
        if dialect == "generic" and lone_cr(code_start, upto):
            # PostgreSQL ends a "--" comment at a carriage return, MySQL
            # does not; what DM8 does is not known here, so there a bare
            # one is refused like the other odd characters.
            out.append(Lexeme("odd", "\r", depth))
        out.extend(Lexeme("odd", ch, depth) for ch in sql[code_start:upto] if is_odd(ch))
        for piece in _PIECE.finditer(sql, code_start, upto):
            text = piece.group(0)
            if len(text) == 1 and is_odd(text):
                continue
            if "0" <= text[0] <= "9":
                out.append(Lexeme("number", text, depth))
            elif len(text) > 1 or (text != "$" and is_name_char(text)):
                out.append(Lexeme("word", text, depth))
            else:
                if text == ")":
                    depth = max(0, depth - 1)
                out.append(Lexeme("punct", text, depth))
                if text == "(":
                    depth += 1

    def odd_in(start: int, end: int) -> None:
        """Odd characters inside the comment ``sql[start:end]``.

        Where a comment ends is decided on ASCII bytes alone (a newline, a
        carriage return, ``*/``), so on PostgreSQL and MySQL a non-ASCII
        character in its body changes nothing: a full-width space in a
        Chinese comment, an emoji's joiner. ASCII control characters stay
        odd there too (MySQL ends a line comment at a NUL byte). For DM8,
        whose lexer is not known here, everything odd stays odd.
        """
        out.extend(
            Lexeme("odd", ch, depth)
            for ch in sql[start:end]
            if is_odd(ch) and (dialect == "generic" or ch < "\x80")
        )
        if dialect == "generic" and lone_cr(start, end):
            out.append(Lexeme("odd", "\r", depth))

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
                    and not (i > 1 and is_name_char(sql[i - 2]))
                )
            else:
                escapes = False
            end = _end_of_quoted(sql, i, escapes)
            if (
                dialect == "postgresql"
                and ch == '"'
                and sql[max(0, i - 2) : i] in ("U&", "u&")
                and not (i > 2 and is_name_char(sql[i - 3]))
            ):
                out.append(Lexeme("unicode_escaped", sql[i - 2 : end], depth))
            if is_name:
                closed = end - 1 if end > i + 1 and sql[end - 1] == ch else end
                out.append(Lexeme("name", sql[i + 1 : closed].replace(ch * 2, ch), depth))
            else:
                out.append(Lexeme("string", sql[i:end], depth))
            i = code_start = end
        elif (ch == "#" and dialect == "mysql") or (
            two == "--"
            # MySQL: a comment only before an ASCII space or control
            # character (or the end); before anything else, a non-ASCII
            # "space" included, it is two minus signs.
            and (
                dialect != "mysql"
                or i + 2 >= n
                or sql[i + 2] in _WHITE_SPACE
                or sql[i + 2] < " "
                or sql[i + 2] == "\x7f"
            )
        ):
            flush(i)
            # MySQL's comment runs to the newline; PostgreSQL's ends at a
            # carriage return as well.
            ends = [sql.find("\n", i)]
            if dialect == "postgresql":
                ends.append(sql.find("\r", i))
            end = min((at for at in ends if at != -1), default=n)
            odd_in(i, end)
            i = code_start = end
        elif two == "/*":
            flush(i)
            end = _end_of_block_comment(sql, i, nested=dialect == "postgresql")
            if dialect == "mysql" and (sql.startswith("/*!", i) or sql.startswith("/*M!", i)):
                out.append(Lexeme("executable", sql[i:end], depth))
            odd_in(i, end)
            i = code_start = end
        elif ch == "$" and dialect == "postgresql" and (tag := _DOLLAR_TAG.match(sql, i)):
            if i > 0 and is_name_char(sql[i - 1]):
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
