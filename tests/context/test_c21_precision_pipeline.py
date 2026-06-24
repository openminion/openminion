from __future__ import annotations

import unittest

from openminion.modules.context.contracts import (
    ContextCompressor,
    ContextRetriever,
    PluginRegistry,
)
from openminion.modules.context.schemas import (
    ArtifactDigest,
    BuildPackRequest,
    EvidenceItem,
    IdentitySnippet,
    SessionSlice,
)
from openminion.modules.context.service import (
    ContextCtlService,
    LayoutDisciplineError,
    _position_aware_v1,
    _assert_layout_discipline,
    _make_segment,
)
from openminion.modules.context.prefix import PrefixCacheAdapter


class _Identity:
    contract_version = "v1"

    def render(
        self, *, agent_id, purpose, max_tokens, provider_pref=None, query_text=None
    ):
        return IdentitySnippet(
            agent_id=agent_id,
            profile_version="pv1",
            render_version="rv1",
            text=f"Identity:{agent_id}",
        )


class _Session:
    contract_version = "v1"

    def __init__(self, turns=None, tool_events=None):
        self._turns = turns or []
        self._tool_events = tool_events or []
        self.events: list[dict] = []

    def get_slice(self, *, session_id, purpose, limits):
        return SessionSlice(
            session_id=session_id,
            slice_version="sv1",
            last_event_id="e1",
            summary_short="short summary",
            recent_turns=self._turns,
            recent_tool_events=self._tool_events,
        )

    def emit_canonical_event(
        self, *, session_id, event_type, payload, actor_type, actor_id
    ):
        self.events.append({"event_type": event_type, "payload": payload})


class _Mem:
    contract_version = "v1"

    def query_facts(self, **_):
        return []

    def query_memory_cards(self, **_):
        return []

    def recall_session_start_memory(self, **_):
        return []

    def recall_mid_session_memory(self, **_):
        return []

    def recall_recent_session_artifacts(self, **_):
        return []

    def get_procedure(self, **_):
        return None


class _Art:
    contract_version = "v1"

    def __init__(self, digests=None):
        self._digests = digests or []

    def query_digests(self, *, session_id, agent_id, query, limit):
        return self._digests[:limit]


def _make_service(digests=None, session=None, registry=None):
    return ContextCtlService(
        identityctl=_Identity(),
        sessctl=session or _Session(),
        memctl=_Mem(),
        artifactctl=_Art(digests=digests),
        plugin_registry=registry,
    )


def _base_request(**kwargs):
    defaults = dict(session_id="s1", agent_id="ag1", purpose="act", query="go")
    defaults.update(kwargs)
    return BuildPackRequest(**defaults)


class RetrieverStub:
    contract_version = "v1"
    name = "stub-retriever"

    def retrieve(self, *, session_id, query, k, filters) -> list[EvidenceItem]:
        return [EvidenceItem(ref="r1", content="evidence", score=0.9, source=self.name)]


class CompressorStub:
    contract_version = "v1"
    name = "stub-compressor"

    def compress(self, *, query, items, budget_tokens) -> list[EvidenceItem]:
        return items[:1]


