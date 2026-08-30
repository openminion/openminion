from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

from openminion.base.errors import error_info_from_exception
from openminion.base.types import Message
from openminion.cli.commands.autonomy_project import (
    apply_resume_overrides,
    configured_cron_store,
    initialize_project,
    persisted_verification_waiver,
    project_task_manager,
    run_project_turn,
    resume_project_task,
    schedule_unattended_project,
    verifier_preflight_error,
    workspace_path_from_ref,
)
from openminion.cli.commands.autonomy_inspect import (
    list_autonomy_runs,
    show_autonomy_run,
)
from openminion.cli.parser.flags import add_json_output_flag
from openminion.cli.presentation.json_output import print_json_payload
from openminion.modules.task.autonomy import (
    AutonomyRun,
    AutonomyRunError,
    AutonomyRunPhase,
    AutonomyRunStatus,
    AutonomyRunStore,
    CommandEvidence,
    ContextBudgetEvidence,
    DelegatedRole,
    DelegatedRoleEvidence,
    DelegatedRoleStatus,
    EvidenceStatus,
    TestEvidence,
    TestEvidenceStatus,
    VerificationDomain,
    VerificationWaiver,
    build_autonomy_run,
    build_local_workspace_ref,
    build_terminal_proof_packet,
    now_ms,
)
from openminion.modules.task.project import (
    ProjectControlAction,
    ProjectCycleDecision,
    ProjectOperatorInboxItem,
    apply_project_control,
    build_project_operator_inbox_item,
    load_latest_project_checkpoint,
    render_project_control_result,
    render_project_operator_inbox_item,
    run_project_verification_commands,
)
from openminion.modules.task.project.checkpoints import project_cycle_summaries
from openminion.modules.task.project.reports import (
    build_project_report_from_task,
    render_project_report,
)
from openminion.modules.task import TaskLifecycleState, TaskManager
from openminion.modules.task.constants import (
    DEFAULT_PROJECT_TURN_TIMEOUT_SECONDS,
    DEFAULT_PROJECT_VERIFICATION_TIMEOUT_SECONDS,
)
from openminion.services.runtime.project_worker import (
    ProjectTurnRequest,
    ProjectTurnResult,
    ProjectWorker,
    ProjectWorkerResult,
    project_cycle_claim_ttl_seconds,
)
from openminion.modules.context.budget import (
    ContextBudgetConfig,
    assemble_budgeted_context,
)


def run_autonomy(args: argparse.Namespace) -> int:
    action = str(getattr(args, "autonomy_command", "") or "").strip().lower()
    store = AutonomyRunStore()
    if action == "start":
        return _start(args, store)
    if action == "list":
        return int(list_autonomy_runs(args, store))
    if action == "show":
        return int(show_autonomy_run(args, store))
    if action == "resume":
        return _resume(args, store)
    if action == "cancel":
        return _cancel(args, store)
    if action == "project":
        return _project(args)
    raise RuntimeError(f"Unknown autonomy command: {action}")


def _validate_cycle_interval(args: argparse.Namespace) -> None:
    value = getattr(args, "cycle_interval_seconds", None)
    if value is None:
        return
    if not bool(getattr(args, "unattended", False)):
        raise ValueError("--cycle-interval-seconds requires --unattended")
    if not 1 <= int(value) <= 3600:
        raise ValueError("--cycle-interval-seconds must be in 1..3600")


