from __future__ import annotations

from collections.abc import Mapping
from typing import cast

from openminion.base.redaction import redact_sensitive_text
from openminion.modules.task.autonomy import (
    AutonomyRun,
    AutonomyRunPhase,
    AutonomyRunStatus,
    now_ms,
)
from openminion.modules.task.plan import (
    TaskPlan,
    TaskPlanRevision,
    apply_task_plan_signals,
)
from openminion.modules.task.runtime.lifecycle import (
    ProjectCycleClaim,
    TaskLifecycleRecord,
    TaskLifecycleState,
    TaskManager,
)

from .constants import (
    REPOSITORY_LIFECYCLE_PAYLOAD_KEY,
    REPOSITORY_LIFECYCLE_RECEIPT_LIMIT,
    REPOSITORY_LIFECYCLE_TEXT_MAX_CHARS,
)
from .models import (
    ProjectCheckpoint,
    ProjectCycleDecision,
    ProjectCycleRecord,
    ProjectRun,
    ProjectVerificationState,
)
from .turn import ProjectTurnResult
from .verification import ProjectDomainVerificationStatus


_OPEN_PROJECT_TASK_STATES = {
    TaskLifecycleState.ACTIVE,
    TaskLifecycleState.PAUSED,
}

ProjectCycleDisposition = tuple[
    ProjectCycleDecision,
    AutonomyRunStatus,
    AutonomyRunPhase,
    ProjectVerificationState,
    int,
    str,
]


def _bounded_repository_text(value: object) -> str:
    redacted, _ = redact_sensitive_text(str(value or "").strip())
    return cast(str, redacted[:REPOSITORY_LIFECYCLE_TEXT_MAX_CHARS])


def _workspace_revision(workspace_ref: str) -> str:
    _, _, fragment = workspace_ref.partition("#")
    for field in fragment.split(";"):
        key, _, value = field.partition("=")
        if key == "commit" and value:
            return _bounded_repository_text(value)
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
) -> dict[str, object]:
    repository_ref = _bounded_repository_text(project_run.workspace_ref)
    repository_revision = _workspace_revision(repository_ref)
    objective = _bounded_repository_text(autonomy_run.goal_text)
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
                "constraints": [],
                "approval": "approved" if launch_approved else None,
                "spec_tracker_paths": [],
                "source_revisions": {
                    "execution_repository": repository_revision,
                },
            },
            project_run.evidence_ledger_ref: {"receipts": []},
            project_run.resume_packet_ref: {
                "workspace_boundary": _bounded_repository_text(
                    workspace_boundary_ref or project_run.workspace_ref
                ),
                "execution_repository": repository_ref,
                "task_id": _bounded_repository_text(project_run.task_id),
                "active_tracker_row": None,
                "current_revisions": {
                    "execution_repository": repository_revision,
                },
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
            _bounded_repository_text(reference)
            for reference in receipt_refs
            if str(reference).strip()
        )
    )[-REPOSITORY_LIFECYCLE_RECEIPT_LIMIT:]

    resume = dict(cast(dict[str, object], lifecycle[project_run.resume_packet_ref]))
    current_revisions = dict(cast(dict[str, object], resume["current_revisions"]))
    if turn.task_plan_revision is not None:
        current_revisions["task_plan"] = _bounded_repository_text(
            turn.task_plan_revision.revision_id
        )
    resume.update(
        {
            "current_revisions": current_revisions,
            "current_phase": project_run.phase.value,
            "next_action": _bounded_repository_text(next_action),
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
            project_run.evidence_ledger_ref: evidence,
            project_run.resume_packet_ref: resume,
            project_run.metrics_summary_ref: metrics,
        }
    )
    return {REPOSITORY_LIFECYCLE_PAYLOAD_KEY: lifecycle}


