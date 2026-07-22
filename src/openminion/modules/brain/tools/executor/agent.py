from typing import Any

from ...constants import (
    BRAIN_ACTION_STATUS_FAILED,
    BRAIN_COMMAND_KIND_AGENT,
)
from ...schemas import (
    ActionError,
    ActionResult,
    JobHandle,
    ThinkCommand,
    ToolCommand,
    WorkingState,
    new_uuid,
)
from ..parser import normalize_tool_name_for_brain


def _is_local_self_agent_command(
    runner: Any,
    *,
    state: WorkingState,
    command: Any,
) -> bool:
    if getattr(command, "kind", "") != BRAIN_COMMAND_KIND_AGENT:
        return False
    target_agent_id = str(getattr(command, "target_agent_id", "") or "").strip().lower()
    if not target_agent_id:
        return False
    local_ids = {
        str(getattr(state, "agent_id", "") or "").strip().lower(),
        str(getattr(getattr(runner, "profile", None), "agent_id", "") or "")
        .strip()
        .lower(),
        "self",
        "local",
        "current",
    }
    return target_agent_id in {item for item in local_ids if item}


def _local_self_respond_prompt(*, state: WorkingState, command: Any) -> str:
    params = getattr(command, "params", {}) or {}
    inputs = getattr(command, "inputs", {}) or {}
    source_request = ""
    for candidate in (
        inputs.get("user_input"),
        params.get("user_input"),
        params.get("message"),
        params.get("text"),
        params.get("prompt"),
        getattr(state, "goal", ""),
    ):
        text = str(candidate or "").strip()
        if text:
            source_request = text
            break
    title = str(getattr(command, "title", "") or "").strip() or "Respond to user"
    if source_request:
        return (
            "Respond directly to the user's message in one assistant reply. "
            "Do not mention internal execution, transport, budgets, or tools unless "
            "the user asked about them.\n"
            f"User message: {source_request}\n"
            f"Execution intent: {title}"
        )
    return (
        "Respond directly to the user's message in one assistant reply. "
        "Do not mention internal execution, transport, budgets, or tools unless "
        "the user asked about them.\n"
        f"Execution intent: {title}"
    )


def _resolve_local_tool_name(
    runner: Any,
    *,
    method: str,
) -> str:
    normalized = normalize_tool_name_for_brain(method)
    if normalized:
        return normalized
    available_tool_names = set()
    tool_name_provider = getattr(runner, "_available_tool_names", None)
    if callable(tool_name_provider):
        try:
            available_tool_names = {
                str(item or "").strip()
                for item in list(tool_name_provider())
                if str(item or "").strip()
            }
        except Exception:  # pragma: no cover
            available_tool_names = set()
    if method in available_tool_names:
        return method
    tool_api = getattr(runner, "tool_api", None)
    list_tools = getattr(tool_api, "list_tools", None)
    if callable(list_tools):
        try:
            runtime_names = {
                str(item.get("name", "")).strip()
                for item in list_tools()
                if isinstance(item, dict) and str(item.get("name", "")).strip()
            }
        except Exception:  # pragma: no cover
            runtime_names = set()
        if method in runtime_names:
            return method
    return ""


def _execute_local_self_agent_command(
    runner: Any,
    *,
    state: WorkingState,
    command: Any,
    logger: Any,
    execute_action_fn,
) -> tuple[ActionResult, JobHandle | None]:
    method = str(getattr(command, "method", "") or "").strip()
    local_tool_name = _resolve_local_tool_name(runner, method=method)
    if local_tool_name:
        tool_command = _local_tool_command_from_agent(
            command,
            local_tool_name=local_tool_name,
        )
        _emit_local_agent_rewrite(
            logger=logger,
            state=state,
            command=command,
            method=method,
            rewritten_kind="tool",
            tool_name=local_tool_name,
        )
        return execute_action_fn(
            runner,
            state=state,
            command=tool_command,
            logger=logger,
        )

    if method.lower() == "respond":
        think_command = _local_think_command_from_agent(state=state, command=command)
        _emit_local_agent_rewrite(
            logger=logger,
            state=state,
            command=command,
            method=method,
            rewritten_kind="think",
        )
        return execute_action_fn(
            runner,
            state=state,
            command=think_command,
            logger=logger,
        )

    result = _unsupported_local_agent_method_result(command=command, method=method)
    runner._remember_idempotency(state=state, command=command, result=result)
    return result, None


