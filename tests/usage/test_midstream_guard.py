"""The quota is watched while an answer streams, from the turn's own meter (B21).

About every 200 characters the turn asked the database what the user had
used (five to six queries each time) and added a quarter of the characters
streamed so far. It now adds up what the user had used when the turn was
admitted and what the turn's meter has counted since, the answer in flight
included, and asks the database nothing.
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import event

from fim_one.core.assembly.quota import QuotaStatus
from fim_one.core.model.usage import estimate_tokens
from fim_one.core.usage.meter import OpenMeter
from fim_one.db.models import User
from fim_one.web.api import chat

from .harness import Usage, events, usage  # noqa: F401
from .provider import UNIT, Provider, provider  # noqa: F401

# Long enough for the guard to look a dozen times.
LONG_ANSWER = "pong " * 600


class _Client:
    headers: dict[str, str] = {}

    async def is_disconnected(self) -> bool:
        return False


async def _stream(usage: Usage, by: User, endpoint: Any) -> Any:  # noqa: F811
    response = await endpoint(
        _Client(), chat.ChatStreamRequest(q="say pong", token=usage.token(by))
    )
    return response.body_iterator


class TestTheGuardAsksTheDatabaseNothing:
    @pytest.mark.parametrize("endpoint", ["react_endpoint", "dag_endpoint"])
    async def test_midstream_guard_no_db(
        self, endpoint: str, usage: Usage, provider: Provider  # noqa: F811
    ) -> None:
        from fim_one.db import engine as engine_mod

        await usage.cap(10_000_000)
        alice = await usage.user("alice")
        provider.stream_text = LONG_ANSWER
        provider.pieces = 150
        # The answer is written by a streamed call of its own (see below).
        provider.use_tool("finish", {})
        quota_tables = ("usage_ledger", "billing_plans", "subscriptions", "system_settings")
        streaming = False
        during: list[str] = []

        assert engine_mod._engine is not None

        @event.listens_for(engine_mod._engine.sync_engine, "before_cursor_execute")
        def _watch(_conn: Any, _cursor: Any, statement: str, *_rest: Any) -> None:
            if streaming and any(table in statement for table in quota_tables):
                during.append(statement)

        deltas = 0
        async for chunk in await _stream(usage, alice, getattr(chat, endpoint)):
            for name, data in events(str(chunk)):
                if name == "answer" and data["data"]["status"] == "start":
                    streaming = True
                elif name == "answer" and data["data"]["status"] == "done":
                    streaming = False
                elif name == "answer":
                    deltas += len(data["data"]["content"])

        # The guard had its dozen looks, and none of them was a query.
        assert deltas >= 2400
        assert during == []


class TestTheStreamIsCutWhenTheTurnUsesUpTheQuota:
    async def test_the_answer_stops_with_the_structured_terminator(
        self, usage: Usage, provider: Provider  # noqa: F811
    ) -> None:
        """Admitted under the cap; the answer itself crosses it."""
        cap = 30 * UNIT
        await usage.cap(cap)
        alice = await usage.user("alice")
        provider.stream_text = LONG_ANSWER
        provider.pieces = 150
        # The agent signals that it is done; its answer is then written by
        # a model call of its own, streamed to the user as it is produced.
        provider.use_tool("finish", {})
        assert estimate_tokens(LONG_ANSWER) > cap

        sent = [
            item
            async for chunk in await _stream(usage, alice, chat.react_endpoint)
            for item in events(str(chunk))
        ]

        names = [name for name, _ in sent]
        (stopped,) = [data["data"] for name, data in sent if name == "error"]
        assert stopped["code"] == "QUOTA_EXCEEDED"
        assert stopped["quota"] == cap and stopped["tokens_used"] >= cap
        # Within a few hundred characters of the cap, not at the end.
        assert stopped["tokens_used"] < cap + 200
        answer = "".join(
            data["data"]["content"]
            for name, data in sent
            if name == "answer" and data["data"]["status"] == "delta"
        )
        assert 0 < len(answer) < len(LONG_ANSWER)
        assert names.index("error") < names.index("done")
        # What the cut-off turn consumed is in the ledger, and the next
        # turn is refused before it starts.
        assert await usage.quota_used(alice) >= cap
        await usage.call(
            "POST", "/api/react", json={"q": "again", "token": usage.token(alice)}, expect=429
        )

    async def test_a_user_with_no_cap_is_never_cut(
        self, usage: Usage, provider: Provider  # noqa: F811
    ) -> None:
        alice = await usage.user("alice")
        provider.stream_text = LONG_ANSWER
        provider.pieces = 150

        sent = [
            item
            async for chunk in await _stream(usage, alice, chat.react_endpoint)
            for item in events(str(chunk))
        ]

        assert "error" not in [name for name, _ in sent]


class TestWhatTheGuardAddsUp:
    def test_what_was_used_at_admission_plus_what_the_meter_holds(self) -> None:
        opened = OpenMeter()
        opened.meter.add("main", {"total_tokens": 120})
        opened.meter.stream_progress(35)

        assert chat._used_by_now(QuotaStatus(used=1000, cap=5000), opened) == 1155
        # Written to the ledger or not makes no difference to the figure.
        opened.meter.drain()
        assert chat._used_by_now(QuotaStatus(used=1000, cap=5000), opened) == 1155
