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
        context = MigrationContext.configure(conn)
        # As ``env.py`` runs them: inside the migration context's own
        # transaction, which an autocommit block steps out of and back into.
        with context.begin_transaction(), Operations.context(context):
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
    # Newest first: the audit fields come after the call log's organization.
    assert chain.index("c2p1a1b2c3d4") < chain.index("c2p0a1b2c3d4")
    revision = script.get_revision("c2p0a1b2c3d4")
    assert revision is not None and revision.down_revision == "t10p5a1b2c3d4"
    revision = script.get_revision("c2p1a1b2c3d4")
    assert revision is not None and revision.down_revision == "c2p0a1b2c3d4"


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


# ---------------------------------------------------------------------------
# c2p1a1b2c3d4: audit_logs fields, login_history.method
# ---------------------------------------------------------------------------

_C2P1 = "fim_one.migrations.versions.c2p1a1b2c3d4_audit_fields"
_AUDIT = "audit_logs"
_LOGINS = "login_history"
_AUDIT_COLUMNS = {
    "actor_kind", "auth_method", "credential_ref", "org_id", "outcome", "changes",
    "ip_address", "user_agent", "request_id", "conversation_id", "workflow_run_id", "agent_id",
}  # fmt: skip
_AUDIT_INDEXES = {
    "ix_audit_logs_created_at": ("created_at",),
    "ix_audit_logs_org_id_created_at": ("org_id", "created_at"),
    "ix_audit_logs_target": ("target_type", "target_id"),
}
_AUDIT_ROW_SQL = (
    "SELECT id, admin_id, admin_username, action, target_type, target_id, target_label, detail"
    f" FROM {_AUDIT} ORDER BY id"
)
_LOGIN_ROW_SQL = f"SELECT id, user_id, email, ip_address, success FROM {_LOGINS}"
_AUDIT_ROWS = [("audit-1", "user-1", "root", "user.create", "user", "user-2", "bob", "created")]
_LOGIN_ROWS = [("login-1", "user-1", "root@x.io", "198.51.100.4", True)]


@pytest.fixture()
def before_c2p1(current_db: sa.Engine) -> sa.Engine:
    """``audit_logs`` and ``login_history`` as they were, one row in each."""
    with current_db.begin() as conn:
        for index in _AUDIT_INDEXES:
            conn.execute(text(f"DROP INDEX {index}"))
        for column in sorted(_AUDIT_COLUMNS):
            conn.execute(text(f"ALTER TABLE {_AUDIT} DROP COLUMN {column}"))
        conn.execute(text(f"ALTER TABLE {_LOGINS} DROP COLUMN method"))
    if current_db.dialect.name == "postgresql":
        with current_db.begin() as conn:
            conn.execute(
                text(f"ALTER TABLE {_AUDIT} ALTER COLUMN admin_username TYPE VARCHAR(50)")
            )
    else:
        with current_db.connect() as conn:
            with Operations.context(MigrationContext.configure(conn)) as ops:
                with ops.batch_alter_table(_AUDIT) as batch:
                    batch.alter_column(
                        "admin_username", existing_type=sa.String(255), type_=sa.String(50)
                    )
            conn.commit()
    with current_db.begin() as conn:
        conn.execute(
            text(
                f"INSERT INTO {_AUDIT} (id, admin_id, admin_username, action, target_type,"
                " target_id, target_label, detail) VALUES ('audit-1', 'user-1', 'root',"
                " 'user.create', 'user', 'user-2', 'bob', 'created')"
            )
        )
        conn.execute(
            text(
                f"INSERT INTO {_LOGINS} (id, user_id, email, ip_address, success)"
                " VALUES ('login-1', 'user-1', 'root@x.io', '198.51.100.4', TRUE)"
            )
        )
    return current_db


