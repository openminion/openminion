from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from openminion.modules.brain.adapters.tool.permission_mode import (
    canonical_permission_mode,
    effective_permission_mode_for_tool,
    is_tool_blocked_by_readonly,
    request_outcome_allows_tool,
)
from openminion.modules.telemetry.trace.phase_timing import active_chat_phase

from ...diagnostics.events import CanonicalEventLogger
from ...config import TOOL_OUTCOME_SUCCESS_ALLOWLIST
from ...constants import (
    BRAIN_ACTION_STATUS_BLOCKED,
    BRAIN_ACTION_STATUS_FAILED,
    BRAIN_ACTION_STATUS_NEEDS_USER,
    BRAIN_ACTION_STATUS_SUCCESS,
    BRAIN_ACTION_STATUS_TIMEOUT,
    BRAIN_COMMAND_KIND_TOOL,
    TOOL_OUTCOME_STAGED_COUNT_KEY as _TOOL_OUTCOME_STAGED_COUNT_KEY,
)
from ...execution.skill_binding import activate_skill_for_command
from ...loop.tools.contracts import (
    CommandExecutionOutcome,
    PreparedToolDispatch,
    PrepareOutcome,
    RawToolResult,
    canonical_tool_arguments,
)
from ...schemas import (
    ActionError,
    ActionResult,
    Command,
    WorkingState,
)
from ..parser import normalize_tool_name_for_brain
from .arguments import (
    _JSON_SCHEMA_TOP_LEVEL_KEYS,
    _parameter_keys_from_spec_payload,
    _spec_like_payload,
    resolve_tool_spec_payload,
    sanitize_tool_command_args,
)
from .dispatch import _command_lineage_payload
from openminion.modules.brain.constants import STATE_KEY_MODULE_STATE
from openminion.base.constants import STATE_KEY_SOURCE_OUTCOME

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ...runner import BrainRunner


_TOOL_OUTCOME_RECORD_TYPE = "tool_outcome"
_TOOL_OUTCOME_MAX_STAGE_PER_TURN = 3
_TOOL_OUTCOME_STATE_KEY = "tool_outcome_memory"
_TOOL_OUTCOME_STAGED_COMMAND_IDS_KEY = "staged_command_ids"
_WATCH_STATE_KEY = "watch_subscription"
_WATCH_ACTION_TURN_KIND = "action"


def _watch_background_write_authorized(state: WorkingState) -> bool:
    module_state = getattr(state, STATE_KEY_MODULE_STATE, None)
    if not isinstance(module_state, dict):
        return False
    watch_state = module_state.get(_WATCH_STATE_KEY)
    if not isinstance(watch_state, dict):
        return False
    return (
        bool(watch_state.get("enabled", False))
        and str(watch_state.get("turn_kind", "") or "").strip().lower()
        == _WATCH_ACTION_TURN_KIND
        and bool(watch_state.get("write_authorized", False))
    )


def _prepare_outcome_disposition(command: Command) -> str:
    disposition = str(getattr(command, "disposition", "") or "").strip().lower()
    return disposition or "ask_user"


def _tool_family(tool_name: str) -> str:
    normalized = str(tool_name or "").strip().lower()
    if "." in normalized:
        return normalized.split(".", 1)[0]
    return normalized


def _tool_outcome_from_result(
    *,
    action_result: ActionResult | None,
    forced_outcome: str | None = None,
) -> str | None:
    if forced_outcome:
        normalized = str(forced_outcome).strip().lower()
        return normalized or None
    if action_result is None:
        return None
    status = str(getattr(action_result, "status", "") or "").strip().lower()
    if status == str(BRAIN_ACTION_STATUS_SUCCESS):
        return "success"
    if status == str(BRAIN_ACTION_STATUS_FAILED):
        return "failure"
    if status == str(BRAIN_ACTION_STATUS_TIMEOUT):
        return "timeout"
    if status in {
        str(BRAIN_ACTION_STATUS_NEEDS_USER),
        str(BRAIN_ACTION_STATUS_BLOCKED),
    }:
        return "policy_denied"
    return None


