from __future__ import annotations

import argparse

from openminion.api.runtime import APIRuntime
from openminion.cli.parser.flags import (
    add_json_output_flag,
    add_tool_session_arg,
)
from openminion.cli.presentation.json_output import print_json_payload
from .task_tools import build_schedule_payload, execute_task_tool


def run_cron(args, app: APIRuntime) -> int:
    action = str(getattr(args, "cron_command", "")).strip().lower()

    if action == "create":
        return _cron_create(app, args)
    if action == "pause":
        return _cron_pause(app, args)
    if action == "resume":
        return _cron_resume(app, args)
    if action == "show":
        return _cron_show(app, args)
    if action == "status":
        return _cron_status(app)
    if action == "run":
        return _cron_run(app, args)
    print(f"Unknown cron command: {action}")
    return 1


def _cron_status(app: APIRuntime) -> int:
    readiness = dict(app.scheduler_readiness())
    state = str(readiness.get("state") or "unavailable")
    print("Scheduler Status")
    print("================")
    print(f"State: {state}")
    if reason := str(readiness.get("reason") or "").strip():
        print(f"Reason: {reason}")
    if heartbeat := str(readiness.get("last_heartbeat_at") or "").strip():
        print(f"Last heartbeat: {heartbeat}")
    if command := str(readiness.get("check_command") or "").strip():
        print(f"Check: {command}")
    return 0 if state == "ready" else 1


def _cron_create(app: APIRuntime, args) -> int:
    instruction = str(getattr(args, "instruction", "") or "").strip()
    if not instruction:
        print("Error: --instruction is required")
        return 1

    try:
        schedule = build_schedule_payload(args)
    except (TypeError, ValueError) as exc:
        print(f"Error: {exc}")
        return 1

    payload: dict[str, object] = {
        "instruction": instruction,
        "schedule": schedule,
    }
    name = str(getattr(args, "name", "") or "").strip()
    if name:
        payload["name"] = name

    output = execute_task_tool(
        app, args, tool_name="task.schedule", payload=payload, owner="cron"
    )

    if bool(getattr(args, "json", False)):
        print_json_payload(output)
        return 0 if output["ok"] else 1

    if not output["ok"]:
        print(f"Error: {output['error'] or 'task.schedule failed'}")
        return 1

    data = output["data"]
    print("Scheduled task created")
    print(f"  task_id: {data.get('task_id')}")
    print(f"  name: {data.get('name')}")
    print(f"  next_due_at: {data.get('next_due_at')}")
    scheduler_note = str(data.get("scheduler_note", "") or "").strip()
    if scheduler_note:
        print(f"  note: {scheduler_note}")
    return 0


def _cron_pause(app: APIRuntime, args) -> int:
    task_id = str(getattr(args, "task_id", "") or "").strip()
    if not task_id:
        print("Error: task_id is required")
        return 1
    output = execute_task_tool(
        app,
        args,
        tool_name="task.pause",
        payload={"task_id": task_id},
        owner="cron",
    )
    if bool(getattr(args, "json", False)):
        print_json_payload(output)
        return 0 if output["ok"] else 1
    if not output["ok"]:
        print(f"Error: {output['error'] or 'task.pause failed'}")
        return 1
    data = output["data"]
    print("Scheduled task paused")
    print(f"  task_id: {data.get('task_id')}")
    print(f"  enabled: {data.get('enabled')}")
    print(f"  next_due_at: {data.get('next_due_at')}")
    return 0


def _cron_resume(app: APIRuntime, args) -> int:
    task_id = str(getattr(args, "task_id", "") or "").strip()
    if not task_id:
        print("Error: task_id is required")
        return 1
    output = execute_task_tool(
        app,
        args,
        tool_name="task.resume",
        payload={"task_id": task_id},
        owner="cron",
    )
    if bool(getattr(args, "json", False)):
        print_json_payload(output)
        return 0 if output["ok"] else 1
    if not output["ok"]:
        print(f"Error: {output['error'] or 'task.resume failed'}")
        return 1
    data = output["data"]
    print("Scheduled task resumed")
    print(f"  task_id: {data.get('task_id')}")
    print(f"  enabled: {data.get('enabled')}")
    print(f"  next_due_at: {data.get('next_due_at')}")
    return 0


def _cron_show(app: APIRuntime, args) -> int:
    task_id = str(getattr(args, "task_id", "") or "").strip()
    if not task_id:
        print("Error: task_id is required")
        return 1
    payload: dict[str, object] = {"task_id": task_id}
    runs_limit = getattr(args, "runs_limit", None)
    if runs_limit is not None:
        payload["runs_limit"] = int(runs_limit)
    output = execute_task_tool(
        app, args, tool_name="task.show", payload=payload, owner="cron"
    )
    if bool(getattr(args, "json", False)):
        print_json_payload(output)
        return 0 if output["ok"] else 1
    if not output["ok"]:
        print(f"Error: {output['error'] or 'task.show failed'}")
        return 1
    task = dict((output.get("data") or {}).get("task") or {})
    print("Scheduled task details")
    print(f"  task_id: {task.get('task_id')}")
    print(f"  enabled: {task.get('enabled')}")
    print(f"  schedule: {task.get('schedule_summary')}")
    print(f"  next_due_at: {task.get('next_due_at')}")
    print(f"  latest_run_state: {task.get('latest_run_state')}")
    print(f"  latest_run_at: {task.get('latest_run_at')}")
    print(f"  failure_count: {task.get('failure_count')}")
    return 0


