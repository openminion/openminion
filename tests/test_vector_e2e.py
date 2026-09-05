import hashlib
import os
import tempfile
from pathlib import Path

import pytest

from openminion.modules.storage.runtime.vector_index import (
    EmbeddingBatchResult,
    EmbeddingResult,
    VectorSpaceIdentity,
)


class _TestEmbeddingProvider:
    semantic_ready = True
    identity = VectorSpaceIdentity("test", "test", 384)

    def embed(self, text: str) -> EmbeddingResult:
        seed = float(sum(text.encode()) % 97) / 97.0
        return EmbeddingResult([seed] * 384, "test", "test")

    def embed_batch(self, texts: list[str]) -> EmbeddingBatchResult:
        return EmbeddingBatchResult([self.embed(text) for text in texts])


class TestVectorE2E:
    @pytest.fixture
    def temp_db_path(self):
        data_root = os.getenv("OPENMINION_DATA_ROOT")
        base_dir = None
        if data_root:
            base_dir = Path(data_root) / "tmp"
            base_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=base_dir) as tmpdir:
            yield Path(tmpdir) / "test.db"

    @pytest.fixture
    def vector_adapter(self, temp_db_path):
        import sys
        from pathlib import Path

        root = Path(__file__).resolve().parents[3]
        candidate = root / "openminion" / "src"
        if candidate.exists() and str(candidate) not in sys.path:
            sys.path.insert(0, str(candidate))

        from openminion.modules.storage.runtime.vector_index import (
            create_vector_index_adapter,
            InMemoryVectorIndex,
        )

        embedding_provider = _TestEmbeddingProvider()
        vector_index = InMemoryVectorIndex(dim=384)

        return create_vector_index_adapter(
            db_path=str(temp_db_path),
            embedding_provider=embedding_provider,
            vector_index=vector_index,
        )

    def test_local_embedding_provider_uses_sentence_transformer(self):
        from openminion.modules.storage.runtime.vector_index import (
            LocalEmbeddingProvider,
        )

        provider = LocalEmbeddingProvider()
        provider._st_model = type(  # noqa: SLF001
            "Model",
            (),
            {
                "encode": lambda self, text, **kwargs: [0.25] * 384,
                "get_sentence_embedding_dimension": lambda self: 384,
            },
        )()

        result = provider.embed("hello world")

        assert result is not None
        assert len(result.vector) == 384
        assert result.provider == "sentence-transformers"
        assert result.model == "all-MiniLM-L6-v2"

    def test_empty_snapshot_rejects_embedding_dimension_mismatch(
        self, temp_db_path: Path
    ) -> None:
        from openminion.modules.storage.runtime.vector_index import (
            LocalEmbeddingProvider,
            SQLiteVecBackend,
            VectorIndexAdapter,
        )

        provider = LocalEmbeddingProvider(dimension=4)
        provider._st_model = type(  # noqa: SLF001
            "Model", (), {"get_sentence_embedding_dimension": lambda self: 3}
        )()
        adapter = VectorIndexAdapter(
            provider,
            SQLiteVecBackend(str(temp_db_path), dimension=4),
        )
        adapter.bind_record_source(
            type(
                "EmptySource",
                (),
                {"vector_sync_snapshot": lambda self: {"current": [], "retired": []}},
            )()
        )

        with pytest.raises(RuntimeError, match="embedding provider is unavailable"):
            adapter.sync_pending_records()
        assert adapter.semantic_ready is False

    def test_batch_embedding(self):
        import time

        provider = _TestEmbeddingProvider()

        texts = [f"text {i}" for i in range(10)]

        start = time.time()
        result = provider.embed_batch(texts)
        elapsed = time.time() - start

        assert len(result.results) == 10
        assert all(len(r.vector) == 384 for r in result.results)
        assert elapsed < 5.0, f"Batch of 10 should embed in < 5s, took {elapsed:.2f}s"

    def test_vector_index_add_and_search(self, vector_adapter):
        test_vectors = [
            ("doc1", [0.1] * 384, {"text": "cat sitting on mat", "scope": "agent"}),
            ("doc2", [0.2] * 384, {"text": "dog running in park", "scope": "agent"}),
            ("doc3", [0.15] * 384, {"text": "cat sleeping on couch", "scope": "agent"}),
        ]

        ids = [v[0] for v in test_vectors]
        vectors = [v[1] for v in test_vectors]
        metadata = [v[2] for v in test_vectors]

        vector_adapter._vector_index.add_vectors(ids, vectors, metadata)

        query_vector = [0.1] * 384
        results = vector_adapter._vector_index.search(query_vector, top_k=2)

        assert len(results) == 2
        assert all(score >= 0 for _, score, _ in results)

    def test_vector_adapter_index_record(self, vector_adapter):

        class MockRecord:
            id = "test-record-1"

        record = MockRecord()
        content = "This is a test memory about cats and dogs"

        vector_id = vector_adapter.index_record(record, content)

        assert vector_id is not None

        retrieved = vector_adapter._vector_index.get_vector(record.id)
        assert retrieved is not None

    def test_vector_adapter_search(self, vector_adapter):

        class MockRecord:
            id = "test-record-2"

        record = MockRecord()
        content = "Information about machine learning models"

        vector_adapter.index_record(record, content)

        results = vector_adapter.search("artificial intelligence", top_k=1)

        assert isinstance(results, list)

    def test_full_pipeline_ingest_embed_index_search(self, vector_adapter):
        test_contents = [
            "Python is a programming language",
            "JavaScript is used for web development",
            "Python is great for data science",
            "Machine learning uses Python",
            "Web development with React and Vue",
            "Data analysis with pandas",
            "Deep learning with PyTorch",
            "Frontend development with JavaScript",
            "Natural language processing with transformers",
            "Computer vision with OpenCV",
        ]

        class MockRecord:
            def __init__(self, idx):
                self.id = f"record-{idx}"

        for idx, content in enumerate(test_contents):
            record = MockRecord(idx)
            vector_adapter.index_record(record, content)

        results = vector_adapter.search("Python programming", top_k=3)

        assert len(results) <= 3

        python_related_ids = {"record-0", "record-2", "record-3", "record-6"}
        result_ids = {str(r[0]) for r in results}

        python_matches = result_ids & python_related_ids
        assert len(python_matches) > 0, "Should find Python-related documents"

    def test_vector_search_respects_top_k(self, vector_adapter):
        test_contents = [
            "The weather today is sunny and warm",
            "I love eating pizza with cheese",
            "Hot sunny weather is expected tomorrow",
            "Pizza is my favorite Italian food",
            "Weekly weather forecast with sunshine all week",
        ]

        class MockRecord:
            def __init__(self, idx):
                self.id = f"semantic-{idx}"

        for idx, content in enumerate(test_contents):
            record = MockRecord(idx)
            vector_adapter.index_record(record, content)

        results = vector_adapter.search("hot sunny weather", top_k=3)

        assert len(results) <= 3

        assert len(results) == 3

    def test_semantic_readiness_requires_complete_initial_sync(
        self, temp_db_path: Path
    ) -> None:
        from openminion.modules.storage.runtime.vector_index import (
            SQLiteVecBackend,
            VectorIndexAdapter,
        )

        backend = SQLiteVecBackend(str(temp_db_path), dimension=384)
        adapter = VectorIndexAdapter(_TestEmbeddingProvider(), backend)

        class _Source:
            def vector_sync_snapshot(self):
                return {
                    "current": [
                        {
                            "record_id": "memory-1",
                            "text": "durable content",
                            "content_fingerprint": hashlib.sha256(
                                b"durable content"
                            ).hexdigest(),
                        }
                    ],
                    "retired": [],
                }

        assert adapter.semantic_ready is False
        adapter.bind_record_source(_Source())
        assert adapter.sync_pending_records() == 1
        assert adapter.semantic_ready is True
        assert adapter.get_vector("memory-1") is not None

        backend.conn.execute(
            "UPDATE vector_entries SET metadata_json = ? WHERE id = ?",
            ('{"vector_space_identity":"other"}', "memory-1"),
        )
        backend.conn.commit()
        assert adapter.get_vector("memory-1") is None

    def test_snapshot_sync_uses_configured_embedding_chunks(
        self, temp_db_path: Path
    ) -> None:
        from openminion.modules.storage.runtime.vector_index import (
            SQLiteVecBackend,
            VectorIndexAdapter,
        )

        class _RecordingEmbeddingProvider(_TestEmbeddingProvider):
            def __init__(self) -> None:
                self.batch_sizes: list[int] = []

            def embed_batch(self, texts: list[str]) -> EmbeddingBatchResult:
                self.batch_sizes.append(len(texts))
                return super().embed_batch(texts)

        provider = _RecordingEmbeddingProvider()
        adapter = VectorIndexAdapter(
            provider,
            SQLiteVecBackend(str(temp_db_path), dimension=384),
            batch_size=2,
        )
        adapter.bind_record_source(
            type(
                "Source",
                (),
                {
                    "vector_sync_snapshot": lambda self: {
                        "current": [
                            {
                                "record_id": f"memory-{index}",
                                "text": f"memory content {index}",
                                "content_fingerprint": hashlib.sha256(
                                    f"memory content {index}".encode()
                                ).hexdigest(),
                            }
                            for index in range(5)
                        ],
                        "retired": [],
                    }
                },
            )()
        )

        assert adapter.sync_pending_records() == 5
        assert provider.batch_sizes == [2, 2, 1]
