"""Checking a run's inputs against what the Start step declares.

The Start step lists the workflow's inputs: a name, a type and whether the
input is required. With the step's ``validate_inputs`` switch on, every
entry point checks a run's inputs against that list before any step starts
(:func:`checked_inputs`), refuses a run whose inputs do not fit with a code
that names the input, and hands the steps values in their declared type: a
number arrives as a number whether the caller sent ``5`` or ``"5"``.

With the switch off (every workflow saved before it existed) the inputs
are passed through untouched, as they always were.

Types, as stored in ``variables[].type`` (the editor's label in brackets):

- ``string`` (Text): text. A number or boolean is written out as text.
- ``number`` (Number): a finite number, or text that is one written plainly:
  ASCII digits with an optional sign, decimal point and exponent (``12``,
  ``-0.5``, ``1e3``). ``1_000``, ``nan``, ``inf`` and digits of other
  scripts are refused.
- ``boolean`` (Boolean): ``true`` / ``false``, or the texts ``true``,
  ``false``, ``yes``, ``no``, ``1``, ``0``, or the numbers 1 and 0.
- ``object`` (JSON): any JSON value; text is parsed as JSON.
- ``array`` (List): a list; text is parsed as JSON and must be a list.

An input that is absent, ``None`` or empty text takes the declared default
(checked like any other value). With no default: a required input is
refused; an optional one is left out, except that empty text given for a
``string`` input is a value and is passed on as ``""`` (for the other
types there is no empty value, so empty text means "not given"). Inputs
the Start step does not declare are passed through as they are.
"""

from __future__ import annotations

import json
import math
import re
from typing import Any

from fim_one.core.assembly.errors import AssemblyRefusal

from .types import NodeType, WorkflowBlueprint

__all__ = [
    "INPUT_INVALID",
    "INPUT_REQUIRED",
    "INPUT_TYPES",
    "InputRefusal",
    "checked_inputs",
]

INPUT_REQUIRED = "workflow_input_required"
INPUT_INVALID = "workflow_input_invalid"

# Stored type -> what a person is told was expected.
INPUT_TYPES: dict[str, str] = {
    "string": "text",
    "number": "a number",
    "boolean": "true or false",
    "object": "JSON",
    "array": "a list",
}
# Other spellings of the same types.
_ALIASES = {
    "text": "string",
    "str": "string",
    "integer": "number",
    "float": "number",
    "bool": "boolean",
    "json": "object",
    "list": "array",
}


class InputRefusal(AssemblyRefusal):
    """An input does not fit the Start step's declaration.

    ``detail_args`` carries ``input`` (its name) and, for a wrong type,
    ``expected``.
    """


class _Unfit(Exception):
    pass


_INTEGER = re.compile(r"[+-]?[0-9]+")
_DECIMAL = re.compile(r"[+-]?(?:[0-9]+\.?[0-9]*|\.[0-9]+)(?:[eE][+-]?[0-9]+)?")


def checked_inputs(blueprint: WorkflowBlueprint, inputs: dict[str, Any] | None) -> dict[str, Any]:
    """*inputs* as the run's steps get them; raises :class:`InputRefusal`.

    Unchanged unless the blueprint's Start step has ``validate_inputs`` on.
    """
    given = dict(inputs or {})
    start = next((n for n in blueprint.nodes if n.type == NodeType.START), None)
    if start is None or start.data.get("validate_inputs") is not True:
        return given

    for variable in start.data.get("variables") or []:
        if not isinstance(variable, dict):
            continue
        name = str(variable.get("name") or "").strip()
        if not name:
            continue
        declared = str(variable.get("type") or "string").strip().lower()
        kind = _ALIASES.get(declared, declared)
        if kind not in INPUT_TYPES:
            kind = "string"

        value = given.get(name)
        if _absent(value):
            value = variable.get("default_value")
        if _absent(value):
            sent = given.get(name)
            if kind == "string" and isinstance(sent, str) and not variable.get("required"):
                # Explicitly empty text for an optional text input.
                given[name] = ""
                continue
            if variable.get("required"):
                raise InputRefusal(
                    INPUT_REQUIRED,
                    422,
                    f"Input '{name}' is required.",
                    {"input": name},
                )
            given.pop(name, None)
            continue
        try:
            given[name] = _fit(value, kind)
        except _Unfit:
            expected = INPUT_TYPES[kind]
            raise InputRefusal(
                INPUT_INVALID,
                422,
                f"Input '{name}' must be {expected}.",
                {"input": name, "expected": expected},
            ) from None
    return given


def _absent(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _fit(value: Any, kind: str) -> Any:
    if kind == "string":
        if isinstance(value, str):
            return value
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, (int, float)):
            return str(value)
        raise _Unfit
    if kind == "number":
        return _number(value)
    if kind == "boolean":
        return _boolean(value)
    if kind == "object":
        return _parsed(value) if isinstance(value, str) else value
    # array
    parsed = _parsed(value) if isinstance(value, str) else value
    if not isinstance(parsed, list):
        raise _Unfit
    return parsed


def _number(value: Any) -> int | float:
    if isinstance(value, bool):
        raise _Unfit
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise _Unfit
        return value
    if isinstance(value, str):
        text = value.strip()
        # Decided by the pattern, not by what int() and float() accept:
        # they also take "1_000", "nan", "Infinity" and non-ASCII digits.
        if _INTEGER.fullmatch(text):
            return int(text)
        if _DECIMAL.fullmatch(text):
            number = float(text)
            if math.isfinite(number):
                return number
    raise _Unfit


def _boolean(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        text = value.strip().lower()
        if text in ("true", "yes", "1"):
            return True
        if text in ("false", "no", "0"):
            return False
    raise _Unfit


def _parsed(text: str) -> Any:
    try:
        return json.loads(text)
    except ValueError:
        raise _Unfit from None
