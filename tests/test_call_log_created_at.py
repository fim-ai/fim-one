"""Every call record has the time it was made.

Migration ``b2d4e6f8a901`` created ``connector_call_logs.created_at``
without the default the model relies on, so on a database built by the
migrations every record was written with an empty ``created_at`` and the
statistics, which read by period, showed no calls at all. The tests that
build their tables with ``create_all`` never saw it: that gives the column
the model's default. These build the database the way a deployment does,
with ``alembic upgrade``.

On SQLite and (with ``FIM_TEST_PG_URL`` pointing at an empty throwaway
database) PostgreSQL. Covers migration ``c2p4a1b2c3d4``: up, again, down
and up.
"""

from __future__ import annotations

import importlib
import os
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import make_url, text
from sqlalchemy.orm import Session

import fim_one
import fim_one.db.models
from fim_one.db.models.connector_call_log import ConnectorCallLog
from fim_one.db.models.conversation import Conversation
from fim_one.db.models.user import User
from fim_one.db.models.workflow import Workflow, WorkflowRun

pytestmark = pytest.mark.timeout(300)

_PG = os.environ.get("FIM_TEST_PG_URL")
_MIGRATION = "fim_one.migrations.versions.c2p4a1b2c3d4_call_log_created_at"
_BEFORE = "c2p3a1b2c3d4"


def _params() -> list[Any]:
    return [
        pytest.param("sqlite", id="sqlite"),
        pytest.param(
            "postgresql",
            id="postgresql",
            marks=pytest.mark.skipif(not _PG, reason="FIM_TEST_PG_URL not set"),
        ),
    ]


