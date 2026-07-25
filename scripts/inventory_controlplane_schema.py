#!/usr/bin/env python3
"""Inspect controlplane SQLite schema, row counts, and migration metadata."""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from typing import Any


def _connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def _table_names(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        """
        SELECT name FROM sqlite_master
        WHERE type='table' AND name NOT LIKE 'sqlite_%'
        ORDER BY name
        """
    ).fetchall()
    return [str(row["name"]) for row in rows]


def _row_count(conn: sqlite3.Connection, table: str) -> int:
    escaped = table.replace('"', '""')
    row = conn.execute(f'SELECT COUNT(*) AS count FROM "{escaped}"').fetchone()
    return int(row["count"] if row else 0)


def _query_dicts(conn: sqlite3.Connection, sql: str) -> list[dict[str, Any]]:
    try:
        return [dict(row) for row in conn.execute(sql).fetchall()]
    except sqlite3.Error:
        return []


def inspect_db(path: Path) -> dict[str, Any]:
    resolved = path.expanduser().resolve(strict=False)
    if not resolved.exists():
        raise FileNotFoundError(f"database does not exist: {resolved}")
    with _connect(resolved) as conn:
        tables = _table_names(conn)
        return {
            "path": str(resolved),
            "tables": {table: _row_count(conn, table) for table in tables},
            "cp_migrations": _query_dicts(
                conn,
                "SELECT version, name, applied_at FROM cp_migrations ORDER BY version",
            ),
            "om_meta": _query_dicts(
                conn, "SELECT key, value FROM om_meta ORDER BY key"
            ),
            "indexes": _query_dicts(
                conn,
                """
                SELECT name, tbl_name
                FROM sqlite_master
                WHERE type='index' AND name NOT LIKE 'sqlite_%'
                ORDER BY tbl_name, name
                """,
            ),
        }


def _print_text(report: dict[str, Any]) -> None:
    for label, payload in report.items():
        print(f"{label}: {payload['path']}")
        print("  tables:")
        for table, count in payload["tables"].items():
            print(f"    {table}: {count}")
        if payload["cp_migrations"]:
            print("  cp_migrations:")
            for row in payload["cp_migrations"]:
                print(f"    {row['version']}: {row['name']} ({row['applied_at']})")
        if payload["om_meta"]:
            print("  om_meta:")
            for row in payload["om_meta"]:
                print(f"    {row['key']}: {row['value']}")
        print("  indexes:")
        for row in payload["indexes"]:
            print(f"    {row['tbl_name']}: {row['name']}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, help="Path to controlplane cp.db")
    parser.add_argument(
        "--telegram-db", type=Path, help="Path to telegram-poll-state.db"
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON")
    args = parser.parse_args()

    if args.db is None and args.telegram_db is None:
        parser.error("provide --db and/or --telegram-db")

    report: dict[str, Any] = {}
    if args.db is not None:
        report["controlplane"] = inspect_db(args.db)
    if args.telegram_db is not None:
        report["telegram"] = inspect_db(args.telegram_db)

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        _print_text(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
