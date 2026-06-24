#!/usr/bin/env python3
"""Detect storage schema drift against the declared migration ledger."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import tempfile
from pathlib import Path

# Ensure the repo's openminion package is importable when this script is
# invoked from anywhere (CI runners, local make targets).
REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from openminion.modules.storage.runtime.migrations import (  # noqa: E402
    migrate_database,
)
from openminion.modules.storage.runtime.schema_drift import (  # noqa: E402
    SchemaDriftReport,
    derive_expected_schema,
    detect_schema_drift,
)


EXIT_OK = 0
EXIT_DRIFT = 1
EXIT_USAGE = 2


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Detect storage schema drift against the canonical migration ledger. "
            "Exit 0 on match, 1 on drift, 2 on usage error."
        )
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--db",
        type=Path,
        help="Path to a SQLite database file to check against the declared schema.",
    )
    group.add_argument(
        "--self-check",
        action="store_true",
        help=(
            "Run the gate against a freshly-migrated temp database. "
            "Useful as a CI smoke ensuring the migration ledger replays cleanly "
            "and matches its own derived schema."
        ),
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress the JSON report; only the exit code communicates the result.",
    )
    return parser.parse_args(argv)


def _emit_report(report: SchemaDriftReport, *, quiet: bool) -> None:
    if quiet:
        return
    payload = report.as_dict()
    json.dump(payload, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")


def _resolve_exit_code(report: SchemaDriftReport) -> int:
    # Deterministic mapping only. No interpretation.
    return EXIT_DRIFT if report.has_drift else EXIT_OK


def run(argv: list[str] | None = None) -> int:
    try:
        args = _parse_args(argv)
    except SystemExit as exc:
        # argparse exits with code 2 on bad usage; preserve that contract.
        return int(exc.code) if isinstance(exc.code, int) else EXIT_USAGE

    expected = derive_expected_schema()

    if args.self_check:
        with tempfile.TemporaryDirectory(prefix="omx-schema-drift-") as tmp:
            db_path = Path(tmp) / "self_check.db"
            migrate_database(db_path)
            report = detect_schema_drift(expected, db_path)
            _emit_report(report, quiet=args.quiet)
            return _resolve_exit_code(report)

    db_path = Path(args.db)
    if not db_path.exists():
        sys.stderr.write(
            f"check_schema_drift: database path does not exist: {db_path}\n"
        )
        return EXIT_USAGE

    connection = sqlite3.connect(str(db_path))
    try:
        report = detect_schema_drift(expected, connection)
    finally:
        connection.close()

    _emit_report(report, quiet=args.quiet)
    return _resolve_exit_code(report)


if __name__ == "__main__":
    raise SystemExit(run())
