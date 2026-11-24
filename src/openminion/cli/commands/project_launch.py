"""Operator request parsing and construction for durable projects."""

from __future__ import annotations

import argparse
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from openminion.cli.commands.project_security import (
    add_security_project_args,
    prepare_security_project,
    security_project_selection,
)
from openminion.modules.task import AutonomyRun
from openminion.modules.task.autonomy import (
    VerificationDomain,
    VerificationWaiver,
    build_autonomy_run,
    build_local_workspace_ref,
)
from openminion.modules.task.constants import (
    DEFAULT_PROJECT_TURN_TIMEOUT_SECONDS,
    DEFAULT_PROJECT_VERIFICATION_TIMEOUT_SECONDS,
)
from openminion.modules.task.project.models import SecurityRepairContext


@dataclass(frozen=True)
class ProjectLaunchRequest:
    run: AutonomyRun
    workspace_boundary: Path
    repository: Path
    task_plan_required: bool
    expected_checks: tuple[str, ...]
    release_tools: bool
    success_criteria: tuple[str, ...] = ()
    source_request: str = ""
    security_repair: SecurityRepairContext | None = None


def focus_project_help() -> str:
    return "\n".join(
        (
            "usage: /project start --goal TEXT [--repository PATH] --verify-command COMMAND",
            "       [--verification-domain coding|research] [--max-iterations N]",
            "       [--max-wall-clock-ms N] [--max-tool-calls N]",
            "       [--expected-check NAME] [--success-criterion TEXT] [--release-tools]",
            "       [--security-source-task-id ID --security-review-ref REF]",
            "       [--security-finding-id ID --security-write-path FILE (repeatable)]",
            "Security repair requires all four security arguments and a separate prepared worktree.",
            "Set positive iteration, tool-call and wall-clock budgets for security repair.",
            "Repository is optional for non-Git work. Verification is required.",
            "usage: /project status [RUN_ID] | show/report RUN_ID",
            "       /project pause RUN_ID",
            "       /project redirect RUN_ID --direction TEXT",
            "       /project resume RUN_ID",
            "       /project cancel RUN_ID",
            "       /project answer RUN_ID --input-request-id ID --answer TEXT",
            "       /project reprioritize RUN_ID --priority TEXT",
            "       /project extend-budget RUN_ID [--extra-iterations N]",
            "Pause takes effect at the checkpoint boundary; it does not roll back work.",
            "Redirect updates the next cycle and requires the project to be paused.",
        )
    )


def resolve_project_repository(workspace_boundary: Path, value: str) -> Path:
    if not value:
        return workspace_boundary
    repository = Path(value).expanduser()
    if not repository.is_absolute():
        repository = workspace_boundary / repository
    return repository.resolve(strict=False)


def build_terminal_project_request(
    args: argparse.Namespace,
    *,
    goal: str,
    workspace_boundary: Path,
    waiver: VerificationWaiver | None,
) -> ProjectLaunchRequest:
    raw_repository = str(getattr(args, "repository", None) or "").strip()
    repository = resolve_project_repository(workspace_boundary, raw_repository)
    turn_timeout_seconds = int(
        getattr(args, "turn_timeout_seconds", None)
        or DEFAULT_PROJECT_TURN_TIMEOUT_SECONDS
    )
    verification_timeout_seconds = int(
        getattr(args, "verification_timeout_seconds", None)
        or DEFAULT_PROJECT_VERIFICATION_TIMEOUT_SECONDS
    )
    request = build_project_launch_request(
        goal=goal,
        session_id=str(getattr(args, "session", None) or "").strip() or "autonomy",
        agent_id=str(getattr(args, "agent", None) or "").strip() or "default",
        workspace_boundary=workspace_boundary,
        repository=repository,
        require_git_repository=bool(raw_repository),
        max_iterations=max(0, int(getattr(args, "max_iterations", 1))),
        max_wall_clock_ms=getattr(args, "max_wall_clock_ms", None),
        max_tool_calls=getattr(args, "max_tool_calls", None),
        permission_profile_id=str(
            getattr(args, "permission_profile", None) or ""
        ).strip()
        or "local-safe",
        config_ref=str(getattr(args, "config", None) or "").strip() or None,
        verification_domain=cast(
            VerificationDomain,
            str(getattr(args, "verification_domain", "coding")),
        ),
        verification_commands=tuple(getattr(args, "verify_command", ()) or ()),
        turn_timeout_seconds=turn_timeout_seconds,
        verification_timeout_seconds=verification_timeout_seconds,
        verification_waiver_reason=waiver.reason if waiver is not None else None,
        goal_id=str(getattr(args, "goal_id", None) or "").strip() or None,
        task_plan_required=True,
        expected_checks=tuple(getattr(args, "expected_check", ()) or ()),
        success_criteria=tuple(getattr(args, "success_criterion", ()) or ()),
        source_request=goal,
    )
    return request


