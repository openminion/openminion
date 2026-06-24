from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from openminion.cli.config import load_cli_config, resolve_cli_roots
from openminion.cli.transport.daemon_client import daemon_request
from openminion.cli.parser.flags import add_json_output_flag
from openminion.cli.presentation.json_output import print_json_payload
from openminion.services.diagnostics.debug import (
    get_debug_registry,
    is_debug_surface_enabled,
)

from .registry import register_core_providers


load_config = load_cli_config


def run_debug(args) -> int:
    config = load_config(args.config)
    if not is_debug_surface_enabled(config, surface="cli"):
        print(
            "debug CLI is disabled by config (runtime.debug_enabled/runtime.debug_cli_enabled)."
        )
        return 1
    action = str(getattr(args, "debug_command", "")).strip().lower()
    if action == "modules":
        return _debug_modules(args)
    if action == "module":
        return _debug_module(args)
    if action == "timeline":
        return _debug_timeline(args, config)
    if action == "trace":
        return _debug_trace(args)
    raise RuntimeError("Unknown debug command")


def _debug_modules(args) -> int:
    registry = get_debug_registry()
    register_core_providers(registry)

    config = load_config(args.config)
    auto_start = bool(getattr(config.runtime, "daemon_auto_start", False))

    daemon_payload = None
    try:
        from openminion.cli.commands.daemon import ensure_daemon_running

        endpoint = ensure_daemon_running(args.config, auto_start=auto_start)
        status, payload = daemon_request(
            endpoint=endpoint,
            method="GET",
            path="/v1/debug/modules",
            timeout_s=10,
        )
        if status < 400 and payload.get("ok"):
            daemon_payload = payload.get("modules", [])
    except Exception:
        pass

    if daemon_payload:
        modules = daemon_payload
    else:
        debug_payloads = registry.get_all_debug()
        modules = [p.to_dict() for p in debug_payloads]

    if getattr(args, "json", False):
        print_json_payload({"ok": True, "modules": modules})
    else:
        for mod in modules:
            status_indicator = {
                "ok": "✓",
                "warn": "⚠",
                "fail": "✗",
                "unknown": "?",
            }.get(mod.get("status", "unknown"), "?")
            print(
                f"{status_indicator} {mod.get('module', 'unknown')}: "
                f"{mod.get('status', 'unknown')} ({mod.get('wiring_source', 'unknown')})"
            )

    return 0


def _debug_module(args) -> int:
    module_name = str(getattr(args, "module_name", "")).strip()
    if not module_name:
        print("Error: --name is required", file="__stderr__")
        return 1

    registry = get_debug_registry()
    register_core_providers(registry)

    config = load_config(args.config)
    auto_start = bool(getattr(config.runtime, "daemon_auto_start", False))

    daemon_payload = None
    try:
        from openminion.cli.commands.daemon import ensure_daemon_running

        endpoint = ensure_daemon_running(args.config, auto_start=auto_start)
        status, payload = daemon_request(
            endpoint=endpoint,
            method="GET",
            path=f"/v1/debug/modules/{module_name}",
            timeout_s=10,
        )
        if status < 400 and payload.get("ok"):
            daemon_payload = payload.get("module")
    except Exception:
        pass

    if daemon_payload:
        module = daemon_payload
    else:
        provider = registry.get_module(module_name)
        if provider is None:
            print(f"Error: Unknown module '{module_name}'", file="__stderr__")
            return 1
        try:
            module = provider.get_debug().to_dict()
        except Exception as exc:
            print(f"Error: Failed to get debug info: {exc}", file="__stderr__")
            return 1

    if getattr(args, "json", False):
        print_json_payload({"ok": True, "module": module})
    else:
        print(f"Module: {module.get('module')}")
        print(f"Status: {module.get('status')}")
        print(f"Mode: {module.get('mode')}")
        print(f"Wiring: {module.get('wiring_source')}")
        if module.get("fallback"):
            print(f"Fallback: {module.get('fallback')}")
        if module.get("last_error"):
            print(f"Last Error: {module.get('last_error')}")
        if module.get("last_success_at"):
            print(f"Last Success: {module.get('last_success_at')}")
        if module.get("details"):
            print(f"Details: {json.dumps(module.get('details'), indent=2)}")
        if module.get("dependency_failures"):
            print(
                "Dependency Failures: "
                f"{json.dumps(module.get('dependency_failures'), indent=2)}"
            )

    return 0


_LAYER_PREFIXES = {
    "run.": "run",
    "brain.": "brain",
    "llm.": "llm",
    "tool.": "tool",
    "turn.": "turn",
    "context.": "context",
    "safety.": "safety",
    "response.": "response",
    "client.": "client",
    "plan.": "plan",
    "skill.": "skill",
    "decide.": "decide",
    "judge.": "judge",
    "policy.": "policy",
    "summary.": "summary",
}

