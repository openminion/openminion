from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from openminion.modules.storage.migrations.transfer import export_omx


def _build_test_db(path: Path) -> None:
    conn = sqlite3.connect(str(path))
    try:
        conn.execute(
            'CREATE TABLE "rows_with_updated_at" ('
            '"id" INTEGER PRIMARY KEY, '
            '"namespace" TEXT, '
            '"updated_at" TEXT NOT NULL, '
            '"payload" TEXT'
            ")"
        )
        conn.execute(
            'CREATE TABLE "rows_no_timestamps" ('
            '"id" INTEGER PRIMARY KEY, '
            '"namespace" TEXT, '
            '"payload" TEXT'
            ")"
        )
        conn.executemany(
            'INSERT INTO "rows_with_updated_at" '
            '("id", "namespace", "updated_at", "payload") VALUES (?, ?, ?, ?)',
            [
                (1, "alpha", "2026-01-01T00:00:00+00:00", "old-alpha"),
                (2, "alpha", "2026-04-01T00:00:00+00:00", "new-alpha"),
                (3, "beta", "2026-04-02T00:00:00+00:00", "new-beta"),
            ],
        )
        conn.executemany(
            'INSERT INTO "rows_no_timestamps" '
            '("id", "namespace", "payload") VALUES (?, ?, ?)',
            [(1, "alpha", "x"), (2, "beta", "y")],
        )
        conn.commit()
    finally:
        conn.close()


def _read_jsonl_rows(path: Path) -> list[dict]:
    import json

    rows: list[dict] = []
    with path.open() as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def test_full_export_includes_all_tables(tmp_path: Path) -> None:
    db_path = tmp_path / "src.db"
    _build_test_db(db_path)
    out = tmp_path / "out"

    manifest = export_omx(
        db_path=db_path,
        module_id="test_module",
        module_application_id=42,
        export_dir=out,
    )
    table_names = {t.name for t in manifest.tables}
    assert table_names == {"rows_with_updated_at", "rows_no_timestamps"}
    assert "partial_filter" not in (manifest.blobs or {})


def test_since_filter_drops_old_rows(tmp_path: Path) -> None:
    db_path = tmp_path / "src.db"
    _build_test_db(db_path)
    out = tmp_path / "out"
    cutoff = datetime(2026, 3, 1, tzinfo=timezone.utc)

    manifest = export_omx(
        db_path=db_path,
        module_id="test_module",
        module_application_id=42,
        export_dir=out,
        since=cutoff,
    )
    rows = _read_jsonl_rows(out / "tables" / "rows_with_updated_at.jsonl")
    assert {r["id"] for r in rows} == {2, 3}  # row 1 dropped (too old)

    # Tables without updated_at/created_at must be skipped under since-filter
    pf = manifest.blobs["partial_filter"]
    assert "rows_no_timestamps" in pf["skipped_tables"]
    assert pf["since"].startswith("2026-03-01")
    assert pf["namespace"] is None


def test_namespace_filter(tmp_path: Path) -> None:
    db_path = tmp_path / "src.db"
    _build_test_db(db_path)
    out = tmp_path / "out"

    manifest = export_omx(
        db_path=db_path,
        module_id="test_module",
        module_application_id=42,
        export_dir=out,
        namespace="alpha",
    )
    rows_a = _read_jsonl_rows(out / "tables" / "rows_with_updated_at.jsonl")
    rows_b = _read_jsonl_rows(out / "tables" / "rows_no_timestamps.jsonl")
    assert all(r["namespace"] == "alpha" for r in rows_a)
    assert all(r["namespace"] == "alpha" for r in rows_b)
    assert {r["id"] for r in rows_a} == {1, 2}
    assert {r["id"] for r in rows_b} == {1}

    pf = manifest.blobs["partial_filter"]
    assert pf["namespace"] == "alpha"
    assert pf["skipped_tables"] == []


def test_combined_since_and_namespace(tmp_path: Path) -> None:
    db_path = tmp_path / "src.db"
    _build_test_db(db_path)
    out = tmp_path / "out"

    manifest = export_omx(
        db_path=db_path,
        module_id="test_module",
        module_application_id=42,
        export_dir=out,
        since=datetime(2026, 3, 1, tzinfo=timezone.utc),
        namespace="alpha",
    )
    rows = _read_jsonl_rows(out / "tables" / "rows_with_updated_at.jsonl")
    assert {r["id"] for r in rows} == {2}
    pf = manifest.blobs["partial_filter"]
    assert pf["namespace"] == "alpha"
    assert "rows_no_timestamps" in pf["skipped_tables"]


def test_where_clause_appended(tmp_path: Path) -> None:
    db_path = tmp_path / "src.db"
    _build_test_db(db_path)
    out = tmp_path / "out"

    manifest = export_omx(
        db_path=db_path,
        module_id="test_module",
        module_application_id=42,
        export_dir=out,
        where_clause='"id" >= 2',
    )
    rows = _read_jsonl_rows(out / "tables" / "rows_with_updated_at.jsonl")
    assert {r["id"] for r in rows} == {2, 3}
    pf = manifest.blobs["partial_filter"]
    assert pf["where_clause"] == '"id" >= 2'
