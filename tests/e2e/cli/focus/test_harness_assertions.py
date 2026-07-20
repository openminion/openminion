from __future__ import annotations

from pathlib import Path
import sys

import pytest
from pyte.screens import Char

from tests.e2e.cli.focus.harness.assertions import (
    assert_expected_markers,
    assert_focus_turn_completed,
    turn_output_text,
)
from tests.e2e.cli.focus.harness.probe import (
    FocusProbe,
    active_approval_visible,
    active_turn_busy,
    approval_prompt_needs_reply,
    composer_echo_probe,
    inline_approval_fingerprint,
    inline_approval_key,
    inline_approval_menu,
    latest_approval_prompt,
    latest_done_event,
    latest_turn_event,
    screen_after_submission,
)
from tests.e2e.cli.focus.harness.pty import PtySession


def test_expected_markers_ignore_echoed_prompt() -> None:
    prompt = "Please end with next steps."
    transcript = f"❯ {prompt}\n● Working\nDone in 4s\n"

    with pytest.raises(AssertionError, match="next steps"):
        assert_expected_markers(transcript, prompt, ("next steps",))


def test_composer_echo_probe_uses_visible_tail_for_long_input() -> None:
    prompt = "beginning that scrolls out of view " + ("x" * 80) + " visible tail"

    probe = composer_echo_probe(prompt)

    assert probe == prompt[-48:]
    assert "beginning" not in probe


def test_screen_after_submission_allows_wrapped_trailing_punctuation() -> None:
    screen = "> finish with the exact label result\nAnalyzing request...\n"

    assert screen_after_submission(screen, "finish with the exact label result.") == (
        "\nAnalyzing request...\n"
    )


def test_screen_after_submission_excludes_stale_completion() -> None:
    screen = "Done in 22s\n> session\nAnalyzing request...\n"

    assert screen_after_submission(screen, "session") == "\nAnalyzing request...\n"


def test_screen_after_submission_includes_new_completion() -> None:
    screen = "Done in 22s\n> session\nApproved.\nDone in 4s\n"

    assert screen_after_submission(screen, "session") == ("\nApproved.\nDone in 4s\n")


def test_screen_after_submission_accepts_terminal_wrapping() -> None:
    screen = (
        "Done in 22s\n"
        "> finish with the exact label `result:` plus the bug and\n"
        "  fix.\n"
        "Analyzing request...\n"
    )

    assert (
        screen_after_submission(
            screen,
            "exact label `result:` plus the bug and fix.",
        )
        == "\nAnalyzing request...\n"
    )


def test_screen_after_submission_accepts_mid_word_terminal_wrapping() -> None:
    screen = (
        "> finish with files changed and validation resu\n"
        "  lt, and remaining follow-ups.\n"
        "Analyzing request...\n"
    )

    assert (
        screen_after_submission(
            screen,
            "files changed and validation result, and remaining follow-ups.",
        )
        == "\nAnalyzing request...\n"
    )


def test_screen_after_submission_requires_rendered_input() -> None:
    assert screen_after_submission("Done in 22s\n", "session") is None


def test_expected_markers_accept_assistant_output_only() -> None:
    prompt = "Please end with next steps."
    transcript = f"❯ {prompt}\n● Here are the next steps.\nDone in 4s\n"

    assert_expected_markers(transcript, prompt, ("next steps",))


def test_expected_markers_accept_bounded_alternatives() -> None:
    prompt = "Please recommend one path."
    transcript = f"❯ {prompt}\n● Recommended direction: keep it small.\nDone in 4s\n"

    assert_expected_markers(transcript, prompt, ("recommendation|recommended",))


def test_turn_completion_rejects_unresolved_approval_prompt() -> None:
    transcript = (
        "Done in 4s\n"
        "● Policy confirmation required.\n"
        "Reply exactly yes to confirm or exactly no to cancel.\n"
    )

    with pytest.raises(AssertionError, match="Policy confirmation required"):
        assert_focus_turn_completed(transcript)


def test_turn_completion_allows_resolved_approval_prompt_history() -> None:
    transcript = (
        "● Policy confirmation required.\n"
        "Reply exactly yes to confirm or exactly no to cancel.\n"
        "● Approved.\n"
        "Done in 4s\n"
    )

    assert_focus_turn_completed(transcript)


def test_latest_turn_event_prefers_completion_over_stale_approval() -> None:
    transcript = (
        "● Policy confirmation required.\n"
        "Reply exactly yes to confirm or exactly no to cancel.\n"
        "● Approved.\n"
        "Done in 4s\n"
    )

    match = latest_turn_event(transcript, offset=0)

    assert match is not None
    assert match.group(0) == "Done in 4s"


