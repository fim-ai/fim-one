"""The quota gate: ``core.assembly.quota`` and its web backend."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from unittest.mock import patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import fim_one.db.models  # noqa: F401
from fim_one.core.assembly import quota
from fim_one.core.assembly.errors import AssemblyRefusal
from fim_one.core.assembly.principal import Binding, provisional_principal
from fim_one.db.base import Base
from fim_one.db.models.user import User

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
