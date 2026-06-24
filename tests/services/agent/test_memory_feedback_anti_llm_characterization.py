from __future__ import annotations

import importlib
from dataclasses import replace
from pathlib import Path

import pytest

from openminion.base.config import OpenMinionConfig
from openminion.modules.memory.config import from_base_config
from openminion.modules.memory.models import MemoryCandidate
from openminion.modules.memory.service import MemoryService
from openminion.modules.memory.storage.memory import InMemoryMemoryStore
from openminion.services.agent.memory import (
    config_values as agent_memory_config,
    learning as agent_memory_learning,
    text_processing as agent_memory_text_processing,
    turn_recording as agent_memory_turn_recording,
)
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


def _memory_config():
    cfg = from_base_config(
        base_config=OpenMinionConfig(),
        home_root=Path("/tmp/openminion-home"),
        data_root=Path("/tmp/openminion-data"),
    )
    return replace(
        cfg,
        candidate_learning=replace(
            cfg.candidate_learning,
            auto_extract_enabled=True,
            auto_extract_notify=True,
        ),
        retrieval=replace(
            cfg.retrieval,
            feedback_boost_on_reference=0.1,
            feedback_demote_on_correction=0.3,
        ),
    )


def _make_adapter():
    store = InMemoryMemoryStore()
    service = MemoryService(store=store)
    retrieve_ctl = FakeRetrieveCtl()
    adapter = MemoryServiceGatewayAdapter(
        service,
        agent_id="mfac-agent",
        memory_config=_memory_config(),
        retrieve_ctl=retrieve_ctl,
    )
    return service, retrieve_ctl, adapter


def test_mfac02_lexical_retrieval_signal_classifier_is_removed() -> None:

    # 1. The two anti-LLM functions are no longer attributes of their owning
    #    modules/classes.
    assert not hasattr(agent_memory_learning, "_detect_retrieval_signals"), (
        "MFAC-02: `_detect_retrieval_signals` was removed as an anti-LLM "
        "runtime owner of user+assistant prose semantics. It must not come "
        "back in any form — create a typed feedback lane instead."
    )
    from openminion.services.agent.memory.learning import LearningMixin

    assert not hasattr(LearningMixin, "_apply_feedback_signals"), (
        "MFAC-02: `_apply_feedback_signals` was removed with its sole "
        "producer `_detect_retrieval_signals`. No runtime path may "
        "reintroduce lexical `referenced`/`corrected` label consumption."
    )

    # 2. The threshold constant that parameterised the lexical classifier
    #    is removed from the agent-memory config owner.
    assert not hasattr(agent_memory_config, "RETRIEVAL_REFERENCE_THRESHOLD"), (
        "MFAC-02: `RETRIEVAL_REFERENCE_THRESHOLD` only powered the lexical "
        "classifier; it must stay removed so a reintroduction cannot silently "
        "re-import a live threshold."
    )

    # 3. The production call-site module no longer imports the classifier.
    #    `importlib.reload` is used so the assertion reflects the *current*
    #    module source, not a stale import-time binding.
    turn_recording = importlib.reload(agent_memory_turn_recording)
    assert "_detect_retrieval_signals" not in vars(turn_recording), (
        "MFAC-02: `turn_recording.py` must not re-import the lexical "
        "retrieval classifier."
    )

    turn_recording_source = Path(agent_memory_turn_recording.__file__).read_text(
        encoding="utf-8"
    )
    forbidden_call_patterns = [
        "_detect_retrieval_signals(",
        "._apply_feedback_signals(",
        "self._apply_feedback_signals",
        "from openminion.services.agent.memory.learning import _detect_retrieval_signals",
        "from openminion.services.agent.memory.learning import _apply_feedback_signals",
    ]
    for pattern in forbidden_call_patterns:
        assert pattern not in turn_recording_source, (
            "MFAC-02: `turn_recording.py` source must not reintroduce the "
            f"lexical retrieval-feedback call pattern {pattern!r}."
        )


