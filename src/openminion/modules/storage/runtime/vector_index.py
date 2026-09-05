import hashlib
import json
import logging
import math
import struct
import time
from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openminion.base.config.env import (
    EnvironmentConfig,
    resolve_environment_config_with_explicit_env,
)
from .sqlite import connect_database
from . import migrations


@dataclass
class EmbeddingResult:
    """Represents an embedding result."""

    vector: list[float]
    provider: str
    model: str
    timestamp: str = ""
    token_usage: dict[str, int] | None = None


@dataclass(frozen=True)
class VectorSpaceIdentity:
    provider: str
    model: str
    dimension: int
    renderer_version: str = "memory-canonical-v1"

    @property
    def key(self) -> str:
        return ":".join(
            (self.provider, self.model, str(self.dimension), self.renderer_version)
        )


class EmbeddingProvider(ABC):
    """Abstract base class for embedding providers."""

    @abstractmethod
    def embed(self, text: str) -> EmbeddingResult:
        """Generate embedding for a single text."""
        pass

    @abstractmethod
    def embed_batch(self, texts: list[str]) -> "EmbeddingBatchResult":
        """Generate embeddings for a batch of texts."""
        pass

    @property
    def semantic_ready(self) -> bool:
        return False

    @property
    @abstractmethod
    def identity(self) -> VectorSpaceIdentity: ...


@dataclass
class EmbeddingBatchResult:
    """Results of a batch embedding operation."""

    results: list[EmbeddingResult]


def _cosine_similarity(left: list[float], right: list[float]) -> float:
    dot_product = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    return dot_product / (left_norm * right_norm) if left_norm and right_norm else 0.0


class LocalEmbeddingProvider(EmbeddingProvider):
    """Local sentence-transformers embedding provider."""

    def __init__(
        self,
        model: str = "all-MiniLM-L6-v2",
        dimension: int = 384,
        *,
        env: EnvironmentConfig | Mapping[str, Any] | None = None,
    ):
        del env
        self.model = model
        self.provider = "sentence-transformers"
        self.dimension = dimension
        self._st_model: Any = None

    def _sentence_transformer(self) -> Any:
        if self._st_model is None:
            from sentence_transformers import SentenceTransformer

            self._st_model = SentenceTransformer(self.model)
        return self._st_model

    @property
    def semantic_ready(self) -> bool:
        try:
            model = self._sentence_transformer()
        except (ImportError, ModuleNotFoundError):
            return False
        return int(model.get_sentence_embedding_dimension()) == self.dimension

    @property
    def identity(self) -> VectorSpaceIdentity:
        return VectorSpaceIdentity(
            provider=self.provider,
            model=self.model,
            dimension=self.dimension,
        )

    def embed(self, text: str) -> EmbeddingResult:
        encoded = self._sentence_transformer().encode(text, normalize_embeddings=True)
        vector = [float(value) for value in encoded]
        if len(vector) != self.dimension:
            raise ValueError(
                f"Embedding dimension {len(vector)} does not match configured "
                f"dimension {self.dimension}"
            )

        return EmbeddingResult(
            vector=vector,
            provider=self.provider,
            model=self.model,
        )

    def embed_batch(self, texts: list[str]) -> EmbeddingBatchResult:
        return EmbeddingBatchResult(results=[self.embed(text) for text in texts])


