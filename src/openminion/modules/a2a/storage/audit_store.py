from __future__ import annotations

import gzip
import json
import shutil
import sqlite3
import threading
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from openminion.modules.a2a.config import validate_audit_retention
from openminion.modules.a2a.models import AuditRecord
from openminion.modules.a2a.storage.base import AuditStore, audit_record_for_storage
from openminion.modules.storage.migrations.module_ids import get_module_application_id
from openminion.modules.storage.migrations.runner import MigrationRunner
from openminion.modules.storage.record_store import RecordStoreSQLite

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine


class SQLiteAuditStore(AuditStore):
    def __init__(
        self,
        root: str | Path,
        *,
        capture_payloads: bool = False,
        retention_days: int = 14,
        archive_retention_days: int = 0,
    ) -> None:
        self.root = Path(root).expanduser().resolve(strict=False)
        _secure_directory(self.root)
        self.archive_dir = self.root / "archive"
        self.capture_payloads = bool(capture_payloads)
        self.retention_days, self.archive_retention_days = validate_audit_retention(
            retention_days,
            archive_retention_days,
        )
        self._lock = threading.RLock()
        with self._lock:
            self._enforce_retention()

    def append_audit(self, record: AuditRecord) -> None:
        stored = audit_record_for_storage(
            record,
            capture_payloads=self.capture_payloads,
        )
        with self._lock:
            day = _date_key(stored.ts)
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
                            stored.ts,
                            stored.msg_id,
                            stored.trace_id,
                            stored.from_agent,
                            stored.to_agent,
                            stored.to_capability,
                            stored.type,
                            stored.method,
                            stored.status,
                            stored.task_id,
                            stored.error_code,
                            stored.error_message,
                            _json(stored.envelope),
                            _json(stored.data),
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

        archived_rows: list[AuditRecord] = []
        with self._lock:
            self._enforce_retention()
            paths = sorted(self.root.glob("*.db"), reverse=True)

        if include_archive and archive_store is not None:
            archive_filter = dict(filter_by)
            archive_filter["limit"] = limit
            archived_rows.extend(archive_store.query_archive(archive_filter))

        active_rows: list[AuditRecord] = []
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
                sql += " ORDER BY ts DESC, msg_id DESC LIMIT ?"
                remaining = limit - len(active_rows)
                cur = conn.execute(sql, (*params, remaining))
                for row in cur.fetchall():
                    active_rows.append(_row_to_audit_record(row))
                if len(active_rows) >= limit:
                    break
            finally:
                conn.close()

        rows = sorted(
            (*active_rows, *archived_rows),
            key=lambda item: (item.ts, item.msg_id),
            reverse=True,
        )
        seen: set[str] = set()
        selected: list[AuditRecord] = []
        for row in rows:
            if row.msg_id in seen:
                continue
            seen.add(row.msg_id)
            selected.append(row)
            if len(selected) >= limit:
                break
        selected.reverse()
        return selected

    def close(self) -> None:
        return None

    def _enforce_retention(self) -> None:
        cutoff = datetime.now(timezone.utc).date() - timedelta(days=self.retention_days)
        for path in sorted(self.root.glob("*.db")):
            day = _daily_file_date(path.name)
            if day is None:
                continue
            if day >= cutoff:
                continue
            if self.archive_retention_days:
                _secure_directory(self.archive_dir)
                archive_target = self.archive_dir / f"{path.name}.gz"
                _checkpoint_sqlite(path)
                with path.open("rb") as src, gzip.open(archive_target, "wb") as dst:
                    shutil.copyfileobj(src, dst)
                archive_target.chmod(0o600)
            path.unlink(missing_ok=True)
            _remove_sqlite_sidecars(path)

        for pattern in ("*.db-wal", "*.db-shm"):
            for path in self.root.glob(pattern):
                day = _daily_file_date(
                    path.name.removesuffix("-wal").removesuffix("-shm")
                )
                if day is not None and day < cutoff:
                    path.unlink(missing_ok=True)

        if not self.archive_dir.exists():
            return
        archive_cutoff = (
            datetime.now(timezone.utc).date()
            - timedelta(days=self.archive_retention_days)
            if self.archive_retention_days
            else None
        )
        for path in sorted(self.archive_dir.glob("*.db.gz")):
            day = _daily_file_date(path.name)
            if day is None:
                continue
            if archive_cutoff is None or day < archive_cutoff:
                path.unlink(missing_ok=True)


