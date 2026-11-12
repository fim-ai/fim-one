"""Read-only report of stored rows that the current access rules refuse.

Run it with the new release against the database before (or after)
upgrading::

    python -m fim_one.web.services.upgrade_check          # text
    python -m fim_one.web.services.upgrade_check --json   # machine-readable

It uses ``DATABASE_URL`` and the same environment as the application
(``DB_CONNECTOR_ALLOWED_HOSTS`` in particular), issues only ``SELECT``
statements on columns that exist in every schema version that has these
tables, resolves host names, and changes nothing.  It lists:

* **database connectors** whose host the application would refuse (an
  internal address not in ``DB_CONNECTOR_ALLOWED_HOSTS``, the
  application's own database or Redis, an unresolvable or malformed host);
* **agents with a custom model endpoint**: inline model settings with a
  ``base_url``, whether the agent is shared, and why the endpoint is
  refused, if it is (no key of its own, an internal address);
* **profile pictures** whose stored value is neither a built-in avatar nor
  an image uploaded for that account; they show no picture.

No credential, model key or stored avatar value is printed.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from dataclasses import asdict, dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


@dataclass
class Report:
    database_connectors: list[dict[str, Any]] = field(default_factory=list)
    agents_with_custom_endpoint: list[dict[str, Any]] = field(default_factory=list)
    avatars: list[dict[str, Any]] = field(default_factory=list)

    def is_empty(self) -> bool:
        return not (self.database_connectors or self.agents_with_custom_endpoint or self.avatars)


async def _database_connectors(db: AsyncSession) -> list[dict[str, Any]]:
    from fim_one.core.security.db_target import DatabaseTargetError, authorize_database_target
    from fim_one.db.models.connector import Connector

    rows = (
        await db.execute(
            select(Connector.id, Connector.name, Connector.user_id, Connector.db_config).where(
                Connector.type == "database"
            )
        )
    ).all()
    found: list[dict[str, Any]] = []
    for cid, name, user_id, config in rows:
        cfg = config if isinstance(config, dict) else {}
        target = {key: cfg.get(key) for key in ("host", "port")}
        try:
            await asyncio.to_thread(authorize_database_target, {**target, "driver": cfg.get("driver")})
        except DatabaseTargetError as exc:
            found.append(
                {"id": cid, "name": name, "user_id": user_id, **target, "reason": exc.reason}
            )
    return found


async def _agents_with_custom_endpoint(db: AsyncSession) -> list[dict[str, Any]]:
    from fim_one.db.models import Agent
    rows = (
        await db.execute(
            select(
                Agent.id,
                Agent.name,
                Agent.user_id,
                Agent.visibility,
                Agent.org_id,
                Agent.model_config_json,
            ).where(Agent.model_config_json.is_not(None))
        )
    ).all()
    found: list[dict[str, Any]] = []
    for aid, name, user_id, visibility, org_id, config in rows:
        if not has_custom_model_endpoint(config):
            continue
        shared = (visibility or "personal") != "personal" or org_id is not None
        found.append(
            {
                "id": aid,
                "name": name,
                "user_id": user_id,
                "shared": shared,
                "endpoint_refused": await asyncio.to_thread(_endpoint_refusal, config),
            }
        )
    return found


def has_custom_model_endpoint(model_config_json: Any) -> bool:
    """Whether an agent's model settings name an endpoint of their own."""
    if not isinstance(model_config_json, dict):
        return False
    base_url = model_config_json.get("base_url")
    return isinstance(base_url, str) and bool(base_url.strip())


def _endpoint_refusal(config: Any) -> str | None:
    from fim_one.web.deps import InlineModelConfigError, check_inline_model_config

    try:
        check_inline_model_config(config, resolve=True)
    except InlineModelConfigError as exc:
        return str(exc)
    return None


async def _avatars(db: AsyncSession) -> list[dict[str, Any]]:
    from fim_one.db.models import User
    from fim_one.web.avatars import public_avatar_value

    rows = (
        await db.execute(
            select(User.id, User.username, User.avatar).where(
                User.avatar.is_not(None), User.avatar != ""
            )
        )
    ).all()
    return [
        {"id": uid, "username": username}
        for uid, username, avatar in rows
        if public_avatar_value(str(uid), avatar) is None
    ]


async def collect(db: AsyncSession) -> Report:
    """Everything listed in the module docstring, read from *db*."""
    report = Report(
        database_connectors=await _database_connectors(db),
        agents_with_custom_endpoint=await _agents_with_custom_endpoint(db),
        avatars=await _avatars(db),
    )
    await db.rollback()
    return report


def _print_text(report: Report) -> None:
    if report.is_empty():
        print("No stored rows are affected.")
        return
    if report.database_connectors:
        print(f"Database connectors refused ({len(report.database_connectors)}):")
        for row in report.database_connectors:
            print(f"  {row['id']}  {row['name']!r}  host={row['host']!r} port={row['port']!r}")
            print(f"      {row['reason']}")
    if report.agents_with_custom_endpoint:
        print(f"Agents with a custom model endpoint ({len(report.agents_with_custom_endpoint)}):")
        for row in report.agents_with_custom_endpoint:
            notes = []
            if row["shared"]:
                notes.append("shared with other users")
            if row["endpoint_refused"]:
                notes.append(f"endpoint refused: {row['endpoint_refused']}")
            print(f"  {row['id']}  {row['name']!r}  owner={row['user_id']}")
            for note in notes or ["not shared; the endpoint is accepted"]:
                print(f"      {note}")
    if report.avatars:
        print(f"Accounts whose stored profile picture is not shown ({len(report.avatars)}):")
        for row in report.avatars:
            print(f"  {row['id']}  {row['username']!r}")


async def _main(as_json: bool) -> int:
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    url = os.environ.get("DATABASE_URL", "sqlite+aiosqlite:///./data/fim_one.db")
    engine = create_async_engine(url)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as db:
            report = await collect(db)
    finally:
        await engine.dispose()
    if as_json:
        json.dump(asdict(report), sys.stdout, ensure_ascii=False, indent=2)
        print()
    else:
        _print_text(report)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--json", action="store_true", help="print JSON")
    args = parser.parse_args(argv)
    return asyncio.run(_main(args.json))


if __name__ == "__main__":
    raise SystemExit(main())
