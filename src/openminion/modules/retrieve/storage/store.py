from __future__ import annotations

import sqlite3
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from openminion.modules.retrieve.interfaces import RETRIEVE_STORAGE_INTERFACE_VERSION
from openminion.modules.storage.runtime.module_store import (
    BaseModuleSQLiteStore,
    BaseModuleStore,
)
from openminion.modules.storage.errors import StorageDomainError
from openminion.modules.storage.record_store import RecordStore
from .base import RetrieveStore
from .migrations import list_migrations
from openminion.modules.storage.migrations.metadata import (
    ensure_module_metadata_for_package,
)


_STORAGE_LOGGER = logging.getLogger("openminion.storage")


def _is_read_sql(sql: str) -> bool:
    normalized = str(sql or "").lstrip().lower()
    return normalized.startswith("select") or normalized.startswith("with")


def _raise_storage_domain_error(
    *,
    operation: str,
    code: str,
    message: str,
    error: Exception,
) -> None:
    text = str(error)
    _STORAGE_LOGGER.warning(
        "storage_domain_error_mapped module=retrieve operation=%s code=%s error=%s",
        operation,
        code,
        text,
    )
    raise StorageDomainError(
        code,
        message,
        {
            "operation": operation,
            "error": text,
        },
    ) from error


class _CompatCursor:
    def __init__(
        self,
        *,
        rows: list[dict[str, Any]] | None = None,
        rowcount: int = 0,
    ) -> None:
        self._rows = list(rows or [])
        self.rowcount = int(rowcount)

    def fetchone(self) -> dict[str, Any] | None:
        return self._rows[0] if self._rows else None

    def fetchall(self) -> list[dict[str, Any]]:
        return list(self._rows)


