from __future__ import annotations

import logging

from openminion.base.config import OpenMinionConfig
from openminion.base.time import utc_now_iso
from openminion.modules.memory.backends import BuiltinKnowledgeBackend
from openminion.modules.memory.models import MemoryRelation
from openminion.modules.memory.runtime.recall import SophiagraphRecallAdapter
from openminion.modules.memory.service import MemoryService
from openminion.modules.memory.storage.memory import InMemoryMemoryStore
from openminion.services.agent.memory.gateway_adapter import MemoryServiceGatewayAdapter
from openminion.services.runtime.memory import _build_memory_v2_gateway_adapter


def _memory() -> tuple[InMemoryMemoryStore, MemoryService, SophiagraphRecallAdapter]:
    store = InMemoryMemoryStore()
    service = MemoryService(store=store)
    recall = SophiagraphRecallAdapter(
        backend=BuiltinKnowledgeBackend(store),
        provider="sophiagraph",
    )
    return store, service, recall


def test_runtime_factory_wires_precision_recall_to_selected_backend(tmp_path) -> None:
    adapter = _build_memory_v2_gateway_adapter(
        config=OpenMinionConfig(),
        agent_id="alpha",
        memory_root=tmp_path,
        logger=logging.getLogger("openminion.tests.precision.factory"),
        config_manager=None,
        home_root=None,
        data_root=None,
        session_context=None,
        retrieve_ctl=None,
        storage_path=None,
        resolve_runtime_memory_config_fn=lambda **_kwargs: {
            "backend": {"provider": "sophiagraph"},
            "store": {"backend": "mock"},
            "retrieval": {"precision_mode": "sophiagraph"},
        },
        artifactctl_factory=lambda: None,
    )

    assert adapter._recall_adapter.capabilities.keyword is True  # noqa: SLF001
    assert adapter._precision_options.mode == "sophiagraph"  # noqa: SLF001


def test_runtime_factory_keeps_backend_none_explicit(tmp_path) -> None:
    adapter = _build_memory_v2_gateway_adapter(
        config=OpenMinionConfig(),
        agent_id="alpha",
        memory_root=tmp_path,
        logger=logging.getLogger("openminion.tests.precision.none"),
        config_manager=None,
        home_root=None,
        data_root=None,
        session_context=None,
        retrieve_ctl=None,
        storage_path=None,
        resolve_runtime_memory_config_fn=lambda **_kwargs: {
            "backend": {"provider": "none"}
        },
        artifactctl_factory=lambda: None,
    )

    assert adapter.enabled is False
    assert adapter.disabled_reason == "backend_none"


def test_precision_recall_preserves_scores_and_graph_evidence() -> None:
    store, service, recall = _memory()
    seed_id = service.write_record(
        scope="agent:alpha",
        record_type="fact",
        title="Seattle weather",
        content={"text": "Seattle has rainy winters."},
    )
    neighbor_id = service.write_record(
        scope="agent:alpha",
        record_type="fact",
        title="Rain season",
        content={"text": "The wettest months are November through March."},
    )
    store.put_relation(
        MemoryRelation(
            relation_id="rel-seattle-rain",
            source_record_id=seed_id,
            target_record_id=neighbor_id,
            relation_type="supports",
            created_at=utc_now_iso(),
        )
    )

    outcome = recall.retrieve(
        query="Seattle",
        scopes=["agent:alpha"],
        limit=5,
        candidate_multiplier=3,
        minimum_score=0.0,
        graph_depth=1,
    )

    assert outcome.status == "ok"
    assert {hit.record.id for hit in outcome.hits} == {seed_id, neighbor_id}
    graph_hit = next(hit for hit in outcome.hits if hit.record.id == neighbor_id)
    assert graph_hit.explanation.via_relation_ids == ["rel-seattle-rain"]
    assert {item.kind for item in graph_hit.explanation.components} == {
        "graph_proximity",
        "recency",
        "trust",
    }
    repeated = recall.retrieve(
        query="Seattle",
        scopes=["agent:alpha"],
        limit=5,
        candidate_multiplier=3,
        minimum_score=0.0,
        graph_depth=1,
    )
    assert [hit.record.id for hit in repeated.hits] == [
        hit.record.id for hit in outcome.hits
    ]


