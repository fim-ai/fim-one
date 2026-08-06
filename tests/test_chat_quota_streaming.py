"""Tests for the mid-stream token-quota guard in ``chat.py``.

These tests cover the two paths that protect users from mid-stream
quota cutoffs being misread as generic network errors:

1. **Pre-stream quota gate** — ``_check_token_quota`` raises 429
   before any LLM work begins.
2. **Mid-stream quota terminator** — ``_get_quota_status`` /
   ``_build_quota_terminator_payload`` produce the structured
   ``error`` event that the streaming generator emits when the
   user runs out of budget partway through the answer.

External services (DB, LLM, settings) are mocked via
``unittest.mock`` per the project's ``Test Rules``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from fim_one.web.api.chat import (
    _build_quota_terminator_payload,
    _check_token_quota,
    _get_quota_status,
    _next_month_reset_iso,
    _quota_reset_iso,
)
from fim_one.web.exceptions import AppError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakeSession:
    """Minimal async-context-manager session whose ``execute`` returns a
    pre-baked sequence of scalar results.

    ``_get_quota_status`` issues four queries in order:
      1. ``User.token_quota`` -> ``int | None``
      2. ``BillingPlan.monthly_token_quota`` (via plan_id join) -> ``int | None``
      3. ``resolve_quota_window`` subscription lookup (read via ``.first()``)
      4. ``coalesce(sum(Conversation.total_tokens), 0)`` -> ``int``

    Query 3 is served as ``.first() -> None`` (see ``execute``), so the
    quota window resolves to the calendar month — the sum value at slot 4
    is therefore independent of the (ignored) WHERE-clause window. The
    ``None`` placeholder in ``_scalar_results`` exists only to keep the
    FIFO index aligned so the sum still lands at slot 4.

    The constructor takes the user_quota / monthly_tokens pair (with an
    optional ``plan_quota`` override) so each test can dial in any state.
    """

    def __init__(
        self,
        *,
        user_quota: int | None,
        monthly_tokens: int,
        default_setting: str = "0",
        plan_quota: int | None = None,
    ) -> None:
        self._scalar_results = [user_quota, plan_quota, None, monthly_tokens]
        self._default_setting = default_setting
        self._scalar_idx = 0

    async def __aenter__(self) -> "_FakeSession":
        return self

    async def __aexit__(self, *_: Any) -> None:
        return None

    async def execute(self, _stmt: Any) -> Any:
        idx = self._scalar_idx
        self._scalar_idx += 1
        result = MagicMock()
        # Both call sites use ``.scalar_one_or_none()`` first then
        # ``.scalar_one()`` for the sum; we serve them out of the same
        # FIFO buffer.  The first call (User.token_quota) uses
        # scalar_one_or_none; the second (sum) uses scalar_one.
        scalar_value = (
            self._scalar_results[idx]
            if idx < len(self._scalar_results)
            else 0
        )
        result.scalar_one_or_none = MagicMock(return_value=scalar_value)
        result.scalar_one = MagicMock(return_value=scalar_value or 0)
        # The quota-window subscription lookup reads via ``.first()``;
        # ``None`` means "no subscription" → calendar-month window.
        result.first = MagicMock(return_value=None)
        return result


def _patch_create_session(session: _FakeSession) -> Any:
    """Patch ``fim_one.db.create_session`` to return *session*.

    ``chat._get_quota_status`` imports ``create_session`` lazily, so we
    have to patch the attribute on the source module rather than on
    ``chat`` itself.
    """

    def factory() -> _FakeSession:
        return session

    return patch("fim_one.db.create_session", new=factory)


def _patch_get_setting(value: str) -> Any:
    """Patch the admin-utils ``get_setting`` coroutine to a constant."""
    return patch(
        "fim_one.web.api.admin_utils.get_setting",
        new=AsyncMock(return_value=value),
    )


# ---------------------------------------------------------------------------
# _get_quota_status
# ---------------------------------------------------------------------------


class TestGetQuotaStatus:
    """``_get_quota_status`` must expose the (used, cap) pair the
    streaming generator depends on for its mid-stream guard."""

    @pytest.mark.asyncio
    async def test_returns_zero_zero_when_user_quota_is_unset_and_default_is_zero(
        self,
    ) -> None:
        session = _FakeSession(user_quota=None, monthly_tokens=0)
        with _patch_create_session(session), _patch_get_setting("0"):
            used, cap = await _get_quota_status("user-1")
        assert used == 0
        assert cap == 0

    @pytest.mark.asyncio
    async def test_falls_back_to_default_setting_when_user_quota_is_none(
        self,
    ) -> None:
        session = _FakeSession(user_quota=None, monthly_tokens=42)
        with _patch_create_session(session), _patch_get_setting("1000"):
            used, cap = await _get_quota_status("user-1")
        assert cap == 1000
        assert used == 42

    @pytest.mark.asyncio
    async def test_uses_explicit_user_quota_when_set(self) -> None:
        session = _FakeSession(user_quota=500, monthly_tokens=120)
        with _patch_create_session(session), _patch_get_setting("9999"):
            used, cap = await _get_quota_status("user-1")
        assert cap == 500
        assert used == 120

    @pytest.mark.asyncio
    async def test_admin_override_wins_over_plan_quota(self) -> None:
        # Scheme A (override-first): the per-user admin cap of 500 wins
        # over the Pro plan's 5M quota — this is the VIP/abuse-control
        # use case where an admin needs to throttle a paying user
        # without revoking their plan.
        session = _FakeSession(
            user_quota=500,
            plan_quota=5_000_000,
            monthly_tokens=120,
        )
        with _patch_create_session(session), _patch_get_setting("0"):
            used, cap = await _get_quota_status("user-1")
        assert cap == 500
        assert used == 120

    @pytest.mark.asyncio
    async def test_plan_quota_used_when_legacy_override_unset(self) -> None:
        # When ``users.token_quota IS NULL``, the user's plan tier
        # provides the quota.
        session = _FakeSession(
            user_quota=None,
            plan_quota=5_000_000,
            monthly_tokens=120,
        )
        with _patch_create_session(session), _patch_get_setting("0"):
            used, cap = await _get_quota_status("user-1")
        assert cap == 5_000_000
        assert used == 120

    @pytest.mark.asyncio
    async def test_user_quota_zero_means_unlimited_even_with_plan(self) -> None:
        # ``users.token_quota == 0`` is the admin VIP gift — the user
        # has unlimited tokens regardless of plan.
        session = _FakeSession(
            user_quota=0,
            plan_quota=5_000_000,
            monthly_tokens=10_000_000,
        )
        with _patch_create_session(session), _patch_get_setting("0"):
            used, cap = await _get_quota_status("user-1")
        assert cap == 0  # 0 = unlimited / disabled in chat.py's contract
        assert used == 0


# ---------------------------------------------------------------------------
# _check_token_quota (pre-stream gate)
# ---------------------------------------------------------------------------


class TestCheckTokenQuota:
    """The pre-stream gate must raise 429 with a stable error code so
    the frontend can surface a clean 402-style UX before any LLM work."""

    @pytest.mark.asyncio
    async def test_raises_when_quota_already_exceeded(self) -> None:
        session = _FakeSession(user_quota=100, monthly_tokens=100)
        with _patch_create_session(session), _patch_get_setting("0"):
            with pytest.raises(AppError) as exc_info:
                await _check_token_quota("user-1")
        assert exc_info.value.error_code == "token_quota_exceeded"
        assert exc_info.value.status_code == 429

    @pytest.mark.asyncio
    async def test_raises_when_used_strictly_above_cap(self) -> None:
        # E.g. last turn overshot — pre-stream gate must still close the door.
        session = _FakeSession(user_quota=100, monthly_tokens=250)
        with _patch_create_session(session), _patch_get_setting("0"):
            with pytest.raises(AppError):
                await _check_token_quota("user-1")

    @pytest.mark.asyncio
    async def test_passes_when_under_cap(self) -> None:
        session = _FakeSession(user_quota=100, monthly_tokens=10)
        with _patch_create_session(session), _patch_get_setting("0"):
            # No raise = pass.
            await _check_token_quota("user-1")

    @pytest.mark.asyncio
    async def test_passes_when_quota_disabled_zero(self) -> None:
        # cap == 0 means "unlimited" — gate must not fire even when
        # monthly usage is huge.
        session = _FakeSession(user_quota=0, monthly_tokens=10_000_000)
        with _patch_create_session(session), _patch_get_setting("0"):
            await _check_token_quota("user-1")


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
        # computed upstream by _quota_reset_iso (covered below).
        payload = _build_quota_terminator_payload(
            monthly_tokens=1,
            user_quota=1,
            reset_at="2027-01-01T00:00:00+00:00",
        )
        assert payload["reset_at"] == "2027-01-01T00:00:00+00:00"


class TestQuotaResetIso:
    """``_quota_reset_iso`` resolves the billing-aligned reset boundary.

    With no subscription the window is the calendar month, so the reset
    is the first of next month — the same contract the old inline
    ``_next_month_reset_iso`` provided."""

    @pytest.mark.asyncio
    async def test_free_user_resets_on_first_of_next_month(self) -> None:
        session = _FakeSession(user_quota=None, monthly_tokens=0)
        with _patch_create_session(session):
            iso = await _quota_reset_iso("user-1")
        parsed = datetime.fromisoformat(iso)
        assert parsed.day == 1
        assert parsed.tzinfo is not None
        assert parsed > datetime.now(UTC)
        assert parsed - datetime.now(UTC) < timedelta(days=32)


class TestNextMonthResetIso:
    """December rollover is the easiest place to introduce off-by-one
    bugs — pin it down explicitly."""

    def test_december_rolls_into_next_year(self) -> None:
        with patch("fim_one.web.api.chat.date") as mock_date:
            mock_date.today.return_value = datetime(2026, 12, 15).date()
            iso = _next_month_reset_iso()
        # Should be 2027-01-01T00:00:00+00:00 (or equivalent).
        parsed = datetime.fromisoformat(iso)
        assert parsed.year == 2027
        assert parsed.month == 1
        assert parsed.day == 1

    def test_mid_year_rolls_to_next_month(self) -> None:
        with patch("fim_one.web.api.chat.date") as mock_date:
            mock_date.today.return_value = datetime(2026, 5, 15).date()
            iso = _next_month_reset_iso()
        parsed = datetime.fromisoformat(iso)
        assert parsed.year == 2026
        assert parsed.month == 6
        assert parsed.day == 1


# ---------------------------------------------------------------------------
# Mid-stream guard composition
# ---------------------------------------------------------------------------


class TestMidStreamGuardComposition:
    """Smoke-test the composition that the streaming generator does:
    fetch quota, decide whether to cut, then build the terminator.
    These three steps are the load-bearing ones — exercising them
    together protects against drift if any helper is renamed."""

    @pytest.mark.asyncio
    async def test_does_not_trigger_when_well_under_budget(self) -> None:
        session = _FakeSession(user_quota=10_000, monthly_tokens=100)
        with _patch_create_session(session), _patch_get_setting("0"):
            used, cap = await _get_quota_status("user-1")
        # Imagine 50 estimated completion tokens added on top.
        triggered = cap > 0 and used + 50 >= cap
        assert triggered is False

    @pytest.mark.asyncio
    async def test_triggers_when_estimated_completion_pushes_over(
        self,
    ) -> None:
        session = _FakeSession(user_quota=200, monthly_tokens=180)
        with _patch_create_session(session), _patch_get_setting("0"):
            used, cap = await _get_quota_status("user-1")
        # 50 estimated completion tokens push us over 200.
        triggered = cap > 0 and used + 50 >= cap
        assert triggered is True
        payload = _build_quota_terminator_payload(
            monthly_tokens=used + 50,
            user_quota=cap,
            reset_at="2026-07-01T00:00:00+00:00",
        )
        assert payload["tokens_used"] == 230
        assert payload["quota"] == 200

    @pytest.mark.asyncio
    async def test_unlimited_plan_skips_guard(self) -> None:
        session = _FakeSession(user_quota=0, monthly_tokens=1_000_000)
        with _patch_create_session(session), _patch_get_setting("0"):
            used, cap = await _get_quota_status("user-1")
        # cap == 0 short-circuits the guard regardless of usage.
        triggered = cap > 0 and used + 50 >= cap
        assert triggered is False


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
