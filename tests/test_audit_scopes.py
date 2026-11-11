"""Who is shown which audit records.

The same records, read by a platform administrator, by an organization's
administrators and by the owner of an account. An organization is shown
what carries its id, never an address or a device; a user is shown the
security events of their own account, with their own address.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest

from fim_one.core.audit import actions
from fim_one.core.audit import query as audit_query
from fim_one.core.audit import writer as audit
from fim_one.core.audit.events import RequestMeta
from fim_one.db.models import ConnectorCallLog, User
from fim_one.db.models.organization import Organization
from fim_one.db.models.review_log import ReviewLog
from tests.audit.harness import World, world  # noqa: F401

ANN_IP = "198.51.100.11"
BOB_IP = "198.51.100.22"
ROOT_IP = "198.51.100.99"
ANN_UA = "AnnBrowser/7"
ROOT_UA = "RootBrowser/9"


async def _event(action: str, user: User, *, ip: str, ua: str = ANN_UA, **fields: Any) -> None:
    await audit.record(
        action,
        actor=audit.actor_of(user),
        request=RequestMeta(ip=ip, user_agent=ua, request_id=str(uuid.uuid4())),
        **fields,
    )


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


class Cast:
    """Two organizations and the people in and around them."""

    root: User  # platform administrator, in no organization
    ann: User  # owner of Acme
    adam: User  # administrator of Acme
    mia: User  # member of Acme
    bob: User  # owner of Globex
    acme: Organization
    globex: Organization


@pytest.fixture()
async def cast(world: World) -> Cast:  # noqa: F811
    c = Cast()
    c.root = await world.user("root", admin=True)
    c.ann = await world.user("ann")
    c.adam = await world.user("adam")
    c.mia = await world.user("mia")
    c.bob = await world.user("bob")
    c.acme = await world.org("Acme", c.ann, admin=c.adam, member=c.mia)
    c.globex = await world.org("Globex", c.bob)

    # Acme's events.
    await _event(
        actions.ORG_MEMBER_ROLE_CHANGE, c.ann, ip=ANN_IP, org_id=c.acme.id,
        target_type="user", target_id=c.adam.id, target_label="adam",
        changes={"role": {"from": "member", "to": "admin"}},
    )  # fmt: skip
    # A row that quotes its own address and device, in the detail and in the changes.
    await _event(
        actions.AGENT_UPDATE, c.mia, ip=ANN_IP, org_id=c.acme.id, target_type="agent",
        target_id="agent-1", target_label="Helper",
        detail=f"changed from {ANN_IP} with {ANN_UA}",
        changes={
            "ip_address": {"from": None, "to": ANN_IP},
            "note": {"from": "x", "to": f"seen at {ANN_IP}"},
        },
    )  # fmt: skip
    # Globex's event.
    await _event(actions.ORG_UPDATE, c.bob, ip=BOB_IP, org_id=c.globex.id,
                 target_type="organization", target_id=c.globex.id)  # fmt: skip
    # What people did to their own accounts.
    await _event(actions.AUTH_PASSWORD_CHANGE, c.mia, ip=ANN_IP, target_type="user",
                 target_id=c.mia.id, target_label="mia")  # fmt: skip
    await _event("api_key.create", c.mia, ip=ANN_IP, target_type="api_key",
                 target_id="key-1", target_label="ci", detail="prefix=fim_abcd")  # fmt: skip
    await _event(actions.AUTH_TWO_FACTOR_ENABLE, c.bob, ip=BOB_IP, target_type="user",
                 target_id=c.bob.id, target_label="bob")  # fmt: skip
    # A platform administrator acting on mia's account, and on something else.
    await _event(
        "user.reset_password", c.root, ip=ROOT_IP, ua=ROOT_UA, target_type="user",
        target_id=c.mia.id, target_label="mia", detail=f"reset from {ROOT_IP}",
    )  # fmt: skip
    await _event("settings.update", c.root, ip=ROOT_IP, ua=ROOT_UA, detail="closed")

    await world.add(
        ReviewLog(org_id=c.acme.id, resource_type="agent", resource_id="agent-1",
                  resource_name="Helper", action="approved", actor_id=c.adam.id,
                  actor_username="adam"),
        ReviewLog(org_id=c.globex.id, resource_type="agent", resource_id="agent-9",
                  resource_name="Other", action="rejected", actor_id=c.bob.id,
                  actor_username="bob"),
        _call(user_id=c.mia.id, org_id=c.acme.id, success=False, response_status=500,
              error_message=f"upstream: customer 42 of {ANN_IP} not found"),
        _call(user_id=c.bob.id, org_id=c.globex.id, connector_name="ERP"),
        _call(user_id=c.mia.id, org_id=None, connector_name="Personal"),
    )  # fmt: skip
    return c


async def _page(world: World, by: User, path: str) -> dict[str, Any]:  # noqa: F811
    return dict((await world.call("GET", path, by=by)).json())


class TestThePlatformAdministrator:
    async def test_sees_every_record_with_its_address(self, world: World, cast: Cast) -> None:
        body = await _page(world, cast.root, "/api/admin/audit-log")

        assert body["total"] == 8
        addresses = {item["ip_address"] for item in body["items"]}
        assert addresses == {ANN_IP, BOB_IP, ROOT_IP}
        orgs = {item["org_id"] for item in body["items"]}
        assert orgs == {None, cast.acme.id, cast.globex.id}


class TestAnOrganizationsAdministrators:
    @pytest.mark.parametrize("who", ["ann", "adam"])
    async def test_the_owner_and_an_administrator_see_their_organizations_events(
        self, world: World, cast: Cast, who: str
    ) -> None:
        reader: User = getattr(cast, who)

        body = await _page(world, reader, f"/api/orgs/{cast.acme.id}/audit-log")

        assert sorted(item["action"] for item in body["items"]) == [
            "agent.update",
            "org.member_role_change",
        ]
        assert body["total"] == 2
        role = next(i for i in body["items"] if i["action"] == "org.member_role_change")
        assert role["actor_label"] == "ann" and role["target_label"] == "adam"
        assert role["changes"] == {"role": {"from": "member", "to": "admin"}}

    async def test_no_address_and_no_device_anywhere_in_the_answer(
        self, world: World, cast: Cast
    ) -> None:
        for source in ("audit", "review", "call"):
            response = await world.call(
                "GET", f"/api/orgs/{cast.acme.id}/audit-log?source={source}", by=cast.ann
            )
            text = response.text
            for secret in (ANN_IP, BOB_IP, ROOT_IP, ANN_UA, ROOT_UA):
                assert secret not in text, f"{secret} in the {source} answer: {text}"
            for item in response.json()["items"]:
                assert "ip_address" not in item and "user_agent" not in item

        # The row that quoted them is still there, with the quotes cut out.
        body = await _page(world, cast.ann, f"/api/orgs/{cast.acme.id}/audit-log")
        quoted = next(i for i in body["items"] if i["action"] == "agent.update")
        assert quoted["detail"] == "changed from [removed] with [removed]"
        assert quoted["changes"] == {"note": {"from": "x", "to": "seen at [removed]"}}

    async def test_the_review_trail_and_the_call_records_of_this_organization_only(
        self, world: World, cast: Cast
    ) -> None:
        base = f"/api/orgs/{cast.acme.id}/audit-log"

        reviews = (await _page(world, cast.adam, f"{base}?source=review"))["items"]
        calls = (await _page(world, cast.adam, f"{base}?source=call"))["items"]

        assert [(r["action"], r["target_label"]) for r in reviews] == [
            ("review.approved", "Helper")
        ]
        assert [(c["action"], c["target_label"], c["outcome"]) for c in calls] == [
            ("call.connector", "CRM", "failed")
        ]
        call = calls[0]
        # The caller is a member: named. What the other system answered is not shown.
        assert call["actor_label"] == "mia" and call["detail"] is None
        assert call["extra"]["response_status"] == 500
        assert call["extra"]["request_url"] == "https://crm.example.test/customers"

    async def test_what_a_member_did_to_their_own_account_is_not_shown(
        self, world: World, cast: Cast
    ) -> None:
        # Even a row of it that carried the organization's id.
        await _event(actions.AUTH_PASSWORD_CHANGE, cast.mia, ip=ANN_IP, org_id=cast.acme.id,
                     target_type="user", target_id=cast.mia.id)  # fmt: skip
        await _event("api_key.create", cast.mia, ip=ANN_IP, org_id=cast.acme.id)
        await _event(actions.ACCOUNT_SELF_DELETE, cast.mia, ip=ANN_IP, org_id=cast.acme.id)

        body = await _page(world, cast.ann, f"/api/orgs/{cast.acme.id}/audit-log")

        assert sorted(item["action"] for item in body["items"]) == [
            "agent.update",
            "org.member_role_change",
        ]

    async def test_filters_stay_inside_the_organization(self, world: World, cast: Cast) -> None:
        base = f"/api/orgs/{cast.acme.id}/audit-log"

        by_bob = await _page(world, cast.ann, f"{base}?actor_id={cast.bob.id}")
        by_action = await _page(world, cast.ann, f"{base}?action=org.update")
        mine = await _page(world, cast.ann, f"{base}?action=org.member_role_change")

        assert by_bob["total"] == 0 and by_action["total"] == 0 and mine["total"] == 1

    async def test_no_address_in_the_target_label_either(self, world: World, cast: Cast) -> None:
        await _event(
            actions.AGENT_UPDATE, cast.mia, ip=ANN_IP, org_id=cast.acme.id,
            target_type="agent", target_id="agent-2", target_label=f"Helper ({ANN_IP}, {ANN_UA})",
        )  # fmt: skip

        found = await _events_for_org(world, cast, target_id="agent-2")

        [row] = found.items
        assert row.target_label is not None and row.target_label.startswith("Helper (")
        assert ANN_IP not in row.target_label and ANN_UA not in row.target_label

    @pytest.mark.parametrize("source", ["review", "call"])
    async def test_an_address_filter_is_ignored_for_every_source(
        self, world: World, cast: Cast, source: str
    ) -> None:
        """The review trail and call records have no address: a filter by one
        would answer with nothing, which a platform reader may be told and an
        organization's administrators are not asked."""
        plain = await _events_for_org(world, cast, source=source)
        filtered = await _events_for_org(world, cast, source=source, ip_address=BOB_IP)

        assert plain.total >= 1
        assert [r.id for r in filtered.items] == [r.id for r in plain.items]

    async def test_an_address_is_not_something_to_filter_by(self, world: World, cast: Cast) -> None:
        """A filter that matched would tell which rows came from an address."""
        base = f"/api/orgs/{cast.acme.id}/audit-log"

        body = await _page(world, cast.ann, f"{base}?ip_address={BOB_IP}")

        assert body["total"] == 2  # ignored, not applied
        found = await _events_for_org(world, cast, ip_address=BOB_IP)
        assert found.total == 2

    async def test_sign_ins_are_not_an_organizations_to_read(
        self, world: World, cast: Cast
    ) -> None:
        await world.call(
            "GET", f"/api/orgs/{cast.acme.id}/audit-log?source=login", by=cast.ann, expect=422
        )
        async with world.factory() as db:
            with pytest.raises(ValueError):
                await audit_query.list_events(
                    db, scope="org", org_id=cast.acme.id,
                    filters=audit_query.EventFilter(source="login"),
                )  # fmt: skip

    async def test_an_ordinary_member_is_refused(self, world: World, cast: Cast) -> None:
        await world.call("GET", f"/api/orgs/{cast.acme.id}/audit-log", by=cast.mia, expect=403)

    async def test_someone_outside_the_organization_is_refused(
        self, world: World, cast: Cast
    ) -> None:
        path = f"/api/orgs/{cast.acme.id}/audit-log"
        await world.call("GET", path, by=cast.bob, expect=403)
        await world.call("GET", path, expect=401)
        # A platform administrator reads these in the platform's log, with
        # its own rules; this endpoint is the organization's.
        await world.call("GET", path, by=cast.root, expect=403)

    async def test_a_platform_administrator_who_administers_the_organization_gets_its_view(
        self, world: World, cast: Cast
    ) -> None:
        both = await world.user("both", admin=True)
        from fim_one.db.models.organization import OrgMembership

        await world.add(OrgMembership(org_id=cast.acme.id, user_id=both.id, role="admin"))

        response = await world.call("GET", f"/api/orgs/{cast.acme.id}/audit-log", by=both)

        assert response.json()["total"] == 2
        assert ANN_IP not in response.text
        # The platform's log still shows them the address.
        platform = await world.call("GET", f"/api/admin/audit-log?org_id={cast.acme.id}", by=both)
        assert ANN_IP in platform.text


