"""The kind a group of call records is reported as (``web/services/call_log_stats.py``).

The grouping expressions themselves run against SQLite and PostgreSQL in
``tests/test_mcp_call_log.py``.
"""

from __future__ import annotations

import pytest

from fim_one.web.services.call_log_stats import kind_of


@pytest.mark.parametrize(
    ("mcp_flag", "database_flag", "kind"),
    [
        (1, 0, "mcp"),
        (1, 1, "mcp"),
        (0, 1, "database"),
        (0, 0, "connector"),
        (0, None, "connector"),  # an aggregate over no rows
    ],
)
def test_kind_of(mcp_flag: int, database_flag: int | None, kind: str) -> None:
    assert kind_of(mcp_flag, database_flag) == kind
