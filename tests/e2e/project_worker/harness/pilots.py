from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path

from openminion.modules.task import (
    AutonomyRunPhase,
    AutonomyRunStatus,
    ProjectMetricSnapshot,
    ProjectOutcomeClassification,
    ProjectVerificationState,
    build_autonomy_run,
    build_project_capability_matrix,
    build_project_report,
    build_project_run_projection,
    render_project_report,
)


@dataclass(frozen=True)
class ProjectPilotSpec:
    pilot_id: str
    duration_label: str
    compressed_duration_ms: int
    objective: str
    scenarios: tuple[str, ...]
    metrics: ProjectMetricSnapshot
    outcome: ProjectOutcomeClassification
    proof_refs: tuple[str, ...]
    failures: tuple[str, ...]
    operator_status: tuple[str, ...]


@dataclass(frozen=True)
class ProjectPilotArtifact:
    pilot_id: str
    json_path: Path
    markdown_path: Path
    status_path: Path


def _verified_metrics(**values: object) -> ProjectMetricSnapshot:
    return ProjectMetricSnapshot(
        objective_completion_percent=100.0,
        milestone_completion_percent=100.0,
        proof_packet_completeness_percent=100.0,
        **values,
    )


def default_pilot_specs() -> tuple[ProjectPilotSpec, ...]:
    return (
        ProjectPilotSpec(
            pilot_id="pilot-30m-local",
            duration_label="30-minute local fixture",
            compressed_duration_ms=30_000,
            objective="Validate the local project-worker loop with report proof.",
            scenarios=(
                "project-report-local",
                "autonomy-cli-project-controls",
                "restart-resume-local",
            ),
            metrics=_verified_metrics(
                verification_pass_count=3,
                restart_resume_success_count=1,
                restart_resume_attempt_count=1,
                checkpoint_interval_ms=10_000,
                active_work_ms=30_000,
                first_visible_progress_ms=250,
                operator_intervention_count=1,
            ),
            outcome=ProjectOutcomeClassification.COMPLETED_VERIFIED,
            proof_refs=(
                "pytest:tests/e2e/project_worker/test_local.py",
                "pytest:tests/task/test_project_run.py",
                "artifact:workspace-tmp/long-horizon-project-worker-v3-2026-07-03/pilots/pilot-30m-local.json",
            ),
            failures=(),
            operator_status=(
                "status: completed-verified",
                "milestones: 3/3",
                "resume: 1/1",
            ),
        ),
        ProjectPilotSpec(
            pilot_id="pilot-2h-coding-research",
            duration_label="2-hour coding/research fixture",
            compressed_duration_ms=120_000,
            objective=(
                "Validate a mixed coding and research project-worker pilot with "
                "permission, failure recovery, and report evidence."
            ),
            scenarios=(
                "permission-local",
                "human-input-local",
                "failure-report-local",
                "chat-cli-local",
                "focus-local",
            ),
            metrics=_verified_metrics(
                verification_pass_count=5,
                restart_resume_success_count=1,
                restart_resume_attempt_count=1,
                checkpoint_interval_ms=20_000,
                active_work_ms=120_000,
                approval_wait_ms=1_000,
                first_visible_progress_ms=500,
                retry_count=1,
                operator_intervention_count=2,
            ),
            outcome=ProjectOutcomeClassification.COMPLETED_VERIFIED,
            proof_refs=(
                "pytest:tests/e2e/project_worker/test_local.py",
                "pytest:tests/e2e/cli/focus/test_local.py",
                "pytest:tests/cli/test_autonomy_command.py",
                "artifact:workspace-tmp/long-horizon-project-worker-v3-2026-07-03/pilots/pilot-2h-coding-research.json",
            ),
            failures=("simulated initial blocked input path recovered",),
            operator_status=(
                "status: completed-verified",
                "milestones: 5/5",
                "human input: answered",
                "permission: deny-first path verified",
            ),
        ),
    )


