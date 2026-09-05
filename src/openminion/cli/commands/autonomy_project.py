"""CLI composition helpers for durable autonomy projects."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
from pathlib import Path

from openminion.api.turns import run_turn
from openminion.api.turns import TurnRequestError, TurnTimeoutError
from openminion.base.errors import ErrorInfo
from openminion.modules.cli_common import resolve_module_cli_db_path
from openminion.modules.task import (
    AutonomyRun,
    AutonomyRunError,
    AutonomyRunStore,
    ProjectCycleDecision,
    TaskLifecycleState,
    TaskManager,
)
from openminion.modules.task.autonomy import (
    VerificationWaiver,
    autonomy_permission_metadata,
    now_ms,
)
from openminion.modules.task.constants import DEFAULT_INTEGRATED_SQLITE_SUBPATH
from openminion.modules.task.project import (
    AutonomyLoopConditionKind,
    build_project_run_projection,
    save_project_run_checkpoint,
    validate_project_verifier,
)
from openminion.modules.task.project import checkpoints as project_checkpoints
from openminion.modules.task.project.turn import project_turn_result_from_response
from openminion.services.runtime.project_worker import (
    ProjectTurnRequest,
    ProjectTurnResult,
    project_turn_inbound_metadata,
)


def workspace_path_from_ref(workspace_ref: str | None) -> Path | None:
    if not workspace_ref or not workspace_ref.startswith("local:"):
        return None
    path_part = workspace_ref.removeprefix("local:").split("#", 1)[0]
    return Path(path_part).expanduser().resolve(strict=False)


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
        "target": "autonomy",
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
            **project_checkpoints.initial_repository_lifecycle_payload(
                run,
                project_run,
                workspace_boundary_ref=workspace_boundary_ref,
            ),
        },
    )
    store.save(run.model_copy(update={"checkpoint_id": checkpoint_id}))


def resume_project_task(
    manager: TaskManager,
    store: AutonomyRunStore,
    run: AutonomyRun,
) -> None:
    if not run.task_id:
        raise RuntimeError("autonomy run is missing its durable task id")
    task = manager.get_task(run.task_id)
    if task is None:
        initialize_project(manager, store, run)
        return
    if task.state == TaskLifecycleState.PAUSED:
        manager.transition_task(task_id=run.task_id, to_state=TaskLifecycleState.ACTIVE)


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
    if waiver is not None:
        selector_updates["verification_waiver_reason"] = waiver.reason
        selector_updates["required_evidence_kinds"] = ("waiver",)
    elif commands:
        selector_updates["required_evidence_kinds"] = ("verification",)

    policy = run.continuation_policy
    max_iterations = getattr(args, "max_iterations", None)
    if max_iterations is not None:
        policy = policy.model_copy(
            update={"max_iterations": max(0, int(max_iterations))}
        )
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
    job_id = f"prun_{run.run_id}:wake:0"
    try:
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
                "cycle_interval_seconds": cycle_interval_seconds,
            },
            agent_id=run.execution_selectors.agent_id,
            session_target="isolated",
            delivery={"mode": "none"},
            delete_after_run=True,
            max_concurrency=1,
            job_id=job_id,
        )
    finally:
        cron_store.close()
    assert run.task_id is not None
    task = manager.get_task(run.task_id)
    if task is not None:
        metadata = dict(task.metadata)
        metadata["linked_cron_job_id"] = job_id
        manager.update_task_metadata(task_id=run.task_id, metadata=metadata)
    scheduled = run.model_copy(
        update={
            "continuation_policy": run.continuation_policy.model_copy(
                update={"resume_on_daemon_restart": True}
            ),
            "operator_summary": "Autonomy project scheduled for unattended execution.",
            "next_action_hint": f"Waiting for project cycle {job_id}.",
            "updated_at_ms": now_ms(),
        }
    )
    store.save(scheduled)
    return scheduled


def configured_cron_store(
    args: argparse.Namespace,
    *,
    config_ref: str | None,
):
    from openminion.cli.commands.status.session_store import build_status_session_store
    from openminion.cli.config import load_cli_manager_from_args

    config_args = argparse.Namespace(**vars(args))
    if config_ref:
        config_args.config = config_ref
    config_manager = load_cli_manager_from_args(config_args)
    return build_status_session_store(config_args, config_manager.base_config)


__all__ = [
    "apply_resume_overrides",
    "configured_cron_store",
    "initialize_project",
    "persisted_verification_waiver",
    "project_task_manager",
    "run_project_turn",
    "resume_project_task",
    "schedule_unattended_project",
    "verifier_preflight_error",
    "workspace_path_from_ref",
]
