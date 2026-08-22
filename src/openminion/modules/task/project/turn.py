"""Typed project-turn contracts shared by foreground and cron execution."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from openminion.modules.controlplane.constants import (
    CALLER_HANDLES_DELIVERY_METADATA_KEY,
)
from openminion.modules.task.autonomy import autonomy_permission_metadata

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
    condition: AutonomyLoopConditionKind = AutonomyLoopConditionKind.PRODUCTIVE
    evidence_refs: tuple[str, ...] = ()
    evidence_kinds: tuple[str, ...] = ()
    effect_refs: tuple[str, ...] = ()
    tool_call_count: int = 0


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
    if bool(turn_result.get("error")):
        raise RuntimeError(str(turn_result.get("summary") or "project turn failed"))
    raw_metadata = turn_result.get("metadata")
    metadata = raw_metadata if isinstance(raw_metadata, Mapping) else {}
    return ProjectTurnResult(
        summary=str(turn_result.get("summary") or "Project cycle completed."),
        condition=project_condition_from_metadata(metadata),
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
    )


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
    "project_metadata_refs",
    "project_runtime_payload",
    "project_turn_from_payload",
    "project_turn_inbound_metadata",
    "project_workspace",
]
