"""The C2 migrations: up, again, down and up.

On SQLite and (with ``FIM_TEST_PG_URL`` pointing at an empty throwaway
database) on PostgreSQL. Each case starts from the schema as it stood
before the migration, with a row in the table, and checks that the row
comes through every step unchanged.
"""

from __future__ import annotations

import importlib
import os
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy import make_url, text

import fim_one
import fim_one.db.models  # noqa: F401
from fim_one.db.base import Base

_PG = os.environ.get("FIM_TEST_PG_URL")


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
def current_db(request: pytest.FixtureRequest) -> Iterator[sa.Engine]:
    """An empty database with today's schema, in a schema of its own on PostgreSQL."""
    url = make_url(request.param)
    if url.get_backend_name() == "postgresql":
        url = url.set(drivername="postgresql+psycopg2")
    engine = sa.create_engine(url, future=True)
    schema = None
    if engine.dialect.name == "postgresql":
        schema = f"c2_migration_{uuid.uuid4().hex[:12]}"
        with engine.begin() as conn:
            conn.execute(text(f'CREATE SCHEMA "{schema}"'))
        engine.dispose()
        engine = sa.create_engine(
            url, future=True, connect_args={"options": f"-csearch_path={schema}"}
        )
    Base.metadata.create_all(engine)
    yield engine
    if schema is not None:
        with engine.begin() as conn:
            conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
    engine.dispose()


def _migrate(engine: sa.Engine, module_name: str, direction: str) -> None:
    module = importlib.import_module(module_name)
    with engine.connect() as conn:
        with Operations.context(MigrationContext.configure(conn)):
            getattr(module, direction)()
        conn.commit()


def _shape(engine: sa.Engine, table: str) -> dict[str, Any]:
    inspector = sa.inspect(engine)
    columns = {c["name"]: c for c in inspector.get_columns(table)}
    return {
        "columns": set(columns),
        "nullable": {name for name, c in columns.items() if c["nullable"]},
        "lengths": {
            name: getattr(c["type"], "length", None)
            for name, c in columns.items()
            if getattr(c["type"], "length", None)
        },
        "foreign_keys": len(inspector.get_foreign_keys(table)),
        "indexes": {i["name"]: tuple(i["column_names"]) for i in inspector.get_indexes(table)},
    }


def _rows(engine: sa.Engine, sql: str) -> list[tuple[Any, ...]]:
    with engine.connect() as conn:
        return [tuple(r) for r in conn.execute(text(sql))]


def test_the_chain_has_one_head_and_the_c2_revisions_are_on_it() -> None:
    script = ScriptDirectory(str(Path(fim_one.__file__).parent / "migrations"))

    (head,) = script.get_heads()
    chain = [revision.revision for revision in script.iterate_revisions(head, "t10p5a1b2c3d4")]
    assert "c2p0a1b2c3d4" in chain
    revision = script.get_revision("c2p0a1b2c3d4")
    assert revision is not None and revision.down_revision == "t10p5a1b2c3d4"


# ---------------------------------------------------------------------------
# c2p0a1b2c3d4: connector_call_logs.org_id
# ---------------------------------------------------------------------------

_C2P0 = "fim_one.migrations.versions.c2p0a1b2c3d4_call_log_org"
_CALLS = "connector_call_logs"
_CALLS_INDEX = "ix_connector_call_logs_org_id"
_CALL_ROW_SQL = (
    f"SELECT id, connector_name, action_name, user_id, request_method, success FROM {_CALLS}"
)


@pytest.fixture()
def before_c2p0(current_db: sa.Engine) -> sa.Engine:
    with current_db.begin() as conn:
        conn.execute(text(f"DROP INDEX {_CALLS_INDEX}"))
        conn.execute(text(f"ALTER TABLE {_CALLS} DROP COLUMN org_id"))
        conn.execute(
            text(
                f"INSERT INTO {_CALLS} (id, connector_name, action_name, user_id,"
                " request_method, request_url, success)"
                " VALUES ('call-1', 'crm', 'list', 'user-1', 'GET', 'https://c.test/x', TRUE)"
            )
        )
    return current_db


class TestCallLogOrganization:
    def test_upgrade_adds_the_column_and_its_index(self, before_c2p0: sa.Engine) -> None:
        before = _shape(before_c2p0, _CALLS)
        row = _rows(before_c2p0, _CALL_ROW_SQL)
        assert "org_id" not in before["columns"]

        _migrate(before_c2p0, _C2P0, "upgrade")

        shape = _shape(before_c2p0, _CALLS)
        assert shape["columns"] == before["columns"] | {"org_id"}
        assert "org_id" in shape["nullable"]
        assert shape["indexes"][_CALLS_INDEX] == ("org_id",)
        assert shape["foreign_keys"] == 0
        assert _rows(before_c2p0, _CALL_ROW_SQL) == row
        assert _rows(before_c2p0, f"SELECT org_id FROM {_CALLS}") == [(None,)]

    def test_upgrade_twice_changes_nothing_more(self, before_c2p0: sa.Engine) -> None:
        _migrate(before_c2p0, _C2P0, "upgrade")
        once = _shape(before_c2p0, _CALLS)

        _migrate(before_c2p0, _C2P0, "upgrade")

        assert _shape(before_c2p0, _CALLS) == once

    def test_down_then_up_again(self, before_c2p0: sa.Engine) -> None:
        before = _shape(before_c2p0, _CALLS)
        row = _rows(before_c2p0, _CALL_ROW_SQL)
        _migrate(before_c2p0, _C2P0, "upgrade")
        upgraded = _shape(before_c2p0, _CALLS)

        _migrate(before_c2p0, _C2P0, "downgrade")
        assert _shape(before_c2p0, _CALLS) == before
        _migrate(before_c2p0, _C2P0, "downgrade")  # again: nothing left to drop
        assert _shape(before_c2p0, _CALLS) == before
        assert _rows(before_c2p0, _CALL_ROW_SQL) == row

        _migrate(before_c2p0, _C2P0, "upgrade")
        assert _shape(before_c2p0, _CALLS) == upgraded
        assert _rows(before_c2p0, _CALL_ROW_SQL) == row