class InMemoryVectorIndex:
    """Simple in-memory vector index for testing."""

    def __init__(self, dim: int = 384):
        self.dim = dim
        self.vectors: dict[str, list[float]] = {}
        self.metadata: dict[str, dict] = {}

    def add_vectors(
        self,
        ids: list[str],
        vectors: list[list[float]],
        metadata_list: list[dict] | None = None,
    ) -> None:
        if metadata_list is None:
            metadata_list = [{} for _ in ids]

        for i, (id_, vector, metadata) in enumerate(zip(ids, vectors, metadata_list)):
            if len(vector) != self.dim:
                raise ValueError(
                    f"Vector {i} has dimension {len(vector)}, expected {self.dim}"
                )
            self.vectors[id_] = vector
            self.metadata[id_] = metadata

    def search(
        self,
        query_vector: list[float],
        top_k: int = 10,
        filters: dict | None = None,
    ) -> list[tuple[str, float, dict]]:
        if len(query_vector) != self.dim:
            raise ValueError(
                f"Query vector has dimension {len(query_vector)}, expected {self.dim}"
            )

        # Calculate cosine similarity scores
        results = []
        for vector_id, stored_vector in self.vectors.items():
            similarity = _cosine_similarity(query_vector, stored_vector)

            if filters:
                meta = self.metadata[vector_id]
                if any(meta.get(key) != value for key, value in filters.items()):
                    continue

            results.append((vector_id, similarity, self.metadata[vector_id]))

        results.sort(key=lambda x: x[1], reverse=True)
        return results[:top_k]

    def get_vector(self, vector_id: str) -> list[float] | None:
        return self.vectors.get(vector_id)

    def has_vector(self, vector_id: str) -> bool:
        return vector_id in self.vectors

    def delete_vectors(self, ids: list[str]) -> None:
        for vector_id in ids:
            self.vectors.pop(vector_id, None)
            self.metadata.pop(vector_id, None)

    def clear(self) -> None:
        self.vectors.clear()
        self.metadata.clear()


class VectorIndexBackend(ABC):
    """Abstract vector index backend."""

    @abstractmethod
    def add_vectors(
        self,
        ids: list[str],
        vectors: list[list[float]],
        metadata_list: list[dict] | None = None,
    ) -> None:
        """Add vectors to the index."""
        pass

    @abstractmethod
    def search(
        self,
        query_vector: list[float],
        top_k: int = 10,
        filters: dict | None = None,
    ) -> list[tuple[str, float, dict]]:
        """Search for similar vectors."""
        pass

    @abstractmethod
    def get_vector(self, vector_id: str) -> list[float] | None:
        """Get a single vector by ID."""
        pass

    @abstractmethod
    def delete_vectors(self, ids: list[str]) -> None:
        """Delete vectors by IDs."""
        pass


class SQLiteVecBackend(VectorIndexBackend):
    """SQLite-backed vector store."""

    def __init__(
        self,
        db_path: str,
        dimension: int,
        collection_name: str = "default_collection",
        *,
        env: EnvironmentConfig | Mapping[str, Any] | None = None,
    ):
        self.db_path = str(Path(db_path).resolve())
        self.dimension = dimension
        self.collection_name = collection_name
        self._env = resolve_environment_config_with_explicit_env(env)
        self.conn = connect_database(self.db_path, env=self._env)

        # Run shared migrations before creating vector-specific tables.
        migrations.run_migrations(self.conn)

        self._init_tables()

    def _init_tables(self):
        cursor = self.conn.cursor()
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS vector_collections (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL UNIQUE,
            dimension INTEGER NOT NULL,
            metadata_json TEXT DEFAULT '{}',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """)

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS vector_entries (
            id TEXT PRIMARY KEY,
            collection_name TEXT NOT NULL,
            embedding BLOB NOT NULL,
            metadata_json TEXT DEFAULT '{}',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (collection_name) REFERENCES vector_collections(name)
        )
        """)

        cursor.execute(
            "INSERT OR IGNORE INTO vector_collections (id, name, dimension) VALUES (?, ?, ?)",
            (self.collection_name, self.collection_name, self.dimension),
        )

        self.conn.commit()

    def add_vectors(
        self,
        ids: list[str],
        vectors: list[list[float]],
        metadata_list: list[dict] | None = None,
    ) -> None:
        if metadata_list is None:
            metadata_list = [{} for _ in ids]

        cursor = self.conn.cursor()
        for vector_id, vector, metadata in zip(ids, vectors, metadata_list):
            if len(vector) != self.dimension:
                raise ValueError(
                    f"Vector dimension is {len(vector)}, expected {self.dimension}"
                )

            embedding_blob = self._vector_to_blob(vector)

            cursor.execute(
                """
                INSERT OR REPLACE INTO vector_entries
                (id, collection_name, embedding, metadata_json, updated_at)
                VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
            """,
                (vector_id, self.collection_name, embedding_blob, json.dumps(metadata)),
            )

        self.conn.commit()

    def search(
        self,
        query_vector: list[float],
        top_k: int = 10,
        filters: dict | None = None,
    ) -> list[tuple[str, float, dict]]:
        """Search using cosine similarity with SQLite (naive implementation)."""
        if len(query_vector) != self.dimension:
            raise ValueError(
                f"Query vector dimension is {len(query_vector)}, expected {self.dimension}"
            )

        cursor = self.conn.cursor()
        cursor.execute(
            """
        SELECT id, embedding, metadata_json
        FROM vector_entries
        WHERE collection_name = ?
        """,
            (self.collection_name,),
        )

        results = []

        for row in cursor.fetchall():
            vector_id, embedding_blob, metadata_json = row
            stored_vector = self._blob_to_vector(embedding_blob)

            similarity = _cosine_similarity(query_vector, stored_vector)

            metadata = json.loads(metadata_json)

            if filters and any(
                metadata.get(key) != value for key, value in filters.items()
            ):
                continue

            results.append((vector_id, similarity, metadata))

        results.sort(key=lambda x: x[1], reverse=True)
        return results[:top_k]

    def get_vector(self, vector_id: str) -> list[float] | None:
        cursor = self.conn.cursor()
        cursor.execute(
            """
        SELECT embedding FROM vector_entries
        WHERE id = ? AND collection_name = ?
        """,
            (vector_id, self.collection_name),
        )

        row = cursor.fetchone()
        return self._blob_to_vector(row[0]) if row else None

    def get_metadata(self, vector_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            """
            SELECT metadata_json FROM vector_entries
            WHERE id = ? AND collection_name = ?
            """,
            (vector_id, self.collection_name),
        ).fetchone()
        return None if row is None else dict(json.loads(row[0]))

    def delete_vectors(self, ids: list[str]) -> None:
        if not ids:
            return

        placeholders = ",".join("?" * len(ids))
        cursor = self.conn.cursor()
        cursor.execute(
            f"""
        DELETE FROM vector_entries
        WHERE id IN ({placeholders})
        AND collection_name = ?
        """,
            ids + [self.collection_name],
        )

        self.conn.commit()

    @staticmethod
    def _vector_to_blob(vector: list[float]) -> bytes:
        return struct.pack(f"{len(vector)}f", *vector)

    @staticmethod
    def _blob_to_vector(blob: bytes) -> list[float]:
        float_count = len(blob) // 4  # 4 bytes per float
        return list(struct.unpack(f"{float_count}f", blob))


