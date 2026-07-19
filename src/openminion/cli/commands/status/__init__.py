from __future__ import annotations

import argparse
from typing import Any

from openminion.cli.parser.flags import add_json_output_flag
from openminion.cli.presentation.json_output import print_json_payload


def run_status(args) -> int:
    if args.status_command == "onboarding":
        from .runtime import run_onboarding_status

        return run_onboarding_status(args)
    config = _load_status_config(args.config)
    from .action_policy import run_action_policy_status
    from .context_trace import run_context_trace_status
    from .identity import run_identity_status, run_self_improvement_status
    from .runtime import (
        run_capabilities_status,
        run_extensions_status,
        run_runtime_status,
        run_tools_status,
    )
    from .self import run_self_status
    from .tokens import run_tokens_status

    handler = {
        "tools": run_tools_status,
        "capabilities": run_capabilities_status,
        "runtime": run_runtime_status,
        "self": run_self_status,
        "identity": run_identity_status,
        "action-policy": run_action_policy_status,
        "extensions": run_extensions_status,
        "tokens": run_tokens_status,
        "context-trace": run_context_trace_status,
    }.get(args.status_command)
    if handler is not None:
        return handler(args, config=config)
    if args.status_command in {"notes", "note-activate"}:
        return run_self_improvement_status(args, config=config)
    if args.status_command == "owner":
        from openminion.api.queries.owner import get_owner_status

        payload = {
            "ok": True,
            **get_owner_status(
                config_path=args.config,
                session_limit=args.session_limit,
                run_limit_per_session=args.run_limit,
                window_hours=args.hours,
            ),
        }
        _print_owner_status(payload=payload, as_json=bool(args.json))
        return 0

    return _run_session_storage_status(args, config=config)


def _run_session_storage_status(args: Any, *, config: Any) -> int:
    from openminion.modules.storage.runtime.context import build_runtime_storage
    from openminion.modules.storage.runtime.sqlite import resolve_database_path
    from openminion.modules.task.run import list_session_run_events, list_session_runs

    runtime_storage = build_runtime_storage(resolve_database_path(config.storage.path))
    try:
        sessions = runtime_storage.sessions
        session = sessions.get_session(args.session_id)
        if session is None:
            raise RuntimeError(f"Session '{args.session_id}' was not found.")

        if args.status_command == "runs":
            runs = list_session_runs(
                sessions,
                session_id=session.id,
                limit=args.limit,
            )
            payload = {
                "ok": True,
                "session": _build_session_payload(session),
                "runs": [run.to_dict() for run in runs],
                "limit": int(args.limit),
            }
            _print_status_runs(payload=payload, as_json=bool(args.json))
            return 0

        if args.status_command == "run-events":
            events = list_session_run_events(
                sessions,
                session_id=session.id,
                run_id=args.run_id,
                limit=args.limit,
            )
            if not events:
                raise RuntimeError(
                    f"Run '{args.run_id}' was not found in session '{session.id}'."
                )

            payload = {
                "ok": True,
                "session": _build_session_payload(session),
                "run_id": args.run_id,
                "events": [event.to_dict() for event in events],
                "limit": int(args.limit),
            }
            _print_run_events(payload=payload, as_json=bool(args.json))
            return 0

        raise RuntimeError(
            "Unknown status command. Use `status runs`, `status run-events`, `status tokens`, `status owner`, `status identity`, `status tools`, `status capabilities`, `status runtime`, `status notes`, or `status action-policy`."
        )
    finally:
        runtime_storage.close()


def _load_status_config(config_path: str) -> Any:
    from openminion.cli.config import load_cli_config

    return load_cli_config(config_path)


def _build_session_payload(session) -> dict[str, object]:
    return {
        "id": session.id,
        "channel": session.channel,
        "target": session.target,
        "created_at": session.created_at,
        "updated_at": session.updated_at,
    }


def _print_status_runs(*, payload: dict, as_json: bool) -> None:
    if as_json:
        print_json_payload(payload)
        return

    session = payload["session"]
    runs = payload["runs"]
    print(f"status runs: session={session['id']} count={len(runs)}")
    for run in runs:
        ended = run["ended_at"] or "-"
        line = (
            f"- run_id={run['run_id']} state={run['state']} step={run['current_step']} "
            f"started_at={run['started_at']} ended_at={ended} events={run['event_count']}"
        )
        if run.get("error"):
            line += f" error={run['error']}"
        print(line)