def _tool_outcome_turn_index(runner: "BrainRunner", *, state: WorkingState) -> int:
    session_api = getattr(runner, "session_api", None)
    list_turns = getattr(session_api, "list_turns", None)
    if callable(list_turns):
        try:
            return max(0, len(list_turns(state.session_id)))
        except Exception:
            return 0
    return 0


def _tool_outcome_should_stage(
    *,
    tool_name: str,
    outcome: str,
) -> bool:
    if outcome != "success":
        return True
    return str(tool_name or "").strip() in TOOL_OUTCOME_SUCCESS_ALLOWLIST


def _tool_outcome_args_signature(command: Command | None) -> str | None:
    if command is None:
        return None
    raw_args = getattr(command, "args", None)
    if not isinstance(raw_args, dict):
        return None
    try:
        signature = canonical_tool_arguments(dict(raw_args))
    except Exception:
        return None
    normalized = str(signature or "").strip()
    return normalized or None


def _stage_tool_outcome_candidate(
    runner: "BrainRunner",
    *,
    state: WorkingState,
    tool_name: str,
    action_result: ActionResult | None,
    command: Command | None,
    forced_outcome: str | None = None,
) -> str | None:
    memory_api = getattr(runner, "memory_api", None)
    normalized_tool_name = str(tool_name or "").strip()
    if memory_api is None or not normalized_tool_name:
        return None
    outcome = _staged_tool_outcome(
        tool_name=normalized_tool_name,
        action_result=action_result,
        forced_outcome=forced_outcome,
    )
    if outcome is None:
        return None

    module_state = state.module_state.setdefault(_TOOL_OUTCOME_STATE_KEY, {})
    staged_command_ids = _tool_outcome_staged_command_ids(module_state)
    command_id = _tool_outcome_command_id(command=command, action_result=action_result)
    if _tool_outcome_already_staged(command_id, staged_command_ids):
        return None

    staged_count = int(module_state.get(_TOOL_OUTCOME_STAGED_COUNT_KEY, 0) or 0)
    if staged_count >= _TOOL_OUTCOME_MAX_STAGE_PER_TURN:
        return None

    error_code = _tool_outcome_error_code(action_result)
    artifact_refs = _tool_outcome_artifact_refs(action_result)
    tool_family = _tool_family(normalized_tool_name)
    args_signature = _tool_outcome_args_signature(command)
    candidate_id = memory_api.stage_candidate(
        scope=f"agent:{runner.profile.agent_id}",
        record_type=_TOOL_OUTCOME_RECORD_TYPE,
        title=_tool_outcome_title(
            tool_name=normalized_tool_name,
            outcome=outcome,
            error_code=error_code,
        ),
        content=_tool_outcome_content(
            runner=runner,
            state=state,
            tool_name=normalized_tool_name,
            tool_family=tool_family,
            outcome=outcome,
            error_code=error_code,
            command=command,
            args_signature=args_signature,
            artifact_refs=artifact_refs,
        ),
        tags=_tool_outcome_tags(tool_family=tool_family, outcome=outcome),
        evidence_refs=artifact_refs or None,
        confidence=_tool_outcome_confidence(outcome),
        meta=_tool_outcome_meta(
            tool_name=normalized_tool_name,
            tool_family=tool_family,
            outcome=outcome,
            args_signature=args_signature,
            command_id=command_id,
        ),
    )
    state.memory_candidates.append(candidate_id)
    module_state[_TOOL_OUTCOME_STAGED_COUNT_KEY] = staged_count + 1
    if command_id:
        staged_command_ids.add(command_id)
        module_state[_TOOL_OUTCOME_STAGED_COMMAND_IDS_KEY] = sorted(staged_command_ids)
    return candidate_id


def _staged_tool_outcome(
    *,
    tool_name: str,
    action_result: ActionResult | None,
    forced_outcome: str | None,
) -> str | None:
    outcome = _tool_outcome_from_result(
        action_result=action_result,
        forced_outcome=forced_outcome,
    )
    if outcome is None:
        return None
    if not _tool_outcome_should_stage(tool_name=tool_name, outcome=outcome):
        return None
    return outcome


def _tool_outcome_staged_command_ids(module_state: dict[str, Any]) -> set[str]:
    return {
        str(item).strip()
        for item in list(module_state.get(_TOOL_OUTCOME_STAGED_COMMAND_IDS_KEY, []) or [])
        if str(item).strip()
    }