def _start(args: argparse.Namespace, store: AutonomyRunStore) -> int:
    _validate_cycle_interval(args)
    goal = _resolve_goal(args)
    workspace = _resolve_workspace(args)
    verification_commands = tuple(getattr(args, "verify_command", ()) or ())
    turn_timeout_seconds = int(
        getattr(args, "turn_timeout_seconds", None)
        or DEFAULT_PROJECT_TURN_TIMEOUT_SECONDS
    )
    verification_timeout_seconds = int(
        getattr(args, "verification_timeout_seconds", None)
        or DEFAULT_PROJECT_VERIFICATION_TIMEOUT_SECONDS
    )
    waiver = _verification_waiver(args)
    run = build_autonomy_run(
        goal_text=goal,
        goal_id=_clean(getattr(args, "goal_id", None)) or None,
        session_id=_clean(getattr(args, "session", None)) or "autonomy",
        workspace_ref=build_local_workspace_ref(workspace),
        max_iterations=max(0, int(getattr(args, "max_iterations", 1))),
        permission_profile_id=_clean(getattr(args, "permission_profile", None))
        or "local-safe",
        agent_id=_clean(getattr(args, "agent", None)) or "default",
        config_ref=_clean(getattr(args, "config", None)) or None,
        verification_domain=cast(
            VerificationDomain,
            str(getattr(args, "verification_domain", "cross_application")),
        ),
        verification_commands=verification_commands,
        turn_timeout_seconds=turn_timeout_seconds,
        verification_timeout_seconds=verification_timeout_seconds,
        verification_waiver_reason=waiver.reason if waiver is not None else None,
        required_evidence_kinds=("waiver",)
        if waiver is not None
        else ("verification",),
    )
    run = run.model_copy(
        update={
            "goal_id": run.goal_id or f"goal_{run.run_id}",
            "task_id": f"task_{run.run_id}",
        }
    )
    store.create(run)
    if run.continuation_policy.max_iterations < 1:
        error = AutonomyRunError(
            code="BUDGET_EXHAUSTED",
            message="max_iterations must be at least 1 to execute a run",
        )
        blocked = store.transition(
            run.run_id,
            status=AutonomyRunStatus.BLOCKED,
            phase=AutonomyRunPhase.CLOSED,
            operator_summary="Autonomy run blocked before execution.",
            next_action_hint="Resume with --max-iterations greater than zero.",
            error=error,
        )
        return _write_terminal_output(
            args,
            store,
            blocked,
            validation_summary="Blocked before execution by continuation policy.",
            final_operator_summary="Autonomy run blocked before execution.",
        )

    verifier_error = verifier_preflight_error(
        run,
        workspace=workspace,
        waiver=waiver,
    )
    if verifier_error is not None:
        blocked = store.transition(
            run.run_id,
            status=AutonomyRunStatus.BLOCKED,
            phase=AutonomyRunPhase.CLOSED,
            operator_summary="Autonomy run blocked before provider execution.",
            next_action_hint="Resume after configuring an available verifier.",
            error=verifier_error,
        )
        return _write_terminal_output(
            args,
            store,
            blocked,
            validation_summary="Blocked before provider execution by verifier preflight.",
            final_operator_summary="Autonomy run blocked by verifier preflight.",
        )

    running = store.transition(
        run.run_id,
        status=AutonomyRunStatus.RUNNING,
        phase=AutonomyRunPhase.EXECUTE,
        operator_summary="Autonomy run started.",
    )
    manager = project_task_manager(args)
    initialize_project(manager, store, running)
    if bool(getattr(args, "unattended", False)):
        scheduled = schedule_unattended_project(args, store, manager, running)
        return _print_run(args, scheduled)
    return _run_foreground_project(args, store, manager, running, workspace=workspace)


def _resume(args: argparse.Namespace, store: AutonomyRunStore) -> int:
    _validate_cycle_interval(args)
    run = store.require(str(args.run_id))
    if run.status in {AutonomyRunStatus.COMPLETED, AutonomyRunStatus.CANCELLED}:
        raise RuntimeError(f"autonomy run cannot be resumed from {run.status}")
    workspace = workspace_path_from_ref(run.workspace_ref) or Path.cwd()
    waiver = _verification_waiver(args)
    run = apply_resume_overrides(args, store, run, waiver=waiver)
    manager = project_task_manager(args)
    verifier_error = verifier_preflight_error(
        run,
        workspace=workspace,
        waiver=waiver,
    )
    if verifier_error is not None:
        blocked = run.model_copy(
            update={
                "status": AutonomyRunStatus.BLOCKED,
                "phase": AutonomyRunPhase.CLOSED,
                "operator_summary": "Autonomy run blocked before provider execution.",
                "next_action_hint": "Resume after configuring an available verifier.",
                "last_error": verifier_error,
                "updated_at_ms": now_ms(),
            }
        )
        store.save(blocked)
        return _write_terminal_output(
            args,
            store,
            blocked,
            validation_summary="Blocked before provider execution by verifier preflight.",
            final_operator_summary="Autonomy run blocked by verifier preflight.",
            cycle_summaries=project_cycle_summaries(
                manager,
                task_id=run.task_id or "",
            ),
        )
    running = store.transition(
        run.run_id,
        status=AutonomyRunStatus.RUNNING,
        phase=AutonomyRunPhase.EXECUTE,
        operator_summary="Autonomy run resumed.",
    )
    resume_project_task(manager, store, running)
    if bool(getattr(args, "unattended", False)):
        scheduled = schedule_unattended_project(args, store, manager, running)
        return _print_run(args, scheduled)
    return _run_foreground_project(args, store, manager, running, workspace=workspace)


