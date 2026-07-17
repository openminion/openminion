from __future__ import annotations

import argparse
from typing import Optional

from openminion.api.runtime import APIRuntime
from openminion.cli.parser.flags import (
    add_json_output_flag,
    add_tool_session_arg,
)
from openminion.cli.presentation.json_output import print_json_payload
from openminion.modules.llm.providers.base import ProviderToolCall
from openminion.modules.tool.base import ToolExecutionContext
from openminion.modules.brain.paths import resolve_brain_sessions_db_path
from openminion.modules.policy.adapters.composition import (
    SEAM_CLI_CRON,
    build_default_composition_boundary_adapter,
)
from openminion.modules.tool.selection import ToolSelectionService
from openminion.modules.tool.runtime.routing import build_runtime_tool_routing_metadata
from openminion.modules.task.constants import DEFAULT_TASK_MIN_EVERY_MS


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
    elif action == "run":
        job_id = getattr(args, "job_id", None)
        return _cron_run(app, job_id=job_id)
    elif action == "tick":
        return _cron_tick(app)
    else:
        print(f"Unknown cron command: {action}")
        return 1


def _cron_status(app: APIRuntime) -> int:
    try:
        from openminion.modules.storage.runtime.sqlite import resolve_database_path
        from openminion.modules.session.storage.sqlite_store import SQLiteSessionStore

        storage_path = resolve_database_path(app.config.storage.path)
        db_path = resolve_brain_sessions_db_path(storage_path=storage_path)
        store = SQLiteSessionStore(db_path)
    except ImportError:
        print("openminion-session is required for cron state")
        return 1

    jobs = store.list_cron_jobs()

    print("Cron Status")
    print("===========")
    print(f"Registered Jobs: {len(jobs)}")
    for j in jobs:
        schedule = j.get("schedule_json") or j.get("schedule", {})
        if isinstance(schedule, dict):
            schedule_summary = f"{schedule.get('kind')}:{schedule.get('expr') or schedule.get('every_ms') or schedule.get('at', '')}"
        else:
            schedule_summary = str(schedule)
        print(
            f"  [{j.get('job_id')}] schedule={schedule_summary} next={j.get('next_due_at')}"
        )

    return 0


def _build_schedule_payload(args) -> dict:
    every_ms = getattr(args, "every_ms", None)
    cron_expr = str(getattr(args, "cron_expr", "") or "").strip()
    at_iso = str(getattr(args, "at_iso", "") or "").strip()
    tz_name = str(getattr(args, "timezone", "") or "").strip()

    if every_ms is not None:
        interval = int(every_ms)
        if interval < DEFAULT_TASK_MIN_EVERY_MS:
            raise ValueError(f"--every-ms must be at least {DEFAULT_TASK_MIN_EVERY_MS}")
        return {"kind": "every", "every_ms": interval}

    if cron_expr:
        payload = {"kind": "cron", "expr": cron_expr}
        if tz_name:
            payload["tz"] = tz_name
        return payload

    if at_iso:
        return {"kind": "at", "at": at_iso}

    raise ValueError("one schedule selector is required: --every-ms, --cron, or --at")


def _tool_execution_metadata(app: APIRuntime, args) -> tuple[dict[str, object], str]:
    runtime_config = getattr(app.config, "runtime", None)
    metadata: dict[str, object] = {
        "origin": "openminion.cron",
        "tool_call_origin": "cli",
        "allow_runtime_direct": "1",
        "runtime_env": dict(getattr(runtime_config, "env", {}) or {}),
        **build_runtime_tool_routing_metadata(getattr(runtime_config, "tools", None)),
        **ToolSelectionService(
            getattr(runtime_config, "tool_selection", None),
            app.tools,
        ).runtime_binding_policy_metadata(),
    }
    agent_id = str(getattr(args, "agent_id", "") or "").strip()
    if agent_id:
        metadata["agent_id"] = agent_id
    session_id = str(getattr(args, "session", "") or "").strip() or "cron"
    return metadata, session_id


def _execute_task_tool(
    app: APIRuntime,
    args,
    *,
    tool_name: str,
    payload: dict[str, object],
) -> dict[str, object]:
    metadata, session_id = _tool_execution_metadata(app, args)
    batch = app.tools.execute_calls(
        [
            ProviderToolCall(
                name=tool_name,
                arguments=payload,
                source="cli-cron",
            )
        ],
        context=ToolExecutionContext(
            channel="console",
            target="cli-cron",
            session_id=session_id,
            metadata=metadata,
            blast_radius_adapter=build_default_composition_boundary_adapter(
                seam_id=SEAM_CLI_CRON,
            ),
        ),
    )
    result = batch.results[0] if getattr(batch, "results", None) else None
    if result is None:
        return {
            "ok": False,
            "tool": tool_name,
            "error": f"{tool_name} produced no result",
            "data": {},
        }
    return {
        "ok": bool(result.ok),
        "tool": tool_name,
        "error": str(getattr(result, "error", "") or ""),
        "data": dict(getattr(result, "data", {}) or {}),
    }


