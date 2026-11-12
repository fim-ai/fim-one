"""Shared fixtures for the database driver tests."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _driver_tests_dial_what_they_configure(monkeypatch: pytest.MonkeyPatch) -> None:
    """These tests exercise drivers against stand-in libraries; where a
    connector may connect is tested in
    ``tests/test_db_connector_target_policy.py``."""
    from fim_one.core.tool.connector.database import base

    monkeypatch.setattr(
        base, "authorize_database_target", lambda config: config.get("host", "localhost")
    )
