"""The builder tools record what they change, like the routes that change the same things.

Any user can open a builder (``POST /api/builder/session``) and have its
agent call these tools in a chat. They switched off an action's
confirmation, pointed a connector elsewhere, rebound an agent and made a
database connector writable with no audit row at all.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from fim_one.core.audit.context import request_auth, request_meta
from fim_one.core.audit.events import RequestMeta
from fim_one.core.tool.builtin import agent_builder, connector_builder, db_builder
from fim_one.db.models import Agent
from fim_one.db.models.connector import Connector, ConnectorAction
from fim_one.db.models.database_schema import DatabaseSchema, SchemaColumn
from tests.audit.harness import CLIENT_IP, USER_AGENT, World, world  # noqa: F401

TOKEN = "crm-owner-token-7f6e5d4c3b2a1908"
BUILDER = "through the builder"


@pytest.fixture()
def in_a_chat_request() -> Any:
    """A tool runs inside the chat request that called it."""
    meta = request_meta.set(RequestMeta(ip=CLIENT_IP, user_agent=USER_AGENT, request_id="req-1"))
    auth = request_auth.set(("session", None))
    yield
    request_auth.reset(auth)
    request_meta.reset(meta)


@pytest.fixture()
async def alice(world: World, in_a_chat_request: None) -> Any:
    return await world.user("alice")


async def _connector(world: World, owner_id: str, **fields: Any) -> None:
    await world.add(
        Connector(
            id="conn-1",
            user_id=owner_id,
            name="crm",
            base_url="https://crm.example.test",
            auth_type="none",
            **fields,
        )
    )


class TestConnectorBuilder:
    async def test_creating_changing_and_deleting_an_action(
        self, world: World, alice: Any
    ) -> None:
        await _connector(world, alice.id)
        args = {"connector_id": "conn-1", "user_id": alice.id}

        created = json.loads(
            await connector_builder.ConnectorCreateActionTool(**args).run(
                name="delete customer",
                method="DELETE",
                path=f"/customers/{{id}}?api_key={TOKEN}",
                requires_confirmation=True,
            )
        )
        action_id = created["action_id"]
        await connector_builder.ConnectorUpdateActionTool(**args).run(
            action_id=action_id, requires_confirmation=False, description="Removes one."
        )
        await connector_builder.ConnectorDeleteActionTool(**args).run(action_id=action_id)

        target = ("connector_action", action_id)
        made = await world.one(
            "connector.action_create",
            actor=alice,
            target=target,
            changes={
                "requires_confirmation": {"from": None, "to": True},
                "method": {"from": None, "to": "DELETE"},
                "path": {"from": None, "to": "/customers/{id}"},
            },
        )
        assert made.detail == f"connector=conn-1; {BUILDER}"
        # Asking a human first was switched off by the agent: recorded.
        await world.one(
            "connector.action_update",
            actor=alice,
            target=target,
            changes={"requires_confirmation": {"from": True, "to": False}},
        )
        gone = await world.one("connector.action_delete", actor=alice, target=target)
        assert gone.detail == f"connector=conn-1; DELETE /customers/{{id}}; {BUILDER}"
        for row in await world.events():
            assert row.auth_method == "session"
            world.assert_absent(row, TOKEN)
        assert await world.get(ConnectorAction, action_id) is None

    async def test_changing_where_the_connector_calls_and_how_it_signs_in(
        self, world: World, alice: Any
    ) -> None:
        await _connector(world, alice.id)

        await connector_builder.ConnectorUpdateSettingsTool(
            connector_id="conn-1", user_id=alice.id
        ).run(
            base_url="https://evil.example.test",
            auth_type="bearer",
            auth_config={"default_token": TOKEN},
        )

        row = await world.one(
            "connector.update",
            actor=alice,
            target=("connector", "conn-1"),
            changes={
                "base_url": {"from": "https://crm.example.test", "to": "https://evil.example.test"},
                "auth_type": {"from": "none", "to": "bearer"},
                "auth_config": {"changed": True},
            },
        )
        assert row.detail == BUILDER
        world.assert_absent(row, TOKEN)

    async def test_a_rename_is_content_and_leaves_no_record(self, world: World, alice: Any) -> None:
        await _connector(world, alice.id)

        await connector_builder.ConnectorUpdateSettingsTool(
            connector_id="conn-1", user_id=alice.id
        ).run(name="CRM (new)")

        assert await world.events() == []

    async def test_importing_actions_from_an_openapi_document(
        self, world: World, alice: Any
    ) -> None:
        await _connector(world, alice.id)
        spec = {
            "openapi": "3.0.0",
            "info": {"title": "Pets"},
            "paths": {"/pets": {"get": {"operationId": "listPets"}}},
        }

        answer = json.loads(
            await connector_builder.ConnectorImportOpenAPITool(
                connector_id="conn-1", user_id=alice.id
            ).run(spec=spec, dry_run=False)
        )

        assert answer["imported"] == 1
        row = await world.one(
            "connector.actions_import", actor=alice, target=("connector", "conn-1")
        )
        assert row.detail == f"1 action(s) from an OpenAPI document; {BUILDER}"

    async def test_someone_elses_connector_is_refused_and_nothing_is_recorded(
        self, world: World, alice: Any
    ) -> None:
        mallory = await world.user("mallory")
        await _connector(world, alice.id)

        answer = await connector_builder.ConnectorUpdateSettingsTool(
            connector_id="conn-1", user_id=mallory.id
        ).run(base_url="https://evil.example.test")

        assert answer.startswith("[Error]")
        assert await world.events() == []


class TestAgentBuilder:
    async def test_tool_categories_and_bound_connectors(self, world: World, alice: Any) -> None:
        await _connector(world, alice.id)
        await world.add(Agent(id="agent-1", user_id=alice.id, name="Helper"))
        args = {"agent_id": "agent-1", "user_id": alice.id}

        await agent_builder.AgentUpdateSettingsTool(**args).run(
            name="Helper 2", tool_categories=["web", "computation"]
        )
        await agent_builder.AgentAddConnectorTool(**args).run(connector_id="conn-1")
        await agent_builder.AgentRemoveConnectorTool(**args).run(connector_id="conn-1")

        categories, added, removed = await world.events("agent.update")
        assert categories.changes == {
            "tool_categories": {"from": None, "to": ["web", "computation"]}
        }
        assert added.changes == {"connector_ids": {"from": None, "to": ["conn-1"]}}
        assert removed.changes == {"connector_ids": {"from": ["conn-1"], "to": []}}
        for row in (categories, added, removed):
            assert (row.actor_id, row.actor_label, row.target_id) == (alice.id, "alice", "agent-1")
            assert (row.detail, row.ip_address) == (BUILDER, CLIENT_IP)

    async def test_a_model_change_is_content_and_leaves_no_record(
        self, world: World, alice: Any
    ) -> None:
        await world.add(Agent(id="agent-1", user_id=alice.id, name="Helper"))

        await agent_builder.AgentSetModelTool(agent_id="agent-1", user_id=alice.id).run(
            temperature=0.2
        )

        assert await world.events() == []
        assert (await world.get(Agent, "agent-1")).model_config_json == {"temperature": 0.2}


class TestDatabaseBuilder:
    async def _database(self, world: World, owner_id: str) -> None:
        await world.add(
            Connector(
                id="db-1",
                user_id=owner_id,
                name="warehouse",
                type="database",
                db_config={"driver": "postgresql", "host": "db.test", "read_only": True},
            )
        )
        await world.add(DatabaseSchema(id="tbl-1", connector_id="db-1", table_name="staff"))
        await world.add(DatabaseSchema(id="tbl-2", connector_id="db-1", table_name="staff_log"))
        await world.add(
            SchemaColumn(id="col-1", schema_id="tbl-1", column_name="salary", data_type="int")
        )

    async def test_switching_read_only_off(self, world: World, alice: Any) -> None:
        await self._database(world, alice.id)

        await db_builder.DbUpdateConnectorSettingsTool(connector_id="db-1", user_id=alice.id).run(
            read_only=False, max_rows=50
        )

        row = await world.one(
            "connector.update",
            actor=alice,
            target=("connector", "db-1"),
            changes={
                "db_config.read_only": {"from": True, "to": False},
                "db_config.max_rows": {"from": None, "to": 50},
            },
        )
        assert row.detail == BUILDER

    async def test_hiding_and_showing_tables(self, world: World, alice: Any) -> None:
        await self._database(world, alice.id)
        args = {"connector_id": "db-1", "user_id": alice.id}

        await db_builder.DbSetTableVisibilityTool(**args).run(table_name="staff", visible=False)
        await db_builder.DbBatchSetVisibilityTool(**args).run(prefixes=["staff"], visible=True)
        await db_builder.DbBatchSetVisibilityTool(**args).run(prefixes=["staff"], visible=True)

        hidden, shown = await world.events("connector.schema_fence_update")
        assert hidden.changes == {
            "staff.is_visible": {"from": True, "to": False},
            "staff.salary.is_visible": {"from": True, "to": False},
        }
        assert shown.changes == {
            "staff.is_visible": {"from": False, "to": True},
            "staff.salary.is_visible": {"from": False, "to": True},
        }
        for row in (hidden, shown):
            assert (row.actor_id, row.target_id) == (alice.id, "db-1")
            assert row.detail is not None and row.detail.endswith(BUILDER)

    async def test_an_annotation_leaves_no_record(self, world: World, alice: Any) -> None:
        await self._database(world, alice.id)

        await db_builder.DbAnnotateTableTool(connector_id="db-1", user_id=alice.id).run(
            table_name="staff", display_name="Staff", description="Everyone on payroll."
        )

        assert await world.events() == []


class TestOpeningABuilder:
    async def test_the_hidden_builder_agent_is_recorded_once(self, world: World) -> None:
        alice = await world.user("alice")
        await _connector(world, alice.id)
        body = {"target_type": "connector", "target_id": "conn-1"}

        first = (await world.call("POST", "/api/builder/session", by=alice, json=body)).json()
        again = (await world.call("POST", "/api/builder/session", by=alice, json=body)).json()

        agent_id = first["data"]["builder_agent_id"]
        assert again["data"]["builder_agent_id"] == agent_id
        row = await world.one("agent.create", actor=alice, target=("agent", agent_id))
        assert row.detail == "builder assistant for connector conn-1"
