from __future__ import annotations

from tests.e2e.runners.run_chat_permutations_e2e import (
    _conversation_messages,
    _latest_prompt_requires_confirmation,
    _transcript_has_known_failure,
)

import pytest

pytestmark = pytest.mark.e2e


def test_latest_prompt_requires_confirmation_when_new_prompt_contains_policy_gate() -> (
    None
):
    previous = "[session|agent] you> alpha\n"
    current = (
        previous
        + "[session|agent] agent: Policy confirmation required.\n"
        + "Reply exactly yes to confirm or exactly no to cancel.\n"
        + "[session|agent] you> "
    )
    assert _latest_prompt_requires_confirmation(previous, current) is True


def test_latest_prompt_requires_confirmation_ignores_old_confirmation_text() -> None:
    previous = (
        "[session|agent] agent: Policy confirmation required.\n[session|agent] you> "
    )
    current = previous + "[session|agent] agent: handled yes\n[session|agent] you> "
    assert _latest_prompt_requires_confirmation(previous, current) is False


def test_transcript_has_known_failure_detects_fail_closed_contracts() -> None:
    assert (
        _transcript_has_known_failure(
            "General act work ended without the required typed "
            "finalization_status contract."
        )
        is True
    )
    assert _transcript_has_known_failure("Adaptive loop stopped unexpectedly.") is True
    assert _transcript_has_known_failure("[chat] turn failed.") is True
    assert _transcript_has_known_failure("normal assistant response") is False


def test_conversation_messages_injects_confirmation_reply_for_write_and_exec() -> None:
    messages = _conversation_messages(
        "Write to file /tmp/x the following content: hi\n"
        'tool run_command {"command":"pwd"}\n'
        "Read file /tmp/x\n"
    )
    assert messages == [
        "Write to file /tmp/x the following content: hi",
        "yes",
        'tool run_command {"command":"pwd"}',
        "yes",
        "Read file /tmp/x",
    ]
