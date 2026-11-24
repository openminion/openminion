"""CLI composition helpers for durable autonomy projects."""

from __future__ import annotations

import argparse
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from openminion.cli.commands.project_security import (
    revalidate_security_project,
    record_security_project_baseline,
)
from openminion.cli.commands.project_launch import ProjectLaunchRequest
from openminion.api.turns import run_turn
from openminion.api.turns import TurnRequestError, TurnTimeoutError
from openminion.base.errors import ErrorInfo
from openminion.modules.cli_common import resolve_module_cli_db_path
from openminion.modules.task import (
    AutonomyRun,
    AutonomyRunError,
    AutonomyRunPhase,
    AutonomyRunStatus,
    AutonomyRunStore,
    ProjectCycleDecision,
    TaskLifecycleState,
    TaskManager,
)
from openminion.modules.task.autonomy import (
    CommandEvidence,
    ContextBudgetEvidence,
    DelegatedRoleEvidence,
    TestEvidence,
    VerificationWaiver,
    autonomy_permission_metadata,
    build_local_workspace_ref,
    build_terminal_proof_packet,
    now_ms,
)
from openminion.modules.task.constants import (
    DEFAULT_INTEGRATED_SQLITE_SUBPATH,
)
from openminion.modules.task.project import (
    AutonomyLoopConditionKind,
    build_project_run_projection,
    save_project_run_checkpoint,
    validate_project_verifier,
)
from openminion.modules.task.project import checkpoints as project_checkpoints
from openminion.modules.task.project.turn import project_turn_result_from_response
from openminion.modules.task.project.models import SecurityRepairContext
from openminion.services.runtime.project_worker import (
    ProjectTurnRequest,
    ProjectTurnResult,
    project_turn_inbound_metadata,
)


def ensure_project_daemon_ready(
    args: argparse.Namespace, *, config_ref: str | None
) -> None:
    from openminion.cli.commands.daemon import ensure_daemon_running
    from openminion.cli.config import load_cli_config

    home_root = getattr(args, "home_root", None)
    data_root = getattr(args, "data_root", None)
    config = load_cli_config(
        config_ref,
        home_root=home_root,
        data_root=data_root,
    )
    ensure_daemon_running(
        config_ref,
        auto_start=bool(config.runtime.daemon_auto_start),
        home_root=home_root,
        data_root=data_root,
    )


def validate_project_cycle_interval(args: argparse.Namespace) -> None:
    value = getattr(args, "cycle_interval_seconds", None)
    if value is None:
        return
    if not bool(getattr(args, "unattended", False)):
        raise ValueError("--cycle-interval-seconds requires --unattended")
    if not 1 <= int(value) <= 3600:
        raise ValueError("--cycle-interval-seconds must be in 1..3600")


def launch_project(
    request: ProjectLaunchRequest,
    *,
    store: AutonomyRunStore,
    manager: TaskManager,
    args: argparse.Namespace | None = None,
) -> AutonomyRun:
    repair = request.security_repair
    revalidate_security_project(request, args=args, manager=manager)
    store.create(request.run)
    baseline: tuple[TestEvidence, ...] = ()
    if repair is not None:
        from openminion.modules.task.project.verification import (
            run_security_repair_baseline,
        )

        baseline = run_security_repair_baseline(
            repair,
            request.run.execution_selectors.verification_commands,
            timeout_seconds=request.run.execution_selectors.verification_timeout_seconds,
        )
    running = store.transition(
        request.run.run_id,
        status=AutonomyRunStatus.RUNNING,
        phase=AutonomyRunPhase.EXECUTE,
        operator_summary="Autonomy run started.",
    )
    initialize_project(
        manager,
        store,
        running,
        workspace_boundary_ref=build_local_workspace_ref(request.workspace_boundary),
        task_plan_required=request.task_plan_required,
        expected_checks=request.expected_checks,
        launch_approved=True,
        release_tools_approved=request.release_tools,
        success_criteria=request.success_criteria,
        source_request=request.source_request,
        security_repair=repair,
        security_repair_baseline=baseline,
    )
    running = store.require(running.run_id)
    if repair is not None:
        assert args is not None
        return record_security_project_baseline(
            repair, running, baseline, manager=manager, store=store, args=args
        )
    return running