class TestPluginRegistry(unittest.TestCase):
    def test_register_and_lookup_retriever(self):
        reg = PluginRegistry()
        plugin = RetrieverStub()
        reg.register_retriever(plugin)
        self.assertIn("stub-retriever", reg.retriever_names)
        self.assertIs(reg.get_retriever("stub-retriever"), plugin)

    def test_register_and_lookup_compressor(self):
        reg = PluginRegistry()
        plugin = CompressorStub()
        reg.register_compressor(plugin)
        self.assertIn("stub-compressor", reg.compressor_names)
        self.assertIs(reg.get_compressor("stub-compressor"), plugin)

    def test_missing_plugin_returns_none(self):
        reg = PluginRegistry()
        self.assertIsNone(reg.get_retriever("nope"))
        self.assertIsNone(reg.get_compressor("nope"))

    def test_registry_accepted_by_service(self):
        reg = PluginRegistry()
        reg.register_retriever(RetrieverStub())
        svc = _make_service(registry=reg)
        pack = svc.build_pack(_base_request())
        self.assertIsNotNone(pack)

    def test_manifest_records_retriever_names(self):
        reg = PluginRegistry()
        reg.register_retriever(RetrieverStub())
        reg.register_compressor(CompressorStub())
        svc = _make_service(registry=reg)
        pack = svc.build_pack(_base_request())
        m = pack.context_manifest
        self.assertIsNotNone(m)
        self.assertIn("stub-retriever", m.retrievers_used)
        self.assertIn("stub-compressor", m.compressors_used)

    def test_manifest_records_pack_policy_used(self):
        svc = _make_service()
        pack = svc.build_pack(_base_request())
        m = pack.context_manifest
        self.assertIsNotNone(m)
        self.assertEqual(m.pack_policy_used, "position_aware_v1")

    def test_protocol_conformance_retriever(self):
        self.assertIsInstance(RetrieverStub(), ContextRetriever)

    def test_protocol_conformance_compressor(self):
        self.assertIsInstance(CompressorStub(), ContextCompressor)


class TestPositionAwareV1(unittest.TestCase):
    def _segs(self, n):
        return [
            _make_segment(f"ev:{i}", "evidence_refs", f"content {i}") for i in range(n)
        ]

    def test_empty_returns_empty(self):
        self.assertEqual(_position_aware_v1([], []), [])

    def test_single_unchanged(self):
        segs = self._segs(1)
        result = _position_aware_v1(segs, [0.8])
        self.assertEqual(result[0].id, segs[0].id)

    def test_highest_score_at_position_zero(self):
        segs = self._segs(4)
        scores = [0.1, 0.9, 0.3, 0.5]  # idx1 is highest
        result = _position_aware_v1(segs, scores)
        self.assertEqual(result[0].id, "ev:1")  # highest at head

    def test_second_highest_at_tail(self):
        segs = self._segs(4)
        scores = [0.1, 0.9, 0.3, 0.5]  # 0.9, 0.5, 0.3, 0.1 sorted desc
        result = _position_aware_v1(segs, scores)
        self.assertEqual(result[-1].id, "ev:3")  # second highest at tail

    def test_output_length_preserved(self):
        segs = self._segs(5)
        scores = [0.5, 0.9, 0.1, 0.7, 0.3]
        result = _position_aware_v1(segs, scores)
        self.assertEqual(len(result), 5)

    def test_all_segments_present(self):
        segs = self._segs(6)
        scores = [float(i) for i in range(6)]
        result = _position_aware_v1(segs, scores)
        ids_in = {s.id for s in segs}
        ids_out = {s.id for s in result}
        self.assertEqual(ids_in, ids_out)

    def test_service_applies_ordering_to_evidence(self):
        digests = [
            ArtifactDigest(ref="art-low", score=0.1, digest_hash="h1", bullets=["low"]),
            ArtifactDigest(
                ref="art-high", score=0.9, digest_hash="h2", bullets=["high"]
            ),
            ArtifactDigest(ref="art-mid", score=0.5, digest_hash="h3", bullets=["mid"]),
        ]
        svc = _make_service(digests=digests)
        pack = svc.build_pack(_base_request())
        ev_segs = [
            s
            for s in pack.segments
            if s.bucket == "evidence_refs" and s.content.strip()
        ]
        if len(ev_segs) >= 2:
            self.assertIn("art-high", ev_segs[0].refs)