def _run_foreground_project(
    args: argparse.Namespace,
    store: AutonomyRunStore,
    manager: TaskManager,
    run: AutonomyRun,
    *,
    workspace: Path,
) -> int:
    try:
        result = _execute_project(args, store, manager, run, workspace=workspace)
    except KeyboardInterrupt:
        current = store.require(run.run_id)
        if current.status != AutonomyRunStatus.RUNNING:
            _print_run(args, current)
            return 130
        task = manager.get_task(run.task_id or "")
        if task is not None and task.state == TaskLifecycleState.ACTIVE:
            manager.transition_task(
                task_id=task.task_id,
                to_state=TaskLifecycleState.PAUSED,
            )
        interrupted = store.transition(
            run.run_id,
            status=AutonomyRunStatus.BLOCKED,
            phase=AutonomyRunPhase.CLOSED,
            operator_summary="Autonomy project interrupted by operator.",
            next_action_hint=f"Resume with `openminion autonomy resume {run.run_id}`.",
            error=AutonomyRunError(
                code="OPERATOR_INTERRUPTED",
                message="Foreground project execution was interrupted by the operator.",
            ),
        )
        _write_terminal_output(
            args,
            store,
            interrupted,
            validation_summary="Foreground project execution was interrupted.",
            final_operator_summary="Autonomy project interrupted by operator.",
            cycle_summaries=project_cycle_summaries(
                manager,
                task_id=run.task_id or "",
            ),
        )
        return 130
    return _print_run(args, result.run)


def _cancel(args: argparse.Namespace, store: AutonomyRunStore) -> int:
    run = store.require(str(args.run_id))
    manager = project_task_manager(args) if run.task_id else None
    if manager is not None:
        task = manager.get_task(run.task_id)
        if task is not None and task.state in {
            TaskLifecycleState.ACTIVE,
            TaskLifecycleState.PAUSED,
        }:
            linked_job_id = str(task.metadata.get("linked_cron_job_id") or "").strip()
            manager.transition_task(
                task_id=run.task_id,
                to_state=TaskLifecycleState.CANCELLED,
            )
            if linked_job_id:
                cron_store = configured_cron_store(
                    args,
                    config_ref=run.execution_selectors.config_ref,
                )
                try:
                    cron_store.delete_cron_job(linked_job_id)
                finally:
                    cron_store.close()
    cancelled = store.transition(
        run.run_id,
        status=AutonomyRunStatus.CANCELLED,
        phase=AutonomyRunPhase.CLOSED,
        operator_summary="Autonomy run cancelled by operator.",
        next_action_hint=None,
    )
    return _write_terminal_output(
        args,
        store,
        cancelled,
        validation_summary="Cancelled by operator request.",
        final_operator_summary="Autonomy run cancelled by operator.",
        cycle_summaries=(
            project_cycle_summaries(manager, task_id=run.task_id or "")
            if manager is not None
            else ()
        ),
    )


