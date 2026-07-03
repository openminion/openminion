from __future__ import annotations

import pytest

from openminion.modules.memory.submissions import (
    SubmissionNamespace,
    emit_retrieval_feedback,
    reset_idempotency_registry,
)
from sophiagraph import SophiaGraphMemoryStore


@pytest.fixture(autouse=True)
def _reset():
    reset_idempotency_registry()
    yield
    reset_idempotency_registry()


def _ns() -> SubmissionNamespace:
    return SubmissionNamespace(agent_id="alpha", session_id="sess-1")


def test_retrieval_feedback_with_selected_and_rejected_ids() -> None:
    store = SophiaGraphMemoryStore()
    result = emit_retrieval_feedback(
        store,
        namespace=_ns(),
        turn_id="t-1",
        payload={
            "content": "feedback for turn t-1",
            "selected_record_ids": ["rec-1", "rec-3"],
            "rejected_record_ids": ["rec-2"],
            "task_outcome": "succeeded",
        },
        source_owner="agent",
        idempotency_key="idem-fb-1",
    )
    assert result.ok
    record = store.get_record(result.object_id)
    assert record is not None
    assert record.content == "feedback for turn t-1"


def test_retrieval_feedback_no_prose_invention_at_submission_surface() -> None:
    from openminion.modules.memory import submissions as mod

    forbidden = {
        "emit_relevance_reason_from_prose",
        "summarize_retrieval_outcome",
        "classify_feedback",
    }
    assert set(mod.__all__) & forbidden == set()


def test_retrieval_feedback_carries_structural_provenance() -> None:
    store = SophiaGraphMemoryStore()
    result = emit_retrieval_feedback(
        store,
        namespace=_ns(),
        turn_id="t-99",
        payload={"content": "rejected all hits", "selected_record_ids": []},
        source_owner="execution",
        idempotency_key="idem-fb-2",
    )
    assert result.ok
    record = store.get_record(result.object_id)
    assert record is not None
    assert record.namespace.agent_id == "alpha"
    assert record.namespace.session_id == "sess-1"
