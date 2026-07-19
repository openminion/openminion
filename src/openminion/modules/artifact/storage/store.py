from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from openminion.modules.artifact.models import (
    AliasRecord,
    ArtifactMeta,
    ViewRecord,
    iso_now,
)
from openminion.modules.artifact.storage.base import ArtifactIndex
from openminion.modules.storage.record_store import RecordStore
from openminion.modules.storage.runtime.module_store import (
    BaseModuleSQLiteStore,
    BaseModuleStore,
)
from .migrations import list_migrations


def _create_artifact_schema(record_store: RecordStore) -> None:
    record_store.execute_count(
        """
        CREATE TABLE IF NOT EXISTS artifacts (
            sha256 TEXT PRIMARY KEY,
            size_bytes INTEGER NOT NULL,
            mime TEXT NOT NULL,
            created_at TEXT NOT NULL,
            original_name TEXT,
            original_path TEXT,
            label TEXT,
            session_id TEXT,
            trace_id TEXT,
            agent_id TEXT,
            encoding TEXT,
            deleted_at TEXT,
            meta_json TEXT
        )
        """
    )
    record_store.execute_count(
        """
        CREATE TABLE IF NOT EXISTS artifact_views (
            raw_sha256 TEXT NOT NULL,
            view_type TEXT NOT NULL,
            schema_version TEXT NOT NULL,
            policy_hash TEXT NOT NULL DEFAULT '',
            view_sha256 TEXT,
            view_path TEXT,
            mime TEXT NOT NULL,
            size_bytes INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            deleted_at TEXT,
            PRIMARY KEY (raw_sha256, view_type, schema_version, policy_hash)
        )
        """
    )
    record_store.execute_count(
        """
        CREATE TABLE IF NOT EXISTS aliases (
            alias TEXT PRIMARY KEY,
            sha256 TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            expires_at TEXT,
            meta_json TEXT
        )
        """
    )
    record_store.execute_count(
        """
        CREATE TABLE IF NOT EXISTS reference_edges (
            ref_id TEXT PRIMARY KEY,
            owner_type TEXT NOT NULL,
            owner_id TEXT NOT NULL,
            sha256 TEXT NOT NULL,
            created_at TEXT NOT NULL,
            deleted_at TEXT,
            UNIQUE(owner_type, owner_id, sha256)
        )
        """
    )
    for sql in (
        "CREATE INDEX IF NOT EXISTS idx_artifacts_created ON artifacts(created_at)",
        "CREATE INDEX IF NOT EXISTS idx_artifacts_size ON artifacts(size_bytes)",
        "CREATE INDEX IF NOT EXISTS idx_artifacts_deleted ON artifacts(deleted_at)",
        "CREATE INDEX IF NOT EXISTS idx_artifacts_session ON artifacts(session_id)",
        "CREATE INDEX IF NOT EXISTS idx_artifacts_trace ON artifacts(trace_id)",
        "CREATE INDEX IF NOT EXISTS idx_artifacts_agent ON artifacts(agent_id)",
        "CREATE INDEX IF NOT EXISTS idx_artifacts_name ON artifacts(original_name)",
        "CREATE INDEX IF NOT EXISTS idx_artifacts_label ON artifacts(label)",
        "CREATE INDEX IF NOT EXISTS idx_artifacts_mime ON artifacts(mime)",
        "CREATE INDEX IF NOT EXISTS idx_views_raw ON artifact_views(raw_sha256)",
        "CREATE INDEX IF NOT EXISTS idx_views_deleted ON artifact_views(deleted_at)",
        "CREATE INDEX IF NOT EXISTS idx_aliases_updated ON aliases(updated_at)",
        "CREATE INDEX IF NOT EXISTS idx_ref_edges_owner ON reference_edges(owner_type, owner_id, deleted_at)",
        "CREATE INDEX IF NOT EXISTS idx_ref_edges_sha ON reference_edges(sha256, deleted_at)",
    ):
        record_store.execute_count(sql)