def _cron_run(app: APIRuntime, args) -> int:
    job_id = str(getattr(args, "job_id", "") or "").strip()
    if not job_id:
        print("Error: job-id is required for run")
        return 1
    output = execute_task_tool(
        app,
        args,
        tool_name="task.run_now",
        payload={"task_id": job_id},
        owner="cron",
    )
    if bool(getattr(args, "json", False)):
        print_json_payload(output)
        return 0 if output["ok"] else 1
    if not output["ok"]:
        print(f"Error: {output['error'] or 'task.run_now failed'}")
        return 1
    print("Scheduled task queued")
    print(f"  task_id: {job_id}")
    print(f"  run_id: {output['data'].get('run_id')}")
    return 0


def _add_cron_session_and_json_args(parser: argparse.ArgumentParser) -> None:
    add_tool_session_arg(
        parser, default="cron", help_text="Session id for tool execution traces"
    )
    add_json_output_flag(parser)


def _finalize_cron_subcommand(parser: argparse.ArgumentParser) -> None:
    parser.set_defaults(handler=run_cron, needs_app=True)


def _register_cron_create_subcommand(cron_subcommands) -> None:
    parser = cron_subcommands.add_parser(
        "create",
        help="Create a scheduled task via task.schedule contract",
    )
    parser.add_argument(
        "--instruction",
        required=True,
        help="Instruction/message to run when the task fires",
    )
    schedule_group = parser.add_mutually_exclusive_group(required=True)
    schedule_group.add_argument(
        "--every-ms", dest="every_ms", type=int, help="Run every N milliseconds"
    )
    schedule_group.add_argument(
        "--cron", dest="cron_expr", help="5-field cron expression"
    )
    schedule_group.add_argument(
        "--at", dest="at_iso", help="One-shot ISO8601 timestamp"
    )
    parser.add_argument(
        "--tz",
        dest="timezone",
        default=None,
        help="Timezone for --cron schedule (for example: UTC, America/Los_Angeles)",
    )
    parser.add_argument("--name", default=None, help="Optional task name")
    parser.add_argument(
        "--agent-id",
        default=None,
        help="Optional agent owner (defaults to runtime context agent)",
    )
    _add_cron_session_and_json_args(parser)
    _finalize_cron_subcommand(parser)


def _register_cron_task_action_subcommand(
    cron_subcommands, *, name: str, help_text: str, add_extra_args=None
) -> None:
    parser = cron_subcommands.add_parser(name, help=help_text)
    parser.add_argument("task_id", help="Exact task id")
    parser.add_argument("--agent-id", default=None)
    if add_extra_args is not None:
        add_extra_args(parser)
    _add_cron_session_and_json_args(parser)
    _finalize_cron_subcommand(parser)


def _add_cron_show_extra_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--runs-limit",
        type=int,
        default=5,
        help="Maximum number of recent runs to display",
    )


def _register_cron_simple_subcommand(
    cron_subcommands, *, name: str, help_text: str
) -> None:
    parser = cron_subcommands.add_parser(name, help=help_text)
    _finalize_cron_subcommand(parser)


def _register_cron_run_subcommand(cron_subcommands) -> None:
    parser = cron_subcommands.add_parser(
        "run", help="Run one exact owned task now"
    )
    parser.add_argument("--job-id", required=True, help="Exact owned task ID to run")
    parser.add_argument("--agent-id", default=None)
    _add_cron_session_and_json_args(parser)
    _finalize_cron_subcommand(parser)


def _register_cron_family(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
    *,
    command_name: str,
    help_text: str,
) -> None:
    cron = subparsers.add_parser(command_name, help=help_text)
    cron_subcommands = cron.add_subparsers(dest="cron_command")

    _register_cron_create_subcommand(cron_subcommands)
    _register_cron_simple_subcommand(
        cron_subcommands, name="status", help_text="Show cron scheduler health"
    )
    _register_cron_task_action_subcommand(
        cron_subcommands,
        name="pause",
        help_text="Pause a scheduled task by exact task id",
    )
    _register_cron_task_action_subcommand(
        cron_subcommands,
        name="resume",
        help_text="Resume a scheduled task by exact task id",
    )
    _register_cron_task_action_subcommand(
        cron_subcommands,
        name="show",
        help_text="Show one scheduled task by exact task id",
        add_extra_args=_add_cron_show_extra_args,
    )
    _register_cron_run_subcommand(cron_subcommands)


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    _register_cron_family(subparsers, command_name="cron", help_text="Cron operations")


def register_schedule_alias(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    _register_cron_family(
        subparsers,
        command_name="schedule",
        help_text="Friendly schedule facade over cron/task operations",
    )