def _execute_project(
    args: argparse.Namespace,
    store: AutonomyRunStore,
    manager: TaskManager,
    run: AutonomyRun,
    *,
    workspace: Path,
) -> ProjectWorkerResult:
    worker = ProjectWorker(
        task_manager=manager,
        autonomy_store=store,
        turn=lambda request: _project_turn(args, run=run, request=request),
        verify=lambda: run_project_verification_commands(
            run.execution_selectors.verification_commands,
            workspace=workspace,
            timeout_seconds=run.execution_selectors.verification_timeout_seconds,
        ),
        claim_ttl_seconds=project_cycle_claim_ttl_seconds(run),
    )
    checkpoint = load_latest_project_checkpoint(manager, task_id=str(run.task_id))
    committed = checkpoint.project_run.committed_cycle_count if checkpoint else 0
    remaining = max(1, run.continuation_policy.max_iterations - committed)
    try:
        result = worker.run(run.run_id, max_cycles=remaining)
    except Exception as exc:
        error_info = error_info_from_exception(exc, default_code=type(exc).__name__)
        failed = store.transition(
            run.run_id,
            status=AutonomyRunStatus.FAILED,
            phase=AutonomyRunPhase.CLOSED,
            operator_summary="Autonomy project failed.",
            next_action_hint="Inspect the proof packet before resuming.",
            error=AutonomyRunError(
                code=error_info.code,
                message=error_info.message,
            ),
        )
        _write_terminal_proof(
            store,
            failed,
            validation_summary="Project worker execution failed.",
            final_operator_summary="Autonomy project failed.",
            cycle_summaries=project_cycle_summaries(
                manager,
                task_id=run.task_id or "",
            ),
        )
        checkpoint = load_latest_project_checkpoint(manager, task_id=str(run.task_id))
        if checkpoint is None:
            raise RuntimeError(
                "project checkpoint missing after worker failure"
            ) from exc
        return ProjectWorkerResult(
            run=store.require(run.run_id),
            project_run=checkpoint.project_run,
            decision=ProjectCycleDecision.BLOCKED,
            verification=(),
        )
    return _finalize_project_result(args, store, manager, result)


def _project_turn(
    args: argparse.Namespace,
    *,
    run: AutonomyRun,
    request: ProjectTurnRequest,
) -> ProjectTurnResult:
    result = run_project_turn(
        run,
        request,
        replay_response=_clean(getattr(args, "replay_response", None)),
    )
    return replace(
        result,
        summary=_synthesize_parent_summary(
            result.summary,
            delegation_results=_delegated_role_evidence(args),
        ),
    )


def _finalize_project_result(
    args: argparse.Namespace,
    store: AutonomyRunStore,
    manager: TaskManager,
    result: ProjectWorkerResult,
) -> ProjectWorkerResult:
    run = result.run
    error = None
    if result.decision in {
        ProjectCycleDecision.BLOCKED,
        ProjectCycleDecision.NEEDS_INPUT,
    }:
        reason = result.project_run.blocked_reason or "verification_failed"
        normalized_reason = reason.upper().replace(":", "_").replace("-", "_")
        code = (
            normalized_reason
            if normalized_reason.startswith("VERIFICATION_")
            else f"PROJECT_{normalized_reason}"
        )
        error = AutonomyRunError(
            code=code,
            message=reason,
        )
        run = run.model_copy(update={"last_error": error})
        store.save(run)
    if result.decision != ProjectCycleDecision.CONTINUE:
        waiver = _verification_waiver(args) or persisted_verification_waiver(run)
        delegation_results = _delegated_role_evidence(args)
        workspace = workspace_path_from_ref(run.workspace_ref) or Path.cwd()
        command = _command_evidence(args, workspace=workspace, started_at_ms=now_ms())
        command = command.model_copy(
            update={
                "ended_at_ms": now_ms(),
                "exit_code": 0,
                "status": EvidenceStatus.SUCCEEDED,
                "summary": "autonomy project reached terminal verification",
            }
        )
        _write_terminal_proof(
            store,
            run,
            validation_summary=_validation_summary(result.verification, waiver=waiver),
            final_operator_summary=run.operator_summary or "Autonomy project closed.",
            cycle_summaries=project_cycle_summaries(
                manager,
                task_id=run.task_id or "",
            ),
            commands_run=(command,),
            tests_run=result.verification,
            verification_waiver=waiver,
            delegation_results=delegation_results,
            delegation_aggregation=_delegation_aggregation(delegation_results),
            context_budget=_context_budget_evidence(
                args,
                goal=run.goal_text,
                delegation_results=delegation_results,
            ),
        )
        run = store.require(run.run_id)
    return ProjectWorkerResult(
        run=run,
        project_run=result.project_run,
        decision=result.decision,
        verification=result.verification,
        reconciled_only=result.reconciled_only,
    )


