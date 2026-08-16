import argparse
import asyncio
from pathlib import Path
import time
from typing import Any
from uuid import uuid4

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
from .bundle import (
    BundleError,
    bundle_exit,
    create_debug_bundle,
    error_result,
)
from .config import load_config
from .events.catalog import TELEMETRY_EXPORT_PROBE
from .interfaces import TELEMETRY_EXPORT_PROBE_TIMEOUT_SECONDS
from .inspection import (
    TELEMETRY_INSPECTION_EXCEPTIONS,
    build_catalog_report,
    build_doctor_report,
    build_telemetry_debug_error,
    build_telemetry_debug_report,
    list_trace_files,
    open_telemetry_inspection,
    parse_invocation_id,
    read_trace_file,
    telemetry_debug_exit,
    telemetry_storage_error_code,
)
from .invocation_inspection import (
    build_invocation_snapshot,
    select_invocation_snapshots,
)
from .service import TelemetryService
from .retention import (
    build_retention_plan,
    parse_retention_selector,
    retention_error,
)
from .reports import (
    build_correlation_report,
    build_timing_report,
    correlation_error,
    parse_report_scope,
    timing_error,
)
from .schemas import (
    TelemetryDebugDiagnostic,
    TelemetryDebugError,
    TelemetryEvent,
    TelemetryExportSmokeReport,
)
from .trace.layout import resolve_trace_root


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return parsed


def _add_debug_and_inspection_commands(sub: Any) -> None:
    debug = sub.add_parser("debug", help="Print the shared telemetry debug report.")
    debug_sub = debug.add_subparsers(dest="debug_command", required=True)
    for command in ("latest", "failed"):
        route = debug_sub.add_parser(command, help=f"Show the {command} invocation.")
        route.add_argument(
            "--db", default=None, help="Explicit telemetry SQLite path override."
        )
    invocation_route = debug_sub.add_parser(
        "invocation", help="Show one invocation by opaque or UUID identifier."
    )
    invocation_route.add_argument("invocation_id")
    invocation_route.add_argument(
        "--db", default=None, help="Explicit telemetry SQLite path override."
    )
    bundle_route = debug_sub.add_parser(
        "bundle", help="Create a sanitized telemetry debug bundle."
    )
    bundle_route.add_argument("invocation_id")
    bundle_route.add_argument(
        "--output",
        default=None,
        help="New bundle directory, relative to the configured data root.",
    )
    bundle_route.add_argument(
        "--db", default=None, help="Explicit telemetry SQLite path override."
    )

    trace = sub.add_parser("trace", help="Inspect local LLM trace artifacts.")
    trace_sub = trace.add_subparsers(dest="trace_command", required=True)
    trace_list = trace_sub.add_parser("list", help="List recent trace artifacts.")
    trace_list.add_argument("--limit", type=_positive_int, default=20)
    trace_list.add_argument("--agent-id", default="")
    trace_show = trace_sub.add_parser("show", help="Print one trace artifact as JSON.")
    trace_show.add_argument("path", help="Trace path relative to the trace root.")
    trace_show.add_argument("--raw", action="store_true", help="Include trace content.")

    invocation = sub.add_parser(
        "invocation", help="Inspect durable invocation telemetry."
    )
    invocation_sub = invocation.add_subparsers(dest="invocation_command", required=True)
    invocation_list = invocation_sub.add_parser("list", help="List invocations.")
    invocation_list.add_argument("--limit", type=_positive_int, default=20)
    invocation_list.add_argument("--agent-id", default="")
    invocation_list.add_argument("--status", default="")
    invocation_list.add_argument("--event-type", default="")
    invocation_list.add_argument("--db", default=None)
    for command in ("show", "graph"):
        route = invocation_sub.add_parser(
            command, help=f"{command.title()} one invocation."
        )
        route.add_argument("invocation_id", help="Invocation UUID.")
        route.add_argument("--event-type", default="")
        route.add_argument("--db", default=None)


