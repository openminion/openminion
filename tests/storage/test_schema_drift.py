from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from openminion.modules.storage.runtime.migrations import (
    DEFAULT_MIGRATIONS,
    Migration,
    migrate_database,
)
from openminion.modules.storage.runtime.schema_drift import (
    ExpectedColumn,
    ExpectedSchema,
    ExpectedTable,
    SchemaDriftFinding,
    SchemaDriftKind,
    SchemaDriftReport,
    derive_expected_schema,
    detect_schema_drift,
)


def _migrated_path(tmp_path: Path, *, name: str = "live.db") -> Path:
    db_path = tmp_path / name
    migrate_database(db_path)
    return db_path


def test_derive_expected_schema_covers_default_migrations() -> None:
    schema = derive_expected_schema()
    assert schema.head_version == max(m.version for m in DEFAULT_MIGRATIONS)
    table_names = {t.name for t in schema.tables}
    # Migrations ledger excluded; user tables present
    assert "migrations" not in table_names
    assert {"sessions", "messages", "events", "memory_records"}.issubset(table_names)


def test_detect_schema_drift_match_on_freshly_migrated_db(tmp_path: Path) -> None:
    db_path = _migrated_path(tmp_path)
    expected = derive_expected_schema()

    report = detect_schema_drift(expected, db_path)

    assert isinstance(report, SchemaDriftReport)
    assert report.expected_head_version == expected.head_version
    assert report.observed_head_version == expected.head_version
    assert report.has_drift is False
    assert len(report.findings) == 1
    assert report.findings[0].kind is SchemaDriftKind.MATCH


def test_detect_schema_drift_missing_table(tmp_path: Path) -> None:
    db_path = _migrated_path(tmp_path)
    expected = derive_expected_schema()

    with sqlite3.connect(str(db_path)) as connection:
        connection.execute("DROP TABLE memory_vectors")
        connection.commit()

    report = detect_schema_drift(expected, db_path)

    assert report.has_drift is True
    kinds = {f.kind for f in report.findings}
    assert SchemaDriftKind.MISSING_TABLE in kinds
    missing = report.findings_by_kind(SchemaDriftKind.MISSING_TABLE)
    assert any(f.table == "memory_vectors" for f in missing)


def test_detect_schema_drift_extra_table(tmp_path: Path) -> None:
    db_path = _migrated_path(tmp_path)
    expected = derive_expected_schema()

    with sqlite3.connect(str(db_path)) as connection:
        connection.execute(
            "CREATE TABLE ad_hoc_extra(id INTEGER PRIMARY KEY, body TEXT NOT NULL)"
        )
        connection.commit()

    report = detect_schema_drift(expected, db_path)

    assert report.has_drift is True
    extras = report.findings_by_kind(SchemaDriftKind.EXTRA_TABLE)
    assert any(f.table == "ad_hoc_extra" for f in extras)


def test_detect_schema_drift_extra_column(tmp_path: Path) -> None:
    db_path = _migrated_path(tmp_path)
    expected = derive_expected_schema()

    with sqlite3.connect(str(db_path)) as connection:
        connection.execute(
            "ALTER TABLE sessions ADD COLUMN ad_hoc_label TEXT NOT NULL DEFAULT ''"
        )
        connection.commit()

    report = detect_schema_drift(expected, db_path)

    extras = report.findings_by_kind(SchemaDriftKind.EXTRA_COLUMN)
    assert any(f.table == "sessions" and f.column == "ad_hoc_label" for f in extras)


def test_detect_schema_drift_missing_column_via_synthetic_expected() -> None:
    # Build a synthetic expected schema with one extra column. The live DB
    # (in-memory baseline migration) doesn't have it, so we expect
    # MISSING_COLUMN.
    expected = derive_expected_schema()
    sessions = expected.table_by_name("sessions")
    assert sessions is not None
    augmented_sessions = ExpectedTable(
        name="sessions",
        columns=sessions.columns
        + (
            ExpectedColumn(
                name="future_only_column",
                type_norm="TEXT",
                notnull=True,
                primary_key=False,
            ),
        ),
    )
    augmented = ExpectedSchema(
        tables=tuple(
            augmented_sessions if t.name == "sessions" else t for t in expected.tables
        ),
        head_version=expected.head_version,
    )

    connection = sqlite3.connect(":memory:")
    try:
        # Apply every default migration so the live shape matches today's head.
        for migration in sorted(DEFAULT_MIGRATIONS, key=lambda m: m.version):
            for statement in migration.statements:
                connection.execute(statement)
        connection.execute(
            "CREATE TABLE migrations(version INTEGER PRIMARY KEY, name TEXT NOT NULL,"
            " applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
        )
        connection.execute(
            "INSERT INTO migrations(version, name) VALUES (?, ?)",
            (expected.head_version, "head"),
        )
        connection.commit()
        report = detect_schema_drift(augmented, connection)
    finally:
        connection.close()

    missing_cols = report.findings_by_kind(SchemaDriftKind.MISSING_COLUMN)
    assert any(
        f.table == "sessions" and f.column == "future_only_column" for f in missing_cols
    )


