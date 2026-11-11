"""The usage ledger's migration: the table, and the totals carried over into it.

``c2p3a1b2c3d4`` creates ``usage_ledger`` and backfills it from
``conversations.total_tokens`` and ``workflow_runs.total_tokens``, so that
each user's "used" is the same right after the migration as right before.
On SQLite and (with ``FIM_TEST_PG_URL`` pointing at an empty throwaway
database) on PostgreSQL.
"""

from __future__ import annotations

import importlib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.orm import Session

import fim_one
from fim_one.db.models import Conversation, User
from fim_one.db.models.workflow import Workflow, WorkflowRun
from tests.test_audit_migrations import _migrate, _shape, current_db  # noqa: F401

_C2P3 = "fim_one.migrations.versions.c2p3a1b2c3d4_usage_ledger"
_LEDGER = "usage_ledger"
NOW = datetime(2026, 11, 11, 12, 0, tzinfo=UTC)
THIS_MONTH = datetime(2026, 11, 1, tzinfo=UTC)

# What the quota check and the usage card summed before the ledger
# (``web/services/quota_backend.window_usage``), per user.
_OLD_SOURCE_SQL = """
SELECT user_id, SUM(total_tokens) FROM (
    SELECT user_id, total_tokens FROM conversations WHERE created_at >= :since
    UNION ALL
    SELECT user_id, total_tokens FROM workflow_runs WHERE created_at >= :since
) AS old_source GROUP BY user_id HAVING SUM(total_tokens) > 0
"""
_LEDGER_SQL = (
    f"SELECT user_id, SUM(total_tokens) FROM {_LEDGER} WHERE created_at >= :since GROUP BY user_id"
)


def _module() -> Any:
    return importlib.import_module(_C2P3)


def _sums(engine: sa.Engine, sql: str, since: datetime) -> dict[str, int]:
    with engine.connect() as conn:
        return {row[0]: int(row[1]) for row in conn.execute(text(sql), {"since": since})}


def _ledger_rows(engine: sa.Engine) -> list[dict[str, Any]]:
    with engine.connect() as conn:
        return [
            dict(row._mapping)
            for row in conn.execute(text(f"SELECT * FROM {_LEDGER} ORDER BY source_id, entry"))
        ]


def _conversation(user: str, key: str, total: int, *, fast: int = 0, days_ago: int = 1,
                  **fields: Any) -> Conversation:
    return Conversation(
        id=f"conv-{key}",
        user_id=user,
        title=key,
        mode=fields.pop("mode", "react"),
        total_tokens=total,
        fast_llm_tokens=fast,
        created_at=NOW - timedelta(days=days_ago),
        **fields,
    )


def _run(user: str, key: str, total: int, *, mode: str | None = "manual",
         days_ago: int = 1) -> WorkflowRun:
    return WorkflowRun(
        id=f"run-{key}",
        workflow_id="wf-1",
        user_id=user,
        blueprint_snapshot={},
        status="completed",
        mode=mode,
        total_tokens=total,
        created_at=NOW - timedelta(days=days_ago),
    )


@pytest.fixture()
def before(current_db: sa.Engine) -> sa.Engine:  # noqa: F811
    """The schema before the migration, with usage on conversations and runs."""
    with current_db.begin() as conn:
        conn.execute(text(f"DROP TABLE {_LEDGER}"))
    with Session(current_db) as db:
        for name in ("alice", "bob", "carol"):
            db.add(User(id=name, username=name, email=f"{name}@example.test"))
        db.flush()
        db.add(Workflow(id="wf-1", user_id="alice", name="wf", blueprint={}))
        db.flush()
        db.add_all(
            [
                # This month.
                _conversation("alice", "a1", 1000, fast=120, model_name="gpt-main", days_ago=2),
                _conversation("alice", "a2", 40, fast=40, mode="dag"),
                _conversation("alice", "a3", 0),
                # Last month: outside the window by the old rule, and so here.
                _conversation("alice", "old", 5000, fast=10, days_ago=40),
                _conversation("bob", "b1", 77),
                # The fast figure can exceed the total it is part of.
                _conversation("bob", "b2", 30, fast=90),
                _run("alice", "r1", 300),
                _run("bob", "r2", 9, mode="test_node"),
                _run("bob", "r3", 11, mode=None, days_ago=45),
                _run("carol", "r4", 0, mode="trigger"),
            ]
        )
        db.commit()
    return current_db