def _tool_outcome_command_id(
    *,
    command: Command | None,
    action_result: ActionResult | None,
) -> str:
    return str(
        getattr(command, "command_id", "")
        or getattr(action_result, "command_id", "")
        or ""
    ).strip()


def _tool_outcome_already_staged(command_id: str, staged_command_ids: set[str]) -> bool:
    return bool(command_id and command_id in staged_command_ids)


def _tool_outcome_error_code(action_result: ActionResult | None) -> str:
    return str(getattr(getattr(action_result, "error", None), "code", "") or "").strip()


def _tool_outcome_artifact_refs(action_result: ActionResult | None) -> list[str]:
    return [
        str(getattr(ref, "ref", "") or "").strip()
        for ref in list(getattr(action_result, "artifact_refs", []) or [])
        if str(getattr(ref, "ref", "") or "").strip()
    ]


def _tool_outcome_title(*, tool_name: str, outcome: str, error_code: str) -> str:
    return ":".join(
        item for item in ["tool_outcome", tool_name, outcome, error_code or ""] if item
    )


def _tool_outcome_content(
    *,
    runner: "BrainRunner",
    state: WorkingState,
    tool_name: str,
    tool_family: str,
    outcome: str,
    error_code: str,
    command: Command | None,
    args_signature: str | None,
    artifact_refs: list[str],
) -> dict[str, Any]:
    return {
        "tool_name": tool_name,
        "tool_family": tool_family,
        "outcome": outcome,
        "error_code": error_code or None,
        "turn_index": _tool_outcome_turn_index(runner, state=state),
        "intent_id": _tool_outcome_intent_id(command),
        "args_signature": args_signature,
        "artifact_ref": artifact_refs[0] if artifact_refs else None,
    }


def _tool_outcome_intent_id(command: Command | None) -> object:
    if command is None:
        return None
    return list(getattr(command, "sub_intent_ids", []) or [None])[0]


def _tool_outcome_tags(*, tool_family: str, outcome: str) -> list[str]:
    return [
        tag
        for tag in [
            "tool_outcome",
            f"tool_family:{tool_family}" if tool_family else "",
            f"outcome:{outcome}",
        ]
        if tag
    ]


def _tool_outcome_confidence(outcome: str) -> float:
    return 0.7 if outcome == "success" else 0.4


def _tool_outcome_meta(
    *,
    tool_name: str,
    tool_family: str,
    outcome: str,
    args_signature: str | None,
    command_id: str,
) -> dict[str, Any]:
    return {
        "source_kind": "tool_outcome",
        "source_negative_outcome": outcome != "success",
        "source_success_path": outcome == "success",
        STATE_KEY_SOURCE_OUTCOME: outcome,
        "source_tool_name": tool_name,
        "source_tool_family": tool_family,
        "source_args_signature": args_signature,
        "source_command_id": command_id or None,
    }


def _tool_api_unavailable_result(*, command_id: str) -> ActionResult:
    return ActionResult(
        command_id=command_id,
        status=BRAIN_ACTION_STATUS_FAILED,
        summary="Tool API unavailable",
        error=ActionError(
            code="TOOL_API_UNAVAILABLE",
            message="Tool API is not configured.",
            details={"reason_code": "tool_api_unavailable"},
        ),
    )


def _readonly_blocked_result(*, command_id: str, tool_name: str) -> ActionResult:
    return ActionResult(
        command_id=command_id,
        status=BRAIN_ACTION_STATUS_BLOCKED,
        summary=f"Tool {tool_name!r} blocked by readonly permission mode",
        error=ActionError(
            code="PERMISSION_DENIED_READONLY",
            message=(
                f"Cannot execute write-capable tool {tool_name!r} in readonly "
                "permission mode. Switch to default or bypass mode via shift+tab "
                "or /permissions <mode>."
            ),
            details={
                "reason_code": "readonly_blocks_write",
                "tool_name": tool_name,
                "permission_mode": "readonly",
            },
        ),
    )