def repository_check_request(
    checkpoint: ProjectCheckpoint,
) -> dict[str, object] | None:
    observation = repository_check_observation(checkpoint)
    if observation is None or observation.get("overall_result") != "pending":
        return None
    return {
        "owner": observation["owner"],
        "repo": observation["repo"],
        "head_sha": observation["head_sha"],
        "expected_checks": list(cast(list[str], observation["expected_checks"])),
    }


def record_repository_check_result(
    checkpoint: ProjectCheckpoint,
    result: Mapping[str, object],
) -> ProjectCheckpoint:
    lifecycle, resume = _repository_lifecycle_resume(checkpoint)
    observation = dict(cast(dict[str, object], resume["ci_observation"]))
    if result.get("head_sha") != observation["head_sha"]:
        raise ValueError("github.fetch_checks returned a different head_sha")
    if result.get("expected_checks") != observation["expected_checks"]:
        raise ValueError("github.fetch_checks returned different expected checks")
    overall_result = str(result.get("overall_result") or "")
    if overall_result not in {"pending", "failure", "success"}:
        raise ValueError("github.fetch_checks returned an invalid overall_result")
    observed_at_ms = now_ms()
    observation.update(
        {
            "overall_result": overall_result,
            "check_count": cast(int, observation.get("check_count") or 0) + 1,
            "observed_at_ms": observed_at_ms,
            **(
                {"completed_at_ms": observed_at_ms}
                if overall_result in {"failure", "success"}
                else {}
            ),
            "missing_expected_checks": list(
                cast(list[str], result.get("missing_expected_checks") or [])
            ),
            "failure_facts": list(
                cast(list[object], result.get("failure_facts") or [])
            ),
        }
    )
    resume["ci_observation"] = observation
    lifecycle[checkpoint.project_run.resume_packet_ref] = resume
    return _with_repository_lifecycle(checkpoint, lifecycle)


def record_repository_check_terminal(
    checkpoint: ProjectCheckpoint,
    *,
    outcome: str,
) -> ProjectCheckpoint:
    if outcome not in {"cancelled", "expired"}:
        raise ValueError("invalid repository check terminal outcome")
    lifecycle, resume = _repository_lifecycle_resume(checkpoint)
    observation = dict(cast(dict[str, object], resume["ci_observation"]))
    completed_at_ms = now_ms()
    observation.update(
        overall_result=outcome,
        observed_at_ms=completed_at_ms,
        completed_at_ms=completed_at_ms,
    )
    resume["ci_observation"] = observation
    lifecycle[checkpoint.project_run.resume_packet_ref] = resume
    return _with_repository_lifecycle(checkpoint, lifecycle)


def begin_repository_check_observation(
    checkpoint: ProjectCheckpoint,
) -> tuple[ProjectCheckpoint, bool]:
    if not isinstance(
        checkpoint.payload.get(REPOSITORY_LIFECYCLE_PAYLOAD_KEY), Mapping
    ):
        return checkpoint, False
    lifecycle, resume = _repository_lifecycle_resume(checkpoint)
    expected_checks = cast(list[str], resume.get("expected_checks") or [])
    if not expected_checks:
        return checkpoint, False
    target = _latest_repository_check_target(checkpoint, resume)
    if target is None:
        return checkpoint, False
    current = resume.get("ci_observation")
    if isinstance(current, Mapping) and (
        current.get("target_effect_id") == target["target_effect_id"]
    ):
        return checkpoint, False
    resume["ci_observation"] = {
        **target,
        "expected_checks": list(expected_checks),
        "overall_result": "pending",
        "check_count": 0,
        "started_at_ms": now_ms(),
        "observed_at_ms": None,
        "completed_at_ms": None,
        "missing_expected_checks": list(expected_checks),
        "failure_facts": [],
    }
    lifecycle[checkpoint.project_run.resume_packet_ref] = resume
    return _with_repository_lifecycle(checkpoint, lifecycle), True


def carry_repository_check_observation(
    checkpoint: ProjectCheckpoint,
    observed: ProjectCheckpoint,
) -> ProjectCheckpoint:
    observation = repository_check_observation(observed)
    if observation is None:
        return checkpoint
    lifecycle, resume = _repository_lifecycle_resume(checkpoint)
    resume["ci_observation"] = observation
    lifecycle[checkpoint.project_run.resume_packet_ref] = resume
    return _with_repository_lifecycle(checkpoint, lifecycle)