class _RetrieveStoreMixin:
    def _normalize_unit_ids(self, unit_ids: Sequence[str]) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()
        for unit_id in unit_ids:
            key = str(unit_id or "").strip()
            if not key or key in seen:
                continue
            seen.add(key)
            normalized.append(key)
        return normalized

    def _feedback_score_clamp_expr(self) -> str:
        return "MIN(1.0, MAX(0.0, COALESCE(feedback_score, 0.0)))"

    def _list_migrations(self) -> list[str]:
        return list_migrations()

    def _module_package(self) -> str:
        return __package__

    def close(self) -> None:
        BaseModuleStore.close(self)

    def get_feedback_state(self, unit_ids: Sequence[str]) -> dict[str, dict[str, Any]]:
        normalized = self._normalize_unit_ids(unit_ids)
        if not normalized:
            return {}
        sql = """
            SELECT unit_id, hit_count, last_hit_at, feedback_score
            FROM retrievectl_units
            WHERE unit_id IN ({placeholders})
        """.format(placeholders=",".join("?" for _ in normalized))
        rows = self.fetchall(sql, tuple(normalized))
        out: dict[str, dict[str, Any]] = {}
        for row in rows:
            unit_id = str(row["unit_id"])
            out[unit_id] = {
                "hit_count": int(row["hit_count"] or 0),
                "last_hit_at": str(row["last_hit_at"]) if row["last_hit_at"] else None,
                "feedback_score": float(row["feedback_score"] or 0.0),
            }
        return out

    def record_hits(self, unit_ids: Sequence[str], *, observed_at: str) -> int:
        normalized = self._normalize_unit_ids(unit_ids)
        updated = 0
        for unit_id in normalized:
            cursor = self.execute(
                f"""
                UPDATE retrievectl_units
                SET hit_count = COALESCE(hit_count, 0) + 1,
                    last_hit_at = ?,
                    feedback_score = {self._feedback_score_clamp_expr()}
                WHERE unit_id = ?
                """,
                (str(observed_at), unit_id),
            )
            updated += int(getattr(cursor, "rowcount", 0) or 0)
        self.commit()
        return updated

    def set_feedback_scores(self, scores_by_unit: Mapping[str, float]) -> int:
        updated = 0
        for unit_id, raw_score in scores_by_unit.items():
            key = str(unit_id or "").strip()
            if not key:
                continue
            score = min(1.0, max(0.0, float(raw_score)))
            cursor = self.execute(
                """
                UPDATE retrievectl_units
                SET feedback_score = ?
                WHERE unit_id = ?
                """,
                (score, key),
            )
            updated += int(getattr(cursor, "rowcount", 0) or 0)
        self.commit()
        return updated

    def apply_feedback_decay(
        self,
        *,
        halflife_days: int,
        min_feedback_score: float,
    ) -> int:
        halflife = max(1, int(halflife_days))
        floor = min(1.0, max(0.0, float(min_feedback_score)))
        now = datetime.now(timezone.utc)
        rows = self.fetchall(
            """
            SELECT unit_id, feedback_score, last_hit_at, created_at
            FROM retrievectl_units
            """
        )
        updated = 0
        for row in rows:
            unit_id = str(row["unit_id"])
            base_score = min(1.0, max(0.0, float(row["feedback_score"] or 0.0)))
            stamp_raw = str(row["last_hit_at"] or row["created_at"] or "").strip()
            age_days = 0.0
            if stamp_raw:
                normalized = (
                    stamp_raw[:-1] + "+00:00" if stamp_raw.endswith("Z") else stamp_raw
                )
                try:
                    stamp = datetime.fromisoformat(normalized)
                    if stamp.tzinfo is None:
                        stamp = stamp.replace(tzinfo=timezone.utc)
                    age_days = max(
                        (now - stamp.astimezone(timezone.utc)).total_seconds()
                        / 86400.0,
                        0.0,
                    )
                except ValueError:
                    age_days = 0.0
            decayed = base_score * (0.5 ** (age_days / float(halflife)))
            new_score = min(1.0, max(floor, decayed))
            if abs(new_score - base_score) < 1e-12:
                continue
            cursor = self.execute(
                """
                UPDATE retrievectl_units
                SET feedback_score = ?
                WHERE unit_id = ?
                """,
                (new_score, unit_id),
            )
            updated += int(getattr(cursor, "rowcount", 0) or 0)
        self.commit()
        return updated

    def _ensure_column(self, table: str, column: str, ddl_tail: str) -> None:
        columns = self._table_columns(table)
        if not columns or column in columns:
            return
        self.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl_tail}")

    def _feedback_score_ddl_tail(self) -> str:
        return "REAL NOT NULL DEFAULT 0.0"

    def _apply_phase0_contract_schema(self) -> None:
        # Phase 0 RMQ prereq: exact-scope identity + feedback defaults.
        self._ensure_column(
            "retrievectl_docs",
            "scope_key",
            "TEXT NOT NULL DEFAULT 'global:legacy'",
        )
        self._ensure_column(
            "retrievectl_units",
            "hit_count",
            "INTEGER NOT NULL DEFAULT 0",
        )
        self._ensure_column(
            "retrievectl_units",
            "last_hit_at",
            "TEXT",
        )
        self._ensure_column(
            "retrievectl_units",
            "feedback_score",
            self._feedback_score_ddl_tail(),
        )

        self.execute(
            """
            UPDATE retrievectl_docs
               SET scope_key = CASE
                   WHEN lower(trim(scope)) = 'session' THEN 'session:legacy'
                   WHEN lower(trim(scope)) = 'agent' THEN 'agent:legacy'
                   WHEN lower(trim(scope)) = 'project' THEN 'project:legacy'
                   WHEN lower(trim(scope)) = 'global' THEN 'global:legacy'
                   ELSE 'global:legacy'
               END
             WHERE scope_key IS NULL
                OR trim(scope_key) = ''
                OR (
                    lower(trim(scope_key)) = 'global:legacy'
                    AND lower(trim(scope)) IN ('session', 'agent', 'project')
                )
            """
        )
        self.execute(
            f"""
            UPDATE retrievectl_units
               SET hit_count = COALESCE(hit_count, 0),
                   feedback_score = {self._feedback_score_clamp_expr()}
             WHERE hit_count IS NULL
                OR feedback_score IS NULL
                OR feedback_score < 0.0
                OR feedback_score > 1.0
            """
        )

        self.execute(
            "CREATE INDEX IF NOT EXISTS idx_retrievectl_docs_scope_key ON retrievectl_docs(scope_key)"
        )
        self.execute(
            "CREATE INDEX IF NOT EXISTS idx_retrievectl_units_last_hit_at ON retrievectl_units(last_hit_at)"
        )