class _ArtifactIndexMixin(ArtifactIndex):
    def _init_schema(self) -> None:
        with self._lock:
            _create_artifact_schema(self._record_store)

    def _list_migrations(self) -> list[str]:
        return list_migrations()

    def _module_package(self) -> str:
        return __package__

    def close(self) -> None:
        BaseModuleStore.close(self)

    def upsert_artifact(self, meta: ArtifactMeta) -> None:
        self._record_store.execute_count(
            """
            INSERT INTO artifacts(
                sha256, size_bytes, mime, created_at, original_name, original_path,
                label, session_id, trace_id, agent_id, encoding, deleted_at, meta_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(sha256) DO UPDATE SET
                original_name=COALESCE(artifacts.original_name, excluded.original_name),
                original_path=COALESCE(artifacts.original_path, excluded.original_path),
                label=COALESCE(artifacts.label, excluded.label),
                session_id=COALESCE(artifacts.session_id, excluded.session_id),
                trace_id=COALESCE(artifacts.trace_id, excluded.trace_id),
                agent_id=COALESCE(artifacts.agent_id, excluded.agent_id),
                encoding=COALESCE(artifacts.encoding, excluded.encoding),
                deleted_at=excluded.deleted_at,
                meta_json=COALESCE(artifacts.meta_json, excluded.meta_json)
            """,
            (
                meta.sha256,
                int(meta.size_bytes),
                meta.mime,
                meta.created_at,
                meta.original_name,
                meta.original_path,
                meta.label,
                meta.session_id,
                meta.trace_id,
                meta.agent_id,
                meta.encoding,
                meta.deleted_at,
                _json(meta.meta_json),
            ),
        )

    def get_artifact(
        self, sha256: str, *, include_deleted: bool = True
    ) -> ArtifactMeta | None:
        where = "sha256 = ?"
        params: list[Any] = [sha256]
        if not include_deleted:
            where += " AND deleted_at IS NULL"

        rows = self._record_store.query_dicts(
            f"""
            SELECT sha256, size_bytes, mime, created_at, original_name, original_path, label,
                   session_id, trace_id, agent_id, encoding, deleted_at, meta_json
            FROM artifacts
            WHERE {where}
            """,
            params,
        )
        return _artifact_from_row(rows[0]) if rows else None

    def list_recent(
        self, limit: int = 50, filters: dict | None = None
    ) -> list[ArtifactMeta]:
        filters = filters or {}
        sql, params = self._artifact_base_query(filters)
        sql += " ORDER BY a.created_at DESC LIMIT ?"
        params.append(_bounded_limit(limit))
        rows = self._record_store.query_dicts(sql, params)
        return [_artifact_from_row(row) for row in rows if row is not None]

    def search(
        self, query: str, filters: dict | None = None, limit: int = 100
    ) -> list[ArtifactMeta]:
        filters = filters or {}
        sql, params = self._artifact_base_query(filters)
        q = f"%{(query or '').strip()}%"
        sql += " AND (a.original_name LIKE ? OR a.label LIKE ? OR a.mime LIKE ? OR a.meta_json LIKE ? OR a.sha256 LIKE ?)"
        params.extend([q, q, q, q, q])
        sql += " ORDER BY a.created_at DESC LIMIT ?"
        params.append(_bounded_limit(limit, default=100, upper=5000))
        rows = self._record_store.query_dicts(sql, params)
        return [_artifact_from_row(row) for row in rows if row is not None]

    def largest(
        self, limit: int = 50, filters: dict | None = None
    ) -> list[ArtifactMeta]:
        filters = filters or {}
        sql, params = self._artifact_base_query(filters)
        sql += " ORDER BY a.size_bytes DESC, a.created_at DESC LIMIT ?"
        params.append(_bounded_limit(limit))
        rows = self._record_store.query_dicts(sql, params)
        return [_artifact_from_row(row) for row in rows if row is not None]

    def upsert_view(self, view: ViewRecord) -> None:
        self._record_store.execute_count(
            """
            INSERT INTO artifact_views(
                raw_sha256, view_type, schema_version, policy_hash,
                view_sha256, view_path, mime, size_bytes, created_at, deleted_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(raw_sha256, view_type, schema_version, policy_hash) DO UPDATE SET
                view_sha256=excluded.view_sha256,
                view_path=excluded.view_path,
                mime=excluded.mime,
                size_bytes=excluded.size_bytes,
                created_at=excluded.created_at,
                deleted_at=excluded.deleted_at
            """,
            (
                view.raw_sha256,
                view.view_type,
                view.schema_version,
                view.policy_hash,
                view.view_sha256,
                view.view_path,
                view.mime,
                int(view.size_bytes),
                view.created_at,
                view.deleted_at,
            ),
        )

    def get_view(
        self,
        raw_sha256: str,
        view_type: str,
        schema_version: str,
        policy_hash: str = "",
        *,
        include_deleted: bool = True,
    ) -> ViewRecord | None:
        where = "raw_sha256 = ? AND view_type = ? AND schema_version = ? AND policy_hash = ?"
        params: list[Any] = [raw_sha256, view_type, schema_version, policy_hash]
        if not include_deleted:
            where += " AND deleted_at IS NULL"

        rows = self._record_store.query_dicts(
            f"""
            SELECT raw_sha256, view_type, schema_version, policy_hash,
                   view_sha256, view_path, mime, size_bytes, created_at, deleted_at
            FROM artifact_views
            WHERE {where}
            """,
            params,
        )
        return _view_from_row(rows[0]) if rows else None

    def list_views(
        self, raw_sha256: str, *, include_deleted: bool = False
    ) -> list[ViewRecord]:
        where = "raw_sha256 = ?"
        params: list[Any] = [raw_sha256]
        if not include_deleted:
            where += " AND deleted_at IS NULL"
        rows = self._record_store.query_dicts(
            f"""
            SELECT raw_sha256, view_type, schema_version, policy_hash,
                   view_sha256, view_path, mime, size_bytes, created_at, deleted_at
            FROM artifact_views
            WHERE {where}
            ORDER BY view_type ASC
            """,
            params,
        )
        return [_view_from_row(row) for row in rows if row is not None]

    def alias_set(
        self,
        alias: str,
        sha256: str,
        *,
        expires_at: str | None = None,
        meta_json: dict | None = None,
    ) -> None:
        updated_at = iso_now()
        self._record_store.execute_count(
            """
            INSERT INTO aliases(alias, sha256, updated_at, expires_at, meta_json)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(alias) DO UPDATE SET
                sha256=excluded.sha256,
                updated_at=excluded.updated_at,
                expires_at=excluded.expires_at,
                meta_json=excluded.meta_json
            """,
            (alias, sha256, updated_at, expires_at, _json(meta_json)),
        )

    def alias_resolve(self, alias: str) -> AliasRecord | None:
        now = iso_now()
        rows = self._record_store.query_dicts(
            """
            SELECT alias, sha256, updated_at, expires_at, meta_json
            FROM aliases
            WHERE alias = ?
              AND (expires_at IS NULL OR expires_at > ?)
            """,
            (alias, now),
        )
        return _alias_from_row(rows[0]) if rows else None

    def alias_list(self, prefix: str | None = None) -> list[AliasRecord]:
        now = iso_now()
        where = "(expires_at IS NULL OR expires_at > ?)"
        params: list[Any] = [now]
        if prefix:
            where += " AND alias LIKE ?"
            params.append(f"{prefix}%")

        rows = self._record_store.query_dicts(
            f"""
            SELECT alias, sha256, updated_at, expires_at, meta_json
            FROM aliases
            WHERE {where}
            ORDER BY alias ASC
            """,
            params,
        )
        return [_alias_from_row(row) for row in rows if row is not None]

    def alias_delete(self, alias: str) -> None:
        self._record_store.execute_count(
            "DELETE FROM aliases WHERE alias = ?", (alias,)
        )

    def add_reference(self, owner_type: str, owner_id: str, sha256: str) -> None:
        created_at = iso_now()
        self._record_store.execute_count(
            """
            INSERT INTO reference_edges(ref_id, owner_type, owner_id, sha256, created_at, deleted_at)
            VALUES (?, ?, ?, ?, ?, NULL)
            ON CONFLICT(owner_type, owner_id, sha256) DO UPDATE SET
                deleted_at=NULL,
                created_at=excluded.created_at
            """,
            (str(uuid.uuid4()), owner_type, owner_id, sha256, created_at),
        )

    def remove_reference(self, owner_type: str, owner_id: str, sha256: str) -> None:
        deleted_at = iso_now()
        self._record_store.execute_count(
            """
            UPDATE reference_edges
            SET deleted_at = ?
            WHERE owner_type = ? AND owner_id = ? AND sha256 = ? AND deleted_at IS NULL
            """,
            (deleted_at, owner_type, owner_id, sha256),
        )

    def active_reference_shas(self) -> set[str]:
        rows = self._record_store.query_dicts(
            """
            SELECT DISTINCT sha256
            FROM reference_edges
            WHERE deleted_at IS NULL
            """
        )
        return {str(row["sha256"]) for row in rows}

    def active_alias_shas(self) -> set[str]:
        now = iso_now()
        rows = self._record_store.query_dicts(
            """
            SELECT DISTINCT sha256
            FROM aliases
            WHERE expires_at IS NULL OR expires_at > ?
            """,
            (now,),
        )
        return {str(row["sha256"]) for row in rows}

    def recent_artifact_shas(self, keep_days: int) -> set[str]:
        cutoff_date = (
            (datetime.now(timezone.utc) - timedelta(days=max(0, int(keep_days))))
            .date()
            .isoformat()
        )
        rows = self._record_store.query_dicts(
            """
            SELECT sha256
            FROM artifacts
            WHERE deleted_at IS NULL
              AND date(created_at) >= ?
            """,
            (cutoff_date,),
        )
        return {str(row["sha256"]) for row in rows}

    def eligible_for_gc(self, older_than_days: int, protected: set[str]) -> list[str]:
        cutoff_date = (
            (datetime.now(timezone.utc) - timedelta(days=max(0, int(older_than_days))))
            .date()
            .isoformat()
        )
        rows = self._record_store.query_dicts(
            """
            SELECT sha256
            FROM artifacts
            WHERE deleted_at IS NULL
              AND date(created_at) < ?
            """,
            (cutoff_date,),
        )
        out: list[str] = []
        for row in rows:
            sha = str(row["sha256"])
            if sha in protected:
                continue
            out.append(sha)
        return out

    def soft_delete_artifacts(self, shas: Iterable[str], deleted_at: str) -> int:
        rows = list({str(item) for item in shas if str(item).strip()})
        if not rows:
            return 0
        count = 0
        with self._record_store.transaction():
            for sha in rows:
                count += self._record_store.execute_count(
                    """
                    UPDATE artifacts
                    SET deleted_at = ?
                    WHERE sha256 = ? AND deleted_at IS NULL
                    """,
                    (deleted_at, sha),
                )
        return count

    def soft_delete_views_for_raw(self, raw_sha256: str, deleted_at: str) -> int:
        return self._record_store.execute_count(
            """
            UPDATE artifact_views
            SET deleted_at = ?
            WHERE raw_sha256 = ? AND deleted_at IS NULL
            """,
            (deleted_at, raw_sha256),
        )

    def purgeable_views(self, grace_days: int) -> list[ViewRecord]:
        cutoff = (
            datetime.now(timezone.utc) - timedelta(days=max(0, int(grace_days)))
        ).isoformat()
        rows = self._record_store.query_dicts(
            """
            SELECT raw_sha256, view_type, schema_version, policy_hash,
                   view_sha256, view_path, mime, size_bytes, created_at, deleted_at
            FROM artifact_views
            WHERE deleted_at IS NOT NULL
              AND deleted_at <= ?
            """,
            (cutoff,),
        )
        return [_view_from_row(row) for row in rows if row is not None]

    def hard_delete_artifact(self, sha256: str) -> int:
        return self._record_store.execute_count(
            """
            DELETE FROM artifacts
            WHERE sha256 = ? AND deleted_at IS NOT NULL
            """,
            (sha256,),
        )

    def hard_delete_views_for_raw(self, raw_sha256: str) -> int:
        return self._record_store.execute_count(
            """
            DELETE FROM artifact_views
            WHERE raw_sha256 = ? AND deleted_at IS NOT NULL
            """,
            (raw_sha256,),
        )

    def purgeable_artifacts(self, grace_days: int) -> list[ArtifactMeta]:
        cutoff = (
            datetime.now(timezone.utc) - timedelta(days=max(0, int(grace_days)))
        ).isoformat()
        rows = self._record_store.query_dicts(
            """
            SELECT sha256, size_bytes, mime, created_at, original_name, original_path, label,
                   session_id, trace_id, agent_id, encoding, deleted_at, meta_json
            FROM artifacts
            WHERE deleted_at IS NOT NULL
              AND deleted_at <= ?
            """,
            (cutoff,),
        )
        return [_artifact_from_row(row) for row in rows if row is not None]

    def all_artifacts(self, *, include_deleted: bool = False) -> list[ArtifactMeta]:
        where = ""
        if not include_deleted:
            where = "WHERE deleted_at IS NULL"
        rows = self._record_store.query_dicts(
            f"""
            SELECT sha256, size_bytes, mime, created_at, original_name, original_path, label,
                   session_id, trace_id, agent_id, encoding, deleted_at, meta_json
            FROM artifacts
            {where}
            ORDER BY created_at ASC
            """
        )
        return [_artifact_from_row(row) for row in rows if row is not None]

    def _artifact_base_query(self, filters: dict[str, Any]) -> tuple[str, list[Any]]:
        where = ["1=1"]
        params: list[Any] = []

        include_deleted = bool(filters.get("include_deleted", False))
        if not include_deleted:
            where.append("a.deleted_at IS NULL")

        for key in ("session_id", "trace_id", "agent_id", "mime"):
            value = filters.get(key)
            if value:
                where.append(f"a.{key} = ?")
                params.append(str(value))

        missing_view_type = filters.get("missing_view_type")
        if missing_view_type:
            where.append(
                "NOT EXISTS ("
                "SELECT 1 FROM artifact_views v "
                "WHERE v.raw_sha256 = a.sha256 "
                "AND v.view_type = ? "
                "AND v.deleted_at IS NULL"
                ")"
            )
            params.append(str(missing_view_type))

        sql = (
            "SELECT a.sha256, a.size_bytes, a.mime, a.created_at, a.original_name, a.original_path, a.label, "
            "a.session_id, a.trace_id, a.agent_id, a.encoding, a.deleted_at, a.meta_json "
            "FROM artifacts a WHERE " + " AND ".join(where)
        )
        return sql, params