def repository_check_facts(checkpoint: ProjectCheckpoint) -> dict[str, object]:
    observation = repository_check_observation(checkpoint)
    if observation is None:
        raise ValueError("project check observation is missing")
    started_at_ms = cast(int, observation["started_at_ms"])
    ended_at_ms = cast(
        int,
        observation.get("completed_at_ms")
        or observation.get("observed_at_ms")
        or now_ms(),
    )
    return {
        "owner": observation["owner"],
        "repo": observation["repo"],
        "head_sha": observation["head_sha"],
        "expected_checks": observation["expected_checks"],
        "overall_result": observation["overall_result"],
        "check_count": observation["check_count"],
        "missing_expected_checks": observation["missing_expected_checks"],
        "failure_facts": observation["failure_facts"],
        "wait_duration_ms": max(0, ended_at_ms - started_at_ms),
        "detail_code": "waiting_for_checks"
        if observation["overall_result"] == "pending"
        else None,
    }


def repository_check_event(
    checkpoint: ProjectCheckpoint,
    *,
    outcome: str | None = None,
) -> dict[str, object]:
    facts = repository_check_facts(checkpoint)
    if outcome is not None:
        facts.update(overall_result=outcome, detail_code=None)
    return facts


def commit_repository_check_wait(
    task_manager: TaskManager,
    checkpoint: ProjectCheckpoint,
    *,
    owner_id: str,
    claim_ttl_seconds: int,
    triggering_cron_job_id: str | None,
    task_state: TaskLifecycleState,
) -> ProjectCheckpoint:
    check_count = cast(int, repository_check_facts(checkpoint)["check_count"])
    project_run = checkpoint.project_run
    observation = cast(dict[str, object], repository_check_observation(checkpoint))
    check_ref = f"{observation['head_sha']}:{check_count}"
    checkpoint_id = f"{project_run.project_run_id}:checks:{check_ref}"
    next_wake_job_id = f"{project_run.project_run_id}:checks:{observation['head_sha']}:{check_count + 1}"
    updated = project_run.model_copy(
        update={
            "status": AutonomyRunStatus.RUNNING,
            "phase": AutonomyRunPhase.VALIDATE,
            "updated_at_ms": now_ms(),
            "last_checkpoint_id": checkpoint_id,
            "blocked_reason": None,
            "task_state": task_state,
            "triggering_cron_job_id": triggering_cron_job_id,
            "next_wake_job_id": next_wake_job_id,
        }
    )
    claim = task_manager.lifecycle_repository.acquire_project_cycle_claim(
        task_id=project_run.task_id,
        owner_id=owner_id,
        expected_checkpoint_id=checkpoint.checkpoint_id,
        ttl_seconds=claim_ttl_seconds,
    )
    try:
        return commit_project_run_checkpoint(
            task_manager,
            updated,
            claim=claim,
            checkpoint_id=checkpoint_id,
            triggering_cron_job_id=triggering_cron_job_id,
            next_wake_job_id=next_wake_job_id,
            payload={
                **checkpoint.payload,
                "decision": ProjectCycleDecision.CONTINUE.value,
                "decision_reason": "waiting_for_checks",
                "detail_code": "waiting_for_checks",
            },
        )
    finally:
        task_manager.lifecycle_repository.release_project_cycle_claim(claim)


def repository_check_observation(
    checkpoint: ProjectCheckpoint,
) -> dict[str, object] | None:
    return _repository_check_observation(
        checkpoint.payload,
        resume_packet_ref=checkpoint.project_run.resume_packet_ref,
    )


