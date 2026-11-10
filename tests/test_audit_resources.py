"""Resources: creating, deleting, switching and rebinding leave a record.

Agents, connectors, MCP servers, knowledge bases, skills and workflows:
none of what their owners did to them was recorded. Creating and deleting
now is, and so is a change to anything that decides who or what a resource
may reach (on/off, a confirmation setting, a bound resource, the address
it calls, the fallback switch, a stored credential). An edit of a name or
of instructions is content and leaves nothing.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from fim_one.db.models import Agent, ResourceSubscription
from fim_one.db.models.connector import Connector, ConnectorAction
from fim_one.db.models.database_schema import DatabaseSchema, SchemaColumn
from fim_one.db.models.knowledge_base import KnowledgeBase
from fim_one.db.models.mcp_server import MCPServer
from fim_one.db.models.skill import Skill
from fim_one.db.models.workflow import Workflow, WorkflowRun
from tests.audit.harness import World, world  # noqa: F401

TOKEN = "crm-owner-token-7f6e5d4c3b2a1908"


def _data(response: Any) -> Any:
    return response.json()["data"]


# kind -> (collection path, a body that creates one, the model)
KINDS: dict[str, tuple[str, dict[str, Any], Any]] = {
    "agent": ("/api/agents", {"name": "Helper"}, Agent),
    "connector": (
        "/api/connectors",
        {"name": "CRM", "base_url": "https://crm.example.test"},
        Connector,
    ),
    "mcp_server": (
        "/api/mcp-servers",
        {"name": "Files", "transport": "streamable_http", "url": "https://mcp.example.test/mcp"},
        MCPServer,
    ),
    "kb": ("/api/knowledge-bases", {"name": "Handbook"}, KnowledgeBase),
    "skill": ("/api/skills", {"name": "Summarise", "content": "Summarise the text."}, Skill),
    "workflow": ("/api/workflows", {"name": "Nightly"}, Workflow),
}


@pytest.fixture()
async def alice(world: World) -> Any:
    await world.enable_module("skills")
    await world.enable_module("workflows")
    return await world.user("alice")


async def _create(world: World, kind: str, by: Any, **extra: Any) -> str:
    path, body, _ = KINDS[kind]
    created = _data(await world.call("POST", path, by=by, json={**body, **extra}))
    return str(created["id"])


class TestEveryKind:
    @pytest.mark.parametrize("kind", sorted(KINDS))
    async def test_creating_one(self, world: World, alice: Any, kind: str) -> None:
        resource_id = await _create(world, kind, alice)

        row = await world.one(f"{kind}.create", actor=alice, target=(kind, resource_id))
        assert row.target_label == KINDS[kind][1]["name"]

    @pytest.mark.parametrize("kind", sorted(KINDS))
    async def test_deleting_one(self, world: World, alice: Any, kind: str) -> None:
        resource_id = await _create(world, kind, alice)

        await world.call("DELETE", f"{KINDS[kind][0]}/{resource_id}", by=alice)

        # The record names something that no longer exists.
        row = await world.one(f"{kind}.delete", actor=alice, target=(kind, resource_id))
        assert row.target_label == KINDS[kind][1]["name"]
        assert await world.get(KINDS[kind][2], resource_id) is None

    @pytest.mark.parametrize("kind", sorted(KINDS))
    async def test_switching_one_off(self, world: World, alice: Any, kind: str) -> None:
        resource_id = await _create(world, kind, alice)

        await world.call(
            "PUT", f"{KINDS[kind][0]}/{resource_id}", by=alice, json={"is_active": False}
        )

        await world.one(
            f"{kind}.update",
            actor=alice,
            target=(kind, resource_id),
            changes={"is_active": {"from": True, "to": False}},
        )

    @pytest.mark.parametrize("kind", sorted(KINDS))
    async def test_an_edit_of_its_content_leaves_no_record(
        self, world: World, alice: Any, kind: str
    ) -> None:
        resource_id = await _create(world, kind, alice)

        await world.call(
            "PUT",
            f"{KINDS[kind][0]}/{resource_id}",
            by=alice,
            json={"name": "Renamed", "description": "A new description."},
        )

        assert await world.events(f"{kind}.update") == []

    @pytest.mark.parametrize("kind", ["connector", "kb", "skill"])
    async def test_the_toggle_button(self, world: World, alice: Any, kind: str) -> None:
        resource_id = await _create(world, kind, alice)

        await world.call("POST", f"{KINDS[kind][0]}/{resource_id}/toggle", by=alice)

        await world.one(
            f"{kind}.update",
            actor=alice,
            target=(kind, resource_id),
            changes={"is_active": {"from": True, "to": False}},
        )

    @pytest.mark.parametrize("kind", sorted(KINDS))
    async def test_someone_else_cannot_delete_it_and_nothing_is_recorded(
        self, world: World, alice: Any, kind: str
    ) -> None:
        resource_id = await _create(world, kind, alice)
        mallory = await world.user("mallory")

        await world.call(
            "DELETE", f"{KINDS[kind][0]}/{resource_id}", by=mallory, expect=(403, 404)
        )

        assert await world.events(f"{kind}.delete") == []
        assert await world.get(KINDS[kind][2], resource_id) is not None


class TestOtherWaysOfCreating:
    @pytest.mark.parametrize("kind", ["agent", "connector", "mcp_server", "skill", "workflow"])
    async def test_a_fork_says_what_it_is_a_fork_of(
        self, world: World, alice: Any, kind: str
    ) -> None:
        source_id = await _create(world, kind, alice)

        forked = _data(await world.call("POST", f"{KINDS[kind][0]}/{source_id}/fork", by=alice))

        _, fork = await world.events(f"{kind}.create")
        assert (fork.target_id, fork.detail) == (forked["id"], f"fork of {source_id}")
        assert (fork.actor_id, fork.ip_address is not None) == (alice.id, True)

    @pytest.mark.parametrize(
        ("kind", "listing", "create"),
        [
            ("agent", "/api/agent-templates", "/api/agent-templates/{id}/create"),
            ("connector", "/api/connector-templates", "/api/connector-templates/{id}/create"),
            ("skill", "/api/skill-templates", "/api/skill-templates/{id}/create"),
            ("workflow", "/api/workflow-templates", "/api/workflow-templates/{id}/create-workflow"),
        ],
    )
    async def test_from_a_template(
        self, world: World, alice: Any, kind: str, listing: str, create: str
    ) -> None:
        templates = _data(await world.call("GET", listing, by=alice))
        if isinstance(templates, dict):  # grouped by category
            templates = [t for group in templates.values() for t in group]
        template_id = templates[0]["id"]

        created = _data(
            await world.call(
                "POST", create.format(id=template_id), by=alice, json={"template_id": template_id}
            )
        )

        created_id = created.get("id") or created[kind]["id"]
        row = await world.one(f"{kind}.create", actor=alice, target=(kind, created_id))
        assert row.detail == f"from template {template_id}"

    async def test_a_workflow_from_a_built_in_template(self, world: World, alice: Any) -> None:
        templates = _data(await world.call("GET", "/api/workflows/templates", by=alice))

        created = _data(
            await world.call(
                "POST",
                "/api/workflows/from-template",
                by=alice,
                json={"template_id": templates[0]["id"]},
            )
        )

        row = await world.one("workflow.create", actor=alice, target=("workflow", created["id"]))
        assert row.detail == "from a built-in template"

    async def test_an_imported_workflow(self, world: World, alice: Any) -> None:
        templates = _data(await world.call("GET", "/api/workflows/templates", by=alice))
        source_id = _data(
            await world.call(
                "POST",
                "/api/workflows/from-template",
                by=alice,
                json={"template_id": templates[0]["id"]},
            )
        )["id"]
        exported = (await world.call("GET", f"/api/workflows/{source_id}/export", by=alice)).json()

        imported = _data(
            await world.call("POST", "/api/workflows/import", by=alice, json=exported)
        )

        _, row = await world.events("workflow.create")
        assert (row.target_id, row.detail) == (imported["workflow"]["id"], "imported")

    async def test_an_imported_connector(self, world: World, alice: Any) -> None:
        created = _data(
            await world.call(
                "POST",
                "/api/connectors/import",
                by=alice,
                json={
                    "name": "Billing",
                    "connector_type": "api",
                    "base_url": "https://billing.example.test",
                    "actions": [{"name": "list", "method": "GET", "path": "/invoices"}],
                },
            )
        )

        row = await world.one(
            "connector.create", actor=alice, target=("connector", created["connector"]["id"])
        )
        assert row.detail == "imported; 1 action(s)"

    async def test_a_connector_from_an_openapi_document(self, world: World, alice: Any) -> None:
        spec = {
            "openapi": "3.0.0",
            "info": {"title": "Pets"},
            "servers": [{"url": "https://pets.example.test"}],
            "paths": {
                "/pets": {"get": {"operationId": "listPets"}, "post": {"operationId": "addPet"}}
            },
        }

        created = _data(
            await world.call("POST", "/api/connectors/import-openapi", by=alice, json={"spec": spec})
        )

        row = await world.one("connector.create", actor=alice, target=("connector", created["id"]))
        assert row.detail == "from an OpenAPI document; 2 action(s)"

        # ... and the same document added to an existing connector.
        await world.call(
            "POST",
            f"/api/connectors/{created['id']}/import-openapi",
            by=alice,
            json={"spec": spec, "replace_existing": True},
        )
        imported = await world.one(
            "connector.actions_import", actor=alice, target=("connector", created["id"])
        )
        assert imported.detail == (
            "2 action(s) from an OpenAPI document; replaced the existing ones"
        )

    async def test_drafted_and_refined_by_the_ai_builder(self, world: World, alice: Any) -> None:
        kb_id = await _create(world, "kb", alice)
        drafted = {"name": "Drafted", "instructions": "Be brief.", "tool_categories": []}
        refined = {"name": "Drafted v2", "kb_ids": [kb_id]}

        with patch(
            "fim_one.web.api.agent_ai.structured_llm_call",
            AsyncMock(side_effect=[SimpleNamespace(value=drafted), SimpleNamespace(value=refined)]),
        ):
            created = _data(
                await world.call(
                    "POST", "/api/agents/ai/create", by=alice, json={"instruction": "an agent"}
                )
            )
            agent_id = created["agent"]["id"]
            await world.call(
                "POST",
                f"/api/agents/{agent_id}/ai/refine",
                by=alice,
                json={"instruction": "use the handbook"},
            )

        row = await world.one("agent.create", actor=alice, target=("agent", agent_id))
        assert row.detail == "drafted by the AI builder"
        # The rename is content; binding a knowledge base is not.
        change = await world.one(
            "agent.update",
            actor=alice,
            target=("agent", agent_id),
            changes={"kb_ids": {"from": None, "to": [kb_id]}},
        )
        assert change.detail == "by the AI builder"


class TestAgentSafetySettings:
    async def test_confirmation_settings_and_bindings_are_recorded_with_before_and_after(
        self, world: World, alice: Any
    ) -> None:
        connector_id = await _create(world, "connector", alice)
        agent_id = await _create(world, "agent", alice)

        await world.call(
            "PUT",
            f"/api/agents/{agent_id}",
            by=alice,
            json={
                "name": "Helper 2",
                "require_confirmation_for_all": True,
                "confirmation_approver_scope": "agent_owner",
                "connector_ids": [connector_id],
            },
        )

        row = await world.one("agent.update", actor=alice, target=("agent", agent_id))
        assert row.changes is not None
        assert row.changes["require_confirmation_for_all"] == {"from": False, "to": True}
        assert row.changes["connector_ids"] == {"from": None, "to": [connector_id]}
        assert row.changes["confirmation_approver_scope"]["to"] == "agent_owner"
        assert "name" not in row.changes

    async def test_saving_the_same_settings_again_records_nothing(
        self, world: World, alice: Any
    ) -> None:
        agent_id = await _create(world, "agent", alice)
        body = {"require_confirmation_for_all": True}
        await world.call("PUT", f"/api/agents/{agent_id}", by=alice, json=body)

        await world.call("PUT", f"/api/agents/{agent_id}", by=alice, json=body)

        assert len(await world.events("agent.update")) == 1


class TestConnectorSafetySettings:
    async def test_creating_one_with_a_credential_says_so_without_the_credential(
        self, world: World, alice: Any
    ) -> None:
        connector_id = await _create(
            world,
            "connector",
            alice,
            auth_type="bearer",
            auth_config={"token_prefix": "Bearer", "default_token": TOKEN},
            allow_fallback=True,
        )

        row = await world.one(
            "connector.create", actor=alice, target=("connector", connector_id)
        )
        assert row.changes == {
            "is_active": {"from": None, "to": True},
            "allow_fallback": {"from": None, "to": True},
            "type": {"from": None, "to": "api"},
            "base_url": {"from": None, "to": "https://crm.example.test"},
            "auth_type": {"from": None, "to": "bearer"},
            "default_credential": {"changed": True},
        }
        world.assert_absent(row, TOKEN)

    async def test_the_fallback_switch_the_address_and_a_new_credential(
        self, world: World, alice: Any
    ) -> None:
        connector_id = await _create(world, "connector", alice)

        await world.call(
            "PUT",
            f"/api/connectors/{connector_id}",
            by=alice,
            json={
                "allow_fallback": True,
                "base_url": "https://user:pw@crm2.example.test/v2?key=abc123",
                "auth_type": "bearer",
                "auth_config": {"default_token": TOKEN},
            },
        )

        row = await world.one(
            "connector.update",
            actor=alice,
            target=("connector", connector_id),
            changes={
                "allow_fallback": {"from": False, "to": True},
                # Where it points, not what authenticates it.
                "base_url": {"from": "https://crm.example.test", "to": "https://crm2.example.test/v2"},
                "auth_type": {"from": "none", "to": "bearer"},
                "default_credential": {"changed": True},
            },
        )
        world.assert_absent(row, TOKEN, "user:pw", "abc123")


class TestConnectorActions:
    async def test_create_change_and_delete(self, world: World, alice: Any) -> None:
        connector_id = await _create(world, "connector", alice)
        actions_path = f"/api/connectors/{connector_id}/actions"

        action = _data(
            await world.call(
                "POST",
                actions_path,
                by=alice,
                json={"name": "delete customer", "method": "DELETE", "path": "/customers/{id}",
                      "requires_confirmation": True},
            )
        )
        await world.call(
            "PUT",
            f"{actions_path}/{action['id']}",
            by=alice,
            json={"requires_confirmation": False, "description": "Removes a customer."},
        )
        await world.call("DELETE", f"{actions_path}/{action['id']}", by=alice)

        target = ("connector_action", action["id"])
        created = await world.one(
            "connector.action_create",
            actor=alice,
            target=target,
            changes={
                "requires_confirmation": {"from": None, "to": True},
                "method": {"from": None, "to": "DELETE"},
                "path": {"from": None, "to": "/customers/{id}"},
            },
        )
        assert (created.target_label, created.detail) == (
            "delete customer",
            f"connector={connector_id}",
        )
        # Asking a human first was switched off: that is the change.
        await world.one(
            "connector.action_update",
            actor=alice,
            target=target,
            changes={"requires_confirmation": {"from": True, "to": False}},
        )
        deleted = await world.one("connector.action_delete", actor=alice, target=target)
        assert deleted.detail == f"connector={connector_id}; DELETE /customers/{{id}}"
        assert await world.get(ConnectorAction, action["id"]) is None

    async def test_an_edit_of_an_actions_description_leaves_no_record(
        self, world: World, alice: Any
    ) -> None:
        connector_id = await _create(world, "connector", alice)
        actions_path = f"/api/connectors/{connector_id}/actions"
        action = _data(
            await world.call("POST", actions_path, by=alice, json={"name": "list", "path": "/x"})
        )

        await world.call(
            "PUT", f"{actions_path}/{action['id']}", by=alice, json={"description": "Lists."}
        )

        assert await world.events("connector.action_update") == []


class TestMcpServerSettings:
    async def test_headers_and_arguments_are_recorded_as_changed_never_by_value(
        self, world: World, alice: Any
    ) -> None:
        header = "Bearer mcp-owner-0123456789abcdef"
        server_id = await _create(
            world, "mcp_server", alice, headers={"Authorization": header}
        )

        created = await world.one(
            "mcp_server.create", actor=alice, target=("mcp_server", server_id)
        )
        assert created.changes == {
            "is_active": {"from": None, "to": True},
            "allow_fallback": {"from": None, "to": False},
            "transport": {"from": None, "to": "streamable_http"},
            "url": {"from": None, "to": "https://mcp.example.test/mcp"},
            "headers": {"changed": True},
        }
        world.assert_absent(created, header, "mcp-owner-0123456789abcdef")

        await world.call(
            "PUT",
            f"/api/mcp-servers/{server_id}",
            by=alice,
            json={
                "allow_fallback": True,
                "url": "https://mcp.example.test/mcp?token=s3cr3t-in-the-query",
                "headers": {"Authorization": header + "-rotated"},
            },
        )

        updated = await world.one(
            "mcp_server.update",
            actor=alice,
            target=("mcp_server", server_id),
            changes={
                "allow_fallback": {"from": False, "to": True},
                # The address differs only in the token: changed, no more.
                "url": {"changed": True},
                "headers": {"changed": True},
            },
        )
        world.assert_absent(updated, header, "s3cr3t-in-the-query")


class TestDatabaseFences:
    async def test_unmasking_a_column_and_hiding_a_table(self, world: World, alice: Any) -> None:
        await world.add(
            Connector(id="db-1", user_id=alice.id, name="warehouse", type="database"),
        )
        await world.add(DatabaseSchema(id="tbl-1", connector_id="db-1", table_name="staff"))
        await world.add(
            SchemaColumn(
                id="col-1", schema_id="tbl-1", column_name="salary", data_type="int", is_pii=True
            )
        )

        await world.call(
            "PUT",
            "/api/connectors/db-1/schemas/tbl-1/columns/col-1",
            by=alice,
            json={"is_pii": False, "description": "Monthly, before tax."},
        )
        await world.call(
            "PUT", "/api/connectors/db-1/schemas/tbl-1", by=alice, json={"is_visible": False}
        )
        await world.call(
            "PUT",
            "/api/connectors/db-1/schemas/bulk",
            by=alice,
            json={
                "tables": [
                    {
                        "table_name": "staff",
                        "is_visible": True,
                        "columns": [{"column_name": "salary", "is_pii": True}],
                    }
                ]
            },
        )

        unmasked, hidden, bulk = await world.events("connector.schema_fence_update")
        assert unmasked.changes == {"staff.salary.is_pii": {"from": True, "to": False}}
        assert hidden.changes == {"staff.is_visible": {"from": True, "to": False}}
        assert bulk.changes == {
            "staff.is_visible": {"from": False, "to": True},
            "staff.salary.is_pii": {"from": False, "to": True},
        }
        for row in (unmasked, hidden, bulk):
            assert (row.actor_id, row.target_type, row.target_id) == (alice.id, "connector", "db-1")
            assert row.ip_address is not None

    async def test_an_annotation_leaves_no_record(self, world: World, alice: Any) -> None:
        await world.add(Connector(id="db-1", user_id=alice.id, name="warehouse", type="database"))
        await world.add(DatabaseSchema(id="tbl-1", connector_id="db-1", table_name="staff"))

        await world.call(
            "PUT",
            "/api/connectors/db-1/schemas/tbl-1",
            by=alice,
            json={"description": "Everyone on payroll."},
        )

        assert await world.events() == []


class TestUnpublishingSomeoneElsesResource:
    @pytest.mark.parametrize(
        ("kind", "model"),
        [
            ("agent", Agent),
            ("connector", Connector),
            ("mcp_server", MCPServer),
            ("skill", Skill),
            ("workflow", Workflow),
        ],
    )
    async def test_an_organization_admin_unpublishes_a_members_resource(
        self, world: World, alice: Any, kind: str, model: Any
    ) -> None:
        bob = await world.user("bob")
        acme = await world.org("Acme", alice, member=bob)
        resource_id = await _create(world, kind, bob)
        async with world.factory() as db:
            row = await db.get(model, resource_id)
            row.visibility, row.org_id, row.publish_status = "org", acme.id, "approved"
            await db.commit()

        await world.call("POST", f"{KINDS[kind][0]}/{resource_id}/unpublish", by=alice)

        # Who unpublished whose: the actor is the administrator, the detail the owner.
        event = await world.one(
            f"{kind}.unpublish_by_admin", actor=alice, org=acme, target=(kind, resource_id)
        )
        assert event.detail == f"owner={bob.id}"
        # The organization's review trail has its entry as before.
        (review,) = await world.reviews()
        assert (review.action, review.actor_id, review.org_id) == ("unpublished", alice.id, acme.id)

    async def test_a_platform_administrator_pressing_unpublish_on_a_personal_resource(
        self, world: World, alice: Any
    ) -> None:
        """Nothing was published, so nothing was taken back: no record."""
        root = await world.user("root", admin=True)
        agent_id = await _create(world, "agent", alice)

        await world.call("POST", f"/api/agents/{agent_id}/unpublish", by=root)

        assert await world.events("agent.unpublish_by_admin") == []
        assert await world.reviews() == []

    async def test_the_owner_unpublishing_their_own_is_in_the_review_trail_only(
        self, world: World, alice: Any
    ) -> None:
        acme = await world.org("Acme", alice)
        agent_id = await _create(world, "agent", alice)
        async with world.factory() as db:
            row = await db.get(Agent, agent_id)
            row.visibility, row.org_id, row.publish_status = "org", acme.id, "approved"
            await db.commit()

        await world.call("POST", f"/api/agents/{agent_id}/unpublish", by=alice)

        assert await world.events("agent.unpublish_by_admin") == []
        assert [r.action for r in await world.reviews()] == ["unpublished"]


class TestSubscriptions:
    async def test_subscribing_and_unsubscribing(self, world: World, alice: Any) -> None:
        bob = await world.user("bob")
        acme = await world.org("Acme", alice, member=bob)
        connector_id = await _create(world, "connector", alice)
        async with world.factory() as db:
            row = await db.get(Connector, connector_id)
            row.visibility, row.org_id, row.publish_status = "org", acme.id, "approved"
            await db.commit()
        body = {"resource_type": "connector", "resource_id": connector_id, "org_id": acme.id}

        await world.call("POST", "/api/market/subscribe", by=bob, json=body)
        await world.call("POST", "/api/market/subscribe", by=bob, json=body)  # already subscribed
        await world.call("DELETE", "/api/market/unsubscribe", by=bob, json=body)

        await world.one(
            "market.subscribe", actor=bob, org=acme, target=("connector", connector_id)
        )
        await world.one(
            "market.unsubscribe", actor=bob, org=acme, target=("connector", connector_id)
        )
        async with world.factory() as db:
            from sqlalchemy import select

            assert (await db.execute(select(ResourceSubscription))).scalars().all() == []


class TestWorkflowRunRecords:
    async def _runs(self, world: World, alice: Any) -> str:
        workflow_id = await _create(world, "workflow", alice)
        await world.add(
            *(
                WorkflowRun(
                    id=f"run-{n}",
                    workflow_id=workflow_id,
                    user_id=alice.id,
                    blueprint_snapshot={"nodes": []},
                    status=status,
                    mode="manual",
                )
                for n, status in enumerate(("completed", "failed", "running"))
            )
        )
        return workflow_id

    async def test_deleting_one_run(self, world: World, alice: Any) -> None:
        workflow_id = await self._runs(world, alice)

        await world.call("DELETE", f"/api/workflows/{workflow_id}/runs/run-0", by=alice)

        row = await world.one(
            "workflow.run_delete", actor=alice, target=("workflow", workflow_id)
        )
        assert row.detail == "run=run-0; status=completed; mode=manual"

    async def test_clearing_the_finished_runs(self, world: World, alice: Any) -> None:
        workflow_id = await self._runs(world, alice)

        await world.call("DELETE", f"/api/workflows/{workflow_id}/runs", by=alice)
        await world.call("DELETE", f"/api/workflows/{workflow_id}/runs", by=alice)  # none left

        row = await world.one(
            "workflow.runs_clear", actor=alice, target=("workflow", workflow_id)
        )
        assert row.detail == "deleted 2 finished run(s)"