def _request_outcome_blocked_result(
    *, command_id: str, tool_name: str, requested_outcome: str
) -> ActionResult:
    return ActionResult(
        command_id=command_id,
        status="blocked",
        summary=f"Tool {tool_name!r} blocked by request outcome {requested_outcome!r}",
        error=ActionError(
            code="REQUEST_OUTCOME_EFFECT_BLOCKED",
            message=(
                f"Cannot execute tool {tool_name!r} while requested_outcome is "
                f"{requested_outcome!r}. Use an execute-ready request for "
                "side-effecting tools."
            ),
            details={
                "reason_code": "request_outcome_blocks_effect",
                "tool_name": tool_name,
                "requested_outcome": requested_outcome,
            },
        ),
    )


def _validation_failed_result(
    *,
    command_id: str,
    validation_result: dict[str, Any],
) -> ActionResult:
    return ActionResult(
        command_id=command_id,
        status=BRAIN_ACTION_STATUS_FAILED,
        summary=f"Invalid tool arguments: {validation_result['message']}",
        error=ActionError(
            code="TOOL_ARG_VALIDATION_FAILED",
            message=validation_result["message"],
            details={
                "reason_code": str(
                    validation_result.get("reason_code") or "tool_arg_validation_failed"
                ),
                "missing_fields": validation_result.get("missing"),
                "suggestion": validation_result.get("suggestion"),
                "source": validation_result.get("source"),
            },
        ),
    )


def _prepare_outcome(
    *,
    command: Command,
    original_command: Command,
    tool_name: str,
    disposition: str,
    action_result: ActionResult,
) -> PrepareOutcome:
    return PrepareOutcome(
        approved_command=command,
        original_command=original_command,
        command_id=command.command_id,
        tool_name=tool_name,
        disposition=disposition,
        action_result=action_result,
    )


def _remembered_prepare_outcome(
    runner: "BrainRunner",
    *,
    state: WorkingState,
    command: Command,
    original_command: Command,
    tool_name: str,
    disposition: str,
    action_result: ActionResult,
) -> PrepareOutcome:
    runner._remember_idempotency(state=state, command=command, result=action_result)
    return _prepare_outcome(
        command=command,
        original_command=original_command,
        tool_name=tool_name,
        disposition=disposition,
        action_result=action_result,
    )


def _normalized_tool_command(command: Command) -> tuple[Command, str]:
    tool_name = str(getattr(command, "tool_name", "") or "").strip()
    normalized_tool_name = normalize_tool_name_for_brain(tool_name) or tool_name
    if normalized_tool_name and normalized_tool_name != tool_name:
        command = command.model_copy(update={"tool_name": normalized_tool_name}, deep=True)
        tool_name = normalized_tool_name
    return command, tool_name


def _requested_outcome(state: WorkingState) -> str:
    readiness = getattr(state, "request_readiness", None)
    return str(getattr(readiness, "requested_outcome", "") or "").strip()


def _request_outcome_block(
    runner: "BrainRunner",
    *,
    state: WorkingState,
    command: Command,
    original_command: Command,
    tool_name: str,
    requested_outcome: str,
) -> PrepareOutcome:
    result = _request_outcome_blocked_result(
        command_id=command.command_id,
        tool_name=tool_name,
        requested_outcome=requested_outcome,
    )
    return _remembered_prepare_outcome(
        runner,
        state=state,
        command=command,
        original_command=original_command,
        tool_name=tool_name,
        disposition="request_outcome_blocked",
        action_result=result,
    )


def _readonly_block(
    runner: "BrainRunner",
    *,
    state: WorkingState,
    command: Command,
    original_command: Command,
    tool_name: str,
) -> PrepareOutcome:
    result = _readonly_blocked_result(command_id=command.command_id, tool_name=tool_name)
    return _remembered_prepare_outcome(
        runner,
        state=state,
        command=command,
        original_command=original_command,
        tool_name=tool_name,
        disposition="readonly_blocked",
        action_result=result,
    )


def _prepare_blocking_outcome(
    runner: "BrainRunner",
    *,
    command: Command,
    original_command: Command,
    tool_name: str,
) -> PrepareOutcome | None:
    if command.kind != BRAIN_COMMAND_KIND_TOOL:
        return None
    if runner.tool_api is None:
        return _prepare_outcome(
            command=command,
            original_command=original_command,
            tool_name=tool_name,
            disposition="tool_api_unavailable",
            action_result=_tool_api_unavailable_result(command_id=command.command_id),
        )
    return None


