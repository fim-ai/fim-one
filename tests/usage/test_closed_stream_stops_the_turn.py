"""A turn whose stream is closed stops calling the model.

The server closes a response's stream when the connection drops, wherever
the stream is waiting. The task that ran the turn was only cancelled where
the stream polls ``request.is_disconnected()``; closed anywhere else, it ran
on: more model calls for a turn nobody reads, made after the turn's meter
had closed, with tools whose connections the clean-up had closed.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from fim_one.db.models import User
from fim_one.web.api import chat

from .harness import Usage, usage  # noqa: F401
from .provider import Provider, provider  # noqa: F401


class _Client:
    headers: dict[str, str] = {}

    async def is_disconnected(self) -> bool:
        # The stream is closed under it; it never learns by asking.
        return False


def _turn_task_is_running() -> bool:
    names = {task.get_coro().__qualname__.rsplit(".", 1)[-1] for task in asyncio.all_tasks()}  # type: ignore[union-attr]
    return bool(names & {"_run", "_exec"})


@pytest.fixture
async def alice(usage: Usage) -> User:  # noqa: F811
    await usage.cap(10_000_000)
    return await usage.user("alice")


# The endpoint, and the first event it sends once the task that runs the
# turn is under way (the calls before it are made by the stream itself).
@pytest.mark.parametrize(
    ("endpoint", "under_way"),
    [("react_endpoint", "event: phase"), ("dag_endpoint", "event: step_progress")],
)
async def test_closed_stream_stops_model_calls(
    endpoint: str, under_way: str, usage: Usage, provider: Provider, alice: User  # noqa: F811
) -> None:
    conversation = await usage.conversation(alice)
    # Every call takes a while: the stream is closed with the turn mid-way.
    provider.delay = 0.2

    response = await getattr(chat, endpoint)(
        _Client(),
        chat.ChatStreamRequest(
            q="say pong", conversation_id=conversation.id, token=usage.token(alice)
        ),
    )
    body: Any = response.body_iterator
    running = False
    async for chunk in body:
        running = _turn_task_is_running()
        if running and str(chunk).startswith(under_way):
            break
    assert running, "the stream ended before it could be closed mid-turn"
    await body.aclose()

    at_close = provider.calls
    await asyncio.sleep(1.0)
    assert provider.calls == at_close, (
        f"{provider.calls - at_close} model call(s) were made after the stream was closed"
    )
