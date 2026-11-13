"""A plain, valid request to each audited route that has no test of its own.

The tests named after a group of actions (``tests/test_audit_*.py``) show
what each new record holds. The routes here already wrote a record before
this work (the platform administration endpoints, the review trail); what
is shown for them is the minimum: a valid request reaches the route and a
row with the listed action appears. ``test_route_proofs.py`` reads what
every request of both sets of tests wrote and requires each audited route
to be among them.
"""

from __future__ import annotations

import os
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from fim_one.db.models import Agent, ConnectorCredential, MCPServerCredential
from fim_one.db.models.connector import Connector
from fim_one.db.models.conversation import Conversation
from fim_one.db.models.eval import EvalDataset, EvalRun
from fim_one.db.models.knowledge_base import KnowledgeBase
from fim_one.db.models.mcp_server import MCPServer
from fim_one.db.models.skill import Skill
from fim_one.db.models.workflow import Workflow
from tests.audit.harness import World, world  # noqa: F401

pytestmark = pytest.mark.timeout(120)


async def _wrote(world: World, *actions: str) -> None:
    """Each of *actions* has at least one row by now."""
    written = {row.action for row in await world.events()}
    assert set(actions) <= written, f"missing {set(actions) - written}; have {sorted(written)}"


@pytest.fixture()
async def root(world: World) -> Any:
    await world.enable_module("skills")
    await world.enable_module("workflows")
    return await world.user("root", admin=True)


class TestUsersAndSessions:
    async def test_creating_and_managing_a_user(self, world: World, root: Any) -> None:
        created = (
            await world.call(
                "POST",
                "/api/admin/users",
                by=root,
                json={"username": "carol", "email": "carol@example.test", "password": "pw-123456"},
            )
        ).json()
        user = f"/api/admin/users/{created['id']}"

        await world.call("PATCH", f"{user}/admin", by=root, json={"is_admin": True})
        await world.call("PATCH", f"{user}/admin", by=root, json={"is_admin": False})
        await world.call("PATCH", f"{user}/quota", by=root, json={"token_quota": 5})
        await world.call("POST", f"{user}/reset-password", by=root, json={"new_password": "pw-7"*3})
        await world.call("POST", f"{user}/force-logout", by=root)
        await world.call("POST", "/api/admin/actions/force-logout-all", by=root)
        await world.call("DELETE", f"/api/admin/storage/user/{created['id']}", by=root)
        await world.call("DELETE", user, by=root)

        await _wrote(
            world,
            "user.create", "user.grant_admin", "user.revoke_admin", "user.set_quota", "user.reset_password", "user.force_logout",
            "auth.force_logout_all", "storage.clear_user", "user.delete",
        )  # fmt: skip

    async def test_api_keys(self, world: World, root: Any) -> None:
        created = (
            await world.call("POST", "/api/admin/api-keys", by=root, json={"name": "ci"})
        ).json()
        key = f"/api/admin/api-keys/{created['id']}"

        await world.call("PATCH", f"{key}/active", by=root, json={"is_active": False})
        await world.call("PATCH", f"{key}/active", by=root, json={"is_active": True})
        await world.call("DELETE", key, by=root)

        await _wrote(world, "api_key.create", "api_key.disable", "api_key.enable", "api_key.delete")

    async def test_ip_rules(self, world: World, root: Any) -> None:
        created = (
            await world.call(
                "POST",
                "/api/admin/ip-rules",
                by=root,
                json={"ip_address": "198.51.100.9", "rule_type": "deny"},
            )
        ).json()
        rule = f"/api/admin/ip-rules/{created['id']}"

        await world.call("PATCH", f"{rule}/active", by=root, json={"is_active": False})
        await world.call("PATCH", f"{rule}/active", by=root, json={"is_active": True})
        await world.call("DELETE", rule, by=root)
        await world.call(
            "POST",
            "/api/admin/ip-rules/batch-import",
            by=root,
            json={"rules": [{"ip_address": "198.51.100.10", "rule_type": "deny"}]},
        )

        await _wrote(
            world, "ip_rule.create", "ip_rule.disable", "ip_rule.enable", "ip_rule.delete",
            "ip_rule.batch_import",
        )  # fmt: skip

    async def test_revoking_someone_elses_credentials(self, world: World, root: Any) -> None:
        bob = await world.user("bob")
        await world.add(
            Connector(id="conn-1", user_id=root.id, name="crm", base_url="https://c.test"),
            MCPServer(id="srv-1", user_id=root.id, name="files", transport="streamable_http",
                      url="https://m.test/mcp"),
        )
        await world.add(
            ConnectorCredential(id="cc-1", connector_id="conn-1", user_id=bob.id, credentials_blob="x"),
            MCPServerCredential(id="mc-1", server_id="srv-1", user_id=bob.id),
        )

        await world.call("DELETE", "/api/admin/credentials/connector/cc-1", by=root)
        await world.call("DELETE", "/api/admin/credentials/mcp/mc-1", by=root)

        assert len(await world.events("credential.admin_revoke")) == 2


