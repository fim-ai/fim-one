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
    refusals._summary_pending.clear()
    yield
    refusals._throttle.reset()
    refusals._summary_pending.clear()


class TestTheThrottle:
    def test_the_first_from_a_source_is_written_the_rest_of_the_window_counted(self) -> None:
        throttle = RefusalThrottle(window_seconds=600)

        assert throttle.admit("blocked", "a", now=0) is True
        assert [throttle.admit("blocked", "a", now=t) for t in (1, 300, 599)] == [False] * 3
        # After the window the source is forgotten: written again.
        assert throttle.admit("blocked", "a", now=600) is True
        assert throttle.take_left_out("blocked") == 3
        assert throttle.take_left_out("blocked") == 0

    def test_sources_are_counted_apart(self) -> None:
        throttle = RefusalThrottle(window_seconds=600)

        assert throttle.admit("blocked", "a", now=0) is True
        assert throttle.admit("blocked", "b", now=1) is True
        assert throttle.admit("blocked", "a", now=2) is False

    def test_a_scan_from_many_addresses_is_bounded_per_window(self) -> None:
        throttle = RefusalThrottle(window_seconds=600, max_records_per_window=3)

        written = [throttle.admit("blocked", f"ip-{n}", now=n) for n in range(10)]

        assert written == [True] * 3 + [False] * 7
        assert throttle.take_left_out("blocked") == 7
        # The next window has its own budget.
        assert throttle.admit("blocked", "ip-9", now=700) is True

    def test_each_kind_of_refusal_has_its_own_budget(self) -> None:
        """A scan that uses up the blocked-address budget hid a trigger call
        with an unknown key from a fresh address in the same window."""
        throttle = RefusalThrottle(window_seconds=600, max_records_per_window=600)

        written = sum(throttle.admit("blocked", f"2001:db8::{n:x}", now=1) for n in range(700))

        assert written == 600
        assert throttle.admit("unknown-key", "203.0.113.50", now=2) is True

    def test_sources_that_never_return_are_forgotten(self) -> None:
        """Sources that had been counted were kept for ever; once as many as
        the limit were held, every new address was left out until restart."""
        throttle = RefusalThrottle(window_seconds=600, max_records_per_window=100)
        for n in range(100):
            assert throttle.admit("blocked", f"scan-{n}", now=0) is True
            assert throttle.admit("blocked", f"scan-{n}", now=1) is False  # counted

        later = [throttle.admit("blocked", f"new-{n}", now=10_000 + n * 700) for n in range(20)]

        assert later == [True] * 20
        assert throttle.remembered() == 1
        assert throttle.take_left_out("blocked") == 100

    def test_twenty_thousand_sources_over_hours_never_block_a_new_source(self) -> None:
        throttle = RefusalThrottle(window_seconds=600, max_records_per_window=600)
        budget_window, used = 0.0, 0
        most_remembered = 0

        for n in range(20_000):
            now = n * 0.9  # five hours of one new address every 0.9 seconds
            if now - budget_window >= 600:
                budget_window, used = now, 0
            admitted = throttle.admit("blocked", f"src-{n}", now=now)
            # A new source is left out only when the window's budget is spent.
            assert admitted is (used < 600), n
            used += admitted
            most_remembered = max(most_remembered, throttle.remembered())

        # Memory: never more sources than one window's budget.
        assert most_remembered <= 600
        assert throttle.admit("blocked", "fresh", now=20_000 * 0.9 + 600) is True


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

    async def test_what_was_left_out_is_reported_in_a_summary(self, world: World) -> None:
        with _blocked((CLIENT_IP, "deny")):
            for _ in range(4):
                await world.call("GET", "/api/agents", expect=403)

        # The summary is scheduled one window after the first uncounted
        # refusal; here it is written directly.
        assert "security.ip_blocked" in refusals._summary_pending
        assert await refusals.write_summary("security.ip_blocked") is True
        assert await refusals.write_summary("security.ip_blocked") is False  # nothing new

        first, summary = await world.events("security.ip_blocked")
        assert (first.actor_kind, first.detail) == ("anonymous", "GET /api/agents")
        assert (summary.actor_kind, summary.actor_label, summary.outcome) == (
            "system",
            "refusal summary",
            "denied",
        )
        assert summary.detail == (
            "3 more refusal(s) of this kind were counted and not recorded one by one "
            "in the last 10 minutes"
        )
        assert summary.ip_address is None

    async def test_the_summary_is_written_by_a_timer(
        self, world: World, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import asyncio

        monkeypatch.setattr(refusals, "WINDOW_SECONDS", 0.05)

        assert await record_refusal("security.ip_blocked", source="203.0.113.99") is True
        assert await record_refusal("security.ip_blocked", source="203.0.113.99") is False
        for _ in range(100):
            await asyncio.sleep(0.02)
            if len(await world.events("security.ip_blocked")) == 2:
                break

        _, summary = await world.events("security.ip_blocked")
        assert summary.actor_label == "refusal summary"

    async def test_a_rule_stored_by_an_administrator_blocks_and_records(
        self, world: World
    ) -> None:
        """End to end: the rule is read from the database."""
        await world.add(IpRule(ip_address=CLIENT_IP, rule_type="deny", is_active=True))

        await world.call("GET", "/api/agents", expect=403)

        await world.one("security.ip_blocked", actor=None, outcome="denied")

    async def test_a_key_or_an_id_in_the_path_is_not_recorded(self, world: World) -> None:
        """The deprecated trigger route has the key in its path, and the
        refusal comes before any route is matched."""
        key = "wf_3CDsrGZhq-a_real-looking_key-0123456789abcdefghi"
        resource = "03cc1615-6573-43c1-ba2e-12fed03ecef5"

        with _blocked((CLIENT_IP, "deny")):
            await world.call("POST", f"/api/workflows/trigger/{key}", expect=403, json={})
            refusals._throttle.reset()
            await world.call("GET", f"/api/agents/{resource}?token=in-the-query", expect=403)

        first, second = await world.events("security.ip_blocked")
        assert first.detail == "POST /api/workflows/trigger/***"
        assert second.detail == "GET /api/agents/***"
        world.assert_absent(first, key, key[:12])
        world.assert_absent(second, resource, "in-the-query")

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
            "key=wf_not-a-r…",
        )
        world.assert_absent(row, self.BAD_KEY, self.BAD_KEY[:14])

    async def test_the_key_in_the_url_route_and_a_missing_key(self, world: World) -> None:
        await world.enable_module("workflows")

        await world.call(
            "POST", f"/api/workflows/trigger/{self.BAD_KEY}", json={"inputs": {}}, expect=401
        )
        refusals._throttle.reset()
        await world.call("POST", "/api/workflows/trigger", json={"inputs": {}}, expect=401)

        with_key, without = await world.events("workflow.trigger_key_invalid")
        assert (with_key.detail, without.detail) == ("key=wf_not-a-r…", "key=(none)")

    @pytest.mark.parametrize(
        "presented", ["hunter2", "wf_short", "sk-live-0123456789abcdef0123456789abcdef"]
    )
    async def test_of_something_that_is_not_a_trigger_key_only_the_length_is_kept(
        self, world: World, presented: str
    ) -> None:
        """Six characters were kept of whatever was sent: all of a short
        password, the start of another service's token."""
        await world.enable_module("workflows")

        await world.call(
            "POST",
            "/api/workflows/trigger",
            headers={"Authorization": f"Bearer {presented}"},
            json={"inputs": {}},
            expect=401,
        )

        row = await world.one("workflow.trigger_key_invalid", actor=None, outcome="denied")
        assert row.detail == f"key=(not a trigger key; {len(presented)} characters)"
        world.assert_absent(row, presented, presented[:5])


class TestDirectly:
    async def test_the_writer_is_not_called_for_a_refusal_that_is_only_counted(
        self, world: World
    ) -> None:
        assert await record_refusal("security.ip_blocked", source="203.0.113.99") is True
        assert await record_refusal("security.ip_blocked", source="203.0.113.99") is False
        # Another kind of refusal from the same address is its own count.
        assert await record_refusal("workflow.trigger_key_invalid", source="203.0.113.99") is True

        assert len(await world.events()) == 2