@pytest.fixture(params=_params())
def database_url(
    request: pytest.FixtureRequest, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[str]:
    """An empty database, as ``DATABASE_URL`` for the migrations."""
    if request.param == "sqlite":
        url = f"sqlite:///{tmp_path / 'fim.db'}"
        monkeypatch.setenv("DATABASE_URL", url)
        yield url
        return

    assert _PG is not None
    base = make_url(_PG).set(drivername="postgresql+psycopg2")
    schema = f"c2p4_{uuid.uuid4().hex[:12]}"
    admin = sa.create_engine(base)
    with admin.begin() as conn:
        conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    url = base.update_query_dict({"options": f"-csearch_path={schema}"})
    rendered = url.render_as_string(hide_password=False)
    monkeypatch.setenv("DATABASE_URL", rendered)
    try:
        yield rendered
    finally:
        with admin.begin() as conn:
            conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


def _alembic(direction: str, revision: str) -> None:
    config = Config()
    config.set_main_option("script_location", str(Path(fim_one.__file__).parent / "migrations"))
    getattr(command, direction)(config, revision)


def _run_module(engine: sa.Engine, direction: str) -> None:
    module = importlib.import_module(_MIGRATION)
    with engine.connect() as conn:
        context = MigrationContext.configure(conn)
        with context.begin_transaction(), Operations.context(context):
            getattr(module, direction)()
        conn.commit()


def _at(day: str) -> datetime:
    return datetime.fromisoformat(f"{day}T12:00:00+00:00")


def _record(**fields: Any) -> ConnectorCallLog:
    values: dict[str, Any] = {
        "connector_name": "files",
        "action_name": "echo",
        "request_method": "MCP",
        "request_url": "mcp://files/echo",
        "success": True,
        "resource_kind": "mcp",
    }
    values.update(fields)
    return ConnectorCallLog(**values)


def _created_at(engine: sa.Engine) -> dict[str, Any]:
    with engine.connect() as conn:
        return dict(conn.execute(text("SELECT id, created_at FROM connector_call_logs")).all())


def _column(engine: sa.Engine) -> dict[str, Any]:
    columns = sa.inspect(engine).get_columns("connector_call_logs")
    (column,) = [c for c in columns if c["name"] == "created_at"]
    return {"nullable": column["nullable"], "has_default": column.get("default") is not None}


def test_a_database_built_by_the_migrations_dates_every_call_record(database_url: str) -> None:
    _alembic("upgrade", "head")
    engine = sa.create_engine(database_url)
    try:
        # As ``core.assembly.calls`` writes a record: no time given.
        with Session(engine) as session:
            session.add(_record(id="call-1"))
            session.commit()
        assert _created_at(engine)["call-1"] is not None
    finally:
        engine.dispose()


@pytest.fixture()
def before(database_url: str) -> Iterator[sa.Engine]:
    """The schema as it stood before ``c2p4a1b2c3d4``, with undated records."""
    _alembic("upgrade", _BEFORE)
    engine = sa.create_engine(database_url)
    with Session(engine) as session:
        session.add(User(id="u-1", email="ada@x.io", username="ada", password_hash="x"))
        session.flush()
        # Started 1 October, last answered 5 November: its calls were made
        # in between, so 1 October would date them too early.
        session.add(
            Conversation(
                id="conv-1",
                user_id="u-1",
                title="t",
                mode="react",
                created_at=_at("2026-10-01"),
                updated_at=_at("2026-11-05"),
            )
        )
        # Never changed after it was created: no upper bound to go by.
        session.add(
            Conversation(
                id="conv-quiet",
                user_id="u-1",
                title="q",
                mode="react",
                created_at=_at("2026-10-01"),
            )
        )
        session.add(Workflow(id="wf-1", user_id="u-1", name="w", blueprint={}))
        session.flush()
        # Created and started 2 October, finished 3 October.
        session.add(
            WorkflowRun(
                id="run-1",
                workflow_id="wf-1",
                user_id="u-1",
                status="completed",
                blueprint_snapshot={},
                created_at=_at("2026-10-02"),
                started_at=_at("2026-10-02"),
                completed_at=_at("2026-10-03"),
            )
        )
        # Still waiting for a decision: it has not finished.
        session.add(
            WorkflowRun(
                id="run-waiting",
                workflow_id="wf-1",
                user_id="u-1",
                status="waiting",
                blueprint_snapshot={},
                created_at=_at("2026-10-02"),
                started_at=_at("2026-10-02"),
            )
        )
        session.commit()
    with Session(engine) as session:
        session.add(_record(id="in-chat", conversation_id="conv-1"))
        session.add(_record(id="in-quiet-chat", conversation_id="conv-quiet"))
        session.add(_record(id="in-run", conversation_id="conv-1", workflow_run_id="run-1"))
        session.add(_record(id="in-waiting-run", workflow_run_id="run-waiting"))
        session.add(_record(id="gone", conversation_id="conv-deleted"))
        session.commit()
    yield engine
    engine.dispose()


def _day(value: Any) -> str:
    return str(value)[:10]


class TestMigration:
    def test_the_records_were_undated_before(self, before: sa.Engine) -> None:
        assert set(_created_at(before).values()) == {None}
        assert _column(before) == {"nullable": True, "has_default": False}

    def test_upgrade_dates_old_records_and_new_ones(self, before: sa.Engine) -> None:
        _run_module(before, "upgrade")

        dated = _created_at(before)
        # Never earlier than the call: the retention sweep reads by age, and
        # a record dated too early is deleted before its time.
        assert _day(dated["in-run"]) == "2026-10-03"
        assert _day(dated["in-chat"]) == "2026-11-05"
        upgraded = datetime.now(UTC).date().isoformat()
        for unbounded in ("in-quiet-chat", "in-waiting-run", "gone"):
            assert _day(dated[unbounded]) >= upgraded, unbounded
        assert _column(before) == {"nullable": False, "has_default": True}

        with Session(before) as session:
            session.add(_record(id="new"))
            session.commit()
        assert _created_at(before)["new"] is not None

    def test_upgrade_twice_changes_nothing_more(self, before: sa.Engine) -> None:
        _run_module(before, "upgrade")
        once = (_created_at(before), _column(before))
        _run_module(before, "upgrade")
        assert (_created_at(before), _column(before)) == once

    def test_down_then_up_again(self, before: sa.Engine) -> None:
        _alembic("upgrade", "head")
        dated = _created_at(before)

        _alembic("downgrade", _BEFORE)
        assert _column(before) == {"nullable": True, "has_default": False}
        # The times filled in stay.
        assert _created_at(before) == dated

        _alembic("upgrade", "head")
        assert _column(before) == {"nullable": False, "has_default": True}
        assert _created_at(before) == dated


def test_it_is_the_single_head_after_the_usage_ledger() -> None:
    from alembic.script import ScriptDirectory

    script = ScriptDirectory(str(Path(fim_one.__file__).parent / "migrations"))
    assert script.get_heads() == ["c2p4a1b2c3d4"]
    revision = script.get_revision("c2p4a1b2c3d4")
    assert revision is not None and revision.down_revision == _BEFORE
