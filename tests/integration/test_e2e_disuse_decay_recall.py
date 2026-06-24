from __future__ import annotations

from tests.helpers.memory_e2e_helpers import E2EMemoryHarness


def test_e2e_cross_session_recall_after_disuse_decay(tmp_path) -> None:
    harness = E2EMemoryHarness(tmp_path, agent_id="decay-agent")

    record = harness.service.upsert_record(
        scope="agent:decay-agent",
        record_type="fact",
        key="pref:language",
        record_patch={
            "title": "Preferred language",
            "content": "My favorite programming language is Python.",
            "confidence": 0.8,
        },
    )

    harness.advance_time(45, record_ids=[record.id])
    harness.trigger_lifecycle("decay-session")

    decayed = harness.service.get(record.id)
    assert decayed.confidence < 0.8

    capsule = harness.build_capsule(
        "decay-session-2",
        "what language do I prefer for coding?",
    )
    assert "Preferred language" in capsule

    refreshed = harness.service.get(record.id)
    assert refreshed.last_hit_at is not None
