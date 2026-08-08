import argparse
import asyncio
from pathlib import Path

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
    if args.command == "summary":
        return asyncio.run(
            _print_summary(
                db_path=db_path,
                session_id=str(getattr(args, "session_id", "") or "").strip(),
            )
        )
    if args.command != "storage":
        raise SystemExit(
            "telemetryctl only supports storage, summary, catalog, doctor, and trace operations"
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