def _local_tool_command_from_agent(command: Any, *, local_tool_name: str) -> ToolCommand:
    return ToolCommand(
        command_id=str(getattr(command, "command_id", "") or new_uuid()),
        title=str(getattr(command, "title", "") or f"Tool call: {local_tool_name}"),
        tool_name=local_tool_name,
        args=_dict_attr(command, "params"),
        inputs=_dict_attr(command, "inputs"),
        success_criteria=_dict_attr(command, "success_criteria"),
        fallback=getattr(command, "fallback", None),
        risk_level=str(getattr(command, "risk_level", "low") or "low"),
        requires_confirmation=bool(getattr(command, "requires_confirmation", False)),
        idempotency_key=str(getattr(command, "idempotency_key", "") or new_uuid()),
        timeout_ms=getattr(command, "timeout_ms", None),
        sub_intent_ids=list(getattr(command, "sub_intent_ids", []) or []),
    )


def _local_think_command_from_agent(
    *,
    state: WorkingState,
    command: Any,
) -> ThinkCommand:
    return ThinkCommand(
        command_id=str(getattr(command, "command_id", "") or new_uuid()),
        title=str(getattr(command, "title", "") or "Respond to user"),
        prompt=_local_self_respond_prompt(state=state, command=command),
        output_key="local_self_respond",
        idempotency_key=str(getattr(command, "idempotency_key", "") or new_uuid()),
        success_criteria=_dict_attr(command, "success_criteria"),
        fallback=getattr(command, "fallback", None),
        risk_level=str(getattr(command, "risk_level", "low") or "low"),
        requires_confirmation=bool(getattr(command, "requires_confirmation", False)),
        timeout_ms=getattr(command, "timeout_ms", None),
        sub_intent_ids=list(getattr(command, "sub_intent_ids", []) or []),
    )


def _emit_local_agent_rewrite(
    *,
    logger: Any,
    state: WorkingState,
    command: Any,
    method: str,
    rewritten_kind: str,
    tool_name: str = "",
) -> None:
    payload = {
        "source_kind": "agent",
        "target_agent_id": str(getattr(command, "target_agent_id", "") or ""),
        "method": method,
        "rewritten_kind": rewritten_kind,
    }
    if tool_name:
        payload["tool_name"] = tool_name
    logger.emit("brain.local_agent.rewrite", payload, trace_id=state.trace_id)


def _unsupported_local_agent_method_result(
    *,
    command: Any,
    method: str,
) -> ActionResult:
    return ActionResult(
        command_id=getattr(command, "command_id", "") or new_uuid(),
        status=BRAIN_ACTION_STATUS_FAILED,
        summary=(
            f"Unsupported same-agent command: "
            f"{str(getattr(command, 'target_agent_id', '') or '').strip()}.{method}"
        ),
        error=ActionError(
            code="LOCAL_SELF_AGENT_METHOD_UNSUPPORTED",
            message=(
                "Same-agent agent commands must be rewritten to a local tool or "
                "respond path before execution."
            ),
            details={
                "reason_code": "local_self_agent_method_unsupported",
                "method": method,
                "target_agent_id": str(
                    getattr(command, "target_agent_id", "") or ""
                ).strip(),
            },
        ),
    )


def _dict_attr(command: Any, name: str) -> dict[str, Any]:
    value = getattr(command, name, None)
    return dict(value) if isinstance(value, dict) else {}


__all__ = [
    "_execute_local_self_agent_command",
    "_is_local_self_agent_command",
    "_local_self_respond_prompt",
    "_resolve_local_tool_name",
]