def _print_run_events(*, payload: dict, as_json: bool) -> None:
    if as_json:
        print_json_payload(payload)
        return

    session = payload["session"]
    events = payload["events"]
    print(
        f"status run-events: session={session['id']} run_id={payload['run_id']} count={len(events)}"
    )
    for event in events:
        line = (
            f"- id={event['id']} created_at={event['created_at']} "
            f"state={event['state']} step={event['current_step']} type={event['event_type']}"
        )
        event_payload = event.get("payload", {})
        if isinstance(event_payload, dict):
            decision_action = str(
                event_payload.get("thread_decision_action", "")
                or event_payload.get("action", "")
            ).strip()
            decision_reason = str(
                event_payload.get("thread_decision_reason", "")
                or event_payload.get("reason_code", "")
            ).strip()
            thread_state_before = str(
                event_payload.get("thread_state_before", "")
            ).strip()
            thread_state_qualifier = str(
                event_payload.get("thread_state_qualifier", "")
            ).strip()
            if decision_action:
                line += f" thread_decision={decision_action}"
            if decision_reason:
                line += f" reason={decision_reason}"
            if thread_state_before:
                line += f" thread_state_before={thread_state_before}"
            if thread_state_qualifier:
                line += f" thread_state_qualifier={thread_state_qualifier}"
        print(line)


def _print_owner_status(*, payload: dict, as_json: bool) -> None:
    if as_json:
        print_json_payload(payload)
        return

    heartbeat = payload.get("heartbeat", {})
    summary = payload.get("summary", {})
    print(
        "status owner: "
        f"heartbeat={heartbeat.get('status', '')} "
        f"sessions={payload.get('sessions_total', 0)} "
        f"recent_runs={summary.get('runs_total', 0)} "
        f"failed={summary.get('failed_runs', 0)} "
        f"active={summary.get('active_runs', 0)}"
    )
    for alert in payload.get("alerts", []):
        print(
            f"- alert level={alert.get('level', '')} "
            f"code={alert.get('code', '')} message={alert.get('message', '')}"
        )
    for failure in payload.get("recent_failures", [])[:3]:
        print(
            f"- failure session={failure.get('session_id', '')} "
            f"run_id={failure.get('run_id', '')} error={failure.get('error', '')}"
        )


def _register_simple_status_subcommand(
    status_subcommands, name: str, help_text: str
) -> argparse.ArgumentParser:
    parser = status_subcommands.add_parser(name, help=help_text)
    add_json_output_flag(parser)
    parser.set_defaults(handler=run_status, needs_app=False)
    return parser


def _register_status_runs_subcommand(status_subcommands) -> None:
    parser = status_subcommands.add_parser(
        "runs", help="List run summaries for a session"
    )
    parser.add_argument("--session-id", required=True, help="Session identifier")
    parser.add_argument(
        "--limit", type=int, default=20, help="Maximum runs to return (default: 20)"
    )
    add_json_output_flag(parser)
    parser.set_defaults(handler=run_status, needs_app=False)


def _register_status_run_events_subcommand(status_subcommands) -> None:
    parser = status_subcommands.add_parser(
        "run-events",
        help="List lifecycle events for one run in a session",
    )
    parser.add_argument("--session-id", required=True, help="Session identifier")
    parser.add_argument("--run-id", required=True, help="Run identifier")
    parser.add_argument(
        "--limit",
        type=int,
        default=200,
        help="Maximum run events to return (default: 200)",
    )
    add_json_output_flag(parser)
    parser.set_defaults(handler=run_status, needs_app=False)


def _register_status_tokens_subcommand(status_subcommands) -> None:
    parser = status_subcommands.add_parser(
        "tokens",
        help="Inspect token usage for a session or run",
    )
    parser.add_argument("--session-id", required=True, help="Session identifier")
    parser.add_argument("--run-id", default="", help="Optional run identifier")
    parser.add_argument(
        "--event-limit",
        type=int,
        default=None,
        help="Optional positive event-read limit",
    )
    add_json_output_flag(parser)
    parser.set_defaults(handler=run_status, needs_app=False)


def _register_status_context_trace_subcommand(status_subcommands) -> None:
    parser = status_subcommands.add_parser(
        "context-trace",
        help="Inspect persisted context decision traces for a session",
    )
    parser.add_argument(
        "--session",
        "--session-id",
        dest="session_id",
        required=True,
        help="Session identifier",
    )
    parser.add_argument(
        "--turn",
        "--turn-id",
        dest="turn_id",
        default="",
        help="Optional turn / LLM-call identifier",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=50,
        help="Maximum traces to return (default: 50)",
    )
    add_json_output_flag(parser)
    parser.set_defaults(handler=run_status, needs_app=False)


def _register_status_notes_subcommand(status_subcommands) -> None:
    parser = status_subcommands.add_parser(
        "notes",
        help="List self-improvement notes for an agent",
    )
    parser.add_argument(
        "--agent-id",
        default=None,
        help="Agent id (default: resolve_default_agent_id(config))",
    )
    add_json_output_flag(parser)
    parser.set_defaults(handler=run_status, needs_app=False)