def _repository_check_observation(
    payload: Mapping[str, object],
    *,
    resume_packet_ref: str,
) -> dict[str, object] | None:
    raw_lifecycle = payload.get(REPOSITORY_LIFECYCLE_PAYLOAD_KEY)
    if not isinstance(raw_lifecycle, Mapping):
        return None
    raw_resume = raw_lifecycle.get(resume_packet_ref)
    if not isinstance(raw_resume, Mapping):
        return None
    raw_observation = raw_resume.get("ci_observation")
    return dict(raw_observation) if isinstance(raw_observation, Mapping) else None


def _with_repository_lifecycle(
    checkpoint: ProjectCheckpoint,
    lifecycle: Mapping[str, object],
) -> ProjectCheckpoint:
    return checkpoint.model_copy(
        update={
            "payload": {
                **checkpoint.payload,
                REPOSITORY_LIFECYCLE_PAYLOAD_KEY: dict(lifecycle),
            }
        }
    )


def _repository_lifecycle_resume(
    checkpoint: ProjectCheckpoint,
) -> tuple[dict[str, object], dict[str, object]]:
    lifecycle = dict(
        cast(
            dict[str, object],
            checkpoint.payload[REPOSITORY_LIFECYCLE_PAYLOAD_KEY],
        )
    )
    resume = dict(
        cast(dict[str, object], lifecycle[checkpoint.project_run.resume_packet_ref])
    )
    return lifecycle, resume


def _latest_repository_check_target(
    checkpoint: ProjectCheckpoint,
    resume: Mapping[str, object],
) -> dict[str, object] | None:
    raw_effects = checkpoint.payload.get("project_effects")
    raw_receipts = checkpoint.payload.get("project_effect_receipts")
    if not isinstance(raw_effects, Mapping) or not isinstance(raw_receipts, Mapping):
        return None

    prior = resume.get("ci_observation")
    pull_request = dict(prior) if isinstance(prior, Mapping) else None
    pull_request_index = -1
    latest_push: tuple[int, str, Mapping[str, object]] | None = None
    repair_push: tuple[int, str, Mapping[str, object]] | None = None
    for index, effect_id in enumerate(checkpoint.project_run.effect_refs):
        raw_effect = raw_effects.get(effect_id)
        receipt = raw_receipts.get(effect_id)
        if not isinstance(raw_effect, Mapping) or not isinstance(receipt, Mapping):
            continue
        capability = raw_effect.get("capability_ref")
        if capability == "git.push":
            latest_push = (index, effect_id, receipt)
            if pull_request is not None and index > pull_request_index:
                repair_push = latest_push
        elif capability == "github.open_pr":
            if latest_push is None or (
                latest_push[2].get("ref") != f"refs/heads/{receipt['head']}"
                or latest_push[2].get("remote_oid") != receipt["head_sha"]
            ):
                continue
            pull_request = {
                "owner": receipt["owner"],
                "repo": receipt["repo"],
                "number": receipt["number"],
                "head": receipt["head"],
                "head_sha": receipt["head_sha"],
                "repository": latest_push[2]["repository"],
                "remote": latest_push[2]["remote"],
                "target_effect_id": effect_id,
            }
            pull_request_index = index
            repair_push = None

    if pull_request is None:
        return None
    if repair_push is not None:
        _index, effect_id, receipt = repair_push
        remote_oid = receipt.get("remote_oid")
        if (
            remote_oid
            and remote_oid != pull_request.get("head_sha")
            and receipt.get("repository") == pull_request.get("repository")
            and receipt.get("remote") == pull_request.get("remote")
            and receipt.get("ref") == f"refs/heads/{pull_request['head']}"
        ):
            pull_request.update(
                {
                    "head_sha": remote_oid,
                    "target_effect_id": effect_id,
                }
            )
    return pull_request


def plan_checkpoint_payload(
    checkpoint: ProjectCheckpoint,
    turn: ProjectTurnResult,
) -> dict[str, object]:
    plan, revision, revision_count = updated_checkpoint_task_plan(checkpoint, turn)
    return {
        "plan_revision_count": revision_count,
        **({"task_plan": plan.model_dump(mode="json")} if plan else {}),
        **(
            {"task_plan_revision": revision.model_dump(mode="json")} if revision else {}
        ),
    }


