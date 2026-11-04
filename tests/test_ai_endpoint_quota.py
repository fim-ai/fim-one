"""Model calls outside chat pass the same quota gate as chat.

Before this, AI-assisted builders, KB and DB chat, and eval runs called the
model for users who were over quota or, under ``paid_only``, had no plan.
"""

from __future__ import annotations

import importlib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.dependencies.models import Dependant
from fastapi.routing import APIRoute

from fim_one.web.exceptions import AppError
from fim_one.web.services.quota_enforcer import get_quota_checked_user

GATED = {
    "agent_ai": ["/api/agents/ai/create", "/api/agents/{agent_id}/ai/refine"],
    "connector_ai": [
        "/api/connectors/ai/create",
        "/api/connectors/{connector_id}/ai/generate-actions",
        "/api/connectors/{connector_id}/ai/refine-action",
    ],
    "db_connectors": [
        "/api/connectors/{connector_id}/ai/db-chat",
        "/api/connectors/{connector_id}/ai/annotate",
    ],
    "knowledge_bases": ["/api/knowledge-bases/{kb_id}/ai/chat"],
    "eval_runs": ["/api/eval/runs"],
}


def _uses_gate(dependant: Dependant) -> bool:
    return any(d.call is get_quota_checked_user or _uses_gate(d) for d in dependant.dependencies)


@pytest.mark.parametrize("module", sorted(GATED))
def test_ai_routes_use_quota_gate(module: str) -> None:
    router = importlib.import_module(f"fim_one.web.api.{module}").router
    posts = {
        r.path: r
        for r in router.routes
        if isinstance(r, APIRoute) and "POST" in (r.methods or set())
    }
    for path in GATED[module]:
        assert path in posts, f"route moved: {path}"
        assert _uses_gate(posts[path].dependant), f"no quota gate: {path}"


@pytest.mark.asyncio
async def test_gate_refuses_over_quota_user() -> None:
    user = MagicMock(id="u1")
    with (
        patch(
            "fim_one.web.api.chat._check_token_quota",
            AsyncMock(side_effect=AppError("token_quota_exceeded", status_code=429)),
        ),
        pytest.raises(AppError) as exc,
    ):
        await get_quota_checked_user(user)
    assert exc.value.error_code == "token_quota_exceeded"


@pytest.mark.asyncio
async def test_gate_passes_user_within_quota() -> None:
    user = MagicMock(id="u1")
    with patch("fim_one.web.api.chat._check_token_quota", AsyncMock(return_value=None)):
        assert await get_quota_checked_user(user) is user
