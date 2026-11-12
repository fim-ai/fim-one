"""Where a database connector may connect.

Properties:

* A connector cannot dial the application's own infrastructure or an
  internal network: the decision is made on the **resolved** address, on
  every ``connect()``, and the driver dials the checked address.
* Internal targets are reachable only through the administrator's
  allow-list; the application's own database and Redis never are.
* Nothing in the config can smuggle credentials, extra hosts, a socket path
  or protocol lines into the connection.
* No driver can opt out of the check.

``FIM_TEST_PG_URL`` (a throwaway PostgreSQL on this machine) enables the
tests that open real connections.
"""

from __future__ import annotations

import os
import socket
import ssl
from typing import Any

import pytest
from sqlalchemy.engine import make_url

from fim_one.core.security import db_target
from fim_one.core.security.db_target import (
    DatabaseTargetError,
    authorize_database_target,
    check_db_config_fields,
    parse_allowlist,
)
from fim_one.core.tool.connector.database import base as driver_base
from fim_one.core.tool.connector.database.base import DatabaseDriver
from fim_one.core.tool.connector.database.drivers import DRIVER_REGISTRY

_NAMES = {
    "db.public.example": ["93.184.216.34"],
    "postgres": ["172.18.0.2"],
    "redis": ["172.18.0.3"],
    "mysql.corp.internal": ["10.20.30.40"],
    "pg.rds.internal": ["10.99.0.7"],
    "mixed.example": ["93.184.216.34", "10.0.0.9"],
    "v6.internal": ["fd00::5"],
    "meta.example": ["169.254.169.254"],
    "loop.example": ["127.0.0.1"],
}


@pytest.fixture(autouse=True)
def _dns(monkeypatch: pytest.MonkeyPatch) -> None:
    real = socket.getaddrinfo

    def _getaddrinfo(host: Any, port: Any, *a: Any, **kw: Any) -> Any:
        key = str(host).lower().rstrip(".")
        if key in _NAMES:
            return [
                (socket.AF_INET6 if ":" in ip else socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port or 0))
                for ip in _NAMES[key]
            ]
        if key.endswith((".example", ".internal")) or "." not in key and not key.isdigit() and key != "localhost":
            raise socket.gaierror("unknown test host")
        return real(host, port, *a, **kw)

    monkeypatch.setattr(socket, "getaddrinfo", _getaddrinfo)
    for env in ("DB_CONNECTOR_ALLOWED_HOSTS", "DATABASE_URL", "REDIS_URL"):
        monkeypatch.delenv(env, raising=False)


def _cfg(host: Any, port: Any = 5432, **kw: Any) -> dict[str, Any]:
    return {"driver": "postgresql", "host": host, "port": port, "database": "app",
            "username": "u", "password": "p", **kw}


_INTERNAL_HOSTS = [
    "127.0.0.1", "localhost", "LOCALHOST", "localhost.", "::1", "[::1]", "0.0.0.0", "127.1", "127.0.1",
    "2130706433", "0x7f000001", "::ffff:127.0.0.1", "::ffff:10.0.0.1",
    "10.0.0.5", "172.16.0.1", "172.31.255.254", "192.168.1.10", "169.254.169.254",
    "100.100.100.200", "100.64.0.1", "fe80::1", "fd00::1", "fc00::1",
    "postgres", "redis", "mysql.corp.internal", "mixed.example", "v6.internal",
    "meta.example", "loop.example", "Postgres", "postgres.",
]

_MALFORMED_HOSTS = [
    "user:pw@db.public.example", "pw@db.public.example", "db.public.example:5432",
    "db.public.example/app", "db.public.example?sslmode=disable", "db.public.example#x",
    "db.public.example,postgres", "postgres,db.public.example", "/var/run/postgresql",
    "/tmp", "./sock", "db.public.example\r\nX", "db.public.example\n", "db public", " ", "",
    "db.public.example\x00", "postgresql://db.public.example", "//db.public.example",
    "[::1]:5432", "-db.example", "db..example", "*", "%", "a" * 300, None, 5, ["postgres"],
]


