"""Configuration helpers for creating model registries from declarative configs."""

from __future__ import annotations

from dataclasses import dataclass, field

from .openai_compatible import OpenAICompatibleLLM
from .registry import ModelRegistry


@dataclass
class ModelConfig:
    """Declarative configuration for a single LLM instance.

    Attributes:
        name: Unique identifier used as the registry key.
        api_key: API key for authentication with the provider.
        base_url: Base URL of the OpenAI-compatible API endpoint.
        model: Model identifier (e.g. ``"gpt-4o"``, ``"gpt-4o-mini"``).
        temperature: Default sampling temperature.
        roles: Roles this model should be registered under (e.g.
            ``["general"]``, ``["fast", "compact"]``).
    """

    name: str
    api_key: str
    base_url: str = "https://api.openai.com/v1"
    model: str = "gpt-4o"
    temperature: float = 0.7
    max_tokens: int = 64000
    roles: list[str] = field(default_factory=list)


def create_registry_from_configs(configs: list[ModelConfig]) -> ModelRegistry:
    """Create a ``ModelRegistry`` from a list of ``ModelConfig`` objects.

    Each config is instantiated as an ``OpenAICompatibleLLM`` and registered
    under its ``name`` with the specified ``roles``.

    Args:
        configs: A list of model configurations.

    Returns:
        A fully-populated ``ModelRegistry``.

    Raises:
        ValueError: If any two configs share the same ``name``.
    """
    registry = ModelRegistry()

    for cfg in configs:
        llm = OpenAICompatibleLLM(
            api_key=cfg.api_key,
            base_url=cfg.base_url,
            model=cfg.model,
            default_temperature=cfg.temperature,
            default_max_tokens=cfg.max_tokens,
        )
        registry.register(cfg.name, llm, roles=cfg.roles)

    return registry
