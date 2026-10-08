from __future__ import annotations

from openminion.modules.brain.loop.tools.confirmation import (
    is_session_confirmation_response,
)


def test_is_session_confirmation_response_matches_session_scope_phrases() -> None:
    assert is_session_confirmation_response("session") is True
    assert is_session_confirmation_response(" allow   this   session ") is True
    assert is_session_confirmation_response("yes") is False
