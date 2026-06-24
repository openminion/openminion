from __future__ import annotations

import argparse
import logging

from openminion.cli.config import resolve_cli_roots
from openminion.cli.parser.flags import add_json_output_flag
from openminion.cli.presentation.json_output import print_json_payload
from openminion.services.bootstrap.migration import migrate_data_root


def run_data(args) -> int:
    if args.data_command != "migrate":
        raise RuntimeError("Unknown data command")

    roots = resolve_cli_roots(
        config_path=getattr(args, "config", None),
        home_root=str(getattr(args, "home_root", "") or "").strip() or None,
        data_root=str(getattr(args, "data_root", "") or "").strip() or None,
    )
    report = migrate_data_root(
        home_root=roots.home_root,
        data_root=roots.data_root,
        dry_run=bool(args.dry_run),
        logger=logging.getLogger("openminion.data_migration"),
    )
    _print_report(report.to_dict(), as_json=bool(getattr(args, "json", False)))
    return 0


def _print_report(payload: dict, *, as_json: bool) -> None:
    if as_json:
        print_json_payload(payload)
        return
    items = payload.get("items", [])
    print(
        "data migrate report: "
        f"started_at={payload.get('started_at')} "
        f"finished_at={payload.get('finished_at')} "
        f"dry_run={payload.get('dry_run')}"
    )
    if isinstance(items, list):
        for item in items:
            if isinstance(item, dict):
                print(
                    f"- {item.get('status')}: {item.get('source')} -> {item.get('target')}"
                    + (f" ({item.get('detail')})" if item.get("detail") else "")
                )


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    data = subparsers.add_parser("data", help="Data root operations")
    data_subcommands = data.add_subparsers(dest="data_command")
    data_migrate = data_subcommands.add_parser(
        "migrate", help="Migrate legacy paths into data_root"
    )
    data_migrate.add_argument(
        "--dry-run",
        action="store_true",
        help="Print planned moves without changing files",
    )
    add_json_output_flag(data_migrate)
    data_migrate.set_defaults(handler=run_data, needs_app=False)
