"""Column-level redaction for database connector query results.

A connector owner marks a column as PII in the schema manager. Two things
then happen on every query through that connector:

- the column is still advertised to the model, tagged, so it knows the
  column exists and does not invent a substitute;
- any value coming back under that column name is replaced with
  :data:`MASK` before the result is serialized for the model.

Masking by column name (rather than by table) is deliberate: a result set
carries only column labels, so this is the one identifier available for a
``SELECT *``, a join, or an aliased projection.

Scope, stated plainly: this stops a marked column's values from reaching
the model or the transcript. It does not stop the database from *using*
the column — ``WHERE salary > 5000`` still filters server-side, and a
query that aliases the column to another name (``SELECT salary AS s``)
returns unmasked values. Use database-side grants when the requirement is
that the query account cannot read the column at all.
"""

from __future__ import annotations

from typing import Any

__all__ = ["MASK", "pii_column_names", "redact_rows"]

MASK = "***"


def pii_column_names(schema_tables: list[dict[str, Any]]) -> frozenset[str]:
    """Collect the PII column names declared across a connector's tables.

    Parameters
    ----------
    schema_tables:
        The schema dicts handed to the database tools, each with a
        ``columns`` list whose entries may carry ``is_pii``.

    Returns
    -------
    frozenset[str]
        Lower-cased column names. Empty when nothing is marked, which
        lets callers skip the row walk entirely.
    """
    names: set[str] = set()
    for table in schema_tables:
        for column in table.get("columns") or []:
            if column.get("is_pii"):
                name = str(column.get("column_name") or "").strip().lower()
                if name:
                    names.add(name)
    return frozenset(names)


def _bare_name(label: str) -> str:
    """Strip a table qualifier so ``e.salary`` matches a ``salary`` rule."""
    return label.rsplit(".", 1)[-1].strip().lower()


def redact_rows(
    columns: list[str],
    rows: list[list[Any]],
    pii: frozenset[str],
) -> tuple[list[list[Any]], list[str]]:
    """Replace values in PII-marked result columns with :data:`MASK`.

    Returns the rows to serialize and the labels that were masked, in
    result order. The caller reports those labels alongside the rows so
    the model reads the mask as policy rather than as missing data and
    stops re-running the query.

    A ``None`` stays ``None``: null carries no information about the
    person, and masking it would tell the model a value exists.
    """
    if not pii or not columns:
        return rows, []

    hit_indexes = [i for i, label in enumerate(columns) if _bare_name(str(label)) in pii]
    if not hit_indexes:
        return rows, []

    masked_rows: list[list[Any]] = []
    for row in rows:
        new_row = list(row)
        for i in hit_indexes:
            if i < len(new_row) and new_row[i] is not None:
                new_row[i] = MASK
        masked_rows.append(new_row)

    return masked_rows, [str(columns[i]) for i in hit_indexes]
