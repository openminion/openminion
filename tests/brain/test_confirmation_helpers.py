from __future__ import annotations

from types import SimpleNamespace

from openminion.modules.brain.loop.tools.confirmation import (
    apply_session_confirmation_grant,
    is_session_confirmation_response,
)


def test_is_session_confirmation_response_matches_session_scope_phrases() -> None:
    assert is_session_confirmation_response("yes for session") is True
    assert is_session_confirmation_response(" yes   this   session ") is True
    assert is_session_confirmation_response("yes") is False


def test_apply_session_confirmation_grant_adds_session_scope_metadata() -> None:
    state = SimpleNamespace(session_id="sess-123")
    command = SimpleNamespace(inputs={"path": "notes.txt"})

    apply_session_confirmation_grant(state, command)

    assert command.inputs == {
        "path": "notes.txt",
        "confirmation_scope": "session",
        "confirmation_scope_session_id": "sess-123",
    }
