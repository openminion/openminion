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


def test_tasks_cli_uses_configured_default_agent(capsys) -> None:
    ctl = InMemoryTaskCtl()
    seen: dict[str, str] = {}
    get_digest = ctl.get_digest

    def capture_digest(*, agent_id: str, session_id: str, limit: int = 5):
        seen["agent_id"] = agent_id
        return get_digest(agent_id=agent_id, session_id=session_id, limit=limit)

    ctl.get_digest = capture_digest  # type: ignore[method-assign]
    args = Namespace(
        tasks_command="list",
        agent_id="",
        session="s1",
        limit=10,
        json=True,
    )
    config = SimpleNamespace(
        agents={"agent-default": object()},
        default_agent="agent-default",
    )

    assert run_tasks(args, SimpleNamespace(task_ctl=ctl, config=config)) == 0
    assert seen["agent_id"] == "agent-default"
    capsys.readouterr()
