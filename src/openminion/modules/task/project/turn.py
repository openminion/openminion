"""Typed project-turn contracts shared by foreground and cron execution."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TypeVar, cast

from openminion.base.errors import ErrorInfo, error_info_from_mapping
from openminion.base.redaction import redact_sensitive_text
from openminion.modules.controlplane.constants import (
    CALLER_HANDLES_DELIVERY_METADATA_KEY,
)
from openminion.modules.task.autonomy import (
    AutonomyRun,
    TestEvidence,
    TestEvidenceStatus,
    autonomy_permission_metadata,
)
from openminion.modules.task.plan import (
    TaskPlan,
    TaskPlanRevision,
    TaskPlanStepBlocked,
    TaskPlanStepCompleted,
    TaskPlanTerminalSignal,
)

from .progress import AutonomyLoopConditionKind
from .models import ProjectCheckpoint, ProjectCycleDecision

_ProjectMetadataModel = TypeVar(
    "_ProjectMetadataModel",
    TaskPlan,
    TaskPlanRevision,
    TaskPlanStepBlocked,
    TaskPlanStepCompleted,
    TaskPlanTerminalSignal,
)


@dataclass(frozen=True)
class ProjectTurnRequest:
    run_id: str
    project_run_id: str
    task_id: str
    goal_id: str
    session_id: str
    cycle_id: str
    milestone: str
    prompt: str
    allowed_tools: tuple[str, ...] = ()


@dataclass(frozen=True)
class ProjectTurnResult:
    summary: str
    gateway_run_id: str = ""
    condition: AutonomyLoopConditionKind = AutonomyLoopConditionKind.PRODUCTIVE
    evidence_refs: tuple[str, ...] = ()
    evidence_kinds: tuple[str, ...] = ()
    effect_refs: tuple[str, ...] = ()
    tool_call_count: int = 0
    task_plan: TaskPlan | None = None
    task_plan_revision: TaskPlanRevision | None = None
    task_plan_step_completed: TaskPlanStepCompleted | None = None
    task_plan_step_blocked: TaskPlanStepBlocked | None = None
    task_plan_abandoned: TaskPlanTerminalSignal | None = None
    task_plan_completed: TaskPlanTerminalSignal | None = None
    error: ErrorInfo | None = None


def project_cycle_prompt(
    run: AutonomyRun,
    checkpoint: ProjectCheckpoint,
    milestone: str,
    *,
    repository_check_observation: Mapping[str, object] | None = None,
) -> str:
    project_run = checkpoint.project_run
    checkpoint_payload = checkpoint.payload
    lines = [
        run.goal_text,
        "",
        f"Current milestone: {milestone}",
        f"Committed cycles: {project_run.committed_cycle_count}",
        "Work on the smallest useful next step. Inspect current state before editing.",
        "Do not claim completion; the configured verifier owns completion.",
    ]
    active_plan = checkpoint_payload.get("task_plan")
    if not isinstance(active_plan, Mapping):
        lines.append(
            "Your first action must use the existing plan loop-control tool "
            "to declare a durable task plan with "
            "continue_plan_autonomously=true, then continue with its first step."
        )
    if project_run.verifier_refs:
        lines.append(
            "Prior verifier refs: " + ", ".join(project_run.verifier_refs[-5:])
        )
    if verification := checkpoint_payload.get("verification"):
        evidence = cast(list[dict[str, object]], verification)
        failed = [
            item
            for item in evidence
            if item["status"] == TestEvidenceStatus.FAILED.value
        ]
        outcome = (failed or evidence)[-1]
        lines.extend(("Prior verifier outcome:", str(outcome["summary"])))
        if failed and isinstance(active_plan, Mapping):
            plan_id = str(active_plan.get("plan_id") or "").strip()
            verifier_refs = ", ".join(project_run.verifier_refs[-5:])
            lines.append(
                "Your first action must use the existing plan loop-control "
                f"tool with action=revise for plan_id={plan_id}. Use a new "
                "revision_id, set continue_plan_autonomously=true, and bind "
                f"verifier_refs to: {verifier_refs}."
            )
    if project_run.progress_refs:
        lines.append(
            "Prior progress refs: " + ", ".join(project_run.progress_refs[-5:])
        )
    if (
        repository_check_observation is not None
        and repository_check_observation["overall_result"] != "pending"
    ):
        lines.extend(
            (
                "GitHub check facts for the exact approved head:",
                json.dumps(repository_check_observation, sort_keys=True),
            )
        )
    return "\n".join(lines)


def project_cycle_checkpoint_payload(
    turn: ProjectTurnResult,
    *,
    effect_payload: Mapping[str, object],
    plan_payload: Mapping[str, object],
    repository_payload: Mapping[str, object],
    decision: ProjectCycleDecision,
    verification: tuple[TestEvidence, ...],
    verification_closure: Mapping[str, object],
    decision_reason: str,
    replan_count: int,
    waiting_for_checks: bool,
) -> dict[str, object]:
    return {
        **effect_payload,
        "decision": decision.value,
        "summary": turn.summary,
        "gateway_run_id": turn.gateway_run_id,
        "verification": [item.model_dump(mode="json") for item in verification],
        "verification_closure": dict(verification_closure),
        "condition": turn.condition.value,
        "decision_reason": decision_reason,
        **({"detail_code": "waiting_for_checks"} if waiting_for_checks else {}),
        "replan_count": replan_count,
        **plan_payload,
        **repository_payload,
        **({"error": turn.error.to_dict()} if turn.error else {}),
    }


_PROJECT_ERROR_DETAIL_KEYS = frozenset(
    {
        "error",
        "request_id",
        "response_bytes",
        "status_code",
        "timeout_seconds",
        "token_budget",
        "token_count",
    }
)
_PROJECT_ERROR_DETAIL_VALUE_CHARS = 256
_PROJECT_ERROR_DETAIL_TOTAL_CHARS = 1024


def project_error_from_payload(
    payload: Mapping[str, object],
    *,
    metadata: Mapping[str, object],
    default_message: str,
) -> ErrorInfo:
    raw_error = payload.get("error")
    error_payload = dict(raw_error) if isinstance(raw_error, Mapping) else {}
    if not error_payload:
        error_payload = {
            "code": metadata.get("error_code"),
            "message": metadata.get("error_message") or default_message,
            "details": _project_error_details(metadata.get("error_details")),
        }
    else:
        error_payload["details"] = _project_error_details(error_payload.get("details"))
    return error_info_from_mapping(
        error_payload,
        default_code="project_turn_failed",
        default_message=default_message,
        namespace="task.project",
    )


def _project_error_details(value: object) -> dict[str, object]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return {"error": "malformed_details"}
    if not isinstance(value, Mapping):
        return {} if value is None else {"error": "non_object_details"}
    details: dict[str, object] = {}
    size = 0
    for key, raw in value.items():
        name = str(key).strip()
        if name not in _PROJECT_ERROR_DETAIL_KEYS:
            continue
        redacted, _ = redact_sensitive_text(str(raw))
        bounded = redacted[:_PROJECT_ERROR_DETAIL_VALUE_CHARS]
        size += len(name) + len(bounded)
        if size > _PROJECT_ERROR_DETAIL_TOTAL_CHARS:
            return {**details, "error": "details_too_large"}
        details[name] = bounded
    return details


def project_metadata_refs(
    metadata: Mapping[str, object],
    *keys: str,
) -> tuple[str, ...]:
    refs: list[str] = []
    for key in keys:
        values = metadata.get(key)
        if isinstance(values, (list, tuple)):
            refs.extend(str(value).strip() for value in values if str(value).strip())
    return tuple(dict.fromkeys(refs))


def project_condition_from_metadata(
    metadata: Mapping[str, object],
) -> AutonomyLoopConditionKind:
    explicit = str(metadata.get("project_condition") or "").strip()
    if explicit:
        return AutonomyLoopConditionKind(explicit)
    brain_status = str(metadata.get("brain_status") or "").strip().lower()
    if brain_status == "waiting_user":
        return AutonomyLoopConditionKind.WAITING
    if str(metadata.get("finish_reason") or "").strip().lower() == "error":
        return AutonomyLoopConditionKind.RETRYABLE_FAILURE
    return AutonomyLoopConditionKind.PRODUCTIVE


def project_turn_inbound_metadata(
    request: ProjectTurnRequest,
    *,
    base: Mapping[str, object] | None = None,
) -> dict[str, object]:
    metadata: dict[str, object] = {
        **dict(base or {}),
        CALLER_HANDLES_DELIVERY_METADATA_KEY: "true",
        "conversation_id": request.project_run_id,
        "linked_task_id": request.task_id,
        "resume": "true",
    }
    if request.allowed_tools:
        metadata.update(
            turn_tool_allowlist=",".join(request.allowed_tools),
            turn_tool_allowlist_supplied="true",
        )
    return metadata


def project_turn_from_payload(
    request: ProjectTurnRequest,
    *,
    payload: Mapping[str, object],
    execute: Callable[[dict[str, object]], Mapping[str, object]],
) -> ProjectTurnResult:
    turn_payload = dict(payload)
    turn_payload.update(
        {
            "kind": "agentTurn",
            "message": request.prompt,
            "session_id": request.session_id,
            "goal_id": request.goal_id,
            "project_run_id": request.project_run_id,
            "task_id": request.task_id,
            "cycle_id": request.cycle_id,
        }
    )
    inbound_metadata = turn_payload.get("inbound_metadata")
    turn_payload["inbound_metadata"] = project_turn_inbound_metadata(
        request,
        base=inbound_metadata if isinstance(inbound_metadata, Mapping) else None,
    )
    return project_turn_result_from_response(response=execute(turn_payload))


def project_turn_result_from_response(
    *,
    response: Mapping[str, object],
) -> ProjectTurnResult:
    raw_metadata = response.get("metadata")
    metadata = raw_metadata if isinstance(raw_metadata, Mapping) else {}
    summary = str(
        response.get("summary")
        or response.get("final_text")
        or response.get("body")
        or "Project cycle completed."
    )
    error = (
        project_error_from_payload(
            response,
            metadata=metadata,
            default_message=summary,
        )
        if bool(response.get("error"))
        else None
    )
    tool_results = _project_tool_results(metadata)
    tool_result_refs = tuple(
        f"tool-call:{call_id}"
        for item in tool_results
        if bool(item.get("ok"))
        and (
            call_id := str(item.get("call_id") or item.get("command_id") or "").strip()
        )
    )
    evidence_refs = project_metadata_refs(metadata, "evidence_refs", "artifact_refs")
    evidence_kinds = project_metadata_refs(metadata, "evidence_kinds")
    return ProjectTurnResult(
        summary=summary,
        gateway_run_id=str(metadata.get("run_id") or "").strip(),
        condition=(
            _project_error_condition(error)
            if error is not None and not metadata.get("project_condition")
            else project_condition_from_metadata(metadata)
        ),
        evidence_refs=tuple(dict.fromkeys((*evidence_refs, *tool_result_refs))),
        evidence_kinds=tuple(
            dict.fromkeys(
                (*evidence_kinds, *(("tool_result",) if tool_result_refs else ()))
            )
        ),
        effect_refs=project_metadata_refs(metadata, "effect_refs"),
        tool_call_count=_project_tool_call_count(metadata, tool_results),
        task_plan=_project_metadata_model(metadata, "task_plan", TaskPlan),
        task_plan_revision=_project_checkpoint_revision(metadata),
        task_plan_step_completed=_project_metadata_model(
            metadata,
            "task_plan.step_completed",
            TaskPlanStepCompleted,
        ),
        task_plan_step_blocked=_project_metadata_model(
            metadata,
            "task_plan.step_blocked",
            TaskPlanStepBlocked,
        ),
        task_plan_abandoned=_project_metadata_model(
            metadata,
            "task_plan.abandoned",
            TaskPlanTerminalSignal,
        ),
        task_plan_completed=_project_metadata_model(
            metadata,
            "task_plan.completed",
            TaskPlanTerminalSignal,
        ),
        error=error,
    )


def _project_tool_results(
    metadata: Mapping[str, object],
) -> tuple[Mapping[str, object], ...]:
    for key in ("tool_calls_cumulative", "tool_results"):
        raw = metadata.get(key)
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except json.JSONDecodeError:
                continue
        if isinstance(raw, list):
            return tuple(item for item in raw if isinstance(item, Mapping))
    return ()


def _project_checkpoint_revision(
    metadata: Mapping[str, object],
) -> TaskPlanRevision | None:
    revision = _project_metadata_model(
        metadata,
        "task_plan.revision",
        TaskPlanRevision,
    )
    if revision is None or not revision.revision_id or not revision.verifier_refs:
        return None
    return revision


def _project_tool_call_count(
    metadata: Mapping[str, object],
    tool_results: tuple[Mapping[str, object], ...],
) -> int:
    for key in ("tool_call_count", "tool_calls_count"):
        value = metadata.get(key)
        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.isdigit():
            return int(value)
    return len(tool_results)


def _project_metadata_model(
    metadata: Mapping[str, object],
    key: str,
    model_type: type[_ProjectMetadataModel],
) -> _ProjectMetadataModel | None:
    value = metadata.get(key)
    if isinstance(value, str):
        value = json.loads(value)
    if not isinstance(value, Mapping):
        return None
    return model_type.model_validate(value)


def _project_error_condition(error: ErrorInfo) -> AutonomyLoopConditionKind:
    if error.code == "cancelled":
        return AutonomyLoopConditionKind.CANCELLED
    return AutonomyLoopConditionKind.RETRYABLE_FAILURE


def project_workspace(workspace_ref: str | None) -> Path:
    if not workspace_ref or not workspace_ref.startswith("local:"):
        raise ValueError("project cycle requires a local workspace reference")
    raw_path = workspace_ref.removeprefix("local:").split("#", 1)[0]
    workspace = Path(raw_path).expanduser().resolve(strict=False)
    if not workspace.is_dir():
        raise ValueError(f"project workspace is unavailable: {workspace}")
    return workspace


def project_runtime_payload(
    payload: Mapping[str, object],
    *,
    permission_profile_id: str,
    workspace: Path,
    turn_timeout_seconds: int,
) -> dict[str, object]:
    prepared = dict(payload)
    prepared["timeout_seconds"] = turn_timeout_seconds
    inbound_metadata = prepared.get("inbound_metadata")
    prepared["inbound_metadata"] = {
        **(dict(inbound_metadata) if isinstance(inbound_metadata, dict) else {}),
        **autonomy_permission_metadata(permission_profile_id),
        "workspace_root": str(workspace),
        "turn_timeout_seconds": str(turn_timeout_seconds),
    }
    return prepared


__all__ = [
    "ProjectTurnRequest",
    "ProjectTurnResult",
    "project_cycle_prompt",
    "project_condition_from_metadata",
    "project_error_from_payload",
    "project_metadata_refs",
    "project_runtime_payload",
    "project_cycle_checkpoint_payload",
    "project_turn_from_payload",
    "project_turn_inbound_metadata",
    "project_workspace",
]