def test_detect_schema_drift_migration_ledger_behind(tmp_path: Path) -> None:
    # Migrate to only the first migration; expected schema knows about all.
    head_only_first = DEFAULT_MIGRATIONS[:1]
    db_path = tmp_path / "partial.db"
    migrate_database(db_path, migrations=head_only_first)
    expected = derive_expected_schema()

    report = detect_schema_drift(expected, db_path)

    assert report.observed_head_version == head_only_first[-1].version
    assert report.expected_head_version > report.observed_head_version
    behind = report.findings_by_kind(SchemaDriftKind.MIGRATION_LEDGER_BEHIND)
    assert len(behind) == 1
    assert behind[0].expected == str(report.expected_head_version)
    assert behind[0].observed == str(report.observed_head_version)


def test_detect_schema_drift_accepts_connection(tmp_path: Path) -> None:
    db_path = _migrated_path(tmp_path)
    expected = derive_expected_schema()

    connection = sqlite3.connect(str(db_path))
    try:
        report = detect_schema_drift(expected, connection)
    finally:
        connection.close()

    assert report.has_drift is False


def test_schema_drift_kind_is_closed_enum() -> None:
    # Closed-enum guard (Anti-LLM §1): the report only emits values defined
    # in SchemaDriftKind. This test pins the membership so a future drop or
    # accidental addition is caught.
    expected_members = {
        "MATCH",
        "MISSING_TABLE",
        "EXTRA_TABLE",
        "MISSING_COLUMN",
        "EXTRA_COLUMN",
        "COLUMN_TYPE_MISMATCH",
        "COLUMN_NULLABILITY_MISMATCH",
        "COLUMN_PRIMARY_KEY_MISMATCH",
        "MIGRATION_LEDGER_BEHIND",
    }
    actual = {k.name for k in SchemaDriftKind}
    assert actual == expected_members


def test_schema_drift_report_as_dict_is_serializable(tmp_path: Path) -> None:
    db_path = _migrated_path(tmp_path)
    expected = derive_expected_schema()
    report = detect_schema_drift(expected, db_path)

    payload = report.as_dict()
    assert payload["has_drift"] is False
    assert payload["expected_head_version"] == expected.head_version
    assert isinstance(payload["findings"], list)

    # Every finding round-trips to a plain dict (closed-enum kind string).
    for entry in payload["findings"]:
        assert set(entry.keys()) == {"kind", "table", "column", "expected", "observed"}
        SchemaDriftKind(entry["kind"])  # raises if not a closed-enum value


def test_detect_schema_drift_with_custom_migration_subset() -> None:
    # Verify the derive_expected_schema helper accepts a Sequence[Migration]
    # so callers can pin a smaller schema for unit testing.
    only_first: tuple[Migration, ...] = DEFAULT_MIGRATIONS[:1]
    schema = derive_expected_schema(only_first)
    assert schema.head_version == only_first[-1].version
    assert {t.name for t in schema.tables} == {
        "sessions",
        "messages",
        "events",
        "idempotency_keys",
    }


def test_detect_schema_drift_finding_dataclass_roundtrip() -> None:
    finding = SchemaDriftFinding(
        kind=SchemaDriftKind.COLUMN_TYPE_MISMATCH,
        table="x",
        column="y",
        expected="TEXT",
        observed="BLOB",
    )
    assert finding.as_dict() == {
        "kind": "column_type_mismatch",
        "table": "x",
        "column": "y",
        "expected": "TEXT",
        "observed": "BLOB",
    }


def test_detect_schema_drift_path_must_exist(tmp_path: Path) -> None:
    expected = derive_expected_schema()
    missing_path = tmp_path / "does_not_exist.db"
    # sqlite3.connect will create an empty DB at this path, so the comparison
    # surfaces every table as MISSING_TABLE without raising.
    report = detect_schema_drift(expected, missing_path)
    assert report.has_drift is True
    missing = report.findings_by_kind(SchemaDriftKind.MISSING_TABLE)
    assert {f.table for f in missing} >= {"sessions", "messages", "events"}


@pytest.mark.parametrize("drop_table", ["events", "memory_records"])
def test_detect_schema_drift_parametrized_missing(
    tmp_path: Path, drop_table: str
) -> None:
    db_path = _migrated_path(tmp_path, name=f"{drop_table}.db")
    expected = derive_expected_schema()
    with sqlite3.connect(str(db_path)) as connection:
        connection.execute(f"DROP TABLE {drop_table}")
        connection.commit()
    report = detect_schema_drift(expected, db_path)
    missing = {f.table for f in report.findings_by_kind(SchemaDriftKind.MISSING_TABLE)}
    assert drop_table in missing