def workspace_path_from_ref(workspace_ref: str | None) -> Path | None:
    if not workspace_ref or not workspace_ref.startswith("local:"):
        return None
    path_part = workspace_ref.removeprefix("local:").split("#", 1)[0]
    return Path(path_part).expanduser().resolve(strict=False)


def write_terminal_proof(
    store: AutonomyRunStore,
    run: AutonomyRun,
    *,
    validation_summary: str,
    final_operator_summary: str,
    cycle_summaries: tuple[str, ...] = (),
    commands_run: tuple[CommandEvidence, ...] = (),
    tests_run: tuple[TestEvidence, ...] = (),
    artifact_refs: tuple[str, ...] = (),
    verification_waiver: VerificationWaiver | None = None,
    delegation_results: tuple[DelegatedRoleEvidence, ...] = (),
    delegation_aggregation: dict[str, object] | None = None,
    context_budget: ContextBudgetEvidence | None = None,
) -> None:
    store.write_proof_packet(
        build_terminal_proof_packet(
            run,
            validation_summary=validation_summary,
            final_operator_summary=final_operator_summary,
            cycle_summaries=cycle_summaries,
            commands_run=commands_run,
            tests_run=tests_run,
            artifact_refs=artifact_refs,
            verification_waiver=verification_waiver,
            delegation_results=delegation_results,
            delegation_aggregation=delegation_aggregation,
            context_budget=context_budget,
        )
    )


def run_project_turn(
    run: AutonomyRun,
    request: ProjectTurnRequest,
    *,
    replay_response: str = "",
) -> ProjectTurnResult:
    summary = replay_response
    if not summary:
        workspace = workspace_path_from_ref(run.workspace_ref)
        api_result = _request_project_turn(run, request, workspace=workspace)
        if isinstance(api_result, ProjectTurnResult):
            return api_result
        return project_turn_result_from_response(
            response=api_result,
        )
    return ProjectTurnResult(
        summary=summary,
    )


def _request_project_turn(
    run: AutonomyRun,
    request: ProjectTurnRequest,
    *,
    workspace: Path | None,
) -> dict[str, object] | ProjectTurnResult:
    payload = {
        "message": request.prompt,
        "agent_id": run.execution_selectors.agent_id,
        "session_id": request.session_id,
        "channel": "console",
        "target": run.execution_selectors.turn_target,
        "deliver": False,
        "timeout_seconds": run.execution_selectors.turn_timeout_seconds,
        "inbound_metadata": project_turn_inbound_metadata(
            request,
            base={
                "source": "openminion.autonomy.project",
                "autonomy_run_id": request.run_id,
                "project_run_id": request.project_run_id,
                "task_id": request.task_id,
                "goal_id": request.goal_id,
                "cycle_id": request.cycle_id,
                "turn_timeout_seconds": str(
                    run.execution_selectors.turn_timeout_seconds
                ),
                **autonomy_permission_metadata(run.permission_profile_id),
                **({"workspace_root": str(workspace)} if workspace else {}),
            },
        ),
    }
    try:
        return run_turn(config_path=run.execution_selectors.config_ref, payload=payload)
    except TurnTimeoutError as exc:
        return _project_exception_result(
            "provider_timeout", AutonomyLoopConditionKind.RETRYABLE_FAILURE, exc
        )
    except TurnRequestError as exc:
        return _project_exception_result(
            "project_turn_request_error",
            AutonomyLoopConditionKind.TERMINAL_INABILITY,
            exc,
        )


def _project_exception_result(
    code: str,
    condition: AutonomyLoopConditionKind,
    error: Exception,
) -> ProjectTurnResult:
    message = str(error) or "project turn failed"
    return ProjectTurnResult(
        summary=message,
        condition=condition,
        error=ErrorInfo(
            code=code,
            message=message,
            namespace="task.project",
        ),
    )