def _add_retention_and_report_commands(sub: Any) -> None:
    retention = sub.add_parser(
        "retention", help="Build advisory read-only retention plans."
    )
    retention_sub = retention.add_subparsers(dest="retention_command", required=True)
    plan = retention_sub.add_parser("plan")
    plan.add_argument("--older-than", default=None)
    plan.add_argument("--keep-last", default=None)
    plan.add_argument("--apply", action="store_true", help=argparse.SUPPRESS)
    plan.add_argument("--force", action="store_true", help=argparse.SUPPRESS)
    plan.add_argument("--plan-hash", default=None, help=argparse.SUPPRESS)
    plan.add_argument(
        "--db", default=None, help="Explicit telemetry SQLite path override."
    )
    report = sub.add_parser("report", help="Build structural telemetry reports.")
    report_sub = report.add_subparsers(dest="report_command", required=True)
    for report_name in ("correlation", "timing"):
        route = report_sub.add_parser(report_name)
        route.add_argument("--session-id", default=None)
        route.add_argument("--recent", default=None)
        route.add_argument("--limit", default=None)
        route.add_argument("--format", default=None, help=argparse.SUPPRESS)
        route.add_argument(
            "--db", default=None, help="Explicit telemetry SQLite path override."
        )


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
    sub.add_parser(
        "catalog",
        help="Print registered telemetry event types and OTel export dispositions.",
    )
    doctor = sub.add_parser(
        "doctor",
        help="Check telemetry database, trace root, and exporter configuration.",
    )
    doctor.add_argument(
        "--db",
        default=None,
        help="Explicit telemetry SQLite path override.",
    )
    doctor.add_argument(
        "--live-export",
        action="store_true",
        help="Record and directly flush one content-free OTLP probe.",
    )
    _add_debug_and_inspection_commands(sub)
    _add_retention_and_report_commands(sub)
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


def _event_row(event) -> dict[str, Any]:
    data = event.data
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


async def _print_invocation(*, args, db_path) -> int:
    command = str(args.invocation_command)
    if command != "list":
        return _print_invocation_detail(args=args, db_path=db_path)
    try:
        with open_telemetry_inspection(
            db_path=db_path,
            home_root=str(args.home_root or "").strip() or None,
        ) as service:
            if service is None:
                rows, legacy_count = [], 0
            else:
                rows, legacy_count = select_invocation_snapshots(
                    service,
                    limit=args.limit,
                    event_type=str(args.event_type or "").strip(),
                    agent_id=str(args.agent_id or "").strip(),
                    status=str(args.status or "").strip(),
                )
        payload = {
            "count": len(rows),
            "invocations": rows,
            "diagnostics": {"legacy_event_count": legacy_count},
        }
    except TELEMETRY_INSPECTION_EXCEPTIONS as exc:
        report = build_telemetry_debug_error(
            telemetry_storage_error_code(exc),
            "storage",
        )
        print_json_payload(report.to_dict())
        return telemetry_debug_exit(report)
    print_json_payload(payload)
    return 0


def _print_invocation_detail(*, args: Any, db_path: Path) -> int:
    try:
        token = parse_invocation_id(args.invocation_id)
    except ValueError:
        report = build_telemetry_debug_error("INVALID_ARGUMENT", "argument")
        print_json_payload(report.to_dict())
        return telemetry_debug_exit(report)
    home_root = str(args.home_root or "").strip() or None
    try:
        with open_telemetry_inspection(
            db_path=db_path,
            home_root=home_root,
        ) as service:
            report = build_telemetry_debug_report(
                service,
                selector_kind="invocation_id",
                invocation_id=token,
                trace_root=_trace_root_from_args(args),
            )
            if report.error is not None or service is None:
                print_json_payload(report.to_dict())
                return telemetry_debug_exit(report)
            invocation_id = str(report.selection.selected_invocation_id)
            payload, events = build_invocation_snapshot(service, invocation_id)
    except TELEMETRY_INSPECTION_EXCEPTIONS as exc:
        report = build_telemetry_debug_error(
            telemetry_storage_error_code(exc),
            "storage",
        )
        print_json_payload(report.to_dict())
        return telemetry_debug_exit(report)
    event_type = str(args.event_type or "").strip()
    filtered_events = (
        [event for event in events if event.event_type == event_type]
        if event_type
        else events
    )
    if args.invocation_command == "show":
        payload["events"] = [_event_row(event) for event in filtered_events]
    elif event_type:
        payload["event_filter"] = {
            "event_type": event_type,
            "matched_event_count": len(filtered_events),
        }
    print_json_payload(payload)
    return 0


