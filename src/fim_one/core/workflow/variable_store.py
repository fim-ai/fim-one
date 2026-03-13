"""Thread-safe variable store for workflow execution.

Supports ``{{var_name}}`` interpolation and ``env.XXX`` namespace for
encrypted environment variables.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any


_VAR_PATTERN = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_.]*)\s*\}\}")


class VariableStore:
    """Async-safe key-value store used during workflow execution.

    Variables are namespaced by node_id using dot notation:
    ``node_id.output_name``. Environment variables are injected under the
    ``env.`` namespace.
    """

    def __init__(self, env_vars: dict[str, str] | None = None) -> None:
        self._data: dict[str, Any] = {}
        self._lock = asyncio.Lock()

        # Inject env vars under "env." namespace
        if env_vars:
            for key, value in env_vars.items():
                self._data[f"env.{key}"] = value

    async def get(self, name: str, default: Any = None) -> Any:
        """Get a variable by name."""
        async with self._lock:
            return self._data.get(name, default)

    async def set(self, name: str, value: Any) -> None:
        """Set a variable."""
        async with self._lock:
            self._data[name] = value

    async def set_many(self, mapping: dict[str, Any]) -> None:
        """Set multiple variables at once."""
        async with self._lock:
            self._data.update(mapping)

    async def interpolate(self, template: str) -> str:
        """Replace ``{{var_name}}`` patterns in *template* with stored values.

        Unknown variables are left as-is (the ``{{var_name}}`` placeholder
        remains in the output).
        """
        async with self._lock:
            data_snapshot = dict(self._data)

        def _replace(match: re.Match) -> str:
            var_name = match.group(1)
            value = data_snapshot.get(var_name)
            if value is None:
                return match.group(0)  # leave placeholder as-is
            if isinstance(value, str):
                return value
            # For non-string values, convert to string representation
            import json

            try:
                return json.dumps(value, ensure_ascii=False)
            except (TypeError, ValueError):
                return str(value)

        return _VAR_PATTERN.sub(_replace, template)

    async def snapshot(self) -> dict[str, Any]:
        """Return a shallow copy of all stored variables."""
        async with self._lock:
            return dict(self._data)

    async def snapshot_safe(self) -> dict[str, Any]:
        """Return a snapshot excluding env.* variables (for expression/template contexts)."""
        async with self._lock:
            return {k: v for k, v in self._data.items() if not k.startswith("env.")}

    def snapshot_sync(self) -> dict[str, Any]:
        """Return a shallow copy without acquiring the lock (for non-async contexts).

        Only safe to call when no concurrent writes are happening.
        """
        return dict(self._data)
