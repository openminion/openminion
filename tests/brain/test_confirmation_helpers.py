from __future__ import annotations

from types import SimpleNamespace

from openminion.modules.brain.loop.tools.confirmation import (
    apply_session_confirmation_grant,
    is_session_confirmation_response,
)


def test_is_session_confirmation_response_matches_session_scope_phrases() -> None:
    assert is_session_confirmation_response("session") is True
    assert is_session_confirmation_response(" allow   this   session ") is True
    assert is_session_confirmation_response("yes") is False


def test_apply_session_confirmation_grant_adds_session_tool_override() -> None:
    state = SimpleNamespace(permission_overrides={})
    command = SimpleNamespace(tool_name="file.write")

    assert apply_session_confirmation_grant(state, command) is True

    assert state.permission_overrides == {"file.write": "bypass"}
