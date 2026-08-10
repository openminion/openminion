import argparse
import asyncio
import uuid
from pathlib import Path
from typing import Any

from openminion.modules.cli_common import (
    add_common_module_root_args,
    apply_home_data_root_env,
    print_json_payload,
    resolve_module_cli_db_path,
)
from openminion.modules.storage.module_cli import (
    add_storage_subcommands,
    run_module_storage_command,
)
from .constants import DEFAULT_INTEGRATED_SQLITE_SUBPATH
from .inspection import (
    build_catalog_report,
    build_doctor_report,
    list_trace_files,
    read_trace_file,
)
from .service import TelemetryService
from .trace.layout import resolve_trace_root


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="telemetryctl",
        description="openminion-telemetry standalone CLI",
    )
    add_common_module_root_args(parser)
    sub = parser.add_subparsers(dest="command", required=True)
    add_storage_subcommands(sub)
    summary = sub.add_parser(
        "summary",
        help="Print per-module operation and counter aggregates for a session.",
    )
    summary.add_argument("session_id", help="Session ID to summarize.")
    summary.add_argument(
        "--db",
        default=None,
        help="Explicit telemetry SQLite path override.",
    )
    catalog = sub.add_parser(
        "catalog",
        help="Print registered telemetry event types and OTel export dispositions.",
    )
    catalog.set_defaults(command="catalog")
    doctor = sub.add_parser(
        "doctor",
        help="Check telemetry database, trace root, and exporter configuration.",
    )
    doctor.add_argument(
        "--db",
        default=None,
        help="Explicit telemetry SQLite path override.",
    )
    trace = sub.add_parser(
        "trace",
        help="Inspect local LLM trace artifacts under the telemetry trace root.",
    )
    trace_sub = trace.add_subparsers(dest="trace_command", required=True)
    trace_list = trace_sub.add_parser("list", help="List recent trace artifacts.")
    trace_list.add_argument(
        "--limit", type=int, default=20, help="Maximum files to list."
    )
    trace_list.add_argument(
        "--agent-id",
        default="",
        help="Optional agent-id folder filter under traces/llm/.",
    )
    trace_show = trace_sub.add_parser("show", help="Print one trace artifact as JSON.")
    trace_show.add_argument("path", help="Trace path relative to the trace root.")
    invocation = sub.add_parser(
        "invocation",
        help="Inspect durable invocation telemetry.",
    )
    invocation_sub = invocation.add_subparsers(
        dest="invocation_command",
        required=True,
    )
    invocation_list = invocation_sub.add_parser("list", help="List invocations.")
    invocation_list.add_argument("--limit", type=int, default=20)
    invocation_list.add_argument("--agent-id", default="")
    invocation_list.add_argument("--status", default="")
    invocation_list.add_argument("--event-type", default="")
    invocation_list.add_argument("--db", default=None)
    for command in ("show", "graph"):
        invocation_show = invocation_sub.add_parser(
            command,
            help=f"{command.title()} one invocation.",
        )
        invocation_show.add_argument("invocation_id")
        invocation_show.add_argument("--event-type", default="")
        invocation_show.add_argument("--db", default=None)
    return parser


def _normalize_summary_output(
    summary: dict[str, dict[str, object]],
) -> dict[str, dict[str, object]]:
    normalized: dict[str, dict[str, object]] = {}
    for module_id in sorted(summary):
        stats = dict(summary[module_id] or {})
        operation_counts = stats.get("operation_counts")
        if isinstance(operation_counts, dict):
            stats["operation_counts"] = {
                key: operation_counts[key] for key in sorted(operation_counts)
            }
        counter_sums = stats.get("custom_counter_sums")
        if isinstance(counter_sums, dict):
            stats["custom_counter_sums"] = {
                key: counter_sums[key] for key in sorted(counter_sums)
            }
        normalized[module_id] = stats
    return normalized


async def _print_summary(*, db_path, session_id: str) -> int:
    service = TelemetryService(db_path)
    try:
        payload = _normalize_summary_output(
            await service.get_module_summary(session_id)
        )
    finally:
        await service.close()
    print_json_payload(payload)
    return 0