_LAYER_LABELS = {
    "run": "RUN   ",
    "brain": "BRAIN ",
    "llm": "LLM   ",
    "tool": "TOOL  ",
    "turn": "TURN  ",
    "context": "CTX   ",
    "safety": "SAFE  ",
    "response": "RESP  ",
    "client": "CLI   ",
    "plan": "PLAN  ",
    "skill": "SKILL ",
    "decide": "DECIDE",
    "judge": "JUDGE ",
    "policy": "POLICY",
    "summary": "SUMRY ",
}


def _classify_layer(event_type: str) -> str:
    for prefix, layer in _LAYER_PREFIXES.items():
        if event_type.startswith(prefix):
            return layer
    return "other"


def _resolve_session_db_path(config: Any) -> Path:
    roots = resolve_cli_roots()
    data_root = roots.data_root
    brain_db = (data_root / "state" / "brain" / "sessions.db").resolve()
    if brain_db.exists():
        return brain_db
    sessctl_db = (data_root / "session" / "sessions.db").resolve()
    if sessctl_db.exists():
        return sessctl_db
    return brain_db


def _resolve_timeline_db_path(args: Any, config: Any) -> Path:
    explicit_db = str(getattr(args, "db_path", "") or "").strip()
    if explicit_db:
        return Path(explicit_db).expanduser().resolve()
    return _resolve_session_db_path(config)


def _filter_events_by_run_id(events: list, run_id_filter: str) -> list:
    return [
        ev
        for ev in events
        if (
            str((ev.get("payload") or {}).get("run_id", "")).strip() == run_id_filter
            or str(ev.get("trace_id", "")).strip() == run_id_filter
        )
    ]


def _render_timeline_json(
    *, session_id: str, run_id_filter: str | None, events: list
) -> None:
    output = {
        "ok": True,
        "session_id": session_id,
        "event_count": len(events),
        "events": events,
    }
    if run_id_filter:
        output["run_id"] = run_id_filter
    print_json_payload(output, sort_keys=False, default=str)


def _render_timeline_text(
    *, session_id: str, run_id_filter: str | None, events: list
) -> None:
    trace_ids_seen: set[str] = set()
    for ev in events:
        tid = str(ev.get("trace_id", "") or "").strip()
        if tid and tid.lower() != "none":
            trace_ids_seen.add(tid)

    print(f"Session: {session_id}")
    if run_id_filter:
        print(f"Run filter: {run_id_filter}")
    print(f"Events: {len(events)}")
    if trace_ids_seen:
        print(f"Traces: {', '.join(sorted(trace_ids_seen))}")
    print("-" * 100)
    print(f"  {'TIMESTAMP':<26}  {'LAYER':<5}  {'EVENT TYPE':<28}  DETAILS")
    print("-" * 100)

    for ev in events:
        timestamp = str(ev.get("ts", ev.get("timestamp", ev.get("created_at", ""))))
        if len(timestamp) > 26:
            timestamp = timestamp[:26]
        event_type = str(ev.get("type", ev.get("event_type", "")))
        layer = _classify_layer(event_type)
        label = _LAYER_LABELS.get(layer, "???? ")
        payload = ev.get("payload") or {}
        details = _extract_details(event_type, payload, ev)
        print(f"  {timestamp:<26}  {label}  {event_type:<28}  {details}")

    print("-" * 100)


def _debug_timeline(args: Any, config: Any) -> int:
    session_id = str(getattr(args, "session_id", "")).strip()
    if not session_id:
        print("Error: --session is required", file=sys.stderr)
        return 1

    run_id_filter = str(getattr(args, "run_id", "") or "").strip() or None
    limit = max(1, min(int(getattr(args, "limit", 500)), 5000))
    as_json = bool(getattr(args, "json", False))

    db_path = _resolve_timeline_db_path(args, config)
    if not db_path.exists():
        print(f"Error: Session database not found at {db_path}", file=sys.stderr)
        return 1

    from openminion.modules.session.storage.sqlite_store import SQLiteSessionStore

    store = SQLiteSessionStore(db_path)
    try:
        raw_events = store.list_events(session_id, limit=limit * 2)
    except Exception as exc:
        print(f"Error querying events: {exc}", file=sys.stderr)
        return 1

    if run_id_filter:
        raw_events = _filter_events_by_run_id(raw_events, run_id_filter)
    if not raw_events:
        print(f"No events found for session '{session_id}'", file=sys.stderr)
        return 1

    events = raw_events[:limit]
    if as_json:
        _render_timeline_json(
            session_id=session_id, run_id_filter=run_id_filter, events=events
        )
    else:
        _render_timeline_text(
            session_id=session_id, run_id_filter=run_id_filter, events=events
        )
    return 0


