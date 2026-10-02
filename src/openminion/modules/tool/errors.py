from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .contracts.schemas import ErrorCode


class ToolRuntimeError(Exception):
    def __init__(
        self,
        code: ErrorCode,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code: ErrorCode = code
        self.message = message
        self.details = details or {}


def tool_result_error_facts(
    error: str, data: Mapping[str, Any] | None
) -> dict[str, Any]:
    """Return the canonical error facts carried by a tool result."""

    facts = dict(data or {})
    nested = facts.get("error")
    nested = dict(nested) if isinstance(nested, Mapping) else {}
    details = facts.get("error_details") or nested.get("details")
    code = facts.get("error_code") or nested.get("code") or "TOOL_EXECUTION_ERROR"
    payload: dict[str, Any] = {"code": str(code), "message": str(error or code)}
    if isinstance(details, Mapping) and details:
        payload["details"] = dict(details)
    return payload


def is_per_tool_budget_denial(error: str, data: Mapping[str, Any] | None) -> bool:
    facts = tool_result_error_facts(error, data)
    return str(facts["code"]).upper().startswith("TOOL_BUDGET") and (
        "max_calls_per_tool" in facts.get("details", {})
    )


def format_tool_budget_denial(message: str, details: Mapping[str, Any]) -> str:
    """Add actionable usage facts to a tool-budget denial message."""

    if "max_calls_per_tool" in details:
        usage = f"{details.get('tool_calls', 0)}/{details['max_calls_per_tool']} calls"
        next_step = "Continue with another available tool or the existing results."
    elif "max_calls_per_run" in details:
        usage = (
            f"{details.get('tool_calls_total', 0)}/{details['max_calls_per_run']} calls"
        )
        next_step = "Continue in a new turn."
    elif "max_budget_cost_per_run" in details:
        usage = (
            f"{details.get('budget_cost_total', 0)}/"
            f"{details['max_budget_cost_per_run']} tool cost"
        )
        next_step = "Continue in a new turn."
    else:
        return message
    return f"{message}: {usage} used for this {'tool' if 'max_calls_per_tool' in details else 'turn'}. {next_step}"