class TestSettingsAndContent:
    async def test_announcements_sensitive_words_and_notifications(
        self, world: World, root: Any
    ) -> None:
        announcement = (
            await world.call(
                "POST", "/api/admin/announcements", by=root, json={"title": "t", "content": "c"}
            )
        ).json()
        path = f"/api/admin/announcements/{announcement['id']}"
        await world.call("PUT", path, by=root, json={"title": "t2"})
        await world.call("DELETE", path, by=root)

        word = (
            await world.call("POST", "/api/admin/sensitive-words", by=root, json={"word": "secret"})
        ).json()
        await world.call(
            "PUT", f"/api/admin/sensitive-words/{word['id']}/toggle", by=root,
            json={"is_active": False},
        )
        await world.call("DELETE", f"/api/admin/sensitive-words/{word['id']}", by=root)
        await world.call(
            "POST", "/api/admin/sensitive-words/batch", by=root, json={"words": ["a1", "b2"]}
        )

        await world.call(
            "PUT", "/api/admin/notifications/config", by=root,
            json={"new_user_registration": True},
        )
        with (
            patch("fim_one.web.email._smtp_configured", return_value=True),
            patch("fim_one.web.email._send_email"),
        ):
            await world.call("POST", "/api/admin/notifications/test", by=root)

        await _wrote(
            world,
            "announcement.create", "announcement.update", "announcement.delete",
            "sensitive_word.create", "sensitive_word.toggle", "sensitive_word.delete",
            "sensitive_word.batch_import", "notifications.config_update", "notifications.test",
        )  # fmt: skip

    async def test_storage_and_exports(self, world: World, root: Any, tmp_path: Any) -> None:
        (tmp_path / "conversations" / "gone-conversation").mkdir(parents=True)
        with patch("fim_one.web.api.admin._UPLOADS_CONVERSATIONS_DIR", tmp_path / "conversations"):
            await world.call("DELETE", "/api/admin/storage/orphaned", by=root)
        await world.call("GET", "/api/admin/export/full-backup", by=root)
        await world.call("GET", "/api/admin/model-config/export", by=root)

        await _wrote(world, "storage.cleanup_orphaned", "export.full_backup", "model_config.export")

    async def test_someone_elses_conversation(self, world: World, root: Any) -> None:
        bob = await world.user("bob")
        await world.add(Conversation(id="cv-1", user_id=bob.id, title="t", mode="react"))

        await world.call("GET", "/api/admin/conversations/cv-1/messages", by=root)
        await world.call("DELETE", "/api/admin/conversations/cv-1", by=root)

        await _wrote(world, "conversation.viewed", "conversation.delete")

    @pytest.mark.skipif(
        not (
            os.environ.get("STRIPE_SECRET_KEY", "").strip()
            and os.environ.get("STRIPE_WEBHOOK_SECRET", "").strip()
        ),
        reason="STRIPE_SECRET_KEY and STRIPE_WEBHOOK_SECRET not set",
    )
    async def test_billing(self, world: World, root: Any) -> None:
        await world.call("POST", "/api/admin/system/billing/activate", by=root)
        await world.call("POST", "/api/admin/system/billing/toggle", by=root, json={"enabled": False})
        await world.call("POST", "/api/admin/system/billing/toggle", by=root, json={"enabled": True})
        plan = (
            await world.call(
                "POST", "/api/admin/billing/plans", by=root,
                json={"slug": "team", "name": "Team", "monthly_token_quota": 10},
            )
        ).json()
        path = f"/api/admin/billing/plans/{plan['id']}"
        await world.call("PATCH", path, by=root, json={"name": "Team 2"})
        await world.call("DELETE", path, by=root)

        await _wrote(
            world, "billing.activate", "billing.disable", "billing.enable",
            "billing_plan.create", "billing_plan.update", "billing_plan.delete",
        )  # fmt: skip


