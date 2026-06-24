import json
from typing import TYPE_CHECKING, Any, Callable, cast

from openminion.modules.tool.dispatch import _get_registry_manager, get_registry

from ...constants import (
    BRAIN_COMMAND_KIND_AGENT,
    BRAIN_COMMAND_KIND_ASK_USER,
    BRAIN_COMMAND_KIND_FINISH,
    BRAIN_COMMAND_KIND_TOOL,
)
from ...schemas.base import new_uuid
from ...schemas.commands import AgentCommand, ToolCommand
from ...schemas.readiness import find_unknown_sentinel_path
from ...schemas.state import WorkingState

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ...runner import BrainRunner


def normalize_tool_name_for_brain(raw_name: str) -> str | None:
    """Normalize model-facing tool names for brain-owned paths only."""

    token = str(raw_name or "").strip()
    if not token:
        return None

    mgr = _get_registry_manager()
    normalized = mgr.normalize_model_input_name(token)
    if normalized:
        return normalized

    registry = get_registry()
    if registry is None:
        return None
    tool = registry.list().get(token)
    if tool is not None and bool(getattr(tool, "prompt_visible_runtime_name", False)):
        return token
    return None


def _placeholder_message(*, path: str, command_kind: str) -> str:
    field_path = path.split(".", 1)[1] if "." in path else path
    field_hint = field_path.replace("[", " [")
    return (
        f"Missing `{field_hint}` is required before this {command_kind} "
        "command can run."
    )


def _parse_optional_json_payload(payload: str) -> dict[str, Any]:
    try:
        maybe = json.loads(payload)
    except json.JSONDecodeError:
        return {"raw": payload}
    return maybe if isinstance(maybe, dict) else {"value": maybe}


def _command_idempotency_key(
    *, runner: "BrainRunner", state: WorkingState, text: str
) -> str:
    key_builder = cast(Callable[..., str], getattr(runner, "_idempotency_key"))
    return key_builder(
        session_id=state.session_id,
        trace_id=state.trace_id or "",
        text=text,
    )


def parse_tool_command(
    *, runner: "BrainRunner", state: WorkingState, text: str
) -> ToolCommand | None:
    parts = text.strip().split(maxsplit=2)
    if len(parts) < 2 or parts[0].lower() != "tool":
        return None
    raw_tool_name = parts[1]
    tool_name = normalize_tool_name_for_brain(raw_tool_name) or raw_tool_name
    args: dict[str, Any] = {}
    if len(parts) == 3 and parts[2].strip():
        args = _parse_optional_json_payload(parts[2].strip())
    return ToolCommand(
        kind="tool",
        title=f"Tool call: {tool_name}",
        tool_name=tool_name,
        args=args,
        success_criteria={"status": "success"},
        idempotency_key=_command_idempotency_key(
            runner=runner,
            state=state,
            text=text,
        ),
        risk_level="low",
    )


def parse_agent_command(
    *, runner: "BrainRunner", state: WorkingState, text: str
) -> AgentCommand | None:
    parts = text.strip().split(maxsplit=3)
    if len(parts) < 3 or parts[0].lower() != "agent":
        return None
    target = parts[1]
    method = parts[2]
    params: dict[str, Any] = {}
    if len(parts) == 4 and parts[3].strip():
        params = _parse_optional_json_payload(parts[3].strip())
    return AgentCommand(
        kind="agent",
        title=f"A2A call: {target}.{method}",
        target_agent_id=target,
        method=method,
        params=params,
        success_criteria={"status": "success"},
        idempotency_key=_command_idempotency_key(
            runner=runner,
            state=state,
            text=text,
        ),
        risk_level="med",
    )


