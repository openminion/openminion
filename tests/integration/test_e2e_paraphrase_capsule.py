from __future__ import annotations

from tests.helpers.memory_e2e_helpers import E2EMemoryHarness


def test_e2e_paraphrase_query_surfaces_capsule_memory(tmp_path) -> None:
    harness = E2EMemoryHarness(tmp_path, agent_id="paraphrase-agent")

    harness.run_turn(
        "paraphrase-session",
        "remember: My favorite programming language is Python.",
        "Captured.",
    )

    capsule = harness.build_capsule(
        "paraphrase-session-2",
        "what language do I prefer for coding?",
    )
    assert "Python" in capsule