def test_latest_done_event_finds_completion_before_stale_waiting_status() -> None:
    transcript = (
        "Policy confirmation required.\n"
        "Reply exactly yes to allow once, session to allow this tool for the session, "
        "or no to cancel.\n"
        "✓ Wrote file.write · <1s\n"
        "Done in 20s\n"
        "• 20s | Waiting for your reply...\n"
        "❯ Ask anything · @ to mention a file · / for commands\n"
    )

    match = latest_done_event(transcript, offset=0)

    assert match is not None
    assert match.group(0) == "Done in 20s"


def test_latest_done_event_excludes_completion_before_new_activity() -> None:
    transcript = (
        "Done in 20s\n"
        "● Policy confirmation required.\n"
        "Reply exactly yes to allow once, session to allow this tool for the "
        "session, or no to cancel.\n"
    )
    offset = transcript.index("● Policy confirmation")

    assert latest_done_event(transcript, offset=offset) is None


def test_latest_approval_prompt_wins_when_completion_text_follows() -> None:
    transcript = (
        "● Policy confirmation required.\n"
        "file.write (path=mini.py)\n"
        "Reply exactly yes to allow once, session to allow this tool for the "
        "session, or no to cancel.\n"
        "✓ Wrote file.write · <1s\n"
        "Done in 21s\n"
    )

    match = latest_approval_prompt(transcript, offset=0)

    assert match is not None
    assert "allow" in match.group(0) or "Policy confirmation" in match.group(0)


def test_approval_prompt_still_needs_reply_when_turn_completion_follows() -> None:
    transcript = (
        "● Policy confirmation required.\n"
        "file.write (path=mini.py)\n"
        "Reply exactly yes to allow once, session to allow this tool for the "
        "session, or no to cancel.\n"
        "Done in 21s\n"
    )

    assert approval_prompt_needs_reply(transcript, offset=0)


def test_approval_prompt_needs_reply_when_current_screen_is_waiting() -> None:
    transcript = (
        "● Policy confirmation required.\n"
        "file.write (path=mini.py)\n"
        "Reply exactly yes to allow once, session to allow this tool for the "
        "session, or no to cancel.\n"
    )

    assert approval_prompt_needs_reply(transcript, offset=0)


def test_tool_activity_does_not_resolve_an_approval_prompt() -> None:
    transcript = (
        "Policy confirmation required.\n"
        "file.write (path=tiny.py)\n"
        "Reply exactly yes to allow once, session to allow this tool for the "
        "session, or no to cancel.\n"
        "✓ Wrote file.write\n"
        "Waiting for your reply...\n"
    )

    assert approval_prompt_needs_reply(transcript, offset=0)


def test_approval_prompt_still_needs_reply_after_unrelated_redraws() -> None:
    transcript = (
        "\x1b[13;2H● Policy confirmation required.\n"
        "file.write (path=mini.py)\n"
        "Reply exactly yes to allow once, session to allow this tool for the "
        "session, or no to cancel.\n"
        "\x1b[18;4H✓ Wrote file.write · <1s\n"
        "\x1b[21;4HDone in 21s\n"
        "\x1b[48;4H❯ Ask anything · @ to mention a file · / for commands\n"
    )

    assert approval_prompt_needs_reply(transcript, offset=0)


def test_approval_prompt_is_not_resolved_by_tool_completion() -> None:
    transcript = (
        "● Policy confirmation required.\n"
        "file.write (path=mini.py)\n"
        "Reply exactly yes to allow once, session to allow this tool for the "
        "session, or no to cancel.\n"
        "✓ Wrote file.write · <1s\n"
        "Done in 21s\n"
        "❯ Ask anything · @ to mention a file · / for commands\n"
    )

    assert approval_prompt_needs_reply(transcript, offset=0)


def test_approval_prompt_does_not_need_reply_after_reply_was_queued() -> None:
    transcript = (
        "● Policy confirmation required.\n"
        "Reply exactly yes to allow once, session to allow this tool for the "
        "session, or no to cancel.\n"
        " > session\n"
        "▊  Queued message (1 pending).\n"
    )

    assert not approval_prompt_needs_reply(transcript, offset=0)


def test_active_approval_visible_accepts_allow_once_prompt() -> None:
    screen = (
        "● Policy confirmation required.\n"
        "file.write (path=tmp/.gitkeep)\n"
        "Reply exactly yes to allow once, session to allow this tool for the "
        "session, or no to cancel.\n"
    )

    assert active_approval_visible(screen)


