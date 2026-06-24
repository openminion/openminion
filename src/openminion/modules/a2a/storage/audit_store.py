from __future__ import annotations

import gzip
import json
import shutil
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from openminion.modules.a2a.models import AuditRecord
from openminion.modules.a2a.storage.base import AuditStore
from openminion.modules.storage.migrations.module_ids import get_module_application_id
from openminion.modules.storage.migrations.runner import MigrationRunner
from openminion.modules.storage.record_store import RecordStoreSQLite

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine


class SQLiteAuditStore(AuditStore):
    def __init__(self, root: str | Path, *, retention_days: int = 14) -> None:
        self.root = Path(root).expanduser().resolve(strict=False)
        self.root.mkdir(parents=True, exist_ok=True)
        self.archive_dir = self.root / "archive"
        self.archive_dir.mkdir(parents=True, exist_ok=True)
        self.retention_days = max(1, int(retention_days))
        self._lock = threading.RLock()

    def append_audit(self, record: AuditRecord) -> None:
        with self._lock:
            day = _date_key(record.ts)
            db_path = self.root / f"{day}.db"
            conn = _connect(db_path)
            try:
                _init_schema(conn)
                with conn:
                    conn.execute(
                        """
                        INSERT INTO audit_records(
                            ts, msg_id, trace_id, from_agent, to_agent, to_capability, type, method, status,
                            task_id, error_code, error_message, envelope_json, data_json
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            record.ts,
                            record.msg_id,
                            record.trace_id,
                            record.from_agent,
                            record.to_agent,
                            record.to_capability,
                            record.type,
                            record.method,
                            record.status,
                            record.task_id,
                            record.error_code,
                            record.error_message,
                            _json(record.envelope),
                            _json(record.data),
                        ),
                    )
            finally:
                conn.close()
            self._enforce_retention()

    def query_audit(self, filter_by: dict | None = None) -> list[AuditRecord]:
        filter_by = dict(filter_by or {})
        include_archive = bool(filter_by.pop("include_archive", False))
        archive_store = filter_by.pop("archive_store", None)
        limit = max(1, min(int(filter_by.get("limit", 1000)), 50_000))
        where, params = _where_clause(filter_by)

        rows: list[AuditRecord] = []
        with self._lock:
            paths = sorted(self.root.glob("*.db"))

        if include_archive and archive_store is not None:
            archive_filter = dict(filter_by)
            archive_filter["limit"] = limit
            rows.extend(archive_store.query_archive(archive_filter))

        for path in paths:
            conn = _connect(path)
            try:
                _init_schema(conn)
                sql = (
                    "SELECT ts, msg_id, trace_id, from_agent, to_agent, to_capability, type, method, status, "
                    "task_id, error_code, error_message, envelope_json, data_json "
                    "FROM audit_records"
                )
                if where:
                    sql += " WHERE " + " AND ".join(where)
                sql += " ORDER BY ts ASC LIMIT ?"
                cur = conn.execute(sql, (*params, limit))
                for row in cur.fetchall():
                    rows.append(_row_to_audit_record(row))
                    if len(rows) >= limit:
                        break
                if len(rows) >= limit:
                    break
            finally:
                conn.close()

        rows.sort(key=lambda item: item.ts)
        if filter_by.get("error_only"):
            rows = [row for row in rows if row.error_code]
        if include_archive and archive_store is not None:
            seen: set[str] = set()
            deduped: list[AuditRecord] = []
            for row in rows:
                if row.msg_id in seen:
                    continue
                seen.add(row.msg_id)
                deduped.append(row)
            rows = deduped
        return rows[:limit]

    def close(self) -> None:
        return None

    def _enforce_retention(self) -> None:
        cutoff = datetime.now(timezone.utc).date() - timedelta(days=self.retention_days)
        for path in sorted(self.root.glob("*.db")):
            stem = path.stem
            try:
                day = datetime.strptime(stem, "%Y-%m-%d").date()
            except ValueError:
                continue
            if day >= cutoff:
                continue
            archive_target = self.archive_dir / f"{path.name}.gz"
            with path.open("rb") as src, gzip.open(archive_target, "wb") as dst:
                shutil.copyfileobj(src, dst)
            path.unlink(missing_ok=True)


class PostgresAuditStore(AuditStore):
    def __init__(
        self,
        pool: Engine,
        *,
        retention_days: int = 14,
        database_path: str | Path | None = None,
        owns_engine: bool = False,
    ) -> None:
        self._engine = pool
        self.retention_days = max(1, int(retention_days))
        self._owns_engine = owns_engine
        placeholder_path = (
            Path(database_path).expanduser().resolve(strict=False)
            if database_path is not None
            else (Path.cwd() / ".openminion-a2a-audit-postgres").resolve()
        )
        placeholder_path.parent.mkdir(parents=True, exist_ok=True)
        self._bootstrap_schema(placeholder_path)

    def close(self) -> None:
        if self._owns_engine:
            self._engine.dispose()

    def append_audit(self, record: AuditRecord) -> None:
        from sqlalchemy import text

        with self._engine.begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO audit_records(
                        record_date, ts, msg_id, trace_id, from_agent, to_agent, to_capability,
                        type, method, status, task_id, error_code, error_message, envelope_json, data_json
                    ) VALUES (
                        :record_date, :ts, :msg_id, :trace_id, :from_agent, :to_agent, :to_capability,
                        :type, :method, :status, :task_id, :error_code, :error_message, :envelope_json, :data_json
                    )
                    """
                ),
                {
                    "record_date": _date_key(record.ts),
                    "ts": record.ts,
                    "msg_id": record.msg_id,
                    "trace_id": record.trace_id,
                    "from_agent": record.from_agent,
                    "to_agent": record.to_agent,
                    "to_capability": record.to_capability,
                    "type": record.type,
                    "method": record.method,
                    "status": record.status,
                    "task_id": record.task_id,
                    "error_code": record.error_code,
                    "error_message": record.error_message,
                    "envelope_json": _json(record.envelope),
                    "data_json": _json(record.data),
                },
            )
            self._enforce_retention(connection=conn)

    def query_audit(self, filter_by: dict | None = None) -> list[AuditRecord]:
        from sqlalchemy import text

        filter_by = filter_by or {}
        limit = max(1, min(int(filter_by.get("limit", 1000)), 50_000))
        where, params = _postgres_where_clause(filter_by)
        sql = (
            "SELECT ts, msg_id, trace_id, from_agent, to_agent, to_capability, type, method, status, "
            "task_id, error_code, error_message, envelope_json, data_json "
            "FROM audit_records"
        )
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY ts ASC LIMIT :limit"
        params["limit"] = limit
        with self._engine.connect() as conn:
            rows = conn.execute(text(sql), params).mappings().all()
        return [_row_to_audit_record(row) for row in rows]

    def _bootstrap_schema(self, placeholder_path: Path) -> None:
        runner = MigrationRunner(
            module_id="a2a",
            db_path=placeholder_path,
            module_application_id=get_module_application_id("a2a"),
            backend_type="postgres",
            engine=self._engine,
        )
        report = runner.migrate(target="head")
        if not report.success:
            raise RuntimeError(report.error or "A2A audit migration failed")

    def _enforce_retention(self, *, connection) -> None:  # type: ignore[no-untyped-def]
        from sqlalchemy import text

        cutoff = datetime.now(timezone.utc).date() - timedelta(days=self.retention_days)
        connection.execute(
            text(
                """
                DELETE FROM audit_records
                WHERE record_date < :cutoff_date
                """
            ),
            {"cutoff_date": cutoff.isoformat()},
        )


