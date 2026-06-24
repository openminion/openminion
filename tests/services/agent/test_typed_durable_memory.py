from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from openminion.base.config import OpenMinionConfig
from openminion.modules.memory.config import MemctlConfig, from_base_config
from openminion.modules.memory.models import MemoryRecord
from openminion.modules.memory.service import MemoryService
from openminion.modules.memory.storage.base import (
    CandidateListOptions,
    ListQueryOptions,
)
from openminion.modules.memory.storage.memory import InMemoryMemoryStore
from openminion.services.agent.memory.extraction import (
    explicit_memory_type_from_content,
)
from openminion.services.agent.memory.gateway_adapter import (
    MemoryServiceGatewayAdapter,
)


def _memory_config(
    *,
    auto_extract_enabled: bool = False,
    type_boost_correction: float = 1.5,
    type_boost_user_preference: float = 1.3,
    type_boost_pin: float = 1.2,
    type_boost_project_convention: float = 1.1,
) -> MemctlConfig:
    cfg = from_base_config(
        base_config=OpenMinionConfig(),
        home_root=Path("/tmp/openminion-home"),
        data_root=Path("/tmp/openminion-data"),
    )
    # `promotion.auto_extract_enabled` was moved
    # to `candidate_learning.auto_extract_enabled`. Set it on the new
    # location directly to avoid triggering the deprecation compat merge.
    return replace(
        cfg,
        candidate_learning=replace(
            cfg.candidate_learning, auto_extract_enabled=auto_extract_enabled
        ),
        retrieval=replace(
            cfg.retrieval,
            type_boost_correction=type_boost_correction,
            type_boost_user_preference=type_boost_user_preference,
            type_boost_pin=type_boost_pin,
            type_boost_project_convention=type_boost_project_convention,
        ),
    )


def _make_real_adapter(
    *,
    project_id: str | None = None,
    auto_extract_enabled: bool = False,
    type_boost_correction: float = 1.5,
    type_boost_user_preference: float = 1.3,
    type_boost_pin: float = 1.2,
    type_boost_project_convention: float = 1.1,
) -> tuple[InMemoryMemoryStore, MemoryService, MemoryServiceGatewayAdapter]:
    store = InMemoryMemoryStore()
    service = MemoryService(store=store)
    adapter = MemoryServiceGatewayAdapter(
        service,
        agent_id="typed-agent",
        project_id=project_id,
        memory_config=_memory_config(
            auto_extract_enabled=auto_extract_enabled,
            type_boost_correction=type_boost_correction,
            type_boost_user_preference=type_boost_user_preference,
            type_boost_pin=type_boost_pin,
            type_boost_project_convention=type_boost_project_convention,
        ),
    )
    return store, service, adapter


def test_explicit_memory_type_helper_only_honors_explicit_prefixes() -> None:
    assert explicit_memory_type_from_content("Preference memory: dark mode") == (
        "user_preference"
    )
    assert explicit_memory_type_from_content("Project memory: pytest") == (
        "project_convention"
    )
    assert explicit_memory_type_from_content("Correction memory: do not mock DB") == (
        "correction"
    )
    assert explicit_memory_type_from_content("I prefer dark mode") == "fact"
    assert explicit_memory_type_from_content("we use PostgreSQL 15") == "fact"


def test_explicit_remember_routes_typed_records_to_expected_scope() -> None:
    _store, service, adapter = _make_real_adapter(project_id="typed-project")

    adapter.record_turn(
        session_id="typed-session",
        run_id="run-1",
        request_id="req-1",
        channel="console",
        target="chat",
        user_message="remember: Preference memory: terse responses",
        assistant_message="Got it.",
    )
    adapter.record_turn(
        session_id="typed-session",
        run_id="run-2",
        request_id="req-2",
        channel="console",
        target="chat",
        user_message="remember: Project memory: we use pytest",
        assistant_message="Noted.",
    )
    adapter.record_turn(
        session_id="typed-session",
        run_id="run-3",
        request_id="req-3",
        channel="console",
        target="chat",
        user_message="remember: Correction memory: don't mock the database",
        assistant_message="Understood.",
    )

    preferences = service.list(
        ListQueryOptions(
            scopes=["agent:typed-agent"],
            types=["user_preference"],
            limit=10,
        )
    )
    corrections = service.list(
        ListQueryOptions(
            scopes=["agent:typed-agent"],
            types=["correction"],
            limit=10,
        )
    )
    conventions = service.list(
        ListQueryOptions(
            scopes=["project:typed-project"],
            types=["project_convention"],
            limit=10,
        )
    )

    assert any("terse responses" in str(item.content).lower() for item in preferences)
    assert any(
        "don't mock the database" in str(item.content).lower() for item in corrections
    )
    assert any("we use pytest" in str(item.content).lower() for item in conventions)


def test_auto_extract_no_longer_stages_heuristic_candidates() -> None:
    _store, service, adapter = _make_real_adapter(
        project_id="typed-project",
        auto_extract_enabled=True,
    )

    adapter.record_turn(
        session_id="typed-auto",
        run_id="run-1",
        request_id="req-1",
        channel="console",
        target="chat",
        user_message="I prefer dark mode. We use pytest.",
        assistant_message="Okay.",
    )

    candidates = service.candidate_list(
        CandidateListOptions(session_id="typed-auto", status="proposed", limit=10)
    )

    assert candidates == []


def test_type_ranking_uses_bm25_scores_and_boosts() -> None:
    _store, _service, adapter = _make_real_adapter()
    records = [
        MemoryRecord(
            id="fact-1",
            scope="agent:typed-agent",
            type="fact",
            title="Generic fact",
            content="testing style archive",
            created_at="2026-03-27T00:00:00+00:00",
            updated_at="2026-03-27T00:00:00+00:00",
            meta={"bm25_score": 0.8},
        ),
        MemoryRecord(
            id="pref-1",
            scope="agent:typed-agent",
            type="user_preference",
            title="Preference",
            content="testing style preference",
            created_at="2026-03-27T00:00:00+00:00",
            updated_at="2026-03-27T00:00:00+00:00",
            meta={"bm25_score": 0.8},
        ),
        MemoryRecord(
            id="corr-1",
            scope="agent:typed-agent",
            type="correction",
            title="Correction",
            content="testing style correction",
            created_at="2026-03-27T00:00:00+00:00",
            updated_at="2026-03-27T00:00:00+00:00",
            meta={"bm25_score": 0.8},
        ),
        MemoryRecord(
            id="proj-1",
            scope="project:typed-project",
            type="project_convention",
            title="Convention",
            content="testing style convention",
            created_at="2026-03-27T00:00:00+00:00",
            updated_at="2026-03-27T00:00:00+00:00",
            meta={"bm25_score": 0.8},
        ),
    ]

    reranked = adapter._rerank_long_term_records(records, use_search_scores=True)  # noqa: SLF001
    assert [record.type for record in reranked] == [
        "correction",
        "user_preference",
        "project_convention",
        "fact",
    ]

    _store, _service, boosted_adapter = _make_real_adapter(
        type_boost_project_convention=2.0,
    )
    boosted = boosted_adapter._rerank_long_term_records(records, use_search_scores=True)  # noqa: SLF001
    assert boosted[0].type == "project_convention"
