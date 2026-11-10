"""Workflow runs can wait and be picked up again; ``workflow_approvals`` goes

A run that reaches an approval now stops and is continued later, possibly
by another server process, so what the continuation needs is kept on the
run itself:

- ``workflow_runs.suspend_state``: the variables and step states of a run
  that is waiting for a decision (JSON, encrypted by the application,
  empty for every other run).
- ``workflow_runs.waiting_on``: which request each waiting step waits on
  (small JSON, in the clear, read by the recovery sweep).
- ``workflow_runs.queued_at``: since when a continued run has been queued
  for a concurrent-run slot.
- ``workflow_runs.worker_id`` / ``heartbeat_at``: which server process is
  executing the run and when it last said so. A run still ``running`` with
  an old heartbeat was left behind by a process that died.
- an index on ``workflow_runs.status``: the recovery sweep looks for
  ``waiting`` and ``running`` runs.

``workflow_approvals`` has not been read or written since ``c1p3a1b2c3d4``
(approvals are rows of ``confirmation_requests``). Its rows are copied to
``confirmation_requests`` as closed ``workflow_approval`` requests (a row
still pending belongs to a run that no longer exists as a process, and is
copied as expired), then the table is dropped.

Downgrade drops the new columns and the index and recreates
``workflow_approvals`` empty; the copied rows stay where they are.

Revision ID: t10p5a1b2c3d4
Revises: c1p3a1b2c3d4
Create Date: 2026-11-10
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op

from fim_one.migrations.helpers import index_exists, table_exists, table_has_column

revision: str = "t10p5a1b2c3d4"
down_revision: str | None = "c1p3a1b2c3d4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_RUNS = "workflow_runs"
_STATUS_INDEX = "ix_workflow_runs_status"
_APPROVALS = "workflow_approvals"
_REQUESTS = "confirmation_requests"


def _new_columns() -> list[sa.Column[Any]]:
    return [
        sa.Column("suspend_state", sa.JSON(), nullable=True),
        sa.Column("waiting_on", sa.JSON(), nullable=True),
        sa.Column("queued_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("worker_id", sa.String(64), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
    ]


def upgrade() -> None:
    bind = op.get_bind()

    if table_exists(bind, _RUNS):
        for column in _new_columns():
            if not table_has_column(bind, _RUNS, column.name):
                op.add_column(_RUNS, column)
        if not index_exists(bind, _RUNS, _STATUS_INDEX):
            op.create_index(_STATUS_INDEX, _RUNS, ["status"])

    if table_exists(bind, _APPROVALS):
        if table_exists(bind, _REQUESTS) and table_has_column(
            bind, _REQUESTS, "workflow_run_id"
        ):
            _copy_approvals(bind)
        op.drop_table(_APPROVALS)


def _copy_approvals(bind: Any) -> None:
    """Keep the history of ``workflow_approvals`` as closed requests."""
    approvals = sa.table(
        _APPROVALS,
        sa.column("id", sa.String),
        sa.column("workflow_run_id", sa.String),
        sa.column("node_id", sa.String),
        sa.column("title", sa.String),
        sa.column("description", sa.Text),
        sa.column("assignee", sa.String),
        sa.column("status", sa.String),
        sa.column("decision_by", sa.String),
        sa.column("decision_note", sa.Text),
        sa.column("timeout_hours", sa.Float),
        sa.column("resolved_at", sa.DateTime(timezone=True)),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    requests = sa.table(
        _REQUESTS,
        sa.column("id", sa.String),
        sa.column("mode", sa.String),
        sa.column("kind", sa.String),
        sa.column("status", sa.String),
        sa.column("payload", sa.JSON),
        sa.column("eligible_scope", sa.String),
        sa.column("eligible_user_id", sa.String),
        sa.column("decided_by_user_id", sa.String),
        sa.column("decision_note", sa.Text),
        sa.column("workflow_run_id", sa.String),
        sa.column("node_id", sa.String),
        sa.column("title", sa.String),
        sa.column("description", sa.Text),
        sa.column("responded_at", sa.DateTime(timezone=True)),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    present = set(bind.execute(sa.select(requests.c.id)).scalars())
    for row in bind.execute(sa.select(approvals)).mappings():
        if row["id"] in present:
            continue
        status = row["status"] if row["status"] in ("approved", "rejected") else "expired"
        bind.execute(
            requests.insert().values(
                id=row["id"],
                mode="inline",
                kind="workflow_approval",
                status=status,
                payload={
                    "timeout_hours": row["timeout_hours"],
                    "assignee": row["assignee"],
                    "copied_from": _APPROVALS,
                },
                eligible_scope="assignee" if row["assignee"] else None,
                eligible_user_id=row["assignee"],
                decided_by_user_id=row["decision_by"],
                decision_note=row["decision_note"],
                workflow_run_id=row["workflow_run_id"],
                node_id=row["node_id"],
                title=row["title"],
                description=row["description"],
                responded_at=row["resolved_at"] or row["updated_at"] or row["created_at"],
                created_at=row["created_at"],
                updated_at=row["updated_at"],
            )
        )


def downgrade() -> None:
    bind = op.get_bind()

    if not table_exists(bind, _APPROVALS):
        op.create_table(
            _APPROVALS,
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column(
                "workflow_run_id",
                sa.String(36),
                sa.ForeignKey("workflow_runs.id", ondelete="CASCADE"),
                nullable=False,
                index=True,
            ),
            sa.Column("node_id", sa.String(100), nullable=False),
            sa.Column("title", sa.String(500), nullable=False),
            sa.Column("description", sa.Text, nullable=True),
            sa.Column("assignee", sa.String(36), nullable=True, index=True),
            sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
            sa.Column("decision_by", sa.String(36), nullable=True),
            sa.Column("decision_note", sa.Text, nullable=True),
            sa.Column("timeout_hours", sa.Float, nullable=False, server_default="24"),
            sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("(CURRENT_TIMESTAMP)"),
                nullable=False,
            ),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        )

    if not table_exists(bind, _RUNS):
        return
    if index_exists(bind, _RUNS, _STATUS_INDEX):
        op.drop_index(_STATUS_INDEX, table_name=_RUNS)
    present = [c.name for c in _new_columns() if table_has_column(bind, _RUNS, c.name)]
    if present:
        with op.batch_alter_table(_RUNS) as batch:
            for name in present:
                batch.drop_column(name)