def _trace_root_from_args(args) -> Path:
    home_root = str(args.home_root or "").strip()
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


def _normalized_export_protocol(value: str) -> str | None:
    normalized = str(value or "").strip().lower().replace("_", "-")
    if normalized in {"http", "http-protobuf", "http/protobuf"}:
        return "http/protobuf"
    if normalized == "grpc":
        return "grpc"
    return None


def _smoke_report(
    *,
    status: str,
    enabled: bool,
    endpoint_configured: bool,
    protocol: str | None,
    event_id: str | None = None,
    local_recording: str = "not_run",
    transport: str = "skipped",
    queue: str = "not_run",
    sampling: str = "not_run",
    flush: str = "not_run",
    cleanup: str = "not_run",
    diagnostic: str | None = None,
    error_code: str | None = None,
    error_category: str | None = None,
    recording_sink: bool = False,
) -> TelemetryExportSmokeReport:
    diagnostics = (
        [TelemetryDebugDiagnostic(diagnostic, "warning", {})] if diagnostic else []
    )
    return TelemetryExportSmokeReport(
        status=status,
        configuration={
            "enabled": enabled,
            "endpoint_configured": endpoint_configured,
            "protocol": protocol,
        },
        probe={
            "event_id": event_id,
            "local_recording": local_recording,
            "transport": transport,
            "queue": queue,
            "sampling": sampling,
            "flush": flush,
            "timeout_ms": int(TELEMETRY_EXPORT_PROBE_TIMEOUT_SECONDS * 1000),
            "cleanup": cleanup,
        },
        proof={
            "recording_sink": recording_sink,
            "otlp_transport": transport == "accepted" and flush == "completed",
            "collector_artifact": False,
            "vendor_visibility": False,
        },
        diagnostics=diagnostics,
        error=(
            TelemetryDebugError(error_code, str(error_category)) if error_code else None
        ),
    )


def _export_preflight(
    *, enabled: bool, endpoint_configured: bool, protocol: str | None
) -> TelemetryExportSmokeReport | None:
    if not enabled:
        diagnostic = "EXPORT_DISABLED"
    elif not endpoint_configured:
        diagnostic = "EXPORT_ENDPOINT_MISSING"
    elif protocol is None:
        diagnostic = "UNKNOWN_EXPORT_PROTOCOL"
    else:
        return None
    return _smoke_report(
        status="incomplete",
        enabled=enabled,
        endpoint_configured=endpoint_configured,
        protocol=protocol,
        diagnostic=diagnostic,
    )


def _export_probe_event(protocol: str) -> TelemetryEvent:
    return TelemetryEvent(
        session_id="telemetry-doctor",
        turn_id="live-export-probe",
        event_type=TELEMETRY_EXPORT_PROBE,
        timestamp=time.time(),
        event_id=str(uuid4()),
        data={
            "probe_kind": "live_export",
            "criticality": "diagnostic",
            "protocol": protocol,
        },
    )