def _budget_exhausted_outcome(
    runner: "BrainRunner",
    *,
    command: Command,
    original_command: Command,
    tool_name: str,
) -> PrepareOutcome:
    return _prepare_outcome(
        command=command,
        original_command=original_command,
        tool_name=tool_name,
        disposition="budget_exhausted",
        action_result=runner._budget_blocked_result(
            command_id=command.command_id,
            budget_name="tool_calls",
        ),
    )


def _emit_sanitized_tool_args(
    *,
    logger: CanonicalEventLogger,
    state: WorkingState,
    tool_name: str,
    sanitized_args: dict[str, Any],
    removed_arg_keys: list[str],
    lineage: dict[str, Any],
) -> None:
    if not removed_arg_keys:
        return
    logger.emit(
        "tool.args_sanitized",
        {
            "tool_name": tool_name,
            "removed_keys": list(removed_arg_keys),
            "retained_keys": sorted(sanitized_args.keys()),
            **lineage,
        },
        trace_id=state.trace_id,
    )


def _validation_failed_outcome(
    runner: "BrainRunner",
    *,
    state: WorkingState,
    command: Command,
    original_command: Command,
    tool_name: str,
    validation_result: dict[str, Any] | None,
) -> PrepareOutcome | None:
    if validation_result is None:
        return None
    result = _validation_failed_result(
        command_id=command.command_id,
        validation_result=validation_result,
    )
    return _remembered_prepare_outcome(
        runner,
        state=state,
        command=command,
        original_command=original_command,
        tool_name=tool_name,
        disposition="validation_failed",
        action_result=result,
    )


def _emit_tool_started_progress(
    runner: "BrainRunner",
    *,
    command: Command,
    tool_name: str,
) -> None:
    emit_tool_progress = getattr(runner, "_emit_tool_progress_event", None)
    if not callable(emit_tool_progress):
        return
    try:
        emit_tool_progress(
            kind="tool_started",
            tool_name=tool_name,
            args=dict(getattr(command, "args", {}) or {}),
            call_id=str(getattr(command, "command_id", "") or ""),
        )
    except Exception:
        pass


def _emit_tool_dispatch_start(
    runner: "BrainRunner",
    *,
    state: WorkingState,
    command: Command,
    tool_name: str,
    logger: CanonicalEventLogger,
    lineage: dict[str, Any],
) -> None:
    logger.emit(
        "tool.request",
        {
            "kind": command.kind,
            "title": command.title,
            "args": getattr(command, "args", None),
            **lineage,
        },
        trace_id=state.trace_id,
    )
    runner._emit_brain_operation(
        session_id=state.session_id,
        turn_id=str(state.trace_id or "").strip(),
        operation="tool_loop",
        extra={"provider": "tool", "tool_name": tool_name},
    )
    _emit_tool_started_progress(runner, command=command, tool_name=tool_name)
    logger.emit(
        "skill.step",
        {
            "step_index": state.cursor,
            "status": "running",
            "note": f"Starting execution of {command.title}",
        },
        trace_id=state.trace_id,
    )


def _tool_dispatch_payload(
    *,
    command: Command,
    state: WorkingState,
    lineage: dict[str, Any],
    permission_mode: str,
) -> dict[str, Any]:
    payload = command.model_dump(mode="json")
    payload_meta = payload.get("meta")
    if not isinstance(payload_meta, dict):
        payload_meta = {}
    payload_meta["orchestration"] = dict(lineage)
    payload["meta"] = payload_meta
    inputs = payload.get("inputs")
    if not isinstance(inputs, dict):
        inputs = {}
        payload["inputs"] = inputs
    inputs.setdefault("permission_mode", permission_mode)
    if _watch_background_write_authorized(state):
        inputs["background_write_authorized"] = True
        inputs["background_write_authorization_source"] = _WATCH_STATE_KEY
    return payload