def _cron_create(app: APIRuntime, args) -> int:
    instruction = str(getattr(args, "instruction", "") or "").strip()
    if not instruction:
        print("Error: --instruction is required")
        return 1

    try:
        schedule = _build_schedule_payload(args)
    except Exception as exc:
        print(f"Error: {exc}")
        return 1

    payload: dict[str, object] = {
        "instruction": instruction,
        "schedule": schedule,
    }
    name = str(getattr(args, "name", "") or "").strip()
    if name:
        payload["name"] = name

    output = _execute_task_tool(app, args, tool_name="task.schedule", payload=payload)

    if bool(getattr(args, "json", False)):
        print_json_payload(output)
        return 0 if output["ok"] else 1

    if not output["ok"]:
        print(f"Error: {output['error'] or 'task.schedule failed'}")
        return 1

    data = output["data"]
    print("Cron job created")
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
    output = _execute_task_tool(
        app,
        args,
        tool_name="task.pause",
        payload={"task_id": task_id},
    )
    if bool(getattr(args, "json", False)):
        print_json_payload(output)
        return 0 if output["ok"] else 1
    if not output["ok"]:
        print(f"Error: {output['error'] or 'task.pause failed'}")
        return 1
    data = output["data"]
    print("Cron job paused")
    print(f"  task_id: {data.get('task_id')}")
    print(f"  enabled: {data.get('enabled')}")
    print(f"  next_due_at: {data.get('next_due_at')}")
    return 0


def _cron_resume(app: APIRuntime, args) -> int:
    task_id = str(getattr(args, "task_id", "") or "").strip()
    if not task_id:
        print("Error: task_id is required")
        return 1
    output = _execute_task_tool(
        app,
        args,
        tool_name="task.resume",
        payload={"task_id": task_id},
    )
    if bool(getattr(args, "json", False)):
        print_json_payload(output)
        return 0 if output["ok"] else 1
    if not output["ok"]:
        print(f"Error: {output['error'] or 'task.resume failed'}")
        return 1
    data = output["data"]
    print("Cron job resumed")
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
    output = _execute_task_tool(app, args, tool_name="task.show", payload=payload)
    if bool(getattr(args, "json", False)):
        print_json_payload(output)
        return 0 if output["ok"] else 1
    if not output["ok"]:
        print(f"Error: {output['error'] or 'task.show failed'}")
        return 1
    task = dict((output.get("data") or {}).get("task") or {})
    print("Cron job details")
    print(f"  task_id: {task.get('task_id')}")
    print(f"  enabled: {task.get('enabled')}")
    print(f"  schedule: {task.get('schedule_summary')}")
    print(f"  next_due_at: {task.get('next_due_at')}")
    print(f"  latest_run_state: {task.get('latest_run_state')}")
    print(f"  latest_run_at: {task.get('latest_run_at')}")
    print(f"  failure_count: {task.get('failure_count')}")
    return 0


def _cron_run(app: APIRuntime, *, job_id: Optional[str]) -> int:
    if not job_id:
        print("Error: job-id is required for run")
        return 1

    try:
        from openminion.modules.storage.runtime.sqlite import resolve_database_path
        from openminion.modules.session.storage.sqlite_store import SQLiteSessionStore

        storage_path = resolve_database_path(app.config.storage.path)
        db_path = resolve_brain_sessions_db_path(storage_path=storage_path)
        store = SQLiteSessionStore(db_path)
    except ImportError:
        print("openminion-session is required for cron run")
        return 1

    job = store.get_cron_job(job_id)
    if not job:
        print(f"Error: cron job {job_id} not found")
        return 1

    print(f"Manually enqueueing cron job: {job_id}")
    run_id = store.trigger_cron_run(job_id=job_id, due_at=job.get("next_due_at"))
    print(f"Enqueued run_id: {run_id}")
    return 0


def _cron_tick(app: APIRuntime) -> int:
    try:
        from openminion.modules.storage.runtime.sqlite import resolve_database_path
        from openminion.modules.session.storage.sqlite_store import SQLiteSessionStore
    except Exception as exc:
        print(f"Failed to import scheduler dependencies: {exc}")
        return 1

    try:
        storage_path = resolve_database_path(app.config.storage.path)
        db_path = resolve_brain_sessions_db_path(storage_path=storage_path)
        store = SQLiteSessionStore(db_path)
    except Exception as exc:
        print(f"Failed to resolve cron store: {exc}")
        return 1

    def _execute_turn(job: dict, run: dict) -> dict:
        print(f"Tick executing run {run['run_id']} for job {job['job_id']}")
        return {"summary": "Manual tick execution", "status": "completed"}

    try:
        store.enqueue_due_cron_runs("cli-manual-tick", lease_ttl_s=60, max_jobs=50)
        acquired = store.acquire_cron_runs("cli-manual-tick", limit=1)
    except Exception as exc:
        print(f"Failed to enqueue/acquire cron runs: {exc}")
        return 1

    if not acquired:
        print("No cron runs acquired.")
        return 0

    errors = 0
    for run in acquired:
        run_id = run.get("run_id", "?")
        job_id = run.get("job_id", "?")
        try:
            job = store.get_cron_job(job_id)
            if not job:
                print(f"Skipping run {run_id}: job {job_id} not found")
                store.finish_cron_run(
                    run_id=run_id, state="failed", summary="Job not found"
                )
                errors += 1
                continue
            print(f"Acquired run: {run_id} for job: {job_id}")
            res = _execute_turn(job, run)
            store.finish_cron_run(
                run_id=run_id,
                state="finished",
                summary=res.get("summary", "Done"),
            )
        except Exception as exc:
            print(f"Error processing run {run_id}: {exc}")
            try:
                store.finish_cron_run(run_id=run_id, state="failed", summary=str(exc))
            except Exception:
                pass
            errors += 1

    return 1 if errors else 0


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
        "run", help="Manually enqueue a run for a cron job"
    )
    parser.add_argument("--job-id", required=True, help="Job ID to run")
    _finalize_cron_subcommand(parser)


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    cron = subparsers.add_parser("cron", help="Cron operations")
    cron_subcommands = cron.add_subparsers(dest="cron_command")

    _register_cron_create_subcommand(cron_subcommands)
    _register_cron_simple_subcommand(
        cron_subcommands, name="status", help_text="Show scheduled cron jobs"
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
    _register_cron_simple_subcommand(
        cron_subcommands, name="tick", help_text="Manually tick the cron scheduler once"
    )