def build_project_launch_request(
    *,
    goal: str,
    session_id: str,
    agent_id: str,
    workspace_boundary: Path,
    repository: Path,
    require_git_repository: bool,
    max_iterations: int = 1,
    max_wall_clock_ms: int | None = None,
    max_tool_calls: int | None = None,
    permission_profile_id: str = "local-safe",
    config_ref: str | None = None,
    verification_domain: VerificationDomain = "coding",
    turn_target: str = "autonomy",
    verification_commands: tuple[str, ...] = (),
    turn_timeout_seconds: int = DEFAULT_PROJECT_TURN_TIMEOUT_SECONDS,
    verification_timeout_seconds: int = DEFAULT_PROJECT_VERIFICATION_TIMEOUT_SECONDS,
    verification_waiver_reason: str | None = None,
    goal_id: str | None = None,
    task_plan_required: bool = True,
    expected_checks: tuple[str, ...] = (),
    release_tools: bool = False,
    success_criteria: tuple[str, ...] = (),
    source_request: str = "",
    security_repair: SecurityRepairContext | None = None,
) -> ProjectLaunchRequest:
    boundary = workspace_boundary.expanduser().resolve(strict=False)
    repo = repository.expanduser().resolve(strict=False)
    if repo != boundary and boundary not in repo.parents:
        raise ValueError("execution repository must be inside the workspace boundary")
    check_names = tuple(name.strip() for name in expected_checks)
    if any(not name for name in check_names) or len(set(check_names)) != len(
        check_names
    ):
        raise ValueError("expected check names must be non-empty and unique")
    if require_git_repository:
        _validate_project_repository(boundary=boundary, repository=repo)
    required_evidence_kinds: tuple[str, ...] = (
        ("waiver",) if verification_waiver_reason else ("verification",)
    )
    if verification_domain == "coding" and require_git_repository:
        required_evidence_kinds = (*required_evidence_kinds, "repository_status")
    run = build_autonomy_run(
        goal_text=goal,
        goal_id=goal_id,
        session_id=session_id,
        workspace_ref=build_local_workspace_ref(repo),
        max_iterations=max_iterations,
        max_wall_clock_ms=max_wall_clock_ms,
        max_tool_calls=max_tool_calls,
        permission_profile_id=permission_profile_id,
        agent_id=agent_id,
        config_ref=config_ref,
        verification_domain=verification_domain,
        turn_target=turn_target,
        verification_commands=verification_commands,
        turn_timeout_seconds=turn_timeout_seconds,
        verification_timeout_seconds=verification_timeout_seconds,
        verification_waiver_reason=verification_waiver_reason,
        required_evidence_kinds=required_evidence_kinds,
    )
    run = run.model_copy(
        update={
            "goal_id": run.goal_id or f"goal_{run.run_id}",
            "task_id": f"task_{run.run_id}",
        }
    )
    return ProjectLaunchRequest(
        run=run,
        workspace_boundary=boundary,
        repository=repo,
        task_plan_required=task_plan_required,
        expected_checks=check_names,
        release_tools=release_tools,
        success_criteria=success_criteria,
        source_request=source_request,
        security_repair=security_repair,
    )