class TestTheChain:
    def test_it_is_on_the_single_head_after_the_audit_fields(self) -> None:
        script = ScriptDirectory(str(Path(fim_one.__file__).parent / "migrations"))

        (head,) = script.get_heads()
        assert "c2p3a1b2c3d4" in [r.revision for r in script.iterate_revisions(head, "base")]
        revision = script.get_revision("c2p3a1b2c3d4")
        assert revision is not None and revision.down_revision == "c2p1a1b2c3d4"


class TestTheTable:
    def test_upgrade_creates_it_with_its_indexes_and_no_foreign_key(
        self, before: sa.Engine
    ) -> None:
        _migrate(before, _C2P3, "upgrade")

        shape = _shape(before, _LEDGER)
        assert shape["foreign_keys"] == 0
        assert shape["indexes"] == {
            "ix_usage_ledger_user_created": ("user_id", "created_at"),
            "ix_usage_ledger_org_created": ("org_id", "created_at"),
            "ix_usage_ledger_source": ("source_type", "source_id"),
        }
        assert {"total_tokens", "source_type", "entry", "kind", "unit", "estimated"} <= (
            shape["columns"] - shape["nullable"]
        )

    def test_it_matches_the_model(self, before: sa.Engine, current_db: sa.Engine) -> None:  # noqa: F811
        """What the migration builds is what ``create_all`` builds from the model."""
        from fim_one.db.models.usage_ledger import UsageLedger

        _migrate(before, _C2P3, "upgrade")
        migrated = _shape(before, _LEDGER)
        with before.begin() as conn:
            conn.execute(text(f"DROP TABLE {_LEDGER}"))
        UsageLedger.__table__.create(before)  # type: ignore[attr-defined]

        assert _shape(before, _LEDGER) == migrated

    def test_down_up_down(self, before: sa.Engine) -> None:
        _migrate(before, _C2P3, "upgrade")
        _migrate(before, _C2P3, "downgrade")
        assert _LEDGER not in sa.inspect(before).get_table_names()
        _migrate(before, _C2P3, "downgrade")  # nothing to drop: no error
        _migrate(before, _C2P3, "upgrade")
        assert len(_ledger_rows(before)) == 10
        _migrate(before, _C2P3, "downgrade")
        assert _LEDGER not in sa.inspect(before).get_table_names()