class SQLiteRetrieveStore(_RetrieveStoreMixin, BaseModuleSQLiteStore, RetrieveStore):
    """SQLite-backed storage adapter for RetrieveCtl."""

    contract_version = RETRIEVE_STORAGE_INTERFACE_VERSION

    def __init__(
        self,
        sqlite_path: str | Path,
        *,
        wal: bool = True,
        record_store: RecordStore | None = None,
    ) -> None:
        BaseModuleSQLiteStore.__init__(
            self,
            sqlite_path,
            wal=wal,
            record_store=record_store,
        )
        self.record_store = self._record_store
        self.connection = self._conn
        self.fts_enabled = False
        self.connection.row_factory = sqlite3.Row

    def _init_schema(self) -> None:
        self._conn.row_factory = sqlite3.Row

    def _reconcile_units_fts_title_schema(self) -> None:
        columns = self._table_columns("retrievectl_units_fts")
        if not columns or "title" in columns:
            return
        if self.fts_enabled:
            self.connection.execute(
                "DROP TABLE IF EXISTS retrievectl_units_fts_rebuild"
            )
            self.connection.execute(
                """
                CREATE VIRTUAL TABLE retrievectl_units_fts_rebuild
                USING fts5(unit_id UNINDEXED, title, fts_text, tags)
                """
            )
            self.connection.execute(
                """
                INSERT INTO retrievectl_units_fts_rebuild(unit_id, title, fts_text, tags)
                SELECT
                    f.unit_id,
                    COALESCE(d.title, ''),
                    f.fts_text,
                    f.tags
                FROM retrievectl_units_fts f
                LEFT JOIN retrievectl_units u ON u.unit_id = f.unit_id
                LEFT JOIN retrievectl_docs d ON d.doc_id = u.doc_id
                """
            )
            self.connection.execute("DROP TABLE retrievectl_units_fts")
            self.connection.execute(
                "ALTER TABLE retrievectl_units_fts_rebuild RENAME TO retrievectl_units_fts"
            )
            return
        self.connection.execute(
            """
            ALTER TABLE retrievectl_units_fts
            ADD COLUMN title TEXT NOT NULL DEFAULT ''
            """
        )
        self.connection.execute(
            """
            UPDATE retrievectl_units_fts
            SET title = COALESCE(
                (
                    SELECT d.title
                    FROM retrievectl_units u
                    JOIN retrievectl_docs d ON d.doc_id = u.doc_id
                    WHERE u.unit_id = retrievectl_units_fts.unit_id
                ),
                ''
            )
            """
        )

    def execute(
        self, sql: str, params: Sequence[Any] | tuple[Any, ...] = ()
    ) -> sqlite3.Cursor:
        try:
            return self.connection.execute(sql, tuple(params))
        except sqlite3.Error as exc:
            _raise_storage_domain_error(
                operation="execute",
                code="RETRIEVE_STORAGE_SQLITE_ERROR",
                message="Retrieve storage sqlite execution failed",
                error=exc,
            )

    def fetchone(
        self, sql: str, params: Sequence[Any] | tuple[Any, ...] = ()
    ) -> sqlite3.Row | None:
        return self.execute(sql, params).fetchone()

    def fetchall(
        self, sql: str, params: Sequence[Any] | tuple[Any, ...] = ()
    ) -> list[sqlite3.Row]:
        return self.execute(sql, params).fetchall()

    def commit(self) -> None:
        try:
            self.connection.commit()
        except sqlite3.Error as exc:
            _raise_storage_domain_error(
                operation="commit",
                code="RETRIEVE_STORAGE_SQLITE_ERROR",
                message="Retrieve storage sqlite commit failed",
                error=exc,
            )

    def ensure_schema(self) -> bool:
        self.connection.execute("PRAGMA foreign_keys=ON")
        self._create_retrieve_tables()
        self._create_retrieve_indexes()
        self._apply_phase0_contract_schema()
        self._ensure_units_fts_table()
        self._reconcile_units_fts_title_schema()
        self.connection.commit()
        ensure_module_metadata_for_package(
            self.connection,
            package=__package__,
            migrations=list_migrations(),
        )
        return self.fts_enabled

    def _create_retrieve_tables(self) -> None:
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS retrievectl_docs(
                doc_id TEXT PRIMARY KEY,
                source_type TEXT NOT NULL,
                source_ref TEXT NOT NULL,
                scope TEXT NOT NULL,
                tags_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                title TEXT NOT NULL DEFAULT '',
                corpus_id TEXT
            )
            """
        )
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS retrievectl_units(
                unit_id TEXT PRIMARY KEY,
                doc_id TEXT NOT NULL,
                unit_kind TEXT NOT NULL,
                level TEXT NULL,
                node_id TEXT NULL,
                text_ref TEXT NOT NULL,
                context_text_ref TEXT NULL,
                fts_text TEXT NOT NULL,
                created_at TEXT NOT NULL,
                token_count INTEGER NOT NULL DEFAULT 0,
                group_id TEXT NULL,
                offsets_json TEXT NOT NULL DEFAULT '{}',
                FOREIGN KEY(doc_id) REFERENCES retrievectl_docs(doc_id) ON DELETE CASCADE
            )
            """
        )
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS retrievectl_raptor_nodes(
                node_id TEXT PRIMARY KEY,
                doc_id TEXT NOT NULL,
                parent_id TEXT NULL,
                level_int INTEGER NOT NULL,
                summary_text_ref TEXT NOT NULL,
                leaf_unit_ids_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(doc_id) REFERENCES retrievectl_docs(doc_id) ON DELETE CASCADE
            )
            """
        )
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS retrievectl_runs(
                run_id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                query TEXT NOT NULL,
                strategy TEXT NOT NULL,
                k INTEGER NOT NULL,
                selected_unit_ids_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )

    def _create_retrieve_indexes(self) -> None:
        self.connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_retrievectl_docs_scope ON retrievectl_docs(scope)"
        )
        self.connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_retrievectl_docs_source_type ON retrievectl_docs(source_type)"
        )
        self.connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_retrievectl_units_doc_id ON retrievectl_units(doc_id)"
        )
        self.connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_retrievectl_units_kind ON retrievectl_units(unit_kind)"
        )
        self.connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_retrievectl_units_node ON retrievectl_units(node_id)"
        )
        self.connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_retrievectl_units_group ON retrievectl_units(group_id)"
        )

    def _ensure_units_fts_table(self) -> None:
        try:
            self.connection.execute(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS retrievectl_units_fts
                USING fts5(unit_id UNINDEXED, title, fts_text, tags)
                """
            )
            self.fts_enabled = True
        except sqlite3.OperationalError:
            self.connection.execute(
                """
                CREATE TABLE IF NOT EXISTS retrievectl_units_fts(
                    unit_id TEXT PRIMARY KEY,
                    title TEXT NOT NULL DEFAULT '',
                    fts_text TEXT NOT NULL,
                    tags TEXT NOT NULL
                )
                """
            )
            self.connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_retrievectl_units_fts_text ON retrievectl_units_fts(fts_text)"
            )
            self.fts_enabled = False

    def _table_columns(self, table: str) -> set[str]:
        rows = self.connection.execute(f"PRAGMA table_info({table})").fetchall()
        return {str(row["name"]) for row in rows}


