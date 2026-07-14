from __future__ import annotations

from argparse import Namespace
from types import SimpleNamespace

from openminion.cli.commands.tasks import run_tasks
from openminion.modules.task import InMemoryTaskCtl, TaskCreateInput


def test_tasks_cli_lists_task_as_json(capsys) -> None:
    ctl = InMemoryTaskCtl()
    ctl.create_task(TaskCreateInput(task_id="t1", title="CLI task"))
    args = Namespace(
        tasks_command="list",
        agent_id="agent",
        session="s1",
        limit=10,
        json=True,
    )

    exit_code = run_tasks(args, SimpleNamespace(task_ctl=ctl))

    out = capsys.readouterr().out
    assert exit_code == 0
    assert '"id": "t1"' in out
    assert '"title": "CLI task"' in out


def test_tasks_cli_shows_missing_task_as_failure(capsys) -> None:
    args = Namespace(
        tasks_command="show",
        task_id="missing",
        agent_id="agent",
        session="s1",
        limit=10,
        json=False,
    )

    exit_code = run_tasks(args, SimpleNamespace(task_ctl=InMemoryTaskCtl()))

    out = capsys.readouterr().out
    assert exit_code == 1
    assert "task not found" in out.lower()