def project_task_manager(args: argparse.Namespace) -> TaskManager:
    raw_path = str(getattr(args, "task_db", None) or "").strip()
    db_path = (
        Path(raw_path).expanduser().resolve(strict=False)
        if raw_path
        else resolve_module_cli_db_path(args, DEFAULT_INTEGRATED_SQLITE_SUBPATH)
    )
    return TaskManager.for_lifecycle_db(db_path=db_path)


def initialize_project(
    manager: TaskManager,
    store: AutonomyRunStore,
    run: AutonomyRun,
    *,
    workspace_boundary_ref: str | None = None,
    task_plan_required: bool = False,
    expected_checks: tuple[str, ...] = (),
    launch_approved: bool = False,
    release_tools_approved: bool = False,
    success_criteria: tuple[str, ...] = (),
    source_request: str = "",
    security_repair: SecurityRepairContext | None = None,
    security_repair_baseline: tuple[TestEvidence, ...] = (),
) -> None:
    assert run.task_id is not None
    manager.create_task(
        session_id=run.session_id,
        mode_name="project",
        goal=run.goal_text,
        agent_id=run.execution_selectors.agent_id,
        task_id=run.task_id,
    )
    project_ref = f"project:{run.run_id}"
    project_run = build_project_run_projection(
        run,
        objective_ledger_ref=f"{project_ref}:objective",
        evidence_ledger_ref=f"{project_ref}:evidence",
        resume_packet_ref=f"{project_ref}:resume",
        operator_decision_log_ref=f"{project_ref}:operator-decisions",
        capability_plan_ref=f"{project_ref}:capabilities",
        metrics_summary_ref=f"{project_ref}:metrics",
    ).model_copy(update={"current_milestone": run.goal_text})
    checkpoint_id = f"prun_{run.run_id}:initial"
    save_project_run_checkpoint(
        manager,
        project_run,
        checkpoint_id=checkpoint_id,
        payload={
            "decision": ProjectCycleDecision.CONTINUE.value,
            "replan_count": 0,
            **(
                {
                    "security_repair": security_repair.model_dump(mode="json"),
                    "security_repair_baseline": [
                        item.model_dump(mode="json")
                        for item in security_repair_baseline
                    ],
                }
                if security_repair is not None
                else {}
            ),
            **project_checkpoints.initial_repository_lifecycle_payload(
                run,
                project_run,
                workspace_boundary_ref=workspace_boundary_ref,
                task_plan_required=task_plan_required,
                expected_checks=expected_checks,
                launch_approved=launch_approved,
                release_tools_approved=release_tools_approved,
                success_criteria=success_criteria,
                verification_commands=run.execution_selectors.verification_commands,
                source_request=source_request,
            ),
        },
    )
    store.save(run.model_copy(update={"checkpoint_id": checkpoint_id}))


def resume_project_task(
    manager: TaskManager,
    run: AutonomyRun,
) -> AutonomyRunError | None:
    if not run.task_id:
        return AutonomyRunError(
            code="PROJECT_TASK_MISSING",
            message="Autonomy run is missing its durable task id.",
        )
    task = manager.get_task(run.task_id)
    if task is None:
        return AutonomyRunError(
            code="PROJECT_TASK_MISSING",
            message="Durable project task is missing from the configured task store.",
        )
    if task.state == TaskLifecycleState.PAUSED:
        manager.transition_task(task_id=run.task_id, to_state=TaskLifecycleState.ACTIVE)
    return None


