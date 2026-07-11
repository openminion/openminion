"""SQLite memory store."""

from __future__ import annotations

from dataclasses import replace
import datetime
import json
import sqlite3
import threading
import uuid
from pathlib import Path
from typing import Any

from openminion.modules.artifact.refs import (
    add_reference_edges,
    create_default_artifactctl,
    normalize_artifact_ref_targets,
    remove_reference_edges,
)
from ..base import (
    MemoryStore,
    ListQueryOptions,
    record_matches_namespaces,
)
from ..migrations import list_migrations
from ..postgres.sql import _clamp01
from openminion.modules.storage.migrations.metadata import (
    ensure_module_metadata_for_package,
)
from ...errors import (
    InvalidArgumentError,
    NotFoundError,
)
from ...models import (
    MemoryRelation,
    MemoryRelationType,
    MemoryRecord,
    MemoryTierTransition,
    MemoryType,
)
from .migrations import run_migrations
from .queries import (
    CREATE_MEMORY_RELATIONS_SOURCE_INDEX,
    CREATE_MEMORY_RELATIONS_TABLE,
    CREATE_MEMORY_RELATIONS_TARGET_INDEX,
)
from .row import (
    _create_sqlite_candidate_from_row as _decode_candidate_row,
    _create_sqlite_record_from_row as _decode_record_row,
    _create_sqlite_relation_from_row as _decode_relation_row,
    _create_sqlite_tier_transition_from_row as _decode_tier_transition_row,
    _decode_sqlite_evidence_ref_values as _decode_row_evidence_ref_values,
)
from .records import (
    apply_supersession as _apply_supersession_impl,
    get_required_record as _get_required_record_impl,
)
from .candidates import (
    candidate_delete as _candidate_delete_workflow,
    candidate_get as _candidate_get_workflow,
    candidate_list as _candidate_list_workflow,
    candidate_put as _candidate_put_workflow,
    candidate_update as _candidate_update_workflow,
)
from .search import (
    retrieve_by_entities as _retrieve_by_entities_query,
    search as _search_query,
)
from .write import (
    apply_outcome_feedback as _apply_outcome_feedback_impl,
    history as _history_impl,
    promote_candidate as _promote_candidate_impl,
    supersede_by_contradiction as _supersede_by_contradiction_impl,
    upsert as _upsert_impl,
)
from openminion.modules.storage.runtime.sqlite import connect_database

_ARTIFACTCTL_UNSET = object()


