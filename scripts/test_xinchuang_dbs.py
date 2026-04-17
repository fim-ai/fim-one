"""Ad-hoc connection probe for Xinchuang (信创) databases.

Lightweight CLI that dials a KingbaseES / HighGo / DM8 instance and
prints the server version + first five table names. Designed for the
roadshow Q&A -- if a judge asks "can FIM One really connect to a
domestic DB?" we run this against a live instance right there.

Usage
-----

::

    uv run python scripts/test_xinchuang_dbs.py \\
        --type kingbase --host 10.0.0.10 --port 54321 \\
        --user system --password system --database test

    uv run python scripts/test_xinchuang_dbs.py \\
        --type dm8 --host 10.0.0.11 --port 5236 \\
        --user SYSDBA --password SYSDBA

    uv run python scripts/test_xinchuang_dbs.py \\
        --type highgo --host 10.0.0.12 --port 5866 \\
        --user highgo --password highgo --database test

Notes
-----

* Never hardcode credentials -- always pass them on the CLI or via
  the ``FIM_DB_*`` environment variables listed below.
* For DM8 you must have downloaded the ``dmPython`` wheel from
  https://eco.dameng.com/ and installed it first::

      uv pip install path/to/dmPython-<ver>-cp<py>-*.whl
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from typing import Any

# The user-facing ``--type`` argument to the registry key used by the
# pool factory.  ``kingbase`` is exposed as a friendly alias so operators
# don't have to remember the awkward ``kingbasees`` spelling.
_TYPE_ALIAS: dict[str, str] = {
    "kingbase": "kingbasees",
    "kingbasees": "kingbasees",
    "highgo": "highgo",
    "dm8": "dm8",
    "pg": "postgresql",
    "postgresql": "postgresql",
    "mysql": "mysql",
}


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Probe a Xinchuang database and list its first five tables.",
    )
    parser.add_argument(
        "--type",
        required=True,
        choices=sorted(_TYPE_ALIAS.keys()),
        help="Database flavour. 'kingbase' and 'kingbasees' are equivalent.",
    )
    parser.add_argument("--host", default=os.getenv("FIM_DB_HOST", "localhost"))
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--user", default=os.getenv("FIM_DB_USER", ""))
    parser.add_argument(
        "--password", default=os.getenv("FIM_DB_PASSWORD", ""), help="(or FIM_DB_PASSWORD env)"
    )
    parser.add_argument("--database", default=os.getenv("FIM_DB_NAME", ""))
    parser.add_argument(
        "--schema",
        default=os.getenv("FIM_DB_SCHEMA"),
        help="Schema / owner (defaults to 'public' for PG-family, uppercased user for DM8).",
    )
    return parser.parse_args(argv)


def _default_port(driver_key: str) -> int:
    return {
        "postgresql": 5432,
        "mysql": 3306,
        "kingbasees": 54321,
        "highgo": 5866,
        "dm8": 5236,
    }.get(driver_key, 5432)


async def _probe(args: argparse.Namespace) -> int:
    driver_key = _TYPE_ALIAS[args.type]
    port = args.port or _default_port(driver_key)

    config: dict[str, Any] = {
        "driver": driver_key,
        "host": args.host,
        "port": port,
        "username": args.user,
        "password": args.password,
        "database": args.database,
    }
    if args.schema:
        config["schema"] = args.schema

    # Imported here so the script still loads when FIM One is
    # installed without the ``database`` extras.
    from fim_one.core.tool.connector.database.pool import ConnectionPoolManager

    print(f"[*] Connecting to {driver_key}://{args.host}:{port}/{args.database or '(default)'}")

    try:
        driver = ConnectionPoolManager._create_driver(config)
        await driver.connect()
    except Exception as exc:
        print(f"[!] Connection failed: {exc}", file=sys.stderr)
        return 1

    try:
        success, version = await driver.test_connection()
        if not success:
            print(f"[!] Server version probe failed: {version}", file=sys.stderr)
            return 2
        print(f"[+] Server version: {version}")

        tables = await driver.list_tables()
        print(f"[+] Tables visible: {len(tables)}")
        for t in tables[:5]:
            print(f"    - {t.table_name} ({t.column_count} cols)")
        if len(tables) > 5:
            print(f"    ... and {len(tables) - 5} more")
        return 0
    finally:
        await driver.disconnect()


def main() -> None:
    args = _parse_args(sys.argv[1:])
    exit_code = asyncio.run(_probe(args))
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
