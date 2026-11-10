"""Platform administration actions that left no record.

Changing a user's email address or display name, changing or deleting an
organization as a platform administrator, importing the Market templates,
and sending a message through an instance-wide webhook.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

from fim_one.core.channels import ChannelSendResult
from fim_one.db.models import User
from fim_one.db.models.organization import Organization
from fim_one.web.platform import MARKET_ORG_ID
from tests.audit.harness import World, world  # noqa: F401


class TestChangingAUser:
    async def test_the_email_address_and_display_name_with_before_and_after(
        self, world: World
    ) -> None:
        root = await world.user("root", admin=True)
        bob = await world.user("bob")

        await world.call(
            "PATCH",
            f"/api/admin/users/{bob.id}",
            by=root,
            json={"email": "robert@example.test", "display_name": "Robert"},
        )

        row = await world.one(
            "user.update",
            actor=root,
            target=("user", bob.id),
            changes={
                "email": {"from": "bob@example.test", "to": "robert@example.test"},
                "display_name": {"from": None, "to": "Robert"},
            },
        )
        assert (row.actor_kind, row.target_label) == ("admin", "bob")
        assert (await world.get(User, bob.id)).email == "robert@example.test"

    async def test_a_save_that_changes_nothing_records_nothing(self, world: World) -> None:
        root = await world.user("root", admin=True)
        bob = await world.user("bob")

        await world.call(
            "PATCH", f"/api/admin/users/{bob.id}", by=root, json={"email": bob.email}
        )

        assert await world.events() == []

    async def test_an_address_someone_else_has_is_refused_and_nothing_is_recorded(
        self, world: World
    ) -> None:
        root = await world.user("root", admin=True)
        bob = await world.user("bob")

        await world.call(
            "PATCH", f"/api/admin/users/{bob.id}", by=root, json={"email": root.email}, expect=409
        )

        assert await world.events() == []


class TestSomeoneElsesOrganization:
    async def test_changing_it(self, world: World) -> None:
        root = await world.user("root", admin=True)
        alice = await world.user("alice")
        acme = await world.org("Acme", alice)

        await world.call(
            "PUT",
            f"/api/admin/organizations/{acme.id}",
            by=root,
            json={"is_active": False, "name": "Acme (suspended)"},
        )

        row = await world.one(
            "org.admin_update",
            actor=root,
            org=acme,
            target=("organization", acme.id),
            changes={
                "is_active": {"from": True, "to": False},
                "name": {"from": "Acme", "to": "Acme (suspended)"},
            },
        )
        assert row.actor_kind == "admin"

    async def test_deleting_it(self, world: World) -> None:
        root = await world.user("root", admin=True)
        alice = await world.user("alice")
        acme = await world.org("Acme", alice)

        await world.call("DELETE", f"/api/admin/organizations/{acme.id}", by=root)

        row = await world.one(
            "org.admin_delete", actor=root, org=acme, target=("organization", acme.id)
        )
        assert (row.target_label, row.detail) == ("Acme", f"owner={alice.id}; members=1")
        assert await world.get(Organization, acme.id) is None

    async def test_a_user_who_is_not_a_platform_administrator_records_nothing(
        self, world: World
    ) -> None:
        alice = await world.user("alice")
        acme = await world.org("Acme", alice)

        await world.call(
            "DELETE", f"/api/admin/organizations/{acme.id}", by=alice, expect=403
        )

        assert await world.events() == []


class TestTheMarket:
    async def test_importing_the_prebuilt_templates(self, world: World) -> None:
        root = await world.user("root", admin=True)

        answer = (await world.call("POST", "/api/admin/market/import-templates", by=root)).json()

        row = await world.one(
            "market.import_templates",
            actor=root,
            org=MARKET_ORG_ID,
            target=("organization", MARKET_ORG_ID),
        )
        assert answer["created"] > 0
        assert row.detail == f"created {answer['created']}, updated {answer['updated']}"


class TestInstanceWebhooks:
    def _channel(self, result: ChannelSendResult) -> Any:
        channel = AsyncMock()
        channel.send_notice = AsyncMock(return_value=result)
        return patch("fim_one.web.api.notifications.instance_channel", return_value=channel)

    async def test_sending_a_message(self, world: World) -> None:
        root = await world.user("root", admin=True)
        body = {"provider": "slack", "title": "Maintenance", "body": "CONFIDENTIAL-NOTICE-TEXT"}

        with self._channel(ChannelSendResult(ok=True)):
            await world.call("POST", "/api/notifications/send", by=root, json=body)
        with self._channel(ChannelSendResult(ok=False, error="403 https://hooks.x/T/B/s3cr3t")):
            await world.call("POST", "/api/notifications/send", by=root, json=body)

        sent, failed = await world.events("notifications.instance_send")
        assert (sent.outcome, failed.outcome) == ("success", "failed")
        for row in (sent, failed):
            assert (row.actor_id, row.detail, row.ip_address is not None) == (
                root.id,
                "provider=slack",
                True,
            )
            # That a message went out, not what it said or what came back.
            world.assert_absent(row, "CONFIDENTIAL-NOTICE-TEXT", "s3cr3t", "Maintenance")

    async def test_sending_a_test_message(self, world: World) -> None:
        root = await world.user("root", admin=True)
        slack = SimpleNamespace(name="slack", display_name="Slack")

        with (
            self._channel(ChannelSendResult(ok=True)),
            patch("fim_one.web.api.notifications.instance_providers", return_value=[slack]),
        ):
            await world.call("POST", "/api/notifications/test", by=root, json={"provider": "slack"})

        row = await world.one("notifications.instance_test", actor=root)
        assert row.detail == "provider=slack"

    async def test_a_provider_that_is_not_configured_sends_and_records_nothing(
        self, world: World
    ) -> None:
        root = await world.user("root", admin=True)

        with patch("fim_one.web.api.notifications.instance_channel", return_value=None):
            answer = await world.call(
                "POST",
                "/api/notifications/send",
                by=root,
                json={"provider": "slack", "title": "t", "body": "b"},
            )

        assert answer.json()["ok"] is False
        assert await world.events() == []
