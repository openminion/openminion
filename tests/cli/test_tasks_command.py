from __future__ import annotations

from argparse import Namespace
from types import SimpleNamespace

import openminion.cli.commands.tasks as tasks_command

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


def test_tasks_cli_run_now_dispatches_registered_tool(monkeypatch, capsys) -> None:
    captured = {}

    def execute(_app, _args, *, tool_name, payload):
        captured.update(tool_name=tool_name, payload=payload)
        return {"ok": True, "error": "", "data": {"run_id": "run-1"}}

    monkeypatch.setattr(tasks_command, "_execute_task_tool", execute)
    args = Namespace(
        tasks_command="run-now",
        task_id="task-1",
        agent_id="agent",
        session="s1",
        limit=10,
        json=True,
    )

    assert run_tasks(args, SimpleNamespace(task_ctl=InMemoryTaskCtl())) == 0
    assert captured == {
        "tool_name": "task.run_now",
        "payload": {"task_id": "task-1"},
    }
    assert '"run_id": "run-1"' in capsys.readouterr().out


def test_tasks_cli_scheduler_pause_dispatches_revision(monkeypatch, capsys) -> None:
    captured = {}

    def execute(_app, args, *, tool_name, payload):
        captured.update(
            tool_name=tool_name,
            payload=payload,
            confirm=bool(getattr(args, "confirm", False)),
        )
        return {
            "ok": True,
            "error": "",
            "data": {"scheduler_control": {"paused": True, "updated_at": "r2"}},
        }

    monkeypatch.setattr(tasks_command, "_execute_task_tool", execute)
    args = Namespace(
        tasks_command="scheduler-pause",
        reason="maintenance",
        expected_updated_at="r1",
        confirm=True,
        agent_id="agent",
        session="s1",
        limit=10,
        json=True,
    )

    assert run_tasks(args, SimpleNamespace(task_ctl=InMemoryTaskCtl())) == 0
    assert captured == {
        "tool_name": "task.scheduler_pause",
        "payload": {"reason": "maintenance", "expected_updated_at": "r1"},
        "confirm": True,
    }
    capsys.readouterr()


def test_tasks_cli_dangerous_controls_use_canonical_confirmation(
    monkeypatch,
) -> None:
    contexts = []

    class Tools:
        def execute_calls(self, _calls, *, context):
            contexts.append(context)
            result = SimpleNamespace(
                ok=False,
                error="Approval required",
                data={"error_code": "CONFIRM_REQUIRED"},
            )
            return SimpleNamespace(results=[result])

    monkeypatch.setattr(
        "openminion.cli.commands.task_tools.ToolSelectionService",
        lambda *_args: SimpleNamespace(runtime_binding_policy_metadata=lambda: {}),
    )
    app = SimpleNamespace(
        config=SimpleNamespace(
            agents={"agent": object()},
            default_agent="agent",
            runtime=SimpleNamespace(env={}, tools=None, tool_selection=None),
        ),
        tools=Tools(),
        scheduler_readiness=lambda: {"state": "ready"},
    )
    args = Namespace(confirm=False)

    for tool_name in (
        "task.delete_history",
        "task.scheduler_pause",
        "task.scheduler_resume",
    ):
        result = tasks_command._execute_task_tool(
            app,
            args,
            tool_name=tool_name,
            payload={},
        )
        assert result["ok"] is False
        assert result["data"]["error_code"] == "CONFIRM_REQUIRED"
    assert all(context.confirm is False for context in contexts)
    assert all(context.target == "cli-tasks" for context in contexts)
    assert all(context.metadata["origin"] == "openminion.tasks" for context in contexts)


def test_tasks_cli_tool_control_uses_configured_default_agent(
    monkeypatch,
    capsys,
) -> None:
    seen: dict[str, str] = {}

    def execute(_app, args, *, tool_name, payload):
        del tool_name, payload
        seen["agent_id"] = args.agent_id
        return {"ok": True, "error": "", "data": {"run_id": "run-1"}}

    monkeypatch.setattr(tasks_command, "_execute_task_tool", execute)
    args = Namespace(
        tasks_command="run-now",
        task_id="task-1",
        agent_id="",
        session="s1",
        limit=10,
        json=True,
    )
    config = SimpleNamespace(
        agents={"agent-default": object()},
        default_agent="agent-default",
    )

    assert (
        run_tasks(args, SimpleNamespace(task_ctl=InMemoryTaskCtl(), config=config)) == 0
    )
    assert seen["agent_id"] == "agent-default"
    capsys.readouterr()