def _print_live_export(*, db_path: Path, home_root: str | None) -> int:
    try:
        config = load_config(home_root=home_root).otel_exporter
    except (OSError, TypeError, ValueError):
        report = _smoke_report(
            status="error",
            enabled=False,
            endpoint_configured=False,
            protocol=None,
            error_code="EXPORT_CONFIGURATION_UNREADABLE",
            error_category="configuration",
        )
        print_json_payload(report.to_dict())
        return 3
    enabled = bool(config.enabled)
    endpoint_configured = bool(str(config.endpoint or "").strip())
    protocol = _normalized_export_protocol(config.protocol)
    if report := _export_preflight(
        enabled=enabled,
        endpoint_configured=endpoint_configured,
        protocol=protocol,
    ):
        print_json_payload(report.to_dict())
        return 1
    event = _export_probe_event(protocol)
    event_id = str(event.event_id)
    service: TelemetryService | None = None
    try:
        service = TelemetryService(
            db_path,
            home_root=home_root,
            otel_exporter_config=config,
        )
        result = service.record_and_probe_export(
            event,
            TELEMETRY_EXPORT_PROBE_TIMEOUT_SECONDS,
        )
        service.close_sync()
        service = None
    except (OSError, RuntimeError, TypeError, ValueError):
        if service is not None:
            try:
                service.close_sync()
            except (OSError, RuntimeError):
                pass
        report = _smoke_report(
            status="error",
            enabled=True,
            endpoint_configured=True,
            protocol=protocol,
            event_id=event_id,
            local_recording="failed",
            error_code="EXPORT_PROBE_RECORDING_FAILED",
            error_category="recording",
        )
        print_json_payload(report.to_dict())
        return 3
    if not result.created:
        report = _smoke_report(
            status="incomplete",
            enabled=True,
            endpoint_configured=True,
            protocol=protocol,
            event_id=event_id,
            local_recording="observed",
            diagnostic="EXPORT_PROBE_DUPLICATE",
        )
        print_json_payload(report.to_dict())
        return 1
    error_code = {
        ("timeout", "not_run"): "EXPORT_PROBE_TIMEOUT",
        ("failed", "not_run"): "EXPORT_PROBE_SEND_FAILED",
        ("rejected", "not_run"): "EXPORT_PROBE_SEND_FAILED",
        ("accepted", "failed"): "EXPORT_PROBE_FLUSH_FAILED",
    }.get((result.transport, result.flush))
    report = _smoke_report(
        status="ready" if error_code is None else "error",
        enabled=True,
        endpoint_configured=True,
        protocol=protocol,
        event_id=event_id,
        local_recording="observed",
        transport=result.transport,
        queue="bypassed",
        sampling="forced_probe",
        flush=result.flush,
        cleanup=result.cleanup,
        error_code=error_code,
        error_category="transport" if error_code else None,
        recording_sink=result.recording_sink,
    )
    print_json_payload(report.to_dict())
    return 0 if error_code is None else 3


def _print_debug(*, args: Any, db_path: Path, home_root: str | None) -> int:
    if args.debug_command == "bundle":
        return _print_debug_bundle(args=args, db_path=db_path, home_root=home_root)
    selector_kind = (
        "invocation_id"
        if args.debug_command == "invocation"
        else str(args.debug_command)
    )
    try:
        with open_telemetry_inspection(
            db_path=db_path,
            home_root=home_root,
        ) as service:
            report = build_telemetry_debug_report(
                service,
                selector_kind=selector_kind,
                invocation_id=getattr(args, "invocation_id", None),
                trace_root=_trace_root_from_args(args),
            )
    except TELEMETRY_INSPECTION_EXCEPTIONS as exc:
        report = build_telemetry_debug_error(
            telemetry_storage_error_code(exc),
            "storage",
        )
    print_json_payload(report.to_dict())
    return telemetry_debug_exit(report)


def _print_debug_bundle(*, args: Any, db_path: Path, home_root: str | None) -> int:
    data_root_value = str(args.data_root or "").strip()
    data_root = (
        Path(data_root_value).expanduser().resolve(strict=False)
        if data_root_value
        else db_path.parent.parent
    )
    try:
        with open_telemetry_inspection(
            db_path=db_path,
            home_root=home_root,
        ) as service:
            result = create_debug_bundle(
                service,
                invocation_id=args.invocation_id,
                data_root=data_root,
                trace_root=_trace_root_from_args(args),
                home_root=Path(home_root) if home_root else None,
                output=args.output,
            )
    except BundleError as exc:
        result = error_result(exc)
    except TELEMETRY_INSPECTION_EXCEPTIONS as exc:
        storage_code = telemetry_storage_error_code(exc)
        if storage_code != "TELEMETRY_STORAGE_UNAVAILABLE":
            storage_code = "TELEMETRY_STORAGE_FAILURE"
        result = error_result(BundleError(storage_code, "storage"))
    print_json_payload(result.to_dict())
    return bundle_exit(result)


def _print_trace_list(args) -> int:
    print_json_payload(
        list_trace_files(
            trace_root=_trace_root_from_args(args),
            limit=args.limit,
            agent_id=args.agent_id,
        )
    )
    return 0


def _print_trace_show(args) -> int:
    print_json_payload(
        read_trace_file(
            trace_root=_trace_root_from_args(args),
            trace_path=args.path,
            include_content=bool(args.raw),
        )
    )
    return 0


