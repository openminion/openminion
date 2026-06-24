from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Mapping, Sequence

from .migrations import (
    DEFAULT_MIGRATIONS,
    Migration,
    _normalize_migrations,
)


class SchemaDriftKind(str, Enum):
    """Closed set of drift kinds emitted by ``detect_schema_drift``.

    Anti-LLM §1: this enum is the only vocabulary the report uses. New kinds
    require an explicit code change here; no free-form classification.
    """

    MATCH = "match"
    MISSING_TABLE = "missing_table"
    EXTRA_TABLE = "extra_table"
    MISSING_COLUMN = "missing_column"
    EXTRA_COLUMN = "extra_column"
    COLUMN_TYPE_MISMATCH = "column_type_mismatch"
    COLUMN_NULLABILITY_MISMATCH = "column_nullability_mismatch"
    COLUMN_PRIMARY_KEY_MISMATCH = "column_primary_key_mismatch"
    MIGRATION_LEDGER_BEHIND = "migration_ledger_behind"


@dataclass(frozen=True)
class ExpectedColumn:
    """Declared (expected) column shape derived from a migration DDL."""

    name: str
    type_norm: str
    notnull: bool
    primary_key: bool


@dataclass(frozen=True)
class ExpectedTable:
    """Declared (expected) table shape derived from a migration DDL."""

    name: str
    columns: tuple[ExpectedColumn, ...]


@dataclass(frozen=True)
class ExpectedSchema:
    """Snapshot of the schema declared by the canonical migration ledger."""

    tables: tuple[ExpectedTable, ...]
    head_version: int

    def table_by_name(self, name: str) -> ExpectedTable | None:
        for table in self.tables:
            if table.name == name:
                return table
        return None


@dataclass(frozen=True)
class SchemaDriftFinding:
    """One structural difference between declared and observed schema."""

    kind: SchemaDriftKind
    table: str
    column: str = ""
    expected: str = ""
    observed: str = ""

    def as_dict(self) -> dict[str, str]:
        return {
            "kind": self.kind.value,
            "table": self.table,
            "column": self.column,
            "expected": self.expected,
            "observed": self.observed,
        }


@dataclass(frozen=True)
class SchemaDriftReport:
    """Typed result of a structural schema-drift comparison.

    ``findings`` lists every drift entry; ``has_drift`` is True iff at least
    one finding has a kind other than ``MATCH``. The CI gate exits non-zero
    on ``has_drift``; the runtime startup hook emits a warning on
    ``has_drift``.
    """

    expected_head_version: int
    observed_head_version: int
    findings: tuple[SchemaDriftFinding, ...] = field(default_factory=tuple)

    @property
    def has_drift(self) -> bool:
        return any(f.kind is not SchemaDriftKind.MATCH for f in self.findings)

    def findings_by_kind(self, kind: SchemaDriftKind) -> tuple[SchemaDriftFinding, ...]:
        return tuple(f for f in self.findings if f.kind is kind)

    def as_dict(self) -> dict[str, object]:
        return {
            "expected_head_version": self.expected_head_version,
            "observed_head_version": self.observed_head_version,
            "has_drift": self.has_drift,
            "findings": [f.as_dict() for f in self.findings],
        }


_LEDGER_TABLE_NAME = "migrations"


RUNTIME_ONLY_TABLES: tuple[str, ...] = (
    "sidecar_ingest_log",
    "core_events",
    "core_sidecar_rows",
)


def _normalize_type(raw: str) -> str:
    """Normalize a SQLite type string for comparison.

    SQLite stores declared types verbatim. We strip whitespace and uppercase
    to keep the comparison case-insensitive without parsing the type
    grammar. Foreign-key/CHECK clauses do not appear in the type column.
    """

    return " ".join(str(raw or "").split()).upper()