def test_mfac02_record_turn_does_not_write_feedback_scores_from_prose_overlap() -> None:

    _service, retrieve_ctl, adapter = _make_adapter()
    adapter._last_retrieved_items["mfac-session"] = [  # noqa: SLF001
        {
            "text": "Team convention: use pytest fixtures for setup.",
            "meta": {"unit_id": "mem-pytest"},
        },
        {
            "text": "User preference: concise summaries.",
            "meta": {"unit_id": "mem-concise"},
        },
    ]

    adapter.record_turn(
        session_id="mfac-session",
        run_id="run-1",
        request_id="req-1",
        channel="console",
        target="chat",
        user_message="pytest fixtures",
        assistant_message="pytest fixtures are the plan",
    )

    # No lexical-overlap-driven write may reach feedback scoring.
    assert retrieve_ctl.scores == {}
    # Session cache is still cleaned so the cleanup invariant survives.
    assert "mfac-session" not in adapter._last_retrieved_items  # noqa: SLF001


def test_mfac03_candidate_turn_signals_method_is_fully_removed() -> None:

    service, _retrieve_ctl, adapter = _make_adapter()
    service.candidate_put(
        MemoryCandidate(
            candidate_id="cand-turn",
            session_id="mfac-session",
            proposed_scope="agent:mfac-agent",
            type="project_convention",
            title="Convention: pytest",
            content="We use pytest.",
            confidence=0.4,
        )
    )

    # Method removal is asserted at adapter attribute level.
    assert not hasattr(adapter, "_record_candidate_turn_signals")
    with pytest.raises(AttributeError):
        adapter._record_candidate_turn_signals(  # type: ignore[attr-defined]  # noqa: SLF001
            user_message="We really do use pytest.",
        )

    # Sanity check: the candidate is untouched; no prose-driven mutation
    # path exists anymore.
    candidate = service.candidate_get("cand-turn")
    assert candidate is not None
    assert "reconfirmation_count" not in candidate.meta
    assert "contradicted" not in candidate.meta
    assert candidate.confidence == 0.4


def test_mfac03_record_candidate_retrieval_hits_is_removed() -> None:

    from openminion.services.agent.memory import context as agent_memory_context
    from openminion.services.agent.memory.learning import LearningMixin

    # 1. Structural absence on the mixin.
    assert not hasattr(LearningMixin, "_record_candidate_retrieval_hits"), (
        "MFAC-03: `_record_candidate_retrieval_hits` was removed as a "
        "runtime owner of free-form query-prose semantics. It must not be "
        "reintroduced in any form — a typed retrieval-hit seam is required "
        "via a new lane."
    )

    # 2. The production trigger module must not call the removed mutator.
    #    Tombstone comments are fine; live call sites are not.
    context_source = Path(agent_memory_context.__file__).read_text(encoding="utf-8")
    assert "self._record_candidate_retrieval_hits(" not in context_source, (
        "MFAC-03: `context.py` must not reintroduce the "
        "query-prose retrieval-hit mutator call site."
    )

    # 3. Observable invariant: exercising the context-build path with a
    #    deliberately overlapping query must leave `retrieval_hit_count`
    #    untouched on every proposed candidate.
    service, _retrieve_ctl, adapter = _make_adapter()
    service.candidate_put(
        MemoryCandidate(
            candidate_id="cand-query",
            session_id="mfac-session",
            proposed_scope="agent:mfac-agent",
            type="user_preference",
            title="Preference: concise summaries",
            content="I prefer concise summaries.",
            confidence=0.4,
        )
    )

    adapter.build_context_with_metadata(
        session_id="mfac-session",
        user_message="concise summaries",
    )

    candidate = service.candidate_get("cand-query")
    assert candidate is not None
    assert "retrieval_hit_count" not in candidate.meta


def test_mfac04_token_overlap_helper_is_removed() -> None:
    learning_source = Path(agent_memory_learning.__file__).read_text(encoding="utf-8")
    text_processing_source = Path(agent_memory_text_processing.__file__).read_text(
        encoding="utf-8"
    )

    assert "_token_overlap_ratio" not in learning_source
    assert "_token_overlap_ratio" not in text_processing_source
