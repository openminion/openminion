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
from openminion.modules.memory.storage.memory import InMemoryMemoryStore
from openminion.services.agent.memory.gateway_adapter import MemoryServiceGatewayAdapter


class FakeRetrieveCtl:
    def __init__(self) -> None:
        self.scores: dict[str, float] = {}

    def feedback_state(self, unit_ids: list[str]) -> dict[str, dict[str, float]]:
        return {
            unit_id: {"feedback_score": self.scores.get(unit_id, 0.0)}
            for unit_id in unit_ids
        }

    def set_feedback_scores(self, scores_by_unit: dict[str, float]) -> int:
        self.scores.update(scores_by_unit)
        return len(scores_by_unit)

    def record_hits(
        self, unit_ids: list[str], *, observed_at: str | None = None
    ) -> int:
        return len(unit_ids)


def _memory_config(
    *, auto_extract_enabled: bool = True, auto_extract_notify: bool = True
):
    cfg = from_base_config(
        base_config=OpenMinionConfig(),
        home_root=Path("/tmp/openminion-home"),
        data_root=Path("/tmp/openminion-data"),
    )
    return replace(
        cfg,
        candidate_learning=replace(
            cfg.candidate_learning,
            auto_extract_enabled=auto_extract_enabled,
            auto_extract_notify=auto_extract_notify,
        ),
        retrieval=replace(
            cfg.retrieval,
            feedback_boost_on_reference=0.1,
            feedback_demote_on_correction=0.3,
        ),
    )


def _make_adapter(
    *, auto_extract_enabled: bool = True, auto_extract_notify: bool = True
):
    store = InMemoryMemoryStore()
    service = MemoryService(store=store)
    retrieve_ctl = FakeRetrieveCtl()
    adapter = MemoryServiceGatewayAdapter(
        service,
        agent_id="phase4-agent",
        memory_config=_memory_config(
            auto_extract_enabled=auto_extract_enabled,
            auto_extract_notify=auto_extract_notify,
        ),
        retrieve_ctl=retrieve_ctl,
    )
    return store, service, retrieve_ctl, adapter


def test_phase4_config_defaults_include_feedback_and_auto_extract() -> None:
    cfg = _memory_config(auto_extract_enabled=False)
    assert cfg.candidate_learning is not None
    assert cfg.candidate_learning.auto_extract_enabled is False
    assert cfg.candidate_learning.survival_halflife_days == 7.0
    assert cfg.candidate_learning.auto_extract_notify is True
    assert cfg.retrieval.feedback_boost_on_reference == 0.1
    assert cfg.retrieval.feedback_demote_on_correction == 0.3
    assert cfg.ranking.type_boost_correction == 1.5
    assert cfg.ranking.type_boost_user_preference == 1.3
    assert cfg.ranking.type_boost_pin == 1.2
    assert cfg.ranking.type_boost_project_convention == 1.1


def test_record_turn_auto_extracts_then_promotes_on_later_usage() -> None:
    _store, service, _retrieve_ctl, adapter = _make_adapter()

    first = adapter.record_turn(
        session_id="s-learning",
        run_id="r1",
        request_id="req1",
        channel="console",
        target="chat",
        user_message="I prefer dark mode.",
        assistant_message="Okay, noted.",
    )
    assert first.facts_auto_extracted == 0
    proposed = service.candidate_list(
        CandidateListOptions(session_id="s-learning", status="proposed", limit=10)
    )
    assert proposed == []

    second = adapter.record_turn(
        session_id="s-learning",
        run_id="r2",
        request_id="req2",
        channel="console",
        target="chat",
        user_message="What theme should I use?",
        assistant_message="You prefer dark mode.",
    )
    assert second.facts_auto_extracted == 0

    for _ in range(3):
        adapter.build_context_with_metadata(
            session_id="s-learning",
            user_message="dark mode preference",
        )

    third = adapter.record_turn(
        session_id="s-learning",
        run_id="r3",
        request_id="req3",
        channel="console",
        target="chat",
        user_message="Thanks.",
        assistant_message="Happy to help.",
    )
    assert third.facts_auto_extracted == 0
    facts = service.list(
        ListQueryOptions(
            scopes=["agent:phase4-agent"],
            types=["user_preference"],
            limit=20,
        )
    )
    assert facts == []


def test_record_turn_skips_auto_extract_when_disabled() -> None:
    _store, service, _retrieve_ctl, adapter = _make_adapter(auto_extract_enabled=False)

    result = adapter.record_turn(
        session_id="s-disabled",
        run_id="r1",
        request_id="req1",
        channel="console",
        target="chat",
        user_message="I prefer dark mode.",
        assistant_message="Okay.",
    )

    assert result.facts_auto_extracted == 0
    assert (
        service.candidate_list(
            CandidateListOptions(session_id="s-disabled", status="proposed", limit=10)
        )
        == []
    )


def test_record_turn_hides_auto_extract_count_when_notify_disabled() -> None:
    _store, service, _retrieve_ctl, adapter = _make_adapter(auto_extract_notify=False)

    result = adapter.record_turn(
        session_id="s-silent",
        run_id="r1",
        request_id="req1",
        channel="console",
        target="chat",
        user_message="I prefer dark mode.",
        assistant_message="Okay.",
    )

    assert result.facts_auto_extracted == 0
    assert (
        service.candidate_list(
            CandidateListOptions(session_id="s-silent", status="proposed", limit=10)
        )
        == []
    )
