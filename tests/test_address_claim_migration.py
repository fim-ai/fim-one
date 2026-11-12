"""Migration ``c2p5a1b2c3d4``: the columns that make the address-claim step reversible.

On SQLite and (with ``FIM_TEST_PG_URL`` pointing at an empty throwaway
database) PostgreSQL: up, again, down and up, and a single head.
"""

from __future__ import annotations

import importlib
import os
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import make_url, text

import fim_one

pytestmark = pytest.mark.timeout(300)

_PG = os.environ.get("FIM_TEST_PG_URL")
_REVISION = "c2p5a1b2c3d4"
_BEFORE = "e4b1a2c3d4e5"
_MIGRATION = "fim_one.migrations.versions.c2p5a1b2c3d4_address_claim_notice"


def _params() -> list[Any]:
    return [
        pytest.param("sqlite", id="sqlite"),
        pytest.param(
            "postgresql",
            id="postgresql",
            marks=pytest.mark.skipif(not _PG, reason="FIM_TEST_PG_URL not set"),
        ),
    ]


@pytest.fixture(params=_params())
def database_url(
    request: pytest.FixtureRequest, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[str]:
    if request.param == "sqlite":
        url = f"sqlite:///{tmp_path / 'fim.db'}"
        monkeypatch.setenv("DATABASE_URL", url)
        yield url
        return
    assert _PG is not None
    base = make_url(_PG).set(drivername="postgresql+psycopg2")
    schema = f"c2p5_{uuid.uuid4().hex[:12]}"
    admin = sa.create_engine(base)
    with admin.begin() as conn:
        conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    rendered = base.update_query_dict({"options": f"-csearch_path={schema}"}).render_as_string(
        hide_password=False
    )
    monkeypatch.setenv("DATABASE_URL", rendered)
    try:
        yield rendered
    finally:
        with admin.begin() as conn:
            conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


def _alembic(direction: str, revision: str) -> None:
    config = Config()
    config.set_main_option("script_location", str(Path(fim_one.__file__).parent / "migrations"))
    getattr(command, direction)(config, revision)


def _columns(url: str) -> set[tuple[str, str]]:
    engine = sa.create_engine(url)
    try:
        inspector = sa.inspect(engine)
        return {
            (table, column["name"])
            for table in ("workflows", "users")
            for column in inspector.get_columns(table)
            if column["name"] in ("access_suspended_at", "security_notice")
        }
    finally:
        engine.dispose()


_ADDED = {("workflows", "access_suspended_at"), ("users", "security_notice")}


def test_up_again_down_and_up(database_url: str) -> None:
    _alembic("upgrade", _BEFORE)
    engine = sa.create_engine(database_url)
    with engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO users (id, email, username, password_hash, is_admin, is_active, "
            "onboarding_completed, email_verified, totp_enabled, preferred_language, "
            "tokens_used_this_period, created_at) VALUES ('u-1', 'a@x.io', 'a', 'x', FALSE, TRUE, "
            "FALSE, FALSE, FALSE, 'auto', 0, CURRENT_TIMESTAMP)"
        ))
    engine.dispose()
    assert _columns(database_url) == set()

    _alembic("upgrade", _REVISION)
    assert _columns(database_url) == _ADDED
    # Again, as on a database built with ``create_all``: nothing more to do.
    engine = sa.create_engine(database_url)
    module = importlib.import_module(_MIGRATION)
    with engine.connect() as conn:
        context = MigrationContext.configure(conn)
        with context.begin_transaction(), Operations.context(context):
            module.upgrade()
        conn.commit()
    with engine.connect() as conn:
        assert conn.execute(text("SELECT security_notice FROM users")).scalar_one() is None
    engine.dispose()

    _alembic("downgrade", _BEFORE)
    assert _columns(database_url) == set()
    _alembic("upgrade", "head")
    assert _columns(database_url) == _ADDED


def test_it_is_the_single_head() -> None:
    from alembic.script import ScriptDirectory

    script = ScriptDirectory(str(Path(fim_one.__file__).parent / "migrations"))
    assert script.get_heads() == [_REVISION]
    revision = script.get_revision(_REVISION)
    assert revision is not None and revision.down_revision == _BEFORE
