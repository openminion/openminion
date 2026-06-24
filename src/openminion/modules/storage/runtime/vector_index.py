import hashlib
import json
import math
import re
import struct
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterator, List, Mapping, Optional
import time

from openminion.base.config.env import (
    EnvironmentConfig,
    resolve_environment_config_with_explicit_env,
)
from ..config import VECTOR_INDEX_CHAR_NGRAM_MAX, VECTOR_INDEX_CHAR_NGRAM_MIN
from .sqlite import connect_database
from .migrations import run_migrations


@dataclass
class EmbeddingResult:
    """Represents an embedding result."""

    vector: List[float]
    provider: str
    model: str
    timestamp: str = ""
    token_usage: Optional[Dict[str, int]] = None


class EmbeddingProvider(ABC):
    """Abstract base class for embedding providers."""

    @abstractmethod
    def embed(self, text: str) -> EmbeddingResult:
        """Generate embedding for a single text."""
        pass

    @abstractmethod
    def embed_batch(self, texts: List[str]) -> "EmbeddingBatchResult":
        """Generate embeddings for a batch of texts."""
        pass


@dataclass
class EmbeddingBatchResult:
    """Results of a batch embedding operation."""

    results: List[EmbeddingResult]


_TOKEN_RE = re.compile(r"[a-z0-9]+")
_SENTENCE_TRANSFORMERS_ENV = "OPENMINION_ENABLE_SENTENCE_TRANSFORMERS"


def _tokenize(text: str) -> List[str]:
    return _TOKEN_RE.findall((text or "").lower())


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


def _l2_normalize(values: List[float]) -> List[float]:
    norm = math.sqrt(sum(v * v for v in values))
    if norm <= 0.0:
        if values:
            values[0] = 1.0
        return values
    return [v / norm for v in values]


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
        self.dimension = int(dimension)
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

    def _embed_fallback(self, text: str) -> List[float]:
        vector = [0.0] * self.dimension
        for feature in _iter_embedding_features(text):
            index, sign = _feature_hash_index(feature, self.dimension)
            vector[index] += sign

        return _l2_normalize(vector)

    def embed(self, text: str) -> EmbeddingResult:
        """Generate embedding for single text."""
        vector: List[float]
        if self._ensure_sentence_transformer():
            encoded = self._st_model.encode(text or "", normalize_embeddings=True)
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

    def embed_batch(self, texts: List[str]) -> EmbeddingBatchResult:
        """Generate embeddings for a batch of texts."""
        results = [self.embed(text) for text in texts]
        return EmbeddingBatchResult(results=results)


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
        """Generate embedding for a single text via API."""
        import hashlib

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

    def embed_batch(self, texts: List[str]) -> EmbeddingBatchResult:
        """Generate embeddings for a batch of texts via API."""
        results = [self.embed(text) for text in texts]
        return EmbeddingBatchResult(results=results)


