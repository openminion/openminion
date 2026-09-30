from __future__ import annotations

from argparse import Namespace
from types import SimpleNamespace

from openminion.cli.commands.tasks import run_tasks
from openminion.modules.session.storage.repository import create_sqlite_cron_repository
from openminion.modules.task import InMemoryTaskCtl, TaskCreateInput, TaskManager


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


def test_tasks_cli_shows_live_scheduler_readiness_for_scheduled_task(
    tmp_path,
    capsys,
) -> None:
    manager = TaskManager.from_cron_repository(
        create_sqlite_cron_repository(db_path=tmp_path / "tasks.db")
    )
    record = manager.schedule_task(
        name="scheduled",
        schedule={"kind": "every", "every_ms": 60_000},
        payload={"kind": "agentTurn", "message": "work"},
        agent_id="agent",
    )
    repository = getattr(manager, "_cron_repository")
    run_id = repository.trigger_cron_run(record.cron_job_id)
    repository.finish_cron_run(
        run_id,
        state="finished",
        summary="done",
        output={"delivery": {"state": "succeeded"}},
        isolated_session_id="result-session",
    )
    repository.mark_cron_delivery_target(run_id, target="session:origin")
    args = Namespace(
        tasks_command="show",
        task_id=record.task_id,
        agent_id="agent",
        session="s1",
        limit=10,
        json=False,
    )
    app = SimpleNamespace(
        task_manager=manager,
        scheduler_readiness=lambda: {
            "state": "ready",
            "hosted_by": "daemon",
            "reason": None,
        },
    )

    assert run_tasks(args, app) == 0
    detail = capsys.readouterr().out
    assert "scheduler: ready" in detail
    assert "result_session: result-session" in detail
    assert "delivery: succeeded" in detail
    assert "delivery_targets: session:origin" in detail

    args.tasks_command = "list"
    assert run_tasks(args, app) == 0
    assert "scheduler: ready" in capsys.readouterr().out


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


def test_tasks_cli_list_always_shows_scheduler_readiness(capsys) -> None:
    args = Namespace(
        tasks_command="list",
        agent_id="agent",
        session="s1",
        limit=10,
        json=False,
    )
    app = SimpleNamespace(
        task_ctl=InMemoryTaskCtl(),
        scheduler_readiness=lambda: {
            "state": "unreachable",
            "check_command": "openminion status",
        },
    )

    assert run_tasks(args, app) == 0
    output = capsys.readouterr().out
    assert "No tasks found." in output
    assert "scheduler: unreachable" in output
    assert "scheduler_check: openminion status" in output


def test_tasks_cli_reports_inventory_backend_failure(capsys) -> None:
    class BrokenRepository:
        def list(self, **kwargs):
            del kwargs
            raise RuntimeError("database unavailable")

    class BrokenManager:
        lifecycle_repository = BrokenRepository()

        def get_task(self, task_id: str):
            del task_id
            return None

    args = Namespace(
        tasks_command="list",
        agent_id="agent",
        session="s1",
        limit=10,
        json=True,
    )
    app = SimpleNamespace(
        task_manager=BrokenManager(),
        scheduler_readiness=lambda: {"state": "unknown"},
    )

    assert run_tasks(args, app) == 1
    assert "TASK_INVENTORY_UNAVAILABLE" in capsys.readouterr().out
