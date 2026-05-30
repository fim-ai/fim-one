"""Tests for the built-in ``CalculatorTool``."""

from __future__ import annotations

import math

import pytest

from fim_one.core.tool.builtin.calculator import CalculatorTool


# ======================================================================
# Fixtures
# ======================================================================


@pytest.fixture()
def tool() -> CalculatorTool:
    return CalculatorTool()


# ======================================================================
# Tool protocol compliance
# ======================================================================


class TestCalculatorToolProperties:
    """Verify tool protocol properties."""

    def test_name(self, tool: CalculatorTool) -> None:
        assert tool.name == "calculator"

    def test_category(self, tool: CalculatorTool) -> None:
        assert tool.category == "computation"

    def test_cacheable(self, tool: CalculatorTool) -> None:
        assert tool.cacheable is True

    def test_description_mentions_supported_ops(self, tool: CalculatorTool) -> None:
        desc = tool.description
        assert "sqrt" in desc
        assert "pi" in desc

    def test_parameters_schema(self, tool: CalculatorTool) -> None:
        schema = tool.parameters_schema
        assert schema["type"] == "object"
        assert "expression" in schema["properties"]
        assert schema["required"] == ["expression"]


# ======================================================================
# Basic arithmetic
# ======================================================================


class TestBasicArithmetic:
    """Tests for arithmetic operators and precedence."""

    async def test_addition(self, tool: CalculatorTool) -> None:
        assert await tool.run(expression="2 + 3") == "5"

    async def test_subtraction(self, tool: CalculatorTool) -> None:
        assert await tool.run(expression="10 - 4") == "6"

    async def test_multiplication(self, tool: CalculatorTool) -> None:
        assert await tool.run(expression="6 * 7") == "42"

    async def test_true_division(self, tool: CalculatorTool) -> None:
        # 7 / 2 == 3.5 — stays float.
        assert await tool.run(expression="7 / 2") == "3.5"

    async def test_integer_division(self, tool: CalculatorTool) -> None:
        assert await tool.run(expression="7 // 2") == "3"

    async def test_modulo(self, tool: CalculatorTool) -> None:
        assert await tool.run(expression="7 % 3") == "1"

    async def test_power(self, tool: CalculatorTool) -> None:
        assert await tool.run(expression="2 ** 10") == "1024"

    async def test_operator_precedence(self, tool: CalculatorTool) -> None:
        # Multiplication before addition.
        assert await tool.run(expression="2 + 3 * 4") == "14"

    async def test_parentheses_override_precedence(self, tool: CalculatorTool) -> None:
        assert await tool.run(expression="(2 + 3) * 4") == "20"

    async def test_unary_negation(self, tool: CalculatorTool) -> None:
        assert await tool.run(expression="-5 + 2") == "-3"

    async def test_unary_plus(self, tool: CalculatorTool) -> None:
        assert await tool.run(expression="+5") == "5"

    async def test_float_result_with_fraction(self, tool: CalculatorTool) -> None:
        assert await tool.run(expression="1 / 4") == "0.25"

    async def test_integer_valued_float_normalized(self, tool: CalculatorTool) -> None:
        # 4.0 / 2 == 2.0 -> normalized to "2" (no trailing .0).
        assert await tool.run(expression="4.0 / 2") == "2"


# ======================================================================
# Functions and constants
# ======================================================================


class TestFunctionsAndConstants:
    """Tests for whitelisted math functions and constants."""

    async def test_sqrt(self, tool: CalculatorTool) -> None:
        assert await tool.run(expression="sqrt(16)") == "4"

    async def test_abs(self, tool: CalculatorTool) -> None:
        assert await tool.run(expression="abs(-9)") == "9"

    async def test_ceil(self, tool: CalculatorTool) -> None:
        assert await tool.run(expression="ceil(2.1)") == "3"

    async def test_floor(self, tool: CalculatorTool) -> None:
        assert await tool.run(expression="floor(2.9)") == "2"

    async def test_round(self, tool: CalculatorTool) -> None:
        assert await tool.run(expression="round(3.14159)") == "3"

    async def test_pi_constant(self, tool: CalculatorTool) -> None:
        result = await tool.run(expression="pi")
        assert result == str(math.pi)

    async def test_e_constant(self, tool: CalculatorTool) -> None:
        result = await tool.run(expression="e")
        assert result == str(math.e)

    async def test_nested_function_and_constant(self, tool: CalculatorTool) -> None:
        # sqrt(2) * pi + 3 ** 2 — exercises functions + constants + ops.
        expected = math.sqrt(2) * math.pi + 3 ** 2
        result = await tool.run(expression="sqrt(2) * pi + 3 ** 2")
        assert result == str(expected)

    async def test_log(self, tool: CalculatorTool) -> None:
        assert await tool.run(expression="log(e)") == "1"

    async def test_log10(self, tool: CalculatorTool) -> None:
        assert await tool.run(expression="log10(1000)") == "3"


