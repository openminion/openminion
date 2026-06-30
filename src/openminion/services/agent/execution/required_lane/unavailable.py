from typing import TYPE_CHECKING, Any, Mapping

from openminion.modules.tool.registry import ToolExecutionBatch
from openminion.services.agent.execution.deps import ExecutorDeps
from openminion.services.agent.execution.loop_quality import (
    exec_command_action_class,
    exec_tool_call_command,
)

from .metadata import build_required_outcome
from .state import _PhaseResult

if TYPE_CHECKING:
    from .runner import RequiredLaneRunner


def _tool_result_unavailable_message(result: Any) -> str:
    data = getattr(result, "data", {}) or {}
    exit_code = data.get("exit_code")
    error = str(getattr(result, "error", "") or "")
    content = str(getattr(result, "content", "") or "")
    combined = f"{error}\n{content}".lower()
    if exit_code == 127 or "command not found" in combined or "code 127" in combined:
        return error or content or "command unavailable"
    return ""


def unavailable_discovery_or_version_message(
    response: Any,
    batch: ToolExecutionBatch,
) -> str:
    calls = list(response.tool_calls or [])
    results = list(batch.results or [])
    for index, call in enumerate(calls):
        command = exec_tool_call_command(call)
        action_class = exec_command_action_class(command)
        if action_class not in {"discovery", "version"}:
            continue
        result = results[index] if index < len(results) else None
        if result is None or bool(getattr(result, "ok", False)):
            continue
        unavailable = _tool_result_unavailable_message(result)
        if not unavailable:
            continue
        command_label = command or "the requested command"
        return (
            f"`{command_label}` could not run in this environment "
            f"({unavailable}). The requested tool appears unavailable here, "
            "so I cannot report a version without installing or changing the "
            "environment."
        )
    return ""


def unavailable_discovery_retry_instruction(
    response: Any,
    batch: ToolExecutionBatch,
) -> str:
    message = unavailable_discovery_or_version_message(response, batch)
    if not message:
        return ""
    return (
        "The previous exec.run result is already enough evidence for this "
        f"discovery/version check: {message} Do not rerun the same "
        "discovery/version command. Return a concise final answer from that "
        "evidence."
    )


def unavailable_discovery_phase_result(
    runner: "RequiredLaneRunner",
    *,
    deps: ExecutorDeps,
    final_response: Any,
    final_sig: str,
    intent_category: str,
    response: Any,
    batch: ToolExecutionBatch,
    attempted_tools: list[str],
    capability_fallback_trigger_reason: str | None,
    shared_capability_meta: Mapping[str, Any],
) -> _PhaseResult | None:
    message = unavailable_discovery_or_version_message(response, batch)
    if not message:
        return None
    return _PhaseResult(
        action="return",
        outcome=build_required_outcome(
            runner,
            deps=deps,
            text=message,
            model=str(getattr(final_response, "model", "") or ""),
            finish_reason=str(
                getattr(final_response, "finish_reason", "") or "tool_calls"
            ),
            intent_category=intent_category,
            termination_reason="tool_unavailable_final",
            tool_calls_sig=final_sig,
            batch=batch,
            tool_calls_count=len(response.tool_calls or []),
            attempted_tools=attempted_tools,
            capability_fallback_trigger_reason=capability_fallback_trigger_reason,
            extra_metadata=dict(shared_capability_meta),
        ),
    )


__all__ = [
    "unavailable_discovery_or_version_message",
    "unavailable_discovery_phase_result",
    "unavailable_discovery_retry_instruction",
]
