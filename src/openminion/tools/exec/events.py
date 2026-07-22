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
from openminion.modules.brain.runtime.escalation import (
    ActionRiskTier,
)

from .constants import (
    EXEC_APPROVAL_PENDING_STATUSES,
    EXEC_ARTIFACT_THRESHOLD_BYTES,
)

_ARTIFACT_THRESHOLD_BYTES = EXEC_ARTIFACT_THRESHOLD_BYTES
_APPROVAL_PENDING_STATUSES = EXEC_APPROVAL_PENDING_STATUSES


_KEY_ALIASES = {
    "ENTER": b"\r",
    "RETURN": b"\r",
    "TAB": b"\t",
    "BACKSPACE": b"\x7f",
    "ESC": b"\x1b",
    "UP": b"\x1b[A",
    "DOWN": b"\x1b[B",
    "LEFT": b"\x1b[D",
    "RIGHT": b"\x1b[C",
    "C-C": b"\x03",
    "C-D": b"\x04",
    "C-Z": b"\x1a",
}
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

_CANONICAL_EXECUTABLE_ALIASES: dict[str, str] = {
    "python3": "python3.11",
}


_UNSUPPORTED_REDIRECTION_HINT_TOOL = "file.list_dir"
_UNSUPPORTED_REDIRECTION_HINT_FIX = (
    "Redirections are not supported. For workspace inspection, use "
    "file.list_dir and file.read instead of shell chains. For command output, "
    "run the command directly; stdout and stderr previews are captured "
    "separately."
)
_UNSUPPORTED_COMMAND_OUTPUT_REDIRECTION_HINT_TOOL = "exec.run"
_UNSUPPORTED_COMMAND_OUTPUT_REDIRECTION_HINT_FIX = (
    "Redirections, pipes, and shell output truncation are not supported. Run the "
    "verification command directly; stdout and stderr previews are captured "
    "separately."
)
_PYTEST_EXECUTABLE_HINT_TOOL = "exec.run"
_PYTEST_EXECUTABLE_HINT_FIX = (
    "Bare `pytest` is not allowlisted. Run pytest through the allowed Python "
    "module form instead: `python -m pytest -q tests`. Do not use pipes, "
    "redirections, shell chaining, or output truncation."
)
_PACKAGE_INSTALL_HINT_TOOL = "exec.run"
_PACKAGE_INSTALL_HINT_FIX = (
    "Package-manager install commands are not allowlisted for this execution "
    "surface. Do not install the project just to verify local changes. If the "
    "task requires Python test verification, run the allowed direct command "
    "`python -m pytest -q tests` from the workspace instead."
)
_DISCOVERY_HINT_TOOL = "exec.run"
_DISCOVERY_HINT_FIX = (
    "Run toolchain discovery as a direct command such as "
    "`command -v nasm`, then run a separate direct version check such as "
    "`nasm --version` if the tool exists. Do not use pipes, redirections, "
    "or shell chaining."
)


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
