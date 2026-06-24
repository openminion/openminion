from __future__ import annotations

from tests.helpers.memory_e2e_helpers import E2EMemoryHarness


def test_e2e_reflection_writes_insight_but_does_not_auto_promote_correction(
    tmp_path,
) -> None:
    harness = E2EMemoryHarness(tmp_path, agent_id="reflection-agent")

    for index in range(3):
        harness.seed_summary(
            key=f"summary:{index}",
            summary_text="Use ruff rather than flake8 for linting.",
            corrections=["Use ruff rather than flake8 for linting."],
            keywords=["ruff", "lint"],
        )

    written = harness.trigger_reflection()
    assert written >= 1

    corrections = harness.query_records(types=["correction"], limit=10)
    assert corrections == []

    insights = harness.query_records(types=["meta_insight"], limit=10)
    assert insights, "Reflection must still write insights via typed synthesis"
    assert any(
        "recurring_correction" in {str(tag) for tag in insight.tags}
        for insight in insights
    )