def _write_terminal_output(
    args: argparse.Namespace,
    store: AutonomyRunStore,
    run: AutonomyRun,
    *,
    validation_summary: str,
    final_operator_summary: str,
    cycle_summaries: tuple[str, ...] = (),
) -> int:
    _write_terminal_proof(
        store,
        run,
        validation_summary=validation_summary,
        final_operator_summary=final_operator_summary,
        cycle_summaries=cycle_summaries,
    )
    return _print_run(args, store.require(run.run_id))


def _write_terminal_proof(
    store: AutonomyRunStore,
    run: AutonomyRun,
    *,
    validation_summary: str,
    final_operator_summary: str,
    cycle_summaries: tuple[str, ...] = (),
    commands_run: tuple[CommandEvidence, ...] = (),
    tests_run: tuple[TestEvidence, ...] = (),
    verification_waiver: VerificationWaiver | None = None,
    delegation_results: tuple[DelegatedRoleEvidence, ...] = (),
    delegation_aggregation: dict[str, object] | None = None,
    context_budget: ContextBudgetEvidence | None = None,
) -> None:
    packet = build_terminal_proof_packet(
        run,
        validation_summary=validation_summary,
        final_operator_summary=final_operator_summary,
        cycle_summaries=cycle_summaries,
        commands_run=commands_run,
        tests_run=tests_run,
        verification_waiver=verification_waiver,
        delegation_results=delegation_results,
        delegation_aggregation=delegation_aggregation,
        context_budget=context_budget,
    )
    store.write_proof_packet(packet)


def _delegated_role_evidence(
    args: argparse.Namespace,
) -> tuple[DelegatedRoleEvidence, ...]:
    raw_values = getattr(args, "delegate_result", ()) or ()
    return tuple(
        _parse_delegated_role_evidence(raw) for raw in raw_values if _clean(raw)
    )


def _parse_delegated_role_evidence(raw: object) -> DelegatedRoleEvidence:
    parts = str(raw or "").split(":", 2)
    if len(parts) != 3:
        raise RuntimeError(
            "--delegate-result must use role:status:summary, for example "
            "worker:success:patched files"
        )
    role, status, summary = (part.strip() for part in parts)
    return DelegatedRoleEvidence(
        role=cast(DelegatedRole, role),
        status=cast(DelegatedRoleStatus, status),
        summary=summary,
    )


def _delegation_aggregation(
    delegation_results: tuple[DelegatedRoleEvidence, ...],
) -> dict[str, object] | None:
    if not delegation_results:
        return None
    status_counts = Counter(result.status for result in delegation_results)
    success_count = status_counts["success"]
    return {
        "total_children": len(delegation_results),
        "success_count": success_count,
        "failure_count": status_counts["failure"],
        "skipped_count": status_counts["skipped"],
        "canceled_count": status_counts["canceled"],
        "completed_required": success_count == len(delegation_results),
        "source_policy": "structural_merge",
        "child_ids": [result.role for result in delegation_results],
        "merged_payload": {
            result.role: {
                "status": result.status,
                "required": True,
                "payload": {"summary": result.summary},
            }
            for result in delegation_results
        },
    }


def _synthesize_parent_summary(
    base_summary: str,
    *,
    delegation_results: tuple[DelegatedRoleEvidence, ...],
) -> str:
    if not delegation_results:
        return base_summary
    role_lines = "; ".join(
        f"{result.role}={result.status}: {result.summary}"
        for result in delegation_results
    )
    return f"{base_summary}\n\nDelegation evidence: {role_lines}"


