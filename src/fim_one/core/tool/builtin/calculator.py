"""Built-in tool for safely evaluating mathematical expressions."""

from __future__ import annotations

import ast
import math
import operator
from typing import Any

from ..base import BaseTool

# ------------------------------------------------------------------
# Whitelisted operators, functions, and constants
# ------------------------------------------------------------------

_BINARY_OPS: dict[type, Any] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}

_UNARY_OPS: dict[type, Any] = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}

_SAFE_FUNCTIONS: dict[str, Any] = {
    "sqrt": math.sqrt,
    "sin": math.sin,
    "cos": math.cos,
    "tan": math.tan,
    "log": math.log,
    "log10": math.log10,
    "exp": math.exp,
    "abs": abs,
    "ceil": math.ceil,
    "floor": math.floor,
    "round": round,
}

_SAFE_CONSTANTS: dict[str, float] = {
    "pi": math.pi,
    "e": math.e,
}


# ------------------------------------------------------------------
# Safe AST evaluator
# ------------------------------------------------------------------


class _SafeEvaluator(ast.NodeVisitor):
    """Walk an AST tree and evaluate only whitelisted node types."""

    def visit(self, node: ast.AST) -> Any:
        return super().visit(node)

    def generic_visit(self, node: ast.AST) -> Any:
        raise ValueError(f"Unsupported expression node: {type(node).__name__}")

    def visit_Expression(self, node: ast.Expression) -> Any:
        return self.visit(node.body)

    def visit_Constant(self, node: ast.Constant) -> Any:
        if isinstance(node.value, (int, float)):
            return node.value
        raise ValueError(f"Unsupported constant type: {type(node.value).__name__}")

    # Python < 3.8 compat (kept for safety; ast.Num is deprecated but
    # some parsers may still emit it).
    visit_Num = visit_Constant

    def visit_Name(self, node: ast.Name) -> Any:
        if node.id in _SAFE_CONSTANTS:
            return _SAFE_CONSTANTS[node.id]
        raise ValueError(f"Unknown variable: '{node.id}'")

    def visit_UnaryOp(self, node: ast.UnaryOp) -> Any:
        op = _UNARY_OPS.get(type(node.op))
        if op is None:
            raise ValueError(f"Unsupported unary operator: {type(node.op).__name__}")
        return op(self.visit(node.operand))

    def visit_BinOp(self, node: ast.BinOp) -> Any:
        op = _BINARY_OPS.get(type(node.op))
        if op is None:
            raise ValueError(f"Unsupported binary operator: {type(node.op).__name__}")
        return op(self.visit(node.left), self.visit(node.right))

    def visit_Call(self, node: ast.Call) -> Any:
        if not isinstance(node.func, ast.Name):
            raise ValueError("Only simple function calls are allowed")
        func_name = node.func.id
        func = _SAFE_FUNCTIONS.get(func_name)
        if func is None:
            raise ValueError(f"Unknown function: '{func_name}'")
        args = [self.visit(arg) for arg in node.args]
        if node.keywords:
            raise ValueError("Keyword arguments are not supported")
        return func(*args)


# ------------------------------------------------------------------
# Tool implementation
# ------------------------------------------------------------------


class CalculatorTool(BaseTool):
    """Safely evaluate a mathematical expression and return the result.

    Uses Python's :mod:`ast` module to parse the expression into an AST
    tree, then walks it with a whitelist-only visitor.  No ``eval()`` or
    ``exec()`` is used.

    Supported operations:
    - Arithmetic: ``+``, ``-``, ``*``, ``/``, ``//``, ``%``, ``**``
    - Functions: ``sqrt``, ``sin``, ``cos``, ``tan``, ``log``, ``log10``,
      ``exp``, ``abs``, ``ceil``, ``floor``, ``round``
    - Constants: ``pi``, ``e``
    """

    # ------------------------------------------------------------------
    # Tool protocol properties
    # ------------------------------------------------------------------

    @property
    def name(self) -> str:
        return "calculator"

    @property
    def cacheable(self) -> bool:
        return True

    @property
    def category(self) -> str:
        return "computation"

    @property
    def description(self) -> str:
        return (
            "Evaluate a mathematical expression and return the numeric result. "
            "Supports basic arithmetic (+, -, *, /, //, %, **), "
            "math functions (sqrt, sin, cos, tan, log, log10, exp, abs, ceil, floor, round), "
            "and constants (pi, e). "
            "Example: 'sqrt(2) * pi + 3 ** 2'"
        )

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "expression": {
                    "type": "string",
                    "description": "A mathematical expression to evaluate (e.g. '2 * pi + sqrt(16)').",
                },
            },
            "required": ["expression"],
        }

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    async def run(self, **kwargs: Any) -> str:
        """Evaluate the mathematical *expression* safely.

        Args:
            **kwargs: Must contain ``expression`` (str).

        Returns:
            The numeric result as a string, or an error message.
        """
        expression: str = kwargs.get("expression", "").strip()
        if not expression:
            return "[Error] No expression provided."

        try:
            tree = ast.parse(expression, mode="eval")
        except SyntaxError as exc:
            return f"[Error] Invalid expression: {exc}"

        try:
            result = _SafeEvaluator().visit(tree)
        except (ValueError, TypeError, ZeroDivisionError, OverflowError) as exc:
            return f"[Error] {exc}"

        # Return integers without trailing .0 for cleanliness.
        if isinstance(result, float) and result.is_integer():
            return str(int(result))
        return str(result)