def resume_project_run(
    manager: TaskManager,
    store: AutonomyRunStore,
    run: AutonomyRun,
    *,
    workspace: Path,
    waiver: VerificationWaiver | None,
) -> AutonomyRun:
    if run.status in {AutonomyRunStatus.COMPLETED, AutonomyRunStatus.CANCELLED}:
        raise RuntimeError(f"autonomy run cannot be resumed from {run.status}")
    error = verifier_preflight_error(run, workspace=workspace, waiver=waiver)
    if error is not None:
        blocked = run.model_copy(
            update={
                "status": AutonomyRunStatus.BLOCKED,
                "phase": AutonomyRunPhase.CLOSED,
                "operator_summary": "Autonomy run blocked before provider execution.",
                "next_action_hint": "Resume after configuring an available verifier.",
                "last_error": error,
                "updated_at_ms": now_ms(),
            }
        )
        store.save(blocked)
        return blocked
    checkpoint = project_checkpoints.load_latest_project_checkpoint(
        manager, task_id=run.task_id or ""
    )
    if checkpoint is None:
        return store.transition(
            run.run_id,
            status=AutonomyRunStatus.BLOCKED,
            phase=AutonomyRunPhase.CLOSED,
            operator_summary="Autonomy run blocked because its durable checkpoint is unavailable.",
            next_action_hint="Restore the original task database and resume this run.",
            error=AutonomyRunError(
                code=(
                    "PROJECT_CHECKPOINT_MISSING"
                    if manager.get_task(run.task_id or "") is not None
                    else "PROJECT_TASK_MISSING"
                ),
                message="Durable project state is missing from the configured task store.",
            ),
        )
    resumed_checkpoint = None
    if checkpoint.payload.get("proof_recovery_failed"):
        payload = dict(checkpoint.payload)
        payload.pop("proof_recovery_failed", None)
        payload.pop("error", None)
        payload["decision_reason"] = "terminal_proof_recovery_resumed"
        resumed_checkpoint = save_project_run_checkpoint(
            manager,
            checkpoint.project_run.model_copy(update={"updated_at_ms": now_ms()}),
            checkpoint_id=(
                f"{checkpoint.project_run.project_run_id}:proof-recovery-resumed:"
                f"{now_ms()}"
            ),
            payload=payload,
        )
    running = store.transition(
        run.run_id,
        status=AutonomyRunStatus.RUNNING,
        phase=AutonomyRunPhase.EXECUTE,
        operator_summary="Autonomy run resumed.",
    )
    if resumed_checkpoint is not None:
        running = running.model_copy(
            update={
                "checkpoint_id": resumed_checkpoint.checkpoint_id,
                "workspace_ref": resumed_checkpoint.project_run.workspace_ref,
            }
        )
        store.save(running)
    task_error = resume_project_task(manager, running)
    if task_error is not None:
        return store.transition(
            run.run_id,
            status=AutonomyRunStatus.BLOCKED,
            phase=AutonomyRunPhase.CLOSED,
            operator_summary=(
                "Autonomy run blocked because durable project state is unavailable."
            ),
            next_action_hint="Restore the original task database or start a new run.",
            error=task_error,
        )
    return store.require(run.run_id)


