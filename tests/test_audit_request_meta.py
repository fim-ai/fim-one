"""Where a request came from reaches the audit row without being passed along.

The HTTP layer puts the caller's address, user agent and a request id in a
context variable; the writer reads it. Before, no row of ``audit_logs``
had an address, an actor kind or an outcome.
"""

from __future__ import annotations

import csv
import hashlib
import io
import uuid

from fim_one.core.audit import writer as audit
from fim_one.core.audit.context import current_request_auth, current_request_meta
from fim_one.core.audit.events import Actor
from fim_one.db.models import AuditLog
from fim_one.db.models.api_key import ApiKey
from tests.audit.harness import CLIENT_IP, USER_AGENT, World, world  # noqa: F401


class TestAnAdministratorsAction:
    async def test_the_row_carries_the_address_the_actor_kind_and_the_outcome(
        self, world: World
    ) -> None:
        root = await world.user("root", admin=True)
        bob = await world.user("bob")

        await world.call(
            "PATCH", f"/api/admin/users/{bob.id}/active", by=root, json={"is_active": False}
        )

        row = await world.one("user.disable", actor=root, target=("user", bob.id))
        assert (row.ip_address, row.user_agent) == (CLIENT_IP, USER_AGENT)
        assert uuid.UUID(row.request_id or "")  # a request id was made
        assert (row.actor_kind, row.outcome) == ("admin", "success")
        assert (row.auth_method, row.credential_ref) == ("session", None)

    async def test_an_api_key_is_named_by_its_id_never_its_value(self, world: World) -> None:
        root = await world.user("root", admin=True)
        bob = await world.user("bob")
        raw_key = "fim_" + "k" * 40
        key = ApiKey(
            id=str(uuid.uuid4()),
            user_id=root.id,
            name="ci",
            key_prefix=raw_key[:8],
            key_hash=hashlib.sha256(raw_key.encode()).hexdigest(),
        )
        await world.add(key)

        await world.call(
            "PATCH",
            f"/api/admin/users/{bob.id}/active",
            headers={"Authorization": f"Bearer {raw_key}"},
            json={"is_active": False},
        )

        row = await world.one("user.disable", actor=root, target=("user", bob.id))
        assert (row.auth_method, row.credential_ref) == ("api_key", key.id)
        world.assert_absent(row, raw_key)

    async def test_two_requests_get_two_request_ids(self, world: World) -> None:
        root = await world.user("root", admin=True)
        bob = await world.user("bob")

        for active in (False, True):
            await world.call(
                "PATCH", f"/api/admin/users/{bob.id}/active", by=root, json={"is_active": active}
            )

        first, second = await world.events()
        assert first.request_id and second.request_id and first.request_id != second.request_id


class TestOutsideARequest:
    async def test_a_background_entry_has_no_address(self, world: World) -> None:
        """A schedule or a sweep is not an HTTP request."""
        assert current_request_meta() is None and current_request_auth() is None

        await audit.record("workflow.cleanup_runs", actor=audit.system_actor("run cleanup"))

        row = await world.one("workflow.cleanup_runs", actor=None, ip=None)
        assert (row.actor_kind, row.actor_label, row.user_agent, row.request_id) == (
            "system",
            "run cleanup",
            None,
            None,
        )

    async def test_the_context_does_not_outlive_the_request(self, world: World) -> None:
        root = await world.user("root", admin=True)
        await world.call("GET", "/api/admin/audit-log", by=root)

        assert current_request_meta() is None and current_request_auth() is None


class TestTheAdministratorsList:
    async def test_new_rows_show_the_new_columns_and_old_rows_read_as_before(
        self, world: World
    ) -> None:
        root = await world.user("root", admin=True)
        # A row as it was written before the columns existed.
        async with world.factory() as db:
            db.add(AuditLog(actor_id="u-old", actor_label="old-admin", action="user.create"))
            await db.commit()
        await audit.record(
            "user.set_quota",
            actor=Actor(root.id, "root", "admin"),
            org_id="org-1",
            outcome="denied",
            changes={"quota": {"from": 1, "to": 2}},
        )

        listing = (await world.call("GET", "/api/admin/audit-log", by=root)).json()

        by_action = {item["action"]: item for item in listing["items"]}
        new, old = by_action["user.set_quota"], by_action["user.create"]
        assert (new["actor_kind"], new["org_id"], new["outcome"]) == ("admin", "org-1", "denied")
        assert new["changes"] == {"quota": {"from": 1, "to": 2}}
        assert (old["admin_id"], old["admin_username"]) == ("u-old", "old-admin")
        assert (old["actor_kind"], old["outcome"], old["ip_address"]) == (None, "success", None)

    async def test_the_export_has_the_new_columns(self, world: World) -> None:
        root = await world.user("root", admin=True)
        bob = await world.user("bob")
        await world.call(
            "PATCH", f"/api/admin/users/{bob.id}/active", by=root, json={"is_active": False}
        )

        export = await world.call("GET", "/api/admin/audit-log/export", by=root)

        (line,) = [r for r in csv.DictReader(io.StringIO(export.text)) if r["action"] == "user.disable"]
        assert (line["admin"], line["actor_kind"], line["outcome"]) == ("root", "admin", "success")
        assert line["ip_address"] == CLIENT_IP