def normalize_command_payload(
    payload: Any,
    *,
    allow_runtime_tool_names: set[str] | None = None,
) -> Any:
    if not isinstance(payload, dict):
        return payload

    normalized = dict(payload)
    kind = str(normalized.get("kind", "")).strip().lower()
    if not kind:
        if str(normalized.get("tool_name", "")).strip():
            kind = BRAIN_COMMAND_KIND_TOOL
        elif (
            str(normalized.get("target_agent_id", "")).strip()
            or str(normalized.get("method", "")).strip()
        ):
            kind = BRAIN_COMMAND_KIND_AGENT
        elif str(normalized.get("question", "")).strip():
            kind = BRAIN_COMMAND_KIND_ASK_USER
        elif str(normalized.get("final_message", "")).strip():
            kind = BRAIN_COMMAND_KIND_FINISH
        else:
            normalized["kind"] = "invalid"
            normalized["error"] = {
                "code": "MISSING_COMMAND_KIND",
                "message": "kind is required when no typed command fields are present",
            }
            return normalized
        normalized["kind"] = kind

    if kind == BRAIN_COMMAND_KIND_TOOL:
        tool_name = str(normalized.get("tool_name", "")).strip()
        if not tool_name:
            normalized["kind"] = "invalid"
            normalized["error"] = {
                "code": "MISSING_TOOL_NAME",
                "message": "tool_name is required for tool commands",
            }
            return normalized
        canonical_tool_name = normalize_tool_name_for_brain(tool_name)
        if not canonical_tool_name:
            if tool_name and tool_name in (allow_runtime_tool_names or set()):
                canonical_tool_name = tool_name
            else:
                normalized["kind"] = "invalid"
                normalized["error"] = {
                    "code": "INVALID_TOOL_NAME",
                    "message": f"tool_name must be a canonical model-facing ID, got {tool_name!r}",
                }
                return normalized
        tool_name = canonical_tool_name
        normalized["tool_name"] = tool_name
        if not isinstance(normalized.get("args"), dict):
            for alias in ("inputs", "params"):
                if isinstance(normalized.get(alias), dict):
                    normalized["args"] = dict(normalized[alias])
                    break
        if not isinstance(normalized.get("args"), dict):
            normalized["args"] = {}
        unknown_arg_path = find_unknown_sentinel_path(
            normalized.get("args", {}) or {},
            prefix="args",
        )
        if unknown_arg_path:
            normalized["kind"] = "invalid"
            normalized["error"] = {
                "code": "UNRESOLVED_TOOL_ARGS",
                "message": _placeholder_message(
                    path=unknown_arg_path,
                    command_kind="tool",
                ),
            }
            return normalized
        if not str(normalized.get("title", "")).strip():
            normalized["title"] = f"Tool call: {tool_name}"
        if not str(normalized.get("idempotency_key", "")).strip():
            normalized["idempotency_key"] = new_uuid()

    elif kind == BRAIN_COMMAND_KIND_AGENT:
        target_agent_id = str(normalized.get("target_agent_id", "")).strip()
        method = str(normalized.get("method", "")).strip()
        # Provider structured-output retries sometimes reshape a local tool call
        if target_agent_id.lower() in {"system", "runtime", "tool-runtime"}:
            canonical_tool_name = normalize_tool_name_for_brain(method)
            if canonical_tool_name:
                tool_payload = dict(normalized)
                tool_payload["kind"] = BRAIN_COMMAND_KIND_TOOL
                tool_payload["tool_name"] = canonical_tool_name
                if not isinstance(tool_payload.get("args"), dict):
                    if isinstance(tool_payload.get("params"), dict):
                        tool_payload["args"] = dict(tool_payload["params"])
                    elif isinstance(tool_payload.get("inputs"), dict):
                        tool_payload["args"] = dict(tool_payload["inputs"])
                tool_payload.pop("target_agent_id", None)
                tool_payload.pop("method", None)
                tool_payload.pop("params", None)
                tool_payload.pop("expect_async", None)
                return normalize_command_payload(
                    tool_payload,
                    allow_runtime_tool_names=allow_runtime_tool_names,
                )

        if not target_agent_id:
            normalized["target_agent_id"] = "unknown-agent"
        if not method:
            normalized["method"] = "unknown_method"
        if not isinstance(normalized.get("params"), dict):
            normalized["params"] = {}
        unknown_params_path = find_unknown_sentinel_path(
            normalized.get("params", {}) or {},
            prefix="params",
        )
        if unknown_params_path:
            normalized["kind"] = "invalid"
            normalized["error"] = {
                "code": "UNRESOLVED_AGENT_PARAMS",
                "message": _placeholder_message(
                    path=unknown_params_path,
                    command_kind="agent",
                ),
            }
            return normalized
        if not str(normalized.get("title", "")).strip():
            normalized["title"] = (
                f"A2A call: {normalized['target_agent_id']}.{normalized['method']}"
            )
        if not str(normalized.get("idempotency_key", "")).strip():
            normalized["idempotency_key"] = new_uuid()

    elif kind == BRAIN_COMMAND_KIND_ASK_USER:
        question = str(normalized.get("question", "")).strip()
        if not question:
            question = "Please clarify your request."
        normalized["question"] = question
        if not str(normalized.get("title", "")).strip():
            normalized["title"] = "Clarification required"

    return normalized