def parse_focus_project_launch(
    line: str,
    *,
    session_id: str,
    agent_id: str,
    workspace_boundary: Path,
    config_ref: str | None,
    security_args: argparse.Namespace | None = None,
) -> ProjectLaunchRequest:
    from openminion.cli.commands.autonomy_project import project_task_manager

    parser = argparse.ArgumentParser(
        prog="/project", add_help=False, exit_on_error=False
    )
    add_security_project_args(parser)
    parser.add_argument("--repository", default="")
    parser.add_argument("--goal", default="")
    parser.add_argument("--max-iterations", type=int, default=1)
    parser.add_argument("--max-wall-clock-ms", type=int, default=None)
    parser.add_argument("--max-tool-calls", type=int, default=None)
    parser.add_argument("--verify-command", action="append", default=[])
    parser.add_argument("--expected-check", action="append", default=[])
    parser.add_argument("--success-criterion", action="append", default=[])
    parser.add_argument("--release-tools", action="store_true")
    parser.add_argument(
        "--verification-domain",
        choices=("coding", "research"),
        default="coding",
    )
    try:
        tokens = shlex.split(line)
        if tokens and tokens[0] == "/project":
            tokens = tokens[1:]
        command = tokens.pop(0) if tokens else ""
        parsed, unknown = parser.parse_known_args(tokens)
    except (argparse.ArgumentError, ValueError) as exc:
        raise ValueError(str(exc)) from exc
    if command != "start" or unknown:
        raise ValueError("usage: /project start --goal TEXT [--repository PATH]")
    repository = str(parsed.repository or "").strip()
    goal = str(parsed.goal or "").strip()
    if not goal:
        raise ValueError("usage: /project start --goal TEXT [--repository PATH]")
    if parsed.max_iterations < 1:
        raise ValueError("--max-iterations must be at least 1")
    repository_path = Path(repository) if repository else workspace_boundary
    resolved_repository = (
        repository_path
        if repository_path.is_absolute()
        else workspace_boundary / repository_path
    )
    request = build_project_launch_request(
        goal=goal,
        session_id=session_id,
        agent_id=agent_id,
        workspace_boundary=workspace_boundary,
        repository=resolved_repository,
        require_git_repository=(
            bool(repository) or (resolved_repository / ".git").exists()
        ),
        max_iterations=parsed.max_iterations,
        max_wall_clock_ms=parsed.max_wall_clock_ms,
        max_tool_calls=parsed.max_tool_calls,
        config_ref=config_ref,
        verification_domain=parsed.verification_domain,
        turn_target="focus",
        verification_commands=tuple(parsed.verify_command),
        task_plan_required=True,
        expected_checks=tuple(parsed.expected_check),
        release_tools=bool(parsed.release_tools),
        success_criteria=tuple(parsed.success_criterion),
        source_request=goal,
    )
    selection = security_project_selection(parsed)
    if selection is not None:
        if security_args is None:
            raise ValueError(
                "Security project preparation requires the configured operator context"
            )
        manager = project_task_manager(security_args)
        try:
            request = prepare_security_project(
                request, selection, args=security_args, manager=manager
            )
        finally:
            manager.close()
    return request


def _validate_project_repository(*, boundary: Path, repository: Path) -> None:
    if not boundary.is_dir():
        raise ValueError(f"workspace boundary is unavailable: {boundary}")
    if not repository.is_dir():
        raise ValueError(f"execution repository is unavailable: {repository}")
    if repository != boundary and boundary not in repository.parents:
        raise ValueError("execution repository must be inside the workspace boundary")
    if not (repository / ".git").exists():
        raise ValueError(f"execution repository does not contain .git: {repository}")
