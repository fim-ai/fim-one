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

        Supports two reference styles (backward-compatible):
        - ``{{var_name}}``              — flat lookup by key
        - ``{{node_id.output_name}}``   — explicit two-level reference

        For the flat style, if no exact key matches, a best-effort search is
        performed against all ``*.var_name`` entries (last segment match).

        Unknown variables are left as-is (the ``{{var_name}}`` placeholder
        remains in the output).
        """
        async with self._lock:
            data_snapshot = dict(self._data)

        def _resolve(var_name: str) -> Any:
            """Resolve a variable name against the data snapshot."""
            # 1. Exact match (covers both flat and dotted references)
            if var_name in data_snapshot:
                return data_snapshot[var_name]

            # 2. Fallback: flat name matches the last segment of a dotted key
            #    e.g. "output" might match "llm_1.output"
            if "." not in var_name:
                for key, val in data_snapshot.items():
                    parts = key.split(".")
                    if len(parts) == 2 and parts[1] == var_name:
                        return val

            return None

        def _replace(match: re.Match) -> str:
            var_name = match.group(1)
            value = _resolve(var_name)
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

    async def get_node_outputs(self, node_id: str) -> dict[str, Any]:
        """Return all output variables for a given node.

        Scans for keys matching ``{node_id}.*`` and returns them as a flat
        dict with the node_id prefix stripped.  Useful for the frontend
        variable picker.
        """
        prefix = f"{node_id}."
        async with self._lock:
            return {
                k[len(prefix):]: v
                for k, v in self._data.items()
                if k.startswith(prefix)
            }

    async def list_available_variables(self) -> list[dict[str, str]]:
        """Return a list of all stored variables with metadata.

        Each entry is ``{node_id, var_name, value_type}`` for dotted keys,
        or ``{node_id: "", var_name: key, value_type}`` for flat keys.
        Env vars (``env.*``) and raw inputs (``input.*``) are excluded.
        """
        async with self._lock:
            data_copy = dict(self._data)

        results: list[dict[str, str]] = []
        for key, value in data_copy.items():
            if key.startswith("env.") or key.startswith("input."):
                continue
            parts = key.split(".", 1)
            if len(parts) == 2:
                node_id, var_name = parts
            else:
                node_id, var_name = "", parts[0]
            results.append({
                "node_id": node_id,
                "var_name": var_name,
                "value_type": type(value).__name__,
            })
        return results

    async def evaluate_expression(self, expression: str) -> Any:
        """Safely evaluate a Python expression with store variables as context.

        Uses ``simpleeval`` for sandboxed evaluation. All store variables
        (excluding ``env.*``) are available in the expression namespace.
        Common builtins (``len``, ``str``, ``int``, ``float``, ``bool``,
        ``abs``, ``min``, ``max``, ``sum``, ``round``, ``sorted``) are also
        available.

        Returns the expression result, or raises ``ValueError`` on failure.
        """
        try:
            from simpleeval import simple_eval, DEFAULT_OPERATORS
        except ImportError:
            raise RuntimeError("simpleeval is required for expression evaluation")

        data = await self.snapshot_safe()

        # Build a flat namespace: both full keys and last-segment aliases
        names: dict[str, Any] = {}
        for key, value in data.items():
            names[key] = value
            # Also expose the last segment as a shortcut
            parts = key.split(".")
            if len(parts) == 2:
                short = parts[1]
                if short not in names:
                    names[short] = value

        safe_functions = {
            "len": len,
            "str": str,
            "int": int,
            "float": float,
            "bool": bool,
            "abs": abs,
            "min": min,
            "max": max,
            "sum": sum,
            "round": round,
            "sorted": sorted,
        }

        try:
            return simple_eval(
                expression,
                operators=DEFAULT_OPERATORS,
                names=names,
                functions=safe_functions,
            )
        except Exception as exc:
            raise ValueError(f"Expression evaluation failed: {exc}") from exc

    async def has(self, name: str) -> bool:
        """Check if a variable exists in the store."""
        async with self._lock:
            return name in self._data

    async def delete(self, name: str) -> bool:
        """Delete a variable from the store. Returns True if it existed."""
        async with self._lock:
            if name in self._data:
                del self._data[name]
                return True
            return False

    async def keys(self) -> list[str]:
        """Return all variable keys."""
        async with self._lock:
            return list(self._data.keys())

    def snapshot_sync(self) -> dict[str, Any]:
        """Return a shallow copy without acquiring the lock (for non-async contexts).

        Only safe to call when no concurrent writes are happening.
        """
        return dict(self._data)
