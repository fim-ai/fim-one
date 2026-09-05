"""DB connector fences: PII column marking and per-call fence audit

``schema_columns.is_pii`` lets a connector owner mark a column whose values
must not reach the model. The column stays visible in the schema so the
model knows it exists; the query paths replace its values with a mask.

``connector_call_logs.scope_rules_applied`` records which fences were in
force for a call (read-only mode, which columns were masked), so an
operator can answer "what was this caller allowed to see" from the log
rather than by reconstructing the connector's configuration at that time.

Both default to the unfenced behaviour, so existing connectors and existing
log rows keep their current meaning.

Revision ID: j1l3n5p7r901
Revises: h9j1l3n5p789
Create Date: 2026-09-06
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from fim_one.migrations.helpers import table_exists, table_has_column

revision: str = "j1l3n5p7r901"
down_revision: Union[str, None] = "h9j1l3n5p789"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_COLUMNS_TABLE = "schema_columns"
_PII_COLUMN = "is_pii"
_LOG_TABLE = "connector_call_logs"
_SCOPE_COLUMN = "scope_rules_applied"


def upgrade() -> None:
    bind = op.get_bind()

    if table_exists(bind, _COLUMNS_TABLE) and not table_has_column(
        bind, _COLUMNS_TABLE, _PII_COLUMN
    ):
        op.add_column(
            _COLUMNS_TABLE,
            sa.Column(
                _PII_COLUMN,
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("FALSE"),
            ),
        )

    if table_exists(bind, _LOG_TABLE) and not table_has_column(
        bind, _LOG_TABLE, _SCOPE_COLUMN
    ):
        op.add_column(
            _LOG_TABLE,
            sa.Column(_SCOPE_COLUMN, sa.Text(), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()

    if table_exists(bind, _LOG_TABLE) and table_has_column(
        bind, _LOG_TABLE, _SCOPE_COLUMN
    ):
        with op.batch_alter_table(_LOG_TABLE) as batch:
            batch.drop_column(_SCOPE_COLUMN)

    if table_exists(bind, _COLUMNS_TABLE) and table_has_column(
        bind, _COLUMNS_TABLE, _PII_COLUMN
    ):
        with op.batch_alter_table(_COLUMNS_TABLE) as batch:
            batch.drop_column(_PII_COLUMN)