def _context_budget_evidence(
    args: argparse.Namespace,
    *,
    goal: str,
    delegation_results: tuple[DelegatedRoleEvidence, ...],
) -> ContextBudgetEvidence | None:
    max_tokens = int(getattr(args, "context_budget_tokens", 0) or 0)
    if max_tokens <= 0:
        return None
    required_facts = tuple(
        fact
        for fact in (
            _clean(value) for value in getattr(args, "context_required_fact", ()) or ()
        )
        if fact
    )
    system_messages = [
        Message(
            channel="system",
            target="autonomy.context_budget",
            body="\n".join(required_facts) if required_facts else "autonomy context",
        )
    ]
    history_messages = [
        Message(channel="user", target="autonomy.goal", body=goal),
        *[
            Message(
                channel="assistant",
                target=f"autonomy.delegate.{result.role}",
                body=result.summary,
                metadata={"role": result.role, "status": result.status},
            )
            for result in delegation_results
        ],
    ]
    before = assemble_budgeted_context(
        system_messages=system_messages,
        history_messages=history_messages,
        budget=ContextBudgetConfig(max_tokens=0),
    )
    after = assemble_budgeted_context(
        system_messages=system_messages,
        history_messages=history_messages,
        budget=ContextBudgetConfig(max_tokens=max_tokens, min_recent_messages=1),
    )
    return ContextBudgetEvidence(
        max_tokens=max_tokens,
        estimated_tokens_before=before.telemetry.estimated_tokens_total,
        estimated_tokens_after=after.telemetry.estimated_tokens_total,
        trimmed_count=after.telemetry.trimmed_count,
        overflow=after.telemetry.overflow,
        retained_required_facts=required_facts,
    )


def _verification_waiver(args: argparse.Namespace) -> VerificationWaiver | None:
    reason = _clean(getattr(args, "verification_waiver", None))
    if not reason:
        return None
    return VerificationWaiver(reason=reason, recorded_at_ms=now_ms())


def _validation_summary(
    verification: tuple[TestEvidence, ...],
    *,
    waiver: VerificationWaiver | None,
) -> str:
    if waiver is not None:
        return (
            "Replay/runtime execution completed with an explicit verification waiver."
        )
    if not verification:
        return "Replay/runtime execution completed; no verification command configured."
    if all(item.status == TestEvidenceStatus.PASSED for item in verification):
        return "Replay/runtime execution completed; verification commands passed."
    return "Replay/runtime execution completed; verification commands did not pass."


def _print_run(args: argparse.Namespace, run: AutonomyRun) -> int:
    if bool(getattr(args, "json", False)):
        print_json_payload({"ok": True, "run": run.model_dump(mode="json")})
        return 0
    print(f"Autonomy run {run.status.value}: {run.run_id}")
    print(f"  goal: {run.goal_text}")
    print(f"  proof: {run.proof_packet_ref or '-'}")
    if run.next_action_hint:
        print(f"  next: {run.next_action_hint}")
    return 0


def _project(args: argparse.Namespace) -> int:
    task_db = _clean(getattr(args, "task_db", None))
    if not task_db:
        raise RuntimeError("autonomy project requires --task-db")
    task_id = _clean(getattr(args, "task_id", None))
    if not task_id:
        raise RuntimeError("autonomy project requires a task id")
    action = ProjectControlAction(str(args.project_command))
    manager = TaskManager.for_lifecycle_db(db_path=Path(task_db))
    if action == ProjectControlAction.REPORT:
        report = build_project_report_from_task(manager, task_id=task_id)
        if bool(getattr(args, "json", False)):
            print_json_payload(
                {"ok": True, "project_report": report.model_dump(mode="json")}
            )
            return 0
        print(render_project_report(report))
        return 0
    result = apply_project_control(
        manager,
        task_id=task_id,
        action=action,
        priority=_clean(getattr(args, "priority", None)) or None,
        input_request_id=_clean(getattr(args, "input_request_id", None)) or None,
        answer=_clean(getattr(args, "answer", None)) or None,
        extra_iterations=int(getattr(args, "extra_iterations", 0) or 0),
        extra_wall_clock_ms=int(getattr(args, "extra_wall_clock_ms", 0) or 0),
        extra_tool_calls=int(getattr(args, "extra_tool_calls", 0) or 0),
    )
    inbox_item = _project_operator_inbox(manager, task_id=task_id)
    if bool(getattr(args, "json", False)):
        payload: dict[str, Any] = {
            "ok": True,
            "project": result.model_dump(mode="json"),
        }
        if inbox_item is not None:
            payload["operator_inbox"] = inbox_item.model_dump(mode="json")
        print_json_payload(payload)
        return 0
    print(render_project_control_result(result))
    if inbox_item is not None:
        print("")
        print(render_project_operator_inbox_item(inbox_item))
    return 0


