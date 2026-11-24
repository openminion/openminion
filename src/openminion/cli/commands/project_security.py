"""CLI admission and replay rules for approved security repairs."""

from __future__ import annotations

import argparse
from contextlib import closing
from dataclasses import replace
from typing import TYPE_CHECKING, cast

from openminion.modules.task import (
    AutonomyRun,
    AutonomyRunError,
    AutonomyRunPhase,
    AutonomyRunStatus,
    AutonomyRunStore,
    TaskLifecycleState,
    TaskManager,
)
from openminion.modules.task.autonomy import TestEvidence, VerificationWaiver
from openminion.modules.task.project import checkpoints as project_checkpoints
from openminion.modules.task.project.models import SecurityRepairContext
from openminion.modules.tool.errors import ToolRuntimeError
from openminion.tools.security.review_schemas import SecurityRepairSelection

if TYPE_CHECKING:
    from openminion.cli.commands.project_launch import ProjectLaunchRequest


def add_security_project_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--security-source-task-id", default="")
    parser.add_argument("--security-review-ref", default="")
    parser.add_argument("--security-finding-id", default="")
    parser.add_argument("--security-write-path", action="append", default=[])


def security_project_selection(
    args: argparse.Namespace,
) -> SecurityRepairSelection | None:
    values = (
        args.security_source_task_id,
        args.security_review_ref,
        args.security_finding_id,
        args.security_write_path,
    )
    if not any(values):
        return None
    if not all(values):
        raise ValueError("all four --security-* arguments are required together")
    return SecurityRepairSelection(
        source_task_id=args.security_source_task_id,
        review_ref=args.security_review_ref,
        finding_id=args.security_finding_id,
        write_paths=tuple(args.security_write_path),
    )


def prepare_security_project(
    request: ProjectLaunchRequest,
    selection: SecurityRepairSelection,
    *,
    args: argparse.Namespace,
    manager: TaskManager,
) -> ProjectLaunchRequest:
    from openminion.cli.config import load_cli_manager_from_args
    from openminion.modules.artifact.config import from_base_config
    from openminion.modules.artifact.control import ArtifactCtl
    from openminion.tools.security.review import validate_security_repair

    from openminion.cli.commands.autonomy_project import configured_cron_store

    selectors = request.run.execution_selectors
    budgets = request.run.continuation_policy
    if any(
        value is None or value < 1
        for value in (
            budgets.max_iterations,
            budgets.max_tool_calls,
            budgets.max_wall_clock_ms,
        )
    ):
        raise ValueError(
            "Security repair requires positive iteration, tool-call and wall-clock budgets"
        )
    if (
        request.release_tools
        or selectors.verification_waiver_reason
        or selectors.verification_domain != "coding"
        or not selectors.verification_commands
    ):
        raise ValueError(
            "Security repair requires coding verification without a waiver or release tools"
        )
    config = load_cli_manager_from_args(args)
    with closing(
        configured_cron_store(
            args, config_ref=request.run.execution_selectors.config_ref
        )
    ) as cron:
        with (
            closing(
                TaskManager.from_cron_repository(
                    cron, db_path=manager.lifecycle_repository.db_path
                )
            ) as source_manager,
            ArtifactCtl(
                from_base_config(
                    base_config=config.base_config,
                    home_root=config.home_root,
                    data_root=config.data_root,
                )
            ) as ctl,
        ):
            try:
                repair = validate_security_repair(
                    selection,
                    manager=source_manager,
                    ctl=ctl,
                    repository=request.repository,
                    agent_id=selectors.agent_id,
                    env=config.env,
                )
            except ToolRuntimeError as exc:
                raise ValueError(f"{exc.code}: {exc.message}") from exc
    return replace(request, security_repair=repair)