def updated_checkpoint_task_plan(
    checkpoint: ProjectCheckpoint,
    turn: ProjectTurnResult,
) -> tuple[TaskPlan | None, TaskPlanRevision | None, int]:
    raw_plan = checkpoint.payload.get("task_plan")
    plan = TaskPlan.model_validate(raw_plan) if isinstance(raw_plan, dict) else None
    raw_revision = checkpoint.payload.get("task_plan_revision")
    revision = (
        TaskPlanRevision.model_validate(raw_revision)
        if isinstance(raw_revision, dict)
        else None
    )
    revision_count = cast(int, checkpoint.payload.get("plan_revision_count", 0))

    incoming_plan = turn.task_plan
    if incoming_plan is not None:
        if (
            plan is not None
            and incoming_plan.plan_id == plan.plan_id
            and incoming_plan.criterion_ids != plan.criterion_ids
        ):
            raise ValueError("task plan criterion_ids are immutable")
        plan = incoming_plan
        if revision is not None and revision.plan_id != plan.plan_id:
            revision = None

    incoming = turn.task_plan_revision
    if incoming is not None:
        if plan is None or incoming.plan_id != plan.plan_id:
            raise ValueError("plan revision must match the checkpoint task plan")
        if not incoming.revision_id:
            raise ValueError("plan revision requires revision_id")
        if not incoming.verifier_refs or any(
            not reference.strip() for reference in incoming.verifier_refs
        ):
            raise ValueError("plan revision requires verifier_refs")
        if incoming.criterion_ids and incoming.criterion_ids != plan.criterion_ids:
            raise ValueError("plan revision cannot change criterion_ids")
        if revision is None and incoming.predecessor_revision_id:
            raise ValueError("first plan revision cannot name a predecessor")
        if revision is not None:
            if incoming.revision_id == revision.revision_id:
                raise ValueError("duplicate plan revision_id")
            if incoming.predecessor_revision_id != revision.revision_id:
                raise ValueError("plan revision predecessor is stale or missing")
        revision = incoming.model_copy(
            update={"criterion_ids": incoming.criterion_ids or plan.criterion_ids}
        )
        plan = revision.to_task_plan(
            fallback_objective=plan.objective,
            fallback_workflow_id=plan.workflow_id,
            fallback_workflow_version_hash=plan.workflow_version_hash,
            fallback_criterion_ids=plan.criterion_ids,
        )
        revision_count += 1

    plan = apply_task_plan_signals(
        plan,
        step_completed=turn.task_plan_step_completed,
        step_blocked=turn.task_plan_step_blocked,
        abandoned=turn.task_plan_abandoned,
        completed=turn.task_plan_completed,
    )
    return plan, revision, revision_count


def repository_task_plan_required(checkpoint: ProjectCheckpoint) -> bool:
    lifecycle = checkpoint.payload.get(REPOSITORY_LIFECYCLE_PAYLOAD_KEY)
    if not isinstance(lifecycle, dict):
        return False
    resume = lifecycle.get(checkpoint.project_run.resume_packet_ref)
    return bool(isinstance(resume, dict) and resume.get("task_plan_required") is True)


def repository_task_plan_progress(
    checkpoint: ProjectCheckpoint,
    turn: ProjectTurnResult,
) -> tuple[bool, str | None]:
    plan, _, _ = updated_checkpoint_task_plan(checkpoint, turn)
    required = repository_task_plan_required(checkpoint)
    incomplete = bool(
        required
        and not (
            plan
            and plan.status == "completed"
            and all(step.status == "completed" for step in plan.steps)
        )
    )
    if not required or plan is None:
        return incomplete, checkpoint.project_run.current_milestone
    milestone = next(
        (
            step.description
            for step in plan.steps
            if step.status in {"pending", "in_progress"}
        ),
        plan.objective,
    )
    return incomplete, milestone


