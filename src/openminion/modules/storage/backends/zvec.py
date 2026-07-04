from __future__ import annotations

import json
import math
import threading
from pathlib import Path
from typing import Any, Iterable

from openminion.base.time import utc_now_iso as _utc_now_iso
from openminion.base.version import OPENMINION_VERSION
from openminion.modules.storage.interfaces import (
    STORAGE_INTERFACE_VERSION,
    BackendDescriptor,
)
from openminion.modules.storage.record_store import RecordStoreSQLite


def _normalize_namespace(namespace: str | None) -> str:
    return str(namespace or "").strip()


def _ensure_vector(values: Iterable[Any]) -> list[float]:
    out: list[float] = []
    for value in values:
        if isinstance(value, bool):
            raise ValueError("vector values must be numeric")
        out.append(float(value))
    if not out:
        raise ValueError("vector cannot be empty")
    return out


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    if len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return float(dot / (norm_a * norm_b))


class ZvecVectorStore:
    """SQLite-backed vector store exposed under the `vector.zvec` backend id.

    This is a storage-module-native adapter that keeps vector persistence in SQLite
    while exposing the v1 VectorStoreInterface expected by modules.
    """

    contract_version = STORAGE_INTERFACE_VERSION

    def __init__(
        self,
        sqlite_path: str | Path,
        *,
        dimension: int | None = None,
        metric: str = "cosine",
        wal: bool = True,
    ) -> None:
        raw_path = str(sqlite_path).strip()
        if not raw_path:
            raise ValueError("sqlite_path is required for vector.zvec backend")
        self._record_store = RecordStoreSQLite(raw_path, wal=wal)
        self._conn = self._record_store.connection
        self._lock = threading.RLock()
        self._dimension = int(dimension) if dimension is not None else None
        self._metric = str(metric or "cosine").strip().lower()
        if self._metric != "cosine":
            raise ValueError(f"unsupported metric: {self._metric}")
        self._init_schema()

    def _init_schema(self) -> None:
        with self._conn:
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS zvec_vectors (
                    namespace TEXT NOT NULL,
                    vector_id TEXT NOT NULL,
                    vector_json TEXT NOT NULL,
                    vector_dim INTEGER NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (namespace, vector_id)
                )
                """
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_zvec_vectors_namespace ON zvec_vectors(namespace)"
            )

    def _validate_dimension(self, vector_dim: int) -> None:
        if self._dimension is None:
            self._dimension = int(vector_dim)
            return
        if int(self._dimension) != int(vector_dim):
            raise ValueError(
                f"vector dimension mismatch: expected={self._dimension}, got={vector_dim}"
            )

    def upsert(
        self,
        vectors: list[list[float]],
        metadata: list[dict[str, Any]],
        ids: list[str],
        namespace: str | None = None,
    ) -> None:
        if not (len(vectors) == len(metadata) == len(ids)):
            raise ValueError("vectors, metadata, and ids must have the same length")
        if not ids:
            return
        normalized_namespace = _normalize_namespace(namespace)
        now = _utc_now_iso()
        with self._lock, self._conn:
            for vector_id, vector, meta in zip(ids, vectors, metadata):
                normalized_id = str(vector_id or "").strip()
                if not normalized_id:
                    raise ValueError("vector id cannot be empty")
                normalized_vector = _ensure_vector(vector)
                self._validate_dimension(len(normalized_vector))
                normalized_meta = dict(meta or {})
                self._conn.execute(
                    """
                    INSERT INTO zvec_vectors(namespace, vector_id, vector_json, vector_dim, metadata_json, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(namespace, vector_id) DO UPDATE SET
                        vector_json=excluded.vector_json,
                        vector_dim=excluded.vector_dim,
                        metadata_json=excluded.metadata_json,
                        updated_at=excluded.updated_at
                    """,
                    (
                        normalized_namespace,
                        normalized_id,
                        json.dumps(
                            normalized_vector, ensure_ascii=True, separators=(",", ":")
                        ),
                        len(normalized_vector),
                        json.dumps(normalized_meta, ensure_ascii=True, sort_keys=True),
                        now,
                    ),
                )

    def search(
        self,
        query_vector: list[float],
        top_k: int = 10,
        filters: dict[str, Any] | None = None,
        namespace: str | None = None,
    ) -> list[dict[str, Any]]:
        normalized_query = _ensure_vector(query_vector)
        self._validate_dimension(len(normalized_query))
        normalized_namespace = _normalize_namespace(namespace)
        safe_top_k = max(1, int(top_k))
        filter_payload = dict(filters or {})
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT vector_id, vector_json, metadata_json
                FROM zvec_vectors
                WHERE namespace = ?
                """,
                (normalized_namespace,),
            ).fetchall()
        scored: list[dict[str, Any]] = []
        for row in rows:
            candidate_vector = _ensure_vector(json.loads(str(row["vector_json"])))
            candidate_meta = json.loads(str(row["metadata_json"]))
            if not isinstance(candidate_meta, dict):
                candidate_meta = {}
            if filter_payload:
                matched = True
                for key, expected_value in filter_payload.items():
                    if candidate_meta.get(key) != expected_value:
                        matched = False
                        break
                if not matched:
                    continue
            score = _cosine_similarity(normalized_query, candidate_vector)
            scored.append(
                {
                    "id": str(row["vector_id"]),
                    "score": score,
                    "metadata": candidate_meta,
                    "namespace": normalized_namespace,
                }
            )
        scored.sort(key=lambda item: float(item.get("score", 0.0)), reverse=True)
        return scored[:safe_top_k]

    def delete(self, ids: list[str], namespace: str | None = None) -> bool:
        normalized_namespace = _normalize_namespace(namespace)
        normalized_ids = [
            str(item or "").strip() for item in ids if str(item or "").strip()
        ]
        if not normalized_ids:
            return True
        placeholders = ",".join("?" for _ in normalized_ids)
        with self._lock, self._conn:
            self._conn.execute(
                f"DELETE FROM zvec_vectors WHERE namespace = ? AND vector_id IN ({placeholders})",
                [normalized_namespace, *normalized_ids],
            )
        return True

    def list_namespaces(self) -> list[str]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT DISTINCT namespace FROM zvec_vectors ORDER BY namespace ASC"
            ).fetchall()
        return [str(row["namespace"]) for row in rows]

    def namespace_stats(self, namespace: str) -> dict[str, Any]:
        normalized_namespace = _normalize_namespace(namespace)
        with self._lock:
            row = self._conn.execute(
                """
                SELECT COUNT(*) AS count, COALESCE(MAX(vector_dim), 0) AS vector_dim
                FROM zvec_vectors
                WHERE namespace = ?
                """,
                (normalized_namespace,),
            ).fetchone()
        return {
            "namespace": normalized_namespace,
            "count": int(row["count"] if row is not None else 0),
            "dimension": int(row["vector_dim"] if row is not None else 0),
        }

    def count(self, namespace: str | None = None) -> int:
        normalized_namespace = _normalize_namespace(namespace)
        with self._lock:
            if namespace is None:
                row = self._conn.execute(
                    "SELECT COUNT(*) AS count FROM zvec_vectors"
                ).fetchone()
            else:
                row = self._conn.execute(
                    "SELECT COUNT(*) AS count FROM zvec_vectors WHERE namespace = ?",
                    (normalized_namespace,),
                ).fetchone()
        return int(row["count"] if row is not None else 0)

    def healthcheck(self) -> dict[str, Any]:
        with self._lock:
            row = self._conn.execute("SELECT 1 AS ok").fetchone()
        return {
            "ok": bool(row and int(row["ok"]) == 1),
            "backend_id": "vector.zvec",
            "metric": self._metric,
            "dimension": self._dimension,
        }

    def describe_backend(self) -> BackendDescriptor:
        return BackendDescriptor(
            backend_id="vector.zvec",
            version=OPENMINION_VERSION,
            planes_supported={"vector"},
            capabilities={
                "metric": self._metric,
                "persistent": True,
                "filters": "metadata-equality",
            },
            limits={
                "dimension": int(self._dimension or 0),
            },
        )

    def close(self) -> None:
        with self._lock:
            close_fn = getattr(self._record_store, "close", None)
            if callable(close_fn):
                close_fn()
