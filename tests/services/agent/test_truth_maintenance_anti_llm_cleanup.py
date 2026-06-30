from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from openminion.base.config import OpenMinionConfig
from openminion.modules.memory.config import from_base_config
from openminion.modules.memory.models import MemoryCandidate, MemoryRecord
from openminion.modules.memory.service import MemoryService
from openminion.modules.memory.storage.memory import InMemoryMemoryStore
from openminion.services.agent.memory import (
    learning as agent_memory_learning,
    text_processing as agent_memory_text_processing,
    turn_recording as agent_memory_turn_recording,
)
from openminion.services.agent.memory.gateway_adapter import (
    MemoryServiceGatewayAdapter,
)
from openminion.services.agent.memory.learning import LearningMixin


_AGENT_ID = "tmac-agent"
_AGENT_SCOPE = f"agent:{_AGENT_ID}"


def _memory_config():
    cfg = from_base_config(
        base_config=OpenMinionConfig(),
        home_root=Path("/tmp/openminion-home"),
        data_root=Path("/tmp/openminion-data"),
    )
    return replace(
        cfg,
        reflection=replace(
            cfg.reflection,
            reflection_enabled=True,
            reflection_interval_sessions=3,
            contradiction_similarity_threshold=0.5,
            promotion_enabled=True,
            max_correction_promotions_per_run=2,
        ),
        candidate_learning=replace(
            cfg.candidate_learning,
            promotion_readiness_threshold=0.0,
            confidence_max=0.9,
        ),
    )


def _make_adapter():
    store = InMemoryMemoryStore()
    service = MemoryService(store=store)
    adapter = MemoryServiceGatewayAdapter(
        service,
        agent_id=_AGENT_ID,
        memory_config=_memory_config(),
    )
    return store, service, adapter


def _put_record(
    store: InMemoryMemoryStore,
    *,
    record_id: str,
    record_type: str,
    key: str,
    title: str,
    content: str,
    tags: list[str] | None = None,
    confidence: float = 0.8,
    created_at: str = "2026-04-01T00:00:00+00:00",
    updated_at: str = "2026-04-01T00:00:00+00:00",
) -> None:
    store.put(
        MemoryRecord(
            id=record_id,
            scope=_AGENT_SCOPE,
            type=record_type,
            key=key,
            title=title,
            content=content,
            tags=list(tags or []),
            entities=[],
            source="validated",
            confidence=confidence,
            created_at=created_at,
            updated_at=updated_at,
        )
    )


def _put_summary(
    store: InMemoryMemoryStore,
    *,
    record_id: str,
    updated_at: str,
    summary_text: str,
    corrections: list[str] | None = None,
    topic_keywords: list[str] | None = None,
) -> None:
    store.put(
        MemoryRecord(
            id=record_id,
            scope=_AGENT_SCOPE,
            type="session_summary",
            key=f"session_summary:{record_id}",
            title=record_id,
            content={
                "decisions": [],
                "open_questions": [],
                "corrections": list(corrections or []),
                "topic_keywords": list(topic_keywords or []),
                "turn_count": 4,
                "summary_text": summary_text,
            },
            tags=["session_summary"],
            entities=[],
            source="validated",
            confidence=0.8,
            created_at=updated_at,
            updated_at=updated_at,
        )
    )


_REMOVED_LEARNING_MEMBERS = (
    "_detect_contradiction",
    "_context_relation",
    "_records_have_distinct_context",
    "_contradiction_threshold_for_type",
    "_should_compare_contradiction_types",
    "_record_candidate_turn_signals",
    "_promote_correction_insights",
)

_REMOVED_TEXT_PROCESSING_MEMBERS = (
    "_NEGATION_HINTS",
    "_contains_negation_pattern",
)

_FORBIDDEN_CALL_PATTERNS_LEARNING = (
    "self._detect_contradiction(",
    "._detect_contradiction(",
    "self._record_candidate_turn_signals(",
    "self._promote_correction_insights(",
    "self._context_relation(",
    "self._records_have_distinct_context(",
    "self._contradiction_threshold_for_type(",
    "self._should_compare_contradiction_types(",
)

_FORBIDDEN_CALL_PATTERNS_TURN_RECORDING = (
    "self._detect_contradiction(",
    "self._record_candidate_turn_signals(",
    "self._should_compare_contradiction_types(",
    'reason="direct_write"',
    "reason='direct_write'",
)