class TestModels:
    async def test_models(self, world: World, root: Any) -> None:
        model = (
            await world.call(
                "POST", "/api/admin/models", by=root,
                json={"name": "m", "model_name": "gpt-x", "base_url": "https://llm.test/v1",
                      "api_key": "k"},
            )
        ).json()
        path = f"/api/admin/models/{model['id']}"
        await world.call("PUT", path, by=root, json={"name": "m2"})
        await world.call("PATCH", f"{path}/active", by=root, json={"is_active": False})
        await world.call("PATCH", f"{path}/active", by=root, json={"is_active": True})
        await world.call("PATCH", f"{path}/role", by=root, json={"role": "general"})
        await world.call("DELETE", path, by=root)

        await _wrote(
            world, "model.create", "model.update", "model.disable", "model.enable",
            "model.set_role", "model.delete",
        )  # fmt: skip

    async def test_providers_their_models_and_groups(self, world: World, root: Any) -> None:
        provider = (
            await world.call(
                "POST", "/api/admin/model-providers", by=root,
                json={"name": "prov", "base_url": "https://llm.test/v1", "api_key": "k"},
            )
        ).json()
        provider_path = f"/api/admin/model-providers/{provider['id']}"
        await world.call("PUT", provider_path, by=root, json={"name": "prov2"})
        first, second = [
            (
                await world.call(
                    "POST", f"{provider_path}/models", by=root,
                    json={"name": name, "model_name": name},
                )
            ).json()
            for name in ("alpha", "beta")
        ]
        await world.call(
            "PUT", f"/api/admin/model-provider-models/{first['id']}", by=root, json={"name": "a2"}
        )

        group = (
            await world.call("POST", "/api/admin/model-groups", by=root, json={"name": "g"})
        ).json()
        group_path = f"/api/admin/model-groups/{group['id']}"
        await world.call("PUT", group_path, by=root, json={"name": "g2"})
        await world.call("PATCH", f"{group_path}/activate", by=root)
        await world.call("POST", "/api/admin/model-groups/deactivate", by=root)
        await world.call("DELETE", group_path, by=root)

        await world.call("DELETE", f"/api/admin/model-provider-models/{first['id']}", by=root)
        await world.call(
            "POST", "/api/admin/model-provider-models/batch-delete", by=root,
            json={"ids": [second["id"]]},
        )
        await world.call("DELETE", provider_path, by=root)

        await _wrote(
            world,
            "model_provider.create", "model_provider.update", "model_provider.delete",
            "model_provider_model.create", "model_provider_model.update",
            "model_provider_model.delete", "model_provider_model.batch_delete",
            "model_group.create", "model_group.update", "model_group.activate",
            "model_group.deactivate_all", "model_group.delete",
        )  # fmt: skip

    async def test_importing_a_model_config(self, world: World, root: Any) -> None:
        exported = (await world.call("GET", "/api/admin/model-config/export", by=root)).json()

        await world.call("POST", "/api/admin/model-config/import", by=root, json=exported)

        await _wrote(world, "model_config.import")


