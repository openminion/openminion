from __future__ import annotations

from openminion.modules.telemetry.events.catalog import (
    IMPROVEMENT_CANDIDATE_PROMOTED,
    IMPROVEMENT_CANDIDATE_ROLLED_BACK,
    IMPROVEMENT_CANDIDATE_STAGED,
    IMPROVEMENT_CANDIDATE_SUPPRESSED,
    SELF_AWARENESS_ANSWER_DEGRADED,
    SELF_MODEL_SNAPSHOT_BUILT,
    SELF_MODEL_SNAPSHOT_DEGRADED,
    register_event_type,
)
from openminion.modules.brain.runtime.improvement.candidates import ImprovementCandidate
from openminion.modules.runtime.self_model import (
    SelfModelSnapshot,
    section_degraded,
    section_ok,
)
from openminion.modules.telemetry.self_awareness import (
    build_improvement_candidate_event,
    build_self_awareness_answer_degraded_event,
    build_self_model_snapshot_event,
)


def test_runtime_self_awareness_events_are_registered_strictly() -> None:
    events = (
        SELF_MODEL_SNAPSHOT_BUILT,
        SELF_MODEL_SNAPSHOT_DEGRADED,
        SELF_AWARENESS_ANSWER_DEGRADED,
        IMPROVEMENT_CANDIDATE_STAGED,
        IMPROVEMENT_CANDIDATE_PROMOTED,
        IMPROVEMENT_CANDIDATE_ROLLED_BACK,
        IMPROVEMENT_CANDIDATE_SUPPRESSED,
    )

    for event in events:
        assert register_event_type(event, strict=True) == event


def test_self_model_snapshot_event_payload_uses_degraded_event_type() -> None:
    snapshot = SelfModelSnapshot.from_sections(
        agent_id="mini",
        identity=section_ok(display_name="Mini"),
        capabilities=section_ok(provider="echo"),
        policy=section_ok(permission_mode="ask"),
        memory_state=section_ok(provider="memory"),
        context_state=section_ok(budget_total=1024),
        knowledge_state=section_ok(providers=[]),
        improvement_state=section_degraded(
            "generic_candidate_registry_unavailable",
            policy="never",
        ),
    )

    event_type, payload = build_self_model_snapshot_event(snapshot)

    assert event_type == SELF_MODEL_SNAPSHOT_DEGRADED
    assert payload["health"] == "degraded"
    assert payload["sections"]["improvement_state"] == "degraded"


def test_self_awareness_answer_degraded_event_payload_shape() -> None:
    event_type, payload = build_self_awareness_answer_degraded_event(
        agent_id="mini",
        question_kind="identity",
        degraded_reasons=["identity_unavailable"],
    )

    assert event_type == SELF_AWARENESS_ANSWER_DEGRADED
    assert payload == {
        "agent_id": "mini",
        "question_kind": "identity",
        "degraded_reasons": ["identity_unavailable"],
    }


def test_improvement_candidate_lifecycle_event_payload_shape() -> None:
    candidate = ImprovementCandidate(
        candidate_id="cand-1",
        target_type="memory",
        target_owner="memory_service",
        summary="Improve memory",
        evidence_refs=["trace:1"],
    )

    event_type, payload = build_improvement_candidate_event(
        IMPROVEMENT_CANDIDATE_STAGED,
        candidate,
    )

    assert event_type == IMPROVEMENT_CANDIDATE_STAGED
    assert payload["candidate_id"] == "cand-1"
    assert payload["target_type"] == "memory"
    assert payload["evidence_ref_count"] == 1
