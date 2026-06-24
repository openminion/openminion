from __future__ import annotations

import tempfile
import time
import types
import unittest
import uuid
from datetime import datetime, timezone
from pathlib import Path

from openminion.modules.memory.models import MemoryRecord
from openminion.modules.memory.service import MemoryService
from openminion.modules.memory.storage.base import ListQueryOptions
from openminion.modules.memory.storage.sqlite.store import SQLiteMemoryStore
from openminion.services.agent.memory.gateway_adapter import MemoryServiceGatewayAdapter
from openminion.services.agent.memory.identity_seeder import seed_identity_pins


def _make_bench_adapter(
    db_path: Path,
    agent_id: str = "bench",
    capsule_max_chars: int = 1600,
    retrieval_min_confidence: float | None = None,
) -> MemoryServiceGatewayAdapter:
    store = SQLiteMemoryStore(db_path)
    service = MemoryService(store=store)
    adapter = MemoryServiceGatewayAdapter(
        service,
        agent_id=agent_id,
        capsule_max_chars=capsule_max_chars,
        trace_enabled=False,
    )
    if retrieval_min_confidence is not None:
        adapter._retrieval_min_confidence = float(retrieval_min_confidence)  # noqa: SLF001
    return adapter


def _bench(label: str, n: int, elapsed: float) -> float:
    ops = n / elapsed if elapsed > 0 else float("inf")
    print(
        f"\nBENCH {label}: n={n} elapsed={elapsed * 1000:.1f}ms ops_per_sec={ops:.1f}"
    )
    return ops


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _seed_records(
    store: SQLiteMemoryStore,
    *,
    scope: str,
    record_type: str = "fact",
    count: int,
    content_prefix: str = "bench fact",
) -> None:
    for i in range(count):
        store.put(
            MemoryRecord(
                id=str(uuid.uuid4()),
                scope=scope,
                type=record_type,
                content=f"{content_prefix} number {i}: unique detail about item {i}",
                created_at=_utc_now(),
                updated_at=_utc_now(),
                title=f"{content_prefix} {i}",
                source="agent_inferred",
                confidence=0.8,
            )
        )


def _make_profile_stub(revision: int = 1) -> types.SimpleNamespace:
    role = types.SimpleNamespace(
        mission="Benchmark agent for performance testing",
        responsibilities=["measure write throughput", "test retrieval latency"],
        hard_constraints=["do not skip benchmarks", "always assert correctness"],
        domain=["testing", "benchmarking"],
    )
    return types.SimpleNamespace(profile_revision=revision, role=role)