class TestModeratingResources:
    async def _resources(self, world: World, owner_id: str) -> None:
        await world.add(
            Agent(id="ag-1", user_id=owner_id, name="A1"),
            Agent(id="ag-2", user_id=owner_id, name="A2"),
            Connector(id="cn-1", user_id=owner_id, name="C1", base_url="https://c.test"),
            KnowledgeBase(id="kb-1", user_id=owner_id, name="K1"),
            KnowledgeBase(id="kb-2", user_id=owner_id, name="K2"),
            Skill(id="sk-1", user_id=owner_id, name="S1", content="x"),
            Skill(id="sk-2", user_id=owner_id, name="S2", content="x"),
            Workflow(id="wf-1", user_id=owner_id, name="W1", blueprint={"nodes": []},
                     schedule_cron="* * * * *", schedule_enabled=True),
            Workflow(id="wf-2", user_id=owner_id, name="W2", blueprint={"nodes": []}),
        )

    async def test_agents_connectors_and_knowledge_bases(self, world: World, root: Any) -> None:
        bob = await world.user("bob")
        await self._resources(world, bob.id)
        off = {"is_active": False}

        await world.call("POST", "/api/admin/agents/batch-toggle", by=root, json={"ids": ["ag-1"], **off})
        await world.call("POST", "/api/admin/agents/batch-delete", by=root, json={"ids": ["ag-1"]})
        await world.call("DELETE", "/api/admin/agents/ag-2", by=root)
        await world.call("POST", "/api/admin/connectors/batch-toggle", by=root, json={"ids": ["cn-1"], **off})
        await world.call("POST", "/api/admin/connectors/batch-delete", by=root, json={"ids": ["cn-1"]})
        await world.call("POST", "/api/admin/knowledge-bases/batch-toggle", by=root, json={"ids": ["kb-1"], **off})
        await world.call("POST", "/api/admin/knowledge-bases/batch-delete", by=root, json={"ids": ["kb-1"]})
        await world.call("DELETE", "/api/admin/knowledge-bases/kb-2", by=root)

        await _wrote(
            world,
            "agent.admin_batch_toggle", "agent.admin_batch_delete", "agent.admin_delete",
            "connector.admin_batch_toggle", "connector.admin_batch_delete",
            "kb.admin_batch_toggle", "kb.admin_batch_delete", "kb.admin_delete",
        )  # fmt: skip

    async def test_skills_workflows_and_schedules(self, world: World, root: Any) -> None:
        bob = await world.user("bob")
        await self._resources(world, bob.id)
        off = {"is_active": False}

        await world.call("PATCH", "/api/admin/skills/sk-1/active", by=root, json=off)
        await world.call("PATCH", "/api/admin/skills/sk-1/active", by=root, json={"is_active": True})
        await world.call("POST", "/api/admin/skills/batch-toggle", by=root, json={"ids": ["sk-1"], **off})
        await world.call("POST", "/api/admin/skills/batch-delete", by=root, json={"ids": ["sk-1"]})
        await world.call("DELETE", "/api/admin/skills/sk-2", by=root)

        await world.call("PATCH", "/api/admin/schedules/wf-1/active", by=root, json={"schedule_enabled": False})
        await world.call("PATCH", "/api/admin/schedules/wf-1/active", by=root, json={"schedule_enabled": True})
        await world.call("POST", "/api/admin/workflows/wf-1/toggle", by=root)
        await world.call("POST", "/api/admin/workflows/batch-toggle", by=root, json={"workflow_ids": ["wf-1"], **off})
        await world.call("POST", "/api/admin/workflows/batch-publish", by=root, json={"workflow_ids": ["wf-1"], "status": "active"})
        await world.call("POST", "/api/admin/workflows/cleanup-runs", by=root)
        await world.call("POST", "/api/admin/workflows/batch-delete", by=root, json={"workflow_ids": ["wf-1"]})
        await world.call("DELETE", "/api/admin/workflows/wf-2", by=root)

        await _wrote(
            world,
            "skill.admin_disable", "skill.admin_enable", "skill.admin_batch_toggle",
            "skill.admin_batch_delete", "skill.admin_delete",
            "schedule.admin_pause", "schedule.admin_resume", "workflow.admin_toggle",
            "workflow.admin_batch_toggle", "workflow.admin_batch_publish",
            "workflow.cleanup_runs", "workflow.admin_batch_delete", "workflow.admin_delete",
        )  # fmt: skip

    async def test_a_suspended_workflow_turned_back_on(self, world: World, root: Any) -> None:
        from datetime import UTC, datetime

        bob = await world.user("bob")
        await world.add(
            Workflow(id="wf-s", user_id=bob.id, name="S", blueprint={"nodes": []},
                     webhook_url="https://hooks.example.test/x", access_suspended_at=datetime.now(UTC))
        )
        await world.call("POST", "/api/workflows/wf-s/resume-access", by=bob)
        await _wrote(world, "workflow.update")

    async def test_eval_data_and_workflow_templates(self, world: World, root: Any) -> None:
        bob = await world.user("bob")
        await world.add(Agent(id="ag-1", user_id=bob.id, name="A1"))
        await world.add(EvalDataset(id="ds-1", user_id=bob.id, name="D1"))
        await world.add(
            EvalRun(id="er-1", user_id=bob.id, dataset_id="ds-1", agent_id="ag-1", status="completed")
        )

        await world.call("POST", "/api/admin/eval/cleanup", by=root, json={"max_age_days": 30})
        await world.call("DELETE", "/api/admin/eval/runs/er-1", by=root)
        await world.call("DELETE", "/api/admin/eval/datasets/ds-1", by=root)

        template = (
            await world.call(
                "POST", "/api/admin/workflow-templates", by=root,
                json={"name": "T", "description": "d", "icon": "x", "category": "general",
                      "blueprint": {"nodes": [], "edges": []}},
            )
        ).json()["data"]
        path = f"/api/admin/workflow-templates/{template['id']}"
        await world.call("PUT", path, by=root, json={"name": "T2"})
        await world.call("DELETE", path, by=root)

        await _wrote(
            world, "eval_run.admin_cleanup", "eval_run.admin_delete", "eval_dataset.admin_delete",
            "workflow_template.create", "workflow_template.update", "workflow_template.delete",
        )  # fmt: skip