def _safe_invocation_id(value: str) -> str:
    normalized = str(value or "").strip()
    uuid.UUID(normalized)
    return normalized


def _event_row(event) -> dict[str, Any]:
    data = event.data if isinstance(event.data, dict) else {}
    error = data.get("error")
    error_type = (
        str(error.get("type") or error.get("code") or "")
        if isinstance(error, dict)
        else ""
    )
    return {
        "agent_id": event.agent_id or "",
        "event_id": event.event_id,
        "event_type": event.event_type,
        "execution_id": event.execution_id or "",
        "session_id": event.session_id,
        "status": str(data.get("status") or ""),
        "timestamp": event.timestamp,
        "turn_id": event.turn_id,
        "error_type": error_type,
    }


def _invocation_summary(invocation_id: str, events: list) -> dict[str, Any]:
    input_tokens = 0
    output_tokens = 0
    cache_read_tokens = 0
    cache_write_tokens = 0
    cost_usd = 0.0
    duration_ms = 0.0
    policy_decisions: dict[str, int] = {}
    executions: dict[str, dict[str, Any]] = {}
    propagation = {"valid": 0, "invalid": 0, "unavailable": 0}
    log_events: list[str] = []
    for event in events:
        data = event.data if isinstance(event.data, dict) else {}
        usage = data.get("usage")
        if isinstance(usage, dict):
            input_tokens += int(
                usage.get("input_tokens") or usage.get("prompt_tokens") or 0
            )
            output_tokens += int(
                usage.get("output_tokens") or usage.get("completion_tokens") or 0
            )
            cache_read_tokens += int(
                usage.get("cached_tokens") or usage.get("cache_read_tokens") or 0
            )
            cache_write_tokens += int(usage.get("cache_creation_tokens") or 0)
        if data.get("cost_source") and isinstance(data.get("cost_usd"), (int, float)):
            cost_usd += float(data["cost_usd"])
        if isinstance(data.get("duration_ms"), (int, float)):
            duration_ms += float(data["duration_ms"])
        if event.event_type == "policy.decision":
            decision = str(data.get("decision") or "unknown")
            policy_decisions[decision] = policy_decisions.get(decision, 0) + 1
        if event.event_type.startswith(
            ("policy.", "safety.", "agent.handoff.", "tool.execution.failed")
        ):
            log_events.append(event.event_type)
        propagation_status = str(data.get("trace_context_status") or "")
        if propagation_status in propagation:
            propagation[propagation_status] += 1
        execution_id = event.execution_id or ""
        if execution_id:
            segment = executions.setdefault(
                execution_id,
                {
                    "execution_id": execution_id,
                    "agent_id": event.agent_id or "",
                    "event_count": 0,
                    "started_at": None,
                    "ended_at": None,
                    "status": "",
                },
            )
            segment["event_count"] += 1
            segment["started_at"] = (
                event.timestamp
                if segment["started_at"] is None
                else min(segment["started_at"], event.timestamp)
            )
            segment["ended_at"] = (
                event.timestamp
                if segment["ended_at"] is None
                else max(segment["ended_at"], event.timestamp)
            )
            if data.get("status"):
                segment["status"] = str(data["status"])
    return {
        "invocation_id": invocation_id,
        "event_count": len(events),
        "segments": [executions[key] for key in sorted(executions)],
        "summary": {
            "cache_read_tokens": cache_read_tokens,
            "cache_write_tokens": cache_write_tokens,
            "cost_usd": round(cost_usd, 12),
            "duration_ms": duration_ms,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "policy_decisions": {
                key: policy_decisions[key] for key in sorted(policy_decisions)
            },
        },
        "correlated_log_events": sorted(log_events),
        "diagnostics": {
            "legacy_identity_gap": False,
            "orphan_terminal_events": sum(
                1
                for event in events
                if event.event_type.endswith(
                    (".completed", ".failed", ".cancelled", ".paused")
                )
                and not event.execution_id
            ),
            "propagation": propagation,
        },
    }