_FORBIDDEN_SUPERSESSION_REASONS = (
    "direct_write",
    "candidate_promotion",
    "reflection_promotion",
    "reflection_contradiction",
)


def _recording_supersede_calls(service: MemoryService) -> list[tuple[str, str, str]]:

    calls: list[tuple[str, str, str]] = []
    original = service.supersede_by_contradiction

    def _recorder(old_id, new_id, reason="candidate_contradiction", *args, **kwargs):
        calls.append((str(old_id), str(new_id), str(reason)))
        return original(old_id, new_id, reason, *args, **kwargs)

    service.supersede_by_contradiction = _recorder  # type: ignore[assignment]
    return calls


def test_tmac03_classifier_and_helpers_are_removed_from_learning_mixin() -> None:

    for name in _REMOVED_LEARNING_MEMBERS:
        assert not hasattr(LearningMixin, name), (
            f"TMAC-03: `LearningMixin.{name}` was removed. Any future "
            "contradiction surface must be typed / LLM-authored through "
            "a new dedicated lane, not by reviving this symbol."
        )
        assert not hasattr(agent_memory_learning, name), (
            f"TMAC-03: `openminion.services.agent.memory.learning.{name}` "
            "must stay removed at module level."
        )


def test_tmac03_negation_list_and_detector_are_removed_from_text_processing() -> None:

    for name in _REMOVED_TEXT_PROCESSING_MEMBERS:
        assert not hasattr(agent_memory_text_processing, name), (
            f"TMAC-03: `services.agent.memory.text_processing.{name}` "
            "must stay removed. No replacement runtime prose classifier "
            "may be introduced here."
        )
        assert name not in set(getattr(agent_memory_text_processing, "__all__", [])), (
            f"TMAC-03: `{name}` must not appear in `text_processing.__all__`."
        )


def test_tmac03_learning_source_has_no_forbidden_call_patterns() -> None:

    source_path = Path(agent_memory_learning.__file__)
    source = source_path.read_text(encoding="utf-8")
    for lineno, line in enumerate(source.splitlines(), start=1):
        if line.lstrip().startswith("#"):
            continue
        for pattern in _FORBIDDEN_CALL_PATTERNS_LEARNING:
            if pattern in line:
                pytest.fail(
                    f"TMAC-03: learning.py:{lineno} reintroduced the "
                    f"forbidden call pattern {pattern!r}."
                )
        for reason in _FORBIDDEN_SUPERSESSION_REASONS:
            if (
                "supersede_by_contradiction" in line and f'reason="{reason}"' in line
            ) or (
                "supersede_by_contradiction" in line and f"reason='{reason}'" in line
            ):
                pytest.fail(
                    f"TMAC-03: learning.py:{lineno} reintroduced a "
                    f"prose-driven `supersede_by_contradiction(...,"
                    f" reason={reason!r})` call site."
                )


def test_tmac03_turn_recording_source_has_no_forbidden_call_patterns() -> None:

    source_path = Path(agent_memory_turn_recording.__file__)
    source = source_path.read_text(encoding="utf-8")
    for lineno, line in enumerate(source.splitlines(), start=1):
        if line.lstrip().startswith("#"):
            continue
        for pattern in _FORBIDDEN_CALL_PATTERNS_TURN_RECORDING:
            if pattern in line:
                pytest.fail(
                    f"TMAC-03: turn_recording.py:{lineno} reintroduced "
                    f"the forbidden call pattern {pattern!r}."
                )
        if "self._service.list(" in line and "ListQueryOptions" in line:
            pytest.fail(
                f"TMAC-03: turn_recording.py:{lineno} reintroduced a "
                "`self._service.list(ListQueryOptions(...))` prefetch "
                "— this previously fed the direct-write contradiction "
                "block and must not return without a typed consumer."
            )


def test_tmac03_token_overlap_helper_is_removed() -> None:
    learning_source = Path(agent_memory_learning.__file__).read_text(encoding="utf-8")
    text_processing_source = Path(agent_memory_text_processing.__file__).read_text(
        encoding="utf-8"
    )

    assert "_token_overlap_ratio" not in learning_source
    assert "_token_overlap_ratio" not in text_processing_source


