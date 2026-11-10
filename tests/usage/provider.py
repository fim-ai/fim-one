"""A model provider that answers every call the same way and bills a fixed amount.

The usage tests run the real model client (``OpenAICompatibleLLM``) and put
this in place of the network (``litellm.acompletion``). Every call that
reaches it is one call the provider would bill: it is counted, and it
reports :data:`UNIT` tokens of usage, streamed or not. A test can then hold
the ledger to ``provider.calls * UNIT``: less means a call went unrecorded,
more means one was recorded twice.
"""

from __future__ import annotations

import asyncio
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
    # The planner, the agent loop, the plan analyzer, the router.
    "steps": [{"id": "step_1", "task": "say pong", "dependencies": []}],
    "type": "final_answer",
    "reasoning": "finished",
    "answer": "pong",
    "achieved": True,
    "confidence": 1.0,
    "mode": "react",
    "domain": "general",
    # A grader, a title, follow-up suggestions.
    "verdict": "pass",
    "title": "Pong",
    "suggestions": ["again?"],
    # The AI-assisted builders and the KB and DB chats.
    "name": "Pong",
    "description": "Says pong.",
    "instructions": "Say pong.",
    "icon": "🤖",
    "base_url": "https://pong.example.test",
    "auth_type": "none",
    "actions": [],
    "tables": [],
    "intent": "general",
    "reply": "pong",
    "message": "pong",
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
    # Seconds a call takes before it answers, and a stream between chunks.
    delay: float = 0.0
    chunk_delay: float = 0.0
    # The call with this number (1-based) never answers: a non-streamed
    # call hangs, a streamed one sends ``stall_after`` chunks and then hangs.
    hang_at: int = 0
    stall_after: int = 2
    # Tool calls the model is to make: (tool name, arguments), each once.
    tool_calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    # Set when the call that hangs has started to; what streams have sent.
    hung: asyncio.Event = field(default_factory=asyncio.Event)
    streamed: list[str] = field(default_factory=list)

    @property
    def billed(self) -> int:
        return self.calls * UNIT

    def use_tool(self, name: str, arguments: dict[str, Any]) -> None:
        """Have the model call tool *name* once, the next time it is offered it."""
        self.tool_calls.append((name, arguments))

    def _next_tool_call(self, offered: Any) -> SimpleNamespace | None:
        names = {
            (tool.get("function") or {}).get("name") for tool in offered or () if isinstance(tool, dict)
        }
        for index, (name, arguments) in enumerate(self.tool_calls):
            if name in names:
                del self.tool_calls[index]
                return SimpleNamespace(
                    id=f"call_{self.calls}",
                    type="function",
                    function=SimpleNamespace(name=name, arguments=json.dumps(arguments)),
                )
        return None

    def _bill(self, kwargs: dict[str, Any]) -> None:
        if self.fail_from and self.calls + 1 >= self.fail_from:
            raise RuntimeError("the provider is down")
        self.calls += 1
        self.models.append(str(kwargs.get("model")))

    async def acompletion(self, **kwargs: Any) -> Any:
        self._bill(kwargs)
        hangs = self.hang_at == self.calls
        if self.delay:
            await asyncio.sleep(self.delay)
        if kwargs.get("stream"):
            return self._stream(kwargs, stall=hangs)
        if hangs:
            self.hung.set()
            await asyncio.Event().wait()
        return self._answer(kwargs)

    def completion(self, **kwargs: Any) -> Any:
        """The synchronous call the OpenAI-shaped shim makes."""
        self._bill(kwargs)
        return self._answer(kwargs)

    def _answer(self, kwargs: dict[str, Any]) -> Any:
        tool_call = self._next_tool_call(kwargs.get("tools"))
        message = SimpleNamespace(
            role="assistant",
            content=None if tool_call else self.text,
            tool_calls=[tool_call] if tool_call else None,
            reasoning_content=None,
        )
        finish = "tool_calls" if tool_call else "stop"
        return SimpleNamespace(
            choices=[SimpleNamespace(message=message, finish_reason=finish)], usage=_usage()
        )

    async def _stream(self, kwargs: dict[str, Any], *, stall: bool = False) -> AsyncIterator[Any]:
        def chunk(finish: str | None = None, **delta: Any) -> SimpleNamespace:
            fields = {"content": None, "reasoning_content": None, "tool_calls": None, **delta}
            return SimpleNamespace(
                choices=[SimpleNamespace(delta=SimpleNamespace(**fields), finish_reason=finish)],
                usage=None,
            )

        tool_call = self._next_tool_call(kwargs.get("tools"))
        if tool_call is not None:
            tool_call.index = 0
            yield chunk(tool_calls=[tool_call])
            yield chunk("tool_calls")
        else:
            size = max(len(self.text) // self.pieces, 1)
            for sent, start in enumerate(range(0, len(self.text), size)):
                if stall and sent == self.stall_after:
                    self.hung.set()
                    await asyncio.Event().wait()
                piece = self.text[start : start + size]
                self.streamed.append(piece)
                yield chunk(content=piece)
                if self.chunk_delay:
                    await asyncio.sleep(self.chunk_delay)
            yield chunk("stop")
        if not self.silent_streams:
            yield SimpleNamespace(choices=[], usage=_usage())


@pytest.fixture()
def provider(monkeypatch: pytest.MonkeyPatch) -> Provider:
    """The network replaced by a :class:`Provider`; no other way out to a model."""
    fake = Provider()

    async def _refuse(**_kwargs: Any) -> Any:
        raise AssertionError("a test reached for the Responses API")

    monkeypatch.setattr("litellm.acompletion", fake.acompletion)
    monkeypatch.setattr("litellm.aresponses", _refuse)
    monkeypatch.setattr("litellm.completion", fake.completion)
    return fake