def derive_expected_schema(
    migrations: Sequence[Migration] = DEFAULT_MIGRATIONS,
) -> ExpectedSchema:
    """Build an ``ExpectedSchema`` by replaying ``migrations`` in memory.

    Pure: opens an in-memory SQLite connection, applies every migration's
    DDL statements (no ledger insert), and reads back the resulting
    schema via ``PRAGMA table_info``. The migrations ledger is filtered
    out of the output because its DDL is internal bookkeeping.
    """

    ordered = _normalize_migrations(list(migrations))
    head_version = max((m.version for m in ordered), default=0)

    connection = sqlite3.connect(":memory:")
    try:
        connection.execute("PRAGMA foreign_keys = OFF")
        for migration in ordered:
            for statement in migration.statements:
                connection.execute(statement)
        connection.commit()
        tables = _read_sqlite_schema(connection)
    finally:
        connection.close()

    filtered = tuple(t for t in tables if t.name != _LEDGER_TABLE_NAME)
    return ExpectedSchema(tables=filtered, head_version=head_version)


def _read_sqlite_schema(connection: sqlite3.Connection) -> tuple[ExpectedTable, ...]:
    rows = connection.execute(
        "SELECT name FROM sqlite_master "
        "WHERE type = 'table' AND name NOT LIKE 'sqlite_%' "
        "ORDER BY name"
    ).fetchall()
    tables: list[ExpectedTable] = []
    for row in rows:
        table_name = row[0] if not isinstance(row, sqlite3.Row) else row["name"]
        column_rows = connection.execute(f"PRAGMA table_info({table_name})").fetchall()
        columns: list[ExpectedColumn] = []
        for column_row in column_rows:
            # PRAGMA table_info: (cid, name, type, notnull, dflt_value, pk)
            cid = column_row[0]
            del cid
            name = column_row[1]
            type_raw = column_row[2]
            notnull = bool(column_row[3])
            primary_key = bool(column_row[5])
            columns.append(
                ExpectedColumn(
                    name=str(name),
                    type_norm=_normalize_type(type_raw),
                    notnull=notnull,
                    primary_key=primary_key,
                )
            )
        columns.sort(key=lambda c: c.name)
        tables.append(ExpectedTable(name=str(table_name), columns=tuple(columns)))
    tables.sort(key=lambda t: t.name)
    return tuple(tables)


def _read_live_tables(
    connection: sqlite3.Connection,
) -> tuple[ExpectedTable, ...]:
    """Read the live database schema into the same shape as ``ExpectedSchema``.

    Identical extraction to ``_read_sqlite_schema`` so the diff is symmetric.
    """

    return _read_sqlite_schema(connection)


def _read_ledger_head(connection: sqlite3.Connection) -> int:
    """Return the highest applied migration version, or 0 if none."""

    try:
        row = connection.execute(
            "SELECT COALESCE(MAX(version), 0) FROM migrations"
        ).fetchone()
    except sqlite3.Error:
        return 0
    if row is None:
        return 0
    value = row[0] if not isinstance(row, sqlite3.Row) else row[0]
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def detect_schema_drift(
    expected_schema: ExpectedSchema,
    live_db: sqlite3.Connection | str | Path,
    *,
    ignore_extra_tables: Sequence[str] = (),
) -> SchemaDriftReport:
    """Detect schema drift helper."""

    owned_connection: sqlite3.Connection | None = None
    if isinstance(live_db, sqlite3.Connection):
        connection = live_db
    else:
        owned_connection = sqlite3.connect(str(live_db))
        connection = owned_connection

    try:
        observed_tables = _read_live_tables(connection)
        observed_head = _read_ledger_head(connection)
    finally:
        if owned_connection is not None:
            owned_connection.close()

    ignored = frozenset(ignore_extra_tables)
    if ignored:
        observed_tables = tuple(t for t in observed_tables if t.name not in ignored)

    findings = list(
        _diff_tables(
            expected_tables=expected_schema.tables,
            observed_tables=observed_tables,
        )
    )

    if expected_schema.head_version > observed_head:
        findings.append(
            SchemaDriftFinding(
                kind=SchemaDriftKind.MIGRATION_LEDGER_BEHIND,
                table=_LEDGER_TABLE_NAME,
                expected=str(expected_schema.head_version),
                observed=str(observed_head),
            )
        )

    if not findings:
        findings.append(
            SchemaDriftFinding(
                kind=SchemaDriftKind.MATCH,
                table="",
            )
        )

    return SchemaDriftReport(
        expected_head_version=expected_schema.head_version,
        observed_head_version=observed_head,
        findings=tuple(findings),
    )


