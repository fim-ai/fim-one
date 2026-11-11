"""A turn whose stream is closed stops calling the model.

The server closes a response's stream when the connection drops, wherever
the stream is waiting. The task that ran the turn was only cancelled where
the stream polls ``request.is_disconnected()``; closed anywhere else, it ran
on: more model calls for a turn nobody reads, made after the turn's meter
had closed, with tools whose connections the clean-up had closed.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

import pytest

from fim_one.core.agent.react import ReActAgent
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


async def _disconnect_mid_turn(
    app: Any, path: str, payload: dict[str, Any], under_way: bytes
) -> None:
    """POST to *path* through the whole app; the client goes once *under_way* was sent.

    The server learns from ``http.disconnect`` and cancels the response,
    stream included: the way a dropped connection ends a turn in production.
    """
    body = json.dumps(payload).encode()
    gone = asyncio.Event()
    delivered = False

    async def receive() -> dict[str, Any]:
        nonlocal delivered
        if not delivered:
            delivered = True
            return {"type": "http.request", "body": body, "more_body": False}
        await gone.wait()
        return {"type": "http.disconnect"}

    async def send(message: dict[str, Any]) -> None:
        if message["type"] != "http.response.body":
            return
        if message.get("body", b"").startswith(under_way):
            gone.set()

    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "POST",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": "",
        "scheme": "http",
        "server": ("test", 80),
        "client": ("127.0.0.1", 1234),
        "headers": [
            (b"host", b"test"),
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode()),
        ],
        "state": {},
    }
    await app(scope, receive, send)
    assert gone.is_set(), "the turn ended before the client could leave mid-way"


@pytest.mark.parametrize(
    ("path", "under_way"),
    [("/api/react", b"event: phase"), ("/api/dag", b"event: step_progress")],
)
async def test_dropped_connection_stops_the_turn(
    path: str, under_way: bytes, usage: Usage, provider: Provider, alice: User  # noqa: F811
) -> None:
    conversation = await usage.conversation(alice)
    provider.delay = 0.2

    await _disconnect_mid_turn(
        usage.app,
        path,
        {"q": "say pong", "conversation_id": conversation.id, "token": usage.token(alice)},
        under_way,
    )

    at_close = provider.calls
    await asyncio.sleep(1.0)
    assert provider.calls == at_close, (
        f"{provider.calls - at_close} model call(s) were made after the connection dropped"
    )
    assert not _turn_task_is_running()


async def test_a_turn_slow_to_stop_does_not_hold_up_the_clean_up(
    usage: Usage, provider: Provider, alice: User, monkeypatch: pytest.MonkeyPatch  # noqa: F811
) -> None:
    """Cancelled once, waited for briefly, then left to finish on its own."""
    cancellations = 0
    started = asyncio.Event()
    finished = asyncio.Event()

    async def run(self: Any, *args: Any, **kwargs: Any) -> Any:
        nonlocal cancellations
        started.set()
        kwargs["on_thinking_delta"]("thinking")  # something for the stream to send
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            cancellations += 1
            try:
                await asyncio.sleep(1.0)  # its own clean-up
            except asyncio.CancelledError:
                cancellations += 1
            finished.set()
            raise

    monkeypatch.setattr(ReActAgent, "run", run)
    monkeypatch.setattr(chat, "_TURN_STOP_GRACE", 0.2)
    conversation = await usage.conversation(alice)
    response = await chat.react_endpoint(
        _Client(),  # type: ignore[arg-type]
        chat.ChatStreamRequest(
            q="say pong", conversation_id=conversation.id, token=usage.token(alice)
        ),
    )
    body: Any = response.body_iterator
    async for _chunk in body:
        if started.is_set():
            break
    assert started.is_set()

    closing = time.monotonic()
    await body.aclose()
    waited = time.monotonic() - closing

    assert waited < 0.9, f"the clean-up waited {waited:.1f}s on a task given 0.2s"
    await asyncio.wait_for(finished.wait(), timeout=3)
    assert cancellations == 1, "the task's own clean-up was interrupted by a second cancel"
