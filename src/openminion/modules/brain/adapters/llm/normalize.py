import copy
import json
import logging
import re
from typing import Any


_LOGGER = logging.getLogger(__name__)

_PLAN_COMMAND_KEYS = {
    "args",
    "artifacts_expected",
    "command_id",
    "cwd",
    "depends_on",
    "description",
    "env",
    "expected_output",
    "id",
    "idempotency_key",
    "inputs",
    "kind",
    "model",
    "order",
    "output_key",
    "parameters",
    "params",
    "prompt",
    "question",
    "requires_confirmation",
    "risk_level",
    "status",
    "step_id",
    "step_number",
    "sub_intent_ids",
    "success_criteria",
    "timeout_ms",
    "title",
    "tool_name",
}
_DECISION_PAYLOAD_KEYS = {
    "answer",
    "act_profile",
    "clarify_context",
    "confidence",
    "execution_target",
    "max_steps_hint",
    "mode",
    "pending_turn_context",
    "question",
    "rationale",
    "reason_code",
    "route",
    "sub_intents",
    "subtasks",
}
_HIDDEN_THINK_BLOCK_RE = re.compile(
    r"<think>[\s\S]*?</think>",
    re.IGNORECASE,
)


def _deserialize_structured_payload(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    candidate = str(value or "").strip()
    if not candidate or candidate[0] not in "[{":
        return value
    try:
        return json.loads(candidate)
    except Exception:
        return value


def _is_empty_command_args(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, dict | list | tuple | set):
        return not value
    if isinstance(value, str):
        return not value.strip()
    return False


def _normalize_tool_command_payload(
    command_payload: Any,
    *,
    field_prefix: str,
) -> tuple[Any, list[str], list[str]]:
    if not isinstance(command_payload, dict):
        return command_payload, [], []

    normalized = dict(command_payload)
    normalized_fields: list[str] = []
    conflicts: list[str] = []

    kind = str(normalized.get("kind", "") or "").strip().lower()
    tool_name = str(normalized.get("tool_name", "") or "").strip()
    recipient_name = str(normalized.get("recipient_name", "") or "").strip()
    if recipient_name:
        if not tool_name:
            normalized["tool_name"] = recipient_name
            normalized.pop("recipient_name", None)
            tool_name = recipient_name
            normalized_fields.extend(
                [f"{field_prefix}.tool_name", f"{field_prefix}.recipient_name_removed"]
            )
        elif tool_name == recipient_name:
            normalized.pop("recipient_name", None)
            normalized_fields.append(f"{field_prefix}.recipient_name_removed")
        else:
            conflicts.append(f"{field_prefix}.recipient_name_conflict")
    if not kind and tool_name:
        normalized["kind"] = "tool"
        normalized_fields.append(f"{field_prefix}.kind")

    if "parameters" in normalized:
        parameters = normalized.get("parameters")
        if "args" not in normalized:
            normalized["args"] = copy.deepcopy(parameters)
            normalized.pop("parameters", None)
            normalized_fields.append(f"{field_prefix}.args")
        elif _is_empty_command_args(
            normalized.get("args")
        ) and not _is_empty_command_args(parameters):
            normalized["args"] = copy.deepcopy(parameters)
            normalized.pop("parameters", None)
            normalized_fields.extend(
                [
                    f"{field_prefix}.args",
                    f"{field_prefix}.parameters_replaced_empty_args",
                ]
            )
        elif normalized.get("args") == parameters:
            normalized.pop("parameters", None)
            normalized_fields.append(f"{field_prefix}.parameters_removed")
        else:
            conflicts.append(f"{field_prefix}.parameters_conflict")

    if "params" in normalized:
        params = normalized.get("params")
        if "args" not in normalized:
            normalized["args"] = copy.deepcopy(params)
            normalized.pop("params", None)
            normalized_fields.append(f"{field_prefix}.args")
        elif _is_empty_command_args(
            normalized.get("args")
        ) and not _is_empty_command_args(params):
            normalized["args"] = copy.deepcopy(params)
            normalized.pop("params", None)
            normalized_fields.extend(
                [f"{field_prefix}.args", f"{field_prefix}.params_replaced_empty_args"]
            )
        elif normalized.get("args") == params:
            normalized.pop("params", None)
            normalized_fields.append(f"{field_prefix}.params_removed")
        else:
            conflicts.append(f"{field_prefix}.params_conflict")

    if "inputs" in normalized:
        inputs = normalized.get("inputs")
        if "args" not in normalized and isinstance(inputs, dict):
            normalized["args"] = copy.deepcopy(inputs)
            normalized.pop("inputs", None)
            normalized_fields.extend(
                [f"{field_prefix}.args", f"{field_prefix}.inputs_removed"]
            )
        elif (
            _is_empty_command_args(normalized.get("args"))
            and isinstance(inputs, dict)
            and inputs
        ):
            normalized["args"] = copy.deepcopy(inputs)
            normalized.pop("inputs", None)
            normalized_fields.extend(
                [f"{field_prefix}.args", f"{field_prefix}.inputs_replaced_empty_args"]
            )
        elif normalized.get("args") == inputs:
            normalized.pop("inputs", None)
            normalized_fields.append(f"{field_prefix}.inputs_removed")
        elif isinstance(inputs, dict) and isinstance(normalized.get("args"), dict):
            merged = {
                **copy.deepcopy(inputs),
                **copy.deepcopy(normalized.get("args", {})),
            }
            normalized["args"] = merged
            normalized.pop("inputs", None)
            normalized_fields.extend(
                [
                    f"{field_prefix}.args",
                    f"{field_prefix}.inputs_merged_into_args",
                ]
            )
        else:
            conflicts.append(f"{field_prefix}.inputs_conflict")

    args_value = normalized.get("args")
    if isinstance(args_value, dict):
        nested_arguments = args_value.get("arguments")
        extra_arg_keys = {
            str(key) for key in args_value.keys() if str(key) != "arguments"
        }
        if isinstance(nested_arguments, dict) and not extra_arg_keys:
            normalized["args"] = copy.deepcopy(nested_arguments)
            normalized_fields.extend(
                [f"{field_prefix}.args", f"{field_prefix}.arguments_unwrapped"]
            )

    if tool_name and not str(normalized.get("title", "") or "").strip():
        normalized["title"] = f"Tool call: {tool_name}"
        normalized_fields.append(f"{field_prefix}.title")

    return normalized, normalized_fields, conflicts


def _normalize_agent_command_payload(
    command_payload: Any,
    *,
    field_prefix: str,
) -> tuple[Any, list[str], list[str]]:
    if not isinstance(command_payload, dict):
        return command_payload, [], []

    normalized = dict(command_payload)
    normalized_fields: list[str] = []
    conflicts: list[str] = []

    kind = str(normalized.get("kind", "") or "").strip().lower()
    target_agent_id = str(normalized.get("target_agent_id", "") or "").strip()
    method = str(normalized.get("method", "") or "").strip()

    if not kind and (target_agent_id or method):
        normalized["kind"] = "agent"
        normalized_fields.append(f"{field_prefix}.kind")

    if "arguments" in normalized and "params" not in normalized:
        arguments = normalized.get("arguments")
        if isinstance(arguments, dict):
            normalized["params"] = copy.deepcopy(arguments)
            normalized.pop("arguments", None)
            normalized_fields.extend(
                [f"{field_prefix}.params", f"{field_prefix}.arguments_removed"]
            )

    if "args" in normalized:
        args = normalized.get("args")
        if "params" not in normalized and isinstance(args, dict):
            normalized["params"] = copy.deepcopy(args)
            normalized.pop("args", None)
            normalized_fields.extend(
                [f"{field_prefix}.params", f"{field_prefix}.args_removed"]
            )
        elif normalized.get("params") == args:
            normalized.pop("args", None)
            normalized_fields.append(f"{field_prefix}.args_removed")
        else:
            conflicts.append(f"{field_prefix}.args_conflict")

    if "inputs" in normalized:
        inputs = normalized.get("inputs")
        if "params" not in normalized and isinstance(inputs, dict):
            normalized["params"] = copy.deepcopy(inputs)
            normalized.pop("inputs", None)
            normalized_fields.extend(
                [f"{field_prefix}.params", f"{field_prefix}.inputs_removed"]
            )
        elif (
            _is_empty_command_args(normalized.get("params"))
            and isinstance(inputs, dict)
            and inputs
        ):
            normalized["params"] = copy.deepcopy(inputs)
            normalized.pop("inputs", None)
            normalized_fields.extend(
                [
                    f"{field_prefix}.params",
                    f"{field_prefix}.inputs_replaced_empty_params",
                ]
            )
        elif normalized.get("params") == inputs:
            normalized.pop("inputs", None)
            normalized_fields.append(f"{field_prefix}.inputs_removed")
        elif isinstance(inputs, dict) and isinstance(normalized.get("params"), dict):
            merged = {
                **copy.deepcopy(inputs),
                **copy.deepcopy(normalized.get("params", {})),
            }
            normalized["params"] = merged
            normalized.pop("inputs", None)
            normalized_fields.extend(
                [
                    f"{field_prefix}.params",
                    f"{field_prefix}.inputs_merged_into_params",
                ]
            )
        else:
            conflicts.append(f"{field_prefix}.inputs_conflict")

    params_value = normalized.get("params")
    if isinstance(params_value, dict):
        nested_arguments = params_value.get("arguments")
        extra_param_keys = {
            str(key) for key in params_value.keys() if str(key) != "arguments"
        }
        if isinstance(nested_arguments, dict) and not extra_param_keys:
            normalized["params"] = copy.deepcopy(nested_arguments)
            normalized_fields.extend(
                [f"{field_prefix}.params", f"{field_prefix}.arguments_unwrapped"]
            )

    target_agent_id = str(normalized.get("target_agent_id", "") or "").strip()
    method = str(normalized.get("method", "") or "").strip()
    if (
        target_agent_id
        and method
        and not str(normalized.get("title", "") or "").strip()
    ):
        normalized["title"] = f"A2A call: {target_agent_id}.{method}"
        normalized_fields.append(f"{field_prefix}.title")

    return normalized, normalized_fields, conflicts


def _normalize_decision_subtasks_payload(
    subtasks: Any,
) -> tuple[Any, list[str], list[str]]:
    if not isinstance(subtasks, list):
        return subtasks, [], []

    normalized_subtasks: list[Any] = []
    normalized_fields: list[str] = []
    conflicts: list[str] = []
    changed = False
    for index, item in enumerate(subtasks):
        normalized_item = item
        item_fields: list[str] = []
        item_conflicts: list[str] = []
        if isinstance(item, dict):
            normalized_item = copy.deepcopy(item)
            kind = str(normalized_item.get("kind", "") or "").strip().lower()
            if (
                kind == "tool"
                or str(normalized_item.get("tool_name", "") or "").strip()
            ):
                normalized_item, item_fields, item_conflicts = (
                    _normalize_tool_command_payload(
                        normalized_item,
                        field_prefix=f"subtasks[{index}]",
                    )
                )
            elif (
                kind == "agent"
                or str(normalized_item.get("target_agent_id", "") or "").strip()
            ):
                normalized_item, item_fields, item_conflicts = (
                    _normalize_agent_command_payload(
                        normalized_item,
                        field_prefix=f"subtasks[{index}]",
                    )
                )
        normalized_subtasks.append(normalized_item)
        if item_fields or item_conflicts or normalized_item is not item:
            changed = True
        normalized_fields.extend(item_fields)
        conflicts.extend(item_conflicts)

    if not changed:
        return subtasks, normalized_fields, conflicts
    return normalized_subtasks, normalized_fields, conflicts


def _normalize_hidden_think_blocks(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    stripped = _HIDDEN_THINK_BLOCK_RE.sub("", value).strip()
    return stripped


def _normalize_decision_submit_output_payload(
    payload: Any,
    *,
    response: Any,
    return_debug: bool = False,
) -> Any:
    if not isinstance(payload, dict):
        if return_debug:
            return payload, {"normalized_fields": [], "conflicts": []}
        return payload

    normalized = copy.deepcopy(payload)
    del response
    normalized_fields: list[str] = []
    conflicts: list[str] = []

    if "response" in normalized and "answer" not in normalized:
        answer = _normalize_hidden_think_blocks(
            _deserialize_structured_payload(normalized.get("response"))
        )
        if isinstance(answer, str):
            normalized["answer"] = answer
            normalized_fields.extend(["answer", "response_removed"])
            normalized.pop("response", None)

    for field_name in ("answer", "question", "rationale"):
        if field_name in normalized:
            cleaned = _normalize_hidden_think_blocks(normalized.get(field_name))
            if cleaned != normalized.get(field_name):
                normalized[field_name] = cleaned
                normalized_fields.append(field_name)

    subtasks = normalized.get("subtasks")
    normalized_subtasks, subtask_fields, subtask_conflicts = (
        _normalize_decision_subtasks_payload(subtasks)
    )
    if subtask_fields or subtask_conflicts or normalized_subtasks is not subtasks:
        normalized["subtasks"] = normalized_subtasks
        normalized_fields.extend(subtask_fields)
        conflicts.extend(subtask_conflicts)

    if return_debug:
        return normalized, {
            "normalized_fields": normalized_fields,
            "conflicts": conflicts,
        }
    return normalized


def _normalize_act_submit_output_payload(
    payload: Any,
    *,
    response: Any,
    return_debug: bool = False,
) -> Any:
    if not isinstance(payload, dict):
        if return_debug:
            return payload, {"normalized_fields": [], "conflicts": []}
        return payload

    bridged_payload = {"route": "act", **copy.deepcopy(payload)}
    normalized = _normalize_decision_submit_output_payload(
        bridged_payload,
        response=response,
        return_debug=return_debug,
    )
    if return_debug:
        normalized_payload, debug = normalized
        if isinstance(normalized_payload, dict):
            normalized_payload = dict(normalized_payload)
            normalized_payload.pop("route", None)
        return normalized_payload, debug
    if isinstance(normalized, dict):
        normalized = dict(normalized)
        normalized.pop("route", None)
    return normalized


def _normalize_plan_step_payload(
    step_payload: Any,
    *,
    field_prefix: str,
) -> tuple[Any, list[str], list[str]]:
    if not isinstance(step_payload, dict):
        return step_payload, [], []

    normalized = copy.deepcopy(step_payload)
    normalized_fields: list[str] = []
    conflicts: list[str] = []

    raw_kind = str(normalized.get("kind", "") or "").strip()
    kind_lower = raw_kind.lower()

    if kind_lower in {"finishcommand", "finish_command"}:
        normalized["kind"] = "finish"
        normalized_fields.append(f"{field_prefix}.kind")
    elif kind_lower and kind_lower not in {
        "agent",
        "ask_user",
        "finish",
        "think",
        "tool",
    }:
        if not str(normalized.get("tool_name", "") or "").strip():
            normalized["tool_name"] = raw_kind
            normalized_fields.append(f"{field_prefix}.tool_name")
        normalized["kind"] = "tool"
        normalized_fields.append(f"{field_prefix}.kind")

    if str(normalized.get("kind", "") or "").strip().lower() == "tool":
        if "args" not in normalized:
            lifted_args = {
                key: copy.deepcopy(value)
                for key, value in normalized.items()
                if key not in _PLAN_COMMAND_KEYS
            }
            if lifted_args:
                normalized["args"] = lifted_args
                normalized_fields.append(f"{field_prefix}.args")
                for key in lifted_args:
                    normalized.pop(key, None)
                    normalized_fields.append(f"{field_prefix}.{key}_moved")
        normalized, tool_fields, tool_conflicts = _normalize_tool_command_payload(
            normalized,
            field_prefix=field_prefix,
        )
        normalized_fields.extend(tool_fields)
        conflicts.extend(tool_conflicts)

    return normalized, normalized_fields, conflicts


def _normalize_plan_submit_output_payload(
    payload: Any,
    *,
    response: Any,
    return_debug: bool = False,
) -> Any:
    if not isinstance(payload, dict):
        if return_debug:
            return payload, {"normalized_fields": [], "conflicts": []}
        return payload

    normalized = copy.deepcopy(payload)
    normalized_fields: list[str] = []
    conflicts: list[str] = []

    nested_plan = normalized.get("plan")
    if isinstance(nested_plan, dict):
        unwrapped = copy.deepcopy(nested_plan)
        if "sub_intents" not in unwrapped and isinstance(
            normalized.get("sub_intents"), list
        ):
            unwrapped["sub_intents"] = copy.deepcopy(normalized["sub_intents"])
            normalized_fields.append("sub_intents.moved_from_wrapper")
        normalized = unwrapped
        normalized_fields.append("plan_unwrapped")

    steps = normalized.get("steps")
    if isinstance(steps, str):
        try:
            parsed_steps = json.loads(steps)
        except Exception:
            parsed_steps = None
        if isinstance(parsed_steps, list):
            normalized["steps"] = parsed_steps
            steps = parsed_steps
            normalized_fields.append("steps.parsed_json_string")

    if isinstance(steps, list):
        normalized_steps: list[Any] = []
        steps_changed = False
        for index, item in enumerate(steps):
            normalized_item, item_fields, item_conflicts = _normalize_plan_step_payload(
                item,
                field_prefix=f"steps[{index}]",
            )
            normalized_steps.append(normalized_item)
            if item_fields or item_conflicts:
                steps_changed = True
            normalized_fields.extend(item_fields)
            conflicts.extend(item_conflicts)
        if steps_changed:
            normalized["steps"] = normalized_steps

    if normalized_fields or conflicts:
        _LOGGER.warning(
            "structured.submit_output_normalized schema=%s provider=%s model=%s session_id=%s normalized_fields=%s conflicts=%s",
            "Plan",
            str(getattr(response, "provider", "") or ""),
            str(getattr(response, "model", "") or ""),
            str(getattr(response, "session_id", "") or ""),
            ",".join(normalized_fields) or "-",
            ",".join(conflicts) or "-",
        )

    if return_debug:
        return normalized, {
            "normalized_fields": list(normalized_fields),
            "conflicts": list(conflicts),
        }
    return normalized


__all__ = [
    "_normalize_act_submit_output_payload",
    "_normalize_decision_submit_output_payload",
    "_normalize_plan_submit_output_payload",
]