def _where_clause(filter_by: dict[str, Any]) -> tuple[list[str], list[Any]]:
    where: list[str] = []
    params: list[Any] = []
    for clause, value in _filter_clauses(filter_by, placeholder="?").items():
        where.append(clause)
        params.append(value)
    return where, params


def _postgres_where_clause(
    filter_by: dict[str, Any],
) -> tuple[list[str], dict[str, Any]]:
    params = _filter_clauses(filter_by, placeholder=":{key}")
    where = list(params)
    if filter_by.get("error_only"):
        where.append("error_code IS NOT NULL")
    return where, {
        clause.split(":")[-1]: value
        for clause, value in params.items()
        if ":" in clause
    }


def _filter_clauses(filter_by: dict[str, Any], *, placeholder: str) -> dict[str, Any]:
    clauses: dict[str, Any] = {}
    for key in _FILTER_KEYS:
        value = filter_by.get(key)
        if value:
            clauses[f"{key} = {_placeholder(placeholder, key)}"] = str(value)
    for key, operator in (("since_ts", ">="), ("until_ts", "<=")):
        value = filter_by.get(key)
        if value:
            clauses[f"ts {operator} {_placeholder(placeholder, key)}"] = str(value)
    return clauses


def _placeholder(template: str, key: str) -> str:
    return template.format(key=key) if "{" in template else template


def _row_to_audit_record(row: Any) -> AuditRecord:
    return AuditRecord(
        ts=str(row["ts"]),
        msg_id=str(row["msg_id"]),
        trace_id=str(row["trace_id"]),
        from_agent=str(row["from_agent"]),
        to_agent=_row_text(row, "to_agent"),
        to_capability=_row_text(row, "to_capability"),
        type=str(row["type"]),
        method=str(row["method"]),
        status=str(row["status"]),
        task_id=_row_text(row, "task_id"),
        error_code=_row_text(row, "error_code"),
        error_message=_row_text(row, "error_message"),
        envelope=_json_load(row["envelope_json"], None),
        data=_json_load(row["data_json"], None),
    )


def _row_text(row: Any, key: str) -> str | None:
    value = row[key]
    return None if value is None else str(value)


def _connect(path: Path) -> sqlite3.Connection:
    store = RecordStoreSQLite(path, wal=True)
    return store.connection


def _init_schema(conn: sqlite3.Connection) -> None:
    with conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS audit_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                msg_id TEXT NOT NULL,
                trace_id TEXT NOT NULL,
                from_agent TEXT NOT NULL,
                to_agent TEXT,
                to_capability TEXT,
                type TEXT NOT NULL,
                method TEXT NOT NULL,
                status TEXT NOT NULL,
                task_id TEXT,
                error_code TEXT,
                error_message TEXT,
                envelope_json TEXT,
                data_json TEXT
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_audit_trace ON audit_records(trace_id)"
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_records(ts)")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_audit_method ON audit_records(method)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_audit_status ON audit_records(status)"
        )


def _date_key(ts: str) -> str:
    try:
        parsed = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        parsed = datetime.now(timezone.utc)
    return parsed.astimezone(timezone.utc).strftime("%Y-%m-%d")


def _json(value: Any) -> str | None:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=True)


def _json_load(raw: Any, default: Any) -> Any:
    if raw in {None, ""}:
        return default
    try:
        return json.loads(str(raw))
    except json.JSONDecodeError:
        return default


_FILTER_KEYS = ("trace_id", "from_agent", "to_agent", "method", "status", "error_code")