def _prepared_tool_dispatch(
    *,
    command: Command,
    original_command: Command,
    state: WorkingState,
    tool_name: str,
    lineage: dict[str, Any],
    permission_mode: str,
    payload: dict[str, Any],
) -> PreparedToolDispatch:
    return PreparedToolDispatch(
        approved_command=command,
        original_command=original_command,
        command_id=command.command_id,
        tool_name=tool_name,
        validated_args=dict(getattr(command, "args", {}) or {}),
        session_id=state.session_id,
        trace_id=str(state.trace_id or ""),
        agent_id=str(getattr(state, "agent_id", "") or ""),
        lineage=lineage,
        permission_mode=permission_mode,
        payload=payload,
    )


def _tool_dispatch_preflight(
    runner: "BrainRunner",
    *,
    state: WorkingState,
    command: Command,
    original_command: Command,
) -> tuple[Command, str, str, dict[str, Any], PrepareOutcome | None]:
    global_permission_mode = canonical_permission_mode(
        str(getattr(state, "permission_mode", "default"))
    )
    state.permission_mode = global_permission_mode

    command, tool_name = _normalized_tool_command(command)
    permission_mode = effective_permission_mode_for_tool(
        global_mode=global_permission_mode,
        permission_overrides=getattr(state, "permission_overrides", {}),
        tool_name=tool_name,
    )
    requested_outcome = _requested_outcome(state)
    if not request_outcome_allows_tool(
        requested_outcome=requested_outcome,
        tool_name=tool_name,
    ):
        outcome = _request_outcome_block(
            runner,
            state=state,
            command=command,
            original_command=original_command,
            tool_name=tool_name,
            requested_outcome=requested_outcome,
        )
        return command, tool_name, permission_mode, {}, outcome
    if permission_mode == "readonly" and is_tool_blocked_by_readonly(tool_name):
        outcome = _readonly_block(
            runner,
            state=state,
            command=command,
            original_command=original_command,
            tool_name=tool_name,
        )
        return command, tool_name, permission_mode, {}, outcome

    lineage = _command_lineage_payload(state=state, command=command)
    if state.budgets_remaining.tool_calls <= 0:
        outcome = _budget_exhausted_outcome(
            runner,
            command=command,
            original_command=original_command,
            tool_name=tool_name,
        )
        return command, tool_name, permission_mode, lineage, outcome
    outcome = _prepare_blocking_outcome(
        runner,
        command=command,
        original_command=original_command,
        tool_name=tool_name,
    )
    return command, tool_name, permission_mode, lineage, outcome


def prepare_tool_dispatch(
    runner: "BrainRunner",
    *,
    state: WorkingState,
    command: Command,
    original_command: Command,
    logger: CanonicalEventLogger,
) -> PreparedToolDispatch | PrepareOutcome:
    activate_skill_for_command(state, command)
    command, tool_name, permission_mode, lineage, blocking_outcome = (
        _tool_dispatch_preflight(
            runner,
            state=state,
            command=command,
            original_command=original_command,
        )
    )
    if blocking_outcome is not None:
        return blocking_outcome

    state.budgets_remaining.tool_calls -= 1
    sanitized_args, removed_arg_keys = sanitize_tool_command_args(
        runner, command=command
    )
    _emit_sanitized_tool_args(
        logger=logger,
        state=state,
        tool_name=tool_name,
        sanitized_args=sanitized_args,
        removed_arg_keys=removed_arg_keys,
        lineage=lineage,
    )

    failed_outcome = _validation_failed_outcome(
        runner,
        state=state,
        command=command,
        original_command=original_command,
        tool_name=tool_name,
        validation_result=runner._validate_tool_args(command=command, state=state),
    )
    if failed_outcome is not None:
        return failed_outcome

    _emit_tool_dispatch_start(
        runner,
        state=state,
        command=command,
        tool_name=tool_name,
        logger=logger,
        lineage=lineage,
    )
    payload = _tool_dispatch_payload(
        command=command,
        state=state,
        lineage=lineage,
        permission_mode=permission_mode,
    )

    return _prepared_tool_dispatch(
        command=command,
        original_command=original_command,
        state=state,
        tool_name=tool_name,
        lineage=lineage,
        permission_mode=permission_mode,
        payload=payload,
    )


