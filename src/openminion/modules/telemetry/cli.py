import argparse
import asyncio

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
from .service import TelemetryService


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


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    home_root = str(getattr(args, "home_root", "") or "").strip()
    data_root = str(getattr(args, "data_root", "") or "").strip()
    apply_home_data_root_env(home_root=home_root, data_root=data_root)

    db_path = resolve_module_cli_db_path(args, DEFAULT_INTEGRATED_SQLITE_SUBPATH)
    if args.command == "summary":
        return asyncio.run(
            _print_summary(
                db_path=db_path,
                session_id=str(getattr(args, "session_id", "") or "").strip(),
            )
        )
    if args.command != "storage":
        raise SystemExit("telemetryctl only supports storage and summary operations")
    return run_module_storage_command(
        args=args,
        module_id="telemetry",
        db_path=db_path,
        home_root=home_root or None,
        data_root=data_root or None,
    )


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