async def _print_invocation(*, args, db_path) -> int:
    service = TelemetryService(db_path)
    try:
        command = str(args.invocation_command)
        if command == "list":
            events = await service.get_events()
            event_type = str(args.event_type or "").strip()
            agent_id = str(args.agent_id or "").strip()
            status = str(args.status or "").strip()
            grouped: dict[str, list] = {}
            legacy_count = 0
            for event in events:
                if event.invocation_id is None:
                    legacy_count += 1
                    continue
                if event_type and event.event_type != event_type:
                    continue
                if agent_id and event.agent_id != agent_id:
                    continue
                if status and str((event.data or {}).get("status") or "") != status:
                    continue
                grouped.setdefault(event.invocation_id, []).append(event)
            rows = [
                _invocation_summary(invocation_id, grouped[invocation_id])
                for invocation_id in sorted(
                    grouped,
                    key=lambda key: max(event.timestamp for event in grouped[key]),
                    reverse=True,
                )[: max(0, int(args.limit))]
            ]
            payload = {
                "count": len(rows),
                "invocations": rows,
                "diagnostics": {"legacy_event_count": legacy_count},
            }
        else:
            invocation_id = _safe_invocation_id(args.invocation_id)
            events = await service.get_invocation_events(invocation_id)
            event_type = str(args.event_type or "").strip()
            if event_type:
                events = [event for event in events if event.event_type == event_type]
            payload = _invocation_summary(invocation_id, events)
            if command == "show":
                payload["events"] = [_event_row(event) for event in events]
    finally:
        await service.close()
    print_json_payload(payload)
    return 0


def _trace_root_from_args(args) -> Path:
    home_root = str(getattr(args, "home_root", "") or "").strip()
    return resolve_trace_root(home_root=Path(home_root) if home_root else None)


def _print_catalog() -> int:
    print_json_payload(build_catalog_report())
    return 0


def _print_doctor(*, db_path, home_root: str | None) -> int:
    print_json_payload(
        build_doctor_report(
            db_path=db_path,
            home_root=home_root or None,
        )
    )
    return 0


def _print_trace_list(args) -> int:
    print_json_payload(
        list_trace_files(
            trace_root=_trace_root_from_args(args),
            limit=int(getattr(args, "limit", 20) or 20),
            agent_id=str(getattr(args, "agent_id", "") or ""),
        )
    )
    return 0


def _print_trace_show(args) -> int:
    print_json_payload(
        read_trace_file(
            trace_root=_trace_root_from_args(args),
            trace_path=str(getattr(args, "path", "") or ""),
        )
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    home_root = str(getattr(args, "home_root", "") or "").strip()
    data_root = str(getattr(args, "data_root", "") or "").strip()
    apply_home_data_root_env(home_root=home_root, data_root=data_root)

    if args.command == "catalog":
        return _print_catalog()

    db_path = resolve_module_cli_db_path(args, DEFAULT_INTEGRATED_SQLITE_SUBPATH)
    if args.command == "doctor":
        return _print_doctor(db_path=db_path, home_root=home_root or None)
    if args.command == "trace":
        trace_command = str(getattr(args, "trace_command", "") or "").strip()
        if trace_command == "list":
            return _print_trace_list(args)
        if trace_command == "show":
            return _print_trace_show(args)
        raise SystemExit("telemetryctl trace requires list or show")
    if args.command == "invocation":
        return asyncio.run(_print_invocation(args=args, db_path=db_path))
    if args.command == "summary":
        return asyncio.run(
            _print_summary(
                db_path=db_path,
                session_id=str(getattr(args, "session_id", "") or "").strip(),
            )
        )
    if args.command != "storage":
        raise SystemExit(
            "telemetryctl only supports storage, summary, catalog, doctor, trace, and invocation operations"
        )
    return run_module_storage_command(
        args=args,
        module_id="telemetry",
        db_path=db_path,
        home_root=home_root or None,
        data_root=data_root or None,
    )


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
