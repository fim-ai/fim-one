"""Record whether a subscription was chosen or installed as a dependency

``resource_subscriptions.source`` separates the row a user created by
subscribing themselves (``direct``) from one added on their behalf because a
solution they subscribed to depends on it (``auto``).

Unsubscribing from a solution cascades through its dependencies and removes
the ones nothing else needs, together with any credentials stored for them.
Without this column the two kinds of row are indistinguishable, so the
cascade could delete a connector the user had subscribed to and configured
independently, discarding an encrypted credential that cannot be recovered.

Existing rows default to ``direct``: the cascade then leaves them alone,
which is the safe reading of a row whose origin is no longer knowable.

Revision ID: k2m4o6q8s012
Revises: j1l3n5p7r901
Create Date: 2026-09-06
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from fim_one.migrations.helpers import table_exists, table_has_column

revision: str = "k2m4o6q8s012"
down_revision: Union[str, None] = "j1l3n5p7r901"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = "resource_subscriptions"
_COLUMN = "source"


def upgrade() -> None:
    bind = op.get_bind()

    if table_exists(bind, _TABLE) and not table_has_column(bind, _TABLE, _COLUMN):
        op.add_column(
            _TABLE,
            sa.Column(
                _COLUMN,
                sa.String(10),
                nullable=False,
                server_default="direct",
            ),
        )


def downgrade() -> None:
    bind = op.get_bind()

    if table_exists(bind, _TABLE) and table_has_column(bind, _TABLE, _COLUMN):
        with op.batch_alter_table(_TABLE) as batch:
            batch.drop_column(_COLUMN)