def soak_pilot_specs() -> tuple[ProjectPilotSpec, ...]:
    return (
        ProjectPilotSpec(
            pilot_id="pilot-24h-restart-resume",
            duration_label="24-hour restart/resume fixture",
            compressed_duration_ms=240_000,
            objective=(
                "Validate restart/resume durability, duplicate-worker protection, "
                "and proof reporting for a compressed 24-hour project-worker pilot."
            ),
            scenarios=(
                "restart-resume-local",
                "permission-local",
                "human-input-local",
                "failure-report-local",
                "focus-local",
            ),
            metrics=_verified_metrics(
                verification_pass_count=6,
                restart_resume_success_count=3,
                restart_resume_attempt_count=3,
                checkpoint_interval_ms=60_000,
                active_work_ms=240_000,
                idle_wait_ms=30_000,
                approval_wait_ms=1_500,
                first_visible_progress_ms=500,
                retry_count=1,
                operator_intervention_count=2,
            ),
            outcome=ProjectOutcomeClassification.COMPLETED_VERIFIED,
            proof_refs=(
                "pytest:tests/task/test_project_run.py",
                "pytest:tests/e2e/project_worker/test_pilots.py",
                "pytest:tests/e2e/cli/focus/test_local.py",
                "artifact:workspace-tmp/long-horizon-project-worker-v3-2026-07-03/pilots/pilot-24h-restart-resume.json",
            ),
            failures=("simulated retry path recovered before final report",),
            operator_status=(
                "status: completed-verified",
                "restart/resume: 3/3",
                "duplicate worker: rejected",
                "permission: deny-first path verified",
            ),
        ),
        ProjectPilotSpec(
            pilot_id="pilot-72h-multiday",
            duration_label="72-hour multi-day fixture",
            compressed_duration_ms=720_000,
            objective=(
                "Validate a compressed multi-day project-worker pilot with an "
                "operator decision, blocked retry path, verification gate, and "
                "explicit no-overclaim status."
            ),
            scenarios=(
                "restart-resume-local",
                "permission-local",
                "human-input-local",
                "failure-report-local",
                "chat-cli-local",
                "focus-local",
            ),
            metrics=_verified_metrics(
                verification_pass_count=7,
                verification_fail_count=1,
                restart_resume_success_count=5,
                restart_resume_attempt_count=5,
                checkpoint_interval_ms=120_000,
                stale_evidence_invalidation_count=1,
                active_work_ms=720_000,
                idle_wait_ms=90_000,
                approval_wait_ms=2_000,
                first_visible_progress_ms=500,
                retry_count=2,
                blocked_duration_ms=15_000,
                operator_intervention_count=3,
            ),
            outcome=ProjectOutcomeClassification.COMPLETED_VERIFIED,
            proof_refs=(
                "pytest:tests/task/test_project_run.py",
                "pytest:tests/e2e/project_worker/test_pilots.py",
                "pytest:tests/e2e/cli/focus/test_local.py",
                "artifact:workspace-tmp/long-horizon-project-worker-v3-2026-07-03/pilots/pilot-72h-multiday.json",
            ),
            failures=(
                "simulated stale evidence invalidated before verification",
                "simulated blocked operator decision resolved before closeout",
            ),
            operator_status=(
                "status: completed-verified",
                "restart/resume: 5/5",
                "operator decision: resolved",
                "blocked retry: recovered",
                "verification gate: passed after stale evidence invalidation",
                "claim status: no unqualified days-or-longer claim from compressed proof",
            ),
        ),
    )


def all_pilot_specs() -> tuple[ProjectPilotSpec, ...]:
    return (*default_pilot_specs(), *soak_pilot_specs())