def _data(response: Any) -> Any:
    return response.json()["data"]


class TestPublishingAndReview:
    """The review trail (``review_log``): submit, resubmit, approve, reject."""

    @pytest.mark.parametrize(
        ("kind", "path", "body"),
        [
            ("agent", "/api/agents", {"name": "A"}),
            ("connector", "/api/connectors", {"name": "C", "base_url": "https://c.test"}),
            ("mcp_server", "/api/mcp-servers",
             {"name": "M", "transport": "streamable_http", "url": "https://m.test/mcp"}),
            ("skill", "/api/skills", {"name": "S", "content": "x"}),
            ("workflow", "/api/workflows", {"name": "W"}),
        ],
    )
    async def test_submitting_and_resubmitting(
        self, world: World, root: Any, kind: str, path: str, body: dict[str, Any]
    ) -> None:
        alice = await world.user("alice")
        bob = await world.user("bob")
        acme = await world.org("Acme", alice, member=bob)
        async with world.factory() as db:
            from fim_one.db.models.organization import Organization

            org = await db.get(Organization, acme.id)
            for flag in ("review_agents", "review_connectors", "review_mcp_servers",
                         "review_skills", "review_workflows"):
                setattr(org, flag, True)
            await db.commit()
        resource = _data(await world.call("POST", path, by=bob, json=body))["id"]

        await world.call(
            "POST", f"{path}/{resource}/publish", by=bob, json={"scope": "org", "org_id": acme.id}
        )
        await world.call(
            "POST", f"/api/orgs/{acme.id}/reviews/reject", by=alice,
            json={"resource_type": kind, "resource_id": resource, "note": "no"},
        )
        await world.call("POST", f"{path}/{resource}/resubmit", by=bob)
        await world.call(
            "POST", f"/api/orgs/{acme.id}/reviews/approve", by=alice,
            json={"resource_type": kind, "resource_id": resource},
        )

        assert [r.action for r in await world.reviews()] == [
            "submitted", "rejected", "resubmitted", "approved",
        ]  # fmt: skip

    async def test_an_administrators_batch_review_and_the_market(
        self, world: World, root: Any
    ) -> None:
        from fim_one.web.platform import MARKET_ORG_ID, ensure_market_org

        bob = await world.user("bob")
        async with world.factory() as db:
            await ensure_market_org(db, owner_id=root.id)
            await db.commit()
        await world.add(
            Agent(id="ag-1", user_id=bob.id, name="A1", visibility="org", org_id=MARKET_ORG_ID,
                  publish_status="pending_review", status="published"),
            Agent(id="ag-2", user_id=bob.id, name="A2", visibility="org", org_id=MARKET_ORG_ID,
                  publish_status="pending_review", status="published"),
            Agent(id="ag-3", user_id=bob.id, name="A3", visibility="org", org_id=MARKET_ORG_ID,
                  publish_status="approved", status="published"),
        )

        one = {"resource_type": "agent", "resource_id": "ag-1"}
        two = {"resource_type": "agent", "resource_id": "ag-2"}
        await world.call("POST", "/api/admin/reviews/batch-approve", by=root, json={"items": [one]})
        await world.call(
            "POST", "/api/admin/reviews/batch-reject", by=root, json={"items": [two], "note": "no"}
        )
        await world.call(
            "PATCH", "/api/admin/market/resources/agent/ag-1/unpublish", by=root
        )
        await world.call("DELETE", "/api/admin/market/resources/agent/ag-3", by=root)

        await _wrote(
            world, "review.admin_batch_approve", "review.admin_batch_reject",
            "market.resource_unpublish", "market.resource_delete",
        )  # fmt: skip
        assert {r.action for r in await world.reviews()} >= {"approved", "rejected"}


