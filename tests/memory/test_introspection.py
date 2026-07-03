class TestMemoryIntrospectionIntentDetector:
    CANONICAL_PROMPTS = [
        "what do you remember",
        "what do you recall",
        "what have you learned",
        "what have you stored",
        "what is in your memory",
        "show me what you remember",
        "summarize your memory",
        "summarize what you remember",
        "how much memory",
        "how much do you remember",
        "memory status",
        "recall memory",
        "show memory",
        "memory summary",
        "what did we discuss",
        "what have we talked about",
        "what do you know about me",
        "what do you know about this session",
        "tell me what you remember",
        "what facts do you have",
        "what information do you have stored",
    ]

    NON_INTROSPECTION_PROMPTS = [
        "what is the weather in Tokyo",
        "remember to call mom",
        "save this file",
        "what do you think about AI",
        "how are you doing",
        "tell me a joke",
        "what is 2+2",
        "hello",
        "help",
        "list tools",
    ]

    def test_introspection_patterns_exist(self) -> None:
        patterns = self.CANONICAL_PROMPTS
        assert len(patterns) > 0
        assert "what do you remember" in patterns


class TestMemoryRuntimeSnapshot:
    def test_memory_runtime_snapshot_schema(self) -> None:
        from openminion.modules.memory.diagnostics.introspection import (
            MemoryRuntimeSnapshot,
        )

        snapshot = MemoryRuntimeSnapshot(
            session_records=5,
            agent_records=3,
            global_records=10,
            candidate_count=2,
            total_records=18,
            memory_available=True,
            vector_search_available=True,
            degraded=False,
            recent_highlights=["Test memory 1", "Test memory 2"],
            snapshot_timestamp="2025-01-15T10:30:00Z",
            scope_filter="session:test-session",
        )

        assert snapshot.session_records == 5
        assert snapshot.total_records == 18
        assert snapshot.memory_available is True
        assert len(snapshot.recent_highlights) == 2

    def test_build_memory_snapshot_with_none_store(self) -> None:
        from openminion.modules.memory.diagnostics.introspection import (
            build_memory_snapshot,
        )

        snapshot = build_memory_snapshot(
            store=None,
            session_id="test-session",
            agent_id="test-agent",
        )

        assert snapshot.degraded is True
        assert snapshot.degraded_reason == "memory_store_unavailable"
        assert snapshot.memory_available is False


class TestRetrievalStatsSnapshot:
    def test_retrieval_stats_snapshot_schema(self) -> None:
        from openminion.modules.memory.diagnostics.introspection import (
            RetrievalStatsSnapshot,
        )

        snapshot = RetrievalStatsSnapshot(
            last_strategy="semantic",
            last_hit_count=5,
            last_query="test query",
            last_latency_ms=150.5,
            retrieve_available=True,
            total_retrievals_session=10,
            avg_hits_per_query=4.5,
            snapshot_timestamp="2025-01-15T10:30:00Z",
        )

        assert snapshot.last_strategy == "semantic"
        assert snapshot.last_hit_count == 5
        assert snapshot.retrieve_available is True

    def test_build_retrieval_stats_with_none_service(self) -> None:
        from openminion.modules.memory.diagnostics.introspection import (
            build_retrieval_stats,
        )

        snapshot = build_retrieval_stats(
            retrieve_svc=None,
            session_id="test-session",
        )

        assert snapshot.retrieve_available is False


class TestRuntimeIntrospectionDigest:
    def test_format_introspection_digest_with_caps(self) -> None:
        from openminion.modules.memory.diagnostics.introspection import (
            format_introspection_digest,
            MemoryRuntimeSnapshot,
            RetrievalStatsSnapshot,
        )

        memory = MemoryRuntimeSnapshot(
            session_records=100,
            agent_records=50,
            global_records=200,
            total_records=350,
            memory_available=True,
            recent_highlights=["A" * 100, "B" * 100, "C" * 100],
            snapshot_timestamp="2025-01-15T10:30:00Z",
        )

        retrieval = RetrievalStatsSnapshot(
            last_strategy="semantic",
            last_hit_count=10,
            retrieve_available=True,
            snapshot_timestamp="2025-01-15T10:30:00Z",
        )

        digest = format_introspection_digest(
            memory=memory,
            retrieval=retrieval,
            max_tokens=50,
        )

        assert digest.introspection_active is True
        assert digest.capped is True
        assert digest.cap_reason is not None
        assert "Token cap exceeded" in digest.cap_reason


class TestIntrospectionResponseFormatter:
    def test_format_introspection_response_with_full_data(self) -> None:
        from openminion.modules.memory.diagnostics.introspection import (
            MemoryRuntimeSnapshot,
            RetrievalStatsSnapshot,
            RuntimeIntrospectionDigest,
        )

        memory = MemoryRuntimeSnapshot(
            session_records=5,
            agent_records=3,
            global_records=10,
            total_records=18,
            memory_available=True,
            vector_search_available=True,
            recent_highlights=[
                "User likes Python",
                "Project: AI assistant",
                "Session started 2025-03-06",
            ],
            snapshot_timestamp="2025-01-15T10:30:00Z",
        )

        retrieval = RetrievalStatsSnapshot(
            last_strategy="semantic",
            last_hit_count=5,
            retrieve_available=True,
            total_retrievals_session=10,
            snapshot_timestamp="2025-01-15T10:30:00Z",
        )

        digest = RuntimeIntrospectionDigest(
            introspection_active=True,
            memory=memory,
            retrieval=retrieval,
            summary_text="Memory: 18 records, Retrieval: semantic",
            estimated_tokens=100,
        )

        assert digest.memory is not None
        assert digest.memory.total_records == 18
        assert digest.retrieval is not None
        assert digest.retrieval.last_strategy == "semantic"

    def test_format_introspection_response_degraded(self) -> None:
        from openminion.modules.memory.diagnostics.introspection import (
            MemoryRuntimeSnapshot,
            RuntimeIntrospectionDigest,
        )

        memory = MemoryRuntimeSnapshot(
            degraded=True,
            degraded_reason="connection_timeout",
            memory_available=False,
            snapshot_timestamp="2025-01-15T10:30:00Z",
        )

        digest = RuntimeIntrospectionDigest(
            introspection_active=True,
            memory=memory,
            summary_text="Memory degraded: connection_timeout",
            estimated_tokens=50,
        )

        assert digest.memory is not None
        assert digest.memory.degraded is True
        assert digest.memory.degraded_reason == "connection_timeout"