# ======================================================================
# Error handling
# ======================================================================


class TestErrorHandling:
    """Tests for invalid expressions and safety guards."""

    async def test_empty_expression(self, tool: CalculatorTool) -> None:
        result = await tool.run(expression="")
        assert "[Error]" in result
        assert "No expression" in result

    async def test_whitespace_only_expression(self, tool: CalculatorTool) -> None:
        result = await tool.run(expression="   ")
        assert "[Error]" in result
        assert "No expression" in result

    async def test_missing_expression_kwarg(self, tool: CalculatorTool) -> None:
        result = await tool.run()
        assert "[Error]" in result

    async def test_division_by_zero(self, tool: CalculatorTool) -> None:
        result = await tool.run(expression="1 / 0")
        assert "[Error]" in result

    async def test_floor_division_by_zero(self, tool: CalculatorTool) -> None:
        result = await tool.run(expression="1 // 0")
        assert "[Error]" in result

    async def test_modulo_by_zero(self, tool: CalculatorTool) -> None:
        result = await tool.run(expression="1 % 0")
        assert "[Error]" in result

    async def test_syntax_error(self, tool: CalculatorTool) -> None:
        result = await tool.run(expression="2 +")
        assert "[Error]" in result
        assert "Invalid expression" in result

    async def test_unknown_variable_rejected(self, tool: CalculatorTool) -> None:
        result = await tool.run(expression="foo + 1")
        assert "[Error]" in result
        assert "Unknown variable" in result

    async def test_unknown_function_rejected(self, tool: CalculatorTool) -> None:
        result = await tool.run(expression="evil(1)")
        assert "[Error]" in result
        assert "Unknown function" in result

    async def test_dunder_name_rejected(self, tool: CalculatorTool) -> None:
        # No name resolution outside the whitelist — __import__ is just an
        # unknown variable, never a callable.
        result = await tool.run(expression="__import__('os')")
        assert "[Error]" in result

    async def test_attribute_access_rejected(self, tool: CalculatorTool) -> None:
        # Attribute access nodes are not whitelisted.
        result = await tool.run(expression="pi.__class__")
        assert "[Error]" in result

    async def test_string_constant_rejected(self, tool: CalculatorTool) -> None:
        result = await tool.run(expression="'hello'")
        assert "[Error]" in result
        assert "Unsupported constant" in result

    async def test_keyword_arguments_rejected(self, tool: CalculatorTool) -> None:
        result = await tool.run(expression="round(3.14159, ndigits=2)")
        assert "[Error]" in result
        assert "Keyword arguments" in result

    async def test_bitwise_operator_rejected(self, tool: CalculatorTool) -> None:
        # Bitwise OR is not in the whitelist of binary operators.
        result = await tool.run(expression="5 | 3")
        assert "[Error]" in result
        assert "Unsupported binary operator" in result

    async def test_comparison_rejected(self, tool: CalculatorTool) -> None:
        # Compare nodes are not handled -> generic_visit raises.
        result = await tool.run(expression="1 < 2")
        assert "[Error]" in result
        assert "Unsupported expression node" in result

    async def test_complex_call_target_rejected(self, tool: CalculatorTool) -> None:
        # An attribute-access call target is not a simple Name -> rejected.
        result = await tool.run(expression="math.sqrt(4)")
        assert "[Error]" in result
        assert "simple function calls" in result


# ======================================================================
# Whitespace / input handling
# ======================================================================


class TestInputHandling:
    """Tests for input normalization."""

    async def test_expression_is_stripped(self, tool: CalculatorTool) -> None:
        assert await tool.run(expression="  2 + 2  ") == "4"
