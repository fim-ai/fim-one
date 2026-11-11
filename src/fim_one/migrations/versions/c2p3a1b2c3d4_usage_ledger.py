"""Usage ledger: an append-only record of what each user consumed

"Used" was the sum of ``conversations.total_tokens`` and
``workflow_runs.total_tokens`` over the rows created in the quota window,
so it shrank whenever a conversation, a run or a workflow was deleted.
``usage_ledger`` records consumption where it happens and is never updated
or deleted; no column is a foreign key.

Schema: the table and three indexes, ``(user_id, created_at)``,
``(org_id, created_at)`` and ``(source_type, source_id)``.

Backfill: so that every user's "used" is the same the moment after this
migration as the moment before, the existing totals are carried over as
rows of kind ``backfill``:

- a conversation with ``total_tokens > 0`` gives one row for the main model
  (``total_tokens - fast_llm_tokens``, model = the conversation's
  ``model_name``) and one for the fast model (``fast_llm_tokens``), each
  only when positive;
- a workflow run with ``total_tokens > 0`` gives one row, its ``entry`` the
  run's ``mode``.

A backfilled row takes the ``created_at`` of its conversation or run: the
old sum assigned usage to the window its source was created in, and only
by copying that do the two agree. Evaluation runs were never counted and
are not carried over.

How it runs:

- in id order, :data:`BATCH` source rows at a time (keyset on the primary
  key), so memory is bounded by one batch whatever the table size;
- on PostgreSQL in autocommit, a few multi-row statements per batch, each a
  transaction of its own: no transaction stays open across the table, and
  the only locks are the row locks of the inserts into the new table (the
  source tables are read under ``ACCESS SHARE``, which blocks no reader or
  writer);
- restartable: a row's id is derived from its source's id (``uuid5``), and
  the insert is an upsert, so running it again writes nothing twice and
  brings a row up to date if its source changed in between. A row that is
  already as its source says is not written again, so a second run over a
  large table rewrites nothing.

At the end the backfilled totals are compared with the source tables per
user (:data:`RECONCILE_SQL`). A difference means a conversation or run was
written while the backfill ran (an instance of the previous release still
serving), or an earlier, interrupted run carried over a source that has
been deleted since. Only the users that differ are carried over again,
their leftovers removed, in up to :data:`RECONCILE_ROUNDS` short rounds;
a difference that remains after them fails the migration. What an instance
of the previous release adds after the migration has finished has no
ledger row; ``python -m fim_one.core.usage.topup`` records it.

Downgrade drops the table, and with it what was metered since.

Revision ID: c2p3a1b2c3d4
Revises: c2p1a1b2c3d4
Create Date: 2026-11-11
"""

from __future__ import annotations

import importlib
import uuid
from collections.abc import Iterator, Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op

from fim_one.migrations.helpers import index_exists, table_exists

revision: str = "c2p3a1b2c3d4"
down_revision: str | None = "c2p1a1b2c3d4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "usage_ledger"
_INDEXES: dict[str, list[str]] = {
    "ix_usage_ledger_user_created": ["user_id", "created_at"],
    "ix_usage_ledger_org_created": ["org_id", "created_at"],
    "ix_usage_ledger_source": ["source_type", "source_id"],
}
# Rounds of carrying over again the users whose totals differ.
RECONCILE_ROUNDS = 5
# Source rows read per query. A conversation gives up to two ledger rows.
BATCH = 2000
# Ledger rows per INSERT: fourteen values a row, under SQLite's limit of
# 32766 bound values in one statement.
_STATEMENT_ROWS = 1000
# The namespace the ids of backfilled rows are derived in.
_NAMESPACE = uuid.UUID("c2b3a1b2-c3d4-4c2b-8a1b-c2b3a1b2c3d4")