def apply_resume_overrides(
    args: argparse.Namespace,
    store: AutonomyRunStore,
    run: AutonomyRun,
    *,
    waiver: VerificationWaiver | None,
) -> AutonomyRun:
    selector_updates: dict[str, object] = {}
    agent = str(getattr(args, "agent", None) or "").strip()
    if agent:
        selector_updates["agent_id"] = agent
    config_ref = str(getattr(args, "config", None) or "").strip()
    if config_ref:
        selector_updates["config_ref"] = config_ref
    commands = tuple(getattr(args, "verify_command", ()) or ())
    if commands:
        selector_updates["verification_commands"] = commands
    turn_timeout = getattr(args, "turn_timeout_seconds", None)
    if turn_timeout is not None:
        selector_updates["turn_timeout_seconds"] = int(turn_timeout)
    verification_timeout = getattr(args, "verification_timeout_seconds", None)
    if verification_timeout is not None:
        selector_updates["verification_timeout_seconds"] = int(verification_timeout)
    domain = str(getattr(args, "verification_domain", None) or "").strip()
    if domain:
        selector_updates["verification_domain"] = domain
    effective_domain = domain or run.execution_selectors.verification_domain
    preserved_evidence_kinds = tuple(
        kind
        for kind in run.execution_selectors.required_evidence_kinds
        if kind not in {"verification", "waiver"}
        and not (kind == "repository_status" and effective_domain != "coding")
    )
    workspace = workspace_path_from_ref(run.workspace_ref)
    if (
        domain == "coding"
        and workspace is not None
        and (workspace / ".git").exists()
        and "repository_status" not in preserved_evidence_kinds
    ):
        preserved_evidence_kinds = (*preserved_evidence_kinds, "repository_status")
    if waiver is not None:
        selector_updates["verification_waiver_reason"] = waiver.reason
        selector_updates["required_evidence_kinds"] = (
            "waiver",
            *preserved_evidence_kinds,
        )
    elif commands:
        selector_updates["verification_waiver_reason"] = None
        selector_updates["required_evidence_kinds"] = (
            "verification",
            *preserved_evidence_kinds,
        )
    elif domain:
        selector_updates["required_evidence_kinds"] = (
            (
                "waiver"
                if run.execution_selectors.verification_waiver_reason
                else "verification"
            ),
            *preserved_evidence_kinds,
        )

    policy = run.continuation_policy
    policy_updates: dict[str, int] = {}
    for field in ("max_iterations", "max_wall_clock_ms", "max_tool_calls"):
        value = getattr(args, field, None)
        if value is not None:
            policy_updates[field] = max(0, int(value))
    if policy_updates:
        policy = policy.model_copy(update=policy_updates)
    updated = run.model_copy(
        update={
            "continuation_policy": policy,
            "execution_selectors": run.execution_selectors.model_copy(
                update=selector_updates
            ),
            "updated_at_ms": now_ms(),
        }
    )
    store.save(updated)
    return updated


def verifier_preflight_error(
    run: AutonomyRun,
    *,
    workspace: Path,
    waiver: VerificationWaiver | None,
) -> AutonomyRunError | None:
    try:
        validate_project_verifier(
            run.execution_selectors.verification_commands,
            workspace=workspace,
            required=(
                waiver is None
                and not run.execution_selectors.verification_waiver_reason
            ),
        )
    except ValueError as exc:
        code = (
            "VERIFICATION_REQUIRED"
            if not run.execution_selectors.verification_commands
            else "VERIFIER_UNAVAILABLE"
        )
        return AutonomyRunError(code=code, message=str(exc))
    return None


def persisted_verification_waiver(run: AutonomyRun) -> VerificationWaiver | None:
    reason = str(run.execution_selectors.verification_waiver_reason or "").strip()
    if not reason:
        return None
    return VerificationWaiver(reason=reason, recorded_at_ms=run.updated_at_ms)


def schedule_unattended_project(
    args: argparse.Namespace,
    store: AutonomyRunStore,
    manager: TaskManager,
    run: AutonomyRun,
) -> AutonomyRun:
    cycle_interval_seconds = int(getattr(args, "cycle_interval_seconds", None) or 1)
    cron_store = configured_cron_store(
        args,
        config_ref=run.execution_selectors.config_ref,
    )
    try:
        return schedule_project_wake(
            cron_store=cron_store,
            store=store,
            manager=manager,
            run=run,
            cycle_interval_seconds=cycle_interval_seconds,
        )
    finally:
        cron_store.close()


