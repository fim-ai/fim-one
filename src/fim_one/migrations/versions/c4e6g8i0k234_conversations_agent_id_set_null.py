"""conversations.agent_id ON DELETE SET NULL

Deleting an agent that is still referenced by a conversation used to be a
hard FK violation once foreign keys are enforced (SQLite now runs with
``PRAGMA foreign_keys=ON``; Postgres always enforces).  Conversations are
historical chat records that should outlive the agent, so the reference is
cleared rather than blocking the delete.  ``Agent.conversations`` already
declares ``passive_deletes=True``, i.e. it relies on this DB-level rule.

Revision ID: c4e6g8i0k234
Revises: z9b1d3f5h678
Create Date: 2026-06-22
"""

from __future__ import annotations

from alembic import op

from fim_one.migrations.helpers import table_exists

revision = "c4e6g8i0k234"
down_revision = "z9b1d3f5h678"
branch_labels = None
depends_on = None

_FK_NAME = "fk_conversations_agent_id_agents"
# Deterministic names so the (otherwise unnamed) reflected FK can be dropped
# in SQLite batch mode.
_NAMING_CONVENTION = {
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s"
}


def upgrade() -> None:
    bind = op.get_bind()
    if not table_exists(bind, "conversations"):
        return

    if bind.dialect.name == "postgresql":
        # Postgres auto-names the original FK ``<table>_<col>_fkey``.
        op.drop_constraint(
            "conversations_agent_id_fkey", "conversations", type_="foreignkey"
        )
        op.create_foreign_key(
            _FK_NAME,
            "conversations",
            "agents",
            ["agent_id"],
            ["id"],
            ondelete="SET NULL",
        )
    else:
        # SQLite cannot ALTER a constraint — batch mode recreates the table.
        # The naming convention gives the reflected unnamed FK a stable name
        # so it can be dropped and re-created with ON DELETE SET NULL.
        with op.batch_alter_table(
            "conversations", naming_convention=_NAMING_CONVENTION
        ) as batch_op:
            batch_op.drop_constraint(_FK_NAME, type_="foreignkey")
            batch_op.create_foreign_key(
                _FK_NAME,
                "agents",
                ["agent_id"],
                ["id"],
                ondelete="SET NULL",
            )


def downgrade() -> None:
    bind = op.get_bind()
    if not table_exists(bind, "conversations"):
        return

    if bind.dialect.name == "postgresql":
        op.drop_constraint(_FK_NAME, "conversations", type_="foreignkey")
        op.create_foreign_key(
            "conversations_agent_id_fkey",
            "conversations",
            "agents",
            ["agent_id"],
            ["id"],
        )
    else:
        with op.batch_alter_table(
            "conversations", naming_convention=_NAMING_CONVENTION
        ) as batch_op:
            batch_op.drop_constraint(_FK_NAME, type_="foreignkey")
            batch_op.create_foreign_key(
                _FK_NAME, "agents", ["agent_id"], ["id"]
            )