class TestAuditFields:
    def test_upgrade_adds_the_columns_widens_the_name_and_indexes(
        self, before_c2p1: sa.Engine
    ) -> None:
        before = _shape(before_c2p1, _AUDIT)
        logins_before = _shape(before_c2p1, _LOGINS)
        assert not before["columns"] & _AUDIT_COLUMNS
        assert before["lengths"]["admin_username"] == 50

        _migrate(before_c2p1, _C2P1, "upgrade")

        shape = _shape(before_c2p1, _AUDIT)
        assert shape["columns"] == before["columns"] | _AUDIT_COLUMNS
        assert shape["nullable"] >= _AUDIT_COLUMNS - {"outcome"}
        assert "outcome" not in shape["nullable"]
        assert shape["lengths"]["admin_username"] == 255
        assert shape["foreign_keys"] == 0
        for name, columns in _AUDIT_INDEXES.items():
            assert shape["indexes"][name] == columns
        # The indexes the table already had are still there.
        assert set(before["indexes"]) <= set(shape["indexes"])
        assert _shape(before_c2p1, _LOGINS)["columns"] == logins_before["columns"] | {"method"}
        # The rows written before: unchanged, and read as a successful action.
        assert _rows(before_c2p1, _AUDIT_ROW_SQL) == _AUDIT_ROWS
        assert _rows(before_c2p1, f"SELECT outcome, actor_kind, ip_address FROM {_AUDIT}") == [
            ("success", None, None)
        ]
        assert _rows(before_c2p1, _LOGIN_ROW_SQL) == _LOGIN_ROWS
        assert _rows(before_c2p1, f"SELECT method FROM {_LOGINS}") == [(None,)]

    def test_upgrade_twice_changes_nothing_more(self, before_c2p1: sa.Engine) -> None:
        _migrate(before_c2p1, _C2P1, "upgrade")
        once = (_shape(before_c2p1, _AUDIT), _shape(before_c2p1, _LOGINS))

        _migrate(before_c2p1, _C2P1, "upgrade")

        assert (_shape(before_c2p1, _AUDIT), _shape(before_c2p1, _LOGINS)) == once
        assert _rows(before_c2p1, _AUDIT_ROW_SQL) == _AUDIT_ROWS

    def test_a_long_operator_name_fits_after_the_upgrade(self, before_c2p1: sa.Engine) -> None:
        _migrate(before_c2p1, _C2P1, "upgrade")
        long_email = "a" * 100 + "@example.test"

        with before_c2p1.begin() as conn:
            conn.execute(
                text(
                    f"INSERT INTO {_AUDIT} (id, admin_username, action)"
                    " VALUES ('audit-2', :name, 'user.create')"
                ),
                {"name": long_email},
            )

        stored = _rows(before_c2p1, f"SELECT admin_username FROM {_AUDIT} WHERE id = 'audit-2'")
        assert stored == [(long_email,)]

    def test_down_then_up_again(self, before_c2p1: sa.Engine) -> None:
        before = (_shape(before_c2p1, _AUDIT), _shape(before_c2p1, _LOGINS))
        _migrate(before_c2p1, _C2P1, "upgrade")
        upgraded = (_shape(before_c2p1, _AUDIT), _shape(before_c2p1, _LOGINS))
        with before_c2p1.begin() as conn:
            conn.execute(
                text(
                    f"INSERT INTO {_AUDIT} (id, admin_username, action, outcome, ip_address)"
                    " VALUES ('audit-2', :name, 'user.create', 'denied', '203.0.113.7')"
                ),
                {"name": "b" * 120},
            )

        _migrate(before_c2p1, _C2P1, "downgrade")
        assert (_shape(before_c2p1, _AUDIT), _shape(before_c2p1, _LOGINS)) == before
        _migrate(before_c2p1, _C2P1, "downgrade")  # again: nothing left to undo
        assert (_shape(before_c2p1, _AUDIT), _shape(before_c2p1, _LOGINS)) == before
        # Rows survive; a name that no longer fits is cut, not lost.
        assert _rows(before_c2p1, _AUDIT_ROW_SQL) == [
            *_AUDIT_ROWS,
            ("audit-2", None, "b" * 50, "user.create", None, None, None, None),
        ]
        assert _rows(before_c2p1, _LOGIN_ROW_SQL) == _LOGIN_ROWS

        _migrate(before_c2p1, _C2P1, "upgrade")
        assert (_shape(before_c2p1, _AUDIT), _shape(before_c2p1, _LOGINS)) == upgraded
        assert len(_rows(before_c2p1, _AUDIT_ROW_SQL)) == 2


class TestAuditIndexesOnPostgreSQL:
    """The three indexes were built with a plain CREATE INDEX, which blocks
    every insert into ``audit_logs`` while it scans the table."""

    def test_the_indexes_are_built_concurrently(
        self, before_c2p1: sa.Engine, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        if before_c2p1.dialect.name != "postgresql":
            pytest.skip("CONCURRENTLY is PostgreSQL's")
        statements: list[str] = []

        @sa.event.listens_for(before_c2p1, "before_cursor_execute")
        def _seen(_c: Any, _cur: Any, statement: str, *_rest: Any) -> None:
            statements.append(statement)

        _migrate(before_c2p1, _C2P1, "upgrade")

        builds = [s for s in statements if s.lstrip().upper().startswith("CREATE INDEX")]
        assert len(builds) == 3 and all("CONCURRENTLY" in s.upper() for s in builds)
        assert set(_AUDIT_INDEXES) <= set(_shape(before_c2p1, _AUDIT)["indexes"])

    def test_an_invalid_index_left_by_a_failed_build_is_rebuilt(
        self, before_c2p1: sa.Engine
    ) -> None:
        if before_c2p1.dialect.name != "postgresql":
            pytest.skip("CONCURRENTLY is PostgreSQL's")
        _migrate(before_c2p1, _C2P1, "upgrade")
        with before_c2p1.begin() as conn:
            conn.execute(
                text(
                    "UPDATE pg_index SET indisvalid = false WHERE indexrelid ="
                    " (SELECT c.oid FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace"
                    "  WHERE c.relname = 'ix_audit_logs_target' AND n.nspname = current_schema())"
                )
            )

        _migrate(before_c2p1, _C2P1, "upgrade")

        with before_c2p1.connect() as conn:
            valid = conn.execute(
                text(
                    "SELECT i.indisvalid FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid"
                    " JOIN pg_namespace n ON n.oid = c.relnamespace"
                    " WHERE c.relname = 'ix_audit_logs_target' AND n.nspname = current_schema()"
                )
            ).scalar_one()
        assert valid is True


class TestDowngradeTouchesOnlyLongNames:
    def test_only_rows_with_a_long_operator_name_are_rewritten(
        self, before_c2p1: sa.Engine
    ) -> None:
        _migrate(before_c2p1, _C2P1, "upgrade")
        statements: list[str] = []

        @sa.event.listens_for(before_c2p1, "before_cursor_execute")
        def _seen(_c: Any, _cur: Any, statement: str, *_rest: Any) -> None:
            statements.append(statement)

        _migrate(before_c2p1, _C2P1, "downgrade")

        (update,) = [s for s in statements if s.lstrip().upper().startswith("UPDATE AUDIT_LOGS")]
        assert "WHERE LENGTH(admin_username) > 50" in update
