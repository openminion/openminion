from __future__ import annotations

from enum import StrEnum
from typing import cast

from pydantic import BaseModel, ConfigDict, Field

from openminion.modules.task.autonomy import (
    AutonomyRunStatus,
    ContinuationPolicy,
    TestEvidence,
    now_ms,
)
from openminion.modules.task.plan import TaskPlan
from openminion.modules.task.project import (
    ProjectRun,
    ProjectVerificationState,
    load_latest_project_checkpoint,
)
from openminion.modules.task.project.checkpoints import project_cycle_summaries
from openminion.modules.task.project.capabilities import ProjectCapabilityMatrix
from openminion.modules.task.project.budget import evaluate_continuation_budget
from openminion.modules.task.project.constants import REPOSITORY_LIFECYCLE_PAYLOAD_KEY
from openminion.modules.task.project.models import ProjectBudgetCheckResult
from openminion.modules.task.runtime.lifecycle import (
    ProjectCycleClaim,
    TaskManager,
    TaskLifecycleState,
)


class _StrictReportModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProjectOutcomeClassification(StrEnum):
    IN_PROGRESS = "in_progress"
    COMPLETED_VERIFIED = "completed-verified"
    COMPLETED_WAIVED = "completed-waived"
    BLOCKED_HUMAN_INPUT = "blocked-human-input"
    BLOCKED_CAPABILITY_GAP = "blocked-capability-gap"
    BLOCKED_PROVIDER_QUOTA = "blocked-provider-quota"
    CANCELLED = "cancelled"
    FAILED_REGRESSION = "failed-regression"
    FAILED_SAFETY = "failed-safety"
    SUPERSEDED = "superseded"


class ProjectMetricSnapshot(_StrictReportModel):
    objective_completion_percent: float = Field(default=0.0, ge=0.0, le=100.0)
    milestone_completion_percent: float = Field(default=0.0, ge=0.0, le=100.0)
    verification_pass_count: int = Field(default=0, ge=0)
    verification_fail_count: int = Field(default=0, ge=0)
    escaped_regression_count: int = Field(default=0, ge=0)
    restart_resume_success_count: int = Field(default=0, ge=0)
    restart_resume_attempt_count: int = Field(default=0, ge=0)
    checkpoint_interval_ms: int = Field(default=0, ge=0)
    stale_evidence_invalidation_count: int = Field(default=0, ge=0)
    active_work_ms: int = Field(default=0, ge=0)
    idle_wait_ms: int = Field(default=0, ge=0)
    approval_wait_ms: int = Field(default=0, ge=0)
    first_visible_progress_ms: int = Field(default=0, ge=0)
    duplicate_tool_call_count: int = Field(default=0, ge=0)
    retry_count: int = Field(default=0, ge=0)
    blocked_duration_ms: int = Field(default=0, ge=0)
    token_usage: int = Field(default=0, ge=0)
    cost_microusd: int = Field(default=0, ge=0)
    proof_packet_completeness_percent: float = Field(default=0.0, ge=0.0, le=100.0)
    operator_intervention_count: int = Field(default=0, ge=0)
    plan_revision_count: int = Field(default=0, ge=0)


class ProjectMetricComparison(_StrictReportModel):
    metric: str = Field(min_length=1)
    baseline: float
    current: float
    delta: float


class ProjectReport(_StrictReportModel):
    project_run: ProjectRun
    outcome: ProjectOutcomeClassification
    metrics: ProjectMetricSnapshot
    goal: str | None = None
    task_plan: TaskPlan | None = None
    verification: tuple[TestEvidence, ...] = ()
    budget: ProjectBudgetCheckResult | None = None
    next_action: str | None = None
    baseline_comparisons: tuple[ProjectMetricComparison, ...] = ()
    capability_matrix: ProjectCapabilityMatrix | None = None
    proof_refs: tuple[str, ...] = ()
    cycle_summaries: tuple[str, ...] = ()
    cycle_claim: ProjectCycleClaim | None = None
    safety_notes: tuple[str, ...] = ()
    ux_notes: tuple[str, ...] = ()
    generated_at_ms: int = Field(default_factory=now_ms, ge=0)