class InMemoryVectorIndex:
    """Simple in-memory vector index for testing."""

    def __init__(self, dim: int = 384):
        self.dim = dim
        self.vectors: Dict[str, List[float]] = {}
        self.metadata: Dict[str, dict] = {}

    def add_vectors(
        self,
        ids: List[str],
        vectors: List[List[float]],
        metadata_list: Optional[List[dict]] = None,
    ) -> None:
        """Add vectors to the index."""
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
        query_vector: List[float],
        top_k: int = 10,
        filters: Optional[dict] = None,
    ) -> List[tuple[str, float, dict]]:
        """Search for similar vectors."""
        if len(query_vector) != self.dim:
            raise ValueError(
                f"Query vector has dimension {len(query_vector)}, expected {self.dim}"
            )

        # Calculate cosine similarity scores
        results = []
        for vector_id, stored_vector in self.vectors.items():
            # Apply cosine similarity
            dot_product = sum(q * s for q, s in zip(query_vector, stored_vector))
            magn_query = sum(q * q for q in query_vector) ** 0.5
            magn_stored = sum(s * s for s in stored_vector) ** 0.5

            if magn_query == 0 or magn_stored == 0:
                similarity = 0.0
            else:
                similarity = dot_product / (magn_query * magn_stored)

            # Apply filter if present
            if filters:
                meta = self.metadata[vector_id]
                matches_filter = True
                for key, value in filters.items():
                    current_value = meta.get(key)
                    if current_value != value:
                        matches_filter = False
                        break
                if not matches_filter:
                    continue

            results.append((vector_id, similarity, self.metadata[vector_id]))

        # Sort by similarity (descending) and return top_k
        results.sort(key=lambda x: x[1], reverse=True)
        return results[:top_k]

    def get_vector(self, vector_id: str) -> Optional[List[float]]:
        """Get vector by ID."""
        return self.vectors.get(vector_id)

    def has_vector(self, vector_id: str) -> bool:
        """Check if a vector exists."""
        return vector_id in self.vectors

    def delete_vectors(self, ids: List[str]) -> None:
        """Delete vectors by ID."""
        for vector_id in ids:
            self.vectors.pop(vector_id, None)
            self.metadata.pop(vector_id, None)

    def clear(self) -> None:
        """Clear the index."""
        self.vectors.clear()
        self.metadata.clear()


class VectorIndexBackend(ABC):
    """Abstract vector index backend."""

    @abstractmethod
    def add_vectors(
        self,
        ids: List[str],
        vectors: List[List[float]],
        metadata_list: Optional[List[dict]] = None,
    ) -> None:
        """Add vectors to the index."""
        pass

    @abstractmethod
    def search(
        self,
        query_vector: List[float],
        top_k: int = 10,
        filters: Optional[dict] = None,
    ) -> List[tuple[str, float, dict]]:
        """Search for similar vectors."""
        pass

    @abstractmethod
    def get_vector(self, vector_id: str) -> Optional[List[float]]:
        """Get a single vector by ID."""
        pass

    @abstractmethod
    def delete_vectors(self, ids: List[str]) -> None:
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
        run_migrations(self.conn)

        self._init_tables()

    def _init_tables(self):
        """Initialize vector-specific tables."""
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
        ids: List[str],
        vectors: List[List[float]],
        metadata_list: Optional[List[dict]] = None,
    ) -> None:
        """Add vectors to SQLite store."""
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
        query_vector: List[float],
        top_k: int = 10,
        filters: Optional[dict] = None,
    ) -> List[tuple[str, float, dict]]:
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

        query_norm = sum(q * q for q in query_vector) ** 0.5
        results = []

        for row in cursor.fetchall():
            vector_id, embedding_blob, metadata_json = row
            stored_vector = self._blob_to_vector(embedding_blob)

            # Compute cosine similarity
            dot_product = sum(q * s for q, s in zip(query_vector, stored_vector))
            stored_norm = sum(s * s for s in stored_vector) ** 0.5
            if query_norm == 0 or stored_norm == 0:
                similarity = 0.0
            else:
                similarity = dot_product / (query_norm * stored_norm)

            metadata = json.loads(metadata_json)

            # Apply filters if provided
            if filters:
                valid = True
                for key, value in filters.items():
                    if metadata.get(key) != value:
                        valid = False
                        break
                if not valid:
                    continue

            results.append((vector_id, similarity, metadata))

        # Sort by similarity descending
        results.sort(key=lambda x: x[1], reverse=True)
        return results[:top_k]

    def get_vector(self, vector_id: str) -> Optional[List[float]]:
        """Get a single vector by ID."""
        cursor = self.conn.cursor()
        cursor.execute(
            """
        SELECT embedding FROM vector_entries
        WHERE id = ? AND collection_name = ?
        """,
            (vector_id, self.collection_name),
        )

        row = cursor.fetchone()
        if row:
            return self._blob_to_vector(row[0])
        return None

    def delete_vectors(self, ids: List[str]) -> None:
        """Delete vectors by IDs."""
        if not ids:
            return

        # Create placeholders for IN clause
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
    def _vector_to_blob(vector: List[float]) -> bytes:
        return struct.pack(f"{len(vector)}f", *vector)

    @staticmethod
    def _blob_to_vector(blob: bytes) -> List[float]:
        float_count = len(blob) // 4  # 4 bytes per float
        return list(struct.unpack(f"{float_count}f", blob))