def _register_status_identity_subcommand(status_subcommands) -> None:
    parser = status_subcommands.add_parser(
        "identity",
        help="Inspect IdentityCtl profile/render state for an agent",
    )
    parser.add_argument(
        "--agent-id",
        default=None,
        help="Agent id (default: resolve_default_agent_id(config))",
    )
    parser.add_argument(
        "--root",
        default="",
        help=(
            "Deprecated compatibility flag. "
            "Use config identity.db_path / identity.bundle_root (or env overrides)."
        ),
    )
    parser.add_argument(
        "--render",
        action="store_true",
        help="Show rendered identity snippet for a purpose",
    )
    parser.add_argument(
        "--purpose",
        default="act",
        help="Purpose for rendering identity (default: act)",
    )
    add_json_output_flag(parser)
    parser.set_defaults(handler=run_status, needs_app=False)


def _register_status_onboarding_subcommand(status_subcommands) -> None:
    parser = status_subcommands.add_parser(
        "onboarding",
        help="Inspect onboarding readiness and configured-vs-available capabilities",
    )
    parser.add_argument(
        "--agent-id",
        default=None,
        help="Optional agent id for provider/profile-specific onboarding inspection",
    )
    add_json_output_flag(parser)
    parser.set_defaults(handler=run_status, needs_app=False)


def _register_status_owner_subcommand(status_subcommands) -> None:
    parser = status_subcommands.add_parser(
        "owner",
        help="Show owner-oriented routine status (heartbeat + digest summary)",
    )
    parser.add_argument(
        "--session-limit",
        type=int,
        default=20,
        help="Maximum sessions to scan (default: 20)",
    )
    parser.add_argument(
        "--run-limit",
        type=int,
        default=20,
        help="Maximum runs per session to scan (default: 20)",
    )
    parser.add_argument(
        "--hours",
        type=int,
        default=24,
        help="Digest window in hours (default: 24)",
    )
    add_json_output_flag(parser)
    parser.set_defaults(handler=run_status, needs_app=False)


def _register_status_action_policy_subcommand(status_subcommands) -> None:
    parser = status_subcommands.add_parser(
        "action-policy",
        help="Inspect effective action policy mode/rules and active grants",
    )
    parser.add_argument(
        "--session-id",
        default="",
        help="Optional session id to include session-scoped grant overlays",
    )
    parser.add_argument(
        "--agent-id",
        default=None,
        help="Optional agent id to resolve the effective per-agent action policy",
    )
    add_json_output_flag(parser)
    parser.set_defaults(handler=run_status, needs_app=False)


def _register_status_note_activate_subcommand(status_subcommands) -> None:
    parser = status_subcommands.add_parser(
        "note-activate",
        help="Manually activate a self-improvement note (review-first workflow)",
    )
    parser.add_argument(
        "--agent-id",
        default=None,
        help="Agent id (default: resolve_default_agent_id(config))",
    )
    parser.add_argument(
        "--signature",
        required=True,
        help="Improvement note signature to activate",
    )
    add_json_output_flag(parser)
    parser.set_defaults(handler=run_status, needs_app=False)


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    status = subparsers.add_parser("status", help="Inspect run/task lifecycle status")
    status_subcommands = status.add_subparsers(dest="status_command")

    _register_status_runs_subcommand(status_subcommands)
    _register_status_run_events_subcommand(status_subcommands)
    _register_status_tokens_subcommand(status_subcommands)
    _register_status_context_trace_subcommand(status_subcommands)
    _register_status_notes_subcommand(status_subcommands)
    _register_status_identity_subcommand(status_subcommands)
    _register_status_onboarding_subcommand(status_subcommands)
    _register_simple_status_subcommand(
        status_subcommands, "tools", "Inspect tool inventory status and readiness"
    )
    _register_simple_status_subcommand(
        status_subcommands,
        "capabilities",
        "Inspect effective tools/providers/modes/plugins capability posture",
    )
    _register_simple_status_subcommand(
        status_subcommands,
        "runtime",
        "Inspect runtime mode, bridge posture, and execution-boundary posture",
    )
    _register_simple_status_subcommand(
        status_subcommands,
        "self",
        "Inspect runtime self-awareness snapshot and degraded sections",
    )
    _register_status_owner_subcommand(status_subcommands)
    _register_simple_status_subcommand(
        status_subcommands,
        "extensions",
        "Inspect extension discovery and registry status",
    )
    _register_status_action_policy_subcommand(status_subcommands)
    _register_status_note_activate_subcommand(status_subcommands)
