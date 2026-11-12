"""An agent whose model settings name their own endpoint is its owner's alone.

Unless an administrator turns on ``shared_custom_model_endpoints``:

* such an agent cannot be published, re-submitted, or given an endpoint
  while it is shared;
* an already-shared one is refused to everyone but its owner
  (``agent_model_endpoint_private``), in chat, delegation and workflow
  steps alike, and is left out of the agents offered for delegation.

Agents that select a configured model are not affected.
"""

from __future__ import annotations

from typing import Any

import pytest

from fim_one.core.assembly.errors import AssemblyRefusal
from fim_one.core.assembly.principal import CallContext, provisional_principal
from fim_one.core.assembly.visibility import ResourceKind, authorize_use, list_usable
from fim_one.db.models import Agent
from fim_one.db.models.model_config import ModelConfig
from fim_one.db.models.resource_subscription import ResourceSubscription
from tests.audit.harness import World, backends, world  # noqa: F401

ENDPOINT = {
    "model_name": "m-1",
    "base_url": "https://llm.example.com/v1",
    "api_key": "sk-owner-0123456789",
}


@pytest.fixture(params=backends())
def audit_backend(request: pytest.FixtureRequest) -> str:
    return str(request.param)


async def _people(world: World) -> tuple[Any, Any, Any, Any]:
    alice = await world.user("alice")
    bob = await world.user("bob")
    admin = await world.user("root", admin=True)
    org = await world.org("acme", alice, member=bob)
    return alice, bob, admin, org


async def _shared_agent(world: World, owner: Any, org: Any, bob: Any, rid: str, cfg: Any) -> None:
    await world.add(
        Agent(id=rid, user_id=owner.id, name=rid, visibility="org", org_id=org.id,
              status="published", model_config_json=cfg),
        ResourceSubscription(user_id=bob.id, resource_type="agent", resource_id=rid, org_id=org.id),
    )


def _ctx(user_id: str) -> CallContext:
    return CallContext(principal=provisional_principal(user_id), binding=None, entry="chat")


async def _allow(world: World, admin: Any, on: bool) -> None:
    r = await world.call(
        "PATCH", "/api/admin/settings", by=admin, json={"shared_custom_model_endpoints": on}
    )
    assert r.json()["shared_custom_model_endpoints"] is on


