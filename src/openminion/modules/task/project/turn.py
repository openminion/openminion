"""Typed project-turn contracts shared by foreground and cron execution."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from openminion.base.errors import ErrorInfo, error_info_from_mapping
from openminion.base.redaction import redact_sensitive_text
from openminion.modules.controlplane.constants import (
    CALLER_HANDLES_DELIVERY_METADATA_KEY,
)
from openminion.modules.task.autonomy import autonomy_permission_metadata
from openminion.modules.task.plan import TaskPlan, TaskPlanRevision

from .progress import AutonomyLoopConditionKind


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
    error: ErrorInfo | None = None


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
    return {
        **dict(base or {}),
        CALLER_HANDLES_DELIVERY_METADATA_KEY: "true",
        "conversation_id": request.project_run_id,
        "resume": "true",
    }


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
    turn_result = execute(turn_payload)
    raw_metadata = turn_result.get("metadata")
    metadata = raw_metadata if isinstance(raw_metadata, Mapping) else {}
    error = (
        project_error_from_payload(
            turn_result,
            metadata=metadata,
            default_message=str(turn_result.get("summary") or "project turn failed"),
        )
        if bool(turn_result.get("error"))
        else None
    )
    return ProjectTurnResult(
        summary=str(turn_result.get("summary") or "Project cycle completed."),
        gateway_run_id=str(metadata.get("run_id") or "").strip(),
        condition=(
            _project_error_condition(error)
            if error is not None and not metadata.get("project_condition")
            else project_condition_from_metadata(metadata)
        ),
        evidence_refs=project_metadata_refs(
            metadata,
            "evidence_refs",
            "artifact_refs",
        ),
        evidence_kinds=project_metadata_refs(metadata, "evidence_kinds"),
        effect_refs=project_metadata_refs(metadata, "effect_refs"),
        tool_call_count=(
            int(metadata["tool_call_count"])
            if isinstance(metadata.get("tool_call_count"), int)
            else 0
        ),
        task_plan=_project_metadata_model(metadata, "task_plan", TaskPlan),
        task_plan_revision=_project_metadata_model(
            metadata,
            "task_plan.revision",
            TaskPlanRevision,
        ),
        error=error,
    )


def _project_metadata_model(
    metadata: Mapping[str, object],
    key: str,
    model_type: type[TaskPlan] | type[TaskPlanRevision],
) -> TaskPlan | TaskPlanRevision | None:
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
    "project_condition_from_metadata",
    "project_error_from_payload",
    "project_metadata_refs",
    "project_runtime_payload",
    "project_turn_from_payload",
    "project_turn_inbound_metadata",
    "project_workspace",
]
