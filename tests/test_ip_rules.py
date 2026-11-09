"""IP allow/deny rules are enforced on API requests.

Before this, rules could be created under admin Security but no request was
ever checked against them.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from starlette.requests import Request

from fim_one.db.models import IpRule
from fim_one.web.api.admin_security import IpRuleCreateRequest, create_ip_rule
from fim_one.web.exceptions import AppError
from fim_one.web.ip_rules import IpNetwork, is_client_allowed, parse_rules


def _rules(*rows: tuple[str, str]) -> tuple[list[IpNetwork], list[IpNetwork]]:
    return parse_rules(rows)


class TestDecision:
    def test_no_rules_allows_everyone(self) -> None:
        assert is_client_allowed("203.0.113.9", *_rules())
        assert is_client_allowed(None, *_rules())

    def test_deny_rule_blocks_match_only(self) -> None:
        allow, deny = _rules(("203.0.113.0/24", "deny"))
        assert not is_client_allowed("203.0.113.9", allow, deny)
        assert is_client_allowed("198.51.100.1", allow, deny)

    def test_allow_rule_switches_to_allowlist(self) -> None:
        allow, deny = _rules(("10.0.0.0/8", "allow"))
        assert is_client_allowed("10.1.2.3", allow, deny)
        assert not is_client_allowed("198.51.100.1", allow, deny)
        assert not is_client_allowed(None, allow, deny)

    def test_deny_wins_over_allow(self) -> None:
        allow, deny = _rules(("10.0.0.0/8", "allow"), ("10.0.0.5", "deny"))
        assert not is_client_allowed("10.0.0.5", allow, deny)
        assert is_client_allowed("10.0.0.6", allow, deny)

    def test_ipv6(self) -> None:
        allow, deny = _rules(("2001:db8::/32", "deny"))
        assert not is_client_allowed("2001:db8::1", allow, deny)
        assert is_client_allowed("2001:db9::1", allow, deny)

    def test_unparseable_rule_ignored(self) -> None:
        allow, deny = _rules(("not-an-ip", "deny"))
        assert (allow, deny) == ([], [])


@pytest.fixture()
def client() -> Iterator[TestClient]:
    from fim_one.web.app import create_app

    with patch("fim_one.web.app.get_setting", AsyncMock(return_value="false")):
        # No context manager: lifespan (DB init) is not needed here.
        yield TestClient(create_app(), raise_server_exceptions=False)


def _blocked(resp: Any) -> bool:
    return bool(resp.status_code == 403 and resp.json().get("error_code") == "ip_not_allowed")


def _with_rules(*rows: tuple[str, str]) -> Any:
    return patch(
        "fim_one.web.ip_rules.RuleCache.get",
        AsyncMock(return_value=parse_rules(rows)),
    )


class TestMiddleware:
    # TestClient's client address is "testclient", which is not an IP.  Routes
    # past the gate may fail without a database; only the gate's 403 matters.

    def test_allowlist_blocks_other_clients(self, client: TestClient) -> None:
        with _with_rules(("10.0.0.0/8", "allow")):
            resp = client.get("/api/auth/me")
        assert _blocked(resp)

    def test_no_rules_passes_through(self, client: TestClient) -> None:
        with _with_rules():
            resp = client.get("/api/auth/me")
        assert not _blocked(resp)

    def test_version_exempt(self, client: TestClient) -> None:
        with _with_rules(("10.0.0.0/8", "allow")):
            resp = client.get("/api/version")
        assert not _blocked(resp)

    def test_enforcement_switch(self, client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("IP_RULES_ENFORCED", "false")
        with _with_rules(("10.0.0.0/8", "allow")):
            resp = client.get("/api/version")
            resp2 = client.get("/api/auth/me")
        assert not _blocked(resp)
        assert not _blocked(resp2)


@pytest_asyncio.fixture()
async def db() -> AsyncIterator[AsyncSession]:
    import fim_one.db.models  # noqa: F401
    from fim_one.db.base import Base

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with async_sessionmaker(engine, expire_on_commit=False)() as s:
        yield s
    await engine.dispose()


def _admin_request(ip: str) -> Request:
    return Request({"type": "http", "headers": [], "client": (ip, 1234)})


class TestSelfLockoutGuard:
    """A rule change that would block the admin making it is refused."""

    @pytest.mark.asyncio
    async def test_allowlist_without_own_ip_refused(self, db: AsyncSession) -> None:
        admin = MagicMock(id="admin")
        with pytest.raises(AppError) as exc:
            await create_ip_rule(
                IpRuleCreateRequest(ip_address="10.0.0.0/8", rule_type="allow", note=None),
                _admin_request("198.51.100.7"),
                admin,
                db,
            )
        assert exc.value.error_code == "ip_rule_self_lockout"
        assert (await db.execute(select(IpRule))).scalars().all() == []

    @pytest.mark.asyncio
    async def test_deny_own_ip_refused(self, db: AsyncSession) -> None:
        with pytest.raises(AppError) as exc:
            await create_ip_rule(
                IpRuleCreateRequest(ip_address="198.51.100.7", rule_type="deny", note=None),
                _admin_request("198.51.100.7"),
                MagicMock(id="admin"),
                db,
            )
        assert exc.value.error_code == "ip_rule_self_lockout"

    @pytest.mark.asyncio
    async def test_allowlist_with_own_ip_accepted(self, db: AsyncSession) -> None:
        with patch("fim_one.web.api.admin_security.write_audit", AsyncMock()):
            resp = await create_ip_rule(
                IpRuleCreateRequest(ip_address="198.51.100.0/24", rule_type="allow", note=None),
                _admin_request("198.51.100.7"),
                MagicMock(id="admin"),
                db,
            )
        assert resp.is_active