@pytest.mark.parametrize(
    ("screen", "menu"),
    (
        ("[A] Allow once   [S] Session allow   [D] Deny", "legacy"),
        ("[y]es / [N]o / [a]lways:", "compact"),
    ),
)
def test_inline_approval_menu_supports_both_focus_surfaces(
    screen: str,
    menu: str,
) -> None:
    assert inline_approval_menu(screen) == menu
    assert active_approval_visible(screen)


@pytest.mark.parametrize(
    "screen",
    [
        "[y]es / [N]o / [a]lways:\n❯ Ask anything",
        "[y]es / [N]o / [a]lways:\n● file.write(example.py)",
        "[y]es / [N]o / [a]lways: a\n❯ ● file.write(example.py)",
        "[y]es / [N]o / [a]lways: a\nFIRST:a",
        "[A] Allow once [S] Session allow [D] Deny\nDone in 2s",
    ],
)
def test_inline_approval_menu_ignores_historical_prompts(screen: str) -> None:
    assert inline_approval_menu(screen) is None
    assert not active_approval_visible(screen)


def test_inline_approval_menu_ignores_persistent_input_footer() -> None:
    screen = (
        "Approval required for 5 queued writes\n"
        "[y]es / [N]o / [a]lways:\n"
        "input: queue next message"
    )

    assert inline_approval_menu(screen) == "compact"
    assert active_approval_visible(screen)


def test_inline_approval_menu_accepts_active_prompt_with_bare_cursor() -> None:
    screen = "Approval required: file.write(module.py)\n[y]es / [N]o / [a]lways:\n❯"

    assert inline_approval_menu(screen) == "compact"
    assert active_approval_visible(screen)


def test_inline_approval_menu_accepts_prompt_with_same_line_status() -> None:
    screen = (
        "Approval required: file.write(test_hello.py)\n"
        "[y]es / [N]o / [a]lways: ● Running file.write(README.md)"
    )

    assert inline_approval_menu(screen) == "compact"
    assert active_approval_visible(screen)


def test_inline_approval_menu_uses_latest_overlapping_prompt() -> None:
    screen = (
        "[y]es / [N]o / [a]lways: Approval required: file.write(wc.py)\n"
        "[y]es / [N]o / [a]lways:"
    )

    assert inline_approval_menu(screen) == "compact"
    assert (
        inline_approval_fingerprint(screen)
        == "compact:Approval required: file.write(wc.py)"
    )


@pytest.mark.parametrize(
    ("screen", "reply", "key"),
    (
        ("[A] Allow once [S] Session allow [D] Deny", "yes", "a"),
        ("[A] Allow once [S] Session allow [D] Deny", "session", "s"),
        ("[A] Allow once [S] Session allow [D] Deny", "no", "d"),
        ("[y]es / [N]o / [a]lways:", "yes", "yes"),
        ("[y]es / [N]o / [a]lways:", "session", "always"),
        ("[y]es / [N]o / [a]lways:", "no", "no"),
    ),
)
def test_inline_approval_key_matches_the_visible_menu(
    screen: str,
    reply: str,
    key: str,
) -> None:
    assert inline_approval_key(screen, reply) == key


def test_inline_approval_fingerprint_distinguishes_consecutive_targets() -> None:
    readme = "Approval required: file.write(README.md)\n[y]es / [N]o / [a]lways:"
    module = "Approval required: file.write(module.py)\n[y]es / [N]o / [a]lways:"

    assert inline_approval_fingerprint(readme) != inline_approval_fingerprint(module)


def test_compact_approval_submission_handles_consecutive_prompts(
    tmp_path: Path,
) -> None:
    script = """
import asyncio
from prompt_toolkit import PromptSession

async def main():
    session = PromptSession()
    first = await session.prompt_async(
        'Approval required: file.write(README.md)\\n[y]es / [N]o / [a]lways: '
    )
    print(f'FIRST:{first}', flush=True)
    second = await session.prompt_async(
        'Approval required: file.write(module.py)\\n[y]es / [N]o / [a]lways: '
    )
    print(f'SECOND:{second}', flush=True)

asyncio.run(main())
"""
    with PtySession(
        argv=(sys.executable, "-c", script),
        cwd=tmp_path,
        rows=20,
        cols=100,
    ) as session:
        session.wait_for_after(r"file\.write\(README\.md\)", offset=0, timeout=5)
        FocusProbe._submit_inline_approval(session, "session")
        session.wait_for_after(r"file\.write\(module\.py\)", offset=0, timeout=5)
        FocusProbe._submit_inline_approval(session, "session")
        transcript = session.wait_for_after(r"SECOND:always", offset=0, timeout=5)

    assert "FIRST:always" in transcript
    assert "FIRST:alwaysalways" not in transcript


