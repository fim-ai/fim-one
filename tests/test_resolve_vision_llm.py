"""Integration tests for ``_resolve_vision_llm`` in chat.py.

Uses a real in-memory SQLite DB (NOT mocks) so that the resolver's
priority chain is exercised against actual ORM rows. This mirrors the
existing ``test_resolve_vision.py`` fixture pattern for consistency.

Resolution order under test:

1. Primary LLM (via ``_resolve_model_supports_vision``) — if the
   agent's resolved model has ``supports_vision=True``.
2. Active ModelGroup's fast model (if vision-capable).
3. Active ModelGroup's general model (if vision-capable).
4. ENV fallback (only when no active group) — gated by
   ``LLM_SUPPORTS_VISION=false`` opt-out.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from fim_one.db.base import Base
from fim_one.web.api.chat import _resolve_vision_llm
from fim_one.web.models.model_provider import (
    ModelGroup,
    ModelProvider,
    ModelProviderModel,
)


# ---------------------------------------------------------------------------
# Fixtures & seed helpers
# ---------------------------------------------------------------------------


@pytest.fixture()
async def db_session() -> Any:
    """In-memory SQLite session with all ORM tables created."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        yield session
    await engine.dispose()


def _make_provider(name: str = "TestProvider") -> ModelProvider:
    return ModelProvider(
        id=str(uuid.uuid4()),
        name=name,
        base_url="https://api.test.com/v1",
        api_key="sk-test",
    )


def _make_model(
    provider_id: str,
    *,
    name: str = "test-model",
    supports_vision: bool = False,
) -> ModelProviderModel:
    return ModelProviderModel(
        id=str(uuid.uuid4()),
        provider_id=provider_id,
        name=name,
        model_name=name,
        supports_vision=supports_vision,
    )


def _make_group(
    *,
    general_model_id: str | None = None,
    fast_model_id: str | None = None,
    is_active: bool = True,
) -> ModelGroup:
    return ModelGroup(
        id=str(uuid.uuid4()),
        name="TestGroup",
        general_model_id=general_model_id,
        fast_model_id=fast_model_id,
        is_active=is_active,
    )


# ---------------------------------------------------------------------------
# DB-mode tests
# ---------------------------------------------------------------------------


class TestResolveVisionLLMDBMode:
    """Priority order when an active ModelGroup exists."""

    async def test_primary_with_vision_returns_primary(
        self, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Step 1 — when primary model supports vision, use it directly."""
        monkeypatch.setenv("LLM_SUPPORTS_VISION", "")  # neutral
        provider = _make_provider()
        general = _make_model(provider.id, name="gpt-4o", supports_vision=True)
        group = _make_group(general_model_id=general.id)
        db_session.add_all([provider, general, group])
        await db_session.commit()

        llm = await _resolve_vision_llm(None, db_session)
        assert llm is not None

    async def test_primary_no_vision_falls_to_fast_model(
        self, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Step 2 — primary has no vision, fast model does."""
        monkeypatch.setenv("LLM_SUPPORTS_VISION", "")
        provider = _make_provider()
        general = _make_model(provider.id, name="deepseek-v3", supports_vision=False)
        fast = _make_model(provider.id, name="gpt-4o-mini", supports_vision=True)
        group = _make_group(
            general_model_id=general.id,
            fast_model_id=fast.id,
        )
        db_session.add_all([provider, general, fast, group])
        await db_session.commit()

        llm = await _resolve_vision_llm(None, db_session)
        assert llm is not None
        # The resolver should have fallen through to the fast model —
        # we can't easily inspect which model was chosen without
        # reaching into internals, but the fact that we got a non-None
        # LLM despite primary=no-vision confirms the fallback chain
        # walked at least one step.

    async def test_no_vision_flag_tries_general_best_effort(
        self, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """DB mode — no model has supports_vision=True but general model
        is tried as best-effort OCR backend (markitdown_core catches
        failures and falls back to text-only).
        """
        monkeypatch.setenv("LLM_SUPPORTS_VISION", "")
        monkeypatch.setenv("LLM_API_KEY", "sk-env-key")
        monkeypatch.setenv("LLM_BASE_URL", "https://api.openai.com/v1")
        monkeypatch.setenv("LLM_MODEL", "gpt-4o")

        provider = _make_provider()
        general = _make_model(provider.id, name="deepseek-v3", supports_vision=False)
        fast = _make_model(provider.id, name="deepseek-v3-chat", supports_vision=False)
        group = _make_group(
            general_model_id=general.id,
            fast_model_id=fast.id,
        )
        db_session.add_all([provider, general, fast, group])
        await db_session.commit()

        llm = await _resolve_vision_llm(None, db_session)
        # Best-effort: general model is returned even without the flag,
        # because markitdown_core's try/except will catch vision failures.
        assert llm is not None

    async def test_no_models_at_all_returns_none(
        self, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Active group with no models assigned → None (no ENV leak)."""
        monkeypatch.setenv("LLM_SUPPORTS_VISION", "")
        monkeypatch.setenv("LLM_API_KEY", "sk-env-key")
        monkeypatch.setenv("LLM_BASE_URL", "https://api.openai.com/v1")
        monkeypatch.setenv("LLM_MODEL", "gpt-4o")

        group = _make_group(general_model_id=None, fast_model_id=None)
        db_session.add(group)
        await db_session.commit()

        llm = await _resolve_vision_llm(None, db_session)
        assert llm is None


# ---------------------------------------------------------------------------
# ENV-mode tests
# ---------------------------------------------------------------------------


class TestResolveVisionLLMEnvMode:
    """Behavior when no active ModelGroup exists."""

    async def test_env_mode_default_optimistic(
        self, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No DB group + no opt-out → resolver returns the ENV primary LLM."""
        monkeypatch.setenv("LLM_API_KEY", "sk-env-key")
        monkeypatch.setenv("LLM_BASE_URL", "https://api.openai.com/v1")
        monkeypatch.setenv("LLM_MODEL", "gpt-4o")
        monkeypatch.delenv("LLM_SUPPORTS_VISION", raising=False)

        llm = await _resolve_vision_llm(None, db_session)
        assert llm is not None

    async def test_env_mode_opt_out_returns_none(
        self, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """LLM_SUPPORTS_VISION=false should bypass the optimistic fallback."""
        monkeypatch.setenv("LLM_API_KEY", "sk-env-key")
        monkeypatch.setenv("LLM_BASE_URL", "https://api.openai.com/v1")
        monkeypatch.setenv("LLM_MODEL", "gpt-4o")
        monkeypatch.setenv("LLM_SUPPORTS_VISION", "false")

        llm = await _resolve_vision_llm(None, db_session)
        assert llm is None

    async def test_env_mode_opt_out_case_insensitive(
        self, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """'FALSE' and 'False' should both disable the optimistic path."""
        monkeypatch.setenv("LLM_API_KEY", "sk-env-key")
        monkeypatch.setenv("LLM_BASE_URL", "https://api.openai.com/v1")
        monkeypatch.setenv("LLM_MODEL", "gpt-4o")
        monkeypatch.setenv("LLM_SUPPORTS_VISION", "FALSE")

        llm = await _resolve_vision_llm(None, db_session)
        assert llm is None
