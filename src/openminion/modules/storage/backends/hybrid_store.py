from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any
from uuid import uuid4

from openminion.base.time import utc_now_iso as iso_now
from openminion.modules.storage.interfaces import STORAGE_INTERFACE_VERSION
from openminion.modules.storage.models import EventRef, ReindexReport, RowRef
from openminion.modules.storage.record_store import RecordStore
from openminion.modules.storage.io import append_jsonl, normalize_namespace
from .blob_store import BlobStore

_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class HybridStore:
    """SQLite-first record writes with append-only JSONL fallback."""

    contract_version = STORAGE_INTERFACE_VERSION

    def __init__(
        self,
        *,
        record_store: RecordStore,
        blob_store: BlobStore,
        fallback_root: str | Path,
        default_namespace: str | None = None,
    ) -> None:
        self.record_store = record_store
        self.blob_store = blob_store
        self.fallback_root = Path(fallback_root).expanduser().resolve(strict=False)
        self.fallback_root.mkdir(parents=True, exist_ok=True)
        self.default_namespace = normalize_namespace(default_namespace)
        self._sqlite_ok = True
        self._last_error: str | None = None
        self._ensure_core_schema()

    def write_blob(self, *args: Any, **kwargs: Any):
        return self.blob_store.put_bytes(*args, **kwargs)

    def write_event(
        self, event: dict[str, Any], *, namespace: str | None = None
    ) -> EventRef:
        payload = dict(event)
        payload.setdefault("ts", iso_now())
        payload.setdefault("event_id", uuid4().hex)

        resolved_namespace = self._resolve_namespace(namespace, apply_default=True)
        namespace_value = self._namespace_storage_value(resolved_namespace)
        if resolved_namespace is not None:
            payload.setdefault("namespace", resolved_namespace)

        session_id = payload.get("session_id")
        event_id = str(payload["event_id"])

        try:
            self.record_store.execute_count(
                """
                INSERT INTO core_events(namespace, event_id, session_id, ts, agent_id, trace_id, event_type, payload_json, blob_refs_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    namespace_value,
                    event_id,
                    session_id,
                    str(payload.get("ts")),
                    payload.get("agent_id"),
                    payload.get("trace_id"),
                    str(payload.get("type", payload.get("event_type", ""))),
                    json.dumps(
                        payload.get("payload", {}),
                        sort_keys=True,
                        separators=(",", ":"),
                        ensure_ascii=True,
                    ),
                    json.dumps(
                        payload.get("blob_refs", payload.get("artifact_refs", [])),
                        sort_keys=True,
                        separators=(",", ":"),
                        ensure_ascii=True,
                    ),
                ),
            )
            self._sqlite_ok = True
            self._last_error = None
            return EventRef(
                event_id=event_id,
                session_id=(None if session_id is None else str(session_id)),
                persisted="sqlite",
                ts=str(payload["ts"]),
                namespace=resolved_namespace,
            )
        except Exception as exc:  # noqa: BLE001
            self._sqlite_ok = False
            self._last_error = str(exc)
            sidecar = self._event_sidecar_path(session_id, namespace=resolved_namespace)
            append_jsonl(sidecar, payload)
            return EventRef(
                event_id=event_id,
                session_id=(None if session_id is None else str(session_id)),
                persisted="sidecar",
                ts=str(payload["ts"]),
                sidecar_path=str(sidecar),
                namespace=resolved_namespace,
            )

    def write_row(
        self, table: str, row: dict[str, Any], *, namespace: str | None = None
    ) -> RowRef:
        normalized_table = str(table or "").strip()
        if not _SAFE_IDENTIFIER.match(normalized_table):
            raise ValueError(f"unsafe table name: {table}")

        payload = dict(row)
        payload.setdefault("ts", iso_now())
        row_id = str(payload.get("row_id") or payload.get("id") or uuid4().hex)
        payload.setdefault("row_id", row_id)

        resolved_namespace = self._resolve_namespace(namespace, apply_default=True)

        try:
            self._insert_dynamic(normalized_table, payload)
            self._sqlite_ok = True
            self._last_error = None
            return RowRef(
                table=normalized_table,
                row_id=row_id,
                persisted="sqlite",
                ts=str(payload["ts"]),
                namespace=resolved_namespace,
            )
        except Exception as exc:  # noqa: BLE001
            self._sqlite_ok = False
            self._last_error = str(exc)
            sidecar = self._row_sidecar_path(
                normalized_table, namespace=resolved_namespace
            )
            append_jsonl(
                sidecar,
                {
                    "namespace": resolved_namespace,
                    "table": normalized_table,
                    "row": payload,
                    "ts": payload["ts"],
                    "row_id": row_id,
                },
            )
            return RowRef(
                table=normalized_table,
                row_id=row_id,
                persisted="sidecar",
                ts=str(payload["ts"]),
                sidecar_path=str(sidecar),
                namespace=resolved_namespace,
            )

    def reindex(
        self,
        from_fs: bool = True,
        since_ts: str | None = None,
        *,
        dry_run: bool = False,
        archive_replayed: bool = False,
        archive_root: str | Path | None = None,
        namespace: str | None = None,
    ) -> ReindexReport:
        report = ReindexReport()
        report.dry_run = bool(dry_run)
        if not from_fs:
            return report

        resolved_namespace = self._resolve_namespace(namespace, apply_default=True)
        files = self._sidecar_files(namespace=resolved_namespace)
        report.scanned_files = len(files)

        archive_base = (
            Path(archive_root).expanduser().resolve(strict=False)
            if archive_root
            else self.fallback_root / "archive"
        )

        tx_started = False
        if dry_run and not self.record_store.in_transaction:
            self.record_store.begin()
            tx_started = True

        try:
            for file_path, file_namespace in files:
                file_report = {
                    "path": str(file_path),
                    "namespace": file_namespace,
                    "scanned_lines": 0,
                    "inserted": 0,
                    "duplicates": 0,
                    "failed": 0,
                    "skipped": 0,
                }
                report.file_reports.append(file_report)
                line_no = 0
                try:
                    with file_path.open("r", encoding="utf-8") as fh:
                        for line in fh:
                            line_no += 1
                            file_report["scanned_lines"] += 1
                            report.scanned_lines += 1
                            if not line.strip():
                                continue
                            if self._already_ingested(
                                file_path, line_no, namespace=file_namespace
                            ):
                                report.duplicates += 1
                                file_report["duplicates"] += 1
                                continue
                            try:
                                payload = json.loads(line)
                            except json.JSONDecodeError:
                                report.failed += 1
                                file_report["failed"] += 1
                                report.errors.append(
                                    f"{file_path}:{line_no}: invalid json"
                                )
                                continue

                            ts_value = str(payload.get("ts", ""))
                            if since_ts and ts_value and ts_value < since_ts:
                                report.skipped += 1
                                file_report["skipped"] += 1
                                if not dry_run:
                                    self._mark_ingested(
                                        file_path,
                                        line_no,
                                        namespace=file_namespace,
                                        event_id=None,
                                        table_name=None,
                                        row_hash=_stable_hash(payload),
                                    )
                                continue

                            try:
                                if self._is_event_sidecar(file_path):
                                    inserted = self._replay_event(
                                        payload, namespace=file_namespace
                                    )
                                else:
                                    inserted = self._replay_sidecar_row(
                                        payload, namespace=file_namespace
                                    )
                                report.inserted += int(inserted)
                                file_report["inserted"] += int(inserted)
                                if not inserted:
                                    report.duplicates += 1
                                    file_report["duplicates"] += 1
                                if not dry_run:
                                    self._mark_ingested(
                                        file_path,
                                        line_no,
                                        namespace=file_namespace,
                                        event_id=payload.get("event_id"),
                                        table_name=payload.get("table"),
                                        row_hash=_stable_hash(payload),
                                    )
                            except Exception as exc:  # noqa: BLE001
                                report.failed += 1
                                file_report["failed"] += 1
                                report.errors.append(f"{file_path}:{line_no}: {exc}")
                except OSError as exc:
                    report.failed += 1
                    file_report["failed"] += 1
                    report.errors.append(f"{file_path}: {exc}")
                    continue

                if archive_replayed and not dry_run:
                    archived_path = self._archive_file(file_path, archive_base)
                    if archived_path:
                        report.archived_files.append(str(archived_path))
        finally:
            if dry_run and tx_started:
                try:
                    self.record_store.rollback()
                except Exception:  # noqa: BLE001
                    pass

        return report

    def list_events(
        self, session_id: str, limit: int = 50, *, namespace: str | None = None
    ) -> list[dict[str, Any]]:
        normalized = str(session_id or "").strip()
        if not normalized:
            return []

        resolved_namespace = self._resolve_namespace(namespace, apply_default=True)
        params: tuple[Any, ...]
        if resolved_namespace is None:
            sql = """
                SELECT namespace, event_id, session_id, ts, agent_id, trace_id, event_type, payload_json, blob_refs_json
                FROM core_events
                WHERE session_id = ?
                ORDER BY ts DESC
                LIMIT ?
            """
            params = (normalized, int(max(1, limit)))
        else:
            sql = """
                SELECT namespace, event_id, session_id, ts, agent_id, trace_id, event_type, payload_json, blob_refs_json
                FROM core_events
                WHERE namespace = ? AND session_id = ?
                ORDER BY ts DESC
                LIMIT ?
            """
            params = (
                self._namespace_storage_value(resolved_namespace),
                normalized,
                int(max(1, limit)),
            )

        rows = self.record_store.query_dicts(sql, params)
        events: list[dict[str, Any]] = []
        for row in rows:
            try:
                payload = json.loads(row["payload_json"])
            except Exception:  # noqa: BLE001
                payload = row["payload_json"]
            try:
                blob_refs = json.loads(row["blob_refs_json"])
            except Exception:  # noqa: BLE001
                blob_refs = row["blob_refs_json"]
            events.append(
                {
                    "namespace": self._namespace_from_storage(row["namespace"]),
                    "event_id": row["event_id"],
                    "session_id": row["session_id"],
                    "ts": row["ts"],
                    "event_type": row["event_type"],
                    "agent_id": row["agent_id"],
                    "trace_id": row["trace_id"],
                    "payload": payload,
                    "blob_refs": blob_refs,
                }
            )
        return events

    def status(self) -> dict[str, Any]:
        health = self.record_store.healthcheck()
        if not bool(health.get("ok", False)):
            self._sqlite_ok = False
            self._last_error = str(health.get("error"))
        return {
            "sqlite_ok": bool(self._sqlite_ok and health.get("ok", False)),
            "fallback_mode": not bool(self._sqlite_ok and health.get("ok", False)),
            "last_error": self._last_error,
        }

    def gc(self, policy: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.blob_store.gc(policy or {})

    def _insert_dynamic(self, table: str, row: dict[str, Any]) -> None:
        columns = list(row.keys())
        if not columns:
            raise ValueError("row must include at least one column")
        for column in columns:
            if not _SAFE_IDENTIFIER.match(column):
                raise ValueError(f"unsafe column name: {column}")
        payload = {column: self._value_for_sql(row[column]) for column in columns}
        explicit_id = payload.get("id")
        if explicit_id is not None:
            self.record_store.delete_rows(table, {"id": explicit_id})
        self.record_store.insert(table, payload)

    def _value_for_sql(self, value: Any) -> Any:
        if isinstance(value, (dict, list)):
            return json.dumps(
                value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
            )
        return value

    def _event_sidecar_path(self, session_id: Any, *, namespace: str | None) -> Path:
        sid = str(session_id or "unknown")
        return self._module_root(namespace) / "sessions" / sid / "events.jsonl"

    def _row_sidecar_path(self, table: str, *, namespace: str | None) -> Path:
        return self._module_root(namespace) / "sidecars" / f"{table}.jsonl"

    def _module_root(self, namespace: str | None) -> Path:
        if namespace is None:
            return self.fallback_root
        return self.fallback_root / "modules" / namespace

    def _sidecar_files(self, *, namespace: str | None) -> list[tuple[Path, str | None]]:
        files: list[tuple[Path, str | None]] = []
        if namespace is None:
            files.extend(
                (path, None)
                for path in sorted(self.fallback_root.glob("sessions/*/events.jsonl"))
            )
            files.extend(
                (path, None)
                for path in sorted((self.fallback_root / "sidecars").glob("*.jsonl"))
            )
            modules_root = self.fallback_root / "modules"
            if modules_root.exists():
                for module_dir in sorted(
                    path for path in modules_root.iterdir() if path.is_dir()
                ):
                    try:
                        module_namespace = normalize_namespace(module_dir.name)
                    except ValueError:
                        continue
                    files.extend(
                        (path, module_namespace)
                        for path in sorted(module_dir.glob("sessions/*/events.jsonl"))
                    )
                    files.extend(
                        (path, module_namespace)
                        for path in sorted((module_dir / "sidecars").glob("*.jsonl"))
                    )
            return files

        module_root = self.fallback_root / "modules" / namespace
        files.extend(
            (path, namespace)
            for path in sorted(module_root.glob("sessions/*/events.jsonl"))
        )
        files.extend(
            (path, namespace)
            for path in sorted((module_root / "sidecars").glob("*.jsonl"))
        )
        return files

    def _is_event_sidecar(self, file_path: Path) -> bool:
        return file_path.name == "events.jsonl" and "sessions" in file_path.parts

    def _ensure_core_schema(self) -> None:
        self.record_store.execute_count(
            """
            CREATE TABLE IF NOT EXISTS core_events (
                namespace TEXT NOT NULL DEFAULT '',
                event_id TEXT PRIMARY KEY,
                session_id TEXT,
                ts TEXT NOT NULL,
                agent_id TEXT,
                trace_id TEXT,
                event_type TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                blob_refs_json TEXT NOT NULL
            )
            """
        )
        self._ensure_column("core_events", "namespace", "TEXT NOT NULL DEFAULT ''")
        self.record_store.execute_count(
            """
            CREATE INDEX IF NOT EXISTS idx_core_events_session_ts
            ON core_events(session_id, ts)
            """
        )
        self.record_store.execute_count(
            """
            CREATE INDEX IF NOT EXISTS idx_core_events_trace_ts
            ON core_events(trace_id, ts)
            """
        )
        self.record_store.execute_count(
            """
            CREATE INDEX IF NOT EXISTS idx_core_events_namespace_session_ts
            ON core_events(namespace, session_id, ts)
            """
        )

        self.record_store.execute_count(
            """
            CREATE TABLE IF NOT EXISTS core_sidecar_rows (
                row_hash TEXT PRIMARY KEY,
                namespace TEXT NOT NULL DEFAULT '',
                table_name TEXT NOT NULL,
                ts TEXT NOT NULL,
                row_json TEXT NOT NULL
            )
            """
        )
        self._ensure_column(
            "core_sidecar_rows", "namespace", "TEXT NOT NULL DEFAULT ''"
        )
        self.record_store.execute_count(
            """
            CREATE INDEX IF NOT EXISTS idx_core_sidecar_rows_table_ts
            ON core_sidecar_rows(table_name, ts)
            """
        )
        self.record_store.execute_count(
            """
            CREATE INDEX IF NOT EXISTS idx_core_sidecar_rows_namespace_table_ts
            ON core_sidecar_rows(namespace, table_name, ts)
            """
        )

        self.record_store.execute_count(
            """
            CREATE TABLE IF NOT EXISTS sidecar_ingest_log (
                source_path TEXT NOT NULL,
                line_no INTEGER NOT NULL,
                namespace TEXT NOT NULL DEFAULT '',
                event_id TEXT,
                table_name TEXT,
                row_hash TEXT NOT NULL,
                ingested_at TEXT NOT NULL,
                PRIMARY KEY (source_path, line_no)
            )
            """
        )
        self._ensure_column(
            "sidecar_ingest_log", "namespace", "TEXT NOT NULL DEFAULT ''"
        )
        self.record_store.execute_count(
            """
            CREATE INDEX IF NOT EXISTS idx_sidecar_ingest_event
            ON sidecar_ingest_log(event_id)
            """
        )
        self.record_store.execute_count(
            """
            CREATE INDEX IF NOT EXISTS idx_sidecar_ingest_table
            ON sidecar_ingest_log(table_name, ingested_at)
            """
        )
        self.record_store.execute_count(
            """
            CREATE INDEX IF NOT EXISTS idx_sidecar_ingest_namespace_path
            ON sidecar_ingest_log(namespace, source_path, ingested_at)
            """
        )

    def _ensure_column(self, table: str, column: str, ddl_tail: str) -> None:
        if not _SAFE_IDENTIFIER.match(table):
            raise ValueError(f"unsafe table name: {table}")
        if not _SAFE_IDENTIFIER.match(column):
            raise ValueError(f"unsafe column name: {column}")
        current = self._table_columns(table)
        if column in current:
            return
        self.record_store.execute_count(
            f"ALTER TABLE {table} ADD COLUMN {column} {ddl_tail}"
        )

    def _table_columns(self, table: str) -> set[str]:
        if self.record_store.capabilities().get("raw_sql", False):
            rows = self.record_store.query_dicts(f"PRAGMA table_info({table})")
            return {str(row["name"]) for row in rows}
        rows = self.record_store.query_dicts(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = current_schema()
              AND table_name = ?
            """,
            (table,),
        )
        return {str(row["column_name"]) for row in rows}

    def _already_ingested(
        self, source_path: Path, line_no: int, *, namespace: str | None
    ) -> bool:
        rows = self.record_store.query_dicts(
            """
            SELECT 1 FROM sidecar_ingest_log
            WHERE source_path = ? AND line_no = ? AND namespace = ?
            LIMIT 1
            """,
            (str(source_path), int(line_no), self._namespace_storage_value(namespace)),
        )
        return bool(rows)

    def _mark_ingested(
        self,
        source_path: Path,
        line_no: int,
        *,
        namespace: str | None,
        event_id: Any,
        table_name: Any,
        row_hash: str,
    ) -> None:
        self.record_store.execute_count(
            """
            INSERT INTO sidecar_ingest_log(
                source_path, line_no, namespace, event_id, table_name, row_hash, ingested_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source_path, line_no) DO NOTHING
            """,
            (
                str(source_path),
                int(line_no),
                self._namespace_storage_value(namespace),
                None if event_id is None else str(event_id),
                None if table_name is None else str(table_name),
                row_hash,
                iso_now(),
            ),
        )

    def _archive_file(self, file_path: Path, archive_root: Path) -> Path | None:
        try:
            rel = file_path.relative_to(self.fallback_root)
        except ValueError:
            rel = Path(file_path.name)
        target = archive_root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            file_path.replace(target)
        except OSError:
            return None
        return target

    def _replay_event(self, payload: dict[str, Any], *, namespace: str | None) -> bool:
        effective_namespace = self._effective_namespace(namespace, payload)
        namespace_value = self._namespace_storage_value(effective_namespace)
        event_id = str(payload.get("event_id") or uuid4().hex)
        inserted = self.record_store.execute_count(
            """
            INSERT INTO core_events(namespace, event_id, session_id, ts, agent_id, trace_id, event_type, payload_json, blob_refs_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(event_id) DO NOTHING
            """,
            (
                namespace_value,
                event_id,
                payload.get("session_id"),
                str(payload.get("ts", iso_now())),
                payload.get("agent_id"),
                payload.get("trace_id"),
                str(payload.get("type", payload.get("event_type", ""))),
                json.dumps(
                    payload.get("payload", {}),
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=True,
                ),
                json.dumps(
                    payload.get("blob_refs", payload.get("artifact_refs", [])),
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=True,
                ),
            ),
        )
        return inserted > 0

    def _replay_sidecar_row(
        self, payload: dict[str, Any], *, namespace: str | None
    ) -> bool:
        effective_namespace = self._effective_namespace(namespace, payload)
        table_name = str(payload.get("table", "")).strip()
        row = payload.get("row") if isinstance(payload.get("row"), dict) else {}
        if table_name and _SAFE_IDENTIFIER.match(table_name):
            try:
                self._insert_dynamic(table_name, row)
                return True
            except Exception:
                # Preserve sidecar row even if target table is unavailable.
                pass

        row_hash = _stable_hash({"namespace": effective_namespace, "payload": payload})
        inserted = self.record_store.execute_count(
            """
            INSERT INTO core_sidecar_rows(row_hash, namespace, table_name, ts, row_json)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(row_hash) DO NOTHING
            """,
            (
                row_hash,
                self._namespace_storage_value(effective_namespace),
                table_name or "unknown",
                str(payload.get("ts", iso_now())),
                json.dumps(
                    payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True
                ),
            ),
        )
        return inserted > 0

    def _effective_namespace(
        self, namespace: str | None, payload: dict[str, Any]
    ) -> str | None:
        if namespace is not None:
            return namespace
        raw_namespace = payload.get("namespace")
        try:
            return normalize_namespace(raw_namespace)
        except ValueError:
            return None

    def _resolve_namespace(
        self, namespace: str | None, *, apply_default: bool
    ) -> str | None:
        if namespace is None:
            if apply_default:
                return self.default_namespace
            return None
        return normalize_namespace(namespace)

    def _namespace_storage_value(self, namespace: str | None) -> str:
        return "" if namespace is None else namespace

    def _namespace_from_storage(self, namespace: Any) -> str | None:
        text = str(namespace or "").strip()
        if not text:
            return None
        try:
            return normalize_namespace(text)
        except ValueError:
            return text


def _stable_hash(value: Any) -> str:
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