class PostgresRetrieveStore(_RetrieveStoreMixin, BaseModuleStore, RetrieveStore):
    """Postgres-backed storage adapter for RetrieveCtl."""

    contract_version = RETRIEVE_STORAGE_INTERFACE_VERSION

    def __init__(self, *, record_store: RecordStore) -> None:
        self.connection = None
        self.fts_enabled = False
        BaseModuleStore.__init__(self, record_store=record_store)
        self.record_store = self._record_store

    def _init_schema(self) -> None:
        # Constructor metadata bootstrapping is handled by BaseModuleStore.
        return None

    def execute(
        self, sql: str, params: Sequence[Any] | tuple[Any, ...] = ()
    ) -> _CompatCursor:
        try:
            if _is_read_sql(sql):
                rows = self._record_store.query_dicts(sql, tuple(params))
                return _CompatCursor(rows=rows, rowcount=len(rows))
            count = self._record_store.execute_count(sql, tuple(params))
            return _CompatCursor(rowcount=count)
        except Exception as exc:  # noqa: BLE001
            _raise_storage_domain_error(
                operation="execute",
                code="RETRIEVE_STORAGE_BACKEND_ERROR",
                message="Retrieve storage backend execution failed",
                error=exc,
            )

    def fetchone(
        self, sql: str, params: Sequence[Any] | tuple[Any, ...] = ()
    ) -> dict[str, Any] | None:
        return self.execute(sql, params).fetchone()

    def fetchall(
        self, sql: str, params: Sequence[Any] | tuple[Any, ...] = ()
    ) -> list[dict[str, Any]]:
        return self.execute(sql, params).fetchall()

    def commit(self) -> None:
        if not self._record_store.in_transaction:
            return
        try:
            self._record_store.commit()
        except Exception as exc:  # noqa: BLE001
            _raise_storage_domain_error(
                operation="commit",
                code="RETRIEVE_STORAGE_BACKEND_ERROR",
                message="Retrieve storage backend commit failed",
                error=exc,
            )

    def ensure_schema(self) -> bool:
        for statement in (
            """
            CREATE TABLE IF NOT EXISTS retrievectl_docs(
                doc_id TEXT PRIMARY KEY,
                source_type TEXT NOT NULL,
                source_ref TEXT NOT NULL,
                scope TEXT NOT NULL,
                tags_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                title TEXT NOT NULL DEFAULT '',
                corpus_id TEXT
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS retrievectl_units(
                unit_id TEXT PRIMARY KEY,
                doc_id TEXT NOT NULL,
                unit_kind TEXT NOT NULL,
                level TEXT NULL,
                node_id TEXT NULL,
                text_ref TEXT NOT NULL,
                context_text_ref TEXT NULL,
                fts_text TEXT NOT NULL,
                created_at TEXT NOT NULL,
                token_count INTEGER NOT NULL DEFAULT 0,
                group_id TEXT NULL,
                offsets_json TEXT NOT NULL DEFAULT '{}',
                FOREIGN KEY(doc_id) REFERENCES retrievectl_docs(doc_id) ON DELETE CASCADE
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS retrievectl_raptor_nodes(
                node_id TEXT PRIMARY KEY,
                doc_id TEXT NOT NULL,
                parent_id TEXT NULL,
                level_int INTEGER NOT NULL,
                summary_text_ref TEXT NOT NULL,
                leaf_unit_ids_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(doc_id) REFERENCES retrievectl_docs(doc_id) ON DELETE CASCADE
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS retrievectl_runs(
                run_id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                query TEXT NOT NULL,
                strategy TEXT NOT NULL,
                k INTEGER NOT NULL,
                selected_unit_ids_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_retrievectl_docs_scope ON retrievectl_docs(scope)",
            "CREATE INDEX IF NOT EXISTS idx_retrievectl_docs_source_type ON retrievectl_docs(source_type)",
            "CREATE INDEX IF NOT EXISTS idx_retrievectl_units_doc_id ON retrievectl_units(doc_id)",
            "CREATE INDEX IF NOT EXISTS idx_retrievectl_units_kind ON retrievectl_units(unit_kind)",
            "CREATE INDEX IF NOT EXISTS idx_retrievectl_units_node ON retrievectl_units(node_id)",
            "CREATE INDEX IF NOT EXISTS idx_retrievectl_units_group ON retrievectl_units(group_id)",
        ):
            self._record_store.execute_count(statement)

        self._apply_phase0_contract_schema()
        self._record_store.execute_count(
            """
            CREATE TABLE IF NOT EXISTS retrievectl_units_fts(
                unit_id TEXT PRIMARY KEY,
                title TEXT NOT NULL DEFAULT '',
                fts_text TEXT NOT NULL,
                tags TEXT NOT NULL
            )
            """
        )
        self._record_store.execute_count(
            "CREATE INDEX IF NOT EXISTS idx_retrievectl_units_fts_text ON retrievectl_units_fts(fts_text)"
        )
        self.fts_enabled = False
        return self.fts_enabled

    def _feedback_score_ddl_tail(self) -> str:
        return "DOUBLE PRECISION NOT NULL DEFAULT 0.0"

    def _feedback_score_clamp_expr(self) -> str:
        return (
            "LEAST(1.0::double precision, "
            "GREATEST(0.0::double precision, COALESCE(feedback_score, 0.0)))"
        )

    def _table_columns(self, table: str) -> set[str]:
        rows = self._record_store.query_dicts(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = current_schema()
              AND table_name = ?
            """,
            (table,),
        )
        return {str(row["column_name"]) for row in rows}


__all__ = ("PostgresRetrieveStore", "SQLiteRetrieveStore")
