"""Organizations and their members: every change leaves a record.

Creating, changing and deleting an organization, adding and removing
members and changing a member's role were recorded nowhere. Each now
leaves one audit event that belongs to the organization, names the member
it concerns, and carries the role before and after.
"""

from __future__ import annotations

from fim_one.db.models.organization import Organization, OrgMembership
from tests.audit.harness import World, world  # noqa: F401


class TestOrganizations:
    async def test_creating_one(self, world: World) -> None:
        alice = await world.user("alice")

        created = (await world.call("POST", "/api/orgs", by=alice, json={"name": "Acme"})).json()

        org_id = created["data"]["id"]
        row = await world.one(
            "org.create", actor=alice, org=org_id, target=("organization", org_id)
        )
        assert row.target_label == "Acme"

    async def test_changing_one_lists_what_changed(self, world: World) -> None:
        alice = await world.user("alice")
        acme = await world.org("Acme", alice)

        await world.call(
            "PUT",
            f"/api/orgs/{acme.id}",
            by=alice,
            # ``description`` is sent with the value it already has.
            json={"name": "Acme Ltd", "review_agents": True, "description": None},
        )

        await world.one(
            "org.update",
            actor=alice,
            org=acme,
            target=("organization", acme.id),
            changes={
                "name": {"from": "Acme", "to": "Acme Ltd"},
                "review_agents": {"from": False, "to": True},
            },
        )

    async def test_deleting_one(self, world: World) -> None:
        alice = await world.user("alice")
        bob = await world.user("bob")
        acme = await world.org("Acme", alice, member=bob)

        await world.call("DELETE", f"/api/orgs/{acme.id}", by=alice)

        # The record names an organization that no longer exists.
        row = await world.one(
            "org.delete", actor=alice, org=acme, target=("organization", acme.id)
        )
        assert (row.target_label, row.detail) == ("Acme", "members=2")
        assert await world.get(Organization, acme.id) is None

    async def test_a_member_who_may_not_change_it_changes_and_records_nothing(
        self, world: World
    ) -> None:
        alice = await world.user("alice")
        bob = await world.user("bob")
        acme = await world.org("Acme", alice, member=bob)

        await world.call(
            "PUT", f"/api/orgs/{acme.id}", by=bob, json={"name": "Bob's"}, expect=403
        )

        assert await world.events() == []


class TestMembers:
    async def test_adding_one(self, world: World) -> None:
        alice = await world.user("alice")
        bob = await world.user("bob")
        acme = await world.org("Acme", alice)

        await world.call(
            "POST",
            f"/api/orgs/{acme.id}/members",
            by=alice,
            json={"username": "bob", "role": "admin"},
        )

        row = await world.one(
            "org.member_add",
            actor=alice,
            org=acme,
            target=("user", bob.id),
            changes={"role": {"from": None, "to": "admin"}},
        )
        assert row.target_label == "bob"

    async def test_org_role_change_audited(self, world: World) -> None:
        alice = await world.user("alice")
        bob = await world.user("bob")
        acme = await world.org("Acme", alice, member=bob)

        await world.call(
            "PATCH", f"/api/orgs/{acme.id}/members/{bob.id}", by=alice, json={"role": "admin"}
        )

        row = await world.one(
            "org.member_role_change",
            actor=alice,
            org=acme,
            target=("user", bob.id),
            changes={"role": {"from": "member", "to": "admin"}},
        )
        assert row.target_label == "bob"

    async def test_removing_one(self, world: World) -> None:
        alice = await world.user("alice")
        bob = await world.user("bob")
        acme = await world.org("Acme", alice, admin=bob)

        await world.call("DELETE", f"/api/orgs/{acme.id}/members/{bob.id}", by=alice)

        row = await world.one(
            "org.member_remove",
            actor=alice,
            org=acme,
            target=("user", bob.id),
            changes={"role": {"from": "admin", "to": None}},
        )
        assert (row.target_label, row.detail) == ("bob", None)

    async def test_leaving(self, world: World) -> None:
        alice = await world.user("alice")
        bob = await world.user("bob")
        acme = await world.org("Acme", alice, member=bob)

        await world.call("DELETE", f"/api/orgs/{acme.id}/members/{bob.id}", by=bob)

        row = await world.one(
            "org.member_remove",
            actor=bob,
            org=acme,
            target=("user", bob.id),
            changes={"role": {"from": "member", "to": None}},
        )
        assert row.detail == "left"

    async def test_an_admin_who_may_not_remove_another_admin_records_nothing(
        self, world: World
    ) -> None:
        alice = await world.user("alice")
        bob = await world.user("bob")
        carol = await world.user("carol")
        acme = await world.org("Acme", alice, admin=bob)
        await world.add(OrgMembership(org_id=acme.id, user_id=carol.id, role="admin"))

        await world.call(
            "DELETE", f"/api/orgs/{acme.id}/members/{carol.id}", by=bob, expect=403
        )

        assert await world.events() == []


class TestFreeTextAndAddresses:
    async def test_a_description_and_an_icon_are_recorded_as_changed_not_quoted(
        self, world: World
    ) -> None:
        """The description's text and the icon's address, signed query
        included, were stored before and after."""
        alice = await world.user("alice")
        acme = await world.org("Acme", alice)

        await world.call(
            "PUT",
            f"/api/orgs/{acme.id}",
            by=alice,
            json={
                "description": "Internal: the merger closes in March.",
                "icon": "https://cdn.example.test/i.png?sig=s1gnedQueryValue",
                "name": "Acme Ltd",
            },
        )

        row = await world.one(
            "org.update",
            actor=alice,
            org=acme,
            changes={
                "description": {"changed": True},
                "icon": {"changed": True},
                "name": {"from": "Acme", "to": "Acme Ltd"},
            },
        )
        world.assert_absent(row, "merger", "s1gnedQueryValue", "cdn.example.test")