class TestPublishing:
    async def test_publish_and_resubmit_are_refused_until_an_admin_allows_it(
        self, world: World
    ) -> None:
        alice, _bob, admin, org = await _people(world)
        await world.add(Agent(id="a-ep", user_id=alice.id, name="ep", model_config_json=ENDPOINT))
        body = {"scope": "org", "org_id": org.id}

        r = await world.call("POST", "/api/agents/a-ep/publish", by=alice, json=body, expect=400)
        assert r.json()["error_code"] == "agent_model_endpoint_not_shareable"
        stored = await world.get(Agent, "a-ep")
        assert stored.visibility == "personal" and stored.org_id is None

        await world.add(
            Agent(id="a-rej", user_id=alice.id, name="rej", visibility="org", org_id=org.id,
                  publish_status="rejected", model_config_json=ENDPOINT)
        )
        r = await world.call("POST", "/api/agents/a-rej/resubmit", by=alice, expect=400)
        assert r.json()["error_code"] == "agent_model_endpoint_not_shareable"

        await _allow(world, admin, True)
        await world.call("POST", "/api/agents/a-ep/publish", by=alice, json=body)
        assert (await world.get(Agent, "a-ep")).org_id == org.id

    async def test_a_configured_model_publishes_as_before(self, world: World) -> None:
        alice, _bob, _admin, org = await _people(world)
        await world.add(
            Agent(id="a-cfg", user_id=alice.id, name="cfg",
                  model_config_json={"model_name": "m-1", "temperature": 0.2})
        )
        await world.call(
            "POST", "/api/agents/a-cfg/publish", by=alice, json={"scope": "org", "org_id": org.id}
        )

    async def test_a_shared_agent_cannot_be_given_an_endpoint(
        self, world: World, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The endpoint's address check resolves the host; this one is public by fiat.
        monkeypatch.setattr("fim_one.core.security.validate_url", lambda url: url)
        alice, bob, _admin, org = await _people(world)
        await _shared_agent(world, alice, org, bob, "a-shared", {"model_name": "m-1"})

        r = await world.call(
            "PUT", "/api/agents/a-shared", by=alice, json={"model_config_json": ENDPOINT},
            expect=400,
        )
        assert r.json()["error_code"] == "agent_model_endpoint_not_shareable"
        assert (await world.get(Agent, "a-shared")).model_config_json == {"model_name": "m-1"}

        # A personal agent may still have one.
        await world.add(Agent(id="a-mine", user_id=alice.id, name="mine"))
        await world.call(
            "PUT", "/api/agents/a-mine", by=alice, json={"model_config_json": ENDPOINT}
        )


class TestUse:
    async def test_only_the_owner_uses_a_shared_agent_with_an_endpoint(self, world: World) -> None:
        alice, bob, admin, org = await _people(world)
        await _shared_agent(world, alice, org, bob, "a-ep", ENDPOINT)
        await _shared_agent(world, alice, org, bob, "a-cfg", {"model_name": "m-1"})

        async with world.factory() as db:
            theirs = await authorize_use(db, _ctx(bob.id), ResourceKind.AGENT, ["a-ep", "a-cfg"])
            assert [row.id for row in theirs.usable] == ["a-cfg"]
            assert theirs.refused["a-ep"].error_code == "agent_model_endpoint_private"
            assert theirs.refused["a-ep"].status_code == 403
            offered = await list_usable(db, _ctx(bob.id), ResourceKind.AGENT)
            assert {row.id for row in offered} == {"a-cfg"}

            own = await authorize_use(db, _ctx(alice.id), ResourceKind.AGENT, ["a-ep"])
            assert [row.id for row in own.usable] == ["a-ep"]

        from fim_one.web.api.chat import _resolve_agent_config

        with pytest.raises(AssemblyRefusal) as refused:
            await _resolve_agent_config("a-ep", None, user_id=bob.id, refuse_unavailable=True)
        assert refused.value.error_code == "agent_model_endpoint_private"
        cfg = await _resolve_agent_config("a-ep", None, user_id=alice.id, refuse_unavailable=True)
        assert cfg is not None and cfg["agent_id"] == "a-ep"

        await _allow(world, admin, True)
        async with world.factory() as db:
            theirs = await authorize_use(db, _ctx(bob.id), ResourceKind.AGENT, ["a-ep"])
            assert [row.id for row in theirs.usable] == ["a-ep"]

    async def test_the_setting_is_off_by_default(self, world: World) -> None:
        admin = await world.user("root", admin=True)
        r = await world.call("GET", "/api/admin/settings", by=admin)
        assert r.json()["shared_custom_model_endpoints"] is False


class TestUserModelConfigs:
    """Selecting a model configuration a user created with its own base_url."""

    async def _configs(self, world: World, owner: Any) -> None:
        await world.add(
            ModelConfig(id="mc-own", user_id=owner.id, name="own", provider="openai",
                        model_name="m-1", base_url="https://llm.example.com/v1",
                        api_key="sk-owner-0123456789"),
            ModelConfig(id="mc-own-nourl", user_id=owner.id, name="own2", provider="openai",
                        model_name="m-1", api_key="sk-owner-0123456789"),
            ModelConfig(id="mc-sys", user_id=None, name="sys", provider="openai",
                        model_name="m-1", base_url="http://10.0.0.9/v1"),
        )

    async def test_counts_as_a_custom_endpoint(self, world: World) -> None:
        alice, bob, _admin, org = await _people(world)
        await self._configs(world, alice)
        await _shared_agent(world, alice, org, bob, "a-own", {"model_config_id": "mc-own"})
        await _shared_agent(world, alice, org, bob, "a-fast", {"fast_model_config_id": "mc-own"})
        await _shared_agent(world, alice, org, bob, "a-nourl", {"model_config_id": "mc-own-nourl"})
        await _shared_agent(world, alice, org, bob, "a-sys", {"model_config_id": "mc-sys"})

        async with world.factory() as db:
            theirs = await authorize_use(
                db, _ctx(bob.id), ResourceKind.AGENT, ["a-own", "a-fast", "a-nourl", "a-sys"]
            )
            assert [row.id for row in theirs.usable] == ["a-nourl", "a-sys"]
            assert theirs.refused["a-own"].error_code == "agent_model_endpoint_private"
            assert theirs.refused["a-fast"].error_code == "agent_model_endpoint_private"
            own = await authorize_use(db, _ctx(alice.id), ResourceKind.AGENT, ["a-own"])
            assert [row.id for row in own.usable] == ["a-own"]

    async def test_publish_is_refused(self, world: World) -> None:
        alice, _bob, _admin, org = await _people(world)
        await self._configs(world, alice)
        await world.add(
            Agent(id="a-pub", user_id=alice.id, name="pub", model_config_json={"model_config_id": "mc-own"}),
            Agent(id="a-pub-sys", user_id=alice.id, name="pubsys",
                  model_config_json={"model_config_id": "mc-sys"}),
        )
        body = {"scope": "org", "org_id": org.id}
        r = await world.call("POST", "/api/agents/a-pub/publish", by=alice, json=body, expect=400)
        assert r.json()["error_code"] == "agent_model_endpoint_not_shareable"
        await world.call("POST", "/api/agents/a-pub-sys/publish", by=alice, json=body)

    async def test_only_the_agent_owners_configs_are_used(self, world: World) -> None:
        from fim_one.web.deps import get_llm_by_config_id

        alice, bob, _admin, _org = await _people(world)
        await self._configs(world, bob)
        async with world.factory() as db:
            assert await get_llm_by_config_id(db, "mc-own", owner_id=alice.id) is None
            assert await get_llm_by_config_id(db, "mc-own", owner_id=None) is None
            assert await get_llm_by_config_id(db, "mc-own", owner_id=bob.id) is not None
            assert await get_llm_by_config_id(db, "mc-sys", owner_id=alice.id) is not None
