"""Migration ``t10p5a1b2c3d4``: runs can wait; ``workflow_approvals`` goes.

Up, again, down and up, on SQLite and (with ``FIM_TEST_PG_URL`` pointing at
an empty throwaway database) on PostgreSQL. A run written before the
migration comes through every step unchanged, and the rows of
``workflow_approvals`` are kept as closed requests.
"""

from __future__ import annotations

import importlib
import os
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy import make_url, text
from sqlalchemy.orm import Session

import fim_one.db.models  # noqa: F401
from fim_one.db.base import Base
from fim_one.db.models.user import User
from fim_one.db.models.workflow import Workflow, WorkflowRun

_PG = os.environ.get("FIM_TEST_PG_URL")
_MIGRATION = "fim_one.migrations.versions.t10p5a1b2c3d4_workflow_run_suspend"
_RUNS = "workflow_runs"
_INDEX = "ix_workflow_runs_status"
_NEW_COLUMNS = {"suspend_state", "waiting_on", "queued_at", "worker_id", "heartbeat_at"}
_APPROVALS_DDL = """
    CREATE TABLE workflow_approvals (
        id VARCHAR(36) PRIMARY KEY,
        workflow_run_id VARCHAR(36) NOT NULL REFERENCES workflow_runs(id) ON DELETE CASCADE,
        node_id VARCHAR(100) NOT NULL,
        title VARCHAR(500) NOT NULL,
        description TEXT,
        assignee VARCHAR(36),
        status VARCHAR(20) NOT NULL DEFAULT 'pending',
        decision_by VARCHAR(36),
        decision_note TEXT,
        timeout_hours FLOAT NOT NULL DEFAULT 24,
        resolved_at TIMESTAMP,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP
    )
"""


def _engine_params() -> list[Any]:
    return [
        pytest.param("sqlite:///:memory:", id="sqlite"),
        pytest.param(
            _PG or "",
            id="postgresql",
            marks=pytest.mark.skipif(not _PG, reason="FIM_TEST_PG_URL not set"),
        ),
    ]


@pytest.fixture(params=_engine_params())
def legacy_db(request: pytest.FixtureRequest) -> Iterator[sa.Engine]:
    """The schema as it stood before the migration: one run, two old approvals."""
    url = make_url(request.param)
    if url.get_backend_name() == "postgresql":
        url = url.set(drivername="postgresql+psycopg2")
    engine = sa.create_engine(url, future=True)
    schema = None
    if engine.dialect.name == "postgresql":
        schema = f"t10p5_migration_{uuid.uuid4().hex[:12]}"
        with engine.begin() as conn:
            conn.execute(text(f'CREATE SCHEMA "{schema}"'))
        engine.dispose()
        engine = sa.create_engine(
            url, future=True, connect_args={"options": f"-csearch_path={schema}"}
        )

    Base.metadata.create_all(engine)
    with Session(engine) as session:
        user = User(id="user-1", email="u@x.io")
        session.add(user)
        session.flush()
        workflow = Workflow(id="wf-1", user_id=user.id, name="w", blueprint={"nodes": []})
        session.add(workflow)
        session.flush()
        session.add(
            WorkflowRun(
                id="run-1",
                workflow_id=workflow.id,
                user_id=user.id,
                blueprint_snapshot={"nodes": []},
                status="completed",
            )
        )
        session.commit()
    with engine.begin() as conn:
        # Back to the shape before the migration.
        conn.execute(text(f"DROP INDEX {_INDEX}"))
        for column in sorted(_NEW_COLUMNS):
            conn.execute(text(f"ALTER TABLE {_RUNS} DROP COLUMN {column}"))
        conn.execute(text(_APPROVALS_DDL))
        conn.execute(
            text(
                "INSERT INTO workflow_approvals (id, workflow_run_id, node_id, title, assignee,"
                " status, decision_by, decision_note) VALUES"
                " ('appr-1', 'run-1', 'human_1', 'Pay?', 'user-1', 'approved', 'user-1', 'ok'),"
                " ('appr-2', 'run-1', 'human_2', 'Ship?', NULL, 'pending', NULL, NULL)"
            )
        )
    yield engine
    if schema is not None:
        with engine.begin() as conn:
            conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
    engine.dispose()


def _migrate(engine: sa.Engine, direction: str) -> None:
    module = importlib.import_module(_MIGRATION)
    with engine.connect() as conn:
        with Operations.context(MigrationContext.configure(conn)):
            getattr(module, direction)()
        conn.commit()


def _shape(engine: sa.Engine) -> dict[str, Any]:
    inspector = sa.inspect(engine)
    columns = {c["name"]: c for c in inspector.get_columns(_RUNS)}
    return {
        "columns": set(columns),
        "nullable": {name for name, c in columns.items() if c["nullable"]},
        "foreign_keys": len(inspector.get_foreign_keys(_RUNS)),
        "indexes": {i["name"] for i in inspector.get_indexes(_RUNS)},
        "approvals_table": inspector.has_table("workflow_approvals"),
    }