class TestLayoutDiscipline(unittest.TestCase):
    def test_valid_layout_no_error(self):
        segs = [
            _make_segment("static", "static_prefix", "identity"),
            _make_segment("ev", "evidence_refs", "evidence"),
            _make_segment("q", "turn_input", "query", role="user"),
        ]
        _assert_layout_discipline(segs)

    def test_violation_raises(self):
        segs = [
            _make_segment("q", "turn_input", "query", role="user"),
            _make_segment("ev", "evidence_refs", "evidence"),
        ]
        with self.assertRaises(LayoutDisciplineError):
            _assert_layout_discipline(segs)

    def test_empty_segment_after_turn_input_ok(self):
        segs = [
            _make_segment("q", "turn_input", "query", role="user"),
            _make_segment("empty", "evidence_refs", ""),  # empty → ignored
        ]
        _assert_layout_discipline(segs)

    def test_service_pack_satisfies_layout(self):
        svc = _make_service()
        pack = svc.build_pack(_base_request())
        _assert_layout_discipline(pack.segments)

    def test_turn_input_is_last_non_empty_segment(self):
        svc = _make_service()
        pack = svc.build_pack(_base_request())
        non_empty = [s for s in pack.segments if s.content.strip()]
        if non_empty:
            self.assertEqual(non_empty[-1].bucket, "turn_input")

    def test_evidence_precedes_query_in_messages(self):
        digests = [
            ArtifactDigest(ref="art1", score=0.5, digest_hash="h1", bullets=["b1"])
        ]
        svc = _make_service(digests=digests)
        pack = svc.build_pack(_base_request())
        user_msgs = [i for i, m in enumerate(pack.messages) if m.role == "user"]
        if user_msgs:
            last_user_idx = max(user_msgs)
            for i, msg in enumerate(pack.messages[:last_user_idx]):
                self.assertNotEqual(
                    msg.role,
                    "user",
                    f"Unexpected user message before query at index {i}",
                )


class TestManifestCreatedEvent(unittest.TestCase):
    def _build_once(self, session=None, **kwargs):
        session = session or _Session()
        svc = _make_service(session=session)
        pack = svc.build_pack(_base_request(**kwargs))
        return pack, session

    def _manifest_events(self, session: _Session):
        return [
            e
            for e in session.events
            if e["event_type"] in {"context.manifest.created", "context.manifest"}
        ]

    def test_manifest_event_type_is_created(self):
        _, session = self._build_once()
        events = session.events
        self.assertTrue(
            any(e["event_type"] == "context.manifest.created" for e in events)
        )

    def test_no_legacy_manifest_event(self):
        _, session = self._build_once()
        legacy = [e for e in session.events if e["event_type"] in ("ctx.pack.created",)]
        self.assertEqual(legacy, [])

    def test_exactly_one_manifest_event_per_call(self):
        session = _Session()
        svc = _make_service(session=session)
        req = _base_request()
        svc.build_pack(req)
        manifest_events = self._manifest_events(session)
        self.assertEqual(len(manifest_events), 1)

    def test_cache_hit_still_emits_manifest_event(self):
        session = _Session()
        svc = _make_service(session=session)
        req = _base_request()
        svc.build_pack(req)
        svc.build_pack(req)  # cache hit
        manifest_events = self._manifest_events(session)
        self.assertEqual(len(manifest_events), 2)
        self.assertFalse(manifest_events[0]["payload"]["cache_hit"])
        self.assertTrue(manifest_events[1]["payload"]["cache_hit"])

    def test_manifest_event_contains_llm_call_id(self):
        _, session = self._build_once()
        events = self._manifest_events(session)
        self.assertTrue(len(events) >= 1)
        payload = events[0]["payload"]
        self.assertIn("llm_call_id", payload)
        self.assertTrue(payload["llm_call_id"])  # non-empty

    def test_caller_provided_llm_call_id_is_used(self):
        session = _Session()
        svc = _make_service(session=session)
        req = _base_request(llm_call_id="custom-call-id-abc")
        svc.build_pack(req)
        events = self._manifest_events(session)
        self.assertEqual(events[0]["payload"]["llm_call_id"], "custom-call-id-abc")

    def test_manifest_event_contains_pack_policy_used(self):
        _, session = self._build_once()
        events = self._manifest_events(session)
        payload = events[0]["payload"]
        self.assertIn("pack_policy_used", payload)
        self.assertEqual(payload["pack_policy_used"], "position_aware_v1")

    def test_manifest_contains_llm_call_id(self):
        pack, _ = self._build_once()
        self.assertIsNotNone(pack.context_manifest)
        self.assertTrue(pack.context_manifest.llm_call_id)

    def test_two_calls_have_different_llm_call_ids(self):
        session1 = _Session()
        session2 = _Session()
        svc1 = _make_service(session=session1)
        svc2 = _make_service(session=session2)
        pack1 = svc1.build_pack(_base_request(query="q1"))
        pack2 = svc2.build_pack(_base_request(query="q2"))
        self.assertNotEqual(
            pack1.context_manifest.llm_call_id,
            pack2.context_manifest.llm_call_id,
        )

    def test_manifest_event_contains_included_segment_ids(self):
        _, session = self._build_once()
        events = self._manifest_events(session)
        payload = events[0]["payload"]
        self.assertIn("included_segment_ids", payload)
        self.assertIsInstance(payload["included_segment_ids"], list)