def _project_operator_inbox(
    manager: TaskManager,
    *,
    task_id: str,
) -> ProjectOperatorInboxItem | None:
    checkpoint = load_latest_project_checkpoint(manager, task_id=task_id)
    if checkpoint is None:
        return None
    record = manager.get_task(task_id)
    current_step_ref = None
    if record is not None:
        current_step_ref = str(record.metadata.get("current_step_ref") or "") or None
    return build_project_operator_inbox_item(
        checkpoint.project_run,
        task_record=record,
        current_step_ref=current_step_ref,
        claim=manager.lifecycle_repository.get_project_cycle_claim(task_id),
    )


def _command_evidence(
    args: argparse.Namespace,
    *,
    workspace: Path,
    started_at_ms: int,
) -> CommandEvidence:
    command = ("openminion", "autonomy", str(getattr(args, "autonomy_command", "")))
    return CommandEvidence(
        command=command,
        cwd_ref=str(workspace),
        started_at_ms=started_at_ms,
        ended_at_ms=started_at_ms,
        exit_code=None,
        status=EvidenceStatus.BLOCKED,
        summary="autonomy command started",
    )


def _resolve_goal(args: argparse.Namespace) -> str:
    goal = _clean(getattr(args, "goal", None))
    if goal:
        return goal
    goal_file = _clean(getattr(args, "goal_file", None))
    if goal_file:
        path = Path(goal_file).expanduser().resolve(strict=False)
        if not path.exists():
            raise RuntimeError(f"goal file not found: {path}")
        text = path.read_text(encoding="utf-8").strip()
        if text:
            return text
    raise RuntimeError("--goal or --goal-file is required")


def _resolve_workspace(args: argparse.Namespace) -> Path:
    raw = _clean(getattr(args, "workspace", None))
    return Path(raw).expanduser().resolve(strict=False) if raw else Path.cwd()


def _clean(value: object) -> str:
    return str(value or "").strip()


def _add_execution_proof_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--replay-response",
        default="",
        help="Deterministic response used for replay-backed autonomy proof",
    )
    parser.add_argument(
        "--verify-command",
        action="append",
        default=[],
        help="Run this command after execution; failing commands block closeout",
    )
    parser.add_argument(
        "--turn-timeout-seconds",
        type=int,
        default=None,
        help=(
            "Maximum runtime for each project agent turn "
            f"(default: {DEFAULT_PROJECT_TURN_TIMEOUT_SECONDS})"
        ),
    )
    parser.add_argument(
        "--verification-timeout-seconds",
        type=int,
        default=None,
        help=(
            "Maximum runtime for each verification command "
            f"(default: {DEFAULT_PROJECT_VERIFICATION_TIMEOUT_SECONDS})"
        ),
    )
    parser.add_argument(
        "--require-verification",
        action="store_true",
        help="Block closeout unless a verification command runs or a waiver is recorded",
    )
    parser.add_argument(
        "--verification-waiver",
        default="",
        help="Explicit waiver reason when configured verification cannot pass",
    )
    parser.add_argument(
        "--delegate-result",
        action="append",
        default=[],
        help="Replay-backed delegated role evidence as role:status:summary",
    )
    parser.add_argument(
        "--context-budget-tokens",
        type=int,
        default=0,
        help="Emit context-budget proof for this autonomy run",
    )
    parser.add_argument(
        "--context-required-fact",
        action="append",
        default=[],
        help="Required fact expected to remain visible in context-budget proof",
    )