async def _events_for_org(world: World, cast: Cast, **filters: Any) -> audit_query.EventPage:  # noqa: F811
    async with world.factory() as db:
        return await audit_query.list_events(
            db, scope="org", org_id=cast.acme.id, filters=audit_query.EventFilter(**filters)
        )


class TestTheOwnerOfAnAccount:
    async def test_sees_their_own_security_events_with_their_own_address(
        self, world: World, cast: Cast
    ) -> None:
        body = await _page(world, cast.mia, "/api/me/security-events")

        own = [i for i in body["items"] if not i["by_administrator"]]
        assert sorted(i["action"] for i in own) == ["api_key.create", "auth.password_change"]
        assert {i["ip_address"] for i in own} == {ANN_IP}
        assert {i["user_agent"] for i in own} == {ANN_UA}
        key = next(i for i in own if i["action"] == "api_key.create")
        assert key["target_label"] == "ci" and key["detail"] == "prefix=fim_abcd"

    async def test_what_an_administrator_did_carries_nothing_of_the_administrator(
        self, world: World, cast: Cast
    ) -> None:
        response = await world.call("GET", "/api/me/security-events", by=cast.mia)

        body = response.json()
        assert body["total"] == 3
        reset = next(i for i in body["items"] if i["action"] == "user.reset_password")
        assert reset["by_administrator"] is True
        assert reset["ip_address"] is None and reset["user_agent"] is None
        assert reset["detail"] is None
        for secret in (ROOT_IP, ROOT_UA, "root"):
            assert secret not in json.dumps(reset), secret
        assert ROOT_IP not in response.text and ROOT_UA not in response.text

    async def test_nothing_of_anyone_else(self, world: World, cast: Cast) -> None:
        mine = await world.call("GET", "/api/me/security-events", by=cast.ann)
        bobs = await _page(world, cast.bob, "/api/me/security-events")

        # ann changed adam's role; that is the organization's event, not her account's.
        assert mine.json() == {"items": [], "total": 0, "page": 1, "size": 20, "pages": 1}
        assert [i["action"] for i in bobs["items"]] == ["auth.two_factor_enable"]
        assert BOB_IP not in mine.text

    async def test_an_administrators_own_actions_on_others_are_not_their_security_events(
        self, world: World, cast: Cast
    ) -> None:
        body = await _page(world, cast.root, "/api/me/security-events")
        assert body["total"] == 0

    async def test_only_events_are_read_in_this_scope(self, world: World, cast: Cast) -> None:
        async with world.factory() as db:
            with pytest.raises(ValueError):
                await audit_query.list_events(
                    db, scope="self", user_id=cast.mia.id,
                    filters=audit_query.EventFilter(source="call"),
                )  # fmt: skip

    async def test_it_needs_a_signed_in_user(self, world: World) -> None:
        await world.call("GET", "/api/me/security-events", expect=401)

    async def test_an_id_of_another_kind_that_equals_theirs_is_not_about_them(
        self, world: World, cast: Cast
    ) -> None:
        """Ids are unique per table, not across: an agent can carry the id of a user."""
        await _event(
            "user.reset_password", cast.root, ip=ROOT_IP, ua=ROOT_UA,
            target_type="agent", target_id=cast.mia.id,
        )  # fmt: skip

        async with world.factory() as db:
            page = await audit_query.list_events(db, scope="self", user_id=cast.mia.id)

        about = [(r.action, r.target_type) for r in page.items if r.target_id == cast.mia.id]
        assert ("user.reset_password", "agent") not in about
        assert ("user.reset_password", "user") in about


class TestWhoMayReadAnOrganization:
    def test_owners_and_administrators_and_nobody_else(self) -> None:
        assert audit_query.may_read_org_audit("owner")
        assert audit_query.may_read_org_audit("admin")
        assert not audit_query.may_read_org_audit("member")
        assert not audit_query.may_read_org_audit(None)
