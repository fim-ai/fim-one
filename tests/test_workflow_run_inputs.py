"""What the Start step's declaration accepts, and what it hands on (ledger F23)."""

from __future__ import annotations

from typing import Any

import pytest

from fim_one.core.workflow.run_inputs import InputRefusal, checked_inputs
from fim_one.core.workflow.types import NodeType, WorkflowBlueprint, WorkflowNodeDef


def _blueprint(variables: list[dict[str, Any]], *, validate: bool = True) -> WorkflowBlueprint:
    data: dict[str, Any] = {"variables": variables}
    if validate:
        data["validate_inputs"] = True
    return WorkflowBlueprint(nodes=[WorkflowNodeDef(id="start_1", type=NodeType.START, data=data)])


def _one(kind: str, value: Any, **declared: Any) -> Any:
    out = checked_inputs(_blueprint([{"name": "x", "type": kind, **declared}]), {"x": value})
    return out.get("x", "<left out>")


@pytest.mark.parametrize(
    ("kind", "given", "arrives"),
    [
        ("string", "hello", "hello"),
        ("string", 5, "5"),
        ("string", True, "true"),
        ("number", 5, 5),
        ("number", 2.5, 2.5),
        ("number", "5", 5),
        ("number", " 2.50 ", 2.5),
        ("number", "-1e3", -1000.0),
        ("number", "+7", 7),
        ("number", ".5", 0.5),
        ("number", "5.", 5.0),
        ("boolean", True, True),
        ("boolean", "false", False),
        ("boolean", "Yes", True),
        ("boolean", 0, False),
        ("object", {"a": 1}, {"a": 1}),
        ("object", '{"a": [1, 2]}', {"a": [1, 2]}),
        ("object", "[1, 2]", [1, 2]),
        ("object", 7, 7),
        ("array", ["a"], ["a"]),
        ("array", '["a", 1]', ["a", 1]),
        # The same types under the names the editor shows.
        ("text", 5, "5"),
        ("json", '{"a": 1}', {"a": 1}),
        ("list", "[]", []),
    ],
)
def test_a_value_that_fits_arrives_in_the_declared_type(kind: str, given: Any, arrives: Any) -> None:
    result = _one(kind, given)
    assert result == arrives and type(result) is type(arrives)


@pytest.mark.parametrize(
    ("kind", "given", "expected"),
    [
        ("string", {"a": 1}, "text"),
        ("string", ["a"], "text"),
        ("number", "five", "a number"),
        ("number", True, "a number"),
        ("number", "nan", "a number"),
        ("number", "1e999", "a number"),
        ("number", "1_000", "a number"),
        ("number", "inf", "a number"),
        ("number", "-Infinity", "a number"),
        ("number", "\u0661\u0662", "a number"),  # Arabic-Indic digits
        ("number", "\uff11\uff12", "a number"),  # full-width digits
        ("number", "0x10", "a number"),
        ("number", "1e", "a number"),
        ("number", ".", "a number"),
        ("number", [1], "a number"),
        ("boolean", "maybe", "true or false"),
        ("boolean", 2, "true or false"),
        ("object", "{not json", "JSON"),
        ("array", '{"a": 1}', "a list"),
        ("array", "a, b", "a list"),
        ("array", 5, "a list"),
    ],
)
def test_a_value_that_does_not_fit_is_refused_naming_the_input(
    kind: str, given: Any, expected: str
) -> None:
    with pytest.raises(InputRefusal) as refused:
        _one(kind, given)
    assert refused.value.error_code == "workflow_input_invalid"
    assert refused.value.status_code == 422
    assert refused.value.detail == f"Input 'x' must be {expected}."
    assert refused.value.detail_args == {"input": "x", "expected": expected}


@pytest.mark.parametrize("missing", [None, "", "   "])
def test_a_required_input_must_be_given(missing: Any) -> None:
    with pytest.raises(InputRefusal) as refused:
        _one("number", missing, required=True)
    assert refused.value.error_code == "workflow_input_required"
    assert refused.value.detail_args == {"input": "x"}
    with pytest.raises(InputRefusal):
        checked_inputs(_blueprint([{"name": "x", "type": "string", "required": True}]), {})


def test_zero_and_false_are_values_not_missing() -> None:
    assert _one("number", 0, required=True) == 0
    assert _one("boolean", False, required=True) is False


def test_a_default_stands_in_and_is_checked_like_any_value() -> None:
    assert _one("number", None, required=True, default_value="3") == 3
    assert _one("boolean", "", default_value="true") is True
    with pytest.raises(InputRefusal) as refused:
        _one("number", None, default_value="three")
    assert refused.value.error_code == "workflow_input_invalid"


def test_an_optional_input_that_is_not_given_is_left_out() -> None:
    assert _one("number", None) == "<left out>"
    assert _one("string", None) == "<left out>"
    # No empty value exists for these types: empty text means "not given".
    for kind in ("number", "boolean", "object", "array"):
        assert _one(kind, "") == "<left out>"


def test_empty_text_for_an_optional_text_input_is_a_value() -> None:
    assert _one("string", "") == ""
    assert _one("string", "", default_value="fallback") == "fallback"
    with pytest.raises(InputRefusal) as refused:
        _one("string", "", required=True)
    assert refused.value.error_code == "workflow_input_required"


def test_undeclared_inputs_and_nameless_rows_pass_through() -> None:
    bp = _blueprint([{"name": "", "type": "number", "required": True}, {"name": "n", "type": "number"}])
    assert checked_inputs(bp, {"n": "1", "other": "1"}) == {"n": 1, "other": "1"}


def test_an_unknown_type_is_treated_as_text() -> None:
    assert _one("money", 5) == "5"


def test_without_the_switch_nothing_is_checked_or_changed() -> None:
    bp = _blueprint([{"name": "x", "type": "number", "required": True}], validate=False)
    assert checked_inputs(bp, {"x": "five"}) == {"x": "five"}
    assert checked_inputs(bp, None) == {}
    assert checked_inputs(WorkflowBlueprint(), {"x": 1}) == {"x": 1}
