"""PostgreSQL memory store."""

from __future__ import annotations

import json
import logging
from contextlib import contextmanager
from pathlib import Path
import threading
import uuid
from typing import TYPE_CHECKING, Any

from openminion.modules.artifact.refs import (
    add_reference_edges,
    create_default_artifactctl,
    normalize_artifact_ref_targets,
    remove_reference_edges,
)
from openminion.modules.storage.migrations.module_ids import get_module_application_id
from openminion.modules.storage.migrations.runner import MigrationRunner

from ..base import MemoryStore
from ..migrations import TARGET_USER_VERSION
from .candidate_supersession import (
    candidate_delete as _candidate_delete_workflow,
    candidate_get as _candidate_get_workflow,
    candidate_list as _candidate_list_workflow,
    candidate_put as _candidate_put_workflow,
    candidate_update as _candidate_update_workflow,
    history as _history_workflow,
    promote_candidate as _promote_candidate_workflow,
    supersede_by_contradiction as _supersede_by_contradiction_workflow,
)
from .queries import (
    _tsquery_candidates as _query_tsquery_candidates,
    get as _get_query,
    list_records as _list_records_query,
    list_scopes as _list_scopes_query,
    retrieve_by_entities as _retrieve_by_entities_query,
    search as _search_query,
    touch_last_hit as _touch_last_hit_query,
)
from .row import (
    _create_candidate_from_row as _decode_candidate_row,
    _create_record_from_row as _decode_record_row,
    _create_tier_transition_from_row as _decode_tier_transition_row,
    _decode_evidence_ref_values as _decode_row_evidence_ref_values,
)
from .sql import _named_params
from .write import (
    _apply_supersession as _apply_supersession_workflow,
    _get_required_record as _get_required_record_workflow,
    _insert_record as _insert_record_workflow,
    _upsert_entities as _upsert_entities_workflow,
    apply_outcome_feedback as _apply_outcome_feedback_workflow,
    delete as _delete_workflow,
    invalidate as _invalidate_workflow,
    put as _put_workflow,
    tombstone as _tombstone_workflow,
    upsert as _upsert_workflow,
)

if TYPE_CHECKING:
    from sqlalchemy.engine import Connection, Engine

from openminion.modules.memory.models import (
    MemoryTierTransition,
    MemoryRelation,
    MemoryRelationType,
    MemoryRecord,
)
from ...errors import InvalidArgumentError, MigrationRequiredError


logger = logging.getLogger(__name__)

_ARTIFACTCTL_UNSET = object()


