"""Migration ``c1p3a1b2c3d4``: who may decide a confirmation is written on the row.

Up, again, down and up, on SQLite and (with ``FIM_TEST_PG_URL`` pointing at
an empty throwaway database) on PostgreSQL. The migration only adds
nullable columns and one index, so a row written before it must come
through every step unchanged.
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

import fim_one.db.models  # noqa: F401
from fim_one.db.base import Base

_PG = os.environ.get("FIM_TEST_PG_URL")
_MIGRATION = "fim_one.migrations.versions.c1p3a1b2c3d4_confirmation_decider"
_TABLE = "confirmation_requests"
_INDEX = "ix_confirmation_requests_workflow_run_id"
_NEW_COLUMNS = {
    "eligible_scope",
    "eligible_user_id",
    "decided_by_user_id",
    "decision_note",
    "expires_at",
    "conversation_id",
    "workflow_run_id",
    "node_id",
    "title",
    "description",
}


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
    """``confirmation_requests`` as it stood before the migration, with one row."""
    url = make_url(request.param)
    if url.get_backend_name() == "postgresql":
        url = url.set(drivername="postgresql+psycopg2")
    engine = sa.create_engine(url, future=True)
    schema = None
    if engine.dialect.name == "postgresql":
        schema = f"c1p3_migration_{uuid.uuid4().hex[:12]}"
        with engine.begin() as conn:
            conn.execute(text(f'CREATE SCHEMA "{schema}"'))
        engine.dispose()
        engine = sa.create_engine(
            url, future=True, connect_args={"options": f"-csearch_path={schema}"}
        )

    with engine.begin() as conn:
        conn.execute(
            text(
                f"""
                CREATE TABLE {_TABLE} (
                    id VARCHAR(36) PRIMARY KEY,
                    tool_call_id VARCHAR(128),
                    agent_id VARCHAR(36),
                    user_id VARCHAR(36),
                    mode VARCHAR(20) NOT NULL DEFAULT 'channel',
                    kind VARCHAR(30) NOT NULL DEFAULT 'confirmation',
                    approver_user_id VARCHAR(36),
                    org_id VARCHAR(36),
                    channel_id VARCHAR(36),
                    status VARCHAR(20) NOT NULL DEFAULT 'pending',
                    payload TEXT,
                    response_payload TEXT,
                    responded_at TIMESTAMP,
                    responded_by_open_id VARCHAR(128),
                    created_at TIMESTAMP,
                    updated_at TIMESTAMP
                )
                """
            )
        )
        conn.execute(
            text(
                f"INSERT INTO {_TABLE} (id, agent_id, user_id, mode, status)"
                " VALUES ('old-1', 'agent-1', 'user-1', 'inline', 'approved')"
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
    columns = {c["name"]: c for c in inspector.get_columns(_TABLE)}
    return {
        "columns": set(columns),
        "nullable": {name for name, c in columns.items() if c["nullable"]},
        "foreign_keys": len(inspector.get_foreign_keys(_TABLE)),
        "indexes": {i["name"] for i in inspector.get_indexes(_TABLE)},
    }


def _old_row(engine: sa.Engine) -> list[tuple[Any, ...]]:
    with engine.connect() as conn:
        return [
            tuple(r)
            for r in conn.execute(
                text(f"SELECT id, agent_id, user_id, mode, status FROM {_TABLE} ORDER BY id")
            )
        ]


class TestMigration:
    def test_upgrade_adds_the_columns_and_keeps_old_rows(self, legacy_db: sa.Engine) -> None:
        before = _shape(legacy_db)

        _migrate(legacy_db, "upgrade")

        shape = _shape(legacy_db)
        assert shape["columns"] == before["columns"] | _NEW_COLUMNS
        assert shape["nullable"] >= _NEW_COLUMNS
        assert shape["foreign_keys"] == before["foreign_keys"]
        assert _INDEX in shape["indexes"]
        assert _old_row(legacy_db) == [("old-1", "agent-1", "user-1", "inline", "approved")]
        with legacy_db.connect() as conn:
            assert conn.execute(
                text(f"SELECT eligible_scope, eligible_user_id, expires_at FROM {_TABLE}")
            ).all() == [(None, None, None)]

    def test_upgrade_twice_changes_nothing_more(self, legacy_db: sa.Engine) -> None:
        _migrate(legacy_db, "upgrade")
        once = _shape(legacy_db)

        _migrate(legacy_db, "upgrade")

        assert _shape(legacy_db) == once
        assert _old_row(legacy_db) == [("old-1", "agent-1", "user-1", "inline", "approved")]

    def test_down_then_up_again(self, legacy_db: sa.Engine) -> None:
        before = _shape(legacy_db)
        _migrate(legacy_db, "upgrade")
        upgraded = _shape(legacy_db)

        _migrate(legacy_db, "downgrade")
        assert _shape(legacy_db) == before
        assert _old_row(legacy_db) == [("old-1", "agent-1", "user-1", "inline", "approved")]
        _migrate(legacy_db, "downgrade")  # nothing left to remove
        assert _shape(legacy_db) == before

        _migrate(legacy_db, "upgrade")
        assert _shape(legacy_db) == upgraded

    def test_a_new_row_fits_after_upgrade(self, legacy_db: sa.Engine) -> None:
        _migrate(legacy_db, "upgrade")
        with legacy_db.begin() as conn:
            conn.execute(
                text(
                    f"INSERT INTO {_TABLE} (id, mode, kind, status, eligible_scope,"
                    " eligible_user_id, workflow_run_id, node_id, title, expires_at)"
                    " VALUES ('new-1', 'inline', 'workflow_approval', 'pending', 'assignee',"
                    " 'user-2', 'run-1', 'human_1', 'Approve payment', CURRENT_TIMESTAMP)"
                )
            )
        with legacy_db.connect() as conn:
            assert conn.execute(
                text(f"SELECT eligible_scope, title FROM {_TABLE} WHERE id = 'new-1'")
            ).one() == ("assignee", "Approve payment")


def test_the_model_and_the_migration_agree() -> None:
    """Every column the migration adds is on the ORM model, and nullable there."""
    table = Base.metadata.tables[_TABLE]
    for name in _NEW_COLUMNS:
        assert name in table.c, name
        assert table.c[name].nullable, name
        assert not table.c[name].foreign_keys, name
    assert _INDEX in {i.name for i in table.indexes}


def test_it_chains_after_c1p2_and_leaves_one_head() -> None:
    from pathlib import Path

    import fim_one

    script = ScriptDirectory(str(Path(fim_one.__file__).parent / "migrations"))
    assert len(script.get_heads()) == 1
    revision = script.get_revision("c1p3a1b2c3d4")
    assert revision is not None and revision.down_revision == "c1p2a1b2c3d4"
