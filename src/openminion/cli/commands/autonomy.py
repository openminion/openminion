from __future__ import annotations

import argparse
import json
import shlex
import subprocess
from pathlib import Path
from typing import Any

from openminion.api.turns import run_turn
from openminion.base.types import Message
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
    DelegatedRoleEvidence,
    EvidenceStatus,
    TestEvidence,
    TestEvidenceStatus,
    VerificationWaiver,
    build_autonomy_run,
    build_local_workspace_ref,
    build_terminal_proof_packet,
    now_ms,
)
from openminion.modules.task.project import (
    ProjectControlAction,
    apply_project_control,
    render_project_control_result,
)
from openminion.modules.task.project_reports import (
    build_project_report_from_task,
    render_project_report,
)
from openminion.modules.task import TaskManager
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
        return _list(args, store)
    if action == "show":
        return _show(args, store)
    if action == "resume":
        return _resume(args, store)
    if action == "cancel":
        return _cancel(args, store)
    if action == "project":
        return _project(args)
    raise RuntimeError(f"Unknown autonomy command: {action}")


def _start(args: argparse.Namespace, store: AutonomyRunStore) -> int:
    goal = _resolve_goal(args)
    workspace = _resolve_workspace(args)
    run = build_autonomy_run(
        goal_text=goal,
        goal_id=_clean(getattr(args, "goal_id", None)) or None,
        session_id=_clean(getattr(args, "session", None)) or "autonomy",
        workspace_ref=build_local_workspace_ref(workspace),
        max_iterations=max(0, int(getattr(args, "max_iterations", 1))),
        permission_profile_id=_clean(getattr(args, "permission_profile", None))
        or "local-safe",
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

    running = store.transition(
        run.run_id,
        status=AutonomyRunStatus.RUNNING,
        phase=AutonomyRunPhase.EXECUTE,
        operator_summary="Autonomy run started.",
    )
    completed = _execute_or_fail(args, store, running, goal=goal, workspace=workspace)
    return _print_run(args, completed)


def _resume(args: argparse.Namespace, store: AutonomyRunStore) -> int:
    run = store.require(str(args.run_id))
    if run.status in {AutonomyRunStatus.COMPLETED, AutonomyRunStatus.CANCELLED}:
        raise RuntimeError(f"autonomy run cannot be resumed from {run.status}")
    running = store.transition(
        run.run_id,
        status=AutonomyRunStatus.RUNNING,
        phase=AutonomyRunPhase.EXECUTE,
        operator_summary="Autonomy run resumed.",
    )
    workspace = _workspace_path_from_ref(running.workspace_ref) or Path.cwd()
    completed = _execute_or_fail(
        args,
        store,
        running,
        goal=running.goal_text,
        workspace=workspace,
    )
    return _print_run(args, completed)


def _cancel(args: argparse.Namespace, store: AutonomyRunStore) -> int:
    run = store.require(str(args.run_id))
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
    )


def _list(args: argparse.Namespace, store: AutonomyRunStore) -> int:
    status = _status_arg(getattr(args, "status", None))
    runs = store.list_runs(status=status, limit=int(getattr(args, "limit", 50)))
    payload = {
        "ok": True,
        "runs": [_run_summary(run) for run in runs],
        "count": len(runs),
    }
    if bool(getattr(args, "json", False)):
        print_json_payload(payload)
        return 0
    if not runs:
        print("No autonomy runs.")
        return 0
    for run in runs:
        print(
            f"{run.run_id} {run.status.value} phase={run.phase.value} "
            f"goal={run.goal_text[:80]}"
        )
    return 0


