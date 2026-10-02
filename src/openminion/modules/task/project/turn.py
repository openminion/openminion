"""Typed project-turn contracts shared by foreground and cron execution."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TypeVar, cast

from openminion.base.constants import STATE_KEY_FINALIZATION_STATUS
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
    VerificationDomain,
)
from openminion.modules.task.plan import (
    TaskPlan,
    TaskPlanRevision,
    TaskPlanStepBlocked,
    TaskPlanStepCompleted,
    TaskPlanTerminalSignal,
)
from openminion.modules.tool.contracts.model_ids import MODEL_GIT_STATUS

from .progress import AutonomyLoopConditionKind
from .models import ProjectCheckpoint, ProjectCycleDecision
from .constants import REPOSITORY_LIFECYCLE_PAYLOAD_KEY

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
    act_profile: VerificationDomain = "coding"
    allowed_tools: tuple[str, ...] = ()
    project_tool_calls_remaining: int | None = None
    plan_revision_required: bool = False


@dataclass(frozen=True)
class ProjectTurnResult:
    summary: str
    gateway_run_id: str = ""
    condition: AutonomyLoopConditionKind = AutonomyLoopConditionKind.PRODUCTIVE
    evidence_refs: tuple[str, ...] = ()
    artifact_refs: tuple[str, ...] = ()
    evidence_kinds: tuple[str, ...] = ()
    effect_refs: tuple[str, ...] = ()
    tool_call_count: int = 0
    task_plan: TaskPlan | None = None
    task_plan_revision: TaskPlanRevision | None = None
    task_plan_revisions: tuple[TaskPlanRevision, ...] = ()
    task_plan_step_completed: TaskPlanStepCompleted | None = None
    task_plan_step_blocked: TaskPlanStepBlocked | None = None
    task_plan_abandoned: TaskPlanTerminalSignal | None = None
    task_plan_completed: TaskPlanTerminalSignal | None = None
    error: ErrorInfo | None = None


def _project_verification_guidance(
    checkpoint: ProjectCheckpoint,
    active_plan: object,
) -> list[str]:
    payload = checkpoint.payload
    verification = payload.get("verification")
    if not verification:
        return []
    evidence = cast(list[dict[str, object]], verification)
    failed = [
        item for item in evidence if item["status"] == TestEvidenceStatus.FAILED.value
    ]
    history = payload.get("verification_history")
    history_evidence = (
        cast(list[dict[str, object]], history)
        if isinstance(history, list)
        else evidence
    )
    revision_required = bool(failed) or payload.get("plan_revision_required") is True
    outcomes = (
        [
            item
            for item in history_evidence
            if item["status"] == TestEvidenceStatus.FAILED.value
        ][-4:]
        if revision_required
        else evidence[-1:]
    )
    lines = [
        "Prior verifier outcome:",
        "\n\n".join(dict.fromkeys(str(item["summary"]) for item in outcomes)),
    ]
    if not revision_required or not isinstance(active_plan, Mapping):
        return lines
    plan_id = str(active_plan.get("plan_id") or "").strip()
    verifier_refs = ", ".join(checkpoint.project_run.verifier_refs[-5:])
    prior_revision = payload.get("task_plan_revision")
    predecessor_id = (
        str(prior_revision.get("revision_id") or "").strip()
        if isinstance(prior_revision, Mapping)
        else ""
    )
    predecessor_guidance = (
        f"Set predecessor_revision_id={predecessor_id}."
        if predecessor_id
        else "Omit predecessor_revision_id because this is the first revision."
    )
    lines.extend(
        (
            "External verification failed after the prior turn. First redeclare "
            "the same plan_id and exact criterion_ids with its remaining or repair "
            "steps and "
            "continue_plan_autonomously=false so it is active in this turn.",
            "Then use the existing plan loop-control "
            f"tool with action=revise for plan_id={plan_id}. Use a new "
            "revision_id, set continue_plan_autonomously=false, and bind "
            f"verifier_refs to: {verifier_refs}. {predecessor_guidance} End "
            "the turn after the revision succeeds; do not complete plan steps "
            "in this revision-only turn.",
        )
    )
    return lines


def project_cycle_prompt(
    run: AutonomyRun,
    checkpoint: ProjectCheckpoint,
    milestone: str,
    *,
    repository_check_observation: Mapping[str, object] | None = None,
    operator_guidance: Mapping[str, object] | None = None,
) -> str:
    project_run, checkpoint_payload = checkpoint.project_run, checkpoint.payload
    lines = [
        run.goal_text,
        "",
        f"Workspace root: {project_workspace(run.workspace_ref)}",
        "Use this workspace for repository tools. Do not infer another workspace "
        "from the goal text or verification command.",
        f"Current milestone: {milestone}",
        f"Committed cycles: {project_run.committed_cycle_count}",
        "Work on the smallest useful next step. Inspect current state before editing.",
        "Do not call the approved verification commands through a tool. The "
        "configured verifier runs them after the turn and records their evidence.",
        "Record step_completed as each plan step finishes. When every plan "
        "step is complete, call plan action=complete once and end the turn. Do not "
        "redeclare a completed plan unless a prior verifier failure below explicitly "
        "requires reactivation.",
        "The configured verifier, not final text, owns project completion.",
    ]
    lines.extend(_repository_status_guidance(run))
    active_plan = checkpoint_payload.get("task_plan")
    objective = _approved_project_objective(checkpoint)
    lines.extend(_approved_source_request_guidance(objective))
    lines.extend(_approved_objective_guidance(objective))
    lines.extend(_active_plan_guidance(active_plan))
    lines.extend(_project_reference_guidance("verifier", project_run.verifier_refs))
    lines.extend(_project_verification_guidance(checkpoint, active_plan))
    lines.extend(_project_reference_guidance("progress", project_run.progress_refs))
    lines.extend(_repository_check_guidance(repository_check_observation))
    if operator_guidance:
        lines.extend(
            (
                "New operator guidance for this cycle:",
                json.dumps(operator_guidance, sort_keys=True),
            )
        )
    return "\n".join(lines)


def project_operator_guidance(
    metadata: Mapping[str, object],
    *,
    consumed_revision: int,
) -> tuple[dict[str, object], int]:
    guidance: dict[str, object] = {}
    included_revision = consumed_revision
    direction_revision = int(str(metadata.get("operator_direction_revision") or 0))
    if direction_revision > consumed_revision:
        guidance["direction"] = str(metadata.get("operator_direction") or "")
        included_revision = direction_revision
    priority_revision = int(str(metadata.get("priority_revision") or 0))
    if priority_revision > consumed_revision:
        guidance["priority"] = str(metadata.get("priority") or "")
        included_revision = max(included_revision, priority_revision)
    operator_answers = metadata.get("operator_answers")
    answers = (
        [
            dict(item)
            for item in operator_answers
            if isinstance(item, Mapping)
            and int(item.get("revision") or 0) > consumed_revision
        ]
        if isinstance(operator_answers, list)
        else []
    )
    if answers:
        guidance["answers"] = answers
        included_revision = max(
            included_revision,
            *(int(item["revision"]) for item in answers),
        )
    return guidance, included_revision


def project_checkpoint_guidance(
    metadata: Mapping[str, object], checkpoint: ProjectCheckpoint
) -> tuple[dict[str, object], int]:
    return project_operator_guidance(
        metadata,
        consumed_revision=int(
            str(checkpoint.payload.get("operator_guidance_consumed_revision") or 0)
        ),
    )


def _active_plan_guidance(active_plan: object) -> list[str]:
    if not isinstance(active_plan, Mapping):
        return [
            "Your first action must use the existing plan loop-control tool "
            "to declare a durable task plan with continue_plan_autonomously=false, "
            "then continue with its first step."
        ]
    identity = {
        "plan_id": active_plan.get("plan_id"),
        "criterion_ids": active_plan.get("criterion_ids", []),
        "steps": [
            {"step_id": step.get("step_id"), "status": step.get("status")}
            for step in cast(list[Mapping[str, object]], active_plan.get("steps", []))
        ],
    }
    return [
        "Canonical active task plan identifiers (preserve these exact values): "
        + json.dumps(identity, sort_keys=True)
    ]


def _repository_status_guidance(run: AutonomyRun) -> list[str]:
    if "repository_status" not in run.execution_selectors.required_evidence_kinds:
        return []
    return [
        "After all repository-changing tool calls, and immediately before plan "
        "completion, request and call git.status once for the approved workspace.",
        "The successful git.status result is required repository evidence; do not "
        "infer it from another command or from final text.",
    ]


def _approved_objective_guidance(objective: Mapping[str, object]) -> list[str]:
    criteria = objective.get("success_criteria")
    if not criteria:
        return []
    criterion_ids = cast(list[str], objective["criterion_ids"])
    return [
        "Approved success criteria (preserve these criterion_ids in the TaskPlan):",
        *(
            f"{criterion_id}: {criterion}"
            for criterion_id, criterion in zip(
                criterion_ids, cast(list[str], criteria), strict=True
            )
        ),
        "Approved verification commands: " + json.dumps(objective["verification"]),
    ]


def _approved_project_objective(
    checkpoint: ProjectCheckpoint,
) -> Mapping[str, object]:
    lifecycle = cast(
        Mapping[str, object],
        checkpoint.payload.get(REPOSITORY_LIFECYCLE_PAYLOAD_KEY, {}),
    )
    return cast(
        Mapping[str, object],
        lifecycle.get(checkpoint.project_run.objective_ledger_ref, {}),
    )


def _approved_source_request_guidance(objective: Mapping[str, object]) -> list[str]:
    source_request = str(objective.get("source_request") or "").strip()
    if not source_request:
        return []
    return [
        "Original approved request (the project handoff is already approved; "
        "preserve its post-approval requirements):",
        source_request,
        "Treat its file and tool restrictions as hard constraints. Do not include "
        "prohibited files or commands in the plan or in tool calls.",
    ]


def _project_reference_guidance(kind: str, refs: tuple[str, ...]) -> list[str]:
    return [f"Prior {kind} refs: " + ", ".join(refs[-5:])] if refs else []


def _repository_check_guidance(observation: Mapping[str, object] | None) -> list[str]:
    if observation is None or observation["overall_result"] == "pending":
        return []
    return [
        "GitHub check facts for the exact approved head:",
        json.dumps(observation, sort_keys=True),
    ]


def project_cycle_checkpoint_payload(
    checkpoint: ProjectCheckpoint,
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
    operator_guidance_consumed_revision: int,
) -> dict[str, object]:
    previous_verification = checkpoint.payload.get("verification_history")
    if not isinstance(previous_verification, list):
        previous_verification = checkpoint.payload.get("verification")
    verification_payload = [item.model_dump(mode="json") for item in verification]
    verification_history = [
        *(previous_verification if isinstance(previous_verification, list) else []),
        *verification_payload,
    ][-8:]
    revision_emitted = bool(turn.task_plan_revisions or turn.task_plan_revision)
    has_model_task_plan = bool(turn.gateway_run_id) and (
        turn.task_plan is not None
        or isinstance(checkpoint.payload.get("task_plan"), Mapping)
    )
    revision_required = (
        bool(checkpoint.payload.get("plan_revision_required"))
        or (
            has_model_task_plan
            and any(item.status == TestEvidenceStatus.FAILED for item in verification)
        )
    ) and not revision_emitted
    return {
        **effect_payload,
        "decision": decision.value,
        "summary": turn.summary,
        "gateway_run_id": turn.gateway_run_id,
        "verification": verification_payload,
        "verification_history": verification_history,
        "plan_revision_required": revision_required,
        "verification_closure": dict(verification_closure),
        "condition": turn.condition.value,
        "decision_reason": decision_reason,
        **({"detail_code": "waiting_for_checks"} if waiting_for_checks else {}),
        "replan_count": replan_count,
        "operator_guidance_consumed_revision": operator_guidance_consumed_revision,
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
    error_code = str(metadata.get("error_code") or "").strip()
    if error_code:
        return _project_error_code_condition(error_code)
    termination = (
        str(metadata.get("tool_loop_termination_reason") or "").strip().lower()
    )
    if termination == "cancelled":
        return AutonomyLoopConditionKind.CANCELLED
    if termination in {"budget_exhausted", "budget_exhausted_with_partial_result"}:
        return AutonomyLoopConditionKind.BUDGET_EXHAUSTED
    if termination in {"timeout", "time_budget_exceeded"}:
        return AutonomyLoopConditionKind.DEADLINE_EXHAUSTED
    brain_status = str(metadata.get("brain_status") or "").strip().lower()
    finalization = _project_finalization_status(metadata)
    if finalization == "blocked":
        if brain_status == "waiting_user":
            return AutonomyLoopConditionKind.WAITING
        return AutonomyLoopConditionKind.TERMINAL_INABILITY
    if finalization == "incomplete":
        return AutonomyLoopConditionKind.STRATEGY_FAILURE
    if brain_status == "waiting_user":
        return AutonomyLoopConditionKind.WAITING
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
        "project_plan_revision_required": str(request.plan_revision_required).lower(),
        "project_act_profile": request.act_profile,
    }
    if request.allowed_tools:
        metadata.update(
            turn_tool_allowlist=",".join(request.allowed_tools),
            turn_tool_allowlist_supplied="true",
        )
    if request.project_tool_calls_remaining is not None:
        metadata["project_tool_calls_remaining"] = str(
            request.project_tool_calls_remaining
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
    artifact_refs = project_metadata_refs(metadata, "artifact_refs")
    evidence_refs = project_metadata_refs(metadata, "evidence_refs")
    evidence_kinds = project_metadata_refs(metadata, "evidence_kinds")
    repository_status_recorded = any(
        item.get("tool_name") == MODEL_GIT_STATUS and bool(item.get("ok"))
        for item in tool_results
    )
    task_plan_revisions = _project_checkpoint_revisions(metadata)
    return ProjectTurnResult(
        summary=summary,
        gateway_run_id=str(metadata.get("run_id") or "").strip(),
        condition=_project_condition(metadata=metadata, error=error),
        evidence_refs=tuple(dict.fromkeys((*evidence_refs, *artifact_refs))),
        artifact_refs=artifact_refs,
        evidence_kinds=tuple(
            dict.fromkeys(
                (
                    *evidence_kinds,
                    *(("repository_status",) if repository_status_recorded else ()),
                )
            )
        ),
        effect_refs=project_metadata_refs(metadata, "effect_refs"),
        tool_call_count=_project_tool_call_count(metadata, tool_results),
        task_plan=_project_metadata_model(metadata, "task_plan", TaskPlan),
        task_plan_revision=(
            task_plan_revisions[-1]
            if task_plan_revisions
            else _project_checkpoint_revision(metadata)
        ),
        task_plan_revisions=task_plan_revisions,
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
    return _project_metadata_model(
        metadata,
        "task_plan.revision",
        TaskPlanRevision,
    )


def _project_checkpoint_revisions(
    metadata: Mapping[str, object],
) -> tuple[TaskPlanRevision, ...]:
    value = metadata.get("task_plan.revisions")
    if isinstance(value, str):
        value = json.loads(value)
    if not isinstance(value, list):
        return ()
    return tuple(
        TaskPlanRevision.model_validate(item)
        for item in value
        if isinstance(item, Mapping)
    )


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
    return _project_error_code_condition(error.code)


def _project_condition(
    *,
    metadata: Mapping[str, object],
    error: ErrorInfo | None,
) -> AutonomyLoopConditionKind:
    explicit = str(metadata.get("project_condition") or "").strip()
    if explicit:
        return AutonomyLoopConditionKind(explicit)
    if error is not None:
        return _project_error_condition(error)
    return project_condition_from_metadata(metadata)


def _project_error_code_condition(code: str) -> AutonomyLoopConditionKind:
    normalized = str(code or "").strip()
    if normalized == "cancelled":
        return AutonomyLoopConditionKind.CANCELLED
    if normalized in {
        "POLICY_DENIED",
        "PERMISSION_DENIED_READONLY",
        "REQUEST_OUTCOME_EFFECT_BLOCKED",
        "tool_exposure_denied",
    }:
        return AutonomyLoopConditionKind.DENIED
    if normalized in {
        "TOOL_API_UNAVAILABLE",
        "TOOL_REQUEST_UNAVAILABLE",
        "PLAN_WORKFLOW_CATALOG_UNAVAILABLE",
        "PLAN_WORKFLOW_NOT_FOUND",
        "PINCHTAB_MISSING",
        "SIDECAR_AUTOSTART_DENIED",
    }:
        return AutonomyLoopConditionKind.MISSING_CAPABILITY
    if normalized in {"BUDGET_EXCEEDED", "ACT_ADAPTIVE_BUDGET_EXHAUSTED"}:
        return AutonomyLoopConditionKind.BUDGET_EXHAUSTED
    if normalized == "TIMEOUT":
        return AutonomyLoopConditionKind.DEADLINE_EXHAUSTED
    return AutonomyLoopConditionKind.RETRYABLE_FAILURE


def _project_finalization_status(metadata: Mapping[str, object]) -> str:
    value = metadata.get("adaptive.finalization_status") or metadata.get(
        STATE_KEY_FINALIZATION_STATUS
    )
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return ""
    if not isinstance(value, Mapping):
        return ""
    return str(value.get("status") or "").strip().lower()


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
    "project_operator_guidance",
    "project_condition_from_metadata",
    "project_error_from_payload",
    "project_metadata_refs",
    "project_runtime_payload",
    "project_cycle_checkpoint_payload",
    "project_turn_from_payload",
    "project_turn_inbound_metadata",
    "project_workspace",
]
