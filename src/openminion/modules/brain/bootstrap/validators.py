from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Iterable

from ..schemas import Command, Decision, Plan, WorkingState, to_structured_sub_intents
from ..tools.capabilities import (
    command_capabilities,
    coverage_missing_sub_intents,
    covered_sub_intent_signals,
    is_known_capability_signal,
)


@dataclass(frozen=True)
class DecisionValidationFailure:
    code: str
    message: str
    details: dict[str, Any]


_SUCCESS_CRITERIA_IGNORED_KEYS = {
    "status",
    "final_status",
    "received_clarification",
    "clarification_received",
    "confirmation_received",
    "user_acknowledged",
}

_CAPABILITY_OUTPUT_FIELDS: dict[str, tuple[str, ...]] = {
    "start_browser": ("status", "instance_id"),
    "navigate_to_url": ("status", "url", "final_url"),
    "capture_screenshot": ("status", "artifact_refs", "image_path"),
    "write_file": ("status", "path", "bytes_written"),
    "create_file": ("status", "path"),
    "read_file": ("status", "content", "path"),
    "verify_file": ("status", "path", "exists"),
    "list_files": ("status", "entries", "path"),
    "find_files": ("status", "matches", "path"),
    "start_shell": ("status", "task_id", "stdout", "stderr"),
    "run_command": ("status", "task_id", "stdout", "stderr", "exit_code"),
    "inspect_command_status": ("status", "task_id", "stdout", "stderr", "exit_code"),
    "stop_command": ("status", "task_id"),
}

_KNOWN_COMMAND_OUTPUT_FIELDS: set[str] = {
    field_name
    for field_names in _CAPABILITY_OUTPUT_FIELDS.values()
    for field_name in field_names
}


def validate_sub_intent_coverage(
    *,
    decision: Decision,
    commands: Iterable[Command],
) -> DecisionValidationFailure | None:
    if not decision.sub_intents:
        return None
    command_list = list(commands)
    missing: set[str]
    try:
        structured_sub_intents = to_structured_sub_intents(decision.sub_intents)
    except Exception:
        structured_sub_intents = []
    if structured_sub_intents:
        covered = covered_sub_intent_signals(command_list)
        missing = set()
        for item in structured_sub_intents:
            description = str(item.description or "").strip()
            if not description:
                continue
            if is_known_capability_signal(description):
                if description not in covered:
                    missing.add(description)
                continue
            if description not in covered and item.id not in covered:
                missing.add(description)
    else:
        missing = coverage_missing_sub_intents(
            sub_intents=decision.sub_intents,
            commands=command_list,
        )
    if not missing:
        return None
    missing_sorted = sorted(missing)
    return DecisionValidationFailure(
        code="sub_intent_not_covered",
        message=(
            "Declared sub-intents are not covered by emitted commands: "
            + ", ".join(missing_sorted)
        ),
        details={
            "missing_sub_intents": missing_sorted,
            "declared_sub_intents": list(decision.sub_intents),
            "command_count": len(command_list),
        },
    )


def _command_output_fields(command: Command) -> set[str]:
    fields = {"status"}
    if command.kind != "tool":
        return fields
    for capability in command_capabilities(command):
        fields.update(_CAPABILITY_OUTPUT_FIELDS.get(capability, ()))
    if isinstance(command.success_criteria, dict):
        for key in command.success_criteria.keys():
            key_text = str(key or "").strip()
            if key_text:
                fields.add(key_text)
    return fields


def validate_success_criteria_coverage(
    *,
    plan: Plan | None,
    commands: Iterable[Command],
) -> DecisionValidationFailure | None:
    if plan is None or not isinstance(plan.success_criteria, dict):
        return None
    criteria_keys = [
        str(key or "").strip()
        for key in plan.success_criteria.keys()
        if str(key or "").strip()
    ]
    criteria_keys = [
        key for key in criteria_keys if key not in _SUCCESS_CRITERIA_IGNORED_KEYS
    ]
    if not criteria_keys:
        return None
    available_fields: set[str] = set()
    for command in commands:
        available_fields.update(_command_output_fields(command))
    explicit_output_keys = [
        key for key in criteria_keys if key in _KNOWN_COMMAND_OUTPUT_FIELDS
    ]
    if not explicit_output_keys:
        return None
    missing = sorted(key for key in explicit_output_keys if key not in available_fields)
    if not missing:
        return None
    return DecisionValidationFailure(
        code="success_criteria_not_producible",
        message=(
            "Plan success_criteria contains keys with no producing command output: "
            + ", ".join(missing)
        ),
        details={
            "missing_success_criteria_keys": missing,
            "available_output_fields": sorted(available_fields),
        },
    )


def _stable_json_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _stable_json_value(val)
            for key, val in sorted(value.items(), key=lambda item: str(item[0]))
        }
    if isinstance(value, (list, tuple)):
        return [_stable_json_value(item) for item in value]
    return value


def semantic_command_signature(command: Command) -> str:
    payload: dict[str, Any] = {"kind": command.kind}
    if command.kind == "tool":
        payload.update(
            {
                "tool_name": command.tool_name,
                "args": _stable_json_value(command.args),
                "cwd": command.cwd,
                "env": _stable_json_value(command.env),
            }
        )
    elif command.kind == "agent":
        payload.update(
            {
                "target_agent_id": command.target_agent_id,
                "method": command.method,
                "params": _stable_json_value(command.params),
                "expect_async": command.expect_async,
            }
        )
    elif command.kind == "think":
        payload.update(
            {
                "prompt": command.prompt,
                "output_key": command.output_key,
                "model": command.model,
            }
        )
    elif command.kind == "ask_user":
        payload.update(
            {
                "question": command.question,
                "options": _stable_json_value(command.options),
            }
        )
    elif command.kind == "finish":
        payload.update(
            {
                "final_message": command.final_message,
                "final_artifact_refs": _stable_json_value(command.final_artifact_refs),
            }
        )
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def semantic_command_sequence_signature(commands: Iterable[Command]) -> str:
    return json.dumps(
        [json.loads(semantic_command_signature(command)) for command in commands],
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )


def validate_continuation_progress(
    *,
    state: WorkingState,
    user_input: str | None,
    commands: Iterable[Command],
) -> DecisionValidationFailure | None:
    if str(user_input or "").strip():
        return None
    prior_signature = str(
        getattr(state, "continuation_guard_command_signature", "") or ""
    ).strip()
    if not prior_signature:
        return None
    command_list = list(commands)
    if not command_list:
        return None
    if semantic_command_sequence_signature(command_list) != prior_signature:
        return None
    reason = str(getattr(state, "continuation_guard_reason", "") or "").strip()
    message = (
        "Continuation after a completed step must make forward progress instead of "
        "repeating the exact same command sequence."
    )
    if reason:
        message = f"{message} Closure guidance: {reason}"
    return DecisionValidationFailure(
        code="repeated_continuation_command",
        message=message + " Choose a distinct action or close the turn.",
        details={
            "command_count": len(command_list),
            "command_titles": [
                str(getattr(command, "title", "") or "").strip()
                for command in command_list
            ],
            "reason": reason,
        },
    )
