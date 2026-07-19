from __future__ import annotations

import argparse
from pathlib import Path
from typing import Callable, cast

from openminion.cli.parser.flags import add_json_output_flag
from openminion.cli.presentation.json_output import print_json_payload
from openminion.modules.runtime.replay import ReplayUseCase
from openminion.modules.task import TaskManager
from openminion.modules.task.replay_commands import (
    BranchMode,
    ReplayCommandResult,
    branch_task_from_checkpoint,
    compare_task_checkpoint,
    list_task_checkpoints,
    replay_task_checkpoint,
    rewind_task_to_checkpoint,
)


Handler = Callable[[argparse.Namespace, TaskManager], ReplayCommandResult]


def run_replay_command(args: argparse.Namespace) -> int:
    try:
        manager = TaskManager.for_lifecycle_db(db_path=Path(str(args.task_db)))
        handler = _handler_for(args)
        result = handler(args, manager)
        payload = result.to_dict()
    except (KeyError, ValueError, RuntimeError, TypeError) as exc:
        payload = {
            "ok": False,
            "action": str(getattr(args, "replay_command", "") or ""),
            "task_id": str(getattr(args, "task_id", "") or ""),
            "error": str(exc),
        }
    if bool(getattr(args, "json", False)):
        print_json_payload(payload, sort_keys=False, default=str)
    else:
        _print_replay_human(payload)
    return 0 if payload.get("ok") else 1


def _handler_for(args: argparse.Namespace) -> Handler:
    action = str(getattr(args, "replay_command", "") or "checkpoints").strip()
    if action == "checkpoints":
        return _checkpoints
    if action == "replay":
        return _replay
    if action == "compare":
        return _compare
    if action == "rewind":
        return _rewind
    if action == "branch":
        return _branch
    raise ValueError(f"unknown replay command: {action}")


def _checkpoints(
    args: argparse.Namespace, manager: TaskManager
) -> ReplayCommandResult:
    return list_task_checkpoints(manager, task_id=str(args.task_id))


def _replay(args: argparse.Namespace, manager: TaskManager) -> ReplayCommandResult:
    return replay_task_checkpoint(
        manager,
        task_id=str(args.task_id),
        checkpoint_id=getattr(args, "checkpoint_id", None),
        use_case=cast(ReplayUseCase, str(getattr(args, "use_case", "debug") or "debug")),
    )


def _compare(args: argparse.Namespace, manager: TaskManager) -> ReplayCommandResult:
    return compare_task_checkpoint(
        manager,
        task_id=str(args.task_id),
        checkpoint_id=getattr(args, "checkpoint_id", None),
        expected_checkpoint_id=getattr(args, "expected_checkpoint_id", None),
    )


def _rewind(args: argparse.Namespace, manager: TaskManager) -> ReplayCommandResult:
    return rewind_task_to_checkpoint(
        manager,
        task_id=str(args.task_id),
        checkpoint_id=str(args.checkpoint_id),
        branch_task_id=getattr(args, "branch_task_id", None),
    )


def _branch(args: argparse.Namespace, manager: TaskManager) -> ReplayCommandResult:
    return branch_task_from_checkpoint(
        manager,
        task_id=str(args.task_id),
        checkpoint_id=str(args.checkpoint_id),
        branch_mode=cast(
            BranchMode,
            str(getattr(args, "branch_mode", "from_checkpoint") or "from_checkpoint"),
        ),
        branch_task_id=getattr(args, "branch_task_id", None),
    )


def _print_replay_human(payload: dict[str, object]) -> None:
    if not payload.get("ok"):
        print(f"Replay command failed: {payload.get('error') or 'unknown error'}")
        return
    action = str(payload.get("action") or "replay")
    print(f"Replay {action}")
    print("=============")
    print(f"task: {payload.get('task_id')}")
    checkpoint_id = payload.get("checkpoint_id")
    if checkpoint_id:
        print(f"checkpoint: {checkpoint_id}")
    branch_task_id = payload.get("branch_task_id")
    if branch_task_id:
        print(f"branch task: {branch_task_id}")
    checkpoints = payload.get("checkpoints")
    if isinstance(checkpoints, list) and checkpoints:
        print("checkpoints:")
        for item in checkpoints:
            print(f"- {item}")
    if payload.get("events_replayed"):
        print(f"events replayed: {payload.get('events_replayed')}")
        divergences = payload.get("divergences")
        print(f"divergences: {len(divergences) if isinstance(divergences, list) else 0}")
    notes = payload.get("nondeterminism_notes")
    if isinstance(notes, list) and notes:
        print("notes:")
        for note in notes:
            print(f"- {note}")


def _add_task_db(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--task-db", required=True, help="Task lifecycle SQLite DB path")
    add_json_output_flag(parser)
    parser.set_defaults(handler=run_replay_command)


def _add_checkpoint_arg(parser: argparse.ArgumentParser, *, required: bool) -> None:
    parser.add_argument("--checkpoint-id", required=required, help="Checkpoint id")


def _register_subcommands(parser: argparse.ArgumentParser) -> None:
    subcommands = parser.add_subparsers(dest="replay_command")

    checkpoints = subcommands.add_parser("checkpoints", help="List task checkpoints")
    checkpoints.add_argument("task_id")
    _add_task_db(checkpoints)

    replay = subcommands.add_parser("replay", help="Replay a checkpoint")
    replay.add_argument("task_id")
    _add_checkpoint_arg(replay, required=False)
    replay.add_argument(
        "--use-case",
        default="debug",
        choices=("debug", "regression_test", "state_recovery", "audit_replay"),
    )
    _add_task_db(replay)

    compare = subcommands.add_parser("compare", help="Compare replay output")
    compare.add_argument("task_id")
    _add_checkpoint_arg(compare, required=False)
    compare.add_argument("--expected-checkpoint-id", required=False)
    _add_task_db(compare)

    rewind = subcommands.add_parser("rewind", help="Create a rewind branch")
    rewind.add_argument("task_id")
    _add_checkpoint_arg(rewind, required=True)
    rewind.add_argument("--branch-task-id")
    _add_task_db(rewind)

    branch = subcommands.add_parser("branch", help="Create a branch from a checkpoint")
    branch.add_argument("task_id")
    _add_checkpoint_arg(branch, required=True)
    branch.add_argument("--branch-task-id")
    branch.add_argument(
        "--branch-mode",
        default="from_checkpoint",
        choices=("from_checkpoint", "before_checkpoint"),
    )
    _add_task_db(branch)

    parser.set_defaults(handler=run_replay_command, replay_command="checkpoints")


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    replay = subparsers.add_parser("replay", help="Replay/checkpoint controls")
    _register_subcommands(replay)

    checkpoint = subparsers.add_parser("checkpoint", help="List task checkpoints")
    checkpoint.add_argument("task_id")
    _add_task_db(checkpoint)
    checkpoint.set_defaults(replay_command="checkpoints")

    rewind = subparsers.add_parser("rewind", help="Create a rewind branch")
    rewind.add_argument("task_id")
    _add_checkpoint_arg(rewind, required=True)
    rewind.add_argument("--branch-task-id")
    _add_task_db(rewind)
    rewind.set_defaults(replay_command="rewind")

    branch = subparsers.add_parser("branch", help="Create a branch from a checkpoint")
    branch.add_argument("task_id")
    _add_checkpoint_arg(branch, required=True)
    branch.add_argument("--branch-task-id")
    branch.add_argument(
        "--branch-mode",
        default="from_checkpoint",
        choices=("from_checkpoint", "before_checkpoint"),
    )
    _add_task_db(branch)
    branch.set_defaults(replay_command="branch")