def task_plan_incomplete_disposition(
    run: AutonomyRun,
    cycle_number: int,
    closure_status: ProjectDomainVerificationStatus,
    has_error: bool,
    required: bool,
    previous_replans: int,
) -> ProjectCycleDisposition | None:
    if not (
        required
        and closure_status == ProjectDomainVerificationStatus.VERIFIED
        and not has_error
    ):
        return None
    if cycle_number < run.continuation_policy.max_iterations:
        return (
            ProjectCycleDecision.CONTINUE,
            AutonomyRunStatus.RUNNING,
            AutonomyRunPhase.EXECUTE,
            ProjectVerificationState.IN_PROGRESS,
            previous_replans,
            "task_plan_incomplete",
        )
    return (
        ProjectCycleDecision.BLOCKED,
        AutonomyRunStatus.BLOCKED,
        AutonomyRunPhase.CLOSED,
        ProjectVerificationState.BLOCKED,
        previous_replans,
        "task_plan_incomplete",
    )


def build_project_run_projection(
    autonomy_run: AutonomyRun,
    *,
    objective_ledger_ref: str,
    evidence_ledger_ref: str,
    resume_packet_ref: str,
    operator_decision_log_ref: str,
    capability_plan_ref: str,
    metrics_summary_ref: str,
    task_record: TaskLifecycleRecord | None = None,
    project_run_id: str | None = None,
    verification_state: ProjectVerificationState = (
        ProjectVerificationState.NOT_STARTED
    ),
    next_wakeup_at_ms: int | None = None,
    blocked_reason: str | None = None,
) -> ProjectRun:
    task_id = autonomy_run.task_id
    goal_id = autonomy_run.goal_id
    workspace_ref = autonomy_run.workspace_ref
    if not task_id:
        raise ValueError("project run projection requires autonomy_run.task_id")
    if not goal_id:
        raise ValueError("project run projection requires autonomy_run.goal_id")
    if not workspace_ref:
        raise ValueError("project run projection requires autonomy_run.workspace_ref")
    if task_record is not None and task_record.task_id != task_id:
        raise ValueError("task_record.task_id must match autonomy_run.task_id")

    return ProjectRun(
        project_run_id=project_run_id or f"prun_{autonomy_run.run_id}",
        autonomy_run_id=autonomy_run.run_id,
        task_id=task_id,
        goal_id=goal_id,
        objective_ledger_ref=objective_ledger_ref,
        evidence_ledger_ref=evidence_ledger_ref,
        resume_packet_ref=resume_packet_ref,
        operator_decision_log_ref=operator_decision_log_ref,
        capability_plan_ref=capability_plan_ref,
        metrics_summary_ref=metrics_summary_ref,
        workspace_ref=workspace_ref,
        session_id=autonomy_run.session_id,
        execution_selectors=autonomy_run.execution_selectors,
        status=autonomy_run.status,
        phase=autonomy_run.phase,
        created_at_ms=autonomy_run.created_at_ms,
        updated_at_ms=autonomy_run.updated_at_ms,
        last_checkpoint_id=autonomy_run.checkpoint_id,
        next_wakeup_at_ms=next_wakeup_at_ms,
        blocked_reason=blocked_reason
        or (autonomy_run.last_error.message if autonomy_run.last_error else None),
        verification_state=verification_state,
        task_state=task_record.state if task_record is not None else None,
        cycle_limit=autonomy_run.continuation_policy.max_iterations,
    )


def find_open_project_worker(
    task_manager: TaskManager,
    *,
    project_run_id: str,
    exclude_task_id: str | None = None,
) -> TaskLifecycleRecord | None:
    normalized_project_id = project_run_id.strip()
    if not normalized_project_id:
        raise ValueError("project_run_id is required")
    excluded = exclude_task_id.strip() if exclude_task_id else None
    for record in task_manager.lifecycle_repository.list(limit=1000):
        if record.state not in _OPEN_PROJECT_TASK_STATES:
            continue
        if excluded is not None and record.task_id == excluded:
            continue
        if str(record.metadata.get("project_run_id") or "") == normalized_project_id:
            return record
    return None


