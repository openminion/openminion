from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterator

from openminion.modules.storage.migrations.meta_rows import rows_to_meta
from openminion.modules.storage.migrations.models import RehydrateReport
from openminion.modules.storage.migrations.omx import (
    OmxManifest,
    OmxResumeChunk,
    OmxSource,
    OmxTableEntry,
    dump_manifest,
    load_manifest,
)
from openminion.modules.storage.migrations.verify import run_verification
from openminion.modules.storage.record_store import RecordStore, RecordStoreSQLite

if TYPE_CHECKING:
    from openminion.modules.storage.progress import ProgressReporter


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_sqlite_store(record_store: RecordStore) -> bool:
    return bool(record_store.capabilities().get("raw_sql", False))


def _list_tables_via_record_store(record_store: RecordStore) -> list[str]:
    if _is_sqlite_store(record_store):
        rows = record_store.query_dicts(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
        return [str(row["name"]) for row in rows]
    rows = record_store.query_dicts(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema = current_schema()"
    )
    return [str(row["table_name"]) for row in rows]


def _read_table_schema_via_record_store(
    record_store: RecordStore, table: str
) -> str | None:
    if _is_sqlite_store(record_store):
        rows = record_store.query_dicts(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name = ?",
            (table,),
        )
        if not rows:
            return None
        value = rows[0].get("sql")
        return None if value is None else str(value)
    # Postgres exports omit DDL.
    return None


def _table_columns_via_record_store(record_store: RecordStore, table: str) -> list[str]:
    if _is_sqlite_store(record_store):
        rows = record_store.query_dicts(f'PRAGMA table_info("{table}")')
        return [str(row["name"]) for row in rows]
    rows = record_store.query_dicts(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema = current_schema() AND table_name = :tn "
        "ORDER BY ordinal_position",
        {"tn": table},
    )
    return [str(row["column_name"]) for row in rows]


def _read_om_meta_via_record_store(record_store: RecordStore) -> dict[str, str]:
    try:
        rows = record_store.query_dicts("SELECT key, value FROM om_meta")
    except Exception:  # noqa: BLE001
        return {}
    return rows_to_meta(
        (row.get("key"), row.get("value")) for row in rows if row.get("key") is not None
    )


def _read_user_version_via_record_store(record_store: RecordStore) -> int:
    if not _is_sqlite_store(record_store):
        return 0
    try:
        rows = record_store.query_dicts("PRAGMA user_version")
    except Exception:  # noqa: BLE001
        return 0
    if not rows:
        return 0
    first = rows[0]
    if "user_version" in first:
        return int(first["user_version"])
    for value in first.values():
        try:
            return int(value)
        except (ValueError, TypeError):
            continue
    return 0


def _table_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    rows = conn.execute(f'PRAGMA table_info("{table}")').fetchall()
    return [str(row[1]) for row in rows]


def _build_partial_where(
    columns: list[str],
    *,
    since: datetime | None,
    namespace: str | None,
    where_clause: str | None,
    placeholder: str = "?",
) -> tuple[str | None, list[Any]]:
    parts: list[str] = []
    params: list[Any] = []
    skip = False

    if since is not None:
        if "updated_at" in columns:
            parts.append(f'"updated_at" >= {placeholder}')
            params.append(since.isoformat())
        elif "created_at" in columns:
            parts.append(f'"created_at" >= {placeholder}')
            params.append(since.isoformat())
        else:
            skip = True

    if namespace is not None and not skip:
        if "namespace" in columns:
            parts.append(f'"namespace" = {placeholder}')
            params.append(namespace)
        else:
            skip = True

    if where_clause and not skip:
        parts.append(f"({where_clause})")

    if skip:
        return "__skip__", []
    if not parts:
        return None, []
    return " AND ".join(parts), params


def _stream_table_rows_via_record_store(
    record_store: RecordStore,
    table: str,
    *,
    where_clause: str | None,
    where_params: list[Any],
) -> Iterator[dict[str, Any]]:
    """Stream rows for ``table`` with the resolved filter."""
    sql = f'SELECT * FROM "{table}"'
    if where_clause:
        sql = f"{sql} WHERE {where_clause}"

    if not where_params:
        yield from record_store.stream_dicts(sql)
        return

    yield from record_store.stream_dicts(sql, tuple(where_params))


def _export_via_record_store(
    record_store: RecordStore,
    *,
    db_path_hint: str,
    module_id: str,
    module_application_id: int,
    export_dir: Path,
    export_notes: str | None,
    since: datetime | None,
    namespace: str | None,
    where_clause: str | None,
    reporter: "ProgressReporter | None",
) -> OmxManifest:
    export_dir.mkdir(parents=True, exist_ok=True)
    tables_dir = export_dir / "tables"
    tables_dir.mkdir(parents=True, exist_ok=True)

    _reporter_started = False
    if reporter is not None:
        try:
            reporter.on_start(total=None, label=f"export_omx[{module_id}]")
            _reporter_started = True
        except Exception:  # noqa: BLE001
            _reporter_started = False

    user_version = _read_user_version_via_record_store(record_store)
    om_meta = _read_om_meta_via_record_store(record_store)

    table_entries: list[OmxTableEntry] = []
    schema_map: dict[str, str] = {}
    skipped_tables: list[str] = []
    partial_filter_active = (
        since is not None or namespace is not None or where_clause is not None
    )

    for table in sorted(_list_tables_via_record_store(record_store)):
        schema = _read_table_schema_via_record_store(record_store, table)
        if schema is not None:
            schema_map[table] = schema

        partial_clause: str | None = None
        partial_params: list[Any] = []
        if partial_filter_active:
            columns = _table_columns_via_record_store(record_store, table)
            clause, params = _build_partial_where(
                columns,
                since=since,
                namespace=namespace,
                where_clause=where_clause,
            )
            if clause == "__skip__":
                skipped_tables.append(table)
                continue
            partial_clause = clause
            partial_params = params

        data_path = tables_dir / f"{table}.jsonl"
        row_count = 0
        with data_path.open("w", encoding="utf-8") as handle:
            for row in _stream_table_rows_via_record_store(
                record_store,
                table,
                where_clause=partial_clause,
                where_params=partial_params,
            ):
                handle.write(json.dumps(row, ensure_ascii=True, default=str) + "\n")
                row_count += 1
                if reporter is not None:
                    try:
                        reporter.on_progress(advance=1, message=table)
                    except Exception:  # noqa: BLE001
                        pass

        sha256 = _sha256_file(data_path)
        chunks: list[OmxResumeChunk] = []
        if row_count > 0:
            chunks.append(
                OmxResumeChunk(
                    chunk_index=0,
                    row_start=0,
                    row_end=row_count - 1,
                    sha256=sha256,
                )
            )

        table_entries.append(
            OmxTableEntry(
                name=table,
                path=str(data_path.relative_to(export_dir)),
                codec="jsonl",
                row_count=row_count,
                sha256=sha256,
                resume_chunks=chunks,
            )
        )

    blobs: dict[str, Any] = {"schemas": schema_map}
    if partial_filter_active:
        blobs["partial_filter"] = {
            "since": since.isoformat() if since is not None else None,
            "namespace": namespace,
            "where_clause": where_clause,
            "skipped_tables": skipped_tables,
        }

    manifest = OmxManifest(
        format="openminion-omx",
        format_version="1",
        module_id=str(module_id),
        module_application_id=int(module_application_id),
        created_at=_utc_now_iso(),
        source=OmxSource(
            db_path=str(db_path_hint),
            user_version=int(user_version),
            schema_head=om_meta.get("schema_head"),
            export_notes=export_notes,
        ),
        tables=table_entries,
        blobs=blobs,
    )
    dump_manifest(manifest, export_dir / "manifest.json")
    if _reporter_started and reporter is not None:
        try:
            reporter.on_end(success=True, message=f"{len(table_entries)} tables")
        except Exception:  # noqa: BLE001
            pass
    return manifest


def export_omx(
    *,
    db_path: str | Path | None = None,
    record_store: RecordStore | None = None,
    module_id: str,
    module_application_id: int,
    export_dir: str | Path,
    export_notes: str | None = None,
    since: datetime | None = None,
    namespace: str | None = None,
    where_clause: str | None = None,
    reporter: "ProgressReporter | None" = None,
) -> OmxManifest:
    """Export a module database to an OMX bundle."""
    if db_path is None and record_store is None:
        raise ValueError("export_omx requires either db_path or record_store")

    export_dir = Path(export_dir).expanduser().resolve(strict=False)

    if record_store is not None:
        db_path_hint = (
            str(Path(db_path).expanduser().resolve(strict=False))
            if db_path is not None
            else ""
        )
        return _export_via_record_store(
            record_store,
            db_path_hint=db_path_hint,
            module_id=module_id,
            module_application_id=module_application_id,
            export_dir=export_dir,
            export_notes=export_notes,
            since=since,
            namespace=namespace,
            where_clause=where_clause,
            reporter=reporter,
        )

    assert db_path is not None
    resolved_db_path = Path(db_path).expanduser().resolve(strict=False)
    sqlite_store = RecordStoreSQLite(resolved_db_path)
    try:
        return _export_via_record_store(
            sqlite_store,
            db_path_hint=str(resolved_db_path),
            module_id=module_id,
            module_application_id=module_application_id,
            export_dir=export_dir,
            export_notes=export_notes,
            since=since,
            namespace=namespace,
            where_clause=where_clause,
            reporter=reporter,
        )
    finally:
        try:
            sqlite_store.close()
        except Exception:  # noqa: BLE001
            pass


def _read_omx_bundle_rows(
    omx_dir: Path, table_entry: Any, *, verify_checksums: bool
) -> list[dict[str, Any]]:
    data_path = omx_dir / table_entry.path
    if verify_checksums:
        actual = _sha256_file(data_path)
        if actual != table_entry.sha256:
            raise ValueError(
                f"Checksum mismatch for table {table_entry.name}: "
                f"{actual} != {table_entry.sha256}"
            )
    rows: list[dict[str, Any]] = []
    with data_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            rows.append(json.loads(line))
    return rows


def _ensure_table_schema_sqlite(
    conn: sqlite3.Connection, schema_map: dict[str, str], table: str
) -> None:
    schema = schema_map.get(table)
    if not schema:
        return
    try:
        conn.execute(schema)
    except sqlite3.OperationalError as exc:
        if "already exists" not in str(exc).lower():
            raise


def _import_via_record_store(
    record_store: RecordStore,
    *,
    omx_dir: Path,
    target_db_path_hint: str,
    manifest: Any,
    verify_checksums: bool,
    reporter: "ProgressReporter | None",
) -> RehydrateReport:
    success = False
    error: str | None = None
    exported_rows = sum(table.row_count for table in manifest.tables)
    imported_rows = 0
    verification = None

    _reporter_started = False
    if reporter is not None:
        try:
            reporter.on_start(
                total=exported_rows, label=f"import_omx[{manifest.module_id}]"
            )
            _reporter_started = True
        except Exception:  # noqa: BLE001
            _reporter_started = False

    try:
        for table in manifest.tables:
            rows = _read_omx_bundle_rows(
                omx_dir, table, verify_checksums=verify_checksums
            )
            try:
                record_store.execute_count(f'DELETE FROM "{table.name}"')
            except Exception:
                if not rows:
                    continue
                raise
            if not rows:
                continue
            inserted = record_store.insert_many(table.name, rows)
            imported_rows += inserted
            if reporter is not None:
                try:
                    reporter.on_progress(advance=inserted, message=table.name)
                except Exception:  # noqa: BLE001
                    pass
        success = True
    except Exception as exc:  # noqa: BLE001
        error = str(exc)

    if _reporter_started and reporter is not None:
        try:
            reporter.on_end(success=success, message=error)
        except Exception:  # noqa: BLE001
            pass

    return RehydrateReport(
        module_id=manifest.module_id,
        source_db_path=manifest.source.db_path,
        target_db_path=target_db_path_hint,
        omx_dir=str(omx_dir),
        success=success,
        exported_rows=exported_rows,
        imported_rows=imported_rows,
        verification=verification,
        error=error,
    )


def import_omx(
    *,
    omx_dir: str | Path,
    target_db_path: str | Path | None = None,
    target_record_store: RecordStore | None = None,
    verify_checksums: bool = True,
    reporter: "ProgressReporter | None" = None,
) -> RehydrateReport:
    """Import an OMX bundle into a module database."""
    if target_db_path is None and target_record_store is None:
        raise ValueError(
            "import_omx requires either target_db_path or target_record_store"
        )

    omx_dir = Path(omx_dir).expanduser().resolve(strict=False)
    manifest = load_manifest(omx_dir / "manifest.json")

    if target_record_store is not None:
        target_hint = (
            str(Path(target_db_path).expanduser().resolve(strict=False))
            if target_db_path is not None
            else ""
        )
        return _import_via_record_store(
            target_record_store,
            omx_dir=omx_dir,
            target_db_path_hint=target_hint,
            manifest=manifest,
            verify_checksums=verify_checksums,
            reporter=reporter,
        )

    assert target_db_path is not None
    resolved_target = Path(target_db_path).expanduser().resolve(strict=False)

    success = False
    error: str | None = None
    exported_rows = sum(table.row_count for table in manifest.tables)
    imported_rows = 0
    verification = None

    _reporter_started = False
    if reporter is not None:
        try:
            reporter.on_start(
                total=exported_rows, label=f"import_omx[{manifest.module_id}]"
            )
            _reporter_started = True
        except Exception:  # noqa: BLE001
            _reporter_started = False

    try:
        resolved_target.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(str(resolved_target)) as conn:
            schema_map: dict[str, str] = {}
            if manifest.blobs:
                schema_map = manifest.blobs.get("schemas", {}) or {}
            for table in manifest.tables:
                _ensure_table_schema_sqlite(conn, schema_map, table.name)
            for table in manifest.tables:
                rows = _read_omx_bundle_rows(
                    omx_dir, table, verify_checksums=verify_checksums
                )
                try:
                    conn.execute(f'DELETE FROM "{table.name}"')
                except sqlite3.OperationalError:
                    if not rows:
                        continue
                    raise
                if not rows:
                    continue
                columns = list(rows[0].keys())
                placeholders = ", ".join(["?"] * len(columns))
                sql = (
                    f'INSERT INTO "{table.name}" ({", ".join(columns)}) '
                    f"VALUES ({placeholders})"
                )
                conn.executemany(
                    sql, [[row.get(col) for col in columns] for row in rows]
                )
                imported_rows += len(rows)
                if reporter is not None:
                    try:
                        reporter.on_progress(advance=len(rows), message=table.name)
                    except Exception:  # noqa: BLE001
                        pass
            conn.commit()

        verification = run_verification(
            module_id=manifest.module_id,
            db_path=resolved_target,
            level="quick",
            raise_on_fatal=False,
        )
        success = True
    except Exception as exc:  # noqa: BLE001
        error = str(exc)

    if _reporter_started and reporter is not None:
        try:
            reporter.on_end(success=success, message=error)
        except Exception:  # noqa: BLE001
            pass

    return RehydrateReport(
        module_id=manifest.module_id,
        source_db_path=manifest.source.db_path,
        target_db_path=str(resolved_target),
        omx_dir=str(omx_dir),
        success=success,
        exported_rows=exported_rows,
        imported_rows=imported_rows,
        verification=verification,
        error=error,
    )


__all__ = ["export_omx", "import_omx"]