def test_precision_recall_does_not_expand_graph_across_scope() -> None:
    store, service, recall = _memory()
    seed_id = service.write_record(
        scope="agent:alpha",
        record_type="fact",
        title="Visible project",
        content={"text": "Visible project context."},
    )
    private_id = service.write_record(
        scope="agent:private",
        record_type="fact",
        title="Private project",
        content={"text": "Private graph detail."},
    )
    store.put_relation(
        MemoryRelation(
            relation_id="rel-cross-scope",
            source_record_id=seed_id,
            target_record_id=private_id,
            relation_type="related_to",
            created_at=utc_now_iso(),
        )
    )

    outcome = recall.retrieve(
        query="Visible",
        scopes=["agent:alpha"],
        limit=5,
        candidate_multiplier=3,
        minimum_score=0.0,
        graph_depth=1,
    )

    assert [hit.record.id for hit in outcome.hits] == [seed_id]


def test_precision_recall_abstains_at_configured_score_boundary() -> None:
    _, service, recall = _memory()
    service.write_record(
        scope="agent:alpha",
        record_type="fact",
        title="Seattle weather",
        content={"text": "Seattle has rainy winters."},
    )

    outcome = recall.retrieve(
        query="Seattle",
        scopes=["agent:alpha"],
        limit=5,
        candidate_multiplier=3,
        minimum_score=0.1,
        graph_depth=1,
    )

    assert outcome.status == "ok"
    assert outcome.hits == ()
    assert outcome.candidate_count == 1
    assert outcome.threshold_drops == 1


def test_precision_recall_excludes_invalidated_graph_neighbor() -> None:
    store, service, recall = _memory()
    seed_id = service.write_record(
        scope="agent:alpha",
        record_type="fact",
        title="Seattle weather",
        content={"text": "Seattle has rainy winters."},
    )
    neighbor_id = service.write_record(
        scope="agent:alpha",
        record_type="fact",
        title="Old rain season",
        content={"text": "This fact has been corrected."},
    )
    store.invalidate(neighbor_id, valid_to=utc_now_iso(), reason="corrected")
    store.put_relation(
        MemoryRelation(
            relation_id="rel-invalidated",
            source_record_id=seed_id,
            target_record_id=neighbor_id,
            relation_type="supports",
            created_at=utc_now_iso(),
        )
    )

    outcome = recall.retrieve(
        query="Seattle",
        scopes=["agent:alpha"],
        limit=5,
        candidate_multiplier=3,
        minimum_score=0.0,
        graph_depth=1,
    )

    assert [hit.record.id for hit in outcome.hits] == [seed_id]


def test_precision_recall_reports_disabled_and_unsupported_backends() -> None:
    backend = BuiltinKnowledgeBackend(InMemoryMemoryStore())

    disabled = SophiagraphRecallAdapter(backend=backend, provider="none").retrieve(
        query="weather",
        scopes=["agent:alpha"],
        limit=5,
        candidate_multiplier=3,
        minimum_score=0.0,
        graph_depth=1,
    )
    unsupported = SophiagraphRecallAdapter(
        backend=backend,
        provider="external",
    ).retrieve(
        query="weather",
        scopes=["agent:alpha"],
        limit=5,
        candidate_multiplier=3,
        minimum_score=0.0,
        graph_depth=1,
    )

    assert (disabled.status, disabled.reason) == ("disabled", "backend_none")
    assert (unsupported.status, unsupported.reason) == (
        "unsupported",
        "precision_recall_unsupported",
    )


def test_gateway_precision_context_keeps_content_and_full_fetch_id() -> None:
    _, service, recall = _memory()
    record_id = service.write_record(
        scope="agent:alpha",
        record_type="fact",
        title="Short title",
        content={
            "text": "The meaningful content follows the title and must remain visible."
        },
    )
    gateway = MemoryServiceGatewayAdapter(
        service,
        agent_id="alpha",
        memory_config={
            "retrieval": {
                "precision_mode": "sophiagraph",
                "precision_min_score": 0.0,
                "precision_max_items": 5,
                "precision_max_tokens": 500,
            }
        },
        recall_adapter=recall,
    )

    content, meta = gateway.build_retrieval_context_with_metadata(
        session_id="session-1",
        user_message="meaningful",
    )

    assert "The meaningful content follows the title" in content
    assert f"full record: fetch authorized memory ID {record_id}" in content
    assert meta["memory_recall_mode"] == "sophiagraph"
    assert meta["memory_recall_status"] == "ok"
    assert meta["memory_recall_abstained"] == "false"
    assert meta["memory_recall_capabilities"] == "keyword,graph,recency,trust"


