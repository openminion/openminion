"""CLI composition helpers for durable autonomy projects."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from openminion.api.turns import run_turn
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
    build_project_run_projection,
    save_project_run_checkpoint,
    validate_project_verifier,
)
from openminion.services.runtime.project_worker import (
    ProjectTurnRequest,
    ProjectTurnResult,
    project_condition_from_metadata,
    project_metadata_refs,
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
    metadata: dict[str, object] = {}
    summary = replay_response
    if not summary:
        workspace = workspace_path_from_ref(run.workspace_ref)
        response = run_turn(
            config_path=run.execution_selectors.config_ref,
            payload={
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
                        **(
                            {"workspace_root": str(workspace)}
                            if workspace is not None
                            else {}
                        ),
                    },
                ),
            },
        )
        summary = (
            str(response.get("final_text", "") or response.get("body", "")).strip()
            or "Project cycle completed without visible final text."
        )
        raw_metadata = response.get("metadata")
        if isinstance(raw_metadata, dict):
            metadata = raw_metadata

    evidence_refs = project_metadata_refs(metadata, "evidence_refs", "artifact_refs")
    evidence_kinds = project_metadata_refs(metadata, "evidence_kinds")
    tool_results = _project_tool_results(metadata)
    tool_result_refs = tuple(
        f"tool-call:{call_id}"
        for item in tool_results
        if bool(item.get("ok"))
        and (
            call_id := str(item.get("call_id") or item.get("command_id") or "").strip()
        )
    )
    tool_call_count = metadata.get("tool_call_count", len(tool_results))
    if isinstance(tool_call_count, str) and tool_call_count.isdigit():
        tool_call_count = int(tool_call_count)
    return ProjectTurnResult(
        summary=summary,
        condition=project_condition_from_metadata(metadata),
        evidence_refs=tuple(dict.fromkeys((*evidence_refs, *tool_result_refs))),
        evidence_kinds=tuple(
            dict.fromkeys(
                (*evidence_kinds, *(("tool_result",) if tool_result_refs else ()))
            )
        ),
        effect_refs=project_metadata_refs(metadata, "effect_refs"),
        tool_call_count=int(tool_call_count) if isinstance(tool_call_count, int) else 0,
    )


def _project_tool_results(metadata: dict[str, object]) -> tuple[dict[str, object], ...]:
    for key in ("tool_calls_cumulative", "tool_results"):
        raw = metadata.get(key)
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except json.JSONDecodeError:
                continue
        if isinstance(raw, list):
            return tuple(item for item in raw if isinstance(item, dict))
    return ()


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
                "at": (datetime.now(timezone.utc) + timedelta(seconds=1)).isoformat(),
            },
            payload={
                "kind": "projectCycle",
                "run_id": run.run_id,
                "task_id": run.task_id,
                "goal_id": run.goal_id,
                "session_id": run.session_id,
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