class TestDefaultDeny:
    @pytest.mark.parametrize("host", _INTERNAL_HOSTS)
    def test_internal_targets_are_refused(self, host: str) -> None:
        with pytest.raises(DatabaseTargetError) as excinfo:
            authorize_database_target(_cfg(host))
        # The refusal does not disclose what the name resolved to.
        for ips in _NAMES.values():
            for ip in ips:
                assert ip not in str(excinfo.value) or ip == host

    @pytest.mark.parametrize("host", _MALFORMED_HOSTS)
    def test_malformed_hosts_are_refused_without_resolving(
        self, host: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _no_dns(*a: Any, **kw: Any) -> Any:
            raise AssertionError("resolved a malformed host")

        monkeypatch.setattr(socket, "getaddrinfo", _no_dns)
        with pytest.raises(DatabaseTargetError):
            authorize_database_target(_cfg(host))
        with pytest.raises(DatabaseTargetError):
            check_db_config_fields(_cfg(host))

    def test_unresolvable_host_is_refused(self) -> None:
        with pytest.raises(DatabaseTargetError):
            authorize_database_target(_cfg("nowhere.example"))

    def test_public_target_returns_the_checked_address(self) -> None:
        assert authorize_database_target(_cfg("db.public.example")) == "93.184.216.34"
        assert authorize_database_target(_cfg("8.8.8.8")) == "8.8.8.8"

    @pytest.mark.parametrize("port", [0, -1, 65536, "x", None, "5432;"])
    def test_bad_ports_are_refused(self, port: Any) -> None:
        with pytest.raises(DatabaseTargetError):
            authorize_database_target(_cfg("db.public.example", port))

    @pytest.mark.parametrize("field", ["username", "password", "database", "schema", "charset"])
    @pytest.mark.parametrize("payload", ["a\r\nb", "a\nb", "a\rb", "a\x00b", "a\tb", "a\x7fb", "\x1b[0m"])
    def test_control_characters_never_reach_the_wire(self, field: str, payload: str) -> None:
        with pytest.raises(DatabaseTargetError):
            authorize_database_target(_cfg("db.public.example", **{field: payload}))

    def test_request_schema_applies_the_same_shape_rules(self) -> None:
        from pydantic import ValidationError

        from fim_one.web.schemas.db_connector import DbConnectionConfig

        ok = dict(host="db.public.example", port=5432, database="d", username="u")
        DbConnectionConfig(**ok)
        for bad in (
            {"host": "u:p@db.public.example"}, {"host": "/var/run/postgresql"},
            {"host": "a,b"}, {"username": "u\r\nSET"}, {"database": "d\x00"}, {"password": "p\nq"},
        ):
            with pytest.raises(ValidationError):
                DbConnectionConfig(**{**ok, **bad})


class TestAllowList:
    def _allowed(self, monkeypatch: pytest.MonkeyPatch, allow: str, host: str, port: int = 5432) -> bool:
        monkeypatch.setenv("DB_CONNECTOR_ALLOWED_HOSTS", allow)
        try:
            authorize_database_target(_cfg(host, port))
        except DatabaseTargetError:
            return False
        return True

    @pytest.mark.parametrize(
        ("allow", "host", "port", "expected"),
        [
            ("10.20.0.0/16", "mysql.corp.internal", 3306, True),
            ("10.20.0.0/16", "10.20.30.40", 3306, True),
            ("10.20.0.0/16", "10.21.0.1", 3306, False),
            ("10.20.0.0/16", "pg.rds.internal", 5432, False),
            ("10.20.30.40", "mysql.corp.internal", 3306, True),
            ("10.20.30.40:3306", "mysql.corp.internal", 3306, True),
            ("10.20.30.40:3306", "mysql.corp.internal", 5432, False),
            ("mysql.corp.internal", "mysql.corp.internal", 3306, True),
            ("mysql.corp.internal", "MySQL.Corp.Internal.", 3306, True),
            ("mysql.corp.internal:3306", "mysql.corp.internal", 3307, False),
            ("mysql.corp.internal", "10.20.30.40", 3306, False),  # a name entry allows the name only
            ("mysql.corp.internal", "xmysql.corp.internal", 3306, False),
            ("*.rds.internal", "pg.rds.internal", 5432, True),
            ("*.rds.internal", "rds.internal", 5432, False),
            ("*.rds.internal", "pg.rds.internal.example", 5432, False),
            ("*.internal", "notinternal", 5432, False),
            ("10.0.0.0/8", "mixed.example", 5432, True),   # every internal address must be covered
            ("10.1.0.0/16", "mixed.example", 5432, False),
            ("fd00::/8", "v6.internal", 5432, True),
            ("[fd00::5]:5432", "v6.internal", 5432, True),
            ("[fd00::5]:5433", "v6.internal", 5432, False),
            ("127.0.0.1", "localhost", 5432, False),        # localhost is also ::1
            ("127.0.0.0/8,::1", "localhost", 5432, True),
            ("127.0.0.1", "::ffff:127.0.0.1", 5432, True),
            (" 10.20.0.0/16 , ,*.rds.internal ", "pg.rds.internal", 5432, True),
            ("", "10.20.30.40", 3306, False),
            ("0.0.0.0/0", "10.20.30.40", 3306, True),       # explicit, if that is what was written
        ],
    )
    def test_matching(
        self, monkeypatch: pytest.MonkeyPatch, allow: str, host: str, port: int, expected: bool
    ) -> None:
        assert self._allowed(monkeypatch, allow, host, port) is expected

    @pytest.mark.parametrize(
        "allow", ["10.0.0.0/8;192.168.0.0/16", "10.0.0.0/33", "db.internal:port", "http://db.internal",
                  "db.internal:0", "db.internal:70000", "*", "*.", "10.0.0.1/24/1", "[fd00::5", "a b"],
    )
    def test_unparseable_allowlist_fails_closed(self, monkeypatch: pytest.MonkeyPatch, allow: str) -> None:
        with pytest.raises(ValueError):
            parse_allowlist(allow)
        monkeypatch.setenv("DB_CONNECTOR_ALLOWED_HOSTS", allow + ",10.20.0.0/16")
        with pytest.raises(DatabaseTargetError):
            authorize_database_target(_cfg("10.20.30.40", 3306))

    def test_an_unparseable_entry_refuses_public_targets_too_and_is_only_logged(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        monkeypatch.setenv("DB_CONNECTOR_ALLOWED_HOSTS", "10.0.0.0/8;secret-typo.corp")
        with caplog.at_level("ERROR", logger="fim_one.core.security.db_target"):
            with pytest.raises(DatabaseTargetError) as refused:
                authorize_database_target(_cfg("db.public.example"))
        assert "secret-typo" not in str(refused.value)
        assert "DB_CONNECTOR_ALLOWED_HOSTS" in str(refused.value)
        assert "secret-typo" in caplog.text


class TestOwnInfrastructureIsNeverATarget:
    @pytest.mark.parametrize(
        ("env", "url", "host", "port"),
        [
            ("DATABASE_URL", "postgresql+asyncpg://fim:fim@postgres:5432/fim", "postgres", 5432),
            ("DATABASE_URL", "postgresql+asyncpg://fim:fim@postgres/fim", "172.18.0.2", 5432),
            ("DATABASE_URL", "postgresql://fim:fim@172.18.0.2:5432/fim", "postgres", 5432),
            ("DATABASE_URL", "postgresql://fim:fim@localhost:5432/fim", "127.0.0.1", 5432),
            ("DATABASE_URL", "postgresql://fim:fim@127.0.0.1:5432/fim", "::1", 5432),
            ("DATABASE_URL", "mysql+aiomysql://a:b@mysql.corp.internal/x", "10.20.30.40", 3306),
            ("REDIS_URL", "redis://redis:6379/0", "redis", 6379),
            ("REDIS_URL", "redis://redis", "172.18.0.3", 6379),
            ("DATABASE_URL", "postgresql://u:p@db.public.example:5432/app", "db.public.example", 5432),
        ],
    )
    def test_refused_even_when_allow_listed(
        self, monkeypatch: pytest.MonkeyPatch, env: str, url: str, host: str, port: int
    ) -> None:
        monkeypatch.setenv(env, url)
        monkeypatch.setenv(
            "DB_CONNECTOR_ALLOWED_HOSTS", "0.0.0.0/0,::/0,postgres,redis,localhost,mysql.corp.internal"
        )
        with pytest.raises(DatabaseTargetError):
            authorize_database_target(_cfg(host, port))

    def test_every_port_of_the_same_host_is_refused(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DATABASE_URL", "postgresql://fim:fim@postgres:5432/fim")
        monkeypatch.setenv("DB_CONNECTOR_ALLOWED_HOSTS", "172.18.0.0/16")
        for port in (5433, 3306, 6379):
            with pytest.raises(DatabaseTargetError, match="cannot be used by a database connector"):
                authorize_database_target(_cfg("postgres", port))
            with pytest.raises(DatabaseTargetError, match="cannot be used by a database connector"):
                authorize_database_target(_cfg("172.18.0.2", port))
        # Another host on the same network still follows the allow-list.
        assert authorize_database_target(_cfg("redis", 5432)) == "172.18.0.3"

    @pytest.mark.parametrize(
        "url",
        [
            "postgresql+asyncpg://fim:fim@/fim?host=postgres",
            "postgresql://fim:fim@/fim?host=172.18.0.2&port=5432",
            "postgresql://fim:fim@db.public.example:5432,postgres:5432/fim",
            "postgresql://fim:fim@[::ffff:172.18.0.2]:5432/fim",
        ],
    )
    def test_query_and_multi_host_forms(self, monkeypatch: pytest.MonkeyPatch, url: str) -> None:
        monkeypatch.setenv("DATABASE_URL", url)
        monkeypatch.setenv("DB_CONNECTOR_ALLOWED_HOSTS", "172.18.0.0/16")
        with pytest.raises(DatabaseTargetError, match="cannot be used by a database connector"):
            authorize_database_target(_cfg("postgres", 6543))

    def test_a_socket_path_is_not_a_network_host(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://fim:fim@/fim?host=/var/run/postgresql")
        assert authorize_database_target(_cfg("db.public.example")) == "93.184.216.34"

    def test_an_own_host_that_cannot_be_resolved_refuses_every_target(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        monkeypatch.setenv("DATABASE_URL", "postgresql://fim:fim@gone.internal:5432/fim")
        with caplog.at_level("ERROR", logger="fim_one.core.security.db_target"):
            with pytest.raises(DatabaseTargetError) as refused:
                authorize_database_target(_cfg("db.public.example"))
        assert "gone.internal" not in str(refused.value)
        assert "gone.internal" in caplog.text

    def test_sqlite_database_url_is_ignored(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///./data/fim_one.db")
        assert authorize_database_target(_cfg("db.public.example")) == "93.184.216.34"


class _Tripwire(Exception):
    pass


@pytest.fixture()
def no_database_libraries(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Record (and stop) every attempt to open a database connection."""
    dialed: list[dict[str, Any]] = []

    async def _create_pool(*a: Any, **kw: Any) -> Any:
        dialed.append(kw)
        raise _Tripwire()

    import aiomysql
    import asyncpg

    from fim_one.core.tool.connector.database.drivers import dm8

    monkeypatch.setattr(asyncpg, "create_pool", _create_pool)
    monkeypatch.setattr(asyncpg, "connect", _create_pool)
    monkeypatch.setattr(aiomysql, "create_pool", _create_pool)
    monkeypatch.setattr(aiomysql, "connect", _create_pool)

    class _Dm:
        @staticmethod
        def connect(**kw: Any) -> Any:
            dialed.append({"host": kw.get("server"), **kw})
            raise _Tripwire()

    monkeypatch.setattr(dm8, "_load_dmpython", lambda: _Dm)
    return dialed


class TestEveryDriverIsGuarded:
    def test_registry_is_not_empty(self) -> None:
        assert {"postgresql", "mysql"} <= set(DRIVER_REGISTRY)

    @pytest.mark.parametrize("driver", sorted(DRIVER_REGISTRY))
    @pytest.mark.parametrize("host", ["127.0.0.1", "postgres", "mixed.example", "169.254.169.254", "u:p@db.public.example"])
    async def test_internal_host_is_refused_before_dialing(
        self, driver: str, host: str, no_database_libraries: list[dict[str, Any]]
    ) -> None:
        instance = DRIVER_REGISTRY[driver](_cfg(host, driver=driver))
        with pytest.raises(DatabaseTargetError):
            await instance.connect()
        ok, message = await instance.test_connection()
        assert ok is False
        assert no_database_libraries == []

    @pytest.mark.parametrize("driver", sorted(DRIVER_REGISTRY))
    async def test_driver_dials_the_checked_address_not_the_name(
        self, driver: str, no_database_libraries: list[dict[str, Any]]
    ) -> None:
        caller_config = _cfg("db.public.example", driver=driver)
        instance = DRIVER_REGISTRY[driver](caller_config)
        with pytest.raises(_Tripwire):
            await instance.connect()
        assert [d["host"] for d in no_database_libraries] == ["93.184.216.34"]
        assert caller_config["host"] == "db.public.example"  # caller's dict untouched

    @pytest.mark.parametrize("driver", sorted(DRIVER_REGISTRY))
    async def test_host_is_checked_again_on_every_connect(
        self, driver: str, no_database_libraries: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        instance = DRIVER_REGISTRY[driver](_cfg("db.public.example", driver=driver))
        with pytest.raises(_Tripwire):
            await instance.connect()
        # The name now answers with an internal address (DNS rebinding).
        monkeypatch.setitem(_NAMES, "db.public.example", ["10.0.0.9"])
        with pytest.raises(DatabaseTargetError):
            await instance.connect()
        assert len(no_database_libraries) == 1

    async def test_a_new_driver_cannot_opt_out(self) -> None:
        dialed: list[str] = []

        class _NewDriver(DatabaseDriver):
            async def connect(self) -> None:
                dialed.append(str(self._config["host"]))

            async def disconnect(self) -> None: ...
            async def test_connection(self) -> tuple[bool, str]:
                return True, ""

            async def list_tables(self, schema: str | None = None) -> list[Any]:
                return []

            async def describe_table(self, table_name: str, schema: str | None = None) -> list[Any]:
                return []

            async def execute_query(self, sql: str, **kw: Any) -> Any:
                return None

        class _Derived(_NewDriver):
            async def connect(self) -> None:
                await super().connect()

        for cls in (_NewDriver, _Derived):
            with pytest.raises(DatabaseTargetError):
                await cls(_cfg("postgres")).connect()
            await cls(_cfg("db.public.example")).connect()
        assert dialed == ["93.184.216.34", "93.184.216.34"]

    def test_tls_keeps_the_configured_server_name(self) -> None:
        context = driver_base.tls_context_for("db.public.example")
        sslobj = context.wrap_bio(ssl.MemoryBIO(), ssl.MemoryBIO(), False, "93.184.216.34")
        assert sslobj.server_hostname == "db.public.example"
        sslobj = context.wrap_bio(ssl.MemoryBIO(), ssl.MemoryBIO(), server_hostname="93.184.216.34")
        assert sslobj.server_hostname == "db.public.example"


class TestApiRefusal:
    async def test_saving_an_internal_target_is_a_400(self) -> None:
        from fim_one.web.api.connectors import _validate_db_target
        from fim_one.web.exceptions import AppError

        for config in (_cfg("postgres"), _cfg("127.0.0.1"), _cfg("u:p@db.public.example"),
                       _cfg("db.public.example", username="u\r\nx")):
            with pytest.raises(AppError) as excinfo:
                await _validate_db_target(config)
            assert excinfo.value.status_code == 400
        await _validate_db_target(_cfg("db.public.example"))
        # Shape only, when the stored target is unchanged.
        await _validate_db_target(_cfg("postgres"), check_network=False)


_LIVE = os.environ.get("FIM_TEST_PG_URL")


@pytest.mark.skipif(not _LIVE, reason="FIM_TEST_PG_URL not set")
class TestAgainstARealPostgres:
    """The same rules with a real server on this machine's loopback."""

    def _config(self, host: str | None = None) -> dict[str, Any]:
        url = make_url(_LIVE or "")
        return {"driver": "postgresql", "host": host or url.host, "port": url.port,
                "database": url.database, "username": url.username, "password": url.password}

    async def _version(self, config: dict[str, Any]) -> tuple[bool, str]:
        driver = DRIVER_REGISTRY["postgresql"](config)
        try:
            return await driver.test_connection()
        finally:
            await driver.disconnect()

    @pytest.mark.parametrize("host", [None, "localhost", "127.1", "2130706433", "::ffff:127.0.0.1"])
    async def test_refused_by_default(self, host: str | None) -> None:
        ok, message = await self._version(self._config(host))
        assert ok is False
        assert "PostgreSQL" not in message
        assert "cannot be used by a database connector" in message

    async def test_reachable_once_allow_listed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        config = self._config()
        monkeypatch.setenv("DB_CONNECTOR_ALLOWED_HOSTS", f"127.0.0.1:{config['port']}")
        ok, message = await self._version(config)
        assert ok is True and "PostgreSQL" in message

        monkeypatch.setenv("DB_CONNECTOR_ALLOWED_HOSTS", f"127.0.0.1:{int(config['port']) + 1}")
        ok, message = await self._version(config)
        assert ok is False

    async def test_the_applications_own_database_is_refused_even_when_allow_listed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        config = self._config()
        monkeypatch.setenv("DB_CONNECTOR_ALLOWED_HOSTS", "127.0.0.0/8,::1,localhost")
        monkeypatch.setenv("DATABASE_URL", (_LIVE or "").replace("postgresql://", "postgresql+asyncpg://"))
        for host in (None, "localhost"):
            ok, message = await self._version(self._config(host))
            assert ok is False
            assert "cannot be used by a database connector" in message