def _register_project_commands(
    autonomy_sub: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    project = autonomy_sub.add_parser("project", help="Control a project task run")
    project.add_argument(
        "--task-db",
        required=True,
        help="Task lifecycle SQLite database path",
    )
    project_sub = project.add_subparsers(dest="project_command", required=True)

    for action_name in ("status", "show", "pause", "resume", "cancel", "report"):
        command = project_sub.add_parser(action_name, help=f"{action_name} a project")
        command.add_argument("task_id")
        add_json_output_flag(command)
        command.set_defaults(handler=run_autonomy, needs_app=False)

    reprioritize = project_sub.add_parser(
        "reprioritize",
        help="Update project priority metadata",
    )
    reprioritize.add_argument("task_id")
    reprioritize.add_argument("--priority", required=True)
    add_json_output_flag(reprioritize)
    reprioritize.set_defaults(handler=run_autonomy, needs_app=False)

    answer_input = project_sub.add_parser(
        "answer-input-request",
        help="Record an operator answer for a blocked project input request",
    )
    answer_input.add_argument("task_id")
    answer_input.add_argument("--input-request-id", required=True)
    answer_input.add_argument("--answer", required=True)
    add_json_output_flag(answer_input)
    answer_input.set_defaults(handler=run_autonomy, needs_app=False)

    extend_budget = project_sub.add_parser(
        "extend-budget",
        help="Extend project budget metadata",
    )
    extend_budget.add_argument("task_id")
    extend_budget.add_argument("--extra-iterations", type=int, default=0)
    extend_budget.add_argument("--extra-wall-clock-ms", type=int, default=0)
    extend_budget.add_argument("--extra-tool-calls", type=int, default=0)
    add_json_output_flag(extend_budget)
    extend_budget.set_defaults(handler=run_autonomy, needs_app=False)


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    autonomy = subparsers.add_parser(
        "autonomy",
        help="Manage long-horizon autonomous runs",
    )
    autonomy_sub = autonomy.add_subparsers(dest="autonomy_command", required=True)

    start = autonomy_sub.add_parser("start", help="Start an autonomy run")
    start.add_argument("--goal", default="", help="Goal text")
    start.add_argument("--goal-file", default="", help="Read goal text from a file")
    start.add_argument("--goal-id", default=None, help="Existing goal id")
    start.add_argument("--session", default="autonomy", help="Session id")
    start.add_argument("--agent", default=None, help="Agent id for runtime execution")
    start.add_argument("--workspace", default="", help="Local workspace root")
    start.add_argument("--max-iterations", type=int, default=1)
    start.add_argument("--permission-profile", default="local-safe")
    start.add_argument(
        "--verification-domain",
        choices=("coding", "research", "operations", "cross_application"),
        default="cross_application",
    )
    start.add_argument(
        "--unattended",
        action="store_true",
        help="Schedule bounded project cycles through the existing daemon",
    )
    start.add_argument("--cycle-interval-seconds", type=int, default=None)
    start.add_argument("--task-db", default="", help=argparse.SUPPRESS)
    _add_execution_proof_args(start)
    add_json_output_flag(start)
    start.set_defaults(handler=run_autonomy, needs_app=False)

    list_runs = autonomy_sub.add_parser("list", help="List autonomy runs")
    list_runs.add_argument(
        "--status",
        choices=[status.value for status in AutonomyRunStatus],
        default=None,
    )
    list_runs.add_argument("--limit", type=int, default=50)
    add_json_output_flag(list_runs)
    list_runs.set_defaults(handler=run_autonomy, needs_app=False)

    show = autonomy_sub.add_parser("show", help="Show an autonomy run")
    show.add_argument("run_id")
    show.add_argument(
        "--include-proof",
        action="store_true",
        help="Include the terminal proof packet when it is available",
    )
    add_json_output_flag(show)
    show.set_defaults(handler=run_autonomy, needs_app=False)

    resume = autonomy_sub.add_parser("resume", help="Resume an autonomy run")
    resume.add_argument("run_id")
    resume.add_argument("--agent", default=None, help="Agent id for runtime execution")
    resume.add_argument("--max-iterations", type=int, default=None)
    resume.add_argument(
        "--verification-domain",
        choices=("coding", "research", "operations", "cross_application"),
        default=None,
    )
    resume.add_argument("--unattended", action="store_true")
    resume.add_argument("--cycle-interval-seconds", type=int, default=None)
    resume.add_argument("--task-db", default="", help=argparse.SUPPRESS)
    _add_execution_proof_args(resume)
    add_json_output_flag(resume)
    resume.set_defaults(handler=run_autonomy, needs_app=False)

    cancel = autonomy_sub.add_parser("cancel", help="Cancel an autonomy run")
    cancel.add_argument("run_id")
    cancel.add_argument("--task-db", default="", help=argparse.SUPPRESS)
    add_json_output_flag(cancel)
    cancel.set_defaults(handler=run_autonomy, needs_app=False)
    _register_project_commands(autonomy_sub)