def build_project_report(
    project_run: ProjectRun,
    *,
    metrics: ProjectMetricSnapshot | None = None,
    baseline_metrics: ProjectMetricSnapshot | None = None,
    capability_matrix: ProjectCapabilityMatrix | None = None,
    outcome: ProjectOutcomeClassification | None = None,
    proof_refs: tuple[str, ...] = (),
    cycle_summaries: tuple[str, ...] = (),
    safety_notes: tuple[str, ...] = (),
    ux_notes: tuple[str, ...] = (),
    cycle_claim: ProjectCycleClaim | None = None,
) -> ProjectReport:
    current_metrics = metrics or ProjectMetricSnapshot()
    return ProjectReport(
        project_run=project_run,
        outcome=outcome or project_outcome_from_verification(project_run),
        metrics=current_metrics,
        baseline_comparisons=compare_project_metrics(
            baseline_metrics,
            current_metrics,
        ),
        capability_matrix=capability_matrix,
        proof_refs=proof_refs,
        cycle_summaries=cycle_summaries,
        cycle_claim=cycle_claim,
        safety_notes=safety_notes,
        ux_notes=ux_notes,
    )


def build_project_report_from_task(
    task_manager: TaskManager,
    *,
    task_id: str,
) -> ProjectReport:
    checkpoint = load_latest_project_checkpoint(task_manager, task_id=task_id)
    if checkpoint is None:
        raise KeyError(f"project checkpoint not found for task: {task_id}")
    record = task_manager.get_task(task_id)
    if record is None:
        raise KeyError(f"task not found: {task_id}")
    proof_refs = tuple(
        ref
        for ref in (
            checkpoint.project_run.evidence_ledger_ref,
            checkpoint.project_run.resume_packet_ref,
            checkpoint.project_run.metrics_summary_ref,
        )
        if ref
    )
    metrics = ProjectMetricSnapshot(
        restart_resume_attempt_count=int(record.metadata.get("resume_count") or 0),
        restart_resume_success_count=int(record.metadata.get("resume_count") or 0),
        operator_intervention_count=_operator_intervention_count(record.metadata),
        proof_packet_completeness_percent=round((len(proof_refs) / 3) * 100, 2),
        plan_revision_count=cast(
            int, checkpoint.payload.get("plan_revision_count") or 0
        ),
    )
    project_run = checkpoint.project_run.model_copy(update={"task_state": record.state})
    if record.state in {
        TaskLifecycleState.PAUSED,
        TaskLifecycleState.CANCELLED,
        TaskLifecycleState.FAILED,
    }:
        project_run = project_run.model_copy(
            update={
                "status": {
                    TaskLifecycleState.PAUSED: AutonomyRunStatus.BLOCKED,
                    TaskLifecycleState.CANCELLED: AutonomyRunStatus.CANCELLED,
                    TaskLifecycleState.FAILED: AutonomyRunStatus.FAILED,
                }[record.state],
            }
        )
    report = build_project_report(
        project_run,
        metrics=metrics,
        proof_refs=proof_refs,
        cycle_summaries=project_cycle_summaries(task_manager, task_id=task_id),
        cycle_claim=task_manager.lifecycle_repository.get_project_cycle_claim(task_id),
    )
    lifecycle = checkpoint.payload.get(REPOSITORY_LIFECYCLE_PAYLOAD_KEY)
    if not isinstance(lifecycle, dict):
        return report
    objective = lifecycle[project_run.objective_ledger_ref]
    resume = lifecycle[project_run.resume_packet_ref]
    counters = lifecycle[project_run.metrics_summary_ref]
    policy = objective.get("continuation_policy")
    budget = (
        evaluate_continuation_budget(
            ContinuationPolicy.model_validate(policy),
            metadata=record.metadata,
            iterations=project_run.committed_cycle_count,
            wall_clock_ms=max(0, now_ms() - project_run.created_at_ms),
            tool_calls=counters["tool_call_count"],
        )
        if policy is not None
        else None
    )
    plan = checkpoint.payload.get("task_plan")
    return report.model_copy(
        update={
            "goal": objective.get("objective"),
            "task_plan": TaskPlan.model_validate(plan) if plan is not None else None,
            "verification": tuple(
                TestEvidence.model_validate(item)
                for item in cast(
                    list[object], checkpoint.payload.get("verification", [])
                )
            ),
            "budget": budget,
            "next_action": resume.get("next_action"),
        }
    )


def compare_project_metrics(
    baseline: ProjectMetricSnapshot | None,
    current: ProjectMetricSnapshot,
) -> tuple[ProjectMetricComparison, ...]:
    if baseline is None:
        return ()
    baseline_values = baseline.model_dump()
    current_values = current.model_dump()
    return tuple(
        ProjectMetricComparison(
            metric=metric,
            baseline=float(baseline_values[metric]),
            current=float(value),
            delta=float(value) - float(baseline_values[metric]),
        )
        for metric, value in current_values.items()
    )