class TestWriteThroughput(unittest.TestCase):
    def _do_write_turns(
        self,
        adapter: MemoryServiceGatewayAdapter,
        n: int,
        multi_session: bool = False,
    ) -> float:
        start = time.perf_counter()
        for i in range(n):
            session_id = f"session-{i % 10}" if multi_session else "session-write"
            adapter.record_turn(
                session_id=session_id,
                run_id=f"run-{i}",
                request_id=f"req-{i}",
                channel="test",
                target="user",
                user_message=f"turn {i}: working on task number {i}",
                assistant_message=f"acknowledged task {i}",
            )
        return time.perf_counter() - start

    def test_write_1_turn(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            adapter = _make_bench_adapter(Path(tmp) / "m.db")
            elapsed = self._do_write_turns(adapter, 1)
            _bench("write_1_turn", 1, elapsed)
            self.assertLess(elapsed, 2.0, "single write took > 2s")

    def test_write_10_turns(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            adapter = _make_bench_adapter(Path(tmp) / "m.db")
            elapsed = self._do_write_turns(adapter, 10)
            ops = _bench("write_10_turns", 10, elapsed)
            self.assertLess(elapsed, 5.0, "10 writes took > 5s")
            self.assertGreater(ops, 0.5, "< 0.5 writes/sec is unexpectedly slow")

    def test_write_100_turns_single_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            adapter = _make_bench_adapter(Path(tmp) / "m.db")
            elapsed = self._do_write_turns(adapter, 100)
            ops = _bench("write_100_turns_single_session", 100, elapsed)
            self.assertLess(elapsed, 30.0, "100 writes took > 30s")
            self.assertGreater(ops, 1.0, "< 1 write/sec is unexpectedly slow")

    def test_write_100_turns_multi_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            adapter = _make_bench_adapter(Path(tmp) / "m.db")
            elapsed = self._do_write_turns(adapter, 100, multi_session=True)
            ops = _bench("write_100_turns_multi_session", 100, elapsed)
            self.assertLess(elapsed, 30.0, "100 multi-session writes took > 30s")
            self.assertGreater(ops, 1.0)

    def test_write_with_remember_prefix(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            adapter = _make_bench_adapter(Path(tmp) / "m.db")
            start = time.perf_counter()
            for i in range(20):
                result = adapter.record_turn(
                    session_id="session-promo",
                    run_id=f"run-{i}",
                    request_id=f"req-{i}",
                    channel="test",
                    target="user",
                    user_message=f"remember: important fact number {i} about the system",
                    assistant_message="noted",
                )
                self.assertGreater(
                    result.facts_added, 0, f"turn {i}: expected fact_added > 0"
                )
            elapsed = time.perf_counter() - start
            _bench("write_20_remember_turns", 20, elapsed)
            self.assertLess(elapsed, 15.0)

    def test_generation_counter_increments(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            adapter = _make_bench_adapter(Path(tmp) / "m.db")
            last_gen = 0
            for i in range(10):
                result = adapter.record_turn(
                    session_id="session-gen",
                    run_id=f"run-{i}",
                    request_id=f"req-{i}",
                    channel="test",
                    target="user",
                    user_message=f"message {i}",
                    assistant_message="ok",
                )
                self.assertGreater(
                    result.generation,
                    last_gen,
                    f"generation should increase at turn {i}",
                )
                last_gen = result.generation
            self.assertEqual(last_gen, 10)


class TestCapsuleBuildLatency(unittest.TestCase):
    def _time_capsule_builds(
        self,
        adapter: MemoryServiceGatewayAdapter,
        session_id: str,
        n: int,
    ) -> float:
        start = time.perf_counter()
        for _ in range(n):
            adapter.build_context(
                session_id=session_id, user_message="what do you know?"
            )
        return time.perf_counter() - start

    def test_capsule_empty_store(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            adapter = _make_bench_adapter(Path(tmp) / "m.db")
            start = time.perf_counter()
            ctx = adapter.build_context(session_id="s1", user_message="hello")
            elapsed = time.perf_counter() - start
            _bench("capsule_empty", 1, elapsed)
            self.assertLess(elapsed, 1.0)
            self.assertIsInstance(ctx, str)

    def test_capsule_10_records(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "m.db"
            store = SQLiteMemoryStore(db_path)
            _seed_records(store, scope="agent:bench", count=5)
            _seed_records(store, scope="session:s1", count=5)
            adapter = _make_bench_adapter(db_path)
            elapsed = self._time_capsule_builds(adapter, "s1", 10)
            _bench("capsule_10_records_x10", 10, elapsed)
            self.assertLess(elapsed, 5.0)
            ctx = adapter.build_context(session_id="s1", user_message="")
            self.assertIn("## Agent Memory", ctx)

    def test_capsule_100_records(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "m.db"
            store = SQLiteMemoryStore(db_path)
            _seed_records(store, scope="agent:bench", count=50)
            _seed_records(store, scope="session:s1", count=50)
            adapter = _make_bench_adapter(db_path)
            elapsed = self._time_capsule_builds(adapter, "s1", 10)
            _bench("capsule_100_records_x10", 10, elapsed)
            self.assertLess(elapsed, 10.0)

    def test_capsule_500_records(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "m.db"
            store = SQLiteMemoryStore(db_path)
            _seed_records(store, scope="agent:bench", count=250)
            _seed_records(store, scope="session:s1", count=250)
            adapter = _make_bench_adapter(db_path)
            elapsed = self._time_capsule_builds(adapter, "s1", 5)
            _bench("capsule_500_records_x5", 5, elapsed)
            self.assertLess(elapsed, 15.0)

    def test_capsule_respects_char_budget(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "m.db"
            store = SQLiteMemoryStore(db_path)
            _seed_records(
                store,
                scope="agent:bench",
                count=100,
                content_prefix="very long fact text with lots of content",
            )
            adapter = _make_bench_adapter(db_path, capsule_max_chars=400)
            ctx = adapter.build_context(session_id="s1", user_message="")
            self.assertLessEqual(
                len(ctx),
                600,  # allow some header overhead above 400
                f"capsule exceeded budget: {len(ctx)} chars",
            )

    def test_capsule_with_metadata_keys(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "m.db"
            store = SQLiteMemoryStore(db_path)
            _seed_records(store, scope="agent:bench", count=10)
            adapter = _make_bench_adapter(db_path)
            ctx, meta = adapter.build_context_with_metadata(
                session_id="s1", user_message="test"
            )
            self.assertIsInstance(ctx, str)
            self.assertIsInstance(meta, dict)
            self.assertIn("memory_envelope_version", meta)
            self.assertIn("memory_lane", meta)


# Class 3: Cross-session persistence


class TestCrossSessionPersistence(unittest.TestCase):
    def test_fact_survives_restart(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "m.db"

            # Write in first adapter instance
            t0 = time.perf_counter()
            adapter1 = _make_bench_adapter(db_path)
            adapter1.record_turn(
                session_id="s1",
                run_id="r1",
                request_id="req1",
                channel="test",
                target="user",
                user_message="remember: the server runs on port 8080",
                assistant_message="",
            )
            write_ms = (time.perf_counter() - t0) * 1000

            # Recreate adapter (simulates restart) and read
            t1 = time.perf_counter()
            adapter2 = _make_bench_adapter(db_path)
            ctx, _ = adapter2.build_context_with_metadata(
                session_id="s1", user_message=""
            )
            read_ms = (time.perf_counter() - t1) * 1000

            print(
                f"\nBENCH persist_restart: write={write_ms:.1f}ms read={read_ms:.1f}ms"
            )
            self.assertIn("8080", ctx, "fact did not survive adapter restart")

    def test_agent_scope_visible_in_new_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "m.db"

            adapter1 = _make_bench_adapter(db_path)
            adapter1.record_turn(
                session_id="session-alpha",
                run_id="r1",
                request_id="req1",
                channel="test",
                target="user",
                user_message="remember: project codename is Thunderbolt",
                assistant_message="",
            )

            adapter2 = _make_bench_adapter(db_path)
            ctx, _ = adapter2.build_context_with_metadata(
                session_id="session-beta",  # NEW session
                user_message="what is the project codename?",
            )
            self.assertIn(
                "Thunderbolt",
                ctx,
                "agent-scope fact not visible in new session after restart",
            )

    def test_100_facts_survive_restart(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "m.db"
            adapter1 = _make_bench_adapter(db_path)

            start = time.perf_counter()
            for i in range(100):
                adapter1.record_turn(
                    session_id=f"session-{i % 10}",
                    run_id=f"r{i}",
                    request_id=f"req{i}",
                    channel="test",
                    target="user",
                    user_message=f"remember: persistent fact number {i} about component X{i}",
                    assistant_message="",
                )
            write_elapsed = time.perf_counter() - start

            # Restart
            t1 = time.perf_counter()
            adapter2 = _make_bench_adapter(db_path)
            store = adapter2._service._store  # type: ignore[attr-defined]
            records = store.list(
                ListQueryOptions(scopes=["agent:bench"], types=["fact"], limit=200)
            )
            read_elapsed = time.perf_counter() - t1

            print(
                f"\nBENCH 100_facts_survive: write={write_elapsed * 1000:.1f}ms "
                f"read_list={read_elapsed * 1000:.1f}ms found={len(records)}"
            )
            self.assertGreaterEqual(
                len(records), 100, f"only {len(records)} of 100 facts survived restart"
            )

    def test_cross_session_isolation_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "m.db"
            adapter1 = _make_bench_adapter(db_path)
            adapter1.record_turn(
                session_id="session-private",
                run_id="r1",
                request_id="req1",
                channel="test",
                target="user",
                user_message="fact: this is a session-scoped secret XYZ9876",
                assistant_message="",
            )

            adapter2 = _make_bench_adapter(db_path)
            ctx, _ = adapter2.build_context_with_metadata(
                session_id="session-other",
                user_message="",
            )
            # Session-scoped fact must NOT appear in a different session's capsule
            self.assertNotIn(
                "XYZ9876", ctx, "session-scoped fact leaked into unrelated session"
            )

    def test_agent_pins_survive_multiple_restarts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "m.db"
            profile = _make_profile_stub(revision=1)

            adapter0 = _make_bench_adapter(db_path)
            seed_identity_pins(
                profile=profile,
                memory_service=adapter0._service,  # type: ignore[attr-defined]
                agent_id="bench",
            )

            for restart in range(5):
                adapterN = _make_bench_adapter(db_path)
                ctx, _ = adapterN.build_context_with_metadata(
                    session_id=f"restart-{restart}",
                    user_message="",
                )
                # Capsule shows pin titles (not content) — verify the mission pin title is present
                self.assertIn(
                    "identity_mission",
                    ctx,
                    f"identity pin missing after restart #{restart + 1}",
                )


# Class 4: Search / retrieval latency


class TestSearchRetrievalLatency(unittest.TestCase):
    def _time_retrievals(
        self,
        adapter: MemoryServiceGatewayAdapter,
        session_id: str,
        queries: list[str],
        n: int,
    ) -> float:
        start = time.perf_counter()
        for i in range(n):
            q = queries[i % len(queries)]
            adapter.build_retrieval_context(session_id=session_id, user_message=q)
        return time.perf_counter() - start

    _QUERIES = [
        "server configuration and settings",
        "database connection parameters",
        "authentication and user accounts",
        "performance tuning options",
        "error handling strategies",
    ]

    def test_retrieval_10_records(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "m.db"
            store = SQLiteMemoryStore(db_path)
            _seed_records(
                store,
                scope="session:s1",
                count=10,
                content_prefix="server config database auth performance",
            )
            adapter = _make_bench_adapter(db_path)
            elapsed = self._time_retrievals(adapter, "s1", self._QUERIES, 20)
            _bench("retrieval_10_records_x20", 20, elapsed)
            self.assertLess(elapsed, 5.0)

    def test_retrieval_100_records(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "m.db"
            store = SQLiteMemoryStore(db_path)
            _seed_records(
                store,
                scope="agent:bench",
                count=50,
                content_prefix="system configuration parameter",
            )
            _seed_records(
                store,
                scope="session:s1",
                count=50,
                content_prefix="user preference setting option",
            )
            adapter = _make_bench_adapter(db_path)
            elapsed = self._time_retrievals(adapter, "s1", self._QUERIES, 20)
            _bench("retrieval_100_records_x20", 20, elapsed)
            avg_ms = elapsed * 1000 / 20
            print(f"  avg retrieval latency: {avg_ms:.1f}ms")
            self.assertLess(elapsed, 15.0)

    def test_retrieval_500_records(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "m.db"
            store = SQLiteMemoryStore(db_path)
            _seed_records(
                store,
                scope="agent:bench",
                count=250,
                content_prefix="configuration parameter setting",
            )
            _seed_records(
                store,
                scope="session:s1",
                count=250,
                content_prefix="session context working memory",
            )
            adapter = _make_bench_adapter(db_path)
            elapsed = self._time_retrievals(adapter, "s1", self._QUERIES, 10)
            _bench("retrieval_500_records_x10", 10, elapsed)
            avg_ms = elapsed * 1000 / 10
            print(f"  avg retrieval latency: {avg_ms:.1f}ms")
            self.assertLess(elapsed, 15.0)

    def test_retrieval_1000_records(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "m.db"
            store = SQLiteMemoryStore(db_path)
            _seed_records(
                store,
                scope="agent:bench",
                count=500,
                content_prefix="long term agent knowledge fact",
            )
            _seed_records(
                store,
                scope="session:s1",
                count=500,
                content_prefix="session working memory item detail",
            )
            adapter = _make_bench_adapter(db_path)
            elapsed = self._time_retrievals(adapter, "s1", self._QUERIES, 5)
            _bench("retrieval_1000_records_x5", 5, elapsed)
            avg_ms = elapsed * 1000 / 5
            print(f"  avg retrieval latency: {avg_ms:.1f}ms")
            self.assertLess(elapsed, 15.0)

    def test_retrieval_relevance_at_scale(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "m.db"
            store = SQLiteMemoryStore(db_path)
            # Seed noise records
            _seed_records(
                store,
                scope="session:s1",
                count=400,
                content_prefix="unrelated noise record filler content",
            )
            # Seed a needle record
            store.put(
                MemoryRecord(
                    id=str(uuid.uuid4()),
                    scope="session:s1",
                    type="fact",
                    content="the_needle: API rate limit is 1000 requests per minute",
                    created_at=_utc_now(),
                    updated_at=_utc_now(),
                    title="API rate limit configuration",
                    source="agent_inferred",
                    confidence=1.0,
                )
            )
            adapter = _make_bench_adapter(db_path)
            result = adapter.build_retrieval_context(
                session_id="s1",
                user_message="what is the API rate limit configuration?",
            )
            self.assertIn(
                "rate limit",
                result.lower(),
                "needle not found in retrieval results at 500 records",
            )

    def test_retrieval_with_metadata_keys(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "m.db"
            store = SQLiteMemoryStore(db_path)
            _seed_records(store, scope="session:s1", count=10)
            adapter = _make_bench_adapter(db_path)
            result, meta = adapter.build_retrieval_context_with_metadata(
                session_id="s1",
                user_message="bench fact detail",
            )
            self.assertIsInstance(result, str)
            self.assertIsInstance(meta, dict)
            self.assertIn("memory_envelope_version", meta)


# Class 5: Long-term scaling scenarios


class TestLongTermScalingScenarios(unittest.TestCase):
    def test_20_sessions_10_turns_each(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            adapter = _make_bench_adapter(Path(tmp) / "m.db")
            start = time.perf_counter()
            for session_i in range(20):
                sid = f"session-{session_i}"
                for turn_i in range(10):
                    adapter.record_turn(
                        session_id=sid,
                        run_id=f"r-{session_i}-{turn_i}",
                        request_id=f"req-{session_i}-{turn_i}",
                        channel="test",
                        target="user",
                        user_message=f"session {session_i} turn {turn_i}: work item details",
                        assistant_message="acknowledged",
                    )
            write_elapsed = time.perf_counter() - start

            t1 = time.perf_counter()
            ctx = adapter.build_context(session_id="session-0", user_message="")
            capsule_elapsed = time.perf_counter() - t1

            total = write_elapsed + capsule_elapsed
            print(
                f"\nBENCH 20sess_10turns: write={write_elapsed * 1000:.1f}ms "
                f"capsule={capsule_elapsed * 1000:.1f}ms total={total * 1000:.1f}ms"
            )
            self.assertLess(total, 60.0, "20x10 scenario exceeded 60s")
            self.assertIsInstance(ctx, str)

    def test_agent_promotion_accumulation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "m.db"
            adapter1 = _make_bench_adapter(db_path)
            start = time.perf_counter()
            for i in range(50):
                adapter1.record_turn(
                    session_id=f"promo-session-{i}",
                    run_id=f"r{i}",
                    request_id=f"req{i}",
                    channel="test",
                    target="user",
                    user_message=f"remember: cross-session fact {i}: system param PARAM{i}=value{i}",
                    assistant_message="",
                )
            write_elapsed = time.perf_counter() - start

            # Fresh adapter → agent-scope should have all 50 facts
            adapter2 = _make_bench_adapter(db_path)
            store = adapter2._service._store  # type: ignore[attr-defined]
            records = store.list(
                ListQueryOptions(scopes=["agent:bench"], types=["fact"], limit=200)
            )
            read_elapsed = time.perf_counter() - start - write_elapsed
            print(
                f"\nBENCH agent_promo_50: write={write_elapsed * 1000:.1f}ms "
                f"list={read_elapsed * 1000:.1f}ms agent_facts={len(records)}"
            )
            self.assertGreaterEqual(
                len(records),
                50,
                f"expected ≥50 promoted agent-scope facts, got {len(records)}",
            )

    def test_search_across_all_sessions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "m.db"
            adapter1 = _make_bench_adapter(db_path)
            # Each session writes one uniquely named fact using plain tokens (no underscores)
            # so FTS tokenization works cleanly
            for i in range(20):
                adapter1.record_turn(
                    session_id=f"multi-sess-{i}",
                    run_id=f"r{i}",
                    request_id=f"req{i}",
                    channel="test",
                    target="user",
                    user_message=f"remember: UXIDWORD{i} is the key for module {i}",
                    assistant_message="",
                )

            # Search from a fresh session — agent-promoted facts are searchable cross-session
            adapter2 = _make_bench_adapter(db_path, retrieval_min_confidence=0.5)
            result = adapter2.build_retrieval_context(
                session_id="search-session",
                user_message="UXIDWORD5 module key",
            )
            self.assertIn(
                "UXIDWORD5",
                result,
                "retrieval did not find cross-session agent-promoted fact",
            )

    def test_generation_counter_at_scale(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            adapter = _make_bench_adapter(Path(tmp) / "m.db")
            last_result = None
            for i in range(50):
                last_result = adapter.record_turn(
                    session_id="gen-session",
                    run_id=f"r{i}",
                    request_id=f"req{i}",
                    channel="test",
                    target="user",
                    user_message=f"message {i}",
                    assistant_message="ok",
                )
            self.assertIsNotNone(last_result)
            self.assertEqual(last_result.generation, 50)

    def test_patch_id_uniqueness_at_scale(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            adapter = _make_bench_adapter(Path(tmp) / "m.db")
            patch_ids: set[str] = set()
            for i in range(100):
                result = adapter.record_turn(
                    session_id="pid-session",
                    run_id=f"r{i}",
                    request_id=f"req{i}",
                    channel="test",
                    target="user",
                    user_message=f"unique message content number {i}",
                    assistant_message="ok",
                )
                self.assertEqual(len(result.patch_id), 12)
                patch_ids.add(result.patch_id)
            self.assertEqual(len(patch_ids), 100, "patch_id collision detected")


# Class 6: Identity pin seeding benchmarks


class TestIdentitySeedBenchmark(unittest.TestCase):
    def _service(self, db_path: Path) -> MemoryService:
        return MemoryService(store=SQLiteMemoryStore(db_path))

    def test_seed_pins_first_time(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            svc = self._service(Path(tmp) / "m.db")
            profile = _make_profile_stub(revision=1)

            start = time.perf_counter()
            count = seed_identity_pins(
                profile=profile, memory_service=svc, agent_id="bench"
            )
            elapsed = time.perf_counter() - start

            _bench("seed_pins_first", count, elapsed)
            self.assertLess(elapsed, 5.0, "first-time pin seeding took > 5s")
            # mission + responsibilities + constraints + domain + version sentinel = 5
            self.assertEqual(count, 5, f"expected 5 pins, got {count}")

    def test_seed_pins_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            svc = self._service(Path(tmp) / "m.db")
            profile = _make_profile_stub(revision=1)

            # First seed
            seed_identity_pins(profile=profile, memory_service=svc, agent_id="bench")

            # Second seed — same revision — should be a no-op
            start = time.perf_counter()
            count = seed_identity_pins(
                profile=profile, memory_service=svc, agent_id="bench"
            )
            elapsed = time.perf_counter() - start

            _bench("seed_pins_idempotent", 1, elapsed)
            self.assertLess(elapsed, 2.0, "idempotent re-seed took > 2s")
            self.assertEqual(count, 0, "idempotent seed should return 0 (skipped)")

    def test_seed_pins_force_update(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            svc = self._service(Path(tmp) / "m.db")
            profile = _make_profile_stub(revision=1)

            seed_identity_pins(profile=profile, memory_service=svc, agent_id="bench")

            start = time.perf_counter()
            count = seed_identity_pins(
                profile=profile, memory_service=svc, agent_id="bench", force=True
            )
            elapsed = time.perf_counter() - start

            _bench("seed_pins_force", count, elapsed)
            self.assertLess(elapsed, 5.0)
            self.assertEqual(count, 5, "force=True should always write all 5 pins")

    def test_seed_pins_new_revision_triggers_reseed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            svc = self._service(Path(tmp) / "m.db")
            profile_v1 = _make_profile_stub(revision=1)
            profile_v2 = _make_profile_stub(revision=2)

            seed_identity_pins(profile=profile_v1, memory_service=svc, agent_id="bench")
            count = seed_identity_pins(
                profile=profile_v2, memory_service=svc, agent_id="bench"
            )
            self.assertEqual(count, 5, "new revision should trigger full reseed")

    def test_pins_visible_in_capsule_after_seed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "m.db"
            adapter = _make_bench_adapter(db_path)
            profile = _make_profile_stub(revision=1)

            seed_identity_pins(
                profile=profile,
                memory_service=adapter._service,  # type: ignore[attr-defined]
                agent_id="bench",
            )

            ctx = adapter.build_context(session_id="s1", user_message="")
            # Capsule renders pin titles (not content bodies) — assert on the mission pin title
            self.assertIn(
                "identity_mission",
                ctx,
                "seeded mission pin title not found in capsule output",
            )
