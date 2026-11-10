"""Refusals anyone can provoke are recorded, and cannot fill the table.

A request from a blocked address and a trigger call with an unknown key
left nothing: an administrator could not see the service being scanned.
Each is now recorded, once per source address and ten minutes, with the
next record saying how many were left out.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from fim_one.core.audit import refusals
from fim_one.core.audit.refusals import RefusalThrottle, record_refusal
from fim_one.db.models import IpRule
from fim_one.web.ip_rules import parse_rules
from tests.audit.harness import CLIENT_IP, World, world  # noqa: F401


@pytest.fixture(autouse=True)
def _fresh_throttle() -> Iterator[None]:
    refusals._throttle.reset()
    yield
    refusals._throttle.reset()


class TestTheThrottle:
    def test_the_first_from_a_source_is_written_the_rest_of_the_window_counted(self) -> None:
        throttle = RefusalThrottle(window_seconds=600)

        assert throttle.admit("a", now=0) == (0, 0)
        assert [throttle.admit("a", now=t) for t in (1, 300, 599)] == [None, None, None]
        # After the window: written again, saying what was left out.
        assert throttle.admit("a", now=600) == (3, 0)
        assert throttle.admit("a", now=1200) == (0, 0)

    def test_sources_are_counted_apart(self) -> None:
        throttle = RefusalThrottle(window_seconds=600)

        assert throttle.admit("a", now=0) == (0, 0)
        assert throttle.admit("b", now=1) == (0, 0)
        assert throttle.admit("a", now=2) is None

    def test_a_scan_from_many_addresses_is_bounded_overall(self) -> None:
        throttle = RefusalThrottle(window_seconds=600, max_records_per_window=3)

        written = [throttle.admit(f"ip-{n}", now=n) for n in range(10)]

        assert written[:3] == [(0, 0)] * 3 and written[3:] == [None] * 7
        # The next window's first record reports the seven.
        assert throttle.admit("ip-0", now=700) == (0, 7)

    def test_no_more_sources_are_remembered_than_the_limit(self) -> None:
        throttle = RefusalThrottle(window_seconds=600, max_sources=2)

        assert throttle.admit("a", now=0) == (0, 0)
        assert throttle.admit("b", now=0) == (0, 0)
        assert throttle.admit("c", now=1) is None  # no room: counted as overflow
        assert len(throttle._seen) == 2
        # Expired sources make room.
        assert throttle.admit("c", now=601) == (0, 1)
        assert len(throttle._seen) <= 2


def _blocked(*rows: tuple[str, str]) -> Any:
    return patch("fim_one.web.ip_rules.RuleCache.get", AsyncMock(return_value=parse_rules(rows)))


class TestABlockedAddress:
    async def test_ip_block_recorded_once_per_window(self, world: World) -> None:
        with _blocked((CLIENT_IP, "deny")):
            for path in ("/api/agents", "/api/agents", "/api/connectors"):
                await world.call("GET", path, expect=403)

        row = await world.one("security.ip_blocked", actor=None, outcome="denied")
        assert (row.actor_kind, row.actor_id, row.actor_label) == ("anonymous", None, "anonymous")
        assert (row.ip_address, row.detail) == (CLIENT_IP, "GET /api/agents")

    async def test_the_next_window_says_how_many_were_left_out(
        self, world: World, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        clock = [1000.0]
        monkeypatch.setattr(refusals.time, "monotonic", lambda: clock[0])

        with _blocked((CLIENT_IP, "deny")):
            for _ in range(4):
                await world.call("GET", "/api/agents", expect=403)
            clock[0] += refusals.WINDOW_SECONDS + 1
            await world.call("POST", "/api/agents", expect=403, json={})

        first, second = await world.events("security.ip_blocked")
        assert first.detail == "GET /api/agents"
        assert second.detail == (
            "POST /api/agents; 3 more from this source were not recorded since its last record"
        )

    async def test_a_rule_stored_by_an_administrator_blocks_and_records(
        self, world: World
    ) -> None:
        """End to end: the rule is read from the database."""
        await world.add(IpRule(ip_address=CLIENT_IP, rule_type="deny", is_active=True))

        await world.call("GET", "/api/agents", expect=403)

        await world.one("security.ip_blocked", actor=None, outcome="denied")

    async def test_an_allowed_request_records_nothing(self, world: World) -> None:
        alice = await world.user("alice")

        with _blocked(("198.51.100.0/24", "deny")):
            await world.call("GET", "/api/agents", by=alice)

        assert await world.events() == []

    async def test_a_record_that_cannot_be_written_still_refuses_the_request(
        self, world: World
    ) -> None:
        def _broken() -> Any:
            raise RuntimeError("audit database unavailable")

        with _blocked((CLIENT_IP, "deny")), patch(
            "fim_one.core.audit.writer._own_session", _broken
        ):
            response = await world.call("GET", "/api/agents", expect=403)

        assert response.json()["error_code"] == "ip_not_allowed"


class TestAnUnknownTriggerKey:
    BAD_KEY = "wf_not-a-real-key-0123456789abcdefghijklmnop"

    async def test_recorded_once_per_address_with_only_the_start_of_the_key(
        self, world: World
    ) -> None:
        await world.enable_module("workflows")

        for _ in range(3):
            await world.call(
                "POST",
                "/api/workflows/trigger",
                headers={"Authorization": f"Bearer {self.BAD_KEY}"},
                json={"inputs": {}},
                expect=401,
            )

        row = await world.one("workflow.trigger_key_invalid", actor=None, outcome="denied")
        assert (row.actor_kind, row.ip_address, row.detail) == (
            "anonymous",
            CLIENT_IP,
            "key=wf_not…",
        )
        world.assert_absent(row, self.BAD_KEY, self.BAD_KEY[:12])

    async def test_the_key_in_the_url_route_and_a_missing_key(self, world: World) -> None:
        await world.enable_module("workflows")

        await world.call(
            "POST", f"/api/workflows/trigger/{self.BAD_KEY}", json={"inputs": {}}, expect=401
        )
        refusals._throttle.reset()
        await world.call("POST", "/api/workflows/trigger", json={"inputs": {}}, expect=401)

        with_key, without = await world.events("workflow.trigger_key_invalid")
        assert (with_key.detail, without.detail) == ("key=wf_not…", "key=(none)")


class TestDirectly:
    async def test_the_writer_is_not_called_for_a_refusal_that_is_only_counted(
        self, world: World
    ) -> None:
        assert await record_refusal("security.ip_blocked", source="203.0.113.99") is True
        assert await record_refusal("security.ip_blocked", source="203.0.113.99") is False
        # Another kind of refusal from the same address is its own count.
        assert await record_refusal("workflow.trigger_key_invalid", source="203.0.113.99") is True

        assert len(await world.events()) == 2