def _show(args: argparse.Namespace, store: AutonomyRunStore) -> int:
    run = store.require(str(args.run_id))
    proof_payload = (
        _load_proof_payload(run)
        if bool(getattr(args, "include_proof", False))
        else None
    )
    payload = {"ok": True, "run": run.model_dump(mode="json")}
    if proof_payload is not None:
        payload["proof"] = proof_payload
    if bool(getattr(args, "json", False)):
        print_json_payload(payload)
        return 0
    print(f"run_id: {run.run_id}")
    print(f"status: {run.status.value}")
    print(f"phase: {run.phase.value}")
    print(f"goal: {run.goal_text}")
    print(f"workspace_ref: {run.workspace_ref or '-'}")
    print(f"proof_packet_ref: {run.proof_packet_ref or '-'}")
    if proof_payload is not None:
        print(f"proof_status: {proof_payload.get('status', '-')}")
        print(f"proof_validation: {proof_payload.get('validation_summary', '-')}")
    if run.next_action_hint:
        print(f"next_action: {run.next_action_hint}")
    return 0


def _execute_or_fail(
    args: argparse.Namespace,
    store: AutonomyRunStore,
    run: AutonomyRun,
    *,
    goal: str,
    workspace: Path,
) -> AutonomyRun:
    started = now_ms()
    command = _command_evidence(args, workspace=workspace, started_at_ms=started)
    try:
        delegation_results = _delegated_role_evidence(args)
        delegation_aggregation = _delegation_aggregation(delegation_results)
        context_budget = _context_budget_evidence(
            args,
            goal=goal,
            delegation_results=delegation_results,
        )
        summary = _synthesize_parent_summary(
            _execute_goal(args, run=run, goal=goal),
            delegation_results=delegation_results,
        )
    except Exception as exc:
        failed = store.transition(
            run.run_id,
            status=AutonomyRunStatus.FAILED,
            phase=AutonomyRunPhase.CLOSED,
            operator_summary="Autonomy run failed.",
            next_action_hint="Inspect proof packet and resume after fixing the blocker.",
            error=AutonomyRunError(
                code=type(exc).__name__,
                message=str(exc) or type(exc).__name__,
            ),
        )
        failed_command = command.model_copy(
            update={
                "ended_at_ms": now_ms(),
                "exit_code": 1,
                "status": EvidenceStatus.FAILED,
                "summary": f"autonomy start failed: {type(exc).__name__}",
            }
        )
        _write_terminal_proof(
            store,
            failed,
            validation_summary="Runtime execution failed.",
            final_operator_summary="Autonomy run failed.",
            commands_run=(failed_command,),
        )
        return store.require(run.run_id)

    verification = _run_verification_commands(args, workspace=workspace)
    waiver = _verification_waiver(args)
    if not verification and bool(getattr(args, "require_verification", False)):
        if waiver is None:
            blocked = store.transition(
                run.run_id,
                status=AutonomyRunStatus.BLOCKED,
                phase=AutonomyRunPhase.CLOSED,
                operator_summary="Autonomy run blocked because verification is required.",
                next_action_hint=(
                    "Resume with --verify-command or provide an explicit "
                    "--verification-waiver reason."
                ),
                error=AutonomyRunError(
                    code="VERIFICATION_REQUIRED",
                    message="verification was required but no verification command ran",
                ),
            )
            blocked_command = command.model_copy(
                update={
                    "ended_at_ms": now_ms(),
                    "exit_code": 0,
                    "status": EvidenceStatus.SUCCEEDED,
                    "summary": "autonomy run reached required verification gate",
                }
            )
            _write_terminal_proof(
                store,
                blocked,
                validation_summary="Runtime execution completed, but verification was required.",
                final_operator_summary="Autonomy run blocked by required verification gate.",
                commands_run=(blocked_command,),
            )
            return store.require(run.run_id)
    failed_verification = next(
        (item for item in verification if item.status != TestEvidenceStatus.PASSED),
        None,
    )
    if failed_verification is not None and waiver is None:
        blocked = store.transition(
            run.run_id,
            status=AutonomyRunStatus.BLOCKED,
            phase=AutonomyRunPhase.CLOSED,
            operator_summary="Autonomy run blocked by verification failure.",
            next_action_hint="Fix the failing verification command and resume the run.",
            error=AutonomyRunError(
                code="VERIFICATION_FAILED",
                message=failed_verification.summary,
            ),
        )
        blocked_command = command.model_copy(
            update={
                "ended_at_ms": now_ms(),
                "exit_code": 0,
                "status": EvidenceStatus.SUCCEEDED,
                "summary": "autonomy run reached verification",
            }
        )
        _write_terminal_proof(
            store,
            blocked,
            validation_summary="Runtime execution completed, but verification failed.",
            final_operator_summary="Autonomy run blocked by verification failure.",
            commands_run=(blocked_command,),
            tests_run=verification,
        )
        return store.require(run.run_id)

    completed = store.transition(
        run.run_id,
        status=AutonomyRunStatus.COMPLETED,
        phase=AutonomyRunPhase.CLOSED,
        operator_summary=summary,
        next_action_hint=None,
    )
    succeeded_command = command.model_copy(
        update={
            "ended_at_ms": now_ms(),
            "exit_code": 0,
            "status": EvidenceStatus.SUCCEEDED,
            "summary": "autonomy run completed",
        }
    )
    _write_terminal_proof(
        store,
        completed,
        validation_summary=_validation_summary(verification, waiver=waiver),
        final_operator_summary=summary,
        commands_run=(succeeded_command,),
        tests_run=verification,
        verification_waiver=waiver,
        delegation_results=delegation_results,
        delegation_aggregation=delegation_aggregation,
        context_budget=context_budget,
    )
    return store.require(run.run_id)