def link_security_project(
    repair: SecurityRepairContext,
    run: AutonomyRun,
    *,
    manager: TaskManager,
    args: argparse.Namespace,
) -> None:
    from openminion.cli.config import load_cli_manager_from_args
    from openminion.modules.artifact.config import from_base_config
    from openminion.modules.artifact.control import ArtifactCtl

    config = load_cli_manager_from_args(args)
    with ArtifactCtl(
        from_base_config(
            base_config=config.base_config,
            home_root=config.home_root,
            data_root=config.data_root,
        )
    ) as ctl:
        for ref in repair.evidence_refs:
            ctl.ref_add("collection", "security-project:" + run.run_id, ref)
    manager.mutate_task_metadata(
        task_id=repair.source_task_id,
        mutate=lambda metadata: {
            **metadata,
            "security_repair": {
                "run_id": run.run_id,
                "task_id": run.task_id,
                "checkpoint_id": run.checkpoint_id,
                "review_ref": repair.review_ref,
                "finding_id": repair.finding_id,
            },
        },
    )


def validate_security_project_resume(
    args: argparse.Namespace,
    run: AutonomyRun,
    *,
    manager: TaskManager,
    waiver: VerificationWaiver | None,
) -> None:
    checkpoint = project_checkpoints.load_latest_project_checkpoint(
        manager, task_id=run.task_id or ""
    )
    if checkpoint is not None and "security_repair" in checkpoint.payload:
        changed = {
            "agent": run.execution_selectors.agent_id,
            "config": run.execution_selectors.config_ref,
            "verification_domain": run.execution_selectors.verification_domain,
            "turn_timeout_seconds": run.execution_selectors.turn_timeout_seconds,
            "verification_timeout_seconds": run.execution_selectors.verification_timeout_seconds,
            "permission_profile": run.permission_profile_id,
        }
        if (
            waiver is not None
            or any(
                getattr(args, key, None) not in (None, "", expected)
                for key, expected in changed.items()
            )
            or (
                args.verify_command
                and tuple(args.verify_command)
                != run.execution_selectors.verification_commands
            )
        ):
            raise ValueError(
                "Security repair launch inputs are immutable; prepare a new operator request"
            )


def revalidate_security_project(
    request: ProjectLaunchRequest,
    *,
    args: argparse.Namespace | None,
    manager: TaskManager,
) -> None:
    repair = request.security_repair
    if repair is not None:
        if args is None:
            raise ValueError(
                "Security project launch requires the configured operator context"
            )
        selection = SecurityRepairSelection(
            source_task_id=repair.source_task_id,
            review_ref=repair.review_ref,
            finding_id=repair.finding_id,
            write_paths=repair.write_paths,
        )
        refreshed = prepare_security_project(
            request, selection, args=args, manager=manager
        )
        if refreshed.security_repair != repair:
            raise ValueError(
                "Security repair evidence changed after preparation; prepare a new request"
            )


def record_security_project_baseline(
    repair: SecurityRepairContext,
    running: AutonomyRun,
    baseline: tuple[TestEvidence, ...],
    *,
    manager: TaskManager,
    store: AutonomyRunStore,
    args: argparse.Namespace,
) -> AutonomyRun:
    link_security_project(repair, running, manager=manager, args=args)
    if any(
        item.exit_code is None or item.exit_code < 0 for item in baseline
    ) or not any(
        item.exit_code is not None and item.exit_code > 0 for item in baseline
    ):
        manager.transition_task(
            task_id=cast(str, running.task_id), to_state=TaskLifecycleState.PAUSED
        )
        return store.transition(
            running.run_id,
            status=AutonomyRunStatus.BLOCKED,
            phase=AutonomyRunPhase.CLOSED,
            operator_summary="Security repair baseline did not reproduce the finding.",
            next_action_hint="Inspect the recorded baseline and prepare a corrected operator request.",
            error=AutonomyRunError(
                code="SECURITY_BASELINE_INVALID",
                message="Baseline requires normal command execution and a failing reproducer.",
            ),
        )
    return running