def link_project_run_to_task(
    task_manager: TaskManager,
    project_run: ProjectRun,
) -> TaskLifecycleRecord:
    record = task_manager.get_task(project_run.task_id)
    if record is None:
        raise KeyError(f"task not found: {project_run.task_id}")
    if (
        record.state not in _OPEN_PROJECT_TASK_STATES
        and record.state != project_run.task_state
    ):
        raise ValueError("project run task state does not match its lifecycle task")
    existing = find_open_project_worker(
        task_manager,
        project_run_id=project_run.project_run_id,
        exclude_task_id=project_run.task_id,
    )
    if existing is not None:
        raise ValueError(
            "open project worker already exists: "
            f"{project_run.project_run_id} on task {existing.task_id}"
        )

    metadata = dict(record.metadata)
    metadata.update(
        {
            "project_run_id": project_run.project_run_id,
            "autonomy_run_id": project_run.autonomy_run_id,
            "goal_id": project_run.goal_id,
            "project_status": project_run.status.value,
            "project_phase": project_run.phase.value,
            "verification_state": project_run.verification_state.value,
        }
    )
    return task_manager.update_task_metadata(
        task_id=project_run.task_id,
        metadata=metadata,
    )


def save_project_run_checkpoint(
    task_manager: TaskManager,
    project_run: ProjectRun,
    *,
    checkpoint_id: str,
    payload: dict[str, object] | None = None,
) -> ProjectCheckpoint:
    link_project_run_to_task(task_manager, project_run)
    checkpoint = ProjectCheckpoint(
        checkpoint_id=checkpoint_id,
        project_run=project_run.model_copy(
            update={"last_checkpoint_id": checkpoint_id}
        ),
        payload=dict(payload or {}),
    )
    task_manager.save_checkpoint(
        project_run.task_id,
        checkpoint_id,
        checkpoint.model_dump(mode="json"),
    )
    return checkpoint


def commit_project_run_checkpoint(
    task_manager: TaskManager,
    project_run: ProjectRun,
    *,
    claim: ProjectCycleClaim,
    checkpoint_id: str,
    payload: dict[str, object] | None = None,
    triggering_cron_job_id: str | None = None,
    next_wake_job_id: str | None = None,
) -> ProjectCheckpoint:
    checkpoint = ProjectCheckpoint(
        checkpoint_id=checkpoint_id,
        project_run=project_run.model_copy(
            update={"last_checkpoint_id": checkpoint_id}
        ),
        payload=dict(payload or {}),
        expected_checkpoint_id=claim.expected_checkpoint_id,
        triggering_cron_job_id=triggering_cron_job_id,
        next_wake_job_id=next_wake_job_id,
    )
    task_manager.lifecycle_repository.commit_project_cycle_checkpoint(
        claim,
        checkpoint_id=checkpoint_id,
        state=checkpoint.model_dump(mode="json"),
    )
    link_project_run_to_task(task_manager, checkpoint.project_run)
    return checkpoint


def load_latest_project_checkpoint(
    task_manager: TaskManager,
    *,
    task_id: str,
) -> ProjectCheckpoint | None:
    latest = task_manager.get_latest_checkpoint(task_id)
    if latest is None:
        return None
    _checkpoint_id, state = latest
    if state.get("kind") != "project_run":
        raise ValueError("latest checkpoint is not a project_run checkpoint")
    return ProjectCheckpoint.model_validate(state)


def project_cycle_summaries(
    task_manager: TaskManager,
    *,
    task_id: str,
) -> tuple[str, ...]:
    summaries: list[str] = []
    for checkpoint_id in task_manager.list_checkpoints(task_id):
        state = task_manager.get_checkpoint(task_id, checkpoint_id)
        if not state or state.get("kind") != "project_run":
            continue
        summary = ProjectCheckpoint.model_validate(state).payload.get("summary")
        if isinstance(summary, str) and summary.strip():
            summaries.append(summary.strip())
    return tuple(summaries)


