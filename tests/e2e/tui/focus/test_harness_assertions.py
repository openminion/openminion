from __future__ import annotations

import pytest

from tests.e2e.tui.focus.harness.assertions import (
    assert_expected_markers,
    assert_focus_turn_completed,
    turn_output_text,
)


def test_expected_markers_ignore_echoed_prompt() -> None:
    prompt = "Please end with next steps."
    transcript = f"❯ {prompt}\n● Working\nDone in 4s\n"

    with pytest.raises(AssertionError, match="next steps"):
        assert_expected_markers(transcript, prompt, ("next steps",))


def test_expected_markers_accept_assistant_output_only() -> None:
    prompt = "Please end with next steps."
    transcript = f"❯ {prompt}\n● Here are the next steps.\nDone in 4s\n"

    assert_expected_markers(transcript, prompt, ("next steps",))


def test_turn_completion_rejects_unresolved_approval_prompt() -> None:
    transcript = (
        "● Policy confirmation required.\n"
        "Reply exactly yes to confirm or exactly no to cancel.\n"
        "Done in 4s\n"
    )

    with pytest.raises(AssertionError, match="Policy confirmation required"):
        assert_focus_turn_completed(transcript)


def test_turn_output_uses_prompt_boundary() -> None:
    prompt = "Generate a result."
    transcript = f"banner\n❯ {prompt}\n● Final result.\nDone in 4s\n"

    assert turn_output_text(transcript, prompt).strip().startswith("● Final result.")
