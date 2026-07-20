from __future__ import annotations

from argparse import Namespace
from pathlib import Path

from openminion.cli.commands.replay import run_replay_command
from openminion.modules.task import TaskManager


def _task_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "tasks.db"
    manager = TaskManager.for_lifecycle_db(db_path=db_path)
    manager.create_task(
        session_id="s1",
        mode_name="project",
        goal="checkpoint cli",
        agent_id="agent-a",
        task_id="task-1",
    )
    manager.save_checkpoint(
        "task-1",
        "cp-1",
        {"event_log": [{"event_id": "e1", "event_type": "tool.completed", "seq": 1, "payload": {"ok": True}}]},
    )
    return db_path


def _args(db_path: Path, command: str, **kwargs: object) -> Namespace:
    values = {
        "task_db": str(db_path),
        "replay_command": command,
        "task_id": "task-1",
        "checkpoint_id": None,
        "expected_checkpoint_id": None,
        "branch_task_id": None,
        "branch_mode": "from_checkpoint",
        "use_case": "debug",
        "json": True,
    }
    values.update(kwargs)
    return Namespace(**values)


def test_replay_cli_lists_checkpoints_as_json(tmp_path: Path, capsys) -> None:
    exit_code = run_replay_command(_args(_task_db(tmp_path), "checkpoints"))

    out = capsys.readouterr().out
    assert exit_code == 0
    assert '"checkpoints": [' in out
    assert '"cp-1"' in out


def test_replay_cli_branches_without_mutating_source(tmp_path: Path, capsys) -> None:
    db_path = _task_db(tmp_path)

    exit_code = run_replay_command(
        _args(
            db_path,
            "branch",
            checkpoint_id="cp-1",
            branch_task_id="branch-1",
        )
    )

    out = capsys.readouterr().out
    assert exit_code == 0
    assert '"branch_task_id": "branch-1"' in out
    manager = TaskManager.for_lifecycle_db(db_path=db_path)
    assert manager.get_task("task-1") is not None
    assert manager.get_task("branch-1") is not None


def test_replay_cli_reports_missing_checkpoint(tmp_path: Path, capsys) -> None:
    exit_code = run_replay_command(
        _args(_task_db(tmp_path), "replay", checkpoint_id="missing")
    )

    out = capsys.readouterr().out
    assert exit_code == 1
    assert '"ok": false' in out
    assert "checkpoint not found" in out


def test_replay_command_family_registers_public_entrypoints() -> None:
    from openminion.cli.parser.base import COMMAND_NAMES, build_parser

    assert {"replay", "checkpoint", "rewind", "branch"}.issubset(COMMAND_NAMES)
    args = build_parser(selected_command="branch").parse_args(
        ["branch", "task-1", "--checkpoint-id", "cp-1", "--task-db", "tasks.db"]
    )
    assert args.replay_command == "branch"
    assert args.task_id == "task-1"