# Per user and source table: what the old sum saw, and what was carried
# over. Any row it returns is a difference. Also the reconciliation an
# operator can run by hand right after the migration.
RECONCILE_SQL = """
SELECT user_id, source_type, SUM(source_total) AS source_total, SUM(ledger_total) AS ledger_total
FROM (
    SELECT user_id, 'conversation' AS source_type,
           total_tokens AS source_total, 0 AS ledger_total
      FROM conversations WHERE total_tokens > 0
    UNION ALL
    SELECT user_id, 'workflow_run', total_tokens, 0
      FROM workflow_runs WHERE total_tokens > 0
    UNION ALL
    SELECT user_id, source_type, 0, total_tokens
      FROM usage_ledger WHERE kind = 'backfill'
) AS both_sides
GROUP BY user_id, source_type
HAVING SUM(source_total) <> SUM(ledger_total)
"""


def _ledger() -> sa.TableClause:
    return sa.table(
        _TABLE,
        sa.column("id", sa.String),
        # Untyped: the source's value is passed on as the driver gave it (a
        # datetime on PostgreSQL, the stored text on SQLite), unchanged.
        sa.column("created_at"),
        sa.column("user_id", sa.String),
        sa.column("org_id", sa.String),
        sa.column("source_type", sa.String),
        sa.column("source_id", sa.String),
        sa.column("entry", sa.String),
        sa.column("agent_id", sa.String),
        sa.column("workflow_id", sa.String),
        sa.column("model", sa.String),
        sa.column("total_tokens", sa.BigInteger),
        sa.column("estimated", sa.Boolean),
        sa.column("kind", sa.String),
        sa.column("unit", sa.String),
    )


def _row_id(kind: str, source_id: str) -> str:
    return str(uuid.uuid5(_NAMESPACE, f"{kind}:{source_id}"))


def _row(
    *, kind: str, source_type: str, source: Any, entry: str, model: str | None, tokens: int,
    agent_id: str | None = None, workflow_id: str | None = None,
) -> dict[str, Any]:
    return {
        "id": _row_id(kind, source.id),
        "created_at": source.created_at,
        "user_id": source.user_id,
        "org_id": None,
        "source_type": source_type,
        "source_id": source.id,
        "entry": entry[:40],
        "agent_id": agent_id,
        "workflow_id": workflow_id,
        "model": model[:100] if model else None,
        "total_tokens": tokens,
        "estimated": False,
        "kind": "backfill",
        "unit": "llm_token",
    }


def _conversation_rows(source: Any) -> Iterator[dict[str, Any]]:
    total = int(source.total_tokens or 0)
    # Never more than the total, so the two rows add up to it exactly.
    fast = min(max(int(source.fast_llm_tokens or 0), 0), total)
    common = {"source_type": "conversation", "source": source, "agent_id": source.agent_id}
    entry = source.mode or "chat"
    if total - fast > 0:
        yield _row(kind="conversation", entry=entry, model=source.model_name,
                   tokens=total - fast, **common)
    if fast > 0:
        yield _row(kind="conversation-fast", entry="background", model=None, tokens=fast, **common)


def _run_rows(source: Any) -> Iterator[dict[str, Any]]:
    yield _row(
        kind="workflow_run",
        source_type="workflow_run",
        source=source,
        entry=source.mode or "manual",
        model=None,
        tokens=int(source.total_tokens or 0),
        workflow_id=source.workflow_id,
    )


_COLUMNS = (
    "id", "created_at", "user_id", "org_id", "source_type", "source_id", "entry",
    "agent_id", "workflow_id", "model", "total_tokens", "estimated", "kind", "unit",
)  # fmt: skip
# What a row that is already there takes from its source again.
_REFRESHED = ("created_at", "user_id", "entry", "model", "total_tokens")


def _changed_sql(dialect: str) -> str:
    """The condition under which an upsert rewrites the row that is there.

    Null-safe: ``IS DISTINCT FROM`` on PostgreSQL, ``IS NOT`` on SQLite.
    """
    if dialect == "postgresql":
        mine = ", ".join(f"{_TABLE}.{name}" for name in _REFRESHED)
        theirs = ", ".join(f"excluded.{name}" for name in _REFRESHED)
        return f"({mine}) IS DISTINCT FROM ({theirs})"
    return " OR ".join(f"{_TABLE}.{name} IS NOT excluded.{name}" for name in _REFRESHED)


