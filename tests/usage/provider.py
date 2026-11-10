"""A model provider that answers every call the same way and bills a fixed amount.

The usage tests run the real model client (``OpenAICompatibleLLM``) and put
this in place of the network (``litellm.acompletion``). Every call that
reaches it is one call the provider would bill: it is counted, and it
reports :data:`UNIT` tokens of usage, streamed or not. A test can then hold
the ledger to ``provider.calls * UNIT``: less means a call went unrecorded,
more means one was recorded twice.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

import pytest

__all__ = ["PROMPT", "COMPLETION", "UNIT", "Provider", "provider"]

PROMPT = 7
COMPLETION = 3
UNIT = PROMPT + COMPLETION

# One reply that reads as a one-step plan to the planner, as a final answer
# to the agent loop, as "achieved" to the plan analyzer, as a route to the
# router and as a verdict to a grader.
EVERY_ANSWER: dict[str, Any] = {
    "steps": [{"id": "step_1", "task": "say pong", "dependencies": []}],
    "type": "final_answer",
    "reasoning": "finished",
    "answer": "pong",
    "achieved": True,
    "confidence": 1.0,
    "mode": "react",
    "verdict": "pass",
    "title": "Pong",
    "suggestions": ["again?"],
    "domain": "general",
}


def _usage() -> SimpleNamespace:
    return SimpleNamespace(
        prompt_tokens=PROMPT, completion_tokens=COMPLETION, total_tokens=UNIT
    )


@dataclass
class Provider:
    """What stands in for the provider; counts the calls that reached it."""

    text: str = field(default_factory=lambda: json.dumps(EVERY_ANSWER))
    calls: int = 0
    models: list[str] = field(default_factory=list)
    # Stream chunks carry no usage (a provider that sends none).
    silent_streams: bool = False
    # Raise instead of answering, from this call on (1-based); 0 = never.
    fail_from: int = 0
    # A streamed answer is cut into this many content chunks.
    pieces: int = 4

    @property
    def billed(self) -> int:
        return self.calls * UNIT

    async def acompletion(self, **kwargs: Any) -> Any:
        if self.fail_from and self.calls + 1 >= self.fail_from:
            raise RuntimeError("the provider is down")
        self.calls += 1
        self.models.append(str(kwargs.get("model")))
        if kwargs.get("stream"):
            return self._stream()
        message = SimpleNamespace(
            role="assistant", content=self.text, tool_calls=None, reasoning_content=None
        )
        return SimpleNamespace(
            choices=[SimpleNamespace(message=message, finish_reason="stop")], usage=_usage()
        )

    async def _stream(self) -> AsyncIterator[Any]:
        size = max(len(self.text) // self.pieces, 1)
        for start in range(0, len(self.text), size):
            delta = SimpleNamespace(
                content=self.text[start : start + size], reasoning_content=None, tool_calls=None
            )
            yield SimpleNamespace(
                choices=[SimpleNamespace(delta=delta, finish_reason=None)], usage=None
            )
        done = SimpleNamespace(content=None, reasoning_content=None, tool_calls=None)
        yield SimpleNamespace(
            choices=[SimpleNamespace(delta=done, finish_reason="stop")], usage=None
        )
        if not self.silent_streams:
            yield SimpleNamespace(choices=[], usage=_usage())


@pytest.fixture()
def provider(monkeypatch: pytest.MonkeyPatch) -> Provider:
    """The network replaced by a :class:`Provider`; no other way out to a model."""
    fake = Provider()

    async def _refuse(**_kwargs: Any) -> Any:
        raise AssertionError("a test reached for the Responses API")

    def _refuse_sync(**_kwargs: Any) -> Any:
        raise AssertionError("a test reached for a synchronous completion")

    monkeypatch.setattr("litellm.acompletion", fake.acompletion)
    monkeypatch.setattr("litellm.aresponses", _refuse)
    monkeypatch.setattr("litellm.completion", _refuse_sync)
    return fake