class TestOtherWaysIn:
    async def test_a_connector_from_a_config_document(self, world: World, root: Any) -> None:
        config = (
            "name: Billing\nbase_url: https://billing.example.test\nauth:\n  type: none\n"
            "actions:\n  - name: list\n    method: GET\n    path: /invoices\n"
        )

        await world.call(
            "POST", "/api/connectors/from-config", by=root, json={"config": config, "format": "yaml"}
        )

        await _wrote(world, "connector.create")

    async def test_the_ai_builder_drafting_a_connector_and_its_actions(
        self, world: World, root: Any
    ) -> None:
        plan = {
            "mode": "generate",
            "connector": {"name": "Drafted", "base_url": "https://d.example.test"},
            "actions": [{"name": "list", "method": "GET", "path": "/x"}],
        }
        more = [{"name": "get", "method": "GET", "path": "/x/{id}"}]

        with patch(
            "fim_one.web.api.connector_ai.structured_llm_call",
            AsyncMock(side_effect=[SimpleNamespace(value=plan), SimpleNamespace(value=more)]),
        ):
            created = _data(
                await world.call(
                    "POST", "/api/connectors/ai/create", by=root, json={"instruction": "a CRM"}
                )
            )
            connector_id = created["connector"]["id"]
            await world.call(
                "POST", f"/api/connectors/{connector_id}/ai/generate-actions", by=root,
                json={"instruction": "one more"},
            )

        await _wrote(world, "connector.create", "connector.actions_import")

    @pytest.mark.parametrize("decision", ["approve", "reject"])
    async def test_a_decision_through_a_channels_card(
        self, world: World, root: Any, decision: str
    ) -> None:
        """The callback route itself, with the carrier's parsing stubbed."""
        from fim_one.core.channels import CarrierDecision
        from fim_one.core.channels.feishu import FeishuChannel
        from fim_one.db.models import UserOAuthBinding
        from fim_one.db.models.channel import Channel, ConfirmationRequest

        acme = await world.org("Acme", root)
        await world.add(
            Channel(id="chan-1", name="approvals", type="feishu", org_id=acme.id, config={},
                    created_by=root.id),
            UserOAuthBinding(user_id=root.id, provider="feishu", oauth_id="ou_root"),
        )
        await world.add(
            ConfirmationRequest(
                id="cnf-1", kind="confirmation", mode="channel", user_id=root.id,
                org_id=acme.id, channel_id="chan-1", payload={"tool_name": "delete_customer"},
                eligible_scope="initiator", eligible_user_id=root.id,
            )
        )
        carrier = MagicMock(spec=FeishuChannel)
        carrier.verify_signature = AsyncMock(return_value=True)
        carrier.decrypt_callback = lambda parsed: parsed
        carrier.handle_callback = AsyncMock(return_value={})
        carrier.parse_decision = lambda parsed: CarrierDecision(
            confirmation_id="cnf-1", decision=decision, actor="ou_root"  # type: ignore[arg-type]
        )

        with patch("fim_one.web.api.channels.build_channel", return_value=carrier):
            await world.call("POST", "/api/channels/chan-1/callback", json={"any": "thing"})

        await _wrote(world, f"confirmation.{decision}")