class QdrantVectorBackend(VectorIndexBackend):
    """Qdrant vector backend."""

    def __init__(
        self,
        collection_name: str,
        url: str,
        api_key: str | None = None,
        dimension: int = 384,
    ):
        self.collection_name = collection_name
        self.url = url
        self.api_key = api_key
        self.dimension = dimension
        self._client = None
        self._qdrant_available = None

    def _ensure_client(self):
        """Ensure the Qdrant client is available and connected."""
        if self._qdrant_available is not None and not self._qdrant_available:
            raise RuntimeError(
                "qdrant-client not available - install qdrant-client package"
            )

        if self._client is not None:
            return

        try:
            from qdrant_client import QdrantClient
        except ImportError:
            self._qdrant_available = False
            raise RuntimeError(
                "qdrant-client not available - install qdrant-client package"
            )

        self._qdrant_available = True

        if self.url.startswith("http://") or self.url.startswith("https://"):
            import urllib.parse

            parsed = urllib.parse.urlparse(self.url)
            self._client = QdrantClient(
                url=f"{parsed.scheme}://{parsed.hostname}"
                + (f":{parsed.port}" if parsed.port else ""),
                api_key=self.api_key,
                https=(parsed.scheme == "https"),
                port=parsed.port
                if parsed.port
                else (443 if parsed.scheme == "https" else 6333),
            )
        else:
            import re

            match = re.match(r"^([^:]+)(?::(\d+))?$", self.url)
            if match:
                host = match.group(1)
                port = int(match.group(2)) if match.group(2) else 6333
                self._client = QdrantClient(host=host, port=port, api_key=self.api_key)
            else:
                self._client = QdrantClient(host=self.url, api_key=self.api_key)

        self._ensure_collection()

    def _ensure_collection(self):
        """Ensure the collection exists with correct vector settings."""
        from qdrant_client.http import models

        try:
            self._client.get_collection(self.collection_name)
        except Exception:
            self._client.create_collection(
                collection_name=self.collection_name,
                vectors_config=models.VectorParams(
                    size=self.dimension, distance=models.Distance.COSINE
                ),
            )

    def add_vectors(
        self,
        ids: list[str],
        vectors: list[list[float]],
        metadata_list: list[dict] | None = None,
    ) -> None:
        self._ensure_client()

        if metadata_list is None:
            metadata_list = [{} for _ in vectors]

        from qdrant_client.http import models

        points = []
        for i, (vector_id, vector, metadata) in enumerate(
            zip(ids, vectors, metadata_list)
        ):
            if len(vector) != self.dimension:
                raise ValueError(
                    f"Vector {i} has dimension {len(vector)}, expected {self.dimension}"
                )

            points.append(
                models.PointStruct(id=vector_id, vector=vector, payload=metadata)
            )

        self._client.upsert(collection_name=self.collection_name, points=points)

    def search(
        self,
        query_vector: list[float],
        top_k: int = 10,
        filters: dict | None = None,
    ) -> list[tuple[str, float, dict]]:
        """Search in Qdrant."""
        self._ensure_client()

        if len(query_vector) != self.dimension:
            raise ValueError(
                f"Query vector has dimension {len(query_vector)}, expected {self.dimension}"
            )

        from qdrant_client.http import models

        qdrant_filters = None
        if filters:
            filter_conditions = []
            for key, value in filters.items():
                filter_conditions.append(
                    models.FieldCondition(key=key, match=models.MatchValue(value=value))
                )
            if filter_conditions:
                qdrant_filters = models.Filter(must=filter_conditions)

        search_results = self._client.search(
            collection_name=self.collection_name,
            query_vector=query_vector,
            limit=top_k,
            query_filter=qdrant_filters,
        )

        return [
            (result.id, float(result.score), result.payload or {})
            for result in search_results
        ]

    def get_vector(self, vector_id: str) -> list[float] | None:
        self._ensure_client()

        results = self._client.retrieve(
            collection_name=self.collection_name, ids=[vector_id], with_vectors=True
        )

        result = results[0] if results else None
        return getattr(result, "vector", None) or None

    def delete_vectors(self, ids: list[str]) -> None:
        self._ensure_client()

        from qdrant_client.http import models

        self._client.delete(
            collection_name=self.collection_name,
            points_selector=models.PointIdsList(points=ids),
        )