def build_project_pilot_report(spec: ProjectPilotSpec):
    autonomy_run = build_autonomy_run(
        goal_text=spec.objective,
        goal_id=f"goal-{spec.pilot_id}",
        session_id=f"session-{spec.pilot_id}",
        workspace_ref="local:/workspace#pilot=compressed",
        max_iterations=max(1, len(spec.scenarios)),
    ).model_copy(
        update={
            "task_id": f"task-{spec.pilot_id}",
            "checkpoint_id": f"checkpoint-{spec.pilot_id}",
            "status": AutonomyRunStatus.COMPLETED,
            "phase": AutonomyRunPhase.PROOF,
        }
    )
    project_run = build_project_run_projection(
        autonomy_run,
        objective_ledger_ref=f"artifact:{spec.pilot_id}/objective.json",
        evidence_ledger_ref=f"artifact:{spec.pilot_id}/evidence.jsonl",
        resume_packet_ref=f"artifact:{spec.pilot_id}/resume.json",
        operator_decision_log_ref=f"artifact:{spec.pilot_id}/operator.jsonl",
        capability_plan_ref=f"artifact:{spec.pilot_id}/capabilities.json",
        metrics_summary_ref=f"artifact:{spec.pilot_id}/metrics.json",
        verification_state=ProjectVerificationState.VERIFIED,
    )
    return build_project_report(
        project_run,
        metrics=spec.metrics,
        capability_matrix=build_project_capability_matrix(
            project_run_id=project_run.project_run_id,
        ),
        outcome=spec.outcome,
        proof_refs=spec.proof_refs,
        safety_notes=(
            "compressed deterministic pilot; no unattended destructive actions",
            "deny-first permission behavior remains under existing policy owners",
        ),
        ux_notes=spec.operator_status,
    )


def write_project_pilot_artifacts(
    output_dir: Path,
    *,
    specs: tuple[ProjectPilotSpec, ...] | None = None,
) -> tuple[ProjectPilotArtifact, ...]:
    output_dir.mkdir(parents=True, exist_ok=True)
    artifacts: list[ProjectPilotArtifact] = []
    for spec in specs or default_pilot_specs():
        report = build_project_pilot_report(spec)
        payload = {
            "pilot_id": spec.pilot_id,
            "duration_label": spec.duration_label,
            "compressed_duration_ms": spec.compressed_duration_ms,
            "objective": spec.objective,
            "scenarios": list(spec.scenarios),
            "failures": list(spec.failures),
            "operator_status": list(spec.operator_status),
            "report": report.model_dump(mode="json"),
        }
        json_path = output_dir / f"{spec.pilot_id}.json"
        markdown_path = output_dir / f"{spec.pilot_id}.md"
        status_path = output_dir / f"{spec.pilot_id}.status.txt"
        json_path.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        markdown_path.write_text(
            _render_pilot_markdown(spec, render_project_report(report)),
            encoding="utf-8",
        )
        status_path.write_text("\n".join(spec.operator_status) + "\n", encoding="utf-8")
        artifacts.append(
            ProjectPilotArtifact(
                pilot_id=spec.pilot_id,
                json_path=json_path,
                markdown_path=markdown_path,
                status_path=status_path,
            )
        )
    return tuple(artifacts)


def _render_pilot_markdown(spec: ProjectPilotSpec, report_text: str) -> str:
    failures = "\n".join(f"- {failure}" for failure in spec.failures) or "- none"
    status = "\n".join(f"- {line}" for line in spec.operator_status)
    scenarios = "\n".join(f"- {scenario}" for scenario in spec.scenarios)
    return (
        f"# {spec.pilot_id}\n\n"
        f"Duration: {spec.duration_label}\n\n"
        "Note: This is a compressed deterministic pilot artifact. It proves the "
        "reporting and local harness path, not elapsed wall-clock endurance.\n\n"
        f"Objective: {spec.objective}\n\n"
        "## Scenarios\n\n"
        f"{scenarios}\n\n"
        "## Operator Status\n\n"
        f"{status}\n\n"
        "## Failures\n\n"
        f"{failures}\n\n"
        "## Project Report\n\n"
        "```text\n"
        f"{report_text}\n"
        "```\n"
    )


__all__ = (
    "ProjectPilotArtifact",
    "ProjectPilotSpec",
    "all_pilot_specs",
    "build_project_pilot_report",
    "default_pilot_specs",
    "soak_pilot_specs",
    "write_project_pilot_artifacts",
)