def test_active_turn_busy_accepts_current_responding_footer() -> None:
    screen = (
        "Done in 12s\n"
        "> session\n"
        "\u25cf responding | 0s | model: openai/MiniMax-M2.7 | Esc cancel\n"
    )

    assert active_turn_busy(screen)


def test_active_turn_busy_ignores_old_progress_above_ready_composer() -> None:
    screen = (
        "\u25cf responding | 4s | model: openai/MiniMax-M2.7\n"
        + "\n".join(f"answer line {index}" for index in range(8))
        + "\n\u276f Ask anything\n"
    )

    assert not active_turn_busy(screen)


def test_active_approval_visible_ignores_waiting_status_without_prompt() -> None:
    screen = "● 19s | Waiting for your reply...\n"

    assert not active_approval_visible(screen)


def test_active_approval_visible_accepts_session_grant_copy() -> None:
    screen = (
        "file.write (path=tmp/.gitkeep)\n"
        "Reply exactly yes to allow once, session to allow this tool for the "
        "session, or no to cancel.\n"
    )

    assert active_approval_visible(screen)


def test_compact_inline_approval_stops_after_key_echo_without_newline() -> None:
    screen = 'Approval required: file.write("wordcount.py")\n[y]es / [N]o / [a]lways: a'

    assert inline_approval_menu(screen) is None
    assert not active_approval_visible(screen)


def test_active_approval_visible_keeps_unanswered_prompt_after_input_returns() -> None:
    screen = (
        "● Policy confirmation required.\n"
        "Reply exactly yes to confirm or exactly no to cancel.\n"
        "Done in 4s\n"
        "❯ Ask anything · @ to mention a file · / for commands\n"
    )

    assert active_approval_visible(screen)


def test_active_approval_visible_keeps_unanswered_prompt_with_boxed_composer() -> None:
    screen = (
        "● Policy confirmation required.\n"
        "Reply exactly yes to allow once, session to allow this tool for the "
        "session, or no to cancel.\n"
        "Done in 4s\n"
        "❯\n"
        "▊  Ask anything · @ to mention a file · / for commands\n"
        "● responding | 2s | queued: 7 | Esc cancel\n"
    )

    assert active_approval_visible(screen)


def test_active_approval_visible_ignores_waiting_history_after_input_returns() -> None:
    screen = (
        "● 19s | Waiting for your reply...\n"
        "Done in 22s\n"
        "❯ Ask anything · @ to mention a file · / for commands\n"
    )

    assert not active_approval_visible(screen)


def test_active_approval_visible_accepts_waiting_prompt_with_visible_composer() -> None:
    screen = (
        "Reply exactly yes to allow once, session to allow this tool for the "
        "session, or no to cancel.\n"
        "● 19s | Waiting for your reply...\n"
        "❯ Type approval response · session to allow this tool\n"
    )

    assert active_approval_visible(screen)


def test_active_approval_visible_keeps_unanswered_prompt_active_after_done() -> None:
    screen = (
        "Reply exactly yes to allow once, session to allow this tool for the "
        "session, or no to cancel.\n"
        "✓ Wrote file.write · <1s\n"
        "Done in 18s\n"
        "● 18s | Waiting for your reply...\n"
    )

    assert active_approval_visible(screen)


def test_turn_output_uses_prompt_boundary() -> None:
    prompt = "Generate a result."
    transcript = f"banner\n❯ {prompt}\n● Final result.\nDone in 4s\n"

    assert turn_output_text(transcript, prompt).strip().startswith("● Final result.")


def test_turn_output_preserves_answers_across_repeated_screen_frames() -> None:
    prompt = "Inspect nasm availability."
    transcript = (
        "old turn\n❯ Inspect nasm\n availability.\n"
        "● Running command -v nasm\n"
        "\f"
        f"old turn\n❯ {prompt}\n● nasm is available.\nDone in 4s\n"
        "\f"
        "● nasm is available.\nDone in 4s\n❯ Ask anything\n"
    )

    output = turn_output_text(transcript, prompt)

    assert "nasm is available" in output
    assert "old turn" not in output


def test_pty_screen_rendering_skips_empty_cells(tmp_path) -> None:
    session = PtySession(argv=("/bin/echo", "unused"), cwd=tmp_path, rows=1, cols=3)
    session._screen.buffer[0][0] = Char(data="A")
    session._screen.buffer[0][1] = Char(data="")
    session._screen.buffer[0][2] = Char(data="B")

    assert session._screen_display_lines() == ["AB"]
