"""An agent's model settings reach a model prompt or a tool result with their secrets masked.

The builder assistant is told the agent's current state in its
instructions, and its tools return the agent's settings; neither carries
the ``api_key`` an agent's inline model settings hold.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from fim_one.core.audit.context import request_auth, request_meta
from fim_one.core.audit.events import RequestMeta
from fim_one.core.security.redact import MASK, is_secret_key, redact_secrets
from fim_one.core.tool.builtin import agent_builder
from fim_one.db.models import Agent
from tests.audit.harness import CLIENT_IP, USER_AGENT, World, world  # noqa: F401

KEY = "sk-inline-0123456789abcdef0123"
CONFIG = {
    "model_name": "m-1",
    "base_url": "https://llm.example.com/v1",
    "api_key": KEY,
    "temperature": 0.3,
    "max_tokens": 900,
    "hooks": {"class_hooks": [{"name": "h", "options": {"authToken": KEY}}]},
}


class TestRedactSecrets:
    @pytest.mark.parametrize(
        "key",
        ["api_key", "apiKey", "x-api-key", "openai_api_key", "secret", "client_secret",
         "password", "access_token", "authToken", "Authorization", "credentials", "key"],
    )
    def test_secret_names(self, key: str) -> None:
        assert is_secret_key(key)

    @pytest.mark.parametrize(
        "key",
        ["max_tokens", "max_output_tokens", "model_name", "base_url", "keyboard", "monkey",
         "temperature", "context_size", "provider"],
    )
    def test_other_names(self, key: str) -> None:
        assert not is_secret_key(key)

    def test_nested_values_are_masked_and_the_input_is_kept(self) -> None:
        out = redact_secrets(CONFIG)
        assert KEY not in json.dumps(out)
        assert out["api_key"] == MASK
        assert out["hooks"]["class_hooks"][0]["options"]["authToken"] == MASK
        assert out["max_tokens"] == 900 and out["base_url"] == CONFIG["base_url"]
        assert CONFIG["api_key"] == KEY

    def test_an_empty_secret_stays_empty(self) -> None:
        assert redact_secrets({"api_key": ""}) == {"api_key": ""}
        assert redact_secrets({"api_key": None}) == {"api_key": None}
        assert redact_secrets(None) is None


@pytest.fixture()
def in_a_chat_request() -> Any:
    meta = request_meta.set(RequestMeta(ip=CLIENT_IP, user_agent=USER_AGENT, request_id="req-1"))
    auth = request_auth.set(("session", None))
    yield
    request_auth.reset(auth)
    request_meta.reset(meta)


class TestBuilderSeesNoKey:
    async def test_builder_instructions(self, world: World) -> None:
        alice = await world.user("alice")
        await world.add(Agent(id="agent-1", user_id=alice.id, name="Helper", model_config_json=CONFIG))

        r = await world.call(
            "POST", "/api/builder/session", by=alice,
            json={"target_type": "agent", "target_id": "agent-1"},
        )
        builder = await world.get(Agent, r.json()["data"]["builder_agent_id"])
        assert KEY not in builder.instructions
        assert "https://llm.example.com/v1" in builder.instructions

        # Opening it again refreshes the instructions the same way.
        await world.call(
            "POST", "/api/builder/session", by=alice,
            json={"target_type": "agent", "target_id": "agent-1"},
        )
        builder = await world.get(Agent, r.json()["data"]["builder_agent_id"])
        assert KEY not in builder.instructions

    async def test_builder_tools(self, world: World, in_a_chat_request: None) -> None:
        alice = await world.user("alice")
        await world.add(Agent(id="agent-1", user_id=alice.id, name="Helper", model_config_json=CONFIG))
        args = {"agent_id": "agent-1", "user_id": alice.id}

        shown = await agent_builder.AgentGetSettingsTool(**args).run()
        assert KEY not in shown
        assert json.loads(shown)["model_config"]["api_key"] == MASK

        changed = await agent_builder.AgentSetModelTool(**args).run(temperature=0.5)
        assert KEY not in changed
        # The stored settings keep the key itself.
        stored = (await world.get(Agent, "agent-1")).model_config_json
        assert stored["api_key"] == KEY and stored["temperature"] == 0.5