def _upsert(bind: sa.Connection, rows: list[dict[str, Any]]) -> None:
    """Insert *rows*, or bring the ones already there up to date.

    One multi-row statement per :data:`_STATEMENT_ROWS`: a statement is a
    transaction of its own in autocommit, and row-by-row that is a commit
    (and a round trip) per row.
    """
    if not rows:
        return
    if bind.dialect.name == "postgresql" and bind.dialect.driver == "psycopg2":
        # The driver's own multi-row insert: building the same statement
        # through SQLAlchemy takes three to four times as long, and on a
        # large table that is most of the migration.
        # (Loaded by name: the driver ships no type information.)
        extras = importlib.import_module("psycopg2.extras")
        cursor = bind.connection.cursor()
        try:
            extras.execute_values(
                cursor,
                f"INSERT INTO {_TABLE} ({', '.join(_COLUMNS)}) VALUES %s"
                " ON CONFLICT (id) DO UPDATE SET "
                + ", ".join(f"{name} = excluded.{name}" for name in _REFRESHED)
                + " WHERE "
                + _changed_sql("postgresql"),
                [tuple(row[name] for name in _COLUMNS) for row in rows],
                page_size=_STATEMENT_ROWS,
            )
        finally:
            cursor.close()
        return

    ledger = _ledger()
    if bind.dialect.name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        insert: Any = pg_insert
    else:
        from sqlalchemy.dialects.sqlite import insert as sqlite_insert

        insert = sqlite_insert
    for start in range(0, len(rows), _STATEMENT_ROWS):
        statement = insert(ledger).values(rows[start : start + _STATEMENT_ROWS])
        excluded = statement.excluded
        bind.execute(
            statement.on_conflict_do_update(
                index_elements=["id"],
                set_={name: getattr(excluded, name) for name in _REFRESHED},
                where=sa.text(_changed_sql(bind.dialect.name)),
            )
        )


_CONVERSATIONS_SQL = (
    "SELECT id, user_id, created_at, total_tokens, fast_llm_tokens, model_name,"
    " mode, agent_id FROM conversations"
    " WHERE id > :last AND total_tokens > 0{owner} ORDER BY id LIMIT :batch"
)
_RUNS_SQL = (
    "SELECT id, user_id, created_at, total_tokens, mode, workflow_id FROM workflow_runs"
    " WHERE id > :last AND total_tokens > 0{owner} ORDER BY id LIMIT :batch"
)


def _copy(
    bind: sa.Connection,
    select_sql: str,
    make_rows: Any,
    params: dict[str, Any] | None = None,
) -> set[str]:
    """Carry one source table over, a batch at a time in id order.

    Returns the ids of the rows written.
    """
    last = ""
    written: set[str] = set()
    while True:
        batch = bind.execute(
            sa.text(select_sql), {"last": last, "batch": BATCH, **(params or {})}
        ).all()
        if not batch:
            return written
        rows = [row for source in batch for row in make_rows(source)]
        _upsert(bind, rows)
        written.update(row["id"] for row in rows)
        last = batch[-1].id


def _backfill(bind: sa.Connection) -> None:
    if table_exists(bind, "conversations"):
        _copy(bind, _CONVERSATIONS_SQL.format(owner=""), _conversation_rows)
    if table_exists(bind, "workflow_runs"):
        _copy(bind, _RUNS_SQL.format(owner=""), _run_rows)


def _differences(bind: sa.Connection) -> list[Any]:
    if not (table_exists(bind, "conversations") and table_exists(bind, "workflow_runs")):
        return []
    return list(bind.execute(sa.text(RECONCILE_SQL)).all())