def test_gateway_precision_context_fits_complete_card_with_bounded_excerpt() -> None:
    _, service, recall = _memory()
    record_id = service.write_record(
        scope="agent:alpha",
        record_type="fact",
        title="Tokyo notes",
        content={"text": "Tokyo " + "東京 detail " * 80},
    )
    gateway = MemoryServiceGatewayAdapter(
        service,
        agent_id="alpha",
        memory_config={
            "retrieval": {
                "precision_mode": "sophiagraph",
                "precision_min_score": 0.0,
                "precision_max_items": 5,
                "precision_max_tokens": 64,
            }
        },
        recall_adapter=recall,
    )

    content, meta = gateway.build_retrieval_context_with_metadata(
        session_id="session-1",
        user_message="Tokyo",
    )

    assert len(content) <= 256
    assert "Tokyo notes" in content
    assert "東京" in content
    assert "…" in content
    assert f"full record: fetch authorized memory ID {record_id}" in content
    assert meta["memory_envelope_included_items"] == "1"


def test_shadow_mode_preserves_legacy_context() -> None:
    _, service, recall = _memory()
    service.write_record(
        scope="agent:alpha",
        record_type="fact",
        title="Legacy visible title",
        content={"text": "Precision-only detail."},
        confidence=0.9,
    )
    base_retrieval = {
        "min_confidence_default": 0.0,
        "precision_min_score": 0.048,
    }
    legacy = MemoryServiceGatewayAdapter(
        service,
        agent_id="alpha",
        memory_config={"retrieval": {**base_retrieval, "precision_mode": "legacy"}},
        recall_adapter=recall,
    )
    shadow = MemoryServiceGatewayAdapter(
        service,
        agent_id="alpha",
        memory_config={"retrieval": {**base_retrieval, "precision_mode": "shadow"}},
        recall_adapter=recall,
    )

    legacy_content, _ = legacy.build_retrieval_context_with_metadata(
        session_id="session-1",
        user_message="Legacy visible title",
    )
    shadow_content, shadow_meta = shadow.build_retrieval_context_with_metadata(
        session_id="session-1",
        user_message="Legacy visible title",
    )

    assert shadow_content == legacy_content
    assert shadow_meta["memory_recall_mode"] == "shadow"


def test_precision_mode_abstains_and_keeps_scope_isolated() -> None:
    _, service, recall = _memory()
    service.write_record(
        scope="agent:private",
        record_type="fact",
        title="Secret project name",
        content={"text": "Never disclose this value."},
        confidence=0.9,
    )
    gateway = MemoryServiceGatewayAdapter(
        service,
        agent_id="alpha",
        memory_config={
            "retrieval": {
                "precision_mode": "sophiagraph",
                "precision_min_score": 0.048,
            }
        },
        recall_adapter=recall,
    )

    content, meta = gateway.build_retrieval_context_with_metadata(
        session_id="session-1",
        user_message="Secret project name",
    )

    assert content == ""
    assert meta["memory_recall_status"] == "ok"
    assert meta["memory_recall_abstained"] == "true"
    assert "Secret project name" not in str(meta)


def test_precision_mode_requires_runtime_recall_adapter() -> None:
    _, service, _ = _memory()
    gateway = MemoryServiceGatewayAdapter(
        service,
        agent_id="alpha",
        memory_config={"retrieval": {"precision_mode": "sophiagraph"}},
    )

    content, meta = gateway.build_retrieval_context_with_metadata(
        session_id="session-1",
        user_message="weather",
    )

    assert content == ""
    assert meta["memory_recall_status"] == "unsupported"
    assert meta["memory_recall_reason"] == "recall_adapter_unavailable"