class TestPluginSchemas(unittest.TestCase):
    def test_evidence_item_defaults(self):
        item = EvidenceItem(ref="r1", content="text")
        self.assertEqual(item.score, 0.0)
        self.assertEqual(item.source, "")


class _StubRetriever:
    contract_version = "v1"
    name = "stub_retriever"

    def __init__(self, items: list[EvidenceItem] | None = None) -> None:
        self._items = items or []
        self.calls: list[dict] = []

    def retrieve(self, *, session_id, query, k, filters):
        self.calls.append({"session_id": session_id, "query": query, "k": k})
        return self._items[:k]


class _StubCompressor:
    contract_version = "v1"
    name = "stub_compressor"

    def __init__(self, keep: int = 99) -> None:
        self._keep = keep
        self.calls: list[dict] = []

    def compress(self, *, query, items, budget_tokens):
        self.calls.append(
            {"query": query, "n_items": len(items), "budget": budget_tokens}
        )
        return items[: self._keep]


class _RaisingRetriever:
    contract_version = "v1"
    name = "raising_retriever"

    def retrieve(self, *, session_id, query, k, filters):
        raise RuntimeError("retriever boom")


class _RaisingCompressor:
    contract_version = "v1"
    name = "raising_compressor"

    def compress(self, *, query, items, budget_tokens):
        raise RuntimeError("compressor boom")


def _make_ev_items(n: int) -> list[EvidenceItem]:
    return [
        EvidenceItem(ref=f"r{i}", content=f"content {i}", score=float(i))
        for i in range(n)
    ]


