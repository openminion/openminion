from datetime import datetime, timezone
from typing import Any

from openminion.modules.tool.contracts.model_ids import (
    MODEL_EXEC_CLEAR,
    MODEL_EXEC_KILL,
    MODEL_EXEC_LIST,
    MODEL_EXEC_PASTE,
    MODEL_EXEC_POLL,
    MODEL_EXEC_RUN,
    MODEL_EXEC_SEND_KEYS,
    MODEL_EXEC_SUBMIT,
)
from openminion.modules.tool.runtime.context import RuntimeContext
from openminion.modules.tool.diagnostics.events import (
    emit_tool_exec_operation_for_context,
)
from openminion.modules.brain.runtime.escalation import ActionRiskTier

_DECLARED_EXEC_RISK_TIERS: dict[str, ActionRiskTier] = {
    MODEL_EXEC_RUN: "approve",
    MODEL_EXEC_POLL: "silent",
    MODEL_EXEC_SEND_KEYS: "approve",
    MODEL_EXEC_SUBMIT: "approve",
    MODEL_EXEC_PASTE: "approve",
    MODEL_EXEC_KILL: "approve",
    MODEL_EXEC_CLEAR: "approve",
    MODEL_EXEC_LIST: "silent",
}


def _declared_exec_risk_tier(tool_name: str | None) -> ActionRiskTier:
    normalized = str(tool_name or "").strip()
    return _DECLARED_EXEC_RISK_TIERS.get(normalized, "silent")


def _timestamp_token() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _slug(value: str, fallback: str = "value") -> str:
    cleaned = "".join(
        ch if ch.isalnum() or ch in {"-", "_"} else "-" for ch in str(value)
    )
    cleaned = cleaned.strip("-")
    return cleaned or fallback


def _emit_exec_operation(
    ctx: RuntimeContext,
    *,
    operation: str,
    tool_name: str,
    status: str = "ok",
    error_code: str | None = None,
    extra: dict[str, Any] | None = None,
) -> None:
    emit_tool_exec_operation_for_context(
        ctx=ctx,
        operation=operation,
        tool_name=tool_name,
        status=status,
        error_code=error_code,
        extra=extra,
    )
