"""The read-only upgrade check lists the rows the current rules refuse, and writes nothing."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import fim_one.db.models  # noqa: F401
from fim_one.db.base import Base
from fim_one.db.models import Agent, SystemSetting, User
from fim_one.db.models.connector import Connector
from fim_one.db.models.model_config import ModelConfig
from fim_one.web.services import upgrade_check

KEY = "sk-check-0123456789abcdef"


@pytest.fixture()
async def db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[AsyncSession]:
    for env in ("DB_CONNECTOR_ALLOWED_HOSTS", "DATABASE_URL", "REDIS_URL"):
        monkeypatch.delenv(env, raising=False)
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'app.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        s.add_all(
            [
                User(id="u-1", username="alice", email="a@example.test", avatar="builtin:cat"),
                User(id="u-2", username="bob", email="b@example.test", avatar="../outside.cfg"),
                User(id="u-3", username="carol", email="c@example.test", avatar="avatars/u-3_17.png"),
                User(id="u-4", username="dave", email="d@example.test", avatar="avatars/u-1_17.png"),
                User(id="u-5", username="erin", email="e@example.test"),
            ]
        )
        s.add_all(
            [
                Connector(id="c-int", user_id="u-1", name="internal", type="database",
                          base_url="", db_config={"driver": "postgresql", "host": "10.0.0.5", "port": 5432}),
                Connector(id="c-pub", user_id="u-1", name="public", type="database",
                          base_url="", db_config={"driver": "postgresql", "host": "93.184.216.34", "port": 5432}),
                Connector(id="c-api", user_id="u-1", name="api", type="api", base_url="http://10.0.0.5"),
            ]
        )
        s.add_all(
            [
                ModelConfig(id="mc-user", user_id="u-1", name="own", provider="openai",
                            model_name="m", base_url="https://93.184.216.34/v1", api_key=KEY),
                ModelConfig(id="mc-admin", user_id=None, name="sys", provider="openai",
                            model_name="m", base_url="http://10.0.0.9/v1"),
            ]
        )
        s.add_all(
            [
                Agent(id="a-shared", user_id="u-1", name="shared", visibility="org",
                      model_config_json={"model_name": "m", "base_url": "https://93.184.216.34/v1", "api_key": KEY}),
                Agent(id="a-mine", user_id="u-1", name="mine",
                      model_config_json={"model_name": "m", "base_url": "https://10.1.1.1/v1", "api_key": KEY}),
                Agent(id="a-nokey", user_id="u-1", name="nokey",
                      model_config_json={"model_name": "m", "base_url": "https://93.184.216.34/v1"}),
                Agent(id="a-cfg", user_id="u-1", name="cfg", visibility="org", model_config_json={"model_name": "m"}),
                Agent(id="a-none", user_id="u-1", name="none"),
                Agent(id="a-usercfg", user_id="u-1", name="usercfg", visibility="org",
                      model_config_json={"model_config_id": "mc-user"}),
                Agent(id="a-admincfg", user_id="u-1", name="admincfg", visibility="org",
                      model_config_json={"model_config_id": "mc-admin"}),
            ]
        )
        await s.commit()
    async with factory() as s:
        yield s
    await engine.dispose()


async def _count(db: AsyncSession) -> tuple[int, int, int]:
    return (
        await db.scalar(select(func.count()).select_from(User)) or 0,
        await db.scalar(select(func.count()).select_from(Connector)) or 0,
        await db.scalar(select(func.count()).select_from(Agent)) or 0,
    )


class TestUpgradeCheck:
    async def test_lists_affected_rows(self, db: AsyncSession) -> None:
        before = await _count(db)
        report = await upgrade_check.collect(db)

        assert [row["id"] for row in report.database_connectors] == ["c-int"]
        assert "does not resolve to a public address" in report.database_connectors[0]["reason"]

        agents = {row["id"]: row for row in report.agents_with_custom_endpoint}
        assert set(agents) == {"a-shared", "a-mine", "a-nokey", "a-usercfg"}
        assert agents["a-usercfg"]["refused_to_others"]
        assert agents["a-shared"]["shared"] and agents["a-shared"]["refused_to_others"]
        assert agents["a-shared"]["endpoint_refused"] is None
        assert not agents["a-mine"]["shared"] and "public" in agents["a-mine"]["endpoint_refused"]
        assert "api_key" in agents["a-nokey"]["endpoint_refused"]

        assert {row["id"] for row in report.avatars} == {"u-2", "u-4"}

        # Nothing secret is reported, and nothing changed.
        assert KEY not in json.dumps(report.__dict__)
        assert "outside.cfg" not in json.dumps(report.__dict__)
        assert await _count(db) == before

    async def test_the_admin_setting_is_reflected(self, db: AsyncSession) -> None:
        db.add(SystemSetting(key="agents.shared_custom_model_endpoints", value="true"))
        await db.commit()
        report = await upgrade_check.collect(db)
        shared = next(row for row in report.agents_with_custom_endpoint if row["id"] == "a-shared")
        assert shared["refused_to_others"] is False

    def test_text_output_names_rows_not_secrets(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        report = upgrade_check.Report(
            database_connectors=[{"id": "c", "name": "n", "user_id": "u", "host": "10.0.0.5",
                                  "port": 5432, "reason": "internal"}],
            agents_with_custom_endpoint=[{"id": "a", "name": "n", "user_id": "u", "shared": True,
                                          "refused_to_others": True, "endpoint_refused": None}],
            avatars=[{"id": "u", "username": "x"}],
        )
        upgrade_check._print_text(report)
        out = capsys.readouterr().out
        assert "Database connectors refused (1)" in out
        assert "refused to everyone but its owner" in out
        assert "profile picture" in out

    def test_cli_on_an_empty_database(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        import asyncio

        url = f"sqlite+aiosqlite:///{tmp_path / 'empty.db'}"

        async def _create() -> None:
            engine = create_async_engine(url)
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            await engine.dispose()

        asyncio.run(_create())
        monkeypatch.setenv("DATABASE_URL", url)
        assert upgrade_check.main(["--json"]) == 0
        data: dict[str, Any] = json.loads(capsys.readouterr().out)
        assert data == {"database_connectors": [], "agents_with_custom_endpoint": [], "avatars": []}