def _run(engine: sa.Engine) -> list[tuple[Any, ...]]:
    with engine.connect() as conn:
        return [
            tuple(r)
            for r in conn.execute(text(f"SELECT id, workflow_id, user_id, status FROM {_RUNS}"))
        ]


def _copied(engine: sa.Engine) -> list[tuple[Any, ...]]:
    with engine.connect() as conn:
        return [
            tuple(r)
            for r in conn.execute(
                text(
                    "SELECT id, kind, status, workflow_run_id, node_id, title, eligible_scope,"
                    " eligible_user_id, decided_by_user_id, decision_note"
                    " FROM confirmation_requests ORDER BY id"
                )
            )
        ]


_RUN_ROW = [("run-1", "wf-1", "user-1", "completed")]
_COPIED_ROWS = [
    (
        "appr-1", "workflow_approval", "approved", "run-1", "human_1", "Pay?",
        "assignee", "user-1", "user-1", "ok",
    ),
    # Still pending in the old table: its run is long gone, so it is closed.
    ("appr-2", "workflow_approval", "expired", "run-1", "human_2", "Ship?", None, None, None, None),
]  # fmt: skip


class TestMigration:
    def test_upgrade_adds_the_columns_and_moves_the_approvals(self, legacy_db: sa.Engine) -> None:
        before = _shape(legacy_db)
        assert before["approvals_table"] and not before["columns"] & _NEW_COLUMNS

        _migrate(legacy_db, "upgrade")

        shape = _shape(legacy_db)
        assert shape["columns"] == before["columns"] | _NEW_COLUMNS
        assert shape["nullable"] >= _NEW_COLUMNS
        assert shape["foreign_keys"] == before["foreign_keys"]
        assert _INDEX in shape["indexes"]
        assert not shape["approvals_table"]
        assert _run(legacy_db) == _RUN_ROW
        assert _copied(legacy_db) == _COPIED_ROWS
        with legacy_db.connect() as conn:
            rows = conn.execute(
                text(f"SELECT suspend_state, waiting_on, worker_id, heartbeat_at FROM {_RUNS}")
            ).all()
            assert [tuple(r) for r in rows] == [(None, None, None, None)]

    def test_upgrade_twice_changes_nothing_more(self, legacy_db: sa.Engine) -> None:
        _migrate(legacy_db, "upgrade")
        once = _shape(legacy_db)

        _migrate(legacy_db, "upgrade")

        assert _shape(legacy_db) == once
        assert _run(legacy_db) == _RUN_ROW
        assert _copied(legacy_db) == _COPIED_ROWS

    def test_down_then_up_again(self, legacy_db: sa.Engine) -> None:
        before = _shape(legacy_db)
        _migrate(legacy_db, "upgrade")
        upgraded = _shape(legacy_db)

        _migrate(legacy_db, "downgrade")
        assert _shape(legacy_db) == before
        assert _run(legacy_db) == _RUN_ROW
        with legacy_db.connect() as conn:
            # Recreated empty; the history stays in confirmation_requests.
            assert conn.execute(text("SELECT COUNT(*) FROM workflow_approvals")).scalar() == 0
        _migrate(legacy_db, "downgrade")  # nothing left to remove
        assert _shape(legacy_db) == before

        _migrate(legacy_db, "upgrade")
        assert _shape(legacy_db) == upgraded
        assert _copied(legacy_db) == _COPIED_ROWS

    def test_a_waiting_run_fits_after_upgrade(self, legacy_db: sa.Engine) -> None:
        _migrate(legacy_db, "upgrade")
        with Session(legacy_db) as session:
            run = session.get(WorkflowRun, "run-1")
            assert run is not None
            run.status = "waiting"
            run.suspend_state = {"version": 1, "sealed": "x"}
            run.waiting_on = {"human_1": {"confirmation_id": "c"}}
            run.worker_id = "host-1-abc"
            session.commit()
        with Session(legacy_db) as session:
            run = session.get(WorkflowRun, "run-1")
            assert run is not None
            assert run.waiting_on["human_1"]["confirmation_id"] == "c"
            assert run.suspend_state == {"version": 1, "sealed": "x"}


def test_the_model_and_the_migration_agree() -> None:
    table = Base.metadata.tables[_RUNS]
    for name in _NEW_COLUMNS:
        assert name in table.c, name
        assert table.c[name].nullable, name
    assert _INDEX in {i.name for i in table.indexes}
    assert "workflow_approvals" not in Base.metadata.tables


def test_it_chains_after_c1p3_and_leaves_one_head() -> None:
    from pathlib import Path

    import fim_one

    script = ScriptDirectory(str(Path(fim_one.__file__).parent / "migrations"))
    assert len(script.get_heads()) == 1
    revision = script.get_revision("t10p5a1b2c3d4")
    assert revision is not None and revision.down_revision == "c1p3a1b2c3d4"
