"""Subject-scoped durable storage for commerce orders and attempts."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from openminion.modules.storage.record_store import RecordStore
from openminion.modules.storage.runtime.module_store import (
    BaseModuleSQLiteStore,
    BaseModuleStore,
)

from ..models import CommerceDigest, CommerceLifecycleState
from ..provider import OrderPreparation
from .migrations import list_migrations
from .models import (
    AttemptKind,
    AttemptReservation,
    AttemptState,
    CommerceAttempt,
    MaterialSnapshot,
    OrderRecord,
    PreparationPayload,
    PreparationRecord,
    SnapshotTarget,
)

_TERMINAL_ATTEMPT_STATES = frozenset({"succeeded", "failed"})


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _digest_json(raw: str) -> str:
    return f"sha256:{hashlib.sha256(raw.encode('utf-8')).hexdigest()}"


def _create_schema(record_store: RecordStore) -> None:
    for statement in _SCHEMA_DDL:
        record_store.execute_count(statement)


class _CommerceOrderStoreOps:
    _record_store: RecordStore

    def _list_migrations(self) -> list[str]:
        return list_migrations()

    def _module_package(self) -> str:
        return __package__

    def _init_schema(self) -> None:
        with self._lock:
            _create_schema(self._record_store)

    def close(self) -> None:
        BaseModuleStore.close(self)

    def save_preparation(
        self,
        *,
        subject_id: str,
        preparation_id: str,
        payload: PreparationPayload,
        prepared: OrderPreparation | None = None,
        preparation_attempt_id: str | None = None,
    ) -> PreparationRecord:
        if preparation_attempt_id is not None:
            attempt = self._get_attempt(subject_id, preparation_attempt_id)
            if attempt is None or attempt.kind != "preparation":
                raise ValueError("preparation attempt must belong to the same subject")
        payload_json = _canonical_json(payload.model_dump(mode="json"))
        prepared_json = (
            _canonical_json(prepared.model_dump(mode="json"))
            if prepared is not None
            else None
        )
        created_at = _now_iso()
        created = self._record_store.execute_count(
            """
            INSERT INTO commerce_preparations(
                preparation_id, subject_id, payload_digest, payload_json,
                prepared_json, preparation_attempt_id, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(preparation_id) DO NOTHING
            """,
            (
                preparation_id,
                subject_id,
                _digest_json(payload_json),
                payload_json,
                prepared_json,
                preparation_attempt_id,
                created_at,
            ),
        )
        record = self.get_preparation(subject_id, preparation_id)
        if created != 1 and (
            record is None or record.payload_digest != _digest_json(payload_json)
            or prepared is not None and record.prepared != prepared
        ):
            raise ValueError("preparation identity is owned by different facts")
        if record is None:
            raise RuntimeError("preparation insert did not produce a readable record")
        return record

    def get_preparation(
        self, subject_id: str, preparation_id: str
    ) -> PreparationRecord | None:
        rows = self._record_store.query_dicts(
            """
            SELECT preparation_id, subject_id, payload_digest, payload_json,
                   prepared_json, preparation_attempt_id, created_at,
                   invalidated_at
            FROM commerce_preparations
            WHERE subject_id = ? AND preparation_id = ?
            """,
            (subject_id, preparation_id),
        )
        return _preparation_from_row(rows[0]) if rows else None

    def invalidate_preparation(
        self, *, subject_id: str, preparation_id: str
    ) -> PreparationRecord:
        updated = self._record_store.execute_count(
            """
            UPDATE commerce_preparations
            SET invalidated_at = COALESCE(invalidated_at, ?)
            WHERE subject_id = ? AND preparation_id = ?
            """,
            (_now_iso(), subject_id, preparation_id),
        )
        if updated != 1:
            raise ValueError("preparation does not belong to subject")
        record = self.get_preparation(subject_id, preparation_id)
        if record is None:
            raise RuntimeError("invalidated preparation is not readable")
        return record

    def save_order(
        self,
        *,
        subject_id: str,
        order_id: str,
        preparation_id: str,
        provider_order_digest: CommerceDigest,
        lifecycle: CommerceLifecycleState,
        placement_attempt_id: str | None = None,
    ) -> OrderRecord:
        preparation = self.get_preparation(subject_id, preparation_id)
        if preparation is None:
            raise ValueError("order preparation must belong to the same subject")
        if placement_attempt_id is not None:
            attempt = self._get_attempt(subject_id, placement_attempt_id)
            if (
                attempt is None
                or attempt.kind != "placement"
                or attempt.target_id != preparation_id
            ):
                raise ValueError("placement attempt must belong to the preparation")
        lifecycle_json = _canonical_json(lifecycle.model_dump(mode="json"))
        now = _now_iso()
        created = self._record_store.execute_count(
            """
            INSERT INTO commerce_orders(
                order_id, subject_id, preparation_id, provider_order_digest,
                payload_digest, payload_json, lifecycle_json,
                placement_attempt_id, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(order_id) DO NOTHING
            """,
            (
                order_id,
                subject_id,
                preparation_id,
                provider_order_digest,
                preparation.payload_digest,
                _canonical_json(preparation.payload.model_dump(mode="json")),
                lifecycle_json,
                placement_attempt_id,
                now,
                now,
            ),
        )
        record = self.get_order(subject_id, order_id)
        if created != 1 and (
            record is None
            or record.preparation_id != preparation_id
            or record.provider_order_digest != provider_order_digest
        ):
            raise ValueError("order identity is owned by different facts")
        if record is None:
            raise RuntimeError("order insert did not produce a readable record")
        return record

    def get_order(self, subject_id: str, order_id: str) -> OrderRecord | None:
        rows = self._record_store.query_dicts(
            """
            SELECT order_id, subject_id, preparation_id, provider_order_digest,
                   payload_digest, payload_json, lifecycle_json,
                   placement_attempt_id, created_at, updated_at
            FROM commerce_orders
            WHERE subject_id = ? AND order_id = ?
            """,
            (subject_id, order_id),
        )
        return _order_from_row(rows[0]) if rows else None

    def update_order_lifecycle(
        self,
        *,
        subject_id: str,
        order_id: str,
        lifecycle: CommerceLifecycleState,
    ) -> OrderRecord:
        updated = self._record_store.execute_count(
            """
            UPDATE commerce_orders
            SET lifecycle_json = ?, updated_at = ?
            WHERE subject_id = ? AND order_id = ?
            """,
            (
                _canonical_json(lifecycle.model_dump(mode="json")),
                _now_iso(),
                subject_id,
                order_id,
            ),
        )
        if updated != 1:
            raise ValueError("order does not belong to subject")
        record = self.get_order(subject_id, order_id)
        if record is None:
            raise RuntimeError("updated order is not readable")
        return record

    def save_material_snapshot(
        self,
        *,
        subject_id: str,
        snapshot_id: str,
        target_type: SnapshotTarget,
        target_id: str,
        lifecycle: CommerceLifecycleState,
        cursor_digest: CommerceDigest | None = None,
    ) -> MaterialSnapshot:
        self._require_owned_target(subject_id, target_type, target_id)
        lifecycle_json = _canonical_json(lifecycle.model_dump(mode="json"))
        payload_digest = _digest_json(
            _canonical_json(
                {
                    "cursor_digest": cursor_digest,
                    "lifecycle": lifecycle.model_dump(mode="json"),
                }
            )
        )
        observed_at = _now_iso()
        created = self._record_store.execute_count(
            """
            INSERT INTO commerce_material_snapshots(
                snapshot_id, subject_id, target_type, target_id, payload_digest,
                lifecycle_json, cursor_digest, observed_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(snapshot_id) DO NOTHING
            """,
            (
                snapshot_id,
                subject_id,
                target_type,
                target_id,
                payload_digest,
                lifecycle_json,
                cursor_digest,
                observed_at,
            ),
        )
        snapshot = self.get_material_snapshot(subject_id, snapshot_id)
        if created != 1 and (
            snapshot is None
            or snapshot.target_type != target_type
            or snapshot.target_id != target_id
            or snapshot.payload_digest != payload_digest
        ):
            raise ValueError("snapshot identity is owned by different facts")
        if snapshot is None:
            raise RuntimeError("snapshot insert did not produce a readable record")
        return snapshot

    def get_material_snapshot(
        self, subject_id: str, snapshot_id: str
    ) -> MaterialSnapshot | None:
        rows = self._record_store.query_dicts(
            """
            SELECT snapshot_id, subject_id, target_type, target_id, payload_digest,
                   lifecycle_json, cursor_digest, observed_at
            FROM commerce_material_snapshots
            WHERE subject_id = ? AND snapshot_id = ?
            """,
            (subject_id, snapshot_id),
        )
        return _snapshot_from_row(rows[0]) if rows else None

    def reserve_preparation(
        self,
        *,
        subject_id: str,
        target_id: str,
        idempotency_key: str,
        request_digest: CommerceDigest,
    ) -> AttemptReservation:
        return self._reserve_attempt(
            subject_id=subject_id,
            kind="preparation",
            target_id=target_id,
            operation="prepare",
            idempotency_key=idempotency_key,
            request_digest=request_digest,
        )

    def reserve_placement(
        self,
        *,
        subject_id: str,
        preparation_id: str,
        idempotency_key: str,
        request_digest: CommerceDigest,
        authorization_hash: CommerceDigest | None = None,
    ) -> AttemptReservation:
        preparation = self.get_preparation(subject_id, preparation_id)
        if preparation is None:
            raise ValueError("placement preparation must belong to the same subject")
        if preparation.invalidated_at is not None:
            raise ValueError("placement preparation is invalidated")
        return self._reserve_attempt(
            subject_id=subject_id,
            kind="placement",
            target_id=preparation_id,
            operation="place",
            idempotency_key=idempotency_key,
            request_digest=request_digest,
            authorization_hash=authorization_hash,
        )

    def reserve_action(
        self,
        *,
        subject_id: str,
        order_id: str,
        operation: str,
        idempotency_key: str,
        request_digest: CommerceDigest,
    ) -> AttemptReservation:
        if self.get_order(subject_id, order_id) is None:
            raise ValueError("action order must belong to the same subject")
        return self._reserve_attempt(
            subject_id=subject_id,
            kind="action",
            target_id=order_id,
            operation=operation,
            idempotency_key=idempotency_key,
            request_digest=request_digest,
        )

    def get_attempt_by_idempotency(
        self,
        *,
        subject_id: str,
        kind: AttemptKind,
        idempotency_key: str,
    ) -> CommerceAttempt | None:
        rows = self._record_store.query_dicts(
            """
            SELECT attempt_id, subject_id, kind, target_id, operation,
                   idempotency_key, request_digest, authorization_hash,
                   attempt_count, state, response_digest,
                   provider_reference_digest, active, created_at, updated_at
            FROM commerce_attempts
            WHERE subject_id = ? AND kind = ? AND idempotency_key = ?
            """,
            (subject_id, kind, idempotency_key),
        )
        return _attempt_from_row(rows[0]) if rows else None

    def begin_placement_attempt(
        self, *, subject_id: str, attempt_id: str
    ) -> CommerceAttempt | None:
        updated = self._record_store.execute_count(
            """
            UPDATE commerce_attempts
            SET state = 'submitted', attempt_count = attempt_count + 1,
                updated_at = ?
            WHERE subject_id = ? AND attempt_id = ? AND kind = 'placement'
              AND state = 'reserved' AND attempt_count = 0
            """,
            (_now_iso(), subject_id, attempt_id),
        )
        return self._get_attempt(subject_id, attempt_id) if updated == 1 else None

    def finish_attempt(
        self,
        *,
        subject_id: str,
        attempt_id: str,
        state: AttemptState,
        response_digest: CommerceDigest | None = None,
        provider_reference_digest: CommerceDigest | None = None,
    ) -> CommerceAttempt:
        active = 0 if state in _TERMINAL_ATTEMPT_STATES else 1
        updated = self._record_store.execute_count(
            """
            UPDATE commerce_attempts
            SET state = ?, response_digest = ?, provider_reference_digest = ?,
                active = ?, updated_at = ?
            WHERE subject_id = ? AND attempt_id = ?
            """,
            (
                state,
                response_digest,
                provider_reference_digest,
                active,
                _now_iso(),
                subject_id,
                attempt_id,
            ),
        )
        if updated != 1:
            raise ValueError("attempt does not belong to subject")
        rows = self._record_store.query_rows(
            "commerce_attempts",
            {"subject_id": subject_id, "attempt_id": attempt_id},
        )
        if not rows:
            raise RuntimeError("updated attempt is not readable")
        return _attempt_from_row(rows[0])

    def _get_attempt(self, subject_id: str, attempt_id: str) -> CommerceAttempt | None:
        rows = self._record_store.query_rows(
            "commerce_attempts",
            {"subject_id": subject_id, "attempt_id": attempt_id},
        )
        return _attempt_from_row(rows[0]) if rows else None

    def _reserve_attempt(
        self,
        *,
        subject_id: str,
        kind: AttemptKind,
        target_id: str,
        operation: str,
        idempotency_key: str,
        request_digest: CommerceDigest,
        authorization_hash: CommerceDigest | None = None,
    ) -> AttemptReservation:
        now = _now_iso()
        created = self._record_store.execute_count(
            """
            INSERT INTO commerce_attempts(
                attempt_id, subject_id, kind, target_id, operation,
                idempotency_key, request_digest, authorization_hash,
                attempt_count, state, response_digest,
                provider_reference_digest, active, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, 'reserved', NULL, NULL, 1, ?, ?)
            ON CONFLICT DO NOTHING
            """,
            (
                str(uuid4()),
                subject_id,
                kind,
                target_id,
                operation,
                idempotency_key,
                request_digest,
                authorization_hash,
                now,
                now,
            ),
        )
        attempt = self.get_attempt_by_idempotency(
            subject_id=subject_id,
            kind=kind,
            idempotency_key=idempotency_key,
        )
        if attempt is None:
            attempt = self._find_reserved_target(subject_id, kind, target_id)
        if attempt is None:
            raise RuntimeError("attempt reservation did not produce a readable record")
        if attempt.idempotency_key == idempotency_key and (
            attempt.target_id != target_id
            or attempt.operation != operation
            or attempt.request_digest != request_digest
            or attempt.authorization_hash != authorization_hash
        ):
            raise ValueError("idempotency key is bound to different request facts")
        return AttemptReservation(created=created == 1, attempt=attempt)

    def _find_reserved_target(
        self, subject_id: str, kind: AttemptKind, target_id: str
    ) -> CommerceAttempt | None:
        active_clause = "AND active = 1" if kind == "action" else ""
        rows = self._record_store.query_dicts(
            f"""
            SELECT attempt_id, subject_id, kind, target_id, operation,
                   idempotency_key, request_digest, authorization_hash,
                   attempt_count, state, response_digest,
                   provider_reference_digest, active, created_at, updated_at
            FROM commerce_attempts
            WHERE subject_id = ? AND kind = ? AND target_id = ? {active_clause}
            ORDER BY created_at DESC, attempt_id DESC
            LIMIT 1
            """,
            (subject_id, kind, target_id),
        )
        return _attempt_from_row(rows[0]) if rows else None

    def _require_owned_target(
        self, subject_id: str, target_type: SnapshotTarget, target_id: str
    ) -> None:
        if target_type == "preparation":
            owned = self.get_preparation(subject_id, target_id)
        else:
            owned = self.get_order(subject_id, target_id)
        if owned is None:
            raise ValueError("snapshot target must belong to the same subject")


class SQLiteCommerceOrderStore(_CommerceOrderStoreOps, BaseModuleSQLiteStore):
    def __init__(
        self,
        database_path: str | Path,
        *,
        record_store: RecordStore | None = None,
        wal: bool = True,
    ) -> None:
        BaseModuleSQLiteStore.__init__(
            self,
            database_path,
            wal=wal,
            record_store=record_store,
        )


class PostgresCommerceOrderStore(_CommerceOrderStoreOps, BaseModuleStore):
    def __init__(self, *, record_store: RecordStore) -> None:
        BaseModuleStore.__init__(self, record_store=record_store)


def _preparation_from_row(row: dict[str, object]) -> PreparationRecord:
    return PreparationRecord(
        preparation_id=str(row["preparation_id"]),
        subject_id=str(row["subject_id"]),
        payload_digest=str(row["payload_digest"]),
        payload=PreparationPayload.model_validate_json(str(row["payload_json"])),
        prepared=(
            OrderPreparation.model_validate_json(str(row["prepared_json"]))
            if row.get("prepared_json") is not None
            else None
        ),
        preparation_attempt_id=(
            str(row["preparation_attempt_id"])
            if row.get("preparation_attempt_id") is not None
            else None
        ),
        created_at=str(row["created_at"]),
        invalidated_at=(
            str(row["invalidated_at"])
            if row.get("invalidated_at") is not None
            else None
        ),
    )


def _order_from_row(row: dict[str, object]) -> OrderRecord:
    return OrderRecord(
        order_id=str(row["order_id"]),
        subject_id=str(row["subject_id"]),
        preparation_id=str(row["preparation_id"]),
        provider_order_digest=str(row["provider_order_digest"]),
        payload_digest=str(row["payload_digest"]),
        payload=PreparationPayload.model_validate_json(str(row["payload_json"])),
        lifecycle=CommerceLifecycleState.model_validate_json(
            str(row["lifecycle_json"])
        ),
        placement_attempt_id=(
            str(row["placement_attempt_id"])
            if row.get("placement_attempt_id") is not None
            else None
        ),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def _snapshot_from_row(row: dict[str, object]) -> MaterialSnapshot:
    return MaterialSnapshot(
        snapshot_id=str(row["snapshot_id"]),
        subject_id=str(row["subject_id"]),
        target_type=str(row["target_type"]),
        target_id=str(row["target_id"]),
        payload_digest=str(row["payload_digest"]),
        lifecycle=CommerceLifecycleState.model_validate_json(
            str(row["lifecycle_json"])
        ),
        cursor_digest=(
            str(row["cursor_digest"]) if row.get("cursor_digest") is not None else None
        ),
        observed_at=str(row["observed_at"]),
    )


def _attempt_from_row(row: dict[str, object]) -> CommerceAttempt:
    return CommerceAttempt(
        attempt_id=str(row["attempt_id"]),
        subject_id=str(row["subject_id"]),
        kind=str(row["kind"]),
        target_id=str(row["target_id"]),
        operation=str(row["operation"]),
        idempotency_key=str(row["idempotency_key"]),
        request_digest=str(row["request_digest"]),
        authorization_hash=(
            str(row["authorization_hash"])
            if row.get("authorization_hash") is not None
            else None
        ),
        attempt_count=int(row.get("attempt_count") or 0),
        state=str(row["state"]),
        response_digest=(
            str(row["response_digest"])
            if row.get("response_digest") is not None
            else None
        ),
        provider_reference_digest=(
            str(row["provider_reference_digest"])
            if row.get("provider_reference_digest") is not None
            else None
        ),
        active=bool(row["active"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


_SCHEMA_DDL = (
    """
    CREATE TABLE IF NOT EXISTS commerce_attempts (
        attempt_id TEXT PRIMARY KEY,
        subject_id TEXT NOT NULL,
        kind TEXT NOT NULL,
        target_id TEXT NOT NULL,
        operation TEXT NOT NULL,
        idempotency_key TEXT NOT NULL,
        request_digest TEXT NOT NULL,
        authorization_hash TEXT,
        attempt_count INTEGER NOT NULL DEFAULT 0,
        state TEXT NOT NULL,
        response_digest TEXT,
        provider_reference_digest TEXT,
        active INTEGER NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE(subject_id, kind, idempotency_key)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS commerce_preparations (
        preparation_id TEXT PRIMARY KEY,
        subject_id TEXT NOT NULL,
        payload_digest TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        prepared_json TEXT,
        preparation_attempt_id TEXT,
        created_at TEXT NOT NULL,
        invalidated_at TEXT,
        UNIQUE(subject_id, preparation_id),
        FOREIGN KEY(preparation_attempt_id) REFERENCES commerce_attempts(attempt_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS commerce_orders (
        order_id TEXT PRIMARY KEY,
        subject_id TEXT NOT NULL,
        preparation_id TEXT NOT NULL UNIQUE,
        provider_order_digest TEXT NOT NULL,
        payload_digest TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        lifecycle_json TEXT NOT NULL,
        placement_attempt_id TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE(subject_id, order_id),
        FOREIGN KEY(subject_id, preparation_id)
            REFERENCES commerce_preparations(subject_id, preparation_id),
        FOREIGN KEY(placement_attempt_id) REFERENCES commerce_attempts(attempt_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS commerce_material_snapshots (
        snapshot_id TEXT PRIMARY KEY,
        subject_id TEXT NOT NULL,
        target_type TEXT NOT NULL,
        target_id TEXT NOT NULL,
        payload_digest TEXT NOT NULL,
        lifecycle_json TEXT NOT NULL,
        cursor_digest TEXT,
        observed_at TEXT NOT NULL,
        UNIQUE(subject_id, target_type, target_id, payload_digest)
    )
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS idx_commerce_attempt_stable_target
    ON commerce_attempts(subject_id, kind, target_id)
    WHERE kind IN ('preparation', 'placement')
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS idx_commerce_active_action
    ON commerce_attempts(subject_id, target_id)
    WHERE kind = 'action' AND active = 1
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_commerce_snapshots_target
    ON commerce_material_snapshots(subject_id, target_type, target_id, observed_at)
    """,
)


__all__ = ["PostgresCommerceOrderStore", "SQLiteCommerceOrderStore"]
