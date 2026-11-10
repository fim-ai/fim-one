"""The quota gate: ``core.assembly.quota`` and its web backend.

The tests that read the database run on SQLite and, with ``FIM_TEST_PG_URL``
pointing at an empty throwaway database, on PostgreSQL.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any
from unittest.mock import patch

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import fim_one.db.models  # noqa: F401
from fim_one.core.assembly import quota
from fim_one.core.assembly.errors import AssemblyRefusal
from fim_one.core.assembly.principal import Binding, provisional_principal
from fim_one.db.base import Base
from fim_one.db.models import BillingPlan, Conversation, SystemSetting
from fim_one.db.models.user import User
from fim_one.db.models.workflow import Workflow, WorkflowRun

RESET = datetime(2027, 1, 1, tzinfo=UTC)


class _Backend:
    """A backend with fixed answers that records what it was asked."""

    def __init__(self, *, used: int = 0, cap: int = 0, unentitled: bool = False) -> None:
        self.used, self.cap, self._unentitled = used, cap, unentitled
        self.asked: list[str] = []

    async def usage_and_cap(self, user_id: str) -> tuple[int, int]:
        self.asked.append("usage_and_cap")
        return self.used, self.cap

    async def unentitled(self, user_id: str) -> bool:
        self.asked.append("unentitled")
        return self._unentitled

    async def reset_at(self, user_id: str) -> datetime:
        self.asked.append("reset_at")
        return RESET


@pytest.fixture()
def backend() -> _Backend:
    fake = _Backend()
    quota.set_quota_backend(fake)
    return fake


class TestPort:
    async def test_nothing_is_let_through_with_no_backend_registered(self) -> None:
        quota.set_quota_backend(None)
        with pytest.raises(RuntimeError, match="No quota backend"):
            await quota.check_quota(provisional_principal("u1"))
        with pytest.raises(RuntimeError, match="No quota backend"):
            await quota.quota_status("u1")

    def test_the_app_registers_the_web_backend(self) -> None:
        from fim_one.web.services.assembly_ports import register_assembly_ports
        from fim_one.web.services.quota_backend import WebQuotaBackend

        quota.set_quota_backend(None)
        register_assembly_ports()
        assert isinstance(quota.get_quota_backend(), WebQuotaBackend)


class TestCheckQuota:
    async def test_within_the_cap_passes(self, backend: _Backend) -> None:
        backend.used, backend.cap = 10, 100
        await quota.check_quota(provisional_principal("u1"))

    @pytest.mark.parametrize("used", [100, 250])
    async def test_at_or_over_the_cap_is_429(self, backend: _Backend, used: int) -> None:
        backend.used, backend.cap = used, 100
        with pytest.raises(AssemblyRefusal) as refused:
            await quota.check_quota(provisional_principal("u1"))
        assert (refused.value.error_code, refused.value.status_code) == (
            "token_quota_exceeded",
            429,
        )

    async def test_a_cap_of_zero_is_no_cap(self, backend: _Backend) -> None:
        backend.used, backend.cap = 10_000_000, 0
        await quota.check_quota(provisional_principal("u1"))

    async def test_no_plan_is_402_and_comes_before_the_cap(self, backend: _Backend) -> None:
        backend.used, backend.cap, backend._unentitled = 500, 100, True
        with pytest.raises(AssemblyRefusal) as refused:
            await quota.check_quota(provisional_principal("u1"))
        assert (refused.value.error_code, refused.value.status_code) == (
            "subscription_required",
            402,
        )


class TestQuotaStatus:
    async def test_the_reset_time_is_only_looked_up_when_asked_for(
        self, backend: _Backend
    ) -> None:
        backend.used, backend.cap = 5, 10
        status = await quota.quota_status("u1")
        assert (status.used, status.cap, status.unlimited, status.exhausted) == (5, 10, False, False)
        assert status.reset_at is None
        assert backend.asked == ["usage_and_cap"]

        status = await quota.quota_status("u1", with_reset=True)
        assert status.reset_at == RESET
        assert await quota.quota_reset_at("u1") == RESET

    async def test_no_cap_is_never_exhausted(self, backend: _Backend) -> None:
        backend.used, backend.cap = 99, 0
        status = await quota.quota_status("u1")
        assert status.unlimited and not status.exhausted


_PG = os.environ.get("FIM_TEST_PG_URL")


def _backends() -> list[Any]:
    return [
        pytest.param("sqlite", id="sqlite"),
        pytest.param(
            "postgresql",
            id="postgresql",
            marks=pytest.mark.skipif(not _PG, reason="FIM_TEST_PG_URL not set"),
        ),
    ]


@pytest.fixture(params=_backends())
async def factory(
    request: pytest.FixtureRequest,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """A database the app's session factory points at: SQLite, and PostgreSQL
    (a schema of its own in the throwaway database) when one is given."""
    admin = None
    schema = f"assembly_quota_{uuid.uuid4().hex[:12]}"
    if request.param == "sqlite":
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    else:
        assert _PG is not None
        url = make_url(_PG).set(drivername="postgresql+asyncpg")
        admin = create_async_engine(url)
        async with admin.begin() as conn:
            await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
        engine = create_async_engine(
            url, connect_args={"server_settings": {"search_path": schema}}
        )
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        maker = async_sessionmaker(engine, expire_on_commit=False)

        @asynccontextmanager
        async def _create_session() -> AsyncIterator[AsyncSession]:
            async with maker() as s:
                yield s

        with patch("fim_one.db.create_session", _create_session):
            yield maker
    finally:
        await engine.dispose()
        if admin is not None:
            async with admin.begin() as conn:
                await conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
            await admin.dispose()


async def _users(factory: async_sessionmaker[AsyncSession], **active: bool) -> None:
    async with factory() as s:
        for uid, is_active in active.items():
            s.add(User(id=uid, email=f"{uid}@x.io", username=uid, is_active=is_active))
        await s.commit()


class TestRunGate:
    """Owner account, acting account, then plan and quota: in that order."""

    BINDING = Binding(kind="workflow", id="wf", owner_id="author")

    async def test_both_accounts_on_and_within_quota_passes(
        self, factory: async_sessionmaker[AsyncSession], backend: _Backend
    ) -> None:
        await _users(factory, author=True, runner=True)
        await quota.AssemblyRunGate().check(provisional_principal("runner"), self.BINDING)
        assert "usage_and_cap" in backend.asked

    @pytest.mark.parametrize(
        ("author", "runner", "code"),
        [
            (False, True, "workflow_owner_inactive"),
            (False, False, "workflow_owner_inactive"),
            (True, False, "account_disabled"),
        ],
    )
    async def test_a_disabled_account_is_refused_before_quota_is_looked_at(
        self,
        factory: async_sessionmaker[AsyncSession],
        backend: _Backend,
        author: bool,
        runner: bool,
        code: str,
    ) -> None:
        await _users(factory, author=author, runner=runner)
        with pytest.raises(AssemblyRefusal) as refused:
            await quota.AssemblyRunGate().check(provisional_principal("runner"), self.BINDING)
        assert (refused.value.error_code, refused.value.status_code) == (code, 403)
        assert backend.asked == []

    @pytest.mark.parametrize("exists", [True, False])
    async def test_with_no_binding_a_disabled_acting_account_is_account_disabled(
        self, factory: async_sessionmaker[AsyncSession], backend: _Backend, exists: bool
    ) -> None:
        """It was reported as ``workflow_owner_inactive``: there is no owner here."""
        if exists:
            await _users(factory, runner=False)
        with pytest.raises(AssemblyRefusal) as refused:
            await quota.AssemblyRunGate().check(provisional_principal("runner"))
        assert (refused.value.error_code, refused.value.status_code) == ("account_disabled", 403)
        assert backend.asked == []

    async def test_an_account_that_does_not_exist_is_refused(
        self, factory: async_sessionmaker[AsyncSession], backend: _Backend
    ) -> None:
        await _users(factory, author=True)
        with pytest.raises(AssemblyRefusal) as refused:
            await quota.AssemblyRunGate().check(provisional_principal("ghost"), self.BINDING)
        assert refused.value.error_code == "account_disabled"

    async def test_over_quota_is_refused(
        self, factory: async_sessionmaker[AsyncSession], backend: _Backend
    ) -> None:
        await _users(factory, author=True, runner=True)
        backend.used, backend.cap = 100, 50
        with pytest.raises(AssemblyRefusal) as refused:
            await quota.AssemblyRunGate().check(provisional_principal("runner"), self.BINDING)
        assert refused.value.error_code == "token_quota_exceeded"

    async def test_with_no_binding_the_acting_account_is_the_only_one_checked(
        self, factory: async_sessionmaker[AsyncSession], backend: _Backend
    ) -> None:
        await _users(factory, runner=True)
        await quota.AssemblyRunGate().check(provisional_principal("runner"))


# ---------------------------------------------------------------------------
# The chain, end to end: check_quota over the web backend and a real database
# ---------------------------------------------------------------------------

PLAN_CAP = 5_000
DEFAULT_CAP = 1_000
OVERRIDE_CAP = 300
UNLIMITED = 0

# (admin override on the user row, access_model, user has a plan)
#   -> the cap enforced, or the refusal that comes before any cap.
# The override wins everywhere; the plan counts only while the instance
# sells access by plan; ``off`` is the system default for everyone.
CHAIN: dict[tuple[int | None, str, bool], int | str] = {
    (None, "off", False): DEFAULT_CAP,
    (None, "off", True): DEFAULT_CAP,  # a leftover plan_id is not read
    (None, "freemium", False): DEFAULT_CAP,
    (None, "freemium", True): PLAN_CAP,
    (None, "paid_only", False): "subscription_required",
    (None, "paid_only", True): PLAN_CAP,
    (0, "off", False): UNLIMITED,
    (0, "off", True): UNLIMITED,
    (0, "freemium", False): UNLIMITED,
    (0, "freemium", True): UNLIMITED,
    (0, "paid_only", False): UNLIMITED,
    (0, "paid_only", True): UNLIMITED,
    (OVERRIDE_CAP, "off", False): OVERRIDE_CAP,
    (OVERRIDE_CAP, "off", True): OVERRIDE_CAP,
    (OVERRIDE_CAP, "freemium", False): OVERRIDE_CAP,
    (OVERRIDE_CAP, "freemium", True): OVERRIDE_CAP,
    (OVERRIDE_CAP, "paid_only", False): OVERRIDE_CAP,
    (OVERRIDE_CAP, "paid_only", True): OVERRIDE_CAP,
}


async def _seed(
    factory: async_sessionmaker[AsyncSession],
    *,
    override: int | None,
    access_model: str,
    has_plan: bool,
    used: int = 0,
) -> str:
    async with factory() as s:
        s.add(SystemSetting(key="access_model", value=access_model))
        s.add(SystemSetting(key="default_token_quota", value=str(DEFAULT_CAP)))
        plan = BillingPlan(slug="pro", name="Pro", monthly_token_quota=PLAN_CAP)
        s.add(plan)
        await s.flush()
        user = User(
            id="u1",
            email="u1@x.io",
            username="u1",
            token_quota=override,
            plan_id=plan.id if has_plan else None,
        )
        s.add(user)
        if used:
            s.add(Conversation(user_id="u1", title="t", mode="react", total_tokens=used))
        await s.commit()
    return "u1"


async def _refusal(user_id: str) -> str | None:
    try:
        await quota.check_quota(provisional_principal(user_id))
    except AssemblyRefusal as refused:
        return refused.error_code
    return None


class TestTheChain:
    """Three override states x three access models x with and without a plan."""

    @pytest.mark.parametrize(("case", "want"), CHAIN.items(), ids=lambda v: str(v))
    async def test_the_cap_enforced_is_the_one_the_usage_card_shows(
        self,
        factory: async_sessionmaker[AsyncSession],
        case: tuple[int | None, str, bool],
        want: int | str,
    ) -> None:
        from fim_one.web.services.quota_enforcer import get_user_quota_by_id

        override, access_model, has_plan = case
        user_id = await _seed(
            factory, override=override, access_model=access_model, has_plan=has_plan
        )

        if isinstance(want, str):
            assert await _refusal(user_id) == want
            return
        status = await quota.quota_status(user_id)
        assert status.cap == want
        # What the Usage card reads (``None`` there is "unlimited").
        async with factory() as s:
            assert (await get_user_quota_by_id(user_id, s) or 0) == want

    @pytest.mark.parametrize(
        ("case", "want"),
        [(c, w) for c, w in CHAIN.items() if isinstance(w, int) and w > 0],
        ids=lambda v: str(v),
    )
    async def test_the_cap_is_where_the_refusal_starts(
        self,
        factory: async_sessionmaker[AsyncSession],
        case: tuple[int | None, str, bool],
        want: int,
    ) -> None:
        override, access_model, has_plan = case
        user_id = await _seed(
            factory, override=override, access_model=access_model, has_plan=has_plan, used=want - 1
        )
        assert await _refusal(user_id) is None
        async with factory() as s:
            s.add(Workflow(id="w", user_id=user_id, name="w", blueprint={}))
            await s.flush()
            s.add(WorkflowRun(workflow_id="w", user_id=user_id, blueprint_snapshot={}, total_tokens=1))
            await s.commit()
        assert await _refusal(user_id) == "token_quota_exceeded"

    async def test_off_with_a_leftover_plan_enforces_the_system_default(
        self, factory: async_sessionmaker[AsyncSession]
    ) -> None:
        """B14. The plan's 5 000 used to be enforced while the card showed 1 000."""
        user_id = await _seed(
            factory, override=None, access_model="off", has_plan=True, used=DEFAULT_CAP
        )
        assert await _refusal(user_id) == "token_quota_exceeded"

    async def test_no_cap_anywhere_is_unlimited(
        self, factory: async_sessionmaker[AsyncSession]
    ) -> None:
        user_id = await _seed(factory, override=None, access_model="off", has_plan=False, used=10**9)
        async with factory() as s:
            row = await s.get(SystemSetting, "default_token_quota")
            assert row is not None
            row.value = "0"
            await s.commit()
        assert await _refusal(user_id) is None
        assert (await quota.quota_status(user_id)).unlimited


