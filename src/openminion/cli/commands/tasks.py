from __future__ import annotations

import argparse
from typing import Any

from openminion.api.runtime import APIRuntime
from openminion.base.config.core import resolve_default_agent_id
from openminion.cli.parser.flags import add_json_output_flag
from openminion.cli.presentation.json_output import print_json_payload
from openminion.modules.task.surface import (
    build_task_surface,
    resolve_task_surface_source,
)


def run_tasks(args: argparse.Namespace, app: APIRuntime) -> int:
    action = str(getattr(args, "tasks_command", "") or "list").strip().lower()
    agent_id = str(getattr(args, "agent_id", "") or "").strip()
    if not agent_id:
        agent_id = resolve_default_agent_id(app.config)
    surface = build_task_surface(
        resolve_task_surface_source(app),
        agent_id=agent_id,
        session_id=str(getattr(args, "session", "") or "").strip(),
        limit=int(getattr(args, "limit", 50) or 50),
    )
    try:
        if action == "list":
            payload = surface.inventory()
        elif action == "show":
            payload = _show_payload(surface, getattr(args, "task_id", ""))
        elif action in {"pause", "resume", "cancel"}:
            payload = surface.apply_action(
                task_id=getattr(args, "task_id", ""), action=action
            )
        elif action in {"allow", "deny"}:
            payload = surface.apply_action(
                task_id=getattr(args, "task_id", ""),
                action=action,
                decision_id=getattr(args, "decision_id", ""),
            )
        else:
            print(f"Unknown tasks command: {action}")
            return 1
    except (KeyError, ValueError, PermissionError, NotImplementedError) as exc:
        payload = {"ok": False, "error": str(exc)}
    except (AttributeError, TypeError, RuntimeError) as exc:
        payload = {"ok": False, "error": str(exc)}

    if payload.get("ok") and _payload_requires_daemon(payload):
        payload["scheduler"] = app.scheduler_readiness()

    if bool(getattr(args, "json", False)):
        print_json_payload(payload, sort_keys=False, default=str)
    else:
        _print_human(action=action, payload=payload)
    return 0 if payload.get("ok") else 1


def _show_payload(surface: Any, task_id: str) -> dict[str, Any]:
    task = surface.show_task(task_id)
    if task is None:
        return {"ok": False, "error": f"task not found: {task_id}"}
    return {"ok": True, "task": task}


def _payload_requires_daemon(payload: dict[str, Any]) -> bool:
    task = payload.get("task")
    if isinstance(task, dict) and task.get("daemon_required"):
        return True
    return any(
        isinstance(item, dict) and item.get("daemon_required")
        for item in payload.get("tasks", [])
    )


def _print_human(*, action: str, payload: dict[str, Any]) -> None:
    if not payload.get("ok"):
        print(f"Error: {payload.get('error') or 'task operation failed'}")
        return
    if action == "list":
        tasks = list(payload.get("tasks", []))
        print("Tasks")
        print("=====")
        if not tasks:
            print("No tasks found.")
            return
        for task in tasks:
            due = task.get("due_at") or "-"
            last_run = task.get("last_run") or {}
            print(
                f"[{task.get('lifecycle_state') or task.get('status', 'PENDING')}] "
                f"{task.get('id')}: {task.get('title')} "
                f"type={task.get('task_kind', '-')} "
                f"schedule={task.get('schedule_summary', '-')} "
                f"next={due} last={last_run.get('state') or '-'}"
            )
        scheduler = dict(payload.get("scheduler") or {})
        if scheduler:
            print(f"scheduler: {scheduler.get('state', 'unknown')}")
            if scheduler.get("check_command"):
                print(f"scheduler_check: {scheduler['check_command']}")
        return
    task = payload.get("task")
    if isinstance(task, dict):
        print("Task")
        print("====")
        print(f"id: {task.get('id')}")
        print(f"title: {task.get('title')}")
        print(f"status: {task.get('status')}")
        due = task.get("due_at")
        if due:
            print(f"due: {due}")
        if task.get("schedule_summary"):
            print(f"schedule: {task.get('schedule_summary')}")
        if task.get("daemon_required"):
            scheduler = dict(payload.get("scheduler") or {})
            print(f"scheduler: {scheduler.get('state', 'unknown')}")
            if scheduler.get("check_command"):
                print(f"scheduler_check: {scheduler['check_command']}")
        if task.get("last_run"):
            last_run = task["last_run"]
            print(f"last_run: {last_run.get('state')}")
            if last_run.get("last_error"):
                error = last_run["last_error"]
                print(f"last_error: {error.get('code')}: {error.get('message')}")
        if task.get("valid_actions"):
            print(f"actions: {', '.join(task['valid_actions'])}")
        return
    print(f"Task action {payload.get('action')} completed")


def _add_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--agent-id", default="", help="Agent id for task inventory")
    parser.add_argument("--session", default="", help="Session id for task inventory")
    parser.add_argument("--limit", type=int, default=50, help="Maximum tasks to show")
    add_json_output_flag(parser)
    parser.set_defaults(handler=run_tasks, needs_app=True)


def _register_task_action(
    subcommands: argparse._SubParsersAction[argparse.ArgumentParser], name: str
) -> None:
    parser = subcommands.add_parser(name, help=f"{name.capitalize()} a task")
    parser.add_argument("task_id", help="Exact task id")
    _add_common_args(parser)


def _register_pending_action(
    subcommands: argparse._SubParsersAction[argparse.ArgumentParser], name: str
) -> None:
    parser = subcommands.add_parser(name, help=f"{name.capitalize()} a pending action")
    parser.add_argument("decision_id", help="Pending action decision id")
    parser.add_argument("task_id", nargs="?", default="", help=argparse.SUPPRESS)
    _add_common_args(parser)


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    tasks = subparsers.add_parser("tasks", help="Task inventory and controls")
    task_subcommands = tasks.add_subparsers(dest="tasks_command")

    list_parser = task_subcommands.add_parser("list", help="List tasks")
    _add_common_args(list_parser)

    show_parser = task_subcommands.add_parser("show", help="Show one task")
    show_parser.add_argument("task_id", help="Exact task id")
    _add_common_args(show_parser)

    for name in ("pause", "resume", "cancel"):
        _register_task_action(task_subcommands, name)
    for name in ("allow", "deny"):
        _register_pending_action(task_subcommands, name)

    tasks.set_defaults(handler=run_tasks, needs_app=True, tasks_command="list")
