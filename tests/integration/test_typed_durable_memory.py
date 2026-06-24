from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from openminion.base.config import OpenMinionConfig
from openminion.modules.memory.config import from_base_config
from openminion.modules.memory.service import MemoryService
from openminion.modules.memory.storage.base import (
    CandidateListOptions,
    ListQueryOptions,
)
from openminion.modules.memory.storage.sqlite.store import SQLiteMemoryStore
from openminion.services.agent.memory.gateway_adapter import MemoryServiceGatewayAdapter


def _memory_config(*, auto_extract_enabled: bool = True):
    cfg = from_base_config(
        base_config=OpenMinionConfig(),
        home_root=Path("/tmp/openminion-home"),
        data_root=Path("/tmp/openminion-data"),
    )
    # `promotion.auto_extract_enabled/notify`
    # moved to `candidate_learning.auto_extract_enabled/notify`. Set on
    # the new location directly to avoid the deprecation compat merge.
    return replace(
        cfg,
        candidate_learning=replace(
            cfg.candidate_learning,
            auto_extract_enabled=auto_extract_enabled,
            auto_extract_notify=True,
        ),
    )


def test_typed_memory_lifecycle_assigns_types_and_reranks_capsule(
    tmp_path: Path,
) -> None:
    store = SQLiteMemoryStore(tmp_path / "memory.db")
    service = MemoryService(store=store)
    adapter = MemoryServiceGatewayAdapter(
        service,
        agent_id="typed-agent",
        project_id="typed-project",
        memory_config=_memory_config(auto_extract_enabled=False),
        capsule_max_chars=2400,
    )

    adapter.record_turn(
        session_id="typed-types",
        run_id="run-1",
        request_id="req-1",
        channel="console",
        target="chat",
        user_message="remember: preference memory: I prefer terse testing style guidance",
        assistant_message="Captured.",
    )
    adapter.record_turn(
        session_id="typed-types",
        run_id="run-2",
        request_id="req-2",
        channel="console",
        target="chat",
        user_message="remember: project memory: we use pytest for testing style guidance",
        assistant_message="Captured.",
    )
    adapter.record_turn(
        session_id="typed-types",
        run_id="run-3",
        request_id="req-3",
        channel="console",
        target="chat",
        user_message="remember: correction memory: don't use mocks in testing style guidance",
        assistant_message="Captured.",
    )
    service.write_record(
        scope="agent:typed-agent",
        record_type="fact",
        title="Archive note from an old retro",
        content="Historical testing archive note from an old retro.",
        tags=["archive"],
    )

    preferences = service.list(
        ListQueryOptions(
            scopes=["agent:typed-agent"],
            types=["user_preference"],
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
    corrections = service.list(
        ListQueryOptions(
            scopes=["agent:typed-agent"],
            types=["correction"],
            limit=10,
        )
    )

    assert len(preferences) == 1
    assert len(conventions) == 1
    assert len(corrections) == 1

    context, _meta = adapter.build_context_with_metadata(
        session_id="typed-query",
        user_message="what testing style guidance should you follow?",
    )

    correction_index = context.index("don't use mocks in testing style guidance")
    preference_index = context.index("I prefer terse testing style guidance")
    project_index = context.index("we use pytest for testing style guidance")
    fact_index = context.index("Archive note from an old retro")

    assert correction_index < preference_index < fact_index
    assert project_index < fact_index


def test_typed_memory_same_type_dedup_blocks_near_duplicate_preferences(
    tmp_path: Path,
) -> None:
    store = SQLiteMemoryStore(tmp_path / "memory.db")
    service = MemoryService(store=store)
    adapter = MemoryServiceGatewayAdapter(
        service,
        agent_id="typed-agent",
        memory_config=_memory_config(auto_extract_enabled=True),
    )

    from openminion.modules.memory.models import MemoryCandidate as _MemoryCandidate

    service.candidate_put(
        _MemoryCandidate(
            candidate_id="cand-typed-dark-mode",
            session_id="typed-dedup",
            proposed_scope="agent:typed-agent",
            type="user_preference",
            title="dark mode terminal theme",
            content="I prefer dark mode terminal theme.",
            confidence=0.6,
        )
    )

    adapter.record_turn(
        session_id="typed-dedup",
        run_id="run-2",
        request_id="req-2",
        channel="console",
        target="chat",
        user_message="What terminal theme should I use?",
        assistant_message="You prefer dark mode terminal theme.",
    )

    adapter.record_turn(
        session_id="typed-dedup",
        run_id="run-3",
        request_id="req-3",
        channel="console",
        target="chat",
        user_message="I prefer dark mode terminal setup.",
        assistant_message="Okay, noted.",
    )

    promoted_preferences = service.list(
        ListQueryOptions(
            scopes=["agent:typed-agent"],
            types=["user_preference"],
            limit=10,
        )
    )
    proposed = service.candidate_list(
        CandidateListOptions(session_id="typed-dedup", status="proposed", limit=10)
    )

    # Dedup-intent assertion: exactly one preference (promoted or proposed).
    assert len(promoted_preferences) + len(proposed) == 1
