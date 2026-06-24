from __future__ import annotations

from tests.helpers.memory_e2e_helpers import E2EMemoryHarness


def test_e2e_candidate_promotion_then_contradiction(tmp_path) -> None:
    harness = E2EMemoryHarness(tmp_path, agent_id="candidate-agent")

    harness.seed_candidate(
        session_id="candidate-session",
        content="Preference memory: tabs for indentation",
        title="Preference: tabs for indentation",
        key="pref:tabs-for-indentation",
    )
    # Nudge the promotion pipeline by running one turn (triggers
    # `_promote_mature_candidates`).
    harness.run_turn("candidate-session", "Thanks.", "Happy to help.")

    preferences = harness.query_records(types=["user_preference"], limit=10)
    promoted = next(
        record for record in preferences if "tabs" in str(record.content).lower()
    )

    conflicting = harness.service.upsert_record(
        scope="agent:candidate-agent",
        record_type="user_preference",
        key="pref:spaces-for-indentation",
        record_patch={
            "title": "Preference: spaces for indentation",
            "content": "Preference memory: spaces for indentation",
            "confidence": 0.8,
        },
    )
    harness.service.supersede_by_contradiction(
        promoted.id,
        conflicting.id,
        reason="candidate_followup",
    )

    history = harness.store.history(
        "agent:candidate-agent",
        "user_preference",
        "pref:tabs-for-indentation",
    )
    assert any(record.is_deleted for record in history)
    assert any(
        str(record.supersession_reason or "").strip() == "candidate_followup"
        for record in history
    )

    capsule = harness.build_capsule(
        "candidate-session-3",
        "what do I prefer for indentation?",
    ).lower()
    assert "spaces" in capsule
