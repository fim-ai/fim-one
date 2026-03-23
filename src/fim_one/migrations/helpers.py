"""Shared helpers for idempotent Alembic migrations.

These utilities allow migrations to safely skip operations when the target
schema object already exists — critical for databases that were originally
created by ``Base.metadata.create_all()`` and have no ``alembic_version``
row, meaning ``alembic upgrade head`` replays ALL migrations from scratch.
"""
from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy import Connection, Engine


def table_exists(bind: Connection | Engine, name: str) -> bool:
    """Return True if *name* exists as a table in the current database."""
    return name in sa.inspect(bind).get_table_names()


def table_has_column(bind: Connection | Engine, table: str, column: str) -> bool:
    """Return True if *table* already has a column named *column*."""
    return column in {c["name"] for c in sa.inspect(bind).get_columns(table)}


def index_exists(bind: Connection | Engine, table: str, index_name: str) -> bool:
    """Return True if *index_name* exists on *table* (index or unique constraint)."""
    insp = sa.inspect(bind)
    for idx in insp.get_indexes(table):
        if idx["name"] == index_name:
            return True
    try:
        for uc in insp.get_unique_constraints(table):
            if uc["name"] == index_name:
                return True
    except Exception:
        pass
    return False
