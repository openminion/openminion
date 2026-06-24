from __future__ import annotations

from tests.helpers.memory_e2e_helpers import E2EMemoryHarness


def test_e2e_preference_stability_boosts_existing_record(tmp_path) -> None:
    harness = E2EMemoryHarness(tmp_path, agent_id="stability-agent")

    harness.service.upsert_record(
        scope="agent:stability-agent",
        record_type="user_preference",
        key="pref:dark-mode",
        record_patch={
            "title": "Dark mode",
            "content": "I prefer dark mode.",
            "confidence": 0.4,
        },
    )

    for index in range(6):
        harness.seed_summary(
            key=f"summary:{index}",
            summary_text="I prefer dark mode.",
            keywords=["theme", "dark-mode"],
            preference_examples=[
                {
                    "topic": "dark-mode",
                    "key": "pref:dark-mode",
                    "content": "I prefer dark mode.",
                    "title": "Dark mode",
                }
            ],
        )

    written = harness.trigger_reflection()
    assert written >= 1

    boosted = harness.store.history(
        "agent:stability-agent",
        "user_preference",
        "pref:dark-mode",
    )[0]
    assert boosted.confidence > 0.4

    insights = harness.query_records(types=["meta_insight"], limit=20)
    stable_preference = next(
        record
        for record in insights
        if "stable_preference" in {str(tag) for tag in record.tags}
    )
    assert "boosted_at" in stable_preference.meta

    capsule = harness.build_capsule(
        "stability-session", "what theme should I use?"
    ).lower()
    assert "dark mode" in capsule
