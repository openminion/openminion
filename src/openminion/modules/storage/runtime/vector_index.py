import hashlib
import json
import logging
import math
import re
import struct
import time
from abc import ABC, abstractmethod
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openminion.base.config.env import (
    EnvironmentConfig,
    resolve_environment_config_with_explicit_env,
)
from ..config import VECTOR_INDEX_CHAR_NGRAM_MAX, VECTOR_INDEX_CHAR_NGRAM_MIN
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


@dataclass
class EmbeddingBatchResult:
    """Results of a batch embedding operation."""

    results: list[EmbeddingResult]


_TOKEN_RE = re.compile(r"[a-z0-9]+")
_SENTENCE_TRANSFORMERS_ENV = "OPENMINION_ENABLE_SENTENCE_TRANSFORMERS"


def _tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.lower())


def _normalized_text(text: str) -> str:
    return " ".join(_tokenize(text))


def _feature_hash_index(value: str, dim: int) -> tuple[int, float]:
    digest = hashlib.sha256(value.encode("utf-8")).digest()
    index = int.from_bytes(digest[:4], "big") % max(1, dim)
    sign = -1.0 if (digest[4] & 1) else 1.0
    return index, sign


def _iter_embedding_features(text: str) -> Iterator[str]:
    normalized = _normalized_text(text)
    if not normalized:
        yield "__empty__"
        return

    tokens = normalized.split()
    for token in tokens:
        yield f"tok:{token}"

    for left, right in zip(tokens, tokens[1:]):
        yield f"bi:{left}_{right}"

    padded = f" {normalized} "
    for size in range(VECTOR_INDEX_CHAR_NGRAM_MIN, VECTOR_INDEX_CHAR_NGRAM_MAX + 1):
        if len(padded) < size:
            continue
        for start in range(len(padded) - size + 1):
            gram = padded[start : start + size]
            if gram.strip():
                yield f"char:{size}:{gram}"


def _l2_normalize(values: list[float]) -> list[float]:
    norm = math.sqrt(sum(v * v for v in values))
    if norm <= 0.0:
        if values:
            values[0] = 1.0
        return values
    return [v / norm for v in values]


def _cosine_similarity(left: list[float], right: list[float]) -> float:
    dot_product = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    return dot_product / (left_norm * right_norm) if left_norm and right_norm else 0.0


class LocalEmbeddingProvider(EmbeddingProvider):
    """Local embedding provider with a no-dependency feature-hashing fallback."""

    def __init__(
        self,
        model: str = "all-MiniLM-L6-v2",
        dimension: int = 384,
        *,
        env: EnvironmentConfig | Mapping[str, Any] | None = None,
    ):
        self.model = model
        self.provider = "local"
        self.dimension = dimension
        self._env = resolve_environment_config_with_explicit_env(env)
        self._st_model: Any = None
        self._st_checked = False

    def _sentence_transformers_enabled(self) -> bool:
        raw = str(self._env.get(_SENTENCE_TRANSFORMERS_ENV, "")).strip().lower()
        return raw not in {"0", "false", "off", "no"}

    def _ensure_sentence_transformer(self) -> bool:
        if self._st_checked:
            return self._st_model is not None
        self._st_checked = True
        if not self._sentence_transformers_enabled():
            self._st_model = None
            return False
        try:
            from sentence_transformers import SentenceTransformer  # type: ignore[import-not-found]

            self._st_model = SentenceTransformer(self.model)
        except Exception:
            self._st_model = None
        return self._st_model is not None

    def _embed_fallback(self, text: str) -> list[float]:
        vector = [0.0] * self.dimension
        for feature in _iter_embedding_features(text):
            index, sign = _feature_hash_index(feature, self.dimension)
            vector[index] += sign

        return _l2_normalize(vector)

    def embed(self, text: str) -> EmbeddingResult:
        vector: list[float]
        if self._ensure_sentence_transformer():
            encoded = self._st_model.encode(text, normalize_embeddings=True)
            vector = [float(v) for v in encoded]
            if len(vector) != self.dimension:
                if len(vector) > self.dimension:
                    vector = vector[: self.dimension]
                else:
                    vector.extend([0.0] * (self.dimension - len(vector)))
                vector = _l2_normalize(vector)
        else:
            vector = self._embed_fallback(text)

        return EmbeddingResult(
            vector=vector,
            provider=self.provider,
            model=self.model,
        )

    def embed_batch(self, texts: list[str]) -> EmbeddingBatchResult:
        return EmbeddingBatchResult(results=[self.embed(text) for text in texts])


class APIEmbeddingProvider(EmbeddingProvider):
    """API-based embedding provider (e.g., OpenAI)."""

    def __init__(
        self,
        api_key: str,
        model: str = "text-embedding-3-small",
        base_url: str = "https://api.openai.com/v1",
    ):
        self.api_key = api_key
        self.model = model
        self.base_url = base_url
        self.provider = "api"

        # Deterministic fallback until a real HTTP client is wired here.

    def embed(self, text: str) -> EmbeddingResult:
        text_hash = hashlib.md5((text + self.model).encode()).hexdigest()

        vector = []
        seed = int(text_hash[:16], 16)
        for i in range(1536):  # Standard OpenAI embedding dim
            value = ((seed + i) % 1013) / 1013.0
            value = (value * 2) - 1
            vector.append(value)

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

    @property
    def _vector_index(self):
        return self.__vector_index

    def index_record(self, record: Any, content: str) -> str:
        embedding_result = self.embedding_provider.embed(content)

        vector_id = getattr(record, "id", f"record_{int(time.time())}")

        self.__vector_index.add_vectors(
            [vector_id], [embedding_result.vector], [{"source": content}]
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
                    "source": content,
                    "record_id": str(getattr(record, "id", i + j)),
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


def create_vector_index_adapter(
    db_path: str | Path,
    embedding_provider: EmbeddingProvider,
    vector_index: VectorIndexBackend,
    *,
    env: EnvironmentConfig | Mapping[str, Any] | None = None,
) -> VectorIndexAdapter:
    """Create a vector index adapter after storage migrations are current."""
    conn = connect_database(db_path, env=env)
    migrations.run_migrations(conn)
    conn.close()

    return VectorIndexAdapter(
        embedding_provider=embedding_provider,
        vector_index=vector_index,
    )


class MockEmbeddingProvider(EmbeddingProvider):
    """Mock embedding provider for testing."""

    def embed(self, text: str) -> EmbeddingResult:
        text_hash = hashlib.md5(text.encode()).hexdigest()
        seed = int(text_hash[:16], 16)

        vector = [(seed ^ i) % 1000 / 1000.0 for i in range(128)]

        return EmbeddingResult(
            vector=vector,
            provider="mock",
            model="mock-model",
        )

    def embed_batch(self, texts: list[str]) -> EmbeddingBatchResult:
        return EmbeddingBatchResult(results=[self.embed(text) for text in texts])


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
