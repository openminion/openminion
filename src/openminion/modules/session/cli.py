from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from openminion.modules.cli_common import (
    add_common_module_root_args,
    apply_home_data_root_env,
    print_json_payload,
)
from openminion.modules.config import (
    is_module_standalone_mode,
    resolve_module_data_root,
    resolve_module_home_root,
)
from openminion.modules.storage.module_cli import (
    add_storage_subcommands,
    run_module_storage_command,
)
from .constants import (
    DEFAULT_INTEGRATED_DB_SUBPATH,
    DEFAULT_STANDALONE_DB_SUBPATH,
)
from .storage.store import SQLiteSessionStore


def _json_arg(raw: str | None, *, default: Any) -> Any:
    if raw is None:
        return default
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON: {exc}") from exc


def _optional_json_arg(raw: str | None, *, default: Any = None) -> Any:
    if raw is None:
        return None
    return _json_arg(raw, default=default)


def _print_result(payload: Any) -> int:
    print_json_payload(payload)
    return 0


def _resolve_db_path(args: argparse.Namespace) -> Path:
    db_raw = str(getattr(args, "db", "") or "").strip()
    if db_raw:
        return Path(db_raw).expanduser().resolve()

    env_map = os.environ
    if is_module_standalone_mode(env_map):
        return (Path.home() / DEFAULT_STANDALONE_DB_SUBPATH).resolve()

    resolved_home_root = resolve_module_home_root(
        None,
        env_map,
        fallback_to_cwd=True,
    )
    resolved_data_root = resolve_module_data_root(
        home_root=resolved_home_root,
        env=env_map,
    )
    return (resolved_data_root / DEFAULT_INTEGRATED_DB_SUBPATH).resolve()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sessctl",
        description="openminion-session standalone CLI",
    )
    add_common_module_root_args(parser)

    def add_db_arg(subparser: argparse.ArgumentParser) -> None:
        subparser.add_argument(
            "--db",
            default=None,
            help="SQLite database path",
        )

    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="Initialize database and schema")
    add_db_arg(init)

    create = sub.add_parser("create-session", help="Create session")
    add_db_arg(create)
    create.add_argument("--title", default=None)
    create.add_argument("--status", default="active")
    create.add_argument("--session-id", default=None)
    create.add_argument("--meta-json", default="{}")
    create.add_argument("--initial-agent-id", default=None)
    create.add_argument("--profile-version", default=None)
    create.add_argument("--tags-json", default="[]")

    get = sub.add_parser("get-session", help="Get session")
    add_db_arg(get)
    get.add_argument("--session-id", required=True)

    list_sessions = sub.add_parser("list-sessions", help="List sessions")
    add_db_arg(list_sessions)
    list_sessions.add_argument("--limit", type=int, default=20)
    list_sessions.add_argument("--status", default=None)
    list_sessions.add_argument("--active-agent-id", default=None)

    set_status = sub.add_parser("set-status", help="Update session status")
    add_db_arg(set_status)
    set_status.add_argument("--session-id", required=True)
    set_status.add_argument("--status", required=True)

    archive = sub.add_parser("archive-session", help="Archive session")
    add_db_arg(archive)
    archive.add_argument("--session-id", required=True)

    bind_agent = sub.add_parser(
        "bind-agent", help="Bind session to active agent/profile"
    )
    add_db_arg(bind_agent)
    bind_agent.add_argument("--session-id", required=True)
    bind_agent.add_argument("--agent-id", required=True)
    bind_agent.add_argument("--profile-version", required=True)
    bind_agent.add_argument("--render-version", default=None)
    bind_agent.add_argument("--reason", default=None)

    llm_started = sub.add_parser(
        "log-llm-request-started", help="Append llm.request.started event"
    )
    add_db_arg(llm_started)
    llm_started.add_argument("--session-id", required=True)
    llm_started.add_argument("--purpose", required=True)
    llm_started.add_argument("--profile-version", required=True)
    llm_started.add_argument("--render-version", required=True)
    llm_started.add_argument("--agent-id", default=None)
    llm_started.add_argument("--trace-id", default=None)
    llm_started.add_argument("--task-id", default=None)
    llm_started.add_argument("--parent-event-id", default=None)

    append_turn = sub.add_parser("append-turn", help="Append turn")
    add_db_arg(append_turn)
    append_turn.add_argument("--session-id", required=True)
    append_turn.add_argument(
        "--role", required=True, choices=["user", "assistant", "system", "tool"]
    )
    append_turn.add_argument("--content", required=True)
    append_turn.add_argument("--attachments-json", default="[]")
    append_turn.add_argument("--meta-json", default="{}")

    list_turns = sub.add_parser("list-turns", help="List stored turns")
    add_db_arg(list_turns)
    list_turns.add_argument("--session-id", required=True)
    list_turns.add_argument("--limit", type=int, default=20)

    recent_turns = sub.add_parser("get-recent-turns", help="Get canonical turn events")
    add_db_arg(recent_turns)
    recent_turns.add_argument("--session-id", required=True)
    recent_turns.add_argument("--limit", type=int, default=20)

    append_event = sub.add_parser("append-event", help="Append event")
    add_db_arg(append_event)
    append_event.add_argument("--session-id", required=True)
    append_event.add_argument("--event-type", required=True)
    append_event.add_argument("--payload-json", default="{}")
    append_event.add_argument("--actor-type", default="system")
    append_event.add_argument("--actor-id", default=None)
    append_event.add_argument("--trace-json", default=None)
    append_event.add_argument("--refs-json", default=None)
    append_event.add_argument("--parent-event-id", default=None)
    append_event.add_argument("--importance", type=int, default=1)
    append_event.add_argument("--redaction", default="none")
    # compatibility fields
    append_event.add_argument("--agent-id", default=None)
    append_event.add_argument("--trace-id", default=None)
    append_event.add_argument("--span-id", default=None)
    append_event.add_argument("--task-id", default=None)
    append_event.add_argument("--parent-id", default=None)
    append_event.add_argument("--artifact-refs-json", default="[]")
    append_event.add_argument("--memory-refs-json", default="[]")
    append_event.add_argument("--status", default=None)
    append_event.add_argument("--error-json", default=None)

    list_events = sub.add_parser("list-events", help="List session events")
    add_db_arg(list_events)
    list_events.add_argument("--session-id", required=True)
    list_events.add_argument("--limit", type=int, default=30)
    list_events.add_argument("--event-type", default=None)
    list_events.add_argument("--trace-id", default=None)
    list_events.add_argument("--agent-id", default=None)
    list_events.add_argument("--status", default=None)

    get_events = sub.add_parser("get-events", help="Get canonical session events")
    add_db_arg(get_events)
    get_events.add_argument("--session-id", required=True)
    get_events.add_argument("--after-seq", type=int, default=None)
    get_events.add_argument("--types-json", default=None)
    get_events.add_argument("--limit", type=int, default=100)

    recent_tools = sub.add_parser(
        "get-recent-tool-events", help="Get recent tool-related events"
    )
    add_db_arg(recent_tools)
    recent_tools.add_argument("--session-id", required=True)
    recent_tools.add_argument("--limit", type=int, default=20)

    put_state = sub.add_parser("put-working-state", help="Store working state")
    add_db_arg(put_state)
    put_state.add_argument("--session-id", required=True)
    put_state.add_argument("--state-ref", default=None)
    put_state.add_argument("--state-inline-json", default=None)

    get_state = sub.add_parser("get-working-state", help="Get latest working state")
    add_db_arg(get_state)
    get_state.add_argument("--session-id", required=True)

    get_active_state = sub.add_parser("get-active-state", help="Get active state view")
    add_db_arg(get_active_state)
    get_active_state.add_argument("--session-id", required=True)

    set_summary_base = sub.add_parser("set-summary-base", help="Set summary base ref")
    add_db_arg(set_summary_base)
    set_summary_base.add_argument("--session-id", required=True)
    set_summary_base.add_argument("--base-ref", required=True)

    append_summary_delta = sub.add_parser(
        "append-summary-delta", help="Append summary delta ref"
    )
    add_db_arg(append_summary_delta)
    append_summary_delta.add_argument("--session-id", required=True)
    append_summary_delta.add_argument("--delta-ref", required=True)

    get_summaries = sub.add_parser("get-summaries", help="Get summary refs")
    add_db_arg(get_summaries)
    get_summaries.add_argument("--session-id", required=True)

    get_summary = sub.add_parser("get-summary", help="Get compact summary text")
    add_db_arg(get_summary)
    get_summary.add_argument("--session-id", required=True)
    get_summary.add_argument(
        "--variant", default="short", choices=["short", "long", "auto"]
    )

    update_summary = sub.add_parser("update-summary", help="Update compact summary")
    add_db_arg(update_summary)
    update_summary.add_argument("--session-id", required=True)
    update_summary.add_argument("--summary-short", required=True)
    update_summary.add_argument("--summary-long", default=None)
    update_summary.add_argument("--based-on-seq", required=True, type=int)

    needs_summary = sub.add_parser(
        "needs-summary-update", help="Check summary staleness"
    )
    add_db_arg(needs_summary)
    needs_summary.add_argument("--session-id", required=True)
    needs_summary.add_argument("--threshold-events", type=int, default=40)

    snapshot = sub.add_parser("create-snapshot", help="Create snapshot")
    add_db_arg(snapshot)
    snapshot.add_argument("--session-id", required=True)
    snapshot.add_argument("--seq-upto", type=int, default=None)

    get_slice = sub.add_parser("get-slice", help="Build SessionSlice")
    add_db_arg(get_slice)
    get_slice.add_argument("--session-id", required=True)
    get_slice.add_argument("--purpose", required=True)
    get_slice.add_argument("--max-turns", type=int, default=8)
    get_slice.add_argument("--max-tool-events", type=int, default=12)
    get_slice.add_argument(
        "--summary-variant", default="auto", choices=["short", "long", "auto"]
    )
    get_slice.add_argument(
        "--include-open-tasks", action=argparse.BooleanOptionalAction, default=True
    )
    get_slice.add_argument(
        "--include-active-state", action=argparse.BooleanOptionalAction, default=True
    )

    storage_status = sub.add_parser(
        "storage-status", help="Inspect sqlite/fallback status"
    )
    add_db_arg(storage_status)

    reindex = sub.add_parser(
        "reindex-sidecars", help="Replay sidecar fallback logs into sqlite"
    )
    add_db_arg(reindex)
    reindex.add_argument("--since-ts", default=None)

    cron_add = sub.add_parser("cron-add", help="Create cron/periodic job")
    add_db_arg(cron_add)
    cron_add.add_argument("--name", required=True)
    cron_add.add_argument("--description", default=None)
    cron_add.add_argument(
        "--enabled", action=argparse.BooleanOptionalAction, default=True
    )
    cron_add.add_argument("--agent-id", default=None)
    cron_add.add_argument("--schedule-json", required=True)
    cron_add.add_argument(
        "--session-target", default=None, choices=["main", "isolated"]
    )
    cron_add.add_argument(
        "--wake-mode", default=None, choices=["now", "next-heartbeat"]
    )
    cron_add.add_argument("--payload-json", required=True)
    cron_add.add_argument("--delivery-json", default='{"mode":"none"}')
    cron_add.add_argument(
        "--delete-after-run", action=argparse.BooleanOptionalAction, default=None
    )
    cron_add.add_argument("--misfire-policy", default="run_once")
    cron_add.add_argument("--max-lateness-s", type=int, default=600)
    cron_add.add_argument("--max-concurrency", type=int, default=1)
    cron_add.add_argument("--job-id", default=None)

    cron_list = sub.add_parser("cron-list", help="List cron/periodic jobs")
    add_db_arg(cron_list)
    cron_list.add_argument("--enabled", default="any", choices=["true", "false", "any"])
    cron_list.add_argument("--limit", type=int, default=50)

    cron_get = sub.add_parser("cron-get", help="Get cron/periodic job")
    add_db_arg(cron_get)
    cron_get.add_argument("--job-id", required=True)

    cron_enable = sub.add_parser("cron-enable", help="Enable cron job")
    add_db_arg(cron_enable)
    cron_enable.add_argument("--job-id", required=True)

    cron_disable = sub.add_parser("cron-disable", help="Disable cron job")
    add_db_arg(cron_disable)
    cron_disable.add_argument("--job-id", required=True)

    cron_remove = sub.add_parser("cron-remove", help="Remove cron job")
    add_db_arg(cron_remove)
    cron_remove.add_argument("--job-id", required=True)

    cron_run = sub.add_parser("cron-run", help="Manually enqueue a cron run")
    add_db_arg(cron_run)
    cron_run.add_argument("--job-id", required=True)
    cron_run.add_argument("--due-at", default=None)

    cron_runs = sub.add_parser("cron-runs", help="List cron job runs")
    add_db_arg(cron_runs)
    cron_runs.add_argument("--job-id", default=None)
    cron_runs.add_argument("--states-json", default=None)
    cron_runs.add_argument("--limit", type=int, default=100)

    add_storage_subcommands(sub)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    home_root = str(getattr(args, "home_root", "") or "").strip()
    data_root = str(getattr(args, "data_root", "") or "").strip()
    apply_home_data_root_env(home_root=home_root, data_root=data_root)
    db_path = _resolve_db_path(args)
    if args.command == "storage":
        return run_module_storage_command(
            args=args,
            module_id="session",
            db_path=db_path,
            home_root=home_root,
            data_root=data_root,
        )

    store = SQLiteSessionStore(db_path)
    try:
        if args.command == "init":
            return _print_result({"ok": True, "db_path": str(store.database_path)})

        if args.command == "create-session":
            meta = _json_arg(args.meta_json, default={})
            tags = _json_arg(args.tags_json, default=[])
            session_id = store.create_session(
                title=args.title,
                status=args.status,
                session_id=args.session_id,
                meta=meta,
                initial_agent_id=args.initial_agent_id,
                profile_version=args.profile_version,
                tags=tags,
            )
            return _print_result({"session_id": session_id})

        if args.command == "get-session":
            return _print_result(store.get_session(args.session_id))

        if args.command == "list-sessions":
            filters = {
                "status": args.status,
                "active_agent_id": args.active_agent_id,
            }
            return _print_result(store.list_sessions(limit=args.limit, filters=filters))

        if args.command == "set-status":
            store.set_status(args.session_id, args.status)
            return _print_result(
                {"ok": True, "session_id": args.session_id, "status": args.status}
            )

        if args.command == "archive-session":
            store.archive_session(args.session_id)
            return _print_result(
                {"ok": True, "session_id": args.session_id, "status": "archived"}
            )

        if args.command == "bind-agent":
            store.bind_agent(
                args.session_id,
                agent_id=args.agent_id,
                profile_version=args.profile_version,
                render_version=args.render_version,
                reason=args.reason,
            )
            return _print_result(
                {
                    "ok": True,
                    "session_id": args.session_id,
                    "agent_id": args.agent_id,
                    "profile_version": args.profile_version,
                }
            )

        if args.command == "log-llm-request-started":
            event_id = store.append_llm_request_started(
                args.session_id,
                purpose=args.purpose,
                profile_version=args.profile_version,
                render_version=args.render_version,
                agent_id=args.agent_id,
                trace_id=args.trace_id,
                task_id=args.task_id,
                parent_event_id=args.parent_event_id,
            )
            return _print_result(
                {
                    "ok": True,
                    "event_id": event_id,
                    "session_id": args.session_id,
                    "purpose": args.purpose,
                    "profile_version": args.profile_version,
                    "render_version": args.render_version,
                }
            )

        if args.command == "append-turn":
            turn_id = store.append_turn(
                args.session_id,
                role=args.role,
                content=args.content,
                attachments=_json_arg(args.attachments_json, default=[]),
                meta=_json_arg(args.meta_json, default={}),
            )
            return _print_result({"turn_id": turn_id})

        if args.command == "list-turns":
            return _print_result(store.list_turns(args.session_id, limit=args.limit))

        if args.command == "get-recent-turns":
            return _print_result(store.get_recent_turns(args.session_id, args.limit))

        if args.command == "append-event":
            trace = _optional_json_arg(args.trace_json)
            refs = _optional_json_arg(args.refs_json)
            event_id = store.append_event(
                args.session_id,
                event_type=args.event_type,
                payload=_json_arg(args.payload_json, default={}),
                actor_type=args.actor_type,
                actor_id=args.actor_id,
                trace=trace,
                refs=refs,
                parent_event_id=args.parent_event_id,
                importance=args.importance,
                redaction=args.redaction,
                agent_id=args.agent_id,
                trace_id=args.trace_id,
                span_id=args.span_id,
                task_id=args.task_id,
                parent_id=args.parent_id,
                artifact_refs=_json_arg(args.artifact_refs_json, default=[]),
                memory_refs=_json_arg(args.memory_refs_json, default=[]),
                status=args.status,
                error=_optional_json_arg(args.error_json, default={}),
            )
            return _print_result({"event_id": event_id})

        if args.command == "list-events":
            return _print_result(
                store.list_events(
                    args.session_id,
                    limit=args.limit,
                    event_type=args.event_type,
                    trace_id=args.trace_id,
                    agent_id=args.agent_id,
                    status=args.status,
                )
            )

        if args.command == "get-events":
            return _print_result(
                store.get_events(
                    args.session_id,
                    after_seq=args.after_seq,
                    types=_optional_json_arg(args.types_json, default=[]),
                    limit=args.limit,
                )
            )

        if args.command == "get-recent-tool-events":
            return _print_result(
                store.get_recent_tool_events(args.session_id, args.limit)
            )

        if args.command == "put-working-state":
            inline = _optional_json_arg(args.state_inline_json, default={})
            version = store.put_working_state(
                args.session_id,
                state_ref=args.state_ref,
                state_inline=inline,
            )
            return _print_result({"version": version})

        if args.command == "get-working-state":
            return _print_result(store.get_latest_working_state(args.session_id))

        if args.command == "get-active-state":
            return _print_result(store.get_active_state(args.session_id))

        if args.command == "set-summary-base":
            store.set_summary_base(args.session_id, args.base_ref)
            return _print_result(
                {"ok": True, "session_id": args.session_id, "base_ref": args.base_ref}
            )

        if args.command == "append-summary-delta":
            store.append_summary_delta(args.session_id, args.delta_ref)
            return _print_result(
                {"ok": True, "session_id": args.session_id, "delta_ref": args.delta_ref}
            )

        if args.command == "get-summaries":
            return _print_result(store.get_summaries(args.session_id))

        if args.command == "get-summary":
            return _print_result(
                {
                    "session_id": args.session_id,
                    "summary": store.get_summary(args.session_id, variant=args.variant),
                }
            )

        if args.command == "update-summary":
            store.update_summary(
                args.session_id,
                summary_short=args.summary_short,
                summary_long=args.summary_long,
                based_on_seq=args.based_on_seq,
            )
            return _print_result({"ok": True, "session_id": args.session_id})

        if args.command == "needs-summary-update":
            needs = store.needs_summary_update(
                args.session_id, threshold_events=args.threshold_events
            )
            return _print_result(
                {"session_id": args.session_id, "needs_summary_update": needs}
            )

        if args.command == "create-snapshot":
            snapshot_id = store.create_snapshot(args.session_id, seq_upto=args.seq_upto)
            return _print_result(
                {"session_id": args.session_id, "snapshot_id": snapshot_id}
            )

        if args.command == "get-slice":
            limits = {
                "max_turns": args.max_turns,
                "max_tool_events": args.max_tool_events,
                "summary_variant": args.summary_variant,
                "include_open_tasks": args.include_open_tasks,
                "include_active_state": args.include_active_state,
            }
            return _print_result(
                store.get_slice(args.session_id, purpose=args.purpose, limits=limits)
            )

        if args.command == "cron-add":
            created_job_id = store.add_cron_job(
                name=args.name,
                description=args.description,
                enabled=args.enabled,
                agent_id=args.agent_id,
                schedule=_json_arg(args.schedule_json, default={}),
                session_target=args.session_target,
                wake_mode=args.wake_mode,
                payload=_json_arg(args.payload_json, default={}),
                delivery=_json_arg(args.delivery_json, default={"mode": "none"}),
                delete_after_run=args.delete_after_run,
                misfire_policy=args.misfire_policy,
                max_lateness_s=args.max_lateness_s,
                max_concurrency=args.max_concurrency,
                job_id=args.job_id,
            )
            return _print_result({"ok": True, "job_id": created_job_id})

        if args.command == "cron-list":
            enabled_filter: bool | None
            if args.enabled == "true":
                enabled_filter = True
            elif args.enabled == "false":
                enabled_filter = False
            else:
                enabled_filter = None
            return _print_result(
                store.list_cron_jobs(enabled=enabled_filter, limit=args.limit)
            )

        if args.command == "cron-get":
            return _print_result(store.get_cron_job(args.job_id))

        if args.command == "cron-enable":
            store.set_cron_job_enabled(args.job_id, True)
            return _print_result({"ok": True, "job_id": args.job_id, "enabled": True})

        if args.command == "cron-disable":
            store.set_cron_job_enabled(args.job_id, False)
            return _print_result({"ok": True, "job_id": args.job_id, "enabled": False})

        if args.command == "cron-remove":
            store.delete_cron_job(args.job_id)
            return _print_result({"ok": True, "job_id": args.job_id, "removed": True})

        if args.command == "cron-run":
            run_id = store.trigger_cron_run(args.job_id, due_at=args.due_at)
            return _print_result({"ok": True, "job_id": args.job_id, "run_id": run_id})

        if args.command == "cron-runs":
            states = _optional_json_arg(args.states_json, default=[])
            return _print_result(
                store.list_cron_runs(
                    job_id=args.job_id, states=states, limit=args.limit
                )
            )

        if args.command == "storage-status":
            return _print_result(store.storage_status())

        if args.command == "reindex-sidecars":
            return _print_result(store.reindex_sidecars(since_ts=args.since_ts))

        parser.error(f"unsupported command: {args.command}")
        return 2
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    finally:
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())