def _carry_over_again(bind: sa.Connection, user_id: str | None) -> None:
    """Bring one user's backfilled rows to what their sources say now.

    Their sources are carried over again, and the backfilled rows of theirs
    that no source gives any more are removed: those of a conversation or
    run deleted (or emptied) since an earlier, interrupted run, and a fast
    or main row its conversation no longer splits off.
    """
    if user_id is None:
        owner, params, mine = " AND user_id IS NULL", {}, "user_id IS NULL"
    else:
        owner, params, mine = " AND user_id = :user", {"user": user_id}, "user_id = :user"
    current = _copy(bind, _CONVERSATIONS_SQL.format(owner=owner), _conversation_rows, params)
    current |= _copy(bind, _RUNS_SQL.format(owner=owner), _run_rows, params)
    there = bind.execute(
        sa.text(f"SELECT id FROM {_TABLE} WHERE kind = 'backfill' AND {mine}"), params
    ).scalars()
    stale = [row_id for row_id in there if row_id not in current]
    for start in range(0, len(stale), _STATEMENT_ROWS):
        bind.execute(
            sa.text(f"DELETE FROM {_TABLE} WHERE id IN :ids AND kind = 'backfill'").bindparams(
                sa.bindparam("ids", expanding=True)
            ),
            {"ids": stale[start : start + _STATEMENT_ROWS]},
        )


def _backfill_and_reconcile(bind: sa.Connection) -> None:
    _backfill(bind)
    differences = _differences(bind)
    # Each round covers only the users that differ, so it takes as long as
    # their sources do, not the table: a writer still running gets a short
    # window to fall between the carry-over and the check.
    for _ in range(RECONCILE_ROUNDS):
        if not differences:
            return
        for user_id in sorted({row.user_id for row in differences}, key=lambda u: u or ""):
            _carry_over_again(bind, user_id)
        differences = _differences(bind)
    if differences:
        shown = ", ".join(
            f"user {row.user_id} {row.source_type}: source {row.source_total},"
            f" ledger {row.ledger_total}"
            for row in differences[:5]
        )
        raise RuntimeError(
            f"usage_ledger backfill does not match its sources for {len(differences)} user(s)"
            f" ({shown}) after {RECONCILE_ROUNDS} rounds of carrying them over again."
            " Conversations or workflow runs are still being written: stop the instances"
            " of the previous release and run the migration again."
        )


def upgrade() -> None:
    bind = op.get_bind()

    if not table_exists(bind, _TABLE):
        op.create_table(
            _TABLE,
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("(CURRENT_TIMESTAMP)"),
            ),
            sa.Column("user_id", sa.String(36), nullable=True),
            sa.Column("org_id", sa.String(36), nullable=True),
            sa.Column("source_type", sa.String(20), nullable=False),
            sa.Column("source_id", sa.String(64), nullable=True),
            sa.Column("entry", sa.String(40), nullable=False),
            sa.Column("agent_id", sa.String(36), nullable=True),
            sa.Column("workflow_id", sa.String(36), nullable=True),
            sa.Column("model", sa.String(100), nullable=True),
            sa.Column("prompt_tokens", sa.Integer(), nullable=True),
            sa.Column("completion_tokens", sa.Integer(), nullable=True),
            sa.Column("cache_read_tokens", sa.Integer(), nullable=True),
            sa.Column("cache_creation_tokens", sa.Integer(), nullable=True),
            sa.Column("total_tokens", sa.BigInteger(), nullable=False),
            sa.Column("estimated", sa.Boolean(), nullable=False, server_default=sa.text("FALSE")),
            sa.Column("kind", sa.String(20), nullable=False, server_default="usage"),
            sa.Column("unit", sa.String(20), nullable=False, server_default="llm_token"),
        )
    for name, columns in _INDEXES.items():
        if not index_exists(bind, _TABLE, name):
            op.create_index(name, _TABLE, columns)

    if bind.dialect.name == "postgresql":
        # Commits the table (and whatever the migration run did before), then
        # every batch on its own: no transaction spans the source tables.
        with op.get_context().autocommit_block():
            _backfill_and_reconcile(op.get_bind())
    else:
        _backfill_and_reconcile(bind)


def downgrade() -> None:
    bind = op.get_bind()
    if not table_exists(bind, _TABLE):
        return
    for name in _INDEXES:
        if index_exists(bind, _TABLE, name):
            op.drop_index(name, table_name=_TABLE)
    op.drop_table(_TABLE)