class QdrantVectorBackend(VectorIndexBackend):
    """Qdrant vector backend."""

    def __init__(
        self,
        collection_name: str,
        url: str,
        api_key: Optional[str] = None,
        dimension: int = 384,
    ):
        # Initialize with config, defer client initialization if qdrant-client is not available
        self.collection_name = collection_name
        self.url = url
        self.api_key = api_key
        self.dimension = dimension
        self._client = None
        self._qdrant_available = None  # Will be set during client init attempt

    def _ensure_client(self):
        """Ensure the Qdrant client is available and connected."""
        if self._qdrant_available is not None and not self._qdrant_available:
            raise RuntimeError(
                "qdrant-client not available - install qdrant-client package"
            )

        if self._client is not None:
            return  # Already initialized

        try:
            from qdrant_client import QdrantClient
        except ImportError:
            self._qdrant_available = False
            raise RuntimeError(
                "qdrant-client not available - install qdrant-client package"
            )

        self._qdrant_available = True

        # Parse URL to determine connection method
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
            # Assume traditional host:port format
            import re

            match = re.match(r"^([^:]+)(?::(\d+))?$", self.url)
            if match:
                host = match.group(1)
                port = int(match.group(2)) if match.group(2) else 6333
                self._client = QdrantClient(host=host, port=port, api_key=self.api_key)
            else:
                # Assume the URL is just a host
                self._client = QdrantClient(host=self.url, api_key=self.api_key)

        # Ensure collection exists
        self._ensure_collection()

    def _ensure_collection(self):
        """Ensure the collection exists with correct vector settings."""
        from qdrant_client.http import models
        from grpc import RpcError

        try:
            self._client.get_collection(self.collection_name)
        except (RpcError, Exception):
            # Create collection if it doesn't exist
            self._client.create_collection(
                collection_name=self.collection_name,
                vectors_config=models.VectorParams(
                    size=self.dimension, distance=models.Distance.COSINE
                ),
            )

    def add_vectors(
        self,
        ids: List[str],
        vectors: List[List[float]],
        metadata_list: Optional[List[dict]] = None,
    ) -> None:
        """Add vectors to Qdrant."""
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
        query_vector: List[float],
        top_k: int = 10,
        filters: Optional[dict] = None,
    ) -> List[tuple[str, float, dict]]:
        """Search in Qdrant."""
        self._ensure_client()

        if len(query_vector) != self.dimension:
            raise ValueError(
                f"Query vector has dimension {len(query_vector)}, expected {self.dimension}"
            )

        from qdrant_client.http import models

        # Map filters to Qdrant conditions if provided
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

        # Format to standard results format (id, score, metadata)
        return [
            (result.id, float(result.score), result.payload or {})
            for result in search_results
        ]

    def get_vector(self, vector_id: str) -> Optional[List[float]]:
        """Get a single vector from Qdrant."""
        self._ensure_client()

        results = self._client.retrieve(
            collection_name=self.collection_name, ids=[vector_id], with_vectors=True
        )

        if results:
            result = results[0]
            if hasattr(result, "vector") and result.vector:
                return result.vector

        return None

    def delete_vectors(self, ids: List[str]) -> None:
        """Delete vectors from Qdrant."""
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
        self.__vector_index = vector_index  # Private attribute
        self.batch_size = batch_size
        self.search_k = search_k

    @property
    def _vector_index(self):
        """Public getter for testing purposes to get direct access to underlying vector index."""
        return self.__vector_index

    def index_record(self, record: Any, content: str) -> str:
        """Index a memory record with its content."""
        # Create embedding from content
        embedding_result = self.embedding_provider.embed(content)

        # Use the record id directly as the vector id, or create deterministic id if no id
        vector_id = getattr(record, "id", f"record_{int(time.time())}")

        # Store in vector index
        self.__vector_index.add_vectors(
            [vector_id], [embedding_result.vector], [{"source": content}]
        )

        return vector_id

    def index_records_batch(self, records: List[Any], contents: List[str]):
        """Batch index multiple records."""
        if len(records) != len(contents):
            raise ValueError("Records and contents must have the same length")

        # Process in batches
        for i in range(0, len(records), self.batch_size):
            batch_records = records[i : i + self.batch_size]
            batch_contents = contents[i : i + self.batch_size]

            # Embed the batch
            embeddings = self.embedding_provider.embed_batch(batch_contents)

            # Prepare data for storage
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
        self, query: str, top_k: Optional[int] = None
    ) -> List[tuple[Any, float, Dict]]:
        """Semantic search in the vector store."""
        # Generate query embedding
        query_embedding = self.embedding_provider.embed(query)

        # Perform search
        search_results = self.__vector_index.search(
            query_embedding.vector, top_k=top_k or self.search_k
        )

        # Return results formatted as (vector_id, score, metadata)
        return search_results


