"""Raw-SQL DB tools are owner-only.

A raw-SQL database tool runs against the connector owner's (typically
high-privilege) DB account with no per-caller scoping — it exposes arbitrary
SELECT over the whole schema. So unlike an API connector (where ``allow_fallback``
lets a subscriber borrow the owner's token and the upstream service still
enforces its own RBAC), a DB connector must NOT expose the raw-SQL surface to a
non-owner even when ``allow_fallback`` is on.

These tests pin ``db_raw_sql_tool_allowed`` — the single predicate both tool
assembly sites in ``fim_one.web.api.chat._resolve_tools`` consult.
"""

from __future__ import annotations

from types import SimpleNamespace

from fim_one.core.security.connector_credentials import db_raw_sql_tool_allowed


def _conn(owner: str, *, allow_fallback: bool = False) -> SimpleNamespace:
    return SimpleNamespace(id="c1", user_id=owner, allow_fallback=allow_fallback)


class TestDbRawSqlToolAllowed:
    def test_owner_allowed(self) -> None:
        assert db_raw_sql_tool_allowed(_conn("alice"), "alice") is True

    def test_non_owner_denied(self) -> None:
        assert db_raw_sql_tool_allowed(_conn("alice"), "bob") is False

    def test_non_owner_denied_even_with_fallback(self) -> None:
        # The crux: allow_fallback does NOT open the raw-SQL surface for a
        # non-owner on a DB connector.
        assert (
            db_raw_sql_tool_allowed(_conn("alice", allow_fallback=True), "bob")
            is False
        )

    def test_owner_allowed_regardless_of_fallback(self) -> None:
        assert (
            db_raw_sql_tool_allowed(_conn("alice", allow_fallback=False), "alice")
            is True
        )

    def test_anonymous_caller_denied(self) -> None:
        assert db_raw_sql_tool_allowed(_conn("alice"), None) is False

    def test_empty_caller_denied(self) -> None:
        assert db_raw_sql_tool_allowed(_conn("alice"), "") is False

    def test_connector_without_owner_denied(self) -> None:
        assert db_raw_sql_tool_allowed(SimpleNamespace(id="c1"), "alice") is False