class SQLiteArtifactIndex(_ArtifactIndexMixin, BaseModuleSQLiteStore):
    def __init__(
        self,
        sqlite_path: str | Path | None = None,
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


class PostgresArtifactIndex(_ArtifactIndexMixin, BaseModuleStore):
    def __init__(self, *, record_store: RecordStore) -> None:
        BaseModuleStore.__init__(self, record_store=record_store)


def _artifact_from_row(row: Mapping[str, Any] | None) -> ArtifactMeta | None:
    if row is None:
        return None
    return ArtifactMeta(
        sha256=str(row["sha256"]),
        size_bytes=int(row["size_bytes"]),
        mime=str(row["mime"]),
        created_at=str(row["created_at"]),
        original_name=(
            None if row["original_name"] is None else str(row["original_name"])
        ),
        original_path=(
            None if row["original_path"] is None else str(row["original_path"])
        ),
        label=(None if row["label"] is None else str(row["label"])),
        session_id=(None if row["session_id"] is None else str(row["session_id"])),
        trace_id=(None if row["trace_id"] is None else str(row["trace_id"])),
        agent_id=(None if row["agent_id"] is None else str(row["agent_id"])),
        encoding=(None if row["encoding"] is None else str(row["encoding"])),
        deleted_at=(None if row["deleted_at"] is None else str(row["deleted_at"])),
        meta_json=_json_load(row["meta_json"]),
    )


def _view_from_row(row: Mapping[str, Any] | None) -> ViewRecord | None:
    if row is None:
        return None
    return ViewRecord(
        raw_sha256=str(row["raw_sha256"]),
        view_type=str(row["view_type"]),
        schema_version=str(row["schema_version"]),
        policy_hash=str(row["policy_hash"]),
        view_sha256=(None if row["view_sha256"] is None else str(row["view_sha256"])),
        view_path=(None if row["view_path"] is None else str(row["view_path"])),
        mime=str(row["mime"]),
        size_bytes=int(row["size_bytes"]),
        created_at=str(row["created_at"]),
        deleted_at=(None if row["deleted_at"] is None else str(row["deleted_at"])),
    )


def _alias_from_row(row: Mapping[str, Any] | None) -> AliasRecord | None:
    if row is None:
        return None
    return AliasRecord(
        alias=str(row["alias"]),
        sha256=str(row["sha256"]),
        updated_at=str(row["updated_at"]),
        expires_at=(None if row["expires_at"] is None else str(row["expires_at"])),
        meta_json=_json_load(row["meta_json"]),
    )


def _json(value: Any) -> str | None:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=True, sort_keys=True)


def _json_load(raw: Any) -> dict | None:
    if raw in {None, ""}:
        return None
    try:
        parsed = json.loads(str(raw))
    except json.JSONDecodeError:
        return None
    if isinstance(parsed, dict):
        return parsed
    return {"value": parsed}


def _bounded_limit(value: int, default: int = 50, upper: int = 1000) -> int:
    try:
        n = int(value)
    except Exception:
        n = default
    return max(1, min(n, upper))
