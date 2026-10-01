from __future__ import annotations

from typing import cast

from openminion.base.redaction import redact_sensitive_text
from openminion.modules.task.autonomy import AutonomyRun

from .constants import (
    REPOSITORY_LIFECYCLE_PAYLOAD_KEY,
    REPOSITORY_LIFECYCLE_RECEIPT_LIMIT,
    REPOSITORY_LIFECYCLE_TEXT_MAX_CHARS,
)
from .models import ProjectCheckpoint, ProjectCycleDecision, ProjectRun
from .turn import ProjectTurnResult


def _bounded_text(value: object) -> str:
    redacted, _ = redact_sensitive_text(str(value or "").strip())
    return cast(str, redacted[:REPOSITORY_LIFECYCLE_TEXT_MAX_CHARS])


def _workspace_revision(workspace_ref: str) -> str:
    _, _, fragment = workspace_ref.partition("#")
    for field in fragment.split(";"):
        key, _, value = field.partition("=")
        if key == "commit" and value:
            return _bounded_text(value)
    return "unknown"


def initial_repository_lifecycle_payload(
    autonomy_run: AutonomyRun,
    project_run: ProjectRun,
    *,
    workspace_boundary_ref: str | None = None,
    task_plan_required: bool = False,
    expected_checks: tuple[str, ...] = (),
    launch_approved: bool = False,
    release_tools_approved: bool = False,
    success_criteria: tuple[str, ...] = (),
    verification_commands: tuple[str, ...] = (),
    source_request: str = "",
) -> dict[str, object]:
    repository_ref = _bounded_text(project_run.workspace_ref)
    repository_revision = _workspace_revision(repository_ref)
    objective = _bounded_text(autonomy_run.goal_text)
    decisions: list[dict[str, str]] = []
    if launch_approved:
        decisions.append(
            {"decision": "project_launch_approved", "objective": objective}
        )
    if launch_approved and release_tools_approved:
        decisions.append(
            {"decision": "project_release_tools_approved", "objective": objective}
        )
    return {
        REPOSITORY_LIFECYCLE_PAYLOAD_KEY: {
            project_run.objective_ledger_ref: {
                "objective": objective,
                "success_criteria": list(success_criteria),
                "criterion_ids": [
                    f"{project_run.goal_id}:criterion:{index}"
                    for index in range(1, len(success_criteria) + 1)
                ],
                "verification": list(verification_commands),
                "continuation_policy": autonomy_run.continuation_policy.model_dump(
                    mode="json"
                ),
                "constraints": [],
                "source_request": _bounded_text(source_request),
                "approval": "approved" if launch_approved else None,
                "spec_tracker_paths": [],
                "source_revisions": {"execution_repository": repository_revision},
            },
            project_run.evidence_ledger_ref: {"receipts": []},
            project_run.resume_packet_ref: {
                "workspace_boundary": _bounded_text(
                    workspace_boundary_ref or project_run.workspace_ref
                ),
                "execution_repository": repository_ref,
                "task_id": _bounded_text(project_run.task_id),
                "active_tracker_row": None,
                "current_revisions": {"execution_repository": repository_revision},
                "current_phase": project_run.phase.value,
                "next_action": ProjectCycleDecision.CONTINUE.value,
                "task_plan_required": task_plan_required,
                "expected_checks": list(expected_checks),
            },
            project_run.operator_decision_log_ref: {"decisions": decisions},
            project_run.capability_plan_ref: {
                "required_model_tool_ids": [],
                "available_model_tool_ids": [],
            },
            project_run.metrics_summary_ref: {
                "cycle_count": 0,
                "tool_call_count": 0,
                "verification_count": 0,
            },
        }
    }


def advance_repository_lifecycle_payload(
    checkpoint: ProjectCheckpoint,
    project_run: ProjectRun,
    *,
    turn: ProjectTurnResult,
    verification_count: int,
    next_action: str,
) -> dict[str, object]:
    raw_lifecycle = checkpoint.payload.get(REPOSITORY_LIFECYCLE_PAYLOAD_KEY)
    if not isinstance(raw_lifecycle, dict):
        return {}

    lifecycle = dict(raw_lifecycle)
    objective = cast(
        dict[str, object], lifecycle[project_run.objective_ledger_ref]
    ).copy()
    source_revisions = cast(
        dict[str, object], objective.get("source_revisions", {})
    ).copy()
    repository_revision = _workspace_revision(project_run.workspace_ref)
    source_revisions["execution_repository"] = repository_revision
    objective["source_revisions"] = source_revisions
    evidence = dict(cast(dict[str, object], lifecycle[project_run.evidence_ledger_ref]))
    existing_receipts = cast(list[object], evidence["receipts"])
    receipt_refs = (
        *existing_receipts,
        *project_run.progress_refs,
        *project_run.effect_refs,
        *project_run.verifier_refs,
    )
    evidence["receipts"] = list(
        dict.fromkeys(
            _bounded_text(reference)
            for reference in receipt_refs
            if str(reference).strip()
        )
    )[-REPOSITORY_LIFECYCLE_RECEIPT_LIMIT:]

    resume = dict(cast(dict[str, object], lifecycle[project_run.resume_packet_ref]))
    current_revisions = dict(cast(dict[str, object], resume["current_revisions"]))
    current_revisions["execution_repository"] = repository_revision
    latest_revision = (
        turn.task_plan_revisions[-1]
        if turn.task_plan_revisions
        else turn.task_plan_revision
    )
    if latest_revision is not None:
        current_revisions["task_plan"] = _bounded_text(latest_revision.revision_id)
    resume.update(
        {
            "execution_repository": _bounded_text(project_run.workspace_ref),
            "current_revisions": current_revisions,
            "current_phase": project_run.phase.value,
            "next_action": _bounded_text(next_action),
        }
    )

    metrics = dict(cast(dict[str, object], lifecycle[project_run.metrics_summary_ref]))
    metrics.update(
        {
            "cycle_count": project_run.committed_cycle_count,
            "tool_call_count": cast(int, metrics["tool_call_count"])
            + turn.tool_call_count,
            "verification_count": cast(int, metrics["verification_count"])
            + verification_count,
        }
    )
    lifecycle.update(
        {
            project_run.objective_ledger_ref: objective,
            project_run.evidence_ledger_ref: evidence,
            project_run.resume_packet_ref: resume,
            project_run.metrics_summary_ref: metrics,
        }
    )
    return {REPOSITORY_LIFECYCLE_PAYLOAD_KEY: lifecycle}


__all__ = [
    "advance_repository_lifecycle_payload",
    "initial_repository_lifecycle_payload",
]