def create_vector_index_adapter(
    db_path: str | Path,
    embedding_provider: EmbeddingProvider,
    vector_index: VectorIndexBackend,
    *,
    env: EnvironmentConfig | Mapping[str, Any] | None = None,
) -> VectorIndexAdapter:
    """Factory to create vector index adapters - FIXED TO RUN MIGRATIONS."""
    # Import and run migrations to ensure tables exist (FIXES ZVECR-01)
    from .migrations import run_migrations

    # Open connection and ensure migrations are run
    conn = connect_database(db_path, env=env)
    run_migrations(conn)  # This is the critical fix
    conn.close()

    # Return adapter instance with providers
    return VectorIndexAdapter(
        embedding_provider=embedding_provider,
        vector_index=vector_index,
    )


class MockEmbeddingProvider(EmbeddingProvider):
    """Mock embedding provider for testing."""

    def embed(self, text: str) -> EmbeddingResult:
        """Return mock embedding result."""
        import hashlib

        text_hash = hashlib.md5(text.encode()).hexdigest()
        seed = int(text_hash[:16], 16)

        vector = [(seed ^ i) % 1000 / 1000.0 for i in range(128)]  # Shorter for testing

        return EmbeddingResult(
            vector=vector,
            provider="mock",
            model="mock-model",
        )

    def embed_batch(self, texts: List[str]) -> EmbeddingBatchResult:
        """Mock batch embedding."""
        results = [self.embed(text) for text in texts]
        return EmbeddingBatchResult(results=results)


def reindex_vectors(
    db_path: str,
    vector_adapter: VectorIndexAdapter,
    *,
    env: EnvironmentConfig | Mapping[str, Any] | None = None,
) -> int:
    """Reindex all memory_records to memory_vectors in batches."""
    import logging
    from .memory_store import _row_to_memory_record

    logger = logging.getLogger(__name__)

    conn = connect_database(db_path, env=env)
    cursor = conn.cursor()

    # Stable ordering keeps vector rebuilds deterministic.
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

    # Process records in batches of 32
    batch_size = 32

    logger.info(f"Starting reindex of {len(rows)} memory_records")

    for i, row in enumerate(rows):
        # Convert row to MemoryRecord object
        record = _row_to_memory_record(row)

        records.append(record)
        contents.append(record.content)

        # Process batch when it reaches the size threshold
        if len(records) >= batch_size:
            # Index the records batch
            vector_adapter.index_records_batch(records, contents)

            total_processed += len(records)
            logger.info(
                f"Processed {total_processed}/{len(rows)} records for reindexing"
            )

            # Reset for next batch
            records = []
            contents = []

    # Process remaining records if any
    if records:
        vector_adapter.index_records_batch(records, contents)
        total_processed += len(records)
        logger.info(f"Processed final batch - total: {total_processed}")

    logger.info(f"Reindex completed - {total_processed} records indexed")
    return total_processed
