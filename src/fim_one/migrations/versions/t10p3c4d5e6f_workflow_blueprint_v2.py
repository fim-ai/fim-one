"""Rewrite stored workflow blueprints as blueprint v2

Every stored blueprint (workflows, workflow versions, DB workflow
templates, run snapshots) is rewritten by
``fim_one.core.workflow.blueprint_v2.upgrade_blueprint``: node types become
the editor's names (``conditionBranch``), ``data.type`` is dropped, and the
field aliases older editors and templates wrote (``prompt``, ``query``,
``kb_id``, ``knowledge_base_id``, ``action``) take their v2 names. The engine
no longer reads the old spellings.

The original JSON of each rewritten row is kept in a new ``blueprint_legacy``
column for one release. Rows that are already v2 are skipped, so the
migration can run again safely.

Revision ID: t10p3c4d5e6f
Revises: t10p1b2c3d4e
Create Date: 2026-11-04
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op

from fim_one.migrations.helpers import table_exists, table_has_column

revision: str = "t10p3c4d5e6f"
down_revision: str | None = "t10p1b2c3d4e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# table -> column holding the blueprint
TABLES: dict[str, str] = {
    "workflows": "blueprint",
    "workflow_versions": "blueprint",
    "workflow_templates": "blueprint",
    "workflow_runs": "blueprint_snapshot",
}
_BATCH = 500


def _load(value: Any) -> Any:
    if isinstance(value, (bytes, bytearray)):
        value = value.decode()
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return None
    return value


def _rewrite_table(bind: sa.Connection, table: str, column: str) -> None:
    from fim_one.core.workflow.blueprint_v2 import is_v2, upgrade_blueprint

    tbl = sa.table(
        table,
        sa.column("id", sa.String),
        sa.column(column, sa.JSON),
        sa.column("blueprint_legacy", sa.JSON),
    )
    last_id = ""
    while True:
        rows = bind.execute(
            sa.select(tbl.c.id, tbl.c[column], tbl.c.blueprint_legacy)
            .where(tbl.c.id > last_id)
            .order_by(tbl.c.id)
            .limit(_BATCH)
        ).fetchall()
        if not rows:
            return
        for row_id, raw_value, legacy in rows:
            last_id = row_id
            original = _load(raw_value)
            if not isinstance(original, dict) or is_v2(original):
                continue
            values: dict[str, Any] = {column: upgrade_blueprint(original)}
            if legacy is None:
                values["blueprint_legacy"] = original
            bind.execute(tbl.update().where(tbl.c.id == row_id).values(**values))


def upgrade() -> None:
    bind = op.get_bind()
    for table, column in TABLES.items():
        if not table_exists(bind, table):
            continue
        if not table_has_column(bind, table, "blueprint_legacy"):
            op.add_column(table, sa.Column("blueprint_legacy", sa.JSON(), nullable=True))
        _rewrite_table(bind, table, column)


def downgrade() -> None:
    bind = op.get_bind()
    for table, column in TABLES.items():
        if not table_exists(bind, table) or not table_has_column(
            bind, table, "blueprint_legacy"
        ):
            continue
        tbl = sa.table(
            table,
            sa.column(column, sa.JSON),
            sa.column("blueprint_legacy", sa.JSON),
        )
        bind.execute(
            tbl.update()
            .where(tbl.c.blueprint_legacy.isnot(None))
            .values({column: tbl.c.blueprint_legacy})
        )
        with op.batch_alter_table(table) as batch:
            batch.drop_column("blueprint_legacy")
