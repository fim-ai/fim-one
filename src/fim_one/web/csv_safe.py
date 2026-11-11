"""Writing CSV that a spreadsheet will not run.

A spreadsheet program treats a cell that starts with ``=``, ``+``, ``-``,
``@`` (or the full-width form of one of those), a tab or a carriage return as
a formula. Exports here carry text
other people chose (a resource's name, a display name, an audit detail), so
a cell like ``=HYPERLINK(...)`` would run on the administrator's machine
when the file is opened. :func:`safe_cell` puts an apostrophe in front of
such text, which makes the program show it as text. Every CSV the
application writes goes through :func:`safe_writer`.
"""

from __future__ import annotations

import csv
import re
from collections.abc import Iterable
from typing import Any, TextIO

__all__ = ["safe_cell", "safe_row", "safe_writer"]

# The full-width forms too: a spreadsheet program set to an East Asian
# locale reads ``＝SUM(…)`` as the formula ``=SUM(…)``.
_FORMULA_START = ("=", "+", "-", "@", "\t", "\r", "＝", "＋", "－", "＠")
# A plain number with a sign is a number, not a formula.
_NUMBER = re.compile(r"[+-]?\d+(?:[.,]\d+)*%?")


def safe_cell(value: Any) -> Any:
    """*value* as it may be written to a CSV cell.

    Only text is touched; a number, a boolean or ``None`` is written as it is.
    """
    if not isinstance(value, str) or not value:
        return value
    if value.startswith(_FORMULA_START) and not _NUMBER.fullmatch(value):
        return "'" + value
    return value


def safe_row(row: Iterable[Any]) -> list[Any]:
    return [safe_cell(value) for value in row]


class _SafeWriter:
    def __init__(self, target: TextIO) -> None:
        self._writer = csv.writer(target)

    def writerow(self, row: Iterable[Any]) -> None:
        self._writer.writerow(safe_row(row))

    def writerows(self, rows: Iterable[Iterable[Any]]) -> None:
        for row in rows:
            self.writerow(row)


def safe_writer(target: TextIO) -> _SafeWriter:
    """A ``csv.writer`` whose cells cannot be read as formulas."""
    return _SafeWriter(target)