class TestPluginEvidencePipeline(unittest.TestCase):
    def _svc(
        self, registry: PluginRegistry | None = None
    ) -> tuple[ContextCtlService, _Session]:
        session = _Session()
        svc = _make_service(session=session, registry=registry)
        return svc, session

    def _req(self, query: str = "q") -> BuildPackRequest:
        return BuildPackRequest(
            session_id="s1",
            agent_id="a1",
            purpose="act",
            query=query,
        )

    def test_retriever_receives_session_and_query(self):
        items = _make_ev_items(3)
        retriever = _StubRetriever(items)
        reg = PluginRegistry()
        reg.register_retriever(retriever)
        svc, _ = self._svc(reg)
        svc.build_pack(self._req("hello"))
        self.assertEqual(len(retriever.calls), 1)
        self.assertEqual(retriever.calls[0]["session_id"], "s1")
        self.assertEqual(retriever.calls[0]["query"], "hello")

    def test_retriever_items_appear_in_pack(self):
        items = _make_ev_items(2)
        reg = PluginRegistry()
        reg.register_retriever(_StubRetriever(items))
        svc, _ = self._svc(reg)
        pack = svc.build_pack(self._req())
        msgs_text = " ".join(m.content for m in pack.messages if m.content)
        self.assertIn("content 0", msgs_text)
        self.assertIn("content 1", msgs_text)

    def test_compressor_receives_retrieved_items(self):
        items = _make_ev_items(5)
        retriever = _StubRetriever(items)
        compressor = _StubCompressor(keep=99)
        reg = PluginRegistry()
        reg.register_retriever(retriever)
        reg.register_compressor(compressor)
        svc, _ = self._svc(reg)
        svc.build_pack(self._req("foo"))
        self.assertEqual(len(compressor.calls), 1)
        self.assertEqual(compressor.calls[0]["n_items"], 5)
        self.assertEqual(compressor.calls[0]["query"], "foo")

    def test_compressor_limits_evidence(self):
        items = _make_ev_items(5)
        reg = PluginRegistry()
        reg.register_retriever(_StubRetriever(items))
        reg.register_compressor(_StubCompressor(keep=2))
        svc, _ = self._svc(reg)
        pack = svc.build_pack(self._req())
        ev_segs = [s for s in pack.segments if s.id.startswith("plugin_ev:")]
        self.assertLessEqual(len(ev_segs), 2)

    def test_multiple_retrievers_all_called(self):
        r1 = _StubRetriever(_make_ev_items(2))
        r1.name = "r1"
        r2 = _StubRetriever(_make_ev_items(2))
        r2.name = "r2"
        reg = PluginRegistry()
        reg.register_retriever(r1)
        reg.register_retriever(r2)
        svc, _ = self._svc(reg)
        svc.build_pack(self._req())
        self.assertEqual(len(r1.calls), 1)
        self.assertEqual(len(r2.calls), 1)

    def test_raising_retriever_silently_skipped(self):
        reg = PluginRegistry()
        reg.register_retriever(_RaisingRetriever())
        svc, _ = self._svc(reg)
        pack = svc.build_pack(self._req())  # must not raise
        self.assertIsNotNone(pack)

    def test_raising_compressor_silently_skipped(self):
        reg = PluginRegistry()
        reg.register_retriever(_StubRetriever(_make_ev_items(3)))
        reg.register_compressor(_RaisingCompressor())
        svc, _ = self._svc(reg)
        pack = svc.build_pack(self._req())  # must not raise
        self.assertIsNotNone(pack)

    def test_no_retrievers_pipeline_not_invoked(self):
        compressor = _StubCompressor()
        reg = PluginRegistry()
        reg.register_compressor(compressor)
        svc, _ = self._svc(reg)
        svc.build_pack(self._req())
        self.assertEqual(compressor.calls, [])

    def test_manifest_records_plugin_names(self):
        r = _StubRetriever()
        r.name = "my_retriever"
        c = _StubCompressor()
        c.name = "my_compressor"
        reg = PluginRegistry()
        reg.register_retriever(r)
        reg.register_compressor(c)
        svc, _ = self._svc(reg)
        pack = svc.build_pack(self._req())
        self.assertIsNotNone(pack.context_manifest)
        self.assertIn("my_retriever", pack.context_manifest.retrievers_used)
        self.assertIn("my_compressor", pack.context_manifest.compressors_used)


