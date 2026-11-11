"""The platform administrators' audit list reads through ``core/audit/query.py``.

One query for the list and the export, new filters (organization, target,
outcome, address), the other three record tables behind ``source``, and
the per-row list of call records.
"""

from __future__ import annotations

import csv
import io
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import update

from fim_one.core.audit import writer as audit
from fim_one.core.audit.events import RequestMeta
from fim_one.db.models import AuditLog, ConnectorCallLog, User
from fim_one.db.models.review_log import ReviewLog
from tests.audit.harness import World, world  # noqa: F401


def _call(**fields: Any) -> ConnectorCallLog:
    values: dict[str, Any] = {
        "id": str(uuid.uuid4()),
        "connector_id": str(uuid.uuid4()),
        "connector_name": "CRM",
        "action_name": "list_customers",
        "request_method": "GET",
        "request_url": "https://crm.example.test/customers",
        "response_status": 200,
        "response_time_ms": 41,
        "success": True,
        "resource_kind": "connector",
    }
    values.update(fields)
    return ConnectorCallLog(**values)


async def _event(action: str, user: User, **fields: Any) -> None:
    await audit.record(
        action,
        actor=audit.actor_of(user),
        request=RequestMeta(
            ip=fields.pop("ip", "198.51.100.9"), user_agent="UA/1", request_id=None
        ),
        **fields,
    )


async def _items(world: World, admin: User, path: str) -> list[dict[str, Any]]:
    body = (await world.call("GET", path, by=admin)).json()
    return list(body["items"])


class TestThePlatformList:
    async def test_the_existing_fields_and_order_are_as_before(self, world: World) -> None:
        admin = await world.user("root", admin=True)
        await _event("settings.update", admin, detail="first")
        # The database stamps rows to the second; the first a minute earlier,
        # or the order of two rows written in the same second is the ids'.
        async with world.factory() as db:
            await db.execute(
                update(AuditLog).values(created_at=datetime.now(UTC) - timedelta(minutes=1))
            )
            await db.commit()
        await _event("model.create", admin, target_type="model", target_id="m1", detail="second")

        body = (await world.call("GET", "/api/admin/audit-log", by=admin)).json()

        assert body["total"] == 2 and body["page"] == 1 and body["pages"] == 1
        newest = body["items"][0]
        assert [i["action"] for i in body["items"]] == ["model.create", "settings.update"]
        assert newest["admin_id"] == admin.id and newest["admin_username"] == "root"
        assert newest["target_type"] == "model" and newest["target_id"] == "m1"
        assert newest["outcome"] == "success" and newest["ip_address"] == "198.51.100.9"
        # New on every row: where it came from and which table it is.
        assert newest["user_agent"] == "UA/1" and newest["source"] == "audit"

    async def test_filters_narrow_by_organization_target_outcome_and_address(
        self, world: World
    ) -> None:
        admin = await world.user("root", admin=True)
        await _event("org.update", admin, org_id="org-a", target_type="organization",
                     target_id="org-a")  # fmt: skip
        await _event("org.update", admin, org_id="org-b", target_type="organization",
                     target_id="org-b", ip="198.51.100.77")  # fmt: skip
        await _event("security.ip_blocked", admin, outcome="denied")

        async def actions(query: str) -> list[tuple[str, str | None]]:
            rows = await _items(world, admin, f"/api/admin/audit-log?{query}")
            return [(r["action"], r["org_id"]) for r in rows]

        assert await actions("org_id=org-a") == [("org.update", "org-a")]
        assert await actions("target_type=organization&target_id=org-b") == [
            ("org.update", "org-b")
        ]
        assert await actions("outcome=denied") == [("security.ip_blocked", None)]
        assert await actions("ip_address=198.51.100.77") == [("org.update", "org-b")]
        assert await actions(f"admin_id={uuid.uuid4()}") == []

    async def test_a_day_filter_includes_the_whole_day(self, world: World) -> None:
        admin = await world.user("root", admin=True)
        await _event("settings.update", admin)
        today = datetime.now(UTC).date()
        yesterday = today - timedelta(days=1)

        assert await _items(world, admin, f"/api/admin/audit-log?date_from={today}&date_to={today}")
        assert not await _items(world, admin, f"/api/admin/audit-log?date_to={yesterday}")
        # A date that is not one is no filter, as it has been.
        assert await _items(world, admin, "/api/admin/audit-log?date_from=nonsense")

    async def test_the_export_takes_the_same_filters(self, world: World) -> None:
        admin = await world.user("root", admin=True)
        await _event("org.update", admin, org_id="org-a")
        await _event("org.update", admin, org_id="org-b")

        response = await world.call("GET", "/api/admin/audit-log/export?org_id=org-b", by=admin)

        rows = list(csv.reader(io.StringIO(response.text)))
        assert rows[0][:3] == ["timestamp", "admin", "action"]
        assert [r[7] for r in rows[1:]] == ["org-b"]
        assert response.headers["X-Export-Total"] == "1"

    async def test_an_unknown_source_is_refused(self, world: World) -> None:
        admin = await world.user("root", admin=True)
        await world.call("GET", "/api/admin/audit-log?source=users", by=admin, expect=422)


