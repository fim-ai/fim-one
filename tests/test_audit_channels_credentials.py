"""Channels, personal credentials and workflow env vars: recorded, without the secret.

A channel's config holds the app secret or the webhook URL that lets
anyone post to the chat; a personal credential is a token or a password; a
workflow's env vars are stored encrypted. Creating, changing and deleting
any of them was recorded nowhere. Each now leaves one audit event, and no
event holds a value: a config key or a secret says only that it changed.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

from fim_one.core.channels.base import ChannelSendResult as SendResult
from fim_one.db.models import ConnectorCredential, ResourceSubscription
from fim_one.db.models.channel import Channel
from fim_one.db.models.connector import Connector
from fim_one.db.models.mcp_server import MCPServer
from fim_one.db.models.workflow import Workflow
from tests.audit.harness import World, world  # noqa: F401

WEBHOOK = "https://hooks.slack.com/services/T0AUDIT/B0AUDIT/s3cr3tT0k3nV4lu3XyZ"
WEBHOOK_2 = "https://hooks.slack.com/services/T0AUDIT/B0AUDIT/An0th3rT0k3nV4lu3Abc"
APP_SECRET = "feishu-app-secret-9f8e7d6c5b4a"
ENCRYPT_KEY = "feishu-encrypt-key-1a2b3c4d"


async def _slack_channel(world: World, org_id: str, creator_id: str) -> Channel:
    channel = Channel(
        id="chan-1",
        name="ops",
        type="slack_webhook",
        org_id=org_id,
        config={"webhook_url": WEBHOOK},
        created_by=creator_id,
    )
    await world.add(channel)
    return channel


class TestChannels:
    async def test_creating_one(self, world: World) -> None:
        alice = await world.user("alice")
        acme = await world.org("Acme", alice)

        created = (
            await world.call(
                "POST",
                "/api/channels",
                by=alice,
                json={
                    "name": "ops",
                    "type": "slack_webhook",
                    "org_id": acme.id,
                    "config": {"webhook_url": WEBHOOK},
                },
            )
        ).json()

        row = await world.one(
            "channel.create",
            actor=alice,
            org=acme,
            target=("channel", created["id"]),
            changes={
                "name": {"from": None, "to": "ops"},
                "type": {"from": None, "to": "slack_webhook"},
                "is_active": {"from": None, "to": True},
                "config.webhook_url": {"changed": True},
            },
        )
        assert row.target_label == "ops"
        world.assert_absent(row, WEBHOOK, "s3cr3tT0k3nV4lu3XyZ", "hooks.slack.com")

    async def test_channel_secret_change_audited(self, world: World) -> None:
        alice = await world.user("alice")
        acme = await world.org("Acme", alice)
        await _slack_channel(world, acme.id, alice.id)

        await world.call(
            "PATCH",
            "/api/channels/chan-1",
            by=alice,
            json={"name": "ops-alerts", "is_active": False, "config": {"webhook_url": WEBHOOK_2}},
        )

        row = await world.one(
            "channel.update",
            actor=alice,
            org=acme,
            target=("channel", "chan-1"),
            changes={
                "name": {"from": "ops", "to": "ops-alerts"},
                "is_active": {"from": True, "to": False},
                "config.webhook_url": {"changed": True},
            },
        )
        world.assert_absent(row, WEBHOOK, WEBHOOK_2, "s3cr3tT0k3nV4lu3XyZ", "An0th3rT0k3nV4lu3Abc")

    async def test_a_feishu_app_secret_is_never_in_the_row(self, world: World) -> None:
        alice = await world.user("alice")
        acme = await world.org("Acme", alice)

        created = (
            await world.call(
                "POST",
                "/api/channels",
                by=alice,
                json={
                    "name": "approvals",
                    "type": "feishu",
                    "org_id": acme.id,
                    "config": {
                        "app_id": "cli_a1b2c3",
                        "app_secret": APP_SECRET,
                        "chat_id": "oc_123",
                        "encrypt_key": ENCRYPT_KEY,
                    },
                },
            )
        ).json()

        row = await world.one(
            "channel.create", actor=alice, org=acme, target=("channel", created["id"])
        )
        assert row.changes is not None
        # Every config key says only that it was set: not the chat either.
        assert {k: v for k, v in row.changes.items() if k.startswith("config.")} == {
            "config.app_id": {"changed": True},
            "config.app_secret": {"changed": True},
            "config.chat_id": {"changed": True},
            "config.encrypt_key": {"changed": True},
        }
        world.assert_absent(row, APP_SECRET, ENCRYPT_KEY, "cli_a1b2c3", "oc_123")

    async def test_a_save_that_changes_nothing_still_says_who_saved(self, world: World) -> None:
        alice = await world.user("alice")
        acme = await world.org("Acme", alice)
        await _slack_channel(world, acme.id, alice.id)

        await world.call("PATCH", "/api/channels/chan-1", by=alice, json={"name": "ops"})

        row = await world.one("channel.update", actor=alice, org=acme, target=("channel", "chan-1"))
        assert row.changes is None

    async def test_deleting_one(self, world: World) -> None:
        alice = await world.user("alice")
        acme = await world.org("Acme", alice)
        await _slack_channel(world, acme.id, alice.id)

        await world.call("DELETE", "/api/channels/chan-1", by=alice)

        row = await world.one("channel.delete", actor=alice, org=acme, target=("channel", "chan-1"))
        assert (row.target_label, row.detail) == ("ops", "type=slack_webhook")
        world.assert_absent(row, WEBHOOK)
        assert await world.get(Channel, "chan-1") is None

    async def test_sending_a_test_message(self, world: World) -> None:
        alice = await world.user("alice")
        bob = await world.user("bob")
        acme = await world.org("Acme", alice, member=bob)
        await _slack_channel(world, acme.id, alice.id)

        for outcome in (SendResult(ok=True), SendResult(ok=False, error=f"403 from {WEBHOOK}")):
            with patch(
                "fim_one.core.channels.webhook.SlackWebhookChannel.send_notice",
                AsyncMock(return_value=outcome),
            ):
                await world.call("POST", "/api/channels/chan-1/test", by=bob)

        sent, failed = await world.events("channel.test")
        assert (sent.outcome, failed.outcome) == ("success", "failed")
        for row in (sent, failed):
            assert (row.actor_id, row.org_id, row.target_id) == (bob.id, acme.id, "chan-1")
            assert (row.detail, row.ip_address is not None) == ("type=slack_webhook", True)
            world.assert_absent(row, WEBHOOK, "s3cr3tT0k3nV4lu3XyZ")

    async def test_a_member_who_may_not_manage_channels_records_nothing(
        self, world: World
    ) -> None:
        alice = await world.user("alice")
        bob = await world.user("bob")
        acme = await world.org("Acme", alice, member=bob)
        await _slack_channel(world, acme.id, alice.id)

        await world.call("DELETE", "/api/channels/chan-1", by=bob, expect=403)

        assert await world.events() == []


def _connector(owner_id: str, **fields: Any) -> Connector:
    return Connector(
        id="conn-1",
        user_id=owner_id,
        name="crm",
        base_url="https://crm.example.test",
        auth_type="bearer",
        **fields,
    )


class TestPersonalCredentials:
    async def test_setting_one_for_a_connector(self, world: World) -> None:
        alice = await world.user("alice")
        await world.add(_connector(alice.id))
        token = "crm-personal-token-5a4b3c2d1e"

        for _ in range(2):
            await world.call(
                "PUT", "/api/connectors/conn-1/my-credentials", by=alice, json={"token": token}
            )

        new, replaced = await world.events("connector.credential_set")
        assert (new.detail, replaced.detail) == (
            "auth_type=bearer; new",
            "auth_type=bearer; replaced",
        )
        for row in (new, replaced):
            assert (row.actor_id, row.target_type, row.target_id, row.target_label) == (
                alice.id,
                "connector",
                "conn-1",
                "crm",
            )
            assert row.ip_address is not None
            world.assert_absent(row, token)

    async def test_a_credential_on_an_organizations_connector_belongs_to_it(
        self, world: World
    ) -> None:
        alice = await world.user("alice")
        bob = await world.user("bob")
        acme = await world.org("Acme", alice, member=bob)
        await world.add(
            _connector(alice.id, visibility="org", org_id=acme.id, publish_status="approved")
        )
        await world.add(
            ResourceSubscription(
                user_id=bob.id, resource_type="connector", resource_id="conn-1", org_id=acme.id
            )
        )

        await world.call(
            "PUT", "/api/connectors/conn-1/my-credentials", by=bob, json={"token": "bobs-token"}
        )

        row = await world.one(
            "connector.credential_set", actor=bob, org=acme, target=("connector", "conn-1")
        )
        world.assert_absent(row, "bobs-token")

    async def test_deleting_one_for_a_connector(self, world: World) -> None:
        alice = await world.user("alice")
        await world.add(_connector(alice.id))
        await world.add(
            ConnectorCredential(connector_id="conn-1", user_id=alice.id, credentials_blob="x")
        )

        await world.call("DELETE", "/api/connectors/conn-1/my-credentials", by=alice)
        # Nothing left to delete: nothing more to record.
        await world.call("DELETE", "/api/connectors/conn-1/my-credentials", by=alice)

        await world.one(
            "connector.credential_delete", actor=alice, target=("connector", "conn-1")
        )

    async def test_setting_and_clearing_one_for_an_mcp_server(self, world: World) -> None:
        alice = await world.user("alice")
        await world.add(
            MCPServer(
                id="srv-1",
                user_id=alice.id,
                name="files",
                transport="streamable_http",
                url="https://mcp.example.test/mcp",
            )
        )
        header = "Bearer mcp-personal-0a1b2c3d4e5f"

        await world.call(
            "PUT",
            "/api/mcp-servers/srv-1/my-credentials",
            by=alice,
            json={"headers": {"Authorization": header}},
        )
        await world.call(
            "PUT", "/api/mcp-servers/srv-1/my-credentials", by=alice, json={"headers": {}}
        )

        row = await world.one(
            "mcp_server.credential_set", actor=alice, target=("mcp_server", "srv-1")
        )
        assert (row.target_label, row.detail) == ("files", "headers; new")
        world.assert_absent(row, header, "mcp-personal-0a1b2c3d4e5f", "Authorization")
        await world.one(
            "mcp_server.credential_delete", actor=alice, target=("mcp_server", "srv-1")
        )


class TestWorkflowEnvVars:
    async def test_the_names_are_recorded_and_no_value(self, world: World) -> None:
        await world.enable_module("workflows")
        alice = await world.user("alice")
        await world.add(
            Workflow(id="wf-1", user_id=alice.id, name="nightly", blueprint={"nodes": []})
        )
        first, second = "pk_live_0123456789abcdef", "postgres://u:hunter2@db/x"

        await world.call(
            "PUT", "/api/workflows/wf-1/env", by=alice, json={"env_vars": {"STRIPE_KEY": first}}
        )
        await world.call(
            "PUT",
            "/api/workflows/wf-1/env",
            by=alice,
            json={"env_vars": {"STRIPE_KEY": first, "DB_URL": second}},
        )

        created, extended = await world.events("workflow.env_update")
        assert created.changes == {"variables": {"from": [], "to": ["STRIPE_KEY"]}}
        assert extended.changes == {
            "variables": {"from": ["STRIPE_KEY"], "to": ["DB_URL", "STRIPE_KEY"]}
        }
        for row in (created, extended):
            assert (row.actor_id, row.target_type, row.target_id, row.target_label) == (
                alice.id,
                "workflow",
                "wf-1",
                "nightly",
            )
            assert row.ip_address is not None
            world.assert_absent(row, first, second, "hunter2")

    async def test_replacing_a_value_under_the_same_name_is_still_an_event(
        self, world: World
    ) -> None:
        await world.enable_module("workflows")
        alice = await world.user("alice")
        await world.add(
            Workflow(id="wf-1", user_id=alice.id, name="nightly", blueprint={"nodes": []})
        )

        for value in ("first-value", "second-value"):
            await world.call(
                "PUT", "/api/workflows/wf-1/env", by=alice, json={"env_vars": {"TOKEN": value}}
            )

        _, replaced = await world.events("workflow.env_update")
        assert (replaced.changes, replaced.detail) == (None, "1 variable(s) stored")
        world.assert_absent(replaced, "first-value", "second-value")
