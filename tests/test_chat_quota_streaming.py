"""Tests for the mid-stream token-quota guard in ``chat.py``.

These tests cover the two paths that protect users from mid-stream
quota cutoffs being misread as generic network errors:

1. **Pre-stream quota gate** — ``check_quota`` refuses with 429 before any
   LLM work begins.
2. **Mid-stream quota terminator** — ``usage_and_cap`` /
   ``_build_quota_terminator_payload`` produce the structured
   ``error`` event that the streaming generator emits when the
   user runs out of budget partway through the answer.

The quota chain itself (override, plan, system default, per access model)
is covered in ``tests/test_assembly_quota.py``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import fim_one.db.models  # noqa: F401
from fim_one.core.assembly.errors import AssemblyRefusal
from fim_one.core.assembly.principal import provisional_principal
from fim_one.core.assembly.quota import check_quota
from fim_one.db.base import Base
from fim_one.db.models import BillingPlan, Conversation, SystemSetting, User
from fim_one.web.api.chat import _build_quota_terminator_payload
from fim_one.web.services.quota_backend import next_month_reset, reset_at, usage_and_cap

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@pytest.fixture()
async def factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def _create_session() -> AsyncIterator[AsyncSession]:
        async with maker() as s:
            yield s

    with patch("fim_one.db.create_session", _create_session):
        yield maker
    await engine.dispose()


async def _user(
    factory: async_sessionmaker[AsyncSession],
    *,
    user_quota: int | None,
    monthly_tokens: int,
    default_setting: str = "0",
    plan_quota: int | None = None,
) -> str:
    """Seed ``user-1`` on an instance that sells access by plan (freemium)."""
    async with factory() as s:
        s.add(SystemSetting(key="access_model", value="freemium"))
        s.add(SystemSetting(key="default_token_quota", value=default_setting))
        plan_id = None
        if plan_quota is not None:
            plan = BillingPlan(slug="pro", name="Pro", monthly_token_quota=plan_quota)
            s.add(plan)
            await s.flush()
            plan_id = plan.id
        s.add(
            User(
                id="user-1",
                email="u@x.io",
                username="user-1",
                token_quota=user_quota,
                plan_id=plan_id,
            )
        )
        if monthly_tokens:
            s.add(
                Conversation(
                    user_id="user-1", title="t", mode="react", total_tokens=monthly_tokens
                )
            )
        await s.commit()
    return "user-1"


# ---------------------------------------------------------------------------
# usage_and_cap
# ---------------------------------------------------------------------------


class TestGetQuotaStatus:
    """``usage_and_cap`` must expose the (used, cap) pair the
    streaming generator depends on for its mid-stream guard."""

    async def test_returns_zero_zero_when_user_quota_is_unset_and_default_is_zero(
        self, factory: async_sessionmaker[AsyncSession]
    ) -> None:
        user = await _user(factory, user_quota=None, monthly_tokens=0)
        assert await usage_and_cap(user) == (0, 0)

    async def test_falls_back_to_default_setting_when_user_quota_is_none(
        self, factory: async_sessionmaker[AsyncSession]
    ) -> None:
        user = await _user(factory, user_quota=None, monthly_tokens=42, default_setting="1000")
        assert await usage_and_cap(user) == (42, 1000)

    async def test_uses_explicit_user_quota_when_set(
        self, factory: async_sessionmaker[AsyncSession]
    ) -> None:
        user = await _user(factory, user_quota=500, monthly_tokens=120, default_setting="9999")
        assert await usage_and_cap(user) == (120, 500)

    async def test_admin_override_wins_over_plan_quota(
        self, factory: async_sessionmaker[AsyncSession]
    ) -> None:
        # Override-first: the per-user admin cap of 500 wins over the Pro
        # plan's 5M quota, so an admin can throttle a paying user without
        # revoking their plan.
        user = await _user(factory, user_quota=500, plan_quota=5_000_000, monthly_tokens=120)
        assert await usage_and_cap(user) == (120, 500)

    async def test_plan_quota_used_when_legacy_override_unset(
        self, factory: async_sessionmaker[AsyncSession]
    ) -> None:
        user = await _user(factory, user_quota=None, plan_quota=5_000_000, monthly_tokens=120)
        assert await usage_and_cap(user) == (120, 5_000_000)

    async def test_user_quota_zero_means_unlimited_even_with_plan(
        self, factory: async_sessionmaker[AsyncSession]
    ) -> None:
        # ``users.token_quota == 0`` is the admin VIP gift: unlimited
        # whatever the plan. A cap of 0 is "no cap"; usage is not summed.
        user = await _user(
            factory, user_quota=0, plan_quota=5_000_000, monthly_tokens=10_000_000
        )
        assert await usage_and_cap(user) == (0, 0)


# ---------------------------------------------------------------------------
# check_quota (pre-stream gate)
# ---------------------------------------------------------------------------


class TestCheckTokenQuota:
    """The pre-stream gate must raise 429 with a stable error code so
    the frontend can surface a clean 402-style UX before any LLM work."""

    @pytest.mark.parametrize("used", [100, 250])
    async def test_raises_at_or_above_the_cap(
        self, factory: async_sessionmaker[AsyncSession], used: int
    ) -> None:
        # Above the cap: the last turn overshot, the gate still closes.
        user = await _user(factory, user_quota=100, monthly_tokens=used)
        with pytest.raises(AssemblyRefusal) as exc_info:
            await check_quota(provisional_principal(user))
        assert exc_info.value.error_code == "token_quota_exceeded"
        assert exc_info.value.status_code == 429

    async def test_passes_when_under_cap(
        self, factory: async_sessionmaker[AsyncSession]
    ) -> None:
        user = await _user(factory, user_quota=100, monthly_tokens=10)
        await check_quota(provisional_principal(user))

    async def test_passes_when_quota_disabled_zero(
        self, factory: async_sessionmaker[AsyncSession]
    ) -> None:
        user = await _user(factory, user_quota=0, monthly_tokens=10_000_000)
        await check_quota(provisional_principal(user))


# ---------------------------------------------------------------------------
# _build_quota_terminator_payload (structured terminator contract)
# ---------------------------------------------------------------------------


class TestQuotaTerminatorPayload:
    """The terminator schema is part of the API contract — frontends
    (and the future webhook design) read these exact fields."""

    def test_carries_required_fields(self) -> None:
        payload = _build_quota_terminator_payload(
            monthly_tokens=140,
            user_quota=100,
            reset_at="2026-07-01T00:00:00+00:00",
        )
        assert payload["type"] == "error"
        assert payload["code"] == "QUOTA_EXCEEDED"
        assert payload["tokens_used"] == 140
        assert payload["quota"] == 100
        assert "plan_slug" in payload

    def test_reset_at_passes_through_verbatim(self) -> None:
        # The builder is a pure passthrough for reset_at; the value is
        # computed upstream by reset_at (covered below).
        payload = _build_quota_terminator_payload(
            monthly_tokens=1,
            user_quota=1,
            reset_at="2027-01-01T00:00:00+00:00",
        )
        assert payload["reset_at"] == "2027-01-01T00:00:00+00:00"


class TestQuotaResetIso:
    """``reset_at`` resolves the billing-aligned reset boundary.

    With no subscription the window is the calendar month, so the reset
    is the first of next month."""

    async def test_free_user_resets_on_first_of_next_month(
        self, factory: async_sessionmaker[AsyncSession]
    ) -> None:
        user = await _user(factory, user_quota=None, monthly_tokens=0)
        parsed = datetime.fromisoformat((await reset_at(user)).isoformat())
        assert parsed.day == 1
        assert parsed.tzinfo is not None
        assert parsed > datetime.now(UTC)
        assert parsed - datetime.now(UTC) < timedelta(days=32)


class TestNextMonthResetIso:
    """December rollover is the easiest place to introduce off-by-one
    bugs — pin it down explicitly."""

    def test_december_rolls_into_next_year(self) -> None:
        with patch("fim_one.web.services.quota_backend.date") as mock_date:
            mock_date.today.return_value = datetime(2026, 12, 15).date()
            iso = next_month_reset().isoformat()
        # Should be 2027-01-01T00:00:00+00:00 (or equivalent).
        parsed = datetime.fromisoformat(iso)
        assert parsed.year == 2027
        assert parsed.month == 1
        assert parsed.day == 1

    def test_mid_year_rolls_to_next_month(self) -> None:
        with patch("fim_one.web.services.quota_backend.date") as mock_date:
            mock_date.today.return_value = datetime(2026, 5, 15).date()
            iso = next_month_reset().isoformat()
        parsed = datetime.fromisoformat(iso)
        assert parsed.year == 2026
        assert parsed.month == 6
        assert parsed.day == 1


# ---------------------------------------------------------------------------
# Mid-stream guard composition
# ---------------------------------------------------------------------------


class TestMidStreamGuardComposition:
    """Smoke-test the composition that the streaming generator does:
    fetch quota, decide whether to cut, then build the terminator."""

    async def test_does_not_trigger_when_well_under_budget(
        self, factory: async_sessionmaker[AsyncSession]
    ) -> None:
        user = await _user(factory, user_quota=10_000, monthly_tokens=100)
        used, cap = await usage_and_cap(user)
        # Imagine 50 estimated completion tokens added on top.
        assert not (cap > 0 and used + 50 >= cap)

    async def test_triggers_when_estimated_completion_pushes_over(
        self, factory: async_sessionmaker[AsyncSession]
    ) -> None:
        user = await _user(factory, user_quota=200, monthly_tokens=180)
        used, cap = await usage_and_cap(user)
        # 50 estimated completion tokens push us over 200.
        assert cap > 0 and used + 50 >= cap
        payload = _build_quota_terminator_payload(
            monthly_tokens=used + 50,
            user_quota=cap,
            reset_at="2026-07-01T00:00:00+00:00",
        )
        assert payload["tokens_used"] == 230
        assert payload["quota"] == 200

    async def test_unlimited_plan_skips_guard(
        self, factory: async_sessionmaker[AsyncSession]
    ) -> None:
        user = await _user(factory, user_quota=0, monthly_tokens=1_000_000)
        used, cap = await usage_and_cap(user)
        # cap == 0 short-circuits the guard regardless of usage.
        assert not (cap > 0 and used + 50 >= cap)


class TestRunFailedPayload:
    """A crashed run must not be delivered as a successful one.

    The handler used to emit a ``done`` event whose ``answer`` was
    ``"Agent error: ..."``.  The frontend renders ``done`` with a green
    check under a "Result" heading, so a crash looked exactly like a
    success and the raw exception was shown as the agent's answer.
    """

    def test_uses_the_typed_error_channel(self) -> None:
        from fim_one.web.api.chat import _run_failed_payload

        payload = _run_failed_payload(ValueError("boom"), 1.5)

        assert payload["type"] == "error"
        assert payload["code"] == "AGENT_ERROR"

    def test_carries_no_answer_field(self) -> None:
        """An ``answer`` key is what let a failure masquerade as a result."""
        from fim_one.web.api.chat import _run_failed_payload

        assert "answer" not in _run_failed_payload(ValueError("boom"), 1.0)

    def test_user_message_is_prose_not_a_python_repr(self) -> None:
        from fim_one.web.api.chat import _run_failed_payload

        payload = _run_failed_payload(ValueError("boom"), 1.0)

        assert "ValueError" not in payload["message"]
        assert payload["message"].endswith(".")

    def test_detail_keeps_the_exception_for_developers(self) -> None:
        from fim_one.web.api.chat import _run_failed_payload

        payload = _run_failed_payload(ValueError("boom"), 1.0)

        assert payload["detail"] == "ValueError: boom"

    def test_elapsed_is_preserved(self) -> None:
        from fim_one.web.api.chat import _run_failed_payload

        assert _run_failed_payload(RuntimeError("x"), 2.25)["elapsed"] == 2.25