def _extract_details(
    event_type: str, payload: dict[str, Any], ev: dict[str, Any] | None = None
) -> str:
    parts: list[str] = []
    ev = ev or {}

    status = str(payload.get("status", ev.get("status", "") or "") or "").strip()
    if status and status.lower() != "none":
        parts.append(f"status={status}")

    state = str(payload.get("state", "")).strip()
    if state and state != status:
        parts.append(f"state={state}")

    step = str(payload.get("step", "")).strip()
    if step:
        parts.append(f"step={step}")

    mode = str(payload.get("mode", "")).strip()
    if mode:
        parts.append(f"mode={mode}")

    mode_state = str(payload.get("mode_state", "")).strip()
    if mode_state:
        parts.append(f"mode_state={mode_state}")

    mode_label = str(payload.get("mode_label", "")).strip()
    if mode_label:
        parts.append(mode_label[:80])

    provider = str(payload.get("provider", "")).strip()
    if provider:
        parts.append(f"provider={provider}")

    purpose = str(payload.get("purpose", "")).strip()
    if purpose:
        parts.append(f"purpose={purpose}")

    tool_name = str(payload.get("tool_name", payload.get("tool", ""))).strip()
    if tool_name:
        parts.append(f"tool={tool_name}")

    title = str(payload.get("title", "")).strip()
    if title and not tool_name:
        parts.append(title)

    summary = str(payload.get("summary", "")).strip()
    if summary:
        parts.append(summary[:80])

    note = str(payload.get("note", "")).strip()
    if note and not summary:
        parts.append(note[:80])

    error = str(ev.get("error", payload.get("error", "") or "") or "").strip()
    if error and error.lower() != "none":
        parts.append(f"error={error[:80]}")

    return "  ".join(parts)


def _debug_trace(args: Any) -> int:
    trace_path = Path(str(getattr(args, "path", "") or "")).expanduser().resolve()
    if not trace_path.exists():
        print(f"Error: Trace file not found at {trace_path}", file=sys.stderr)
        return 1
    try:
        payload = json.loads(trace_path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"Error reading trace file: {exc}", file=sys.stderr)
        return 1

    if bool(getattr(args, "json", False)):
        print_json_payload(payload)
        return 0

    response = payload.get("response")
    if not isinstance(response, dict):
        print(f"Trace: {trace_path}")
        print("No structured response payload found.")
        return 0

    print(f"Trace: {trace_path}")
    print(f"Provider: {str(payload.get('provider', '') or '').strip()}")
    print(f"Model: {str(payload.get('model', '') or '').strip()}")
    print(f"Finish: {str(response.get('finish_reason', '') or '').strip()}")
    print(f"Tool calls: {len(list(response.get('tool_calls') or []))}")
    output_text = str(response.get("output_text", "") or "").strip()
    if output_text:
        print(f"Output: {output_text[:160]}")

    if bool(getattr(args, "include_thinking", False)):
        raw_blocks = response.get("thinking_blocks")
        if isinstance(raw_blocks, list) and raw_blocks:
            print("Thinking:")
            for index, item in enumerate(raw_blocks, start=1):
                if not isinstance(item, dict):
                    continue
                content = str(item.get("content", "") or "").strip()
                if content:
                    print(f"  [{index}] {content}")
        else:
            print("Thinking: none")
    return 0


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    debug = subparsers.add_parser("debug", help="Debug module diagnostics")
    debug_subcommands = debug.add_subparsers(dest="debug_command", required=True)

    debug_modules = debug_subcommands.add_parser(
        "modules", help="List all module debug info"
    )
    add_json_output_flag(debug_modules)
    debug_modules.set_defaults(handler=run_debug, needs_app=False)

    debug_module = debug_subcommands.add_parser(
        "module", help="Get debug info for a specific module"
    )
    debug_module.add_argument(
        "--name", required=True, dest="module_name", help="Module name"
    )
    add_json_output_flag(debug_module)
    debug_module.set_defaults(handler=run_debug, needs_app=False)

    debug_timeline = debug_subcommands.add_parser(
        "timeline", help="Show ordered event timeline for a session"
    )
    debug_timeline.add_argument(
        "--session", required=True, dest="session_id", help="Session ID"
    )
    debug_timeline.add_argument(
        "--run-id", default=None, dest="run_id", help="Filter to a specific run ID"
    )
    debug_timeline.add_argument(
        "--db",
        default=None,
        dest="db_path",
        help="Path to sessions.db (auto-resolved if omitted)",
    )
    debug_timeline.add_argument(
        "--limit", type=int, default=500, help="Max events to display"
    )
    add_json_output_flag(debug_timeline)
    debug_timeline.set_defaults(handler=run_debug, needs_app=False)

    debug_trace = debug_subcommands.add_parser(
        "trace", help="Render a structured trace file"
    )
    debug_trace.add_argument(
        "--path", required=True, help="Path to a *-structured.json trace file"
    )
    debug_trace.add_argument(
        "--include-thinking",
        action="store_true",
        dest="include_thinking",
        help="Render captured thinking blocks inline when present",
    )
    add_json_output_flag(debug_trace)
    debug_trace.set_defaults(handler=run_debug, needs_app=False)
