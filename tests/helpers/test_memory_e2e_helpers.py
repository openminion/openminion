from __future__ import annotations

from pathlib import Path

from tests.helpers.memory_e2e_helpers import E2EMemoryHarness


def test_e2e_memory_harness_runs_turn_and_builds_capsule(tmp_path: Path) -> None:
    harness = E2EMemoryHarness(tmp_path, agent_id="harness-agent")
    result = harness.run_turn(
        "helper-session",
        "remember: I prefer compact diffs.",
        "Noted.",
    )
    capsule = harness.build_capsule("helper-session", "what do I prefer?")

    assert result.generation >= 1
    assert isinstance(capsule, str)
