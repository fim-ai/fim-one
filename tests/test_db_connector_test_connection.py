"""Ad-hoc ``POST /api/connectors/test-connection`` password resolution.

The masked ``***`` password is swapped for the stored one only when the
connector belongs to the caller and the request targets the same server,
database and account the password was saved for. Otherwise a caller could
point the stored credential at a host of their choosing.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Iterator
from typing import Any, ClassVar
from unittest.mock import patch

import pytest
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from fim_one.core.security.encryption import decrypt_db_config, encrypt_db_config
from fim_one.core.tool.connector.database.base import DatabaseDriver
from fim_one.db.base import Base
from fim_one.db.models.connector import Connector
from fim_one.db.models.user import User
from fim_one.web.api import connectors, db_connectors
from fim_one.web.exceptions import AppError
from fim_one.web.schemas import db_connector as schemas
from fim_one.web.schemas.connector import ConnectorUpdate

STORED = {
    "driver": "postgresql",
    "host": "db.internal",
    "port": 5432,
    "database": "prod",
    "username": "svc",
    "password": "s3cret",
}


class _RecordingDriver(DatabaseDriver):
    """Driver stub that records the config it was built with."""

    seen: ClassVar[list[dict[str, Any]]] = []

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(config)
        _RecordingDriver.seen.append(dict(config))

    async def connect(self) -> None: ...

    async def disconnect(self) -> None: ...

    async def test_connection(self) -> tuple[bool, str]:
        return True, "stub"

    async def list_tables(self, schema: str | None = None) -> Any: ...

    async def describe_table(self, table_name: str, schema: str | None = None) -> Any: ...

    async def execute_query(self, sql: str, params: Any = None, **kwargs: Any) -> Any: ...


@pytest.fixture()
async def session() -> AsyncIterator[AsyncSession]:
    import fim_one.db.models  # noqa: F401

    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


@pytest.fixture(autouse=True)
def driver_registry() -> Iterator[None]:
    _RecordingDriver.seen = []
    with patch.dict(
        "fim_one.core.tool.connector.database.drivers.DRIVER_REGISTRY",
        {"postgresql": _RecordingDriver},
    ):
        yield


async def _make_user(session: AsyncSession) -> User:
    user = User(id=str(uuid.uuid4()), email=f"{uuid.uuid4()}@x.io")
    session.add(user)
    await session.flush()
    return user


async def _make_db_connector(session: AsyncSession, owner: User) -> Connector:
    conn = Connector(
        user_id=owner.id,
        name="prod-db",
        type="database",
        db_config=encrypt_db_config(dict(STORED)),
        status="draft",
    )
    session.add(conn)
    await session.flush()
    return conn


def _request(connector_id: str, **overrides: Any) -> schemas.TestConnectionRequest:
    fields = {**STORED, "password": "***", **overrides}
    return schemas.TestConnectionRequest(
        db_config=schemas.DbConnectionConfig(**fields), connector_id=connector_id
    )


class TestAdhocPasswordResolution:
    @pytest.mark.asyncio
    async def test_formatting_differences_still_match(self, session: AsyncSession) -> None:
        owner = await _make_user(session)
        conn = await _make_db_connector(session, owner)

        await db_connectors.test_connection_adhoc(
            _request(conn.id, host=" DB.Internal "), current_user=owner, db=session
        )

        assert _RecordingDriver.seen[-1]["password"] == "s3cret"

    @pytest.mark.asyncio
    async def test_owner_same_target_gets_stored_password(self, session: AsyncSession) -> None:
        owner = await _make_user(session)
        conn = await _make_db_connector(session, owner)

        resp = await db_connectors.test_connection_adhoc(
            _request(conn.id), current_user=owner, db=session
        )

        assert resp.data["success"] is True
        assert _RecordingDriver.seen[-1]["password"] == "s3cret"

    @pytest.mark.asyncio
    async def test_other_user_cannot_use_stored_password(self, session: AsyncSession) -> None:
        owner = await _make_user(session)
        attacker = await _make_user(session)
        conn = await _make_db_connector(session, owner)

        with pytest.raises(AppError) as exc:
            await db_connectors.test_connection_adhoc(
                _request(conn.id, host="attacker.example"),
                current_user=attacker,
                db=session,
            )

        assert exc.value.error_code == "connector_not_found"
        assert _RecordingDriver.seen == []

    @pytest.mark.parametrize(
        "override",
        [
            {"host": "attacker.example"},
            {"port": 6543},
            {"database": "other"},
            {"username": "postgres"},
            {"ssl": True},
            {"ca_cert": "-----BEGIN CERTIFICATE-----"},
        ],
    )
    @pytest.mark.asyncio
    async def test_owner_changed_target_must_reenter_password(
        self, session: AsyncSession, override: dict[str, Any]
    ) -> None:
        owner = await _make_user(session)
        conn = await _make_db_connector(session, owner)

        with pytest.raises(AppError) as exc:
            await db_connectors.test_connection_adhoc(
                _request(conn.id, **override), current_user=owner, db=session
            )

        assert exc.value.error_code == "db_password_required"
        assert _RecordingDriver.seen == []

    @pytest.mark.asyncio
    async def test_explicit_password_skips_lookup(self, session: AsyncSession) -> None:
        user = await _make_user(session)

        await db_connectors.test_connection_adhoc(
            _request(str(uuid.uuid4()), host="anywhere.example", password="typed"),
            current_user=user,
            db=session,
        )

        assert _RecordingDriver.seen[-1]["password"] == "typed"


async def _update(
    session: AsyncSession, owner: User, conn: Connector, **overrides: Any
) -> Connector:
    db_config = {k: v for k, v in STORED.items() if k != "password"}
    db_config.update(overrides)
    db_config = {k: v for k, v in db_config.items() if v is not None}
    await connectors.update_connector(
        conn.id,
        ConnectorUpdate(db_config=db_config),
        current_user=owner,
        db=session,
    )
    refreshed = await session.get(Connector, conn.id)
    assert refreshed is not None
    return refreshed


class TestUpdateKeepsPasswordBoundToTarget:
    @pytest.mark.parametrize("password", [None, "", "***"])
    @pytest.mark.asyncio
    async def test_blank_or_masked_password_keeps_stored_one(
        self, session: AsyncSession, password: str | None
    ) -> None:
        owner = await _make_user(session)
        conn = await _make_db_connector(session, owner)

        updated = await _update(session, owner, conn, password=password, max_rows=50)

        assert decrypt_db_config(updated.db_config)["password"] == "s3cret"

    @pytest.mark.parametrize("password", [None, "***"])
    @pytest.mark.asyncio
    async def test_changed_target_without_password_is_rejected(
        self, session: AsyncSession, password: str | None
    ) -> None:
        owner = await _make_user(session)
        conn = await _make_db_connector(session, owner)

        with pytest.raises(AppError) as exc:
            await _update(session, owner, conn, password=password, host="attacker.example")

        assert exc.value.error_code == "db_password_required"

    @pytest.mark.asyncio
    async def test_changed_target_with_new_password_is_saved(self, session: AsyncSession) -> None:
        owner = await _make_user(session)
        conn = await _make_db_connector(session, owner)

        updated = await _update(session, owner, conn, password="n3w", host="replica.internal")

        cfg = decrypt_db_config(updated.db_config)
        assert cfg["host"] == "replica.internal"
        assert cfg["password"] == "n3w"
