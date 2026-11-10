"""Data leaving the system, and looks at data, leave a record.

A user exporting their own data, a conversation or a workflow's runs, an
administrator exporting the audit log or reading the application log, and
an owner running SQL against a database connector from the portal: none
of it was recorded. Each is written on its own, after the fact, so it can
neither fail nor hold up what it describes.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

from sqlalchemy.ext.asyncio import AsyncSession

from fim_one.db.models.connector import Connector
from fim_one.db.models.conversation import Conversation
from fim_one.db.models.workflow import Workflow, WorkflowRun
from tests.audit.harness import World, world  # noqa: F401


class TestAUsersOwnExports:
    async def test_everything_held_about_them(self, world: World) -> None:
        alice = await world.user("alice")

        export = await world.call("GET", "/api/auth/me/export", by=alice)

        assert "fim-one-data-export" in export.headers["content-disposition"]
        row = await world.one("export.own_data", actor=alice, target=("user", alice.id))
        assert row.target_label == "alice"

    async def test_a_conversation(self, world: World) -> None:
        alice = await world.user("alice")
        await world.add(Conversation(id="conv-1", user_id=alice.id, title="Plans", mode="react"))

        await world.call("GET", "/api/conversations/conv-1/export?format=md", by=alice)

        row = await world.one(
            "export.conversation", actor=alice, target=("conversation", "conv-1")
        )
        assert row.detail == "format=md; detail=full"
        # The title is the user's content: the conversation is named by id.
        assert row.target_label is None

    async def test_someone_elses_conversation_is_not_found_and_nothing_is_recorded(
        self, world: World
    ) -> None:
        alice = await world.user("alice")
        mallory = await world.user("mallory")
        await world.add(Conversation(id="conv-1", user_id=alice.id, title="Plans", mode="react"))

        await world.call(
            "GET", "/api/conversations/conv-1/export?format=md", by=mallory, expect=404
        )

        assert await world.events() == []

    async def test_a_workflows_runs(self, world: World) -> None:
        await world.enable_module("workflows")
        alice = await world.user("alice")
        await world.add(
            Workflow(id="wf-1", user_id=alice.id, name="nightly", blueprint={"nodes": []})
        )
        await world.add(
            WorkflowRun(
                id="run-1",
                workflow_id="wf-1",
                user_id=alice.id,
                blueprint_snapshot={"nodes": []},
                status="completed",
                mode="manual",
                inputs={"customer": "ACME-CONFIDENTIAL"},
            )
        )

        await world.call("GET", "/api/workflows/wf-1/runs/export", by=alice)

        row = await world.one("export.workflow_runs", actor=alice, target=("workflow", "wf-1"))
        assert (row.target_label, row.detail) == ("nightly", "1 run(s)")
        world.assert_absent(row, "ACME-CONFIDENTIAL")


class TestAnAdministratorsReads:
    async def test_exporting_the_audit_log(self, world: World) -> None:
        root = await world.user("root", admin=True)
        bob = await world.user("bob")
        await world.call(
            "PATCH", f"/api/admin/users/{bob.id}/active", by=root, json={"is_active": False}
        )

        first = await world.call("GET", "/api/admin/audit-log/export", by=root)

        row = await world.one("export.audit_log", actor=root)
        assert row.detail == "1 row(s)"
        # Written after the rows were read: an export does not hold its own record ...
        assert "export.audit_log" not in first.text
        # ... and the next one shows that the log was exported.
        second = await world.call("GET", "/api/admin/audit-log/export", by=root)
        assert "export.audit_log" in second.text

    async def test_reading_the_application_log(
        self, world: World, tmp_path: Any, monkeypatch: Any
    ) -> None:
        root = await world.user("root", admin=True)
        logs = tmp_path / "logs"
        logs.mkdir()
        (logs / "fim_one.log").write_text(
            "2026-11-11 10:00:00 - fim_one.web.app - INFO - started\n"
            "2026-11-11 10:00:01 - fim_one.web.app - ERROR - key wf_SECRETPREFIX refused\n",
            encoding="utf-8",
        )
        monkeypatch.chdir(tmp_path)

        read = await world.call("GET", "/api/admin/system-logs?level=ERROR", by=root)

        assert len(read.json()["entries"]) == 1
        row = await world.one("system_logs.read", actor=root)
        assert row.detail == "1 line(s); level=ERROR"
        # That the log was read, not what was in it.
        world.assert_absent(row, "wf_SECRETPREFIX")

    async def test_a_user_who_is_not_an_administrator_reads_and_records_nothing(
        self, world: World
    ) -> None:
        bob = await world.user("bob")

        await world.call("GET", "/api/admin/system-logs", by=bob, expect=403)
        await world.call("GET", "/api/admin/audit-log/export", by=bob, expect=403)

        assert await world.events() == []


class TestSqlFromThePortal:
    SQL = "SELECT name, salary FROM staff WHERE name = 'CONFIDENTIAL-NAME'"

    async def _connector(self, world: World, owner_id: str) -> None:
        await world.add(
            Connector(
                id="db-1",
                user_id=owner_id,
                name="warehouse",
                type="database",
                db_config={"driver": "postgresql", "host": "db.test", "read_only": True},
            )
        )

    def _driver(self, **behaviour: Any) -> Any:
        result = SimpleNamespace(
            columns=["name", "salary"],
            rows=[["CONFIDENTIAL-NAME", 4200]],
            row_count=1,
            truncated=False,
            execution_time_ms=3,
        )
        driver = SimpleNamespace(execute_query=AsyncMock(return_value=result, **behaviour))
        pool = SimpleNamespace(get_driver=AsyncMock(return_value=driver))
        return patch(
            "fim_one.web.api.db_connectors.ConnectionPoolManager.get_instance", return_value=pool
        )

    async def test_a_query_is_recorded_without_the_statement_or_its_result(
        self, world: World
    ) -> None:
        alice = await world.user("alice")
        await self._connector(world, alice.id)

        with self._driver():
            answer = await world.call(
                "POST", "/api/connectors/db-1/query", by=alice, json={"sql": self.SQL}
            )

        assert answer.json()["data"]["row_count"] == 1
        row = await world.one("database.query", actor=alice, target=("connector", "db-1"))
        assert (row.target_label, row.detail) == ("warehouse", "read_only=True; rows=1")
        world.assert_absent(row, "CONFIDENTIAL-NAME", "salary", "SELECT")

    async def test_a_statement_the_fence_refuses_is_recorded_as_denied(
        self, world: World
    ) -> None:
        alice = await world.user("alice")
        await self._connector(world, alice.id)

        with self._driver():
            await world.call(
                "POST", "/api/connectors/db-1/query", by=alice, json={"sql": "DROP TABLE staff"}
            )

        row = await world.one(
            "database.query", actor=alice, target=("connector", "db-1"), outcome="denied"
        )
        world.assert_absent(row, "DROP", "staff")

    async def test_a_query_the_database_rejects_is_recorded_as_failed(
        self, world: World
    ) -> None:
        alice = await world.user("alice")
        await self._connector(world, alice.id)

        with self._driver(side_effect=RuntimeError("relation staff: CONFIDENTIAL-NAME")):
            await world.call(
                "POST", "/api/connectors/db-1/query", by=alice, json={"sql": self.SQL}
            )

        row = await world.one(
            "database.query", actor=alice, target=("connector", "db-1"), outcome="failed"
        )
        world.assert_absent(row, "CONFIDENTIAL-NAME")


class TestTheRecordCannotFailTheExport:
    async def test_an_export_succeeds_when_its_record_cannot_be_written(
        self, world: World
    ) -> None:
        alice = await world.user("alice")

        def _broken() -> AsyncSession:
            raise RuntimeError("audit database unavailable")

        with patch("fim_one.core.audit.writer._own_session", _broken):
            export = await world.call("GET", "/api/auth/me/export", by=alice)

        assert export.status_code == 200
        assert await world.events() == []


class TestAnExportThatIsCutShort:
    async def test_it_says_so_in_the_file_and_in_the_response(
        self, world: World, monkeypatch: Any
    ) -> None:
        """The export stops at the newest 10,000 entries; nothing said that
        a file was not the whole log."""
        from fim_one.core.audit import writer as audit
        from fim_one.web.api import admin

        monkeypatch.setattr(admin, "_AUDIT_EXPORT_LIMIT", 3)
        root = await world.user("root", admin=True)
        for _ in range(5):
            await audit.record("user.create", actor=audit.actor_of(root))

        export = await world.call("GET", "/api/admin/audit-log/export", by=root)

        lines = export.text.strip().splitlines()
        assert len(lines) == 1 + 3 + 1
        assert lines[-1].startswith("# truncated: the newest 3 of 5 entries that match")
        assert (
            export.headers["X-Export-Truncated"],
            export.headers["X-Export-Rows"],
            export.headers["X-Export-Total"],
        ) == ("true", "3", "5")
        # The record of the export says it too.
        (row,) = await world.events("export.audit_log")
        assert row.detail == "3 row(s) of 5; truncated"

    async def test_a_date_range_gets_the_rest(self, world: World, monkeypatch: Any) -> None:
        from datetime import UTC, datetime, timedelta

        from fim_one.core.audit import writer as audit
        from fim_one.db.models import AuditLog
        from fim_one.web.api import admin

        monkeypatch.setattr(admin, "_AUDIT_EXPORT_LIMIT", 3)
        root = await world.user("root", admin=True)
        for _ in range(4):
            await audit.record("user.create", actor=audit.actor_of(root))
        old = AuditLog(actor_id=root.id, actor_label="root", action="user.delete")
        old.created_at = datetime.now(UTC) - timedelta(days=40)  # type: ignore[assignment]
        await world.add(old)
        day = (datetime.now(UTC) - timedelta(days=40)).strftime("%Y-%m-%d")

        export = await world.call(
            "GET", f"/api/admin/audit-log/export?date_from={day}&date_to={day}", by=root
        )

        assert export.headers["X-Export-Truncated"] == "false"
        assert [line.split(",")[2] for line in export.text.strip().splitlines()[1:]] == [
            "user.delete"
        ]

    async def test_an_export_of_everything_has_no_marker(self, world: World) -> None:
        root = await world.user("root", admin=True)

        export = await world.call("GET", "/api/admin/audit-log/export", by=root)

        assert export.headers["X-Export-Truncated"] == "false"
        assert "# truncated" not in export.text
