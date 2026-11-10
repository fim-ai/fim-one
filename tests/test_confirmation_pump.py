"""The pump that puts a turn's confirmation cards on its stream.

The ReAct and the DAG handler start it through one entry,
``_start_confirmation_pump``: under the agent, or under the conversation
for a chat with no agent, and never showing a request an earlier turn
left behind.
"""

from __future__ import annotations

import asyncio
import inspect
import json
from collections.abc import AsyncIterator
from contextlib import suppress
from typing import Any

import pytest

from fim_one.web import confirmation_sse
from fim_one.web.api import chat as chat_mod
from fim_one.web.api.chat import _start_confirmation_pump

pytestmark = pytest.mark.timeout(30)


@pytest.fixture(autouse=True)
async def _clean_queues() -> AsyncIterator[None]:
    await confirmation_sse.clear_all_queues()
    yield
    await confirmation_sse.clear_all_queues()


def _event(confirmation_id: str) -> dict[str, Any]:
    return {
        "type": "awaiting_confirmation",
        "confirmation_id": confirmation_id,
        "tool_name": "oa__pay",
        "arguments": {},
        "timeout_at": "2030-01-01T00:00:00+00:00",
        "agent_id": "",
        "mode": "inline",
        "channel_label": "",
        "approver_scope": "initiator",
    }


async def _stop(task: asyncio.Task[None] | None) -> None:
    assert task is not None
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task


def _ids(sse_events: list[dict[str, Any]]) -> list[str]:
    return [e["data"]["confirmation_id"] for e in sse_events]


async def test_a_chat_with_no_agent_is_served_under_its_conversation() -> None:
    """The scope the bridge files a no-agent request under is the one the pump reads."""
    progress: asyncio.Queue[str] = asyncio.Queue()
    sse_events: list[dict[str, Any]] = []
    task = _start_confirmation_pump(None, "conv-1", "user-1", progress, sse_events)
    try:
        assert task is not None
        scope = confirmation_sse.scope_for(None, "conv-1")
        confirmation_sse.queue_for(scope, "user-1").put_nowait(_event("c-1"))
        frame = await asyncio.wait_for(progress.get(), timeout=2)
    finally:
        await _stop(task)

    assert frame.startswith("event: awaiting_confirmation")
    assert json.loads(frame.split("data: ", 1)[1])["confirmation_id"] == "c-1"
    assert _ids(sse_events) == ["c-1"]


async def test_an_agent_turn_is_served_under_the_agent() -> None:
    progress: asyncio.Queue[str] = asyncio.Queue()
    sse_events: list[dict[str, Any]] = []
    task = _start_confirmation_pump("agent-1", "conv-1", "user-1", progress, sse_events)
    try:
        confirmation_sse.queue_for("agent-1", "user-1").put_nowait(_event("c-2"))
        await asyncio.wait_for(progress.get(), timeout=2)
    finally:
        await _stop(task)
    assert _ids(sse_events) == ["c-2"]


async def test_events_left_by_an_earlier_turn_are_not_shown() -> None:
    """A request nobody was listening for must not appear as a card of the next turn."""
    scope = confirmation_sse.scope_for(None, "conv-1")
    queue = confirmation_sse.queue_for(scope, "user-1")
    queue.put_nowait(_event("stale-1"))
    queue.put_nowait(_event("stale-2"))

    progress: asyncio.Queue[str] = asyncio.Queue()
    sse_events: list[dict[str, Any]] = []
    task = _start_confirmation_pump(None, "conv-1", "user-1", progress, sse_events)
    try:
        queue.put_nowait(_event("fresh"))
        await asyncio.wait_for(progress.get(), timeout=2)
        await asyncio.sleep(0.05)
    finally:
        await _stop(task)

    assert _ids(sse_events) == ["fresh"]
    assert progress.empty()


async def test_nothing_to_listen_on_means_no_pump() -> None:
    progress: asyncio.Queue[str] = asyncio.Queue()
    assert _start_confirmation_pump(None, None, "user-1", progress, []) is None
    assert _start_confirmation_pump("agent-1", "conv-1", None, progress, []) is None


def test_both_chat_handlers_start_the_pump_through_the_one_entry() -> None:
    """The DAG handler used to start it for agent turns only, under its own key."""
    source = inspect.getsource(chat_mod)
    # The definition, and the call inside the entry.
    assert source.count("_pump_inline_confirmations(") == 2
    # The definition, the ReAct handler and the DAG handler.
    assert source.count("_start_confirmation_pump(") == 3
    assert "_dag_confirmation_pump_task = _start_confirmation_pump(" in source