def project_outcome_from_verification(
    project_run: ProjectRun,
) -> ProjectOutcomeClassification:
    if project_run.status == AutonomyRunStatus.CANCELLED:
        return ProjectOutcomeClassification.CANCELLED
    if project_run.task_state == TaskLifecycleState.PAUSED:
        return ProjectOutcomeClassification.BLOCKED_HUMAN_INPUT
    if project_run.verification_state == ProjectVerificationState.VERIFIED:
        return ProjectOutcomeClassification.COMPLETED_VERIFIED
    if project_run.verification_state == ProjectVerificationState.WAIVED:
        return ProjectOutcomeClassification.COMPLETED_WAIVED
    if project_run.verification_state == ProjectVerificationState.FAILED:
        return ProjectOutcomeClassification.FAILED_REGRESSION
    if project_run.verification_state == ProjectVerificationState.BLOCKED:
        return ProjectOutcomeClassification.BLOCKED_HUMAN_INPUT
    return ProjectOutcomeClassification.IN_PROGRESS


def render_project_report(report: ProjectReport) -> str:
    lines = [
        f"project_run_id: {report.project_run.project_run_id}",
        f"task_id: {report.project_run.task_id}",
        f"autonomy_run_id: {report.project_run.autonomy_run_id}",
        f"goal_id: {report.project_run.goal_id}",
        f"session_id: {report.project_run.session_id}",
        f"goal: {report.goal or 'unavailable'}",
        f"status: {report.project_run.status.value}",
        f"task_state: {report.project_run.task_state.value if report.project_run.task_state else 'unknown'}",
        f"milestone: {report.project_run.current_milestone}",
        f"cycles: {report.project_run.committed_cycle_count}/{report.project_run.cycle_limit}",
        f"checkpoint: {report.project_run.last_checkpoint_id}",
        f"outcome: {report.outcome.value}",
        f"verification_state: {report.project_run.verification_state.value}",
        f"blocker: {report.project_run.blocked_reason or 'none'}",
        f"next_action: {report.next_action or 'unavailable'}",
        "metrics:",
    ]
    for name, value in report.metrics.model_dump().items():
        lines.append(f"  {name}: {value}")
    if report.task_plan is not None:
        lines.append(f"plan: {report.task_plan.plan_id} ({report.task_plan.status})")
        lines.extend(
            f"  {step.step_id}: {step.status} — {step.description}"
            for step in report.task_plan.steps
        )
    if report.verification:
        lines.append("verification:")
        lines.extend(
            f"  {item.status.value}: {item.summary}" for item in report.verification
        )
    if report.budget is not None:
        lines.append("budget:")
        lines.extend(
            f"  {key}: used={used}, remaining={report.budget.remaining.get(key, 'unlimited')}"
            for key, used in report.budget.used.items()
        )
    else:
        lines.append("budget: unavailable")
    if report.baseline_comparisons:
        lines.append("baseline_comparisons:")
        for comparison in report.baseline_comparisons:
            lines.append(
                f"  {comparison.metric}: baseline={comparison.baseline:g}, "
                f"current={comparison.current:g}, delta={comparison.delta:g}"
            )
    if report.capability_matrix is not None:
        lines.append(f"capabilities: {len(report.capability_matrix.rows)} rows")
    if report.proof_refs:
        lines.append("proof_refs:")
        lines.extend(f"  - {ref}" for ref in report.proof_refs)
    if report.cycle_summaries:
        lines.append("cycle_summaries:")
        lines.extend(
            f"  {index}: {summary}"
            for index, summary in enumerate(report.cycle_summaries, start=1)
        )
    if report.cycle_claim is not None:
        lines.append(
            "cycle_claim: "
            f"owner={report.cycle_claim.owner_id} "
            f"fence={report.cycle_claim.fence_token} "
            f"expires={report.cycle_claim.expires_at}"
        )
    if report.safety_notes:
        lines.append("safety_notes:")
        lines.extend(f"  - {note}" for note in report.safety_notes)
    if report.ux_notes:
        lines.append("ux_notes:")
        lines.extend(f"  - {note}" for note in report.ux_notes)
    return "\n".join(lines)


def _operator_intervention_count(metadata: dict[str, object]) -> int:
    answers = metadata.get("operator_answers")
    answer_count = len(answers) if isinstance(answers, list) else 0
    extensions = metadata.get("budget_extensions")
    extension_count = 1 if isinstance(extensions, dict) and extensions else 0
    priority_count = 1 if metadata.get("priority") else 0
    return answer_count + extension_count + priority_count


__all__ = (
    "ProjectMetricComparison",
    "ProjectMetricSnapshot",
    "ProjectOutcomeClassification",
    "ProjectReport",
    "build_project_report",
    "build_project_report_from_task",
    "compare_project_metrics",
    "project_outcome_from_verification",
    "render_project_report",
)