class PostgresAuditStore(AuditStore):
    def __init__(
        self,
        pool: Engine,
        *,
        capture_payloads: bool = False,
        retention_days: int = 14,
        archive_retention_days: int = 0,
        database_path: str | Path | None = None,
        owns_engine: bool = False,
    ) -> None:
        self._engine = pool
        self.capture_payloads = bool(capture_payloads)
        self.retention_days, self.archive_retention_days = validate_audit_retention(
            retention_days,
            archive_retention_days,
        )
        self.effective_retention_days = (
            self.archive_retention_days or self.retention_days
        )
        self._owns_engine = owns_engine
        placeholder_path = (
            Path(database_path).expanduser().resolve(strict=False)
            if database_path is not None
            else (Path.cwd() / ".openminion-a2a-audit-postgres").resolve()
        )
        placeholder_path.parent.mkdir(parents=True, exist_ok=True)
        _secure_directory(placeholder_path.parent)
        self._bootstrap_schema(placeholder_path)
        if placeholder_path.exists():
            placeholder_path.chmod(0o600)
        self._enforce_retention()

    def close(self) -> None:
        if self._owns_engine:
            self._engine.dispose()

    def append_audit(self, record: AuditRecord) -> None:
        from sqlalchemy import text

        stored = audit_record_for_storage(
            record,
            capture_payloads=self.capture_payloads,
        )
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
                    "record_date": _date_key(stored.ts),
                    "ts": stored.ts,
                    "msg_id": stored.msg_id,
                    "trace_id": stored.trace_id,
                    "from_agent": stored.from_agent,
                    "to_agent": stored.to_agent,
                    "to_capability": stored.to_capability,
                    "type": stored.type,
                    "method": stored.method,
                    "status": stored.status,
                    "task_id": stored.task_id,
                    "error_code": stored.error_code,
                    "error_message": stored.error_message,
                    "envelope_json": _json(stored.envelope),
                    "data_json": _json(stored.data),
                },
            )
            self._enforce_retention(connection=conn)

    def query_audit(self, filter_by: dict | None = None) -> list[AuditRecord]:
        from sqlalchemy import text

        filter_by = filter_by or {}
        self._enforce_retention()
        limit = max(1, min(int(filter_by.get("limit", 1000)), 50_000))
        where, params = _postgres_where_clause(filter_by)
        sql = (
            "SELECT ts, msg_id, trace_id, from_agent, to_agent, to_capability, type, method, status, "
            "task_id, error_code, error_message, envelope_json, data_json "
            "FROM audit_records"
        )
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY ts DESC, msg_id DESC LIMIT :limit"
        params["limit"] = limit
        with self._engine.connect() as conn:
            rows = conn.execute(text(sql), params).mappings().all()
        selected = [_row_to_audit_record(row) for row in rows]
        selected.reverse()
        return selected

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

    def _enforce_retention(self, *, connection: Any | None = None) -> None:
        from sqlalchemy import text

        if connection is None:
            with self._engine.begin() as owned_connection:
                self._enforce_retention(connection=owned_connection)
            return
        cutoff = datetime.now(timezone.utc).date() - timedelta(
            days=self.effective_retention_days
        )
        connection.execute(
            text("DELETE FROM audit_records WHERE record_date < :cutoff_date"),
            {"cutoff_date": cutoff.isoformat()},
        )


def _where_clause(filter_by: dict[str, Any]) -> tuple[list[str], list[Any]]:
    clauses = _filter_clauses(filter_by, placeholder="?")
    where = list(clauses)
    if filter_by.get("error_only"):
        where.append("error_code IS NOT NULL")
    return where, list(clauses.values())


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
    path.chmod(0o600)
    return store.connection


def _secure_directory(path: Path) -> None:
    path.mkdir(parents=True, mode=0o700, exist_ok=True)
    path.chmod(0o700)


def _checkpoint_sqlite(path: Path) -> None:
    connection = _connect(path)
    try:
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        connection.close()


def _remove_sqlite_sidecars(path: Path) -> None:
    for suffix in ("-wal", "-shm"):
        path.with_name(f"{path.name}{suffix}").unlink(missing_ok=True)


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


def _daily_file_date(name: str) -> date | None:
    date_text = name.removesuffix(".gz").removesuffix(".db")
    try:
        return datetime.strptime(date_text, "%Y-%m-%d").date()
    except ValueError:
        return None


def _json(value: Any) -> str | None:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=True)


def _json_load(raw: Any, default: Any) -> Any:
    if raw in (None, ""):
        return default
    try:
        return json.loads(str(raw))
    except json.JSONDecodeError:
        return default


_FILTER_KEYS = ("trace_id", "from_agent", "to_agent", "method", "status", "error_code")