class SQLiteMemoryStore(MemoryStore):
    """SQLite implementation of MemoryStore."""

    _decode_evidence_ref_values = staticmethod(_decode_row_evidence_ref_values)
    _create_record_from_row = staticmethod(_decode_record_row)
    _create_relation_from_row = staticmethod(_decode_relation_row)
    _create_candidate_from_row = staticmethod(_decode_candidate_row)
    _create_tier_transition_from_row = staticmethod(_decode_tier_transition_row)

    def __init__(
        self,
        db_path: str | Path,
        busy_timeout: int = 5000,
        *,
        artifactctl: Any = _ARTIFACTCTL_UNSET,
    ) -> None:
        """Initialize the store and apply migrations."""
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.busy_timeout = busy_timeout
        self._artifactctl = artifactctl
        self._write_lock = threading.RLock()

        with self._connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            run_migrations(conn)
            conn.execute(CREATE_MEMORY_RELATIONS_TABLE)
            conn.execute(CREATE_MEMORY_RELATIONS_SOURCE_INDEX)
            conn.execute(CREATE_MEMORY_RELATIONS_TARGET_INDEX)
            ensure_module_metadata_for_package(
                conn,
                package="openminion.modules.memory.storage",
                migrations=list_migrations(),
            )

    def _connect(self) -> sqlite3.Connection:
        conn = connect_database(self.db_path)
        conn.execute(f"PRAGMA busy_timeout={max(0, int(self.busy_timeout))}")
        conn.isolation_level = None
        return conn

    def _resolve_artifactctl(self) -> Any | None:
        if self._artifactctl is _ARTIFACTCTL_UNSET:
            self._artifactctl = create_default_artifactctl()
        return self._artifactctl

    def _add_artifact_refs(self, *, owner_id: str, ref_values: Any) -> None:
        targets = normalize_artifact_ref_targets(ref_values)
        if not targets:
            return
        add_reference_edges(
            artifactctl=self._resolve_artifactctl(),
            owner_type="memory",
            owner_id=owner_id,
            ref_values=targets,
        )

    def _remove_artifact_refs(self, *, owner_id: str, ref_values: Any) -> None:
        targets = normalize_artifact_ref_targets(ref_values)
        if not targets:
            return
        remove_reference_edges(
            artifactctl=self._resolve_artifactctl(),
            owner_type="memory",
            owner_id=owner_id,
            ref_values=targets,
        )

    _apply_supersession = staticmethod(_apply_supersession_impl)
    _get_required_record = staticmethod(_get_required_record_impl)
    _clamp01 = staticmethod(_clamp01)
    apply_outcome_feedback = _apply_outcome_feedback_impl
    search = _search_query
    retrieve_by_entities = _retrieve_by_entities_query
    candidate_put = _candidate_put_workflow
    candidate_get = _candidate_get_workflow
    candidate_delete = _candidate_delete_workflow
    candidate_list = _candidate_list_workflow
    candidate_update = _candidate_update_workflow

    def put(self, record: MemoryRecord) -> str:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO memory_records (
                    id, scope, namespace_json, type, key, title, content_json, tags_json, entities_json, goal_id,
                    source, confidence, evidence_json, meta_json, last_hit_at, event_time, valid_to, tier, access_count, expires_at, created_at, updated_at,
                    supersedes_id, superseded_by_id, supersession_reason, is_deleted,
                    deleted_at, deleted_reason
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.id,
                    record.scope,
                    json.dumps(record.namespace.as_dict())
                    if record.namespace is not None
                    else None,
                    record.type,
                    record.key,
                    record.title,
                    json.dumps(record.content),
                    json.dumps(record.tags),
                    json.dumps(record.entities),
                    getattr(record, "goal_id", None),
                    record.source,
                    record.confidence,
                    json.dumps([vars(r) for r in record.evidence_refs]),
                    json.dumps(record.meta),
                    record.last_hit_at,
                    record.event_time or record.created_at,
                    record.valid_to,
                    record.tier,
                    int(record.access_count),
                    record.expires_at,
                    record.created_at,
                    record.updated_at,
                    record.supersedes_id,
                    record.superseded_by_id,
                    record.supersession_reason,
                    int(record.is_deleted),
                    record.deleted_at,
                    record.deleted_reason,
                ),
            )
            conn.execute(
                """
                INSERT INTO memory_fts (id, scope, type, key, title, content_text, tags_text, entities_text)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.id,
                    record.scope,
                    record.type,
                    record.key,
                    record.title,
                    json.dumps(record.content)
                    if isinstance(record.content, dict)
                    else record.content,
                    " ".join(record.tags),
                    " ".join(record.entities),
                ),
            )

            if record.entities:
                for entity in record.entities:
                    conn.execute(
                        """
                        INSERT OR IGNORE INTO memory_entities (entity, record_id, scope, type, created_at)
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        (
                            entity,
                            record.id,
                            record.scope,
                            record.type,
                            record.created_at,
                        ),
                    )

        if not record.is_deleted:
            self._add_artifact_refs(owner_id=record.id, ref_values=record.evidence_refs)
        return record.id

    def upsert(
        self, scope: str, type: MemoryType, key: str, record_patch: dict[str, Any]
    ) -> MemoryRecord:
        return _upsert_impl(self, scope, type, key, record_patch)

    def get(self, record_id: str) -> MemoryRecord | None:
        with self._connect() as conn:
            cursor = conn.execute(
                "SELECT * FROM memory_records WHERE id = ?", (record_id,)
            )
            row = cursor.fetchone()
            if not row:
                return None
            return self._create_record_from_row(row)

    def list_records_by_goal_id(
        self,
        goal_id: str,
        *,
        scopes: list[str] | None = None,
        limit: int | None = None,
    ) -> list[MemoryRecord]:
        normalized_goal_id = str(goal_id or "").strip()
        if not normalized_goal_id:
            return []
        params: list[Any] = [normalized_goal_id]
        where = ["goal_id = ?", "is_deleted = 0"]
        if scopes:
            placeholders = ", ".join("?" for _ in scopes)
            where.append(f"scope IN ({placeholders})")
            params.extend(scopes)
        sql = (
            "SELECT * FROM memory_records "
            f"WHERE {' AND '.join(where)} "
            "ORDER BY updated_at DESC"
        )
        if limit is not None:
            sql += " LIMIT ?"
            params.append(max(1, int(limit)))
        with self._connect() as conn:
            rows = conn.execute(sql, tuple(params)).fetchall()
        return [self._create_record_from_row(row) for row in rows]

    def delete(
        self,
        record_id: str,
        *,
        reason: str | None = None,
        deleted_at: str | None = None,
    ) -> None:
        """Soft-delete a memory record with optional audit metadata."""

        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        existing: MemoryRecord | None = None
        with self._connect() as conn:
            cursor = conn.execute(
                "SELECT * FROM memory_records WHERE id = ?",
                (record_id,),
            )
            row = cursor.fetchone()
            if row is not None:
                existing = self._create_record_from_row(row)
            updates = ["is_deleted = 1", "updated_at = ?"]
            params: list[Any] = [now]
            if reason is not None:
                updates.extend(["deleted_at = ?", "deleted_reason = ?"])
                params.extend([deleted_at if deleted_at is not None else now, reason])
            params.append(record_id)
            conn.execute(
                f"UPDATE memory_records SET {', '.join(updates)} WHERE id = ?",
                tuple(params),
            )
            conn.execute("DELETE FROM memory_fts WHERE id = ?", (record_id,))
        if existing is not None:
            self._remove_artifact_refs(
                owner_id=record_id,
                ref_values=existing.evidence_refs,
            )

    def invalidate(
        self,
        record_id: str,
        *,
        valid_to: str,
        reason: str,
    ) -> MemoryRecord:
        del reason

        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        with self._write_lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = self._get_required_record(conn, record_id)
                conn.execute(
                    """
                        UPDATE memory_records
                           SET valid_to = ?, updated_at = ?
                         WHERE id = ?
                        """,
                    (valid_to, now, record_id),
                )
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
        return replace(
            self._create_record_from_row(row),
            valid_to=valid_to,
            updated_at=now,
        )

    def tombstone(self, scope: str, type: MemoryType, key: str) -> None:
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        tombstoned: list[MemoryRecord] = []
        with self._connect() as conn:
            cursor = conn.execute(
                """
                SELECT * FROM memory_records
                WHERE scope = ? AND type = ? AND key = ? AND is_deleted = 0 AND superseded_by_id IS NULL
                """,
                (scope, type, key),
            )
            tombstoned = [
                self._create_record_from_row(row) for row in cursor.fetchall()
            ]
            conn.execute(
                """
                DELETE FROM memory_fts
                WHERE id IN (
                    SELECT id FROM memory_records
                    WHERE scope = ? AND type = ? AND key = ? AND is_deleted = 0 AND superseded_by_id IS NULL
                )
                """,
                (scope, type, key),
            )
            conn.execute(
                """
                UPDATE memory_records SET is_deleted = 1, updated_at = ?
                WHERE scope = ? AND type = ? AND key = ? AND is_deleted = 0 AND superseded_by_id IS NULL
                """,
                (now, scope, type, key),
            )
        for record in tombstoned:
            self._remove_artifact_refs(
                owner_id=record.id,
                ref_values=record.evidence_refs,
            )

    def list(self, options: ListQueryOptions) -> list[MemoryRecord]:
        query = (
            "SELECT * FROM memory_records WHERE (is_deleted = 0 OR superseded_by_id IS NOT NULL)"
            if options.include_invalidated
            else "SELECT * FROM memory_records WHERE is_deleted = 0"
        )
        params = []
        if not options.include_invalidated:
            query += " AND (valid_to IS NULL OR valid_to > ?)"
            params.append(datetime.datetime.now(datetime.timezone.utc).isoformat())

        if options.scopes:
            placeholders = ",".join("?" * len(options.scopes))
            query += f" AND scope IN ({placeholders})"
            params.extend(options.scopes)

        if options.types:
            placeholders = ",".join("?" * len(options.types))
            query += f" AND type IN ({placeholders})"
            params.extend(options.types)

        if options.tiers:
            placeholders = ",".join("?" * len(options.tiers))
            query += f" AND tier IN ({placeholders})"
            params.extend(options.tiers)

        if options.order_by:
            query += (
                " ORDER BY updated_at DESC"
                if options.order_by.value == "updated_at_desc"
                else " ORDER BY updated_at ASC"
            )

        if options.limit is not None and not options.namespaces:
            query += " LIMIT ?"
            params.append(options.limit)
            if options.offset is not None:
                query += " OFFSET ?"
                params.append(options.offset)

        with self._connect() as conn:
            cursor = conn.execute(query, params)
            records = [self._create_record_from_row(row) for row in cursor.fetchall()]
        if options.namespaces:
            records = [
                record
                for record in records
                if record_matches_namespaces(record, options.namespaces)
            ]
            offset = max(0, int(options.offset or 0))
            records = records[offset:]
            if options.limit is not None:
                records = records[: max(1, int(options.limit))]
        return records

    def list_scopes(self) -> list[str]:
        with self._connect() as conn:
            cursor = conn.execute(
                """
                SELECT DISTINCT scope
                FROM memory_records
                WHERE is_deleted = 0
                ORDER BY scope ASC
                """
            )
            return [
                str(row["scope"])
                for row in cursor.fetchall()
                if str(row["scope"] or "").strip()
            ]

    def touch_last_hit(self, record_id: str) -> None:
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        with self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE memory_records
                   SET last_hit_at = ?, access_count = COALESCE(access_count, 0) + 1
                 WHERE id = ?
                """,
                (now, record_id),
            )
            if cursor.rowcount <= 0:
                raise NotFoundError(
                    f"record not found: {record_id}", details={"record_id": record_id}
                )

    def transition_tier(
        self,
        record_id: str,
        *,
        to_tier: str,
        transition_reason: str,
        transition_at: str,
        meta: dict[str, Any] | None = None,
    ) -> MemoryTierTransition:
        transition_id = uuid.uuid4().hex
        with self._write_lock:
            with self._connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                try:
                    row = self._get_required_record(conn, record_id)
                    from_tier = str(row["tier"] or "working")
                    if from_tier == str(to_tier):
                        raise InvalidArgumentError("from_tier and to_tier must differ")
                    conn.execute(
                        """
                        UPDATE memory_records
                           SET tier = ?, updated_at = ?
                         WHERE id = ?
                        """,
                        (to_tier, transition_at, record_id),
                    )
                    conn.execute(
                        """
                        INSERT INTO memory_tier_transitions(
                            transition_id, record_id, scope, record_type, from_tier,
                            to_tier, transition_reason, transition_at, access_count, meta_json
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            transition_id,
                            record_id,
                            row["scope"],
                            row["type"],
                            from_tier,
                            to_tier,
                            transition_reason,
                            transition_at,
                            int(row["access_count"] or 0),
                            json.dumps(meta or {}, sort_keys=True),
                        ),
                    )
                    conn.execute("COMMIT")
                except Exception:
                    conn.execute("ROLLBACK")
                    raise
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT transition_id, record_id, scope, record_type, from_tier,
                       to_tier, transition_reason, transition_at, access_count, meta_json
                  FROM memory_tier_transitions
                 WHERE transition_id = ?
                """,
                (transition_id,),
            ).fetchone()
            if row is None:
                raise RuntimeError(
                    "tier transition missing after insert"
                )  # allow-bare-raise: internal invariant — post-write read-back guard
            return self._create_tier_transition_from_row(row)

    def list_tier_transitions(
        self,
        *,
        record_id: str | None = None,
        scopes: list[str] | None = None,
        limit: int | None = None,
    ) -> list[MemoryTierTransition]:
        query = """
            SELECT transition_id, record_id, scope, record_type, from_tier,
                   to_tier, transition_reason, transition_at, access_count, meta_json
              FROM memory_tier_transitions
             WHERE 1 = 1
        """
        params: list[Any] = []
        if record_id:
            query += " AND record_id = ?"
            params.append(record_id)
        if scopes:
            placeholders = ",".join("?" * len(scopes))
            query += f" AND scope IN ({placeholders})"
            params.extend(scopes)
        query += " ORDER BY transition_at DESC, transition_id DESC"
        if limit is not None:
            query += " LIMIT ?"
            params.append(limit)
        with self._connect() as conn:
            cursor = conn.execute(query, params)
            return [
                self._create_tier_transition_from_row(row) for row in cursor.fetchall()
            ]

    def put_tier_transition(self, transition: MemoryTierTransition) -> str:
        import json

        with self._write_lock:
            with self._connect() as conn:
                self._get_required_record(conn, transition.record_id)
                conn.execute(
                    """
                    INSERT OR REPLACE INTO memory_tier_transitions(
                        transition_id, record_id, scope, record_type, from_tier,
                        to_tier, transition_reason, transition_at, access_count, meta_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        transition.transition_id,
                        transition.record_id,
                        transition.scope,
                        transition.record_type,
                        transition.from_tier,
                        transition.to_tier,
                        transition.transition_reason,
                        transition.transition_at,
                        int(transition.access_count or 0),
                        json.dumps(transition.meta or {}, sort_keys=True),
                    ),
                )
        return transition.transition_id

    def put_relation(self, relation: MemoryRelation) -> str:
        import json

        with self._write_lock, self._connect() as conn:
            self._get_required_record(conn, relation.source_record_id)
            self._get_required_record(conn, relation.target_record_id)
            conn.execute(
                """
                    INSERT OR REPLACE INTO memory_relations(
                        relation_id, source_record_id, target_record_id, relation_type,
                        meta_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                (
                    relation.relation_id,
                    relation.source_record_id,
                    relation.target_record_id,
                    relation.relation_type,
                    json.dumps(relation.meta),
                    relation.created_at,
                ),
            )
        return relation.relation_id

    def list_relations(
        self,
        record_id: str,
        *,
        relation_types: list[MemoryRelationType] | None = None,
        limit: int | None = None,
    ) -> list[MemoryRelation]:
        params: list[Any] = [record_id, record_id]
        query = """
            SELECT relation_id, source_record_id, target_record_id, relation_type,
                   meta_json, created_at
            FROM memory_relations
            WHERE source_record_id = ? OR target_record_id = ?
        """
        if relation_types:
            placeholders = ",".join("?" * len(relation_types))
            query += f" AND relation_type IN ({placeholders})"
            params.extend(relation_types)
        query += " ORDER BY created_at DESC, relation_id DESC"
        if limit is not None:
            query += " LIMIT ?"
            params.append(limit)
        with self._connect() as conn:
            cursor = conn.execute(query, params)
            return [self._create_relation_from_row(row) for row in cursor.fetchall()]

    def get_related_records(
        self,
        record_id: str,
        scopes: list[str],
        *,
        relation_types: list[MemoryRelationType] | None = None,
        limit: int | None = None,
    ) -> list[MemoryRecord]:
        if not scopes:
            return []

        scope_placeholders = ",".join("?" * len(scopes))
        params: list[Any] = [record_id, record_id, record_id] + list(scopes)
        query = f"""
            SELECT DISTINCT r.*
            FROM memory_relations rel
            JOIN memory_records r
              ON r.id = CASE
                    WHEN rel.source_record_id = ? THEN rel.target_record_id
                    ELSE rel.source_record_id
                 END
            WHERE (rel.source_record_id = ? OR rel.target_record_id = ?)
              AND r.is_deleted = 0
              AND r.superseded_by_id IS NULL
              AND r.scope IN ({scope_placeholders})
        """
        if relation_types:
            placeholders = ",".join("?" * len(relation_types))
            query += f" AND rel.relation_type IN ({placeholders})"
            params.extend(relation_types)
        query += " ORDER BY r.updated_at DESC"
        if limit is not None:
            query += " LIMIT ?"
            params.append(limit)
        with self._connect() as conn:
            cursor = conn.execute(query, params)
            return [self._create_record_from_row(row) for row in cursor.fetchall()]

    def promote_candidate(self, candidate_id: str, target_scope: str) -> MemoryRecord:
        return _promote_candidate_impl(self, candidate_id, target_scope)

    def history(self, scope: str, type: MemoryType, key: str) -> list[MemoryRecord]:
        return _history_impl(self, scope, type, key)

    def supersede_by_contradiction(
        self, old_record_id: str, new_record_id: str, reason: str = ""
    ) -> MemoryRecord:
        return _supersede_by_contradiction_impl(
            self,
            old_record_id,
            new_record_id,
            reason,
        )
