import pytest
import tempfile
import os
from pathlib import Path


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
            LocalEmbeddingProvider,
            InMemoryVectorIndex,
        )

        embedding_provider = LocalEmbeddingProvider()
        vector_index = InMemoryVectorIndex(dim=384)

        return create_vector_index_adapter(
            db_path=str(temp_db_path),
            embedding_provider=embedding_provider,
            vector_index=vector_index,
        )

    def test_local_embedding_provider(self):
        from openminion.modules.storage.runtime.vector_index import (
            LocalEmbeddingProvider,
        )

        provider = LocalEmbeddingProvider()

        result = provider.embed("hello world")

        assert result is not None
        assert len(result.vector) == 384
        assert result.provider == "local"
        assert result.model == "all-MiniLM-L6-v2"

    def test_embedding_similarity(self):
        from openminion.modules.storage.runtime.vector_index import (
            LocalEmbeddingProvider,
        )

        provider = LocalEmbeddingProvider()

        result_python = provider.embed("python programming")
        result_python_docs = provider.embed("python data science")
        result_airplane = provider.embed("airplane flight")

        def cosine_sim(a, b):
            dot = sum(x * y for x, y in zip(a, b))
            norm_a = sum(x * x for x in a) ** 0.5
            norm_b = sum(x * x for x in b) ** 0.5
            return dot / (norm_a * norm_b)

        sim_python_pair = cosine_sim(result_python.vector, result_python_docs.vector)
        sim_python_airplane = cosine_sim(result_python.vector, result_airplane.vector)

        assert sim_python_pair > sim_python_airplane, (
            f"sim(python,pair)={sim_python_pair} should be > sim(python, airplane)={sim_python_airplane}"
        )

    def test_batch_embedding(self):
        from openminion.modules.storage.runtime.vector_index import (
            LocalEmbeddingProvider,
        )
        import time

        provider = LocalEmbeddingProvider()

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

    def test_semantic_search_relevance(self, vector_adapter):
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

        weather_ids = {"semantic-0", "semantic-2", "semantic-4"}
        result_ids = {str(r[0]) for r in results}

        weather_matches = result_ids & weather_ids
        assert len(weather_matches) >= 2, "Should find weather-related documents"