def _print_retention_plan(*, args: Any, db_path: Path, home_root: str | None) -> int:
    if args.apply or args.force or args.plan_hash is not None:
        plan = retention_error("INVALID_ARGUMENT", "argument")
        print_json_payload(plan.to_dict())
        return 2
    try:
        selector = parse_retention_selector(
            older_than=args.older_than,
            keep_last=args.keep_last,
        )
    except (TypeError, ValueError):
        plan = retention_error("INVALID_ARGUMENT", "argument")
        print_json_payload(plan.to_dict())
        return 2
    try:
        with open_telemetry_inspection(
            db_path=db_path,
            home_root=home_root,
        ) as service:
            plan = build_retention_plan(service, selector=selector)
    except TELEMETRY_INSPECTION_EXCEPTIONS as exc:
        code = telemetry_storage_error_code(exc)
        mapped = {
            "TELEMETRY_STORAGE_UNAVAILABLE": "RETENTION_STORAGE_UNAVAILABLE",
            "TELEMETRY_STORAGE_CORRUPT": "RETENTION_STORAGE_CORRUPT",
            "TELEMETRY_SCHEMA_INCOMPATIBLE": "RETENTION_SCHEMA_INCOMPATIBLE",
        }.get(code, "RETENTION_INTERNAL_FAILURE")
        category = "storage" if mapped != "RETENTION_INTERNAL_FAILURE" else "internal"
        plan = retention_error(mapped, category, selector=selector)
    print_json_payload(plan.to_dict())
    return 0 if plan.error is None else 3


def _print_structural_report(*, args: Any, db_path: Path, home_root: str | None) -> int:
    builder = (
        build_correlation_report
        if args.report_command == "correlation"
        else build_timing_report
    )
    error_builder = (
        correlation_error if args.report_command == "correlation" else timing_error
    )
    if args.format is not None:
        report = error_builder()
        print_json_payload(report.to_dict())
        return 2
    try:
        scope = parse_report_scope(
            session_id=args.session_id,
            recent=args.recent,
            limit=args.limit,
        )
    except (TypeError, ValueError):
        report = error_builder()
        print_json_payload(report.to_dict())
        return 2
    try:
        with open_telemetry_inspection(
            db_path=db_path,
            home_root=home_root,
        ) as service:
            report = builder(service, scope=scope)
    except TELEMETRY_INSPECTION_EXCEPTIONS:
        report = error_builder(
            scope,
            code="TELEMETRY_STORAGE_FAILURE",
            category="storage",
        )
    print_json_payload(report.to_dict())
    return 0 if report.error is None else 3


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    home_root = str(args.home_root or "").strip()
    data_root = str(args.data_root or "").strip()
    apply_home_data_root_env(home_root=home_root, data_root=data_root)

    if args.command == "catalog":
        return _print_catalog()

    db_path = resolve_module_cli_db_path(args, DEFAULT_INTEGRATED_SQLITE_SUBPATH)
    if args.command == "doctor":
        if bool(args.live_export):
            return _print_live_export(db_path=db_path, home_root=home_root or None)
        return _print_doctor(db_path=db_path, home_root=home_root or None)
    if args.command == "debug":
        return _print_debug(
            args=args,
            db_path=db_path,
            home_root=home_root or None,
        )
    if args.command == "trace":
        trace_command = args.trace_command
        if trace_command == "list":
            return _print_trace_list(args)
        if trace_command == "show":
            return _print_trace_show(args)
        raise SystemExit("telemetryctl trace requires list or show")
    if args.command == "retention":
        return _print_retention_plan(
            args=args,
            db_path=db_path,
            home_root=home_root or None,
        )
    if args.command == "report":
        return _print_structural_report(
            args=args,
            db_path=db_path,
            home_root=home_root or None,
        )
    if args.command == "invocation":
        return asyncio.run(_print_invocation(args=args, db_path=db_path))
    if args.command == "summary":
        return asyncio.run(
            _print_summary(
                db_path=db_path,
                session_id=args.session_id.strip(),
            )
        )
    if args.command != "storage":
        raise SystemExit(
            "telemetryctl only supports storage, summary, catalog, doctor, debug, trace, invocation, retention, and report operations"
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