class TestTheBackfill:
    def test_every_users_used_is_what_it_was(self, before: sa.Engine) -> None:
        """In the current window, in an older one and over all time."""
        windows = [THIS_MONTH, NOW - timedelta(days=60), datetime(2000, 1, 1, tzinfo=UTC)]
        was = [_sums(before, _OLD_SOURCE_SQL, since) for since in windows]

        _migrate(before, _C2P3, "upgrade")

        assert [_sums(before, _LEDGER_SQL, since) for since in windows] == was
        assert was[0] == {"alice": 1340, "bob": 116}
        assert was[2] == {"alice": 6340, "bob": 127}

    def test_what_a_conversation_and_a_run_become(self, before: sa.Engine) -> None:
        _migrate(before, _C2P3, "upgrade")

        rows = {(r["source_id"], r["entry"]): r for r in _ledger_rows(before)}
        assert set(rows) == {
            ("conv-a1", "react"), ("conv-a1", "background"),
            ("conv-a2", "background"),
            ("conv-old", "react"), ("conv-old", "background"),
            ("conv-b1", "react"),
            ("conv-b2", "background"),
            # A run from before runs recorded their mode is a manual one.
            ("run-r1", "manual"), ("run-r2", "test_node"), ("run-r3", "manual"),
        }  # fmt: skip
        main, fast = rows[("conv-a1", "react")], rows[("conv-a1", "background")]
        assert (main["total_tokens"], main["model"]) == (880, "gpt-main")
        assert (fast["total_tokens"], fast["model"]) == (120, None)
        # All of it was the fast model's: one row, and no more than the total.
        assert rows[("conv-a2", "background")]["total_tokens"] == 40
        assert rows[("conv-b2", "background")]["total_tokens"] == 30
        for row in rows.values():
            assert (row["kind"], row["unit"], bool(row["estimated"])) == (
                "backfill", "llm_token", False,
            )  # fmt: skip
            assert row["prompt_tokens"] is None and row["org_id"] is None
            assert row["total_tokens"] > 0
        run = rows[("run-r1", "manual")]
        assert (run["source_type"], run["workflow_id"], run["user_id"]) == (
            "workflow_run", "wf-1", "alice",
        )  # fmt: skip

    def test_a_row_takes_the_time_its_source_was_created(self, before: sa.Engine) -> None:
        _migrate(before, _C2P3, "upgrade")

        with before.connect() as conn:
            pairs = conn.execute(
                text(
                    f"SELECT l.created_at, c.created_at FROM {_LEDGER} l"
                    " JOIN conversations c ON c.id = l.source_id"
                )
            ).all()
        assert len(pairs) == 7
        assert all(str(ledger)[:19] == str(source)[:19] for ledger, source in pairs)

    def test_running_it_again_writes_nothing_twice(self, before: sa.Engine) -> None:
        _migrate(before, _C2P3, "upgrade")
        first = _ledger_rows(before)

        _migrate(before, _C2P3, "upgrade")

        assert _ledger_rows(before) == first

    def test_it_goes_through_the_tables_in_batches(
        self, before: sa.Engine, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        module = _module()
        monkeypatch.setattr(module, "BATCH", 2)
        sizes: list[int] = []
        upsert = module._upsert

        def _counting(bind: Any, rows: list[dict[str, Any]]) -> None:
            sizes.append(len(rows))
            upsert(bind, rows)

        monkeypatch.setattr(module, "_upsert", _counting)

        _migrate(before, _C2P3, "upgrade")

        # Two source rows a batch, at most two ledger rows from each.
        assert len(sizes) >= 4 and max(sizes) <= 4
        assert _sums(before, _LEDGER_SQL, datetime(2000, 1, 1, tzinfo=UTC)) == {
            "alice": 6340,
            "bob": 127,
        }

    def test_interrupted_it_is_finished_by_running_it_again(
        self, before: sa.Engine, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Killed after some batches; meanwhile a conversation grew and one was deleted."""
        module = _module()
        monkeypatch.setattr(module, "BATCH", 2)
        upsert = module._upsert
        seen = 0

        def _dies(bind: Any, rows: list[dict[str, Any]]) -> None:
            nonlocal seen
            seen += 1
            if seen == 3:
                raise KeyboardInterrupt("killed")
            upsert(bind, rows)

        monkeypatch.setattr(module, "_upsert", _dies)
        with pytest.raises(KeyboardInterrupt):
            _migrate(before, _C2P3, "upgrade")
        monkeypatch.setattr(module, "_upsert", upsert)
        if before.dialect.name == "postgresql":
            # Batches committed on their own: what was written stayed.
            assert len(_ledger_rows(before)) > 0
        # (SQLite rolled the whole run back, table included.)
        with before.begin() as conn:
            conn.execute(text("UPDATE conversations SET total_tokens = 1500 WHERE id = 'conv-a1'"))
            conn.execute(text("DELETE FROM conversations WHERE id = 'conv-a2'"))

        _migrate(before, _C2P3, "upgrade")

        everything = datetime(2000, 1, 1, tzinfo=UTC)
        assert _sums(before, _LEDGER_SQL, everything) == _sums(
            before, _OLD_SOURCE_SQL, everything
        )
        assert _sums(before, _LEDGER_SQL, everything)["alice"] == 6340 + 500 - 40
        assert not [r for r in _ledger_rows(before) if r["source_id"] == "conv-a2"]

    def test_a_backfill_that_does_not_add_up_fails_the_migration(
        self, before: sa.Engine, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        module = _module()
        rows_of = module._run_rows

        def _short(source: Any) -> Any:
            for row in rows_of(source):
                yield {**row, "total_tokens": row["total_tokens"] - 1}

        monkeypatch.setattr(module, "_run_rows", _short)

        with pytest.raises(RuntimeError, match="does not match its sources"):
            _migrate(before, _C2P3, "upgrade")

    def test_the_reconciliation_query_names_the_user_that_differs(self, before: sa.Engine) -> None:
        _migrate(before, _C2P3, "upgrade")
        module = _module()
        with before.connect() as conn:
            assert conn.execute(text(module.RECONCILE_SQL)).all() == []
        with before.begin() as conn:
            conn.execute(text("UPDATE workflow_runs SET total_tokens = 12 WHERE id = 'run-r2'"))

        with before.connect() as conn:
            (row,) = conn.execute(text(module.RECONCILE_SQL)).all()
        assert (row.user_id, row.source_type, int(row.source_total), int(row.ledger_total)) == (
            "bob", "workflow_run", 23, 20,
        )  # fmt: skip

    def test_usage_metered_since_is_left_alone(self, before: sa.Engine) -> None:
        """Rows the application wrote are not the backfill's to touch."""
        _migrate(before, _C2P3, "upgrade")
        with before.begin() as conn:
            conn.execute(
                text(
                    f"INSERT INTO {_LEDGER} (id, user_id, source_type, source_id, entry,"
                    " total_tokens) VALUES ('live-1', 'alice', 'conversation', 'gone', 'chat', 55)"
                )
            )

        _migrate(before, _C2P3, "upgrade")

        with before.connect() as conn:
            assert conn.execute(
                text(f"SELECT kind, total_tokens FROM {_LEDGER} WHERE id = 'live-1'")
            ).one() == ("usage", 55)


def _write_meanwhile(engine: sa.Engine, bind: Any, sql: str) -> None:
    """A write by an instance of the previous release, committed while the migration runs.

    On PostgreSQL from a connection of its own; SQLite's in-memory database
    has one connection, and the migration's reads see what it writes.
    """
    if engine.dialect.name == "postgresql":
        with engine.begin() as conn:
            conn.execute(text(sql))
    else:
        bind.execute(text(sql))


class TestWithTheSourcesStillBeingWritten:
    """An instance of the previous release still serving adds to the totals as it runs."""

    def _watch(
        self, module: Any, monkeypatch: pytest.MonkeyPatch, write: Any
    ) -> list[tuple[bool, list[str]]]:
        """Record each upsert (after the first check?, its sources) and write after each."""
        upserts: list[tuple[bool, list[str]]] = []
        checked = False
        upsert, differences = module._upsert, module._differences

        def _upserting(bind: Any, rows: list[dict[str, Any]]) -> None:
            upsert(bind, rows)
            upserts.append((checked, [row["source_id"] for row in rows]))
            write(bind, len(upserts))

        def _checking(bind: Any) -> list[Any]:
            nonlocal checked
            checked = True
            return differences(bind)

        monkeypatch.setattr(module, "_upsert", _upserting)
        monkeypatch.setattr(module, "_differences", _checking)
        return upserts

    def test_only_the_users_that_differ_are_carried_over_again(
        self, before: sa.Engine, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        module = _module()
        monkeypatch.setattr(module, "BATCH", 2)

        def _grows_once(bind: Any, count: int) -> None:
            if count == 1:  # conv-a1 has just been carried over
                _write_meanwhile(
                    before, bind,
                    "UPDATE conversations SET total_tokens = 1500 WHERE id = 'conv-a1'",
                )  # fmt: skip

        upserts = self._watch(module, monkeypatch, _grows_once)

        _migrate(before, _C2P3, "upgrade")

        everything = datetime(2000, 1, 1, tzinfo=UTC)
        assert _sums(before, _LEDGER_SQL, everything) == _sums(
            before, _OLD_SOURCE_SQL, everything
        )
        again = {source for checked, sources in upserts if checked for source in sources}
        assert again == {"conv-a1", "conv-a2", "conv-old", "run-r1"}  # alice's, not bob's

    def test_a_writer_on_other_users_does_not_hold_it_up(
        self, before: sa.Engine, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Bob differs once; carol's total keeps growing from the check on.

        Only bob is carried over again and compared. What carol gains after
        the check is the top-up's: the migration finishes, and the
        reconciliation query shows exactly that much for her.
        """
        module = _module()
        monkeypatch.setattr(module, "BATCH", 2)
        writes_after_check = 0

        def _writes(bind: Any, count: int) -> None:
            nonlocal writes_after_check
            if count == 2:  # conv-b1 has just been carried over
                _write_meanwhile(
                    before, bind, "UPDATE conversations SET total_tokens = 80 WHERE id = 'conv-b1'"
                )
            if upserts[-1][0]:  # after the check
                writes_after_check += 1
                _write_meanwhile(
                    before, bind,
                    "UPDATE workflow_runs SET total_tokens = total_tokens + 1 WHERE id = 'run-r4'",
                )  # fmt: skip

        upserts = self._watch(module, monkeypatch, _writes)

        _migrate(before, _C2P3, "upgrade")

        again = {source for checked, sources in upserts if checked for source in sources}
        assert again == {"conv-b1", "conv-b2", "run-r2", "run-r3"}
        assert writes_after_check >= 2
        with before.connect() as conn:
            left = [
                (row.user_id, row.source_type, int(row.source_total), int(row.ledger_total))
                for row in conn.execute(text(module.RECONCILE_SQL))
            ]
        assert left == [("carol", "workflow_run", writes_after_check, 0)]

    def test_a_writer_on_the_same_user_does_not_hold_it_up(
        self, before: sa.Engine, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Bob's run grows after every batch, during his own carry-over too.

        His carry-over and its comparison read one snapshot, so the
        migration finishes; the writes committed after that snapshot began
        are left over, for the top-up.
        """
        if before.dialect.name != "postgresql":
            pytest.skip(
                "SQLite: the migration holds the write lock from its first write to its"
                " commit, so no other connection writes during a carry-over"
            )
        module = _module()

        def _always(bind: Any, count: int) -> None:
            _write_meanwhile(
                before, bind,
                "UPDATE workflow_runs SET total_tokens = total_tokens + 1 WHERE id = 'run-r2'",
            )  # fmt: skip

        upserts = self._watch(module, monkeypatch, _always)

        _migrate(before, _C2P3, "upgrade")

        again = [sources for checked, sources in upserts if checked]
        # Bob's conversations and his runs, once each.
        assert again == [["conv-b1", "conv-b2"], ["run-r2", "run-r3"]]
        with before.connect() as conn:
            left = [
                (row.user_id, row.source_type, int(row.source_total) - int(row.ledger_total))
                for row in conn.execute(text(module.RECONCILE_SQL))
            ]
        # One write after each of his two batches, both after his snapshot.
        assert left == [("bob", "workflow_run", 2)]

    def test_a_carry_over_postgresql_cannot_serialize_is_tried_again(
        self, before: sa.Engine, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Another replica wrote the same rows: the transaction is rolled back and run again."""
        if before.dialect.name != "postgresql":
            pytest.skip("SQLite's carry-over runs in the migration's own transaction")
        module = _module()
        _migrate(before, _C2P3, "upgrade")
        with before.begin() as conn:  # leaves bob a stale row the backfill does not remove
            conn.execute(text("DELETE FROM workflow_runs WHERE id = 'run-r3'"))
        compare = module._differences_of
        calls = 0

        class _ConflictError(Exception):
            pgcode = "40001"

        def _once(bind: Any, user_id: Any) -> list[Any]:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise _ConflictError("could not serialize access due to concurrent update")
            return compare(bind, user_id)

        monkeypatch.setattr(module, "_differences_of", _once)

        _migrate(before, _C2P3, "upgrade")

        assert calls == 2
        with before.connect() as conn:
            assert conn.execute(text(module.RECONCILE_SQL)).all() == []

    def test_a_carry_over_leaves_the_users_metered_rows_alone(
        self, before: sa.Engine, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Bob differs and has a stale backfilled row, a metered row and an adjustment.

        The stale row goes; the other two are not even proposed for removal.
        """
        _migrate(before, _C2P3, "upgrade")
        with before.begin() as conn:
            for row_id, kind in (("live-b", "usage"), ("adj-b", "adjustment")):
                conn.execute(
                    text(
                        f"INSERT INTO {_LEDGER} (id, user_id, source_type, source_id, entry,"
                        " total_tokens, kind) VALUES (:id, 'bob', 'conversation', 'conv-b1',"
                        " 'chat', 7, :kind)"
                    ),
                    {"id": row_id, "kind": kind},
                )
            conn.execute(text("DELETE FROM workflow_runs WHERE id = 'run-r3'"))
            conn.execute(text("UPDATE conversations SET total_tokens = 80 WHERE id = 'conv-b1'"))
        proposed: list[str] = []

        def _deletes(conn: Any, cursor: Any, statement: str, parameters: Any, *_: Any) -> None:
            if statement.lstrip().upper().startswith("DELETE"):
                values = parameters.values() if isinstance(parameters, dict) else parameters
                proposed.extend(str(value) for value in values)

        sa.event.listen(before, "before_cursor_execute", _deletes)
        try:
            _migrate(before, _C2P3, "upgrade")
        finally:
            sa.event.remove(before, "before_cursor_execute", _deletes)

        stale = module_row_id("workflow_run", "run-r3")
        assert proposed == [stale]
        with before.connect() as conn:
            kinds = dict(
                conn.execute(
                    text(f"SELECT id, kind FROM {_LEDGER} WHERE id IN ('live-b', 'adj-b', :stale)"),
                    {"stale": stale},
                ).all()
            )
            assert conn.execute(text(_module().RECONCILE_SQL)).all() == []
        assert kinds == {"live-b": "usage", "adj-b": "adjustment"}

    def test_a_second_run_rewrites_no_row_that_is_up_to_date(self, before: sa.Engine) -> None:
        _migrate(before, _C2P3, "upgrade")
        with before.begin() as conn:
            if before.dialect.name == "postgresql":
                versions = f"SELECT id, xmin::text FROM {_LEDGER} ORDER BY id"
            else:
                conn.execute(text("CREATE TABLE rewrites (id TEXT)"))
                conn.execute(
                    text(
                        f"CREATE TRIGGER count_rewrites AFTER UPDATE ON {_LEDGER}"
                        " BEGIN INSERT INTO rewrites VALUES (new.id); END"
                    )
                )
                versions = "SELECT id FROM rewrites ORDER BY id"
            conn.execute(text("UPDATE conversations SET total_tokens = 81 WHERE id = 'conv-b1'"))
        with before.connect() as conn:
            first = conn.execute(text(versions)).all()

        _migrate(before, _C2P3, "upgrade")

        with before.connect() as conn:
            second = conn.execute(text(versions)).all()
        if before.dialect.name == "postgresql":
            changed = {row_id for (row_id, version) in set(second) - set(first)}
        else:
            changed = {row_id for (row_id,) in second}
        assert changed == {module_row_id("conversation", "conv-b1")}


def module_row_id(kind: str, source_id: str) -> str:
    return str(_module()._row_id(kind, source_id))


class TestTheQuotaIndex:
    def test_on_postgresql_the_sum_is_read_from_the_index(
        self, before: sa.Engine, current_db: sa.Engine  # noqa: F811
    ) -> None:
        if before.dialect.name != "postgresql":
            pytest.skip("SQLite has no INCLUDE; there the index is the plain composite")
        from fim_one.db.models.usage_ledger import UsageLedger

        definition = (
            "SELECT indexdef FROM pg_indexes WHERE schemaname = current_schema()"
            " AND indexname = 'ix_usage_ledger_user_created'"
        )
        _migrate(before, _C2P3, "upgrade")
        with before.connect() as conn:
            migrated = conn.execute(text(definition)).scalar_one()
        with before.begin() as conn:
            conn.execute(text(f"DROP TABLE {_LEDGER}"))
        UsageLedger.__table__.create(before)  # type: ignore[attr-defined]
        with before.connect() as conn:
            modelled = conn.execute(text(definition)).scalar_one()

        assert "(user_id, created_at) INCLUDE (total_tokens)" in migrated
        assert modelled == migrated
