import ast
import math
import operator
import re
from datetime import datetime, timezone
from typing import Any, Mapping

from pydantic import BaseModel, Field

from openminion.modules.tool.errors import ToolRuntimeError
from openminion.modules.tool.registry import ToolRegistry, ToolSpec
from openminion.modules.tool.runtime import RuntimeContext

from .constants import (
    UTILITY_ALLOWED_FORMATS,
    UTILITY_MAX_ABS_VALUE,
    UTILITY_MAX_AST_NODES,
)


class UTCNowArgs(BaseModel):
    format: str = Field(default="iso", description="Output format: iso or epoch")


class CalculateExpressionArgs(BaseModel):
    expression: str = Field(
        min_length=1, max_length=512, description="Arithmetic expression"
    )


class TextStatsArgs(BaseModel):
    text: str = Field(max_length=200_000, description="Text to analyze")


_ALLOWED_BINARY_OPERATORS: Mapping[type[ast.AST], Any] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_ALLOWED_UNARY_OPERATORS: Mapping[type[ast.AST], Any] = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}


def _h_utc_now(args: dict[str, Any], ctx: RuntimeContext) -> dict[str, Any]:
    del ctx
    now = datetime.now(timezone.utc)
    output_format = str(args.get("format", "iso")).strip().lower() or "iso"
    if output_format in {"epoch", "unix"}:
        return {
            "epoch_seconds": int(now.timestamp()),
            "timezone": "UTC",
        }
    if output_format != UTILITY_ALLOWED_FORMATS[0]:
        raise ToolRuntimeError(
            "INVALID_ARGUMENT",
            "Unsupported format for utc_now",
            {"supported": list(UTILITY_ALLOWED_FORMATS)},
        )
    return {
        "iso": now.isoformat(),
        "timezone": "UTC",
    }


def _eval_expression(expression: str) -> float:
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as exc:
        raise ToolRuntimeError(
            "INVALID_ARGUMENT",
            "Expression is not valid Python arithmetic syntax",
            {"expression": expression},
        ) from exc

    node_count = sum(1 for _ in ast.walk(tree))
    if node_count > UTILITY_MAX_AST_NODES:
        raise ToolRuntimeError(
            "INVALID_ARGUMENT",
            "Expression is too complex",
            {"max_nodes": UTILITY_MAX_AST_NODES},
        )

    def _eval(node: ast.AST) -> float:
        if isinstance(node, ast.Expression):
            return _eval(node.body)
        if isinstance(node, ast.Constant):
            value = node.value
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ToolRuntimeError(
                    "INVALID_ARGUMENT", "Only numeric literals are allowed"
                )
            return float(value)
        if isinstance(node, ast.BinOp):
            operator_fn = _ALLOWED_BINARY_OPERATORS.get(type(node.op))
            if operator_fn is None:
                raise ToolRuntimeError("INVALID_ARGUMENT", "Operator is not allowed")
            left = _eval(node.left)
            right = _eval(node.right)
            if type(node.op) in {ast.Div, ast.FloorDiv, ast.Mod} and right == 0:
                raise ToolRuntimeError("INVALID_ARGUMENT", "Division by zero")
            if type(node.op) is ast.Pow and abs(right) > 64:
                raise ToolRuntimeError("INVALID_ARGUMENT", "Exponent is too large")
            result = operator_fn(left, right)
            if not math.isfinite(result):
                raise ToolRuntimeError(
                    "INVALID_ARGUMENT", "Expression result is not finite"
                )
            if abs(result) > UTILITY_MAX_ABS_VALUE:
                raise ToolRuntimeError(
                    "INVALID_ARGUMENT", "Expression result is out of bounds"
                )
            return float(result)
        if isinstance(node, ast.UnaryOp):
            operator_fn = _ALLOWED_UNARY_OPERATORS.get(type(node.op))
            if operator_fn is None:
                raise ToolRuntimeError(
                    "INVALID_ARGUMENT", "Unary operator is not allowed"
                )
            return float(operator_fn(_eval(node.operand)))
        raise ToolRuntimeError("INVALID_ARGUMENT", "Unsupported expression element")

    return _eval(tree)


def _h_calculate_expression(
    args: dict[str, Any], ctx: RuntimeContext
) -> dict[str, Any]:
    del ctx
    expression = str(args.get("expression", "")).strip()
    if not expression:
        raise ToolRuntimeError("INVALID_ARGUMENT", "expression is required")
    value = _eval_expression(expression)
    # Preserve integers where possible for cleaner responses.
    result: int | float = int(value) if value.is_integer() else value
    return {
        "expression": expression,
        "result": result,
    }


def _h_text_stats(args: dict[str, Any], ctx: RuntimeContext) -> dict[str, Any]:
    del ctx
    text = str(args.get("text", ""))
    line_count = 0 if not text else text.count("\n") + 1
    word_count = len(re.findall(r"\b\w+\b", text))
    sentence_count = len([item for item in re.split(r"[.!?]+", text) if item.strip()])
    char_count = len(text)
    return {
        "char_count": char_count,
        "word_count": word_count,
        "line_count": line_count,
        "sentence_count": sentence_count,
    }


def register(registry: ToolRegistry) -> None:
    registry.add(
        ToolSpec(
            name="utility.utc_now",
            args_model=UTCNowArgs,
            min_scope="READ_ONLY",
            handler=_h_utc_now,
            dangerous=False,
            idempotent=True,
            tags=("plugin", "utility", "time"),
            capabilities=("read_only", "utility", "time"),
        )
    )
    registry.add(
        ToolSpec(
            name="utility.calculate_expression",
            args_model=CalculateExpressionArgs,
            min_scope="READ_ONLY",
            handler=_h_calculate_expression,
            dangerous=False,
            idempotent=True,
            tags=("plugin", "utility", "math"),
            capabilities=("read_only", "utility", "math"),
        )
    )
    registry.add(
        ToolSpec(
            name="utility.text_stats",
            args_model=TextStatsArgs,
            min_scope="READ_ONLY",
            handler=_h_text_stats,
            dangerous=False,
            idempotent=True,
            tags=("plugin", "utility", "text"),
            capabilities=("read_only", "utility", "text"),
        )
    )


__all__ = ["register"]