class PostgresMemoryStore(MemoryStore):
    """Postgres implementation of ``MemoryStore`` using an injected pooled engine."""

    def __init__(
        self,
        pool: Engine,
        *,
        database_path: str | Path | None = None,
        artifactctl: Any = _ARTIFACTCTL_UNSET,
        owns_engine: bool = False,
    ) -> None:
        self._engine = pool
        self._artifactctl = artifactctl
        self._owns_engine = owns_engine
        self._lock = threading.RLock()
        placeholder_path = (
            Path(database_path).expanduser().resolve(strict=False)
            if database_path is not None
            else (Path.cwd() / ".openminion-memory-postgres").resolve()
        )
        placeholder_path.parent.mkdir(parents=True, exist_ok=True)
        self._bootstrap_schema(placeholder_path)

    def close(self) -> None:
        if self._owns_engine:
            self._engine.dispose()

    @contextmanager
    def gc_connection(self):
        with self._engine.begin() as connection:
            yield connection

    def _bootstrap_schema(self, placeholder_path: Path) -> None:
        runner = MigrationRunner(
            module_id="memory",
            db_path=placeholder_path,
            module_application_id=get_module_application_id("memory"),
            target_user_version=TARGET_USER_VERSION,
            backend_type="postgres",
            engine=self._engine,
        )
        report = runner.migrate(target="head")
        if not report.success:
            raise MigrationRequiredError(
                report.error or "Alembic migration failed for module 'memory'",
            )
        with self._engine.begin() as conn:
            conn.exec_driver_sql(
                """
                CREATE TABLE IF NOT EXISTS memory_relations (
                    relation_id TEXT PRIMARY KEY,
                    source_record_id TEXT NOT NULL,
                    target_record_id TEXT NOT NULL,
                    relation_type TEXT NOT NULL,
                    meta_json JSONB NOT NULL DEFAULT '{}'::jsonb,
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.exec_driver_sql(
                """
                CREATE INDEX IF NOT EXISTS idx_memory_relations_source
                ON memory_relations(source_record_id, created_at DESC)
                """
            )
            conn.exec_driver_sql(
                """
                CREATE INDEX IF NOT EXISTS idx_memory_relations_target
                ON memory_relations(target_record_id, created_at DESC)
                """
            )

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

    def _fetchall(
        self,
        sql: str,
        params: dict[str, Any] | None = None,
        *,
        connection: Connection | None = None,
    ) -> list[dict[str, Any]]:
        from sqlalchemy import text

        statement = text(sql)
        if connection is not None:
            result = connection.execute(statement, params or {})
            return [dict(row) for row in result.mappings().all()]
        with self._engine.connect() as conn:
            result = conn.execute(statement, params or {})
            return [dict(row) for row in result.mappings().all()]

    def _fetchone(
        self,
        sql: str,
        params: dict[str, Any] | None = None,
        *,
        connection: Connection | None = None,
    ) -> dict[str, Any] | None:
        rows = self._fetchall(sql, params, connection=connection)
        return rows[0] if rows else None

    def _execute(
        self,
        sql: str,
        params: dict[str, Any] | None = None,
        *,
        connection: Connection | None = None,
    ) -> int:
        from sqlalchemy import text

        statement = text(sql)
        if connection is not None:
            result = connection.execute(statement, params or {})
            return int(result.rowcount or 0)
        with self._engine.begin() as conn:
            result = conn.execute(statement, params or {})
            return int(result.rowcount or 0)

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
        params: dict[str, Any] = {"goal_id": normalized_goal_id}
        query = [
            """
            SELECT *
              FROM memory_records
             WHERE goal_id = :goal_id
               AND COALESCE(is_deleted, 0) = 0
            """
        ]
        if scopes:
            scopes_sql, scopes_params = _named_params("scope", list(scopes))
            query.append(f"AND scope IN ({scopes_sql})")
            params.update(scopes_params)
        query.append("ORDER BY updated_at DESC")
        if limit is not None:
            query.append("LIMIT :limit")
            params["limit"] = int(limit)
        rows = self._fetchall(" ".join(query), params)
        return [self._create_record_from_row(row) for row in rows]

    _create_record_from_row = staticmethod(_decode_record_row)
    _create_candidate_from_row = staticmethod(_decode_candidate_row)
    _create_tier_transition_from_row = staticmethod(_decode_tier_transition_row)
    _decode_evidence_ref_values = staticmethod(_decode_row_evidence_ref_values)
    _get_required_record = _get_required_record_workflow
    _apply_supersession = _apply_supersession_workflow
    _upsert_entities = _upsert_entities_workflow
    _insert_record = _insert_record_workflow
    put = _put_workflow
    upsert = _upsert_workflow
    get = _get_query
    delete = _delete_workflow
    invalidate = _invalidate_workflow
    tombstone = _tombstone_workflow
    list = _list_records_query
    list_scopes = _list_scopes_query
    touch_last_hit = _touch_last_hit_query
    apply_outcome_feedback = _apply_outcome_feedback_workflow
    _tsquery_candidates = staticmethod(_query_tsquery_candidates)
    search = _search_query
    retrieve_by_entities = _retrieve_by_entities_query
    candidate_put = _candidate_put_workflow
    candidate_get = _candidate_get_workflow
    candidate_delete = _candidate_delete_workflow
    candidate_list = _candidate_list_workflow
    candidate_update = _candidate_update_workflow
    promote_candidate = _promote_candidate_workflow
    history = _history_workflow
    supersede_by_contradiction = _supersede_by_contradiction_workflow

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
        with self._engine.begin() as conn:
            row = self._get_required_record(conn, record_id)
            from_tier = str(row.get("tier") or "working")
            if from_tier == str(to_tier):
                raise InvalidArgumentError("from_tier and to_tier must differ")
            conn.exec_driver_sql(
                """
                UPDATE memory_records
                   SET tier = %s, updated_at = %s
                 WHERE id = %s
                """,
                (to_tier, transition_at, record_id),
            )
            conn.exec_driver_sql(
                """
                INSERT INTO memory_tier_transitions(
                    transition_id, record_id, scope, record_type, from_tier,
                    to_tier, transition_reason, transition_at, access_count, meta_json
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
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
                    int(row.get("access_count") or 0),
                    json.dumps(meta or {}, sort_keys=True),
                ),
            )
        row = self._fetchone(
            """
            SELECT transition_id, record_id, scope, record_type, from_tier,
                   to_tier, transition_reason, transition_at, access_count, meta_json
              FROM memory_tier_transitions
             WHERE transition_id = :transition_id
            """,
            {"transition_id": transition_id},
        )
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
        params: dict[str, Any] = {}
        query = [
            """
            SELECT transition_id, record_id, scope, record_type, from_tier,
                   to_tier, transition_reason, transition_at, access_count, meta_json
              FROM memory_tier_transitions
             WHERE 1 = 1
            """
        ]
        if record_id:
            query.append("AND record_id = :record_id")
            params["record_id"] = record_id
        if scopes:
            scopes_sql, scopes_params = _named_params("scope", list(scopes))
            query.append(f"AND scope IN ({scopes_sql})")
            params.update(scopes_params)
        query.append("ORDER BY transition_at DESC, transition_id DESC")
        if limit is not None:
            query.append("LIMIT :limit")
            params["limit"] = int(limit)
        return [
            self._create_tier_transition_from_row(row)
            for row in self._fetchall(" ".join(query), params)
        ]

    def put_tier_transition(self, transition: MemoryTierTransition) -> str:
        with self._engine.begin() as conn:
            self._get_required_record(conn, transition.record_id)
            conn.exec_driver_sql(
                """
                INSERT INTO memory_tier_transitions(
                    transition_id, record_id, scope, record_type, from_tier,
                    to_tier, transition_reason, transition_at, access_count, meta_json
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
                ON CONFLICT (transition_id) DO UPDATE SET
                    record_id = EXCLUDED.record_id,
                    scope = EXCLUDED.scope,
                    record_type = EXCLUDED.record_type,
                    from_tier = EXCLUDED.from_tier,
                    to_tier = EXCLUDED.to_tier,
                    transition_reason = EXCLUDED.transition_reason,
                    transition_at = EXCLUDED.transition_at,
                    access_count = EXCLUDED.access_count,
                    meta_json = EXCLUDED.meta_json
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
        with self._engine.begin() as conn:
            self._get_required_record(conn, relation.source_record_id)
            self._get_required_record(conn, relation.target_record_id)
            conn.exec_driver_sql(
                """
                INSERT INTO memory_relations(
                    relation_id, source_record_id, target_record_id, relation_type,
                    meta_json, created_at
                ) VALUES (%s, %s, %s, %s, %s::jsonb, %s)
                ON CONFLICT (relation_id) DO UPDATE SET
                    source_record_id = EXCLUDED.source_record_id,
                    target_record_id = EXCLUDED.target_record_id,
                    relation_type = EXCLUDED.relation_type,
                    meta_json = EXCLUDED.meta_json,
                    created_at = EXCLUDED.created_at
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
        params: dict[str, Any] = {
            "record_id": record_id,
            "limit": int(limit) if limit is not None else None,
        }
        query = """
            SELECT relation_id, source_record_id, target_record_id, relation_type,
                   meta_json, created_at
            FROM memory_relations
            WHERE source_record_id = :record_id OR target_record_id = :record_id
        """
        if relation_types:
            params["relation_types"] = list(relation_types)
            query += " AND relation_type = ANY(:relation_types)"
        query += " ORDER BY created_at DESC, relation_id DESC"
        if limit is not None:
            query += " LIMIT :limit"
        rows = self._fetchall(query, params)
        return [
            MemoryRelation(
                relation_id=str(row["relation_id"]),
                source_record_id=str(row["source_record_id"]),
                target_record_id=str(row["target_record_id"]),
                relation_type=str(row["relation_type"]),
                created_at=str(row["created_at"]),
                meta=dict(row.get("meta_json") or {}),
            )
            for row in rows
        ]

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
        params: dict[str, Any] = {
            "record_id": record_id,
            "scopes": list(scopes),
            "limit": int(limit) if limit is not None else None,
        }
        query = """
            SELECT DISTINCT r.*
            FROM memory_relations rel
            JOIN memory_records r
              ON r.id = CASE
                    WHEN rel.source_record_id = :record_id THEN rel.target_record_id
                    ELSE rel.source_record_id
                 END
            WHERE (rel.source_record_id = :record_id OR rel.target_record_id = :record_id)
              AND r.is_deleted = FALSE
              AND r.superseded_by_id IS NULL
              AND r.scope = ANY(:scopes)
        """
        if relation_types:
            params["relation_types"] = list(relation_types)
            query += " AND rel.relation_type = ANY(:relation_types)"
        query += " ORDER BY r.updated_at DESC"
        if limit is not None:
            query += " LIMIT :limit"
        rows = self._fetchall(query, params)
        return [self._create_record_from_row(row) for row in rows]


__all__ = ["PostgresMemoryStore"]