def _execute_goal(args: argparse.Namespace, *, run: AutonomyRun, goal: str) -> str:
    replay_response = _clean(getattr(args, "replay_response", None))
    if replay_response:
        return replay_response
    turn = run_turn(
        config_path=getattr(args, "config", None),
        payload={
            "message": goal,
            "agent_id": _clean(getattr(args, "agent", None)) or "default",
            "session_id": run.session_id,
            "channel": "console",
            "target": "autonomy",
            "deliver": False,
            "inbound_metadata": {
                "source": "openminion.autonomy",
                "autonomy_run_id": run.run_id,
                "goal_id": run.goal_id or "",
            },
        },
    )
    final_text = str(turn.get("final_text", "") or turn.get("body", "")).strip()
    return final_text or "Autonomy run completed without visible final text."


def _write_terminal_output(
    args: argparse.Namespace,
    store: AutonomyRunStore,
    run: AutonomyRun,
    *,
    validation_summary: str,
    final_operator_summary: str,
) -> int:
    _write_terminal_proof(
        store,
        run,
        validation_summary=validation_summary,
        final_operator_summary=final_operator_summary,
    )
    return _print_run(args, store.require(run.run_id))


def _write_terminal_proof(
    store: AutonomyRunStore,
    run: AutonomyRun,
    *,
    validation_summary: str,
    final_operator_summary: str,
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
    return DelegatedRoleEvidence(role=role, status=status, summary=summary)


def _delegation_aggregation(
    delegation_results: tuple[DelegatedRoleEvidence, ...],
) -> dict[str, object] | None:
    if not delegation_results:
        return None
    success_count = sum(
        1 for result in delegation_results if result.status == "success"
    )
    failure_count = sum(
        1 for result in delegation_results if result.status == "failure"
    )
    skipped_count = sum(
        1 for result in delegation_results if result.status == "skipped"
    )
    canceled_count = sum(
        1 for result in delegation_results if result.status == "canceled"
    )
    return {
        "total_children": len(delegation_results),
        "success_count": success_count,
        "failure_count": failure_count,
        "skipped_count": skipped_count,
        "canceled_count": canceled_count,
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


def _run_verification_commands(
    args: argparse.Namespace,
    *,
    workspace: Path,
) -> tuple[TestEvidence, ...]:
    commands = tuple(
        command
        for command in getattr(args, "verify_command", ()) or ()
        if _clean(command)
    )
    if not commands:
        return ()
    return tuple(
        _run_verification_command(command, workspace=workspace) for command in commands
    )


def _run_verification_command(command: str, *, workspace: Path) -> TestEvidence:
    started = now_ms()
    try:
        argv = tuple(shlex.split(command))
    except ValueError as exc:
        ended = now_ms()
        return TestEvidence(
            command=command,
            cwd_ref=str(workspace),
            started_at_ms=started,
            ended_at_ms=ended,
            exit_code=None,
            status=TestEvidenceStatus.FAILED,
            summary=f"verification command could not be parsed: {exc}",
        )
    if not argv:
        ended = now_ms()
        return TestEvidence(
            command=command,
            cwd_ref=str(workspace),
            started_at_ms=started,
            ended_at_ms=ended,
            exit_code=None,
            status=TestEvidenceStatus.SKIPPED,
            summary="verification command was empty",
        )
    try:
        completed = subprocess.run(
            argv,
            cwd=workspace,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=120,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        ended = now_ms()
        return TestEvidence(
            command=argv,
            cwd_ref=str(workspace),
            started_at_ms=started,
            ended_at_ms=ended,
            exit_code=None,
            status=TestEvidenceStatus.FAILED,
            summary=f"verification command failed to run: {type(exc).__name__}",
        )
    ended = now_ms()
    status = (
        TestEvidenceStatus.PASSED
        if completed.returncode == 0
        else TestEvidenceStatus.FAILED
    )
    return TestEvidence(
        command=argv,
        cwd_ref=str(workspace),
        started_at_ms=started,
        ended_at_ms=ended,
        exit_code=completed.returncode,
        passed=1 if completed.returncode == 0 else 0,
        failed=0 if completed.returncode == 0 else 1,
        status=status,
        summary=_verification_summary(completed.returncode, completed.stdout),
    )


def _verification_summary(exit_code: int, output: str) -> str:
    first_line = next(
        (line.strip() for line in output.splitlines() if line.strip()), ""
    )
    if exit_code == 0:
        return first_line or "verification command passed"
    return first_line or f"verification command failed with exit code {exit_code}"


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
    return "Replay/runtime execution completed; verification commands passed."


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
    if bool(getattr(args, "json", False)):
        print_json_payload({"ok": True, "project": result.model_dump(mode="json")})
        return 0
    print(render_project_control_result(result))
    return 0


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


def _run_summary(run: AutonomyRun) -> dict[str, Any]:
    return {
        "run_id": run.run_id,
        "goal_id": run.goal_id,
        "goal_text": run.goal_text,
        "session_id": run.session_id,
        "status": run.status.value,
        "phase": run.phase.value,
        "workspace_ref": run.workspace_ref,
        "proof_packet_ref": run.proof_packet_ref,
        "created_at_ms": run.created_at_ms,
        "updated_at_ms": run.updated_at_ms,
    }


def _load_proof_payload(run: AutonomyRun) -> dict[str, Any] | None:
    if not run.proof_packet_ref:
        return None
    path = Path(run.proof_packet_ref).expanduser().resolve(strict=False)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


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


def _workspace_path_from_ref(workspace_ref: str | None) -> Path | None:
    if not workspace_ref or not workspace_ref.startswith("local:"):
        return None
    path_part = workspace_ref.removeprefix("local:").split("#", 1)[0]
    return Path(path_part).expanduser().resolve(strict=False)


def _status_arg(value: object) -> AutonomyRunStatus | None:
    raw = _clean(value)
    return AutonomyRunStatus(raw) if raw else None


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
    _add_execution_proof_args(resume)
    add_json_output_flag(resume)
    resume.set_defaults(handler=run_autonomy, needs_app=False)

    cancel = autonomy_sub.add_parser("cancel", help="Cancel an autonomy run")
    cancel.add_argument("run_id")
    add_json_output_flag(cancel)
    cancel.set_defaults(handler=run_autonomy, needs_app=False)
    _register_project_commands(autonomy_sub)
