"""Agent binding uses visibility (own + subscribed), not strict ownership (PR-1.3).

Binding a connector/KB/MCP server to an agent used to require the user to *own*
it, so a connector the user had legitimately subscribed to (org-shared or
Market-installed) was rejected with ``connector_ownership_denied``. Binding now
follows the same visibility model as chat assembly and the resource lists: a
subscribed resource is bindable; a resource the user can neither see nor
subscribe to is still refused.
"""

from __future__ import annotations

from typing import AsyncIterator

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from fim_one.db.base import Base
from fim_one.db.models.connector import Connector
from fim_one.db.models.resource_subscription import ResourceSubscription
from fim_one.web.api.agents import _validate_binding_ownership
from fim_one.web.exceptions import AppError

OWNER = "owner-user"
SUBSCRIBER = "subscriber-user"
STRANGER = "stranger-user"
CID = "connector-1"


@pytest.fixture()
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        s.add(
            Connector(
                id=CID,
                user_id=OWNER,
                name="shared-conn",
                type="api",
                base_url="https://api.example.com",
                status="published",
                # A subscribable connector is one its owner published: the
                # subscription only grants access while that holds.
                visibility="org",
                org_id="org-1",
            )
        )
        # SUBSCRIBER has an explicit subscription to OWNER's connector.
        s.add(
            ResourceSubscription(
                user_id=SUBSCRIBER,
                resource_type="connector",
                resource_id=CID,
                org_id="org-1",
            )
        )
        await s.commit()
        yield s
    await engine.dispose()


class TestBindingVisibility:
    @pytest.mark.asyncio
    async def test_owner_can_bind_own_connector(self, session: AsyncSession) -> None:
        # Should not raise.
        await _validate_binding_ownership(OWNER, session, connector_ids=[CID])

    @pytest.mark.asyncio
    async def test_subscriber_can_bind_subscribed_connector(
        self, session: AsyncSession
    ) -> None:
        # The fix: a subscribed connector is now bindable (previously 403).
        await _validate_binding_ownership(SUBSCRIBER, session, connector_ids=[CID])

    @pytest.mark.asyncio
    async def test_stranger_cannot_bind_unseen_connector(
        self, session: AsyncSession
    ) -> None:
        with pytest.raises(AppError) as exc:
            await _validate_binding_ownership(STRANGER, session, connector_ids=[CID])
        assert exc.value.error_code == "connector_ownership_denied"

    @pytest.mark.asyncio
    async def test_empty_binding_is_noop(self, session: AsyncSession) -> None:
        await _validate_binding_ownership(STRANGER, session, connector_ids=None)
        await _validate_binding_ownership(STRANGER, session, connector_ids=[])

    @pytest.mark.asyncio
    async def test_unpublished_connector_is_no_longer_bindable(
        self, session: AsyncSession
    ) -> None:
        """The subscription row survives unpublish; the access does not."""
        conn = await session.get(Connector, CID)
        assert conn is not None
        conn.visibility = "personal"
        conn.org_id = None
        await session.commit()

        with pytest.raises(AppError) as exc:
            await _validate_binding_ownership(SUBSCRIBER, session, connector_ids=[CID])
        assert exc.value.error_code == "connector_ownership_denied"

    @pytest.mark.asyncio
    async def test_pending_review_connector_is_not_bindable(
        self, session: AsyncSession
    ) -> None:
        """A resource put back into review is not reachable meanwhile."""
        conn = await session.get(Connector, CID)
        assert conn is not None
        conn.publish_status = "pending_review"
        await session.commit()

        with pytest.raises(AppError) as exc:
            await _validate_binding_ownership(SUBSCRIBER, session, connector_ids=[CID])
        assert exc.value.error_code == "connector_ownership_denied"

    @pytest.mark.asyncio
    async def test_owner_keeps_access_after_unpublish(
        self, session: AsyncSession
    ) -> None:
        """Revocation applies to subscribers, never to the owner."""
        conn = await session.get(Connector, CID)
        assert conn is not None
        conn.visibility = "personal"
        conn.org_id = None
        await session.commit()

        await _validate_binding_ownership(OWNER, session, connector_ids=[CID])
