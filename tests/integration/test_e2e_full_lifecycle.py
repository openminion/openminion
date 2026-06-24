from __future__ import annotations

from tests.helpers.memory_e2e_helpers import E2EMemoryHarness


def test_e2e_full_lifecycle_smoke(tmp_path) -> None:
    harness = E2EMemoryHarness(tmp_path, agent_id="full-agent")

    # directly (prose→candidate auto-extract moved to brain AFE; see
    harness.seed_candidate(
        session_id="pref-session",
        content="I prefer dark mode.",
        title="dark mode preference",
    )
    assert (
        len(harness.query_candidates(session_id="pref-session", status="proposed")) == 1
    )

    harness.run_turn("pref-session", "Thanks.", "Happy to help.")

    preferences = harness.query_records(types=["user_preference"], limit=10)
    assert any("dark mode" in str(record.content).lower() for record in preferences)

    harness.run_turn(
        "lint-session-1", "remember: Use tabs for indentation.", "Captured."
    )
    harness.run_turn(
        "lint-session-2",
        "remember: Actually use spaces for indentation.",
        "Updated.",
    )

    harness.seed_summary(
        key="summary:1",
        summary_text="Use spaces rather than tabs. I prefer dark mode.",
        corrections=["Use spaces rather than tabs."],
        keywords=["indentation", "theme"],
    )
    harness.seed_summary(
        key="summary:2",
        summary_text="Use spaces rather than tabs for this project.",
        corrections=["Use spaces rather than tabs."],
        keywords=["indentation"],
    )
    harness.seed_summary(
        key="summary:3",
        summary_text="Use spaces rather than tabs in active work.",
        corrections=["Use spaces rather than tabs."],
        keywords=["indentation"],
    )
    written = harness.trigger_reflection()
    assert written >= 1

    insights = harness.query_records(types=["meta_insight"], limit=20)
    assert any(
        "recurring_correction" in {str(tag) for tag in record.tags}
        for record in insights
    )

    harness.service.upsert_record(
        scope="agent:full-agent",
        record_type="fact",
        key="stale:cleanup",
        record_patch={
            "title": "stale cleanup",
            "content": "This stale fact should fall out.",
            "confidence": 0.2,
        },
    )
    harness.advance_time(45)
    harness.trigger_lifecycle("lifecycle-pass")

    capsule = harness.build_capsule(
        "final-session",
        "what theme and indentation style should I use?",
    ).lower()
    # Explicit `remember:` writes (spaces / tabs) go to durable fact
    # records and surface in the capsule; CREF-02 is about reflection
    # promotion, not explicit-remember writes.
    assert "spaces" in capsule
    assert "stale fact should fall out" not in capsule