class VectorIndexAdapter:
    """Adapter layer between embedding providers and backends."""

    def __init__(
        self,
        embedding_provider: EmbeddingProvider,
        vector_index: VectorIndexBackend,
        batch_size: int = 32,
        search_k: int = 10,
    ):
        self.embedding_provider = embedding_provider
        self.__vector_index = vector_index
        self.batch_size = batch_size
        self.search_k = search_k
        self._record_source: Any | None = None
        self._initial_sync_complete = False

    @property
    def semantic_ready(self) -> bool:
        return (
            self._initial_sync_complete
            and isinstance(self.__vector_index, SQLiteVecBackend)
            and bool(self.embedding_provider.semantic_ready)
        )

    @property
    def vector_space_identity(self) -> VectorSpaceIdentity:
        return self.embedding_provider.identity

    def bind_record_source(self, source: Any) -> None:
        self._record_source = source

    def get_vector(self, record_id: str) -> list[float] | None:
        metadata_reader = getattr(self.__vector_index, "get_metadata", None)
        metadata = metadata_reader(record_id) if callable(metadata_reader) else None
        if not metadata or (
            metadata.get("vector_space_identity") != self.vector_space_identity.key
        ):
            return None
        return self.__vector_index.get_vector(record_id)

    @property
    def _vector_index(self):
        return self.__vector_index

    def index_record(self, record: Any, content: str) -> str:
        embedding_result = self.embedding_provider.embed(content)

        vector_id = getattr(record, "id", f"record_{int(time.time())}")

        fingerprint = hashlib.sha256(content.encode()).hexdigest()
        self.__vector_index.add_vectors(
            [vector_id],
            [embedding_result.vector],
            [
                {
                    "record_id": str(vector_id),
                    "content_fingerprint": fingerprint,
                    "vector_space_identity": self.vector_space_identity.key,
                }
            ],
        )

        return vector_id

    def index_records_batch(self, records: list[Any], contents: list[str]) -> None:
        if len(records) != len(contents):
            raise ValueError("Records and contents must have the same length")

        for i in range(0, len(records), self.batch_size):
            batch_records = records[i : i + self.batch_size]
            batch_contents = contents[i : i + self.batch_size]

            embeddings = self.embedding_provider.embed_batch(batch_contents)

            vector_ids = [
                str(getattr(record, "id", i + j))
                for j, record in enumerate(batch_records)
            ]
            vectors = [result.vector for result in embeddings.results]
            metadata = [
                {
                    "record_id": str(getattr(record, "id", i + j)),
                    "content_fingerprint": hashlib.sha256(content.encode()).hexdigest(),
                    "vector_space_identity": self.vector_space_identity.key,
                }
                for j, (record, content) in enumerate(
                    zip(batch_records, batch_contents)
                )
            ]

            self.__vector_index.add_vectors(vector_ids, vectors, metadata)

    def search(
        self, query: str, top_k: int | None = None
    ) -> list[tuple[Any, float, dict]]:
        query_embedding = self.embedding_provider.embed(query)

        search_results = self.__vector_index.search(
            query_embedding.vector, top_k=top_k or self.search_k
        )

        return search_results

    def sync_pending_records(self) -> int:
        if self._record_source is None:
            raise RuntimeError("Vector record source is not bound")
        if not self.embedding_provider.semantic_ready:
            raise RuntimeError("Local semantic embedding provider is unavailable")
        snapshot = self._record_source.vector_sync_snapshot()
        pending_records: list[Any] = []
        pending_contents: list[str] = []
        for item in snapshot.get("current", []):
            record_id = str(item["record_id"])
            metadata_reader = getattr(self.__vector_index, "get_metadata", None)
            metadata = metadata_reader(record_id) if callable(metadata_reader) else None
            if metadata and (
                metadata.get("content_fingerprint") == item["content_fingerprint"]
                and metadata.get("vector_space_identity")
                == self.vector_space_identity.key
            ):
                continue
            pending_records.append(type("VectorRecord", (), {"id": record_id})())
            pending_contents.append(str(item["text"]))
        if pending_records:
            self.index_records_batch(pending_records, pending_contents)
        processed = len(pending_records)
        retired_ids = [str(item) for item in snapshot.get("retired", [])]
        if retired_ids:
            self.__vector_index.delete_vectors(retired_ids)
            processed += len(retired_ids)
        self._initial_sync_complete = True
        return processed