class TestTheOtherTables:
    async def test_sign_ins_review_entries_and_calls_read_as_the_same_record(
        self, world: World
    ) -> None:
        admin = await world.user("root", admin=True)
        ann = await world.user("ann")
        await audit.record_login(
            user_id=ann.id, username="ann", email=ann.email, success=False,
            method="password", failure_reason="wrong_password",
            request=RequestMeta(ip="198.51.100.4", user_agent="UA/2", request_id=None),
        )  # fmt: skip
        await world.add(
            ReviewLog(org_id="org-a", resource_type="agent", resource_id="a1",
                      resource_name="Helper", action="approved", actor_id=ann.id,
                      actor_username="ann", note="fine"),
            _call(user_id=ann.id, org_id="org-a", success=False, response_status=502,
                  error_message="upstream said no"),
        )  # fmt: skip

        login = (await _items(world, admin, "/api/admin/audit-log?source=login"))[0]
        review = (await _items(world, admin, "/api/admin/audit-log?source=review"))[0]
        call = (await _items(world, admin, "/api/admin/audit-log?source=call"))[0]

        assert (login["action"], login["outcome"], login["detail"]) == (
            "auth.login", "failed", "wrong_password",
        )  # fmt: skip
        assert login["ip_address"] == "198.51.100.4" and login["extra"] == {"method": "password"}
        assert (review["action"], review["org_id"], review["target_label"], review["detail"]) == (
            "review.approved", "org-a", "Helper", "fine",
        )  # fmt: skip
        assert (call["action"], call["outcome"], call["detail"]) == (
            "call.connector", "failed", "upstream said no",
        )  # fmt: skip
        assert call["extra"]["request_url"] == "https://crm.example.test/customers"
        assert call["extra"]["response_status"] == 502

    async def test_the_call_record_list_filters_by_user_kind_and_outcome(
        self, world: World
    ) -> None:
        admin = await world.user("root", admin=True)
        ann = await world.user("ann")
        mcp_id = str(uuid.uuid4())
        await world.add(
            _call(user_id=ann.id, org_id="org-a"),
            _call(user_id=ann.id, resource_kind="mcp", connector_id=None, mcp_server_id=mcp_id,
                  connector_name="Search", success=False),
            # Written before the kind was recorded: a connector call.
            _call(resource_kind=None, connector_name="Legacy"),
        )  # fmt: skip

        async def names(query: str = "") -> list[str]:
            rows = await _items(world, admin, f"/api/admin/call-log?{query}")
            return sorted(r["target_label"] for r in rows)

        assert await names() == ["CRM", "Legacy", "Search"]
        assert await names(f"user_id={ann.id}") == ["CRM", "Search"]
        assert await names("kind=mcp") == ["Search"]
        assert await names("kind=connector") == ["CRM", "Legacy"]
        assert await names(f"resource_id={mcp_id}") == ["Search"]
        assert await names("outcome=failed") == ["Search"]
        assert await names("org_id=org-a") == ["CRM"]

    async def test_only_a_platform_administrator_reads_them(self, world: World) -> None:
        ann = await world.user("ann")
        await world.call("GET", "/api/admin/call-log", by=ann, expect=403)
        await world.call("GET", "/api/admin/audit-log?source=call", by=ann, expect=403)


class TestRowsOfTheSameSecond:
    async def test_pages_do_not_repeat_or_skip_a_row(self, world: World) -> None:
        admin = await world.user("root", admin=True)
        stamp = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
        for n in range(5):
            await _event("settings.update", admin, detail=str(n))
        async with world.factory() as db:
            await db.execute(update(AuditLog).values(created_at=stamp))
            await db.commit()

        seen: list[str] = []
        for page in (1, 2, 3):
            rows = await _items(world, admin, f"/api/admin/audit-log?page={page}&size=2")
            seen += [r["id"] for r in rows]

        assert len(seen) == 5 and len(set(seen)) == 5
