"""Registry of named LLM instances with role-based selection."""

from __future__ import annotations

import logging

from .base import BaseLLM

logger = logging.getLogger(__name__)


class ModelRegistry:
    """Registry of named LLM instances with role-based selection.

    Models are registered with a unique name and an optional list of roles
    (e.g. ``"general"``, ``"fast"``, ``"vision"``, ``"compact"``).  The
    registry supports lookup by exact name, by role, and a sensible default
    fallback.

    Example::

        registry = ModelRegistry()
        registry.register("gpt-4o", gpt4o_llm, roles=["general", "vision"])
        registry.register("gpt-4o-mini", mini_llm, roles=["fast", "compact"])

        registry.get("gpt-4o")          # exact name lookup
        registry.get_by_role("fast")     # first LLM registered for "fast"
        registry.get_default()           # LLM with "general" role, or first registered
    """

    def __init__(self) -> None:
        self._models: dict[str, BaseLLM] = {}
        self._roles: dict[str, list[str]] = {}  # role -> [model_name, ...]
        self._insertion_order: list[str] = []

    def register(
        self,
        name: str,
        llm: BaseLLM,
        roles: list[str] | None = None,
    ) -> None:
        """Register an LLM with a name and optional roles.

        Args:
            name: A unique identifier for this model instance.
            llm: The LLM instance to register.
            roles: Optional list of roles this model fulfils (e.g.
                ``["general", "fast"]``).

        Raises:
            ValueError: If a model with the same *name* is already registered.
        """
        if name in self._models:
            raise ValueError(
                f"Model '{name}' is already registered. "
                "Unregister it first or use a different name."
            )

        self._models[name] = llm
        self._insertion_order.append(name)

        for role in roles or []:
            self._roles.setdefault(role, []).append(name)

        logger.debug(
            "Registered model '%s' with roles %s",
            name,
            roles or [],
        )

    def get(self, name: str) -> BaseLLM:
        """Get an LLM by its exact registered name.

        Args:
            name: The model name to look up.

        Returns:
            The registered ``BaseLLM`` instance.

        Raises:
            KeyError: If no model with the given name exists.
        """
        if name not in self._models:
            raise KeyError(
                f"Model '{name}' is not registered. "
                f"Available models: {self.list_models()}"
            )
        return self._models[name]

    def get_by_role(self, role: str) -> BaseLLM:
        """Get the first LLM registered for a given role.

        Args:
            role: The role to look up (e.g. ``"fast"``, ``"vision"``).

        Returns:
            The first ``BaseLLM`` instance registered under that role.

        Raises:
            KeyError: If no model is registered for the given role.
        """
        names = self._roles.get(role)
        if not names:
            raise KeyError(
                f"No model registered for role '{role}'. "
                f"Available roles: {sorted(self._roles.keys())}"
            )
        return self._models[names[0]]

    def get_default(self) -> BaseLLM:
        """Get the default LLM.

        Resolution order:

        1. The first model registered with the ``"general"`` role.
        2. The first model registered (by insertion order).

        Returns:
            The default ``BaseLLM`` instance.

        Raises:
            RuntimeError: If the registry is empty.
        """
        if not self._models:
            raise RuntimeError("ModelRegistry is empty -- no models registered.")

        # Prefer the model with the "general" role.
        general_names = self._roles.get("general")
        if general_names:
            return self._models[general_names[0]]

        # Fall back to the first registered model.
        first_name = self._insertion_order[0]
        return self._models[first_name]

    def list_models(self) -> list[str]:
        """List all registered model names in insertion order.

        Returns:
            A list of model name strings.
        """
        return list(self._insertion_order)

    # ------------------------------------------------------------------
    # Dunder helpers
    # ------------------------------------------------------------------

    def __len__(self) -> int:
        return len(self._models)

    def __contains__(self, name: str) -> bool:
        return name in self._models

    def __repr__(self) -> str:
        model_names = ", ".join(self._insertion_order)
        return f"ModelRegistry([{model_names}])"