class TestALookupThatFailsRefuses:
    """B14. A plan or usage lookup that errors used to let the run through."""

    async def test_the_cap_lookup_failing_is_503(
        self, factory: async_sessionmaker[AsyncSession]
    ) -> None:
        user_id = await _seed(factory, override=None, access_model="freemium", has_plan=True)
        with patch(
            "fim_one.web.services.quota_enforcer.get_user_quota_by_id",
            side_effect=RuntimeError("billing_plans is gone"),
        ):
            with pytest.raises(AssemblyRefusal) as refused:
                await quota.check_quota(provisional_principal(user_id))
        assert (refused.value.error_code, refused.value.status_code) == ("quota_unavailable", 503)

    async def test_the_plan_check_failing_is_503(
        self, factory: async_sessionmaker[AsyncSession]
    ) -> None:
        user_id = await _seed(factory, override=None, access_model="paid_only", has_plan=False)
        with patch(
            "fim_one.web.services.quota_enforcer.is_user_unentitled",
            side_effect=RuntimeError("database is down"),
        ):
            with pytest.raises(AssemblyRefusal) as refused:
                await quota.check_quota(provisional_principal(user_id))
        assert (refused.value.error_code, refused.value.status_code) == ("quota_unavailable", 503)

    async def test_the_message_does_not_carry_the_error(
        self, factory: async_sessionmaker[AsyncSession]
    ) -> None:
        user_id = await _seed(factory, override=None, access_model="freemium", has_plan=True)
        with patch(
            "fim_one.web.services.quota_enforcer.get_user_quota_by_id",
            side_effect=RuntimeError("password=hunter2"),
        ):
            with pytest.raises(AssemblyRefusal) as refused:
                await quota.check_quota(provisional_principal(user_id))
        assert "hunter2" not in refused.value.detail