def resume_project_run_from_latest_checkpoint(
    task_manager: TaskManager,
    *,
    task_id: str,
) -> ProjectRun:
    checkpoint = load_latest_project_checkpoint(task_manager, task_id=task_id)
    if checkpoint is None:
        raise KeyError(f"project checkpoint not found: {task_id}")
    record = task_manager.get_task(task_id)
    if record is None:
        raise KeyError(f"task not found: {task_id}")
    metadata = dict(record.metadata)
    metadata["resume_count"] = int(metadata.get("resume_count") or 0) + 1
    metadata["last_resume_checkpoint_id"] = checkpoint.checkpoint_id
    task_manager.update_task_metadata(task_id=task_id, metadata=metadata)
    return checkpoint.project_run


def record_project_cycle(
    task_manager: TaskManager,
    project_run: ProjectRun,
    *,
    cycle_id: str,
    milestone: str,
    intended_action: str,
    evidence_refs: tuple[str, ...],
    validation_refs: tuple[str, ...],
    decision: ProjectCycleDecision,
    decision_reason: str | None = None,
    checkpoint_id: str | None = None,
    payload: dict[str, object] | None = None,
) -> ProjectCycleRecord:
    normalized_reason = decision_reason.strip() if decision_reason else None
    if decision != ProjectCycleDecision.CONTINUE and normalized_reason is None:
        raise ValueError("decision_reason is required unless decision is continue")
    normalized_checkpoint_id = (checkpoint_id or "").strip()
    effective_checkpoint_id = normalized_checkpoint_id or (
        f"{project_run.project_run_id}:{cycle_id.strip()}"
    )
    record = ProjectCycleRecord(
        cycle_id=cycle_id,
        project_run_id=project_run.project_run_id,
        task_id=project_run.task_id,
        goal_id=project_run.goal_id,
        milestone=milestone,
        intended_action=intended_action,
        evidence_refs=evidence_refs,
        validation_refs=validation_refs,
        checkpoint_id=effective_checkpoint_id,
        decision=decision,
        decision_reason=normalized_reason,
        created_at_ms=now_ms(),
    )
    checkpoint_payload: dict[str, object] = {"cycle": record.model_dump(mode="json")}
    if payload:
        checkpoint_payload["payload"] = dict(payload)
    save_project_run_checkpoint(
        task_manager,
        project_run,
        checkpoint_id=effective_checkpoint_id,
        payload=checkpoint_payload,
    )
    return record


def replay_project_cycles(
    task_manager: TaskManager,
    *,
    task_id: str,
) -> tuple[ProjectCycleRecord, ...]:
    cycles: list[ProjectCycleRecord] = []
    for checkpoint_id in task_manager.list_checkpoints(task_id):
        state = task_manager.get_checkpoint(task_id, checkpoint_id)
        if not state or state.get("kind") != "project_run":
            continue
        checkpoint = ProjectCheckpoint.model_validate(state)
        raw_cycle = checkpoint.payload.get("cycle")
        if isinstance(raw_cycle, dict):
            cycles.append(ProjectCycleRecord.model_validate(raw_cycle))
    return tuple(cycles)


__all__ = [
    "advance_repository_lifecycle_payload",
    "begin_repository_check_observation",
    "build_project_run_projection",
    "carry_repository_check_observation",
    "commit_project_run_checkpoint",
    "commit_repository_check_wait",
    "find_open_project_worker",
    "initial_repository_lifecycle_payload",
    "link_project_run_to_task",
    "load_latest_project_checkpoint",
    "project_cycle_summaries",
    "record_project_cycle",
    "record_repository_check_result",
    "record_repository_check_terminal",
    "repository_check_event",
    "repository_check_facts",
    "repository_check_observation",
    "repository_check_request",
    "replay_project_cycles",
    "resume_project_run_from_latest_checkpoint",
    "save_project_run_checkpoint",
]