def test_tmac03_direct_write_never_produces_supersession_from_prose_negation() -> None:

    store, service, adapter = _make_adapter()
    _put_record(
        store,
        record_id="orig-dark",
        record_type="user_preference",
        key="pref:theme:dark",
        title="Dark mode",
        content="I prefer dark mode terminal theme.",
    )

    supersede_calls = _recording_supersede_calls(service)
    trace_events: list[tuple[str, dict]] = []
    adapter._trace = lambda event, payload: trace_events.append(  # type: ignore[method-assign]  # noqa: SLF001
        (str(event), dict(payload or {}))
    )

    ok = adapter._write_record_safe(  # noqa: SLF001
        scope=_AGENT_SCOPE,
        record_type="user_preference",
        title="Light mode",
        content="I do not prefer dark mode terminal theme.",
        tags=["theme"],
        entities=["dark", "mode", "terminal"],
        trace_event="memory.record.written",
        trace_payload={"scope": _AGENT_SCOPE, "type": "user_preference"},
    )
    assert ok is True
    assert store.get("orig-dark").supersession_reason is None
    assert supersede_calls == []
    assert not any(
        event == "memory.semantic_supersession" for event, _payload in trace_events
    )


def test_tmac03_candidate_promotion_never_produces_supersession_from_prose() -> None:

    store, service, adapter = _make_adapter()
    _put_record(
        store,
        record_id="orig-editor",
        record_type="user_preference",
        key="pref:editor:vim",
        title="Prefer vim",
        content="I prefer vim for code editing.",
    )
    service.candidate_put(
        MemoryCandidate(
            candidate_id="cand-editor",
            session_id="tmac-session",
            proposed_scope=_AGENT_SCOPE,
            type="user_preference",
            title="Prefer emacs",
            content="I do not prefer vim for code editing.",
            confidence=0.9,
            claim_key="preference:editor",
            tags=["editor"],
            entities=["vim", "code", "editing"],
            source_class="user_input",
        )
    )
    supersede_calls = _recording_supersede_calls(service)

    promoted = adapter._promote_mature_candidates(  # noqa: SLF001
        session_id="tmac-session",
        user_message="",
        assistant_message="",
    )
    assert promoted >= 1
    assert not any(
        reason == "candidate_promotion" for _old, _new, reason in supersede_calls
    )
    original = store.get("orig-editor")
    assert original.supersession_reason is None


def test_tmac03_record_candidate_turn_signals_is_removed_not_silently_no_op() -> None:

    _store, _service, adapter = _make_adapter()
    assert not hasattr(adapter, "_record_candidate_turn_signals")
    with pytest.raises(AttributeError):
        adapter._record_candidate_turn_signals(  # type: ignore[attr-defined]  # noqa: SLF001
            user_message="Actually, I do not prefer pytest for testing."
        )


def test_tmac03_promote_correction_insights_is_removed() -> None:

    _store, _service, adapter = _make_adapter()
    assert not hasattr(adapter, "_promote_correction_insights")


def test_tmac03_reflection_run_never_emits_reflection_contradiction_supersession() -> (
    None
):

    store, service, adapter = _make_adapter()
    now = datetime(2026, 4, 1, tzinfo=timezone.utc)
    for index in range(3):
        _put_summary(
            store,
            record_id=f"summary-{index}",
            updated_at=(now - timedelta(days=index)).isoformat(),
            summary_text="We always use pytest for testing.",
            corrections=["We do not always use pytest for testing."],
            topic_keywords=["pytest", "testing"],
        )
    store.put(
        MemoryRecord(
            id="existing-meta-insight",
            scope=_AGENT_SCOPE,
            type="meta_insight",
            key="insight:pytest-always",
            title="Always use pytest",
            content="We always use pytest for testing.",
            tags=["meta_insight"],
            entities=["pytest", "testing"],
            source="agent_inferred",
            confidence=0.7,
            created_at="2026-04-01T00:00:00+00:00",
            updated_at="2026-04-01T00:00:00+00:00",
        )
    )
    supersede_calls = _recording_supersede_calls(service)

    adapter._maybe_run_reflection()  # noqa: SLF001

    assert not any(
        reason == "reflection_contradiction" for _old, _new, reason in supersede_calls
    ), (
        "TMAC-03: reflection insight-write path must not call "
        "`supersede_by_contradiction(reason='reflection_contradiction')`."
    )
    assert not any(
        reason == "reflection_promotion" for _old, _new, reason in supersede_calls
    ), (
        "TMAC-03: reflection correction-promotion path was deleted in "
        "full; its supersession call must not fire."
    )
