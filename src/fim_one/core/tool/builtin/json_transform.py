"""Built-in tool for JSON data transformation utilities."""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any

from ..base import BaseTool

_MISSING = object()


class JsonTransformTool(BaseTool):
    """JSON data transformation — query, merge, pick, flatten, and more."""

    @property
    def name(self) -> str:
        return "json_transform"

    @property
    def cacheable(self) -> bool:
        return True

    @property
    def display_name(self) -> str:
        return "JSON Transform"

    @property
    def category(self) -> str:
        return "general"

    @property
    def description(self) -> str:
        return (
            "JSON data transformation and query utilities. "
            "Supported operations: "
            '"path_get" — extract a value by dot-notation path (e.g. "user.address.city" or "items[0].name"); '
            '"merge" — deep-merge two JSON objects (requires data and extra); '
            '"pick" — keep only specified comma-separated keys from an object (requires keys); '
            '"omit" — remove specified comma-separated keys from an object (requires keys); '
            '"flatten" — flatten a nested object into dot-notation keys (optional sep, default "."); '
            '"to_csv" — convert a JSON array of objects to a Markdown table; '
            '"keys" — list all top-level keys of an object. '
            "All operations require the 'data' parameter (a JSON string)."
        )

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "operation": {
                    "type": "string",
                    "enum": ["path_get", "merge", "pick", "omit", "flatten", "to_csv", "keys"],
                    "description": "The JSON operation to perform.",
                },
                "data": {
                    "type": "string",
                    "description": "Input JSON string.",
                },
                "path": {
                    "type": "string",
                    "description": (
                        "Dot-notation path for path_get. "
                        "Supports array indexing: 'items[0].name'."
                    ),
                },
                "extra": {
                    "type": "string",
                    "description": "Second JSON object string for merge.",
                },
                "keys": {
                    "type": "string",
                    "description": "Comma-separated key names for pick/omit.",
                },
                "sep": {
                    "type": "string",
                    "description": "Key separator for flatten (default: '.').",
                },
            },
            "required": ["operation", "data"],
        }

    async def run(self, **kwargs: Any) -> str:
        return await asyncio.to_thread(self._run_sync, **kwargs)

    def _run_sync(self, **kwargs: Any) -> str:
        op: str = kwargs.get("operation", "").strip()
        raw_data: str = kwargs.get("data", "")
        path: str = kwargs.get("path", "")
        raw_extra: str = kwargs.get("extra", "")
        keys_str: str = kwargs.get("keys", "")
        sep: str = kwargs.get("sep", ".")

        if not op:
            return "[Error] No operation specified."

        try:
            data = json.loads(raw_data)
        except json.JSONDecodeError as e:
            return f"[Error] Invalid JSON in 'data': {e}"

        try:
            if op == "path_get":
                if not path:
                    return "[Error] 'path' is required for path_get."
                result = self._path_get(data, path)
                if result is _MISSING:
                    return f"[Error] Path not found: {path}"
                if isinstance(result, (dict, list)):
                    return json.dumps(result, ensure_ascii=False, indent=2)
                return str(result)

            elif op == "merge":
                if not raw_extra:
                    return "[Error] 'extra' is required for merge."
                try:
                    extra = json.loads(raw_extra)
                except json.JSONDecodeError as e:
                    return f"[Error] Invalid JSON in 'extra': {e}"
                if not isinstance(data, dict) or not isinstance(extra, dict):
                    return "[Error] Both 'data' and 'extra' must be JSON objects for merge."
                return json.dumps(self._deep_merge(data, extra), ensure_ascii=False, indent=2)

            elif op == "pick":
                if not keys_str:
                    return "[Error] 'keys' is required for pick."
                if not isinstance(data, dict):
                    return "[Error] 'data' must be a JSON object for pick."
                pick_keys = [k.strip() for k in keys_str.split(",") if k.strip()]
                return json.dumps(
                    {k: data[k] for k in pick_keys if k in data},
                    ensure_ascii=False, indent=2,
                )

            elif op == "omit":
                if not keys_str:
                    return "[Error] 'keys' is required for omit."
                if not isinstance(data, dict):
                    return "[Error] 'data' must be a JSON object for omit."
                omit_keys = {k.strip() for k in keys_str.split(",") if k.strip()}
                return json.dumps(
                    {k: v for k, v in data.items() if k not in omit_keys},
                    ensure_ascii=False, indent=2,
                )

            elif op == "flatten":
                if not isinstance(data, dict):
                    return "[Error] 'data' must be a JSON object for flatten."
                return json.dumps(self._flatten(data, sep=sep), ensure_ascii=False, indent=2)

            elif op == "to_csv":
                if not isinstance(data, list):
                    return "[Error] 'data' must be a JSON array for to_csv."
                if not data:
                    return "(empty array)"
                headers: list[str] = []
                for item in data:
                    if isinstance(item, dict):
                        for k in item:
                            if k not in headers:
                                headers.append(k)
                rows = [
                    [str(item.get(h, "")) if isinstance(item, dict) else "" for h in headers]
                    for item in data
                ]
                widths = [
                    max(len(h), max((len(r[i]) for r in rows), default=0))
                    for i, h in enumerate(headers)
                ]

                def fmt(cells: list[str]) -> str:
                    return "| " + " | ".join(
                        c.ljust(widths[i]) for i, c in enumerate(cells)
                    ) + " |"

                sep_row = "|-" + "-|-".join("-" * w for w in widths) + "-|"
                lines = [fmt(headers), sep_row] + [fmt(r) for r in rows]
                return "\n".join(lines)

            elif op == "keys":
                if not isinstance(data, dict):
                    return "[Error] 'data' must be a JSON object for keys."
                return "\n".join(data.keys())

            else:
                return f"[Error] Unknown operation: {op}"
        except Exception as exc:
            return f"[Error] {type(exc).__name__}: {exc}"

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _path_get(self, data: Any, path: str) -> Any:
        """Navigate a dot-notation path, supporting array indexing like items[0]."""
        current: Any = data
        tokens: list[Any] = []
        for part in path.split("."):
            m = re.fullmatch(r"(\w*)\[(\d+)\]", part)
            if m:
                key, idx = m.group(1), int(m.group(2))
                if key:
                    tokens.append(key)
                tokens.append(idx)
            else:
                tokens.append(part)
        for token in tokens:
            if current is _MISSING:
                break
            if isinstance(token, int):
                if isinstance(current, list) and 0 <= token < len(current):
                    current = current[token]
                else:
                    return _MISSING
            elif isinstance(current, dict):
                current = current.get(token, _MISSING)
            else:
                return _MISSING
        return current

    def _deep_merge(self, base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
        result = dict(base)
        for k, v in override.items():
            if k in result and isinstance(result[k], dict) and isinstance(v, dict):
                result[k] = self._deep_merge(result[k], v)
            else:
                result[k] = v
        return result

    def _flatten(self, data: dict[str, Any], prefix: str = "", sep: str = ".") -> dict[str, Any]:
        items: dict[str, Any] = {}
        for k, v in data.items():
            new_key = f"{prefix}{sep}{k}" if prefix else k
            if isinstance(v, dict):
                items.update(self._flatten(v, new_key, sep))
            else:
                items[new_key] = v
        return items