def execute_prepared_tool_dispatch(
    runner: "BrainRunner",
    prepared_dispatch: PreparedToolDispatch,
) -> RawToolResult:
    started = time.monotonic()
    with active_chat_phase("tool_calls"):
        raw = runner.tool_api.execute(
            command=prepared_dispatch.payload,
            session_id=prepared_dispatch.session_id,
            trace_id=prepared_dispatch.trace_id,
        )
    duration_ms = int((time.monotonic() - started) * 1000)
    error_payload = (
        dict(raw.get("error"))
        if isinstance(raw, dict) and isinstance(raw.get("error"), dict)
        else None
    )
    artifacts = (
        tuple(raw.get("artifact_refs", []) or []) if isinstance(raw, dict) else tuple()
    )
    return RawToolResult(
        command_id=prepared_dispatch.command_id,
        tool_name=prepared_dispatch.tool_name,
        raw_output=raw,
        timing={"duration_ms": duration_ms},
        artifacts=artifacts,
        error_payload=error_payload,
    )


def finalize_tool_result(
    runner: "BrainRunner",
    *,
    state: WorkingState,
    prepared_dispatch: PreparedToolDispatch,
    raw_result: RawToolResult,
    logger: CanonicalEventLogger,
) -> CommandExecutionOutcome:
    normalized, job = runner._normalize_execution_result(
        command_id=prepared_dispatch.command_id,
        raw=raw_result.raw_output,
        provider="tool",
    )
    if job is None:
        logger.emit(
            "tool.completed",
            {
                "status": normalized.status,
                "summary": normalized.summary,
                **prepared_dispatch.lineage,
            },
            trace_id=state.trace_id,
            artifact_refs=[a.ref for a in normalized.artifact_refs],
            memory_refs=normalized.memory_refs,
            status="ok"
            if normalized.status == BRAIN_ACTION_STATUS_SUCCESS
            else "error",
            error=normalized.error.model_dump(mode="json")
            if normalized.error
            else None,
        )
        # producer-side dict-shape `tool_completed` bridge —
        # paired with the `tool_started` emission in `prepare_tool_dispatch`.
        _emit_tool_progress = getattr(runner, "_emit_tool_progress_event", None)
        if callable(_emit_tool_progress):
            try:
                _duration_ms = None
                _timing = getattr(raw_result, "timing", None)
                if isinstance(_timing, dict):
                    _duration_ms = _timing.get("duration_ms")
                _emit_tool_progress(
                    kind="tool_completed",
                    tool_name=str(getattr(prepared_dispatch, "tool_name", "") or ""),
                    args=dict(getattr(prepared_dispatch, "validated_args", {}) or {}),
                    call_id=str(getattr(prepared_dispatch, "command_id", "") or ""),
                    duration_ms=_duration_ms,
                    ok=(normalized.status == BRAIN_ACTION_STATUS_SUCCESS),
                    content=str(getattr(normalized, "summary", "") or ""),
                )
            except Exception:
                pass
        runner._remember_idempotency(
            state=state,
            command=prepared_dispatch.approved_command,
            result=normalized,
        )
        _stage_tool_outcome_candidate(
            runner,
            state=state,
            tool_name=prepared_dispatch.tool_name,
            action_result=normalized,
            command=prepared_dispatch.original_command,
        )
    return CommandExecutionOutcome(
        approved_command=prepared_dispatch.approved_command,
        action_result=normalized,
        job=job,
    )



__all__ = [
    "_JSON_SCHEMA_TOP_LEVEL_KEYS",
    "_TOOL_OUTCOME_MAX_STAGE_PER_TURN",
    "_TOOL_OUTCOME_RECORD_TYPE",
    "_TOOL_OUTCOME_STAGED_COMMAND_IDS_KEY",
    "_TOOL_OUTCOME_STATE_KEY",
    "_WATCH_ACTION_TURN_KIND",
    "_WATCH_STATE_KEY",
    "_parameter_keys_from_spec_payload",
    "_prepare_outcome_disposition",
    "_spec_like_payload",
    "_stage_tool_outcome_candidate",
    "_tool_api_unavailable_result",
    "_tool_family",
    "_tool_outcome_from_result",
    "_tool_outcome_should_stage",
    "_tool_outcome_turn_index",
    "_validation_failed_result",
    "_watch_background_write_authorized",
    "execute_prepared_tool_dispatch",
    "finalize_tool_result",
    "prepare_tool_dispatch",
    "resolve_tool_spec_payload",
    "sanitize_tool_command_args",
]