def create_vector_index_adapter(
    db_path: str | Path,
    embedding_provider: EmbeddingProvider,
    vector_index: VectorIndexBackend,
    *,
    batch_size: int = 32,
    env: EnvironmentConfig | Mapping[str, Any] | None = None,
) -> VectorIndexAdapter:
    """Create a vector index adapter after storage migrations are current."""
    conn = connect_database(db_path, env=env)
    migrations.run_migrations(conn)
    conn.close()

    return VectorIndexAdapter(
        embedding_provider=embedding_provider,
        vector_index=vector_index,
        batch_size=batch_size,
    )


def reindex_vectors(
    db_path: str,
    vector_adapter: VectorIndexAdapter,
    *,
    env: EnvironmentConfig | Mapping[str, Any] | None = None,
) -> int:
    """Reindex all memory_records to memory_vectors in batches."""
    from .memory_store import _row_to_memory_record

    logger = logging.getLogger(__name__)

    conn = connect_database(db_path, env=env)
    cursor = conn.cursor()

    cursor.execute("""
        SELECT * FROM memory_records
        WHERE content IS NOT NULL
        ORDER BY id
    """)

    rows = cursor.fetchall()
    conn.close()

    if not rows:
        logger.info("No records found in memory_records to reindex")
        return 0

    records = []
    contents = []
    total_processed = 0

    batch_size = 32

    logger.info("Starting reindex of %d memory_records", len(rows))

    for row in rows:
        record = _row_to_memory_record(row)

        records.append(record)
        contents.append(record.content)

        if len(records) >= batch_size:
            vector_adapter.index_records_batch(records, contents)

            total_processed += len(records)
            logger.info(
                f"Processed {total_processed}/{len(rows)} records for reindexing"
            )

            records = []
            contents = []

    if records:
        vector_adapter.index_records_batch(records, contents)
        total_processed += len(records)
        logger.info(f"Processed final batch - total: {total_processed}")

    logger.info(f"Reindex completed - {total_processed} records indexed")
    return total_processed