def _diff_tables(
    *,
    expected_tables: Sequence[ExpectedTable],
    observed_tables: Sequence[ExpectedTable],
) -> list[SchemaDriftFinding]:
    expected_by_name: Mapping[str, ExpectedTable] = {t.name: t for t in expected_tables}
    observed_by_name: Mapping[str, ExpectedTable] = {
        t.name: t for t in observed_tables if t.name != _LEDGER_TABLE_NAME
    }

    findings: list[SchemaDriftFinding] = []

    for name in sorted(expected_by_name.keys() | observed_by_name.keys()):
        expected_table = expected_by_name.get(name)
        observed_table = observed_by_name.get(name)
        if expected_table is None and observed_table is not None:
            findings.append(
                SchemaDriftFinding(
                    kind=SchemaDriftKind.EXTRA_TABLE,
                    table=name,
                )
            )
            continue
        if expected_table is not None and observed_table is None:
            findings.append(
                SchemaDriftFinding(
                    kind=SchemaDriftKind.MISSING_TABLE,
                    table=name,
                )
            )
            continue
        # both present
        assert expected_table is not None and observed_table is not None
        findings.extend(_diff_columns(expected_table, observed_table))
    return findings


def _diff_columns(
    expected_table: ExpectedTable,
    observed_table: ExpectedTable,
) -> list[SchemaDriftFinding]:
    expected_by_name: Mapping[str, ExpectedColumn] = {
        c.name: c for c in expected_table.columns
    }
    observed_by_name: Mapping[str, ExpectedColumn] = {
        c.name: c for c in observed_table.columns
    }
    findings: list[SchemaDriftFinding] = []
    for name in sorted(expected_by_name.keys() | observed_by_name.keys()):
        expected_col = expected_by_name.get(name)
        observed_col = observed_by_name.get(name)
        if expected_col is None and observed_col is not None:
            findings.append(
                SchemaDriftFinding(
                    kind=SchemaDriftKind.EXTRA_COLUMN,
                    table=expected_table.name,
                    column=name,
                    observed=observed_col.type_norm,
                )
            )
            continue
        if expected_col is not None and observed_col is None:
            findings.append(
                SchemaDriftFinding(
                    kind=SchemaDriftKind.MISSING_COLUMN,
                    table=expected_table.name,
                    column=name,
                    expected=expected_col.type_norm,
                )
            )
            continue
        assert expected_col is not None and observed_col is not None
        if expected_col.type_norm != observed_col.type_norm:
            findings.append(
                SchemaDriftFinding(
                    kind=SchemaDriftKind.COLUMN_TYPE_MISMATCH,
                    table=expected_table.name,
                    column=name,
                    expected=expected_col.type_norm,
                    observed=observed_col.type_norm,
                )
            )
        if expected_col.notnull != observed_col.notnull:
            findings.append(
                SchemaDriftFinding(
                    kind=SchemaDriftKind.COLUMN_NULLABILITY_MISMATCH,
                    table=expected_table.name,
                    column=name,
                    expected="NOT NULL" if expected_col.notnull else "NULL",
                    observed="NOT NULL" if observed_col.notnull else "NULL",
                )
            )
        if expected_col.primary_key != observed_col.primary_key:
            findings.append(
                SchemaDriftFinding(
                    kind=SchemaDriftKind.COLUMN_PRIMARY_KEY_MISMATCH,
                    table=expected_table.name,
                    column=name,
                    expected="PK" if expected_col.primary_key else "non-PK",
                    observed="PK" if observed_col.primary_key else "non-PK",
                )
            )
    return findings


__all__ = [
    "ExpectedColumn",
    "ExpectedSchema",
    "ExpectedTable",
    "RUNTIME_ONLY_TABLES",
    "SchemaDriftFinding",
    "SchemaDriftKind",
    "SchemaDriftReport",
    "derive_expected_schema",
    "detect_schema_drift",
]