def schedule_project_wake(
    *,
    cron_store: Any,
    store: AutonomyRunStore,
    manager: TaskManager,
    run: AutonomyRun,
    cycle_interval_seconds: int = 1,
) -> AutonomyRun:
    assert run.task_id is not None
    task = manager.get_task(run.task_id)
    checkpoint = project_checkpoints.load_latest_project_checkpoint(
        manager, task_id=run.task_id
    )
    linked_job_id = str(task.metadata.get("linked_cron_job_id") or "") if task else ""
    existing_job = cron_store.get_cron_job(linked_job_id) if linked_job_id else None
    if existing_job is not None:
        payload = existing_job["payload"]
        if (
            payload.get("kind") != "projectCycle"
            or payload.get("run_id") != run.run_id
            or payload.get("task_id") != run.task_id
        ):
            raise ValueError("linked wake does not belong to this project")
        job_id = linked_job_id
    else:
        job_id = (
            checkpoint.project_run.next_wake_job_id
            if checkpoint is not None and checkpoint.project_run.next_wake_job_id
            else f"prun_{run.run_id}:wake:{checkpoint.project_run.committed_cycle_count if checkpoint else 0}"
        )
    prior_metadata = dict(task.metadata) if task is not None else None
    if existing_job is None:
        cron_store.add_cron_job(
            name=f"Project cycle {run.run_id}",
            schedule={
                "kind": "at",
                "at": (
                    datetime.now(timezone.utc)
                    + timedelta(seconds=cycle_interval_seconds)
                ).isoformat(),
            },
            payload={
                "kind": "projectCycle",
                "run_id": run.run_id,
                "task_id": run.task_id,
                "goal_id": run.goal_id,
                "session_id": run.session_id,
                "user": run.execution_selectors.turn_target,
                "cycle_interval_seconds": cycle_interval_seconds,
            },
            agent_id=run.execution_selectors.agent_id,
            session_target="isolated",
            delivery={"mode": "none"},
            delete_after_run=True,
            max_concurrency=1,
            job_id=job_id,
        )
    try:
        if task is not None:
            metadata = dict(task.metadata)
            metadata["linked_cron_job_id"] = job_id
            manager.update_task_metadata(task_id=run.task_id, metadata=metadata)
        scheduled = run.model_copy(
            update={
                "continuation_policy": run.continuation_policy.model_copy(
                    update={"resume_on_daemon_restart": True}
                ),
                "operator_summary": (
                    "Autonomy project scheduled for unattended execution."
                ),
                "next_action_hint": f"Waiting for project cycle {job_id}.",
                "updated_at_ms": now_ms(),
            }
        )
        store.save(scheduled)
        return scheduled
    except (OSError, RuntimeError, ValueError, sqlite3.Error):
        if existing_job is None:
            cron_store.delete_cron_job(job_id)
        if prior_metadata is not None:
            manager.update_task_metadata(
                task_id=run.task_id,
                metadata=prior_metadata,
            )
        raise


def cancel_project_task_wake(
    args: argparse.Namespace,
    run: AutonomyRun,
    manager: TaskManager,
) -> None:
    assert run.task_id is not None
    task = manager.get_task(run.task_id)
    if task is None:
        return
    linked_job_id = str(task.metadata.get("linked_cron_job_id") or "").strip()
    if task.state in {TaskLifecycleState.ACTIVE, TaskLifecycleState.PAUSED}:
        manager.lifecycle_repository.transition(
            task_id=run.task_id,
            to_state=TaskLifecycleState.CANCELLED,
        )
    if not linked_job_id:
        return
    cron_store = configured_cron_store(
        args,
        config_ref=run.execution_selectors.config_ref,
    )
    try:
        cron_store.delete_cron_job(linked_job_id)
    finally:
        cron_store.close()
    manager.update_task_metadata(
        task_id=run.task_id,
        metadata={
            key: value
            for key, value in task.metadata.items()
            if key != "linked_cron_job_id"
        },
    )


def configured_cron_store(
    args: argparse.Namespace,
    *,
    config_ref: str | None,
) -> Any:
    from openminion.cli.commands.status.session_store import build_status_session_store
    from openminion.cli.config import load_cli_manager_from_args

    config_args = argparse.Namespace(**vars(args))
    if config_ref:
        config_args.config = config_ref
    config_manager = load_cli_manager_from_args(config_args)
    return build_status_session_store(config_args, config_manager.base_config)


__all__ = [
    "apply_resume_overrides",
    "cancel_project_task_wake",
    "configured_cron_store",
    "initialize_project",
    "persisted_verification_waiver",
    "project_task_manager",
    "run_project_turn",
    "resume_project_task",
    "schedule_unattended_project",
    "schedule_project_wake",
    "validate_project_cycle_interval",
    "verifier_preflight_error",
    "workspace_path_from_ref",
]
