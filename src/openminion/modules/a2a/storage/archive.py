"""A2A Postgres audit archive storage."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from openminion.base.logging import get_logger
from openminion.modules.a2a.models import AuditRecord
from openminion.modules.a2a.storage.migrations import MODULE_APPLICATION_ID, MODULE_ID
from openminion.modules.storage.migrations.runner import MigrationRunner
from openminion.modules.storage.record_store import RecordStoreSQLite

_LOG = get_logger("a2a.storage.archive")

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine

    from openminion.modules.storage.backends.postgres import (
        RecordStorePostgres,
    )


ARCHIVE_TABLE_NAME = "a2a_audit_archive"


@dataclass
class ArchiveReport:
    """Summary returned by ``archive_files_older_than``."""

    archived_files: list[str]
    archived_rows: int
    skipped_files: list[str]
    deleted_files: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "archived_files": list(self.archived_files),
            "archived_rows": int(self.archived_rows),
            "skipped_files": list(self.skipped_files),
            "deleted_files": list(self.deleted_files),
        }


class PostgresAuditArchiveStore:
    """Canonical owner of the ``a2a_audit_archive`` Postgres table."""

    table_name: str = ARCHIVE_TABLE_NAME

    def __init__(
        self,
        *,
        record_store: "RecordStorePostgres",
        audit_root: str | Path,
        engine: "Engine | None" = None,
        owns_engine: bool = False,
    ) -> None:
        self._record_store = record_store
        self.audit_root = Path(audit_root).expanduser().resolve(strict=False)
        self.audit_root.mkdir(parents=True, exist_ok=True)
        self._engine = engine
        self._owns_engine = owns_engine
        if engine is not None:
            self._bootstrap_schema(engine)

    def close(self) -> None:
        if self._owns_engine and self._engine is not None:
            try:
                self._engine.dispose()
            except Exception as exc:  # noqa: BLE001 — cleanup must not raise
                _LOG.debug("postgres audit archive engine dispose failed: %s", exc)

    def archive_file(self, sqlite_path: str | Path) -> int:
        path = Path(sqlite_path).expanduser().resolve(strict=False)
        if not path.exists():
            raise FileNotFoundError(str(path))
        rows = self._read_rows_from_sqlite(path)
        if not rows:
            return 0
        record_date = _date_key_from_filename(path.name)
        for row in rows:
            row["source_file"] = path.name
            row["record_date"] = record_date
        return self._record_store.insert_many(self.table_name, rows)

    def archive_files_older_than(
        self,
        older_than_days: int,
        *,
        keep_files: bool = False,
        now: datetime | None = None,
    ) -> ArchiveReport:
        """Archive every daily file older than ``older_than_days``."""

        if older_than_days < 1:
            raise ValueError("older_than_days must be >= 1")
        anchor = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        cutoff = anchor.date() - timedelta(days=older_than_days)

        archived: list[str] = []
        skipped: list[str] = []
        deleted: list[str] = []
        total_rows = 0

        for path in sorted(self.audit_root.glob("*.db")):
            day = _date_from_filename(path.name)
            if day is None:
                skipped.append(path.name)
                continue
            if day >= cutoff:
                skipped.append(path.name)
                continue
            inserted = self.archive_file(path)
            total_rows += inserted
            archived.append(path.name)
            if not keep_files:
                path.unlink(missing_ok=True)
                deleted.append(path.name)
        return ArchiveReport(
            archived_files=archived,
            archived_rows=total_rows,
            skipped_files=skipped,
            deleted_files=deleted,
        )

    def query_archive(self, filter_by: dict | None = None) -> list[AuditRecord]:
        """Read rows back out of the Postgres archive table."""

        filter_by = dict(filter_by or {})
        limit = max(1, min(int(filter_by.get("limit", 1000)), 50_000))
        where, params = _archive_where_clause(filter_by)
        sql = (
            "SELECT ts, msg_id, trace_id, from_agent, to_agent, to_capability, "
            "type, method, status, task_id, error_code, error_message, "
            "envelope_json, data_json "
            f"FROM {self.table_name}"
        )
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY ts ASC LIMIT :limit"
        params["limit"] = limit

        rows = self._record_store.query_dicts(sql, params)
        return [_audit_record_from_row(row) for row in rows]

    def _bootstrap_schema(self, engine: "Engine") -> None:
        placeholder = self.audit_root / ".a2a-audit-archive-placeholder"
        placeholder.parent.mkdir(parents=True, exist_ok=True)
        runner = MigrationRunner(
            module_id=MODULE_ID,
            db_path=placeholder,
            module_application_id=MODULE_APPLICATION_ID,
            backend_type="postgres",
            engine=engine,
        )
        report = runner.migrate(target="head")
        if not report.success:
            raise RuntimeError(report.error or "A2A audit archive migration failed")

    def _read_rows_from_sqlite(self, path: Path) -> list[dict[str, Any]]:
        store = RecordStoreSQLite(path, wal=True)
        try:
            conn = store.connection
            cur = conn.execute(
                "SELECT ts, msg_id, trace_id, from_agent, to_agent, to_capability, "
                "type, method, status, task_id, error_code, error_message, "
                "envelope_json, data_json FROM audit_records ORDER BY id ASC"
            )
            return [_dict_from_sqlite_row(row) for row in cur.fetchall()]
        except sqlite3.OperationalError:
            return []
        finally:
            try:
                store.close()
            except Exception as exc:  # noqa: BLE001 — cleanup must not raise
                _LOG.debug("daily-file record store close failed: %s", exc)


def _dict_from_sqlite_row(row: Any) -> dict[str, Any]:
    return {
        "ts": str(row["ts"]),
        "msg_id": str(row["msg_id"]),
        "trace_id": str(row["trace_id"]),
        "from_agent": str(row["from_agent"]),
        "to_agent": _row_text(row, "to_agent"),
        "to_capability": _row_text(row, "to_capability"),
        "type": str(row["type"]),
        "method": str(row["method"]),
        "status": str(row["status"]),
        "task_id": _row_text(row, "task_id"),
        "error_code": _row_text(row, "error_code"),
        "error_message": _row_text(row, "error_message"),
        "envelope_json": _row_text(row, "envelope_json"),
        "data_json": _row_text(row, "data_json"),
    }


def _audit_record_from_row(row: dict[str, Any]) -> AuditRecord:
    return AuditRecord(
        ts=str(row["ts"]),
        msg_id=str(row["msg_id"]),
        trace_id=str(row["trace_id"]),
        from_agent=str(row["from_agent"]),
        to_agent=_mapping_text(row, "to_agent"),
        to_capability=_mapping_text(row, "to_capability"),
        type=str(row["type"]),
        method=str(row["method"]),
        status=str(row["status"]),
        task_id=_mapping_text(row, "task_id"),
        error_code=_mapping_text(row, "error_code"),
        error_message=_mapping_text(row, "error_message"),
        envelope=_json_load(row.get("envelope_json"), None),
        data=_json_load(row.get("data_json"), None),
    )


def _archive_where_clause(
    filter_by: dict[str, Any],
) -> tuple[list[str], dict[str, Any]]:
    params = _filter_clauses(filter_by)
    where = list(params)
    if filter_by.get("error_only"):
        where.append("error_code IS NOT NULL")
    return where, {clause.split(":")[-1]: value for clause, value in params.items()}


def _date_from_filename(name: str) -> "datetime.date | None":
    stem = Path(name).stem
    try:
        return datetime.strptime(stem, "%Y-%m-%d").date()
    except ValueError:
        return None


def _date_key_from_filename(name: str) -> str:
    day = _date_from_filename(name)
    if day is None:
        return datetime.now(timezone.utc).date().isoformat()
    return day.isoformat()


def _filter_clauses(filter_by: dict[str, Any]) -> dict[str, Any]:
    clauses: dict[str, Any] = {}
    for key in _FILTER_KEYS:
        value = filter_by.get(key)
        if value:
            clauses[f"{key} = :{key}"] = str(value)
    for key, operator in (("since_ts", ">="), ("until_ts", "<=")):
        value = filter_by.get(key)
        if value:
            clauses[f"ts {operator} :{key}"] = str(value)
    return clauses


def _row_text(row: Any, key: str) -> str | None:
    value = row[key]
    return None if value is None else str(value)


def _mapping_text(row: dict[str, Any], key: str) -> str | None:
    value = row.get(key)
    return None if value is None else str(value)


def _json_load(raw: Any, default: Any) -> Any:
    if raw in (None, ""):
        return default
    try:
        return json.loads(str(raw))
    except json.JSONDecodeError:
        return default


__all__ = [
    "ARCHIVE_TABLE_NAME",
    "ArchiveReport",
    "PostgresAuditArchiveStore",
]


_FILTER_KEYS = ("trace_id", "from_agent", "to_agent", "method", "status", "error_code")