class TestPrefixCacheAdapter(unittest.TestCase):
    def test_default_provider_is_generic(self):
        adapter = PrefixCacheAdapter()
        self.assertEqual(adapter.provider, "generic")

    def test_explicit_provider_stored(self):
        for p in ("openai", "anthropic", "generic"):
            adapter = PrefixCacheAdapter(provider=p)
            self.assertEqual(adapter.provider, p)

    def test_unsupported_provider_raises(self):
        with self.assertRaises(ValueError):
            PrefixCacheAdapter(provider="cohere")

    def test_same_inputs_same_key(self):
        a = PrefixCacheAdapter(provider="generic")
        k1 = a.build_cache_key(
            agent_id="ag",
            static_prefix_hash="sph",
            tool_schema_hash="tsh",
            policy_hash="ph",
            model_hint="gpt-4",
        )
        k2 = a.build_cache_key(
            agent_id="ag",
            static_prefix_hash="sph",
            tool_schema_hash="tsh",
            policy_hash="ph",
            model_hint="gpt-4",
        )
        self.assertEqual(k1, k2)

    def test_different_agent_different_key(self):
        a = PrefixCacheAdapter()
        k1 = a.build_cache_key(
            agent_id="a1", static_prefix_hash="x", tool_schema_hash="x", policy_hash="x"
        )
        k2 = a.build_cache_key(
            agent_id="a2", static_prefix_hash="x", tool_schema_hash="x", policy_hash="x"
        )
        self.assertNotEqual(k1, k2)

    def test_different_prefix_hash_different_key(self):
        a = PrefixCacheAdapter()
        k1 = a.build_cache_key(
            agent_id="a", static_prefix_hash="h1", tool_schema_hash="x", policy_hash="x"
        )
        k2 = a.build_cache_key(
            agent_id="a", static_prefix_hash="h2", tool_schema_hash="x", policy_hash="x"
        )
        self.assertNotEqual(k1, k2)

    def test_key_prefixed_with_provider(self):
        for p in ("openai", "anthropic", "generic"):
            adapter = PrefixCacheAdapter(provider=p)
            key = adapter.build_cache_key(
                agent_id="a",
                static_prefix_hash="s",
                tool_schema_hash="t",
                policy_hash="p",
            )
            self.assertTrue(key.startswith(f"{p}:"), key)

    def test_openai_includes_model_in_key(self):
        a = PrefixCacheAdapter(provider="openai")
        k_with = a.build_cache_key(
            agent_id="a",
            static_prefix_hash="s",
            tool_schema_hash="t",
            policy_hash="p",
            model_hint="gpt-4o",
        )
        k_without = a.build_cache_key(
            agent_id="a",
            static_prefix_hash="s",
            tool_schema_hash="t",
            policy_hash="p",
            model_hint="",
        )
        self.assertNotEqual(k_with, k_without)

    def test_anthropic_includes_model_in_key(self):
        a = PrefixCacheAdapter(provider="anthropic")
        k1 = a.build_cache_key(
            agent_id="a",
            static_prefix_hash="s",
            tool_schema_hash="t",
            policy_hash="p",
            model_hint="claude-3-opus",
        )
        k2 = a.build_cache_key(
            agent_id="a",
            static_prefix_hash="s",
            tool_schema_hash="t",
            policy_hash="p",
            model_hint="claude-3-sonnet",
        )
        self.assertNotEqual(k1, k2)

    def test_generic_ignores_model_hint(self):
        a = PrefixCacheAdapter(provider="generic")
        k1 = a.build_cache_key(
            agent_id="a",
            static_prefix_hash="s",
            tool_schema_hash="t",
            policy_hash="p",
            model_hint="model-a",
        )
        k2 = a.build_cache_key(
            agent_id="a",
            static_prefix_hash="s",
            tool_schema_hash="t",
            policy_hash="p",
            model_hint="model-b",
        )
        self.assertEqual(k1, k2)

    def test_anthropic_cache_control_block_has_type(self):
        a = PrefixCacheAdapter(provider="anthropic")
        block = a.cache_control_blocks(prefix_hash="abc123")
        self.assertIn("cache_control", block)
        self.assertEqual(block["cache_control"]["type"], "ephemeral")
        self.assertEqual(block["prefix_hash"], "abc123")

    def test_openai_cache_control_block(self):
        a = PrefixCacheAdapter(provider="openai")
        block = a.cache_control_blocks(prefix_hash="xyz")
        self.assertIn("cache_control", block)
        self.assertEqual(block["prefix_hash"], "xyz")

    def test_generic_cache_control_block_has_prefix_hash(self):
        a = PrefixCacheAdapter(provider="generic")
        block = a.cache_control_blocks(prefix_hash="hash1")
        self.assertEqual(block["prefix_hash"], "hash1")

    def test_service_uses_adapter_key_in_pack(self):
        adapter = PrefixCacheAdapter(provider="anthropic")
        svc = ContextCtlService(
            identityctl=_Identity(),
            sessctl=_Session(),
            memctl=_Mem(),
            artifactctl=_Art(),
            prefix_cache_adapter=adapter,
        )
        req = BuildPackRequest(
            session_id="s1",
            agent_id="a1",
            purpose="act",
            query="hello",
            model_hint="claude-3-opus",
        )
        pack = svc.build_pack(req)
        self.assertTrue(
            pack.prompt_cache_key.startswith("anthropic:"), pack.prompt_cache_key
        )

    def test_service_without_adapter_uses_generic_hash(self):
        svc = ContextCtlService(
            identityctl=_Identity(),
            sessctl=_Session(),
            memctl=_Mem(),
            artifactctl=_Art(),
        )
        req = BuildPackRequest(session_id="s1", agent_id="a1", purpose="act", query="q")
        pack = svc.build_pack(req)
        self.assertFalse(pack.prompt_cache_key.startswith(("openai:", "anthropic:")))
